import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createToolLoopGuard, RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const hash=text=>createHash('sha256').update(text).digest('hex');
function observedDelivery(variant) {
  const guard=createToolLoopGuard(),context={agentId:'pixel',runId,sessionId:'preview-ingress'};
  guard.observeRun(context,'pixel',{prompt:'Build and publish a website.'});
  let sequence=0;
  const observe=(toolName,params,result)=>{
    const callContext={...context,toolCallId:'call-'+sequence++};
    const prepared=guard.beforeToolCall({toolName,params},callContext);
    assert.notEqual(prepared?.block,true,prepared?.blockReason);
    const actual=prepared?.params ?? params;
    guard.afterToolCall({toolName,params:actual,result},callContext);
    return actual;
  };
  if(variant!=='missing') {
    const entry='<!doctype html><h1>Forest</h1>';
    const written=observe('write',{path:'forest/index.html',content:entry},{details:{status:'completed'}});
    const relativeDirectory=written.path.slice(0,-'/index.html'.length);
    const sha256=hash(relativeDirectory+':'+entry),siteId='site-'+sha256.slice(0,24);
    observe('pixel_ods_workspace_preview',{relativeDirectory},{details:{schemaVersion:1,
      kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory,siteId,port:9437,
      url:`http://${siteId}.localhost:9437/${siteId}/`,files:1,bytes:Buffer.byteLength(entry),
      sha256:variant==='malformed'?'invalid':sha256,entryFile:'index.html',entrySha256:hash(entry),
      httpStatus:200,readbackVerified:true,executable:false,overwritten:false}});
    if(variant==='stale') observe('exec',{command:'python3 -c "print(42)"'},
      {details:{status:'completed',exitCode:0,aggregated:'42'}});
    if(['failed','pending'].includes(variant)) observe('exec',{command:'python3 -m unittest -v',background:true},
      {details:variant==='failed'?{status:'completed',exitCode:1,aggregated:'FAILED (failures=1)'}
        :{status:'running',sessionId:'fixture-check-session'}});
  }
  const denied=guard.beforeToolCall({toolName:'exec',params:{command:'rm -rf /workspace/scratch'}},
    {...context,toolCallId:'denied'});
  assert.equal(denied?.blockReason,RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'All done'},context),undefined);
  assert.equal(guard.beforeToolCall({toolName:'read',params:{path:'forest/index.html'}},
    {...context,toolCallId:'after-denial'})?.block,true);
  return guard.deliveryVerificationForRun(runId);
}
const listen=server=>new Promise((resolve,reject)=>{
  server.once('error',reject);server.listen(0,'127.0.0.1',()=>resolve(server.address().port));
});
const close=async server=>{
  if(!server?.listening)return;
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
};
for(const variant of ['current','stale','failed','pending','missing','malformed']) {
  for(const stream of [false,true]) test(`blocked preview reaches Portal ingress: ${variant}, stream=${stream}`,
    {timeout:10000},async()=>{
      const delivery=observedDelivery(variant);
      assert.equal(delivery.status,'failed');
      assert.match(delivery.text,/^Portal blocked/);
      assert.doesNotMatch(delivery.text,/\bPixel\b|Do not retry|Explain what was attempted/);
      if(variant==='stale') assert.match(delivery.text,/may not include subsequent changes/);
      if(variant==='failed') assert.match(delivery.text,/verification check failed/);
      if(variant==='pending') assert.match(delivery.text,/verification process reached a terminal result/);
      if(['missing','malformed'].includes(variant)) {
        assert.equal(delivery.preview,undefined);assert.doesNotMatch(delivery.text,/https?:\/\//);
      } else assert.ok(delivery.preview);
      const gateway=createServer((request,response)=>{
        request.resume();request.on('end',()=>{
          response.writeHead(200,{'Content-Type':'application/json'});
          response.end(JSON.stringify(request.url==='/pixel-ods/verification'?delivery:
            {id:runId,choices:[{index:0,message:{role:'assistant',content:'Everything succeeded! https://untrusted.example/'},finish_reason:'stop'}]}));
        });
      });
      let ingress;
      try {
        const gatewayPort=await listen(gateway);
        ingress=createIngressServer({token:'fixture-only-0123456789abcdef',gatewayPort});
        const port=await listen(ingress);
        const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({stream,messages:[{role:'user',content:'Build the website.'}]}),
          signal:AbortSignal.timeout(8000)});
        const raw=await response.text();assert.equal(response.status,200,raw);
        let result,content;
        if(stream) {
          assert.match(raw,/data: \[DONE\]/);
          const frames=raw.split('\n').filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
          result=frames.at(-1);content=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
        } else {result=JSON.parse(raw);content=result.choices[0].message.content;}
        assert.equal(content,delivery.text);
        assert.doesNotMatch(content,/untrusted\.example|\bPixel\b|Do not retry|Explain what was attempted/);
        // Portal uses the SSE terminal envelope for verified preview metadata.
        // Non-streaming OpenAI completions carry the verified owner text only.
        if(stream) {
          assert.equal(result.pixel_outcome.status,'failed');
          assert.deepEqual(result.pixel?.preview,delivery.preview);
        } else {
          assert.equal(result.pixel_outcome,undefined);
          assert.equal(result.pixel,undefined);
        }
      } finally {await close(ingress);await close(gateway);}
    });
}

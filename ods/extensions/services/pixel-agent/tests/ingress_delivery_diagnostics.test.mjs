import test from 'node:test';
import assert from 'node:assert/strict';

const {createIngressServer} = await import(process.env.PIXEL_INGRESS_MODULE ?? '../host/pixel_ingress.mjs');
const RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const SECRET = 'PRIVATE-PROMPT-TOOL-TOKEN-ERROR-CONTENT';
const json = (value, status=200) => new Response(JSON.stringify(value), {status, headers:{'content-type':'application/json'}});
const receipt = (status='not-delegated') => ({schemaVersion:1,kind:'ods-subagent-delivery',runId:RUN,status});
const error = () => new Error(SECRET, {cause:new Error(SECRET)});

async function fixture(t, fault, stream) {
  const logs=[], counts={submissions:0,receipts:0};
  t.mock.method(console,'warn',value=>logs.push(String(value)));
  let abortParent;
  const deps={setTimeout:(callback,ms)=>{
    if(ms===1920000) abortParent=callback;
    return setTimeout(callback,ms);
  },clearTimeout,fetch:async(url,options)=>{
    const endpoint=new URL(url).pathname;
    if(endpoint==='/health')return json({ok:true});
    if(endpoint==='/pixel-ods/activity')return json({task:null});
    if(endpoint==='/pixel-ods/verification')return json({status:'none'});
    if(endpoint==='/pixel-ods/read-only-extension-continuation')return json({schemaVersion:1,kind:'ods-extension-read-only-continuation',eligible:false});
    if(endpoint==='/pixel-ods/unfinished-extension-decision')return json({schemaVersion:1,kind:'ods-extension-unfinished-decision',eligible:false});
    if(endpoint==='/v1/chat/completions'){
      counts.submissions++;
      return json({id:fault==='identity'?SECRET:RUN,choices:[{finish_reason:'stop',message:{role:'assistant',content:'completed answer'}}]});
    }
    if(endpoint==='/pixel-ods/subagent-delivery'){
      counts.receipts++;
      if(fault==='transport')throw error();
      // An error name/cause/message is not authoritative timeout evidence.
      if(fault==='fake-timeout')throw Object.assign(error(),{name:'TimeoutError',status:418});
      if(fault==='http')return new Response(SECRET,{status:409,headers:{'content-type':'application/json','x-private':SECRET}});
      if(fault==='content-type')return new Response(SECRET,{headers:{'content-type':`text/${SECRET}`}});
      if(fault==='json')return new Response(SECRET,{headers:{'content-type':'application/json'}});
      if(fault==='oversized')return new Response(SECRET.repeat(40000),{headers:{'content-type':'application/json'}});
      if(fault==='body')return new Response(new ReadableStream({start(controller){controller.error(error())}}),{headers:{'content-type':'application/json'}});
      if(fault==='schema')return json({...receipt(),runId:SECRET,private:SECRET});
      if(fault==='verification')return json({...receipt('ready'),text:SECRET,verification:{status:'passed',private:SECRET}});
      if(fault==='interrupted')return json({...receipt('interrupted'),message:SECRET});
      if(fault==='deadline' || fault==='parent-abort')return new Promise((resolve,reject)=>{
        options.signal.addEventListener('abort',()=>reject(error()),{once:true});
        if(fault==='parent-abort')queueMicrotask(abortParent);
      });
      if(fault==='ready')return json({...receipt('ready'),text:'consolidated answer',verification:{status:'passed'}});
      if(fault==='waiting')return json(counts.receipts===1?receipt('waiting'):{...receipt('ready'),text:'consolidated answer',verification:{status:'passed'}});
      return json(receipt());
    }
    throw new Error('Unexpected fixture endpoint');
  }};
  const server=createIngressServer({token:SECRET,gatewayPort:18789,deps});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
  t.after(()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections()}));
  const started=performance.now();
  const response=await fetch(`http://127.0.0.1:${server.address().port}/v1/chat/completions`,{
    method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({user:'delivery-diagnostic-owner',messages:[{role:'user',content:SECRET}],stream}),
  });
  return {logs,counts,status:response.status,body:await response.text(),elapsed:performance.now()-started};
}

const failures=[
  ['identity','identity_invalid',undefined],
  ['transport','transport_failed',undefined],
  ['fake-timeout','transport_failed',undefined],
  ['http','http_rejected',409],
  ['content-type','content_type_invalid',200],
  ['json','json_invalid',200],
  ['oversized','body_invalid',200],
  ['body','body_invalid',200],
  ['schema','schema_invalid',200],
  ['verification','schema_invalid',200],
  ['interrupted','receipt_interrupted',200],
  ['parent-abort','request_aborted',undefined],
  ['deadline','receipt_deadline',undefined],
];

for(const stream of [false,true]) {
  for(const [fault,reason,status] of failures)test(`${fault} has private fixed delivery diagnostics (stream=${stream})`,async t=>{
    const result=await fixture(t,fault,stream);
    assert.equal(result.status,stream?200:502);
    assert.match(result.body,stream?/upstream stream failed/:/error/);
    assert.doesNotMatch(result.body,/completed answer|consolidated answer/);
    assert.equal(result.counts.submissions,1,'never resubmit model work');
    assert.equal(result.counts.receipts,fault==='identity'?0:1,'never retry a failed receipt read');
    assert.equal(result.logs.length,1,'one classified log, including nonstream failures');
    const expected=`pixel-ingress response failed at subagent-delivery; run=${fault==='identity'?'unavailable':RUN}; reason=${reason}${status===undefined?'':`; status=${status}`}`;
    assert.equal(result.logs[0],expected);
    assert.ok(!JSON.stringify({logs:result.logs,body:result.body}).includes(SECRET),'no prompt, completion, token, error/cause, header or receipt content escapes');
    if(fault==='deadline')assert.ok(result.elapsed>=4900,'the existing five-second receipt budget is preserved');
    if(fault==='fake-timeout')assert.doesNotMatch(result.logs[0],/receipt_deadline|418/);
  });
  for(const fault of ['not-delegated','ready','waiting'])test(`${fault} still delivers without diagnostic noise or replay (stream=${stream})`,async t=>{
    const result=await fixture(t,fault,stream);
    assert.equal(result.status,200);
    assert.match(result.body,fault==='not-delegated'?/completed answer/:/consolidated answer/);
    assert.deepEqual(result.logs,[]);
    assert.equal(result.counts.submissions,1);
    assert.equal(result.counts.receipts,fault==='waiting'?2:1);
  });
}

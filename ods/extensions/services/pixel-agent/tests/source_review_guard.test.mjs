import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

test('direct and Tool Search publication preserve explicit source scope through guard and delivered receipt',()=>{
  for(const wrapped of [false,true]) {
    const guard=createToolLoopGuard();
    const scope={agentId:'pixel',runId:'run-1',sessionId:'session-1'};
    guard.observeRun(scope,'pixel',{prompt:'Build and show me a demo website.'});
    const content='<h1>Built app</h1>',params={path:'demo/dist/index.html',content};
    guard.beforeToolCall({toolName:'write',params},scope,'pixel');
    guard.afterToolCall({toolName:'write',params,result:{details:{status:'completed'}}},scope,'pixel');
    const args={relativeDirectory:'demo/dist',sourceDirectory:'demo'},tool='pixel_ods_workspace_preview';
    const event={toolName:wrapped?'tool_call':tool,params:wrapped?{id:tool,args}:args};
    const normalized=guard.beforeToolCall(event,{...scope,toolName:event.toolName},'pixel');
    assert.notEqual(normalized?.block,true);
    assert.deepEqual(wrapped?normalized.params.args:normalized.params,args);
    const raw=Buffer.from(content),name=Buffer.from('index.html');
    const digest=createHash('sha256').update(Buffer.from([0,0,0,name.length])).update(name);
    const size=Buffer.alloc(8);size.writeBigUInt64BE(BigInt(raw.length));
    const sha256=digest.update(size).update(raw).digest('hex'),siteId='site-'+sha256.slice(0,24);
    const source={schemaVersion:1,sourceId:'source-'+'c'.repeat(24),sha256:'c'.repeat(64),relativeDirectory:'demo',files:2,bytes:80,omitted:{directories:1,files:0,sensitiveFiles:0}};
    const details={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:'demo/dist',siteId,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,files:1,bytes:raw.length,sha256,entryFile:'index.html',entrySha256:createHash('sha256').update(raw).digest('hex'),httpStatus:200,readbackVerified:true,executable:false,overwritten:false,source};
    // Tool Search re-enters the native hook for its selected inner tool.
    if(wrapped)assert.deepEqual(guard.beforeToolCall({toolName:tool,params:normalized.params.args},{...scope,toolName:tool},'pixel').params,args);
    guard.afterToolCall({toolName:tool,runId:scope.runId,params:args,result:{details}}, {...scope,toolName:tool},'pixel');
    const verification=guard.verificationForRun('run-1');
    assert.ok(verification.preview,JSON.stringify({wrapped,verification}));
    assert.deepEqual(verification.preview.source,source);
    const invalid={...args,sourceDirectory:'other'};
    assert.equal(guard.beforeToolCall({toolName:tool,params:invalid},scope,'pixel')?.block,true);
  }
});

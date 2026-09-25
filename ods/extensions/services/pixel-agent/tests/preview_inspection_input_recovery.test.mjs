import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspacePreviewInspectTool} from '../plugin/workspace-preview-inspect.mjs';

const valid = () => ({siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),
  viewport:{width:800,height:600},steps:[{action:'assert-hidden',locator:{selector:'#details'}}]});

test('invalid inspection arguments never contact the broker or report it unavailable',async()=>{
  let calls=0;
  const tool=createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('should not execute');}});
  const observed={siteId:'882fd036b2cd4263ba3ffbb7',sha256:'3341',viewport:{width:1920,height:1080},
    steps:[{action:'assert-hidden',locator:{role:'article',name:'Midnight Sold-Out Concert',exact:true}}]};
  const unsupportedRole=valid();unsupportedRole.steps[0].locator={role:'article',name:'Details',exact:true};
  const wrongBinding=valid();wrongBinding.siteId='site-'+ 'b'.repeat(24);
  const wrongViewport=valid();wrongViewport.viewport.width=2000;
  for(const params of [observed,unsupportedRole,wrongBinding,wrongViewport,null,{}]) {
    const result=await tool.execute('inspect',params);
    assert.equal(result.isError,true);
    assert.equal(result.details.errorCode,'invalid_request');
    assert.match(result.content[0].text,/rejected before execution/);
    assert.match(result.content[0].text,/tool_describe/);
    assert.match(result.content[0].text,/Requested behavior remains unverified/);
  }
  assert.equal(calls,0);
});

test('valid input still distinguishes unavailable transport and cancellation',async()=>{
  let calls=0;
  const tool=createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('not reachable');}});
  assert.equal((await tool.execute('inspect',valid())).details.errorCode,'unavailable');
  assert.equal(calls,1);
  const controller=new AbortController();controller.abort();
  for(const params of [valid(),null]) {
    const result=await tool.execute('cancelled',params,controller.signal);
    assert.equal(result.details.errorCode,'cancelled');
    assert.equal(result.isError,true);
  }
  assert.equal(calls,1);
});

test('untrusted malformed broker receipts cannot become argument errors or successes',async()=>{
  const tool=createWorkspacePreviewInspectTool({request:async()=>({status:'passed'})});
  const result=await tool.execute('inspect',valid());
  assert.equal(result.isError,true);
  assert.equal(result.details.errorCode,'unavailable');
});

test('observed digest guessing gets actionable feedback without contacting the broker',async()=>{
  let calls=0;
  const tool=createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('offline');}});
  const shortened=valid();shortened.sha256='a'.repeat(24);
  assert.match((await tool.execute('short',shortened)).content[0].text,/full 64-character lowercase snapshot digest/);
  const fileDigest=valid();fileDigest.sha256='b'.repeat(64);
  assert.match((await tool.execute('file',fileDigest)).content[0].text,/same latest publication receipt; do not use entrySha256/);
  assert.equal(calls,0);
  assert.equal((await tool.execute('corrected',valid())).details.errorCode,'unavailable');
  assert.equal(calls,1);
});

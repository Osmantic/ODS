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

test('empty accessible names identify every affected step before a corrected retry',async()=>{
  const calls=[];
  const tool=createWorkspacePreviewInspectTool({request:async params=>{calls.push(structuredClone(params));throw Error('offline fixture');}});
  // Synthetic public-shaped reproduction, not a private browser transcript.
  const params={...valid(),viewport:{width:375,height:667},steps:[
    {action:'assert-visible',locator:{role:'heading',name:'Packing Checklist',exact:true}},
    {action:'assert-visible',locator:{role:'textbox',name:'',exact:true}},
    {action:'fill',locator:{role:'textbox',name:'',exact:true},value:'Passport'},
    {action:'click',locator:{role:'button',name:'Add',exact:true}},
    {action:'assert-text',locator:{selector:'#counter'},expectedText:'1 of 1'},
  ]};
  const before=structuredClone(params);
  const result=await tool.execute('invalid-empty-names',params);
  assert.equal(result.details.errorCode,'invalid_request');
  assert.equal(result.isError,true);
  assert.equal(calls.length,0);
  assert.match(result.content[0].text,/Step 2 \(steps\[1\]\.locator\.name\)/);
  assert.match(result.content[0].text,/Step 3 \(steps\[2\]\.locator\.name\)/);
  assert.match(result.content[0].text,/non-empty accessible name/);
  assert.match(result.content[0].text,/unnamed.*CSS selector/i);
  assert.match(result.content[0].text,/Retain an interaction and its visible postcondition/);
  assert.deepEqual(params,before,'invalid input is never corrected automatically');

  const corrected=structuredClone(params);
  corrected.steps[1].locator={selector:'#item-input'};
  corrected.steps[2].locator={selector:'#item-input'};
  assert.equal((await tool.execute('corrected',corrected)).details.errorCode,'unavailable');
  assert.equal(calls.length,1,'an explicit corrected plan reaches transport');
  assert.deepEqual(calls[0].steps,corrected.steps,'the inspector never changes the heading or counter expectation to make a test pass');
  assert.equal(calls[0].steps[4].expectedText,'1 of 1','a potentially wrong postcondition stays the caller responsibility');
});

test('inspection tool schema excludes empty CSS selectors and accessible names',()=>{
  const schema=createWorkspacePreviewInspectTool().parameters.properties.steps.items.properties.locator;
  assert.equal(schema.oneOf[0].properties.selector.minLength,1);
  assert.equal(schema.oneOf[1].properties.name.minLength,1);
});

test('bounded step diagnostics identify fields without reflecting untrusted argument text',async()=>{
  const tool=createWorkspacePreviewInspectTool({request:async()=>assert.fail('invalid plan must not run')});
  const marker='UNTRUSTED_ARGUMENT_DO_NOT_REPEAT';
  const params={...valid(),steps:[
    {action:'assert-visible',locator:{selector:''}},
    {action:'click',locator:{role:marker,name:'Add',exact:true}},
    {action:'click',locator:{role:'button',name:'Add',exact:false}},
    {action:'assert-text',locator:{selector:'#counter'},expectedText:' invalid spacing '},
    {action:'fill',locator:{selector:'#item-input'},value:marker+'\n'},
    null,
  ]};
  const result=await tool.execute('invalid-fields',params);
  for(const [index,field] of ['locator.selector','locator.role','locator.exact','expectedText','value',''].entries()) {
    assert.ok(result.content[0].text.includes(`Step ${index+1} (steps[${index}]${field?'.'+field:''})`));
  }
  assert.doesNotMatch(result.content[0].text,new RegExp(marker));
  assert.equal(result.details.errorCode,'invalid_request');
  assert.equal(result.isError,true);
  const sparse={...valid(),steps:Array(1)};
  assert.equal((await tool.execute('sparse',sparse)).details.errorCode,'invalid_request');
});

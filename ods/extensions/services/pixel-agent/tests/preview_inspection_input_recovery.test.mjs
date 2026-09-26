import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {argumentCorrection,createWorkspacePreviewInspectTool,normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const valid = () => ({siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),
  viewport:{width:800,height:600},steps:[{action:'assert-hidden',locator:{selector:'#details'}}]});
const load = name => JSON.parse(fs.readFileSync(new URL(`./${name}`, import.meta.url), 'utf8'));
const inspects = fixture => fixture.turns[0].calls.filter(call => call.tool === 'pixel_ods_workspace_preview_inspect');
const STRIXY = load('inspection-recovery-strixy-round107.json');
const LAPTOP = load('inspection-recovery-laptop-round107.json');
const byCall = (fixture, number) => inspects(fixture).find(call => call.call === number);
const BUTTON = {role:'button',name:'Show sold out',exact:true};
const READY = /Next step: call pixel_ods_workspace_preview_inspect \(a tool in your list; call it by name\) with exactly these args: (\{.*?\})(?:; these are your own identifiers and locators in the required shape\.$| The target )/;
const readyArgs = text => { const match = READY.exec(text); return match && JSON.parse(match[1]); };
const rejected = async (params, options = {}) => {
  let calls = 0;
  const tool = createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('should not execute');}, ...options});
  const result = await tool.execute('inspect', params);
  assert.equal(calls, 0, 'the inspector is never contacted on invalid input');
  assert.equal(result.isError, true);
  assert.deepEqual(result.details, {schemaVersion:1,kind:'ods-pixel-preview-inspection',status:'failed',errorCode:'invalid_request',
    scope:result.details.scope});
  const text = result.content[0].text;
  assert.match(text, /^Preview inspection request rejected before execution: invalid arguments\. /);
  assert.match(text, /The inspector was not contacted; this does not establish service unavailability\. Requested behavior remains unverified\. Next step: /);
  assert.doesNotMatch(text, /tool_describe|through tool_call/);
  assert.doesNotMatch(text, /\bpassed\b|\bverified\b/);
  return text;
};

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
    // Strixy round 107 obeyed the old hint as tool_call {id:"tool_describe"}.
    assert.doesNotMatch(result.content[0].text,/tool_describe|through tool_call/);
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

test('rejections name the failing step and key; messages stay the hint keys',()=>{
  const failure=params=>{ try { normalizeWorkspacePreviewInspectionParams(params); } catch (error) { return error; } assert.fail('accepted'); };
  const steps=(...list)=>({...valid(),steps:list});
  const cases=[
    [{},'invalid preview inspection fields','siteId',undefined],
    [{...valid(),url:'http://localhost'},'invalid preview inspection fields','url',undefined],
    [{...valid(),sha256:'a'.repeat(24)},'invalid preview inspection digest','sha256',undefined],
    [{...valid(),siteId:'site-'+'b'.repeat(24)},'invalid preview inspection snapshot binding','siteId',undefined],
    [{...valid(),viewport:{width:2000,height:600}},'invalid preview inspection viewport','viewport',undefined],
    [steps(),'invalid preview inspection steps','steps',undefined],
    [steps(valid().steps[0],{action:'assert-hidden',selector:'#midnight-card'}),'invalid inspection step','selector',1],
    [steps({action:'hover',locator:{selector:'#a'}}),'invalid inspection step','action',0],
    [steps({action:'click',locator:{selector:'xpath=//a'}}),'invalid CSS locator','locator.selector',0],
    [steps({action:'click',locator:{role:'button',name:'Show sold out'}}),'invalid semantic locator','locator.exact',0],
    [steps({action:'click',locator:{role:'article',name:'Card',exact:true}}),'invalid semantic locator','locator.role',0],
    [steps({action:'click',locator:{role:'button',name:'',exact:true}}),'invalid semantic locator','locator.name',0],
    [steps({action:'click',locator:{role:'button',name:'Go',exact:false}}),'invalid semantic locator','locator.exact',0],
  ];
  for (const [params,message,key,step] of cases) {
    const error=failure(params);
    assert.deepEqual([error.message,error.key,error.step],[message,key,step],JSON.stringify(params));
  }
  assert.equal(failure(steps({action:'click',locator:{role:'article',name:'Card',exact:true}})).role,'article');
});

// Strixy round 107 (Qwen3.6-35B-A3B), calls 8 and 11: the recorded flattened
// steps. The lossless correction is exactly the owner's transition plan.
test('strixy round 107: flattened steps get the exact corrected args, which normalize',async()=>{
  const call8=byCall(STRIXY,8), call11=byCall(STRIXY,11);
  assert.deepEqual(call8.arguments.steps,[{action:'assert-hidden',selector:'#midnight-card'},
    {action:'click',name:'Show sold out',role:'button'},{action:'assert-visible',selector:'#midnight-card'}]);
  assert.match(call8.text,/Call tool_describe with id/,'the recorded hint');
  const expected={siteId:'site-57136a656368ffaf5e525770',sha256:'57136a656368ffaf5e5257705e1d02f5030477bdb03ba1faede43363c168b59f',
    viewport:{width:375,height:667},steps:[{action:'assert-hidden',locator:{selector:'#midnight-card'}},{action:'click',locator:BUTTON},
      {action:'assert-visible',locator:{selector:'#midnight-card'}}]};
  for (const call of [call8,call11]) {
    const text=await rejected(call.arguments);
    assert.ok(text.startsWith('Preview inspection request rejected before execution: invalid arguments. At step 1, key "selector": '+
      'Each step needs only a supported action and locator. '),text);
    const args=readyArgs(text);
    assert.deepEqual(args,{...expected,viewport:call.arguments.viewport});
    assert.ok(text.endsWith('; these are your own identifiers and locators in the required shape.'),text);
    assert.deepEqual(argumentCorrection(call.arguments),args);
    assert.ok(normalizeWorkspacePreviewInspectionParams(args));
  }
});

test('laptop round 107 call 4: a role/name locator without exact gets exact:true',async()=>{
  const call=byCall(LAPTOP,4);
  assert.deepEqual(call.arguments.steps[2],{action:'click',locator:{role:'button',name:'Show sold out'}});
  const text=await rejected(call.arguments);
  assert.ok(text.includes('At step 3, key "locator.exact": Use a supported role, exact accessible name and exact:true, or a CSS selector.'),text);
  const args=readyArgs(text);
  assert.deepEqual(args,{...call.arguments,steps:call.arguments.steps.map((step,index)=>index===2?{action:'click',locator:BUTTON}:step)});
  // exact:false is not a different matcher: the capsule only matches exactly.
  const loose=structuredClone(call.arguments);loose.steps[2].locator.exact=false;
  assert.deepEqual(readyArgs(await rejected(loose)),args);
});

test('laptop round 107 call 14: {} gets one shape example and no args',async()=>{
  const call=byCall(LAPTOP,14);
  assert.deepEqual(call.arguments,{});
  const text=await rejected(call.arguments);
  assert.equal(readyArgs(text),null);
  assert.ok(text.startsWith('Preview inspection request rejected before execution: invalid arguments. At key "siteId": '+
    'Provide only siteId, sha256, viewport and steps.'),text);
  assert.ok(text.endsWith('with arguments in this shape: {"siteId":"<siteId from the latest publication receipt>",'+
    '"sha256":"<full sha256 from the latest publication receipt>","viewport":{"width":375,"height":667},"steps":['+
    '{"action":"assert-hidden","locator":{"selector":"#<id of the affected element>"}},'+
    '{"action":"click","locator":{"role":"button","name":"<exact button text>","exact":true}},'+
    '{"action":"assert-visible","locator":{"selector":"#<id of the affected element>"}}]}; '+
    'copy siteId and sha256 from the latest publication receipt and each locator from your source. Do not guess snapshot identifiers.'),text);
  for (const params of [null,[],'x',{...valid(),sha256:'a'.repeat(24)}]) assert.equal(readyArgs(await rejected(params)),null);
});

// Tower2 round 094 asserted the card by role "article".
test('an unsupported role gets no lossless args; the owner requirement supplies its plan',async()=>{
  const article={role:'article',name:'Midnight Sold-Out Concert',exact:true};
  const params={...valid(),steps:[{action:'assert-hidden',locator:article},{action:'click',role:'button',name:'Show sold out'},
    {action:'assert-visible',locator:article}]};
  assert.equal(argumentCorrection(params),undefined);
  const plain=await rejected(params);
  assert.ok(plain.includes('At step 1, key "locator.role": role "article" is not one of button, link, checkbox, radio, textbox, combobox, heading, tab, switch; use a CSS selector such as an id.'),plain);
  assert.equal(readyArgs(plain),null);
  const asked=[];
  const requirement={target:'Midnight sold-out concert',control:{role:'button',name:'Show sold out'},initiallyHidden:true};
  const text=await rejected(params,{transitionRequirement:(id,value)=>{asked.push([id,value]);return requirement;}});
  assert.deepEqual(asked,[['inspect',params]],'bound to exactly this call');
  const heading={role:'heading',name:'Midnight sold-out concert',exact:true};
  assert.deepEqual(readyArgs(text),{...valid(),steps:[{action:'assert-hidden',locator:heading},{action:'click',locator:BUTTON},
    {action:'assert-visible',locator:heading}]});
  assert.ok(text.includes(' The target is a heading named "Midnight sold-out concert" from the owner\'s request;'),text);
  assert.doesNotMatch(text,/these are your own identifiers/);
  // Only the role error names the role list.
  assert.doesNotMatch(await rejected({...valid(),steps:[{action:'click',locator:{role:'button',name:'Go'}}]}),/is not one of/);
  // No requirement plan without a valid snapshot binding: identifiers are never invented.
  const unbound={...params,sha256:'b'.repeat(64)};
  assert.equal(readyArgs(await rejected(unbound,{transitionRequirement:()=>requirement})),null);
  // A throwing requirement is ignored.
  assert.equal(readyArgs(await rejected(params,{transitionRequirement:()=>{throw Error('x');}})),null);
});

test('extra unknown keys, unrepairable steps and unchanged input get no args',async()=>{
  const flattened={action:'assert-hidden',selector:'#details'};
  for (const params of [
    {...valid(),steps:[flattened],url:'http://localhost'},
    {...valid(),steps:[{...flattened,exact:true}]},
    {...valid(),steps:[{action:'assert-hidden',locator:{selector:'#details'},note:'x'}]},
    {...valid(),steps:[{action:'assert-hidden',locator:{selector:'#details',exact:true}}]},
    {...valid(),steps:[{action:'hover',selector:'#details'}]},
    {...valid(),steps:[{action:'click',role:'button',name:'Go',exact:true,level:1}]},
    {...valid(),steps:[{action:'click',role:'article',name:'Go'}]},
    {...valid(),steps:[{action:'assert-hidden',selector:'xpath=//a'}]},
    {...valid(),viewport:{width:2000,height:600},steps:[flattened]},
  ]) {
    assert.equal(argumentCorrection(params),undefined,JSON.stringify(params));
    assert.equal(readyArgs(await rejected(params)),null,JSON.stringify(params));
  }
  assert.equal(argumentCorrection(valid()),undefined,'valid input needs no correction');
});

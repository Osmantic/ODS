import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspacePreviewInspectTool, correctedInspectionArgs, normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

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
    // The tool is directly visible; the former tool_describe/tool_call advice
    // preceded strixy round 107's unresolvable tool_call {id:"tool_describe"}.
    assert.match(result.content[0].text,/Next step: call pixel_ods_workspace_preview_inspect directly with the exact published siteId/);
    assert.doesNotMatch(result.content[0].text,/tool_describe|tool_call/);
    assert.match(result.content[0].text,/Requested behavior remains unverified/);
  }
  assert.equal(calls,0);
});

// Recorded rejected plans (fleet r049 round 107): strixy calls 8, 11 and 12
// put a selector or role/name beside action; laptop call 4 left exact:true
// off a role/name locator. Each is the model's own plan with only its
// locators nested; the site identifiers are the published snapshots'.
const STRIXY_SITE={siteId:'site-57136a656368ffaf5e525770',sha256:'57136a656368ffaf5e5257705e1d02f5030477bdb03ba1faede43363c168b59f',viewport:{width:375,height:667}};
const LAPTOP_SITE={siteId:'site-ba3eb2f6e71217b5dbcf343e',sha256:'ba3eb2f6e71217b5dbcf343e4d8d8f75ce4dab637426c2a77682649a5c04f6fd',viewport:{width:375,height:667}};
const hidden={action:'assert-hidden',locator:{selector:'#midnight-card'}};
const shown={action:'assert-visible',locator:{selector:'#midnight-card'}};
const click={action:'click',locator:{role:'button',name:'Show sold out',exact:true}};
test('a plan rejected only for where its locators sit gets itself back, nested and ready to send',async()=>{
  let calls=0;
  const tool=createWorkspacePreviewInspectTool({request:async()=>{calls++;throw Error('should not execute');}});
  for(const [params,corrected] of [
    [{...STRIXY_SITE,steps:[{action:'assert-hidden',selector:'#midnight-card'},{action:'click',name:'Show sold out',role:'button'},{action:'assert-visible',selector:'#midnight-card'}]},
      {...STRIXY_SITE,steps:[hidden,click,shown]}],
    [{...STRIXY_SITE,steps:[{action:'assert-hidden',selector:'#midnight-card'},{action:'click',role:'button',name:'Show sold out',exact:true},{action:'assert-visible',selector:'#midnight-card'}]},
      {...STRIXY_SITE,steps:[hidden,click,shown]}],
    [{...STRIXY_SITE,steps:[{action:'assert-hidden',selector:'#midnight-card'},{action:'click',locator:{exact:true,name:'Show sold out',role:'button'}},{action:'assert-visible',selector:'#midnight-card'}]},
      {...STRIXY_SITE,steps:[hidden,click,shown]}],
    [{...LAPTOP_SITE,steps:[{action:'assert-visible',locator:{selector:'h1'}},{action:'assert-visible',locator:{selector:'.event-card.sold-out.hidden'}},
      {action:'click',locator:{role:'button',name:'Show sold out'}},{action:'assert-visible',locator:{selector:'.event-card.sold-out:not(.hidden)'}}]},
    {...LAPTOP_SITE,steps:[{action:'assert-visible',locator:{selector:'h1'}},{action:'assert-visible',locator:{selector:'.event-card.sold-out.hidden'}},
      click,{action:'assert-visible',locator:{selector:'.event-card.sold-out:not(.hidden)'}}]}],
  ]) {
    assert.deepEqual(correctedInspectionArgs(params),corrected);
    assert.doesNotThrow(()=>normalizeWorkspacePreviewInspectionParams(corrected));
    const text=(await tool.execute('inspect',params)).content[0].text;
    assert.ok(text.includes(`Next step: call pixel_ods_workspace_preview_inspect directly with exactly these args, your own steps with each locator nested: ${JSON.stringify(corrected)}.`),text);
    assert.doesNotMatch(text,/tool_describe|tool_call/);
  }
  assert.equal(calls,0);
});

test('no plan is offered as ready unless nesting alone makes it valid',()=>{
  const valid={...STRIXY_SITE,steps:[hidden,click,shown]};
  for(const params of [
    valid, // nothing to correct
    {}, null, // laptop round 107 call 14 sent no arguments
    {...valid,steps:[{action:'assert-hidden',selector:'#midnight-card',locator:{selector:'#other'}}]}, // ambiguous
    {...valid,steps:[{action:'assert-hidden',selector:'a >> b'}]}, // still an invalid locator
    {...valid,steps:[{action:'hover',selector:'#midnight-card'}]}, // unsupported action
    {...valid,steps:[{action:'click',role:'button',name:'Show sold out',exact:false}]}, // not exact
    {...valid,steps:[{action:'assert-hidden',selector:'#midnight-card'}],url:'https://example.org'}, // extra field
    {...valid,siteId:'site-'+'b'.repeat(24),steps:[{action:'assert-hidden',selector:'#midnight-card'}]}, // wrong binding
  ]) assert.equal(correctedInspectionArgs(params),undefined,JSON.stringify(params));
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

import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams as normalize,
  validateWorkspacePreviewInspectionReceipt as validate, inspectionPlanHash,
  INSPECTION_KIND, INSPECTION_SCOPE, SELECT_INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';

const params = (steps=[{action:'select-option',locator:{selector:'#customer'},value:'Lia'}]) => ({
  siteId:'site-'+ 'a'.repeat(24), sha256:'a'.repeat(64), viewport:{width:800,height:600}, steps});
const state = value => ({count:1,visible:true,display:'inline-block',visibility:'visible',opacity:'1',
  hidden:false,hiddenUntilFound:false,rectCount:1,selection:{native:true,multiple:false,disabled:false,
    optionCount:1,optionDisabled:false,value,truncated:false}});
function receipt(request) {
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:request.sha256,
    planSha256:inspectionPlanHash(request),viewport:request.viewport,
    steps:[{index:0,...request.steps[0],before:state(''),after:state('Lia'),stable:true,status:'passed'}],
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:SELECT_INSPECTION_SCOPE};
}
test('exact bounded values including empty and unicode; no commands or extra fields',()=>{
  for(const value of ['', 'Lia', 'São Paulo', 'x'.repeat(256)]) normalize(params([{...params().steps[0],value}]));
  for(const value of [null,true,3,[], 'x'.repeat(257),'\n','\u0000','\u2028'])
    assert.throws(()=>normalize(params([{...params().steps[0],value}])));
  assert.throws(()=>normalize(params([{...params().steps[0],script:'arbitrary()'}])));
  assert.throws(()=>normalize(params([{...params().steps[0],action:'click'}])));
});
test('selection pass binds exact value, isolated observations, new scope and plan',()=>{
  const request=normalize(params()); const good=receipt(request); assert.equal(validate(good,request),good);
  const mutations=[r=>r.steps[0].value='Hugo',r=>r.steps[0].after.selection.value='Hugo',
    r=>delete r.steps[0].after,r=>r.steps[0].before.visible=false,r=>r.steps[0].after.selection.optionCount=2,
    r=>r.steps[0].after.selection.multiple=true,r=>r.steps[0].before.selection.disabled=true,
    r=>r.steps[0].after.selection.truncated=true,r=>r.scope=INSPECTION_SCOPE,r=>r.planSha256='b'.repeat(64)];
  for(const mutate of mutations){const bad=structuredClone(good);mutate(bad);assert.throws(()=>validate(bad,request));}
});
test('old capsule capability failure is unverified and does not prescribe redesign',async()=>{
  const request=normalize(params()); const result=receipt(request);
  for(const key of ['viewport','steps','diagnostics','blockedRequests']) delete result[key];
  result.status='failed';result.errorCode='unsupported_capability';
  const tool=createWorkspacePreviewInspectTool({request:async()=>result});
  const output=await tool.execute('test',params());
  assert.equal(output.isError,true);assert.match(output.content[0].text,/Update the ODS inspector/);
  assert.match(output.content[0].text,/Keep the published select unchanged/);
});
test('blocked download remains failure with an inspector limitation, not a product diagnosis',async()=>{
  const p=params([{action:'click',locator:{selector:'#download'}}]);const request=normalize(p);
  const result=receipt(request);result.scope=INSPECTION_SCOPE;result.status='failed';result.blockedRequests=['download'];
  const before=state('');delete before.selection;
  result.steps=[{index:0,...request.steps[0],before,stable:true,status:'failed',errorCode:'click_failed'}];
  const output=await createWorkspacePreviewInspectTool({request:async()=>result}).execute('test',p);
  assert.equal(output.isError,true);assert.equal(output.details.status,'failed');
  assert.match(output.content[0].text,/inspection limitation, not evidence/);
  assert.match(output.content[0].text,/do not repeat the same blocked click/);
});
test('select alone never satisfies existing show-hide requirement',async()=>{
  const request=normalize(params());
  const output=await createWorkspacePreviewInspectTool({request:async()=>receipt(request),
    transitionRequirement:()=>({initiallyHidden:true,target:'details'})}).execute('test',params());
  assert.equal(output.details.status,'incomplete');assert.equal(output.details.errorCode,'transition_untested');
});

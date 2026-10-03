import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeWorkspacePreviewInspectionParams as normalize, validateWorkspacePreviewInspectionReceipt as validate,
  inspectionPlanHash, INSPECTION_KIND, INSPECTION_SCOPE, hasVisibilityTransitionPlan} from '../plugin/workspace-preview-inspect.mjs';
const digest='a'.repeat(64);
const args={siteId:`site-${digest.slice(0,24)}`,sha256:digest,viewport:{width:800,height:600},
  steps:[{action:'assert-text',locator:{selector:'#count'},expectedText:'1'}]};
const receipt=request=>({schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:digest,
  planSha256:inspectionPlanHash(request),viewport:request.viewport,scope:INSPECTION_SCOPE,
  diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],
  steps:[{index:0,...request.steps[0],stable:true,status:'passed',before:{count:1,visible:true,display:'block',
    visibility:'visible',opacity:'1',hidden:false,hiddenUntilFound:false,rectCount:1,text:{actual:'1',truncated:false}}}]});
test('text assertions are bound to expected text and actual visible bounded observations',()=>{
  const request=normalize(args);assert.equal(validate(receipt(request),request).status,'passed');
  for(const change of [s=>s.expectedText='2',s=>s.before.text.actual='2',s=>s.before.text.truncated=true,
    s=>s.before.visible=false,s=>delete s.before.text]) {
    const forged=receipt(request);change(forged.steps[0]);assert.throws(()=>validate(forged,request));
  }
  for(const expectedText of ['', 'x'.repeat(257),' 1','1\n2','1\0'])
    assert.throws(()=>normalize({...args,steps:[{...args.steps[0],expectedText}]}));
  assert.throws(()=>normalize({...args,steps:[{...args.steps[0],action:'click'}]}));
  assert.throws(()=>normalize({...args,steps:[{action:'assert-text',locator:{selector:'#count'}}]}));
});
test('text assertions cannot substitute for a requested visibility transition',()=>{
  assert.equal(hasVisibilityTransitionPlan({steps:[args.steps[0],{action:'click',locator:{selector:'#add'}},
    {action:'assert-visible',locator:{selector:'#count'}}]}),false);
});

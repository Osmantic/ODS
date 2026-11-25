import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams as normalize,
  validateWorkspacePreviewInspectionReceipt as validate, inspectionPlanHash,
  INSPECTION_KIND, INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';
const request=normalize({siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:800,height:600},steps:[{action:'assert-visible',locator:{selector:'h1'}}]});
const receipt=()=>({schemaVersion:1,kind:INSPECTION_KIND,status:'failed',siteId:request.siteId,sha256:request.sha256,planSha256:inspectionPlanHash(request),viewport:request.viewport,
 steps:[{index:0,...request.steps[0],before:{count:1,visible:true,display:'block',visibility:'visible',opacity:'1',hidden:false,hiddenUntilFound:false,rectCount:1},stable:true,status:'passed'}],
 diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE,
 resourceErrors:[{type:'stylesheet',path:'/assets/app.css',reason:'http',status:404}]});
test('missing required resources override passing DOM steps and reach the model',async()=>{
 const value=receipt();assert.equal(validate(value,request),value);
 const result=await createWorkspacePreviewInspectTool({request:async()=>value}).execute('test',{siteId:request.siteId,sha256:request.sha256,viewport:request.viewport,steps:request.steps});
 assert.equal(result.isError,true);assert.equal(result.details.status,'failed');
 assert.match(result.content[0].text,/stylesheet resources failed/);
 assert.match(result.content[0].text,/visible heading does not verify/);
});
test('resource evidence is bounded and cannot endorse a pass',()=>{
 for(const mutate of [v=>v.status='passed',v=>v.resourceErrors=[],v=>v.resourceErrors=Array(17).fill(v.resourceErrors[0]),
 v=>v.resourceErrors[0].type='image',v=>v.resourceErrors[0].status=200,v=>v.resourceErrors[0].path='x'.repeat(257),v=>v.resourceErrors[0].secret='bad']) {
  const bad=receipt();mutate(bad);assert.throws(()=>validate(bad,request));
 }
 const old=receipt();delete old.resourceErrors;old.status='passed';assert.equal(validate(old,request),old);
});

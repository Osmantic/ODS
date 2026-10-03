import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createWorkspacePreviewInspectTool,normalizeWorkspacePreviewInspectionParams as normalize,validateWorkspacePreviewInspectionReceipt as validate,inspectionPlanHash,INSPECTION_KIND,INSPECTION_SCOPE,FILL_INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';
const params=value=>({siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:800,height:600},steps:[{action:'fill',locator:{selector:'#task'},value}]});
const state=matches=>({count:1,visible:true,display:'inline-block',visibility:'visible',opacity:'1',hidden:false,hiddenUntilFound:false,rectCount:1,input:{eligible:true,disabled:false,readOnly:false,matches}});
const receipt=request=>({schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:request.sha256,planSha256:inspectionPlanHash(request),viewport:request.viewport,steps:[{index:0,...request.steps[0],before:state(false),after:state(true),stable:true,status:'passed'}],diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE+FILL_INSPECTION_SCOPE});
test('fill accepts only exact bounded printable synthetic input',()=>{
 for(const v of ['', 'QA após compactação','é'.repeat(256)]) normalize(params(v));
 for(const v of [null,3,true,{},'x'.repeat(257),'a\nb','\u0000','\u202e']) assert.throws(()=>normalize(params(v)));
 assert.throws(()=>normalize({...params('x'),steps:[{...params('x').steps[0],script:'alert(1)'}]}));
});
test('fill receipt binds value, eligibility, both observations, scope and plan',()=>{
 const request=normalize(params('QA'));const good=receipt(request);assert.equal(validate(good,request),good);
 for(const mutate of [r=>r.steps[0].value='different',r=>delete r.steps[0].after,r=>r.steps[0].after.input.matches=false,r=>r.steps[0].after.input.eligible=false,r=>r.steps[0].before.input.disabled=true,r=>r.steps[0].before.input.readOnly=true,r=>r.steps[0].before.visible=false,r=>r.steps[0].after.input.previous='secret',r=>r.scope=INSPECTION_SCOPE,r=>r.planSha256='b'.repeat(64)]){
  const bad=structuredClone(good);mutate(bad);assert.throws(()=>validate(bad,request));
 }
});
test('old image fill failure is actionable without suggesting a website rewrite',async()=>{
 const request=normalize(params('QA'));const result=receipt(request);
 for(const key of ['viewport','steps','diagnostics','blockedRequests']) delete result[key];
 result.status='failed';result.errorCode='unsupported_capability';
 const output=await createWorkspacePreviewInspectTool({request:async()=>result}).execute('fill',params('QA'));
 assert.equal(output.isError,true);assert.match(output.content[0].text,/text\/number-field filling/);assert.match(output.content[0].text,/Update the ODS inspector/);
});
test('sensitive field refusal stays unverified with no original value',async()=>{
 const request=normalize(params('QA'));const result=receipt(request);result.status='failed';result.steps[0].status='failed';result.steps[0].errorCode='text_field_required';result.steps[0].before.input.eligible=false;delete result.steps[0].after;
 const output=await createWorkspacePreviewInspectTool({request:async()=>result}).execute('fill',params('QA'));
 assert.equal(output.isError,true);assert.match(output.content[0].text,/synthetic/);assert.match(output.content[0].text,/not verified/);
});

test('numeric receipts expose only bounded constraint flags and cannot forge a fill',()=>{
 const request=normalize(params('0')),good=receipt(request);
 const numeric={syntaxValid:true,valueMissing:false,rangeUnderflow:true,rangeOverflow:false,stepMismatch:false,badInput:false};
 good.steps[0].before.input.numeric={...numeric,rangeUnderflow:false};
 good.steps[0].after.input.numeric=numeric;
 assert.equal(validate(good,request).status,'passed','constraint-invalid synthetic values are intentional test inputs');
 for(const change of [r=>r.steps[0].after.input.numeric.syntaxValid=false,
   r=>r.steps[0].after.input.numeric.badInput='false',r=>r.steps[0].after.input.numeric.previousValue='secret',
   r=>delete r.steps[0].after.input.numeric.rangeUnderflow]){
  const bad=structuredClone(good);change(bad);assert.throws(()=>validate(bad,request));
 }
});

test('invalid number syntax has actionable same-snapshot feedback',async()=>{
 const request=normalize(params('Infinity')),result=receipt(request);
 result.status='failed';result.steps[0].status='failed';result.steps[0].errorCode='numeric_value_required';delete result.steps[0].after;
 result.steps[0].before.input.numeric={syntaxValid:false,valueMissing:false,rangeUnderflow:false,rangeOverflow:false,stepMismatch:false,badInput:false};
 const output=await createWorkspacePreviewInspectTool({request:async()=>result}).execute('fill',params('Infinity'));
 assert.equal(output.isError,true);assert.match(output.content[0].text,/finite decimal/);assert.match(output.content[0].text,/same snapshot/);
});

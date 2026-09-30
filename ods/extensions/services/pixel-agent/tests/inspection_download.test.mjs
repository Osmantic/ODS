import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams as normalize,
  validateWorkspacePreviewInspectionReceipt as validate, inspectionPlanHash,
  INSPECTION_KIND, INSPECTION_SCOPE, DOWNLOAD_INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';

const download = {action:'download',locator:{selector:'#download'},path:'delivery/report.pdf',expectedBytes:100,expectedSha256:'b'.repeat(64)};
const params = (steps=[download]) => ({siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:800,height:600},steps});
function receipt(request) {
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:request.sha256,
    planSha256:inspectionPlanHash(request),viewport:request.viewport,steps:[{index:0,...request.steps[0],
      before:{count:1,visible:true,display:'inline',visibility:'visible',opacity:'1',hidden:false,hiddenUntilFound:false,rectCount:1},
      stable:true,status:'passed',download:{bytes:100,sha256:'b'.repeat(64),completed:true,eventCount:1,trustedClick:true}}],
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE+DOWNLOAD_INSPECTION_SCOPE};
}

test('download is a single final step with bounded immutable file identity',()=>{
  normalize(params());
  normalize(params([{...download,path:'report.ZIP',expectedBytes:4194304}]));
  for(const change of [{path:'../a.pdf'},{path:'/a.pdf'},{path:'a.pdf?x'},{path:'https://x/a.pdf'},
    {path:'a.js'},{path:'a\\b.pdf'},{expectedBytes:0},{expectedBytes:4194305},{expectedBytes:true},
    {expectedSha256:'A'.repeat(64)},{url:'https://x'}]) assert.throws(()=>normalize(params([{...download,...change}])));
  assert.throws(()=>normalize(params([download,download])));
  assert.throws(()=>normalize(params([download,{action:'click',locator:{selector:'#x'}}])));
});

test('receipt requires actual completed bytes bound to the request, never HEAD-only or filename proof',()=>{
  const request=normalize(params()), good=receipt(request);
  assert.equal(validate(good,request),good);
  for(const change of [r=>delete r.steps[0].download,r=>r.steps[0].download.bytes=101,
    r=>r.steps[0].download.sha256='c'.repeat(64),r=>r.steps[0].download.completed=false,
    r=>r.steps[0].download.eventCount=2,r=>delete r.steps[0].download.trustedClick,r=>r.steps[0].download.trustedClick=false,r=>r.steps[0].download.filename='report.pdf',
    r=>r.steps[0].path='other.pdf',r=>r.steps[0].expectedBytes=101,
    r=>r.steps[0].before.visible=false,r=>r.scope=INSPECTION_SCOPE,
    r=>r.planSha256='c'.repeat(64),r=>r.blockedRequests.push('download')]) {
    const bad=structuredClone(good);change(bad);assert.throws(()=>validate(bad,request));
  }
});

test('old capsule is unverified; success states capsule-only scope',async()=>{
  const request=normalize(params()), good=receipt(request);
  const success=await createWorkspacePreviewInspectTool({request:async()=>good}).execute('id',params());
  assert.equal(success.details.status,'passed');
  assert.match(success.content[0].text,/No file was exported to the user computer/);
  const old={schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode:'unsupported_capability',
    siteId:request.siteId,sha256:request.sha256,planSha256:inspectionPlanHash(request),scope:good.scope};
  const result=await createWorkspacePreviewInspectTool({request:async()=>old}).execute('id',params());
  assert.equal(result.isError,true);assert.match(result.content[0].text,/download behavior remains unverified/);
});

test('tool schema advertises the file identity and explicit action',()=>{
  const schema=createWorkspacePreviewInspectTool().parameters.properties.steps.items.properties;
  assert.ok(schema.action.enum.includes('download'));
  assert.equal(schema.expectedBytes.maximum,4194304);
  assert.equal(schema.expectedSha256.pattern,'^[a-f0-9]{64}$');
});

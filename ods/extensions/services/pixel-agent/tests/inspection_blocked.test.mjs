import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams as normalize,
  validateWorkspacePreviewInspectionReceipt as validate, inspectionPlanHash, INSPECTION_KIND, INSPECTION_SCOPE}
  from '../plugin/workspace-preview-inspect.mjs';
const params = {siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:375,height:812},
  steps:[{action:'assert-text',locator:{selector:'h1'},expectedText:'Ready'}]};
function failure(code='request_blocked') { return {schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode:code,
  siteId:params.siteId,sha256:params.sha256,planSha256:inspectionPlanHash(normalize(params)),scope:INSPECTION_SCOPE}; }
test('bound policy refusal offers snapshot repair without claiming service failure or widening access', async()=>{
  const result=await createWorkspacePreviewInspectTool({request:async()=>failure()}).execute('blocked',params);
  assert.equal(result.isError,true); assert.equal(result.details.errorCode,'request_blocked');
  assert.match(result.content[0].text,/assets relative/); assert.match(result.content[0].text,/rebuild and republish/);
  assert.match(result.content[0].text,/Do not broaden network access/);
  assert.match(result.content[0].text,/does not establish that the inspection runtime is unavailable/);
});
test('policy evidence cannot add URLs, host paths, reasons or change bound identity',()=>{
  const request=normalize(params);
  for (const delta of [{url:'https://example.invalid/token'},{path:'/home/private'},{reason:'secret'},
    {siteId:'site-'+'b'.repeat(24)},{planSha256:'b'.repeat(64)},{status:'passed'}]) {
    assert.throws(()=>validate({...failure(),...delta},request));
  }
});
test('real transport outage still reports unavailable',async()=>{
  const result=await createWorkspacePreviewInspectTool({request:async()=>{throw Error('private path');}}).execute('outage',params);
  assert.equal(result.details.errorCode,'unavailable'); assert.doesNotMatch(result.content[0].text,/private path|assets relative/);
});

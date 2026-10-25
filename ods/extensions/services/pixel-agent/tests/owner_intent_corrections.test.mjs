import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {userMessageRequestsWorkspaceDocumentDelivery as delivery} from '../plugin/tool-loop-guard.mjs';

function fixture(t,intent) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-owner-intent-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  return (tool,params)=>routePlaygroundTool({state:{},root,session:'owner-intent',intent,tool,params});
}

test('explicit parent directory operands preserve child locations without a correction',t=>{
  const base='Create a Python project in a new folder totals-project.';
  const parent='/workspace/.openclaw/tmp/totals-project';
  for(const operand of [parent,parent+'/',parent.slice('/workspace/'.length),parent.replaceAll('/','\\')]) {
    const call=fixture(t,`${base} Also use exactly ${operand}.`);
    const write={path:`${parent}/src/totals.py`,content:'print(42)'};
    const original=structuredClone(write);
    assert.equal(call('write',write),undefined,operand);
    assert.deepEqual(write,original);
    assert.equal(call('exec',{command:'python3 totals.py',workdir:`${parent}/src`}),undefined,operand);
  }
  for(const operand of [parent+'-extra','not-'+parent,'/workspace/other/totals-project']) {
    const call=fixture(t,`${base} Also use exactly ${operand}.`);
    assert.equal(call('write',{path:`${parent}/totals.py`,content:'x'}).block,true,operand);
  }
});

test('a prohibition on adding files suppresses the inferred directory correction',t=>{
  for(const refusal of ["but don't add files there",'do not create files there','but never write anything there','with no files']) {
    const call=fixture(t,`Create a Python project in a new folder totals-project, ${refusal}.`);
    const args={path:'/workspace/.openclaw/tmp/totals-project/totals.py',content:'x'};
    assert.equal(call('write',args),undefined,refusal);
    assert.equal(args.path,'/workspace/.openclaw/tmp/totals-project/totals.py');
  }
});

test('blanket file handoff refusals retain ordinary modifiers and file objects',()=>{
  for(const refusal of ['Do not send me anything at all.','Please do not send me anything at all.',
    'Do not send me any project files.','Do not attach the source files, please.',
    'Do not send me anything at all, please.','Do not provide the project files.']) {
    assert.equal(delivery([],`Give me the project files. ${refusal}`),false,refusal);
  }
  assert.equal(delivery([],'Do not attach old.zip; give me current.zip.'),true);
  assert.equal(delivery([],'Give me the project files. Ask Alice not to send me anything.'),true);
  assert.equal(delivery([],'Create report.md. Then attach it.'),true);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {registerProjectBuild} from '../plugin/project-registration.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';

test('project tool is absent without an installed service configuration', () => {
  let calls = 0;
  registerProjectBuild({registerTool: () => calls++});
  assert.equal(calls, 0);
});

test('only Portal Pixel sessions receive the configured capability', () => {
  let factory;
  registerProjectBuild({pluginConfig: {projectBuildSocket: '/run/project/control.sock'},
    on() {},
    registerTool: (create, options) => {factory = create; assert.deepEqual(options.names, ['pixel_ods_project_build']);}});
  assert.equal(factory({agentId: 'other', sessionKey: 'session'}), null);
  assert.equal(factory({agentId: 'pixel', sessionKey: 'unknown'}), null);
  assert.equal(factory({agentId: 'pixel', sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64)}).name,
    'pixel_ods_project_build');
});

test('installed capability guides the owner to managed builds without granting authority', () => {
  let hook;
  registerProjectBuild({pluginConfig: {projectBuildSocket: '/run/project/control.sock'},
    registerTool() {}, on(name, callback) {assert.equal(name, 'before_prompt_build'); hook = callback;}});
  assert.equal(hook({}, {agentId:'other'}), undefined);
  assert.equal(hook({}, {agentId:'pixel',sessionKey:'unknown'}), undefined);
  const result = hook({}, {agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)});
  assert.match(result.appendSystemContext, /pixel_ods_project_build/);
  assert.match(result.appendSystemContext, /tool_search\/tool_describe/);
  assert.match(result.appendSystemContext, /not a permission grant/);
  assert.match(result.appendSystemContext, /do not resubmit/);
  assert.match(result.appendSystemContext, /Python 3.11/);
  assert.match(result.appendSystemContext, /verified PyPI wheel SHA-256/);
  assert.match(result.appendSystemContext, /even when the conversational sandbox lacks pip/);
  assert.match(result.appendSystemContext, /action capabilities and runtime python/);
  assert.match(result.appendSystemContext, /never the host or conversational sandbox architecture/);
  assert.match(result.appendSystemContext, /do not claim the lock is portable/);
});

test('real malformed model submits are actionable without a job or an admission bypass', async () => {
  const control=createProjectRunControl();
  const scope={agentId:'pixel',sessionId:'session-a',runId:'run-a',
    sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
  let factory, sent=0;
  const guarded={...control,bind:(id,params,context)=>control.bind(id,params,context,async()=>{
    sent++; return {schemaVersion:1,kind:'ods-project-job',status:'invalid-request'};
  })};
  registerProjectBuild({pluginConfig:{projectBuildSocket:'/not-contacted.sock'},on(){},
    registerTool:value=>{factory=value;}},undefined,guarded);
  const tool=factory(scope);
  const invalid={action:'submit',runtime:'npm',project:'Playground/ods-qa-site-final-20260930-1456',
    outputDirectory:'Playground/ods-qa-site-final-20260930-1456/dist'};
  control.before({toolName:'tool_call',params:{id:tool.name,args:invalid}}, {...scope,toolCallId:'bad'});
  const first=await tool.execute('tool_search_code:bad:pixel_ods_project_build:1',invalid);
  assert.equal(first.details.status,'invalid-request');
  assert.equal(first.details.field,'runtime');
  assert.equal(first.details.executionStarted,false);
  assert.match(first.details.hint,/inferred/);
  const {runtime,...badPath}=invalid;
  const second=await tool.execute('unadmitted-bad',badPath);
  assert.equal(second.details.field,'outputDirectory');
  assert.match(second.details.hint,/basename/);
  assert.equal(sent,0);
  assert.equal(await control.cancel(scope),true); // No unknown job was created.
  const valid={...badPath,outputDirectory:'dist'};
  await assert.rejects(tool.execute('unadmitted-good',valid),/not bound/);
  control.before({toolName:'tool_call',params:{id:tool.name,args:valid}}, {...scope,toolCallId:'good'});
  const retry=await tool.execute('tool_search_code:good:pixel_ods_project_build:1',valid);
  assert.equal(retry.details.status,'invalid-request'); // Real controller denial is preserved.
  assert.equal(sent,1);
  assert.equal(await control.cancel(scope),true);
});

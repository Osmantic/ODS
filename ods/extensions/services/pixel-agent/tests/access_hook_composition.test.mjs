import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createWorkspaceArtifactAdmission,normalizeWorkspaceArtifact} from '../plugin/workspace-artifact.mjs';
import {createWorkspaceBundleAdmission} from '../plugin/workspace-bundle.mjs';
import {createLayaBatchAdmission} from '../plugin/laya-batch.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';
import {withCronCommandPayloadBlock} from '../plugin/cron-command-payload-guard.mjs';
import {withPixelSubagentWorkspace} from '../plugin/subagent-workspace.mjs';

// Exercise the actual registration callbacks without importing the installed
// OpenClaw SDK. This is a source composition fixture, not gateway qualification.
const source = fs.readFileSync(process.env.PIXEL_PLUGIN_ENTRY ??
  new URL('../plugin/index.js', import.meta.url), 'utf8');
const start = source.indexOf('    if (!managedRuntime) {');
const end = source.indexOf('    api.registerHttpRoute(', start);
assert.ok(start >= 0 && end > start, 'expected tool lifecycle registration block');
function hooks(guardResult, managedRuntime = false, delivery = {}) {
  const callbacks = {}, calls = [], activity = [], bundleAdmission = createWorkspaceBundleAdmission(), artifactAdmission = createWorkspaceArtifactAdmission();
  const lifecycleCalls = [], warnings = [];
  const conversationImageLifecycle = {observe() {lifecycleCalls.push('image');}};
  const projectRunControl = createProjectRunControl();
  const runtime = {
    isProbe: context => context?.runId === 'private-proof',
    beforeTool: () => { calls.push('admit'); },
    afterTool: () => { calls.push('finish'); },
    admit: () => { calls.push('run-admit'); },
    finish: () => { calls.push('run-finish'); },
  };
  vm.runInNewContext(source.slice(start, end), {
    api: {on: (name, callback) => { callbacks[name] = callback; }, logger: {warn: message => warnings.push(message)}},
    toolLoopGuard: {
      beforeToolCall: () => { calls.push('guard'); return guardResult; },
      afterToolCall: () => { calls.push('observe'); },
      endPreviewRevalidation() {},
      observeAgentEnd() {},
    },
    taskActivity: {
      before: (_event, _context, blocked) => activity.push(blocked ? 'blocked' : 'before'),
      after: () => activity.push('after'),
      finish: () => activity.push('finish'),
    },
    goalProgress: {before() {}, update() {}, finish() {}},
    conversationImageLifecycle,
    bundleAdmission, artifactAdmission, layaBatchAdmission:createLayaBatchAdmission(), projectRunControl, managedRuntime, accessRuntime: runtime, withPixelCronDeliveryDefault, withCronCommandPayloadBlock,
    delegationDelivery:{end(){lifecycleCalls.push('delegation');},blocked(){},before(){},after(){},admission(){},...delivery},
    withPixelSubagentWorkspace, resolveUserPath: value=>value,
    resolveAgentWorkspaceDir:config=>config?.agents?.list?.find(agent=>agent.id==='pixel')?.workspace,
    AGENT_ID: 'pixel',
  });
  return {callbacks, calls, runtime, activity, bundleAdmission, artifactAdmission, projectRunControl,
    lifecycleCalls, conversationImageLifecycle, warnings};
}
const context = {agentId: 'pixel', runId: 'cron-request', toolName: 'cron'};
const event = {toolCallId: 'cron-1', toolName: 'cron', params: {
  action: 'add', payload: {kind: 'agentTurn', message: 'Check disk space'},
}};

test('native tracking preserves the committed cron delivery repair', async () => {
  const {callbacks, calls, activity} = hooks();
  const result = await callbacks.before_tool_call(event, context);
  assert.equal(result?.params?.delivery?.mode, 'none');
  assert.deepEqual(calls, ['guard', 'admit']);
  assert.deepEqual(activity, ['before']);
  assert.equal(event.params.delivery, undefined);
});

test('guard denials never acquire a native tool slot', async () => {
  const denied = {block: true, blockReason: 'owner authorization missing'};
  const {callbacks, calls, activity} = hooks(denied);
  assert.equal(await callbacks.before_tool_call(event, context), denied);
  assert.deepEqual(calls, ['guard']);
  assert.deepEqual(activity, ['blocked']);
});

test('guard rewritten cron params survive admission and receive the default', async () => {
  const params = {...event.params, name: 'guard-approved'};
  const {callbacks} = hooks({params});
  const result = await callbacks.before_tool_call(event, context);
  assert.equal(result.params.name, 'guard-approved');
  assert.equal(result.params.delivery.mode, 'none');
  assert.equal(params.delivery, undefined);
});

test('agent cron calls cannot create command jobs through the actual hook', async () => {
  const {callbacks, calls, activity} = hooks();
  const command = {...event, params: {action: 'add', payload: {kind: 'Command', argv: ['id']}}};
  const result = await callbacks.before_tool_call(command, context);
  assert.equal(result?.block, true);
  assert.match(result.blockReason, /cannot run OS commands/);
  // Refused before native admission, like any other guard denial.
  assert.deepEqual(calls, ['guard']);
  assert.deepEqual(activity, ['blocked']);
});

test('held native gate wins over allowed or rewritten cron requests', async () => {
  const {callbacks, runtime, activity} = hooks();
  const denied = {block: true, blockReason: 'transition held'};
  runtime.beforeTool = () => denied;
  assert.equal(await callbacks.before_tool_call(event, context), denied);
  assert.deepEqual(activity, ['blocked']);
});

test('result observations and internal proof behavior remain composed', async () => {
  const {callbacks, calls, activity} = hooks();
  callbacks.after_tool_call(event, context);
  assert.deepEqual(calls, ['finish', 'observe']);
  assert.deepEqual(activity, ['after']);
  calls.length = 0;
  activity.length = 0;
  await callbacks.before_tool_call(event, {...context, runId: 'private-proof'});
  assert.deepEqual(calls, []);
  assert.deepEqual(activity, []);
});

for (const managed of [false, true]) {
  test(`cancel fence blocks before inference without acquiring another native slot (managed=${managed})`, () => {
    const denied={outcome:'block',reason:'ods-delegation-interrupted'};
    const {callbacks,calls}=hooks(undefined,managed,{admission:()=>denied});
    assert.equal(callbacks.before_agent_run(event,context),denied);
    assert.deepEqual(calls,[]);
  });
  test(`agent activity ends without duplicate managed admission/cleanup (managed=${managed})`, () => {
    const {callbacks, calls, activity, lifecycleCalls} = hooks(undefined, managed);
    assert.equal(typeof callbacks.before_agent_run, 'function');
    callbacks.before_agent_run?.(event, context);
    callbacks.agent_end(event, context);
    assert.deepEqual(calls, managed ? [] : ['run-admit', 'run-finish']);
    assert.deepEqual(activity, ['finish']);
    assert.deepEqual(lifecycleCalls, ['delegation', 'image'], 'both merged terminal observers must run');
    activity.length = 0;
    callbacks.agent_end(event, {...context, runId: 'private-proof'});
    assert.deepEqual(activity, [], 'private proofs must not create workbench activity');
  });
  test(`image custody failure preserves delegated terminal delivery and admission cleanup (managed=${managed})`, () => {
    const {callbacks, calls, activity, lifecycleCalls, conversationImageLifecycle, warnings} = hooks(undefined, managed);
    conversationImageLifecycle.observe = () => {throw new Error('private image custody path');};
    callbacks.agent_end(event, context);
    assert.deepEqual(lifecycleCalls, ['delegation']);
    assert.deepEqual(calls, managed ? [] : ['run-finish']);
    assert.deepEqual(activity, ['finish']);
    assert.deepEqual(warnings, ['Portal conversation image custody could not be updated.']);
  });
}

test('cancelled announcement is fenced before actual prompt hook registers guard or workbench activity',async()=>{
  const begin=source.indexOf('    api.on("before_prompt_build",');
  const finish=source.indexOf('    api.on("model_call_started",',begin);
  assert.ok(begin>=0 && finish>begin);
  let callback;const calls=[];
  vm.runInNewContext(source.slice(begin,finish),{
    api:{config:{},on:(_name,fn)=>{callback=fn;}},AGENT_ID:'pixel',
    privateBrowserAccessForAgent:()=>false,executionHostForAgent:()=> 'gateway',
    toolLoopGuard:{observeRun:()=>calls.push('guard'),ownerIntentEventForRun:(_id,e)=>e,promptContextForRun:()=>undefined,verificationStatus:()=>undefined},
    accessRuntime:{isProbe:()=>false},delegationDelivery:{admission:()=>({outcome:'block'}),observe:()=>calls.push('observe'),promptContext:()=>undefined},
    goalProgress:{begin:()=>calls.push('goal'),active:()=>false},taskActivity:{begin:()=>calls.push('activity')},
    promptContractForAgent:()=>undefined,configuredContextWindow:32768,configuredLeanPrompt:true,
  });
  await callback({prompt:'late child event'},context);
  assert.deepEqual(calls,[],'a late announcement cannot replace the active owner or start workbench activity');
});


test('bundle scope is recorded only after guard and native admission both permit the call',async()=>{
  const name='pixel_ods_workspace_bundle';
  const args={outputRoot:'project/bundles',mappingPath:'map.json',files:[{source:'project/a.py',key:'a',copyTo:'project/a.txt'}]};
  const ctx={agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'key',toolCallId:'bundle'};
  const event={toolName:name,toolCallId:'bundle',params:args};
  for(const held of [false,true]) {
    const {callbacks,runtime,bundleAdmission}=hooks();
    if(held)runtime.beforeTool=()=>({block:true});
    await callbacks.before_tool_call(event,ctx);
    if(held)assert.throws(()=>bundleAdmission.take('bundle',args,ctx),/unbound/);
    else assert.deepEqual(bundleAdmission.take('bundle',args,ctx),ctx);
    callbacks.after_tool_call(event,ctx);
    assert.throws(()=>bundleAdmission.take('bundle',args,ctx),/unbound/);
  }
});


test('document receipt scope requires both guard and native admission and ends with the actual hook',async()=>{
  const args={relativePath:'project/report.pdf'},payload=normalizeWorkspaceArtifact(args);
  const ctx={agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'key',toolCallId:'artifact'};
  const event={toolName:'pixel_ods_workspace_artifact',toolCallId:'artifact',params:args};
  for(const deniedBy of ['none','guard','native']) {
    const {callbacks,runtime,artifactAdmission}=hooks(deniedBy==='guard'?{block:true}:undefined);
    if(deniedBy==='native')runtime.beforeTool=()=>({block:true});
    await callbacks.before_tool_call(event,ctx);
    if(deniedBy==='none')assert.deepEqual(artifactAdmission.take('artifact',payload,ctx),ctx);
    else assert.throws(()=>artifactAdmission.take('artifact',payload,ctx),/unbound/);
    callbacks.after_tool_call(event,ctx);
    assert.throws(()=>artifactAdmission.take('artifact',payload,ctx),/unbound/);
  }
});
test('project job binding follows actual admission and cannot survive denied or completed calls',async()=>{
  const args={action:'submit',project:'demo',outputDirectory:'dist'};
  const ctx={agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'key',toolCallId:'project'};
  const event={toolName:'pixel_ods_project_build',toolCallId:'project',params:args};
  for(const held of [false,true]) {
    const {callbacks,runtime,projectRunControl}=hooks();
    if(held)runtime.beforeTool=()=>({block:true});
    await callbacks.before_tool_call(event,ctx);
    if(held)assert.throws(()=>projectRunControl.bind('project',args,ctx,()=>{}),/not bound/);
    else assert.equal(typeof projectRunControl.bind('project',args,ctx,()=>{}),'function');
    callbacks.after_tool_call(event,ctx);
    assert.throws(()=>projectRunControl.bind('project',args,ctx,()=>{}),/not bound/);
  }
});

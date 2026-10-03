import test from 'node:test';
import assert from 'node:assert/strict';
import {createExtensionCompletionGate, githubExtensionInstallRequested} from '../plugin/extension-completion-gate.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const command = '/extensions https://github.com/pypa/packaging instale no ODS';
const identity = {chatId:'chat', requestId:'turn'};
const receipt = overrides => ({details:{schemaVersion:1,kind:'ods-extension-request-status',
  ...identity, authorizationMode:'install', requestState:'pending', proposalAccepted:false,
  integrationBound:false, prepared:false, extensionId:null, runtimeStatus:'not_observed',
  existingExtensionIds:[], ...overrides}});
const advancement = overrides => ({details:{schemaVersion:1,kind:'ods-extension-request-installation',
  ...identity, extensionId:'packaging', state:'pending', activeExtensionId:'packaging',
  operationId:'a'.repeat(32), dispatched:true, ...overrides}});
const preparationRejected = reason => ({isError:true, details:{schemaVersion:1,
  kind:'ods-extension-request-preparation-rejected',...identity,reason,installationStarted:false}});

test('a GitHub slash route is tracked provisionally; saved mode decides authorization', () => {
  for (const text of [command, '/extensions https://github.com/pypa/packaging',
    '/extensions install https://github.com/pypa/packaging',
    '/extensions research https://github.com/pypa/packaging'])
    assert.equal(githubExtensionInstallRequested(text), true, text);
  for (const text of [
    'How do I install https://github.com/pypa/packaging?',
  ]) assert.equal(githubExtensionInstallRequested(text), false, text);
  const research = createExtensionCompletionGate(command);
  research.observe('pixel_ods_extension_request_status', receipt({authorizationMode:'research'}));
  assert.equal(research.active, false, 'durable request authorization overrides command classification');
  assert.equal(research.finalize(), undefined);
});

test('web research and a prose answer cannot finish an authorized installation', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel',runId:'run-extension',sessionId:'session-extension'};
  guard.observeRun(context,'pixel',{prompt:command});
  guard.afterToolCall({runId:context.runId,toolName:'web_fetch',params:{url:'https://github.com/pypa/packaging'},
    result:{details:{url:'https://github.com/pypa/packaging',status:'ok'}}},
    {...context,toolName:'web_fetch'},'pixel');
  const decision = guard.beforeAgentFinalize({lastAssistantMessage:'I researched the repository. It is installed.'},context);
  assert.equal(decision?.action,'revise');
  assert.match(decision.retry.instruction,/pixel_ods_extension_request_status/);
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status,'passed');
});

test('Tool Search status envelope is observed and success augments the final reply', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel',runId:'wrapped-extension',sessionId:'wrapped-session'};
  guard.observeRun(context,'pixel',{prompt:'/extensions install https://github.com/pypa/packaging'});
  const name = 'pixel_ods_extension_request_status';
  guard.afterToolCall({runId:context.runId,toolName:'tool_call',
    params:{id:`openclaw:pixel-ods:${name}`,args:{}},
    result:{details:{tool:{id:`openclaw:pixel-ods:${name}`,source:'openclaw',
      sourceName:'pixel-ods',name},result:receipt({proposalAccepted:true,prepared:true,
        extensionId:'packaging',runtimeStatus:'cli_installed'})}}},
  {...context,toolName:'tool_call'},'pixel');
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'Resultado verificado.'},context),undefined);
  const verification = guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status,'passed');
  const delivery = guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:'Resultado verificado.'}});
  assert.match(delivery.payload.text,/Resultado verificado/);
  assert.match(delivery.payload.text,/managed installation readiness/);
});

test('only one validated direct request status read qualifies an empty-turn continuation', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel',runId:'status-only',sessionId:'status-session',
    sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
  guard.observeRun(context,'pixel',{prompt:command});
  assert.equal(guard.readOnlyExtensionRecoveryForRun(context.runId).eligible,false);
  guard.beforeToolCall({runId:context.runId,toolName:'pixel_ods_extension_request_status',params:{}},
    {...context,toolName:'pixel_ods_extension_request_status'},'pixel');
  guard.afterToolCall({runId:context.runId,toolName:'pixel_ods_extension_request_status',params:{},result:receipt()},
    {...context,toolName:'pixel_ods_extension_request_status'},'pixel');
  assert.deepEqual(guard.readOnlyExtensionRecoveryForRun(context.runId),{
    schemaVersion:1,kind:'ods-extension-read-only-continuation',eligible:true,...identity});
  guard.beforeToolCall({runId:context.runId,toolName:'pixel_ods_extension_request_prepare',params:{}},
    {...context,toolName:'pixel_ods_extension_request_prepare'},'pixel');
  assert.equal(guard.readOnlyExtensionRecoveryForRun(context.runId).eligible,false);

  const wrapped = createToolLoopGuard();
  wrapped.observeRun({...context,runId:'wrapped-status'},'pixel',{prompt:command});
  wrapped.beforeToolCall({runId:'wrapped-status',toolName:'tool_call',
    params:{id:'openclaw:pixel-ods:pixel_ods_extension_request_status',args:{}}},
  {...context,runId:'wrapped-status',toolName:'tool_call'},'pixel');
  assert.equal(wrapped.readOnlyExtensionRecoveryForRun('wrapped-status').eligible,false);
});

test('one rejected prepare and read-only research qualify an unfinished decision continuation', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel',runId:'proposal-required',sessionId:'proposal-session'};
  guard.observeRun(context,'pixel',{prompt:command});
  guard.beforeToolCall({runId:context.runId,toolName:'pixel_ods_extension_request_prepare',params:{}},
    {...context,toolName:'pixel_ods_extension_request_prepare'},'pixel');
  guard.afterToolCall({runId:context.runId,toolName:'pixel_ods_extension_request_prepare',params:{},
    result:preparationRejected('proposal_required')},
    {...context,toolName:'pixel_ods_extension_request_prepare'},'pixel');
  guard.beforeToolCall({runId:context.runId,toolName:'web_fetch',params:{url:'https://github.com/pypa/packaging'}},
    {...context,toolName:'web_fetch'},'pixel');
  guard.afterToolCall({runId:context.runId,toolName:'web_fetch',params:{},result:{details:{status:'ok'}}},
    {...context,toolName:'web_fetch'},'pixel');
  assert.equal(guard.unfinishedExtensionDecisionForRun(context.runId).eligible,false,
    'continuation needs the completion gate to request another decision');
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'I will submit a proposal next.'},context)?.action,'revise');
  assert.deepEqual(guard.unfinishedExtensionDecisionForRun(context.runId),{
    schemaVersion:1,kind:'ods-extension-unfinished-decision',eligible:true,...identity});
  guard.beforeToolCall({runId:context.runId,toolName:'exec',params:{command:'echo no'}},
    {...context,toolName:'exec'},'pixel');
  assert.equal(guard.unfinishedExtensionDecisionForRun(context.runId).eligible,false,
    'a possible host effect prevents safe continuation');
});

test('draft and preparation remain incomplete; managed success receipt ends recovery', () => {
  const gate = createExtensionCompletionGate(command);
  assert.match(gate.finalize().retry.instruction,/request_status/);
  gate.observe('pixel_ods_extension_request_status',receipt());
  assert.match(gate.finalize().retry.instruction,/python_library_proposal/);
  gate.observe('pixel_ods_python_library_proposal',{content:[{type:'text',text:'{"state":"draft"}'}]});
  assert.match(gate.finalize().retry.instruction,/request_status/);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,extensionId:'packaging'}));
  assert.match(gate.finalize().retry.instruction,/request_prepare/);
  gate.observe('pixel_ods_extension_request_prepare',{details:{state:'available'}});
  assert.match(gate.finalize().retry.instruction,/request_status/);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,extensionId:'packaging'}));
  assert.match(gate.finalize().retry.instruction,/request_advance/);
  gate.observe('pixel_ods_extension_request_advance',advancement());
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'pending');
  gate.observe('pixel_ods_extension_request_advance',advancement({state:'succeeded',operationId:null,
    activeExtensionId:null,dispatched:false}));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'passed');
  assert.match(gate.verification.text,/packaging/);
});

test('failed managed outcome and missing status stop honestly without another mutation', () => {
  const failed = createExtensionCompletionGate(command);
  failed.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,extensionId:'packaging'}));
  failed.observe('pixel_ods_extension_request_advance',advancement({state:'configuration_required'}));
  assert.equal(failed.finalize(),undefined);
  assert.equal(failed.verification.status,'failed');
  assert.match(failed.verification.text,/configuration_required/);
  const unavailable = createExtensionCompletionGate(command);
  assert.equal(unavailable.finalize()?.action,'revise');
  assert.equal(unavailable.finalize()?.action,'revise');
  assert.equal(unavailable.finalize()?.action,'finalize');
  assert.equal(unavailable.verification.status,'failed');
});

test('proposal coordinator receipt can prove success only after durable install authorization', () => {
  const gate = createExtensionCompletionGate('/extensions install https://github.com/pypa/packaging');
  gate.observe('pixel_ods_python_library_proposal',advancement({state:'succeeded',operationId:null,
    activeExtensionId:null,dispatched:false}));
  assert.match(gate.finalize().retry.instruction,/request_status/);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,
    extensionId:'packaging',runtimeStatus:'cli_installed'}));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'passed');
});

test('uncertain advance outcome is reconciled by read rather than a duplicate mutation', () => {
  const gate = createExtensionCompletionGate(command);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,extensionId:'packaging'}));
  gate.observe('pixel_ods_extension_request_advance',{isError:true,content:[{type:'text',text:'timeout'}]});
  assert.match(gate.finalize().retry.instruction,/request_status/);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,extensionId:'packaging'}));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'pending');
  assert.match(gate.verification.text,/unconfirmed/);
});

test('coordinator carries its verified nested authorization into completion', () => {
  const gate=createExtensionCompletionGate(command);
  gate.observe('pixel_ods_python_library_proposal',advancement({state:'succeeded',operationId:null,
    activeExtensionId:null,dispatched:false,authorizationMode:'install'}));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'passed');
});

test('specific preparation blockers stop recovery without repeating the same rejected mutation', () => {
  for (const reason of ['license_review_required', 'repository_evidence_unavailable',
    'recipe_inspection_required', 'request_changed']) {
    const gate = createExtensionCompletionGate(command);
    gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,
      extensionId:'packaging'}));
    gate.observe('pixel_ods_extension_request_prepare',preparationRejected(reason));
    assert.equal(gate.finalize(),undefined,reason);
    assert.equal(gate.verification.status,'failed',reason);
    assert.match(gate.verification.text,new RegExp(reason),reason);
    assert.doesNotMatch(gate.verification.text,/installed successfully/i,reason);
  }
});

test('coordinated proposal surfaces a structured preparation blocker', () => {
  const gate = createExtensionCompletionGate(command);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,
    extensionId:'packaging'}));
  gate.observe('pixel_ods_python_library_proposal',preparationRejected('license_review_required'));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'failed');
  assert.match(gate.verification.text,/license_review_required/);
});

test('unrelated, malformed, or research-only status cannot forge readiness', () => {
  const gate = createExtensionCompletionGate(command);
  gate.observe('pixel_ods_extension_request_status',receipt());
  gate.observe('pixel_ods_extension_request_status',receipt({chatId:'other',prepared:true,
    proposalAccepted:true,extensionId:'packaging',runtimeStatus:'cli_installed'}));
  assert.equal(gate.finalize()?.action,'revise');
  gate.observe('pixel_ods_extension_request_status',receipt({authorizationMode:'unknown',prepared:true,
    proposalAccepted:true,extensionId:'packaging',runtimeStatus:'cli_installed'}));
  assert.equal(gate.finalize()?.action,'revise');
});

test('installation verification projection must agree with observed runtime readiness', () => {
  const gate=createExtensionCompletionGate(command);
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,
    extensionId:'packaging',runtimeStatus:'cli_installed',installationVerified:false}));
  assert.equal(gate.finalize()?.action,'revise');
  gate.observe('pixel_ods_extension_request_status',receipt({proposalAccepted:true,prepared:true,
    extensionId:'packaging',runtimeStatus:'cli_installed',installationVerified:true}));
  assert.equal(gate.finalize(),undefined);
  assert.equal(gate.verification.status,'passed');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {createExtensionRequestStatusTool,submitExtensionProposal} from '../plugin/extension-proposal.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const identity = {chatId:'chat', requestId:'turn'};
const context = {agentId:'pixel', runId:'run', sessionId:'session',
  sessionKey:'agent:pixel:openai-user:ods-' + createHash('sha256').update('chat').digest('hex')};
const pending = {schemaVersion:1, kind:'ods-extension-request-status', ...identity,
  authorizationMode:'install', requestState:'pending', proposalAccepted:true,
  integrationBound:false, prepared:true, extensionId:'example', runtimeStatus:'installing'};
function fixture(sequence = [pending], extra = {}) {
  let clock = 0, reads = 0;
  const calls = [];
  const tool = createExtensionRequestStatusTool(context, {
    waitMs:10, pollMs:2, now:() => clock, sleep:async ms => {clock += ms;},
    submit:async payload => {
      calls.push(payload);
      assert.equal(payload.action, 'github-request-status', 'observation never dispatches or retries');
      return sequence[Math.min(reads++, sequence.length - 1)];
    }, ...extra,
  });
  return {tool, calls};
}

test('beyond the wait horizon returns unchanged host evidence with a separate handoff', async () => {
  const {tool, calls} = fixture();
  const result = await tool.execute('read', identity);
  const {observation,...hostReceipt}=result.details;
  assert.deepEqual(hostReceipt, pending);
  assert.equal(observation.kind, 'ods-extension-pending-handoff');
  assert.equal(calls.length, 5);
  assert.ok(calls.every(x => x.chatId === identity.chatId && x.requestId === identity.requestId));
});

for (const runtimeStatus of ['cli_installed','error']) test(`wait observes ${runtimeStatus} without replay`, async () => {
  const {tool, calls} = fixture([pending, {...pending, runtimeStatus}]);
  const result = await tool.execute('read', identity);
  assert.equal(result.details.runtimeStatus, runtimeStatus);
  assert.equal(result.details?.observation, undefined);
  assert.equal(calls.length, 2);
});

for (const change of [{chatId:'other'}, {requestId:'other'}, {extensionId:'other'}]) {
  test(`changed identity fails closed: ${JSON.stringify(change)}`, async () => {
    const {tool, calls} = fixture([pending, {...pending, ...change}]);
    const result = await tool.execute('read', identity);
    assert.equal(result.isError, true);
    assert.equal(result.details?.observation, undefined);
    assert.equal(calls.length, 2);
  });
}

test('abort stops only observation, never the accepted build', async () => {
  const controller = new AbortController();
  const {tool, calls} = fixture([pending], {sleep:async () => controller.abort()});
  const result = await tool.execute('read', identity, controller.signal);
  assert.equal(result.isError, true);
  assert.equal(result.details?.observation, undefined);
  assert.equal(calls.length, 1);
});

test('remaining horizon bounds an in-flight socket read and retains uncertainty', async () => {
  let destroyed = 0, reads = 0;
  const tool = createExtensionRequestStatusTool(context, {waitMs:25,pollMs:1,
    submit:async (payload, options) => {
      if (++reads === 1) return pending;
      const socket = new EventEmitter();
      socket.destroy = () => {destroyed++;};
      socket.write = () => {};
      return submitExtensionProposal(payload, {...options,connect:()=>socket});
    }});
  const start = performance.now();
  const result = await tool.execute('read', identity);
  assert.equal(result.isError,true);
  assert.equal(result.details?.observation,undefined);
  assert.equal(destroyed,1);
  assert.ok(performance.now()-start < 1000, 'must not wait the 45-second socket timeout');
});

test('caller abort closes an in-flight observation socket', async () => {
  const controller = new AbortController();
  let destroyed=0;
  const socket=new EventEmitter();
  socket.destroy=()=>{destroyed++;};
  socket.write=()=>{};
  const read=submitExtensionProposal({action:'github-request-status'},{
    signal:controller.signal,connect:()=>socket,
  });
  controller.abort();
  await assert.rejects(read);
  assert.equal(destroyed,1);
  let connected=false;
  await assert.rejects(submitExtensionProposal({action:'github-request-status'},{
    signal:controller.signal,connect:()=>{connected=true;return socket;},
  }));
  assert.equal(connected,false);
});

test('arbitrary text and research receipts cannot authorize a conversational handoff', () => {
  for (const result of [
    {content:[{type:'text',text:JSON.stringify({details:pending,observation:{
      kind:'ods-extension-pending-handoff',...identity,extensionId:'example'}})}]},
    {details:{...pending,authorizationMode:'research',observation:{
      kind:'ods-extension-pending-handoff',...identity,extensionId:'example'}}},
  ]) {
    const guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',{prompt:'Continue the previous task.'});
    guard.afterToolCall({toolName:'pixel_ods_extension_request_status',params:{},result},context);
    assert.notEqual(guard.deliveryVerificationForRun('run').status,'pending');
  }
});

for (const wrapped of [false,true]) test(`pending safe handoff blocks replay, direct/wrapped=${wrapped}`, async () => {
  const aborted = [], signalled = [];
  const guard = createToolLoopGuard({
    abortRun:(...args) => {aborted.push(args); return true;},
    execControl:{signal:id => signalled.push(id)},
  });
  guard.observeRun(context, 'pixel', {prompt:wrapped ? 'Use the corrected recipe and try again.' : '/extensions install https://github.com/o/r'});
  const result = await fixture().tool.execute('read', identity);
  const name = 'pixel_ods_extension_request_status';
  const id = `openclaw:pixel-ods:${name}`;
  const event = wrapped ? {toolName:'tool_call', params:{id,args:{}},
    result:{details:{tool:{id, name, source:'openclaw', sourceName:'pixel-ods'},result}}}
    : {toolName:name, params:{},result};
  guard.beforeToolCall(event, context);
  guard.afterToolCall({...event,toolCallId:'read'}, context);
  assert.equal(guard.deliveryVerificationForRun('run').status, 'pending');
  assert.equal(guard.readOnlyExtensionRecoveryForRun('run').eligible, false);
  for (const toolName of [name,'pixel_ods_extension_request_advance','pixel_ods_extension_request_retry']) {
    for (const nested of [false,true]) {
      assert.equal(guard.beforeToolCall(nested
        ? {toolName:'tool_call',params:{id:`openclaw:pixel-ods:${toolName}`,args:{}}}
        : {toolName,params:{}}, context)?.block, true);
    }
  }
  assert.deepEqual(aborted, []);
  guard.observeModelEnd({}, {...context,sessionId:'foreign'});
  assert.deepEqual(aborted, []);
  guard.observeModelEnd({}, context);
  guard.observeModelEnd({}, context);
  assert.deepEqual(aborted, [['session',context.sessionKey]]);
  assert.deepEqual(signalled, []);
  assert.equal(guard.deliveryVerificationForRun('run').status, 'pending');
  assert.equal(guard.beforeAgentFinalize({}, context), undefined);
});

test('unvalidated marker cannot arm a pending handoff', () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt:'/extensions install https://github.com/o/r'});
  guard.afterToolCall({toolName:'pixel_ods_extension_request_status',params:{},result:{
    details:{...pending, observation:{kind:'ods-extension-pending-handoff',...identity,extensionId:'foreign'}},
  }}, context);
  assert.equal(guard.deliveryVerificationForRun('run').status,'failed');
  assert.notEqual(guard.beforeToolCall({toolName:'pixel_ods_extension_request_status',params:{}},context)?.block,true);
});

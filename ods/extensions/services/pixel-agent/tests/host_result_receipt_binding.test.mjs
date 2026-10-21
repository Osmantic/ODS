import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createToolLoopGuard} = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

const OBSERVE = 'pixel_ods_host_observe';
const COMMAND = 'pixel_ods_host_command_propose';
const A = 'ops-1234567890123-aaaaaaaaaaaa';
const B = 'ops-1234567890123-bbbbbbbbbbbb';
const text = message => (message?.content ?? []).filter(b => b.type === 'text').map(b => b.text).join('\n');
const wrap = (name, result) => ({content: [{type:'text', text:JSON.stringify(result)}], details: {
  tool: {id:`openclaw:pixel-ods:${name}`, source:'openclaw', sourceName:'pixel-ods', name}, result,
}});
function receipt(jobId, action, stdout, status = 'succeeded') {
  return {content:[{type:'text', text:`raw receipt ${jobId}`}], details:{jobId, status, waitTimedOut:false,
    steps:[{stepId:'observe-1', target:'ods-host', action, stdout, stderr:'',
      exitCode:status === 'succeeded' ? 0 : 1,
      outputTruncated:{stdout:false, stderr:false}, riskSignals:[]}]}};
}
function fixture(prompt = 'Check the ODS host hostname.') {
  const guard = createToolLoopGuard();
  const ctx = {agentId:'pixel', runId:'host-receipts', sessionId:'host-session', sessionKey:'host-key'};
  guard.observeRun(ctx, 'pixel', {prompt});
  function prepare(id, params, wrapped, selected = OBSERVE) {
    const toolName = wrapped ? 'tool_call' : selected;
    const context = {...ctx, toolCallId:id, toolName};
    let args = wrapped ? {id:selected, args:params} : params;
    const event = {runId:ctx.runId, toolCallId:id, toolName, params:args};
    const before = guard.beforeToolCall(event, context);
    assert.notEqual(before?.block, true, before?.blockReason);
    args = before?.params ?? args;
    return {id, selected, toolName, wrapped, context, event:{...event, params:args}};
  }
  function after(call, result, eventChanges = {}, contextChanges = {}) {
    const transported = call.wrapped ? wrap(call.selected, result) : result;
    guard.afterToolCall({...call.event, result:transported, ...eventChanges}, {...call.context, ...contextChanges});
    return transported;
  }
  function persist(call, result, changes = {}) {
    const message = {role:'toolResult', toolCallId:call.id, toolName:call.toolName, ...result, ...changes.message};
    const persisted = guard.toolResultPersist({toolCallId:call.id, toolName:call.toolName,
      message, ...changes.event}, {...call.context, ...changes.context});
    return persisted?.message ?? message;
  }
  return {guard, ctx, prepare, after, persist};
}

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'native';
  test(`${transport}: each observation shows its own receipt, including an additional action`, () => {
    const f = fixture();
    const a = f.prepare('first', {actions:['host.identity']}, wrapped);
    const first = f.persist(a, f.after(a, receipt(A, 'host.identity', 'first-host\n')));
    assert.match(text(first), /Hostname: `first-host`/);
    const b = f.prepare('second', {actions:['host.kernel']}, wrapped);
    const second = f.persist(b, f.after(b, receipt(B, 'host.kernel', 'kernel-second\n')));
    assert.match(text(second), /Kernel: `kernel-second`/);
    assert.match(text(second), new RegExp(B));
    assert.doesNotMatch(text(second), new RegExp(A));
    assert.doesNotMatch(text(second), /first-host/);
    assert.equal(f.guard.verificationForRun(f.ctx.runId).status, 'passed');
  });

  test(`${transport}: delayed persistence retains the exact call, while owner completion accumulates`, () => {
    const f = fixture('Check the ODS host hostname and kernel.');
    const a = f.prepare('first', {actions:['host.identity']}, wrapped);
    const resultA = f.after(a, receipt(A, 'host.identity', 'first-host\n'));
    assert.equal(f.guard.verificationForRun(f.ctx.runId).status, 'failed');
    const b = f.prepare('second', {actions:['host.kernel']}, wrapped);
    const resultB = f.after(b, receipt(B, 'host.kernel', 'kernel-second\n'));
    assert.equal(f.guard.verificationForRun(f.ctx.runId).status, 'passed');
    const second = text(f.persist(b, resultB));
    const first = text(f.persist(a, resultA));
    assert.match(second, /kernel-second/);
    assert.doesNotMatch(second, /first-host/);
    assert.match(first, /first-host/);
    assert.doesNotMatch(first, /kernel-second/);
  });

  test(`${transport}: a failed second job cannot reuse successful first-job evidence`, () => {
    const f = fixture();
    const a = f.prepare('first', {actions:['host.identity']}, wrapped);
    f.persist(a, f.after(a, receipt(A, 'host.identity', 'first-host\n')));
    const b = f.prepare('second', {actions:['host.kernel']}, wrapped);
    const result = text(f.persist(b, f.after(b, receipt(B, 'host.kernel', '', 'failed'))));
    assert.match(result, /terminal status failed/);
    assert.match(result, new RegExp(B));
    assert.doesNotMatch(result, /first-host/);
    assert.doesNotMatch(result, new RegExp(A));
  });

  for (const mismatch of ['job', 'stdout', 'run', 'session', 'session-key', 'call', 'error', 'inner-error', 'no-after']) {
    test(`${transport}: ${mismatch} mismatch does not substitute captured host evidence`, () => {
      const f = fixture();
      const a = f.prepare('first', {actions:['host.identity']}, wrapped);
      let result = mismatch === 'no-after'
        ? (wrapped ? wrap(OBSERVE, receipt(A, 'host.identity', 'first-host\n')) : receipt(A, 'host.identity', 'first-host\n'))
        : f.after(a, receipt(A, 'host.identity', 'first-host\n'));
      result = structuredClone(result);
      const changes = {};
      const details = wrapped ? result.details.result.details : result.details;
      if (mismatch === 'job') details.jobId = B;
      if (mismatch === 'stdout') details.steps[0].stdout = 'different-host\n';
      if (mismatch === 'run') changes.context = {runId:'other-run'};
      if (mismatch === 'session') changes.context = {sessionId:'other-session'};
      if (mismatch === 'session-key') changes.context = {sessionKey:'other-key'};
      if (mismatch === 'call') changes.message = {toolCallId:'other-call'};
      if (mismatch === 'error') changes.message = {isError:true};
      if (mismatch === 'inner-error') (wrapped ? result.details.result : result).isError = true;
      assert.doesNotMatch(text(f.persist(a, result, changes)), /Receipt custody:/);
    });
  }

  test(`${transport}: after-hook parameters must match the admitted call snapshot`, () => {
    const f = fixture();
    const params = {actions:['host.identity']};
    const a = f.prepare('first', params, wrapped);
    params.actions[0] = 'host.kernel';
    const result = f.after(a, receipt(A, 'host.kernel', 'kernel-second\n'));
    assert.doesNotMatch(text(f.persist(a, result)), /Receipt custody:/);
  });

  test(`${transport}: peer receipts describe their own ports without completing missing owner ports`, () => {
    const f = fixture('Probe Strixy on the local network and report whether it is reachable.');
    const ports = [22];
    const call = f.prepare('peer', {actions:['host.network-peer'], peer:'Strixy', ports}, wrapped);
    const result = receipt(A, 'host.network-peer', JSON.stringify({
      schemaVersion:1, kind:'ods-host-network-peer', target:'Strixy', ports,
      resolved:true, reachable:true, addresses:[{address:'192.168.0.50', family:'ipv4', scope:'lan',
        icmpReachable:true, tcp:[{port:22, open:false}]}],
      tailscale:{available:true, found:false, online:null, addresses:[]},
    }) + '\n');
    const projected = text(f.persist(call, f.after(call, result)));
    assert.match(projected, /Private network peer `Strixy`/);
    assert.match(projected, /Receipt custody:/);
    assert.equal(f.guard.verificationForRun(f.ctx.runId).status, 'failed');
  });

  test(`${transport}: raw command evidence remains bound to its own externally approved plan`, () => {
    const f = fixture('Run the command `id` on the actual ODS host.');
    const call = f.prepare('command', {command:'id'}, wrapped, COMMAND);
    const result = receipt(A, 'raw-shell', 'uid=1000(michael)\n');
    Object.assign(result.details, {approvalRequired:true, planHash:'a'.repeat(64)});
    result.details.steps[0].durationSeconds = 0.01;
    const projected = text(f.persist(call, f.after(call, result)));
    assert.match(projected, /uid=1000/);
    assert.match(projected, /external owner approval/);
    assert.match(projected, /Receipt custody:/);
  });
}

test('Tool Search child and parent hooks preserve their own receipt through folding', () => {
  const f = fixture();
  const parent = f.prepare('outer', {actions:['host.identity']}, true);
  const child = f.prepare('tool_search_code:outer:pixel_ods_host_observe:1', {actions:['host.identity']}, false);
  const result = receipt(A, 'host.identity', 'first-host\n');
  f.after(child, result);
  const projected = text(f.persist(parent, f.after(parent, result)));
  assert.match(projected, /Hostname: `first-host`/);
  assert.match(projected, /Receipt custody:/);
});

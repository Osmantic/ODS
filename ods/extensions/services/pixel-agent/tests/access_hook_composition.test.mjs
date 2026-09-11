import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';

// Exercise the actual registration callbacks without importing the installed
// OpenClaw SDK. This is a source composition fixture, not gateway qualification.
const source = fs.readFileSync(process.env.PIXEL_PLUGIN_ENTRY ??
  new URL('../plugin/index.js', import.meta.url), 'utf8');
const start = source.indexOf('    if (!managedRuntime) {');
const end = source.indexOf('    api.registerHttpRoute(', start);
assert.ok(start >= 0 && end > start, 'expected tool lifecycle registration block');
function hooks(guardResult, managedRuntime = false) {
  const callbacks = {}, calls = [], activity = [];
  const runtime = {
    isProbe: context => context?.runId === 'private-proof',
    beforeTool: () => { calls.push('admit'); },
    afterTool: () => { calls.push('finish'); },
    admit: () => { calls.push('run-admit'); },
    finish: () => { calls.push('run-finish'); },
  };
  vm.runInNewContext(source.slice(start, end), {
    api: {on: (name, callback) => { callbacks[name] = callback; }},
    toolLoopGuard: {
      beforeToolCall: () => { calls.push('guard'); return guardResult; },
      afterToolCall: () => { calls.push('observe'); },
    },
    taskActivity: {
      before: (_event, _context, blocked) => activity.push(blocked ? 'blocked' : 'before'),
      after: () => activity.push('after'),
      finish: () => activity.push('finish'),
    },
    managedRuntime, accessRuntime: runtime, withPixelCronDeliveryDefault, AGENT_ID: 'pixel',
  });
  return {callbacks, calls, runtime, activity};
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
  test(`agent activity ends without duplicate managed admission/cleanup (managed=${managed})`, () => {
    const {callbacks, calls, activity} = hooks(undefined, managed);
    assert.equal(typeof callbacks.before_agent_run, managed ? 'undefined' : 'function');
    callbacks.before_agent_run?.(event, context);
    callbacks.agent_end(event, context);
    assert.deepEqual(calls, managed ? [] : ['run-admit', 'run-finish']);
    assert.deepEqual(activity, ['finish']);
    activity.length = 0;
    callbacks.agent_end(event, {...context, runId: 'private-proof'});
    assert.deepEqual(activity, [], 'private proofs must not create workbench activity');
  });
}

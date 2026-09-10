import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';

// Exercise the actual registration callbacks without importing the installed
// OpenClaw SDK. This is a source composition fixture, not gateway qualification.
const source = fs.readFileSync(process.env.PIXEL_PLUGIN_ENTRY ??
  new URL('../plugin/index.js', import.meta.url), 'utf8');
const start = source.indexOf('    api.on("before_tool_call",');
const end = source.indexOf('    api.registerHttpRoute(', start);
assert.ok(start >= 0 && end > start, 'expected tool lifecycle registration block');
function hooks(guardResult) {
  const callbacks = {}, calls = [];
  const runtime = {
    isProbe: context => context?.runId === 'private-proof',
    beforeTool: () => { calls.push('admit'); },
    afterTool: () => { calls.push('finish'); },
  };
  vm.runInNewContext(source.slice(start, end), {
    api: {on: (name, callback) => { callbacks[name] = callback; }},
    toolLoopGuard: {
      beforeToolCall: () => { calls.push('guard'); return guardResult; },
      afterToolCall: () => { calls.push('observe'); },
    },
    accessRuntime: runtime, withPixelCronDeliveryDefault, AGENT_ID: 'pixel',
  });
  return {callbacks, calls, runtime};
}
const context = {agentId: 'pixel', runId: 'cron-request', toolName: 'cron'};
const event = {toolCallId: 'cron-1', toolName: 'cron', params: {
  action: 'add', payload: {kind: 'agentTurn', message: 'Check disk space'},
}};

test('native tracking preserves the committed cron delivery repair', async () => {
  const {callbacks, calls} = hooks();
  const result = await callbacks.before_tool_call(event, context);
  assert.equal(result?.params?.delivery?.mode, 'none');
  assert.deepEqual(calls, ['guard', 'admit']);
  assert.equal(event.params.delivery, undefined);
});

test('guard denials never acquire a native tool slot', async () => {
  const denied = {block: true, blockReason: 'owner authorization missing'};
  const {callbacks, calls} = hooks(denied);
  assert.equal(await callbacks.before_tool_call(event, context), denied);
  assert.deepEqual(calls, ['guard']);
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
  const {callbacks, runtime} = hooks();
  const denied = {block: true, blockReason: 'transition held'};
  runtime.beforeTool = () => denied;
  assert.equal(await callbacks.before_tool_call(event, context), denied);
});

test('result observations and internal proof behavior remain composed', async () => {
  const {callbacks, calls} = hooks();
  callbacks.after_tool_call(event, context);
  assert.deepEqual(calls, ['finish', 'observe']);
  calls.length = 0;
  await callbacks.before_tool_call(event, {...context, runId: 'private-proof'});
  assert.deepEqual(calls, []);
});

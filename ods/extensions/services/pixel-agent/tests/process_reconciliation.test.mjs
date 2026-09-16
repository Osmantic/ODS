import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';

const linux = {skip: process.platform !== 'linux'};
const context = {agentId: 'pixel', sessionKey: 'agent:pixel:owned', sessionId: 'conversation', runId: 'run'};
const terminal = (startedAt = 1000) => ({sessionId: 'job', startedAt, status: 'completed'});
function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-process-reconciliation-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  return createAccessRuntime({directory: path.join(root, 'state'), runtimeVersion: '2026.6.33', hooksAllowed: true, ...options});
}
function launch(runtime, startedAt = 1000) {
  runtime.admit({}, context);
  runtime.beforeTool({toolCallId: 'launch'}, context);
  runtime.afterTool({toolCallId: 'launch', toolName: 'exec', result: {details: {
    status: 'running', sessionId: 'job', startedAt,
  }}}, context);
  runtime.finish({}, context);
  assert.equal(runtime.status().active, 1);
}

test('a completed background job releases admission without a model poll', linux, async t => {
  let result = [{...terminal(), status: 'running'}];
  const runtime = fixture(t, {readProcessSessions: async scope => {
    assert.deepEqual(scope, {agentId: 'pixel', sessionKey: 'agent:pixel:owned', sessionId: 'conversation'});
    return result;
  }});
  launch(runtime);
  await runtime.reconcileDetached();
  assert.equal(runtime.status().active, 1);
  const revision = runtime.status().revision;
  result = [terminal()];
  await runtime.reconcileDetached();
  assert.equal(runtime.status().active, 0);
  assert.equal(runtime.status().phase, 'idle');
  assert.notEqual(runtime.status().revision, revision);
  assert.equal(runtime.acquire('a'.repeat(64), runtime.status().revision).phase, 'held');
});

test('unknown, malformed, expired, duplicate and reused identities remain held', linux, async t => {
  let result;
  const runtime = fixture(t, {readProcessSessions: async () => {
    if (result instanceof Error) throw result;
    return result;
  }});
  launch(runtime);
  for (result of [undefined, {}, [], [terminal(999)], [terminal(), terminal()],
    [{...terminal(), status: 'unknown'}], new Error('unavailable')]) {
    await runtime.reconcileDetached();
    assert.equal(runtime.status().active, 1);
    assert.throws(() => runtime.acquire('a'.repeat(64), runtime.status().revision), /busy/);
  }
  result = [terminal()];
  await runtime.reconcileDetached();
});

test('an in-flight result cannot delete a newer record and checks do not overlap', linux, async t => {
  let complete, calls = 0;
  const runtime = fixture(t, {readProcessSessions: () => { calls++; return new Promise(resolve => { complete = resolve; }); }});
  launch(runtime);
  const pending = runtime.reconcileDetached();
  assert.equal(runtime.reconcileDetached(), pending);
  launch(runtime, 2000);
  complete([terminal()]);
  await pending;
  assert.equal(calls, 1);
  assert.equal(runtime.status().active, 1);
  const next = runtime.reconcileDetached();
  complete([terminal(2000)]);
  await next;
  assert.equal(runtime.status().active, 0);
});

test('process-tool errors are not mistaken for terminal execution receipts', linux, async t => {
  const runtime = fixture(t);
  launch(runtime);
  runtime.afterTool({toolName: 'process', params: {sessionId: 'job'}, result: {details: {status: 'failed'}}}, context);
  assert.equal(runtime.status().active, 1);
  runtime.afterTool({toolName: 'process', params: {sessionId: 'job'}, result: {details: {status: 'completed', exitCode: 0}}}, context);
  assert.equal(runtime.status().active, 0);
});

test('the SDK adapter executes only scoped list and excludes its own hook activity', linux, async t => {
  let runtime, called = 0;
  runtime = fixture(t, {config: () => ({agents: {list: [{id: 'pixel', workspace: '/unused'}]}}),
    createTools: options => [{name: 'process', execute: async (id, args) => {
      assert.equal(options.sessionKey, context.sessionKey);
      assert.deepEqual(args, {action: 'list'});
      assert.equal(id, options.runId);
      assert.equal(runtime.isProbe(options), true);
      runtime.beforeTool({toolCallId: id}, options);
      runtime.afterTool({toolCallId: id, toolName: 'process'}, options);
      assert.equal(runtime.status().active, 1);
      called++;
      return {details: {status: 'completed', sessions: [terminal()]}};
    }}]});
  launch(runtime);
  await runtime.reconcileDetached();
  assert.equal(called, 1);
  assert.equal(runtime.status().active, 0);
});

test('a completing background job cannot clear an unrelated active run', linux, async t => {
  const runtime = fixture(t, {readProcessSessions: async () => [terminal()]});
  launch(runtime);
  runtime.admit({}, {...context, runId: 'other'});
  await runtime.reconcileDetached();
  assert.equal(runtime.status().active, 1);
  assert.equal(runtime.status().phase, 'busy');
  runtime.finish({}, {...context, runId: 'other'});
  assert.equal(runtime.status().phase, 'idle');
});

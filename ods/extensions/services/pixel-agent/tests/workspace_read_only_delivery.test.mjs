import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createToolLoopGuard,
  WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX,
  WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX,
} from '../plugin/tool-loop-guard.mjs';

const prompt = 'Make the synthetic service demo usable. Work only in a NEW folder new-copy. Copy finish-q4/index.html first, repair the form, and publish a preview.';

function setup() {
  const guard = createToolLoopGuard();
  const context = {
    agentId: 'pixel',
    runId: 'synthetic-read-run',
    sessionId: 'synthetic-session',
    sessionKey: 'synthetic-key',
  };
  guard.observeRun(context, 'pixel', {prompt});
  return {guard, context};
}

function tool({guard, context}, name, params, {error = false} = {}) {
  const id = `${name}-1`;
  const ctx = {...context, toolName: name, toolCallId: id};
  const prepared = guard.beforeToolCall({
    toolName: name, runId: context.runId, toolCallId: id, params,
  }, ctx);
  assert.notEqual(prepared?.block, true, prepared?.blockReason);
  guard.afterToolCall({
    toolName: name,
    runId: context.runId,
    toolCallId: id,
    params: prepared?.params ?? params,
    result: {
      isError: error,
      content: [{
        type: 'text',
        text: name === 'read' ? '<!doctype html><title>Existing sample</title>' : 'File write completed',
      }],
    },
  }, ctx);
}

test('reading the seed without creating the requested copy never claims files were saved', () => {
  const s = setup();
  tool(s, 'read', {path: 'finish-q4/index.html'});
  const result = s.guard.deliveryVerificationForRun(s.context.runId);
  assert.equal(result.status, 'failed');
  assert.equal(result.preview, undefined);
  assert.doesNotMatch(result.text, /Portal saved|didn't create/);
  assert.match(result.text, /read existing website files/);
  assert.match(result.text, /hasn't verified new or changed files/);
});

test('verified current write retains saved-files delivery', () => {
  const s = setup();
  tool(s, 'read', {path: 'finish-q4/index.html'});
  tool(s, 'write', {path: 'new-copy/index.html', content: '<!doctype html><title>New sample</title>'});
  const result = s.guard.deliveryVerificationForRun(s.context.runId);
  assert.equal(result.text, WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX);
  assert.equal(result.status, 'failed');
});

test('failed writes cannot turn a successful read into saved output', () => {
  const s = setup();
  tool(s, 'read', {path: 'finish-q4/index.html'});
  tool(s, 'write', {path: 'new-copy/index.html', content: '<!doctype html><title>New sample</title>'}, {error: true});
  const result = s.guard.deliveryVerificationForRun(s.context.runId);
  assert.doesNotMatch(result.text, /Portal saved/);
  assert.match(result.text, /read existing/);
  assert.equal(result.status, 'failed');
});

test('failed reads create no successful read or write claim', () => {
  const s = setup();
  tool(s, 'read', {path: 'finish-q4/index.html'}, {error: true});
  const result = s.guard.deliveryVerificationForRun(s.context.runId);
  assert.equal(result.text, WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX);
});

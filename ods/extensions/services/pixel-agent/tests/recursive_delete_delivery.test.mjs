// Focused tests for the recursive-delete-denied terminal delivery path.
// Uses the real createToolLoopGuard and native tool receipts through its
// public before/after hooks. No guard implementation is mocked.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {
  createToolLoopGuard,
  RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
  WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX,
  WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX,
} from '../plugin/tool-loop-guard.mjs';

function snapshot(relativeDirectory, writes) {
  const digest = createHash('sha256');
  let bytes = 0;
  const ordered = [...writes].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  for (const {path: fullPath, content} of ordered) {
    const rel = fullPath.slice(`${relativeDirectory}/`.length);
    const p = Buffer.from(rel, 'utf8');
    const c = Buffer.from(content, 'utf8');
    const pl = Buffer.alloc(4); pl.writeUInt32BE(p.length);
    const cl = Buffer.alloc(8); cl.writeBigUInt64BE(BigInt(c.length));
    digest.update(pl); digest.update(p); digest.update(cl); digest.update(c);
    bytes += c.length;
  }
  const sha256 = digest.digest('hex');
  const entry = writes.find(({path}) => path === `${relativeDirectory}/index.html`);
  return {
    siteId: `site-${sha256.slice(0, 24)}`,
    files: writes.length,
    bytes,
    sha256,
    entryFile: 'index.html',
    entrySha256: createHash('sha256').update(entry.content, 'utf8').digest('hex'),
  };
}

function previewDetails(relativeDirectory, writes, port = 9437) {
  const snap = snapshot(relativeDirectory, writes);
  return {
    schemaVersion: 1,
    kind: 'ods-pixel-workspace-preview',
    status: 'succeeded',
    relativeDirectory,
    ...snap,
    port,
    url: `http://${snap.siteId}.localhost:${port}/${snap.siteId}/`,
    httpStatus: 200,
    readbackVerified: true,
    executable: false,
    overwritten: false,
  };
}

function writeAndPublish(guard, context, relativeDirectory, content) {
  const write = {path: `${relativeDirectory}/index.html`, content};
  guard.beforeToolCall({toolName: 'write', params: write, toolCallId: 'w'}, context, 'pixel');
  guard.afterToolCall({toolName: 'write', toolCallId: 'w', params: write,
    result: {details: {status: 'completed'}}}, context, 'pixel');
  const details = previewDetails(relativeDirectory, [write]);
  const params = {relativeDirectory};
  guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: 'p'}, context, 'pixel');
  guard.afterToolCall({toolName: 'pixel_ods_workspace_preview', toolCallId: 'p', params,
    result: {details}}, context, 'pixel');
  return details;
}

function denyRecursiveDelete(guard, context) {
  const params = {command: 'rm -rf /workspace/site', workdir: '/workspace'};
  const blocked = guard.beforeToolCall({toolName: 'exec', params, toolCallId: 'd'}, context, 'pixel');
  assert.equal(blocked?.block, true);
  assert.equal(blocked.blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  return blocked;
}

test('verified publication then denied deletion keeps failed status and immutable preview', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
  guard.observeRun(context, 'pixel', {prompt: 'Build and publish a website.'});
  const details = writeAndPublish(guard, context, 'site-a', '<!doctype html><p>a</p>');
  assert.equal(guard.verificationForRun('run-1').status, 'passed');
  denyRecursiveDelete(guard, context);
  const delivery = guard.deliveryVerificationForRun('run-1');
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview?.kind, 'ods-pixel-workspace-preview');
  assert.equal(delivery.preview?.sha256, details.sha256);
  assert.equal(delivery.preview?.entrySha256, details.entrySha256);
  assert.equal(delivery.preview?.url, details.url);
  assert.match(delivery.text, /blocked/i);
  assert.match(delivery.text, /incomplete/i);
  assert.doesNotMatch(delivery.text, /Do not retry/);
  assert.doesNotMatch(delivery.text, /\bPixel\b/);
  assert.doesNotMatch(delivery.text, /Explain what was attempted/);
  assert.doesNotMatch(delivery.text, /cleanup ran/i);
});

test('prior publication invalidated by later successful mutation then denial', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-2', sessionId: 'session-2'};
  guard.observeRun(context, 'pixel', {prompt: 'Build and publish a website.'});
  const details = writeAndPublish(guard, context, 'site-b', '<!doctype html><p>b</p>');
  assert.equal(guard.verificationForRun('run-2').status, 'passed');
  const edit = {path: 'site-b/index.html', oldText: 'b', newText: 'b2'};
  guard.beforeToolCall({toolName: 'edit', params: edit, toolCallId: 'e'}, context, 'pixel');
  guard.afterToolCall({toolName: 'edit', toolCallId: 'e', params: edit,
    result: {details: {status: 'completed'}}}, context, 'pixel');
  assert.notEqual(guard.verificationForRun('run-2').status, 'passed');
  denyRecursiveDelete(guard, context);
  const delivery = guard.deliveryVerificationForRun('run-2');
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview?.sha256, details.sha256);
  assert.equal(delivery.preview?.url, details.url);
  assert.match(delivery.text, /last published preview/i);
  assert.match(delivery.text, /may not include subsequent changes/i);
  assert.doesNotMatch(delivery.text, /Do not retry/);
  assert.doesNotMatch(delivery.text, /\bPixel\b/);
});

test('no preview reports saved workspace files only when tracked, no URL', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-3', sessionId: 'session-3'};
  guard.observeRun(context, 'pixel', {prompt: 'Build a website.'});
  const write = {path: 'site-c/index.html', content: '<!doctype html><p>c</p>'};
  guard.beforeToolCall({toolName: 'write', params: write, toolCallId: 'w'}, context, 'pixel');
  guard.afterToolCall({toolName: 'write', toolCallId: 'w', params: write,
    result: {details: {status: 'completed'}}}, context, 'pixel');
  denyRecursiveDelete(guard, context);
  const delivery = guard.deliveryVerificationForRun('run-3');
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview, undefined);
  assert.match(delivery.text, /blocked/i);
  assert.match(delivery.text, /incomplete/i);
  assert.doesNotMatch(delivery.text, /http:\/\/localhost/);
  assert.doesNotMatch(delivery.text, /site was created/i);
  assert.doesNotMatch(delivery.text, /Do not retry/);
  assert.doesNotMatch(delivery.text, /\bPixel\b/);
});

test('no preview and no tracked files avoids claiming a site was created', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-4', sessionId: 'session-4'};
  guard.observeRun(context, 'pixel', {prompt: 'Build a website.'});
  denyRecursiveDelete(guard, context);
  const delivery = guard.deliveryVerificationForRun('run-4');
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview, undefined);
  assert.doesNotMatch(delivery.text, /http:\/\/localhost/);
  assert.doesNotMatch(delivery.text, /site was created/i);
  assert.doesNotMatch(delivery.text, /Do not retry/);
  assert.doesNotMatch(delivery.text, /\bPixel\b/);
});

test('malformed or untrusted preview receipts are ignored', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-5', sessionId: 'session-5'};
  guard.observeRun(context, 'pixel', {prompt: 'Build a website.'});
  const write = {path: 'site-d/index.html', content: '<!doctype html><p>d</p>'};
  guard.beforeToolCall({toolName: 'write', params: write, toolCallId: 'w'}, context, 'pixel');
  guard.afterToolCall({toolName: 'write', toolCallId: 'w', params: write,
    result: {details: {status: 'completed'}}}, context, 'pixel');
  const params = {relativeDirectory: 'site-d'};
  guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: 'p'}, context, 'pixel');
  guard.afterToolCall({toolName: 'pixel_ods_workspace_preview', toolCallId: 'p', params,
    result: {details: {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded',
      relativeDirectory: 'site-d', siteId: 'site-000000000000000000000000', port: 9437,
      url: 'http://site-000000000000000000000000.localhost:9437/site-000000000000000000000000/',
      files: 1, bytes: 1, sha256: 'not-a-hash', entrySha256: 'not-a-hash',
      entryFile: 'index.html', httpStatus: 200, readbackVerified: true,
      executable: false, overwritten: false}}}, context, 'pixel');
  assert.equal(guard.verificationForRun('run-5').status, 'failed');
  denyRecursiveDelete(guard, context);
  const delivery = guard.deliveryVerificationForRun('run-5');
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview, undefined);
  assert.doesNotMatch(delivery.text, /http:\/\/localhost/);
});

test('subsequent tools remain blocked after denial', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-6', sessionId: 'session-6'};
  guard.observeRun(context, 'pixel', {prompt: 'Build a website.'});
  denyRecursiveDelete(guard, context);
  for (const toolName of ['write', 'edit', 'exec', 'process', 'tool_search']) {
    const blocked = guard.beforeToolCall({toolName, params: {}, toolCallId: `t-${toolName}`}, context, 'pixel');
    assert.equal(blocked?.block, true, toolName);
    assert.equal(blocked.blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON, toolName);
  }
});

test('beforeAgentFinalize does not request continuation after denial', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-7', sessionId: 'session-7'};
  guard.observeRun(context, 'pixel', {prompt: 'Build a website.'});
  denyRecursiveDelete(guard, context);
  const decision = guard.beforeAgentFinalize({lastAssistantMessage: 'done'}, context, 'pixel');
  assert.equal(decision, undefined);
});

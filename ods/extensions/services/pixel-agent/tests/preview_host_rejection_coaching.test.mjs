// Regression for the coding-v3 Tower2 failure: a host-rejected publication
// arrived inside a Tool Search envelope (outer isError absent), was coached as
// a success, and every later hint and provenance block named the rejected
// directory again while the model wandered through renamed copies.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, WORKSPACE_PREVIEW_REQUIRES_FILES_REASON} from '../plugin/tool-loop-guard.mjs';

const BOUNDARY = 'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.';
const P = 'expense-coding';
const success = text => ({content: [{type: 'text', text}], details: {status: 'completed', exitCode: 0}});
function rejected(errorCode) {
  const result = {content: [{type: 'text', text: 'ODS could not publish the preview.'}], isError: true,
    details: {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'failed', errorCode, boundary: BOUNDARY}};
  const envelope = {tool: {id: 'openclaw:pixel-ods:pixel_ods_workspace_preview', source: 'openclaw',
    sourceName: 'pixel-ods', name: 'pixel_ods_workspace_preview'}, result};
  return {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope};
}
function fixture() {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'reject-run', sessionId: 'reject-session', sessionKey: 'agent:pixel:reject'};
  guard.observeRun(context, 'pixel', {prompt: `In a new workspace project ${P}, implement a Python CSV report CLI with unittest tests and run them. Then create a public directory with index.html and deliver ONLY public as the viewable artifact.`});
  let sequence = 0;
  return (toolName, params, result) => {
    const toolCallId = `reject-${++sequence}`, ctx = {...context, toolName, toolCallId};
    const prepared = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    if (prepared?.block) return {blocked: prepared.blockReason};
    guard.afterToolCall({toolName, params: prepared?.params ?? params, toolCallId, result}, ctx);
    const persisted = guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
    return {text: (persisted?.message?.content ?? result.content).map(part => part.text ?? '').join('\n')};
  };
}

for (const code of ['unsafe_directory', 'unsupported_file_type']) {
  test(`a wrapped ${code} rejection is not coached back to the rejected directory`, () => {
    const invoke = fixture();
    invoke('write', {path: `${P}/report.py`, content: 'print(1)\n'}, success('written'));
    const entry = invoke('write', {path: `${P}/index.html`, content: '<!doctype html><h1>Report</h1>'}, success('written'));
    assert.match(entry.text, /"relativeDirectory":"expense-coding"/);  // unchanged pre-failure coaching
    const failed = invoke('tool_call', {id: 'pixel_ods_workspace_preview', args: {relativeDirectory: P}}, rejected(code));
    assert.doesNotMatch(failed.text, /\[ODS Pixel next step\]/);
    const next = invoke('exec', {command: `cd /workspace/${P} && mkdir -p public && cp index.html public/`}, success(''));
    assert.doesNotMatch(next.text, /"relativeDirectory":"expense-coding"/);
    assert.match(next.text, /Prepare a browser-ready directory/);
    const early = invoke('tool_call', {id: 'pixel_ods_workspace_preview', args: {relativeDirectory: `${P}/public`}}, success('unused'));
    assert.equal(early.blocked, WORKSPACE_PREVIEW_REQUIRES_FILES_REASON);
    invoke('read', {path: `${P}/public/index.html`}, success('<!doctype html><h1>Report</h1>'));
    assert.equal(invoke('tool_call', {id: 'pixel_ods_workspace_preview', args: {relativeDirectory: `${P}/public`}}, rejected('unavailable')).blocked, undefined);
  });
}

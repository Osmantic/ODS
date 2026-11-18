import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaskActivity} from '../plugin/task-activity.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {ARTIFACT_TOOL, ARCHIVE_BOUNDARY, createWorkspaceArtifactAdmission, createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';

const user = 'ods-' + 'a'.repeat(64);
const context = {trigger: 'user', agentId: 'pixel', runId: 'chatcmpl_11111111-1111-4111-8111-111111111111',
  sessionId: 'owned-session', sessionKey: 'agent:pixel:openai-user:' + user};
const prompt = 'Fix the README and deliver the project as a downloadable ZIP.';

function fixture() {
  const guard = createToolLoopGuard({abortRun: () => true});
  let instant = '2026-10-27T04:57:41.929Z';
  const activity = createTaskActivity({now: () => instant});
  const first = {prompt};
  function begin(event = {prompt}, scope = context) {
    guard.observeRun(scope, 'pixel', event);
    activity.begin(event, scope);
  }
  function end(event = {success: true}) {
    guard.observeAgentEnd(event, context);
    activity.finish(event, context);
    return event;
  }
  begin(first);
  return {guard, activity, first, begin, end, time: value => {instant = value;}};
}

for (const success of [true, false]) test(`a fresh owned attempt restores publication after success=${success}`, async () => {
  const f = fixture();
  f.time('2026-10-27T04:57:42.073Z');
  const ended = f.end({success});
  assert.equal(f.guard.workspaceArtifactUnavailableReason(context), 'run-ended');
  f.time('2026-10-27T04:57:42.258Z');
  f.begin();
  assert.equal(f.activity.activeForUser(user)?.runId, context.runId);
  assert.equal(f.activity.projection(context.runId).finishedAt, null);
  assert.equal(f.guard.workspaceArtifactUnavailableReason(context), undefined);

  // Re-delivery of the already observed end hook cannot close the new attempt.
  f.end(ended);
  assert.equal(f.activity.projection(context.runId).state, 'running');
  assert.equal(f.guard.workspaceArtifactUnavailableReason(context), undefined);
  const args = {sourcePath: 'project', relativePath: 'project.zip'};
  const scope = {...context, toolCallId: 'publish'};
  const event = {toolName: ARTIFACT_TOOL, params: args};
  const admission = createWorkspaceArtifactAdmission();
  admission.before(event, scope, f.guard.beforeToolCall(event, scope));
  f.activity.before(event, scope);
  const tool = createWorkspaceArtifactTool(context, {admission,
    reserve: s => f.guard.reserveWorkspaceArtifact(s),
    unavailableReason: s => f.guard.workspaceArtifactUnavailableReason(s),
    accept: (s, r) => f.guard.acceptWorkspaceArtifact(s, r),
    request: async () => ({schemaVersion: 1, kind: 'ods-pixel-workspace-artifact',
      relativePath: 'project.zip', siteId: 'site-' + 'a'.repeat(24), sha256: 'a'.repeat(64),
      file: {path: 'project.zip', bytes: 100, sha256: 'b'.repeat(64)}, status: 'succeeded',
      httpStatus: 200, readbackVerified: true, executable: false, overwritten: false,
      boundary: ARCHIVE_BOUNDARY, archive: {sourcePath: 'project', fileCount: 4, sourceBytes: 200, omittedCacheEntries: 2}})});
  const result = await tool.execute(scope.toolCallId, args);
  assert.notEqual(result.isError, true);
  f.activity.after({result}, scope);
  f.time('2026-10-27T04:59:24.198Z');
  f.end();
  const view = f.activity.projection(context.runId);
  assert.equal(view.startedAt, '2026-10-27T04:57:41.929Z');
  assert.equal(view.finishedAt, '2026-10-27T04:59:24.198Z');
  assert.equal(view.calls, 1);
  assert.equal(f.guard.deliveryVerificationForRun(context.runId).artifacts.length, 1);
});

test('replayed prompts and missing or mismatched identities cannot reopen publication', () => {
  for (const variant of ['same-event', 'blank-prompt', 'missing-prompt', 'session', 'key', 'missing-session', 'missing-key', 'agent', 'noninteractive']) {
    const f = fixture();
    f.end();
    const scope = {...context};
    if (variant === 'session') scope.sessionId = 'other';
    if (variant === 'key') scope.sessionKey = 'agent:pixel:openai-user:ods-' + 'b'.repeat(64);
    if (variant === 'missing-session') delete scope.sessionId;
    if (variant === 'missing-key') delete scope.sessionKey;
    if (variant === 'agent') scope.agentId = 'other';
    if (variant === 'noninteractive') scope.trigger = 'cron';
    f.begin(variant === 'same-event' ? f.first : variant === 'blank-prompt' ? {prompt: ' '} : variant === 'missing-prompt' ? {} : {prompt}, scope);
    assert.ok(f.guard.workspaceArtifactUnavailableReason(context), variant);
  }
});

test('attempts preserve cancellation and recursive-delete refusal', async () => {
  for (const stop of ['cancel', 'delete']) {
    const f = fixture();
    if (stop === 'cancel') await f.guard.abortUserRun(user);
    else assert.equal(f.guard.beforeToolCall({toolName: 'exec', toolCallId: 'delete',
      params: {command: 'rm -rf /workspace/project'}}, context)?.block, true);
    f.end({success: false});
    f.begin();
    assert.ok(f.guard.workspaceArtifactUnavailableReason(context), stop);
  }
});

test('attempts preserve the per-request publication budget', () => {
  const f = fixture();
  for (let i = 0; i < 4; i++) assert.ok(f.guard.reserveWorkspaceArtifact(context));
  f.end();
  f.begin();
  assert.equal(f.guard.workspaceArtifactUnavailableReason(context), 'publication-attempt-limit');
});

test('a late receipt reserved by the ended attempt cannot attach after reopening', () => {
  const f = fixture();
  const prior = {...context, toolCallId: 'prior-publication'};
  const next = {...context, toolCallId: 'next-publication'};
  const receipt = {schemaVersion: 1, kind: 'ods-pixel-workspace-artifact',
    relativePath: 'project.zip', siteId: 'site-' + 'a'.repeat(24), sha256: 'a'.repeat(64),
    file: {path: 'project.zip', bytes: 100, sha256: 'b'.repeat(64)}};
  assert.equal(f.guard.reserveWorkspaceArtifact(prior), true);
  f.end();
  f.begin();
  assert.equal(f.guard.reserveWorkspaceArtifact(next), true);
  assert.equal(f.guard.acceptWorkspaceArtifact(prior, receipt), false);
  assert.equal(f.guard.acceptWorkspaceArtifact({...next}, receipt), false);
  assert.equal(f.guard.acceptWorkspaceArtifact(next, receipt), true);
});

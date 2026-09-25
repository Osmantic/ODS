// Tower2 round 092, coding-v1: after publishing, the model ran a CLI check and
// then rewrote test-results.txt with identical bytes as its last call. The
// pinned runtime ended the turn on that write without answer text, so
// before_agent_finalize, and with it the only host byte comparison, never ran:
// the delivery said the preview had not been verified since later activity.
// A saved result that settles the comparison now starts it at once.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams}
  from '../plugin/workspace-preview-inspect.mjs';

const context = {agentId: 'pixel', runId: 'run', sessionId: 'session', sessionKey: 'agent:pixel:openai-user:owner'};
const CODING = 'Create and publish a static website in a new workspace directory site.';
const TOGGLE = 'Create and publish a website in a new workspace directory site. Add a button that toggles hidden details.';

function snapshot(directory, files) {
  const digest = createHash('sha256');
  for (const {path, content} of [...files].sort((a, b) => a.path < b.path ? -1 : 1)) {
    const name = Buffer.from(path.slice(directory.length + 1)), data = Buffer.from(content);
    const nameLength = Buffer.alloc(4), dataLength = Buffer.alloc(8);
    nameLength.writeUInt32BE(name.length); dataLength.writeBigUInt64BE(BigInt(data.length));
    digest.update(nameLength).update(name).update(dataLength).update(data);
  }
  const sha256 = digest.digest('hex'), siteId = 'site-' + sha256.slice(0, 24);
  const entry = files.find(file => file.path === directory + '/index.html');
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: directory,
    siteId, sha256, entryFile: 'index.html', entrySha256: createHash('sha256').update(entry.content).digest('hex'),
    files: files.length, bytes: files.reduce((sum, file) => sum + Buffer.byteLength(file.content), 0),
    port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true,
    executable: false, overwritten: false};
}

// Hook order and contexts as OpenClaw 2026.6.33 delivers them. The persist
// hook names the session key, never the run or session ID.
function fixture({verify = async () => true, prompt = CODING, publish} = {}) {
  let probes = 0, publications = 0;
  const guard = createToolLoopGuard({
    verifyWorkspacePreview: async (...args) => { probes++; return verify(...args); },
    ...(publish ? {publishWorkspacePreview: async (...args) => { publications++; return publish(...args); }} : {}),
  });
  guard.observeRun(context, 'pixel', {prompt});
  const dispatch = (name, params, id) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const prepared = guard.beforeToolCall({toolName: name, runId: context.runId, toolCallId: id, params}, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    return {name, id, ctx, params: prepared?.params ?? params};
  };
  const complete = (call, result) => guard.afterToolCall(
    {toolName: call.name, runId: context.runId, toolCallId: call.id, params: call.params, result}, call.ctx);
  const save = (call, result, sessionKey = context.sessionKey) => guard.toolResultPersist(
    {toolName: call.name, toolCallId: call.id, message: {role: 'toolResult', toolName: call.name, toolCallId: call.id, ...result}},
    {agentId: 'pixel', sessionKey, toolName: call.name, toolCallId: call.id});
  const invoke = (name, params, result, id) => { const call = dispatch(name, params, id); complete(call, result); save(call, result); };
  const page = {path: 'site/index.html', content: '<!doctype html><button>Show details</button><p id="details" hidden>Details</p>'};
  invoke('write', page, {content: [{type: 'text', text: 'Successfully wrote file.'}]}, 'write-page');
  const preview = snapshot('site', [page]);
  invoke('pixel_ods_workspace_preview', {relativeDirectory: 'site'}, {content: [{type: 'text', text: 'published'}], details: preview}, 'publish');
  return {guard, preview, dispatch, complete, save, invoke, probes: () => probes, publications: () => publications};
}

const CLI = {command: 'cd /workspace/site && python3 report.py /tmp/test.csv'};
const CLI_RESULT = {content: [{type: 'text', text: '{"food": "16.00"}'}], details: {status: 'completed', exitCode: 0}};
const IDENTICAL = {path: 'test-results.txt', content: 'ok\n'};
const IDENTICAL_RESULT = {content: [{type: 'text', text: 'No changes made to test-results.txt. The file already has identical content.'}]};

test('R092 coding-v1: the saved identical write restores currency without finalization', async () => {
  const f = fixture();
  assert.equal(f.guard.verificationForRun('run').status, 'passed');
  f.invoke('exec', CLI, CLI_RESULT, 'cli');
  f.invoke('write', IDENTICAL, IDENTICAL_RESULT, 'identical-write');
  const stale = f.guard.verificationForRun('run');
  assert.equal(stale.status, 'failed');
  assert.match(stale.text, /not been verified again since later tool activity/);
  // The turn ends here: no model call and no before_agent_finalize. Delivery
  // (the ingress verification route) waits for the comparison.
  await f.guard.settleDelivery('run');
  const delivered = f.guard.deliveryVerificationForRun('run');
  assert.equal(delivered.status, 'passed');
  assert.equal(delivered.preview.sha256, f.preview.sha256);
  assert.equal(f.probes(), 1, 'the stale comparison for the CLI check never asks the host');
});

test('the attempt may end right after the saved result; that comparison still answers', async () => {
  const f = fixture();
  f.invoke('exec', CLI, CLI_RESULT, 'cli');
  f.invoke('write', IDENTICAL, IDENTICAL_RESULT, 'identical-write');
  f.guard.endPreviewRevalidation({}, context);
  f.guard.observeAgentEnd({}, context);
  await f.guard.settleDelivery('run');
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'passed');
  // The ended candidate is gone; nothing compares or restores it again.
  assert.equal(await f.guard.revalidateWorkspacePreview({}, context), false);
  assert.equal(f.probes(), 1);
});

test('a comparison is never requested after the end', async () => {
  const f = fixture();
  const cli = f.dispatch('exec', CLI, 'cli');
  f.complete(cli, CLI_RESULT);
  f.guard.endPreviewRevalidation({}, context);
  f.save(cli, CLI_RESULT);
  await f.guard.settleDelivery('run');
  assert.equal(f.probes(), 0);
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'failed');
});

test('changed host bytes stay failed, and republication remains a finalization step', async () => {
  const f = fixture({verify: async () => false, publish: async () => { throw new Error('not reached'); }});
  f.invoke('exec', CLI, CLI_RESULT, 'cli');
  f.invoke('write', IDENTICAL, IDENTICAL_RESULT, 'identical-write');
  await f.guard.settleDelivery('run');
  assert.equal(f.probes(), 1);
  assert.equal(f.publications(), 0);
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'failed');
  assert.equal(await f.guard.revalidateWorkspacePreview({}, context), false);
  assert.equal(f.probes(), 1, 'a mismatch is not asked again in the same generation');
});

test('the comparison waits until every call of a parallel batch is saved', async () => {
  const f = fixture();
  const first = f.dispatch('exec', CLI, 'cli');
  const second = f.dispatch('write', IDENTICAL, 'identical-write');
  f.complete(first, CLI_RESULT);
  f.complete(second, IDENTICAL_RESULT);
  f.save(first, CLI_RESULT);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(f.probes(), 0, 'a call of the batch is still unsaved');
  f.save(second, IDENTICAL_RESULT);
  await f.guard.settleDelivery('run');
  assert.equal(f.probes(), 1);
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'passed');
});

test('a result saved under another session key starts nothing', async () => {
  const f = fixture();
  const cli = f.dispatch('exec', CLI, 'cli');
  f.complete(cli, CLI_RESULT);
  f.save(cli, CLI_RESULT, 'agent:pixel:openai-user:someone-else');
  await f.guard.settleDelivery('run');
  assert.equal(f.probes(), 0);
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'failed');
});

test('a match that arrives while a read is pending is asked again once the read is saved', async () => {
  let release;
  const answers = [new Promise(resolve => { release = resolve; }), Promise.resolve(true)];
  const f = fixture({verify: () => answers.shift()});
  f.invoke('exec', CLI, CLI_RESULT, 'cli');
  for (let tick = 0; tick < 20 && !f.probes(); tick++) await Promise.resolve();
  assert.equal(f.probes(), 1);
  // A slow host: the model's next call, a read, starts before the answer.
  const read = f.dispatch('read', {path: 'site/index.html'}, 'read');
  release(true);
  for (let tick = 0; tick < 20; tick++) await Promise.resolve();
  assert.equal(f.guard.verificationForRun('run').status, 'failed', 'not restored while the read is pending');
  const readResult = {content: [{type: 'text', text: '<!doctype html>'}]};
  f.complete(read, readResult);
  f.save(read, readResult);
  await f.guard.settleDelivery('run');
  assert.equal(f.probes(), 2);
  assert.equal(f.guard.deliveryVerificationForRun('run').status, 'passed');
});

// Tower2 round 092 also had a website run: after publishing, a read-only grep
// cleared the current preview until the byte comparison. When that comparison
// has answered before the model's next call, as the model's own latency
// usually allows, an inspection of the same snapshot binds to a current preview.
function plan(preview) {
  return {siteId: preview.siteId, sha256: preview.sha256, viewport: {width: 800, height: 600}, steps: [
    {action: 'assert-hidden', locator: {selector: '#details'}},
    {action: 'click', locator: {role: 'button', name: 'Show details', exact: true}},
    {action: 'assert-visible', locator: {selector: '#details'}},
  ]};
}
function inspectionResult(params) {
  const request = normalizeWorkspacePreviewInspectionParams(params);
  const state = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible', opacity: '1',
    hidden: !visible, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
  return {details: {schemaVersion: 1, kind: INSPECTION_KIND, status: 'passed', siteId: params.siteId, sha256: params.sha256,
    planSha256: inspectionPlanHash(request), viewport: params.viewport,
    steps: params.steps.map((step, index) => ({index, ...step, before: state(step.action !== 'assert-hidden'),
      ...(step.action === 'click' ? {after: state(true)} : {}), stable: true, status: 'passed'})),
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE}};
}

test('R092 website: a grep saved after publication is compared before the next call, so the inspection binds', async () => {
  const f = fixture({prompt: TOGGLE});
  const grep = {content: [{type: 'text', text: '1: <button>Show details</button>'}], details: {status: 'completed', exitCode: 0}};
  f.invoke('exec', {command: 'grep -n "details" site/index.html'}, grep, 'grep');
  await f.guard.settleDelivery('run');
  const params = plan(f.preview);
  f.invoke(PREVIEW_INSPECTION_TOOL, params, inspectionResult(params), 'inspect');
  await f.guard.settleDelivery('run');
  const outcome = f.guard.deliveryVerificationForRun('run');
  assert.equal(outcome.status, 'passed', outcome.text);
  assert.equal(f.probes(), 1);
});

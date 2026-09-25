// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): "make me a cool
// looking webpage ... Best you can do" was one whole-page write cut at the
// output limit after 220 s. OpenClaw 2026.6.33 skips before_agent_finalize for
// such a turn, so the Portal ingress asks Pixel for one continuation turn.
// The ingress half runs over loopback TCP (also on Windows).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, userMessageExtensionLifecycleIntent, userMessageOperationsContinuation,
  userMessageRequestsExactByteDownload, userMessageRequestsNewPlaygroundProject, userMessageRequestsPrivateUrl,
  userMessageRequestsWorkspaceContinuation, userMessageRequestsWorkspacePreview, userMessageRequestsWorkspaceTools,
  userMessageRequestsWorkspaceVisualContinuation, userMessageRequiresOperations,
  workspacePreviewMode} from '../plugin/tool-loop-guard.mjs';
import {OUTPUT_LIMIT_CONTINUATION_PROMPT, OUTPUT_LIMIT_UNRECOVERED_TEXT} from '../plugin/output-limit-recovery.mjs';
import {ODS_COMPACT_CONVERSATION_CONTRACT, ODS_CONVERSATION_CONTRACT, promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {OUTPUT_LIMIT_CONTINUATION_PROMPT as INGRESS_PROMPT, computeSessionUser,
  createIngressServer} from '../host/pixel_ingress.mjs';

const CHAT = 'strixy-forest-demo';
const USER = computeSessionUser({user: CHAT});
const OWNER_KEY = `agent:pixel:openai-user:${USER}`;
const STRIXY_PROMPT = 'as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest ' +
  "type effects.  Best you can do.\n\n[ODS Portal delivery requirement: Answer the owner's complete message above.]";
// The recorded reply: one sentence, then the whole page in one write call,
// cut at the output limit (its arguments end mid-document).
const CUT = {role: 'assistant', stopReason: 'length', usage: {output: 8192}, content: [
  {type: 'text', text: 'Let me build something impressive — a full forest-themed interactive experience.'},
  {type: 'toolCall', id: 'call-1', name: 'write',
    arguments: {path: 'Playground/forest/index.html', content: '<!doctype html><html><head><style>:root{--moss:#2f5d3a'}}]};
const DONE = {role: 'assistant', stopReason: 'stop', content: [{type: 'text', text: 'Your forest page is published.'}]};
const INELIGIBLE = {schemaVersion: 1, kind: 'ods-output-limit-continuation', eligible: false};
const GENERIC = "⚠️ Agent couldn't generate a response. Please try again.";

function turn(guard, {runId, prompt = STRIXY_PROMPT, sessionKey = OWNER_KEY, trigger = 'user'} = {}) {
  const context = {agentId: 'pixel', runId, sessionId: 'session-1', sessionKey, trigger};
  guard.observeRun(context, 'pixel', {prompt});
  return {context,
    reply: message => guard.observeAssistantMessage({message}, context),
    finalize: message => guard.beforeAgentFinalize({lastAssistantMessage: message.content[0].text}, context),
    grant: () => guard.outputLimitContinuationForRun(runId),
    verification: () => guard.deliveryVerificationForRun(runId)};
}

function publishSplitPage(guard, context) {
  const directory = 'Playground/forest';
  const files = [
    {path: `${directory}/index.html`, content: '<!doctype html><link rel="stylesheet" href="styles.css"><script src="script.js" defer></script><h1>Forest</h1>'},
    {path: `${directory}/styles.css`, content: 'body{background:#0b3d20;color:#e8f5e9}'},
    {path: `${directory}/script.js`, content: 'document.body.dataset.fireflies = "on";'},
  ];
  files.forEach((params, index) => {
    const ctx = {...context, toolName: 'write', toolCallId: `write-${index}`};
    const decision = guard.beforeToolCall({toolName: 'write', params, toolCallId: ctx.toolCallId}, ctx);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName: 'write', params: decision?.params ?? params, toolCallId: ctx.toolCallId,
      result: {content: [{type: 'text', text: 'ok'}], details: {status: 'completed'}}}, ctx);
  });
  const params = {relativeDirectory: directory};
  const ctx = {...context, toolName: 'pixel_ods_workspace_preview', toolCallId: 'preview-1'};
  assert.notEqual(guard.beforeToolCall({toolName: ctx.toolName, params, toolCallId: ctx.toolCallId}, ctx)?.block, true);
  const digest = createHash('sha256');
  let bytes = 0;
  for (const {path, content} of [...files].sort((a, b) => a.path < b.path ? -1 : 1)) {
    const name = Buffer.from(path.slice(directory.length + 1)), body = Buffer.from(content);
    const nameLength = Buffer.alloc(4), bodyLength = Buffer.alloc(8);
    nameLength.writeUInt32BE(name.length); bodyLength.writeBigUInt64BE(BigInt(body.length));
    digest.update(nameLength); digest.update(name); digest.update(bodyLength); digest.update(body);
    bytes += body.length;
  }
  const sha256 = digest.digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
  guard.afterToolCall({toolName: ctx.toolName, params, toolCallId: ctx.toolCallId, result: {details: {
    schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: directory,
    siteId, files: files.length, bytes, sha256, entryFile: 'index.html',
    entrySha256: createHash('sha256').update(files[0].content).digest('hex'), port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true,
    executable: false, overwritten: false}}}, ctx);
}

test('the ingress and the plugin carry the same continuation bytes', () => {
  assert.equal(INGRESS_PROMPT, OUTPUT_LIMIT_CONTINUATION_PROMPT);
  assert.doesNotMatch(OUTPUT_LIMIT_CONTINUATION_PROMPT, /\d/, 'no per-turn variable text');
});

test('the continuation message selects no workspace, preview, visual-edit or host route', () => {
  const prompt = OUTPUT_LIMIT_CONTINUATION_PROMPT;
  for (const classify of [userMessageRequestsWorkspacePreview, userMessageRequestsWorkspaceTools,
    userMessageRequestsNewPlaygroundProject, userMessageRequestsWorkspaceVisualContinuation,
    userMessageRequestsWorkspaceContinuation, userMessageRequiresOperations, userMessageOperationsContinuation,
    userMessageExtensionLifecycleIntent, userMessageRequestsPrivateUrl, userMessageRequestsExactByteDownload,
    workspacePreviewMode]) {
    assert.ok(!classify([], prompt), classify.name);
  }
  for (const [lean, conversation] of [[true, ODS_COMPACT_CONVERSATION_CONTRACT], [false, ODS_CONVERSATION_CONTRACT]]) {
    assert.equal(promptContractForAgent({agentId: 'pixel', contextTokenBudget: 65536}, 'pixel', {prompt},
      {configuredLeanPrompt: lean}).appendSystemContext, conversation);
  }
});

test('the recorded strixy turn is granted exactly one continuation, bound to its chat', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  // OpenClaw 2026.6.33 does not run before_agent_finalize for this turn.
  assert.deepEqual(owner.grant(), {...INELIGIBLE, eligible: true, user: USER});
  assert.deepEqual(owner.grant(), INELIGIBLE, 'consumed on grant');
  assert.ok(owner.verification().text.startsWith(OUTPUT_LIMIT_UNRECOVERED_TEXT), 'the honest report stands for the cut run');
});

test('the continuation turn can deliver the page as split files', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT});
  publishSplitPage(guard, next.context);
  next.reply(DONE);
  assert.equal(next.finalize(DONE), undefined);
  const verification = next.verification();
  assert.equal(verification.status, 'passed');
  assert.equal(verification.preview.relativeDirectory, 'Playground/forest');
  assert.equal(verification.preview.files, 3);
});

test('a continuation cut again keeps the honest report and gets no further pass', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT});
  next.reply(CUT);
  assert.notEqual(next.finalize(CUT)?.retry?.idempotencyKey, 'ods-output-limit-recovery', 'no in-run pass either');
  assert.deepEqual(next.grant(), INELIGIBLE);
  const verification = next.verification();
  assert.equal(verification.status, 'failed');
  assert.ok(verification.text.startsWith(OUTPUT_LIMIT_UNRECOVERED_TEXT));
});

test('no continuation after an in-run pass, after a later reply, or outside owner chat turns', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const revised = turn(guard, {runId: 'revised'});
  revised.reply(CUT);
  assert.equal(revised.finalize(CUT)?.action, 'revise', 'the hook ran and used the one pass');
  revised.reply(CUT);
  assert.deepEqual(revised.grant(), INELIGIBLE);
  const finished = turn(guard, {runId: 'finished'});
  finished.reply(CUT);
  finished.reply(DONE);
  assert.deepEqual(finished.grant(), INELIGIBLE, 'a later reply means the run moved past the cut');
  for (const [runId, options] of [['heartbeat', {trigger: 'heartbeat'}], ['cron', {trigger: 'cron'}],
    ['main', {sessionKey: 'agent:pixel:main'}],
    ['team', {prompt: "You are the Builder in the owner's Portal team. Implement the plan."}]]) {
    const other = turn(guard, {runId, ...options});
    other.reply(CUT);
    assert.deepEqual(other.grant(), INELIGIBLE, runId);
  }
});

test('cancelled, host-operation, extension and exact-download turns keep the honest report', async () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const cancelled = turn(guard, {runId: 'cancelled'});
  cancelled.reply(CUT);
  assert.equal(await guard.abortUserRun(USER), true);
  assert.deepEqual(cancelled.grant(), INELIGIBLE);
  for (const prompt of [
    'Tell me the ODS host hostname, kernel, and machine architecture using Operations capabilities.',
    '/extensions install https://github.com/example/project',
    'Download https://example.com into a file and report the SHA-256 of the exact bytes saved.',
  ]) {
    const other = turn(guard, {runId: `lane-${prompt.length}`, prompt});
    other.reply(CUT);
    assert.equal(other.verification().status, 'failed', prompt);
    assert.deepEqual(other.grant(), INELIGIBLE, prompt);
  }
});

// Loopback gateway double: completions in order, one grant answer per run.
async function fixture(t, {grant = {...INELIGIBLE, eligible: true, user: USER}, grantStatus = 200,
  second = DONE_COMPLETION, verifications = {}, holdGrant = false, readOnlyProof} = {}) {
  const seen = {submissions: [], grants: [], verifications: [], readOnly: 0};
  let release, grantRequested;
  const released = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { grantRequested = resolve; });
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    const json = value => { res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify(value)); };
    switch (req.url) {
      case '/health': return json({ok: true});
      case '/v1/chat/completions':
        seen.submissions.push(body);
        return json(seen.submissions.length === 1 ? CUT_COMPLETION : second);
      case '/pixel-ods/output-limit-continuation':
        seen.grants.push(body);
        grantRequested();
        if (holdGrant) await released;
        if (grantStatus !== 200) { res.writeHead(grantStatus); return res.end(); }
        return json(grant);
      case '/pixel-ods/verification':
        seen.verifications.push(body.runId);
        return json(verifications[body.runId] ?? {status: 'none'});
      case '/pixel-ods/read-only-extension-continuation':
        seen.readOnly++;
        return json(readOnlyProof ?? {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false});
      case '/pixel-ods/unfinished-extension-decision':
        return json({schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false});
      case '/pixel-ods/activity': return json({task: null});
      default: res.writeHead(404); return res.end();
    }
  });
  const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
  const ingress = createIngressServer({token: 'test-gateway-token-0123456789abcdef', gatewayPort: await listen(gateway)});
  const port = await listen(ingress);
  t.after(async () => {
    release();
    for (const server of [ingress, gateway]) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  });
  const chat = async ({stream = true, signal = AbortSignal.timeout(5000)} = {}) => {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST', signal,
      headers: {'content-type': 'application/json', connection: 'close'},
      body: JSON.stringify({user: CHAT, stream, messages: [{role: 'system', content: 'Trusted identity'},
        {role: 'user', content: STRIXY_PROMPT}]})});
    const body = await response.text();
    if (!stream) return {status: response.status, text: JSON.parse(body).choices[0].message.content};
    const frames = body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)));
    return {status: response.status, frames, outcome: frames.at(-1).pixel_outcome?.status,
      text: frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')};
  };
  return {seen, chat, release, requested};
}
const RUN_1 = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const RUN_2 = 'chatcmpl_aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const CUT_COMPLETION = {id: RUN_1, choices: [{index: 0, message: {role: 'assistant', content: GENERIC}, finish_reason: 'stop'}]};
const DONE_COMPLETION = {id: RUN_2, choices: [{index: 0, message: {role: 'assistant', content: DONE.content[0].text}, finish_reason: 'stop'}]};
const HONEST = {status: 'failed', text: OUTPUT_LIMIT_UNRECOVERED_TEXT};

for (const stream of [true, false]) {
  test(`the ingress continues a cut owner turn once with the fixed message (stream=${stream})`, {timeout: 10000}, async t => {
    const f = await fixture(t, {verifications: {[RUN_1]: HONEST}});
    const result = await f.chat({stream});
    assert.equal(result.status, 200);
    assert.equal(result.text, 'Your forest page is published.');
    assert.equal(f.seen.submissions.length, 2, 'one continuation, never a replay');
    const [first, continuation] = f.seen.submissions;
    assert.equal(first.messages.at(-1).content, STRIXY_PROMPT);
    assert.deepEqual(continuation.messages, [{role: 'system', content: 'Trusted identity'},
      {role: 'user', content: OUTPUT_LIMIT_CONTINUATION_PROMPT}], 'same trusted system text, fixed message only');
    assert.equal(continuation.user, first.user);
    assert.equal(continuation.stream, false);
    assert.deepEqual(f.seen.grants, [{runId: RUN_1}]);
    assert.deepEqual(f.seen.verifications, [RUN_2], 'delivery verifies the continuation run');
  });
}

test('a continuation cut again is reported honestly and never continued again', {timeout: 10000}, async t => {
  const second = {...CUT_COMPLETION, id: RUN_2};
  const f = await fixture(t, {second, verifications: {[RUN_1]: HONEST, [RUN_2]: HONEST}});
  const result = await f.chat();
  assert.equal(result.text, OUTPUT_LIMIT_UNRECOVERED_TEXT);
  assert.equal(result.outcome, 'failed');
  assert.equal(f.seen.submissions.length, 2);
  assert.deepEqual(f.seen.grants, [{runId: RUN_1}], 'the continuation run is never offered another turn');
});

test('refused, unbound or unavailable grants keep the honest report without a second turn', {timeout: 20000}, async t => {
  for (const [label, options] of [
    ['refused', {grant: INELIGIBLE}],
    ['other chat', {grant: {...INELIGIBLE, eligible: true, user: `ods-${'0'.repeat(64)}`}}],
    ['extra field', {grant: {...INELIGIBLE, eligible: true, user: USER, replay: true}}],
    ['older plugin without the route', {grantStatus: 404}],
  ]) {
    await t.test(label, async t => {
      const f = await fixture(t, {...options, verifications: {[RUN_1]: HONEST}});
      const result = await f.chat();
      assert.equal(result.text, OUTPUT_LIMIT_UNRECOVERED_TEXT);
      assert.equal(result.outcome, 'failed');
      assert.equal(f.seen.submissions.length, 1);
      assert.deepEqual(f.seen.verifications, [RUN_1]);
    });
  }
});

test('an owner who leaves before the grant gets no continuation', {timeout: 10000}, async t => {
  const f = await fixture(t, {holdGrant: true, verifications: {[RUN_1]: HONEST}});
  const controller = new AbortController();
  const pending = f.chat({signal: controller.signal}).catch(error => error);
  await f.requested;
  controller.abort();
  assert.equal((await pending).name, 'AbortError');
  f.release();
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(f.seen.submissions.length, 1, 'the cancelled transport never starts a new turn');
});

test('a turn another continuation already recovered is not continued again', {timeout: 10000}, async t => {
  const f = await fixture(t, {readOnlyProof: {schemaVersion: 1, kind: 'ods-extension-read-only-continuation',
    eligible: true, chatId: CHAT, requestId: 'turn'}});
  const result = await f.chat();
  assert.equal(result.text, 'Your forest page is published.');
  assert.equal(f.seen.readOnly, 1);
  assert.equal(f.seen.submissions.length, 2);
  assert.deepEqual(f.seen.grants, [], 'at most one continuation of any kind per owner turn');
});

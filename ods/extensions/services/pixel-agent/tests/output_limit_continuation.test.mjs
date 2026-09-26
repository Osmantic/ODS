// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): "make me a cool
// looking webpage ... Best you can do" was one whole-page write cut at the
// output limit after 220 s. OpenClaw 2026.6.33 skips before_agent_finalize for
// such a turn, so the Portal ingress asks Pixel for one continuation turn.
// The ingress half runs over loopback TCP (also on Windows).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {createToolLoopGuard, userMessageExtensionLifecycleIntent, userMessageOperationsContinuation,
  userMessageRequestsExactByteDownload, userMessageRequestsNewPlaygroundProject, userMessageRequestsPrivateUrl,
  userMessageRequestsWorkspaceContinuation, userMessageRequestsWorkspacePreview, userMessageRequestsWorkspaceTools,
  userMessageRequestsWorkspaceVisualContinuation, userMessageRequiresOperations,
  workspacePreviewMode} from '../plugin/tool-loop-guard.mjs';
import {OUTPUT_LIMIT_CONTINUATION_PROMPT, OUTPUT_LIMIT_CONTINUED_TEXT,
  OUTPUT_LIMIT_WORKSPACE_TEXT} from '../plugin/output-limit-recovery.mjs';
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

// before_prompt_build as plugin/index.js runs it: the run is classified by
// outputLimitContinuationEvent's event, which observeRun and the contract read.
function turn(guard, {runId, prompt = STRIXY_PROMPT, sessionKey = OWNER_KEY, trigger = 'user', workspaceRoot} = {}) {
  const context = {agentId: 'pixel', runId, sessionId: 'session-1', sessionKey, trigger};
  const event = guard.outputLimitContinuationEvent(context, 'pixel', {prompt});
  guard.observeRun(context, 'pixel', event, workspaceRoot ? {workspaceRoot} : undefined);
  return {context, event,
    contract: () => promptContractForAgent(context, 'pixel', event, {maxOutputTokens: 8192}).appendSystemContext,
    reply: message => guard.observeAssistantMessage({message}, context),
    finalize: message => guard.beforeAgentFinalize({lastAssistantMessage: message.content[0].text}, context),
    grant: () => guard.outputLimitContinuationForRun(runId),
    verification: () => guard.deliveryVerificationForRun(runId)};
}

// OpenClaw 2026.6.33 buildToolContext: the run identity, never the turn's trigger.
const toolContext = (context, toolName, toolCallId) => ({toolName, agentId: context.agentId,
  sessionKey: context.sessionKey, sessionId: context.sessionId, runId: context.runId, toolCallId});

// One completed write through the tool hooks; returns the path it wrote.
function write(guard, context, params, toolCallId, root = undefined) {
  const ctx = toolContext(context, 'write', toolCallId);
  const decision = guard.beforeToolCall({toolName: 'write', params, toolCallId}, ctx);
  assert.notEqual(decision?.block, true, decision?.blockReason);
  const executed = decision?.params ?? params;
  if (root) {
    mkdirSync(dirname(join(root, executed.path)), {recursive: true});
    writeFileSync(join(root, executed.path), executed.content);
  }
  guard.afterToolCall({toolName: 'write', params: executed, toolCallId,
    result: {content: [{type: 'text', text: 'ok'}], details: {status: 'completed'}}}, ctx);
  return executed.path;
}

function publishSplitPage(guard, context) {
  const directory = 'Playground/forest';
  const files = [
    {path: `${directory}/index.html`, content: '<!doctype html><link rel="stylesheet" href="styles.css"><script src="script.js" defer></script><h1>Forest</h1>'},
    {path: `${directory}/styles.css`, content: 'body{background:#0b3d20;color:#e8f5e9}'},
    {path: `${directory}/script.js`, content: 'document.body.dataset.fireflies = "on";'},
  ];
  files.forEach((params, index) => write(guard, context, params, `write-${index}`));
  const params = {relativeDirectory: directory};
  const ctx = toolContext(context, 'pixel_ods_workspace_preview', 'preview-1');
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

// Without a grant (another chat, or a copy typed later) the message is
// classified as itself; alone it must not select any route of its own.
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
  const report = owner.verification();
  assert.equal(report.status, 'failed');
  assert.equal(report.text.split('\n\n')[0], OUTPUT_LIMIT_WORKSPACE_TEXT, 'the honest report leads for the cut run');
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

test('a cut after earlier tool work in the same turn is still granted', () => {
  // The tool hooks carry no trigger; they must not unbind the owner's chat.
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  write(guard, owner.context, {path: 'Playground/forest/styles.css', content: 'body{background:#0b3d20}'}, 'write-styles');
  owner.reply(CUT);
  assert.deepEqual(owner.grant(), {...INELIGIBLE, eligible: true, user: USER});
});

const FALSE_READY = {role: 'assistant', stopReason: 'stop', content: [{type: 'text',
  text: 'Your forest page is ready in Playground/forest as index.html, styles.css and script.js.'}]};

test('the continuation run keeps the owner request contract and delivery checks', () => {
  // Control: the same unfounded answer in an uncut owner turn.
  const control = turn(createToolLoopGuard({abortRun: () => true}), {runId: 'control'});
  control.reply(FALSE_READY);
  control.finalize(FALSE_READY);
  const expected = control.verification();
  assert.equal(expected.status, 'failed');
  assert.match(expected.text, /did not create or verify the requested website files/);

  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  // OpenClaw 2026.6.33 puts its timestamp envelope in front of the message.
  const message = `[Fri 2026-09-25 18:40 EDT] ${OUTPUT_LIMIT_CONTINUATION_PROMPT}`;
  const next = turn(guard, {runId: 'run-2', prompt: message});
  assert.equal(next.event.prompt, STRIXY_PROMPT, 'classified as the owner message it continues');
  assert.equal(next.contract(), owner.contract(), 'the same system contract, byte for byte');
  assert.equal(guard.outputLimitContinuationEvent(next.context, 'pixel', {prompt: message}).prompt, STRIXY_PROMPT,
    'every attempt of the continuation run');
  next.reply(FALSE_READY);
  next.finalize(FALSE_READY);
  assert.deepEqual(next.verification(), expected, 'the same delivery check as the uncut turn');
  assert.deepEqual(next.grant(), INELIGIBLE);
});

test('only the next owner turn of the granted chat is classified as the cut request', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const message = OUTPUT_LIMIT_CONTINUATION_PROMPT;
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  const otherChat = `agent:pixel:openai-user:${computeSessionUser({user: 'another-chat'})}`;
  assert.equal(turn(guard, {runId: 'other-chat', prompt: message, sessionKey: otherChat}).event.prompt, message);
  assert.equal(turn(guard, {runId: 'heartbeat', prompt: message, trigger: 'heartbeat'}).event.prompt, message);
  // The chat's next owner turn takes the grant even when it is a new message.
  assert.equal(turn(guard, {runId: 'run-2', prompt: 'Thanks. What else can you do?'}).event.prompt,
    'Thanks. What else can you do?');
  assert.equal(turn(guard, {runId: 'run-3', prompt: message}).event.prompt, message, 'a later copy is not continued');
});

test('the continuation keeps writing into the Playground project of the cut turn', {skip: process.platform === 'win32'}, t => {
  for (const wroteFirst of [true, false]) {
    const root = mkdtempSync(join(tmpdir(), 'ods-output-limit-'));
    t.after(() => rmSync(root, {recursive: true, force: true}));
    const guard = createToolLoopGuard({abortRun: () => true});
    const owner = turn(guard, {runId: 'run-1', workspaceRoot: root});
    if (wroteFirst) {
      assert.equal(write(guard, owner.context, {path: 'Playground/forest/styles.css', content: 'body{}'}, 'w1', root),
        'Playground/forest/styles.css');
    }
    owner.reply(CUT);
    assert.equal(owner.grant().eligible, true);
    const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT, workspaceRoot: root});
    assert.equal(write(guard, next.context, {path: 'Playground/forest/index.html', content: '<!doctype html>'}, 'w2', root),
      'Playground/forest/index.html', `one project (wrote first: ${wroteFirst})`);
    assert.equal(existsSync(join(root, 'Playground', 'forest-2')), false);
    assert.equal(existsSync(join(root, '.ods-projects')), true, 'the project is recorded for the chat');
  }
});

test('a continuation cut again keeps the honest report and gets no further pass', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT});
  next.reply(CUT);
  assert.deepEqual(next.grant(), INELIGIBLE);
  const verification = next.verification();
  assert.equal(verification.status, 'failed');
  assert.equal(verification.text.split('\n\n')[0], `${OUTPUT_LIMIT_CONTINUED_TEXT} ${OUTPUT_LIMIT_WORKSPACE_TEXT}`,
    'the report says the automatic continuation was cut too');
});

test('no continuation after a later reply or outside owner chat turns', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
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
async function fixture(t, {first = CUT_COMPLETION, grant = {...INELIGIBLE, eligible: true, user: USER}, grantStatus = 200,
  grantReply, second = DONE_COMPLETION, secondStatus = 200, verifications = {}, holdGrant = false, readOnlyProof} = {}) {
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
        if (seen.submissions.length === 1) return json(first);
        if (secondStatus !== 200) {
          res.writeHead(secondStatus, {'content-type': 'application/json'});
          return res.end('{"error":{"message":"unavailable"}}');
        }
        return json(second);
      case '/pixel-ods/output-limit-continuation':
        seen.grants.push(body);
        grantRequested();
        if (holdGrant) await released;
        if (grantReply) return grantReply(res);
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
const HONEST = {status: 'failed', text: OUTPUT_LIMIT_WORKSPACE_TEXT};

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
  assert.equal(result.text, OUTPUT_LIMIT_WORKSPACE_TEXT);
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
      assert.equal(result.text, OUTPUT_LIMIT_WORKSPACE_TEXT);
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

// An ordinary answer is never a cut turn: its delivery does not depend on the
// plugin's continuation route, even when that route is broken.
for (const stream of [true, false]) {
  test(`an ordinary answer never asks for a continuation (stream=${stream})`, {timeout: 10000}, async t => {
    for (const [label, grantReply] of [
      ['reset', res => res.socket.destroy()],
      ['not json', res => { res.writeHead(200, {'content-type': 'application/json'}); res.end('<html>not json'); }],
    ]) {
      for (const content of ['Your forest page is published.', 'NO_REPLY']) {
        const f = await fixture(t, {first: {...DONE_COMPLETION, choices: [{index: 0,
          message: {role: 'assistant', content}, finish_reason: 'stop'}]}, grantReply});
        const result = await f.chat({stream});
        assert.equal(result.status, 200, label);
        if (content !== 'NO_REPLY') assert.equal(result.text, content, label);
        assert.deepEqual(f.seen.grants, [], label);
        assert.equal(f.seen.submissions.length, 1, label);
      }
    }
  });
}

// OpenClaw 2026.6.33 (resolveIncompleteTurnPayloadText) ends a cut turn with
// one of two texts; after tool activity it is the second.
const AFTER_TOOLS = "⚠️ Agent couldn't generate a response. Note: some tool actions may have already been executed — please verify before retrying.";

test('every OpenClaw incomplete-turn text of a cut turn is offered the continuation', {timeout: 20000}, async t => {
  for (const content of [GENERIC, AFTER_TOOLS, `Wrote Playground/forest/styles.css.\n\n${GENERIC}`]) {
    await t.test(content.slice(0, 60), async t => {
      const f = await fixture(t, {first: {...CUT_COMPLETION, choices: [{index: 0,
        message: {role: 'assistant', content}, finish_reason: 'stop'}]}, verifications: {[RUN_1]: HONEST}});
      const result = await f.chat();
      assert.equal(result.text, 'Your forest page is published.');
      assert.deepEqual(f.seen.grants, [{runId: RUN_1}]);
      assert.equal(f.seen.submissions.length, 2);
    });
  }
});

for (const stream of [true, false]) {
  test(`a continuation the gateway does not complete keeps the honest report (stream=${stream})`, {timeout: 10000}, async t => {
    const f = await fixture(t, {secondStatus: 503, verifications: {[RUN_1]: HONEST}});
    const result = await f.chat({stream});
    assert.equal(result.status, 200);
    assert.equal(result.text, `${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\nPixel's automatic continuation after the output limit ` +
      'did not complete (gateway HTTP 503). It may have acted before it stopped; check what it changed before asking Pixel to continue.');
    if (stream) assert.equal(result.outcome, 'failed');
    assert.equal(f.seen.submissions.length, 2, 'never retried');
    assert.deepEqual(f.seen.grants, [{runId: RUN_1}]);
    assert.deepEqual(f.seen.verifications, [RUN_1], 'the cut run is the one reported');
  });
}

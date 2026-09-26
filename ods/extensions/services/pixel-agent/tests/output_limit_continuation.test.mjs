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
import {OUTPUT_LIMIT_AFTER_REPLY, OUTPUT_LIMIT_ANSWER_TEXT, OUTPUT_LIMIT_CONTINUATION_PROMPT, OUTPUT_LIMIT_CONTINUED_TEXT,
  OUTPUT_LIMIT_WORKSPACE_TEXT} from '../plugin/output-limit-recovery.mjs';
import {ODS_COMPACT_CONVERSATION_CONTRACT, ODS_CONVERSATION_CONTRACT, promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {OUTPUT_LIMIT_AFTER_REPLY as INGRESS_AFTER_REPLY, OUTPUT_LIMIT_CONTINUATION_PROMPT as INGRESS_PROMPT, computeSessionUser,
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
    grant: options => guard.outputLimitContinuationForRun(runId, options),
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

const SPLIT_PAGE = [
  {path: 'Playground/forest/index.html', content: '<!doctype html><link rel="stylesheet" href="styles.css"><script src="script.js" defer></script><h1>Forest</h1>'},
  {path: 'Playground/forest/styles.css', content: 'body{background:#0b3d20;color:#e8f5e9}'},
  {path: 'Playground/forest/script.js', content: 'document.body.dataset.fireflies = "on";'},
];

// Writes the split page's files (all, or those an earlier turn did not save)
// and publishes the page.
function publishSplitPage(guard, context, {written = SPLIT_PAGE} = {}) {
  const directory = 'Playground/forest';
  const files = SPLIT_PAGE;
  written.forEach((params, index) => write(guard, context, params, `write-${index}`));
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
  assert.equal(INGRESS_AFTER_REPLY, OUTPUT_LIMIT_AFTER_REPLY);
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

// #6743 review: following the prevention line, the first turn saved
// index.html and its styles.css write was cut. The continuation, classified
// as the owner request, was refused the preview ("has not created or
// inspected an index.html"), and rewriting index.html byte for byte ends
// OpenClaw's run ("No changes made"). The cut run's saved files now count as
// inspected by the continuation.
test('a continuation publishes a page whose index.html the cut turn already saved', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const owner = turn(guard, {runId: 'run-1'});
  write(guard, owner.context, SPLIT_PAGE[0], 'write-index');
  owner.reply({...CUT, content: [CUT.content[0], {type: 'toolCall', id: 'call-2', name: 'write',
    arguments: {path: 'Playground/forest/styles.css', content: ':root{--moss:#2f5d3a'}}]});
  assert.equal(owner.grant().eligible, true);
  const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT});
  publishSplitPage(guard, next.context, {written: SPLIT_PAGE.slice(1)});
  next.reply(DONE);
  assert.equal(next.finalize(DONE), undefined);
  const verification = next.verification();
  assert.equal(verification.status, 'passed', verification.text);
  assert.equal(verification.preview.files, 3);
});

// OpenClaw delivers a cut reply's own text after a tool error. A workspace
// task is still continued; a written answer keeps what OpenClaw delivered,
// followed by the report.
test('a cut whose own text OpenClaw delivered is continued only for a workspace task', () => {
  const guard = createToolLoopGuard({abortRun: () => true});
  const site = turn(guard, {runId: 'site'});
  site.reply(CUT);
  assert.deepEqual(site.grant({incompleteTurn: false}), {...INELIGIBLE, eligible: true, user: USER});
  const answerKey = `agent:pixel:openai-user:${computeSessionUser({user: 'long-answer'})}`;
  const answer = turn(guard, {runId: 'answer', sessionKey: answerKey,
    prompt: 'Write me the longest, most detailed guide you can about how old-growth forests store carbon.'});
  answer.reply({...CUT, content: [{type: 'text', text: 'Old-growth forests store carbon in'}]});
  assert.deepEqual(answer.grant({incompleteTurn: false}), INELIGIBLE);
  assert.deepEqual(answer.verification(), {status: 'failed', text: OUTPUT_LIMIT_ANSWER_TEXT, deliveryMode: 'after-reply'});
  assert.equal(guard.continuationAllowed('answer'), true, 'a /goal plan stays active, as without the report');
});

// #6743 review: a result the host already verified was reported as failed and
// continued when only the closing reply was cut.
test('a cut closing reply keeps a verified result and gets no continuation', () => {
  const receipts = [];
  for (const cut of [false, true]) {
    const guard = createToolLoopGuard({abortRun: () => true});
    const owner = turn(guard, {runId: 'run-1'});
    publishSplitPage(guard, owner.context);
    owner.reply(cut ? CUT : DONE);
    if (cut) {
      assert.deepEqual(owner.grant(), INELIGIBLE);
      assert.deepEqual(owner.grant({incompleteTurn: false}), INELIGIBLE);
    }
    receipts.push(owner.verification());
  }
  assert.equal(receipts[0].status, 'passed');
  assert.deepEqual(receipts[1], receipts[0], 'the same receipt as the uncut turn');
});

// #6743 review: a Stop after the grant, before OpenClaw starts the
// continuation, found only the ended cut run and aborted nothing.
test('a Stop after the grant withdraws the continuation before it starts', async () => {
  const guard = createToolLoopGuard({abortRun: () => false});
  const owner = turn(guard, {runId: 'run-1'});
  owner.reply(CUT);
  assert.equal(owner.grant().eligible, true);
  assert.equal(await guard.abortUserRun(USER), true, 'acknowledged, so the ingress closes the continuation request');
  assert.equal(await guard.abortUserRun(USER), false, 'withdrawn once');
  const next = turn(guard, {runId: 'run-2', prompt: OUTPUT_LIMIT_CONTINUATION_PROMPT});
  const ctx = toolContext(next.context, 'write', 'late-write');
  const decision = guard.beforeToolCall({toolName: 'write', toolCallId: 'late-write',
    params: {path: 'Playground/forest/index.html', content: '<h1>late</h1>'}}, ctx);
  assert.equal(decision?.block, true, 'a continuation that still starts is cancelled');
  assert.deepEqual(next.grant(), INELIGIBLE);
  // The chat's next owner message is its own turn.
  const later = turn(guard, {runId: 'run-3', prompt: 'Thanks. What else can you do?'});
  assert.equal(later.event.prompt, 'Thanks. What else can you do?');
  assert.notEqual(guard.beforeToolCall({toolName: 'read', toolCallId: 'read-1', params: {path: 'notes.md'}},
    toolContext(later.context, 'read', 'read-1'))?.block, true);
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
  grantReply, second = DONE_COMPLETION, secondStatus = 200, verifications = {}, holdGrant = false, readOnlyProof,
  activity = () => null, beforeFirst, beforeSecond} = {}) {
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
        if (seen.submissions.length === 1) { await beforeFirst?.(); return json(first); }
        await beforeSecond?.();
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
      case '/pixel-ods/activity': return json({task: activity()});
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
    assert.deepEqual(f.seen.grants, [{runId: RUN_1, incompleteTurn: true}]);
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
  assert.deepEqual(f.seen.grants, [{runId: RUN_1, incompleteTurn: true}], 'the continuation run is never offered another turn');
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

// Only the plugin knows whether a run was cut (OpenClaw delivers a cut reply's
// own text after a tool error), so every completed owner turn asks, saying
// whether OpenClaw answered with its incomplete-turn text. The request is
// optional: a broken route is a refusal, never a failed turn (#6743 review:
// a bad grant answer ended the turn with a stream error and no text).
const BROKEN_GRANT_ROUTES = [
  ['reset', res => res.socket.destroy()],
  ['not json', res => { res.writeHead(200, {'content-type': 'application/json'}); res.end('<html>not json'); }],
  ['truncated json', res => { res.writeHead(200, {'content-type': 'application/json'}); res.end('{"schemaVersion":1,'); }],
];
for (const stream of [true, false]) {
  test(`an ordinary answer is delivered whatever the continuation route answers (stream=${stream})`, {timeout: 10000}, async t => {
    for (const [label, grantReply] of BROKEN_GRANT_ROUTES) {
      for (const content of ['Your forest page is published.', 'NO_REPLY']) {
        const f = await fixture(t, {first: {...DONE_COMPLETION, choices: [{index: 0,
          message: {role: 'assistant', content}, finish_reason: 'stop'}]}, grantReply});
        const result = await f.chat({stream});
        assert.equal(result.status, 200, label);
        if (stream) assert.deepEqual(result.frames.filter(frame => frame.error), [], label);
        if (content !== 'NO_REPLY') assert.equal(result.text, content, label);
        assert.deepEqual(f.seen.grants, [{runId: RUN_2, incompleteTurn: false}], label);
        assert.equal(f.seen.submissions.length, 1, label);
      }
    }
  });

  test(`a cut turn whose grant request fails delivers the cut run's report (stream=${stream})`, {timeout: 10000}, async t => {
    for (const [label, grantReply] of BROKEN_GRANT_ROUTES) {
      const f = await fixture(t, {grantReply, verifications: {[RUN_1]: HONEST}});
      const result = await f.chat({stream});
      assert.equal(result.status, 200, label);
      if (stream) {
        assert.deepEqual(result.frames.filter(frame => frame.error), [], label);
        assert.equal(result.outcome, 'failed', label);
      }
      assert.equal(result.text, OUTPUT_LIMIT_WORKSPACE_TEXT, label);
      assert.equal(f.seen.submissions.length, 1, label);
      assert.deepEqual(f.seen.verifications, [RUN_1], label);
    }
  });
}

// After a tool error OpenClaw delivers the cut reply's own text instead of its
// incomplete-turn text; the plugin decides (a workspace task is continued).
test('a cut turn that OpenClaw answered with its own text asks as such and may be continued', {timeout: 10000}, async t => {
  const opening = 'Let me build something impressive — a full forest-themed interactive experience.';
  const f = await fixture(t, {first: {...CUT_COMPLETION, choices: [{index: 0,
    message: {role: 'assistant', content: opening}, finish_reason: 'stop'}]}, verifications: {[RUN_1]: HONEST}});
  const result = await f.chat();
  assert.equal(result.text, 'Your forest page is published.');
  assert.deepEqual(f.seen.grants, [{runId: RUN_1, incompleteTurn: false}]);
  assert.equal(f.seen.submissions.length, 2);
});

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
      assert.deepEqual(f.seen.grants, [{runId: RUN_1, incompleteTurn: true}]);
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
    assert.deepEqual(f.seen.grants, [{runId: RUN_1, incompleteTurn: true}]);
    assert.deepEqual(f.seen.verifications, [RUN_1], 'the cut run is the one reported');
  });
}

// #6743 review: the Portal's live activity stayed on the cut run (running, 0
// calls) while the continuation did the turn's work, because the ingress
// poller locked onto the first run it saw.
test('the live activity follows the continuation run', {timeout: 15000}, async t => {
  const started = {}, served = {};
  let finished, secondServed;
  const secondSeen = new Promise(resolve => { secondServed = resolve; });
  const task = runId => ({schemaVersion: 1, runId, startedAt: started[runId] ??= new Date().toISOString(),
    finishedAt: runId === RUN_1 && finished ? finished : null, state: runId === RUN_1 && finished ? 'completed' : 'running',
    calls: 0, failures: 0, blocked: 0, truncated: false, activities: []});
  let current = RUN_1, firstServed;
  const firstSeen = new Promise(resolve => { firstServed = resolve; });
  const f = await fixture(t, {verifications: {[RUN_1]: HONEST},
    activity: () => {
      served[current] = (served[current] ?? 0) + 1;
      (current === RUN_1 ? firstServed : secondServed)();
      return task(current);
    },
    beforeFirst: async () => { await firstSeen; finished = new Date(Date.now() + 1).toISOString(); },
    beforeSecond: async () => { current = RUN_2; await secondSeen; }});
  const result = await f.chat();
  assert.equal(result.text, 'Your forest page is published.');
  assert.deepEqual(result.frames.filter(frame => frame.object === 'ods.task.activity').map(frame => frame.id), [RUN_1, RUN_2]);
  assert.ok(served[RUN_2] >= 1);
});

// Replays the laptop run of 2026-10-18 on the owner's API route: the model
// wrote a whole game file in one write call, the reply stopped at its
// 8,192-token output limit (stopReason "length"), the harness never ran the
// cut-off call, and Portal said only that no failure reason was returned.
// The ingress case runs over real loopback TCP (also on Windows).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createIngressServer, parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {outputLimitCut} from '../plugin/output-limit.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const RUN_ID = 'chatcmpl_8b0f4f8e-7f6d-4c1a-9f3e-6d2a1c5b7e90';
const NEXT_RUN_ID = 'chatcmpl_0c6e2d3b-1a4f-4e8d-b2c7-5f9a8e7d6c41';
const USER = 'ods-' + 'b'.repeat(64);
const OWNER = {agentId: 'pixel', runId: RUN_ID, sessionId: 'laptop-session', sessionKey: `agent:pixel:openai-user:${USER}`, trigger: 'user'};
const PROMPT = 'Make me a space shooter game I can play in the browser.';
const CAPABILITIES = {runTimeoutSeconds: 1800, executionHost: 'sandbox', workspaceRoot: '/workspace'};
// An existing project, so its files may live in game/ (run_deadline_delivery).
const PREVIEW_PROMPT = 'Fix the wolf and boss hitboxes in my game. Publish the game preview when it works.';
const HELP = 'If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';
const REMOTE = 'If your API allows longer replies, raise Max output tokens in Settings → Remote model → Advanced. ';
const stopCopy = (remote = false) => '**Portal stopped: the model reached its 8,192-token output limit while writing `space-game/game.js`.** ' +
  'That call did not run, so it did not change `space-game/game.js`. Send "continue" and Portal will write it in smaller parts. ' +
  (remote ? REMOTE : '') + HELP;
const NEXT_TURN_CONTEXT = "[ODS Pixel output limit] Your previous reply reached the model's 8192-token output limit while writing " +
  '"space-game/game.js", so that call never ran and changed nothing. If the owner still wants that work, do not send it ' +
  'as one call again: split the program into several files, or write a short first version and add the rest with edit ' +
  'calls. Keep each call under half of that limit (about 410 lines of code).';
// The laptop transcript's last reply: narration, then the cut-off write.
const CUT_WRITE = {role: 'assistant', stopReason: 'length', usage: {input: 21480, output: 8192},
  content: [{type: 'text', text: 'Now the game logic:'},
    {type: 'toolCall', id: 'write-game', name: 'write', arguments: {path: 'space-game/game.js', content: 'const canvas = document.'}}]};
const CUT_ANSWER = {role: 'assistant', stopReason: 'length', usage: {output: 8192}, content: [{type: 'text', text: 'Once upon a time the wolf'}]};
const DONE = {role: 'assistant', stopReason: 'stop', content: [{type: 'text', text: 'The game is ready.'}]};
const history = last => [{role: 'user', content: [{type: 'text', text: PROMPT}]}, last];

function fixture({context = OWNER, prompt = PROMPT, remote = false} = {}) {
  const aborts = [];
  const guard = createToolLoopGuard({
    abortRun: sessionId => { aborts.push(sessionId); return true; },
    abortRunAndDrain: async () => ({aborted: true, drained: true}),
    remoteModelRoute: () => remote,
  });
  const begin = (ctx = context, text = prompt) => guard.observeRun(ctx, 'pixel', {prompt: text}, CAPABILITIES);
  begin();
  // Each reply is written to the transcript (before_message_write), then the
  // run ends with the whole session history (agent_end).
  const end = (replies, ctx = context, extra = {}) => {
    for (const message of replies) guard.observeReplyEnd({message}, ctx, 'pixel');
    guard.observeAgentEnd({success: true, ...extra, messages: history(replies.at(-1))}, ctx);
  };
  const invoke = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    assert.notEqual(before?.block, true, before?.blockReason);
    guard.afterToolCall({toolName, params: before?.params ?? params, result, toolCallId}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
  };
  // A verified publication of game/ (the host snapshot digest the guard checks).
  const publish = () => {
    const content = '<!doctype html><title>Space</title><script src="game.js"></script>';
    invoke('write', {path: 'game/index.html', content}, {content: [{type: 'text', text: 'written'}], details: {status: 'completed'}}, 'write-index');
    const length = (bytes, value) => { const buffer = Buffer.alloc(bytes); bytes === 4 ? buffer.writeUInt32BE(value) : buffer.writeBigUInt64BE(BigInt(value)); return buffer; };
    const sha256 = createHash('sha256').update(length(4, 10)).update('index.html').update(length(8, content.length)).update(content).digest('hex');
    const siteId = `site-${sha256.slice(0, 24)}`;
    const details = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: 'game', port: 9437,
      url: `http://${siteId}.localhost:9437/${siteId}/`, siteId, files: 1, bytes: content.length, sha256, entryFile: 'index.html',
      entrySha256: createHash('sha256').update(content).digest('hex'), httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
    invoke('pixel_ods_workspace_preview', {relativeDirectory: 'game'}, {content: [{type: 'text', text: 'published'}], details}, 'preview');
    return details.url;
  };
  return {guard, aborts, begin, end, publish};
}

test('a reply cut at the limit is read from its stop reason, usage and last tool call', () => {
  assert.deepEqual(outputLimitCut(CUT_WRITE), {tokens: 8192, tool: 'write', path: 'space-game/game.js', answered: false});
  assert.equal(outputLimitCut({...CUT_WRITE, stopReason: 'toolUse'}), undefined);
  assert.equal(outputLimitCut({...CUT_WRITE, role: 'user'}), undefined);
  // Only the last call can be the cut one; an earlier call in the reply is complete.
  assert.equal(outputLimitCut({...CUT_WRITE, content: [{type: 'toolCall', id: 'a', name: 'read', arguments: {path: 'notes.md'}},
    ...CUT_WRITE.content]}).path, 'space-game/game.js');
  // Model-supplied names cannot break out of their code span, add lines or links.
  assert.deepEqual(outputLimitCut({...CUT_WRITE, usage: {}, content: [{type: 'toolCall', id: 'w',
    name: '[Open preview](https://evil.example/x)', arguments: {path: '`a`\nb.js'}}]}),
  {tokens: undefined, tool: 'tool', path: 'ab.js', answered: false});
  assert.deepEqual(outputLimitCut(CUT_ANSWER), {tokens: 8192, tool: undefined, path: undefined, answered: true});
});

test('laptop replay: the owner learns the limit, the file and the remedy instead of no reason', () => {
  const f = fixture();
  f.end([CUT_WRITE]);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.equal(verification.status, 'failed');
  assert.ok(verification.text.startsWith(stopCopy()), verification.text);
  assert.doesNotThrow(() => parseVerificationResponse(verification, RUN_ID));
  const reply = f.guard.replyPayloadSending({runId: RUN_ID, kind: 'final', payload: {text: ''}});
  assert.equal(reply.payload.text, verification.text);
});

test('on a remote API route the owner is also told where to raise the limit', () => {
  const f = fixture({remote: true});
  f.end([CUT_WRITE]);
  assert.ok(f.guard.deliveryVerificationForRun(RUN_ID).text.startsWith(stopCopy(true)));
});

test('a cut after a verified publication keeps the preview link and receipt', () => {
  const f = fixture({prompt: PREVIEW_PROMPT});
  const url = f.publish();
  f.end([CUT_WRITE]);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.equal(verification.status, 'failed');
  assert.ok(verification.text.startsWith(stopCopy()), verification.text);
  assert.ok(verification.text.includes(url), verification.text);
  assert.equal(verification.preview.url, url);
  assert.doesNotThrow(() => parseVerificationResponse(verification, RUN_ID));
});

test('a reply that spent the whole limit before answering says so', () => {
  const f = fixture({prompt: 'Explain how the hitbox code works.'});
  f.end([{role: 'assistant', stopReason: 'length', usage: {output: 8192}, content: [{type: 'thinking', thinking: 'The hitboxes are...'}]}]);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text:
    '**Portal stopped: the model used its 8,192-token output limit before it wrote an answer.** ' +
    `Send "continue" to try again. ${HELP}`});
});

test('a cut-off text answer keeps its delivery and gains one note line the ingress accepts', () => {
  for (const prompt of ['Write a long story about the wolf.', PREVIEW_PROMPT]) {
    const f = fixture({prompt, remote: true});
    if (prompt.includes('Publish')) f.publish();
    const before = f.guard.deliveryVerificationForRun(RUN_ID);
    f.end([CUT_ANSWER]);
    const verification = f.guard.deliveryVerificationForRun(RUN_ID);
    const {notice, ...rest} = verification;
    assert.deepEqual(rest, before, prompt);
    assert.equal(notice, "This reply was cut off at the model's 8,192-token output limit. " +
      'Send "continue" for the rest. If your API allows longer replies, raise Max output tokens in Settings → Remote model → Advanced.');
    assert.doesNotThrow(() => parseVerificationResponse(verification, RUN_ID));
  }
});

test('the chat\'s next owner turn is told about the cut, once', () => {
  const f = fixture();
  f.end([CUT_WRITE]);
  assert.equal(f.guard.promptContextForRun(RUN_ID), undefined, 'not the cut run itself');
  const next = {...OWNER, runId: NEXT_RUN_ID};
  f.begin(next, 'continue');
  assert.equal(f.guard.promptContextForRun(NEXT_RUN_ID), NEXT_TURN_CONTEXT);
  f.begin(next, 'continue');
  assert.equal(f.guard.promptContextForRun(NEXT_RUN_ID), NEXT_TURN_CONTEXT, 'every attempt of that run');
  const later = {...OWNER, runId: 'chatcmpl_later'};
  f.begin(later, 'Make the ship blue.');
  assert.equal(f.guard.promptContextForRun('chatcmpl_later'), undefined, 'taken once');
});

test('another chat, a cron run or a cut answer gets no turn context', () => {
  const f = fixture();
  f.end([CUT_WRITE]);
  f.begin({...OWNER, runId: NEXT_RUN_ID, sessionKey: 'agent:pixel:openai-user:other'}, 'Hello');
  assert.equal(f.guard.promptContextForRun(NEXT_RUN_ID), undefined);
  f.begin({...OWNER, runId: 'chatcmpl_cron', trigger: 'cron'}, 'Nightly check');
  assert.equal(f.guard.promptContextForRun('chatcmpl_cron'), undefined);
  const g = fixture();
  g.end([CUT_ANSWER]);
  g.begin({...OWNER, runId: NEXT_RUN_ID}, 'continue');
  assert.equal(g.guard.promptContextForRun(NEXT_RUN_ID), undefined);
});

test('only this attempt\'s replies count: an old cut in the history or a later finish clears it', () => {
  const f = fixture();
  // A precheck agent_end carries only the history, which still ends on the old cut.
  f.guard.observeAgentEnd({success: true, messages: history(CUT_WRITE)}, OWNER);
  assert.doesNotMatch(f.guard.deliveryVerificationForRun(RUN_ID).text ?? '', /output limit/);
  f.end([CUT_WRITE]);
  assert.match(f.guard.deliveryVerificationForRun(RUN_ID).text, /output limit/);
  f.begin();
  f.end([DONE]);
  assert.doesNotMatch(f.guard.deliveryVerificationForRun(RUN_ID).text ?? '', /output limit/);
  f.begin({...OWNER, runId: NEXT_RUN_ID}, 'continue');
  assert.equal(f.guard.promptContextForRun(NEXT_RUN_ID), undefined, 'the finished retry dropped the chat record');
});

for (const [name, context, prompt] of [
  ['a cron turn', {...OWNER, trigger: 'cron', sessionKey: 'agent:pixel:cron:nightly'}, PROMPT],
  ['a subagent', {...OWNER, sessionKey: 'agent:pixel:subagent:7f2c'}, PROMPT],
  ['a team worker in the owner session', OWNER, `You are the Builder in the owner's Portal team.\n\n${PROMPT}`],
]) test(`no output-limit copy for ${name}`, () => {
  const f = fixture({context, prompt});
  const before = f.guard.deliveryVerificationForRun(RUN_ID);
  f.end([CUT_WRITE], context);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), before);
});

for (const [name, last, extra] of [
  ['a completed answer', DONE, {}],
  ['a tool turn', {...CUT_WRITE, stopReason: 'toolUse'}, {}],
  ['an error', {role: 'assistant', stopReason: 'error', errorMessage: 'upstream 500', content: []}, {}],
  ['a failed run', CUT_WRITE, {success: false}],
]) test(`no output-limit copy for ${name}`, () => {
  const f = fixture();
  const before = f.guard.deliveryVerificationForRun(RUN_ID);
  f.end([last], OWNER, extra);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), before);
});

test('an owner cancel keeps the cancel outcome', async () => {
  const f = fixture();
  assert.equal(await f.guard.abortUserRun(USER), true);
  const cancelled = f.guard.deliveryVerificationForRun(RUN_ID);
  f.end([CUT_WRITE]);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), cancelled);
});

test('a budget stop keeps its own text', () => {
  const f = fixture();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const toolCallId = `failed-${i}`;
    f.guard.toolResultPersist({toolCallId, message: {role: 'toolResult', toolName: 'web_fetch', toolCallId, isError: true,
      content: [{type: 'text', text: 'Web fetch failed (403)'}]}}, {...OWNER, toolName: 'web_fetch', toolCallId});
  }
  f.end([CUT_WRITE]);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.match(verification.text, new RegExp('^' + RUN_PROGRESS_STOP_REASON.slice(0, 40)));
  assert.doesNotMatch(verification.text, /output limit/);
});

test('the plugin entry reads the remote route from the live config and observes every reply', () => {
  const entry = readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const declaration = entry.match(/remoteModelRoute: (\(\) => [\s\S]*?=== 'string'),/);
  assert.ok(declaration);
  const resolve = new Function('api', `return (${declaration[1]})();`);
  const config = settings => ({plugins: {entries: {'pixel-ods': {config: settings}}}});
  const fingerprint = 'a'.repeat(64);
  assert.equal(resolve({runtime: {config: {current: () => config({modelRouteFingerprint: fingerprint})}}, config: config({})}), true);
  assert.equal(resolve({runtime: {config: {current: () => config({})}}, config: config({modelRouteFingerprint: fingerprint})}), false);
  assert.equal(resolve({config: config({modelRouteFingerprint: fingerprint})}), true);
  assert.equal(resolve({config: {}}), false);
  assert.match(entry, /api\.on\("before_message_write", \(event, context\) => \{[^}]*toolLoopGuard\.observeReplyEnd\(event, context, AGENT_ID\);/);
});

async function listen(server) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}

for (const stream of [false, true])
test(`Portal receives the output-limit stop over the real ingress, stream=${stream}`, async t => {
  const f = fixture({remote: true});
  const task = {schemaVersion: 1, runId: RUN_ID, startedAt: '2026-10-18T14:02:11.000Z', finishedAt: '2026-10-18T14:05:40.000Z',
    state: 'completed', calls: 1, failures: 0, blocked: 0, truncated: false,
    activities: [{kind: 'edit', calls: 1, failures: 0, blocked: 0}]};
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    let result;
    switch (req.url) {
      case '/health': result = {ok: true}; break;
      case '/v1/chat/completions':
        // The cut-off reply carried no visible answer.
        f.end([CUT_WRITE]);
        result = {id: RUN_ID, choices: [{index: 0, message: {role: 'assistant', content: ''}, finish_reason: 'length'}]};
        break;
      case '/pixel-ods/verification': result = {...f.guard.deliveryVerificationForRun(body.runId), task}; break;
      case '/pixel-ods/subagent-delivery':
        result = {schemaVersion: 1, kind: 'ods-subagent-delivery', runId: body.runId, status: 'not-delegated'}; break;
      case '/pixel-ods/read-only-extension-continuation':
        result = {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false}; break;
      case '/pixel-ods/unfinished-extension-decision':
        result = {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false}; break;
      case '/pixel-ods/activity': result = {task: null}; break;
      default: res.writeHead(404); res.end(); return;
    }
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify(result));
  });
  const ingress = createIngressServer({token: 'test-gateway-token-0123456789abcdef', gatewayPort: await listen(gateway)});
  const port = await listen(ingress);
  t.after(async () => { await close(ingress); await close(gateway); });
  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
    headers: {'content-type': 'application/json', connection: 'close'}, signal: AbortSignal.timeout(5000),
    body: JSON.stringify({user: 'ods-laptop-game-20261018', stream, messages: [{role: 'user', content: PROMPT}]})});
  const body = await response.text();
  assert.equal(response.status, 200, body);
  const frames = stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6))) : [];
  const text = stream ? frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')
    : JSON.parse(body).choices[0].message.content;
  assert.ok(text.startsWith(stopCopy(true)), text);
  assert.doesNotMatch(body, /No detailed failure reason/);
  if (stream) assert.equal(frames.at(-1).pixel_outcome.status, 'failed');
});

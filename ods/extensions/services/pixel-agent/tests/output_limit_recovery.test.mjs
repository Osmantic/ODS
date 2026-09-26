// A reply that reaches the model output limit ends the run with nothing it
// was writing saved, and OpenClaw 2026.6.33 replaces it, text answers
// included, with a generic "couldn't generate a response". These tests run the
// Portal path as production does: the real ingress over loopback TCP (also on
// Windows) in front of a gateway double whose Pixel routes are served by a
// real tool-loop guard. Each model submission is one run, classified and
// observed through the hooks plugin/index.js registers; the double answers
// with the text OpenClaw returns for that run.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash, randomUUID} from 'node:crypto';
import {createToolLoopGuard, VERIFICATION_PENDING_DELIVERY_PREFIX} from '../plugin/tool-loop-guard.mjs';
import {OUTPUT_LIMIT_ANSWER_TEXT, OUTPUT_LIMIT_CONTINUATION_PROMPT, OUTPUT_LIMIT_CONTINUED_TEXT,
  OUTPUT_LIMIT_WORKSPACE_TEXT, outputLimitReply, outputLimitReport} from '../plugin/output-limit-recovery.mjs';
import {computeSessionUser, createIngressServer} from '../host/pixel_ingress.mjs';

const DELIVERY = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above.]";
// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): this request was one
// whole-page write cut after 220 s; the owner received nothing.
const SITE = 'as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest ' +
  'type effects.  Best you can do.' + DELIVERY;
const TEXT = 'Write me the longest, most detailed guide you can about how old-growth forests store carbon.' + DELIVERY;
const MIXED = '/extensions install searxng; create /workspace/report.md with the findings.';
const TEAM = "You are the Builder in the owner's Portal team. Implement the plan.";
const OPENING = 'Let me build something impressive — a full forest-themed interactive experience.';
const READY = 'Your forest page is published.';
const GUIDE = 'Old-growth forests store carbon in living wood, dead wood and deep soils; here is the concise guide.';
// Production shape: openai-completions keeps no toolCall block on a reply whose
// stopReason is not toolUse, so a cut write arrives as thinking and text only.
const CUT = {role: 'assistant', stopReason: 'length', usage: {output: 8192},
  content: [{type: 'thinking', thinking: 'Plan the whole page.'}, {type: 'text', text: OPENING}]};
const said = text => ({role: 'assistant', stopReason: 'stop', content: [{type: 'text', text}]});
// OpenClaw 2026.6.33 resolveIncompleteTurnPayloadText for a cut turn.
const GENERIC = "⚠️ Agent couldn't generate a response. Please try again.";
const MAX_TEXT = 32 * 1024;
const TOKEN = 'test-gateway-token-0123456789abcdef';

test('only an assistant reply that stopped at the output limit counts', () => {
  assert.equal(outputLimitReply(CUT), true);
  for (const message of [said(GUIDE), {...CUT, role: 'user'}, {...CUT, stopReason: 'toolUse'},
    {...CUT, stopReason: 'error'}, undefined])
    assert.equal(outputLimitReply(message), false, JSON.stringify(message));
});

// One run of the gateway double, through the hooks plugin/index.js registers.
function pixelRun(guard, {runId, user, prompt, trigger}) {
  const sessionKey = `agent:pixel:openai-user:${user}`;
  const context = {agentId: 'pixel', runId, sessionId: `session-${user}`, sessionKey, trigger};
  // before_prompt_build classifies the run (a granted continuation as the
  // owner message it continues).
  guard.observeRun(context, 'pixel', guard.outputLimitContinuationEvent(context, 'pixel', {prompt}));
  let calls = 0;
  // OpenClaw 2026.6.33 buildToolContext: the run identity, never the trigger.
  const call = (toolName, params, result) => {
    const toolCallId = `call-${++calls}`;
    const ctx = {agentId: 'pixel', sessionKey, sessionId: context.sessionId, runId, toolName, toolCallId};
    const decision = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    const executed = decision?.params ?? params;
    guard.afterToolCall({toolName, params: executed, toolCallId, result}, ctx);
    return executed;
  };
  // A deferred Tool Search call, as OpenClaw wraps core and plugin tools.
  const wrapped = (name, args, result) => {
    const sourceName = ['read', 'write', 'edit', 'exec', 'process'].includes(name) ? 'core' : 'pixel-ods';
    const id = `openclaw:${sourceName}:${name}`;
    return call('tool_call', {id, args}, {details: {tool: {id, name, source: 'openclaw', sourceName}, result}});
  };
  return {
    call, wrapped,
    exec: exitCode => wrapped('exec', {command: `python3 -c 'raise SystemExit(${exitCode})'`, workdir: '/workspace'},
      {content: [{type: 'text', text: exitCode ? `Command exited with code ${exitCode}` : 'ok'}],
        details: {status: 'completed', exitCode}}),
    // before_message_write carries only the agent and the session key.
    reply: message => guard.observeAssistantMessage({message}, {agentId: 'pixel', sessionKey}, 'pixel'),
  };
}

// A failed wrapped exec followed by a successful one: the guard marks
// OpenClaw's retained exec warning as stale (suppressStaleExecWarning), and
// OpenClaw keeps it as the run's lastToolError, so it delivers a cut reply's
// own text instead of its incomplete-turn text.
function staleExecWarning(run) {
  run.exec(7);
  run.exec(0);
}

// The same three-file page the continuation writes, published and verified.
function publishSplitPage(run) {
  const directory = 'Playground/forest';
  const files = [
    {path: `${directory}/index.html`, content: '<!doctype html><link rel="stylesheet" href="styles.css"><script src="script.js" defer></script><h1>Forest</h1>'},
    {path: `${directory}/styles.css`, content: 'body{background:#0b3d20;color:#e8f5e9}'},
    {path: `${directory}/script.js`, content: 'document.body.dataset.fireflies = "on";'},
  ];
  for (const file of files) run.call('write', file, {content: [{type: 'text', text: 'ok'}], details: {status: 'completed'}});
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
  run.call('pixel_ods_workspace_preview', {relativeDirectory: directory}, {details: {
    schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: directory,
    siteId, files: files.length, bytes, sha256, entryFile: 'index.html',
    entrySha256: createHash('sha256').update(files[0].content).digest('hex'), port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true,
    executable: false, overwritten: false}});
}

// The real ingress in front of a loopback gateway double. `route(url, body)`
// answers the gateway's routes with a JSON value, {httpStatus} or undefined
// (404).
async function ingressWith(t, route) {
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const answer = req.url === '/health' ? {ok: true} : await route(req.url, raw ? JSON.parse(raw) : null);
    if (answer === undefined) { res.writeHead(404); return res.end(); }
    if (answer.httpStatus) {
      res.writeHead(answer.httpStatus, {'content-type': 'application/json'});
      return res.end('{"error":{"message":"unavailable"}}');
    }
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify(answer));
  });
  const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
  const ingress = createIngressServer({token: TOKEN, gatewayPort: await listen(gateway)});
  const port = await listen(ingress);
  t.after(async () => {
    for (const server of [ingress, gateway]) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  });
  return async (prompt, {stream = true} = {}) => {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      signal: AbortSignal.timeout(8000), headers: {'content-type': 'application/json', connection: 'close'},
      body: JSON.stringify({user: 'output-limit-report', stream, messages: [{role: 'user', content: prompt}]})});
    const body = await response.text();
    if (!stream) return {status: response.status, body, text: response.status === 200 ? JSON.parse(body).choices[0].message.content : undefined};
    const frames = body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)));
    return {status: response.status, body, errors: frames.filter(frame => frame.error), outcome: frames.at(-1)?.pixel_outcome?.status,
      text: frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')};
  };
}

const completion = (id, content) => ({id, object: 'chat.completion',
  choices: [{index: 0, message: {role: 'assistant', content}, finish_reason: 'stop'}]});
const INELIGIBLE_READ_ONLY = {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false};
const INELIGIBLE_DECISION = {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false};

// The gateway double backed by a real guard: `scripts` are the model runs in
// submission order; each returns the text OpenClaw answers with.
async function portal(t, scripts, {trigger = 'user'} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true});
  const seen = {runs: [], prompts: [], grants: [], receipts: []};
  const chat = await ingressWith(t, async (url, body) => {
    switch (url) {
      case '/v1/chat/completions': {
        const runId = `chatcmpl_${randomUUID()}`, prompt = body.messages.at(-1).content;
        const script = scripts[seen.runs.length];
        seen.runs.push(runId);
        seen.prompts.push(prompt);
        return completion(runId, script(pixelRun(guard, {runId, user: body.user, prompt, trigger})));
      }
      case '/pixel-ods/verification': {
        await guard.settleDelivery(body.runId);
        const receipt = guard.deliveryVerificationForRun(body.runId);
        seen.receipts.push({runId: body.runId, receipt});
        return receipt;
      }
      case '/pixel-ods/output-limit-continuation': {
        const grant = guard.outputLimitContinuationForRun(body.runId);
        seen.grants.push({runId: body.runId, eligible: grant.eligible});
        return grant;
      }
      // No extension backend here: these continuations are never granted.
      case '/pixel-ods/read-only-extension-continuation': return INELIGIBLE_READ_ONLY;
      case '/pixel-ods/unfinished-extension-decision': return INELIGIBLE_DECISION;
      case '/pixel-ods/activity': return {task: null};
      default: return undefined;
    }
  });
  return {guard, seen, chat};
}

const cutTurn = run => { run.reply(CUT); return GENERIC; };

// #6742 review: a receipt forced to failed kept suppressStaleExecWarning, which
// the ingress accepts only on a none or passed receipt, so the owner got an
// HTTP 502 (or an SSE error) instead of any answer.
for (const stream of [false, true]) {
  test(`a cut reply after a stale exec warning is reported, not rejected (stream=${stream})`, {timeout: 10000}, async t => {
    const {seen, chat} = await portal(t, [run => { staleExecWarning(run); run.reply(CUT); return OPENING; }]);
    const result = await chat(TEXT, {stream});
    assert.equal(result.status, 200, result.body);
    assert.deepEqual(result.errors ?? [], [], result.body);
    assert.equal(result.text, OUTPUT_LIMIT_ANSWER_TEXT);
    if (stream) assert.equal(result.outcome, 'failed');
    assert.deepEqual(seen.receipts.map(({receipt}) => receipt), [{status: 'failed', text: OUTPUT_LIMIT_ANSWER_TEXT}]);
    assert.deepEqual(seen.grants, [], 'OpenClaw delivered text, so the ingress asks for no continuation');
  });

  test(`a lane stop after a stale exec warning is reported, not rejected (stream=${stream})`, {timeout: 10000}, async t => {
    const answer = 'The report is saved; the extension step failed.';
    const {seen, chat} = await portal(t, [run => {
      staleExecWarning(run);
      for (let i = 0; i < 4; i++) run.wrapped('pixel_ods_extension_request_prepare', {},
        {isError: true, content: [{type: 'text', text: 'Repository evidence unavailable'}]});
      run.reply(said(answer));
      return answer;
    }]);
    const result = await chat(MIXED, {stream});
    assert.equal(result.status, 200, result.body);
    assert.deepEqual(result.errors ?? [], [], result.body);
    assert.match(result.text, /^The extension portion of this response stopped after repeated failures\./);
    if (stream) assert.equal(result.outcome, 'failed');
    const [{receipt}] = seen.receipts;
    assert.equal(receipt.status, 'failed');
    assert.equal(Object.hasOwn(receipt, 'suppressStaleExecWarning'), false);
  });
}

// Each kind of request gets words that fit it, whether no continuation ran
// (OpenClaw delivered the cut reply's text) or the continuation was cut too.
for (const [kind, prompt, report] of [['text answer', TEXT, OUTPUT_LIMIT_ANSWER_TEXT],
  ['workspace task', SITE, OUTPUT_LIMIT_WORKSPACE_TEXT]]) {
  const words = text => {
    if (kind === 'text answer') assert.doesNotMatch(text, /\bfiles?\b|\bwrit|workspace|\bsaved\b|\bsteps?\b/i, text);
    else assert.match(text, /such as a large file, was not saved/, text);
  };

  test(`a cut ${kind} without a continuation is reported in its own words`, {timeout: 10000}, async t => {
    const {seen, chat} = await portal(t, [run => { staleExecWarning(run); run.reply(CUT); return OPENING; }]);
    const result = await chat(prompt);
    assert.equal(result.status, 200, result.body);
    assert.equal(result.outcome, 'failed');
    assert.equal(result.text.split('\n\n')[0], report);
    if (kind === 'text answer') assert.equal(result.text, report, 'no receipt text of its own');
    words(result.text);
    assert.deepEqual(seen.grants, []);
  });

  test(`a ${kind} cut again after its continuation is reported in its own words`, {timeout: 10000}, async t => {
    const {seen, chat} = await portal(t, [cutTurn, cutTurn]);
    const result = await chat(prompt);
    assert.equal(result.status, 200, result.body);
    assert.equal(result.outcome, 'failed');
    assert.equal(result.text.split('\n\n')[0], `${OUTPUT_LIMIT_CONTINUED_TEXT} ${report}`);
    words(result.text);
    assert.equal(seen.prompts[1], OUTPUT_LIMIT_CONTINUATION_PROMPT, 'the continuation ran first');
    assert.deepEqual(seen.grants, [{runId: seen.runs[0], eligible: true}], 'never continued twice');
    assert.deepEqual(seen.receipts.map(({runId}) => runId), [seen.runs[1]], 'the continuation run is reported');
  });
}

// The continuation comes first: once it completes, the cut run's report is
// never delivered.
test('a cut workspace task that is continued delivers the continuation, not the report', {timeout: 10000}, async t => {
  const {seen, chat} = await portal(t, [cutTurn,
    run => { publishSplitPage(run); run.reply(said(READY)); return READY; }]);
  const result = await chat(SITE);
  assert.equal(result.status, 200, result.body);
  assert.equal(result.outcome, 'passed');
  assert.ok(result.text.startsWith(`${READY}\n\n`), result.text);
  assert.doesNotMatch(result.text, /output limit/);
  assert.deepEqual(seen.prompts, [SITE, OUTPUT_LIMIT_CONTINUATION_PROMPT]);
  assert.deepEqual(seen.grants, [{runId: seen.runs[0], eligible: true}]);
  assert.deepEqual(seen.receipts.map(({runId}) => runId), [seen.runs[1]]);
  assert.equal(seen.receipts[0].receipt.preview.files, 3);
});

test('a cut text answer that is continued delivers the concise answer', {timeout: 10000}, async t => {
  const {seen, chat} = await portal(t, [cutTurn, run => { run.reply(said(GUIDE)); return GUIDE; }]);
  const result = await chat(TEXT);
  assert.equal(result.status, 200, result.body);
  assert.equal(result.text, GUIDE);
  assert.notEqual(result.outcome, 'failed');
  assert.deepEqual(seen.receipts.map(({runId}) => runId), [seen.runs[1]]);
});

test('a later complete reply in the same run clears the report', {timeout: 10000}, async t => {
  const {seen, chat} = await portal(t, [run => { run.reply(CUT); run.reply(said(GUIDE)); return GUIDE; }]);
  const result = await chat(TEXT);
  assert.equal(result.text, GUIDE);
  assert.notEqual(result.outcome, 'failed');
  assert.deepEqual(seen.grants, []);
});

// Only an owner chat turn is reported: a heartbeat, cron or team-worker run
// keeps the receipt it would have had without the cut, at the ingress and on
// the channel delivery hook.
test('heartbeat, cron and team-worker receipts are unchanged by a cut reply', {timeout: 30000}, async t => {
  for (const [label, prompt, trigger] of [['heartbeat', TEXT, 'heartbeat'], ['cron', TEXT, 'cron'],
    ['team worker', TEAM, 'user']]) {
    await t.test(label, async t => {
      const served = [];
      for (const cut of [true, false]) {
        const {guard, seen, chat} = await portal(t, [run => { run.reply(cut ? CUT : said(GUIDE)); return cut ? GENERIC : GUIDE; }],
          {trigger});
        const result = await chat(prompt, {stream: false});
        assert.equal(result.status, 200, result.body);
        assert.doesNotMatch(result.text, /output limit/i);
        assert.equal(guard.replyPayloadSending({runId: seen.runs[0], kind: 'final', payload: {text: OPENING}}), undefined,
          'the channel delivery keeps the run reply');
        assert.deepEqual(seen.grants.map(({eligible}) => eligible), cut ? [false] : []);
        served.push(seen.receipts[0].receipt);
      }
      assert.deepEqual(served[0], served[1], 'the same receipt as without the cut');
    });
  }
});

// A receipt that still waits on the host (here a test run that has not
// finished) decides what happens next; the cut does not turn it into a
// failure, and the turn is not continued.
test('a receipt still waiting on its verification is unchanged by a cut reply', {timeout: 10000}, async t => {
  const {seen, chat} = await portal(t, [run => {
    run.call('write', {path: 'calc.py', content: 'def add(a, b):\n    return a + b\n'},
      {content: [{type: 'text', text: 'ok'}], details: {status: 'completed'}});
    run.call('exec', {command: 'python3 -m unittest -v', workdir: '/workspace'},
      {content: [{type: 'text', text: 'running'}], details: {status: 'running', sessionId: 'unittest-1'}});
    run.reply(CUT);
    return GENERIC;
  }]);
  const result = await chat('Write a Python script calc.py with unit tests in test_calc.py and run the tests.');
  assert.equal(result.status, 200, result.body);
  assert.equal(result.outcome, 'pending');
  assert.equal(result.text, VERIFICATION_PENDING_DELIVERY_PREFIX);
  assert.deepEqual(seen.grants, [{runId: seen.runs[0], eligible: false}]);
});

// The ingress rejects receipt text over 32 KiB (MAX_VERIFICATION_TEXT).
test('the report and the receipt it leads stay within the 32 KiB ingress bound', {timeout: 20000}, async t => {
  const RUN = `chatcmpl_${randomUUID()}`;
  const deliver = async receipt => (await ingressWith(t, async url => url === '/v1/chat/completions' ? completion(RUN, OPENING)
    : url === '/pixel-ods/verification' ? receipt
      : url === '/pixel-ods/output-limit-continuation' ? {schemaVersion: 1, kind: 'ods-output-limit-continuation', eligible: false}
        : url === '/pixel-ods/read-only-extension-continuation' ? INELIGIBLE_READ_ONLY
          : url === '/pixel-ods/unfinished-extension-decision' ? INELIGIBLE_DECISION
            : url === '/pixel-ods/activity' ? {task: null} : undefined))(SITE, {stream: false});
  const near = {status: 'failed', text: 'x'.repeat(MAX_TEXT - 64)};
  // Control: prepending the report to a receipt that already nearly fills the
  // bound is rejected.
  assert.equal((await deliver({...near, text: `${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\n${near.text}`})).status, 502);
  const alone = outputLimitReport(near, {workspace: true}, MAX_TEXT);
  assert.deepEqual(alone, {status: 'failed', text: OUTPUT_LIMIT_WORKSPACE_TEXT}, 'the report alone');
  const delivered = await deliver(alone);
  assert.equal(delivered.status, 200, delivered.body);
  assert.equal(delivered.text, OUTPUT_LIMIT_WORKSPACE_TEXT);
  const edge = {status: 'failed', text: 'y'.repeat(MAX_TEXT - OUTPUT_LIMIT_WORKSPACE_TEXT.length - 2)};
  const both = outputLimitReport(edge, {workspace: true}, MAX_TEXT);
  assert.equal(both.text.length, MAX_TEXT, 'a receipt that fits is kept');
  assert.equal((await deliver(both)).text, `${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\n${edge.text}`);
  assert.deepEqual(outputLimitReport({status: 'none', suppressStaleExecWarning: true}, {}, MAX_TEXT),
    {status: 'failed', text: OUTPUT_LIMIT_ANSWER_TEXT}, 'no passed-only field on the failed receipt');
});

test('a continuation the gateway does not complete keeps the bounded report', {timeout: 10000}, async t => {
  const RUN_1 = `chatcmpl_${randomUUID()}`;
  const receipt = outputLimitReport({status: 'failed', text: 'z'.repeat(MAX_TEXT - OUTPUT_LIMIT_WORKSPACE_TEXT.length - 40)},
    {workspace: true}, MAX_TEXT);
  assert.ok(receipt.text.length <= MAX_TEXT && receipt.text.startsWith(`${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\nzzz`));
  let submissions = 0;
  const chat = await ingressWith(t, async url => {
    switch (url) {
      case '/v1/chat/completions': return ++submissions === 1 ? completion(RUN_1, GENERIC) : {httpStatus: 503};
      case '/pixel-ods/output-limit-continuation': return {schemaVersion: 1, kind: 'ods-output-limit-continuation',
        eligible: true, user: computeSessionUser({user: 'output-limit-report'})};
      case '/pixel-ods/verification': return receipt;
      case '/pixel-ods/read-only-extension-continuation': return INELIGIBLE_READ_ONLY;
      case '/pixel-ods/unfinished-extension-decision': return INELIGIBLE_DECISION;
      case '/pixel-ods/activity': return {task: null};
      default: return undefined;
    }
  });
  const result = await chat(SITE);
  assert.equal(result.status, 200, result.body);
  assert.equal(result.outcome, 'failed');
  assert.equal(submissions, 2, 'one continuation, never retried');
  assert.equal(result.text, `${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\nPixel's automatic continuation after the output limit ` +
    'did not complete (gateway HTTP 503). It may have acted before it stopped; check what it changed before asking Pixel to continue.');
  assert.ok(result.text.length <= MAX_TEXT);
});

// Replays the laptop run of 2026-10-18: after a verified host observation the
// model kept calling a tool outside the Operations boundary, the guard aborted
// the run ("Pixel stopped a tool retry after the Operations boundary ...
// aborted=true"), and the harness's own error for the aborted request reached
// the owner as "LLM request timed out." above the verified host facts. The
// ingress case runs over real loopback TCP (also on Windows).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createIngressServer, parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard, OPERATIONS_LOOP_ABORT_REASON, OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX,
  operationsLoopDeliveryText, operationsLoopVerifiedText} from '../plugin/tool-loop-guard.mjs';

const RUN_ID = 'chatcmpl_5e2b7c1d-3f4a-4b6c-8d9e-0a1b2c3d4e5f';
const USER = 'ods-' + 'c'.repeat(64);
const CTX = {agentId: 'pixel', runId: RUN_ID, sessionId: 'laptop-session', sessionKey: `agent:pixel:openai-user:${USER}`, trigger: 'user'};
const PROMPT = 'Check the ODS host hostname.';
const TIMEOUT_TEXT = 'LLM request timed out.';
const HOST_RECEIPT = {details: {jobId: 'ops-1234567890123-abcdef123456', status: 'succeeded', waitTimedOut: false,
  steps: [{stepId: 'observe-1', target: 'ods-host', action: 'host.identity', exitCode: 0, stdout: 'test-host\n', stderr: '',
    outputTruncated: {stdout: false, stderr: false}, riskSignals: []}]}};
const HELP = 'If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';

function fixture({abortRun = () => true} = {}) {
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: sessionId => { aborts.push(sessionId); return abortRun(sessionId); },
    abortRunAndDrain: async () => ({aborted: true, drained: true})});
  guard.observeRun(CTX, 'pixel', {prompt: PROMPT});
  let round = 0;
  const nextRound = () => guard.observeModelCall({runId: RUN_ID, callId: `call-${++round}`}, CTX);
  const observeHost = () => {
    const params = {actions: ['host.identity']};
    const ctx = {...CTX, toolName: 'pixel_ods_host_observe'};
    assert.notEqual(guard.beforeToolCall({toolName: 'pixel_ods_host_observe', params}, ctx, 'pixel')?.block, true);
    guard.afterToolCall({toolName: 'pixel_ods_host_observe', params, result: HOST_RECEIPT}, ctx, 'pixel');
  };
  const message = () => guard.beforeToolCall({toolName: 'message', params: {action: 'send', message: 'done'}},
    {...CTX, toolName: 'message'}, 'pixel');
  return {guard, aborts, nextRound, observeHost, message};
}

// The verified host facts, then the model's repeated off-boundary call.
function abortAfterEvidence(f) {
  f.nextRound();
  f.observeHost();
  assert.equal(f.guard.verificationForRun(RUN_ID).status, 'passed');
  f.nextRound();
  assert.equal(f.message()?.block, true, 'corrected once');
  assert.deepEqual(f.aborts, []);
  f.nextRound();
  assert.equal(f.message()?.blockReason, OPERATIONS_LOOP_ABORT_REASON);
  assert.deepEqual(f.aborts, [CTX.sessionId]);
}

test('laptop replay: the verified facts stand, under why Portal stopped, instead of a timeout', () => {
  const f = fixture();
  abortAfterEvidence(f);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  const cause = operationsLoopVerifiedText({tool: 'message', boundary: 'operations'});
  assert.equal(cause, '**Portal stopped this response after verifying the result below:** this request needed ODS ' +
    'Operations work on this computer, and the model kept calling `message`, which that step does not allow, so Portal ' +
    'ended the response rather than let it loop. That call did not run. If you need more, such as a file, ask for it in ' +
    `a new message. ${HELP}`);
  assert.equal(verification.status, 'passed', 'the host facts were verified');
  assert.ok(verification.text.startsWith(`${cause}\n\n`), verification.text);
  assert.match(verification.text, /Hostname: `test-host`/);
  assert.equal(verification.deliveryMode, undefined, 'replaces the harness error instead of appending to it');
  assert.doesNotThrow(() => parseVerificationResponse(verification, RUN_ID));
  const reply = f.guard.replyPayloadSending({runId: RUN_ID, kind: 'final', payload: {text: TIMEOUT_TEXT, isError: true}});
  assert.equal(reply.payload.text, verification.text);
});

test('the stop copy names the boundary, and a combined request is asked in two messages', () => {
  assert.equal(operationsLoopDeliveryText({tool: 'pixel_ods_workspace_artifact', boundary: 'operations'}),
    '**Portal stopped this response:** this request needed ODS Operations work on this computer, and the model kept ' +
    'calling `pixel_ods_workspace_artifact`, which that step does not allow, so Portal ended the response rather than ' +
    'let it loop. That call did not run. The conversation and any saved files are preserved. Send a new message to try ' +
    `again, and ask for a file or other work in a message of its own. ${HELP}`);
  assert.match(operationsLoopDeliveryText({tool: 'pixel_ops_inventory', boundary: 'continuation'}),
    /waiting for the result of an ODS Operations job, and the model called `pixel_ops_inventory` instead of reading it/);
});

test('a model-written tool name reaches the owner only as a plain identifier', () => {
  for (const tool of ['evil`](https://x.test)', 'visit-help.example.com', 'Message', undefined]) {
    assert.match(operationsLoopDeliveryText({tool, boundary: 'operations'}), /kept calling another tool,/, String(tool));
  }
});

test('without host evidence the existing Operations failure is kept', () => {
  const f = fixture();
  f.nextRound();
  assert.equal(f.message()?.block, true);
  f.nextRound();
  assert.equal(f.message()?.blockReason, OPERATIONS_LOOP_ABORT_REASON);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX});
});

test('a correction alone, a refused abort, or a later completed model call changes nothing', () => {
  const corrected = fixture();
  corrected.nextRound();
  corrected.observeHost();
  corrected.nextRound();
  corrected.message();
  assert.equal(corrected.guard.deliveryVerificationForRun(RUN_ID).deliveryMode, 'append');

  const refused = fixture({abortRun: () => false});
  refused.nextRound(); refused.observeHost(); refused.nextRound(); refused.message(); refused.nextRound();
  assert.equal(refused.message()?.blockReason, OPERATIONS_LOOP_ABORT_REASON);
  assert.equal(refused.guard.deliveryVerificationForRun(RUN_ID).deliveryMode, 'append', 'the run went on; its own answer leads');

  const resumed = fixture();
  abortAfterEvidence(resumed);
  resumed.guard.observeModelEnd({callId: 'call-4', outcome: 'completed'}, CTX);
  assert.equal(resumed.guard.deliveryVerificationForRun(RUN_ID).deliveryMode, 'append', 'a later reply completed');
});

test('an owner cancel keeps the cancel outcome', async () => {
  const f = fixture();
  f.nextRound();
  f.observeHost();
  assert.equal(await f.guard.abortUserRun(USER), true);
  const cancelled = f.guard.deliveryVerificationForRun(RUN_ID);
  f.nextRound(); f.message(); f.nextRound(); f.message();
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), cancelled);
  assert.doesNotMatch(cancelled.text ?? '', /Portal stopped this response/);
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
test(`Portal receives the cause instead of the timeout text over the real ingress, stream=${stream}`, async t => {
  const f = fixture();
  const task = {schemaVersion: 1, runId: RUN_ID, startedAt: '2026-10-18T15:20:01.000Z', finishedAt: '2026-10-18T15:21:12.000Z',
    state: 'failed', calls: 1, failures: 0, blocked: 0, truncated: false,
    activities: [{kind: 'read', calls: 1, failures: 0, blocked: 0}]};
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    let result;
    switch (req.url) {
      case '/health': result = {ok: true}; break;
      case '/v1/chat/completions':
        abortAfterEvidence(f);
        result = {id: RUN_ID, choices: [{index: 0, message: {role: 'assistant', content: TIMEOUT_TEXT}, finish_reason: 'stop'}]};
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
    body: JSON.stringify({user: 'ods-laptop-ops-20261018', stream, messages: [{role: 'user', content: PROMPT}]})});
  const body = await response.text();
  assert.equal(response.status, 200, body);
  const frames = stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6))) : [];
  const text = stream ? frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')
    : JSON.parse(body).choices[0].message.content;
  assert.ok(text.startsWith('**Portal stopped this response after verifying the result below:**'), text);
  assert.match(text, /Hostname: `test-host`/);
  assert.doesNotMatch(body, /LLM request timed out/);
});

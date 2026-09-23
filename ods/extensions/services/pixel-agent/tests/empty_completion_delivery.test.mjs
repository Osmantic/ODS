// Exercise the real trusted ingress over loopback TCP (also runs on Windows).
// These are deterministic protocol tests, not model/hardware acceptance tests.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createIngressServer } from '../host/pixel_ingress.mjs';

const RUN_ID = 'chatcmpl_e5261a35-a837-4170-b2a1-0023b46e312c';
const CHAT_ID = 'ods-tower3-missing-preview-8860-20260923b';
const PROMPT = `The directory Playground/${CHAT_ID} intentionally does not exist. Try to publish a workspace preview of that exact directory only. Do not create any files or directories, do not use shell commands, and do not contact external sites. If it cannot be published, tell me the actual reason plainly and do not invent a preview URL.`;
const SILENT = ['', ' \n\t ', 'NO_REPLY', 'No response from OpenClaw.',
  "⚠️ Agent couldn't generate a response. Please try again."];
const TASK = { schemaVersion: 1, runId: RUN_ID, startedAt: '2026-09-23T12:00:00.000Z',
  finishedAt: '2026-09-23T12:00:01.000Z', state: 'completed', calls: 0, failures: 0,
  blocked: 0, truncated: false, activities: [] };
const SHA = 'a'.repeat(64), SITE_ID = `site-${SHA.slice(0, 24)}`;
const PREVIEW = { schemaVersion: 1, kind: 'ods-pixel-workspace-preview',
  relativeDirectory: 'existing-result', siteId: SITE_ID, port: 9437,
  url: `http://${SITE_ID}.localhost:9437/${SITE_ID}/`, files: 3, bytes: 4096,
  sha256: SHA, entrySha256: 'b'.repeat(64) };

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
async function fixture(t, { content = '', verification = { status: 'none', task: TASK },
  completion, holdCompletion = false, holdVerification = false } = {}) {
  const observed = { submissions: [], verificationReads: 0, aborts: 0 };
  let onSubmitted, onVerification;
  const submitted = new Promise(resolve => { onSubmitted = resolve; });
  const verificationStarted = new Promise(resolve => { onVerification = resolve; });
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    let result;
    switch (req.url) {
      case '/health': result = { ok: true }; break;
      case '/v1/chat/completions':
        observed.submissions.push(body);
        onSubmitted();
        if (holdCompletion) return;
        result = completion ?? { id: RUN_ID, choices: [{ index: 0,
          message: { role: 'assistant', content }, finish_reason: 'stop' }] };
        break;
      case '/pixel-ods/verification':
        observed.verificationReads++;
        onVerification();
        if (holdVerification) return;
        result = verification;
        break;
      case '/pixel-ods/read-only-extension-continuation':
        result = { schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false };
        break;
      case '/pixel-ods/unfinished-extension-decision':
        result = { schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false };
        break;
      case '/pixel-ods/activity': result = { task: null }; break;
      case '/pixel-ods/abort': observed.aborts++; result = { aborted: true }; break;
      default: res.writeHead(404); res.end(); return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(result));
  });
  const gatewayPort = await listen(gateway);
  const ingress = createIngressServer({ token: 'test-gateway-token-0123456789abcdef', gatewayPort });
  const port = await listen(ingress);
  t.after(async () => { await close(ingress); await close(gateway); });
  return { observed, submitted, verificationStarted,
    post: (route, body) => fetch(`http://127.0.0.1:${port}${route}`, {
      method: 'POST', headers: { 'content-type': 'application/json', connection: 'close' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
    }),
  };
}
async function complete(f, { stream = true, prompt = PROMPT } = {}) {
  const response = await f.post('/v1/chat/completions', {
    user: CHAT_ID, stream, messages: [{ role: 'user', content: prompt }],
  });
  const body = await response.text();
  const frames = stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {'))
    .map(line => JSON.parse(line.slice(6))) : [];
  const json = stream ? null : JSON.parse(body);
  return { response, body, frames, json, terminal: frames.at(-1),
    text: stream ? frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')
      : json.choices?.[0]?.message?.content };
}
function assertOneUnchangedRequest(f) {
  assert.equal(f.observed.submissions.length, 1, 'never replay the owner request');
  assert.equal(f.observed.submissions[0].stream, false, 'only terminal upstream completion');
  assert.equal(f.observed.submissions[0].messages.at(-1).content, PROMPT);
}

for (const content of SILENT) {
  for (const stream of [false, true]) {
    test(`missing visible output is incomplete without replay: ${JSON.stringify(content)}, stream=${stream}`, async t => {
      const f = await fixture(t, { content });
      const result = await complete(f, { stream });
      assert.equal(result.response.status, 200);
      assert.match(result.text, /request is incomplete/);
      assert.match(result.text, /Earlier tool activity may have completed/);
      assert.match(result.text, /No detailed failure reason was returned/);
      assert.doesNotMatch(result.text, /NO_REPLY|No response from OpenClaw|couldn't generate|missing directory|does not exist|https?:\/\//);
      if (stream) {
        assert.deepEqual(result.terminal.pixel_outcome, { schemaVersion: 1, status: 'failed' });
        assert.deepEqual(result.terminal.pixel_task, TASK, 'lifecycle receipt stays factual');
      }
      assertOneUnchangedRequest(f);
    });
  }
}

test('missing answer preserves real effects and failure counters without inventing their cause', async t => {
  const task = { ...TASK, calls: 2, failures: 1, activities: [
    { kind: 'edit', calls: 1, failures: 0, blocked: 0 },
    { kind: 'run', calls: 1, failures: 1, blocked: 0 },
  ] };
  const f = await fixture(t, { content: 'NO_REPLY', verification: { status: 'none', task } });
  const result = await complete(f);
  assert.deepEqual(result.terminal.pixel_task, task);
  assert.equal(result.terminal.pixel_outcome.status, 'failed');
  assert.match(result.text, /check its receipts before repeating any action/);
  assert.doesNotMatch(result.text, /no tools ran|no specific tool.*failure|missing directory/i);
  assertOneUnchangedRequest(f);
});

for (const content of SILENT) {
  test(`trusted appended preview survives missing model text without sentinel leak: ${JSON.stringify(content)}`, async t => {
    const receipt = 'The existing snapshot was verified; this does not prove other requested work completed.';
    const f = await fixture(t, { content,
      verification: { status: 'passed', text: receipt, deliveryMode: 'append', preview: PREVIEW, task: TASK } });
    const result = await complete(f);
    assert.equal(result.text, receipt);
    assert.deepEqual(result.terminal.pixel, { schemaVersion: 1, preview: PREVIEW });
    assert.equal(result.terminal.pixel_outcome.status, 'passed');
    assertOneUnchangedRequest(f);
  });
}

for (const status of ['pending', 'failed']) {
  test(`authoritative ${status} receipt remains unchanged despite empty model output`, async t => {
    const text = status === 'pending' ? 'Waiting for the approved operation to finish.'
      : 'The observed publication receipt reports that the requested directory was not found.';
    const f = await fixture(t, { verification: { status, text, task: TASK } });
    const result = await complete(f);
    assert.equal(result.text, text);
    assert.equal(result.terminal.pixel_outcome.status, status);
    assertOneUnchangedRequest(f);
  });
}

test('passed tool evidence without a delivered answer remains factual but does not imply task completion', async t => {
  const f = await fixture(t, { verification: { status: 'passed', task: TASK, suppressStaleExecWarning: true } });
  const result = await complete(f);
  assert.equal(result.terminal.pixel_outcome.status, 'failed');
  assert.deepEqual(result.terminal.pixel_task, TASK);
  assert.match(result.text, /request is incomplete/);
  assertOneUnchangedRequest(f);
});

for (const content of ['Hello! How can I help?', 'OK', 'The literal token is NO_REPLY.', 'I checked the report; the total is 12.']) {
  test(`normal no-tool conversation or meaningful answer is unchanged: ${content}`, async t => {
    const f = await fixture(t, { content });
    const result = await complete(f, { prompt: 'Reply exactly or answer normally.' });
    assert.equal(result.text, content);
    assert.equal(result.terminal.pixel_outcome.status, 'none');
    assert.equal(f.observed.submissions.length, 1);
  });
}

test('genuine successful answer retains its appended evidence and scope', async t => {
  const f = await fixture(t, { content: 'I updated the report and checked the total.', verification: {
    status: 'passed', text: 'The saved report was independently read back.', deliveryMode: 'append', task: TASK,
  } });
  const result = await complete(f);
  assert.match(result.text, /^I updated the report and checked the total\./);
  assert.match(result.text, /The saved report was independently read back/);
  assert.match(result.text, /does not establish completion of other requested work/);
  assert.equal(result.terminal.pixel_outcome.status, 'passed');
  assertOneUnchangedRequest(f);
});

for (const stream of [false, true]) {
  test(`pending receipt missing required text is invalid, not an inferred task failure: stream=${stream}`, async t => {
    const f = await fixture(t, { verification: { status: 'pending', task: TASK } });
    const result = await complete(f, { stream });
    if (stream) {
      assert.equal(result.terminal.error.type, 'pixel_ingress_error');
      assert.equal(result.terminal.pixel_outcome, undefined);
    } else {
      assert.equal(result.response.status, 502);
      assert.equal(result.json.error.message, 'verification state unavailable');
    }
    assert.doesNotMatch(result.body, /request is incomplete/);
    assertOneUnchangedRequest(f);
  });
  for (const message of [{ role: 'assistant', content: null }, { role: 'assistant' }]) {
    test(`malformed missing/non-string content is invalid upstream, not a silent completion: ${JSON.stringify(message)}, stream=${stream}`, async t => {
      const f = await fixture(t, { completion: { id: RUN_ID, choices: [{ message, finish_reason: 'stop' }] } });
      const result = await complete(f, { stream });
      if (stream) {
        assert.equal(result.terminal.error.type, 'pixel_ingress_error');
        assert.equal(result.terminal.pixel_outcome, undefined);
      } else {
        assert.equal(result.response.status, 502);
        assert.equal(result.json.error.message, 'invalid upstream response');
      }
      assert.doesNotMatch(result.body, /request is incomplete/);
      assertOneUnchangedRequest(f);
    });
  }
}

for (const stage of ['completion', 'verification']) {
  test(`cancellation during ${stage} does not manufacture a missing-answer outcome or replay`, { timeout: 8000 }, async t => {
    const f = await fixture(t, { holdCompletion: stage === 'completion', holdVerification: stage === 'verification' });
    const response = await f.post('/v1/chat/completions', {
      user: CHAT_ID, stream: true, messages: [{ role: 'user', content: PROMPT }],
    });
    // Both transport destruction and an existing sanitized SSE error are valid
    // cancellation delivery paths; neither may invent a completed task result.
    const delivered = response.text().catch(() => '');
    await (stage === 'completion' ? f.submitted : f.verificationStarted);
    const cancelled = await f.post('/v1/chat/cancel', { user: CHAT_ID });
    assert.deepEqual(await cancelled.json(), { aborted: true });
    const body = await delivered;
    assert.doesNotMatch(body, /request is incomplete|pixel_outcome|"finish_reason":"stop"/);
    assert.equal(f.observed.aborts, 1);
    assertOneUnchangedRequest(f);
  });
}

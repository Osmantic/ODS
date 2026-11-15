import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createPixelTiming, createIngressServer } from '../host/pixel_ingress.mjs';

const RUN_ID = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const TOKEN = 'test-gateway-token-0123456789abcdef';

function makeClock(values) {
  let i = 0;
  return () => {
    if (i >= values.length) return values[values.length - 1];
    return values[i++];
  };
}

function collectSink() {
  const lines = [];
  return { lines, sink: (line) => lines.push(line) };
}

function parseReceipt(line) {
  return JSON.parse(line);
}

// ---- Unit tests for createPixelTiming ----

test('disabled recorder does not call clock or sink', () => {
  let clockCalls = 0;
  const { lines, sink } = collectSink();
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '0' },
    pixelTimingClock: () => { clockCalls++; return 0; },
    pixelTimingSink: sink,
  });
  const result = timing.wrap('preparation', () => 42);
  assert.equal(result, 42);
  timing.markRunId(RUN_ID);
  timing.terminal('delivered');
  assert.equal(clockCalls, 0);
  assert.equal(lines.length, 0);
});

test('enabled sync wrapper preserves result and records exact duration', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([100, 100, 150, 150]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  const result = timing.wrap('preparation', () => 'value');
  assert.equal(result, 'value');
  timing.markRunId(RUN_ID);
  timing.terminal('delivered');
  assert.equal(lines.length, 1);
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.stageDurationsMs.preparation, 50);
  assert.equal(receipt.totalMs, 50);
  assert.equal(receipt.clockValid, true);
  assert.equal(receipt.runId, RUN_ID);
  assert.equal(receipt.outcome, 'delivered');
});

test('enabled async wrapper preserves result and duration', async () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 25, 25]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  const result = await timing.wrap('gatewayHeaders', async () => 'async-value');
  assert.equal(result, 'async-value');
  timing.markRunId(RUN_ID);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.stageDurationsMs.gatewayHeaders, 25);
  assert.equal(receipt.totalMs, 25);
});

test('sync throw preserves error identity', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 10, 10]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  const err = new Error('boom');
  assert.throws(() => timing.wrap('preparation', () => { throw err; }), (e) => e === err);
  timing.terminal('error');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.stageDurationsMs.preparation, 10);
  assert.equal(receipt.outcome, 'error');
});

test('async rejection preserves error identity', async () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 5, 5]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  const err = new Error('rejected');
  await assert.rejects(
    timing.wrap('gatewayBody', async () => { throw err; }),
    (e) => e === err,
  );
  timing.terminal('error');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.stageDurationsMs.gatewayBody, 5);
});

test('terminal is once-only', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 10, 10, 20, 20]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.terminal('delivered');
  timing.terminal('error');
  assert.equal(lines.length, 1);
  assert.equal(parseReceipt(lines[0]).outcome, 'delivered');
});

test('only allowlisted stage names appear in receipt', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 1, 1, 2, 2, 3, 3]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.wrap('preparation', () => 1);
  timing.wrap('unknown-stage', () => 2);
  timing.wrap('__proto__', () => 3);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.deepEqual(Object.keys(receipt.stageDurationsMs), ['preparation']);
  assert.ok(!lines[0].includes('unknown-stage'));
  assert.ok(!lines[0].includes('__proto__'));
});

test('invalid run ID never appears in receipt', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 1, 1]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.markRunId('not-a-valid-run-id');
  timing.markRunId('chatcmpl_zzzz');
  timing.markRunId(12345);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.runId, null);
  assert.ok(!lines[0].includes('not-a-valid-run-id'));
});

test('valid run ID appears in receipt', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 1, 1]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.markRunId(RUN_ID);
  timing.terminal('delivered');
  assert.equal(parseReceipt(lines[0]).runId, RUN_ID);
});

test('stream flag true vs false', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, 1, 1, 2, 2, 3, 3]);
  const t1 = createPixelTiming({ env: { ODS_PIXEL_TIMING: '1' }, pixelTimingClock: clock, pixelTimingSink: sink }, true);
  t1.terminal('delivered');
  const t2 = createPixelTiming({ env: { ODS_PIXEL_TIMING: '1' }, pixelTimingClock: clock, pixelTimingSink: sink }, false);
  t2.terminal('delivered');
  assert.equal(parseReceipt(lines[0]).stream, true);
  assert.equal(parseReceipt(lines[1]).stream, false);
});

test('sink throwing cannot change completed chat', () => {
  const clock = makeClock([0, 0, 1, 1]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: () => { throw new Error('sink failure'); },
  });
  const result = timing.wrap('preparation', () => 'ok');
  assert.equal(result, 'ok');
  assert.doesNotThrow(() => timing.terminal('delivered'));
});

test('invalid clock produces clockValid false and null metrics', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([0, 0, NaN, NaN]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.wrap('preparation', () => 1);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.clockValid, false);
  assert.equal(receipt.totalMs, null);
  assert.deepEqual(receipt.stageDurationsMs, {});
});

test('regressing clock produces clockValid false', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([100, 100, 50, 50]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.wrap('preparation', () => 1);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.clockValid, false);
  assert.equal(receipt.totalMs, null);
});

test('missing opt-in avoids all diagnostic work', () => {
  const fail = () => { throw new Error('disabled recorder invoked'); };
  const timing = createPixelTiming({ env: {}, pixelTimingClock: fail, pixelTimingSink: fail });
  assert.equal(timing.wrap('delivery', () => 'unchanged'), 'unchanged');
  timing.terminal('delivered');
});

test('throwing diagnostic clock cannot change the operation result', () => {
  const lines = [];
  const timing = createPixelTiming({ env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: () => { throw new Error('clock failed'); },
    pixelTimingSink: (line) => lines.push(line) });
  assert.equal(timing.wrap('delivery', () => 'unchanged'), 'unchanged');
  timing.terminal('delivered');
  assert.equal(JSON.parse(lines[0]).clockValid, false);
  assert.equal(JSON.parse(lines[0]).totalMs, null);
});

test('unknown replacement completion cannot inherit an earlier run identity', () => {
  const lines = [];
  const timing = createPixelTiming(makeDeps(lines));
  timing.markRunId(RUN_ID);
  timing.markRunId(undefined);
  timing.terminal('error');
  assert.equal(JSON.parse(lines[0]).runId, null);
  assert.ok(Buffer.byteLength(lines[0]) <= 4096);
});

test('overflowed diagnostic duration is unknown rather than an infinite metric', () => {
  const lines = [];
  const timing = createPixelTiming({ ...makeDeps(lines), pixelTimingClock: makeClock([0, 0, Number.MAX_VALUE, Number.MAX_VALUE]) });
  timing.wrap('delivery', () => 1);
  timing.terminal('delivered');
  const receipt = JSON.parse(lines[0]);
  assert.equal(receipt.clockValid, false);
  assert.equal(receipt.totalMs, null);
});

test('totalMs captures whole interval', () => {
  const { lines, sink } = collectSink();
  const clock = makeClock([10, 10, 20, 20, 30, 100]);
  const timing = createPixelTiming({
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: clock,
    pixelTimingSink: sink,
  });
  timing.wrap('preparation', () => 1);
  timing.wrap('gatewayHeaders', () => 2);
  timing.terminal('delivered');
  const receipt = parseReceipt(lines[0]);
  assert.equal(receipt.totalMs, 90);
  assert.equal(receipt.stageDurationsMs.preparation, 10);
  assert.equal(receipt.stageDurationsMs.gatewayHeaders, 10);
});

// ---- Integration tests via loopback HTTP ingress ----

function fakeGateway({ completionStatus = 200, completionText = 'verified reply', verification = { status: 'none' } } = {}) {
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      if (req.url === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
        return;
      }
      if (req.url === '/pixel-ods/verification') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(verification));
        return;
      }
      if (req.url === '/pixel-ods/subagent-delivery') {
        const parsed = body ? JSON.parse(body) : {};
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ schemaVersion: 1, kind: 'ods-subagent-delivery', runId: parsed.runId, status: 'not-delegated' }));
        return;
      }
      if (req.url === '/pixel-ods/read-only-extension-continuation') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false }));
        return;
      }
      if (req.url === '/pixel-ods/unfinished-extension-decision') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false }));
        return;
      }
      if (req.url === '/pixel-ods/abort') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ aborted: true }));
        return;
      }
      res.writeHead(completionStatus, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ id: RUN_ID, object: 'chat.completion', created: 1, model: 'openclaw/default', choices: [{ index: 0, finish_reason: 'stop', message: { role: 'assistant', content: completionText } }] }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

function startIngress({ gatewayPort, deps }) {
  const server = createIngressServer({ token: TOKEN, gatewayPort, deps });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function request(server, method, path, { body, headers } = {}) {
  const port = server.address().port;
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path, headers }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function makeDeps(sinkLines) {
  let tick = 0;
  return {
    setTimeout, clearTimeout, fetch,
    env: { ODS_PIXEL_TIMING: '1' },
    pixelTimingClock: () => { tick += 5; return tick; },
    pixelTimingSink: (line) => sinkLines.push(line),
  };
}

test('integration JSON: receipt outcome=delivered, reply unchanged, no diagnostics field', async (t) => {
  const gw = await fakeGateway({ completionText: 'verified reply' });
  const sinkLines = [];
  const deps = makeDeps(sinkLines);
  const srv = await startIngress({ gatewayPort: gw.port, deps });
  t.after(async () => {
    srv.closeAllConnections();
    await new Promise((r) => srv.close(r));
    gw.server.closeAllConnections();
    await new Promise((r) => gw.server.close(r));
  });
  const result = await request(srv, 'POST', '/v1/chat/completions', {
    body: JSON.stringify({ user: 'timing-fixture-user', messages: [{ role: 'user', content: 'hello' }], stream: false }),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  });
  assert.equal(result.status, 200);
  const parsed = JSON.parse(result.body);
  assert.equal(parsed.choices[0].message.content, 'verified reply');
  assert.equal(parsed.id, RUN_ID);
  assert.ok(!('diagnostics' in parsed));
  assert.ok(!('pixel_timing' in parsed));
  assert.equal(sinkLines.length, 1);
  const receipt = parseReceipt(sinkLines[0]);
  assert.equal(receipt.outcome, 'delivered');
  assert.equal(receipt.runId, RUN_ID);
  assert.equal(receipt.stream, false);
  assert.equal(receipt.clockValid, true);
  assert.ok(typeof receipt.totalMs === 'number');
});

test('integration SSE: receipt outcome=delivered, reply unchanged', async (t) => {
  const gw = await fakeGateway({ completionText: 'streamed verified reply' });
  const sinkLines = [];
  const deps = makeDeps(sinkLines);
  const srv = await startIngress({ gatewayPort: gw.port, deps });
  t.after(async () => {
    srv.closeAllConnections();
    await new Promise((r) => srv.close(r));
    gw.server.closeAllConnections();
    await new Promise((r) => gw.server.close(r));
  });
  const result = await request(srv, 'POST', '/v1/chat/completions', {
    body: JSON.stringify({ user: 'timing-fixture-user', messages: [{ role: 'user', content: 'hello' }], stream: true }),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  });
  assert.equal(result.status, 200);
  assert.ok(result.body.includes('streamed verified reply'));
  assert.ok(result.body.endsWith('data: [DONE]\n\n'));
  assert.equal(sinkLines.length, 1);
  const receipt = parseReceipt(sinkLines[0]);
  assert.equal(receipt.outcome, 'delivered');
  assert.equal(receipt.runId, RUN_ID);
  assert.equal(receipt.stream, true);
});

test('integration upstream 500: receipt outcome=error not delivered', async (t) => {
  const gw = await fakeGateway({ completionStatus: 500, completionText: 'private-provider-secret' });
  const sinkLines = [];
  const deps = makeDeps(sinkLines);
  const srv = await startIngress({ gatewayPort: gw.port, deps });
  t.after(async () => {
    srv.closeAllConnections();
    await new Promise((r) => srv.close(r));
    gw.server.closeAllConnections();
    await new Promise((r) => gw.server.close(r));
  });
  const result = await request(srv, 'POST', '/v1/chat/completions', {
    body: JSON.stringify({ user: 'timing-fixture-user', messages: [{ role: 'user', content: 'hello' }], stream: false }),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  });
  assert.equal(result.status, 502);
  assert.ok(!result.body.includes('private-provider-secret'));
  assert.equal(sinkLines.length, 1);
  const receipt = parseReceipt(sinkLines[0]);
  assert.equal(receipt.outcome, 'error');
  assert.notEqual(receipt.outcome, 'delivered');
});

test('integration downstream disconnect records aborted and aborts pending fetch', { timeout: 5000 }, async (t) => {
  const lines = [];
  let fetchStarted;
  const started = new Promise((resolve) => { fetchStarted = resolve; });
  let receiptDone;
  const receipt = new Promise((resolve) => { receiptDone = resolve; });
  const deps = { ...makeDeps(lines),
    fetch: (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      fetchStarted();
    }),
    pixelTimingSink: (line) => { lines.push(line); receiptDone(); },
  };
  const srv = await startIngress({ gatewayPort: 1, deps });
  t.after(async () => { srv.closeAllConnections(); await new Promise((r) => srv.close(r)); });
  const req = http.request({ host: '127.0.0.1', port: srv.address().port,
    path: '/v1/chat/completions', method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` } });
  req.on('error', () => {});
  req.end(JSON.stringify({ user: 'disconnect-fixture', messages: [{ role: 'user', content: 'hello' }], stream: false }));
  await started;
  req.destroy();
  await receipt;
  assert.equal(lines.length, 1);
  assert.equal(JSON.parse(lines[0]).outcome, 'aborted');
});

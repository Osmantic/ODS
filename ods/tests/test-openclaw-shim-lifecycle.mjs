import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import vm from 'node:vm';
import test from 'node:test';

const require = createRequire(import.meta.url);
const injector = fs.readFileSync(new URL('../config/openclaw/inject-token.js', import.meta.url), 'utf8');

async function harness(t, handleGateway) {
  const gateway = http.createServer(handleGateway);
  gateway.listen(0, '127.0.0.1');
  await once(gateway, 'listening');
  const files = new Map();
  vm.runInNewContext(injector, {
    process: { env: { OPENCLAW_HTTP_API: 'true', HOME: '/fixture' } },
    console: { log() {}, warn() {}, error() {} },
    require(name) {
      if (name === 'fs') return {
        existsSync: () => false,
        writeFileSync: (path, data) => files.set(path, data),
      };
      if (name === 'child_process') return { spawn: () => ({ pid: 1, unref() {} }) };
      return require(name);
    },
  });
  let server;
  vm.runInNewContext(files.get('/tmp/openai-shim.js'), {
    process: { on() {} }, console: { log() {}, error() {} }, setTimeout,
    require(name) {
      assert.equal(name, 'http');
      return {
        request: (options, callback) => http.request({ ...options, port: gateway.address().port }, callback),
        createServer(handler) {
          server = http.createServer(handler);
          const listen = server.listen.bind(server);
          server.listen = (_port, _host, callback) => listen(0, '127.0.0.1', callback);
          return server;
        },
      };
    },
  });
  await once(server, 'listening');
  t.after(async () => {
    await Promise.all([gateway, server].map(instance => new Promise(resolve => {
      instance.close(resolve);
      instance.closeAllConnections();
    })));
  });
  return `http://127.0.0.1:${server.address().port}`;
}

async function within(promise, message) {
  let timer;
  try {
    await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), 1000); }),
    ]);
  } finally { clearTimeout(timer); }
}

test('disconnecting a streaming caller closes the gateway response', async t => {
  let closed;
  const gatewayClosed = new Promise(resolve => { closed = resolve; });
  const base = await harness(t, (_req, res) => {
    res.on('close', closed);
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  const request = http.get(`${base}/v1/chat/completions`);
  const [response] = await once(request, 'response');
  await once(response, 'data');
  response.destroy();
  await within(gatewayClosed, 'gateway generation stayed connected after caller cancellation');
});

test('a truncated gateway response terminates the downstream stream', async t => {
  let truncate;
  const base = await harness(t, (_req, res) => {
    truncate = () => res.destroy();
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  const request = http.get(`${base}/v1/chat/completions`);
  const [response] = await once(request, 'response');
  response.on('error', () => {});
  await once(response, 'data');
  const closed = new Promise(resolve => response.once('close', resolve));
  truncate();
  await within(closed, 'truncated gateway response left the caller waiting');
  assert.equal(response.complete, false);
});

test('an aborted upload releases the gateway request', async t => {
  let started, closed;
  const gatewayStarted = new Promise(resolve => { started = resolve; });
  const gatewayClosed = new Promise(resolve => { closed = resolve; });
  const base = await harness(t, (req, res) => {
    req.once('data', started);
    res.once('close', closed);
  });
  const request = http.request(`${base}/v1/chat/completions`, {
    method: 'POST', headers: { 'Content-Length': '1000' },
  });
  request.on('error', () => {});
  request.write('{"model":');
  await within(gatewayStarted, 'upload did not reach the gateway');
  request.destroy();
  await within(gatewayClosed, 'aborted upload stayed connected to the gateway');
});

test('a gateway failure before headers still returns 502', async t => {
  const base = await harness(t, (_req, res) => res.destroy());
  const response = await fetch(`${base}/v1/chat/completions`);
  assert.equal(response.status, 502);
  assert.equal(await response.text(), 'gateway unavailable');
});

test('completed uploads keep their response stream and model discovery works', async t => {
  const base = await harness(t, async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    assert.deepEqual(JSON.parse(body), { model: 'openclaw', stream: true });
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('data: first\n\n');
    setImmediate(() => res.end('data: [DONE]\n\n'));
  });
  const response = await fetch(`${base}/v1/chat/completions`, {
    method: 'POST', body: JSON.stringify({ model: 'openclaw', stream: true }),
  });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'data: first\n\ndata: [DONE]\n\n');
  const models = await (await fetch(`${base}/v1/models`)).json();
  assert.equal(models.data[0].id, 'openclaw');
});

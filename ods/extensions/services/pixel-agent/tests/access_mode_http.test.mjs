import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {accessPort, createAccessServer} from '../host/access_mode_http.mjs';
import {requestAccessController} from '../host/access_mode_relay.mjs';

const ownerKey = 'o'.repeat(64);
const state = {schemaVersion: 1, status: 'ready', revision: 'b'.repeat(64),
  contract: {model: 'Bonsai', contextLength: 16384, maxTokens: 8192, reasoning: false},
  pending: false, transactionId: null, outcome: null};

test('native transport requires a key and a bounded literal port', () => {
  assert.equal(accessPort(), 18790);
  assert.equal(accessPort('54321'), 54321);
  for (const port of ['', '0', '65536', '18790/path', '018790', '18790\n', 'http://other', 18790]) {
    assert.throws(() => accessPort(port));
  }
  for (const key of [undefined, '', 'short', ownerKey + '\n']) {
    assert.throws(() => createAccessServer({ownerKey: key}));
  }
});

test('HTTP to real Unix socket preserves owner authority and model control protocol', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'ods-access-'));
  const socketPath = path.join(directory, 'control.sock');
  const calls = [];
  let disconnect = false;
  const controller = net.createServer(socket => {
    let raw = '';
    socket.on('data', chunk => {
      raw += chunk.toString();
      if (!raw.includes('\n')) return;
      calls.push(JSON.parse(raw));
      if (disconnect) return socket.destroy();
      socket.end(JSON.stringify({status: 200, body: {...state, privateJournal: 'not-public'}}) + '\n');
    });
  });
  await new Promise(resolve => controller.listen(socketPath, resolve));
  const server = createAccessServer({ownerKey,
    request: (payload, options) => requestAccessController(payload, {...options, socketPath})});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await new Promise(resolve => controller.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const send = (body = {operation: 'model-status'}, headers = {}, route = '/v1/model-control') =>
    fetch(origin + route, {method: 'POST', headers: {
      authorization: 'Bearer ' + ownerKey, 'content-type': 'application/json', ...headers},
    body: JSON.stringify(body)});
  for (const headers of [{authorization: ''}, {authorization: 'Bearer ' + 'c'.repeat(64)},
    {origin: 'http://generated.localhost'}, {'sec-fetch-site': 'cross-site'}]) {
    assert.equal((await send(undefined, headers)).status, 403);
  }
  for (const route of ['/v1/model-control?path=/etc', '/v1/chat/completions', '/health', '/']) {
    assert.equal((await send(undefined, {}, route)).status, 404);
  }
  assert.equal((await send({operation: 'exec', command: 'whoami'})).status, 400);
  assert.equal((await send({padding: 'x'.repeat(2049)})).status, 413);
  assert.equal(calls.length, 0);
  const response = await send();
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), state);
  assert.deepEqual(calls, [{operation: 'model-route-status'}]);
  disconnect = true;
  const failed = await send({operation: 'model-finish',
    request: {transactionId: 'a'.repeat(64), outcome: 'rollback'}});
  assert.equal(failed.status, 503);
  assert.equal(calls.length, 2); // A lost response must not replay the mutation.
});

test('opt-in Docker Desktop client reaches the loopback owner relay', {
  skip: process.platform !== 'darwin' || !process.env.ODS_TEST_DOCKER_IMAGE,
}, async t => {
  const calls = [];
  const server = createAccessServer({ownerKey, request: async payload => {
    calls.push(payload);
    return {status: 200, body: state};
  }});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const code = `import json,sys,urllib.request,urllib.error
value=json.load(sys.stdin)
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
url='http://host.docker.internal:%d/v1/model-control'%value['port']
for key,expected in [(value['chatKey'],403),(value['ownerKey'],200)]:
    request=urllib.request.Request(url,data=b'{"operation":"model-status"}',headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        response=client.open(request,timeout=10)
    except urllib.error.HTTPError as error:
        response=error
    with response:
        assert response.status==expected,response.status
        if expected==200: assert json.load(response)==value['expected']
print('docker-desktop-control-transport-ok')
`;
  const child = spawn('docker', ['run', '--rm', '--pull', 'never', '-i', '--read-only',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--pids-limit', '32',
    '--memory', '128m', '--cpus', '1', '--user', '65534:65534', '--entrypoint', 'python3',
    process.env.ODS_TEST_DOCKER_IMAGE, '-I', '-c', code], {stdio: ['pipe', 'pipe', 'pipe']});
  let output = '', error = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { error += chunk; });
  child.stdin.end(JSON.stringify({port: server.address().port, ownerKey,
    chatKey: 'c'.repeat(64), expected: state}));
  const result = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', resolve);
  });
  assert.equal(result, 0, error);
  assert.equal(output.trim(), 'docker-desktop-control-transport-ok');
  assert.deepEqual(calls, [{operation: 'model-route-status'}]);
});

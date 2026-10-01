import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createProjectTransport} from '../plugin/project-transport.mjs';

async function withServer(handler, run) {
  const root = await mkdtemp(join(tmpdir(), 'ods-project-'));
  const socketPath = join(root, 's');
  const connections = new Set();
  const server = net.createServer(socket => {
    connections.add(socket);
    socket.on('error', () => {});
    socket.on('close', () => connections.delete(socket));
    socket.once('data', data => handler(socket, JSON.parse(data)));
  });
  await new Promise((resolve, reject) => {server.once('error', reject); server.listen(socketPath, resolve);});
  try {await run(createProjectTransport({socketPath, sessionId: 'session-1'}));}
  finally {
    for (const socket of connections) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    await rm(root, {recursive: true, force: true});
  }
}

test('socket reconstructs split UTF-8 responses and sends separate trusted context', {skip: process.platform === 'win32'}, async () => {
  await withServer((socket, envelope) => {
    assert.deepEqual(envelope.context, {sessionId: 'session-1', toolCallId: 'call-1'});
    const bytes = Buffer.from('{"message":"ação"}\n');
    const split = bytes.indexOf(0xc3) + 1;
    socket.write(bytes.subarray(0, split));
    setImmediate(() => socket.end(bytes.subarray(split)));
  }, async request => assert.deepEqual(await request({action: 'observe'}, {toolCallId: 'call-1'}), {message: 'ação'}));
});

test('partial reply and abort are unknown outcomes with one connection', {skip: process.platform === 'win32'}, async () => {
  let calls = 0;
  await withServer(socket => {calls++; socket.end('{"status":');}, async request => {
    await assert.rejects(request({}, {toolCallId: 'call-1'}), /incomplete|disconnected/);
  });
  assert.equal(calls, 1);
  const abort = new AbortController();
  await withServer(() => {calls++; abort.abort();}, async request => {
    await assert.rejects(request({}, {toolCallId: 'call-2', signal: abort.signal}), /unknown/);
  });
  assert.equal(calls, 2);
});

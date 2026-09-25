import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import {testing} from '../plugin/workspace-preview.mjs';

async function withServer(t, respond) {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'preview-deadline-'));
  const socketPath = path.join(root, 'control.sock');
  const sockets = new Set();
  const timers = new Set();
  const server = net.createServer({allowHalfOpen:true}, socket => {
    sockets.add(socket);
    socket.on('error', error => {if (error.code !== 'ECONNRESET') throw error;});
    socket.on('close', () => sockets.delete(socket));
    socket.once('data', () => respond(socket, timers));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, resolve);
  });
  t.after(async () => {
    for (const timer of timers) {clearTimeout(timer); clearInterval(timer);}
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive:true, force:true});
  });
  return socketPath;
}

const options = {skip:process.platform === 'win32' ? 'Uses the POSIX preview control socket' : false};

test('partial response activity cannot extend the total receipt deadline', options, async t => {
  const socketPath = await withServer(t, (socket, timers) => {
    socket.write(' ');
    const drip = setInterval(() => {if (!socket.destroyed) socket.write(' ');}, 40);
    timers.add(drip);
    timers.add(setTimeout(() => {
      clearInterval(drip);
      if (!socket.destroyed) socket.end('{}\n');
    }, 1200));
  });
  await assert.rejects(testing.socketRequest({action:'publish'}, {socketPath, timeoutMs:250}),
    /Pixel workspace preview timed out/);
});

test('a complete framed response within the deadline is returned intact', options, async t => {
  const socketPath = await withServer(t, socket => socket.end('{"receipt":"complete"}\n'));
  assert.deepEqual(await testing.socketRequest({action:'publish'}, {socketPath, timeoutMs:1000}),
    {receipt:'complete'});
});

test('an already aborted call does not connect even with a receipt deadline', options, async () => {
  await assert.rejects(testing.socketRequest({}, {
    socketPath:'/does-not-exist/preview.sock', signal:AbortSignal.abort(), timeoutMs:250,
  }), /cancelled/);
});

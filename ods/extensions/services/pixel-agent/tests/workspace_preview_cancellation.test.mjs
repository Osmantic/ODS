import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import {createWorkspacePreviewTool, testing} from '../plugin/workspace-preview.mjs';

test('an already cancelled tool call sends no publication request', async () => {
  let requests = 0;
  const controller = new AbortController();
  controller.abort();
  const tool = createWorkspacePreviewTool({request:async () => {requests++; return {};}});
  const result = await tool.execute('cancelled', {relativeDirectory:'site'}, controller.signal);
  assert.equal(requests, 0);
  assert.equal(result.details.errorCode, 'cancelled');
  assert.equal(result.isError, true);
});

test('cancellation settles a stalled publication socket without claiming host cancellation',
  {skip:process.platform === 'win32' ? 'Requires the POSIX preview service Unix socket' : false}, async () => {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'pixel-preview-abort-'));
  const socketPath = path.join(root, 'preview.sock');
  const sockets = new Set();
  let received;
  const ready = new Promise(resolve => {received = resolve;});
  const server = net.createServer({allowHalfOpen:true}, socket => {
    sockets.add(socket);
    socket.on('error', error => { if (error.code !== 'ECONNRESET') throw error; });
    socket.on('data', received);
    socket.on('close', () => sockets.delete(socket));
  });
  await new Promise(resolve => server.listen(socketPath, resolve));
  let timer;
  try {
    const controller = new AbortController();
    const tool = createWorkspacePreviewTool({
      request:(payload, options) => testing.socketRequest(payload, {...options, socketPath}),
    });
    const pending = tool.execute('pending', {relativeDirectory:'site'}, controller.signal);
    await ready;
    controller.abort();
    const result = await Promise.race([pending, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Preview ignored cancellation')), 1000);
    })]);
    assert.equal(result.details.errorCode, 'cancelled');
    assert.match(result.content[0].text, /may still complete/);
    assert.equal(result.isError, true);
  } finally {
    clearTimeout(timer);
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('a late valid receipt cannot revive a cancelled publication wait', async () => {
  const controller = new AbortController();
  const sha256 = 'a'.repeat(64);
  const siteId = 'site-' + sha256.slice(0, 24);
  const receipt = {
    schemaVersion:1, kind:'ods-pixel-workspace-preview', status:'succeeded',
    relativeDirectory:'site', siteId, port:9437,
    url:`http://${siteId}.localhost:9437/${siteId}/`,
    files:1, bytes:40, sha256, entryFile:'index.html', entrySha256:'b'.repeat(64),
    httpStatus:200, readbackVerified:true, executable:false, overwritten:false,
    boundary:testing.BOUNDARY,
  };
  const tool = createWorkspacePreviewTool({request:async () => {
    controller.abort();
    return receipt;
  }});
  const result = await tool.execute('late-receipt', {relativeDirectory:'site'}, controller.signal);
  assert.equal(result.isError, true);
  assert.equal(result.details.errorCode, 'cancelled');
  assert.equal(result.details.siteId, undefined);
  assert.match(result.content[0].text, /may still complete/);
});

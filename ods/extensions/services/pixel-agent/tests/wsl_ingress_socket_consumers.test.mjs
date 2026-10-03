import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readArchivedHistory} from '../plugin/history-context.mjs';
import {readConversationImage, readConversationImagePolicy} from '../plugin/chat-image-transport.mjs';

const user = 'ods-' + 'c'.repeat(64);
const reference = {id: 'img-' + 'd'.repeat(32), sha256: 'e'.repeat(64)};

test('gateway history and image readers use the configured WSL ingress socket',
  {skip: process.platform === 'win32'}, async () => {
  const root = await mkdtemp(join(tmpdir(), 'ods-wsl-ingress-'));
  const socket = join(root, 'ingress.sock');
  const seen = [];
  const server = http.createServer((request, response) => {
    seen.push(request.url);
    request.resume();
    response.setHeader('content-type', 'application/json');
    if (request.url === '/v1/chat/history') {
      response.end(JSON.stringify({schemaVersion: 1, source: 'archived-conversation', untrusted: true, messages: []}));
    } else if (request.url === '/v1/chat/image') {
      response.end(JSON.stringify({schemaVersion: 1, image: {...reference, mimeType: 'image/png', bytes: 3, data: 'YWJj'}}));
    } else if (request.url === '/v1/chat/image-policy') {
      response.end(JSON.stringify({schemaVersion: 1, policy: {imageInput: 'supported', routeFingerprint: 'f'.repeat(64), unknownConsent: false}}));
    } else {
      response.statusCode = 404;
      response.end('{}');
    }
  });
  const previous = process.env.PIXEL_INGRESS_SOCKET;
  try {
    await new Promise(resolve => server.listen(socket, resolve));
    process.env.PIXEL_INGRESS_SOCKET = socket;
    assert.deepEqual((await readArchivedHistory(user, {})).messages, []);
    assert.equal((await readConversationImage(user, reference)).image.data, 'YWJj');
    assert.equal((await readConversationImagePolicy(user)).policy.imageInput, 'supported');
    assert.deepEqual(seen, ['/v1/chat/history', '/v1/chat/image', '/v1/chat/image-policy']);
  } finally {
    if (previous === undefined) delete process.env.PIXEL_INGRESS_SOCKET;
    else process.env.PIXEL_INGRESS_SOCKET = previous;
    await new Promise(resolve => server.close(resolve));
    await rm(root, {recursive: true, force: true});
  }
});

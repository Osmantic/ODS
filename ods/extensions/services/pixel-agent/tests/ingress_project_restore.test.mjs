// Portal's "Put back files" relay: the ingress holds the chat's history lock,
// refuses while a turn of that chat runs or is unsettled, and returns only the
// gateway's validated restore receipt (plugin/project-snapshots.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {computeSessionUser, createIngressServer, projectRestoreReceipt} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const rawUser = 'restore-chat', user = computeSessionUser({user: rawUser});
const snapshot = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const undo = 'restore_99999999-2222-4333-8444-555555555555';
const request = {user: rawUser, project: 'Playground/game', snapshot};
const receipt = (extra = {}) => ({schemaVersion: 1, project: 'Playground/game', snapshot, written: 1, removed: 2,
  kept: ['node_modules'], keptCount: 1, failed: [], failedCount: 0, complete: true, undo, ...extra});
const nativeState = () => ({schemaVersion: 1, status: 'ready', sessionExists: true, sessionRevision: 'a'.repeat(64), context: null,
  model: null, compaction: {status: 'idle', requestId: null, tokensBefore: null, tokensAfter: null, reason: null, count: 0}});
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));

async function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-ingress-restore-'));
  fs.chmodSync(dir, 0o700);
  const ledger = createChatHistoryLedger(dir), calls = [], native = nativeState();
  let reply = [200, receipt()];
  const gateway = http.createServer(async (req, res) => {
    res.setHeader('content-type', 'application/json');
    // The ingress finds a healthy loopback listener before it sends a body.
    if (req.url === '/health') return res.end('{"ok":true}');
    let raw = '';
    for await (const part of req) raw += part;
    calls.push({path: req.url, method: req.method, authorization: req.headers.authorization, body: raw ? JSON.parse(raw) : null});
    if (req.url === '/pixel-ods/context') return res.end(JSON.stringify(native));
    if (req.url === '/pixel-ods/project-restore') {
      res.statusCode = reply[0];
      return res.end(typeof reply[1] === 'string' ? reply[1] : JSON.stringify(reply[1]));
    }
    res.statusCode = 404;
    res.end('{}');
  });
  const port = await listen(gateway);
  const ingress = createIngressServer({token: 'gateway-token', gatewayPort: port, historyLedger: ledger});
  const ingressPort = await listen(ingress);
  t.after(async () => {
    await Promise.all([new Promise(resolve => ingress.close(resolve)), new Promise(resolve => gateway.close(resolve))]);
    fs.rmSync(dir, {recursive: true, force: true});
  });
  async function restore(body = request, {method = 'POST', contentType = 'application/json', route = '/v1/project-restore'} = {}) {
    const response = await fetch(`http://127.0.0.1:${ingressPort}${route}`, {method, headers: {'content-type': contentType},
      ...(method === 'POST' ? {body: typeof body === 'string' ? body : JSON.stringify(body)} : {})});
    return {status: response.status, value: await response.json()};
  }
  const restores = () => calls.filter(call => call.path === '/pixel-ods/project-restore');
  return {ledger, calls, native, restore, restores, setReply: value => { reply = value; }};
}

test('the relay sends one exact request for the chat session and returns only the validated receipt', async t => {
  const f = await fixture(t);
  assert.deepEqual(await f.restore(), {status: 200, value: receipt()});
  assert.deepEqual(f.restores(), [{path: '/pixel-ods/project-restore', method: 'POST', authorization: 'Bearer gateway-token',
    body: {user, project: 'Playground/game', snapshot}}]);
  assert.match(user, /^ods-[a-f0-9]{64}$/);
  // The history lock is released afterwards: the next turn can take it.
  f.ledger.lock(user)();
});

test('a restore is refused while a turn of the chat holds its lock, is unsettled or still runs natively', async t => {
  const f = await fixture(t);
  const release = f.ledger.lock(user);
  try { assert.deepEqual(await f.restore(), {status: 409, value: {error: 'project-restore-busy'}}); }
  finally { release(); }
  assert.equal(f.calls.length, 0, 'nothing is read or restored while a turn holds the lock');
  f.native.status = 'busy';
  assert.deepEqual(await f.restore(), {status: 409, value: {error: 'project-restore-busy'}});
  assert.equal(f.restores().length, 0);
  f.native.status = 'ready';
  assert.equal((await f.restore()).status, 200);
});

test('a turn whose outcome is unsettled after a restart counts as running', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-ingress-restore-unsettled-'));
  fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  // The previous ingress process prepared a turn and never finished it.
  createChatHistoryLedger(dir).prepare(user, 'cut-off', {schemaVersion: 1, messages: [{role: 'user', content: 'Fix the game'}]}, nativeState());
  const ledger = createChatHistoryLedger(dir);
  assert.equal(ledger.read(user).status, 'unknown');
  const calls = [];
  const gateway = http.createServer((req, res) => {
    res.setHeader('content-type', 'application/json');
    if (req.url !== '/health') calls.push(req.url);
    res.end(req.url === '/health' ? '{"ok":true}' : JSON.stringify(nativeState()));
  });
  const port = await listen(gateway);
  const ingress = createIngressServer({token: 'gateway-token', gatewayPort: port, historyLedger: ledger});
  const ingressPort = await listen(ingress);
  t.after(async () => { await Promise.all([new Promise(r => ingress.close(r)), new Promise(r => gateway.close(r))]); });
  const response = await fetch(`http://127.0.0.1:${ingressPort}/v1/project-restore`, {method: 'POST',
    headers: {'content-type': 'application/json'}, body: JSON.stringify(request)});
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), {error: 'project-restore-busy'});
  assert.deepEqual(calls, []);
});

test('refusals keep their code without upstream detail, and every other failure is a fixed 503', async t => {
  const f = await fixture(t);
  for (const [status, error] of [[404, 'project-snapshot-unavailable'], [409, 'project-restore-busy'], [422, 'project-snapshot-damaged']]) {
    f.setReply([status, {error, private: '/home/owner/.openclaw'}]);
    assert.deepEqual(await f.restore(), {status, value: {error}});
  }
  for (const reply of [[503, {error: 'private detail'}], [200, receipt({project: 'Playground/other'})], [200, receipt({snapshot: undo})],
    [200, receipt({undo: '../x'})], [200, receipt({extra: '/private'})], [200, receipt({written: -1})], [200, receipt({complete: 'yes'})],
    [200, receipt({kept: ['a\nb'], keptCount: 1})], [200, receipt({failed: ['a'], failedCount: 0})], [200, 'x'.repeat(17000)], [200, 'not json']]) {
    f.setReply(reply);
    assert.deepEqual(await f.restore(), {status: 503, value: {error: 'project-restore-unavailable'}}, JSON.stringify(reply).slice(0, 80));
  }
});

test('only one exact request shape reaches the gateway', async t => {
  const f = await fixture(t);
  for (const body of [{...request, project: 'Playground/../etc'}, {...request, project: 'Playground/game/src'}, {...request, project: '/etc'},
    {...request, snapshot: 'restore_x'}, {...request, user: '../x'}, {...request, extra: 1}, {project: 'Playground/game', snapshot}, 'not json']) {
    assert.equal((await f.restore(body)).status, 400, JSON.stringify(body));
  }
  assert.equal((await f.restore(request, {contentType: 'text/plain'})).status, 415);
  assert.equal((await f.restore(request, {method: 'GET'})).status, 405);
  assert.equal((await f.restore(request, {route: '/v1/project-restore?x=1'})).status, 400);
  assert.equal(f.calls.length, 0);
});

test('the projection accepts exactly the gateway receipt', () => {
  assert.deepEqual(projectRestoreReceipt(receipt(), request), receipt());
  assert.deepEqual(projectRestoreReceipt(receipt({undo: null, written: 0, removed: 0}), request).undo, null);
  for (const bad of [receipt({schemaVersion: 2}), receipt({kept: Array(9).fill('a'), keptCount: 9}), receipt({keptCount: 70000}),
    receipt({undo: snapshot}), {...receipt(), complete: undefined}]) {
    assert.throws(() => projectRestoreReceipt(bad, request), JSON.stringify(bad));
  }
});

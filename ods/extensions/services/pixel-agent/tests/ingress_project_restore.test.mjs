// Portal's "Put back files" relays: the ingress holds the chat's history lock,
// refuses while a turn of that chat runs or is unsettled, passes the owner's
// yes, and returns only the gateway's validated receipt or a fixed refusal
// code; the options relay returns only validated options
// (plugin/project-snapshots.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {computeSessionUser, createIngressServer, projectRestoreOptions, projectRestoreReceipt} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const rawUser = 'restore-chat', user = computeSessionUser({user: rawUser});
const snapshot = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const undo = 'restore_99999999-2222-4333-8444-555555555555';
const token = 'c'.repeat(64);
const request = {user: rawUser, project: 'Playground/game', snapshot};
const receipt = (extra = {}) => ({schemaVersion: 2, status: 'restored', project: 'Playground/game', snapshot, written: 1, removed: 2,
  left: ['big.bin'], leftCount: 1, failed: [], failedCount: 0, failure: null, late: false, undo, confirm: null, ...extra});
const confirmReceipt = (extra = {}) => receipt({status: 'confirm', written: 0, removed: 0, undo: null,
  confirm: {token, overwrite: ['chars.js'], overwriteCount: 1, remove: ['new.js', 'x.js'], removeCount: 3}, ...extra});
const option = (extra = {}) => ({project: 'Playground/game', snapshot, late: false, restore: {undo, side: 'before'}, ...extra});
const nativeState = () => ({schemaVersion: 1, status: 'ready', sessionExists: true, sessionRevision: 'a'.repeat(64), context: null,
  model: null, compaction: {status: 'idle', requestId: null, tokensBefore: null, tokensAfter: null, reason: null, count: 0}});
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));

async function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-ingress-restore-'));
  fs.chmodSync(dir, 0o700);
  const ledger = createChatHistoryLedger(dir), calls = [], native = nativeState();
  const replies = {'/pixel-ods/project-restore': [200, receipt()], '/pixel-ods/project-restore-options': [200, {schemaVersion: 1, projects: [option()]}]};
  const gateway = http.createServer(async (req, res) => {
    res.setHeader('content-type', 'application/json');
    // The ingress finds a healthy loopback listener before it sends a body.
    if (req.url === '/health') return res.end('{"ok":true}');
    let raw = '';
    for await (const part of req) raw += part;
    calls.push({path: req.url, method: req.method, authorization: req.headers.authorization, body: raw ? JSON.parse(raw) : null});
    if (req.url === '/pixel-ods/context') return res.end(JSON.stringify(native));
    const reply = replies[req.url];
    if (reply) {
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
  async function send(route, body, {method = 'POST', contentType = 'application/json'} = {}) {
    const response = await fetch(`http://127.0.0.1:${ingressPort}${route}`, {method, headers: {'content-type': contentType},
      ...(method === 'POST' ? {body: typeof body === 'string' ? body : JSON.stringify(body)} : {})});
    return {status: response.status, value: await response.json()};
  }
  const restore = (body = request, options = {}) => send(options.route ?? '/v1/project-restore', body, options);
  const options = (body = {runs: [snapshot]}, fields = {}) => send(fields.route ?? '/v1/project-restore-options', body, fields);
  const sent = route => calls.filter(call => call.path === route);
  return {ledger, calls, native, restore, options, sent, setReply: (route, value) => { replies[route] = value; }};
}

test('the relay sends one exact request for the chat session, with the owner\'s yes, and returns only the validated receipt', async t => {
  const f = await fixture(t);
  assert.deepEqual(await f.restore(), {status: 200, value: receipt()});
  f.setReply('/pixel-ods/project-restore', [200, confirmReceipt()]);
  assert.deepEqual(await f.restore(), {status: 200, value: confirmReceipt()});
  f.setReply('/pixel-ods/project-restore', [200, receipt()]);
  assert.deepEqual(await f.restore({...request, confirm: token}), {status: 200, value: receipt()});
  assert.deepEqual(f.sent('/pixel-ods/project-restore').map(call => [call.method, call.authorization, call.body]), [
    ['POST', 'Bearer gateway-token', {user, project: 'Playground/game', snapshot}],
    ['POST', 'Bearer gateway-token', {user, project: 'Playground/game', snapshot}],
    ['POST', 'Bearer gateway-token', {user, project: 'Playground/game', snapshot, confirm: token}],
  ]);
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
  assert.equal(f.sent('/pixel-ods/project-restore').length, 0);
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

test('refusals keep their fixed code without upstream detail, and every other failure is an unknown outcome', async t => {
  const f = await fixture(t);
  const route = '/pixel-ods/project-restore';
  for (const [status, error] of [[404, 'project-snapshot-unavailable'], [404, 'project-snapshot-pruned'], [409, 'project-restore-busy'],
    [422, 'project-snapshot-damaged'], [422, 'project-folder-unusable'], [503, 'project-copies-unusable'],
    [503, 'project-restore-unsupported']]) {
    f.setReply(route, [status, {error, private: '/home/owner/.openclaw'}]);
    assert.deepEqual(await f.restore(), {status, value: {error}});
  }
  // An unexpected code, or none, is no refusal ODS knows: the outcome is unknown.
  for (const reply of [[404, {error: 'project-snapshot-damaged'}], [422, {error: '/private'}], [409, {error: 'x'.repeat(2000)}],
    [404, 'Not Found'], [503, {error: 'private detail'}], [500, {error: 'project-snapshot-pruned'}], [400, {error: 'invalid'}],
    [200, receipt({project: 'Playground/other'})], [200, receipt({snapshot: undo})], [200, receipt({schemaVersion: 1})],
    [200, receipt({undo: '../x'})], [200, receipt({undo: null})], [200, receipt({status: 'unchanged'})], [200, receipt({status: 'done'})],
    [200, receipt({extra: '/private'})], [200, receipt({written: -1})], [200, receipt({late: 'yes'})], [200, receipt({failure: 'disk'})],
    [200, receipt({left: ['a\nb'], leftCount: 1})], [200, receipt({left: ['notes\u0085.md'], leftCount: 1})],
    [200, receipt({failed: ['a'], failedCount: 0})],
    [200, receipt({confirm: {token, overwrite: [], overwriteCount: 0, remove: [], removeCount: 0}})],
    [200, confirmReceipt({confirm: {token: 'x', overwrite: [], overwriteCount: 0, remove: [], removeCount: 0}})],
    [200, confirmReceipt({confirm: {token, overwrite: Array(9).fill('a'), overwriteCount: 9, remove: [], removeCount: 0}})],
    [200, confirmReceipt({undo})], [200, 'x'.repeat(17000)], [200, 'not json']]) {
    f.setReply(route, reply);
    assert.deepEqual(await f.restore(), {status: 503, value: {error: 'project-restore-unavailable'}}, JSON.stringify(reply).slice(0, 120));
  }
});

test('only one exact request shape reaches the gateway', async t => {
  const f = await fixture(t);
  for (const body of [{...request, project: 'Playground/../etc'}, {...request, project: 'Playground/game/src'}, {...request, project: '/etc'},
    {...request, snapshot: 'restore_x'}, {...request, user: '../x'}, {...request, extra: 1}, {project: 'Playground/game', snapshot},
    {...request, confirm: 'yes'}, {...request, confirm: token.toUpperCase()}, 'not json']) {
    assert.equal((await f.restore(body)).status, 400, JSON.stringify(body));
  }
  assert.equal((await f.restore(request, {contentType: 'text/plain'})).status, 415);
  assert.equal((await f.restore(request, {method: 'GET'})).status, 405);
  assert.equal((await f.restore(request, {route: '/v1/project-restore?x=1'})).status, 400);
  assert.equal(f.calls.length, 0);
});

test('the options relay sends the runs and returns only validated options', async t => {
  const f = await fixture(t);
  const route = '/pixel-ods/project-restore-options';
  assert.deepEqual(await f.options(), {status: 200, value: {schemaVersion: 1, projects: [option()]}});
  assert.deepEqual(f.sent(route).map(call => [call.method, call.authorization, call.body]),
    [['POST', 'Bearer gateway-token', {runs: [snapshot]}]]);
  for (const body of [{runs: [undo]}, {runs: Array(65).fill(snapshot)}, {runs: [snapshot], user: rawUser}, {runs: snapshot}, 'not json']) {
    assert.equal((await f.options(body)).status, 400, JSON.stringify(body));
  }
  assert.equal((await f.options({runs: []}, {method: 'GET'})).status, 405);
  for (const reply of [[200, {schemaVersion: 1, projects: [option({snapshot: 'chatcmpl_00000000-2222-4333-8444-555555555555'})]}],
    [200, {schemaVersion: 1, projects: [option({restore: {undo, side: 'later'}})]}], [200, {schemaVersion: 1, projects: [option({x: 1})]}],
    [200, {schemaVersion: 1, projects: [option({project: 'Playground/a/b'})]}], [200, {schemaVersion: 2, projects: []}],
    [200, {schemaVersion: 1, projects: Array(33).fill(option())}], [503, {error: 'project-copies-unusable'}], [200, 'not json']]) {
    f.setReply(route, reply);
    assert.deepEqual(await f.options(), {status: 503, value: {error: 'project-restore-unavailable'}}, JSON.stringify(reply).slice(0, 120));
  }
  assert.equal(f.sent(route).length, 9);
});

test('the projections accept exactly the gateway receipt and options', () => {
  assert.deepEqual(projectRestoreReceipt(receipt(), request), receipt());
  assert.deepEqual(projectRestoreReceipt(confirmReceipt(), request), confirmReceipt());
  assert.deepEqual(projectRestoreReceipt(receipt({status: 'unchanged', undo: null, written: 0, removed: 0}), request).undo, null);
  for (const bad of [receipt({schemaVersion: 1}), receipt({left: Array(9).fill('a'), leftCount: 9}), receipt({leftCount: 70000}),
    receipt({undo: snapshot}), {...receipt(), late: undefined}, receipt({confirm: null, status: 'confirm', undo: null})]) {
    assert.throws(() => projectRestoreReceipt(bad, request), JSON.stringify(bad));
  }
  assert.deepEqual(projectRestoreOptions({schemaVersion: 1, projects: [option(), option({project: 'Playground/b', restore: null, late: true})]},
    [snapshot]).projects.length, 2);
  assert.throws(() => projectRestoreOptions({schemaVersion: 1, projects: [option()]}, []));
});

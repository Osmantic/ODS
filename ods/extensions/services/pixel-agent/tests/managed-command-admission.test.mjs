import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';
import {createManagedCommandAdmission} from '../plugin/managed-command-admission.mjs';

const token = 'a'.repeat(64);
const context = () => ({signal: new AbortController().signal});
const notStarted = () => ({state: 'not-started'});
const running = () => ({state: 'running', scope: 'process-group', pid: 4321});
const exited = () => ({state: 'exited', scope: 'process-group', pid: 4321});
const controls = (commandId, inspectProcess = notStarted, cancelProcess = async () => {}) =>
  ({commandId, inspectProcess, cancelProcess});
function fixture(t, options = {}, wrap = value => value) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-command-admission-'));
  fs.chmodSync(directory, 0o700);
  const owner = createAccessRuntime({directory, runtimeVersion: '2026.6.33', hooksAllowed: true});
  assert.equal(owner.status().available, true);
  const adapter = createManagedCommandAdmission({accessRuntime: wrap(owner),
    cleanupTimeoutMs: 80, pollIntervalMs: 2, ...options});
  t.after(() => fs.rmSync(directory, {recursive: true}));
  const admit = (commandId = 'command', ctx = context()) => adapter.beforeCommandRun({commandId}, ctx);
  return {directory, owner, adapter, admit};
}

test('factory rejects malformed owner and unbounded or incompatible timer options', () => {
  assert.throws(() => createManagedCommandAdmission());
  const accessRuntime = {status() {}, admit() {}, finish() {}};
  for (const cleanupTimeoutMs of [0, -1, NaN, Infinity, 2 ** 31, 1.5]) {
    assert.throws(() => createManagedCommandAdmission({accessRuntime, cleanupTimeoutMs}));
  }
  for (const pollIntervalMs of [0, -1, Infinity, 101]) {
    assert.throws(() => createManagedCommandAdmission({accessRuntime, cleanupTimeoutMs: 100, pollIntervalMs}));
  }
});

test('held owner denies without a reservation and can admit after explicit release', async t => {
  const {owner, adapter, admit} = fixture(t);
  owner.acquire(token, owner.status().revision);
  assert.equal(admit().action, 'block');
  assert.equal(owner.status().active, 0);
  assert.equal(adapter.status().active, 0);
  owner.release(token);
  const admitted = admit(); assert.equal(admitted.action, 'allow');
  assert.equal(owner.status().active, 1);
  assert.throws(() => owner.acquire(token, owner.status().revision));
  await admitted.finish(controls('command'));
  assert.equal(owner.status().phase, 'idle');
});

test('unavailable, interrupted and aborted admission never reports allow', t => {
  for (const status of [{available: false, phase: 'idle'}, {available: true, phase: 'interrupted'},
    null, {available: true, phase: 'invented'}]) {
    const {owner, admit} = fixture(t, {}, actual => ({...actual, status: () => status}));
    assert.equal(admit().action, 'block'); assert.equal(owner.status().active, 0);
  }
  const {owner, admit} = fixture(t);
  const controller = new AbortController(); controller.abort();
  assert.equal(admit('aborted', {signal: controller.signal}).action, 'block');
  assert.equal(owner.status().active, 0);
});

test('private owner IDs cannot collide with an agent or another active command', async t => {
  const seen = [];
  const {owner, adapter, admit} = fixture(t, {}, actual => ({...actual, admit(event, ctx) {
    seen.push({event, ctx}); return actual.admit(event, ctx);
  }}));
  owner.admit(null, {runId: 'same'});
  const a = admit('same'), b = admit('other');
  assert.equal(a.action, 'allow'); assert.equal(b.action, 'allow');
  assert.equal(admit('same').action, 'block');
  assert.equal(owner.status().active, 3);
  assert.equal(seen[0].event, undefined);
  assert.notEqual(seen[0].ctx.runId, 'same');
  assert.notEqual(seen[0].ctx.runId, seen[1].ctx.runId);
  await a.finish(controls('same', exited));
  assert.equal(owner.status().active, 2);
  assert.equal(adapter.status().active, 1);
  await b.finish(controls('other'));
  assert.equal(owner.status().active, 1);
  owner.finish(null, {runId: 'same'});
  assert.equal(owner.status().phase, 'idle');
});

test('payloads are not inspected, forwarded to the owner or exposed by status', async t => {
  const {adapter} = fixture(t);
  const event = {commandId: 'private'};
  for (const key of ['jobId', 'argv', 'cwd', 'input', 'env']) Object.defineProperty(event, key,
    {get() { throw new Error('payload must not be read'); }});
  const admitted = adapter.beforeCommandRun(event, context());
  assert.equal(admitted.action, 'allow');
  assert.deepEqual(Object.keys(adapter.status()).sort(), ['active', 'closed', 'unknown']);
  await admitted.finish(controls('private'));
});

test('finish is pinned once and cannot release a later reservation with a reused command ID', async t => {
  let calls = 0;
  const {owner, admit} = fixture(t, {}, actual => ({...actual, finish(...args) {
    calls++; return actual.finish(...args);
  }}));
  const first = admit();
  const completed = first.finish(controls('command'));
  assert.equal(first.finish(controls('foreign', running)), completed);
  await completed;
  const second = admit(); assert.equal(second.action, 'allow');
  await first.finish(controls('command', exited));
  assert.equal(owner.status().active, 1); assert.equal(calls, 1);
  await second.finish(controls('command'));
  assert.equal(calls, 2);
});

test('normal command runtime is not limited by the cleanup deadline before finish arrives', async t => {
  const {owner, adapter, admit} = fixture(t, {cleanupTimeoutMs: 15});
  const admitted = admit();
  await delay(25);
  assert.equal(adapter.status().closed, false); assert.equal(owner.status().active, 1);
  await admitted.finish(controls('command'));
});

test('running descendants keep activity until terminal proof; cancellation is not automatic', async t => {
  const {owner, adapter, admit} = fixture(t);
  let state = running(), cancellations = 0;
  const admitted = admit();
  const completion = admitted.finish(controls('command', () => state, async () => { cancellations++; }));
  await delay(6);
  assert.equal(owner.status().active, 1); assert.equal(cancellations, 0);
  assert.throws(() => owner.acquire(token, owner.status().revision));
  state = exited(); await completion;
  assert.deepEqual(adapter.status(), {closed: false, active: 0, unknown: false});
});

for (const [name, supplied] of [
  ['mismatched identity', controls('foreign')],
  ['missing controls', {commandId: 'command'}],
  ['unknown', controls('command', () => ({state: 'unknown', scope: 'untracked'}))],
  ['exited without group ownership', controls('command', () => ({state: 'exited', pid: 4321}))],
  ['invalid process ID', controls('command', () => ({state: 'exited', scope: 'process-group', pid: 0}))],
  ['inspection throws', controls('command', () => { throw new Error('private diagnostic'); })],
]) test(`${name} retains unknown activity and pins rejection`, async t => {
  const {owner, adapter, admit} = fixture(t);
  const admitted = admit(), failed = admitted.finish(supplied);
  await assert.rejects(failed);
  assert.equal(admitted.finish(controls('command')), failed);
  assert.equal(owner.status().active, 1);
  assert.deepEqual(adapter.status(), {closed: true, active: 1, unknown: true});
  assert.equal(admit('another').action, 'block');
  await assert.rejects(adapter.shutdown());
});

test('cleanup timeout cannot be revived by later exit evidence', async t => {
  const {owner, adapter, admit} = fixture(t, {cleanupTimeoutMs: 12});
  let state = running(), inspections = 0;
  const admitted = admit();
  const failed = admitted.finish(controls('command', () => { inspections++; return state; }));
  await assert.rejects(failed);
  const stoppedAt = inspections; state = exited(); await delay(12);
  assert.equal(inspections, stoppedAt); assert.equal(owner.status().active, 1);
  assert.equal(admitted.finish(controls('command')), failed);
  await assert.rejects(adapter.shutdown());
});

test('cancel unavailable before finish does not pretend to stop a running root', async t => {
  const {owner, adapter, admit} = fixture(t);
  const admitted = admit();
  await assert.rejects(adapter.cancel('command'));
  assert.equal(owner.status().active, 1);
  await admitted.finish(controls('command'));
});

test('explicit cancellation is memoized and resolves only with independent exit proof', async t => {
  const {owner, adapter, admit} = fixture(t);
  let state = running(), cancellations = 0, cancelled = false;
  const admitted = admit();
  const completion = admitted.finish(controls('command', () => state, async () => { cancellations++; }));
  const cancel = adapter.cancel('command'); assert.equal(adapter.cancel('command'), cancel);
  cancel.then(() => { cancelled = true; });
  await delay(8);
  assert.equal(cancellations, 1); assert.equal(cancelled, false); assert.equal(owner.status().active, 1);
  state = exited(); await Promise.all([cancel, completion]);
  assert.equal(owner.status().active, 0);
});

test('cancel failure is not swallowed even if subsequent inspection would say exited', async t => {
  const {owner, adapter, admit} = fixture(t);
  let state = running();
  const admitted = admit();
  const completion = admitted.finish(controls('command', () => state,
    async () => { state = exited(); throw new Error('cancel failed'); }));
  const cancelled = adapter.cancel('command');
  await Promise.all([assert.rejects(cancelled), assert.rejects(completion)]);
  assert.equal(owner.status().active, 1); assert.equal(adapter.status().unknown, true);
});

test('pending cancellation is deadline bounded and cannot release on a late promise', async t => {
  const {owner, adapter, admit} = fixture(t, {cleanupTimeoutMs: 12});
  let release;
  const admitted = admit();
  const completion = admitted.finish(controls('command', running, () => new Promise(resolve => { release = resolve; })));
  const cancelled = adapter.cancel('command');
  await Promise.all([assert.rejects(cancelled), assert.rejects(completion)]);
  release(); await delay(4);
  assert.equal(owner.status().active, 1); assert.equal(adapter.status().unknown, true);
});

test('shutdown closes admission synchronously, waits for known cleanup and is idempotent', async t => {
  const {adapter, admit} = fixture(t);
  const admitted = admit();
  const closing = adapter.shutdown(); assert.equal(adapter.shutdown(), closing);
  assert.equal(admit('new').action, 'block');
  await admitted.finish(controls('command'));
  await closing;
  assert.deepEqual(adapter.status(), {closed: true, active: 0, unknown: false});
});

test('shutdown timeout retains a reservation even when finish has never arrived', async t => {
  const {owner, adapter, admit} = fixture(t, {cleanupTimeoutMs: 12});
  const admitted = admit();
  const closing = adapter.shutdown(); await assert.rejects(closing);
  assert.equal(adapter.shutdown(), closing);
  await assert.rejects(admitted.finish(controls('command')));
  assert.equal(owner.status().active, 1); assert.equal(adapter.status().unknown, true);
});

test('owner partial admission failure is closed and unknown', t => {
  const {owner, adapter, admit} = fixture(t, {}, actual => ({...actual, admit(...args) {
    actual.admit(...args); throw new Error('owner failed after reserving');
  }}));
  assert.equal(admit().action, 'block');
  assert.equal(owner.status().active, 1);
  assert.equal(adapter.status().closed, true); assert.equal(adapter.status().unknown, true);
});

test('actual owner custody failure cannot become a successful reservation or shutdown', async t => {
  const {directory, owner, adapter, admit} = fixture(t);
  fs.chmodSync(directory, 0o755);
  assert.equal(admit().action, 'block');
  // In parent563, save() checks privateEntry before its try/catch. The slot
  // remains busy but available can stay true. This test remains valid if the
  // parent also repairs that flag; denial alone still is not cleanup proof.
  assert.equal(owner.status().active, 1);
  assert.equal(adapter.status().active, 1);
  assert.equal(adapter.status().unknown, true);
  await assert.rejects(adapter.shutdown());
});

test('owner finish failure is sticky even after the owner already removed its slot', async t => {
  const {owner, adapter, admit} = fixture(t, {}, actual => ({...actual, finish(...args) {
    actual.finish(...args); throw new Error('owner finish failed');
  }}));
  const admitted = admit();
  await assert.rejects(admitted.finish(controls('command')));
  assert.equal(owner.status().active, 0);
  assert.deepEqual(adapter.status(), {closed: true, active: 1, unknown: true});
  await assert.rejects(adapter.shutdown());
});

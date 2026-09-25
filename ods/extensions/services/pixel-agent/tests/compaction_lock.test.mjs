import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json', import.meta.url)));
const replacement = manifest.replacements.find(([before]) => before.startsWith('\t\tasync reacquireAfterPrompt()'));
assert.ok(replacement, 'test the exact installed repair, not a copy of its implementation');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture({patched = true, fence = async () => {}, acquire, drain = async () => {}} = {}) {
  let acquisitions = 0, releases = 0;
  const calls = [];
  const lock = {async release() { releases++; }};
  const acquireLock = async () => {
    acquisitions++;
    if (acquire) return await acquire(acquisitions, lock);
    const request = deferred();
    calls.push(request);
    return await request.promise;
  };
  const controller = new Function('acquireLock', 'assertSessionFileFence', 'waitForHeldLockDrain',
    'let heldLock, promptLockReacquisition, takeoverDetected = false; return {' +
    replacement[patched ? 1 : 0] +
    'release() { heldLock = undefined; }, takeover() { takeoverDetected = true; }};')(
    acquireLock, fence, drain);
  return {controller, calls, lock, get acquisitions() { return acquisitions; }, get releases() { return releases; }};
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test('baseline reproduces duplicate physical acquisition for parallel summaries', async () => {
  const f = fixture({patched: false});
  const runs = [f.controller.reacquireAfterPrompt(), f.controller.reacquireAfterPrompt()];
  const settled = Promise.allSettled(runs);
  await tick();
  assert.equal(f.acquisitions, 2);
  f.calls[0].resolve(f.lock);
  f.calls[1].reject(new Error('session file locked (timeout 60000ms)'));
  assert.deepEqual((await settled).map(r => r.status), ['fulfilled', 'rejected']);
});

test('parallel summary calls share acquisition and also await fence verification', async () => {
  const fence = deferred();
  const f = fixture({fence: () => fence.promise});
  let completed = 0;
  const runs = Array.from({length: 8}, () => f.controller.reacquireAfterPrompt().then(() => completed++));
  await tick();
  assert.equal(f.acquisitions, 1);
  f.calls[0].resolve(f.lock);
  await tick();
  runs.push(f.controller.reacquireAfterPrompt().then(() => completed++));
  await tick();
  assert.equal(completed, 0, 'held lock alone is not a verified session');
  fence.resolve();
  await Promise.all(runs);
  assert.equal(completed, 9);
  assert.equal(f.acquisitions, 1);
  await f.controller.reacquireAfterPrompt();
  assert.equal(f.acquisitions, 1, 'already-held lock is reused');
});

test('acquisition failure reaches every waiter and a later attempt can recover', async () => {
  const failure = new Error('external owner still holds session');
  const f = fixture();
  const settled = Promise.allSettled([f.controller.reacquireAfterPrompt(), f.controller.reacquireAfterPrompt()]);
  await tick();
  f.calls[0].reject(failure);
  const outcomes = await settled;
  assert.ok(outcomes.every(r => r.status === 'rejected' && r.reason === failure));
  assert.equal(f.releases, 0);
  const retry = f.controller.reacquireAfterPrompt();
  await tick();
  f.calls[1].resolve(f.lock);
  await retry;
  assert.equal(f.acquisitions, 2);
});

test('fence failure releases exactly once and is never accepted by another waiter', async () => {
  const failure = new Error('session changed while prompt lock was released');
  const fence = deferred();
  const f = fixture({fence: () => fence.promise});
  const settled = Promise.allSettled([f.controller.reacquireAfterPrompt(), f.controller.reacquireAfterPrompt()]);
  await tick();
  f.calls[0].resolve(f.lock);
  await tick();
  fence.reject(failure);
  assert.ok((await settled).every(r => r.status === 'rejected' && r.reason === failure));
  assert.equal(f.releases, 1);
});

test('sequential compaction cycles reacquire once each without retaining a settled promise', async () => {
  const f = fixture({acquire: async (_n, lock) => lock});
  for (let cycle = 1; cycle <= 5; cycle++) {
    await Promise.all([f.controller.reacquireAfterPrompt(), f.controller.reacquireAfterPrompt()]);
    assert.equal(f.acquisitions, cycle);
    f.controller.release();
  }
});

test('takeover during drain does not acquire a new lock', async () => {
  const drain = deferred();
  const f = fixture({drain: () => drain.promise});
  const run = f.controller.reacquireAfterPrompt();
  f.controller.takeover();
  drain.resolve();
  await run;
  assert.equal(f.acquisitions, 0);
});

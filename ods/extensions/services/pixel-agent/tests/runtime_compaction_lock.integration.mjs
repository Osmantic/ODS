// OPENCLAW_PACKAGE_DIR supplies the pinned package. All session writes use temporary files.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {AsyncLocalStorage} from 'node:async_hooks';
import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {pathToFileURL} from 'node:url';

const root = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root, 'provide the reviewed OpenClaw package explicitly');
const manifest = JSON.parse(fs.readFileSync(new URL('../host/openclaw-compaction-budget.json', import.meta.url)));
const checksum = value => createHash('sha256').update(value).digest('hex');
let original = fs.readFileSync(path.join(root, 'dist/selection-BEwSQKM-.js'), 'utf8');
const digest = checksum(original);
const installed = digest === manifest.patchedSha256 ? manifest.replacements : manifest.previousReplacements[digest];
if (installed) {
  for (const [before, after] of [...installed].reverse()) {
    assert.equal(original.split(after).length, 2);
    original = original.replace(after, before);
  }
}
assert.equal(checksum(original), manifest.sourceSha256, 'reject unreviewed runtime bytes');
let candidate = original;
for (const [before, after] of manifest.replacements) {
  assert.equal(candidate.split(before).length, 2);
  candidate = candidate.replace(before, after);
}
assert.equal(checksum(candidate), manifest.patchedSha256);

process.env.VITEST = 'true'; // Disable the periodic lock watchdog, not physical locking.
const load = name => import(pathToFileURL(path.join(root, 'dist', name)));
const {t: acquireSessionWriteLock} = await load('session-write-lock-qJaTPAUi.js');
const deps = {
  AsyncLocalStorage, createHash, isDeepStrictEqual, fs$1: fsp,
  readFileSync: fs.readFileSync, statSync: fs.statSync, createReadStream: fs.createReadStream,
  resolveEmbeddedSessionFileKey: (await load('runs-BAx657TN.js')).C,
  resolveGlobalSingleton: (await load('global-singleton-PwlQSEal.js')).n,
  normalizeStringEntries: (await load('string-normalization-CRyoFBPt.js')).l,
  isTranscriptOnlyOpenClawAssistantMessage$1: (await load('session-accessor-FolLuwNc.js')).U,
  isSessionWriteLockAcquireError: (await load('session-write-lock-error-CYOzPsPk.js')).r,
};

function runtime(source, overrides = {}) {
  const start = '//#region src/agents/embedded-agent-runner/run/attempt.session-lock.ts';
  const end = '//#region src/agents/embedded-agent-runner/google-prompt-cache.ts';
  assert.equal(source.split(start).length, 2);
  assert.equal(source.split(end).length, 2);
  const region = source.slice(source.indexOf(start), source.indexOf(end));
  const runtimeDeps = {...deps, ...overrides};
  return new Function(...Object.keys(runtimeDeps), region +
    '\nreturn {createEmbeddedAttemptSessionLockController, installPromptSubmissionLockRelease};')(...Object.values(runtimeDeps));
}

const fixtureHeader = '{"type":"session","id":"fixture"}\n';

async function fixture(t, source = candidate, overrides = {}) {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'ods-compaction-lock-'));
  const sessionFile = path.join(dir, 'session.jsonl');
  await fsp.writeFile(sessionFile, fixtureHeader);
  const api = runtime(source, overrides);
  const controller = await api.createEmbeddedAttemptSessionLockController({acquireSessionWriteLock,
    lockOptions: {sessionFile, timeoutMs: 200, staleMs: 30000, maxHoldMs: 30000}});
  t.after(async () => {
    await controller.dispose();
    await fsp.rm(dir, {recursive: true, force: true});
  });
  return {api, controller, sessionFile};
}

test('unpatched runtime reproduces the split-summary self-lock timeout', async t => {
  const {controller} = await fixture(t, original);
  await controller.releaseForPrompt();
  const outcomes = await Promise.allSettled([controller.reacquireAfterPrompt(), controller.reacquireAfterPrompt()]);
  assert.equal(outcomes.filter(r => r.status === 'fulfilled').length, 1);
  assert.match(outcomes.find(r => r.status === 'rejected').reason.message, /session file locked/);
});

test('parallel streams reacquire once, resume writes and retain external exclusion', async t => {
  const {api, controller, sessionFile} = await fixture(t);
  const session = {agent: {async streamFn() { return 'summary'; }}};
  api.installPromptSubmissionLockRelease({session, ...controller});
  for (let cycle = 0; cycle < 3; cycle++) {
    assert.deepEqual(await Promise.all([session.agent.streamFn(), session.agent.streamFn()]), ['summary', 'summary']);
    await assert.rejects(acquireSessionWriteLock({sessionFile, timeoutMs: 100}), /session file locked/);
    await controller.withSessionWriteLock(() => fsp.appendFile(sessionFile, '{"type":"custom","value":1}\n'));
  }
  await controller.dispose();
  assert.equal(fs.existsSync(sessionFile + '.lock'), false);
});

test('parallel waiters still reject an externally replaced transcript', async t => {
  const {controller, sessionFile} = await fixture(t);
  await controller.releaseForPrompt();
  await fsp.writeFile(sessionFile + '.replacement', '{"type":"session","id":"other-owner"}\n');
  await fsp.rename(sessionFile + '.replacement', sessionFile);
  const outcomes = await Promise.allSettled([controller.reacquireAfterPrompt(), controller.reacquireAfterPrompt()]);
  assert.ok(outcomes.every(r => r.status === 'rejected' && r.reason.name === 'EmbeddedAttemptSessionTakeoverError'));
  assert.equal(controller.hasSessionTakeover(), true);
  assert.equal(fs.existsSync(sessionFile + '.lock'), false);
});

test('independent sessions can compact concurrently', async t => {
  const a = await fixture(t), b = await fixture(t);
  await Promise.all([a.controller.releaseForPrompt(), b.controller.releaseForPrompt()]);
  await Promise.all([a.controller.reacquireAfterPrompt(), a.controller.reacquireAfterPrompt(),
    b.controller.reacquireAfterPrompt(), b.controller.reacquireAfterPrompt()]);
  assert.equal(fs.existsSync(a.sessionFile + '.lock'), true);
  assert.equal(fs.existsSync(b.sessionFile + '.lock'), true);
});

// Use the last shipped recipe, not an invented controller, for the negative control.
const predecessorHash = '670a1c0007f7fc2d3c623b3d0882e94d472513b60c43bf97b1d3ac3f586d2149';
assert.ok(manifest.previousReplacements[predecessorHash], 'reviewed predecessor recipe is required');
let predecessor = original;
for (const [before, after] of manifest.previousReplacements[predecessorHash]) {
  assert.equal(predecessor.split(before).length, 2);
  predecessor = predecessor.replace(before, after);
}
assert.equal(checksum(predecessor), predecessorHash);

async function bounded(promise) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('session fence regression timed out')), 2000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

for (const [name, source] of [['predecessor', predecessor], ['candidate', candidate]]) {
  test(`${name}: owned write queued at fingerprint capture`, async t => {
    let armed = false, current, resolveOwned, rejectOwned;
    const trace = [];
    const owned = new Promise((resolve, reject) => { resolveOwned = resolve; rejectOwned = reject; });
    owned.catch(() => {});
    function capture(file, value) {
      if (armed && path.resolve(file) === current.sessionFile) {
        armed = false;
        trace.push(Number(value.size));
        // The same scheduling hook wraps actual async and sync filesystem reads.
        // The old await yields to this owned append; the fixed comparison does not.
        queueMicrotask(() => {
          try {
            fs.appendFileSync(file, '{"type":"message","id":"owned","parentId":null,"message":{"role":"assistant","content":"owned"}}\n');
            current.controller.refreshAfterOwnedSessionWrite();
            trace.push(fs.statSync(file).size);
            resolveOwned();
          } catch (error) { rejectOwned(error); }
        });
      }
      return value;
    }
    current = await fixture(t, source, {
      fs$1: {...fsp, stat: async (file, options) => capture(file, await fsp.stat(file, options))},
      statSync: (file, options) => capture(file, fs.statSync(file, options)),
    });
    await current.controller.releaseForPrompt();
    armed = true;
    const outcome = await bounded(current.controller.reacquireAfterPrompt().then(
      () => ({ok: true}), error => ({ok: false, name: error.name})));
    await bounded(owned);
    assert.equal(trace.length, 2);
    assert.equal(trace[0], Buffer.byteLength(fixtureHeader));
    assert.ok(trace[1] > trace[0]);
    if (name === 'predecessor') {
      assert.deepEqual(outcome, {ok: false, name: 'EmbeddedAttemptSessionTakeoverError'});
      assert.equal(current.controller.hasSessionTakeover(), true);
    } else {
      assert.deepEqual(outcome, {ok: true});
      assert.equal(current.controller.hasSessionTakeover(), false);
      let entered = false;
      await current.controller.withSessionWriteLock(() => { entered = true; });
      assert.equal(entered, true);
    }
  });

  for (const mutation of ['replace', 'rewrite']) {
    test(`${name}: external ${mutation} rejects before executing a write`, async t => {
      const {controller, sessionFile} = await fixture(t, source);
      await controller.releaseForPrompt();
      const target = mutation === 'replace' ? sessionFile + '.replacement' : sessionFile;
      await fsp.writeFile(target, '{"type":"session","id":"external"}\n');
      if (mutation === 'replace') await fsp.rename(target, sessionFile);
      await assert.rejects(bounded(controller.reacquireAfterPrompt()),
        error => error.name === 'EmbeddedAttemptSessionTakeoverError');
      let entered = false;
      await assert.rejects(controller.withSessionWriteLock(() => { entered = true; }),
        error => error.name === 'EmbeddedAttemptSessionTakeoverError');
      assert.equal(entered, false);
      assert.equal(fs.existsSync(sessionFile + '.lock'), false);
    });
  }
}

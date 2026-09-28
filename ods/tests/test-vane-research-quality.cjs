'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { patchBundle, runCli, PATCHES } = require('../extensions/services/perplexica/patch-research-quality.js');

// Verbatim executeAll from the pinned v1.12.2 production ActionRegistry.
const originalMethod = 'static async executeAll(a,b){let c=[];return await Promise.all(a.map(async a=>{let d=await this.execute(a.name,a.arguments,b);c.push(d)})),c}';
const calls = [{ id: 'request-a', name: 'slow', arguments: {} }, { id: 'request-b', name: 'fast', arguments: {} }];
function registry(source) { return Function('return class Registry {' + source + '}')(); }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

for (const patched of [false, true]) {
  test(`${patched ? 'patched' : 'upstream'} results bind to tool IDs when completion order reverses`, async () => {
    const source = patched ? patchBundle(originalMethod).source : originalMethod;
    const R = registry(source), slow = deferred(), fast = deferred(), started = [];
    R.execute = async name => { started.push(name); return name === 'slow' ? slow.promise : fast.promise; };
    const pending = R.executeAll(calls, {});
    assert.deepEqual(started, ['slow', 'fast'], 'both actions start before either gate resolves');
    fast.resolve('fast evidence');
    await Promise.resolve(); await Promise.resolve();
    slow.resolve('slow evidence');
    const results = await pending;
    const pairs = calls.map((call, i) => [call.id, results[i]]);
    assert.deepEqual(pairs, patched
      ? [['request-a', 'slow evidence'], ['request-b', 'fast evidence']]
      : [['request-a', 'fast evidence'], ['request-b', 'slow evidence']]);
  });
}

test('ordered registry preserves rejection and an empty action list', async () => {
  const R = registry(patchBundle(originalMethod).source);
  R.execute = async name => { if (name === 'fast') throw new Error('reader failed'); return 'ok'; };
  await assert.rejects(R.executeAll(calls, {}), /reader failed/);
  R.execute = () => { throw new Error('must not execute'); };
  assert.deepEqual(await R.executeAll([], {}), []);
});

test('extractor prefix replacements are idempotent and repair mixed old/new occurrences', () => {
  const old = 'Assistant is an AI information extractor.';
  const once = patchBundle(old).source;
  assert.match(once, /empty extracted_facts string/);
  assert.equal(patchBundle(once).source, once);
  assert.equal(patchBundle(once + '\n' + old).source, once + '\n' + once);
  assert.equal(patchBundle(old + '\n' + old).source, once + '\n' + once);
});

test('deployed attribution-free prompts upgrade without duplicating prior instructions', () => {
  const legacyExtractor = PATCHES.find(p => p.id === 'quality-15').replacement;
  const legacyWriter = PATCHES.find(p => p.id === 'quality-14').replacement;
  const input = legacyExtractor + '\n' + legacyWriter;
  const upgraded = patchBundle(input);
  assert.deepEqual(upgraded.applied.map(p => p.id), ['quality-18', 'quality-19']);
  assert.equal(upgraded.source.split('Missing information in this chunk').length - 1, 1);
  assert.equal(upgraded.source.split('Extraction notes such as').length - 1, 1);
  assert.equal(patchBundle(upgraded.source).source, upgraded.source);
  assert.equal(patchBundle(upgraded.source).applied.length, 0);
  const mixed = patchBundle(upgraded.source + '\n' + input);
  assert.equal(mixed.source, upgraded.source + '\n' + upgraded.source);
});

test('unknown bundles are untouched; partial recognized bundles fail before any writes', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-vane-quality-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const a = path.join(root, 'a.js'), b = path.join(root, 'b.js');
  fs.writeFileSync(a, 'const unchanged = 1;');
  assert.deepEqual(runCli(root), { skipped: true, files: 0, replacements: 0 });
  fs.writeFileSync(b, originalMethod);
  assert.throws(() => runCli(root), /Unsupported partial research bundle/);
  assert.equal(fs.readFileSync(a, 'utf8'), 'const unchanged = 1;');
  assert.equal(fs.readFileSync(b, 'utf8'), originalMethod);
});

const actualPath = process.env.VANE_TEST_BUNDLE;
test('actual pinned image bundle has all anchors and remains valid/idempotent', { skip: !actualPath }, t => {
  const source = fs.readFileSync(actualPath, 'utf8');
  const result = patchBundle(source);
  assert.equal(new Set(result.recognized).size, 19);
  assert.equal(result.applied.length, 19);
  assert.ok(!result.source.includes('SHALL NOT BE LESS THAN AT LEAST 2000 WORDS'));
  assert.ok(!result.source.includes('exhaust your research budget first'));
  assert.match(result.source, /The iteration budget is an upper bound, not a quota/);
  assert.match(result.source, /requested question is supported/);
  assert.match(result.source, /not statements made by the cited page/);
  assert.match(result.source, /For each fact, retain who made the claim and who or what it concerns/);
  assert.match(result.source, /Do not infer authorship, endorsement or agreement from quotation or proximity/);
  assert.equal(patchBundle(result.source).source, result.source);
  assert.equal(patchBundle(result.source).applied.length, 0);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-vane-real-bundle-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const split = source.indexOf(',43090:');
  assert.ok(split > 0, 'split on the actual prompt module boundary');
  const a = path.join(root, 'a.js'), b = path.join(root, 'b.js');
  fs.writeFileSync(a, patchBundle(source.slice(0, split)).source);
  fs.writeFileSync(b, source.slice(split));
  assert.ok(runCli(root).files > 0, 'patched fragments still contribute to global validation');
  assert.equal(fs.readFileSync(a, 'utf8') + fs.readFileSync(b, 'utf8'), result.source);
  assert.deepEqual(runCli(root), { skipped: false, files: 0, replacements: 0 });
  const complete = path.join(root, 'complete.cjs');
  fs.writeFileSync(complete, result.source);
  const checked = require('node:child_process').spawnSync(process.execPath, ['--check', complete], { encoding: 'utf8' });
  assert.equal(checked.status, 0, checked.stderr);
});

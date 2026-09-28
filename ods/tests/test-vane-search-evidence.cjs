'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { patchBundle } = require('../extensions/services/perplexica/patch-research-quality.js');

// Verbatim module 40824 from the pinned full v1.12.2 image. Imports are stubbed;
// the production search action itself is executed, not a rewritten algorithm.
const original = fs.readFileSync(path.join(__dirname, 'fixtures/vane-search-action-v1.12.2.js.txt'), 'utf8').replace(/\r\n/g, '\n').trimEnd();
const patched = patchBundle(original).source;
const results = [
  { title: 'Aria Vance quoted by J. Pell', url: 'https://example.test/article', content: 'Aria Vance; The River Remembers; unrelated header and navigation.' },
  { title: 'Aria Vance profile', url: 'https://example.test/profile', content: 'Aria Vance founded Example Lab.' },
  { title: 'Mohamed Osman artist', url: 'https://example.test/unrelated', content: 'Different person, artist and politician.' },
];

test('fixture matches the pinned production module and an optional full image bundle', () => {
  assert.equal(crypto.createHash('sha256').update(original).digest('hex'), 'f2ffc9e140df88f3de126d1086b2af1d764cde03dbe300d975405612a51bdcd9');
  if (process.env.VANE_TEST_BUNDLE) {
    const bundle = fs.readFileSync(process.env.VANE_TEST_BUNDLE, 'utf8');
    const start = bundle.indexOf('40824:'), end = bundle.indexOf(',41438:', start);
    assert.ok(start > 0 && end > start);
    assert.equal(bundle.slice(start + '40824:'.length, end), original);
  }
  assert.equal(patchBundle(patched).source, patched);
  assert.equal(patchBundle(patched).applied.length, 0);
});

async function execute(source, mode, options = {}) {
  const calls = { embedding: 0, picker: 0, extraction: 0, pages: [], active: 0, maxActive: 0 };
  const schema = { describe() { return this; } };
  const dependencies = {
    56471: { n: async () => ({ results }) },
    25341: { A: (a, b) => a[0] * b[0] + a[1] * b[1] },
    46901: { Ay$: { object: () => schema, array: () => schema, number: () => schema, string: () => schema } },
    96227: { A: { scrape: async url => {
      calls.pages.push(url);
      if (options.readerFails) throw Error('fixture reader unavailable');
      return { content: 'Page author: J. Pell. Quoted speaker: Aria Vance.' };
    } } },
    29092: { A: content => Array.from({ length: 6 }, (_, index) => content + ' chunk ' + index) },
  };
  const requireModule = id => {
    assert.ok(Object.hasOwn(dependencies, id), 'unexpected production import ' + id);
    return dependencies[id];
  };
  requireModule.d = (exports, getters) => Object.defineProperties(exports,
    Object.fromEntries(Object.entries(getters).map(([key, get]) => [key, { get }])));
  const exports = {};
  Function('crypto', 'console', 'return (' + source + ')')(crypto, { log() {} })({}, exports, requireModule);
  const documents = await exports.k({
    mode, queries: ['Aria Vance in AI'],
    researchBlock: { id: 'fixture-research', data: { subSteps: [] } }, session: { updateBlock() {} },
    embedding: { embedText: async texts => {
      calls.embedding++;
      if (!options.workingEmbedding) throw Error('fixture embedding unavailable');
      return texts.map(text => text.includes('Mohamed') ? [0, 1] : [1, 0]);
    } },
    llm: { generateObject: async request => {
      if (request.messages[0].content.includes('search result picker')) {
        calls.picker++;
        if (options.pickerFails) throw Error('fixture picker unavailable');
        return { picked_indices: [0, 1] };
      }
      if (source !== original) assert.match(request.messages[0].content, /Preserve attribution roles/);
      const ordinal = ++calls.extraction;
      calls.active++;
      calls.maxActive = Math.max(calls.maxActive, calls.active);
      try {
        await new Promise(resolve => setTimeout(resolve, 3));
        if (options.firstExtractionFails && ordinal === 1) throw Error('fixture extractor unavailable');
        return { extracted_facts: options.emptyFacts ? ' \n ' : 'Page author: J. Pell. Quoted speaker: Aria Vance.' };
      } finally { calls.active--; }
    } },
  });
  return { documents, calls };
}

for (const mode of ['speed', 'balanced']) {
  test(`upstream ${mode} leaks unread snippets when embeddings fail`, async () => {
    const { documents, calls } = await execute(original, mode);
    assert.equal(calls.picker, 0);
    assert.equal(calls.pages.length, 0);
    assert.ok(documents.some(document => document.metadata.url.endsWith('/unrelated')));
  });
  test(`upstream ${mode} still returns unread snippets with working embeddings`, async () => {
    const { documents, calls } = await execute(original, mode, { workingEmbedding: true });
    assert.equal(calls.picker, 0);
    assert.equal(calls.pages.length, 0);
    assert.ok(documents.some(document => document.content.includes('unrelated header and navigation')));
  });
}

for (const mode of ['speed', 'balanced', 'quality']) {
  test(`${mode} supplies bounded page facts and keeps their source metadata`, async () => {
    const { documents, calls } = await execute(patched, mode);
    const pages = mode === 'speed' ? 1 : 2;
    assert.equal(calls.embedding, 0);
    assert.equal(calls.picker, 1);
    assert.equal(calls.pages.length, pages);
    assert.equal(calls.extraction, pages * (mode === 'speed' ? 2 : mode === 'balanced' ? 4 : 6));
    if (mode !== 'quality') assert.ok(calls.maxActive <= pages);
    assert.ok(documents.length > 0);
    assert.ok(documents.every(document => document.content.trimEnd().split('\n').every(fact =>
      fact === 'Page author: J. Pell. Quoted speaker: Aria Vance.')));
    assert.ok(documents.every(document => calls.pages.includes(document.metadata.url)));
    assert.ok(!documents.some(document => document.metadata.url.endsWith('/unrelated')));
    if (mode === 'quality') {
      const upstream = await execute(original, mode);
      assert.equal(calls.extraction, upstream.calls.extraction, 'Quality retains full chunk coverage');
      assert.deepEqual(calls.pages, upstream.calls.pages);
      assert.ok(calls.maxActive > 2, 'Quality concurrency is not constrained by the short-mode queue');
    }
  });
  test(`${mode} drops empty extracted facts without falling back to snippets`, async () => {
    const { documents, calls } = await execute(patched, mode, { emptyFacts: true });
    assert.ok(calls.extraction > 0);
    assert.deepEqual(documents, []);
  });
  test(`${mode} returns no snippet fallback when page reading fails`, async () => {
    const { documents, calls } = await execute(patched, mode, { readerFails: true });
    assert.equal(calls.picker, 1);
    assert.equal(calls.extraction, 0);
    assert.deepEqual(documents, []);
  });
}

for (const mode of ['speed', 'balanced']) {
  test(`${mode} releases its extraction queue after a failed chunk`, { timeout: 2000 }, async () => {
    const { documents, calls } = await execute(patched, mode, { firstExtractionFails: true });
    const expected = mode === 'speed' ? 2 : 8;
    assert.equal(calls.extraction, expected);
    assert.equal(documents.reduce((count, document) => count + document.content.trimEnd().split('\n').length, 0), expected - 1);
    assert.ok(calls.maxActive <= (mode === 'speed' ? 1 : 2));
  });
}

test('picker failure rejects and unsupported modes produce no raw evidence fallback', async () => {
  await assert.rejects(execute(patched, 'balanced', { pickerFails: true }), /fixture picker unavailable/);
  const unsupported = await execute(patched, 'unsupported');
  assert.deepEqual(unsupported.documents, []);
  assert.equal(unsupported.calls.picker, 0);
  assert.equal(unsupported.calls.embedding, 0);
});

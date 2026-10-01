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
  const calls = { embedding: 0, picker: 0, extraction: 0, pages: [], active: 0, maxActive: 0,
    pickerRequests: [], extractionRequests: [] };
  const schema = (kind, shape) => ({kind, shape, describe() { return this; }});
  const pageContent = options.pageContent ?? 'Page author: J. Pell. Quoted speaker: Aria Vance.';
  const researchBlock = {id:'fixture-research',data:{subSteps:(options.visitedURLs ?? []).map(url =>
    ({type:'reading',reading:[{metadata:{url}}]}))}};
  const dependencies = {
    56471: { n: async () => ({ results: options.searchResults ?? results }) },
    25341: { A: (a, b) => a[0] * b[0] + a[1] * b[1] },
    46901: { Ay$: { object: shape => schema('object', shape), array: shape => schema('array', shape),
      number: () => schema('number'), string: () => schema('string') } },
    96227: { A: { scrape: async url => {
      calls.pages.push(url);
      if (options.readerFails || options.readerFailsURL === url) throw Error('fixture reader unavailable');
      return {content:options.pageContentByURL?.[url] ?? pageContent};
    } } },
    29092: { A: content => options.chunks ?? Array.from({ length: 6 }, (_, index) => content + ' chunk ' + index) },
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
    researchBlock, session: { updateBlock() {} },
    embedding: { embedText: async texts => {
      calls.embedding++;
      if (!options.workingEmbedding) throw Error('fixture embedding unavailable');
      return texts.map(text => text.includes('Mohamed') ? [0, 1] : [1, 0]);
    } },
    llm: { generateObject: async request => {
      if (request.messages[0].content.includes('search result picker')) {
        calls.picker++;
        calls.pickerRequests.push(request);
        if (options.pickerFails) throw Error('fixture picker unavailable');
        return { picked_indices: options.pickedIndices ?? [0, 1] };
      }
      calls.extractionRequests.push(request);
      const ordinal = ++calls.extraction;
      calls.active++;
      calls.maxActive = Math.max(calls.maxActive, calls.active);
      try {
        await new Promise(resolve => setTimeout(resolve, 3));
        if (options.firstExtractionFails && ordinal === 1) throw Error('fixture extractor unavailable');
        if (source === original) return {extracted_facts: pageContent};
        if (options.responseForRequest) return options.responseForRequest(request, ordinal);
        return options.extractionResponse ?? {
          facts: options.emptyFacts ? [] : [{text: pageContent, evidence_quote: pageContent}],
          retrieval_notes: 'Reader could not establish an unrelated role.',
        };
      } finally { calls.active--; }
    } },
  });
  calls.readingAttempts = researchBlock.data.subSteps.filter(step => step.type === 'reading')
    .slice((options.visitedURLs ?? []).length).flatMap(step => step.reading.map(doc => doc.metadata.url));
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
      fact === 'Page author: J. Pell. Quoted speaker: Aria Vance.' ||
      fact === 'Source quote: Page author: J. Pell. Quoted speaker: Aria Vance.')));
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
    assert.equal(documents.reduce((count, document) => count + document.content.trimEnd().split('\n').length, 0), 2 * (expected - 1));
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

const relatedResults = [results[0], results[1],
  {title:'Aria Vance research',url:'https://example.test/research',content:'Aria Vance works in AI.'}];
for (const mode of ['speed', 'balanced']) {
  test(`${mode} applies its page budget to eligible picks after a previous reading`, async () => {
    const {documents, calls} = await execute(patched, mode, {
      searchResults:relatedResults, pickedIndices:[0,1,2], visitedURLs:[results[0].url],
    });
    assert.deepEqual(calls.pages, mode === 'speed'
      ? [relatedResults[1].url] : [relatedResults[1].url, relatedResults[2].url]);
    assert.ok(documents.length > 0);
  });
}
test('short-mode invalid and duplicate picks do not consume eligible page slots', async () => {
  const invalid = await execute(patched, 'speed', {pickedIndices:[99,0,1]});
  assert.deepEqual(invalid.calls.pages, [results[0].url]);
  const duplicate = await execute(patched, 'balanced', {pickedIndices:[0,0,1]});
  assert.deepEqual(duplicate.calls.pages, [results[0].url, results[1].url]);
  const visitedDuplicates = await execute(patched, 'speed', {
    pickedIndices:[0,0,0,1], visitedURLs:[results[0].url],
  });
  assert.deepEqual(visitedDuplicates.calls.pages, [results[1].url]);
});
test('Quality retains its original selected-index cap and full page extraction', async () => {
  const {calls} = await execute(patched, 'quality', {
    searchResults:relatedResults, pickedIndices:[99,0,1,2], visitedURLs:[results[0].url],
  });
  assert.deepEqual(calls.pages, [results[1].url]);
  assert.equal(calls.extraction, 6);
});

for (const mode of ['speed', 'balanced']) {
  test(`${mode} picker omits prior URLs without renumbering remaining indices`, async () => {
    const {calls} = await execute(patched, mode, {
      searchResults:relatedResults, pickedIndices:[1,2], visitedURLs:[results[0].url],
    });
    const [request] = calls.pickerRequests;
    assert.match(request.messages[0].content, /rather than being renumbered/);
    assert.ok(!request.messages[1].content.includes(results[0].url));
    assert.ok(!request.messages[1].content.includes('<result indice=0>'));
    assert.match(request.messages[1].content, /<result indice=1>/);
    assert.match(request.messages[1].content, /<result indice=2>/);
  });
}
test('Quality picker messages are byte-identical to upstream, including prior URLs', async () => {
  const options = {searchResults:relatedResults, pickedIndices:[0,1], visitedURLs:[results[0].url]};
  const upstream = await execute(original, 'quality', options);
  const candidate = await execute(patched, 'quality', options);
  assert.deepEqual(candidate.calls.pickerRequests[0].messages, upstream.calls.pickerRequests[0].messages);
});
test('a picker returning only a prior index never triggers blind reading', async () => {
  const {calls, documents} = await execute(patched, 'speed', {
    searchResults:relatedResults, pickedIndices:[0], visitedURLs:[results[0].url],
  });
  assert.deepEqual(calls.pages, []);
  assert.equal(calls.extraction, 0);
  assert.deepEqual(documents, []);
});
test('typed schema and prompt retain semantic guards and remove the legacy output contract', async () => {
  const {calls} = await execute(patched, 'speed');
  const [request] = calls.extractionRequests;
  const prompt = request.messages[0].content;
  assert.match(prompt, /Preserve attribution roles/);
  assert.match(prompt, /Check entity identity/);
  assert.match(prompt, /decorative interface labels/);
  assert.match(prompt, /Genuine source-stated negative facts/);
  assert.match(prompt, /Return raw JSON/);
  assert.ok(!prompt.includes('extracted_facts'));
  assert.deepEqual(Object.keys(request.schema.shape), ['facts', 'retrieval_notes']);
  assert.equal(request.schema.shape.facts.kind, 'array');
  assert.deepEqual(Object.keys(request.schema.shape.facts.shape.shape), ['text', 'evidence_quote']);
});
test('positive facts and genuine source-stated negatives survive, while coverage notes do not', async () => {
  const positive = 'Aria Vance founded Example Lab.';
  const negative = 'Aria Vance has not joined Example University.';
  const {documents} = await execute(patched, 'speed', {
    pageContent:positive + ' ' + negative,
    extractionResponse:{facts:[{text:positive,evidence_quote:positive},{text:negative,evidence_quote:negative}],
      retrieval_notes:'No information found about funding; the reader could not establish it.'},
  });
  assert.equal(documents.length, 1);
  assert.ok(documents[0].content.includes(positive));
  assert.ok(documents[0].content.includes(negative));
  assert.ok(!documents[0].content.includes('funding'));
  assert.equal(documents[0].metadata.url, results[0].url);
});
test('quote verification uses the current actual chunk, not another chunk of the page', async () => {
  const supported = 'Aria Vance founded Example Lab.';
  const {documents} = await execute(patched, 'speed', {
    chunks:['A different paragraph.', supported],
    extractionResponse:{facts:[{text:supported,evidence_quote:supported}],retrieval_notes:'Do not forward'},
  });
  assert.equal(documents.length, 1);
  assert.equal(documents[0].content, supported + '\nSource quote: ' + supported + '\n');
});
test('whitespace and Unicode in an actual supporting quote are accepted without changing the quote', async () => {
  const quote = 'Aria\u00a0Vance\n founded\tExample Lab.';
  const {documents} = await execute(patched, 'speed', {
    pageContent:'Aria Vance founded Example Lab.',
    extractionResponse:{facts:[{text:'She founded Example Lab.',evidence_quote:quote}],retrieval_notes:''},
  });
  assert.equal(documents.length, 1);
  assert.ok(documents[0].content.includes('Source quote: ' + quote));
});
test('unsupported, malformed, missing and empty quotes cannot become cited documents', async () => {
  const {documents} = await execute(patched, 'speed', {extractionResponse:{facts:[
    null, 'a string', {text:42,evidence_quote:'Page author: J. Pell.'},
    {text:'Wrong fact',evidence_quote:'Absent from the actual page'},
    {text:'No quote'}, {text:'Empty',evidence_quote:' '}, {text:'',evidence_quote:'Page author: J. Pell.'},
    {text:'Non-string quote',evidence_quote:42},
  ],retrieval_notes:'No information found'}});
  assert.deepEqual(documents, []);
});
test('legacy flat notes and typed coverage-only outputs produce no documents', async () => {
  for (const extractionResponse of [
    {extracted_facts:'The reader could not establish a fact.'},
    {facts:[],retrieval_notes:'No information found about the requested person.'},
  ]) {
    const {documents, calls} = await execute(patched, 'balanced', {extractionResponse});
    assert.ok(calls.extraction > 0);
    assert.deepEqual(documents, []);
  }
});

test('Speed tries the next explicitly selected page after an empty first extraction', async () => {
  const fact = 'Aria Vance founded Example Lab.';
  const {documents,calls} = await execute(patched, 'speed', {
    pageContentByURL:{[results[0].url]:'Unrelated paragraph.',[results[1].url]:fact},
    responseForRequest:request => request.messages[1].content.includes(fact)
      ? {facts:[{text:fact,evidence_quote:fact}],retrieval_notes:''}
      : {facts:[],retrieval_notes:'Reader could not establish the requested role.'},
  });
  assert.deepEqual(calls.pages,[results[0].url,results[1].url]);
  assert.deepEqual(calls.readingAttempts,calls.pages);
  assert.equal(calls.extraction,4);
  assert.equal(calls.maxActive,1);
  assert.equal(documents.length,1);
  assert.equal(documents[0].metadata.url,results[1].url);
  assert.ok(!documents[0].content.includes('could not establish'));
});
test('Speed stops after the first useful source without reading or reporting the runner-up', async () => {
  const {documents,calls} = await execute(patched,'speed',{pickedIndices:[0,1,2]});
  assert.equal(documents.length,1);
  assert.deepEqual(calls.pages,[results[0].url]);
  assert.deepEqual(calls.readingAttempts,calls.pages);
  assert.equal(calls.extraction,2);
});
test('Speed bounds all-empty fallback to two attempted pages and four chunk extractions', async () => {
  const {documents,calls} = await execute(patched,'speed',{emptyFacts:true,pickedIndices:[0,1,2]});
  assert.deepEqual(documents,[]);
  assert.deepEqual(calls.pages,[results[0].url,results[1].url]);
  assert.deepEqual(calls.readingAttempts,calls.pages);
  assert.equal(calls.extraction,4);
  assert.equal(calls.maxActive,1);
});
test('Speed releases its page queue after a failed first reader', {timeout:2000}, async () => {
  const {documents,calls} = await execute(patched,'speed',{readerFailsURL:results[0].url});
  assert.deepEqual(calls.pages,[results[0].url,results[1].url]);
  assert.deepEqual(calls.readingAttempts,calls.pages);
  assert.equal(calls.extraction,2);
  assert.equal(documents.length,1);
  assert.equal(documents[0].metadata.url,results[1].url);
});
test('Speed rejects unverified quotes in the first page and uses a supported selected runner-up', async () => {
  const fact = 'Aria Vance founded Example Lab.';
  const {documents,calls} = await execute(patched,'speed',{
    pageContentByURL:{[results[0].url]:'Other content.',[results[1].url]:fact},
    responseForRequest:() => ({facts:[{text:fact,evidence_quote:fact}],retrieval_notes:''}),
  });
  assert.deepEqual(calls.pages,[results[0].url,results[1].url]);
  assert.equal(documents.length,1);
  assert.equal(documents[0].metadata.url,results[1].url);
});

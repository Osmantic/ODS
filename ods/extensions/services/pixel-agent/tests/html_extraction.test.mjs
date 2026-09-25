// HTML extraction off the gateway thread, and the shared reader's contract.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  boundedHtml, createIsolatedHtmlExtractor, locateSdkExtractor, HTML_EXTRACTION_ABORTED, HTML_EXTRACTION_LIMITS,
  HTML_EXTRACTION_TIMEOUT,
} from '../plugin/html-extraction.mjs';
import {createPublicPageReader} from '../plugin/web-extract.mjs';
import {t as fixtureExtract} from './fixtures/html-extraction/extractor.mjs';

const FIXTURES = new URL('./fixtures/html-extraction/', import.meta.url);
const located = {moduleUrl: new URL('extractor.mjs', FIXTURES).href, exportName: 't'};
// 1 MB of list items whose closing tags are missing: quadratic for lazy
// element regexes (tower2, pinned SDK extractor: 10.2 s in-process).
const UNCLOSED = '<html><body><ul>' + '<li>item text '.repeat(75_000) + '</ul></body></html>';

// Largest gap between 10 ms timer ticks while `work` runs: a measure of how
// long the calling thread was blocked.
async function blockedFor(work) {
  let last = performance.now(), gap = 0;
  const timer = setInterval(() => { const now = performance.now(); gap = Math.max(gap, now - last); last = now; }, 10);
  try {
    const result = await work();
    return {result, gap};
  } catch (error) {
    return {error, gap};
  } finally {
    clearInterval(timer);
  }
}

test('the SDK extractor module is located from the entry\'s own import statement', () => {
  const entry = new URL('sdk-entry.js', FIXTURES).href;
  assert.deepEqual(locateSdkExtractor(entry), located);
  assert.equal(locateSdkExtractor(entry, () => 'export const nothing = 1;'), undefined);
  assert.equal(locateSdkExtractor(entry, () => 'import { t as extractBasicHtmlContent } from "https://evil.example/x.js";'), undefined);
  assert.equal(locateSdkExtractor('data:text/javascript,1'), undefined);
  assert.equal(locateSdkExtractor(undefined), undefined);
});

test('an isolated extraction returns what the same function returns in-process', async () => {
  const extractHtml = createIsolatedHtmlExtractor({extract: fixtureExtract, locate: () => located});
  const html = '<title>Events</title><ul><li><a href="/e/1">Gritty 5K</a> Sep 26, 2026</li></ul>';
  for (const extractMode of ['text', 'markdown']) {
    assert.deepEqual(await extractHtml({html, url: 'https://a.example.org/', extractMode}),
      await fixtureExtract({html, extractMode}));
  }
  assert.match((await extractHtml({html, url: 'https://a.example.org/', extractMode: 'markdown'})).text, /\[Gritty 5K\]\(\/e\/1\)/);
});

test('adversarial markup: the worker is terminated at its deadline and the caller never blocks', async () => {
  const extractHtml = createIsolatedHtmlExtractor({extract: fixtureExtract, locate: () => located, limits: {timeoutMs: 1500}});
  const started = performance.now();
  const {error, gap} = await blockedFor(() => extractHtml({html: UNCLOSED, url: 'https://a.example.org/', extractMode: 'text'}));
  const elapsed = performance.now() - started;
  assert.equal(error?.code, HTML_EXTRACTION_TIMEOUT);
  assert.ok(elapsed < 1500 + 1500, `returned ${Math.round(elapsed)} ms after the start`);
  assert.ok(gap < 400, `the calling thread was blocked for ${Math.round(gap)} ms`);
});

test('an owner abort terminates the extraction at once', async () => {
  const extractHtml = createIsolatedHtmlExtractor({extract: (await import(new URL('slow-extractor.mjs', FIXTURES))).t,
    locate: () => ({moduleUrl: new URL('slow-extractor.mjs', FIXTURES).href, exportName: 't'})});
  const controller = new AbortController();
  const pending = extractHtml({html: '<p>x</p>', url: 'https://a.example.org/', extractMode: 'text', signal: controller.signal});
  await new Promise(resolve => setTimeout(resolve, 100));
  const started = performance.now();
  controller.abort();
  await assert.rejects(pending, error => error.code === HTML_EXTRACTION_ABORTED);
  assert.ok(performance.now() - started < 500);
});

test('a module that is not the imported function is never run; extraction falls back in-process, bounded', async () => {
  const warnings = [];
  let inProcess = 0;
  const other = async ({html}) => { inProcess++; return {text: `fallback ${html.length}`}; };
  const extractHtml = createIsolatedHtmlExtractor({extract: other, locate: () => located, warn: message => warnings.push(message)});
  assert.deepEqual(await extractHtml({html: 'x'.repeat(400_000), url: 'https://a.example.org/'}),
    {text: `fallback ${HTML_EXTRACTION_LIMITS.inProcessMaxChars}`});
  assert.equal(inProcess, 1);
  assert.deepEqual(warnings, ['Pixel HTML extraction runs in-process on a bounded prefix: extractor module mismatch']);
  await extractHtml({html: '<p>again</p>', url: 'https://a.example.org/'});
  assert.equal(inProcess, 2, 'no further worker is started');
  // Without a located module the extraction is in-process from the start.
  let calls = 0;
  const unlocated = createIsolatedHtmlExtractor({extract: async () => { calls++; return null; }, locate: () => undefined});
  assert.equal(await unlocated({html: '<p>x</p>', url: 'https://a.example.org/'}), null);
  assert.equal(calls, 1);
});

test('the in-process path bounds the adversarial input it hands the extractor', async () => {
  assert.equal(boundedHtml('a'.repeat(10), 4), 'aaaa');
  assert.equal(boundedHtml('a😀b', 2), 'a', 'never ends inside a surrogate pair');
  const {result, gap} = await blockedFor(() => fixtureExtract({html: boundedHtml(UNCLOSED), extractMode: 'text'}));
  assert.ok(result.text.length > 1000);
  // Bounded, not free: about 0.7 s on tower2 for the pinned extractor.
  assert.ok(gap < 8000, `in-process bounded extraction blocked for ${Math.round(gap)} ms`);
});

// The shared reader over a stubbed guarded transport.
function reader(options = {}) {
  const calls = [];
  const deps = {
    guardedFetch: async request => ({
      response: new Response(options.body ?? '<title>Specs | Example</title><p>Board power 250 W</p>',
        {status: 200, headers: {'Content-Type': 'text/html; charset=utf-8'}}),
      finalUrl: request.url, release() {},
    }),
    readResponseText: async response => ({text: await response.text(), truncated: false}),
    extractBasicHtmlContent: async params => { calls.push(params); return fixtureExtract(params); },
    ...options.deps,
  };
  return {calls, readPage: createPublicPageReader(deps)};
}

test('the reader returns the page title and passes the extraction mode and signal through', async () => {
  const seen = [];
  const {readPage} = reader({deps: {extractHtml: async params => { seen.push(params); return fixtureExtract(params); }}});
  const controller = new AbortController();
  const page = await readPage('https://docs.example.org/specs', {extractMode: 'markdown', signal: controller.signal});
  assert.equal(page.ok, true);
  assert.equal(page.title, 'Specs | Example');
  assert.match(page.text, /Board power 250 W/);
  assert.equal(seen[0].extractMode, 'markdown');
  assert.equal(seen[0].signal, controller.signal);
  const plain = await readPage('https://docs.example.org/specs');
  assert.equal(plain.ok, true);
  assert.equal(seen[1].extractMode, 'text', 'plain text stays the default for existing callers');
});

test('an extraction past its deadline is a timed-out read; the default reader bounds its input', async () => {
  const {readPage} = reader({deps: {extractHtml: async () => { throw Object.assign(new Error('x'), {code: HTML_EXTRACTION_TIMEOUT}); }}});
  assert.deepEqual(await readPage('https://docs.example.org/specs'), {ok: false, reason: 'timeout'});
  const plain = reader({body: `<title>t</title>${'<p>x</p>'.repeat(60_000)}`});
  assert.equal((await plain.readPage('https://docs.example.org/big')).ok, true);
  assert.ok(plain.calls[0].html.length <= HTML_EXTRACTION_LIMITS.inProcessMaxChars);
});

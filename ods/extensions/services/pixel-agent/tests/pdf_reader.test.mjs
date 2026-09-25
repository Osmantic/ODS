// PDF and other binary bodies in the shared public-page reader, the read
// receipts that list pages read, and web_fetch results carrying such bodies.
//
// Fleet evidence (2026-09-25): OpenClaw web_fetch returned the NVIDIA GeForce
// RTX 5070 user guide PDF with HTTP 200, contentType application/pdf,
// extractor "raw" and its bytes undecoded (tower2 round 061, session b63ee849
// row 276: 2,444 U+FFFD in 15,994 characters; tower1 session f702a671 row
// 327: 2,722). ODS then listed that PDF among the pages read successfully.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {deflateSync} from 'node:zlib';
import {extractPdfText, extractPdfTextSync, glyphUnicode, PDF_TEXT_LIMITS} from '../plugin/pdf-text.mjs';
import {binaryText, pdfExtractionTimeoutMs, webFetchBinaryBody} from '../plugin/document-body.mjs';
import {createPublicPageReader, createPublicWebExtractTool, PUBLIC_PAGE_TEXT_TYPES} from '../plugin/web-extract.mjs';
import {createCompletionAssurance} from '../plugin/completion-assurance.mjs';
import {createHostCitationVerifier} from '../plugin/citation-verification.mjs';
import {binaryFetchNote, binaryFetchReceipt, projectBinaryFetch, projectWebResult} from '../plugin/web-result-projection.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {TOWER1_R060_FINAL_ANSWER} from './fixtures/philly-events-detail-links.mjs';
import {imageOnlyPdf, pdfFile, textPdf} from './fixtures/pdf-fixtures.mjs';

const REPLACEMENT = String.fromCharCode(0xfffd);
const GUIDE = 'https://www.nvidia.com/content/geforce-gtx/geforce-rtx-5070-user-guide-r1.pdf';
const GUIDE_PAGES = [
  ['GeForce RTX 5070 User Guide', 'Total Graphics Power (TGP): 250 W', 'Required System Power: 650 W'],
  ['Memory Configuration: 12 GB GDDR7', 'Memory Interface Width: 192-bit'],
];
const GUIDE_TEXT = `[Page 1]\n${GUIDE_PAGES[0].join('\n')}\n\n[Page 2]\n${GUIDE_PAGES[1].join('\n')}`;
const OWNER = 'Read the official NVIDIA source pages and tell me the RTX 5070 total graphics power.';
const bytesOf = text => new Uint8Array(Buffer.from(text, 'latin1'));
const reasonOf = run => { try { run(); } catch (error) { return error.reason ?? error; } return 'no failure'; };

// A document whose single page takes the parser a while: many drawing
// operators (no text, so the text bound does not end it early) in one
// compressed content stream, then one line of text.
function slowPdf(lines = 200_000) {
  const content = `${'0.5 g 10 10 m 20 20 l 30 30 l h f q 1 0 0 1 0 0 cm Q\n'.repeat(lines)}` +
    'BT /F1 11 Tf 72 720 Td (The one line of text after the drawing) Tj ET';
  return pdfFile([
    {num: 1, body: '<< /Type /Catalog /Pages 2 0 R >>'},
    {num: 2, body: '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'},
    {num: 3, body: '<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>'},
    {num: 4, dict: '/Filter /FlateDecode', stream: deflateSync(Buffer.from(content, 'latin1'))},
    {num: 5, body: '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'},
  ], {root: 1});
}

// The guarded transport behind the real reader. The body is a stream, so the
// test can see how much of it the reader pulled.
function transport({body = '', contentType = 'application/pdf', status = 200, headers = {}, finalUrl} = {}) {
  const calls = [];
  let released = 0, pulled = 0;
  const deps = {
    guardedFetch: async options => {
      calls.push(options);
      const bytes = body instanceof Uint8Array ? body : new TextEncoder().encode(body);
      const size = 64 * 1024;
      let offset = 0;
      const stream = new ReadableStream({pull(controller) {
        if (offset >= bytes.byteLength) { controller.close(); return; }
        const chunk = bytes.slice(offset, offset + size);
        offset += chunk.byteLength;
        pulled += chunk.byteLength;
        controller.enqueue(chunk);
      }});
      return {response: new Response(stream, {status, headers: {'Content-Type': contentType, ...headers}}),
        finalUrl: finalUrl ?? options.url, release: () => { released += 1; }};
    },
    readResponseText: async (response, {maxBytes}) => ({text: await response.text(), truncated: false, maxBytes}),
    extractBasicHtmlContent: async ({html}) => ({text: html.replace(/<[^>]+>/g, '\n')}),
  };
  return {calls, deps, released: () => released, pulled: () => pulled,
    readPage: createPublicPageReader(deps), tool: createPublicWebExtractTool(deps)};
}

// An OpenClaw web_fetch receipt as the fleet recorded it: the body wrapped in
// untrusted-content markers after the security notice, undecoded.
function fetchReceipt({url = GUIDE, contentType = 'application/pdf', body} = {}) {
  body ??= `%PDF-1.7\r%${REPLACEMENT.repeat(4)}\r\n942 0 obj\r<</Linearized 1/L 5381674/O 94${REPLACEMENT.repeat(2400)}`;
  const text = 'SECURITY NOTICE: The following content is from an EXTERNAL, UNTRUSTED source (e.g., email, webhook).\n' +
    `<<<EXTERNAL_UNTRUSTED_CONTENT id="f1">>>\nSource: Web Fetch\n---\n${body}\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="f1">>>`;
  const details = {url, finalUrl: url, status: 200, contentType, extractMode: 'markdown', extractor: 'raw',
    externalContent: {untrusted: true, source: 'web_fetch', wrapped: true}, truncated: true, rawLength: body.length, text};
  return {content: [{type: 'text', text: JSON.stringify(details, null, 2)}], details};
}

// ---------------------------------------------------------------------------
// Extraction

test('generated PDFs in each producer layout yield their known text in page order', () => {
  for (const options of [{compress: false}, {}, {font: 'type0'}, {font: 'kerned'}, {objectStreams: true},
    {font: 'type0', objectStreams: true}]) {
    assert.deepEqual(extractPdfTextSync(textPdf(GUIDE_PAGES, options)),
      {text: GUIDE_TEXT, pagesRead: 2, pageCount: 2, truncated: false}, JSON.stringify(options));
  }
  assert.equal(glyphUnicode('eacute'), 'e'.concat(String.fromCharCode(0x301)).normalize('NFC'));
  assert.equal(glyphUnicode('uni00410042'), 'AB');
  assert.equal(glyphUnicode('f_f_i'), 'ffi');
  assert.equal(glyphUnicode('g34'), undefined);
});

test('only the first pages are read, and the page count says how many there are', () => {
  const result = extractPdfTextSync(textPdf(Array.from({length: 14}, (_, i) => [`Section ${i + 1} describes the card`])));
  assert.equal(result.pagesRead, PDF_TEXT_LIMITS.maxPages);
  assert.equal(result.pageCount, 14);
  assert.equal(result.truncated, true);
  assert.match(result.text, /\[Page 10\]\nSection 10 describes the card$/);
  assert.doesNotMatch(result.text, /Section 11/);
});

test('encrypted, scanned, unmapped and malformed PDFs are not read, each with its reason', () => {
  assert.equal(reasonOf(() => extractPdfTextSync(textPdf(GUIDE_PAGES, {encrypted: true}))), 'encrypted');
  assert.equal(reasonOf(() => extractPdfTextSync(imageOnlyPdf())), 'no-text');
  // A subset font whose ToUnicode map is gone: its glyph codes are not guessed.
  const unmapped = Buffer.from(textPdf(GUIDE_PAGES, {font: 'type0'})).toString('latin1').replace('/ToUnicode 6 0 R', '/ToUnicodX 6 0 R');
  assert.equal(reasonOf(() => extractPdfTextSync(bytesOf(unmapped))), 'no-text');
  assert.equal(reasonOf(() => extractPdfTextSync(bytesOf(`%PDF-1.7\n${'\x07garbage\x00'.repeat(4000)}`))), 'unsupported');
  assert.equal(reasonOf(() => extractPdfTextSync(bytesOf('<!doctype html><title>Not a PDF</title>'))), 'unsupported');
});

test('decompression, text, size and time bounds hold', () => {
  // A content stream that decompresses past the per-stream bound.
  assert.equal(reasonOf(() => extractPdfTextSync(slowPdf(20_000), {limits: {maxStreamBytes: 64 * 1024}})), 'too-large');
  const short = extractPdfTextSync(textPdf(GUIDE_PAGES), {limits: {maxTextChars: 48}});
  assert.equal(short.truncated, true);
  assert.ok(short.text.length <= 48);
  assert.ok(short.text.startsWith('[Page 1]\nGeForce RTX 5070 User Guide'));
  assert.equal(reasonOf(() => extractPdfTextSync(new Uint8Array(PDF_TEXT_LIMITS.maxBytes + 1))), 'too-large');
  assert.equal(reasonOf(() => extractPdfTextSync(slowPdf(), {budgetMs: 1})), 'timeout');
});

class StubWorker extends EventEmitter {
  constructor(url, options) {
    super();
    StubWorker.last = this;
    this.url = url;
    this.options = options;
    this.terminated = 0;
  }
  terminate() { this.terminated += 1; return Promise.resolve(1); }
  unref() {}
}

test('extraction runs in a worker thread and is terminated at its deadline or on abort', async () => {
  const real = await extractPdfText(textPdf(GUIDE_PAGES, {font: 'type0'}));
  assert.deepEqual(real, {ok: true, text: GUIDE_TEXT, pagesRead: 2, pageCount: 2, truncated: false});

  let fire, delay;
  const pending = extractPdfText(textPdf(GUIDE_PAGES), {WorkerImpl: StubWorker, timeoutMs: 700,
    setTimer: (callback, ms) => { fire = callback; delay = ms; return 1; }, clearTimer: () => {}});
  const {options} = StubWorker.last;
  assert.equal(delay, 700);
  assert.equal(options.workerData.budgetMs, 450, 'the worker stops itself before it is terminated');
  assert.deepEqual(options.transferList, [options.workerData.bytes], 'a private copy is transferred');
  assert.equal(options.resourceLimits.maxOldGenerationSizeMb, PDF_TEXT_LIMITS.workerHeapMb);
  assert.deepEqual(options.env, {}, 'no environment reaches the worker');
  fire();
  assert.deepEqual(await pending, {ok: false, reason: 'timeout'});
  assert.equal(StubWorker.last.terminated, 1);

  const controller = new AbortController();
  const aborted = extractPdfText(textPdf(GUIDE_PAGES), {WorkerImpl: StubWorker, signal: controller.signal});
  controller.abort();
  assert.deepEqual(await aborted, {ok: false, reason: 'aborted'});
  assert.equal(StubWorker.last.terminated, 1);
  const crashed = extractPdfText(textPdf(GUIDE_PAGES), {WorkerImpl: StubWorker});
  StubWorker.last.emit('error', Object.assign(new Error('heap'), {code: 'ERR_WORKER_OUT_OF_MEMORY'}));
  assert.deepEqual(await crashed, {ok: false, reason: 'too-large'});
  const forged = extractPdfText(textPdf(GUIDE_PAGES), {WorkerImpl: StubWorker});
  StubWorker.last.emit('message', {type: 'failed', reason: 'anything else'});
  assert.deepEqual(await forged, {ok: false, reason: 'unsupported'});
});

test('a long extraction leaves the event loop free and a real worker is stopped at the deadline', async () => {
  let ticks = 0;
  const interval = setInterval(() => { ticks += 1; }, 5);
  const started = performance.now();
  const stopped = await extractPdfText(slowPdf(), {timeoutMs: 150});
  const elapsed = performance.now() - started;
  clearInterval(interval);
  assert.deepEqual(stopped, {ok: false, reason: 'timeout'});
  assert.ok(elapsed < 1500, `stopped after ${Math.round(elapsed)} ms`);
  assert.ok(ticks >= 5, `the gateway thread kept running timers (${ticks} ticks in ${Math.round(elapsed)} ms)`);
});

// ---------------------------------------------------------------------------
// The shared reader and pixel_ods_web_extract

test('pixel_ods_web_extract reads a PDF page as text, cites it and leaves a read receipt', async () => {
  const pdf = textPdf(GUIDE_PAGES, {font: 'type0', objectStreams: true});
  const h = transport({body: pdf, headers: {'Content-Length': String(pdf.byteLength)}});
  const params = {url: GUIDE, query: 'Total Graphics Power'};
  const result = await h.tool.execute('pdf', params);
  assert.equal(result.isError, undefined);
  assert.equal(result.details.matched, true);
  assert.equal(result.details.source_url, GUIDE);
  assert.deepEqual(result.details.document, {kind: 'pdf', read: true, bytes: pdf.byteLength, pages_read: 2,
    page_count: 2, text_truncated: false});
  const [line, notice, open] = result.content[0].text.split('\n');
  assert.equal(line, `Targeted evidence from ${GUIDE} (PDF text, pages 1-2 of 2)`);
  assert.match(notice, /untrusted webpage evidence/);
  assert.match(open, /^<<<EXTERNAL_UNTRUSTED_CONTENT id="[0-9a-f]{24}">>>$/);
  assert.ok(result.content[0].text.includes('Total Graphics Power (TGP): 250 W'));
  assert.equal(h.released(), 1);
  const overview = await h.tool.execute('overview', {url: GUIDE});
  assert.equal(overview.details.mode, 'overview');
  assert.ok(overview.content[0].text.includes('Memory Interface Width: 192-bit'));

  const assurance = createCompletionAssurance();
  assurance.begin(OWNER);
  assurance.observe('pixel_ods_web_extract', {params, result});
  assert.deepEqual(assurance.readPages, [{url: GUIDE}]);
  assert.equal(assurance.finalize(`The RTX 5070 has a total graphics power of 250 W (${GUIDE}).`), undefined);
});

test('a PDF served as application/octet-stream is recognized by its signature', async () => {
  const page = await transport({body: textPdf(GUIDE_PAGES), contentType: 'application/octet-stream'}).readPage(GUIDE);
  assert.equal(page.ok, true);
  assert.equal(page.kind, 'pdf');
  assert.equal(page.text, GUIDE_TEXT);
  const other = transport({body: `PK\x03\x04${'\x00'.repeat(3000)}`, contentType: 'application/octet-stream'});
  assert.deepEqual(await other.readPage(GUIDE), {ok: false, reason: 'content-type', contentType: 'application/octet-stream',
    kind: 'binary', status: 200, finalUrl: GUIDE, requests: 1});
  assert.ok(other.pulled() <= 64 * 1024, 'reading stopped after the signature check');
});

test('an oversized PDF is a bounded not-read receipt, whether or not its size is declared', async () => {
  const declared = PDF_TEXT_LIMITS.maxBytes + 1024 * 1024;
  const big = new Uint8Array(PDF_TEXT_LIMITS.maxBytes + 256 * 1024);
  big.set(Buffer.from('%PDF-1.7\n', 'latin1'));
  const h = transport({body: big, headers: {'Content-Length': String(declared)}});
  const result = await h.tool.execute('big', {url: GUIDE, query: 'Total Graphics Power'});
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /is a PDF document \(application\/pdf, 9437184 bytes\), but its text was not read: it is larger than the PDF size bound/);
  assert.deepEqual(result.details.document, {kind: 'pdf', read: false, bytes: declared, not_read: 'too-large'});
  assert.ok(h.pulled() <= 128 * 1024, `only the signature was read (${h.pulled()} bytes)`);
  const undeclared = transport({body: big});
  assert.deepEqual(await undeclared.readPage(GUIDE), {ok: false, reason: 'pdf-text', contentType: 'application/pdf',
    kind: 'pdf', notRead: 'too-large', status: 200, finalUrl: GUIDE, requests: 1});
  assert.ok(undeclared.pulled() <= PDF_TEXT_LIMITS.maxBytes + 128 * 1024, 'reading stopped at the size bound');
});

test('garbage, encrypted and scanned PDFs are bounded not-read receipts that never count as reads', async () => {
  for (const [body, notRead] of [
    [bytesOf(`%PDF-1.7\n${'\x07garbage\x00'.repeat(4000)}`), 'unsupported'],
    [textPdf(GUIDE_PAGES, {encrypted: true}), 'encrypted'],
    [imageOnlyPdf(), 'no-text'],
  ]) {
    const h = transport({body});
    const page = await h.readPage(GUIDE);
    assert.deepEqual(page, {ok: false, reason: 'pdf-text', contentType: 'application/pdf', kind: 'pdf', bytes: body.byteLength,
      notRead, status: 200, finalUrl: GUIDE, requests: 1});
    const result = await h.tool.execute('bad', {url: GUIDE});
    assert.equal(result.isError, true);
    assert.equal(result.details.matched, false);
    assert.deepEqual(result.details.document, {kind: 'pdf', read: false, bytes: body.byteLength, not_read: notRead});
    assert.match(result.content[0].text, /No evidence was extracted; do not cite it as read\.$/);
    const assurance = createCompletionAssurance();
    assurance.begin(OWNER);
    assurance.observe('pixel_ods_web_extract', {params: {url: GUIDE}, result});
    assert.deepEqual(assurance.readPages, []);
  }
});

test('an image body is not read, and its receipt says what it was', async () => {
  const h = transport({body: new Uint8Array(200_000), contentType: 'image/png', headers: {'Content-Length': '200000'}});
  assert.deepEqual(await h.readPage(GUIDE), {ok: false, reason: 'content-type', contentType: 'image/png', kind: 'binary',
    bytes: 200_000, status: 200, finalUrl: GUIDE, requests: 1});
  assert.ok(h.pulled() <= 64 * 1024, 'the image body was not read');
  const result = await h.tool.execute('image', {url: GUIDE});
  assert.equal(result.isError, true);
  assert.equal(result.content[0].text,
    'The public page is not a supported text document (a binary document, image/png, 200000 bytes); it was not read and is not evidence.');
  assert.deepEqual(result.details.document, {kind: 'binary', read: false, bytes: 200_000, not_read: 'not-a-text-document'});
});

test('a text-labelled body that is really binary is not a page', async () => {
  const pdf = await transport({body: Buffer.from(textPdf(GUIDE_PAGES)).toString('latin1'), contentType: 'text/plain'}).readPage(GUIDE);
  assert.equal(pdf.reason, 'content-type');
  assert.equal(pdf.kind, 'pdf');
  const nul = await transport({body: `<html>${'\x00\x01'.repeat(500)}</html>`, contentType: 'text/html'}).readPage(GUIDE);
  assert.equal(nul.reason, 'content-type');
  assert.equal(nul.kind, 'binary');
  const text = await transport({body: 'Plain release notes: driver 580.1 adds support.', contentType: 'text/plain'}).readPage(GUIDE);
  assert.equal(text.ok, true);
});

test('extraction fits each caller budget, and an abort ends it at once', async () => {
  assert.equal(pdfExtractionTimeoutMs(4), 2_000, 'host citation check: 4 s for its reads');
  assert.equal(pdfExtractionTimeoutMs(12), PDF_TEXT_LIMITS.timeoutMs, 'search_read: 12 s per read inside 15 s per call');
  assert.equal(pdfExtractionTimeoutMs(20), PDF_TEXT_LIMITS.timeoutMs, 'pixel_ods_web_extract');
  assert.equal(pdfExtractionTimeoutMs(1), 1_000);
  const h = transport({body: slowPdf()});
  const controller = new AbortController();
  const started = performance.now();
  setTimeout(() => controller.abort(), 60);
  const page = await h.readPage(GUIDE, {signal: controller.signal});
  assert.deepEqual(page, {ok: false, reason: 'blocked'});
  assert.ok(performance.now() - started < 1_500);
  assert.equal(h.released(), 1);
});

test('the host citation check verifies a cited PDF from its text, through the same reader', async () => {
  const gritty = 'https://www.xfinitymobilearena.com/events/detail/gritty-5k-presented-by-penn-medicine';
  const lines = ['Gritty 5K Presented by Penn Medicine | Xfinity Mobile Arena', 'Gritty 5K Presented by Penn Medicine',
    'Date', 'Sep 26, 2026', 'Event Starts', '7:30 AM'];
  assert.ok(PUBLIC_PAGE_TEXT_TYPES.has('application/pdf'));
  const verify = async body => {
    const {readPage} = transport({body});
    return createHostCitationVerifier({readPage}).verify({answer: TOWER1_R060_FINAL_ANSWER, urls: [gritty]});
  };
  assert.deepEqual((await verify(textPdf([lines], {font: 'type0'}))).verified.map(entry => entry.url), [gritty]);
  const other = await verify(textPdf([['Xfinity Mobile Arena', 'Event calendar and seating chart']]));
  assert.equal(other.results[0].reason, 'anchors-not-found');
  const encrypted = await verify(textPdf([lines], {encrypted: true}));
  assert.equal(encrypted.results[0].reason, 'pdf-text');
});

// ---------------------------------------------------------------------------
// web_fetch receipts

test('a web_fetch PDF or image body is not a page read, and cited alone needs a real read', () => {
  assert.deepEqual(webFetchBinaryBody(fetchReceipt().details), {kind: 'pdf', contentType: 'application/pdf', chars: 2456});
  const png = fetchReceipt({contentType: 'image/png', body: `${REPLACEMENT}PNG\r\n\x1a\n\x00\x00\x00\rIHDR${REPLACEMENT.repeat(300)}`});
  assert.equal(webFetchBinaryBody(png.details).kind, 'binary');
  // Bytes decide when the type says nothing, or says text.
  assert.equal(webFetchBinaryBody(fetchReceipt({contentType: 'application/octet-stream'}).details).kind, 'pdf');
  assert.equal(webFetchBinaryBody(fetchReceipt({contentType: 'text/html'}).details).kind, 'pdf');
  const readme = fetchReceipt({contentType: 'application/octet-stream', body: 'Release notes: driver 580.1 adds RTX 5070 support.'});
  assert.equal(webFetchBinaryBody(readme.details), undefined);
  assert.equal(webFetchBinaryBody(fetchReceipt({contentType: 'image/svg+xml', body: '<svg><text>5070</text></svg>'}).details), undefined);
  // A page decoded with the wrong charset keeps a few replacement characters.
  assert.equal(binaryText(`Programa${REPLACEMENT}${REPLACEMENT}o de eventos em Filad${REPLACEMENT}lfia `.repeat(40)), undefined);

  for (const receipt of [fetchReceipt(), png]) {
    const assurance = createCompletionAssurance();
    assurance.begin(OWNER);
    assurance.observe('web_fetch', {result: receipt});
    assert.deepEqual(assurance.readPages, []);
    const decision = assurance.finalize(`The RTX 5070 has a total graphics power of 250 W (${GUIDE}).`);
    assert.equal(decision.action, 'revise');
    assert.ok(decision.retry.instruction.includes(JSON.stringify([GUIDE])));
  }
  const assurance = createCompletionAssurance();
  assurance.begin(OWNER);
  assurance.observe('web_fetch', {result: readme});
  assert.deepEqual(assurance.readPages, [{url: GUIDE}], 'readable text served as octet-stream is still a read');
});

test('a web_fetch PDF result is replaced by a receipt that points to the ODS reader', () => {
  const receipt = binaryFetchReceipt(fetchReceipt());
  assert.deepEqual(receipt, {kind: 'pdf', contentType: 'application/pdf', chars: 2456, url: GUIDE});
  const note = binaryFetchNote(receipt);
  assert.match(note, /^ODS read receipt \(not source evidence\): web_fetch returned a PDF document \(application\/pdf, 2456 characters of undecoded bytes\)\./);
  assert.match(note, /the page was not read and is not evidence/);
  assert.ok(note.includes(`pixel_ods_web_extract and call it with {"url":${JSON.stringify(GUIDE)}}`));
  assert.match(note, /first 10 pages of a PDF up to 8 MB/);
  assert.match(binaryFetchNote(binaryFetchReceipt(fetchReceipt({contentType: 'image/png', body: '\x00PNG'}))),
    /binary document \(image\/png, 4 characters of undecoded bytes\)\..*Use another source for text evidence/);
  assert.equal(binaryFetchReceipt({...fetchReceipt(), isError: true}), undefined);
  assert.equal(binaryFetchReceipt(fetchReceipt({contentType: 'text/html', body: '<p>RTX 5070</p>'})), undefined);

  // Deferred (tool_call) web_fetch: no copy of the bytes reaches the model.
  const tool = {id: 'openclaw:core:web_fetch', source: 'openclaw', sourceName: 'core', name: 'web_fetch'};
  const envelope = {tool, result: fetchReceipt()};
  const projected = projectWebResult({role: 'toolResult', toolName: 'tool_call', content: [{type: 'text', text: 'wrapper'}]}, envelope);
  assert.deepEqual(projected.content.map(block => block.text), [
    JSON.stringify({tool: {id: tool.id, source: 'openclaw', sourceName: 'core', name: 'web_fetch'}, result: {binaryBody: true}}), note]);
  assert.equal(projected.details, envelope);
  assert.equal(projectBinaryFetch({role: 'toolResult', toolName: 'web_fetch', isError: true, content: []}, receipt), undefined);
  assert.equal(projectBinaryFetch({role: 'toolResult', toolName: 'web_fetch', content: []}, undefined), undefined);
});

test('a native web_fetch PDF result bound to its call is persisted as the receipt', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'pdf-run', sessionId: 'pdf-session', toolCallId: 'pdf-call'};
  guard.observeRun(context, 'pixel', {prompt: OWNER});
  const params = {url: GUIDE, maxChars: 15000};
  const event = {toolName: 'web_fetch', toolCallId: context.toolCallId, runId: context.runId, params};
  const ctx = {...context, toolName: 'web_fetch'};
  assert.notEqual(guard.beforeToolCall(event, ctx)?.block, true);
  const result = fetchReceipt();
  guard.afterToolCall({...event, result}, ctx);
  const message = {role: 'toolResult', toolName: 'web_fetch', toolCallId: context.toolCallId, content: result.content,
    details: {persistedDetailsTruncated: true, status: 200}};
  const persisted = guard.toolResultPersist({message}, ctx)?.message;
  assert.equal(persisted.content.length, 1);
  assert.equal(persisted.content[0].text, binaryFetchNote(binaryFetchReceipt(result)));
  assert.ok(!persisted.content[0].text.includes('%PDF') && !persisted.content[0].text.includes(REPLACEMENT));
  assert.deepEqual(persisted.details, message.details);
});

test('a binary file from the requested GitHub repository is not a repository source read', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
  guard.observeRun(context, 'pixel', {prompt: 'Research the official Osmantic/ODS GitHub repository.'});
  const fetch = (id, url, result) => {
    const event = {toolName: 'web_fetch', toolCallId: id, runId: 'run-1', params: {url}};
    const ctx = {...context, toolName: 'web_fetch', toolCallId: id};
    assert.notEqual(guard.beforeToolCall(event, ctx, 'pixel')?.block, true);
    guard.afterToolCall({...event, result}, ctx, 'pixel');
  };
  const guide = 'https://raw.githubusercontent.com/Osmantic/ODS/HEAD/docs/guide.pdf';
  fetch('pdf', guide, fetchReceipt({url: guide}));
  assert.equal(guard.verificationForRun('run-1').status, 'failed', 'the PDF bytes are not a source read');
  const readme = 'https://raw.githubusercontent.com/Osmantic/ODS/HEAD/README.md';
  fetch('readme', readme, fetchReceipt({url: readme, contentType: 'text/plain', body: '# ODS\nLocal AI stack.'}));
  assert.equal(guard.verificationForRun('run-1').status, 'none');
});

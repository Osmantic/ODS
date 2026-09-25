// Response bodies that are not page text: what the public-page reader
// (web-extract.mjs) does with them, and how read receipts recognize them
// (completion-assurance.mjs, web-result-projection.mjs).
//
// OpenClaw's web_fetch decodes any 2xx body as text, so a PDF or an image
// comes back as undecoded bytes with extractor "raw". On 2026-09-25 the NVIDIA
// GeForce RTX 5070 user guide (a 5.4 MB PDF) came back that way on tower2
// (round 061, session b63ee849) and on tower1 (session f702a671): 15,994
// characters with 2,444 and 2,722 U+FFFD, and ODS listed the PDF among the
// pages read successfully. A binary body is never a page read.
//
// The shared reader instead extracts a PDF's text within fixed bounds
// (pdf-text.mjs) or returns a receipt that says what was not read and why.

import {extractPdfText, hasPdfMagic, PDF_TEXT_LIMITS} from './pdf-text.mjs';

export const PDF_TYPES = new Set(['application/pdf', 'application/x-pdf', 'application/acrobat', 'applications/vnd.pdf']);
// Types that say nothing about the body. Their bytes decide: a PDF signature
// makes the body a PDF, anything else stays "not a text document".
const UNDECLARED_TYPES = new Set(['', 'application/octet-stream', 'binary/octet-stream', 'application/unknown',
  'application/download', 'application/x-download', 'application/force-download']);
// RTF and PostScript are text formats, so they are not listed here.
const BINARY_TYPES = new Set(['application/zip', 'application/gzip', 'application/x-gzip', 'application/x-tar',
  'application/x-7z-compressed', 'application/x-rar-compressed', 'application/vnd.rar', 'application/x-bzip2',
  'application/x-xz', 'application/zstd', 'application/wasm', 'application/msword',
  'application/java-archive', 'application/x-msdownload', 'application/x-executable', 'application/x-sharedlib',
  'application/x-mach-binary', 'application/vnd.android.package-archive', 'application/epub+zip',
  'application/x-shockwave-flash']);
const BINARY_PREFIXES = ['image/', 'audio/', 'video/', 'font/', 'model/', 'application/vnd.ms-',
  'application/vnd.openxmlformats-', 'application/vnd.oasis.opendocument.', 'application/x-font'];

export const mediaType = value => String(value ?? '').split(';', 1)[0].trim().toLowerCase();

// A declared type whose body is not text (SVG is XML text).
export function binaryMediaType(type) {
  if (PDF_TYPES.has(type) || BINARY_TYPES.has(type)) return true;
  return type !== 'image/svg+xml' && BINARY_PREFIXES.some(prefix => type.startsWith(prefix));
}

// A decoded body that is really binary: a PDF signature at its start, a NUL,
// or mostly replacement and control characters. "pdf", "binary" or undefined.
export function binaryText(text) {
  if (typeof text !== 'string' || !text) return undefined;
  const head = text.slice(0, 8192);
  if (/^\ufeff?[\0\t\n\f\r ]{0,64}%PDF-\d/.test(head)) return 'pdf';
  if (head.includes('\0')) return 'binary';
  const odd = head.match(/[\ufffd\u0001-\u0008\u000e-\u001f]/g)?.length ?? 0;
  return odd >= 32 && odd / head.length > 0.2 ? 'binary' : undefined;
}

// web_fetch wraps the body in untrusted-content markers after a warning.
function fetchedBody(text) {
  const marker = text.indexOf('<<<EXTERNAL_UNTRUSTED_CONTENT');
  const start = marker < 0 ? -1 : text.indexOf('\n---\n', marker);
  return start < 0 ? text : text.slice(start + 5);
}

// A 2xx OpenClaw web_fetch receipt whose body is a PDF or other binary data:
// {kind: "pdf" | "binary", contentType, chars?}, or undefined for text.
export function webFetchBinaryBody(details) {
  if (!details || typeof details !== 'object' || Array.isArray(details)) return undefined;
  const type = mediaType(details.contentType);
  const sniffed = typeof details.text === 'string' ? binaryText(fetchedBody(details.text)) : undefined;
  const kind = PDF_TYPES.has(type) || sniffed === 'pdf' ? 'pdf' : binaryMediaType(type) || sniffed ? 'binary' : undefined;
  if (!kind) return undefined;
  return {kind, contentType: type || 'unknown',
    ...(Number.isSafeInteger(details.rawLength) && details.rawLength >= 0 ? {chars: details.rawLength} : {})};
}

function declaredLength(headers) {
  let value;
  try { value = headers?.get?.('content-length'); } catch { value = undefined; }
  const length = Number(value);
  return value !== null && value !== undefined && value !== '' && Number.isSafeInteger(length) && length >= 0
    ? length : undefined;
}

// At most `maxBytes` of the body. `accept(prefix)` decides from the first
// 1 KiB whether reading continues. {bytes, size, complete, truncated,
// accepted}; `size` is the body's size only when `complete`.
async function readBodyBytes(response, {maxBytes, signal, accept}) {
  const reader = response.body?.getReader?.();
  const chunks = [];
  let size = 0, truncated = false, complete = false, accepted;
  const prefix = () => Buffer.concat(chunks.map(chunk => Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength)), size);
  if (!reader) {
    const all = new Uint8Array(typeof response.arrayBuffer === 'function' ? await response.arrayBuffer() : new ArrayBuffer(0));
    truncated = all.byteLength > maxBytes;
    const bytes = all.subarray(0, maxBytes);
    return {bytes, size: all.byteLength, complete: true, truncated, accepted: accept(bytes.subarray(0, 1024))};
  }
  try {
    for (;;) {
      if (signal?.aborted) throw signal.reason ?? new Error('aborted');
      const {done, value} = await reader.read();
      if (done) { complete = true; break; }
      if (!value?.byteLength) continue;
      const room = maxBytes + 1 - size;
      const chunk = value.byteLength > room ? value.subarray(0, room) : value;
      chunks.push(chunk);
      size += chunk.byteLength;
      if (accepted === undefined && size >= 1024) {
        accepted = accept(prefix().subarray(0, 1024));
        if (!accepted) break;
      }
      if (size > maxBytes) { truncated = true; break; }
    }
  } finally {
    reader.cancel?.().catch?.(() => {});
    try { reader.releaseLock?.(); } catch { /* Already released. */ }
  }
  const bytes = prefix();
  accepted ??= accept(bytes.subarray(0, 1024));
  return {bytes: new Uint8Array(bytes.buffer, bytes.byteOffset, Math.min(bytes.byteLength, maxBytes)), size, complete,
    truncated, accepted};
}

// Extraction must finish well inside the caller's read budget: at most
// PDF_TEXT_LIMITS.timeoutMs and at most half of the read's timeout (2 s of
// the host check's 4 s, 5 s of pixel_ods_search_read's 12 s per read).
export const pdfExtractionTimeoutMs = timeoutSeconds =>
  Math.min(PDF_TEXT_LIMITS.timeoutMs, Math.max(1_000, Math.floor(numberOr(timeoutSeconds, 20) * 500)));
function numberOr(value, fallback) { return typeof value === 'number' && Number.isFinite(value) ? value : fallback; }

// For the public-page reader, after the status and challenge checks: a
// result for a body that is not page text, or undefined for a text body the
// reader handles as before. A PDF becomes {ok: true, kind: "pdf", text, ...}
// when the caller accepts "application/pdf" and its text is extracted within
// the bounds; otherwise, and for every other binary body, the result says it
// was not read: reason "content-type" (not a text document) or "pdf-text"
// (a PDF whose text could not be read, with `notRead` saying why).
export async function readNonTextBody(response, {contentType, types, signal, timeoutSeconds, extract = extractPdfText}) {
  const declared = declaredLength(response.headers);
  const size = declared === undefined ? {} : {bytes: declared};
  const pdfDeclared = PDF_TYPES.has(contentType);
  if (!pdfDeclared && !UNDECLARED_TYPES.has(contentType)) {
    if (types.has(contentType) || !binaryMediaType(contentType)) return undefined;
    return {ok: false, reason: 'content-type', contentType, kind: 'binary', ...size};
  }
  if (!types.has('application/pdf')) {
    return {ok: false, reason: 'content-type', contentType: contentType || 'unknown', ...(pdfDeclared ? {kind: 'pdf'} : {}), ...size};
  }
  const tooLarge = declared !== undefined && declared > PDF_TEXT_LIMITS.maxBytes;
  const body = await readBodyBytes(response, {maxBytes: PDF_TEXT_LIMITS.maxBytes, signal,
    accept: prefix => hasPdfMagic(prefix) && !tooLarge});
  const type = contentType || 'unknown';
  const bytes = body.complete ? body.size : declared;
  if (!hasPdfMagic(body.bytes.subarray(0, 1024))) {
    // Not a PDF, and its type names no text document: not read. Only bytes
    // that look binary are called binary (an error page labelled as a PDF is
    // not).
    const prefix = Buffer.from(body.bytes.buffer, body.bytes.byteOffset, Math.min(body.bytes.byteLength, 1024)).toString('latin1');
    return {ok: false, reason: 'content-type', contentType: type, ...(binaryText(prefix) ? {kind: 'binary'} : {}),
      ...(bytes === undefined ? {} : {bytes})};
  }
  if (tooLarge || body.truncated) {
    return {ok: false, reason: 'pdf-text', contentType: type, kind: 'pdf', notRead: 'too-large',
      ...(bytes === undefined ? {} : {bytes})};
  }
  const pdf = await extract(body.bytes, {signal, timeoutMs: pdfExtractionTimeoutMs(timeoutSeconds)});
  // An abort by the caller is not a property of the document.
  if (pdf?.reason === 'aborted') throw signal?.reason ?? new Error('aborted');
  if (!pdf?.ok) return {ok: false, reason: 'pdf-text', contentType: type, kind: 'pdf', bytes: body.size, notRead: pdf?.reason ?? 'unsupported'};
  return {ok: true, contentType: 'application/pdf', kind: 'pdf', text: pdf.text, truncated: pdf.truncated,
    bytes: body.size, pages: {read: pdf.pagesRead, total: pdf.pageCount}};
}

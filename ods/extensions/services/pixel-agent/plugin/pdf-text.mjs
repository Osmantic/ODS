// Bounded text extraction from a PDF body read by the public-page reader
// (web-extract.mjs, through document-body.mjs).
//
// OpenClaw's web_fetch returns any 2xx body as decoded text, so a PDF reaches
// the model as undecoded bytes. This module reads a PDF's text itself, with
// no dependency: plain JavaScript over node:zlib, so the gateway thread never
// parses a document.
//
// Each extraction runs in its own short-lived Node process
// (pdf-text-worker.mjs), started with an empty environment and a V8 heap
// limit, and killed (SIGKILL) at its deadline or when the caller aborts. A
// document that exhausts that heap ends only that process, and is reported as
// too large. At most PDF_EXTRACTION_CONCURRENCY such processes run at once.
// The process is not a sandbox: it has the gateway's file and network access,
// and the extractor simply never uses them.
//
// Bounds inside the process: at most PDF_TEXT_LIMITS.maxBytes of body, the
// first maxPages pages, maxTextChars of text, maxDecodedBytes of decoded
// streams (maxStreamBytes for one stream, checked before a decoder grows its
// output), maxObjects objects and maxTableEntries font table entries
// (ToUnicode mappings and glyph widths, all fonts together), and a
// wall-clock deadline. Nothing in the document is executed: no font
// programs, scripts, links or embedded files.
//
// Read: classic and compressed object layouts (object streams), FlateDecode,
// ASCII85 and ASCIIHex content streams, simple fonts (standard, WinAnsi and
// MacRoman encodings with Differences) and composite fonts through their
// ToUnicode maps, and form XObjects. Reported as not read: encrypted files
// (including ones with only an owner password, which some readers open),
// pages whose text is drawn as images or outlines (scans), composite fonts
// without a ToUnicode map, and anything that does not fit the bounds.

import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {constants as zlib, inflateRawSync, inflateSync} from 'node:zlib';

export const PDF_TEXT_LIMITS = Object.freeze({
  maxBytes: 8 * 1024 * 1024,           // response body
  maxPages: 10,                        // the first pages, in page-tree order
  timeoutMs: 5_000,                    // one extraction: waiting, process start and parsing
  maxTextChars: 300_000,               // extracted text
  maxStreamBytes: 16 * 1024 * 1024,    // one decoded stream
  maxDecodedBytes: 48 * 1024 * 1024,   // all decoded streams together
  maxObjects: 250_000,
  maxTableEntries: 500_000,            // ToUnicode mappings and glyph widths, all fonts together
  heapMb: 192,                         // V8 heap of the extraction process
});
// Extraction processes running at once in one gateway; the others wait for a
// free one within their own deadline.
export const PDF_EXTRACTION_CONCURRENCY = 2;

// Why a PDF was not read. Any other outcome is reported as unsupported.
export const PDF_NOT_READ_REASONS = Object.freeze(['too-large', 'timeout', 'encrypted', 'no-text', 'unsupported',
  'unavailable']);
const REASONS = new Set(PDF_NOT_READ_REASONS);

class Failure extends Error {
  constructor(reason) {
    super(`PDF text not read: ${reason}`);
    this.reason = reason;
  }
}
// The text bound was reached: keep what was extracted.
const FULL = Symbol('pdf-text-full');

class PdfString { constructor(bytes) { this.bytes = bytes; } }
class Ref { constructor(num) { this.num = num; } }
class Stream { constructor(dict, start, end) { this.dict = dict; this.start = start; this.end = end; } }
class Keyword { constructor(word) { this.word = word; } }

// A PDF signature within the first 1024 bytes, as readers accept it.
export function hasPdfMagic(bytes) {
  if (!(bytes instanceof Uint8Array)) return false;
  const head = Buffer.from(bytes.buffer, bytes.byteOffset, Math.min(bytes.byteLength, 1024)).toString('latin1');
  return /%PDF-\d/.test(head);
}

// ---------------------------------------------------------------------------
// Lexical level (PDF 32000-1 §7.2-7.3). The file is handled as a latin1
// string, so character offsets are byte offsets.

const WHITE = new Uint8Array(256);
for (const code of [0, 9, 10, 12, 13, 32]) WHITE[code] = 1;
const DELIMITER = new Uint8Array(256);
for (const char of '()<>[]{}/%') DELIMITER[char.charCodeAt(0)] = 1;
const regular = code => code < 256 && !WHITE[code] && !DELIMITER[code];
const NUMBER = /[+-]?(?:\d+(?:\.\d*)?|\.\d+)/y;
const INTEGER = /^\d+$/;
const MAX_DEPTH = 48;

function skipSpace(s, p) {
  const n = s.length;
  while (p < n) {
    const code = s.charCodeAt(p);
    if (WHITE[code]) p++;
    else if (code === 37) {
      while (p < n && s.charCodeAt(p) !== 10 && s.charCodeAt(p) !== 13) p++;
    } else break;
  }
  return p;
}

function parseName(s, p) {
  let q = p + 1;
  while (q < s.length && regular(s.charCodeAt(q))) q++;
  let name = s.slice(p + 1, q);
  if (name.includes('#')) name = name.replace(/#([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
  return ['/' + name, q];
}

function parseLiteral(s, p) {
  const parts = [];
  let depth = 1, start = p;
  const n = s.length;
  while (p < n) {
    const code = s.charCodeAt(p);
    if (code === 92) {
      parts.push(s.slice(start, p));
      const escaped = s[++p];
      if (escaped === undefined) break;
      const simple = {n: '\n', r: '\r', t: '\t', b: '\b', f: '\f', '(': '(', ')': ')', '\\': '\\'}[escaped];
      if (simple !== undefined) { parts.push(simple); p++; }
      else if (escaped === '\r') { p++; if (s[p] === '\n') p++; }
      else if (escaped === '\n') p++;
      else if (escaped >= '0' && escaped <= '7') {
        let value = 0;
        for (let digits = 0; digits < 3 && s[p] >= '0' && s[p] <= '7'; digits++) value = value * 8 + s.charCodeAt(p++) - 48;
        parts.push(String.fromCharCode(value & 255));
      } else { parts.push(escaped); p++; }
      start = p;
    } else {
      if (code === 40) depth++;
      else if (code === 41 && --depth === 0) {
        parts.push(s.slice(start, p));
        return [new PdfString(parts.join('')), p + 1];
      }
      p++;
    }
  }
  parts.push(s.slice(start, p));
  return [new PdfString(parts.join('')), n];
}

function parseHex(s, p) {
  let end = s.indexOf('>', p);
  if (end < 0) end = s.length;
  let hex = s.slice(p, end);
  if (/[^0-9A-Fa-f]/.test(hex)) hex = hex.replace(/[^0-9A-Fa-f]/g, '');
  if (hex.length % 2) hex += '0';
  return [new PdfString(Buffer.from(hex, 'hex').toString('latin1')), end + 1];
}

function parseDict(s, p, depth) {
  const dict = new Map();
  for (;;) {
    p = skipSpace(s, p);
    if (p >= s.length) return [dict, s.length];
    const code = s.charCodeAt(p);
    if (code === 62 && s.charCodeAt(p + 1) === 62) return [dict, p + 2];
    if (code !== 47) {
      const [, q] = parseValue(s, p, depth + 1);
      p = q > p ? q : p + 1;
      continue;
    }
    const [key, q] = parseName(s, p);
    const [value, r] = parseValue(s, q, depth + 1);
    if (value !== undefined) dict.set(key.slice(1), value);
    p = r;
    if (dict.size > 100_000) throw new Failure('unsupported');
  }
}

// One object at p: [value, end]. `>>` is left for the enclosing dictionary.
function parseValue(s, p, depth = 0) {
  if (depth > MAX_DEPTH) throw new Failure('unsupported');
  p = skipSpace(s, p);
  const n = s.length;
  if (p >= n) return [undefined, n];
  const code = s.charCodeAt(p);
  if (code === 47) return parseName(s, p);
  if (code === 40) return parseLiteral(s, p + 1);
  if (code === 60) return s.charCodeAt(p + 1) === 60 ? parseDict(s, p + 2, depth) : parseHex(s, p + 1);
  if (code === 62) return [undefined, s.charCodeAt(p + 1) === 62 ? p : p + 1];
  if (code === 91) {
    const items = [];
    p++;
    for (;;) {
      p = skipSpace(s, p);
      if (p >= n) return [items, n];
      if (s.charCodeAt(p) === 93) return [items, p + 1];
      const [value, q] = parseValue(s, p, depth + 1);
      if (q <= p) { p++; continue; }
      if (value !== undefined) items.push(value);
      p = q;
      if (items.length > 1_000_000) throw new Failure('unsupported');
    }
  }
  if (code === 43 || code === 45 || code === 46 || (code >= 48 && code <= 57)) {
    NUMBER.lastIndex = p;
    const number = NUMBER.exec(s);
    if (!number) return [undefined, p + 1];
    const end = p + number[0].length;
    // An integer followed by "generation R" is an indirect reference.
    if (INTEGER.test(number[0])) {
      const q = skipSpace(s, end);
      NUMBER.lastIndex = q;
      const generation = NUMBER.exec(s);
      if (generation && INTEGER.test(generation[0])) {
        const r = skipSpace(s, q + generation[0].length);
        if (s.charCodeAt(r) === 82 && !regular(s.charCodeAt(r + 1))) return [new Ref(Number(number[0])), r + 1];
      }
    }
    return [Number(number[0]), end];
  }
  if (DELIMITER[code]) return [undefined, p + 1];
  let q = p;
  while (q < n && regular(s.charCodeAt(q))) q++;
  const word = s.slice(p, q);
  if (word === 'true') return [true, q];
  if (word === 'false') return [false, q];
  if (word === 'null') return [null, q];
  return [new Keyword(word), q];
}

const arrayOf = value => Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
const numberOr = (value, fallback) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;

// ---------------------------------------------------------------------------
// Encodings and glyph names (PDF 32000-1 Annex D), reduced to what text needs.

const latin1Table = () => Array.from({length: 256}, (_, code) => code >= 32 && code !== 127 ? String.fromCharCode(code) : '');
const WIN_ANSI = latin1Table();
'€\u0000‚ƒ„…†‡ˆ‰Š‹Œ\u0000Ž\u0000\u0000‘’“”•–—˜™š›œ\u0000žŸ'.split('').forEach((char, index) => {
  WIN_ANSI[128 + index] = char === '\u0000' ? '' : char;
});
const STANDARD = latin1Table();
STANDARD[39] = '’';
STANDARD[96] = '‘';
for (let code = 128; code < 256; code++) STANDARD[code] = '';
for (const [code, char] of [[0xa1, '¡'], [0xa2, '¢'], [0xa3, '£'], [0xa4, '⁄'], [0xa5, '¥'], [0xa6, 'ƒ'], [0xa7, '§'],
  [0xa8, '¤'], [0xa9, "'"], [0xaa, '“'], [0xab, '«'], [0xac, '‹'], [0xad, '›'], [0xae, 'ﬁ'], [0xaf, 'ﬂ'], [0xb1, '–'],
  [0xb2, '†'], [0xb3, '‡'], [0xb4, '·'], [0xb6, '¶'], [0xb7, '•'], [0xb8, '‚'], [0xb9, '„'], [0xba, '”'], [0xbb, '»'],
  [0xbc, '…'], [0xbd, '‰'], [0xbf, '¿'], [0xc1, '`'], [0xc2, '´'], [0xc3, 'ˆ'], [0xc4, '˜'], [0xc5, '¯'], [0xc6, '˘'],
  [0xc7, '˙'], [0xc8, '¨'], [0xca, '˚'], [0xcb, '¸'], [0xcd, '˝'], [0xce, '˛'], [0xcf, 'ˇ'], [0xd0, '—'], [0xe1, 'Æ'],
  [0xe3, 'ª'], [0xe8, 'Ł'], [0xe9, 'Ø'], [0xea, 'Œ'], [0xeb, 'º'], [0xf1, 'æ'], [0xf5, 'ı'], [0xf8, 'ł'], [0xf9, 'ø'],
  [0xfa, 'œ'], [0xfb, 'ß']]) STANDARD[code] = char;
const MAC_ROMAN = latin1Table();
'ÄÅÇÉÑÖÜáàâäãåçéèêëíìîïñóòôöõúùûü†°¢£§•¶ß®©™´¨≠ÆØ∞±≤≥¥µ∂∑∏π∫ªºΩæø¿¡¬√ƒ≈∆«»… ÀÃÕŒœ–—“”‘’÷◊ÿŸ⁄€‹›ﬁﬂ‡·‚„‰ÂÊÁËÈÍÎÏÌÓÔ\uf8ffÒÚÛÙıˆ˜¯˘˙˚¸˝˛ˇ'
  .split('').forEach((char, index) => { MAC_ROMAN[128 + index] = char; });
const BASE_ENCODINGS = {'/WinAnsiEncoding': WIN_ANSI, '/StandardEncoding': STANDARD, '/MacRomanEncoding': MAC_ROMAN,
  '/MacExpertEncoding': STANDARD};

const GLYPHS = {
  space: ' ', exclam: '!', quotedbl: '"', numbersign: '#', dollar: '$', percent: '%', ampersand: '&',
  quotesingle: "'", quoteright: '’', parenleft: '(', parenright: ')', asterisk: '*', plus: '+', comma: ',',
  hyphen: '-', period: '.', slash: '/', zero: '0', one: '1', two: '2', three: '3', four: '4', five: '5', six: '6',
  seven: '7', eight: '8', nine: '9', colon: ':', semicolon: ';', less: '<', equal: '=', greater: '>',
  question: '?', at: '@', bracketleft: '[', backslash: '\\', bracketright: ']', asciicircum: '^',
  underscore: '_', grave: '`', quoteleft: '‘', braceleft: '{', bar: '|', braceright: '}', asciitilde: '~',
  bullet: '•', endash: '–', emdash: '—', quotedblleft: '“', quotedblright: '”', quotesinglbase: '‚',
  quotedblbase: '„', ellipsis: '…', trademark: '™', registered: '®', copyright: '©', degree: '°', fi: 'fi',
  fl: 'fl', ff: 'ff', ffi: 'ffi', ffl: 'ffl', dagger: '†', daggerdbl: '‡', section: '§', paragraph: '¶',
  periodcentered: '·', minus: '−', multiply: '×', divide: '÷', plusminus: '±', mu: 'µ', nbspace: ' ',
  nonbreakingspace: ' ', sfthyphen: '-', softhyphen: '-', exclamdown: '¡', questiondown: '¿', cent: '¢',
  sterling: '£', yen: '¥', Euro: '€', euro: '€', currency: '¤', brokenbar: '¦', ordfeminine: 'ª',
  ordmasculine: 'º', guillemotleft: '«', guillemotright: '»', guilsinglleft: '‹', guilsinglright: '›',
  logicalnot: '¬', onesuperior: '¹', twosuperior: '²', threesuperior: '³', onehalf: '½', onequarter: '¼',
  threequarters: '¾', perthousand: '‰', germandbls: 'ß', ae: 'æ', AE: 'Æ', oe: 'œ', OE: 'Œ', oslash: 'ø',
  Oslash: 'Ø', lslash: 'ł', Lslash: 'Ł', dotlessi: 'ı', florin: 'ƒ', fraction: '⁄', eth: 'ð', Eth: 'Ð',
  thorn: 'þ', Thorn: 'Þ', acute: '´', dieresis: '¨', macron: '¯', cedilla: '¸', circumflex: 'ˆ', tilde: '˜',
  arrowright: '→', arrowleft: '←', lessequal: '≤', greaterequal: '≥', notequal: '≠', infinity: '∞',
};
const ACCENTS = {acute: '\u0301', grave: '\u0300', circumflex: '\u0302', tilde: '\u0303', dieresis: '\u0308',
  ring: '\u030a', cedilla: '\u0327', caron: '\u030c', macron: '\u0304', breve: '\u0306', ogonek: '\u0328',
  dotaccent: '\u0307', hungarumlaut: '\u030b'};
const ACCENTED = new RegExp(`^([A-Za-z])(${Object.keys(ACCENTS).join('|')})$`);

export function glyphUnicode(name) {
  if (typeof name !== 'string' || !name) return undefined;
  if (Object.hasOwn(GLYPHS, name)) return GLYPHS[name];
  if (/^[A-Za-z]$/.test(name)) return name;
  const accented = ACCENTED.exec(name);
  if (accented) return (accented[1] + ACCENTS[accented[2]]).normalize('NFC');
  const uni = /^uni((?:[0-9A-F]{4})+)$/.exec(name);
  if (uni) return uni[1].match(/.{4}/g).map(hex => String.fromCharCode(parseInt(hex, 16))).join('');
  const u = /^u([0-9A-F]{4,6})$/.exec(name);
  if (u) { const point = parseInt(u[1], 16); return point <= 0x10ffff ? String.fromCodePoint(point) : undefined; }
  const base = name.split('.')[0];
  if (base !== name) return glyphUnicode(base);
  if (name.includes('_')) {
    const parts = name.split('_').map(glyphUnicode);
    return parts.every(part => part !== undefined) ? parts.join('') : undefined;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// CMaps: code space ranges and ToUnicode mappings (§9.7.5, §9.10.3).

const HEX_PAIR = /<([0-9A-Fa-f\s]*)>\s*<([0-9A-Fa-f\s]*)>/g;
const RANGE = /<([0-9A-Fa-f\s]*)>\s*<([0-9A-Fa-f\s]*)>\s*(?:<([0-9A-Fa-f\s]*)>|\[([^\]]*)\])/g;
const HEX_STRING = /<([0-9A-Fa-f\s]*)>/g;
const hexBytes = hex => { hex = hex.replace(/\s/g, ''); return Buffer.from(hex.length % 2 ? `${hex}0` : hex, 'hex'); };
const hexCode = hex => { const bytes = hexBytes(hex); let code = 0; for (const byte of bytes) code = code * 256 + byte; return {code, length: bytes.length}; };
function utf16(bytes) {
  let text = '';
  for (let i = 0; i + 1 < bytes.length; i += 2) text += String.fromCharCode(bytes[i] << 8 | bytes[i + 1]);
  if (bytes.length % 2) text += String.fromCharCode(bytes[bytes.length - 1]);
  return text;
}
// A mapping's Unicode value: at most 512 bytes, as §9.10.3 allows.
const unicodeOf = hex => utf16(hexBytes(hex).subarray(0, 512));
// Linear: each section ends at its own end keyword; a missing one ends parsing.
function* sections(text, begin, end) {
  for (let at = text.indexOf(begin); at >= 0; at = text.indexOf(begin, at)) {
    const stop = text.indexOf(end, at + begin.length);
    if (stop < 0) return;
    yield text.slice(at + begin.length, stop);
    at = stop + end.length;
  }
}

// One map keeps at most CMAP_MAX_CODES mapped codes, CMAP_MAX_RANGES
// bfrange runs and CMAP_MAX_CODESPACE code space ranges; later entries are
// ignored. `budget.entries` is shared by every table of one document (maps
// and glyph widths), and running out of it ends the extraction as too large.
const CMAP_MAX_CODES = 65_536, CMAP_MAX_RANGES = 10_000, CMAP_MAX_CODESPACE = 256;
function spend(budget) {
  if (--budget.entries < 0) throw new Failure('too-large');
}

export function parseCMap(text, budget = {entries: Infinity}) {
  const cmap = {codespace: [], single: new Map(), ranges: []};
  for (const body of sections(text, 'begincodespacerange', 'endcodespacerange')) {
    for (const [, low, high] of body.matchAll(HEX_PAIR)) {
      if (cmap.codespace.length >= CMAP_MAX_CODESPACE) break;
      const lo = hexCode(low), hi = hexCode(high);
      if (lo.length < 1 || lo.length > 4 || lo.length !== hi.length) continue;
      spend(budget);
      cmap.codespace.push({bytes: lo.length, lo: lo.code, hi: hi.code});
    }
  }
  for (const body of sections(text, 'beginbfchar', 'endbfchar')) {
    for (const [, source, target] of body.matchAll(HEX_PAIR)) {
      if (cmap.single.size >= CMAP_MAX_CODES) break;
      spend(budget);
      cmap.single.set(hexCode(source).code, unicodeOf(target));
    }
  }
  for (const body of sections(text, 'beginbfrange', 'endbfrange')) {
    for (const [, low, high, start, list] of body.matchAll(RANGE)) {
      const lo = hexCode(low).code, hi = hexCode(high).code;
      if (hi < lo || hi - lo > 65_535) continue;
      if (start !== undefined) {
        const units = cmap.ranges.length < CMAP_MAX_RANGES ? unicodeOf(start) : '';
        if (!units) continue;
        spend(budget);
        cmap.ranges.push({lo, hi, prefix: units.slice(0, -1), last: units.charCodeAt(units.length - 1)});
      } else {
        // A list names one target per code, read one at a time.
        let code = lo;
        for (const [, target] of list.matchAll(HEX_STRING)) {
          if (code > hi || cmap.single.size >= CMAP_MAX_CODES) break;
          spend(budget);
          cmap.single.set(code++, unicodeOf(target));
        }
      }
    }
  }
  return cmap;
}

function cmapLookup(cmap, code) {
  const single = cmap.single.get(code);
  if (single !== undefined) return single;
  for (const range of cmap.ranges) {
    if (code >= range.lo && code <= range.hi) return range.prefix + String.fromCharCode(range.last + code - range.lo);
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Stream filters (§7.4) that text can use. Image codecs are never decoded.

// At most `max` bytes are written, into an array of at most four times the
// input ("z" is one byte for four); more fails as too large.
function ascii85(data, max) {
  const out = new Uint8Array(Math.min(max, data.length * 4));
  let size = 0, group = 0, count = 0;
  const put = (value, bytes) => {
    if (size + bytes > out.length) throw new Failure('too-large');
    for (let k = 0; k < bytes; k++) out[size++] = (value >>> (24 - 8 * k)) & 255;
  };
  for (let i = 0; i < data.length; i++) {
    const code = data[i];
    if (code === 126) break;
    if (WHITE[code]) continue;
    if (code === 122 && count === 0) { put(0, 4); continue; }
    if (code < 33 || code > 117) return undefined;
    group = group * 85 + code - 33;
    if (++count === 5) {
      put(group, 4);
      group = 0;
      count = 0;
    }
  }
  if (count > 1) {
    for (let k = count; k < 5; k++) group = group * 85 + 84;
    put(group, count - 1);
  }
  return out.subarray(0, size);
}

function asciiHex(data) {
  let text = Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('latin1');
  const end = text.indexOf('>');
  if (end >= 0) text = text.slice(0, end);
  return hexBytes(text.replace(/[^0-9A-Fa-f]/g, ''));
}

// ---------------------------------------------------------------------------
// Document: objects, pages and fonts.

class PdfDocument {
  constructor(bytes, limits, deadline) {
    this.bytes = bytes;
    this.s = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString('latin1');
    this.limits = limits;
    this.deadline = deadline;
    this.ticks = 0;
    this.decoded = 0;
    // Font table entries left for the whole document (parseCMap, cidWidths),
    // and glyph lookups still cached (unicode).
    this.tables = {entries: limits.maxTableEntries};
    this.memoRoom = 262_144;
    this.objects = new Map();
    this.trailers = [];
    this.objectStreams = [];
    this.fonts = new WeakMap();
    this.scan();
    this.loadObjectStreams();
  }

  tick() {
    if ((++this.ticks & 255) === 0 && performance.now() > this.deadline) throw new Failure('timeout');
  }

  // Every "n g obj" in file order; a later definition of a number replaces
  // an earlier one, as an incremental update does. Stream data is skipped.
  scan() {
    const s = this.s;
    const header = /(?<![0-9])(\d{1,10})[\0\t\n\f\r ]+\d{1,5}[\0\t\n\f\r ]+obj(?![^\0\t\n\f\r ()<>[\]{}\/%])/g;
    for (let match; (match = header.exec(s));) {
      this.tick();
      let value, end;
      try {
        [value, end] = parseValue(s, match.index + match[0].length);
      } catch (error) {
        if (error instanceof Failure && error.reason !== 'unsupported') throw error;
        continue;
      }
      let entry = value;
      if (value instanceof Map) {
        const keyword = skipSpace(s, end);
        if (s.startsWith('stream', keyword) && !regular(s.charCodeAt(keyword + 6))) {
          let start = keyword + 6;
          if (s.charCodeAt(start) === 13) start++;
          if (s.charCodeAt(start) === 10) start++;
          const stop = this.streamEnd(value, start);
          entry = new Stream(value, start, stop);
          end = stop;
          if (value.get('Type') === '/XRef') this.trailers.push({at: match.index, dict: value});
          if (value.get('Type') === '/ObjStm') this.objectStreams.push({at: match.index, stream: entry});
        }
      }
      header.lastIndex = Math.max(header.lastIndex, end);
      const num = Number(match[1]);
      const known = this.objects.get(num);
      if (!known || known.at <= match.index) this.objects.set(num, {at: match.index, value: entry});
      if (this.objects.size > this.limits.maxObjects) throw new Failure('too-large');
    }
    const trailer = /trailer[\0\t\n\f\r ]*<</g;
    for (let match; (match = trailer.exec(s));) {
      this.tick();
      try {
        const [dict] = parseValue(s, match.index + 7);
        if (dict instanceof Map) this.trailers.push({at: match.index, dict});
      } catch (error) {
        if (error instanceof Failure && error.reason !== 'unsupported') throw error;
      }
    }
    this.trailers.sort((a, b) => a.at - b.at);
  }

  streamEnd(dict, start) {
    const s = this.s;
    const length = dict.get('Length');
    if (Number.isInteger(length) && length >= 0 && start + length <= s.length &&
        s.startsWith('endstream', skipSpace(s, start + length))) return start + length;
    const found = s.indexOf('endstream', start);
    if (found < 0) return s.length;
    let stop = found;
    if (s.charCodeAt(stop - 1) === 10) stop--;
    if (s.charCodeAt(stop - 1) === 13) stop--;
    return Math.max(start, stop);
  }

  loadObjectStreams() {
    for (const {at, stream} of this.objectStreams) {
      this.tick();
      const data = this.decode(stream);
      const count = stream.dict.get('N'), first = stream.dict.get('First');
      if (data === undefined || !Number.isInteger(count) || !Number.isInteger(first) || count < 0 ||
          count > 100_000 || first < 0 || first > data.length) continue;
      const offsets = [];
      let p = 0;
      for (let i = 0; i < count; i++) {
        const [num, q] = parseValue(data, p);
        const [offset, r] = parseValue(data, q);
        if (!Number.isInteger(num) || !Number.isInteger(offset)) break;
        offsets.push([num, offset]);
        p = r;
      }
      for (const [num, offset] of offsets) {
        this.tick();
        const known = this.objects.get(num);
        if (known && known.at > at) continue;
        if (!known && this.objects.size >= this.limits.maxObjects) throw new Failure('too-large');
        try {
          this.objects.set(num, {at, value: parseValue(data, first + offset)[0]});
        } catch (error) {
          if (error instanceof Failure && error.reason !== 'unsupported') throw error;
        }
      }
    }
  }

  resolve(value) {
    for (let hops = 0; value instanceof Ref && hops < 16; hops++) value = this.objects.get(value.num)?.value;
    return value instanceof Ref ? undefined : value;
  }

  // The stream's decoded bytes as a latin1 string, or undefined when a filter
  // is not one text needs.
  decode(stream) {
    this.tick();
    let data = this.bytes.subarray(stream.start, stream.end);
    const filters = arrayOf(this.resolve(stream.dict.get('Filter'))).map(filter => this.resolve(filter));
    const parameters = arrayOf(this.resolve(stream.dict.get('DecodeParms'))).map(value => this.resolve(value));
    // Every decoder stops at this stream's share of the decoded bytes bound.
    const room = Math.min(this.limits.maxStreamBytes, this.limits.maxDecodedBytes - this.decoded);
    for (const [index, filter] of filters.entries()) {
      const predictor = parameters[index] instanceof Map ? this.resolve(parameters[index].get('Predictor')) : undefined;
      if (typeof predictor === 'number' && predictor > 1) return undefined;
      if (filter === '/FlateDecode' || filter === '/Fl') data = this.inflate(data, room);
      else if (filter === '/ASCII85Decode' || filter === '/A85') data = ascii85(data, Math.max(0, room));
      else if (filter === '/ASCIIHexDecode' || filter === '/AHx') data = asciiHex(data);
      else return undefined;
      if (!data) return undefined;
      if (data.byteLength > room) throw new Failure('too-large');
    }
    this.decoded += data.byteLength;
    if (this.decoded > this.limits.maxDecodedBytes) throw new Failure('too-large');
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('latin1');
  }

  inflate(data, room) {
    const maxOutputLength = Math.max(1, room);
    // A stream cut short still yields the text before the cut.
    const options = {maxOutputLength, finishFlush: zlib.Z_SYNC_FLUSH};
    for (const inflate of [inflateSync, inflateRawSync]) {
      try {
        return inflate(data, options);
      } catch (error) {
        if (error?.code === 'ERR_BUFFER_TOO_LARGE') throw new Failure('too-large');
      }
    }
    return undefined;
  }

  catalog() {
    const root = [...this.trailers].reverse().find(trailer => trailer.dict.has('Root'));
    const catalog = root && this.resolve(root.dict.get('Root'));
    if (catalog instanceof Map) return catalog;
    let found;
    for (const {at, value} of this.objects.values()) {
      if (value instanceof Map && value.get('Type') === '/Catalog' && (!found || at > found.at)) found = {at, value};
    }
    return found?.value;
  }

  // The first pages in page-tree order, with inherited resources, and the
  // declared page count.
  pages() {
    const limit = this.limits.maxPages;
    const tree = this.resolve(this.catalog()?.get('Pages'));
    const pages = [];
    let total = tree instanceof Map ? this.resolve(tree.get('Count')) : undefined;
    const seen = new Set();
    const stack = tree instanceof Map ? [{node: tree, resources: undefined}] : [];
    while (stack.length && pages.length < limit) {
      this.tick();
      const {node, resources: inherited} = stack.pop();
      const resources = node.has('Resources') ? node.get('Resources') : inherited;
      const kids = this.resolve(node.get('Kids'));
      if (Array.isArray(kids) && node.get('Type') !== '/Page') {
        for (let i = kids.length - 1; i >= 0; i--) {
          const kid = kids[i];
          if (kid instanceof Ref) { if (seen.has(kid.num)) continue; seen.add(kid.num); }
          const child = this.resolve(kid);
          if (child instanceof Map && stack.length < 100_000) stack.push({node: child, resources});
        }
      } else {
        pages.push({node, resources});
      }
    }
    if (!pages.length) {
      // No usable page tree: page objects in file order.
      const loose = [...this.objects.values()].filter(({value}) => value instanceof Map && value.get('Type') === '/Page')
        .sort((a, b) => a.at - b.at);
      total = loose.length;
      for (const {value} of loose.slice(0, limit)) pages.push({node: value, resources: value.get('Resources')});
    }
    return {pages, total: Number.isInteger(total) && total >= pages.length ? total : pages.length};
  }

  font(resources, name) {
    const fonts = this.resolve(this.resolve(resources)?.get?.('Font'));
    const dict = typeof name === 'string' && fonts instanceof Map ? this.resolve(fonts.get(name.slice(1))) : undefined;
    if (!(dict instanceof Map)) return undefined;
    if (!this.fonts.has(dict)) this.fonts.set(dict, this.loadFont(dict));
    return this.fonts.get(dict);
  }

  loadFont(dict) {
    const subtype = dict.get('Subtype');
    const font = {composite: subtype === '/Type0', scale: 0.001, memo: new Map(), unmapped: 0};
    const toUnicode = this.resolve(dict.get('ToUnicode'));
    if (toUnicode instanceof Stream) {
      const text = this.decode(toUnicode);
      if (text) font.map = parseCMap(text, this.tables);
    }
    if (font.composite) {
      const encoding = this.resolve(dict.get('Encoding'));
      if (encoding instanceof Stream) {
        const text = this.decode(encoding);
        const codespace = text ? parseCMap(text, this.tables).codespace : [];
        if (codespace.length) font.codespace = codespace;
      }
      font.codespace ??= font.map?.codespace.length ? font.map.codespace : [{bytes: 2, lo: 0, hi: 0xffff}];
      font.lengths = [...new Set(font.codespace.map(range => range.bytes))].sort((a, b) => a - b);
      font.identity = encoding === '/Identity-H' || encoding === '/Identity-V';
      const descendant = this.resolve(arrayOf(this.resolve(dict.get('DescendantFonts')))[0]);
      font.defaultWidth = 1000;
      if (descendant instanceof Map) {
        font.defaultWidth = numberOr(this.resolve(descendant.get('DW')), 1000);
        const widths = this.resolve(descendant.get('W'));
        if (Array.isArray(widths) && font.identity) font.cidWidths = this.cidWidths(widths);
      }
      return font;
    }
    font.firstChar = numberOr(this.resolve(dict.get('FirstChar')), 0);
    const widths = this.resolve(dict.get('Widths'));
    if (Array.isArray(widths)) font.widths = widths.map(width => numberOr(this.resolve(width), 0));
    const descriptor = this.resolve(dict.get('FontDescriptor'));
    const missing = descriptor instanceof Map ? numberOr(this.resolve(descriptor.get('MissingWidth')), 0) : 0;
    font.missingWidth = missing > 0 ? missing : font.widths ? 0 : 500;
    if (subtype === '/Type3') {
      const matrix = this.resolve(dict.get('FontMatrix'));
      const scale = Array.isArray(matrix) ? numberOr(this.resolve(matrix[0]), 0.001) : 0.001;
      if (scale > 0 && scale < 1) font.scale = scale;
    }
    const flags = descriptor instanceof Map ? numberOr(this.resolve(descriptor.get('Flags')), 0) : 0;
    font.encoding = this.simpleEncoding(this.resolve(dict.get('Encoding')), subtype, (flags & 4) !== 0);
    return font;
  }

  simpleEncoding(encoding, subtype, symbolic) {
    const name = encoding instanceof Map ? this.resolve(encoding.get('BaseEncoding')) : encoding;
    const base = BASE_ENCODINGS[name] ?? (symbolic ? latin1Table() : subtype === '/TrueType' ? WIN_ANSI : STANDARD);
    const table = [...base];
    const differences = encoding instanceof Map ? this.resolve(encoding.get('Differences')) : undefined;
    if (Array.isArray(differences)) {
      let code = 0;
      for (const item of differences) {
        const value = this.resolve(item);
        if (typeof value === 'number') code = value;
        else if (typeof value === 'string' && code >= 0 && code < 256) table[code++] = glyphUnicode(value.slice(1)) ?? '';
      }
    }
    return table;
  }

  // At most 65,536 widths per font, each spent from the document's budget.
  cidWidths(entries) {
    const widths = new Map();
    const set = (cid, width) => { spend(this.tables); widths.set(cid, width); };
    for (let i = 0; i < entries.length && widths.size < 65_536;) {
      const first = this.resolve(entries[i]), next = this.resolve(entries[i + 1]);
      if (typeof first !== 'number') break;
      if (Array.isArray(next)) {
        for (let k = 0; k < next.length && widths.size < 65_536; k++) set(first + k, numberOr(this.resolve(next[k]), 0));
        i += 2;
      } else {
        const last = next, width = numberOr(this.resolve(entries[i + 2]), 0);
        if (typeof last !== 'number' || last < first) break;
        for (let cid = first; cid <= last && widths.size < 65_536; cid++) set(cid, width);
        i += 3;
      }
    }
    return widths;
  }

  // ---- content streams (§9.4 text objects, §8.8 external objects)

  pageText(page, context) {
    const contents = arrayOf(this.resolve(page.node.get('Contents')))
      .map(item => this.resolve(item)).filter(item => item instanceof Stream);
    const text = contents.map(stream => this.decode(stream) ?? '').join('\n');
    this.run(text, page.resources, 0, context);
  }

  run(content, resources, depth, context) {
    const s = content, n = s.length, operands = [];
    let state = {font: undefined, size: 0, charSpacing: 0, wordSpacing: 0, scale: 1, leading: 0,
      matrix: [1, 0, 0, 1, 0, 0], line: [1, 0, 0, 1, 0, 0]};
    const saved = [];
    const number = (index) => numberOr(operands[operands.length + index], 0);
    const move = (x, y) => {
      const [a, b, c, d, e, f] = state.line;
      state.line = [a, b, c, d, x * a + y * c + e, x * b + y * d + f];
      state.matrix = [...state.line];
    };
    const advance = (distance) => {
      state.matrix[4] += distance * state.matrix[0];
      state.matrix[5] += distance * state.matrix[1];
    };
    const show = (string) => {
      if (!(string instanceof PdfString)) return;
      const font = state.font;
      const bytes = string.bytes;
      const size = Math.abs(state.size) * Math.hypot(state.matrix[2], state.matrix[3]) || Math.abs(state.size) || 1;
      let text = '', distance = 0;
      const room = this.limits.maxTextChars;
      this.codes(font, bytes, (code, length) => {
        // Past the text bound the rest is never kept: stop growing it.
        if (text.length <= room) text += this.unicode(font, code);
        const width = font ? this.width(font, code) * font.scale : 0.5;
        distance += (width * state.size + state.charSpacing + (length === 1 && code === 32 ? state.wordSpacing : 0)) * state.scale;
      });
      context.emit(text, state.matrix[4], state.matrix[5], size);
      advance(distance);
      context.last = {x: state.matrix[4], y: state.matrix[5], size};
    };
    let p = 0;
    while (p < n) {
      this.tick();
      const code = s.charCodeAt(p);
      if (WHITE[code]) { p++; continue; }
      if (code === 37) { while (p < n && s.charCodeAt(p) !== 10 && s.charCodeAt(p) !== 13) p++; continue; }
      if (operands.length > 100_000) operands.length = 0;
      if (code === 40) { const [value, q] = parseLiteral(s, p + 1); operands.push(value); p = q; continue; }
      if (code === 60) {
        const [value, q] = s.charCodeAt(p + 1) === 60 ? parseDict(s, p + 2, 1) : parseHex(s, p + 1);
        operands.push(value);
        p = q;
        continue;
      }
      if (code === 47) { const [value, q] = parseName(s, p); operands.push(value); p = q; continue; }
      if (code === 43 || code === 45 || code === 46 || (code >= 48 && code <= 57)) {
        // Content streams have no indirect references: no lookahead.
        NUMBER.lastIndex = p;
        const match = NUMBER.exec(s);
        if (match) { operands.push(Number(match[0])); p += match[0].length; } else p++;
        continue;
      }
      if (code === 91) { operands.push(ARRAY_START); p++; continue; }
      if (code === 93) {
        const start = operands.lastIndexOf(ARRAY_START);
        const items = start >= 0 ? operands.splice(start).slice(1) : [];
        operands.push(items);
        p++;
        continue;
      }
      if (DELIMITER[code]) { p++; continue; }
      let q = p;
      while (q < n && regular(s.charCodeAt(q))) q++;
      const operator = s.slice(p, q);
      p = q;
      switch (operator) {
        case 'BT': state.matrix = [1, 0, 0, 1, 0, 0]; state.line = [1, 0, 0, 1, 0, 0]; break;
        case 'Tf':
          state.font = this.font(resources, operands[operands.length - 2]);
          state.size = number(-1);
          break;
        case 'Tc': state.charSpacing = number(-1); break;
        case 'Tw': state.wordSpacing = number(-1); break;
        case 'Tz': state.scale = number(-1) / 100; break;
        case 'TL': state.leading = number(-1); break;
        case 'Td': move(number(-2), number(-1)); break;
        case 'TD': state.leading = -number(-1); move(number(-2), number(-1)); break;
        case 'Tm':
          if (operands.length >= 6 && operands.slice(-6).every(value => typeof value === 'number')) {
            state.line = operands.slice(-6);
            state.matrix = [...state.line];
          }
          break;
        case 'T*': move(0, -state.leading); break;
        case 'Tj': show(operands[operands.length - 1]); break;
        case "'": move(0, -state.leading); show(operands[operands.length - 1]); break;
        case '"':
          state.wordSpacing = number(-3);
          state.charSpacing = number(-2);
          move(0, -state.leading);
          show(operands[operands.length - 1]);
          break;
        case 'TJ': {
          const items = operands[operands.length - 1];
          if (Array.isArray(items)) {
            for (const item of items) {
              if (item instanceof PdfString) show(item);
              else if (typeof item === 'number') advance(-item / 1000 * state.size * state.scale);
            }
          }
          break;
        }
        case 'q': if (saved.length < 64) saved.push({...state, matrix: [...state.matrix], line: [...state.line]}); break;
        case 'Q': if (saved.length) state = saved.pop(); break;
        case 'Do': this.form(operands[operands.length - 1], resources, depth, context); break;
        case 'BI': {
          // Inline image data is binary: skip from ID to EI.
          const data = s.indexOf('ID', p);
          const end = /[\0\t\n\f\r ]EI(?![^\0\t\n\f\r ])/g;
          end.lastIndex = data < 0 ? n : data + 2;
          const match = data < 0 ? null : end.exec(s);
          p = match ? match.index + 3 : n;
          break;
        }
        default: break;
      }
      operands.length = 0;
    }
  }

  form(name, resources, depth, context) {
    const objects = this.resolve(this.resolve(resources)?.get?.('XObject'));
    const form = typeof name === 'string' && objects instanceof Map ? this.resolve(objects.get(name.slice(1))) : undefined;
    if (!(form instanceof Stream) || form.dict.get('Subtype') !== '/Form' || depth >= 4 ||
        context.forms >= 64) return;
    context.forms++;
    const content = this.decode(form);
    if (!content) return;
    context.last = undefined;
    context.breakLine = true;
    this.run(content, form.dict.get('Resources') ?? resources, depth + 1, context);
    context.last = undefined;
    context.breakLine = true;
  }

  codes(font, bytes, visit) {
    if (!font?.composite) {
      for (let i = 0; i < bytes.length; i++) visit(bytes.charCodeAt(i), 1);
      return;
    }
    for (let i = 0; i < bytes.length;) {
      let taken = 0;
      for (const length of font.lengths) {
        if (i + length > bytes.length) break;
        let code = 0;
        for (let k = 0; k < length; k++) code = code * 256 + bytes.charCodeAt(i + k);
        if (font.codespace.some(range => range.bytes === length && code >= range.lo && code <= range.hi)) {
          visit(code, length);
          taken = length;
          break;
        }
      }
      if (!taken) {
        taken = Math.min(font.lengths[0] ?? 2, bytes.length - i) || 1;
        let code = 0;
        for (let k = 0; k < taken; k++) code = code * 256 + bytes.charCodeAt(i + k);
        visit(code, taken);
      }
      i += taken;
    }
  }

  unicode(font, code) {
    if (!font) return code >= 32 && code < 127 ? String.fromCharCode(code) : '';
    let text = font.memo.get(code);
    if (text !== undefined) return text;
    text = font.map ? cmapLookup(font.map, code) : undefined;
    if (text === undefined && !font.composite) text = font.encoding[code];
    if (text === undefined || text === '') { font.unmapped++; text = ''; }
    if (this.memoRoom > 0) { this.memoRoom--; font.memo.set(code, text); }
    return text;
  }

  width(font, code) {
    if (font.composite) return font.cidWidths?.get(code) ?? font.defaultWidth;
    const width = font.widths?.[code - font.firstChar];
    return width > 0 ? width : font.missingWidth;
  }

  extract() {
    const catalog = this.catalog();
    if (this.trailers.some(trailer => trailer.dict.has('Encrypt'))) throw new Failure('encrypted');
    if (!catalog && !this.objects.size) throw new Failure('unsupported');
    const {pages, total} = this.pages();
    if (!pages.length) throw new Failure('unsupported');
    const limit = this.limits.maxTextChars;
    const parts = [];
    let chars = 0, pagesRead = 0, truncated = pages.length < total;
    const context = {
      out: [], last: undefined, breakLine: false, forms: 0,
      emit(text, x, y, size) {
        if (!text) return;
        let separator = '';
        if (this.breakLine && this.out.length) separator = '\n';
        else if (this.last) {
          const scale = Math.max(this.last.size, size, 0.01);
          const dx = x - this.last.x, dy = y - this.last.y;
          if (Math.abs(dy) > scale * 0.6) separator = '\n';
          else if (dx > scale * 0.18 || dx < -scale * 1.5) separator = ' ';
        }
        this.breakLine = false;
        const previous = this.out.at(-1) ?? '';
        if (separator === ' ' && (previous.endsWith(' ') || text.startsWith(' '))) separator = '';
        this.out.push(separator + text);
        chars += separator.length + text.length;
        if (chars > limit) throw FULL;
      },
    };
    for (const [index, page] of pages.entries()) {
      context.out = [];
      context.last = undefined;
      context.breakLine = false;
      context.forms = 0;
      let stop = false;
      try {
        this.pageText(page, context);
      } catch (error) {
        // The text bound keeps this page's text so far; the deadline or the
        // decompression bound after some pages keeps the pages already read.
        if (error === FULL) { truncated = true; stop = true; }
        else if (error instanceof Failure && ['timeout', 'too-large'].includes(error.reason) && pagesRead > 0) {
          truncated = true;
          break;
        } else throw error;
      }
      parts.push(`[Page ${index + 1}]\n${tidy(context.out.join(''))}`);
      pagesRead++;
      if (stop) break;
    }
    const text = parts.join('\n\n').slice(0, limit);
    const letters = text.replace(/\[Page \d+\]/g, '').match(/[\p{L}\p{N}]/gu)?.length ?? 0;
    const opaque = text.match(/[\ufffd\ue000-\uf8ff]/g)?.length ?? 0;
    if (letters < 16 || opaque > letters) throw new Failure('no-text');
    return {text, pagesRead, pageCount: total, truncated};
  }
}

const ARRAY_START = Symbol('array-start');

function tidy(text) {
  return text.normalize('NFKC')
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\ufffe\uffff]/g, ' ')
    .replace(/[ \t\u00a0\u2000-\u200b]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// Synchronous extraction, for the worker (and tests). Throws an Error with a
// `reason` from PDF_NOT_READ_REASONS when the text cannot be read.
export function extractPdfTextSync(input, {limits: overrides = {}, budgetMs} = {}) {
  const limits = {...PDF_TEXT_LIMITS, ...overrides};
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (bytes.byteLength > limits.maxBytes) throw new Failure('too-large');
  if (!hasPdfMagic(bytes)) throw new Failure('unsupported');
  const deadline = performance.now() + Math.max(1, budgetMs ?? limits.timeoutMs);
  return new PdfDocument(bytes, limits, deadline).extract();
}

// The extraction process's script. A plugin loaded from anything but a file
// has none, and every extraction is then reported as unavailable.
const WORKER_SCRIPT = (() => {
  try { return fileURLToPath(new URL('./pdf-text-worker.mjs', import.meta.url)); } catch { return undefined; }
})();
// The one JSON line a worker writes: the text, escaped, and a little more.
const MAX_WORKER_OUTPUT = 4 * 1024 * 1024;

// PDF_EXTRACTION_CONCURRENCY slots for the whole gateway. A slot is held
// until its process has exited, not only until its result is known.
let running = 0;
const waiting = [];
function whenSlotFree(start) {
  if (running < PDF_EXTRACTION_CONCURRENCY) {
    running++;
    start();
    return () => {};
  }
  waiting.push(start);
  return () => { const at = waiting.indexOf(start); if (at >= 0) waiting.splice(at, 1); };
}
function releaseSlot() {
  const next = waiting.shift();
  if (next) next();
  else running--;
}

// A worker that ended without a result: V8 aborts when the heap limit is
// reached (SIGABRT, or exit code 134 on Windows), and a kernel OOM kill is a
// SIGKILL that is not ours. Exit code 1 is Node failing to load the script.
function endedWithout(code, signal) {
  if (signal === 'SIGABRT' || signal === 'SIGKILL' || code === 134) return 'too-large';
  return code === 1 ? 'unavailable' : 'unsupported';
}

// One extraction in its own process: {ok: true, text, pagesRead, pageCount,
// truncated} or {ok: false, reason}. The process is killed at `timeoutMs`
// after this call (time spent waiting for a slot included) or when `signal`
// aborts ({ok: false, reason: "aborted"}); it stops by itself a little
// earlier and then keeps the pages it finished.
export function extractPdfText(bytes, {
  signal,
  timeoutMs = PDF_TEXT_LIMITS.timeoutMs,
  limits: overrides = {},
  spawnImpl = spawn,
  execPath = process.execPath,
  script = WORKER_SCRIPT,
  now = Date.now,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  const limits = {...PDF_TEXT_LIMITS, ...overrides};
  return new Promise(resolve => {
    let child, timer, cancelWait, settled = false;
    const finish = outcome => {
      if (settled) return;
      settled = true;
      clearTimer(timer);
      signal?.removeEventListener?.('abort', onAbort);
      cancelWait?.();
      if (child && child.exitCode === null && child.signalCode === null) {
        try { child.kill('SIGKILL'); } catch { /* It has already exited. */ }
      }
      resolve(outcome);
    };
    const onAbort = () => finish({ok: false, reason: 'aborted'});
    if (signal?.aborted) { onAbort(); return; }
    if (!(bytes instanceof Uint8Array) || bytes.byteLength > limits.maxBytes) {
      finish({ok: false, reason: 'too-large'});
      return;
    }
    // The worker stops itself 250 ms before it would be killed.
    const deadline = now() + timeoutMs - 250;
    const start = () => {
      let released = false;
      const release = () => { if (!released) { released = true; releaseSlot(); } };
      const options = Buffer.from(JSON.stringify({limits, deadline})).toString('base64');
      try {
        if (!script) throw new Error('no worker script');
        // No environment (so no NODE_OPTIONS) and no inherited flags.
        child = spawnImpl(execPath, [`--max-old-space-size=${limits.heapMb}`, script, options],
          {stdio: ['pipe', 'pipe', 'ignore'], env: {}, windowsHide: true});
      } catch {
        release();
        finish({ok: false, reason: 'unavailable'});
        return;
      }
      const output = [];
      let size = 0;
      child.on('error', () => {
        // Spawning failed: there is no process to wait for.
        if (child.pid === undefined) release();
        finish({ok: false, reason: 'unavailable'});
      });
      child.on('close', (code, signalName) => {
        release();
        if (settled) return;
        let message;
        try { message = JSON.parse(Buffer.concat(output, size).toString('utf8')); } catch { message = undefined; }
        if (message?.type === 'result' && typeof message.text === 'string' && message.text.length <= limits.maxTextChars &&
            Number.isInteger(message.pagesRead) && Number.isInteger(message.pageCount)) {
          finish({ok: true, text: message.text, pagesRead: message.pagesRead, pageCount: message.pageCount,
            truncated: message.truncated === true});
        } else if (message?.type === 'failed') {
          finish({ok: false, reason: REASONS.has(message.reason) ? message.reason : 'unsupported'});
        } else {
          finish({ok: false, reason: message === undefined ? endedWithout(code, signalName) : 'unsupported'});
        }
      });
      child.stdout?.on('data', chunk => {
        size += chunk.length;
        if (size > MAX_WORKER_OUTPUT) finish({ok: false, reason: 'unsupported'});
        else output.push(chunk);
      });
      child.stdout?.on('error', () => {});
      // A worker that exits early closes its end: the write error is expected.
      child.stdin?.on('error', () => {});
      child.stdin?.end(Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength));
    };
    signal?.addEventListener?.('abort', onAbort, {once: true});
    timer = setTimer(() => finish({ok: false, reason: 'timeout'}), timeoutMs);
    cancelWait = whenSlotFree(() => { if (!settled) start(); else releaseSlot(); });
    if (settled) cancelWait();
  });
}

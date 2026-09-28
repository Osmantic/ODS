// Bounded, query-directed excerpts of page text the run read successfully.
// Pure text helpers: no I/O and no imports, so the read ledger
// (completion-assurance.mjs) and the stop synthesis can both use them.

const MAX_SOURCE_CHARS = 400000;
const CHUNK_CHARS = 400;
const STOPWORDS = new Set(('the and for with from that this these those into onto over under your you are was were ' +
  'will would should could can not but any all each per via use using one two three four five than then them they ' +
  'their there here what when where which while who how why also only just more most less such same both other ' +
  'about after before between find return state find actually search live web open sources source page pages ' +
  'que para com por uma um dos das nos nas pelo pela seu sua sobre como mais').split(' '));

// Page-provided text arrives inside untrusted-content markers: web_fetch adds
// a "Source: …" header and "---" after the start marker, targeted extraction
// does not. Return the wrapped bodies in order, or the text unchanged.
export function externalContentBody(value) {
  if (typeof value !== 'string') return '';
  const text = value.length > MAX_SOURCE_CHARS ? value.slice(0, MAX_SOURCE_CHARS) : value;
  if (!text.includes('<<<EXTERNAL_UNTRUSTED_CONTENT')) return text;
  const bodies = [];
  const pattern = /<<<EXTERNAL_UNTRUSTED_CONTENT[^\n]*>>>\n(?:Source:[^\n]*\n(?:[A-Za-z]+:[^\n]*\n)*---\n)?([\s\S]*?)\n?<<<END_EXTERNAL_UNTRUSTED_CONTENT[^\n]*>>>/g;
  for (const match of text.matchAll(pattern)) bodies.push(match[1]);
  return bodies.join('\n');
}

// Distinct lowercase words of the owner's request (and a tool query), minus
// filler: words of three or more letters, and any token with a digit.
export function requestTerms(...texts) {
  const terms = new Set();
  for (const text of texts) {
    if (typeof text !== 'string') continue;
    for (const word of text.slice(0, 12000).toLowerCase().normalize('NFKC').match(/[\p{L}\p{N}]+/gu) ?? []) {
      if ((/\p{N}/u.test(word) && word.length >= 2) || (word.length >= 3 && !STOPWORDS.has(word))) terms.add(word);
      if (terms.size >= 64) return terms;
    }
  }
  return terms;
}

// A quantity with a unit or currency: the facts a comparison usually needs.
const QUANTITY = /(?:[$€£]\s?\d|\d(?:[\d.,]*)\s?(?:%|gb|mb|tb|w|watts?|mhz|ghz|fps|ms|hz|mm|in|kg|lbs?|usd|eur|gbps|nm)\b)/i;

// The lines (and long-line chunks) that carry the most request terms or
// quantities, kept in page order up to `max` characters; the page's opening
// lines when nothing scores. Markdown link targets are dropped, repeated
// lines count once, short lines without a quantity weigh half (navigation,
// labels), and spec rows stay whole: a short label keeps the short value line
// after it, and a value line keeps its label. Gaps are marked with an ellipsis.
export function pageExcerpt(value, terms, max = 1800) {
  const text = externalContentBody(value).replace(/\r/g, '').replace(/!?\[([^\]]{0,300})\]\([^)\s]{0,1000}\)/g, '$1')
    .replace(/[ \t\f\v]+/g, ' ');
  const chunks = [];
  for (const raw of text.split(/\n+/)) {
    const line = raw.replace(/^[\s#>*|-]+|[\s|]+$/g, '').trim();
    if (!/[\p{L}\p{N}]/u.test(line)) continue;
    for (let start = 0; start < line.length && chunks.length < 2000; start += CHUNK_CHARS) chunks.push(line.slice(start, start + CHUNK_CHARS));
  }
  if (!chunks.length) return '';
  const wanted = terms instanceof Set ? terms : new Set();
  const seen = new Set();
  const scored = chunks.map((chunk, index) => {
    const repeated = seen.has(chunk);
    seen.add(chunk);
    const words = new Set(chunk.toLowerCase().normalize('NFKC').match(/[\p{L}\p{N}]+/gu) ?? []);
    let score = 0;
    for (const term of wanted) if (words.has(term)) score += 1;
    const quantity = QUANTITY.test(chunk);
    if (quantity) score += 2;
    if (chunk.length < 40 && !quantity) score /= 2;
    return {index, chunk, score: repeated ? 0 : score, quantity, short: chunk.length < 60};
  });
  let ranked = scored.filter(entry => entry.score > 0).sort((a, b) => b.score - a.score || a.index - b.index);
  if (!ranked.length) ranked = scored;
  const picked = new Map();
  let used = 0;
  const take = entry => {
    if (!entry || picked.has(entry.index) || used + entry.chunk.length + 2 > max) return false;
    picked.set(entry.index, entry);
    used += entry.chunk.length + 2;
    return true;
  };
  // A label is a short line without a quantity; its values are the short
  // numeric lines right after it (up to three).
  const numeric = entry => Boolean(entry?.short && /\d/.test(entry.chunk));
  for (const entry of ranked) {
    if (!take(entry) || !entry.short) continue;
    const before = scored[entry.index - 1];
    if (entry.quantity && before?.short && !/\d/.test(before.chunk)) take(before);
    if (!entry.quantity) {
      for (let next = entry.index + 1; next <= entry.index + 3 && numeric(scored[next]); next++) take(scored[next]);
    }
  }
  if (!picked.size) return chunks[0].slice(0, max);
  const ordered = [...picked.values()].sort((a, b) => a.index - b.index);
  return ordered.map((entry, position) => (position > 0 && entry.index !== ordered[position - 1].index + 1 ? '…\n' : '') + entry.chunk)
    .join('\n');
}

// One closed owner sentence that limits a repeat publication of a uniquely
// named index.html to the case where that file changed after the owner's
// explicit first publication (native goal c211ecda, 2026-11-18):
//   "If that post-publication hash check did not change the file, do not republish it."
//   "If the file is unchanged, do not republish it."
// Pure syntax only. It never authorizes publication by itself: the guard
// evaluates the remaining owner words with its existing stance, deferral and
// request rules, and the host decides each repeat from its one captured
// snapshot. Every other wording, including any unconditioned "do not
// republish", keeps the existing refusal.

const PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const CONDITION = new RegExp(String.raw`^if\s+(?:` +
  String.raw`(?:that|the)\s+post-publication\s+(?:hash\s+|sha-?256\s+)?(?:check|verification)\s+(?:did\s+not|didn['’]t)\s+change\s+(?:the|that)\s+(?:file|page|entry)` +
  String.raw`|(?:the|that)\s+(?:file|page|entry)\s+(?:is|was|remains|stays)\s+unchanged` +
  String.raw`|(?:the|that)\s+(?:file|page|entry)\s+(?:did\s+not|didn['’]t)\s+change` +
  String.raw`)\s*,\s*(?:then\s+)?(?:do\s+not|don['’]t)\s+republish\s+it$`, 'i');
const REPUBLISH_WORD = /\brepublish/i;
const HTML_PATH = /(?:^|[^A-Za-z0-9._/-])((?:[A-Za-z0-9][A-Za-z0-9._-]{0,127}\/){0,11}[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.html?)(?![A-Za-z0-9_/-])/gi;

// The same untrusted spans the owner preview stance ignores: fenced code,
// blockquotes and quoted text neither request nor constrain publication.
function ownerProse(text) {
  return text.replace(/(?:\x60{3}|~{3})[\s\S]*?(?:\x60{3}|~{3})/g, ' ')
    .replace(/^\s*>[^\n]*/gm, ' ')
    .replace(/"[^"\n]*"|\x60[^\x60\n]*\x60/g, ' ');
}

function sentences(text) {
  const out = [];
  let start = 0;
  for (const boundary of [...text.matchAll(/[!?;\n]+|\.(?=\s|$)/g), {index: text.length, 0: ''}]) {
    out.push({start, end: boundary.index, text: text.slice(start, boundary.index).trim()});
    start = boundary.index + boundary[0].length;
  }
  return out.filter(sentence => sentence.text);
}

// Exactly one directory-qualified index.html, possibly repeated, and no other
// HTML name; a bare "index.html" refers to that same entry.
function namedEntryDirectory(text) {
  const qualified = new Set();
  for (const match of text.replace(/\bhttps?:\/\/[^\s<>"'\x60]+/gi, ' ').matchAll(HTML_PATH)) {
    const path = match[1];
    const parts = path.split('/');
    if (parts.at(-1) !== 'index.html') return undefined;
    if (parts.length > 1) qualified.add(path);
  }
  if (qualified.size !== 1) return undefined;
  const directory = [...qualified][0].slice(0, -'/index.html'.length);
  const parts = directory.split('/');
  return parts.length <= 12 && parts.every(part => !['.', '..'].includes(part) && PATH_COMPONENT.test(part))
    ? directory : undefined;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// The conditional sentence, the named entry directory, the independent
// earlier publication request, and the owner prose without that sentence.
// undefined unless every closed requirement holds.
export function extractConditionalRepublication(text) {
  if (typeof text !== 'string' || !text.trim() || text.length > 65536) return undefined;
  const prose = ownerProse(text);
  const all = sentences(prose);
  const conditions = all.filter(sentence => CONDITION.test(sentence.text));
  if (conditions.length !== 1) return undefined;
  const condition = conditions[0];
  // Any other mention of republication stays with the existing stance rules.
  if (all.some(sentence => sentence !== condition && REPUBLISH_WORD.test(sentence.text))) return undefined;
  const relativeDirectory = namedEntryDirectory(prose);
  if (!relativeDirectory) return undefined;
  const target = escapeRegExp(relativeDirectory);
  const requestPattern = new RegExp(String.raw`^(?:(?:please|now|then|also|and|finally)[,\s]+)*publish\s+` +
    String.raw`(?:the\s+(?:site|page|folder|directory|project)\s+)?${target}(?:\/index\.html)?(?=$|[\s,])`, 'i');
  const request = all.find(sentence => sentence.end <= condition.start && requestPattern.test(sentence.text));
  if (!request) return undefined;
  const remaining = prose.slice(0, condition.start) + ' ' + prose.slice(condition.end);
  return Object.freeze({
    relativeDirectory,
    entryFile: 'index.html',
    condition: condition.text,
    request: request.text,
    afterRequest: remaining.slice(request.end),
    remaining,
  });
}

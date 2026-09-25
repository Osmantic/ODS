// Edit-miss recovery: a closest-text note for each missed edit.
//
// Fleet evidence (round 078, Mac mini, Qwen3.5-9B, website_edit): a ten-edit
// batch failed only because edits[8] indented `<footer>` by four spaces where
// line 275 of the file has two. OpenClaw names only the index ("Could not
// find edits[8]"), and its single-edit error shows the first 800 characters
// of the file, so the model re-read the file and rewrote all of it (3,138
// generated tokens). This module names the closest current text for each
// missed edit and says which edits matched, so that the retry is the same
// edit call with the miss corrected instead of a rewrite of the whole file.
// It keeps no state between calls: OpenClaw may run the tool calls of one
// model message in parallel, and every note describes the file as it is when
// its own call completes.
//
// Matching mirrors OpenClaw 2026.6.33 src/agents/sessions/tools/edit.ts and
// its diff helpers (MIT, Copyright (c) 2026 OpenClaw Foundation): BOM strip,
// LF normalization, exact match, then normalizeForFuzzyMatch with uniqueness.
// It only classifies. Every change still runs through OpenClaw's own edit
// tool, all edits of one call together or none.

export const EDIT_NOTE_PREFIX = '[ODS Pixel edit]';
const MAX_NOTED_MISSES = 3;
const MAX_EXCERPT_LINES = 12;
const MAX_EXCERPT_CHARS = 1000;
const MIN_SIMILARITY = 0.6;

// OpenClaw's two not-found messages. The single-edit form is followed by
// "\nCurrent file contents:\n" and the first 800 characters of the file.
const EDIT_MISS = /Could not find (?:edits\[\d+\]|the exact text) in /;
const MISMATCH_HEAD = /(Could not find the exact text in [^\n]*)\nCurrent file contents:\n[\s\S]*$/;

export function normalizeToLF(text) {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

export function normalizeForFuzzyMatch(text) {
  return text.normalize('NFKC').split('\n').map(line => line.trimEnd()).join('\n')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'").replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-')
    .replace(/[\u00A0\u2002-\u200A\u202F\u205F\u3000]/g, ' ');
}

const stripBom = content => content.startsWith('\uFEFF') ? content.slice(1) : content;
const fileText = content => normalizeToLF(stripBom(content));
const occurrences = (haystack, needle) => needle ? haystack.split(needle).length - 1 : 0;

// The failed edit's error text, from a direct receipt, a Tool Search
// envelope or an SDK error. Undefined unless it is an OpenClaw not-found miss.
export function editMissError(...values) {
  for (const value of values) {
    if (typeof value === 'string' && EDIT_MISS.test(value)) return value;
  }
  return undefined;
}

// The model's replacement pairs, or undefined for anything else. OpenClaw
// hands before_tool_call its prepared `edits` form; the legacy single pair is
// accepted for a Tool Search envelope, whose arguments are not prepared yet.
export function editPairs(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return undefined;
  const pairs = Array.isArray(params.edits) ? [...params.edits] : [];
  if (typeof params.oldText === 'string' || typeof params.newText === 'string') {
    pairs.push({oldText: params.oldText, newText: params.newText});
  }
  if (!pairs.length || !pairs.every(pair => pair && typeof pair === 'object' && !Array.isArray(pair) &&
      typeof pair.oldText === 'string' && typeof pair.newText === 'string')) return undefined;
  return pairs.map(pair => ({oldText: pair.oldText, newText: pair.newText}));
}

// OpenClaw's outcome for every edit of one call against one file: match,
// missing, ambiguous (several places), overlap, noop or empty. Positions are
// in the content space OpenClaw would use for the whole call.
export function classifyEditHunks(content, edits) {
  const normalized = fileText(content);
  const fuzzyContent = normalizeForFuzzyMatch(normalized);
  const hunks = edits.map((edit, index) => ({index, oldText: normalizeToLF(edit.oldText), newText: normalizeToLF(edit.newText)}));
  const result = new Array(hunks.length);
  const real = [];
  for (const hunk of hunks) {
    if (!hunk.oldText) result[hunk.index] = {index: hunk.index, status: 'empty'};
    else if (hunk.oldText === hunk.newText || normalizeForFuzzyMatch(hunk.oldText) === normalizeForFuzzyMatch(hunk.newText)) {
      // OpenClaw validates a no-op edit alone and then skips it.
      const found = normalized.includes(hunk.oldText) || fuzzyContent.includes(normalizeForFuzzyMatch(hunk.oldText));
      result[hunk.index] = {index: hunk.index, status: found ? 'noop' : 'missing'};
    } else real.push(hunk);
  }
  const fuzzy = real.some(hunk => !normalized.includes(hunk.oldText) &&
    fuzzyContent.includes(normalizeForFuzzyMatch(hunk.oldText)));
  const base = fuzzy ? fuzzyContent : normalized;
  for (const hunk of real) {
    const fuzzyOld = normalizeForFuzzyMatch(hunk.oldText);
    let start = base.indexOf(hunk.oldText), length = hunk.oldText.length;
    if (start < 0) { start = fuzzyContent.indexOf(fuzzyOld); length = fuzzyOld.length; }
    if (start < 0) { result[hunk.index] = {index: hunk.index, status: 'missing'}; continue; }
    const count = occurrences(fuzzyContent, fuzzyOld);
    result[hunk.index] = count > 1 ? {index: hunk.index, status: 'ambiguous', count}
      : {index: hunk.index, status: 'match', start, end: start + length};
  }
  const matched = result.filter(hunk => hunk.status === 'match').sort((left, right) => left.start - right.start);
  for (let index = 1; index < matched.length; index++) {
    if (matched[index - 1].end > matched[index].start) {
      matched[index - 1].status = 'overlap';
      matched[index].status = 'overlap';
    }
  }
  return {space: fuzzy ? 'fuzzy' : 'exact', hunks: result};
}

const squash = line => line.normalize('NFKC').replace(/\s+/g, ' ').trim();

function bigrams(text) {
  const counts = new Map();
  for (let index = 0; index < text.length - 1; index++) {
    const pair = text.slice(index, index + 2);
    counts.set(pair, (counts.get(pair) ?? 0) + 1);
  }
  return counts;
}

function similarity(left, right) {
  if (!left || !right) return 0;
  if (left === right) return 1;
  const a = bigrams(left), b = bigrams(right);
  let shared = 0;
  for (const [pair, count] of a) shared += Math.min(count, b.get(pair) ?? 0);
  return (2 * shared) / (Math.max(1, left.length - 1) + Math.max(1, right.length - 1));
}

// Current-file regions (0-based inclusive line ranges) that the missed
// oldText most likely meant: its first and last non-blank lines, compared
// with whitespace collapsed; otherwise the single most similar line
// (similarity at least 0.6), widened to the oldText's line count. At most two.
export function nearMissRegions(content, oldText) {
  const lines = fileText(content).split('\n');
  const want = normalizeToLF(oldText).split('\n');
  const anchors = want.map((line, index) => ({index, text: squash(line)})).filter(anchor => anchor.text);
  if (!anchors.length) return [];
  const first = anchors[0], last = anchors.at(-1);
  const regions = [];
  for (let start = 0; start < lines.length && regions.length < 3; start++) {
    if (squash(lines[start]) !== first.text) continue;
    if (anchors.length === 1) { regions.push({start, end: start}); continue; }
    const limit = Math.min(lines.length, start + want.length + 10);
    for (let end = start + 1; end < limit; end++) {
      if (squash(lines[end]) === last.text) { regions.push({start, end}); break; }
    }
  }
  if (regions.length) return regions.slice(0, 2);
  const target = anchors.reduce((best, anchor) => anchor.text.length > best.text.length ? anchor : best);
  let best = 0, at = [];
  lines.forEach((line, index) => {
    const score = similarity(squash(line), target.text);
    if (score > best + 1e-9) { best = score; at = [index]; }
    else if (Math.abs(score - best) <= 1e-9 && at.length < 2) at.push(index);
  });
  if (best < MIN_SIMILARITY) return [];
  return at.map(index => {
    const start = Math.max(0, index - target.index);
    return {start, end: Math.min(lines.length - 1, start + want.length - 1), similar: true};
  });
}

function excerpt(lines, region, maxLines = MAX_EXCERPT_LINES, maxChars = MAX_EXCERPT_CHARS) {
  const end = Math.min(region.end, region.start + maxLines - 1);
  const rows = [];
  let size = 0;
  for (let index = region.start; index <= end; index++) {
    let row = `${index + 1}|${lines[index]}`;
    if (size + row.length + 1 > maxChars) {
      if (rows.length) break;
      row = `${row.slice(0, maxChars - 1)}…`;
    }
    rows.push(row);
    size += row.length + 1;
  }
  return {text: rows.join('\n'), start: region.start + 1, end: region.start + rows.length};
}

const leadingWhitespace = line => /^[ \t]*/.exec(line)[0];
function describeLead(lead) {
  const spaces = [...lead].filter(char => char === ' ').length, tabs = lead.length - spaces;
  if (!lead) return 'no leading whitespace';
  const parts = [spaces && `${spaces} leading space${spaces === 1 ? '' : 's'}`, tabs && `${tabs} leading tab${tabs === 1 ? '' : 's'}`];
  return parts.filter(Boolean).join(' and ');
}
// "yours has 4" when both sides are plain spaces, as in the Mac evidence.
function describeLeads(file, yours) {
  const spacesOnly = lead => /^ +$/.test(lead);
  return {file: describeLead(file), yours: spacesOnly(file) && spacesOnly(yours) ? String(yours.length) : describeLead(yours)};
}

// Why the region's lines are not the oldText, when the region has the same
// number of lines. The first oldText line may start mid-line and the last may
// end mid-line, as OpenClaw matches substrings; trailing spaces never count.
export function describeDifference(regionLines, oldText) {
  const want = normalizeToLF(oldText).split('\n');
  if (regionLines.length !== want.length) return undefined;
  const fits = (line, wanted, index) => {
    const have = line.trimEnd(), need = wanted.trimEnd();
    if (want.length === 1) return have.includes(need);
    if (index === 0) return have.endsWith(need);
    if (index === want.length - 1) return have.startsWith(need);
    return have === need;
  };
  const index = regionLines.findIndex((line, position) => !fits(line, want[position], position));
  if (index < 0) return undefined;
  if (regionLines.every((line, position) => line.trim() === want[position].trim())) {
    return {kind: 'indentation', line: index, ...describeLeads(leadingWhitespace(regionLines[index]), leadingWhitespace(want[index]))};
  }
  if (regionLines.every((line, position) => squash(line) === squash(want[position]))) return {kind: 'spacing', line: index};
  return {kind: 'content', line: index};
}

// Lines (1-based) where newText already is, when oldText is absent.
export function alreadyAppliedLines(content, oldText, newText) {
  const normalized = fileText(content);
  const wanted = normalizeToLF(newText);
  if (wanted.trim().length < 8) return undefined;
  const fuzzy = normalizeForFuzzyMatch(normalized);
  if (normalized.includes(normalizeToLF(oldText)) || fuzzy.includes(normalizeForFuzzyMatch(normalizeToLF(oldText)))) return undefined;
  let space = normalized, at = normalized.indexOf(wanted), needle = wanted;
  if (at < 0) { space = fuzzy; needle = normalizeForFuzzyMatch(wanted); at = fuzzy.indexOf(needle); }
  if (at < 0) return undefined;
  const start = occurrences(space.slice(0, at), '\n') + 1;
  return {start, end: start + occurrences(needle.replace(/\n+$/, ''), '\n')};
}

// "edits[0-7], edits[9]" for a sorted index list.
export function indexRanges(indices) {
  const runs = [];
  for (const index of indices) {
    const run = runs.at(-1);
    if (run && index === run[1] + 1) run[1] = index;
    else runs.push([index, index]);
  }
  return runs.map(([first, last]) => first === last ? `edits[${first}]` : `edits[${first}-${last}]`).join(', ');
}

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;
const lineRange = ({start, end}) => start === end ? `line ${start}` : `lines ${start}-${end}`;

// `label` names the edit: "The oldText" for a single edit, else "edits[N]".
function missSentences(lines, content, hunk, edit, label, single) {
  if (hunk.status === 'ambiguous') {
    return [`${label} matches ${hunk.count} places; include one or two surrounding lines so it matches exactly one.`];
  }
  if (hunk.status === 'overlap') return [`${label} overlaps another edit in this call; merge the two into one edit.`];
  if (hunk.status === 'empty') return [`${label} has an empty oldText; copy it from the current file.`];
  const applied = alreadyAppliedLines(content, edit.oldText, edit.newText);
  if (applied) {
    return [`${single ? "This edit's" : label} newText is already in the file at ${lineRange(applied)}, so this change may already be applied; check those lines before editing again.`];
  }
  const regions = nearMissRegions(content, edit.oldText);
  if (!regions.length) {
    return [`${label} was not found, and no similar text is in the current file. Read the lines you mean to change before retrying.`];
  }
  if (regions.length > 1) {
    const shown = regions.map(region => excerpt(lines, region, 6, MAX_EXCERPT_CHARS / 2));
    return [`${label} was not found. Similar current text appears in more than one place, including ${lineRange(shown[0])} and ${lineRange(shown[1])}:`,
      shown[0].text, shown[1].text, 'Include enough lines to match only the intended place.'];
  }
  const shown = excerpt(lines, regions[0]);
  const sentences = [`${label} was not found. ${regions[0].similar ? 'Most similar' : 'Closest'} current text, ${lineRange(shown)}:`, shown.text];
  const difference = describeDifference(lines.slice(regions[0].start, regions[0].end + 1), edit.oldText);
  const at = difference ? regions[0].start + difference.line + 1 : undefined;
  if (difference?.kind === 'indentation') {
    sentences.push(`Your oldText differs only in indentation (line ${at} has ${difference.file}; yours has ${difference.yours}).`);
  } else if (difference?.kind === 'spacing') {
    sentences.push(`Your oldText differs only in spacing within line ${at}.`);
  } else if (difference?.kind === 'content') {
    sentences.push(`Your oldText differs from line ${at}.`);
  }
  return sentences;
}

/// Recovery for one failed edit call against the current file content: the
// closest current text for up to three missed edits, and what to send next.
// Nothing is kept between calls. OpenClaw applies the edits of one call
// together or not at all, so the retry is the same call with the misses
// corrected; the note says so when other edits of the call matched.
export function editRecovery(content, edits) {
  if (typeof content !== 'string' || !Array.isArray(edits) || !edits.length) return undefined;
  const lines = fileText(content).split('\n');
  const {hunks} = classifyEditHunks(content, edits);
  const single = edits.length === 1;
  const problems = hunks.filter(hunk => !['match', 'noop'].includes(hunk.status));
  if (!problems.length) return undefined;
  const sentences = [single ? `${EDIT_NOTE_PREFIX} Nothing was changed.`
    : `${EDIT_NOTE_PREFIX} Nothing was changed; edits in one call apply together or not at all.`];
  for (const hunk of problems.slice(0, MAX_NOTED_MISSES)) {
    sentences.push(...missSentences(lines, content, hunk, edits[hunk.index], single ? 'The oldText' : `edits[${hunk.index}]`, single));
  }
  if (problems.length > MAX_NOTED_MISSES) {
    sentences.push(`${plural(problems.length - MAX_NOTED_MISSES, 'more edit')} also did not match: ${indexRanges(problems.slice(MAX_NOTED_MISSES).map(hunk => hunk.index))}.`);
  }
  // A missing edit whose newText is already in the file is left out, not corrected.
  const applied = problems.filter(hunk => hunk.status === 'missing' &&
    alreadyAppliedLines(content, edits[hunk.index].oldText, edits[hunk.index].newText));
  const correctable = problems.filter(hunk => !applied.includes(hunk));
  const others = edits.length - problems.length;
  if (single) {
    sentences.push(correctable.length ? 'Copy oldText exactly from the current file and retry this one edit. Do not rewrite the whole file.'
      : 'Do not rewrite the whole file.');
  } else if (!others) {
    sentences.push(correctable.length ? 'Retry the edit with corrected oldText copied from the current file. Do not rewrite the whole file.'
      : 'Do not rewrite the whole file.');
  } else {
    const changes = [
      ...(correctable.length ? [`a corrected ${indexRanges(correctable.map(hunk => hunk.index))}, copying oldText from the current file`] : []),
      ...(applied.length ? [`without ${indexRanges(applied.map(hunk => hunk.index))}`] : []),
    ];
    const one = others === 1;
    sentences.push(`The other ${plural(others, 'edit')} match${one ? 'es' : ''} the current file but ${one ? 'was' : 'were'} not applied. ` +
      `Send one edit for this file again with ${one ? 'it' : 'all of them'} unchanged and ${changes.join(' and ')}. Do not rewrite the whole file.`);
  }
  return {note: sentences.join('\n')};
}

// OpenClaw's single-edit miss appends the first 800 characters of the file.
// Once the closest-text note is attached, drop that head from the persisted
// result, inside JSON text blocks and details alike.
export function withoutMismatchHead(value, depth = 0) {
  if (typeof value === 'string') {
    const trimmed = value.replace(MISMATCH_HEAD, '$1');
    if (trimmed !== value) return trimmed;
    if (depth === 0 || !/^\s*[{[]/.test(value) || !value.includes('Current file contents:')) return value;
    try {
      const parsed = JSON.parse(value);
      const next = withoutMismatchHead(parsed, depth + 1);
      return next === parsed ? value : JSON.stringify(next, null, /^\s*[{[]\n/.test(value) ? 2 : undefined);
    } catch { return value; }
  }
  if (depth > 10 || !value || typeof value !== 'object') return value;
  if (Array.isArray(value)) {
    const next = value.map(item => withoutMismatchHead(item, depth + 1));
    return next.some((item, index) => item !== value[index]) ? next : value;
  }
  let changed = false;
  const next = {};
  for (const [key, item] of Object.entries(value)) {
    next[key] = withoutMismatchHead(item, depth + 1);
    if (next[key] !== item) changed = true;
  }
  return changed ? next : value;
}

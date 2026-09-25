// Edit-miss recovery: a closest-text note, and one held retry.
//
// Fleet evidence (round 078, Mac mini, Qwen3.5-9B, website_edit): a ten-edit
// batch failed only because edits[8] indented `<footer>` by four spaces where
// line 275 of the file has two. OpenClaw names only the index ("Could not
// find edits[8]"), and its single-edit error shows the first 800 characters
// of the file, so the model re-read the file and rewrote all of it (3,138
// generated tokens). This module names the closest current text for each
// missed edit, and keeps the edits that matched so that the retry is one
// corrected edit instead of the whole batch or the whole file.
//
// Matching mirrors OpenClaw 2026.6.33 src/agents/sessions/tools/edit.ts and
// its diff helpers (MIT, Copyright (c) 2026 OpenClaw Foundation): BOM strip,
// LF normalization, exact match, then normalizeForFuzzyMatch with uniqueness.
// It only classifies. Every change still runs through OpenClaw's own edit
// tool, all edits of one call together or none.

import { createHash } from 'node:crypto';

export const EDIT_NOTE_PREFIX = '[ODS Pixel edit]';
export const MAX_HELD_HUNKS = 32;
export const MAX_HELD_BYTES = 64 * 1024;
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

export function sha256Hex(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

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

// Recovery for one failed edit call against the current file content.
// `held` describes hunks Pixel added from an earlier rejected call (appended
// after the model's `own` edits). With `retainable` (the file is unchanged
// and this was their first merged attempt) they stay held for one more
// attempt when only the model's own edits missed; otherwise they are
// reported as no longer held. Returns {note, hold, retained}: hold lists the
// model's matched edits to keep, retained says the held edits stay held.
export function editRecovery(content, edits, {held} = {}) {
  if (typeof content !== 'string' || !Array.isArray(edits) || !edits.length) return undefined;
  const lines = fileText(content).split('\n');
  const {hunks} = classifyEditHunks(content, edits);
  const ownCount = held ? held.own : edits.length;
  const own = hunks.slice(0, ownCount);
  const single = ownCount === 1;
  const problems = own.filter(hunk => !['match', 'noop'].includes(hunk.status));
  if (!problems.length) return undefined;
  const sentences = [held ? `${EDIT_NOTE_PREFIX} Nothing was changed; your ${single ? 'edit' : 'edits'} and the held edits apply together or not at all.`
    : single ? `${EDIT_NOTE_PREFIX} Nothing was changed.`
      : `${EDIT_NOTE_PREFIX} Nothing was changed; edits in one call apply together or not at all.`];
  for (const hunk of problems.slice(0, MAX_NOTED_MISSES)) {
    sentences.push(...missSentences(lines, content, hunk, edits[hunk.index], single ? 'The oldText' : `edits[${hunk.index}]`, single));
  }
  if (problems.length > MAX_NOTED_MISSES) {
    sentences.push(`${plural(problems.length - MAX_NOTED_MISSES, 'more edit')} also did not match: ${indexRanges(problems.slice(MAX_NOTED_MISSES).map(hunk => hunk.index))}.`);
  }
  const matched = own.filter(hunk => hunk.status === 'match');
  const toFix = problems.filter(hunk => hunk.status === 'missing' || hunk.status === 'ambiguous' || hunk.status === 'empty' || hunk.status === 'overlap');
  const correctable = toFix.filter(hunk => !(hunk.status === 'missing' && alreadyAppliedLines(content, edits[hunk.index].oldText, edits[hunk.index].newText)));
  if (held) {
    const count = held.indices.length, one = count === 1;
    const retained = Boolean(held.retainable) && correctable.length > 0 &&
      hunks.slice(ownCount).every(hunk => hunk.status === 'match');
    if (retained) {
      const fix = single ? 'a corrected oldText' : `a corrected ${indexRanges(correctable.map(hunk => hunk.index))}`;
      sentences.push(`The ${plural(count, 'held edit')} from the earlier call ${one ? 'was' : 'were'} included and not applied; ` +
        `${one ? 'it is' : 'they are'} still held for one more attempt. Send one edit for this file with ${fix}` +
        `${matched.length ? ' and your other edits from this call' : ''}, copying oldText from the current file; ` +
        `Pixel applies the held ${one ? 'edit' : 'edits'} with it. Do not rewrite the whole file.`);
    } else {
      sentences.push(`The ${plural(count, 'held edit')} from the earlier call ${one ? 'was' : 'were'} included and not applied; ${one ? 'it is' : 'they are'} no longer held, so include ${one ? 'it' : 'them'} again with your corrected edit. Do not rewrite the whole file.`);
    }
    return {note: sentences.join('\n'), hold: undefined, retained};
  }
  const heldBytes = matched.reduce((size, hunk) => size + Buffer.byteLength(edits[hunk.index].oldText) + Buffer.byteLength(edits[hunk.index].newText), 0);
  const hold = matched.length && correctable.length && !problems.some(hunk => hunk.status === 'overlap') &&
    matched.length <= MAX_HELD_HUNKS && heldBytes <= MAX_HELD_BYTES
    ? matched.map(hunk => ({index: hunk.index, oldText: edits[hunk.index].oldText, newText: edits[hunk.index].newText}))
    : undefined;
  if (hold) {
    const fix = indexRanges(correctable.map(hunk => hunk.index));
    sentences.push(`The other ${plural(hold.length, 'edit')} match${hold.length === 1 ? 'es' : ''} the current file and ${hold.length === 1 ? 'is' : 'are'} held. ` +
      `Send one edit for this file with only a corrected ${fix}, copying oldText from the lines above; ` +
      `Pixel applies the ${plural(hold.length, 'held edit')} with it in the same all-or-nothing edit. Do not rewrite the whole file.`);
  } else if (correctable.length) {
    sentences.push(single ? 'Copy oldText exactly from the current file and retry this one edit. Do not rewrite the whole file.'
      : 'Retry one edit with corrected oldText copied from the current file. Do not rewrite the whole file.');
  } else {
    sentences.push('Do not rewrite the whole file.');
  }
  return {note: sentences.join('\n'), hold, retained: false};
}

// The text an edit adds: its newText without the leading and trailing lines
// it shares with its oldText, whitespace collapsed.
const flatten = text => text.normalize('NFKC').replace(/\s+/g, ' ').trim();
const MIN_DUPLICATE_CHARS = 12;
function insertedText(oldText, newText) {
  const before = normalizeToLF(oldText).split('\n'), after = normalizeToLF(newText).split('\n');
  let head = 0;
  while (head < before.length && head < after.length && squash(before[head]) === squash(after[head])) head++;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head &&
    squash(before[before.length - 1 - tail]) === squash(after[after.length - 1 - tail])) tail++;
  return flatten(after.slice(head, after.length - tail).join('\n'));
}
// The model's own edit adds what a held edit adds (re-anchored elsewhere),
// so applying both would insert it twice.
function addsSameText(held, own) {
  const added = insertedText(held.oldText, held.newText);
  return added.length >= MIN_DUPLICATE_CHARS && flatten(own.newText).includes(added) && !flatten(own.oldText).includes(added);
}

// The next edit of the same file in the same run, with the held edits added
// after the model's own. The new edit always wins; a held edit is left out
// when the new call re-sends its oldText (resent), when one of the new edits
// already adds the same text elsewhere (duplicate), or when it no longer
// matches alone and uniquely in the merged call, for example because a new
// edit overlaps its region (overlap). `edits` is undefined when no held edit
// remains.
export function mergeHeldHunks(content, ownEdits, held) {
  if (typeof content !== 'string' || !Array.isArray(ownEdits) || !ownEdits.length || !Array.isArray(held) || !held.length) return undefined;
  const resent = new Set(ownEdits.map(edit => normalizeToLF(edit.oldText)));
  const duplicate = [], overlap = [];
  let kept = [];
  for (const hunk of held) {
    if (resent.has(normalizeToLF(hunk.oldText))) continue;
    if (ownEdits.some(edit => addsSameText(hunk, edit))) duplicate.push(hunk.index);
    else kept.push(hunk);
  }
  for (let pass = 0; pass <= held.length && kept.length; pass++) {
    const {hunks} = classifyEditHunks(content, [...ownEdits, ...kept]);
    const next = kept.filter((_, index) => hunks[ownEdits.length + index].status === 'match');
    if (next.length === kept.length) break;
    overlap.push(...kept.filter(hunk => !next.includes(hunk)).map(hunk => hunk.index));
    kept = next;
  }
  const byIndex = (left, right) => left - right;
  return {edits: kept.length ? [...ownEdits.map(edit => ({oldText: edit.oldText, newText: edit.newText})),
    ...kept.map(hunk => ({oldText: hunk.oldText, newText: hunk.newText}))] : undefined,
  held: kept.map(hunk => hunk.index), duplicate: duplicate.sort(byIndex), overlap: overlap.sort(byIndex)};
}

// Held edits the merge left out because of the new call (see mergeHeldHunks).
// An overlapped one is lost unless the model's own edit carries its change.
export function heldEditsSkipped({duplicate = [], overlap = []} = {}) {
  const sentences = [];
  const verb = indices => indices.length === 1 ? 'was' : 'were';
  if (overlap.length) {
    sentences.push(`Held ${indexRanges(overlap)} ${verb(overlap)} not applied because your edit changes the same text; ` +
      `re-send ${overlap.length === 1 ? 'it' : 'them'} if still needed.`);
  }
  if (duplicate.length) sentences.push(`Held ${indexRanges(duplicate)} ${verb(duplicate)} not applied because your edit already adds the same text.`);
  return sentences.length ? sentences.join(' ') : undefined;
}

export function mergedEditNote(ownCount, heldIndices, skipped) {
  const total = ownCount + heldIndices.length;
  const rest = heldEditsSkipped(skipped);
  return `${EDIT_NOTE_PREFIX} Applied ${plural(total, 'replacement')} in one edit: yours plus ${heldIndices.length} held from the rejected call (its ${indexRanges(heldIndices)}).${rest ? ` ${rest}` : ''}`;
}

// A hold that ended without being applied: the file's bytes changed (or can
// no longer be read) since the rejected call. `file` names it on the result
// of a call other than an edit of that file.
export function heldEditsChangedNote(count, file) {
  const one = count === 1;
  return `${EDIT_NOTE_PREFIX} The ${plural(count, 'held edit')}${file ? ` for ${file}` : ''} ${one ? 'was' : 'were'} not applied (file changed); ` +
    `re-send ${one ? 'it' : 'them'} if still needed.`;
}

// A merged call that failed for a reason the miss analysis cannot explain.
export function heldEditsDroppedNote(count) {
  const one = count === 1;
  return `${EDIT_NOTE_PREFIX} The ${plural(count, 'held edit')} from the earlier call ${one ? 'was' : 'were'} included and not applied; ` +
    `${one ? 'it is' : 'they are'} no longer held. Re-send ${one ? 'it' : 'them'} if still needed.`;
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

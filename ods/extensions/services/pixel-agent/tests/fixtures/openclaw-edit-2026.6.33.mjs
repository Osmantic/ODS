// Test double for OpenClaw 2026.6.33's built-in `edit` tool, copied from its
// dist (src/agents/sessions/tools/edit.ts and edit diff helpers; MIT,
// Copyright (c) 2026 OpenClaw Foundation). Kept verbatim in logic so the
// Pixel edit-recovery tests replay the real matching, error texts and
// all-or-nothing application, independently of plugin/edit-recovery.mjs.
import fs from 'node:fs';
import path from 'node:path';

function detectLineEnding(content) {
  const crlfIdx = content.indexOf('\r\n');
  const lfIdx = content.indexOf('\n');
  if (lfIdx === -1) return '\n';
  if (crlfIdx === -1) return '\n';
  return crlfIdx < lfIdx ? '\r\n' : '\n';
}
function normalizeToLF(text) { return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n'); }
function restoreLineEndings(text, ending) { return ending === '\r\n' ? text.replace(/\n/g, '\r\n') : text; }
function normalizeForFuzzyMatch(text) {
  return text.normalize('NFKC').split('\n').map((line) => line.trimEnd()).join('\n').replace(/[\u2018\u2019\u201A\u201B]/g, "'").replace(/[\u201C\u201D\u201E\u201F]/g, '"').replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-').replace(/[\u00A0\u2002-\u200A\u202F\u205F\u3000]/g, ' ');
}
class EditNoChangeError extends Error {}
function fuzzyFindText(content, oldText) {
  const exactIndex = content.indexOf(oldText);
  if (exactIndex !== -1) return {found: true, index: exactIndex, matchLength: oldText.length, usedFuzzyMatch: false};
  const fuzzyContent = normalizeForFuzzyMatch(content);
  const fuzzyOldText = normalizeForFuzzyMatch(oldText);
  const fuzzyIndex = fuzzyContent.indexOf(fuzzyOldText);
  if (fuzzyIndex === -1) return {found: false, index: -1, matchLength: 0, usedFuzzyMatch: false};
  return {found: true, index: fuzzyIndex, matchLength: fuzzyOldText.length, usedFuzzyMatch: true};
}
function stripBom(content) { return content.startsWith('\uFEFF') ? {bom: '\uFEFF', text: content.slice(1)} : {bom: '', text: content}; }
function countOccurrences(content, oldText) {
  return normalizeForFuzzyMatch(content).split(normalizeForFuzzyMatch(oldText)).length - 1;
}
function getNotFoundError(file, editIndex, totalEdits) {
  if (totalEdits === 1) return new Error(`Could not find the exact text in ${file}. The old text must match exactly including all whitespace and newlines.`);
  return new Error(`Could not find edits[${editIndex}] in ${file}. The oldText must match exactly including all whitespace and newlines.`);
}
function getDuplicateError(file, editIndex, totalEdits, occurrences) {
  if (totalEdits === 1) return new Error(`Found ${occurrences} occurrences of the text in ${file}. The text must be unique. Please provide more context to make it unique.`);
  return new Error(`Found ${occurrences} occurrences of edits[${editIndex}] in ${file}. Each oldText must be unique. Please provide more context to make it unique.`);
}
export function applyEditsToNormalizedContent(normalizedContent, edits, file) {
  const normalizedEdits = edits.map((edit) => ({oldText: normalizeToLF(edit.oldText), newText: normalizeToLF(edit.newText)}));
  for (let i = 0; i < normalizedEdits.length; i++) {
    if (normalizedEdits[i].oldText.length === 0) {
      throw new Error(normalizedEdits.length === 1 ? `oldText must not be empty in ${file}.` : `edits[${i}].oldText must not be empty in ${file}.`);
    }
  }
  const baseContent = normalizedEdits.map((edit) => fuzzyFindText(normalizedContent, edit.oldText)).some((match) => match.usedFuzzyMatch)
    ? normalizeForFuzzyMatch(normalizedContent) : normalizedContent;
  const matchedEdits = [];
  for (let i = 0; i < normalizedEdits.length; i++) {
    const edit = normalizedEdits[i];
    const matchResult = fuzzyFindText(baseContent, edit.oldText);
    if (!matchResult.found) throw getNotFoundError(file, i, normalizedEdits.length);
    const occurrences = countOccurrences(baseContent, edit.oldText);
    if (occurrences > 1) throw getDuplicateError(file, i, normalizedEdits.length, occurrences);
    matchedEdits.push({editIndex: i, matchIndex: matchResult.index, matchLength: matchResult.matchLength, newText: edit.newText});
  }
  matchedEdits.sort((a, b) => a.matchIndex - b.matchIndex);
  for (let i = 1; i < matchedEdits.length; i++) {
    const previous = matchedEdits[i - 1];
    const current = matchedEdits[i];
    if (previous.matchIndex + previous.matchLength > current.matchIndex) {
      throw new Error(`edits[${previous.editIndex}] and edits[${current.editIndex}] overlap in ${file}. Merge them into one edit or target disjoint regions.`);
    }
  }
  let newContent = baseContent;
  for (let i = matchedEdits.length - 1; i >= 0; i--) {
    const edit = matchedEdits[i];
    newContent = newContent.slice(0, edit.matchIndex) + edit.newText + newContent.slice(edit.matchIndex + edit.matchLength);
  }
  if (baseContent === newContent) {
    throw new EditNoChangeError(normalizedEdits.length === 1
      ? `No changes made to ${file}. The replacement produced identical content. This might indicate an issue with special characters or the text not existing as expected.`
      : `No changes made to ${file}. The replacements produced identical content.`);
  }
  return {baseContent, newContent};
}
function splitNoOpEdits(normalizedContent, edits, file) {
  const noOpEdits = [], realEdits = [];
  for (const edit of edits) {
    if (edit.oldText === edit.newText || normalizeForFuzzyMatch(edit.oldText) === normalizeForFuzzyMatch(edit.newText)) {
      applyEditsToNormalizedContent(normalizedContent, [{oldText: edit.oldText, newText: ''}], file);
      noOpEdits.push(edit);
    } else realEdits.push(edit);
  }
  return {noOpEdits, realEdits};
}
const EDIT_MISMATCH_MESSAGE = 'Could not find the exact text in';
const EDIT_MISMATCH_HINT_LIMIT = 800;
function appendMismatchHint(error, currentContent) {
  const snippet = currentContent.length <= EDIT_MISMATCH_HINT_LIMIT ? currentContent : `${currentContent.slice(0, EDIT_MISMATCH_HINT_LIMIT)}\n... (truncated)`;
  return new Error(`${error.message}\nCurrent file contents:\n${snippet}`);
}

// Runs one prepared edit call against `root` (the sandbox /workspace) and
// returns the tool result OpenClaw persists: success text, or its error
// envelope ({status:'error', tool:'edit', error}) as JSON text and details.
export function executeOpenClawEdit(root, params) {
  const file = params.path;
  const absolute = path.join(root, ...file.split('/'));
  const rawContent = fs.readFileSync(absolute).toString('utf-8');
  try {
    const {bom, text: content} = stripBom(rawContent);
    const originalEnding = detectLineEnding(content);
    const normalizedContent = normalizeToLF(content);
    const {realEdits} = splitNoOpEdits(normalizedContent, params.edits, file);
    if (realEdits.length === 0) return {content: [{type: 'text', text: `No changes made to ${file}. The replacement text is identical to the original.`}], details: {}};
    const {newContent} = applyEditsToNormalizedContent(normalizedContent, realEdits, file);
    fs.writeFileSync(absolute, bom + restoreLineEndings(newContent, originalEnding), 'utf-8');
    return {content: [{type: 'text', text: `Successfully replaced ${realEdits.length} block(s) in ${file}.`}], details: {diff: '', patch: ''}};
  } catch (error) {
    const thrown = error.message.includes(EDIT_MISMATCH_MESSAGE) ? appendMismatchHint(error, rawContent) : error;
    const details = {status: 'error', tool: 'edit', error: thrown.message};
    return {isError: true, content: [{type: 'text', text: JSON.stringify(details, null, 2)}], details};
  }
}

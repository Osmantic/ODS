import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {alreadyAppliedLines, classifyEditHunks, describeDifference, editMissError, editPairs, editRecovery, indexRanges,
  mergeHeldHunks, mergedEditNote, nearMissRegions, withoutMismatchHead} from '../plugin/edit-recovery.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {applyEditsToNormalizedContent, executeOpenClawEdit} from './fixtures/openclaw-edit-2026.6.33.mjs';
import {guardReplay, passthroughExecControl, removeWorkspaces, workspaceWith} from './fixtures/guard-replay.mjs';

// Fleet evidence: Mac mini, Qwen3.5-9B, round 078, website_edit. The
// ten-edit batch missed only edits[8] (four spaces before <footer>, where
// line 275 has two); the retry repeated the indentation and got the first
// 800 characters of the file; the model then rewrote the file (3,138 tokens).
const MAC = JSON.parse(fs.readFileSync(new URL('./fixtures/edit-miss-mac-round078.json', import.meta.url), 'utf8'));
const CORRECTED = {oldText: MAC.batch[8].oldText.replace('    <footer>', '  <footer>'),
  newText: MAC.batch[8].newText.replace('    <footer>', '  <footer>')};
const MAC_NOTE = [
  '[ODS Pixel edit] Nothing was changed; edits in one call apply together or not at all.',
  'edits[8] was not found. Closest current text, lines 275-277:',
  '275|  <footer>',
  '276|    <p>© 2026 Night Garden Event Series. All rights reserved.</p>',
  '277|  </footer>',
  'Your oldText differs only in indentation (line 275 has 2 leading spaces; yours has 4).',
  'The other 9 edits match the current file and are held. Send one edit for this file with only a corrected edits[8], ' +
    'copying oldText from the lines above; Pixel applies the 9 held edits with it in the same all-or-nothing edit. Do not rewrite the whole file.',
].join('\n');
const MERGED_NOTE = '[ODS Pixel edit] Applied 10 replacements in one edit: yours plus 9 held from the rejected call (its edits[0-7], edits[9]).';

// Laptop round 081, website_create A#15: the edit's newText was already in the
// file (an earlier edit had applied it) and its oldText was gone; the model
// then rewrote the whole page (1,966 tokens). Synthetic page, same tail.
const LAPTOP_PAGE = '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <title>Night Garden</title>\n</head>\n<body>\n' +
  '  <main>\n    <h1>Night Garden</h1>\n  </main>\n  <script>\n    const reveal = document.getElementById("reveal");\n' +
  '  <script src="script.js" defer></script>\n</body>\n</html>\n';
const LAPTOP_EDIT = {oldText: '  </script>\n  <script src="script.js" defer></script>\n</body>\n</html>',
  newText: '  <script src="script.js" defer></script>\n</body>\n</html>'};

test('the Mac batch classifies exactly as OpenClaw rejects it, and the note names the indentation', () => {
  const {hunks} = classifyEditHunks(MAC.content, MAC.batch);
  assert.deepEqual(hunks.map(hunk => hunk.status), ['match', 'match', 'match', 'match', 'match', 'match', 'match', 'match', 'missing', 'match']);
  assert.throws(() => applyEditsToNormalizedContent(MAC.content, MAC.batch, MAC.path), {message: MAC.batchError});
  const recovery = editRecovery(MAC.content, MAC.batch);
  assert.equal(recovery.note, MAC_NOTE);
  assert.deepEqual(recovery.hold.map(hunk => hunk.index), [0, 1, 2, 3, 4, 5, 6, 7, 9]);
  assert.deepEqual(recovery.hold.map(({oldText, newText}) => ({oldText, newText})), MAC.batch.filter((_, index) => index !== 8));
});

test('a single-edit miss names the closest text and holds nothing', () => {
  const recovery = editRecovery(MAC.content, MAC.retry);
  assert.equal(recovery.hold, undefined);
  assert.equal(recovery.note, [
    '[ODS Pixel edit] Nothing was changed.',
    'The oldText was not found. Closest current text, lines 275-277:',
    '275|  <footer>', '276|    <p>© 2026 Night Garden Event Series. All rights reserved.</p>', '277|  </footer>',
    'Your oldText differs only in indentation (line 275 has 2 leading spaces; yours has 4).',
    'Copy oldText exactly from the current file and retry this one edit. Do not rewrite the whole file.',
  ].join('\n'));
});

test('OpenClaw\'s 800-character file head is removed from the persisted copy only', () => {
  const result = executeOpenClawEdit(workspaceWith({[MAC.path]: MAC.content}), {path: MAC.path, edits: MAC.retry});
  assert.match(result.details.error, /\nCurrent file contents:\n<!DOCTYPE html>/);
  const stripped = withoutMismatchHead({role: 'toolResult', ...result});
  const message = `Could not find the exact text in ${MAC.path}. The old text must match exactly including all whitespace and newlines.`;
  assert.equal(stripped.details.error, message);
  assert.equal(stripped.content[0].text, JSON.stringify({status: 'error', tool: 'edit', error: message}, null, 2));
  assert.equal(result.details.error.includes('Current file contents'), true, 'the input is not mutated');
  const multi = {details: {error: MAC.batchError}};
  assert.equal(withoutMismatchHead(multi), multi, 'a multi-edit miss has no head and is unchanged');
});

test('an edit whose newText is already present reports that instead of a rewrite path', () => {
  assert.deepEqual(alreadyAppliedLines(LAPTOP_PAGE, LAPTOP_EDIT.oldText, LAPTOP_EDIT.newText), {start: 12, end: 14});
  const recovery = editRecovery(LAPTOP_PAGE, [LAPTOP_EDIT]);
  assert.equal(recovery.hold, undefined);
  assert.equal(recovery.note, '[ODS Pixel edit] Nothing was changed.\n' +
    "This edit's newText is already in the file at lines 12-14, so this change may already be applied; check those lines before editing again.\n" +
    'Do not rewrite the whole file.');
});

test('held edits join a corrected edit and OpenClaw applies all of them at once', () => {
  const {hold} = editRecovery(MAC.content, MAC.batch);
  const merged = mergeHeldHunks(MAC.content, [CORRECTED], hold);
  assert.deepEqual(merged.held, [0, 1, 2, 3, 4, 5, 6, 7, 9]);
  assert.deepEqual(merged.edits, [CORRECTED, ...MAC.batch.filter((_, index) => index !== 8)]);
  const intended = MAC.batch.map((edit, index) => index === 8 ? CORRECTED : edit);
  assert.equal(applyEditsToNormalizedContent(MAC.content, merged.edits, MAC.path).newContent,
    applyEditsToNormalizedContent(MAC.content, intended, MAC.path).newContent);
  assert.equal(mergedEditNote(1, merged.held), MERGED_NOTE);
});

test('a re-sent or overlapping held edit yields to the new call', () => {
  const {hold} = editRecovery(MAC.content, MAC.batch);
  const intended = MAC.batch.map((edit, index) => index === 8 ? CORRECTED : edit);
  assert.equal(mergeHeldHunks(MAC.content, intended, hold), undefined, 'every held edit was re-sent');
  // A new edit of the same :root block replaces held edits[0].
  const rootEdit = {oldText: '      --bg-dark:#0f0f1a;', newText: '      --bg-dark:#000000;'};
  const merged = mergeHeldHunks(MAC.content, [CORRECTED, rootEdit], hold);
  assert.deepEqual(merged.held, [1, 2, 3, 4, 5, 6, 7, 9]);
  assert.doesNotThrow(() => applyEditsToNormalizedContent(MAC.content, merged.edits, MAC.path));
});

// Each case: the classification must agree with OpenClaw's own outcome (the
// first failing index and kind, or success).
const PARITY = [
  ['trailing whitespace in the file', 'a = 1   \nb = 2\n', [{oldText: 'a = 1\nb = 2', newText: 'a = 3\nb = 2'}]],
  ['smart quotes', 'say(“hi”)\n', [{oldText: 'say("hi")', newText: 'say("bye")'}]],
  ['non-breaking space', 'x = 1\n', [{oldText: 'x = 1', newText: 'x = 2'}]],
  ['NFKC ligature', 'deﬁne(x)\n', [{oldText: 'define(x)', newText: 'define(y)'}]],
  ['unicode dash', 'a — b\n', [{oldText: 'a - b', newText: 'a + b'}]],
  ['CRLF file', 'one\r\ntwo\r\nthree\r\n', [{oldText: 'one\ntwo', newText: 'one\n2'}]],
  ['BOM', '﻿head\nbody\n', [{oldText: 'head', newText: 'HEAD'}]],
  ['duplicate', 'x = 1\nx = 1\n', [{oldText: 'x = 1', newText: 'x = 2'}]],
  ['duplicate only after fuzzy normalization', 'x = 1  \nx = 1\n', [{oldText: 'x = 1\n', newText: 'x = 2\n'}]],
  ['overlap', 'abcdef\n', [{oldText: 'abcd', newText: 'ABCD'}, {oldText: 'cdef', newText: 'CDEF'}]],
  ['second missing', 'a\nb\n', [{oldText: 'a', newText: 'A'}, {oldText: 'z', newText: 'Z'}]],
  ['first missing of two', 'a\nb\n', [{oldText: 'q', newText: 'Q'}, {oldText: 'b', newText: 'B'}]],
  ['fuzzy space for all', 'x = “q”\ny = 2   \n', [{oldText: 'x = "q"', newText: 'x = "r"'}, {oldText: 'y = 2', newText: 'y = 3'}]],
  ['no-op present', 'a\nb\n', [{oldText: 'a', newText: 'a'}, {oldText: 'b', newText: 'B'}]],
];
for (const [name, content, edits] of PARITY) {
  test(`classification agrees with OpenClaw: ${name}`, () => {
    const {hunks} = classifyEditHunks(content, edits);
    let outcome;
    try {
      const {bom, text} = content.startsWith('﻿') ? {bom: 1, text: content.slice(1)} : {bom: 0, text: content};
      assert.ok(bom === 0 || bom === 1);
      const lf = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      const real = edits.filter(edit => edit.oldText !== edit.newText);
      applyEditsToNormalizedContent(lf, real, 'f');
      outcome = 'ok';
    } catch (error) { outcome = error.message; }
    if (outcome === 'ok') {
      assert.ok(hunks.every(hunk => ['match', 'noop'].includes(hunk.status)), JSON.stringify(hunks));
      return;
    }
    const first = hunks.find(hunk => !['match', 'noop'].includes(hunk.status));
    if (/^Could not find/.test(outcome)) assert.equal(first.status, 'missing');
    else if (/occurrences/.test(outcome)) assert.equal(first.status, 'ambiguous');
    else if (/overlap/.test(outcome)) assert.equal(first.status, 'overlap');
    else assert.fail(outcome);
  });
}

test('closest-text regions: anchors first, then similarity, never an unrelated line', () => {
  const file = 'alpha\n  <div class="card">\n    <p>Dawn jazz</p>\n  </div>\nomega\n';
  assert.deepEqual(nearMissRegions(file, '<div class="card">\n  <p>Dawn jazz</p>\n</div>'), [{start: 1, end: 3}]);
  assert.deepEqual(nearMissRegions(file, '    <p>Dawn  jazz!</p>'), [{start: 2, end: 2, similar: true}]);
  assert.deepEqual(nearMissRegions(file, 'completely different text here'), []);
  const twice = 'x\n<li>item</li>\ny\n<li>item</li>\n';
  assert.equal(nearMissRegions(twice, '  <li>item</li>').length, 2);
  const recovery = editRecovery(twice, [{oldText: '  <li>item</li>', newText: '<li>other</li>'}]);
  assert.match(recovery.note, /Similar current text appears in more than one place, including line 2 and line 4:\n2\|<li>item<\/li>\n4\|<li>item<\/li>\n/);
});

test('difference kinds: substring ends are allowed, trailing spaces never count', () => {
  assert.equal(describeDifference(['  <footer>', '  </footer>'], '<footer>\n  </footer>'), undefined);
  assert.deepEqual(describeDifference(['  <footer>', '    <p>x</p>'], '  <footer>\n  <p>x</p>'),
    {kind: 'indentation', line: 1, file: '4 leading spaces', yours: '2'});
  assert.deepEqual(describeDifference(['\t<a>', '\t</a>'], '  <a>\n\t</a>'),
    {kind: 'indentation', line: 0, file: '1 leading tab', yours: '2 leading spaces'});
  assert.deepEqual(describeDifference(['a  =  1'], 'a = 1'), {kind: 'spacing', line: 0});
  assert.deepEqual(describeDifference(['a = 1'], 'b = 1'), {kind: 'content', line: 0});
});

test('helpers: miss detection, edit pairs and index ranges', () => {
  assert.equal(editMissError(undefined, MAC.batchError), MAC.batchError);
  assert.equal(editMissError('Found 2 occurrences of the text in f.'), undefined);
  assert.deepEqual(editPairs({path: 'p', oldText: 'a', newText: 'b'}), [{oldText: 'a', newText: 'b'}]);
  assert.equal(editPairs({path: 'p', edits: [{oldText: 'a'}]}), undefined);
  assert.equal(indexRanges([0, 1, 2, 3, 4, 5, 6, 7, 9]), 'edits[0-7], edits[9]');
  assert.equal(indexRanges([2]), 'edits[2]');
});

test('more than three misses are summarized, and a hold needs a correctable miss', () => {
  const file = 'a\nb\nc\nd\ne\nf\n';
  const edits = ['a', 'x1', 'x2', 'x3', 'x4', 'x5'].map(oldText => ({oldText, newText: `${oldText}!`}));
  const recovery = editRecovery(file, edits);
  assert.match(recovery.note, /2 more edits also did not match: edits\[4-5\]\./);
  assert.deepEqual(recovery.hold.map(hunk => hunk.index), [0]);
  assert.equal(editRecovery(file, [{oldText: 'a', newText: 'A'}]), undefined, 'nothing missed');
});

// Guard integration: OpenClaw's hook order around a real edit on disk.
after(removeWorkspaces);
const PROMPT = 'In fleet-qualification-bf541c4bc8e3, update the site styles and footer of the existing index.html.';
const editHarness = options => guardReplay({prompt: PROMPT, ...options});

for (const wrapped of [false, true]) {
  test(`Mac replay: the rejected batch leaves nine held edits and one corrected edit applies all ten (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {read, edit} = editHarness({wrapped, root});
    read(MAC.path);
    const rejected = edit(MAC.path, MAC.batch);
    assert.match(rejected.text, /Could not find edits\[8\]/, 'OpenClaw\'s own error is kept');
    assert.ok(rejected.text.includes(MAC_NOTE), rejected.text);
    assert.equal(fs.readFileSync(path.join(root, MAC.path), 'utf8'), MAC.content, 'nothing changed');
    const retry = edit(MAC.path, [CORRECTED]);
    assert.deepEqual(retry.executed.edits, [CORRECTED, ...MAC.batch.filter((_, index) => index !== 8)]);
    assert.match(retry.text, /Successfully replaced 10 block\(s\)/);
    assert.ok(retry.text.includes(MERGED_NOTE));
    const intended = MAC.batch.map((item, index) => index === 8 ? CORRECTED : item);
    assert.equal(fs.readFileSync(path.join(root, MAC.path), 'utf8'),
      `${applyEditsToNormalizedContent(MAC.content, intended, MAC.path).newContent}`);
    // The hold was used once: a later edit of the file is left as sent.
    const later = edit(MAC.path, [{oldText: '© 2026', newText: '© 2027'}]);
    assert.deepEqual(later.executed.edits, [{oldText: '© 2026', newText: '© 2027'}]);
    assert.doesNotMatch(later.text, /\[ODS Pixel edit\]/);
  });

  test(`a single-edit miss persists the closest text instead of the file head (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {edit} = editHarness({wrapped, root});
    const rejected = edit(MAC.path, MAC.retry);
    assert.doesNotMatch(JSON.stringify(rejected.persisted), /Current file contents/);
    assert.match(rejected.text, /The oldText was not found\. Closest current text, lines 275-277:\n275\|  <footer>/);
    assert.equal((wrapped ? rejected.persisted.details.result : rejected.persisted).isError, true, 'still a failure');
  });

  test(`the model's own path spelling binds the same hold (wrapped=${wrapped})`, () => {
    // The Mac model wrote "workspace/fleet-qualification-…/index.html".
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {edit} = editHarness({wrapped, root});
    assert.ok(edit(MAC.modelPath, MAC.batch).text.includes(MAC_NOTE));
    const retry = edit(MAC.modelPath, [CORRECTED]);
    assert.equal(retry.executed.edits.length, 10);
    assert.ok(retry.text.includes(MERGED_NOTE), retry.text);
  });

  test(`laptop A#15 replay: an edit whose change is already applied says so (wrapped=${wrapped})`, () => {
    const file = 'fleet-qualification-5abc4de4e9e3/index.html';
    const root = workspaceWith({[file]: LAPTOP_PAGE});
    const {edit} = editHarness({wrapped, root, prompt: 'In fleet-qualification-5abc4de4e9e3, fix the event page script in index.html.'});
    const rejected = edit(file, [LAPTOP_EDIT]);
    assert.match(rejected.text, /Could not find the exact text/);
    assert.match(rejected.text, /This edit's newText is already in the file at lines 12-14, so this change may already be applied/);
    assert.doesNotMatch(JSON.stringify(rejected.persisted), /Current file contents/);
    // Nothing is held for a single edit, so the next edit is left as sent.
    const next = edit(file, [{oldText: '<h1>Night Garden</h1>', newText: '<h1>Night Garden!</h1>'}]);
    assert.equal(next.executed.edits.length, 1);
  });
}

for (const variant of ['changed-bytes', 'write', 'patch', 'exec-names-file', 'new-run', 'second-attempt']) {
  test(`held edits are not merged after: ${variant}`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const guard = createToolLoopGuard({execControl: passthroughExecControl});
    const first = editHarness({root, guard});
    first.edit(MAC.path, MAC.batch);
    let current = first;
    if (variant === 'changed-bytes') fs.appendFileSync(path.join(root, MAC.path), '<!-- changed -->\n');
    if (variant === 'write') first.write(MAC.path, MAC.content);
    if (variant === 'patch') {
      first.call('apply_patch', {input: `*** Begin Patch\n*** Update File: ${MAC.path}\n@@\n-</html>\n+</html>\n*** End Patch`},
        () => ({content: [{type: 'text', text: 'Done'}], details: {}}));
    }
    if (variant === 'exec-names-file') first.exec(`sed -n 270,280p ${MAC.path}`);
    if (variant === 'new-run') current = editHarness({root, guard, runId: 'run-2'});
    if (variant === 'second-attempt') {
      // The merged call itself misses: nothing applies and nothing is held again.
      const missed = first.edit(MAC.path, [{oldText: 'not in the file', newText: 'x'}]);
      assert.match(missed.text, /The 9 held edits from the earlier call were included and not applied; they are no longer held/);
    }
    const retry = current.edit(MAC.path, [CORRECTED]);
    assert.deepEqual(retry.executed.edits, [CORRECTED]);
    assert.doesNotMatch(retry.text, /held from the rejected call/);
  });
}

test('confinement: a linked, hard-linked or oversized file gets neither note nor hold', () => {
  for (const variant of ['symlink', 'hardlink', 'oversize']) {
    const root = workspaceWith();
    const real = path.join(root, 'real.html');
    const content = variant === 'oversize' ? `${MAC.content}${'<!-- pad -->\n'.repeat(22000)}` : MAC.content;
    fs.writeFileSync(real, content);
    fs.mkdirSync(path.join(root, 'fleet-qualification-bf541c4bc8e3'));
    const target = path.join(root, ...MAC.path.split('/'));
    if (variant === 'symlink') fs.symlinkSync(real, target);
    else if (variant === 'hardlink') fs.linkSync(real, target);
    else fs.renameSync(real, target);
    const {edit} = editHarness({root});
    const rejected = edit(MAC.path, MAC.batch);
    assert.match(rejected.text, /Could not find edits\[8\]/, variant);
    assert.doesNotMatch(rejected.text, /\[ODS Pixel edit\]/, variant);
    const retry = edit(MAC.path, [CORRECTED]);
    assert.deepEqual(retry.executed.edits, [CORRECTED], variant);
  }
});

test('an edit with an empty oldText is not merged and keeps its create-file correction', () => {
  const root = workspaceWith({[MAC.path]: MAC.content});
  const {edit} = editHarness({root});
  edit(MAC.path, MAC.batch);
  const blocked = edit(MAC.path, [{oldText: '', newText: 'new file'}]);
  assert.equal(blocked.decision?.block, true);
  assert.match(blocked.decision.blockReason, /edit cannot create a new file/);
});

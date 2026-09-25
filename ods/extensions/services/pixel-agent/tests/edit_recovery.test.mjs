import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {alreadyAppliedInCall, alreadyAppliedLines, classifyEditHunks, describeDifference, editMissError, editPairs, editRecovery, indexRanges,
  nearMissRegions, withoutMismatchHead} from '../plugin/edit-recovery.mjs';
import {applyEditsToNormalizedContent, executeOpenClawEdit} from './fixtures/openclaw-edit-2026.6.33.mjs';
import {execResult, guardReplay, removeWorkspaces, workspaceWith} from './fixtures/guard-replay.mjs';

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
  'The other 9 edits match the current file but were not applied. Send one edit for this file again with all of them unchanged ' +
    'and a corrected edits[8], copying oldText from the current file. Do not rewrite the whole file.',
].join('\n');

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
  assert.deepEqual(editRecovery(MAC.content, MAC.batch), {note: MAC_NOTE});
});

test('a single-edit miss names the closest text', () => {
  const recovery = editRecovery(MAC.content, MAC.retry);
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
  assert.equal(recovery.note, '[ODS Pixel edit] Nothing was changed.\n' +
    "This edit's newText is already in the file at lines 12-14, so this change may already be applied; check those lines before editing again.\n" +
    'Do not rewrite the whole file.');
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

test('more than three misses are summarized, and the next step names what to send', () => {
  const file = 'a\nb\nc\nd\ne\nf\n';
  const edits = ['a', 'x1', 'x2', 'x3', 'x4', 'x5'].map(oldText => ({oldText, newText: `${oldText}!`}));
  const recovery = editRecovery(file, edits);
  assert.match(recovery.note, /2 more edits also did not match: edits\[4-5\]\./);
  assert.match(recovery.note, /The other 1 edit matches the current file but was not applied\. Send one edit for this file again with it unchanged and a corrected edits\[1-5\], copying oldText from the current file\. Do not rewrite the whole file\.$/);
  assert.match(editRecovery(file, edits.slice(1)).note, /\nRetry the edit with corrected oldText copied from the current file\. Do not rewrite the whole file\.$/);
  assert.equal(editRecovery(file, [{oldText: 'a', newText: 'A'}]), undefined, 'nothing missed');
});

// Guard integration: OpenClaw's hook order around a real edit on disk.
after(removeWorkspaces);
const PROMPT = 'In fleet-qualification-bf541c4bc8e3, update the site styles and footer of the existing index.html.';
const editHarness = options => guardReplay({prompt: PROMPT, ...options});
const INTENDED = MAC.batch.map((edit, index) => index === 8 ? CORRECTED : edit);
const intendedPage = () => applyEditsToNormalizedContent(MAC.content, INTENDED, MAC.path).newContent;
const pageNow = root => fs.readFileSync(path.join(root, MAC.path), 'utf8');
const EDIT_NOTE = /\[ODS Pixel edit\]/;

for (const wrapped of [false, true]) {
  test(`Mac replay: the rejected batch names the miss, and the corrected batch applies all ten (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {read, edit} = editHarness({wrapped, root});
    read(MAC.path);
    const rejected = edit(MAC.path, MAC.batch);
    assert.match(rejected.text, /Could not find edits\[8\]/, 'OpenClaw\'s own error is kept');
    assert.ok(rejected.text.includes(MAC_NOTE), rejected.text);
    assert.equal(pageNow(root), MAC.content, 'nothing changed');
    // What the note asks for: the same call with edits[8] corrected.
    const retry = edit(MAC.path, INTENDED);
    assert.deepEqual(retry.executed.edits, INTENDED);
    assert.match(retry.text, /Successfully replaced 10 block\(s\)/);
    assert.doesNotMatch(retry.text, EDIT_NOTE);
    assert.equal(pageNow(root), intendedPage());
  });

  test(`nothing is held: after a rejected batch the next edit runs exactly as sent (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {edit, exec} = editHarness({wrapped, root});
    edit(MAC.path, MAC.batch);
    assert.doesNotMatch(exec(`grep -n footer ${MAC.path}`, () => execResult('275:  <footer>', 0)).text, EDIT_NOTE);
    const single = edit(MAC.path, [CORRECTED]);
    assert.deepEqual(single.executed, {path: MAC.path, edits: [CORRECTED]});
    assert.match(single.text, /Successfully replaced 1 block\(s\)/);
    assert.doesNotMatch(single.text, EDIT_NOTE);
  });

  test(`a single-edit miss persists the closest text instead of the file head (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {edit} = editHarness({wrapped, root});
    const rejected = edit(MAC.path, MAC.retry);
    assert.doesNotMatch(JSON.stringify(rejected.persisted), /Current file contents/);
    assert.match(rejected.text, /The oldText was not found\. Closest current text, lines 275-277:\n275\|  <footer>/);
    assert.equal((wrapped ? rejected.persisted.details.result : rejected.persisted).isError, true, 'still a failure');
  });

  test(`the model's own path spelling reads the same file (wrapped=${wrapped})`, () => {
    // The Mac model wrote "workspace/fleet-qualification-…/index.html".
    const root = workspaceWith({[MAC.path]: MAC.content});
    const {edit} = editHarness({wrapped, root});
    assert.ok(edit(MAC.modelPath, MAC.batch).text.includes(MAC_NOTE));
  });

  test(`laptop A#15 replay: an edit whose change is already applied says so (wrapped=${wrapped})`, () => {
    const file = 'fleet-qualification-5abc4de4e9e3/index.html';
    const root = workspaceWith({[file]: LAPTOP_PAGE});
    const {edit} = editHarness({wrapped, root, prompt: 'In fleet-qualification-5abc4de4e9e3, fix the event page script in index.html.'});
    const rejected = edit(file, [LAPTOP_EDIT]);
    assert.match(rejected.text, /Could not find the exact text/);
    assert.match(rejected.text, /This edit's newText is already in the file at lines 12-14, so this change may already be applied/);
    assert.doesNotMatch(JSON.stringify(rejected.persisted), /Current file contents/);
  });
}

/// Final review: in a move or a swap within one call, the missed edit's
// newText is in the file only because another edit of the call removes or
// replaces it. Calling that "already applied" and asking to resend the call
// without it lost the moved text (app.js references went to 0).
const MOVE_FILE = 'fleet-qualification-bf541c4bc8e3/move.html';
const MOVE_PAGE = '<html>\n<head>\n  <script src="app.js"></script>\n</head>\n<body>\n  <main></main>\n  <!-- scripts -->\n</body>\n</html>\n';
const MOVE = [
  {oldText: '  <script src="app.js"></script>\n</head>', newText: '</head>'},
  {oldText: '  <!--scripts-->', newText: '  <script src="app.js"></script>'},
];
const MOVE_FIXED = [MOVE[0], {oldText: '  <!-- scripts -->', newText: MOVE[1].newText}];
const NAV_FILE = 'fleet-qualification-bf541c4bc8e3/index.html';
const NAV_PAGE = '<nav>\n  <ul>\n    <li><a href="#about">About</a></li>\n    <li><a href="#events">Events</a></li>\n  </ul>\n</nav>\n';
const SWAP = [
  {oldText: '    <li><a href="#about">About</a></li>', newText: '    <li><a href="#events">Events</a></li>'},
  {oldText: '    <li><a href="#event">Events</a></li>', newText: '    <li><a href="#about">About</a></li>'},
];
const SWAP_FIXED = [SWAP[0], {oldText: '    <li><a href="#events">Events</a></li>', newText: SWAP[1].newText}];

test('a moved or swapped text is not "already applied" when another edit of the call removes it', () => {
  assert.deepEqual(alreadyAppliedLines(MOVE_PAGE, MOVE[1].oldText, MOVE[1].newText), {start: 3, end: 3}, 'alone it looks applied');
  assert.equal(alreadyAppliedInCall(MOVE_PAGE, MOVE, 1), undefined);
  assert.equal(alreadyAppliedInCall(NAV_PAGE, SWAP, 1), undefined);
  // Without another edit touching that text, it still counts.
  assert.deepEqual(alreadyAppliedInCall(LAPTOP_PAGE, [{oldText: '<h1>Night Garden</h1>', newText: '<h1>Night Garden</h1>!'}, LAPTOP_EDIT], 1),
    {start: 12, end: 14});
  for (const [page, edits] of [[MOVE_PAGE, MOVE], [NAV_PAGE, SWAP]]) {
    const {note} = editRecovery(page, edits);
    assert.doesNotMatch(note, /already in the file|without edits/);
    assert.match(note, /Send one edit for this file again with it unchanged and a corrected edits\[1\], copying oldText from the current file\./);
  }
});

for (const wrapped of [false, true]) {
  for (const [name, file, page, edits, fixed, check] of [
    ['move', MOVE_FILE, MOVE_PAGE, MOVE, MOVE_FIXED, text => {
      assert.equal(text.split('app.js').length - 1, 1);
      assert.ok(text.includes('  <script src="app.js"></script>\n</body>'), text);
    }],
    ['swap', NAV_FILE, NAV_PAGE, SWAP, SWAP_FIXED, text => {
      assert.match(text, /#events">Events<\/a><\/li>\n {4}<li><a href="#about">About/);
    }],
  ]) {
    test(`${name} with a missed edit: the note asks for the corrected call, which keeps the text (wrapped=${wrapped})`, () => {
      const root = workspaceWith({[file]: page});
      const {read, edit} = editHarness({wrapped, root});
      read(file);
      const rejected = edit(file, edits);
      assert.match(rejected.text, /edits\[1\] was not found\./);
      assert.doesNotMatch(rejected.text, /already in the file|without edits/);
      assert.match(rejected.text, /with it unchanged and a corrected edits\[1\]/);
      const retry = edit(file, fixed);
      assert.match(retry.text, /Successfully replaced 2 block\(s\)/);
      check(fs.readFileSync(path.join(root, ...file.split('/')), 'utf8'));
    });
  }
}

// OpenClaw runs the tool calls of one model message in parallel by default:
// every before hook first, then executions (one file's mutations in message
// order) and after hooks as calls complete, then the results in message order.
// The note keeps no state and never changes what runs. In every ordering each
// call runs exactly as sent and only a failed edit gets a note. The note reads
// the file when that edit's own after hook runs, so it can include a sibling's
// change; when another call of the message targets the same file, it asks for
// a read and only the missing changes instead of "resend unchanged".
const TITLE = {oldText: '      <h1>Night Garden FLEET-9201630fb1</h1>', newText: '      <h1>Night Garden FLEET-9201630fb1 Revised</h1>'};
const OTHER = 'fleet-qualification-bf541c4bc8e3/other.css';
const noteLines = text => text.split('\n').filter(line => /^\[ODS Pixel edit\]|not found|newText is already|The other /.test(line));
const SIBLING = 'Another tool call in the same message also changes this file, so it may no longer be what you read. ' +
  'Read the file first, then send only the changes that are still missing with a corrected edits[8] copied from it. Do not rewrite the whole file.';
const RESEND = /with (?:it|all of them) unchanged/;
for (const wrapped of [false, true]) {
  // Sibling edits of one file: the batch misses; B makes the batch's own
  // edits[9] change. Before B runs, the note sees nine matching edits; after,
  // it sees that edits[9] is already applied. Either way: read first.
  for (const [schedule, sawB] of [[['run 0', 'after 0', 'run 1', 'after 1'], false], [['run 0', 'run 1', 'after 1', 'after 0'], true],
    [['run 0', 'run 1', 'after 0', 'after 1'], true]]) {
    test(`parallel: sibling edits of one file, ${schedule.join(', ')} (wrapped=${wrapped})`, () => {
      const root = workspaceWith({[MAC.path]: MAC.content});
      const h = editHarness({wrapped, root});
      h.read(MAC.path);
      const [a, b] = h.message([h.editArgs(MAC.path, MAC.batch), h.editArgs(MAC.path, [TITLE])], {schedule});
      assert.deepEqual(a.executed.edits, MAC.batch);
      assert.deepEqual(b.executed.edits, [TITLE]);
      assert.match(b.text, /Successfully replaced 1 block\(s\)/);
      assert.doesNotMatch(b.text, EDIT_NOTE);
      assert.match(a.text, /edits\[8\] was not found\. Closest current text, lines 275-277:/);
      if (sawB) assert.match(a.text, /edits\[9\] newText is already in the file at line \d+, so this change may already be applied/);
      else assert.doesNotMatch(a.text, /edits\[9\]/);
      assert.ok(a.text.includes(SIBLING), a.text);
      assert.doesNotMatch(a.text, RESEND);
      assert.equal(pageNow(root), applyEditsToNormalizedContent(MAC.content, [TITLE], MAC.path).newContent, 'only B changed the file');
    });
  }

  // Final review probe: the batch inserts a line and misses its second edit;
  // a sibling inserts the same line. "Resend unchanged" would insert it twice.
  const PAGE = '<html>\n<body>\n  <main>\n    <h1>Title</h1>\n  </main>\n  <footer>\n    <p>Copyright</p>\n  </footer>\n</body>\n</html>\n';
  const INSERT = {oldText: '    <h1>Title</h1>', newText: '    <h1>Title</h1>\n    <p class="lead">Welcome to the garden</p>'};
  const MISS = {oldText: '    <footer>\n    <p>Copyright</p>', newText: '    <footer>\n    <p>Copyright 2026</p>'};
  for (const schedule of [['run 0', 'after 0', 'run 1', 'after 1'], ['run 0', 'run 1', 'after 0', 'after 1'], ['run 0', 'run 1', 'after 1', 'after 0']]) {
    test(`parallel: a sibling makes the same insertion, ${schedule.join(', ')} (wrapped=${wrapped})`, () => {
      const file = 'fleet-qualification-bf541c4bc8e3/index.html';
      const root = workspaceWith({[file]: PAGE});
      const h = editHarness({wrapped, root});
      const [a, b] = h.message([h.editArgs(file, [INSERT, MISS]), h.editArgs(file, [INSERT])], {schedule});
      assert.deepEqual(a.executed.edits, [INSERT, MISS]);
      assert.match(b.text, /Successfully replaced 1 block\(s\)/);
      assert.doesNotMatch(b.text, EDIT_NOTE);
      assert.match(a.text, /Another tool call in the same message also changes this file[^\n]*Read the file first, then send only the changes that are still missing with a corrected edits\[1\]/);
      assert.doesNotMatch(a.text, RESEND);
    });
  }

  // A write and an edit of one file: the edit runs on the written bytes, and
  // its note describes them; the write's result carries no edit note.
  test(`parallel: write then edit of one file (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const h = editHarness({wrapped, root});
    const written = MAC.content.replace('  <footer>', '  <footer class="site">');
    const [w, e] = h.message([h.writeArgs(MAC.path, written), h.editArgs(MAC.path, [CORRECTED])],
      {schedule: ['run 0', 'run 1', 'after 1', 'after 0']});
    assert.deepEqual(e.executed.edits, [CORRECTED]);
    assert.doesNotMatch(w.text, EDIT_NOTE);
    assert.match(e.text, /The oldText was not found\. Most similar current text, lines 275-277:\n275\|  <footer class="site">/);
    assert.match(e.text, /Your oldText differs from line 275\./);
    assert.match(e.text, /Another tool call in the same message also changes this file[^\n]*with corrected oldText copied from it\./);
    assert.equal(pageNow(root), written);
  });

  // An edit and a call on another file: the other call never changes the
  // edit's note, whichever after hook runs first.
  for (const schedule of [['run 0', 'run 1', 'after 0', 'after 1'], ['run 0', 'run 1', 'after 1', 'after 0'], ['run 1', 'after 1', 'run 0', 'after 0']]) {
    test(`parallel: an edit and an edit of another file, ${schedule.join(', ')} (wrapped=${wrapped})`, () => {
      const root = workspaceWith({[MAC.path]: MAC.content, [OTHER]: 'body { margin: 0; }\n'});
      const h = editHarness({wrapped, root});
      const [a, b] = h.message([h.editArgs(MAC.path, MAC.batch), h.editArgs(OTHER, [{oldText: 'margin: 0', newText: 'margin: 1px'}])], {schedule});
      assert.deepEqual(a.executed.edits, MAC.batch);
      assert.ok(a.text.includes(MAC_NOTE), a.text);
      assert.match(b.text, /Successfully replaced 1 block\(s\)/);
      assert.doesNotMatch(b.text, EDIT_NOTE);
    });
  }

  // Two failing edits of one file in one message: each has its own note, each
  // names the other call, and neither affects the next message.
  test(`parallel: two failing edits of one file, then the next message (wrapped=${wrapped})`, () => {
    const root = workspaceWith({[MAC.path]: MAC.content});
    const h = editHarness({wrapped, root});
    const [a, b] = h.message([h.editArgs(MAC.path, MAC.batch), h.editArgs(MAC.path, MAC.retry)],
      {schedule: ['run 0', 'run 1', 'after 1', 'after 0']});
    assert.ok(a.text.includes(SIBLING), a.text);
    assert.deepEqual(noteLines(b.text).slice(0, 2), ['[ODS Pixel edit] Nothing was changed.',
      'The oldText was not found. Closest current text, lines 275-277:']);
    assert.match(b.text, /Another tool call in the same message also changes this file/);
    const [next] = h.message([h.editArgs(MAC.path, [CORRECTED])]);
    assert.deepEqual(next.executed.edits, [CORRECTED]);
    assert.doesNotMatch(next.text, EDIT_NOTE);
    // A later single-call message has no sibling, so the note is the ordinary one.
    const [alone] = h.message([h.editArgs(MAC.path, MAC.retry)]);
    assert.doesNotMatch(alone.text, /Another tool call/);
  });
}

test('confinement: a linked, hard-linked or oversized file gets no note', () => {
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
    assert.doesNotMatch(rejected.text, EDIT_NOTE, variant);
  }
});

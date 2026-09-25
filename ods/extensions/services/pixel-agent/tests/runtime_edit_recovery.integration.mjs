// OPENCLAW_PACKAGE_DIR must be an absolute path to the pinned runtime package.
// The pinned runtime's own edit tool agrees with the test double that the
// edit-recovery replays use (tests/fixtures/openclaw-edit-2026.6.33.mjs), it
// applies the corrected call that the closest-text note asks for, and it
// queues parallel edits of one file in the order they start, as the guard
// replays of one model message assume.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {classifyEditHunks, editRecovery} from '../plugin/edit-recovery.mjs';
import {executeOpenClawEdit} from './fixtures/openclaw-edit-2026.6.33.mjs';

const packageDir = process.env.OPENCLAW_PACKAGE_DIR;
if (!packageDir || !path.isAbsolute(packageDir)) {
  throw new Error('Set OPENCLAW_PACKAGE_DIR to the absolute path of the pinned OpenClaw package.');
}
assert.equal(JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8')).version, '2026.6.33');
const {X: createEditToolDefinition} = await import(pathToFileURL(path.join(packageDir, 'dist', 'sessions-CZbwb3_c.js')).href);

const MAC = JSON.parse(fs.readFileSync(new URL('./fixtures/edit-miss-mac-round078.json', import.meta.url), 'utf8'));
const CORRECTED = {oldText: MAC.batch[8].oldText.replace('    <footer>', '  <footer>'),
  newText: MAC.batch[8].newText.replace('    <footer>', '  <footer>')};
const roots = [];
after(() => { for (const root of roots) fs.rmSync(root, {recursive: true, force: true}); });
function workspace(content) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-edit-runtime-')));
  roots.push(root);
  fs.mkdirSync(path.join(root, path.dirname(MAC.path)), {recursive: true});
  fs.writeFileSync(path.join(root, MAC.path), content);
  return root;
}
// The runtime tool, called as the agent loop does after prepareArguments.
async function runtimeEdit(root, args) {
  const tool = createEditToolDefinition(root);
  const prepared = tool.prepareArguments(structuredClone(args));
  try {
    return {text: (await tool.execute('call', prepared, undefined, undefined, undefined)).content[0].text};
  } catch (error) {
    return {error: error.message};
  }
}
const read = root => fs.readFileSync(path.join(root, MAC.path), 'utf8');

test('the recorded Mac batch fails in the runtime exactly as in the double, and as classified', async () => {
  const root = workspace(MAC.content), doubleRoot = workspace(MAC.content);
  const runtime = await runtimeEdit(root, {path: MAC.path, edits: MAC.batch});
  assert.equal(runtime.error, MAC.batchError);
  assert.equal(executeOpenClawEdit(doubleRoot, {path: MAC.path, edits: MAC.batch}).details.error, runtime.error);
  assert.equal(classifyEditHunks(MAC.content, MAC.batch).hunks[8].status, 'missing');
  assert.equal(read(root), MAC.content);

  const single = await runtimeEdit(root, {path: MAC.path, oldText: MAC.retry[0].oldText, newText: MAC.retry[0].newText});
  assert.equal(single.error, executeOpenClawEdit(doubleRoot, {path: MAC.path, edits: MAC.retry}).details.error);
  assert.match(single.error, /\nCurrent file contents:\n<!DOCTYPE html>/);
});

test('the runtime applies the corrected call that the note asks for, byte for byte as the double', async () => {
  assert.match(editRecovery(MAC.content, MAC.batch).note, /Send one edit for this file again with all of them unchanged and a corrected edits\[8\]/);
  const intended = MAC.batch.map((edit, index) => index === 8 ? CORRECTED : edit);
  const root = workspace(MAC.content), doubleRoot = workspace(MAC.content);
  const runtime = await runtimeEdit(root, {path: MAC.path, edits: intended});
  assert.equal(runtime.text, `Successfully replaced 10 block(s) in ${MAC.path}.`);
  assert.match(executeOpenClawEdit(doubleRoot, {path: MAC.path, edits: intended}).content[0].text, /Successfully replaced 10 block\(s\)/);
  assert.equal(read(root), read(doubleRoot));
  assert.ok(read(root).includes(CORRECTED.newText));
});

test('parallel edits of one file run in the order they start: each sees the bytes the one before it left', async () => {
  const root = workspace(MAC.content);
  const tool = createEditToolDefinition(root);
  const start = args => tool.execute('call', tool.prepareArguments(structuredClone(args)), undefined, undefined, undefined)
    .then(result => ({text: result.content[0].text}), error => ({error: error.message}));
  const title = {oldText: '<h1>Night Garden FLEET-9201630fb1</h1>', newText: '<h1>Night Garden FLEET-9201630fb1 Revised</h1>'};
  const [batch, first, second] = await Promise.all([
    start({path: MAC.path, edits: MAC.batch}),
    start({path: MAC.path, edits: [title]}),
    start({path: MAC.path, edits: [{oldText: 'FLEET-9201630fb1 Revised</h1>', newText: 'FLEET-9201630fb1 Revised!</h1>'}]}),
  ]);
  assert.equal(batch.error, MAC.batchError);
  assert.match(first.text, /Successfully replaced 1 block\(s\)/);
  assert.match(second.text, /Successfully replaced 1 block\(s\)/);
  assert.ok(read(root).includes('<h1>Night Garden FLEET-9201630fb1 Revised!</h1>'));
  // A note computed now describes these bytes: edits[9]'s change is applied.
  assert.deepEqual(classifyEditHunks(read(root), MAC.batch).hunks.map(hunk => hunk.status).slice(8), ['missing', 'missing']);
});

const PARITY = [
  ['trailing whitespace', 'a = 1   \nb = 2\n', [{oldText: 'a = 1\nb = 2', newText: 'a = 3\nb = 2'}]],
  ['smart quotes', 'say(“hi”)\n', [{oldText: 'say("hi")', newText: 'say("bye")'}]],
  ['non-breaking space', 'x = 1\n', [{oldText: 'x = 1', newText: 'x = 2'}]],
  ['duplicate', 'x = 1\nx = 1\n', [{oldText: 'x = 1', newText: 'x = 2'}]],
  ['overlap', 'abcdef\n', [{oldText: 'abcd', newText: 'ABCD'}, {oldText: 'cdef', newText: 'CDEF'}]],
  ['second missing', 'a\nb\n', [{oldText: 'a', newText: 'A'}, {oldText: 'z', newText: 'Z'}]],
  ['CRLF', 'one\r\ntwo\r\n', [{oldText: 'one\ntwo', newText: 'one\n2'}]],
  // A move and a swap within one call (edit_recovery.test.mjs): the missed
  // form fails as a whole, and the corrected form applies both edits.
  ...[['move', '<head>\n  <script src="app.js"></script>\n</head>\n<body>\n  <!-- scripts -->\n</body>\n',
    [{oldText: '  <script src="app.js"></script>\n</head>', newText: '</head>'}], '  <!--scripts-->', '  <!-- scripts -->', '  <script src="app.js"></script>'],
  ['swap', '<ul>\n  <li>About</li>\n  <li>Events</li>\n</ul>\n',
    [{oldText: '  <li>About</li>', newText: '  <li>Events</li>'}], '  <li>Event</li>', '  <li>Events</li>', '  <li>About</li>'],
  ].flatMap(([name, content, first, missed, fixed, newText]) => [
    [`${name} with a missed edit`, content, [...first, {oldText: missed, newText}]],
    [`${name}, corrected`, content, [...first, {oldText: fixed, newText}]],
  ]),
];
for (const [name, content, edits] of PARITY) {
  test(`runtime and double agree: ${name}`, async () => {
    const root = workspace(content), doubleRoot = workspace(content);
    const runtime = await runtimeEdit(root, {path: MAC.path, edits});
    const double = executeOpenClawEdit(doubleRoot, {path: MAC.path, edits});
    assert.equal(runtime.error ?? runtime.text, double.details?.error ?? double.content[0].text);
    assert.equal(read(root), read(doubleRoot));
    const failed = classifyEditHunks(content, edits).hunks.find(hunk => !['match', 'noop'].includes(hunk.status));
    assert.equal(Boolean(failed), Boolean(runtime.error), JSON.stringify(classifyEditHunks(content, edits)));
  });
}

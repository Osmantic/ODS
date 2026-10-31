import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createChangedFileChecks, debugGlobalWrites, FILE_CHECK_INSTRUCTION, FILE_CHECK_LIMITS, fileCheckFacts,
  fileCheckInstruction, parseCheck, scratchEntry, topLevelDefinitions, touchedProjects} from '../plugin/changed-file-checks.mjs';

// Portal's checks of the files a response changed (changed-file-checks.mjs).
// On 2026-10-30/31 a one-off Python patch run through exec rewrote the game's
// chars.js as prefix + prefix + new + suffix + suffix, debug globals such as
// globalThis.__SF stayed in its code, and .crop-return.txt stayed in its
// folder; none was reported until the owner named it.

// The incident's shape: a patch inside SPRITE_CACHE written as prefix +
// prefix + new + suffix + suffix.
const PREFIX = 'function SPRITE_pose(e, time) {\n  return Math.floor(time * 8) % 4;\n}\n' +
  'function SPRITE_blit(g, e, time) {\n  g.drawImage(e.sheet, SPRITE_pose(e, time) * 32, 0, 32, 32, e.x, e.y, 32, 32);\n}\n' +
  'const SPRITE_CACHE = {\n  size: 32,\n';
const SUFFIX = '};\nfunction drawEnemy(g, e, time) {\n  SPRITE_blit(g, e, time);\n}\n';
const CHARS = `${PREFIX}  frames: 4,\n${SUFFIX}`;
const DOUBLED = `${PREFIX}${PREFIX}  frames: 8,\n${SUFFIX}${SUFFIX}`;
// The second copy of the prefix starts inside the object literal.
const DOUBLED_LINE = PREFIX.split('\n').length;

function workspace(t, files = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-checks-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const at = relative => path.join(root, ...relative.split('/'));
  const write = (relative, content) => { fs.mkdirSync(path.dirname(at(relative)), {recursive: true}); fs.writeFileSync(at(relative), content); };
  for (const [relative, content] of Object.entries(files)) write(relative, content);
  return {root, at, write};
}

test('top-level definitions are counted at column 0, as the doubled file repeated them', () => {
  const source = 'function a() {}\nasync function b() {}\nfunction* c() {}\nfunction *d() {}\nconst e = 1;\nlet f, g;\n' +
    'var h = 2;\nclass I {}\nexport function j() {}\nexport const k = 1;\nexport default function l() {}\n' +
    '  function nested() {}\nfunctional = 1;\nconstant = 2;\n// function commented() {}\nfunction a() {}\n';
  assert.deepEqual(Object.fromEntries(topLevelDefinitions(source)),
    {a: 2, b: 1, c: 1, d: 1, e: 1, f: 1, h: 1, I: 1, j: 1, k: 1, l: 1});
  assert.deepEqual(Object.fromEntries(topLevelDefinitions(DOUBLED)),
    {SPRITE_pose: 2, SPRITE_blit: 2, SPRITE_CACHE: 2, drawEnemy: 2});
});

test('debug globals are writes to globalThis, window or self properties named __...', () => {
  const source = [
    'globalThis.__SF = SPRITE_CACHE;', 'window.__SPRITEFOR = (e) => e;', "self['__WARM'] = true;",
    'globalThis.__ENTRY++;', 'window.__E1 += 1;', 'globalThis.__REPLAY ??= [];', 'globalThis.__SF = other;',
    // Reads, comparisons and other names are not writes of a debug global.
    'if (globalThis.__SF) draw();', 'ok = globalThis.__SF === x;', 'ok = window.__E1 == 2;', 'ok = window.__E1 >= 2;',
    'game.window.__x = 1;', 'window.__proto__ = null;', 'globalThis.game = game;', 'window._single = 1;',
    '// globalThis.__OFF = 1;', '  * window.__DOC = 1', '/* self.__BLOCK = 1 */',
  ].join('\n');
  assert.deepEqual(Object.fromEntries(debugGlobalWrites(source)), {
    'globalThis.__SF': 2, 'window.__SPRITEFOR': 1, 'self.__WARM': 1, 'globalThis.__ENTRY': 1, 'window.__E1': 1,
    'globalThis.__REPLAY': 1});
});

test('a new dotfile or scratch-looking name in the project folder is reported', () => {
  for (const name of ['.crop-return.txt', '.crop_actual.js', '.env', '.cache', 'chars.js.bak', 'chars.js.orig',
    'out.tmp', 'tmp-build', 'chars-tmp.js', 'build-tmp2', 'NOTES.BAK']) assert.equal(scratchEntry(name), true, name);
  for (const name of ['chars.js', 'index.html', 'README.md', 'tmp', 'template.js', 'attempt.js', 'chart-tmpl.js',
    'backup.js', 'orig.js', undefined]) assert.equal(scratchEntry(name), false, String(name));
});

test('the parse check finds the doubled file and passes scripts, CommonJS and ES modules', () => {
  assert.deepEqual(parseCheck(CHARS, 'chars.js'), {ok: true});
  assert.deepEqual(parseCheck(DOUBLED, 'chars.js'), {ok: false, line: DOUBLED_LINE});
  // The shipped game's own chars.js, doubled the same way around a patch inside
  // the array wolfHitShapes returns: "use strict" lands inside the array.
  const shipped = fs.readFileSync(new URL('../example-projects/elf-of-doom/chars.js', import.meta.url), 'utf8');
  const old = '    hitCircle(33, -31, 3.5),     // nose (2.6x2.2), leading edge\n';
  const prefix = shipped.slice(0, shipped.indexOf(old)), suffix = shipped.slice(shipped.indexOf(old) + old.length);
  assert.deepEqual(parseCheck(shipped, 'chars.js'), {ok: true});
  assert.deepEqual(parseCheck(`${prefix}${prefix}    hitCircle(34, -31, 3.5),\n${suffix}${suffix}`, 'chars.js'),
    {ok: false, line: prefix.split('\n').length + 1});
  // A classic script with a hashbang; CommonJS with a top-level return.
  assert.deepEqual(parseCheck('#!/usr/bin/env node\nconst x = 1;\n', 'tool.js'), {ok: true});
  assert.deepEqual(parseCheck('const a = 1;\nif (a) return;\nmodule.exports = a;\n', 'server.js'), {ok: true});
  assert.deepEqual(parseCheck('const a = 1;\nif (a) return;\nmodule.exports = a;\n', 'server.cjs'), {ok: true});
  assert.deepEqual(parseCheck('const a = 1;\n\nlet = ;\n', 'server.cjs'), {ok: false, line: 3});
  // ES modules, through `node --check` (a .js file whose script parse stops at module syntax).
  assert.deepEqual(parseCheck('import {x} from "./x.js";\nexport const y = x + 1;\nawait Promise.resolve();\n', 'main.js'), {ok: true});
  assert.deepEqual(parseCheck('import {x} from "./x.js";\n\nexport function (\n', 'main.js'), {ok: false, line: 3});
  assert.deepEqual(parseCheck('export const a = 1;\n', 'lib.mjs'), {ok: true});
  assert.deepEqual(parseCheck('const ok = 1;\nexport const = 1;\n', 'lib.mjs'), {ok: false, line: 2});
  // A syntax error ahead of any module syntax is the script's own.
  assert.deepEqual(parseCheck('const a = ;\nimport x from "y";\n', 'main.js'), {ok: false, line: 1});
  // Nothing is claimed when the check cannot finish.
  assert.equal(parseCheck('export const a = 1;\n', 'main.js', () => undefined), undefined);
  assert.equal(parseCheck('export const a = 1;\n', 'lib.mjs', null), undefined);
  assert.equal(parseCheck('['.repeat(200000), 'deep.js'), undefined);
});

test('the module check never runs the module', t => {
  const marker = workspace(t).at('ran.txt');
  const source = `import fs from 'node:fs';\nfs.writeFileSync(${JSON.stringify(marker)}, 'ran');\nexport const a = 1;\n`;
  assert.deepEqual(parseCheck(source, 'main.mjs'), {ok: true});
  assert.equal(fs.existsSync(marker), false);
});

test('a call touches the Playground projects it may change, as written or routed', () => {
  const root = '/home/ods/.openclaw/workspace';
  assert.deepEqual(touchedProjects('write', {path: 'Playground/elf-of-doom/chars.js'}, root), ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('edit', {path: '/workspace/Playground/elf-of-doom/ui.js'}, root), ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('edit', {path: `${root}/Playground/snake/./main.js`}, root), ['Playground/snake']);
  assert.deepEqual(touchedProjects('exec', {command: 'python3 patch.py', workdir: '/workspace/Playground/elf-of-doom'}, root),
    ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('exec', {command: 'cd Playground/elf-of-doom && node --check chars.js'}, root), ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('exec', {command: "python3 - <<'EOF'\nsrc = open('/workspace/Playground/elf-of-doom/chars.js').read()\n" +
    "open('Playground/snake/x.js', 'w').write(src)\nEOF", workdir: '/workspace'}, root), ['Playground/elf-of-doom', 'Playground/snake']);
  assert.deepEqual(touchedProjects('apply_patch', {input: '*** Begin Patch\n*** Update File: Playground/elf-of-doom/chars.js\n@@\n-a\n+b\n' +
    '*** Add File: Playground/new-game/index.html\n+<p>hi</p>\n*** End Patch'}, root), ['Playground/elf-of-doom', 'Playground/new-game']);
  assert.deepEqual(touchedProjects('tool_call', {id: 'openclaw:core:exec', args: {command: 'ls', workdir: 'Playground/elf-of-doom/src'}}, root),
    ['Playground/elf-of-doom']);
  // Reads change nothing; other folders, other spellings and bare names are not projects.
  for (const [tool, params] of [['read', {path: 'Playground/elf-of-doom/chars.js'}], ['write', {path: 'notes/todo.md'}],
    ['write', {path: 'playground/snake/main.js'}], ['exec', {command: 'ls Playground/', workdir: '/workspace'}],
    ['exec', {command: 'cat MyPlayground/snake/x.js'}], ['exec', {command: 'ls', workdir: '/workspace/Playground'}],
    ['write', {path: 'Playground/con/x.js'}], ['tool_call', {id: 'openclaw:core:read', args: {path: 'Playground/a/b.js'}}],
    ['web_fetch', {url: 'https://example.com/Playground/x'}]]) assert.deepEqual(touchedProjects(tool, params, root), [], JSON.stringify(params));
  // At most FILE_CHECK_LIMITS.projects per call.
  const many = Array.from({length: 9}, (_, i) => `Playground/game-${i}/x.js`).join(' ');
  assert.equal(touchedProjects('exec', {command: `cat ${many}`}, root).length, FILE_CHECK_LIMITS.projects);
});

test('only what changed since first touch is reported, and each finding is told once', t => {
  const {root, write} = workspace(t, {
    'Playground/sprite-game/chars.js': `${CHARS}var legacy = 1;\nvar legacy = 2;\nglobalThis.__OLD = legacy;\n`,
    'Playground/sprite-game/broken.js': 'const a = ;\n',
    'Playground/sprite-game/.prettierrc': '{}\n',
    'Playground/sprite-game/old.bak': 'x',
    'Playground/other/chars.js': CHARS,
  });
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/sprite-game']);
  // Nothing changed: what was already there is not this response's.
  assert.deepEqual(checks.findings(), []);
  // The exec that rewrote chars.js, a debug global, a scratch file and a
  // change to a project this response never touched.
  write('Playground/sprite-game/chars.js', `${DOUBLED}var legacy = 1;\nvar legacy = 2;\nglobalThis.__OLD = legacy;\nglobalThis.__SF = SPRITE_CACHE;\n`);
  write('Playground/sprite-game/.crop-return.txt', '{"x": 1}');
  write('Playground/sprite-game/assets/.hidden', 'x');
  write('Playground/sprite-game/notes.txt', 'plain notes');
  write('Playground/other/chars.js', DOUBLED);
  const expected = [
    {kind: 'parse', project: 'Playground/sprite-game', file: 'chars.js', line: DOUBLED_LINE},
    ...['SPRITE_pose', 'SPRITE_blit', 'SPRITE_CACHE', 'drawEnemy'].map(name =>
      ({kind: 'definition', project: 'Playground/sprite-game', file: 'chars.js', name, count: 2})),
    {kind: 'global', project: 'Playground/sprite-game', file: 'chars.js', name: 'globalThis.__SF'},
    {kind: 'entry', project: 'Playground/sprite-game', name: '.crop-return.txt', folder: false},
  ];
  assert.deepEqual(checks.findings(), expected);
  assert.deepEqual(checks.take(), expected);
  assert.deepEqual(checks.take(), [], 'told once');
  // A third definition is a new finding; the ones already told stay told.
  write('Playground/sprite-game/chars.js', `${DOUBLED}var legacy = 1;\nvar legacy = 2;\nvar legacy = 3;\nglobalThis.__OLD = legacy;\nglobalThis.__SF = SPRITE_CACHE;\n`);
  assert.deepEqual(checks.take(), [{kind: 'definition', project: 'Playground/sprite-game', file: 'chars.js', name: 'legacy', count: 3}]);
  // Fixed: nothing is left to report, though the model was told.
  write('Playground/sprite-game/chars.js', `${CHARS}\n`);
  fs.rmSync(path.join(root, 'Playground', 'sprite-game', '.crop-return.txt'));
  assert.deepEqual(checks.findings(), []);
  // A broken file the response changes is reported, broken before or not.
  write('Playground/sprite-game/broken.js', 'const a = ;\nconst b = 2;\n');
  assert.deepEqual(checks.findings(), [{kind: 'parse', project: 'Playground/sprite-game', file: 'broken.js', line: 1}]);
});

test('a project the first touch creates reports everything in its new files', t => {
  const {root, write} = workspace(t);
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/new-game']);
  write('Playground/new-game/index.html', '<script src="game.js"></script>');
  write('Playground/new-game/game.js', 'function tick() {}\nfunction tick() {}\nwindow.__DEBUG = true;\n');
  write('Playground/new-game/.scratch.js', 'x');
  write('Playground/new-game/tmp-out/a.txt', 'x');
  assert.deepEqual(checks.findings(), [
    {kind: 'definition', project: 'Playground/new-game', file: 'game.js', name: 'tick', count: 2},
    {kind: 'global', project: 'Playground/new-game', file: 'game.js', name: 'window.__DEBUG'},
    {kind: 'entry', project: 'Playground/new-game', name: '.scratch.js', folder: false},
    {kind: 'entry', project: 'Playground/new-game', name: 'tmp-out', folder: true},
  ]);
});

test('node_modules, dot folders and links are never entered or followed', t => {
  const {root, write, at} = workspace(t, {
    'Playground/game/main.js': 'const a = 1;\n',
    'Playground/game/node_modules/lib/index.js': 'ok = 1;\n',
    'Playground/game/.cache/x.js': 'ok = 1;\n',
    'outside/target/broken.js': 'ok = 1;\n',
  });
  fs.symlinkSync(at('outside/target'), at('Playground/game/linked'), process.platform === 'win32' ? 'junction' : 'dir');
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/game']);
  for (const file of ['Playground/game/node_modules/lib/index.js', 'Playground/game/.cache/x.js', 'outside/target/broken.js'])
    write(file, 'const = ;\n');
  write('outside/target/new.js', 'function x() {}\nfunction x() {}\n');
  assert.deepEqual(checks.findings(), []);
});

test('a linked Playground is the owner\'s layout: its projects are not checked', t => {
  const {root, write, at} = workspace(t, {'elsewhere/game/main.js': 'const a = 1;\n'});
  fs.symlinkSync(at('elsewhere'), at('Playground'), process.platform === 'win32' ? 'junction' : 'dir');
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/game']);
  write('elsewhere/game/main.js', 'const a = ;\n');
  write('elsewhere/game/.crop-return.txt', 'x');
  assert.deepEqual(checks.findings(), []);
});

test('the walk is bounded: a larger project, or files below the depth limit, are not checked', t => {
  const {root, write} = workspace(t);
  for (let i = 0; i <= FILE_CHECK_LIMITS.entries; i++) write(`Playground/big/assets/tile-${i}.txt`, '');
  const deep = `Playground/deep/${Array.from({length: FILE_CHECK_LIMITS.depth}, (_, i) => `d${i}`).join('/')}/x.js`;
  write(deep, 'const a = 1;\n');
  write('Playground/deep/d0/y.js', 'const b = 1;\n');
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/big', 'Playground/deep']);
  write('Playground/big/main.js', 'const = ;\n');
  write(deep, 'const = ;\n');
  write('Playground/deep/d0/y.js', 'const = ;\n');
  assert.deepEqual(checks.findings(), [{kind: 'parse', project: 'Playground/deep', file: 'd0/y.js', line: 1}]);
});

test('module parses are bounded per check, and the same bytes are checked once', t => {
  const {root, write} = workspace(t);
  const sources = [];
  const checks = createChangedFileChecks({checkModule: source => { sources.push(source); return {ok: false, line: 2}; }});
  checks.touch(root, ['Playground/mods']);
  for (let i = 0; i < FILE_CHECK_LIMITS.moduleChecks + 2; i++) write(`Playground/mods/m${i}.mjs`, `export const a = ${i};\n`);
  write('Playground/mods/copy.mjs', 'export const a = 0;\n');
  const first = checks.findings().map(finding => finding.file);
  assert.equal(sources.length, FILE_CHECK_LIMITS.moduleChecks);
  assert.deepEqual(first, ['copy.mjs', 'm0.mjs', 'm1.mjs', 'm2.mjs'], 'copy.mjs and m0.mjs share one check');
  assert.deepEqual(checks.findings().map(finding => finding.file), ['copy.mjs', 'm0.mjs', 'm1.mjs', 'm2.mjs', 'm3.mjs', 'm4.mjs']);
  assert.equal(sources.length, FILE_CHECK_LIMITS.moduleChecks + 2);
  checks.findings();
  assert.equal(sources.length, FILE_CHECK_LIMITS.moduleChecks + 2, 'nothing is parsed twice');
});

const project = 'Playground/elf-of-doom';
const finding = (kind, rest) => ({kind, project, ...rest});

test('the owner reads the findings as one bounded line of fixed text and names', () => {
  const findings = [
    finding('parse', {file: 'chars.js', line: 3255}),
    finding('definition', {file: 'chars.js', name: 'SPRITE_pose', count: 2}),
    finding('global', {file: 'chars.js', name: 'globalThis.__ENTRY'}),
    finding('entry', {name: '.crop-return.txt', folder: false}),
  ];
  assert.equal(fileCheckFacts(findings), "Portal's checks of the changed files in `Playground/elf-of-doom`: " +
    '`chars.js` does not parse (line 3255); `SPRITE_pose` is defined twice in `chars.js`; ' +
    'new debug global `globalThis.__ENTRY` in `chars.js`; new file `.crop-return.txt` in the project folder.');
  assert.equal(fileCheckInstruction(findings), '[ODS Pixel check] Portal checked the files this response changed in ' +
    '`Playground/elf-of-doom`: `chars.js` does not parse (line 3255); `SPRITE_pose` is defined twice in `chars.js`; ' +
    'new debug global `globalThis.__ENTRY` in `chars.js`; new file `.crop-return.txt` in the project folder. ' +
    FILE_CHECK_INSTRUCTION);
  assert.equal(fileCheckFacts([]), '');
  assert.equal(fileCheckInstruction(undefined), '');
  // Several names and kinds: grouped per file, the first three named.
  const names = ['SPRITE_pose', 'SPRITE_blit', 'SPRITE_CACHE', 'drawEnemy', 'drawBoss'];
  assert.equal(fileCheckFacts([
    ...names.map(name => finding('definition', {file: 'chars.js', name, count: 2})),
    finding('definition', {file: 'ui.js', name: 'draw', count: 3}),
    finding('definition', {file: 'engine.js', name: 'step', count: 2}), finding('definition', {file: 'engine.js', name: 'tick', count: 4}),
    finding('global', {file: 'ui.js', name: 'globalThis.__E1'}), finding('global', {file: 'ui.js', name: 'window.__REPLAY'}),
    finding('entry', {name: '.crop-return.txt', folder: false}), finding('entry', {name: '.crop_actual.js', folder: false}),
    finding('entry', {name: 'tmp-out', folder: true}), finding('parse', {file: 'ui.js'}),
  ]), "Portal's checks of the changed files in `Playground/elf-of-doom`: `ui.js` does not parse; " +
    '`SPRITE_pose`, `SPRITE_blit`, `SPRITE_CACHE` and 2 more names are each defined twice in `chars.js`; ' +
    '`draw` is defined 3 times in `ui.js`; `step` and `tick` are defined more than once in `engine.js`; ' +
    'new debug globals `globalThis.__E1` and `window.__REPLAY` in `ui.js`; ' +
    'new files `.crop-return.txt` and `.crop_actual.js` in the project folder; new folder `tmp-out` in the project folder.');
  // Two projects, each named once.
  assert.equal(fileCheckFacts([finding('parse', {file: 'a.js', line: 1}), {kind: 'parse', project: 'Playground/snake', file: 'b.js', line: 2}]),
    "Portal's checks of the changed files in `Playground/elf-of-doom`: `a.js` does not parse (line 1). " +
    'In `Playground/snake`: `b.js` does not parse (line 2).');
});

test('a line past its room keeps the first items whole and counts the rest', () => {
  const findings = Array.from({length: 40}, (_, i) => finding('parse', {file: `src/part-${i}.js`, line: i + 1}));
  const line = fileCheckFacts(findings);
  assert.ok(line.length <= FILE_CHECK_LIMITS.lineChars, `${line.length}`);
  assert.match(line, /^Portal's checks of the changed files in `Playground\/elf-of-doom`: `src\/part-0\.js` does not parse \(line 1\); /);
  const shown = line.match(/does not parse/g).length;
  assert.ok(line.endsWith(`; and ${40 - shown} more.`), line);
  assert.equal(fileCheckFacts(findings, 120), "Portal's checks of the changed files in `Playground/elf-of-doom`: " +
    '`src/part-0.js` does not parse (line 1); and 39 more.');
  assert.equal(fileCheckFacts(findings, 60), '');
  assert.ok(fileCheckInstruction(findings).length <= 1600);
  assert.ok(fileCheckInstruction(findings).endsWith(` more. ${FILE_CHECK_INSTRUCTION}`));
});

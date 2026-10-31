import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createChangedFileChecks, createModuleCheckQueue, debugGlobalWrites, FILE_CHECK_INSTRUCTION, FILE_CHECK_LIMITS, FILE_CHECK_REMOVAL,
  FILE_CHECKS_OFF, fileCheckFacts, fileCheckInstruction, generatedFile, nodeModuleCheck, parseCheck, scratchEntry, topLevelDefinitions,
  touchedProjects} from '../plugin/changed-file-checks.mjs';

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
const LINK = process.platform === 'win32' ? 'junction' : 'dir';
const parsed = result => result && {ok: result.ok, ...(result.line ? {line: result.line} : {})};

function workspace(t, files = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-checks-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const at = relative => path.join(root, ...relative.split('/'));
  const write = (relative, content) => { fs.mkdirSync(path.dirname(at(relative)), {recursive: true}); fs.writeFileSync(at(relative), content); };
  for (const [relative, content] of Object.entries(files)) write(relative, content);
  return {root, at, write};
}

test('top-level definitions are counted at column 0, outside comments and template strings', () => {
  const source = 'function a() {}\nasync function b() {}\nfunction* c() {}\nfunction *d() {}\nconst e = 1;\nlet f, g;\n' +
    'class I {}\nexport function j() {}\nexport const k = 1;\nexport default function l() {}\n' +
    '  function nested() {}\nfunctional = 1;\nconstant = 2;\n// function commented() {}\nfunction a() {}\n' +
    // A var may be declared twice; a commented-out or quoted copy is no definition.
    'var canvas = null;\nvar canvas = document.querySelector("canvas");\n' +
    '/*\nfunction a() { return 0; }\n*/\nconst src = `\nfunction b() {}\n${1}\nclass I {}\n`;\n' +
    "const re = /'/;\nfunction m() {}\nconst s = 'x\\\n';\nfunction n() {}\n" +
    // A regular expression holding /* (a later comment closes no comment of
    // its), and templates nested in a ${} across lines.
    'const re2 = /x\\/*/;\nfunction m2() {}\n/* note */\n' +
    "const page = `<ul>${items.map(item => `\n<li>${item}</li>`).join('')}</ul>`;\nfunction render() {}\n" +
    'const t = `a ${`\nfunction hidden() {}\n`} b`;\nfunction after() {}\n';
  assert.deepEqual(Object.fromEntries(topLevelDefinitions(source)),
    {a: 2, b: 1, c: 1, d: 1, e: 1, f: 1, I: 1, j: 1, k: 1, l: 1, src: 1, re: 1, m: 1, s: 1, n: 1, re2: 1, m2: 1, page: 1, render: 1,
      t: 1, after: 1});
  assert.deepEqual(Object.fromEntries(topLevelDefinitions(DOUBLED)),
    {SPRITE_pose: 2, SPRITE_blit: 2, SPRITE_CACHE: 2, drawEnemy: 2});
  // A backquote the lexer cannot place (JSX text, a regular expression after
  // a closing parenthesis) leaves a template open at the end: every line after
  // it counts, and the lines before it as they were read.
  assert.deepEqual(Object.fromEntries(topLevelDefinitions('const doc = `\nfunction hidden() {}\n`;\n' +
    'function P() { return <p>Press the ` key</p>; }\nfunction P() {}\n')), {doc: 1, P: 2});
  assert.deepEqual(Object.fromEntries(topLevelDefinitions('if (ok) /`/.test(s);\nfunction Q() {}\nfunction Q() {}\n')), {Q: 2});
});

test('debug globals are writes to globalThis, window or self properties named __...', () => {
  const source = [
    'globalThis.__SF = SPRITE_CACHE;', 'window.__SPRITEFOR = (e) => e;', "self['__WARM'] = true;",
    'globalThis.__ENTRY++;', 'window.__E1 += 1;', 'globalThis.__REPLAY ??= [];', 'globalThis.__SF = other;',
    // Reads, comparisons and other names are not writes of a debug global.
    'if (globalThis.__SF) draw();', 'ok = globalThis.__SF === x;', 'ok = window.__E1 == 2;', 'ok = window.__E1 >= 2;',
    'game.window.__x = 1;', 'window.__proto__ = null;', 'globalThis.game = game;', 'window._single = 1;',
    '// globalThis.__OFF = 1;', '  * window.__DOC = 1', '/* self.__BLOCK = 1 */', '/*', 'window.__IN_BLOCK = 1;', '*/',
  ].join('\n');
  assert.deepEqual(Object.fromEntries(debugGlobalWrites(source)), {
    'globalThis.__SF': 2, 'window.__SPRITEFOR': 1, 'self.__WARM': 1, 'globalThis.__ENTRY': 1, 'window.__E1': 1,
    'globalThis.__REPLAY': 1});
});

test('a new entry is reported when it looks like scratch; conventional dotfiles are not', () => {
  for (const name of ['.crop-return.txt', '.crop_actual.js', '.crop', '.tmp-out', '.scratch', '.debug.log', '.notes.json',
    '.frame.png', 'chars.js.bak', 'chars.js.orig', 'out.tmp', 'tmp-build', 'tmp-crops', 'chars-tmp.js', 'build-tmp2', 'NOTES.BAK',
    // Not an rc file: a tool's name has three letters or more before rc.
    '.src.js', '.orc.txt'])
    assert.equal(scratchEntry(name), true, name);
  for (const name of ['.gitignore', '.gitattributes', '.git', '.github', '.editorconfig', '.prettierrc', '.prettierrc.json',
    '.eslintrc.cjs', '.env', '.env.local', '.npmrc', '.nvmrc', '.babelrc', '.vscode', '.idea', '.well-known', '.htaccess',
    '.nojekyll', '.DS_Store', '.next', '.parcel-cache', '.svelte-kit', '.astro', '.nuxt', '.output', '.pytest_cache', '.venv',
    '.cache', '.dockerignore', '.husky', '.foo', 'chars.js', 'index.html', 'README.md', 'tmp', 'template.js', 'attempt.js',
    'chart-tmpl.js', 'backup.js', 'orig.js', undefined,
    // CI, hook, lint and release settings.
    '.travis.yml', '.pre-commit-config.yaml', '.pre-commit-hooks.yaml', '.markdownlint.json', '.markdownlint-cli2.jsonc',
    '.release-it.json', '.codeclimate.yml', '.readthedocs.yaml', '.golangci.yml', '.goreleaser.yaml', '.hadolint.yaml',
    '.mergify.yml', '.lintstagedrc.json', '.mocharc.yml']) assert.equal(scratchEntry(name), false, String(name));
});

test('the parse check finds the doubled file and passes scripts, CommonJS and ES modules', async () => {
  assert.deepEqual(parsed(await parseCheck(CHARS, 'chars.js')), {ok: true});
  assert.deepEqual(parsed(await parseCheck(DOUBLED, 'chars.js')), {ok: false, line: DOUBLED_LINE});
  // The shipped game's own chars.js, doubled the same way around a patch inside
  // the array wolfHitShapes returns: "use strict" lands inside the array.
  const shipped = fs.readFileSync(new URL('../example-projects/elf-of-doom/chars.js', import.meta.url), 'utf8');
  const old = '    hitCircle(33, -31, 3.5),     // nose (2.6x2.2), leading edge\n';
  const prefix = shipped.slice(0, shipped.indexOf(old)), suffix = shipped.slice(shipped.indexOf(old) + old.length);
  assert.deepEqual(parsed(await parseCheck(shipped, 'chars.js')), {ok: true});
  assert.deepEqual(parsed(await parseCheck(`${prefix}${prefix}    hitCircle(34, -31, 3.5),\n${suffix}${suffix}`, 'chars.js')),
    {ok: false, line: prefix.split('\n').length + 1});
  // A classic script with a hashbang; CommonJS with a top-level return.
  assert.deepEqual(await parseCheck('#!/usr/bin/env node\nconst x = 1;\n', 'tool.js'), {ok: true});
  assert.deepEqual(await parseCheck('const a = 1;\nif (a) return;\nmodule.exports = a;\n', 'server.js'), {ok: true});
  assert.deepEqual(await parseCheck('const a = 1;\nif (a) return;\nmodule.exports = a;\n', 'server.cjs'), {ok: true});
  assert.deepEqual(parsed(await parseCheck('const a = 1;\n\nlet = ;\n', 'server.cjs')), {ok: false, line: 3});
  // ES modules, through `node --check`: by name, or by module syntax in a .js file.
  for (const source of ['import {x} from "./x.js";\nexport const y = x + 1;\nawait Promise.resolve();\n',
    'const r = await fetch("x");\n', 'for await (const x of [1]) {}\n', 'for await (const x of [1]) {}\nexport const a = 1;\n',
    'function f() {}\nimport {x} from "./x.js";\n']) assert.deepEqual(await parseCheck(source, 'main.js'), {ok: true}, source);
  assert.deepEqual(parsed(await parseCheck('import {x} from "./x.js";\n\nexport function (\n', 'main.js')), {ok: false, line: 3});
  assert.deepEqual(await parseCheck('export const a = 1;\n', 'lib.mjs'), {ok: true});
  assert.deepEqual(parsed(await parseCheck('const ok = 1;\nexport const = 1;\n', 'lib.mjs')), {ok: false, line: 2});
  // A syntax error ahead of any module syntax is the script's own; a module
  // statement makes the file a module, whose top-level return is the failure.
  assert.deepEqual(parsed(await parseCheck('const a = ;\nconst b = 2;\n', 'main.js')), {ok: false, line: 1});
  assert.deepEqual(parsed(await parseCheck('if (x) return;\nexport const a = 1;\n', 'main.js')), {ok: false, line: 1});
  // Nothing is claimed when the check cannot finish.
  assert.equal(await parseCheck('export const a = 1;\n', 'main.js', async () => undefined), undefined);
  assert.equal(await parseCheck('['.repeat(200000), 'deep.js'), undefined);
  assert.equal(await nodeModuleCheck('export const a = 1;\n', 1), undefined, 'past its timeout');
});

test('the module check never runs the module, nor anything the gateway\'s environment preloads', async t => {
  const {at, write} = workspace(t);
  const marker = at('ran.txt'), preloaded = at('preloaded.txt');
  write('preload.cjs', `require('node:fs').writeFileSync(${JSON.stringify(preloaded)}, 'preloaded');\n`);
  const saved = process.env.NODE_OPTIONS;
  process.env.NODE_OPTIONS = `--require ${JSON.stringify(at('preload.cjs'))}`;
  t.after(() => { if (saved === undefined) delete process.env.NODE_OPTIONS; else process.env.NODE_OPTIONS = saved; });
  const source = `import fs from 'node:fs';\nfs.writeFileSync(${JSON.stringify(marker)}, 'ran');\nexport const a = 1;\n`;
  assert.deepEqual(await parseCheck(source, 'main.mjs'), {ok: true});
  assert.equal(fs.existsSync(marker), false);
  assert.equal(fs.existsSync(preloaded), false);
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
  // A path joined in a script, cd from the Playground folder, a native
  // Windows separator, and a Windows root in another letter case.
  assert.deepEqual(touchedProjects('exec', {command: "python3 -c \"import os; open(os.path.join('Playground', 'elf-of-doom', 'chars.js'), 'a')\"",
    workdir: '/workspace'}, root), ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('exec', {command: "python3 - <<'EOF'\nfrom pathlib import Path\np = Path('Playground') / 'snake' / 'x.js'\nEOF"}, root),
    ['Playground/snake']);
  assert.deepEqual(touchedProjects('exec', {command: 'cd snake && python3 patch.py', workdir: '/workspace/Playground'}, root), ['Playground/snake']);
  assert.deepEqual(touchedProjects('exec', {command: 'cd snake/src && python3 patch.py', workdir: '/workspace/Playground'}, root),
    ['Playground/snake']);
  assert.deepEqual(touchedProjects('exec', {command: 'cd Playground && cd elf-of-doom && python3 patch.py', workdir: '/workspace'}, root),
    ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('exec', {command: 'cd Playground\\elf-of-doom && python patch.py'}, root), ['Playground/elf-of-doom']);
  assert.deepEqual(touchedProjects('exec', {command: 'python patch.py', workdir: 'c:\\users\\ods\\workspace\\Playground\\snake'},
    'C:\\Users\\ods\\workspace'), ['Playground/snake']);
  // Reads change nothing; other folders, other spellings and bare names are not projects.
  for (const [tool, params] of [['read', {path: 'Playground/elf-of-doom/chars.js'}], ['write', {path: 'notes/todo.md'}],
    ['write', {path: 'playground/snake/main.js'}], ['exec', {command: 'ls Playground/', workdir: '/workspace'}],
    ['exec', {command: 'cat MyPlayground/snake/x.js'}], ['exec', {command: 'ls', workdir: '/workspace/Playground'}],
    ['exec', {command: 'cd snake', workdir: '/workspace'}], ['write', {path: 'Playground/con/x.js'}],
    ['exec', {command: 'cd Playground && ls', workdir: '/workspace'}],
    ['exec', {command: `echo '["Playground", "notes"]' > folders.json`, workdir: '/workspace'}],
    ['tool_call', {id: 'openclaw:core:read', args: {path: 'Playground/a/b.js'}}],
    ['web_fetch', {url: 'https://example.com/Playground/x'}]]) assert.deepEqual(touchedProjects(tool, params, root), [], JSON.stringify(params));
  // At most FILE_CHECK_LIMITS.projects per call.
  const many = Array.from({length: 9}, (_, i) => `Playground/game-${i}/x.js`).join(' ');
  assert.equal(touchedProjects('exec', {command: `cat ${many}`}, root).length, FILE_CHECK_LIMITS.projects);
});

test('only what changed since first touch is reported, and a finding is told once, again only after it was gone', t => {
  const {root, write} = workspace(t, {
    'Playground/sprite-game/chars.js': `${CHARS}function legacy() {}\nfunction legacy() {}\nglobalThis.__OLD = legacy;\n`,
    'Playground/sprite-game/old.bak': 'x',
    'Playground/other/chars.js': CHARS,
  });
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/sprite-game']);
  // Nothing changed: what was already there is not this response's.
  assert.deepEqual(checks.findings(), []);
  // The exec that rewrote chars.js, a debug global, a scratch file and a
  // change to a project this response never touched.
  const doubled = `${DOUBLED}function legacy() {}\nfunction legacy() {}\nglobalThis.__OLD = legacy;\nglobalThis.__SF = SPRITE_CACHE;\n`;
  write('Playground/sprite-game/chars.js', doubled);
  write('Playground/sprite-game/.crop-return.txt', '{"x": 1}');
  write('Playground/sprite-game/assets/.hidden.json', 'x');
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
  write('Playground/sprite-game/chars.js', `${doubled}function legacy() {}\n`);
  assert.deepEqual(checks.take(), [{kind: 'definition', project: 'Playground/sprite-game', file: 'chars.js', name: 'legacy', count: 3}]);
  // Fixed: nothing is left to report.
  write('Playground/sprite-game/chars.js', `${CHARS}\n`);
  fs.rmSync(path.join(root, 'Playground', 'sprite-game', '.crop-return.txt'));
  assert.deepEqual(checks.take(), []);
  assert.deepEqual(checks.findings(), []);
  // Broken again: told again.
  write('Playground/sprite-game/chars.js', `${CHARS}const = 1;\n`);
  assert.deepEqual(checks.take(), [{kind: 'parse', project: 'Playground/sprite-game', file: 'chars.js', line: CHARS.split('\n').length}]);
});

test('a file broken at first touch is not reported when it changes; a new file, or one that parsed then, is', t => {
  const {root, write, at} = workspace(t, {'Playground/wip/main.js': 'const a = 1;\n', 'Playground/wip/experiment.js': 'function half( {\n'});
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/wip']);
  // The owner's unfinished experiment, touched by a formatter: still broken, not this response's.
  const later = new Date(Date.now() + 5000);
  fs.utimesSync(at('Playground/wip/experiment.js'), later, later);
  write('Playground/wip/experiment.js', 'function half( {\n  return 1;\n');
  assert.deepEqual(checks.findings(), []);
  write('Playground/wip/main.js', 'const a = ;\n');
  write('Playground/wip/added.js', 'const b = ;\n');
  assert.deepEqual(checks.findings(), [
    {kind: 'parse', project: 'Playground/wip', file: 'added.js', line: 1},
    {kind: 'parse', project: 'Playground/wip', file: 'main.js', line: 1},
  ]);
});

test('JSX, and a file the project\'s HTML loads as text/babel, are not reported for their <', async t => {
  const {root, write} = workspace(t, {
    'Playground/cra/src/App.js': "import React from 'react';\nfunction App() { return null; }\nexport default App;\n",
    'Playground/babel/index.html': '<script src="https://unpkg.com/@babel/standalone"></script>\n<script type="text/babel" src="./app.js"></script>\n',
    'Playground/babel/app.js': 'const root = 1;\n',
  });
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/cra', 'Playground/babel']);
  write('Playground/cra/src/App.js', "import React from 'react';\nfunction App() {\n  return (<div className=\"App\"><h1>Hello</h1></div>);\n}\nexport default App;\n");
  write('Playground/cra/src/Card.js', 'export const Card = props => <section>{props.children}</section>;\n');
  // In-browser Babel: no tag on the failing line's own terms, loaded as text/babel.
  write('Playground/babel/app.js', 'const root = 1;\nconst element = < Game / >;\n');
  await checks.settle();
  assert.deepEqual(checks.findings(), []);
  // Not JSX: a stray < is a failure.
  write('Playground/cra/src/util.js', 'export const less = (a, b) => a < ;\n');
  await checks.settle();
  checks.findings();
  await checks.settle();
  assert.deepEqual(checks.findings({final: true}), [{kind: 'parse', project: 'Playground/cra', file: 'src/util.js', line: 1}]);
});

test('a file the project\'s HTML loads as a classic script parses as one: pasted tags, export and top-level await fail there', async t => {
  const {root, write} = workspace(t, {
    'Playground/g/index.html': '<canvas id="c"></canvas>\n<script src="game.js"></script>\n<script src="js/ui.js?v=2"></script>\n' +
      "<script type='text/javascript' src='./js/net.js'></script>\n<script type=\"module\" src=\"/src/main.js\"></script>\n" +
      '<script src="https://cdn.example.com/lib.js"></script>\n',
    'Playground/g/game.js': 'function tick() {}\n',
    'Playground/g/js/ui.js': 'const ui = {};\n',
    'Playground/g/js/net.js': 'const net = {};\n',
    'Playground/g/src/main.js': 'const main = 1;\n',
    'Playground/g/levels.js': 'const levels = [];\n',
    // A page in a folder loads its script from that folder.
    'Playground/g/levels/one.html': '<script src="one.js"></script>\n',
    'Playground/g/levels/one.js': 'const one = 1;\n',
  });
  // A stand-in for node --check: a module may not use with (strict mode).
  const checks = createChangedFileChecks({checkModule: async source => source.includes('with (')
    ? {ok: false, line: 2, message: 'Strict mode code may not include a with statement'} : {ok: true}});
  checks.touch(root, ['Playground/g']);
  // The model moved the inline script into game.js and kept its tags.
  write('Playground/g/game.js', '<script>\nconst canvas = document.getElementById("c");\nfunction tick() {}\n</script>\n');
  write('Playground/g/js/ui.js', 'export const ui = {};\n');
  write('Playground/g/js/net.js', 'const data = await fetch("levels.json");\n');
  write('Playground/g/levels/one.js', 'export const one = 1;\n');
  // Loaded as a module: parsed as one, even with no module syntax. Loaded by
  // no page: module syntax is its to use.
  write('Playground/g/src/main.js', 'const main = 1;\nwith (Math) { main2 = PI; }\n');
  write('Playground/g/levels.js', 'export const levels = await fetch("levels.json");\n');
  // Delivery scans and asks for what it needs, with no result in between.
  await checks.settle();
  assert.deepEqual(checks.findings({final: true}), [
    {kind: 'parse', project: 'Playground/g', file: 'game.js', line: 1},
    {kind: 'parse', project: 'Playground/g', file: 'js/net.js', line: 1},
    {kind: 'parse', project: 'Playground/g', file: 'js/ui.js', line: 1},
    {kind: 'parse', project: 'Playground/g', file: 'levels/one.js', line: 1},
    {kind: 'parse', project: 'Playground/g', file: 'src/main.js', line: 2},
  ]);
  // A page changed in the response is read again.
  write('Playground/g/index.html', '<script src="game.js"></script>\n<script src="extra.js"></script>\n');
  write('Playground/g/extra.js', 'export const extra = 1;\n');
  await checks.settle();
  assert.deepEqual(checks.findings({final: true}).filter(finding => finding.file === 'extra.js'),
    [{kind: 'parse', project: 'Playground/g', file: 'extra.js', line: 1}]);
});

test('vendored and minified files get the parse check only; a lib folder or a long line is not one; dist, build and venv are not entered', t => {
  const {root, write} = workspace(t, {'Playground/three/main.js': 'const a = 1;\n'});
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/three']);
  const three = "const REVISION = '160';\nif ( window.__THREE__ ) {\n} else {\n\twindow.__DEBUG_HOOK = REVISION;\n}\nfunction a() {}\nfunction a() {}\n";
  // Vendored: a vendor folder, a .min.js name, a library's file name, a license banner; minified: long lines on average.
  write('Playground/three/vendor/sprites.js', three);
  write('Playground/three/js/game.min.js', three);
  write('Playground/three/lib/three.js', three);
  write('Playground/three/js/helpers.js', `/*! helpers v2 | MIT */\n${three}`);
  write('Playground/three/js/bundle.js', `${three.replaceAll('\n', ';')}var pad = ${'1+'.repeat(FILE_CHECK_LIMITS.minifiedLine)}1;\n`);
  // The project's own code: a lib folder, a level written on one long line, a file named like a game's stats.
  write('Playground/three/lib/engine.js', three);
  write('Playground/three/js/level.js', `${three}const LEVEL = "${'#'.repeat(FILE_CHECK_LIMITS.minifiedLine + 200)}";\n`);
  write('Playground/three/js/stats.js', 'function show() {}\nfunction show() {}\n');
  for (const folder of ['dist', 'build', 'venv', 'node_modules', '.next']) write(`Playground/three/${folder}/chunk.js`, 'const = ;\n');
  write('Playground/three/vendor/broken.js', 'const = ;\n');
  const own = file => [{kind: 'definition', project: 'Playground/three', file, name: 'a', count: 2},
    {kind: 'global', project: 'Playground/three', file, name: 'window.__DEBUG_HOOK'}];
  assert.deepEqual(checks.findings(), [...own('js/level.js'),
    {kind: 'definition', project: 'Playground/three', file: 'js/stats.js', name: 'show', count: 2}, ...own('lib/engine.js'),
    {kind: 'parse', project: 'Playground/three', file: 'vendor/broken.js', line: 1}]);
  assert.equal(generatedFile('src/app.js', '/**\n * Game helpers.\n * @license MIT\n */\nconst a = 1;\n'), true);
  assert.equal(generatedFile('src/app.js', '/** Game helpers. */\nconst a = 1;\n// @license MIT\n'), false);
});

test('library and devtools hooks, and names the owner\'s request uses, are not debug globals', t => {
  const {root, write} = workspace(t, {'Playground/pixi/main.js': 'const app = {};\n'});
  const checks = createChangedFileChecks({requested: 'In Playground/pixi, expose the game as window.__GAME for the console.'});
  checks.touch(root, ['Playground/pixi']);
  write('Playground/pixi/main.js', ['const app = {};', 'window.__GAME = app;', 'globalThis.__PIXI_APP__ = app;', 'window.__THREE__ = "160";',
    'window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = hook;', 'window.__VUE_DEVTOOLS_HOOK_REPLAY__ = [];', 'window.__REDUX_DEVTOOLS_EXTENSION__ = x;',
    'window.__GAMEPLAY = 1;', 'globalThis.__SF = 1;'].join('\n'));
  assert.deepEqual(checks.findings().map(finding => finding.name), ['window.__GAMEPLAY', 'globalThis.__SF']);
});

test('node_modules, dot folders and links are never entered or followed, and hard-linked files are never read', t => {
  const {root, write, at} = workspace(t, {
    'Playground/game/main.js': 'const a = 1;\n',
    'Playground/game/node_modules/lib/index.js': 'ok = 1;\n',
    'Playground/game/.cache/x.js': 'ok = 1;\n',
    'outside/target/broken.js': 'ok = 1;\n',
    'outside/secret.js': 'ok = 1;\n',
  });
  fs.symlinkSync(at('outside/target'), at('Playground/game/linked'), LINK);
  fs.linkSync(at('outside/secret.js'), at('Playground/game/hard.js'));
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/game']);
  for (const file of ['Playground/game/node_modules/lib/index.js', 'Playground/game/.cache/x.js', 'outside/target/broken.js', 'outside/secret.js'])
    write(file, 'const = ;\nfunction x() {}\nfunction x() {}\n');
  write('outside/target/new.js', 'function x() {}\nfunction x() {}\n');
  assert.deepEqual(checks.findings(), []);
});

test('a linked Playground is the owner\'s layout: its projects are not checked', t => {
  const {root, write, at} = workspace(t, {'elsewhere/game/main.js': 'const a = 1;\n'});
  fs.symlinkSync(at('elsewhere'), at('Playground'), LINK);
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/game']);
  write('elsewhere/game/main.js', 'const a = ;\n');
  write('elsewhere/game/.crop-return.txt', 'x');
  assert.deepEqual(checks.findings(), []);
});

test('the walk is bounded: a larger project is said to be not checked, files below the depth limit are left out', t => {
  const {root, write} = workspace(t);
  for (let i = 0; i <= FILE_CHECK_LIMITS.entries; i++) write(`Playground/big/assets/tile-${i}.txt`, '');
  // Generated folders do not count toward the bound.
  for (let i = 0; i <= FILE_CHECK_LIMITS.entries; i++) write(`Playground/app/node_modules/pkg/f-${i}.js`, '');
  const deep = `Playground/deep/${Array.from({length: FILE_CHECK_LIMITS.depth}, (_, i) => `d${i}`).join('/')}/x.js`;
  write(deep, 'const a = 1;\n');
  write('Playground/deep/d0/y.js', 'const b = 1;\n');
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/big', 'Playground/deep', 'Playground/app']);
  write('Playground/big/main.js', 'const = ;\n');
  write(deep, 'const = ;\n');
  write('Playground/deep/d0/y.js', 'const = ;\n');
  write('Playground/app/main.js', 'const = ;\n');
  assert.deepEqual(checks.findings(), [
    {kind: 'unchecked', project: 'Playground/big'},
    {kind: 'parse', project: 'Playground/deep', file: 'd0/y.js', line: 1},
    {kind: 'parse', project: 'Playground/app', file: 'main.js', line: 1},
  ]);
  assert.equal(fileCheckInstruction(checks.findings()).includes('Playground/big'), false, 'the model is not told');
});

test('at most four projects per response are fingerprinted', t => {
  const {root, write} = workspace(t);
  const names = Array.from({length: FILE_CHECK_LIMITS.projects + 2}, (_, i) => `Playground/game-${i}`);
  for (const name of names) write(`${name}/main.js`, 'const a = 1;\n');
  const checks = createChangedFileChecks();
  checks.touch(root, names.slice(0, 3));
  checks.touch(root, names.slice(3));
  for (const name of names) write(`${name}/main.js`, 'const a = ;\n');
  assert.deepEqual(checks.findings().map(finding => finding.project), names.slice(0, FILE_CHECK_LIMITS.projects));
});

test('counts are compared only with a first touch that read the file', t => {
  // The first touch reads a.js (29 bytes) and has 11 of its 40 bytes left: b.js (16) is not read.
  const {root, write} = workspace(t, {'Playground/game/a.js': `const a = ${'1'.repeat(17)};\n`, 'Playground/game/b.js': 'const b = 1;\n//\n'});
  const checks = createChangedFileChecks({limits: {readBytes: 40}});
  checks.touch(root, ['Playground/game']);
  write('Playground/game/b.js', 'class y{}\nclass y{}\n');
  write('Playground/game/c.js', 'class z{}\nclass z{}\n');
  // b.js's definitions at first touch are unknown, so its own count is not compared; c.js is new.
  assert.deepEqual(checks.findings().filter(finding => finding.kind === 'definition').map(finding => finding.file), ['c.js']);
});

test('a file rewritten with the same size and mtime is read again', t => {
  const {root, write, at} = workspace(t, {'Playground/game/a.js': 'const a = 1;\n'});
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/game']);
  // Both versions carry the same whole-second mtime, so only ctime and the inode tell them apart.
  const second = new Date('2026-10-30T12:00:00Z');
  write('Playground/game/a.js', 'function x() {}\nfunction x() {}\n');
  fs.utimesSync(at('Playground/game/a.js'), second, second);
  assert.equal(checks.findings().length, 1);
  write('Playground/game/a.js', 'function x() {}\nfunction y() {}\n');
  fs.utimesSync(at('Playground/game/a.js'), second, second);
  assert.equal(fs.statSync(at('Playground/game/a.js')).mtimeMs, second.getTime());
  assert.deepEqual(checks.findings(), []);
});

test('the same finding in two projects is told for each, and names the texts cannot print are left out', t => {
  const {root, write} = workspace(t, {'Playground/a/x.js': 'const a = 1;\n', 'Playground/b/x.js': 'const a = 1;\n'});
  const checks = createChangedFileChecks();
  checks.touch(root, ['Playground/a', 'Playground/b']);
  write('Playground/a/x.js', 'const = ;\n');
  assert.equal(checks.take().length, 1);
  write('Playground/b/x.js', 'const = ;\n');
  write('Playground/b/bad`name.js', 'const = ;\n');
  assert.deepEqual(checks.take(), [{kind: 'parse', project: 'Playground/b', file: 'x.js', line: 1}]);
});

test('module parses run in the background on changed versions only, two at a time, each version once for every response', async t => {
  const {root, write} = workspace(t, {'Playground/mods/base.mjs': 'export const base = 1;\n', 'Playground/mods/keep.mjs': 'export const keep = 1;\n'});
  const sources = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const queue = createModuleCheckQueue({checkModule: async source => {
    sources.push(source);
    await gate;
    return /= ;/.test(source) ? {ok: false, line: 1} : {ok: true};
  }});
  const checks = createChangedFileChecks({queue, limits: {moduleChecks: 4, moduleFinal: 1, moduleBaseline: 1}});
  checks.touch(root, ['Playground/mods']);
  assert.deepEqual(sources, [], 'the first touch parses no module');
  write('Playground/mods/base.mjs', 'export const base = ;\n');
  write('Playground/mods/copy.mjs', 'export const a0 = ;\n');
  for (let i = 0; i < 5; i++) write(`Playground/mods/m${i}.mjs`, `export const a${i} = ;\n`);
  // Not yet known: nothing told; two run, two wait, the rest are past the
  // response's checks while it works. m0 is copy.mjs's version.
  assert.deepEqual(checks.take(), []);
  assert.equal(sources.length, 2, 'two at a time');
  release();
  // At delivery: m3 from the final checks, and base.mjs as it was at first
  // touch, now that its changed version fails. m4 is past both.
  await checks.settle();
  assert.deepEqual(sources.slice(2).sort(),
    ['export const a1 = ;\n', 'export const a2 = ;\n', 'export const a3 = ;\n', 'export const base = 1;\n']);
  // Results come on a later result, even one that walks nothing.
  assert.deepEqual(checks.take({walk: false}).map(finding => finding.file), ['base.mjs', 'copy.mjs', 'm0.mjs', 'm1.mjs', 'm2.mjs', 'm3.mjs']);
  assert.deepEqual(checks.take({walk: false}), []);
  assert.deepEqual(checks.findings({final: true}).filter(finding => finding.kind === 'unparsed'),
    [{kind: 'unparsed', project: 'Playground/mods', file: 'm4.mjs'}]);
  assert.equal(sources.length, 6);
  // Another response through the same queue: a version parsed before is
  // known at once, and not parsed again.
  const next = createChangedFileChecks({queue});
  next.touch(root, ['Playground/mods']);
  write('Playground/mods/m5.mjs', 'export const a0 = ;\n');
  assert.deepEqual(next.take(), [{kind: 'parse', project: 'Playground/mods', file: 'm5.mjs', line: 1}]);
  assert.equal(sources.length, 6);
});

test('a newer version of a file takes the place of its check still waiting', async t => {
  const {root, write} = workspace(t);
  const sources = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const queue = createModuleCheckQueue({checkModule: async source => { sources.push(source); await gate; return {ok: true}; }, limits: {concurrency: 1}});
  const checks = createChangedFileChecks({queue});
  checks.touch(root, ['Playground/mods']);
  write('Playground/mods/a.mjs', 'export const a = 1;\n');
  write('Playground/mods/b.mjs', 'export const b = 1;\n');
  checks.take();
  // b.mjs waits behind a.mjs; two newer versions of it arrive meanwhile.
  write('Playground/mods/b.mjs', 'export const b = 2;\n');
  checks.take();
  write('Playground/mods/b.mjs', 'export const b = 3;\n');
  checks.take();
  release();
  await checks.settle();
  assert.deepEqual(sources, ['export const a = 1;\n', 'export const b = 3;\n']);
  assert.deepEqual(checks.findings({final: true}), []);
});

test('the first touch of many modules parses none; only a failing file is parsed as it was then', async t => {
  const files = {'Playground/big/style.css': 'body {}\n'};
  for (let i = 0; i < 20; i++) files[`Playground/big/src/m${String(i).padStart(2, '0')}.mjs`] = `export const m${i} = ${i};\n`;
  const {root, write} = workspace(t, files);
  const sources = [];
  const checks = createChangedFileChecks({checkModule: async source => {
    sources.push(source);
    return source.includes('BROKEN') ? {ok: false, line: 1} : {ok: true};
  }});
  checks.touch(root, ['Playground/big']);
  // A change to the style sheet alone parses nothing, and delivery does not wait.
  write('Playground/big/style.css', 'body { color: red; }\n');
  assert.deepEqual(checks.take(), []);
  const started = Date.now();
  await checks.settle();
  assert.ok(Date.now() - started < 1000);
  assert.deepEqual(sources, []);
  write('Playground/big/src/m19.mjs', 'export const m19 = BROKEN BROKEN;\n');
  write('Playground/big/src/m03.mjs', 'export const m3 = BROKEN BROKEN;\n');
  write('Playground/big/src/m10.mjs', 'export const m10 = 100;\n');
  checks.take();
  await checks.settle();
  assert.deepEqual(checks.findings({final: true}), [
    {kind: 'parse', project: 'Playground/big', file: 'src/m03.mjs', line: 1},
    {kind: 'parse', project: 'Playground/big', file: 'src/m19.mjs', line: 1},
  ]);
  assert.equal(sources.length, 5, 'the three changed versions, and the two failing ones as they were at first touch');
});

test('a told parse failure stays told while the next version of its file is still being checked', async t => {
  const {root, write} = workspace(t);
  let release;
  const checks = createChangedFileChecks({checkModule: async source => {
    if (source.includes('again')) await new Promise(resolve => { release = resolve; });
    return {ok: false, line: 1};
  }});
  checks.touch(root, ['Playground/mods']);
  write('Playground/mods/m.mjs', 'export const = ;\n');
  checks.take();
  await checks.settle();
  assert.equal(checks.take({walk: false}).length, 1);
  // Still broken, in a version whose check has not finished: not forgotten,
  // so not told again when that check finds the same.
  write('Playground/mods/m.mjs', 'export const = ; // again\n');
  assert.deepEqual(checks.take(), []);
  release();
  await checks.settle();
  assert.deepEqual(checks.take({walk: false}), []);
});

test('a failing module whose first-touch version could not be parsed in time is the owner\'s to hear about, as unknown before', async t => {
  const {root, write} = workspace(t, {'Playground/mods/m.mjs': 'export const a = 1; // first touch\n'});
  const checks = createChangedFileChecks({checkModule: source => source.includes('first touch') ? new Promise(() => {}) : {ok: false, line: 1}});
  checks.touch(root, ['Playground/mods']);
  write('Playground/mods/m.mjs', 'export const = ;\n');
  checks.take();
  await checks.settle(50);
  // It may have been broken already: the model is not told, and the owner
  // reads that it does not parse, and that before could not be checked.
  assert.deepEqual(checks.take({walk: false}), []);
  assert.deepEqual(checks.findings(), []);
  const found = checks.findings({final: true});
  assert.deepEqual(found, [{kind: 'parse', project: 'Playground/mods', file: 'm.mjs', line: 1, before: 'unknown'}]);
  assert.equal(fileCheckFacts(found), "Portal's checks of the changed files in `Playground/mods`: `m.mjs` does not parse (line 1); " +
    'whether it parsed before these changes could not be checked.');
  assert.equal(fileCheckInstruction(found), '');
});

test('a module check that never ends is not waited for past the settle time, and could not be checked', async t => {
  const {root, write} = workspace(t);
  const checks = createChangedFileChecks({checkModule: () => new Promise(() => {})});
  checks.touch(root, ['Playground/mods']);
  write('Playground/mods/m.mjs', 'export const a = 1;\n');
  assert.deepEqual(checks.findings(), []);
  const started = Date.now();
  await checks.settle(50);
  assert.ok(Date.now() - started < 2000);
  assert.deepEqual(checks.findings({final: true}), [{kind: 'unparsed', project: 'Playground/mods', file: 'm.mjs'}]);
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
    `${FILE_CHECK_INSTRUCTION} ${FILE_CHECK_REMOVAL}`);
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
    finding('unparsed', {file: 'mod.mjs'}),
  ]), "Portal's checks of the changed files in `Playground/elf-of-doom`: could not check whether `mod.mjs` parses; " +
    '`ui.js` does not parse; `SPRITE_pose`, `SPRITE_blit`, `SPRITE_CACHE` and 2 more names are each defined twice in `chars.js`; ' +
    '`draw` is defined 3 times in `ui.js`; `step` and `tick` are defined more than once in `engine.js`; ' +
    'new debug globals `globalThis.__E1` and `window.__REPLAY` in `ui.js`; ' +
    'new files `.crop-return.txt` and `.crop_actual.js` in the project folder; new folder `tmp-out` in the project folder.');
  // Two projects, each named once; a larger project not checked; what an
  // earlier response could not show, after this one's.
  assert.equal(fileCheckFacts([finding('parse', {file: 'a.js', line: 1}), {kind: 'unchecked', project: 'Playground/big'}], 900,
    [{kind: 'parse', project: 'Playground/snake', file: 'b.js', line: 2}]),
  "Portal's checks of the changed files in `Playground/elf-of-doom`: `a.js` does not parse (line 1). " +
    'In `Playground/big`: not checked, the folder has more than 2,000 files and folders. ' +
    'Still found from earlier changes in `Playground/snake`: `b.js` does not parse (line 2).');
  // A defect switched the checks off in the turn: said first. What an earlier
  // response could not check is not carried.
  assert.equal(fileCheckFacts([], 900, [], true), FILE_CHECKS_OFF);
  assert.equal(fileCheckFacts([finding('parse', {file: 'a.js', line: 1})], 900, [finding('unparsed', {file: 'm.mjs'})], true),
    `${FILE_CHECKS_OFF} Portal's checks of the changed files in \`Playground/elf-of-doom\`: \`a.js\` does not parse (line 1).`);
  assert.equal(fileCheckFacts([], 900, [{kind: 'unchecked', project: 'Playground/big'}]), '');
});

test('the model is never told of a new folder or of what could not be checked', () => {
  // A folder goes with rm -rf, which Portal refuses for the rest of the response.
  for (const owner of [finding('entry', {name: 'tmp-crops', folder: true}), finding('unparsed', {file: 'm.mjs'}),
    {kind: 'unchecked', project}]) {
    assert.equal(fileCheckInstruction([owner]), '');
    assert.ok(fileCheckFacts([owner]).startsWith("Portal's checks of the changed files in"));
  }
  assert.equal(fileCheckInstruction([finding('entry', {name: 'tmp-crops', folder: true}), finding('parse', {file: 'a.js', line: 2})]),
    `[ODS Pixel check] Portal checked the files this response changed in \`${project}\`: \`a.js\` does not parse (line 2). ${FILE_CHECK_INSTRUCTION}`);
});

test('a line past its room keeps the first items whole and counts the rest, in one pass', () => {
  const findings = Array.from({length: 40}, (_, i) => finding('parse', {file: `src/part-${i}.js`, line: i + 1}));
  const line = fileCheckFacts(findings);
  assert.ok(line.length <= FILE_CHECK_LIMITS.ownerChars, `${line.length}`);
  assert.match(line, /^Portal's checks of the changed files in `Playground\/elf-of-doom`: `src\/part-0\.js` does not parse \(line 1\); /);
  const shown = line.match(/does not parse/g).length;
  assert.ok(line.endsWith(`; and ${40 - shown} more.`), line);
  assert.equal(fileCheckFacts(findings, 120), "Portal's checks of the changed files in `Playground/elf-of-doom`: " +
    '`src/part-0.js` does not parse (line 1); and 39 more.');
  assert.equal(fileCheckFacts(findings, 60), '');
  assert.ok(fileCheckInstruction(findings).length <= FILE_CHECK_LIMITS.modelChars);
  assert.ok(fileCheckInstruction(findings).endsWith(` more. ${FILE_CHECK_INSTRUCTION}`));
  // Thousands of findings compose in a few milliseconds, not quadratic time.
  const many = Array.from({length: 6000}, (_, i) => finding('parse', {file: `parts/p-${i}.js`, line: 1}));
  const started = performance.now();
  fileCheckFacts(many);
  fileCheckInstruction(many);
  assert.ok(performance.now() - started < 500, `${performance.now() - started} ms`);
});

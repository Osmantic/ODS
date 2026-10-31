// Portal's checks of the files a response changed in a Playground project.
// On 2026-10-30/31 Portal left a game in states it did not report: a one-off
// Python patch run through exec rewrote chars.js as prefix + prefix + new +
// suffix + suffix (SPRITE_pose and SPRITE_blit defined twice, a SyntaxError
// at line 3255); debug globals such as globalThis.__SF stayed in the game
// code, one of them written every frame; and scratch files such as
// .crop-return.txt stayed in the project folder. Each was removed only after
// the owner named it.
//
// The first time a response's tool call may change Playground/<project> (a
// write, edit or patch there, an exec whose workdir is there, or a command
// that names the folder), the folder is fingerprinted before the call runs:
// each regular file's size and mtime; each JavaScript file's parse status,
// top-level definitions and debug-global writes; and the names in the project
// folder. A command can change any file, so a file changed when its size or
// mtime differs, or when it is new. Then:
// - a changed .js/.mjs/.cjs file that is new, or parsed at first touch, still
//   parses (scripts and CommonJS in process; ES modules with `node --check`,
//   in the background, which never runs them). A JSX file's `<` is not a
//   failure;
// - no top-level function, class, const or let is defined more often than at
//   first touch (outside comments and template strings);
// - no globalThis/window/self.__name write was added, other than library and
//   devtools hooks and names the owner's request uses;
// - no scratch file or folder was added to the project folder. Conventional
//   dotfiles (.gitignore, .env, .eslintrc.json, build caches) are not scratch.
// Vendored, minified and generated files get the parse check only, and
// node_modules, dist, build, venv and dot folders are not entered.
// tool-loop-guard.mjs tells the model each file finding once, on a tool
// result, and lists what is still found under the answer. New folders and
// what could not be checked are told to the owner only: a folder is removed
// with rm -rf, which Portal refuses for the rest of the response. Links are
// never followed, every walk, read and parse is bounded, and nothing here
// changes a file.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {filesystemError} from './example-projects.mjs';
import {workspaceMutationFiles} from './workspace-projects.mjs';

export const FILE_CHECK_LIMITS = Object.freeze({
  // Project folders fingerprinted per response.
  projects: 4,
  // Entries one walk visits, and folders deep; a larger project is not checked,
  // and the owner reads that it was not.
  entries: 2000,
  depth: 8,
  // JavaScript read per file, and per fingerprint or check.
  fileBytes: 2 * 1024 * 1024,
  readBytes: 16 * 1024 * 1024,
  // ES module parses (`node --check`), in the background: per response, of
  // which at most moduleBaseline for files as they were at first touch, so
  // many at a time, each this long at most. Delivery waits for them settleMs.
  moduleChecks: 32,
  moduleBaseline: 16,
  moduleConcurrency: 2,
  moduleTimeoutMs: 2000,
  settleMs: 4000,
  // A line this long marks a minified or generated file.
  minifiedLine: 1000,
  // Names listed per finding; characters in the owner's line and the model's notice.
  names: 3,
  ownerChars: 900,
  modelChars: 1600,
});

// Tools whose call can change a project's files, and so can touch it first.
export const FILE_CHANGING_TOOLS = Object.freeze(['write', 'edit', 'apply_patch', 'exec']);

// ------------------------------------------------------------- the touch --

const PROJECT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const projectFolderName = name => typeof name === 'string' && PROJECT_NAME.test(name) && !name.endsWith('.') &&
  !RESERVED.test(name) ? `Playground/${name}` : undefined;
// Playground/<name> where a command names it: a cd target, an argument, or a
// path in a script's own text.
const COMMAND_PROJECT = /(?<![A-Za-z0-9._-])Playground[\\/]([A-Za-z0-9][A-Za-z0-9._-]{0,63})(?![A-Za-z0-9._-])/g;
// os.path.join('Playground', '<name>'), Path('Playground') / '<name>'.
const JOINED_PROJECT = /(['"])Playground\1\s*\)?\s*[,/]\s*(['"])([A-Za-z0-9][A-Za-z0-9._-]{0,63})\2/g;
// cd <name> as a step of a command run in the Playground folder itself.
const ENTERED_PROJECT = /(?:^|[;&|(\n])\s*cd\s+(['"]?)([A-Za-z0-9][A-Za-z0-9._-]{0,63})\1(?=[ \t]*(?:$|[;&|)\n]))/g;

// A path relative to the workspace: /workspace/... and <root>/... (any letter
// case on a Windows root) lose that prefix; ./ is dropped.
function workspaceRelative(value, root) {
  let text = value.replaceAll('\\', '/');
  const base = typeof root === 'string' ? root.replaceAll('\\', '/').replace(/\/+$/, '') : '';
  const fold = /^[A-Za-z]:\//.test(base) ? value => value.toLowerCase() : value => value;
  if (base && fold(text).startsWith(fold(`${base}/`))) text = text.slice(base.length + 1);
  else if (text.startsWith('/workspace/')) text = text.slice('/workspace/'.length);
  return text.replace(/^(?:\.\/)+/, '');
}
function pathProject(value, root) {
  if (typeof value !== 'string' || value.length > 4096) return undefined;
  const [first, name] = workspaceRelative(value, root).split('/');
  return first === 'Playground' ? projectFolderName(name) : undefined;
}

// The Playground project folders a call may change, as workspace-relative
// Playground/<name>; a Tool Search call is read as the tool it selects.
export function touchedProjects(tool, params, root) {
  if (tool === 'tool_call') return touchedProjects(String(params?.id ?? '').split(':').at(-1), params?.args, root);
  if (!FILE_CHANGING_TOOLS.includes(tool) || !params || typeof params !== 'object') return [];
  const found = [];
  const add = folder => { if (folder && !found.includes(folder) && found.length < FILE_CHECK_LIMITS.projects) found.push(folder); };
  if (tool === 'apply_patch') for (const file of workspaceMutationFiles(tool, params)) add(pathProject(file, root));
  else if (tool !== 'exec') add(pathProject(params.path, root));
  else {
    const workdir = typeof params.workdir === 'string' && params.workdir.length <= 4096
      ? workspaceRelative(params.workdir, root).replace(/\/+$/, '') : '';
    if (workdir) add(pathProject(`${workdir}/`, root));
    const command = typeof params.command === 'string' && params.command.length <= 65536 ? params.command : '';
    for (const match of command.matchAll(COMMAND_PROJECT)) add(projectFolderName(match[1]));
    for (const match of command.matchAll(JOINED_PROJECT)) add(projectFolderName(match[3]));
    if (workdir === 'Playground') for (const match of command.matchAll(ENTERED_PROJECT)) add(projectFolderName(match[2]));
  }
  return found;
}

// ------------------------------------------------------------ JavaScript --

const JAVASCRIPT = /\.(?:js|mjs|cjs)$/i;
// A top-level function, class, const or let at column 0, as the doubled
// chars.js repeated them. A var may legitimately be declared twice.
const TOP_LEVEL = /^(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?function\b\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|(?:const|let|class)\s+([A-Za-z_$][\w$]*))/;
// A write to a globalThis, window or self property named __...: assignment,
// compound assignment or ++/--, by dot or by quoted name.
const DEBUG_GLOBAL = /(?<![\w$.])(globalThis|window|self)\s*(?:\.\s*(__[\w$]+)|\[\s*(['"`])(__[\w$]+)\3\s*\])\s*(?:\+\+|--|(?:\*\*|<<|>>>?|&&|\|\||\?\?|[-+*/%&|^])?=(?!=))/g;
// Hooks that libraries and devtools write: not debug globals.
const LIBRARY_GLOBAL = /^__(?:THREE|PIXI_[A-Z0-9_]+|VUE(?:_[A-Z0-9_]+)?|REDUX_DEVTOOLS_EXTENSION(?:_COMPOSE)?|APOLLO_[A-Z0-9_]+|NEXT_DATA|NUXT|INITIAL_STATE|PRELOADED_STATE|PHASER_GAME|[A-Z0-9_]*DEVTOOLS[A-Z0-9_]*|[A-Z0-9_]*_GLOBAL_HOOK)__$|^__VUE_DEVTOOLS_/;
const COMMENT_LINE = /^\s*(?:\/\/|\/\*|\*)/;
// Vendored and minified files: the parse check only.
const VENDORED = /(?:^|\/)(?:vendor|lib)\/|\.min\.[cm]?js$/i;
// JSX-looking tags: the file is not plain JavaScript.
const JSX_TAG = /<\/?[A-Za-z][\w.:-]*(?:\s[^<>]*?)?\/?>|<\/?>/;
// A regular expression may start after these words (otherwise / divides).
const REGEX_AFTER = new Set(['return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void', 'throw', 'case', 'do',
  'else', 'yield', 'await']);

const counted = (counts, key) => counts.set(key, (counts.get(key) ?? 0) + 1);

// Lexer modes, and the characters of a name or number.
const CODE = 0, LINE = 1, BLOCK = 2, SINGLE = 3, DOUBLE = 4, TEMPLATE = 5, REGEX = 6, CLASS = 7;
const wordCode = c => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95 || c === 36 || c > 127;

// Whether each line starts in code rather than inside a block comment, a
// string or a template literal. A small lexer over character codes
// (comments, strings, templates with ${} nesting, regular expressions after
// an operator or keyword): enough to tell a commented-out or quoted
// definition from a real one, not a parser.
function codeLineStarts(source) {
  const starts = [true], templates = [];
  let mode = CODE, depth = 0, regex = true;
  for (let i = 0; i < source.length; i++) {
    const c = source.charCodeAt(i);
    if (c === 10) {
      // A quoted string ends at its line unless the line ends with a backslash.
      if (mode === LINE || mode === REGEX || mode === CLASS ||
          ((mode === SINGLE || mode === DOUBLE) && source.charCodeAt(i - 1) !== 92)) mode = CODE;
      starts.push(mode === CODE);
      continue;
    }
    if (mode === LINE) continue;
    if (mode === BLOCK) { if (c === 42 && source.charCodeAt(i + 1) === 47) { mode = CODE; i++; } continue; }
    if (mode !== CODE) {
      if (c === 92) { if (source.charCodeAt(i + 1) !== 10) i++; continue; }
      if (mode === SINGLE || mode === DOUBLE) { if (c === (mode === SINGLE ? 39 : 34)) { mode = CODE; regex = false; } continue; }
      if (mode === TEMPLATE) {
        if (c === 96) { mode = CODE; regex = false; }
        else if (c === 36 && source.charCodeAt(i + 1) === 123) { templates.push(depth); depth = 0; mode = CODE; regex = true; i++; }
        continue;
      }
      if (mode === CLASS) { if (c === 93) mode = REGEX; continue; }
      if (c === 91) mode = CLASS;
      else if (c === 47) { mode = CODE; regex = false; }
      continue;
    }
    if (c === 47) {
      const next = source.charCodeAt(i + 1);
      if (next === 47) { mode = LINE; i++; }
      else if (next === 42) { mode = BLOCK; i++; }
      else if (regex) mode = REGEX;
      else regex = true;
      continue;
    }
    if (c === 39) { mode = SINGLE; continue; }
    if (c === 34) { mode = DOUBLE; continue; }
    if (c === 96) { mode = TEMPLATE; continue; }
    if (c === 123) { depth++; regex = true; continue; }
    if (c === 125) {
      if (depth === 0 && templates.length) { depth = templates.pop(); mode = TEMPLATE; continue; }
      depth--; regex = true; continue;
    }
    if (c === 32 || c === 9 || c === 13) continue;
    if (wordCode(c)) {
      let end = i + 1;
      while (end < source.length && wordCode(source.charCodeAt(end))) end++;
      regex = end - i >= 2 && end - i <= 10 && REGEX_AFTER.has(source.slice(i, end));
      i = end - 1;
      continue;
    }
    regex = c !== 41 && c !== 93;
  }
  return starts;
}

// How often each top-level name is defined, and each debug global written
// (as receiver.__name), in one pass. A line that starts in a comment, a
// string or a template literal counts for neither.
function sourceCounts(source) {
  const text = String(source), starts = codeLineStarts(text), definitions = new Map(), globals = new Map();
  for (let start = 0, line = 0; start <= text.length; line++) {
    let end = text.indexOf('\n', start);
    if (end < 0) end = text.length;
    if (starts[line]) {
      const content = text.slice(start, end);
      const match = /^[acefl]/.test(content) && TOP_LEVEL.exec(content);
      if (match) counted(definitions, match[1] ?? match[2]);
      if (content.includes('__') && !COMMENT_LINE.test(content)) {
        for (const write of content.matchAll(DEBUG_GLOBAL)) {
          const name = write[2] ?? write[4];
          if (name !== '__proto__') counted(globals, `${write[1]}.${name}`);
        }
      }
    }
    start = end + 1;
  }
  return {definitions, globals};
}
export const topLevelDefinitions = source => sourceCounts(source).definitions;
export const debugGlobalWrites = source => sourceCounts(source).globals;

// A vendored, minified or generated file: its definitions and globals are
// not this project's code.
function generatedFile(key, source) {
  if (VENDORED.test(key)) return true;
  for (let start = 0, end; start < source.length; start = end + 1) {
    end = source.indexOf('\n', start);
    if (end < 0) end = source.length;
    if (end - start > FILE_CHECK_LIMITS.minifiedLine) return true;
  }
  return false;
}

// Where a script parse stops when the file is an ES module.
const MODULE_SYNTAX = /^(?:Cannot use import statement outside a module|Unexpected token 'export'|Cannot use 'import\.meta' outside a module|await is only valid in async functions and the top level bodies of modules|Unexpected reserved word)$/;
const MODULE_STATEMENT = /^[ \t]*(?:import[ \t]*[\w{*'"]|export[ \t{*])/m;
const COMMONJS_PARAMETERS = ['exports', 'require', 'module', '__filename', '__dirname'];

// Compiles without running: {ok: true}, or {ok: false, line, message} for a
// syntax error; undefined when the compiler gave up (too deeply nested).
function compiled(compile) {
  try { compile(); return {ok: true}; }
  catch (error) {
    if (error instanceof RangeError) return undefined;
    if (!(error instanceof SyntaxError)) throw error;
    const line = /^checked\.js:(\d+)/.exec(String(error.stack))?.[1];
    return {ok: false, message: error.message, ...(line ? {line: Number(line)} : {})};
  }
}

// The parse that runs in process, as the kind the name and syntax make the
// file: .cjs, and .js with a top-level return, as CommonJS; anything else as a
// classic script. {module: true} for an .mjs file, or a .js file whose script
// parse stops at module syntax: that needs an ES module check.
export function scriptCheck(source, file) {
  const commonjs = () => compiled(() => vm.compileFunction(source, COMMONJS_PARAMETERS, {filename: 'checked.js'}));
  const extension = path.extname(String(file)).toLowerCase();
  if (extension === '.mjs') return {module: true};
  if (extension === '.cjs') return commonjs();
  const script = compiled(() => new vm.Script(source, {filename: 'checked.js'}));
  if (script?.ok !== false) return script;
  if (MODULE_SYNTAX.test(script.message) || MODULE_STATEMENT.test(source)) return {module: true};
  return script.message === 'Illegal return statement' ? commonjs() : script;
}

// `node --check` of an ES module read from standard input, by this Node
// without the gateway's environment (so no NODE_OPTIONS preload), in the
// background: it parses the module and never runs it. Resolves {ok: true},
// {ok: false, line, message}, or undefined when it did not finish in time.
export function nodeModuleCheck(source, timeout = FILE_CHECK_LIMITS.moduleTimeoutMs) {
  return new Promise(resolve => {
    let stderr = '', done = false, timer;
    const finish = value => { if (!done) { done = true; clearTimeout(timer); resolve(value); } };
    const child = spawn(process.execPath, ['--check', '--input-type=module', '-'],
      {env: {}, windowsHide: true, stdio: ['pipe', 'ignore', 'pipe']});
    timer = setTimeout(() => { child.kill('SIGKILL'); finish(undefined); }, timeout);
    child.on('error', () => finish(undefined));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => { if (stderr.length < 65536) stderr += chunk; });
    child.on('close', (status, signal) => {
      if (signal || status === null) return finish(undefined);
      if (status === 0) return finish({ok: true});
      const line = /^\[stdin\]:(\d+)/m.exec(stderr)?.[1];
      const message = /^SyntaxError: (.*?)\r?$/m.exec(stderr)?.[1];
      finish(message === undefined ? undefined : {ok: false, message, ...(line ? {line: Number(line)} : {})});
    });
    // A child that exits before reading all of its input closes the pipe;
    // its exit status above is the answer.
    child.stdin.on('error', () => {});
    child.stdin.end(source);
  });
}

// Whether JavaScript source parses as the kind its name and syntax make it
// (scriptCheck, then checkModule for an ES module).
export async function parseCheck(source, file, checkModule = nodeModuleCheck) {
  const result = scriptCheck(source, file);
  return result?.module ? checkModule(source) : result;
}

// ------------------------------------------------------------ the folder --

const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());
// Folders a walk does not enter: dependencies and build output.
const SKIPPED_FOLDER = /^(?:node_modules|venv|dist|build)$/i;

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (filesystemError(error)) return null; throw error; }
}

// The real folder of Playground/<name> in the configured workspace, whether
// it exists yet, and its lstat; undefined when the workspace cannot be read,
// or Playground or the project is a link or not a folder. The configured root
// may itself be a platform alias (macOS /var); no link below it is followed.
function projectFolder(root, project) {
  if (typeof root !== 'string' || !path.isAbsolute(root)) return undefined;
  let base;
  try { base = fs.realpathSync(root); }
  catch (error) { if (filesystemError(error)) return undefined; throw error; }
  const folder = path.join(base, ...project.split('/'));
  const playground = lstatOrNull(path.dirname(folder));
  if (playground && !realDirectory(playground)) return undefined;
  const stat = playground && lstatOrNull(folder);
  if (!stat) return {folder, exists: false};
  return realDirectory(stat) ? {folder, exists: true, stat} : undefined;
}

// Every regular file under folder by its relative path, with its lstat, and
// the kind of each entry in the folder itself, in sorted, breadth-first
// order. Links and special files are never followed or read; dot folders,
// node_modules, venv, dist and build are not entered. A folder swapped for
// another (or a link) after it was queued is not listed. {truncated: true}
// past limits.entries.
function walk(folder, stat, limits) {
  const files = new Map(), entries = new Map(), pending = [['', 0, stat]];
  let visited = 0;
  while (pending.length) {
    const [relative, depth, queued] = pending.shift();
    const directory = relative ? path.join(folder, ...relative.split('/')) : folder;
    let names;
    try { names = fs.readdirSync(directory); }
    catch (error) { if (filesystemError(error)) continue; throw error; }
    const listed = lstatOrNull(directory);
    if (!realDirectory(listed) || listed.dev !== queued.dev || listed.ino !== queued.ino) continue;
    for (const name of names.sort()) {
      if (++visited > limits.entries) return {truncated: true};
      const entry = lstatOrNull(path.join(directory, name));
      if (!entry) continue;
      const key = relative ? `${relative}/${name}` : name;
      if (!relative) entries.set(name, realDirectory(entry) ? 'folder' : 'file');
      if (realDirectory(entry)) {
        if (!name.startsWith('.') && !SKIPPED_FOLDER.test(name) && depth + 1 < limits.depth) pending.push([key, depth + 1, entry]);
      } else if (entry.isFile()) files.set(key, entry);
    }
  }
  return {files, entries};
}

// Exactly stat.size bytes of the single-link regular file stat describes:
// its real path is the walked path (no link on the way), and it is opened
// without following a link put in its place. Undefined when it is no longer
// that file, or larger than a check reads.
function readExact(folder, key, stat, limits) {
  if (stat.nlink > 1 || stat.size > limits.fileBytes) return undefined;
  const file = path.join(folder, ...key.split('/'));
  let fd;
  try {
    if (fs.realpathSync(file) !== file) return undefined;
    fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.ino !== stat.ino || opened.dev !== stat.dev || opened.size !== stat.size) return undefined;
    const bytes = Buffer.alloc(stat.size + 1);
    let length = 0, read;
    while (length < bytes.length && (read = fs.readSync(fd, bytes, length, bytes.length - length, length)) > 0) length += read;
    return length === stat.size ? bytes.subarray(0, length) : undefined;
  } catch (error) {
    if (filesystemError(error)) return undefined;
    throw error;
  } finally { if (fd !== undefined) fs.closeSync(fd); }
}

// Paths the owner and model texts can print in a code span (letters in any
// script, no backquote or control character); other files are left out.
const LISTABLE_PATH = /^[\p{L}\p{M}\p{N}_@+,.() /-]{1,120}$/u;
const listablePath = value => LISTABLE_PATH.test(value) && value.split('/').every(part => part.trim());
// *.bak, *.orig, *.tmp, tmp-* and *-tmp* (not a word such as -tmpl).
const SCRATCH = /^(?:tmp-.*|.*-tmp(?![a-z]).*|.*\.(?:bak|orig|tmp))$/i;
// Dot entries that look like scratch: .crop*, .tmp*, .scratch*.
const SCRATCH_DOT = /^\.(?:crop|tmp|scratch)/i;
// A dotfile with a data or code extension (.notes.json, .debug.log).
const DATA_OR_CODE = /\.(?:txt|log|out|json|jsonl|ndjson|csv|tsv|xml|ya?ml|toml|ini|md|html?|css|[cm]?js|jsx|tsx?|py|rb|php|pl|lua|sh|bash|ps1|go|rs|java|kt|c|cc|cpp|h|hpp|cs|swift|png|jpe?g|gif|webp|bmp|svg|ico|tiff?|mp3|wav|ogg|mp4|webm|bin|dat|db|sqlite|pkl|pickle|npy|patch|diff)$/i;
// Conventional dot entries, never scratch: git, editor and tool settings,
// environment files, web server files and build caches.
const CONVENTIONAL_DOT = /^\.(?:git.*|editorconfig|env(?:\..+)?|vscode|idea|well-known|htaccess|nojekyll|ds_store|next|parcel-cache|svelte-kit|astro|nuxt|output|pytest_cache|venv|cache|[\w-]*rc(?:\.\w+)?|[\w-]*ignore)$/i;
// A new entry in the project folder that the checks report.
export function scratchEntry(name) {
  if (typeof name !== 'string' || !name) return false;
  if (SCRATCH.test(name) || SCRATCH_DOT.test(name)) return true;
  return name.startsWith('.') && !CONVENTIONAL_DOT.test(name) && DATA_OR_CODE.test(name);
}

// Script files a project's HTML loads as text/babel (in-browser JSX).
function babelScripts(folder, walked, limits) {
  const found = new Set();
  for (const [key, stat] of walked.files) {
    if (key.includes('/') || !/\.html?$/i.test(key) || stat.size > 256 * 1024) continue;
    const html = readExact(folder, key, stat, limits)?.toString('utf8') ?? '';
    for (const [tag] of html.matchAll(/<script\b[^>]*>/gi)) {
      const source = /\bsrc\s*=\s*["']([^"'?#]+)/i.exec(tag)?.[1];
      if (/\btype\s*=\s*["']?text\/(?:babel|jsx)\b/i.test(tag) && source && !/^[a-z]+:|^\/\//i.test(source))
        found.add(source.replace(/^(?:\.\/|\/)+/, ''));
    }
  }
  return found;
}

// --------------------------------------------------------------- the run --

// Module check states besides a result: waiting for its turn or running,
// and not done because the response used its checks. A file past a scan's
// bytes waits for a later scan.
const PENDING = Symbol('pending');
const CAPPED = Symbol('capped');
const DEFERRED = Symbol('deferred');
// Findings the owner reads but the model is not told: a new folder (its
// removal is a recursive delete), and what could not be checked.
export const ownerOnly = finding => finding.kind === 'unchecked' || finding.kind === 'unparsed' ||
  (finding.kind === 'entry' && finding.folder);
export const fileCheckKey = finding => JSON.stringify([finding.kind, finding.project, finding.file ?? '', finding.name ?? '']);

// One response's checks: the projects it touched, the findings told to the
// model, and caches so an unchanged version of a file is analyzed once.
// requested: the owner's request, whose names are not debug globals.
export function createChangedFileChecks({checkModule = nodeModuleCheck, requested = '', limits: overrides = {}} = {}) {
  const limits = {...FILE_CHECK_LIMITS, ...overrides};
  const projects = new Map(), told = new Set();
  // ES module results by content digest, so the same bytes are checked once.
  const modules = new Map(), queue = [], waiting = [];
  let running = 0, used = 0, baselineUsed = 0, arrived = false, rows = [];

  const idle = () => !running && !queue.length;
  function pump() {
    while (running < limits.moduleConcurrency && queue.length) {
      const [digest, source] = queue.shift();
      running += 1;
      let check;
      // A check that throws has no result, like one that did not finish.
      try { check = Promise.resolve(checkModule(source)); } catch { check = Promise.resolve(null); }
      check.then(result => result ?? null, () => null).then(result => {
        modules.set(digest, result);
        running -= 1;
        arrived = true;
        pump();
        if (idle()) for (const resolve of waiting.splice(0)) resolve();
      });
    }
  }
  // The digest of source, whose module check is queued unless it is known or
  // the response used its checks.
  function moduleDigest(source, baseline) {
    const digest = createHash('sha256').update(source).digest('hex');
    if (modules.has(digest)) return digest;
    if (used >= limits.moduleChecks || (baseline && baselineUsed >= limits.moduleBaseline)) {
      modules.set(digest, CAPPED);
      return digest;
    }
    used += 1;
    if (baseline) baselineUsed += 1;
    modules.set(digest, PENDING);
    queue.push([digest, source]);
    pump();
    return digest;
  }
  // A version's parse: the in-process result, or its module check's.
  const parseOf = record => record.digest ? modules.get(record.digest) : record.parse;

  // The source's analysis: parse (or the digest of its module check), counts
  // unless the file is vendored or generated, and whether it has JSX tags.
  function analyze(key, source, baseline) {
    const parse = scriptCheck(source, key);
    const generated = generatedFile(key, source);
    return {...(parse?.module ? {digest: moduleDigest(source, baseline)} : {parse}), jsx: JSX_TAG.test(source),
      ...(generated ? {generated} : sourceCounts(source))};
  }

  // The project as it is before a call changes it. {skipped} for a project
  // the checks leave alone: a link (silently) or too large ('size').
  function fingerprint(root, project) {
    const located = projectFolder(root, project);
    if (!located) return {skipped: 'link'};
    const baseline = {root, project, files: new Map(), entries: new Map(), analyzed: new Map()};
    if (!located.exists) return baseline;
    const walked = walk(located.folder, located.stat, limits);
    if (walked.truncated) return {root, project, skipped: 'size'};
    let budget = limits.readBytes;
    for (const [key, stat] of walked.files) {
      const record = {size: stat.size, mtimeMs: stat.mtimeMs};
      const bytes = JAVASCRIPT.test(key) && stat.size <= budget ? readExact(located.folder, key, stat, limits) : undefined;
      if (bytes) {
        budget -= bytes.length;
        Object.assign(record, analyze(key, bytes.toString('utf8'), true));
      }
      baseline.files.set(key, record);
    }
    baseline.entries = walked.entries;
    return baseline;
  }

  // A changed file's analysis, once per version (size, times and inode);
  // DEFERRED past this scan's bytes, undefined when it cannot be read now.
  function analysis(baseline, folder, key, stat, budget) {
    const version = [stat.size, stat.mtimeMs, stat.ctimeMs, stat.ino].join(':');
    const known = baseline.analyzed.get(key);
    if (known?.version === version) return known;
    if (stat.size > budget.bytes) return DEFERRED;
    const bytes = readExact(folder, key, stat, limits);
    if (!bytes) return undefined;
    budget.bytes -= bytes.length;
    const entry = {version, ...analyze(key, bytes.toString('utf8'), false)};
    baseline.analyzed.set(key, entry);
    return entry;
  }

  // What every touched project holds now that differs from first touch, as
  // rows that collect() turns into findings.
  function scan() {
    const budget = {bytes: limits.readBytes};
    rows = [];
    for (const baseline of projects.values()) {
      const project = baseline.project;
      if (baseline.skipped) {
        if (baseline.skipped === 'size') rows.push({project, unchecked: true});
        continue;
      }
      const located = projectFolder(baseline.root, project);
      if (!located?.exists) continue;
      const walked = walk(located.folder, located.stat, limits);
      if (walked.truncated) { rows.push({project, unchecked: true}); continue; }
      let babel;
      for (const [key, stat] of walked.files) {
        const before = baseline.files.get(key);
        if (!JAVASCRIPT.test(key) || !listablePath(key) ||
            (before && before.size === stat.size && before.mtimeMs === stat.mtimeMs)) continue;
        const after = analysis(baseline, located.folder, key, stat, budget);
        if (after === DEFERRED) rows.push({project, key, deferred: true});
        if (!after || after === DEFERRED) continue;
        // A '<' parse failure is no failure for a JSX file, nor for one the
        // project's HTML loads as text/babel (looked up once per scan).
        const babelFile = () => (babel ??= babelScripts(located.folder, walked, limits)).has(key);
        rows.push({project, key, before, after, babelFile});
      }
      for (const [name, kind] of walked.entries) {
        if (!baseline.entries.has(name) && scratchEntry(name) && listablePath(name))
          rows.push({project, entry: name, folder: kind === 'folder'});
      }
    }
    return rows;
  }

  // The findings in rows; pending holds the files whose parse is still to be
  // told (a module check that has not finished). With final, those, and the
  // ones the response had no checks left for, could not be checked.
  function collect({final = false} = {}) {
    const found = [], pending = new Set();
    for (const row of rows) {
      const {project} = row;
      if (row.unchecked) { found.push({kind: 'unchecked', project}); continue; }
      if (row.entry) { found.push({kind: 'entry', project, name: row.entry, folder: row.folder}); continue; }
      if (row.deferred) { pending.add(JSON.stringify([project, row.key])); continue; }
      const {key: file, before, after} = row;
      const parse = parseOf(after);
      const prior = before && parseOf(before);
      if (parse === PENDING || (parse?.ok === false && prior === PENDING)) {
        pending.add(JSON.stringify([project, file]));
        if (final) found.push({kind: 'unparsed', project, file});
      } else if (parse === CAPPED || parse === null) {
        found.push({kind: 'unparsed', project, file});
      } else if (parse?.ok === false && (!before || prior?.ok === true) &&
          !(parse.message === "Unexpected token '<'" && (after.jsx || row.babelFile()))) {
        found.push({kind: 'parse', project, file, ...(parse.line ? {line: parse.line} : {})});
      }
      // Counts compare with first touch: a file read then, or a new file.
      if (after.generated || (before && !before.definitions)) continue;
      for (const [name, count] of after.definitions) {
        if (count >= 2 && count > (before?.definitions.get(name) ?? 0) && name.length <= 64)
          found.push({kind: 'definition', project, file, name, count});
      }
      for (const [name, count] of after.globals) {
        const bare = name.slice(name.indexOf('.') + 1);
        if (count > (before?.globals.get(name) ?? 0) && name.length <= 80 && !LIBRARY_GLOBAL.test(bare) &&
            !new RegExp(`(?<![\\w$])${bare.replace(/\$/g, '\\$')}(?![\\w$])`).test(requested))
          found.push({kind: 'global', project, file, name});
      }
    }
    return {found, pending};
  }

  return {
    // Before a call that may change these Playground/<name> folders runs: the
    // first touch of each fingerprints it.
    touch(root, touched) {
      for (const project of touched) {
        if (projects.has(project) || projects.size >= limits.projects) continue;
        projects.set(project, fingerprint(root, project));
      }
    },
    // What the checks find now in every project this response touched. With
    // final (the delivery), a module check still running could not be checked.
    findings({final = false} = {}) {
      scan();
      return collect({final}).found;
    },
    // The model's findings not told yet. Each is told once, and again only
    // after it was gone (a file that breaks again). With walk false (after a
    // call that changed nothing), only module checks finished since count.
    take({walk = true} = {}) {
      if (walk) scan();
      else if (!arrived) return [];
      arrived = false;
      const {found, pending} = collect();
      const tellable = found.filter(finding => !ownerOnly(finding));
      const present = new Set(tellable.map(fileCheckKey));
      for (const key of told) {
        const [, project, file] = JSON.parse(key);
        if (!present.has(key) && !pending.has(JSON.stringify([project, file]))) told.delete(key);
      }
      const fresh = tellable.filter(finding => !told.has(fileCheckKey(finding)));
      for (const finding of fresh) told.add(fileCheckKey(finding));
      return fresh;
    },
    // Resolves once the module checks this response queued have finished, or
    // after ms; never rejects.
    settle(ms = limits.settleMs) {
      if (idle()) return Promise.resolve();
      return new Promise(resolve => {
        const timer = setTimeout(resolve, ms);
        waiting.push(() => { clearTimeout(timer); resolve(); });
      });
    },
  };
}

// --------------------------------------------------------------- the text --

const code = value => `\`${value}\``;
// `a`, `b`, `c` and 2 more names.
function names(list, noun = '') {
  const shown = list.slice(0, FILE_CHECK_LIMITS.names).map(code);
  const more = list.length - shown.length;
  if (more) return `${shown.join(', ')} and ${more} more${noun && ` ${noun}${more > 1 ? 's' : ''}`}`;
  return shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown.at(-1)}` : shown[0];
}

// One plain item per kind and file, per project: what could not be checked,
// parse failures, repeated definitions, debug globals, then new entries.
const KINDS = ['unchecked', 'unparsed', 'parse', 'definition', 'global', 'entry'];
function items(findings) {
  const projects = new Map();
  for (const finding of findings) {
    const groups = projects.get(finding.project) ?? projects.set(finding.project, new Map()).get(finding.project);
    const where = finding.kind === 'entry' ? String(finding.folder) : finding.kind === 'unparsed' ? '' : finding.file ?? '';
    const key = `${KINDS.indexOf(finding.kind)}\u0000${where}`;
    (groups.get(key) ?? groups.set(key, []).get(key)).push(finding);
  }
  return [...projects].map(([project, groups]) => [project, [...groups.values()]
    .sort((a, b) => KINDS.indexOf(a[0].kind) - KINDS.indexOf(b[0].kind)).map(list => {
      const [first] = list, file = code(first.file), plural = list.length > 1;
      if (first.kind === 'unchecked') {
        return `not checked, the folder has more than ${FILE_CHECK_LIMITS.entries.toLocaleString('en-US')} files and folders`;
      }
      if (first.kind === 'unparsed') return `could not check whether ${names(list.map(item => item.file))} ${plural ? 'parse' : 'parses'}`;
      if (first.kind === 'parse') return `${file} does not parse${first.line ? ` (line ${first.line})` : ''}`;
      if (first.kind === 'global') return `new debug global${plural ? 's' : ''} ${names(list.map(item => item.name), 'global')} in ${file}`;
      if (first.kind === 'entry') {
        return `new ${first.folder ? 'folder' : 'file'}${plural ? 's' : ''} ${names(list.map(item => item.name))} in the project folder`;
      }
      const twice = list.every(item => item.count === 2);
      const times = twice ? (plural ? 'each defined twice' : 'defined twice') : plural ? 'defined more than once' : `defined ${first.count} times`;
      return `${names(list.map(item => item.name), 'name')} ${plural ? 'are' : 'is'} ${times} in ${file}`;
    })]);
}

// The longest text within maxChars, in one pass: sections in order, each a
// lead and its items per project; the items that do not fit are counted at
// the end. '' when nothing was found or none fits.
function compose(sections, end, maxChars) {
  const flat = sections.flatMap(({lead, findings}, section) =>
    items(findings).flatMap(([project, list]) => list.map(item => ({section, lead, project, item}))));
  let text = '', shown = 0, last;
  const close = more => `${more ? `; and ${more} more` : ''}.${end ? ` ${end}` : ''}`;
  for (const {section, lead, project, item} of flat) {
    const piece = section !== last?.section ? `${text ? '. ' : ''}${lead} ${code(project)}: ${item}`
      : project !== last.project ? `. In ${code(project)}: ${item}` : `; ${item}`;
    if (text.length + piece.length + close(flat.length - shown - 1).length > maxChars) break;
    text += piece;
    shown += 1;
    last = {section, project};
  }
  return shown ? text + close(flat.length - shown) : '';
}

// The owner's line of host facts under the answer: this turn's findings, and
// what an earlier response's checks still find that its delivery could not
// show (a question card carries no notice).
export function fileCheckFacts(findings, maxChars = FILE_CHECK_LIMITS.ownerChars, carried = []) {
  return compose([{lead: "Portal's checks of the changed files in", findings: Array.isArray(findings) ? findings : []},
    {lead: 'Still found from earlier changes in', findings: Array.isArray(carried) ? carried : []}], '', maxChars);
}

// The model's one notice of new findings, on a tool result. Owner-only
// findings are never in it.
export const FILE_CHECK_INSTRUCTION = 'Fix them, or say plainly in your final answer that they remain.';
export const FILE_CHECK_REMOVAL = 'To delete a listed new file, use rm without -rf.';
export function fileCheckInstruction(findings) {
  const tellable = (Array.isArray(findings) ? findings : []).filter(finding => !ownerOnly(finding));
  const end = tellable.some(finding => finding.kind === 'entry') ? `${FILE_CHECK_INSTRUCTION} ${FILE_CHECK_REMOVAL}` : FILE_CHECK_INSTRUCTION;
  return compose([{lead: '[ODS Pixel check] Portal checked the files this response changed in', findings: tellable}], end,
    FILE_CHECK_LIMITS.modelChars);
}

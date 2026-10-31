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
// each regular file's size and mtime, each JavaScript file's top-level
// definitions and debug-global writes, and the names in the project folder.
// A command can change any file, so a file changed when its size or mtime
// differs, or when it is new. Then:
// - every changed .js/.mjs/.cjs file parses (a script or CommonJS file in
//   process; an ES module with `node --check`, which never runs it);
// - no top-level name is defined more often than at first touch;
// - no globalThis/window/self.__name write was added;
// - no dotfile or scratch-looking entry was added to the project folder.
// tool-loop-guard.mjs tells the model each finding once, on the result of the
// call after which it is found, and lists what is still found under the
// answer. Links are never followed, node_modules and dot folders are skipped,
// and every walk, read and parse is bounded. Nothing here changes a file.
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {filesystemError} from './example-projects.mjs';
import {workspaceMutationFiles} from './workspace-projects.mjs';

export const FILE_CHECK_LIMITS = Object.freeze({
  // Project folders fingerprinted per response.
  projects: 4,
  // Entries one walk visits, and folders deep; a larger project is not checked.
  entries: 2000,
  depth: 8,
  // JavaScript read per file, and per fingerprint or check.
  fileBytes: 2 * 1024 * 1024,
  readBytes: 16 * 1024 * 1024,
  // ES module parses (`node --check`) per check, each this long at most.
  moduleChecks: 3,
  moduleTimeoutMs: 2000,
  // Names listed per finding; characters in the owner's line.
  names: 3,
  lineChars: 900,
});

// Tools whose call can change a project's files, and so can touch it first.
export const FILE_CHANGING_TOOLS = Object.freeze(['write', 'edit', 'apply_patch', 'exec']);

const PROJECT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const projectFolderName = name => typeof name === 'string' && PROJECT_NAME.test(name) && !name.endsWith('.') &&
  !RESERVED.test(name) ? `Playground/${name}` : undefined;
// Playground/<name> where a command names it: a cd target, an argument, or a
// path in a script's own text.
const COMMAND_PROJECT = /(?<![A-Za-z0-9._-])Playground\/([A-Za-z0-9][A-Za-z0-9._-]{0,63})(?![A-Za-z0-9._-])/g;

// The project folder a path is in: Playground/<name>/..., relative to the
// workspace or spelled /workspace/... or <root>/....
function pathProject(value, root) {
  if (typeof value !== 'string' || value.length > 4096) return undefined;
  let text = value.replaceAll('\\', '/');
  const base = typeof root === 'string' ? root.replaceAll('\\', '/').replace(/\/+$/, '') : '';
  if (base && text.startsWith(`${base}/`)) text = text.slice(base.length + 1);
  else if (text.startsWith('/workspace/')) text = text.slice('/workspace/'.length);
  const [first, name] = text.replace(/^(?:\.\/)+/, '').split('/');
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
    if (typeof params.workdir === 'string') add(pathProject(`${params.workdir}/`, root));
    if (typeof params.command === 'string' && params.command.length <= 65536)
      for (const match of params.command.matchAll(COMMAND_PROJECT)) add(projectFolderName(match[1]));
  }
  return found;
}

// ------------------------------------------------------------ JavaScript --

const JAVASCRIPT = /\.(?:js|mjs|cjs)$/i;
// A top-level definition at column 0, as the doubled chars.js repeated them.
const TOP_LEVEL = /^(?:export\s+(?:default\s+)?)?(?:(?:async\s+)?function\b\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|(?:const|let|var|class)\s+([A-Za-z_$][\w$]*))/;
// A write to a globalThis, window or self property named __...: assignment,
// compound assignment or ++/--, by dot or by quoted name.
const DEBUG_GLOBAL = /(?<![\w$.])(globalThis|window|self)\s*(?:\.\s*(__[\w$]+)|\[\s*(['"`])(__[\w$]+)\3\s*\])\s*(?:\+\+|--|(?:\*\*|<<|>>>?|&&|\|\||\?\?|[-+*/%&|^])?=(?!=))/g;
const COMMENT_LINE = /^\s*(?:\/\/|\/\*|\*)/;

const counted = (counts, key) => counts.set(key, (counts.get(key) ?? 0) + 1);

// How often each top-level name is defined.
export function topLevelDefinitions(source) {
  const counts = new Map();
  for (const line of String(source).split('\n')) {
    const match = TOP_LEVEL.exec(line);
    if (match) counted(counts, match[1] ?? match[2]);
  }
  return counts;
}

// How often each debug global is written, as receiver.__name. A line that
// starts as a comment does not count.
export function debugGlobalWrites(source) {
  const counts = new Map();
  for (const line of String(source).split('\n')) {
    if (COMMENT_LINE.test(line)) continue;
    for (const match of line.matchAll(DEBUG_GLOBAL)) {
      const name = match[2] ?? match[4];
      if (name !== '__proto__') counted(counts, `${match[1]}.${name}`);
    }
  }
  return counts;
}

// Where a script parse stops when the file is an ES module.
const MODULE_SYNTAX = /^(?:Cannot use import statement outside a module|Unexpected token 'export'|Cannot use 'import\.meta' outside a module|await is only valid in async functions and the top level bodies of modules)$/;
const COMMONJS_PARAMETERS = ['exports', 'require', 'module', '__filename', '__dirname'];

// `node --check` of an ES module read from standard input, by this Node
// without the gateway's environment (so no NODE_OPTIONS preload): it parses
// the module and never runs it. Undefined when it did not finish.
export function nodeModuleCheck(source, timeout = FILE_CHECK_LIMITS.moduleTimeoutMs) {
  const run = spawnSync(process.execPath, ['--check', '--input-type=module', '-'],
    {input: source, env: {}, timeout, encoding: 'utf8', windowsHide: true, maxBuffer: 1024 * 1024});
  if (run.error || run.signal) return undefined;
  if (run.status === 0) return {ok: true};
  const line = /^\[stdin\]:(\d+)/m.exec(run.stderr ?? '')?.[1];
  return /\bSyntaxError\b/.test(run.stderr ?? '') ? {ok: false, ...(line ? {line: Number(line)} : {})} : undefined;
}

// Compiles without running: {ok: true}, or {ok: false, message, line} for a
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
const outcome = result => result && {ok: result.ok, ...(result.line ? {line: result.line} : {})};

// Whether JavaScript source parses as the kind its name and syntax make it:
// .cjs, and .js with a top-level return, as CommonJS; .mjs, and .js whose
// script parse stops at module syntax, as an ES module (checkModule);
// anything else as a classic script. {ok: true}, {ok: false, line}, or
// undefined when it could not be told.
export function parseCheck(source, file, checkModule = nodeModuleCheck) {
  const commonjs = () => compiled(() => vm.compileFunction(source, COMMONJS_PARAMETERS, {filename: 'checked.js'}));
  const extension = path.extname(String(file)).toLowerCase();
  if (extension === '.mjs') return checkModule?.(source);
  if (extension === '.cjs') return outcome(commonjs());
  const script = compiled(() => new vm.Script(source, {filename: 'checked.js'}));
  if (script?.ok !== false) return script;
  if (MODULE_SYNTAX.test(script.message)) return checkModule?.(source);
  return outcome(script.message === 'Illegal return statement' ? commonjs() : script);
}

// ------------------------------------------------------------ the folder --

const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (filesystemError(error)) return null; throw error; }
}

// The real folder of Playground/<name> in the configured workspace, and
// whether it exists yet; undefined when the workspace cannot be read, or
// Playground or the project is a link or not a folder. The configured root
// may itself be a platform alias (macOS /var); no link below it is followed.
function projectFolder(root, project) {
  if (typeof root !== 'string' || !path.isAbsolute(root)) return undefined;
  let base;
  try { base = fs.realpathSync(root); }
  catch (error) { if (filesystemError(error)) return undefined; throw error; }
  const folder = path.join(base, ...project.split('/'));
  const playground = lstatOrNull(path.dirname(folder));
  if (playground && !realDirectory(playground)) return undefined;
  const entry = playground && lstatOrNull(folder);
  if (!entry) return {folder, exists: false};
  return realDirectory(entry) ? {folder, exists: true} : undefined;
}

// Every regular file under folder by its relative path, with its lstat, and
// the kind of each entry in the folder itself, in sorted, breadth-first
// order. Links and special files are never followed or read, and
// node_modules and dot folders are not entered. {truncated: true} past
// FILE_CHECK_LIMITS.entries.
function walk(folder) {
  const files = new Map(), entries = new Map(), pending = [['', 0]];
  let visited = 0;
  while (pending.length) {
    const [relative, depth] = pending.shift();
    const directory = relative ? path.join(folder, ...relative.split('/')) : folder;
    let names;
    try { names = fs.readdirSync(directory).sort(); }
    catch (error) { if (filesystemError(error)) continue; throw error; }
    for (const name of names) {
      if (++visited > FILE_CHECK_LIMITS.entries) return {truncated: true};
      const stat = lstatOrNull(path.join(directory, name));
      if (!stat) continue;
      const key = relative ? `${relative}/${name}` : name;
      if (!relative) entries.set(name, realDirectory(stat) ? 'folder' : 'file');
      if (realDirectory(stat)) {
        if (name !== 'node_modules' && !name.startsWith('.') && depth + 1 < FILE_CHECK_LIMITS.depth) pending.push([key, depth + 1]);
      } else if (stat.isFile()) files.set(key, stat);
    }
  }
  return {files, entries};
}

// Exactly stat.size bytes of the single-link regular file stat describes,
// opened without following a link put in its place; undefined when it is no
// longer that file, or larger than a check reads.
function readExact(folder, key, stat) {
  if (stat.nlink > 1 || stat.size > FILE_CHECK_LIMITS.fileBytes) return undefined;
  let fd;
  try {
    fd = fs.openSync(path.join(folder, ...key.split('/')),
      fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
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
// *.bak, *.orig, *.tmp, tmp-*, *-tmp* (not a word such as -tmpl) and .crop*.
const SCRATCH = /^(?:tmp-.*|.*-tmp(?![a-z]).*|.*\.(?:bak|orig|tmp)|\.crop.*)$/i;
// A new entry in the project folder that the checks report: a dotfile, or a
// name that looks like scratch.
export const scratchEntry = name => typeof name === 'string' && (name.startsWith('.') || SCRATCH.test(name));

// The project as it is before a call changes it: its folder (or none yet),
// each file's size and mtime, each JavaScript file's counts, and its entries.
// {skipped: true} for a project the checks leave alone.
function fingerprint(root, project) {
  const located = projectFolder(root, project);
  if (!located) return {skipped: true};
  const baseline = {root, project, files: new Map(), entries: new Map(), analyzed: new Map()};
  if (!located.exists) return baseline;
  const walked = walk(located.folder);
  if (walked.truncated) return {skipped: true};
  let budget = FILE_CHECK_LIMITS.readBytes;
  for (const [key, stat] of walked.files) {
    const record = {size: stat.size, mtimeMs: stat.mtimeMs};
    const bytes = JAVASCRIPT.test(key) && stat.size <= budget ? readExact(located.folder, key, stat) : undefined;
    if (bytes) {
      budget -= bytes.length;
      const source = bytes.toString('utf8');
      Object.assign(record, {definitions: topLevelDefinitions(source), globals: debugGlobalWrites(source)});
    }
    baseline.files.set(key, record);
  }
  baseline.entries = walked.entries;
  return baseline;
}

// --------------------------------------------------------------- the run --

// One response's checks: the projects it touched, the findings told to the
// model, and caches so an unchanged version of a file is analyzed once.
export function createChangedFileChecks({checkModule = nodeModuleCheck} = {}) {
  const projects = new Map();
  const told = new Set();
  // ES module results by content digest (null: the check did not finish), so
  // the same bytes are never checked twice.
  const modules = new Map();

  // A changed JavaScript file's counts and parse result, per version (size,
  // times and inode). A module parse past this check's budget, or a read past
  // its bytes, waits for a later check.
  function analysis(baseline, folder, key, stat, budget) {
    const version = [stat.size, stat.mtimeMs, stat.ctimeMs, stat.ino].join(':');
    let entry = baseline.analyzed.get(key);
    if (entry?.version !== version) {
      if (stat.size > budget.bytes) return undefined;
      const bytes = readExact(folder, key, stat);
      if (!bytes) return undefined;
      budget.bytes -= bytes.length;
      const source = bytes.toString('utf8');
      entry = {version, source, definitions: topLevelDefinitions(source), globals: debugGlobalWrites(source)};
      baseline.analyzed.set(key, entry);
    }
    if (entry.source !== undefined) {
      let deferred = false;
      const parse = parseCheck(entry.source, key, source => {
        const digest = createHash('sha256').update(source).digest('hex');
        if (!modules.has(digest)) {
          if (budget.modules <= 0) { deferred = true; return undefined; }
          budget.modules -= 1;
          modules.set(digest, checkModule(source) ?? null);
        }
        return modules.get(digest) ?? undefined;
      });
      if (!deferred) Object.assign(entry, {parse, source: undefined});
    }
    return entry;
  }

  function projectFindings(baseline, budget) {
    const located = projectFolder(baseline.root, baseline.project);
    if (!located?.exists) return [];
    const walked = walk(located.folder);
    if (walked.truncated) return [];
    const found = [], project = baseline.project;
    for (const [key, stat] of walked.files) {
      const before = baseline.files.get(key);
      if (!JAVASCRIPT.test(key) || !listablePath(key) ||
          (before && before.size === stat.size && before.mtimeMs === stat.mtimeMs)) continue;
      const after = analysis(baseline, located.folder, key, stat, budget);
      if (!after) continue;
      if (after.parse?.ok === false) found.push({kind: 'parse', project, file: key, ...(after.parse.line ? {line: after.parse.line} : {})});
      // Counts compare with first touch: a file read then, or a new file.
      if (before && !before.definitions) continue;
      for (const [name, count] of after.definitions) {
        if (count >= 2 && count > (before?.definitions.get(name) ?? 0) && name.length <= 64)
          found.push({kind: 'definition', project, file: key, name, count});
      }
      for (const [name, count] of after.globals) {
        if (count > (before?.globals.get(name) ?? 0) && name.length <= 80) found.push({kind: 'global', project, file: key, name});
      }
    }
    for (const [name, kind] of walked.entries) {
      if (!baseline.entries.has(name) && scratchEntry(name) && listablePath(name))
        found.push({kind: 'entry', project, name, folder: kind === 'folder'});
    }
    return found;
  }

  function findings() {
    const budget = {modules: FILE_CHECK_LIMITS.moduleChecks, bytes: FILE_CHECK_LIMITS.readBytes};
    return [...projects.values()].filter(baseline => !baseline.skipped).flatMap(baseline => projectFindings(baseline, budget));
  }

  return {
    // Before a call that may change these Playground/<name> folders runs: the
    // first touch of each fingerprints it.
    touch(root, touched) {
      for (const project of touched) {
        if (projects.has(project) || projects.size >= FILE_CHECK_LIMITS.projects) continue;
        projects.set(project, fingerprint(root, project));
      }
    },
    // What the checks find now, in every project this response touched.
    findings,
    // What they find now that the model has not been told yet. Each finding
    // is told once.
    take() {
      const fresh = findings().filter(finding => !told.has(findingKey(finding)));
      for (const finding of fresh) told.add(findingKey(finding));
      return fresh;
    },
  };
}

const findingKey = finding => JSON.stringify([finding.kind, finding.project, finding.file ?? '', finding.name ?? '']);

// --------------------------------------------------------------- the text --

const code = value => `\`${value}\``;
// `a`, `b`, `c` and 2 more names.
function names(list, noun = '') {
  const shown = list.slice(0, FILE_CHECK_LIMITS.names).map(code);
  const more = list.length - shown.length;
  if (more) return `${shown.join(', ')} and ${more} more${noun && ` ${noun}${more > 1 ? 's' : ''}`}`;
  return shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown.at(-1)}` : shown[0];
}

// One plain item per kind and file, per project: parse failures first, then
// repeated definitions, debug globals and new entries in the project folder.
const KINDS = ['parse', 'definition', 'global', 'entry'];
function items(findings) {
  const projects = new Map();
  for (const finding of findings) {
    const groups = projects.get(finding.project) ?? projects.set(finding.project, new Map()).get(finding.project);
    const key = JSON.stringify([KINDS.indexOf(finding.kind), finding.kind === 'entry' ? String(finding.folder) : finding.file]);
    (groups.get(key) ?? groups.set(key, []).get(key)).push(finding);
  }
  return [...projects].map(([project, groups]) => [project, [...groups.values()]
    .sort((a, b) => KINDS.indexOf(a[0].kind) - KINDS.indexOf(b[0].kind)).map(list => {
      const [first] = list, file = code(first.file), plural = list.length > 1;
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

// The longest text within maxChars: items in order, those that do not fit
// counted at the end. '' when nothing was found or none fits.
function compose(findings, lead, end, maxChars) {
  const groups = items(findings);
  const total = groups.reduce((sum, [, list]) => sum + list.length, 0);
  for (let shown = total; shown > 0; shown--) {
    let left = shown;
    const parts = groups.flatMap(([project, list]) => {
      const kept = list.slice(0, left);
      left -= kept.length;
      return kept.length ? [`${code(project)}: ${kept.join('; ')}`] : [];
    });
    const more = total - shown;
    const text = `${lead} ${parts.join('. In ')}${more ? `; and ${more} more` : ''}.${end ? ` ${end}` : ''}`;
    if (text.length <= maxChars) return text;
  }
  return '';
}

// The owner's line of host facts under the answer.
export function fileCheckFacts(findings, maxChars = FILE_CHECK_LIMITS.lineChars) {
  return Array.isArray(findings) && findings.length
    ? compose(findings, "Portal's checks of the changed files in", '', maxChars) : '';
}

// The model's one notice of new findings, on a tool result.
export const FILE_CHECK_INSTRUCTION = 'Fix them, or say plainly in your final answer that they remain.';
export function fileCheckInstruction(findings) {
  return Array.isArray(findings) && findings.length
    ? compose(findings, '[ODS Pixel check] Portal checked the files this response changed in', FILE_CHECK_INSTRUCTION, 1600) : '';
}

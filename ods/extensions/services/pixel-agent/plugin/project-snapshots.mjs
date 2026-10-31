// Portal's one-step undo for Playground projects. Project folders in Portal's
// workspace are not Git repositories, so a turn that broke a file had no way
// back: on 2026-10-30 a one-off Python patch run through exec doubled a
// game's chars.js. Now, the first time a Portal response touches a Playground
// project, ODS keeps a copy of that project's files before the call runs, and
// the owner can put them back with "Put back files" under the turn.
//
// A call touches Playground/<name> when it names a path in it: a read, write,
// edit or patch, an exec whose working directory is in it or whose command
// mentions it (also inside interpreter code), or an ODS tool given the
// project's folder. Exec is covered by what it names, not by watching what it
// writes, so the copy is taken before any command runs there.
//
// A restore first keeps a copy of the files as they are, so it can be undone
// the same way, then makes the project's files match the turn's copy: files
// the turn changed or deleted are written back and files it created are
// removed. It changes nothing outside that project folder, and it is refused
// while a Portal response runs in that chat, or in any chat whose response
// touched the project.
//
// Copies live in Portal's (OpenClaw's) state folder, which the agent's sandbox
// never mounts and no preview publishes:
// .ods-project-snapshots/<sha256 of workspace and project>/<time>-<id>/ holds
// manifest.json and one file per distinct content, named by its SHA-256. Only
// regular files are copied, read without following links (example-guard.mjs
// readFile). Links, hard-linked and special files, node_modules, dot-folders
// such as .git, files over 8 MiB and anything past 2,048 files or 32 MiB are
// left out and listed in the manifest; a restore leaves those paths as they
// are and says so. The last 5 turn copies and 3 restore copies of each project
// are kept.
import {createHash, randomBytes, randomUUID} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {readFile} from './example-guard.mjs';
import {filesystemError} from './example-projects.mjs';

export const SNAPSHOT_DIRECTORY = '.ods-project-snapshots';
export const SNAPSHOT_LIMITS = Object.freeze({fileBytes: 8 * 1024 * 1024, totalBytes: 32 * 1024 * 1024, files: 2048,
  entries: 8192, depth: 16, turns: 5, restores: 3});
const ROUTE = '/pixel-ods/project-restore';
const MAX_BODY = 512;
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
// How many kept and failed paths a receipt names; it counts all of them.
const LISTED = 8;
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const RUN = new RegExp(`^chatcmpl_${UUID}$`, 'i');
const SNAPSHOT = new RegExp(`^(?:chatcmpl|restore)_${UUID}$`, 'i');
const ENTRY = new RegExp(`^(\\d{15})-((?:chatcmpl|restore)_${UUID})$`);
const SHA = /^[a-f0-9]{64}$/;
const USER = /^ods-[a-f0-9]{64}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const REASONS = Object.freeze(['folder', 'link', 'large', 'limit', 'other']);

const sha256 = value => createHash('sha256').update(value).digest('hex');
const exact = (value, keys) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join() === keys.split(',').sort().join();
const projectName = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)
  && !value.endsWith('.') && !RESERVED.test(value);
const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());
const parentOf = relative => relative.includes('/') ? relative.slice(0, relative.lastIndexOf('/')) : '';
const baseOf = relative => relative.slice(relative.lastIndexOf('/') + 1);
const ancestors = relative => relative.split('/').slice(0, -1).map((_, index, parts) => parts.slice(0, index + 1).join('/'));
// A refusal to touch an entry that is not what a copy says is there.
const unsafe = message => Object.assign(new Error(message), {unsafe: true});
class DamagedSnapshot extends Error {}

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// The canonical workspace root, or null for one that is unusable. The
// configured root itself is trusted and may use a platform alias such as
// macOS /var -> /private/var; nothing below it may be a link.
function canonicalRoot(workspaceRoot) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return null;
  const root = fs.realpathSync(path.resolve(workspaceRoot));
  return root === path.parse(root).root || !fs.statSync(root).isDirectory() ? null : root;
}

// ------------------------------------------------------- what a call touches --

const FILE_KEYS = ['path', 'file_path', 'filePath'];
// The workspace paths each tool takes: a file, exec's working directory, or
// the project folder (or a file in it) an ODS project tool works on.
const PATH_KEYS = new Map([['read', FILE_KEYS], ['write', FILE_KEYS], ['edit', FILE_KEYS], ['exec', ['workdir']],
  ['pixel_ods_workspace_preview', ['relativeDirectory']], ['pixel_ods_workspace_artifact', ['relativePath', 'sourcePath']],
  ['pixel_ods_workspace_bundle', ['outputRoot']], ['pixel_ods_download_promote', ['relativePath']],
  ['pixel_ods_project_build', ['project']]]);
const PATCH_FILE = /^\*\*\* (?:(?:Add|Update|Delete) File|Move to): ([^\r\n]+)$/gm;
// Playground/<name> anywhere in a command, also inside interpreter code or a
// heredoc: a Python patch that opens Playground/<name>/chars.js names it.
const COMMAND_PROJECT = /(?<![A-Za-z0-9._-])playground[\\/]+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/gi;
const WORD = /[A-Za-z0-9][A-Za-z0-9._-]{0,63}/g;

// A path as the workspace sees it: relative, under the sandbox alias
// /workspace or under the configured root, normalized. Null for any other
// absolute path and for one that leaves the workspace.
function workspaceRelative(value, root) {
  if (typeof value !== 'string' || !value || value.length > 4096 || value.includes('\0')) return null;
  let text = value.replaceAll('\\', '/');
  const base = root.replaceAll('\\', '/').replace(/\/+$/, '');
  const folded = text.toLowerCase();
  if (folded === base.toLowerCase() || folded.startsWith(`${base.toLowerCase()}/`)) text = text.slice(base.length);
  else if (text === '/workspace' || text.startsWith('/workspace/')) text = text.slice('/workspace'.length);
  else if (/^(?:\/|[A-Za-z]:|~)/.test(text)) return null;
  const normal = path.posix.normalize(text.replace(/^\/+/, '') || '.');
  return normal === '..' || normal.startsWith('../') ? null : normal;
}

function projectOf(value, root) {
  const [first, name] = workspaceRelative(value, root)?.split('/') ?? [];
  return /^playground$/i.test(first ?? '') && projectName(name) ? name : null;
}

// The Playground project names one tool call touches: its path parameters, the
// files of a patch and, for exec, every Playground/<name> its command
// mentions. When exec runs in the Playground folder itself, a command word
// that names one of its folders (folders() lists them) counts too, as in
// "cd game && python3 fix.py". More names than the call uses only cost a copy.
export function projectsNamed(toolName, params, root, folders = () => []) {
  let name = toolName, args = params;
  if (toolName === 'tool_call') {
    name = typeof params?.id === 'string' ? params.id.split(':').at(-1) : '';
    args = params?.args;
  }
  if (!args || typeof args !== 'object' || typeof root !== 'string') return [];
  const found = new Set();
  const add = value => { const project = projectOf(value, root); if (project) found.add(project); };
  for (const key of PATH_KEYS.get(name) ?? []) add(args[key]);
  if (name === 'apply_patch' && typeof args.input === 'string') {
    for (const match of args.input.slice(0, 1048576).matchAll(PATCH_FILE)) add(match[1].trim());
  }
  if (name === 'pixel_ods_workspace_bundle' && Array.isArray(args.files)) {
    for (const file of args.files.slice(0, 64)) add(file?.source);
  }
  if (name === 'exec' && typeof args.command === 'string') {
    const command = args.command.slice(0, 65536);
    // A name ending a sentence ("see Playground/game.") does not end in a dot.
    for (const match of command.matchAll(COMMAND_PROJECT)) add(`Playground/${match[1].replace(/\.+$/, '')}`);
    if (/^playground$/i.test(workspaceRelative(args.workdir, root) ?? '')) {
      const present = new Set(folders());
      for (const [word] of command.matchAll(WORD)) if (present.has(word) && projectName(word)) found.add(word);
    }
  }
  return [...found];
}

// The folder names directly in Playground, for an exec run there.
function playgroundFolders(root) {
  try { return fs.readdirSync(path.join(root, 'Playground')); }
  catch (error) { if (filesystemError(error)) return []; throw error; }
}

// Playground/<name> in the canonical root, with the name as that folder holds
// it: a disk that ignores case finds Playground/Game as Playground/game, and
// the folder's own spelling names its copies, so a restore finds them
// whichever spelling a turn used. A missing project is an empty one, so a
// turn that creates it can be undone. Null when the workspace has no real
// Playground folder or the name is a file or link there.
function projectFolder(root, name) {
  if (!projectName(name)) return null;
  const playground = path.join(root, 'Playground');
  if (!realDirectory(lstatOrNull(playground))) return null;
  const entry = lstatOrNull(path.join(playground, name));
  if (entry && !realDirectory(entry)) return null;
  const names = entry ? fs.readdirSync(playground) : [];
  const actual = !entry || names.includes(name) ? name
    : names.find(item => item.toLowerCase() === name.toLowerCase()) ?? name;
  return {name: actual, folder: path.join(playground, actual)};
}

// ------------------------------------------------------------------ copies --

function privateDirectory(directory, create) {
  if (create) fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const info = fs.lstatSync(directory);
  if (!realDirectory(info) || (process.platform !== 'win32'
      && (info.uid !== process.getuid() || (info.mode & 0o077) !== 0))) throw unsafe('Project copies need a private folder');
  return directory;
}

// One project's kept copies, newest first.
function copies(store) {
  return fs.readdirSync(store).flatMap(name => {
    const match = ENTRY.exec(name);
    return match ? [{name, id: match[2], kind: match[2].startsWith('restore_') ? 'restore' : 'turn', time: Number(match[1])}] : [];
  }).sort((a, b) => b.time - a.time || (a.name < b.name ? 1 : -1));
}

function temporaryIn(store) {
  const temporary = path.join(store, `.tmp-${randomBytes(8).toString('hex')}`);
  fs.mkdirSync(temporary, {mode: 0o700});
  return temporary;
}

// A temporary that was not committed; one that cannot be removed now goes
// with the next prune.
function discard(temporary) {
  try { fs.rmSync(temporary, {recursive: true, force: true}); }
  catch (error) { if (!filesystemError(error)) throw error; }
}

function writeBlob(blobs, digest, content) {
  let fd;
  try { fd = fs.openSync(path.join(blobs, digest), 'wx', 0o600); }
  catch (error) { if (error.code === 'EEXIST') return; throw error; }
  try { fs.writeFileSync(fd, content); } finally { fs.closeSync(fd); }
}

// Writes the manifest into the temporary folder and renames that into place,
// so a copy is either complete or absent.
function commit(store, temporary, manifest) {
  const fd = fs.openSync(path.join(temporary, 'manifest.json'), 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(manifest)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, path.join(store, `${String(Date.parse(manifest.createdAt)).padStart(15, '0')}-${manifest.id}`));
}

// Keeps the newest copies of each kind and drops temporaries a crash left.
function prune(store, limits) {
  for (const name of fs.readdirSync(store)) {
    if (name.startsWith('.tmp-')) fs.rmSync(path.join(store, name), {recursive: true, force: true});
  }
  const counts = {turn: 0, restore: 0};
  for (const copy of copies(store)) {
    if (++counts[copy.kind] > (copy.kind === 'turn' ? limits.turns : limits.restores)) {
      fs.rmSync(path.join(store, copy.name), {recursive: true, force: true});
    }
  }
}

// Copies the regular files of one project folder into blobs and lists them,
// its folders and what it left out (each entry left out covers everything
// below it). A missing folder is an empty project. Every entry is lstat-ed
// and links are never followed. complete is false when the folder holds more
// entries than a copy visits; such a copy never removes files on a restore.
// A name with a backslash or a control character is neither copied nor
// listed, so no restore ever touches it.
export function captureProject(folder, blobs, limits = SNAPSHOT_LIMITS) {
  const files = [], dirs = [], skipped = [];
  let bytes = 0, entries = 0, complete = true;
  const top = lstatOrNull(folder);
  if (!top) return {complete, dirs, files, skipped};
  if (!realDirectory(top)) throw unsafe('The project folder is not a folder');
  const pending = [''];
  walk: while (pending.length) {
    const key = pending.shift();
    let names;
    try { names = fs.readdirSync(key ? path.join(folder, ...key.split('/')) : folder).sort(); }
    catch (error) {
      if (!key || !filesystemError(error)) throw error;
      dirs.splice(dirs.indexOf(key), 1);
      skipped.push({path: key, reason: 'other'});
      continue;
    }
    for (const name of names) {
      if (++entries > limits.entries) { complete = false; break walk; }
      if (/[\\\x00-\x1f\x7f]/.test(name)) continue;
      const relative = key ? `${key}/${name}` : name;
      const skip = reason => skipped.push({path: relative, reason});
      const target = path.join(folder, ...relative.split('/'));
      const stat = lstatOrNull(target);
      if (!stat) continue;
      if (stat.isSymbolicLink()) skip('link');
      else if (stat.isDirectory()) {
        if (name === 'node_modules' || name.startsWith('.') || relative.split('/').length >= limits.depth) skip('folder');
        else { dirs.push(relative); pending.push(relative); }
      } else if (!stat.isFile() || stat.nlink !== 1) skip('other');
      else if (stat.size > limits.fileBytes) skip('large');
      else if (files.length >= limits.files || bytes + stat.size > limits.totalBytes) skip('limit');
      else {
        let content = null;
        try { content = readFile(target, stat); } catch (error) { if (!filesystemError(error)) throw error; }
        if (!content) { skip('other'); continue; }
        const digest = sha256(content);
        writeBlob(blobs, digest, content);
        files.push({path: relative, size: content.length, sha256: digest, mode: stat.mode & 0o777});
        bytes += content.length;
      }
    }
  }
  return {complete, dirs, files, skipped};
}

const relativePath = (value, limits) => typeof value === 'string' && value.length <= 4096
  && value.split('/').length <= limits.depth && value.split('/').every(part => part && part !== '.' && part !== '..'
    && !/[\\\x00-\x1f\x7f]/.test(part));

function validManifest(value, id, project, limits) {
  if (!exact(value, 'schemaVersion,id,kind,project,createdAt,complete,dirs,files,skipped') || value.schemaVersion !== 1
      || value.id !== id || value.project !== project || !['turn', 'restore'].includes(value.kind)
      || typeof value.createdAt !== 'string' || !Number.isFinite(Date.parse(value.createdAt)) || typeof value.complete !== 'boolean'
      || !Array.isArray(value.dirs) || !Array.isArray(value.files) || !Array.isArray(value.skipped)
      || value.files.length > limits.files || value.dirs.length + value.files.length + value.skipped.length > limits.entries) return false;
  const seen = new Set();
  let bytes = 0;
  for (const dir of value.dirs) {
    if (!relativePath(dir, limits) || seen.has(dir)) return false;
    seen.add(dir);
  }
  for (const file of value.files) {
    if (!exact(file, 'path,size,sha256,mode') || !relativePath(file.path, limits) || seen.has(file.path)
        || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > limits.fileBytes || !SHA.test(file.sha256)
        || !Number.isInteger(file.mode) || file.mode < 0 || file.mode > 0o777) return false;
    seen.add(file.path);
    bytes += file.size;
  }
  return bytes <= limits.totalBytes && value.skipped.every(item => exact(item, 'path,reason')
    && relativePath(item.path, limits) && REASONS.includes(item.reason));
}

function readManifest(directory, id, project, limits) {
  const file = path.join(directory, 'manifest.json'), stat = lstatOrNull(file);
  const bytes = stat?.isFile() && !stat.isSymbolicLink() && stat.size <= MAX_MANIFEST_BYTES ? readFile(file, stat) : null;
  let value;
  try { value = JSON.parse(bytes?.toString('utf8') ?? ''); }
  catch (error) { if (error instanceof SyntaxError) throw new DamagedSnapshot('unreadable manifest'); throw error; }
  if (!validManifest(value, id, project, limits)) throw new DamagedSnapshot('invalid manifest');
  return value;
}

// The saved bytes a restore writes, each checked against its SHA-256 before
// anything in the project changes.
function loadBlobs(directory, files) {
  const blobs = new Map();
  for (const file of files) {
    if (blobs.has(file.sha256)) continue;
    const target = path.join(directory, file.sha256), stat = lstatOrNull(target);
    const bytes = stat?.isFile() && !stat.isSymbolicLink() && stat.size === file.size ? readFile(target, stat) : null;
    if (!bytes || sha256(bytes) !== file.sha256) throw new DamagedSnapshot('changed copy');
    blobs.set(file.sha256, bytes);
  }
  return blobs;
}

// ---------------------------------------------------------------- restore --

// A test for whether a copy left relative out: it or a folder above it.
function coverage(skipped) {
  const paths = new Set(skipped.map(item => item.path));
  return relative => paths.has(relative) || ancestors(relative).some(parent => paths.has(parent));
}

// What a restore changes so the project's files match the saved copy, given
// the copy just taken of them as they are (current). Files that differ or are
// gone are written back. When the saved copy is complete, files and folders
// it did not have are removed. A path either copy left out is never written
// or removed: the saved copy does not know it, or current could not keep it
// for an undo.
export function planRestore(saved, current) {
  const before = new Map(saved.files.map(file => [file.path, file]));
  const now = new Map(current.files.map(file => [file.path, file]));
  const keptBefore = coverage(saved.skipped), keptNow = coverage(current.skipped);
  const presentDirs = new Set(current.dirs), savedDirs = new Set(saved.dirs);
  return {
    write: saved.files.filter(file => !keptNow(file.path)
      && !(now.get(file.path)?.sha256 === file.sha256 && now.get(file.path).mode === file.mode)),
    remove: saved.complete ? current.files.filter(file => !before.has(file.path) && !keptBefore(file.path)).map(file => file.path) : [],
    // Deepest first, and only once empty.
    removeDirs: saved.complete ? current.dirs.filter(dir => !savedDirs.has(dir) && !keptBefore(dir))
      .sort((a, b) => b.split('/').length - a.split('/').length) : [],
    createDirs: saved.dirs.filter(dir => !presentDirs.has(dir) && !keptNow(dir)),
  };
}

// The real folder at relative inside folder, never reached through a link or
// a file. With create, missing folders are made.
function realFolder(folder, relative, create = false) {
  let current = folder;
  for (const part of relative ? relative.split('/') : []) {
    current = path.join(current, part);
    const stat = lstatOrNull(current);
    if (!stat && create) fs.mkdirSync(current);
    else if (!realDirectory(stat)) throw unsafe(`${relative} is not a folder`);
  }
  return current;
}

function removeFile(folder, relative) {
  const target = path.join(realFolder(folder, parentOf(relative)), baseOf(relative)), stat = lstatOrNull(target);
  if (stat && (!stat.isFile() || stat.isSymbolicLink())) throw unsafe(`${relative} is not a file`);
  if (stat) fs.unlinkSync(target);
}

// A saved file written beside its place and renamed over it, so the place
// holds the old file or the saved one. An empty folder there is removed
// first; a link or special file there stays.
function writeFile(folder, file, bytes) {
  const directory = realFolder(folder, parentOf(file.path), true);
  const target = path.join(directory, baseOf(file.path)), stat = lstatOrNull(target);
  if (realDirectory(stat)) fs.rmdirSync(target);
  else if (stat && (!stat.isFile() || stat.isSymbolicLink())) throw unsafe(`${file.path} is not a file`);
  const temporary = path.join(directory, `.ods-restore-${randomBytes(8).toString('hex')}`);
  try {
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, bytes); fs.fchmodSync(fd, file.mode); } finally { fs.closeSync(fd); }
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, {force: true}); }
}

// Applies a plan inside the project folder (made again if it was deleted).
// A path that cannot be changed is listed and the rest still proceed; a
// folder the turn made stays when it still holds what the restore keeps.
function applyPlan(folder, plan, blobs) {
  const failed = [];
  const attempt = (relative, change) => {
    try { change(); return true; }
    catch (error) { if (!filesystemError(error) && !error.unsafe) throw error; failed.push(relative); return false; }
  };
  if ((plan.write.length || plan.createDirs.length) && !lstatOrNull(folder)) fs.mkdirSync(folder);
  const removed = plan.remove.filter(relative => attempt(relative, () => removeFile(folder, relative))).length;
  for (const relative of plan.removeDirs) {
    try {
      const target = path.join(realFolder(folder, parentOf(relative)), baseOf(relative));
      if (realDirectory(lstatOrNull(target))) fs.rmdirSync(target);
    } catch (error) { if (!filesystemError(error) && !error.unsafe) throw error; }
  }
  for (const relative of plan.createDirs) attempt(relative, () => realFolder(folder, relative, true));
  const written = plan.write.filter(file => attempt(file.path, () => writeFile(folder, file, blobs.get(file.sha256)))).length;
  return {written, removed, failed};
}

// What the owner is told: the counts, and the paths the restore left as they
// are (what either copy left out, folders standing for their contents) or
// could not change, each list naming at most LISTED.
function restoreReceipt(request, saved, current, applied, undo) {
  const skipped = new Set([...saved.skipped, ...current.skipped].map(item => item.path));
  const kept = [...skipped].filter(item => !ancestors(item).some(parent => skipped.has(parent))).sort();
  const listed = items => items.filter(item => item.length <= 512).slice(0, LISTED);
  return {schemaVersion: 1, project: request.project, snapshot: request.snapshot, written: applied.written,
    removed: applied.removed, kept: listed(kept), keptCount: kept.length, failed: listed(applied.failed),
    failedCount: applied.failed.length, complete: saved.complete && current.complete, undo};
}

function validRequest(value) {
  return exact(value, 'project,snapshot,user') && typeof value.user === 'string' && USER.test(value.user)
    && typeof value.project === 'string' && value.project.startsWith('Playground/') && projectName(value.project.slice(11))
    && typeof value.snapshot === 'string' && SNAPSHOT.test(value.snapshot);
}

// Adds value to the bounded set at key; true when it was not there.
function note(map, key, value, keys = 512, values = 32) {
  let set = map.get(key);
  if (!set) {
    if (map.size >= keys) map.delete(map.keys().next().value);
    map.set(key, set = new Set());
  }
  if (set.has(value)) return false;
  if (set.size >= values) set.delete(set.values().next().value);
  set.add(value);
  return true;
}

// One per gateway. before() runs in before_tool_call with the call's final
// decision and keeps a copy of each project a response touches for the first
// time; restore() serves the owner's route. workspaceRoot() is the configured
// workspace, ownerRun(context) the owner run a delegated run works for,
// busy(user) whether a response runs in that chat, active(sessionKey) whether
// one runs in a session, and changed(root, name) hears of each restore that
// changed a project (the shipped example's guard keeps the owner's change).
export function createProjectSnapshots({agentId = 'pixel', workspaceRoot = () => undefined, ownerRun = () => undefined,
  busy = () => true, active = () => true, changed = () => {}, warn = () => {}, now = Date.now, limits = SNAPSHOT_LIMITS,
  stateDir = path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), SNAPSHOT_DIRECTORY)} = {}) {
  // The projects each response already has a copy of, and the sessions whose
  // calls touched each project, for the restore's busy check.
  const runs = new Map(), sessions = new Map();
  const storeFor = (key, create) => {
    privateDirectory(stateDir, create);
    return privateDirectory(path.join(stateDir, sha256(key)), create);
  };
  const manifest = (id, kind, project, captured) => ({schemaVersion: 1, id, kind, project: `Playground/${project.name}`,
    createdAt: new Date(now()).toISOString(), ...captured});
  // Older copies that cannot be removed now stay until the next copy.
  const tidy = store => {
    try { prune(store, limits); }
    catch (error) { if (!filesystemError(error)) throw error; warn(`Portal could not remove older project copies (${error.code}).`); }
  };

  function keep(key, project, id) {
    const store = storeFor(key, true);
    if (copies(store).some(copy => copy.id === id)) return;
    const temporary = temporaryIn(store);
    try { commit(store, temporary, manifest(id, 'turn', project, captureProject(project.folder, temporary, limits))); }
    finally { discard(temporary); }
    tidy(store);
  }

  function refusal(status, error) { return {status, body: {error}}; }

  return {
    before(event, context, decision) {
      if (decision?.block === true || context?.agentId !== agentId) return;
      const runId = ownerRun(context) ?? context?.runId ?? event?.runId;
      if (typeof runId !== 'string' || !RUN.test(runId)) return;
      let root = null;
      try { root = canonicalRoot(workspaceRoot()); } catch (error) { if (!filesystemError(error)) throw error; }
      if (!root) return;
      const toolName = context?.toolName ?? event?.toolName, folders = () => playgroundFolders(root);
      // The call as the model wrote it (an exec command before ODS wraps it)
      // and as it will run (a routed path or working directory).
      const names = new Set([...projectsNamed(toolName, event?.params, root, folders),
        ...projectsNamed(toolName, decision?.params, root, folders)]);
      for (const spelled of names) {
        let project = null;
        try { project = projectFolder(root, spelled); } catch (error) { if (!filesystemError(error)) throw error; }
        if (!project) continue;
        const key = `${root}\n${project.name}`;
        if (typeof context?.sessionKey === 'string') note(sessions, key, context.sessionKey);
        if (!note(runs, runId.toLowerCase(), key)) continue;
        try { keep(key, project, runId.toLowerCase()); }
        catch (error) {
          if (!filesystemError(error) && !error.unsafe) throw error;
          warn(`Portal could not keep a copy of Playground/${project.name} before this turn (${error.code ?? error.message}).`);
        }
      }
    },

    // {status, body}: 200 with the receipt, 400 for a malformed request, 404
    // when that copy is not kept, 409 while a response runs, 422 when the
    // copy is damaged (nothing changed) and 503 when the workspace or the
    // copies cannot be used.
    restore(request) {
      if (!validRequest(request)) return refusal(400, 'invalid-project-restore-request');
      const id = request.snapshot.toLowerCase();
      try {
        const root = canonicalRoot(workspaceRoot());
        const project = root && projectFolder(root, request.project.slice('Playground/'.length));
        if (!project) return refusal(503, 'project-restore-unavailable');
        const key = `${root}\n${project.name}`;
        if (busy(request.user) || [...sessions.get(key) ?? []].some(sessionKey => active(sessionKey))) {
          return refusal(409, 'project-restore-busy');
        }
        if (!lstatOrNull(path.join(stateDir, sha256(key)))) return refusal(404, 'project-snapshot-unavailable');
        const store = storeFor(key, false), copy = copies(store).find(item => item.id === id);
        if (!copy) return refusal(404, 'project-snapshot-unavailable');
        const saved = readManifest(path.join(store, copy.name), id, `Playground/${project.name}`, limits);
        const temporary = temporaryIn(store);
        let body;
        try {
          const current = captureProject(project.folder, temporary, limits), plan = planRestore(saved, current);
          const blobs = loadBlobs(path.join(store, copy.name), plan.write);
          const changes = plan.write.length + plan.remove.length + plan.removeDirs.length + plan.createDirs.length;
          // The files as they are now, kept first so this restore can be undone.
          const undo = changes ? `restore_${randomUUID()}` : null;
          if (undo) commit(store, temporary, manifest(undo, 'restore', project, current));
          body = restoreReceipt(request, saved, current, undo ? applyPlan(project.folder, plan, blobs)
            : {written: 0, removed: 0, failed: []}, undo);
          if (undo) changed(root, project.name);
        } finally { discard(temporary); }
        tidy(store);
        return {status: 200, body};
      } catch (error) {
        if (error instanceof DamagedSnapshot) return refusal(422, 'project-snapshot-damaged');
        if (filesystemError(error) || error.unsafe) return refusal(503, 'project-restore-unavailable');
        throw error;
      }
    },
  };
}

async function readRestoreRequest(req) {
  const contentType = String(req.headers?.['content-type'] ?? '').toLowerCase();
  if (contentType.split(';', 1)[0].trim() !== 'application/json') return null;
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_BODY) return null;
    chunks.push(Buffer.from(chunk));
  }
  let body;
  try { body = JSON.parse(Buffer.concat(chunks, total).toString('utf8')); }
  catch (error) { if (error instanceof SyntaxError) return null; throw error; }
  return validRequest(body) ? body : null;
}

export const projectSnapshotTesting = Object.freeze({applyPlan});

// POST {"user","project","snapshot"}: the host ingress asks for one owner
// restore, under that chat's history lock.
export function createProjectRestoreRoute({snapshots, sendJson}) {
  return async (req, res) => {
    if (req.url !== ROUTE) { sendJson(res, 400, {error: 'invalid request'}); return true; }
    if (req.method !== 'POST') { sendJson(res, 405, {error: 'method not allowed'}); return true; }
    const request = await readRestoreRequest(req);
    if (!request) { sendJson(res, 400, {error: 'invalid-project-restore-request'}); return true; }
    const {status, body} = snapshots.restore(request);
    sendJson(res, status, body);
    return true;
  };
}

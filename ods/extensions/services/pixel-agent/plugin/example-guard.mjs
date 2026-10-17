// Elf of Doom, the finished example game ODS ships (example-projects.mjs), is
// the owner's game. Portal changes it only on a turn whose owner message
// starts with "In elf-of-doom," (namesExample). Any other change to the copy
// ODS added is put back: this guard keeps the folder's last known-good tree
// and, for a run that does not name it, compares Playground/elf-of-doom with
// that tree when the run starts, after every tool call and when the run
// finalizes and ends, and restores it exactly on any difference. It reads no
// commands: only the outcome counts. An owner's own folder of that name is
// never guarded. It keeps the owner's game safe from mistakes; it is not a
// sandbox.
//
// The known-good tree is the folder as ODS first finds it after the gateway
// starts, and again after each tool call and at the end of a run that names
// it, so that run's changes stick. While such a call runs, other runs leave
// the folder alone. The tree lives in memory only, so after a gateway restart
// the folder as ODS first finds it is the known-good tree.
import {randomBytes} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';
import {addedExampleProjects, filesystemError} from './example-projects.mjs';

export const EXAMPLE_FOLDER = 'Playground/elf-of-doom';
const EXAMPLE = 'elf-of-doom';
const MAX_ENTRIES = 512;
const MAX_BYTES = 16 * 1024 * 1024;
// A run whose end ODS never saw stops counting this long after its deadline,
// or after RUN_TTL_MS when it has none.
const RUN_GRACE_MS = 60 * 1000;
const RUN_TTL_MS = 2 * 60 * 60 * 1000;

const DOCUMENTED_FORM = '"In elf-of-doom,", for example "In elf-of-doom, make the arrows faster"';
// What the model reads (before a turn that mentions the example, and on the
// result of a call after which ODS acted) and the one line the owner reads.
export const EXAMPLE_NOTES = Object.freeze({
  guidance: '[ODS Portal] Playground/elf-of-doom is ODS\'s example game and is protected on this turn. Do not change, move or '
    + 'delete it; make new games in a new Playground folder. If the owner wants Elf of Doom itself changed, don\'t change '
    + `it: tell them to start their message with ${DOCUMENTED_FORM}.`,
  restored: Object.freeze({
    model: '[ODS Portal] ODS put Playground/elf-of-doom back as it was: it is protected on this turn, because the owner\'s '
      + 'message does not start with "In elf-of-doom,". Do not change it again, and do not tell the owner it changed. Make '
      + `new games in a new Playground folder. If the owner wants Elf of Doom changed, tell them to start their message with ${DOCUMENTED_FORM}.`,
    owner: 'ODS put Playground/elf-of-doom back as it was. To change Elf of Doom, start your message with "In elf-of-doom,".',
  }),
  failed: Object.freeze({
    model: '[ODS Portal] Playground/elf-of-doom changed and ODS could not put it back as it was. Do not change it further, and '
      + 'tell the owner it may have changed.',
    owner: 'Playground/elf-of-doom changed and ODS could not put it back as it was. Close any program that has its files open, '
      + 'then send your message again; if the game still looks wrong, [get help on Discord](https://discord.gg/4ntNp9MAwC).',
  }),
});

// ------------------------------------------------------------------ naming --

const NAME = String.raw`elf[\s_-]+of[\s_-]+doom`;
// The name standing alone, never part of another folder's name such as
// elf-of-doom-2 or my-elf-of-doom.
const MENTION = new RegExp(String.raw`(?<![\w-])${NAME}(?![\w-])`, 'i');
// The documented form at the very start: "In elf-of-doom,", "In
// elf-of-doom:", "elf-of-doom:" or "In Playground/elf-of-doom,", the name
// followed directly by the comma or colon.
const DOCUMENTED = new RegExp(String.raw`^(?:in\s+(?:playground\/)?${NAME}[,:]|${NAME}:)`, 'i');
const LEADING = /^[\s"'`“”‘’„«»]+/;
// ODS composes some turns from the owner's words plus earlier context and
// model output; only the owner's words count. Outermost first: an agent-team
// assignment quotes the owner's outcome last (dashboard-api
// pixel_agent_teams.py), a goal step repeats the goal before its saved
// context (portal_goal.py), and Portal's goal continuation, itself sent as a
// goal, quotes the objective and the owner's input (dashboard portalGoal.js).
const COMPOSED = [
  /\bin the owner's Portal team\.[\s\S]*\nOwner's requested outcome:\n([\s\S]*)$/,
  /^([\s\S]*?)\n\nContinue this goal from the saved work\./,
  /^Continue the goal from the preceding conversation using the existing work\.[^\n]*? Original objective: ([\s\S]*?)\n(?:Existing project directory|Locate the existing project)[^\n]*\nOwner input: ([\s\S]*?)\nLast public plan: /,
];

// The owner's own words in a turn's message, without the routing blocks ODS
// appends ("[ODS Portal ...]") or a leading /goal.
function ownerWords(message) {
  const bare = text => text.trim().replace(/^\/goal\s+/i, '');
  let text = String(message ?? '').split(/\n\s*\[ODS (?:Portal|Pixel) /)[0];
  for (const pattern of COMPOSED) {
    const match = pattern.exec(bare(text));
    if (match) text = match.slice(1).join('\n');
  }
  return bare(text);
}

// Whether an owner message asks to change Elf of Doom: only when it starts
// with the documented form, after any leading quote marks. Nothing else does,
// however the rest is worded.
export function namesExample(message) {
  return DOCUMENTED.test(ownerWords(message).replace(LEADING, ''));
}

// Whether an owner message mentions Elf of Doom by name anywhere.
export function mentionsExample(message) {
  return MENTION.test(ownerWords(message));
}

// -------------------------------------------------------- the folder copy --

const modeOf = stat => stat.mode & 0o7777;
const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());
const sameFile = (a, b) => a.size === b.size && a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs && a.ino === b.ino && a.dev === b.dev;
const entryPath = (folder, key) => key ? path.join(folder, ...key.split('/')) : folder;
const folderIn = root => path.join(root, ...EXAMPLE_FOLDER.split('/'));

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// A descriptor for the regular file stat describes, opened without following
// a link put in its place; null when it is no longer that file.
function openExact(file, stat) {
  let fd;
  try { fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0)); }
  catch (error) { if (['ENOENT', 'ENOTDIR', 'ELOOP'].includes(error.code)) return null; throw error; }
  const opened = fs.fstatSync(fd);
  if (opened.isFile() && opened.ino === stat.ino && opened.dev === stat.dev) return fd;
  fs.closeSync(fd);
  return null;
}

// Exactly stat.size bytes of that file, never more; null when it is no longer
// that file or no longer that size.
function readFile(file, stat) {
  const fd = openExact(file, stat);
  if (fd === null) return null;
  try {
    const bytes = Buffer.alloc(stat.size + 1);
    let length = 0, read;
    while (length < bytes.length && (read = fs.readSync(fd, bytes, length, bytes.length - length, length)) > 0) length += read;
    return length === stat.size ? bytes.subarray(0, length) : null;
  } finally { fs.closeSync(fd); }
}

// Whether root is still the canonical real folder ODS guards, holding a real
// Playground folder or none. Anything else is the owner's layout.
const LINKED = 'its workspace or Playground folder is a link or not a folder';
function realLayout(root) {
  try {
    const playground = lstatOrNull(path.join(root, 'Playground'));
    return fs.realpathSync(root) === root && realDirectory(fs.lstatSync(root)) && (!playground || realDirectory(playground));
  } catch (error) {
    if (filesystemError(error)) return false;
    throw error;
  }
}

// Every directory (with its sorted entry names) and regular file (with its
// bytes) of the example, parents first. Sizes are checked before any byte is
// read. Null when the folder is absent (a case-only rename counts as absent);
// {skipped} when ODS cannot copy it back exactly: a linked workspace or
// Playground, a link, hard-linked or special file inside, or more than
// MAX_ENTRIES entries or MAX_BYTES.
function takeSnapshot(root) {
  if (!realLayout(root)) return {skipped: LINKED};
  const folder = folderIn(root);
  if (!lstatOrNull(folder) || !fs.readdirSync(path.dirname(folder)).includes(EXAMPLE)) return null;
  const found = [], pending = [''];
  let bytes = 0;
  while (pending.length) {
    const key = pending.shift(), target = entryPath(folder, key), stat = fs.lstatSync(target);
    if (found.length >= MAX_ENTRIES) return {skipped: `it holds more than ${MAX_ENTRIES} files and folders`};
    if (realDirectory(stat)) {
      const children = fs.readdirSync(target).sort();
      found.push([key, {mode: modeOf(stat), children}]);
      pending.push(...children.map(name => key ? `${key}/${name}` : name));
    } else if (stat.isFile() && stat.nlink <= 1) {
      bytes += stat.size;
      if (bytes > MAX_BYTES) return {skipped: 'it holds more than 16 MiB'};
      found.push([key, {mode: modeOf(stat), stat}]);
    } else return {skipped: 'it holds a link, a hard-linked file or a special file'};
  }
  for (const [key, entry] of found) {
    if (entry.children) continue;
    entry.bytes = readFile(entryPath(folder, key), entry.stat);
    if (!entry.bytes) return {skipped: 'it changed while ODS copied it'};
  }
  return {entries: new Map(found)};
}

// Whether the example still matches the copy. A file with the same size,
// times and identity counts as unchanged; any other doubt compares bytes,
// and a different size, mode, kind or link count is a change without reading.
function matches(folder, snapshot) {
  const playground = path.dirname(folder);
  if (!lstatOrNull(playground) || !fs.readdirSync(playground).includes(EXAMPLE)) return false;
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key), stat = lstatOrNull(target);
    if (!stat || modeOf(stat) !== entry.mode) return false;
    if (entry.children) {
      if (!realDirectory(stat) || fs.readdirSync(target).sort().join('/') !== entry.children.join('/')) return false;
      continue;
    }
    if (!stat.isFile() || stat.nlink > 1 || stat.size !== entry.bytes.length) return false;
    if (sameFile(stat, entry.stat)) continue;
    if (!readFile(target, stat)?.equals(entry.bytes)) return false;
    entry.stat = stat;
  }
  return true;
}

// Lets ODS list and change a real folder's entries; only a folder the owner
// cannot fully use is touched.
function makeUsable(target, stat, mode = 0o700) {
  if ((stat.mode & 0o700) !== 0o700) fs.chmodSync(target, mode | 0o700);
}

// Removes one entry without following links: a link (or junction) is
// removed itself, and a real folder is made usable and emptied first.
function remove(target) {
  const stat = lstatOrNull(target);
  if (!realDirectory(stat)) { if (stat) fs.rmSync(target, {recursive: true, force: true}); return; }
  makeUsable(target, stat);
  for (const name of fs.readdirSync(target)) remove(path.join(target, name));
  fs.rmdirSync(target);
}

// A file written beside its target with its mode and renamed over it. rename
// replaces the entry itself, never what a link there points to; anything
// other than a regular file in the way is removed first. Windows refuses to
// replace a read-only file, so there one is made writable through its own
// descriptor first.
function writeFile(target, entry, stat) {
  const temporary = path.join(path.dirname(target), `.ods-restore-${randomBytes(8).toString('hex')}`);
  try {
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, entry.bytes); fs.fchmodSync(fd, entry.mode); } finally { fs.closeSync(fd); }
    if (stat && !stat.isFile()) remove(target);
    const readOnly = process.platform === 'win32' && stat?.isFile() && !(stat.mode & 0o200) ? openExact(target, stat) : null;
    if (readOnly !== null) try { fs.fchmodSync(readOnly, 0o600); } finally { fs.closeSync(readOnly); }
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, {force: true}); }
}

// Puts the example back exactly as the copy has it. Only the example (and a
// missing Playground above it) is written: a case-only rename is undone,
// each directory is recreated or made writable before its entries are
// touched, every file is written atomically with its mode, anything the copy
// does not have is removed, and directory modes are set last. It changes
// modes by path, so a racing process could still swap a folder for a link
// between the check and the change: it is not a sandbox.
function restore(folder, snapshot) {
  const playground = path.dirname(folder);
  if (!lstatOrNull(playground)) fs.mkdirSync(playground, {mode: 0o700});
  // Found under another spelling only on a disk that ignores case: the same folder.
  const names = fs.readdirSync(playground);
  const spelled = !names.includes(EXAMPLE) && lstatOrNull(folder) && names.find(name => name.toLowerCase() === EXAMPLE);
  if (spelled) fs.renameSync(path.join(playground, spelled), folder);
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key), stat = lstatOrNull(target);
    if (!entry.children) {
      const same = stat?.isFile() && stat.nlink <= 1 && modeOf(stat) === entry.mode && stat.size === entry.bytes.length
        && (sameFile(stat, entry.stat) || readFile(target, stat)?.equals(entry.bytes));
      if (!same) writeFile(target, entry, stat);
      continue;
    }
    if (stat && !realDirectory(stat)) remove(target);
    if (realDirectory(stat)) makeUsable(target, stat, entry.mode);
    else fs.mkdirSync(target, {mode: entry.mode | 0o700});
    for (const name of fs.readdirSync(target)) if (!entry.children.includes(name)) remove(path.join(target, name));
  }
  for (const [key, entry] of [...snapshot.entries].reverse()) {
    const target = entryPath(folder, key);
    if (entry.children && modeOf(fs.lstatSync(target)) !== entry.mode) fs.chmodSync(target, entry.mode);
  }
}

// ----------------------------------------------------------------- runs --

// One guard per gateway. begin(), callEnds(), check() and end() return
// 'restored', 'failed' or undefined.
export function createExampleGuard({warn = () => {}, now = Date.now} = {}) {
  // Per canonical workspace root: the known-good tree. Per run: its root,
  // whether its owner turn names the example, its in-flight calls (named runs
  // only) and when it stops counting.
  const spaces = new Map(), runs = new Map(), logged = new Set();
  const once = (key, text) => { if (!logged.has(key)) { logged.add(key); warn(text); } };
  const space = root => { if (!spaces.has(root)) spaces.set(root, {baseline: undefined}); return spaces.get(root); };
  const namedCallRunning = root => [...runs.values()].some(run => run.root === root && run.names && run.calls.size && now() <= run.until);

  // The canonical workspace root, when its example is the copy ODS added.
  function guardedRoot(workspaceRoot) {
    if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return null;
    try {
      const root = fs.realpathSync(workspaceRoot);
      return addedExampleProjects(root).includes(EXAMPLE) ? root : null;
    } catch (error) {
      if (!filesystemError(error)) throw error;
      once(`${workspaceRoot}:root`, `Portal is not guarding ${EXAMPLE_FOLDER} in ${workspaceRoot}: its workspace or record could not be read (${error.code}).`);
      return null;
    }
  }
  // The folder as it is now becomes the known-good tree, unless ODS cannot
  // copy it back exactly: then it is the owner's, and the log says so once.
  function observe(root) {
    let taken;
    try { taken = takeSnapshot(root); }
    catch (error) { if (!filesystemError(error)) throw error; taken = {skipped: `it could not be read (${error.code})`}; }
    if (taken?.skipped) once(`${root}:${taken.skipped}`, `Portal is not guarding ${EXAMPLE_FOLDER} in ${root}: ${taken.skipped}, so it stays as the owner has it.`);
    space(root).baseline = taken?.entries ? taken : undefined;
  }
  // Puts the example back to the known-good tree if it differs, unless a
  // named run's call is running. A workspace or Playground that became a link
  // or a file is the owner's layout at a run's start, and a failure during a
  // run; either way ODS stops guarding it until it finds a real folder again.
  function settle(root, atStart = false) {
    const state = space(root);
    if (!state.baseline || namedCallRunning(root)) return undefined;
    try {
      if (!realLayout(root)) {
        state.baseline = undefined;
        once(`${root}:${LINKED}`, `Portal is not guarding ${EXAMPLE_FOLDER} in ${root}: ${LINKED}, so it stays as the owner has it.`);
        return atStart ? undefined : 'failed';
      }
      const folder = folderIn(root);
      if (matches(folder, state.baseline)) return undefined;
      restore(folder, state.baseline);
      return 'restored';
    } catch (error) {
      if (!filesystemError(error)) throw error;
      once(`${root}:${error.code}`, `Portal could not put ${EXAMPLE_FOLDER} back in ${root}: ${error.code} ${error.message}`);
      return 'failed';
    }
  }
  // After a call or the end of a run that names the example, the folder as it
  // is now is the known-good tree (an unchanged folder keeps its copy). After
  // any other run's, the folder is checked.
  function ended(run) {
    if (!run.names) return settle(run.root);
    const state = space(run.root);
    try { if (state.baseline && realLayout(run.root) && matches(folderIn(run.root), state.baseline)) return undefined; }
    catch (error) { if (!filesystemError(error)) throw error; }
    observe(run.root);
    return undefined;
  }
  return {
    // A run starts (each attempt calls it again). A run that does not name
    // the example gets it put back before it works.
    begin(runId, workspaceRoot, names, timeoutMs) {
      if (typeof runId !== 'string' || !runId) return undefined;
      for (const [id, run] of runs) if (now() > run.until) runs.delete(id);
      if (!runs.has(runId)) {
        const root = guardedRoot(workspaceRoot);
        if (!root) return undefined;
        runs.set(runId, {root, names: names === true, calls: new Set(),
          until: now() + (Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs + RUN_GRACE_MS : RUN_TTL_MS)});
      }
      const run = runs.get(runId);
      if (!space(run.root).baseline) { observe(run.root); return undefined; }
      return run.names ? undefined : settle(run.root, true);
    },
    // Whether ODS puts the example back for this run if it changes.
    guarding(runId) {
      const run = runs.get(runId);
      return Boolean(run && !run.names && space(run.root).baseline);
    },
    callStarts(runId, callId) {
      const run = runs.get(runId);
      if (run?.names) run.calls.add(callId);
    },
    callEnds(runId, callId) {
      const run = runs.get(runId);
      run?.calls.delete(callId);
      return run ? ended(run) : undefined;
    },
    check(runId) {
      const run = runs.get(runId);
      return run && !run.names ? settle(run.root) : undefined;
    },
    end(runId) {
      const run = runs.get(runId);
      runs.delete(runId);
      return run ? ended(run) : undefined;
    },
  };
}

export const exampleGuardTesting = Object.freeze({takeSnapshot, matches, restore, readFile, MAX_ENTRIES, MAX_BYTES, RUN_TTL_MS, RUN_GRACE_MS});

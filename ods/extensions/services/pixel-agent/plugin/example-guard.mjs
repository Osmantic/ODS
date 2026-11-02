// Elf of Doom, the finished example game ODS ships (example-projects.mjs), is
// the owner's game. Portal changes it only on a turn whose owner message
// starts with "In elf-of-doom," (namesExample). For the copy ODS added, this
// guard keeps a known-good tree of Playground/elf-of-doom in memory and undoes
// what a run that does not name it changes there while that run works:
// - When a run starts while no other Portal run works in that workspace, the
//   folder as it is then becomes the known-good tree. Whatever changed while
//   Portal was idle is the owner's and stays: an edit made by hand, a folder
//   deleted by hand, or what an earlier run's background process left after
//   that run ended.
// - After each tool call of a run that does not name it, at its finalize and
//   at its end, the folder is compared with that tree and restored exactly on
//   any difference. It reads no commands: only the outcome counts. Nothing
//   it finds there is deleted: what it takes out (an edited file, or a file
//   or folder moved in) goes to a new folder beside it,
//   Playground/elf-of-doom-recovered-<UTC time>, and the owner's line names
//   that folder.
// - From the start of a run that names it until that run ends (or its
//   deadline plus a grace, if ODS never sees the end), no other run's change
//   is undone, and when it ends the folder as it is then becomes the
//   known-good tree. So with two chats at once, what one changes while the
//   other names the example stays. A run that starts while another works
//   keeps the known-good tree.
// An owner's own folder of that name is never guarded. This keeps the
// owner's game safe from mistakes; it is not a sandbox.
import {randomBytes} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';
import {addedExampleProjects, filesystemError} from './example-projects.mjs';

const EXAMPLE_FOLDER = 'Playground/elf-of-doom';
const EXAMPLE = 'elf-of-doom';
const MAX_ENTRIES = 512;
const MAX_BYTES = 16 * 1024 * 1024;
// A run whose end ODS never saw stops counting this long after its deadline
// (Portal's ingress gives a response 120 s past it), or after RUN_TTL_MS when
// it has none.
const RUN_GRACE_MS = 2 * 60 * 1000;
const RUN_TTL_MS = 2 * 60 * 60 * 1000;

const DOCUMENTED_FORM = '"In elf-of-doom," (with the comma), for example "In elf-of-doom, make the arrows faster"';
// The owner's line for each outcome: its first sentence, then the rest.
// exampleNotice puts where ODS moved what it took out between the two.
const OWNER_LINES = Object.freeze({
  restored: ['ODS put Playground/elf-of-doom back as it was.',
    `To change Elf of Doom, send a new message that starts with ${DOCUMENTED_FORM}.`],
  failed: ['Playground/elf-of-doom changed and ODS could not put it back as it was, so it stays as it is now.',
    'If the game no longer works, send a new message that starts with "In elf-of-doom," asking Portal to fix it, or '
      + '[get help on Discord](https://discord.gg/4ntNp9MAwC).'],
});
// What the model reads (before a turn that mentions the example, and on the
// result of a call after which ODS acted) and the one line the owner reads at
// the end of the reply when ODS moved nothing out of the example.
export const EXAMPLE_NOTES = Object.freeze({
  guidance: '[ODS Portal] Playground/elf-of-doom is ODS\'s example game and is protected on this turn. Do not change, move or '
    + 'delete it; make new games in a new Playground folder. If the owner wants Elf of Doom itself changed, don\'t change '
    + `it: tell them to send a new message that starts with ${DOCUMENTED_FORM}.`,
  restored: Object.freeze({
    model: '[ODS Portal] ODS put Playground/elf-of-doom back as it was: it is protected on this turn. Do not change it again, '
      + 'and do not tell the owner it changed. Make new games in a new Playground folder. If the owner wants Elf of Doom '
      + `changed, tell them to send a new message that starts with ${DOCUMENTED_FORM}.`,
    owner: OWNER_LINES.restored.join(' '),
  }),
  failed: Object.freeze({
    model: '[ODS Portal] Playground/elf-of-doom changed and ODS could not put it back as it was. Do not change it further, and '
      + 'tell the owner it may have changed.',
    owner: OWNER_LINES.failed.join(' '),
  }),
});
const RECOVERY_NAMED = 3;

// The owner's one line for a turn's outcome ('restored' or 'failed'). When
// ODS moved anything out of the example during the turn (recovered: the
// Playground folders it moved it to, oldest first), the line says where:
// the first three by name and how many more, so it stays one short line.
export function exampleNotice(outcome, recovered = []) {
  const [first, rest] = OWNER_LINES[outcome];
  if (!recovered.length) return `${first} ${rest}`;
  const more = recovered.length - RECOVERY_NAMED;
  const places = [...recovered.slice(0, RECOVERY_NAMED),
    ...(more > 0 ? [`${more} more Playground folders whose names start with ${EXAMPLE}-recovered-`] : [])];
  const where = places.length > 1 ? `${places.slice(0, -1).join(', ')} and ${places.at(-1)}` : places[0];
  return `${first} What ODS took out of it is in ${where}. ${rest}`;
}

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
// that file or no longer that size. Project snapshots read with it too.
export function readFile(file, stat) {
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
// Playground folder or none. Anything else is the owner's layout. A folder
// that cannot be read throws its filesystem error.
const LINKED = 'its workspace or Playground folder is a link or not a folder';
function realLayout(root) {
  const playground = lstatOrNull(path.join(root, 'Playground'));
  return fs.realpathSync(root) === root && realDirectory(fs.lstatSync(root)) && (!playground || realDirectory(playground));
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

// Whether the file at target (with stat) is still the copy's file: a regular
// file with one name and the same mode and size, and the same identity and
// times or else the same bytes. A different size is a change without reading.
function sameAsCopy(target, stat, entry) {
  return Boolean(stat?.isFile()) && stat.nlink <= 1 && modeOf(stat) === entry.mode && stat.size === entry.bytes.length
    && (sameFile(stat, entry.stat) || Boolean(readFile(target, stat)?.equals(entry.bytes)));
}

// Whether the example still matches the copy, kind, mode and link count
// included.
function matches(folder, snapshot) {
  const playground = path.dirname(folder);
  if (!lstatOrNull(playground) || !fs.readdirSync(playground).includes(EXAMPLE)) return false;
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key), stat = lstatOrNull(target);
    if (entry.children) {
      if (!realDirectory(stat) || modeOf(stat) !== entry.mode || fs.readdirSync(target).sort().join('/') !== entry.children.join('/')) return false;
      continue;
    }
    if (!sameAsCopy(target, stat, entry)) return false;
    entry.stat = stat;
  }
  return true;
}

// Lets ODS list and change a real folder's entries; only a folder the owner
// cannot fully use is touched.
function makeUsable(target, stat, mode = 0o700) {
  if ((stat.mode & 0o700) !== 0o700) fs.chmodSync(target, mode | 0o700);
}

// The recovery folder of one restore: made beside the example only when ODS
// first moves something into it, as Playground/elf-of-doom-recovered-<UTC
// time>, or that name with -2, -3 and so on when something already has it.
// Making it never replaces or enters anything that is there. A Playground
// the owner cannot write to is made writable for that and gets its mode
// back. name is the folder's name once it is made.
function recoveryBeside(folder, time) {
  const playground = path.dirname(folder);
  const base = `${EXAMPLE}-recovered-${new Date(time).toISOString().replace(/[-:]|\.\d+/g, '')}`;
  const recovery = {name: undefined};
  const reserve = () => {
    const stat = fs.lstatSync(playground);
    makeUsable(playground, stat, modeOf(stat));
    try {
      for (let n = 1; ; n++) {
        const name = n === 1 ? base : `${base}-${n}`;
        try { fs.mkdirSync(path.join(playground, name)); return name; }
        catch (error) { if (error.code !== 'EEXIST') throw error; }
      }
    } finally { if (modeOf(fs.lstatSync(playground)) !== modeOf(stat)) fs.chmodSync(playground, modeOf(stat)); }
  };
  // Where the entry at key goes: the same relative path in the recovery
  // folder, and the example's own folder (key '') as elf-of-doom in it.
  recovery.place = key => {
    recovery.name ??= reserve();
    const destination = path.join(playground, recovery.name, ...(key || EXAMPLE).split('/'));
    fs.mkdirSync(path.dirname(destination), {recursive: true});
    return destination;
  };
  return recovery;
}

// Takes the entry at target out of the example without deleting it: a link
// (or junction) is removed itself, never what it points to, and anything
// else is renamed to its place in the recovery folder, on the same disk, so
// no byte is copied or lost. A folder the owner cannot write to is made
// writable for the move (POSIX needs that to move a folder to another
// folder) and gets its mode back after.
function setAside(target, key, recovery) {
  const stat = lstatOrNull(target);
  if (!stat) return;
  if (stat.isSymbolicLink()) { fs.rmSync(target, {recursive: true, force: true}); return; }
  const destination = recovery.place(key);
  const locked = stat.isDirectory() && !(stat.mode & 0o200);
  if (locked) fs.chmodSync(target, modeOf(stat) | 0o200);
  fs.renameSync(target, destination);
  if (locked) fs.chmodSync(destination, modeOf(stat));
}

// A file written beside its target with its mode and renamed into place.
// restore has set aside whatever was there, so nothing is replaced.
function writeFile(target, entry) {
  const temporary = path.join(path.dirname(target), `.ods-restore-${randomBytes(8).toString('hex')}`);
  try {
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, entry.bytes); fs.fchmodSync(fd, entry.mode); } finally { fs.closeSync(fd); }
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, {force: true}); }
}

// Puts the example back exactly as the copy has it. Only the example, a
// missing Playground above it and the recovery folder beside it are written:
// a case-only rename is undone, each directory is recreated or made writable
// before its entries are touched, anything in the way that the copy does not
// have exactly (an extra entry, a changed file, a file where a folder
// belongs) is set aside into the recovery folder, every file is written with
// its mode, and directory modes are set last. It changes modes by path, so a
// racing process could still swap a folder for a link between the check and
// the change: it is not a sandbox.
function restore(folder, snapshot, recovery) {
  const playground = path.dirname(folder);
  if (!lstatOrNull(playground)) fs.mkdirSync(playground, {mode: 0o700});
  // Found under another spelling only on a disk that ignores case: the same folder.
  const names = fs.readdirSync(playground);
  const spelled = !names.includes(EXAMPLE) && lstatOrNull(folder) && names.find(name => name.toLowerCase() === EXAMPLE);
  if (spelled) fs.renameSync(path.join(playground, spelled), folder);
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key), stat = lstatOrNull(target);
    if (!entry.children) {
      if (!sameAsCopy(target, stat, entry)) { setAside(target, key, recovery); writeFile(target, entry); }
      continue;
    }
    if (stat && !realDirectory(stat)) setAside(target, key, recovery);
    if (realDirectory(stat)) makeUsable(target, stat, entry.mode);
    else fs.mkdirSync(target, {mode: entry.mode | 0o700});
    for (const name of fs.readdirSync(target)) {
      if (!entry.children.includes(name)) setAside(path.join(target, name), key ? `${key}/${name}` : name, recovery);
    }
  }
  for (const [key, entry] of [...snapshot.entries].reverse()) {
    const target = entryPath(folder, key);
    if (entry.children && modeOf(fs.lstatSync(target)) !== entry.mode) fs.chmodSync(target, entry.mode);
  }
}

// ----------------------------------------------------------------- runs --

// One guard per gateway. begin(), check() and end() return undefined when
// ODS did nothing, or {outcome, recovered}: outcome 'restored' or 'failed',
// and recovered, when ODS moved anything out of the example, the folder it
// moved it to (Playground/elf-of-doom-recovered-...), even when the restore
// then failed.
export function createExampleGuard({warn = () => {}, now = Date.now} = {}) {
  // Per canonical workspace root: the known-good tree. Per run: its root,
  // whether its owner turn names the example and when it stops counting.
  const baselines = new Map(), runs = new Map(), logged = new Set();
  const once = (key, text) => { if (!logged.has(key)) { logged.add(key); warn(text); } };
  const working = root => [...runs.values()].some(run => run.root === root);
  const namedWorking = root => [...runs.values()].some(run => run.root === root && run.names);

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
  // The folder as it is now becomes the known-good tree, unless it is gone
  // or ODS cannot copy it back exactly: then it is the owner's, and a copy
  // ODS cannot take is logged once.
  function observe(root) {
    let taken;
    try { taken = takeSnapshot(root); }
    catch (error) { if (!filesystemError(error)) throw error; taken = {skipped: `it could not be read (${error.code})`}; }
    if (taken?.skipped) once(`${root}:${taken.skipped}`, `Portal is not guarding ${EXAMPLE_FOLDER} in ${root}: ${taken.skipped}, so it stays as the owner has it.`);
    if (taken?.entries) baselines.set(root, taken);
    else baselines.delete(root);
  }
  // Puts the example back to the known-good tree if it differs, unless a run
  // that names it is working (so never for such a run itself). While the
  // workspace or Playground is a link or not a folder nothing is written, and
  // the tree is kept for a later check.
  function settle(root) {
    const baseline = baselines.get(root);
    if (!baseline || namedWorking(root)) return undefined;
    let recovery;
    const result = outcome => recovery?.name ? {outcome, recovered: `${path.posix.dirname(EXAMPLE_FOLDER)}/${recovery.name}`} : {outcome};
    try {
      if (!realLayout(root)) {
        once(`${root}:put-back:${LINKED}`, `Portal could not put ${EXAMPLE_FOLDER} back in ${root}: ${LINKED}.`);
        return result('failed');
      }
      const folder = folderIn(root);
      if (matches(folder, baseline)) return undefined;
      recovery = recoveryBeside(folder, now());
      restore(folder, baseline, recovery);
      return result('restored');
    } catch (error) {
      if (!filesystemError(error)) throw error;
      once(`${root}:${error.code}`, `Portal could not put ${EXAMPLE_FOLDER} back in ${root}: ${error.code} ${error.message}`);
      return result('failed');
    }
  }
  // Runs whose end ODS never saw stop counting once their time is up; one
  // that names the example then ends as if ODS had seen it.
  function expire() {
    for (const [id, run] of runs) {
      if (now() <= run.until) continue;
      runs.delete(id);
      if (run.names) observe(run.root);
    }
  }
  return {
    // A run starts; each attempt calls it again, which refreshes its time. A
    // new run that finds no other run working takes the folder as it is as
    // the known-good tree; it never restores. A later attempt of a run that
    // does not name it gets that run's earlier changes put back.
    begin(runId, workspaceRoot, names, timeoutMs) {
      if (typeof runId !== 'string' || !runId) return undefined;
      expire();
      const until = now() + (Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs + RUN_GRACE_MS : RUN_TTL_MS);
      const known = runs.get(runId);
      if (known) {
        known.until = until;
        return settle(known.root);
      }
      const root = guardedRoot(workspaceRoot);
      if (!root) return undefined;
      if (!working(root)) observe(root);
      runs.set(runId, {root, names: names === true, until});
      return undefined;
    },
    // Whether ODS puts the example back for this run if it changes.
    guarding(runId) {
      const run = runs.get(runId);
      return Boolean(run && !run.names && baselines.has(run.root));
    },
    // After each tool call, and at finalize.
    check(runId) {
      expire();
      const run = runs.get(runId);
      return run ? settle(run.root) : undefined;
    },
    // The owner changed a Playground project outside any run (Portal's Put
    // back files). When that is the example while a run that does not name
    // it works, the folder as it is now becomes the known-good tree, so that
    // run keeps the owner's change. A run that names it takes the folder as
    // it is at its end, and with no run working the next run starts from it.
    ownerChanged(workspaceRoot, project) {
      if (project !== EXAMPLE) return;
      expire();
      const root = guardedRoot(workspaceRoot);
      if (root && working(root) && !namedWorking(root)) observe(root);
    },
    // The run ended. One that names the example leaves the folder as it is
    // now as the known-good tree.
    end(runId) {
      expire();
      const run = runs.get(runId);
      if (!run) return undefined;
      runs.delete(runId);
      if (!run.names) return settle(run.root);
      observe(run.root);
      return undefined;
    },
  };
}

export const exampleGuardTesting = Object.freeze({takeSnapshot, readFile, MAX_ENTRIES, RUN_GRACE_MS});

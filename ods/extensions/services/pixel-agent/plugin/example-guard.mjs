// Elf of Doom, the finished example game ODS ships (example-projects.mjs), is
// the owner's game. Portal changes it only on a turn whose own owner message
// names it, for example "In elf-of-doom, make the arrows give 7". On any other
// turn this guard copies Playground/elf-of-doom when the run starts and puts
// it back exactly after every tool call, and at the end of the run, that left
// it different. It reads no commands: only the outcome counts. Only the copy
// ODS added is guarded (its record decides); an owner's own folder of that
// name never is. It keeps the owner's game safe from mistakes; it is not a
// sandbox.
import {randomBytes} from 'node:crypto';
import * as fs from 'node:fs';
import path from 'node:path';
import {addedExampleProjects} from './example-projects.mjs';
import {requestsNewPlaygroundProject} from './playground-projects.mjs';

export const EXAMPLE_FOLDER = 'Playground/elf-of-doom';
const EXAMPLE = 'elf-of-doom';
const MAX_ENTRIES = 512;
const MAX_BYTES = 16 * 1024 * 1024;
// Far past the 30-minute run deadline: a run this old ended unseen.
const RUN_TTL_MS = 2 * 60 * 60 * 1000;

// What the model reads on the result of the call after which ODS acted, and
// the one line the owner reads in that turn's reply.
export const EXAMPLE_NOTES = Object.freeze({
  restored: Object.freeze({
    model: '[ODS Portal] ODS put Playground/elf-of-doom back the way it was, because the owner\'s message does not ask to change Elf of Doom. '
      + 'Make new games in a new Playground folder. To change Elf of Doom, the owner names it, for example: '
      + '"In elf-of-doom, make the arrows faster". Do not tell the owner that Elf of Doom changed.',
    owner: 'ODS put Playground/elf-of-doom back the way it was: your message did not ask to change Elf of Doom. '
      + 'To change it, name it, for example "In elf-of-doom, make the arrows faster".',
  }),
  failed: Object.freeze({
    model: '[ODS Portal] ODS could not put Playground/elf-of-doom back the way it was after this call changed it, although the owner\'s '
      + 'message does not ask to change Elf of Doom. Do not change it further, and tell the owner it may have changed.',
    owner: 'ODS could not put Playground/elf-of-doom back the way it was after this task changed it. Close any program that has its '
      + 'files open and check the game; if it still looks wrong, [get help on Discord](https://discord.gg/4ntNp9MAwC).',
  }),
});

// ------------------------------------------------------------------ naming --

const NAME = String.raw`elf[\s_-]*of[\s_-]*doom`;
const MENTION = new RegExp(String.raw`\b${NAME}\b`, 'i');
// The documented way to ask: "In elf-of-doom, ..." or "elf-of-doom: ...".
const EXPLICIT = new RegExp(String.raw`^(?:in\s+${NAME}\s*,|${NAME}\s*:)`, 'i');
// Any of these in a sentence that names the example means that sentence does
// not ask to change it: it keeps, compares, excludes or copies it, or asks for
// something new.
const PROTECTIVE = new RegExp([
  String.raw`\b(?:keep(?:s|ing)?|kept|leav(?:e|es|ing)|preserv\w*|protect\w*|spare|untouched|alone|as[\s-]is)\b`,
  String.raw`\b(?:don['’]?t|do\s+not|never)\s+(?:touch|change|modify|edit|delete|remove)`,
  String.raw`\b(?:except|besides|other\s+than|apart\s+from|rest\s+of|the\s+others|other\s+(?:games?|projects?|folders?))\b`,
  String.raw`\b(?:new\s+(?:games?|projects?|folders?|versions?)|another\s+(?:game|project))\b`,
  String.raw`\b(?:(?:un)?like|similar|inspired\s+by|based\s+on|sequels?|remakes?|clones?|copy\s+of|version\s+of)\b`,
  String.raw`\b(?:from|out\s+of)\s+(?:(?:the|my)\s+)?(?:[\w./-]*\/)?${NAME}\b`,
  String.raw`\b${NAME}['’]s\b`,
].join('|'), 'i');
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

// Whether a turn's own owner message names Elf of Doom as something to
// change: it starts "In elf-of-doom," or "elf-of-doom:", or a sentence that
// names it has no protective word. One protective word anywhere in that
// sentence is enough, so "Keep elf-of-doom, delete the rest" never names it.
// A new-game request goes to a new folder, so it names the example only in
// the explicit form ("Make me an elf of doom game" does not).
export function namesExample(message) {
  const words = ownerWords(message);
  if (EXPLICIT.test(words)) return true;
  return !requestsNewPlaygroundProject(words)
    && words.split(/[!?;\n]+|\.(?=\s|$)/).some(sentence => MENTION.test(sentence) && !PROTECTIVE.test(sentence));
}

// -------------------------------------------------------- the folder copy --

const filesystemError = error => typeof error?.code === 'string' && Boolean(error.syscall);
const modeOf = stat => stat.mode & 0o7777;
const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());
const sameFile = (a, b) => a.size === b.size && a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs && a.ino === b.ino && a.dev === b.dev;
const entryPath = (folder, key) => key ? path.join(folder, ...key.split('/')) : folder;

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// One regular file's bytes, read without following a link put in its place.
// Null when it is no longer the file stat describes.
function readFile(file, stat) {
  let fd;
  try { fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0)); }
  catch (error) { if (['ENOENT', 'ENOTDIR', 'ELOOP'].includes(error.code)) return null; throw error; }
  try {
    const opened = fs.fstatSync(fd);
    return opened.isFile() && opened.ino === stat.ino && opened.dev === stat.dev ? fs.readFileSync(fd) : null;
  } finally { fs.closeSync(fd); }
}

// Every directory (with its sorted entry names) and regular file (with its
// bytes) of the folder, parents first. Null when the folder is absent;
// {skipped} when ODS cannot copy it back exactly: a link or special file, or
// more than MAX_ENTRIES entries or MAX_BYTES.
function takeSnapshot(folder) {
  if (!lstatOrNull(folder)) return null;
  const entries = new Map(), pending = [''];
  let bytes = 0;
  while (pending.length) {
    const key = pending.shift(), target = entryPath(folder, key), stat = fs.lstatSync(target);
    if (entries.size >= MAX_ENTRIES) return {skipped: `it holds more than ${MAX_ENTRIES} files and folders`};
    if (realDirectory(stat)) {
      const children = fs.readdirSync(target).sort();
      entries.set(key, {mode: modeOf(stat), children});
      pending.push(...children.map(name => key ? `${key}/${name}` : name));
      continue;
    }
    if (!stat.isFile()) return {skipped: 'it holds a link or special file'};
    const content = readFile(target, stat);
    if (!content) return {skipped: 'it changed while ODS copied it'};
    bytes += content.length;
    if (bytes > MAX_BYTES) return {skipped: 'it holds more than 16 MiB'};
    entries.set(key, {mode: modeOf(stat), bytes: content, stat});
  }
  return {entries};
}

// Whether the folder still matches the copy. A file with the same size,
// times and identity counts as unchanged; any doubt compares its bytes.
function matches(folder, snapshot) {
  if (!realDirectory(lstatOrNull(path.dirname(folder)))) return false;
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key), stat = lstatOrNull(target);
    if (!stat || modeOf(stat) !== entry.mode) return false;
    if (entry.children) {
      if (!realDirectory(stat) || fs.readdirSync(target).sort().join('/') !== entry.children.join('/')) return false;
      continue;
    }
    if (!stat.isFile()) return false;
    if (sameFile(stat, entry.stat)) continue;
    if (!readFile(target, stat)?.equals(entry.bytes)) return false;
    entry.stat = stat;
  }
  return true;
}

// A file written beside its target and renamed over it. rename replaces the
// entry itself, never what a link there points to; a link or folder in the
// way is removed first.
function writeFile(target, entry, stat) {
  const temporary = path.join(path.dirname(target), `.ods-restore-${randomBytes(8).toString('hex')}`);
  try {
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, entry.bytes); fs.fchmodSync(fd, entry.mode); } finally { fs.closeSync(fd); }
    if (stat && !stat.isFile()) fs.rmSync(target, {recursive: true, force: true});
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, {force: true}); }
}

// Puts the folder back exactly as the copy has it. Only the folder (and a
// missing Playground above it) is written: directories are recreated, every
// file is written atomically with its mode, and anything the copy does not
// have is removed. Removing a link removes the link, never its target.
function restore(folder, snapshot) {
  if (!realDirectory(lstatOrNull(path.dirname(folder)))) fs.mkdirSync(path.dirname(folder), {mode: 0o700});
  for (const [key, entry] of snapshot.entries) {
    const target = entryPath(folder, key);
    let stat = lstatOrNull(target);
    if (!entry.children) {
      const same = stat?.isFile() && modeOf(stat) === entry.mode && (sameFile(stat, entry.stat) || readFile(target, stat)?.equals(entry.bytes));
      if (!same) writeFile(target, entry, stat);
      continue;
    }
    if (stat && !realDirectory(stat)) { fs.rmSync(target, {recursive: true, force: true}); stat = null; }
    if (!stat) fs.mkdirSync(target, {mode: entry.mode});
    for (const name of fs.readdirSync(target)) {
      if (!entry.children.includes(name)) fs.rmSync(path.join(target, name), {recursive: true, force: true});
    }
    if (modeOf(fs.lstatSync(target)) !== entry.mode) fs.chmodSync(target, entry.mode);
  }
}

// ----------------------------------------------------------------- runs --

// One guard per gateway. Each run registers with whether its owner turn names
// the example. While any active run in a workspace names it, nothing there is
// restored; when the last such run ends, the other active runs copy the folder
// again, so its changes stick. check() and end() return 'restored', 'failed'
// or undefined.
export function createExampleGuard({warn = () => {}, now = Date.now} = {}) {
  const spaces = new Map(), runRoots = new Map(), logged = new Set();
  const once = (key, text) => { if (!logged.has(key)) { logged.add(key); warn(text); } };
  const folderIn = root => path.join(root, ...EXAMPLE_FOLDER.split('/'));
  const naming = space => [...space.runs.values()].some(run => run.names);

  // The canonical workspace root, when its example is the copy ODS added.
  function guardedRoot(workspaceRoot) {
    if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return null;
    try {
      const root = fs.realpathSync(workspaceRoot);
      return addedExampleProjects(root).includes(EXAMPLE) ? root : null;
    } catch (error) {
      if (!filesystemError(error)) throw error;
      once(workspaceRoot, `Portal is not guarding ${EXAMPLE_FOLDER} in ${workspaceRoot}: its workspace or record could not be read (${error.code}).`);
      return null;
    }
  }
  function copy(root, space) {
    space.snapshot = undefined;
    if (!space.runs.size || naming(space)) return;
    let taken;
    try { taken = takeSnapshot(folderIn(root)); }
    catch (error) { if (!filesystemError(error)) throw error; taken = {skipped: `it could not be read (${error.code})`}; }
    if (taken?.skipped) once(root, `Portal is not guarding ${EXAMPLE_FOLDER} in ${root}: ${taken.skipped}, so it stays as the owner has it.`);
    if (taken?.entries) space.snapshot = taken;
  }
  function forget(runId) {
    const root = runRoots.get(runId), space = spaces.get(root);
    runRoots.delete(runId);
    if (!space) return;
    const named = space.runs.get(runId)?.names;
    space.runs.delete(runId);
    if (!space.runs.size) spaces.delete(root);
    else if (named) copy(root, space);
  }
  function check(runId) {
    const root = runRoots.get(runId), space = spaces.get(root);
    if (!space?.snapshot || naming(space)) return undefined;
    const folder = folderIn(root);
    try {
      if (matches(folder, space.snapshot)) return undefined;
      restore(folder, space.snapshot);
      return 'restored';
    } catch (error) {
      if (!filesystemError(error)) throw error;
      warn(`Portal could not put ${EXAMPLE_FOLDER} back in ${root}: ${error.code} ${error.message}`);
      return 'failed';
    }
  }
  return {
    // A run starts. A later attempt of the same run keeps its registration.
    begin(runId, workspaceRoot, names) {
      if (typeof runId !== 'string' || !runId || runRoots.has(runId)) return;
      for (const [id, root] of runRoots) if (now() - spaces.get(root).runs.get(id).at > RUN_TTL_MS) forget(id);
      const root = guardedRoot(workspaceRoot);
      if (!root) return;
      const space = spaces.get(root) ?? {runs: new Map(), snapshot: undefined};
      spaces.set(root, space);
      space.runs.set(runId, {names: names === true, at: now()});
      runRoots.set(runId, root);
      if (!space.snapshot) copy(root, space);
    },
    check,
    end(runId) {
      const outcome = check(runId);
      forget(runId);
      return outcome;
    },
  };
}

export const exampleGuardTesting = Object.freeze({takeSnapshot, matches, restore, MAX_ENTRIES, MAX_BYTES, RUN_TTL_MS});

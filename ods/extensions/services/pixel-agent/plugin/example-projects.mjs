// ODS ships one finished example project, Elf of Doom, a game Portal wrote in
// ODS from natural-language prompts. The installers add it to the owner's
// workspace at Playground/elf-of-doom whenever Playground/elf-of-doom is
// absent and this workspace has never received it
// (installers/lib/pixel-example-projects.py), and record that ODS added it in
// a private file in Portal's state folder, which the agent's sandbox never
// mounts (.ods-example-projects/<sha256 of the workspace path>.json). After
// that it is ordinary owner content. A folder of that name the owner already
// had is never the shipped example. This module lets Portal's sidebar list
// the example like any other project and open it: it reports which shipped
// examples ODS added and are still present, and publishes one through the
// same create-only preview service the model tool uses, with no model run. It
// never writes, copies, renames or deletes workspace files, and ODS never
// supplies starter bytes for new work.
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {PREVIEW_FOLDER_FAILURES} from './workspace-preview.mjs';

export const SHIPPED_EXAMPLE_PROJECTS = Object.freeze(['elf-of-doom']);
// The installers' records of the examples each workspace received, in
// Portal's (OpenClaw's) state folder.
export const EXAMPLE_RECORD_DIRECTORY = '.ods-example-projects';

const ROUTE = '/pixel-ods/example-projects';
const MAX_BODY = 256;
const MAX_RECORD_BYTES = 4096;

// The record folder the gateway reads, unless a caller names another.
export function exampleRecordDirectory() {
  return path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), EXAMPLE_RECORD_DIRECTORY);
}

// The record file for one canonical workspace path, as the installers name it.
export function exampleRecordFile(root, directory = exampleRecordDirectory()) {
  return path.join(directory, `${createHash('sha256').update(root).digest('hex')}.json`);
}

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// A missing, unreadable or looping path: a filesystem answer, never a crash.
// Anything else is a defect.
export const filesystemError = error => typeof error?.code === 'string' && Boolean(error.syscall);

// The owner's own entry: never a link, and on POSIX this user's (a folder
// also private to them).
function ownerEntry(entry, folder = false) {
  if (!entry || entry.isSymbolicLink() || !(folder ? entry.isDirectory() : entry.isFile())) return false;
  if (process.platform === 'win32') return true;
  return entry.uid === process.getuid() && (!folder || (entry.mode & 0o077) === 0);
}

function validRecord(value, root) {
  const names = list => Array.isArray(list) && list.every(name => typeof name === 'string');
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join() === 'added,received,schemaVersion,workspace' && value.schemaVersion === 1
    && value.workspace === root
    && names(value.received) && names(value.added) && value.added.every(name => value.received.includes(name));
}

// The shipped examples ODS itself added to the workspace at root (a canonical
// directory), as its record lists them. A folder the owner already had is
// recorded only as received, so it stays ordinary owner content, and so does
// every folder while the record is missing, a link, not the owner's one
// regular file in their private record folder, for another workspace or
// malformed. A record that exists but cannot be read raises its filesystem
// error, for the caller to decide.
export function addedExampleProjects(root, {recordDirectory = exampleRecordDirectory()} = {}) {
  if (!ownerEntry(lstatOrNull(recordDirectory), true)) return [];
  const file = exampleRecordFile(root, recordDirectory);
  const entry = lstatOrNull(file);
  if (!ownerEntry(entry) || entry.nlink !== 1 || entry.size > MAX_RECORD_BYTES) return [];
  let descriptor;
  try { descriptor = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0)); }
  catch (error) { if (['ENOENT', 'ELOOP'].includes(error.code)) return []; throw error; }
  try {
    const opened = fs.fstatSync(descriptor);
    if (!opened.isFile() || opened.ino !== entry.ino || opened.dev !== entry.dev) return [];
    const bytes = Buffer.alloc(MAX_RECORD_BYTES + 1);
    const length = fs.readSync(descriptor, bytes, 0, bytes.length, 0);
    if (length > MAX_RECORD_BYTES) return [];
    let value;
    try { value = JSON.parse(bytes.subarray(0, length).toString('utf8')); }
    catch (error) { if (error instanceof SyntaxError) return []; throw error; }
    return validRecord(value, root) ? SHIPPED_EXAMPLE_PROJECTS.filter(name => value.added.includes(name)) : [];
  } finally { fs.closeSync(descriptor); }
}

// The canonical workspace root, or null for one that is unusable. The
// configured root itself is trusted and may use a platform alias such as
// macOS /var -> /private/var; nothing below it may be a link.
function canonicalRoot(workspaceRoot) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return null;
  const root = fs.realpathSync(path.resolve(workspaceRoot));
  return root === path.parse(root).root || !fs.statSync(root).isDirectory() ? null : root;
}

// Whether one shipped example can open: 'present' (ODS added it and its
// folder holds a regular index.html), 'missing' (Playground/<name> is gone:
// deleted, renamed or never there) or 'not-playable' (a folder of that name is
// there, but it is a link, has no index.html, or the record does not say ODS
// added it). Nothing is created, and a workspace the gateway cannot read has
// no example.
export function exampleProjectState(workspaceRoot, name, options = {}) {
  // A filesystem error means the workspace has no example, and a folder
  // whose record cannot be read is not playable as one.
  let root;
  try {
    root = canonicalRoot(workspaceRoot);
    if (!root || !SHIPPED_EXAMPLE_PROJECTS.includes(name)) return 'missing';
    const playground = lstatOrNull(path.join(root, 'Playground'));
    if (!playground?.isDirectory() || playground.isSymbolicLink()) return 'missing';
    const folder = lstatOrNull(path.join(root, 'Playground', name));
    if (!folder) return 'missing';
    if (!folder.isDirectory() || folder.isSymbolicLink()) return 'not-playable';
    const entry = lstatOrNull(path.join(root, 'Playground', name, 'index.html'));
    if (!entry?.isFile() || entry.isSymbolicLink()) return 'not-playable';
  } catch (error) {
    if (filesystemError(error)) return 'missing';
    throw error;
  }
  try {
    return addedExampleProjects(root, options).includes(name) ? 'present' : 'not-playable';
  } catch (error) {
    if (filesystemError(error)) return 'not-playable';
    throw error;
  }
}

// The shipped examples ODS added whose folder is in the workspace now: a real
// Playground directory holding a real <name> directory with a regular
// index.html. A deleted, renamed, linked or emptied folder is simply absent,
// and an owner's own folder of that name is not the example.
export function presentExampleProjects(workspaceRoot, options = {}) {
  return SHIPPED_EXAMPLE_PROJECTS.filter(name => exampleProjectState(workspaceRoot, name, options) === 'present');
}

// The dashboard's receipt fields: the same verified preview a model run
// delivers, without the host's internal boundary and readback fields.
function dashboardPreview(receipt) {
  return {
    schemaVersion: 1,
    kind: 'ods-pixel-workspace-preview',
    relativeDirectory: receipt.relativeDirectory,
    siteId: receipt.siteId,
    port: receipt.port,
    url: receipt.url,
    files: receipt.files,
    bytes: receipt.bytes,
    sha256: receipt.sha256,
    entrySha256: receipt.entrySha256,
  };
}

async function readName(req) {
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
  try {
    body = JSON.parse(Buffer.concat(chunks, total).toString('utf8'));
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  }
  if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).join() !== 'name'
      || !SHIPPED_EXAMPLE_PROJECTS.includes(body.name)) return null;
  return body.name;
}

// GET lists the present examples. POST {"name"} publishes that one present
// example and returns its verified receipt. Only shipped example names are
// accepted, so this route cannot publish any other owner folder.
export function createExampleProjectRoute({workspaceRoot, publish, sendJson}) {
  return async (req, res) => {
    if (req.url !== ROUTE) { sendJson(res, 400, {error: 'invalid request'}); return true; }
    if (req.method === 'GET') {
      sendJson(res, 200, {schemaVersion: 1, projects: presentExampleProjects(workspaceRoot())});
      return true;
    }
    if (req.method !== 'POST') { sendJson(res, 405, {error: 'method not allowed'}); return true; }
    const name = await readName(req);
    if (!name) { sendJson(res, 400, {error: 'invalid example project request'}); return true; }
    // 404: the folder is gone. 422: a folder of that name is there but cannot
    // open as the example (no index.html, or not the copy ODS added).
    const state = exampleProjectState(workspaceRoot(), name);
    if (state !== 'present') {
      sendJson(res, state === 'missing' ? 404 : 422,
        {error: state === 'missing' ? 'example-project-unavailable' : 'example-project-not-playable'});
      return true;
    }
    try {
      const receipt = await publish(`Playground/${name}`);
      sendJson(res, 200, {schemaVersion: 1, preview: dashboardPreview(receipt)});
    } catch (error) {
      // 409: the host refused files in the folder, which the owner can fix
      // there. Anything else is an unavailable preview service.
      if (PREVIEW_FOLDER_FAILURES.includes(error?.code)) sendJson(res, 409, {error: 'example-project-not-previewable'});
      else sendJson(res, 503, {error: 'example-project-preview-unavailable'});
    }
    return true;
  };
}

// ODS ships one finished example project, Elf of Doom, a game Portal wrote in
// ODS from natural-language prompts. The installers add it to the owner's
// workspace at Playground/elf-of-doom whenever Playground/elf-of-doom is
// absent and this workspace has never received it
// (installers/lib/pixel-example-projects.py), and record in the workspace's
// hidden .ods-examples.json that ODS added it. After that it is ordinary owner
// content. A folder of that name the owner already had is never the shipped
// example. This module lets Portal's sidebar list the example like any other
// project and open it: it reports which shipped examples ODS added and are
// still present, and publishes one through the same create-only preview
// service the model tool uses, with no model run. It never writes, copies,
// renames or deletes workspace files, and ODS never supplies starter bytes for
// new work.
import * as fs from 'node:fs';
import path from 'node:path';
import {PREVIEW_FOLDER_FAILURES} from './workspace-preview.mjs';

export const SHIPPED_EXAMPLE_PROJECTS = Object.freeze(['elf-of-doom']);
// The installers' record of the examples a workspace received, at its root.
export const EXAMPLE_RECORD = '.ods-examples.json';

const ROUTE = '/pixel-ods/example-projects';
const MAX_BODY = 256;
const MAX_RECORD_BYTES = 4096;

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

function validRecord(value) {
  const names = list => Array.isArray(list) && list.every(name => typeof name === 'string');
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join() === 'added,received,schemaVersion' && value.schemaVersion === 1
    && names(value.received) && names(value.added) && value.added.every(name => value.received.includes(name));
}

// The shipped examples ODS itself added to the workspace at root (a canonical
// directory), as its record lists them. A folder the owner already had is
// recorded only as received, so it stays ordinary owner content, and so does
// every folder while the record is missing, a link, not one regular file or
// malformed. A record that exists but cannot be read raises its filesystem
// error, for the caller to decide.
export function addedExampleProjects(root) {
  const file = path.join(root, EXAMPLE_RECORD);
  const entry = lstatOrNull(file);
  if (!entry?.isFile() || entry.isSymbolicLink() || entry.nlink !== 1 || entry.size > MAX_RECORD_BYTES) return [];
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
    return validRecord(value) ? SHIPPED_EXAMPLE_PROJECTS.filter(name => value.added.includes(name)) : [];
  } finally { fs.closeSync(descriptor); }
}

// The shipped examples ODS added whose folder is in the workspace now: a real
// Playground directory holding a real <name> directory with a regular
// index.html. A deleted, renamed, linked or emptied folder is simply absent,
// and an owner's own folder of that name is not the example. Nothing is
// created, and a workspace the gateway cannot read lists nothing.
export function presentExampleProjects(workspaceRoot) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return [];
  try {
    // The configured root itself is trusted and may use a platform alias such
    // as macOS /var -> /private/var; nothing below it may be a link.
    const root = fs.realpathSync(path.resolve(workspaceRoot));
    if (root === path.parse(root).root || !fs.statSync(root).isDirectory()) return [];
    const playground = lstatOrNull(path.join(root, 'Playground'));
    if (!playground?.isDirectory() || playground.isSymbolicLink()) return [];
    const added = addedExampleProjects(root);
    return SHIPPED_EXAMPLE_PROJECTS.filter(name => {
      if (!added.includes(name)) return false;
      const folder = lstatOrNull(path.join(root, 'Playground', name));
      if (!folder?.isDirectory() || folder.isSymbolicLink()) return false;
      const entry = lstatOrNull(path.join(root, 'Playground', name, 'index.html'));
      return Boolean(entry?.isFile() && !entry.isSymbolicLink());
    });
  } catch (error) {
    // A missing, unreadable or looping workspace path is a filesystem answer:
    // no example is present. Anything else is a defect and propagates.
    if (typeof error?.code === 'string' && error.syscall) return [];
    throw error;
  }
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
    if (!presentExampleProjects(workspaceRoot()).includes(name)) {
      sendJson(res, 404, {error: 'example-project-unavailable'});
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

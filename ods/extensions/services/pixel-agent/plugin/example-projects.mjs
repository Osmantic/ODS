// ODS ships one finished example project, Elf of Doom, a game Portal wrote in
// ODS from natural-language prompts. The installers add it to the owner's
// workspace at Playground/elf-of-doom whenever that name is absent
// (installers/lib/pixel-example-projects.py). After that it is ordinary owner
// content. This module lets Portal's sidebar list it like any other project
// and open it: it reports which shipped examples are present and publishes one
// through the same create-only preview service the model tool uses, with no
// model run. It never writes, copies, renames or deletes workspace files, and
// ODS never supplies starter bytes for new work.
import * as fs from 'node:fs';
import path from 'node:path';
import {PREVIEW_FOLDER_FAILURES} from './workspace-preview.mjs';

export const SHIPPED_EXAMPLE_PROJECTS = Object.freeze(['elf-of-doom']);

const ROUTE = '/pixel-ods/example-projects';
const MAX_BODY = 256;

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// The shipped examples whose folder is in the workspace now: a real Playground
// directory holding a real <name> directory with a regular index.html. A
// deleted, renamed, linked or emptied folder is simply absent. Nothing is
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
    return SHIPPED_EXAMPLE_PROJECTS.filter(name => {
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

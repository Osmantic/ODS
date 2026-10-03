// Diagnostic observations, not a release attestation. Node has already resolved
// static imports when this runs: these hashes describe initialization-time
// files, not the bytes evaluated by the module loader or a measured boot.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';

const MAX_FILES = 512;
const MAX_BYTES = 16 * 1024 * 1024;
const digest = value => createHash('sha256').update(value).digest('hex');

function fileBytes(filename, budget) {
  const before = fs.lstatSync(filename);
  if (!before.isFile() || before.isSymbolicLink() || before.size > budget.bytes) throw new Error('unavailable');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino || opened.size > budget.bytes) throw new Error('unavailable');
    // Read at most the approved size; a growing file cannot allocate unbounded memory.
    const content = Buffer.alloc(opened.size);
    let count = 0;
    while (count < content.length) {
      const read = fs.readSync(fd, content, count, content.length - count, count);
      if (!read) throw new Error('unavailable');
      count += read;
    }
    const after = fs.fstatSync(fd), current = fs.lstatSync(filename);
    if (!current.isFile() || current.isSymbolicLink() || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs
        || after.ctimeMs !== opened.ctimeMs || current.dev !== opened.dev || current.ino !== opened.ino) throw new Error('unavailable');
    budget.bytes -= content.length;
    return content;
  } finally { fs.closeSync(fd); }
}

// Matches Pixel's extension-hash framing. Reject rather than omit unknown files.
export function pluginTreeDigest(root) {
  const hash = createHash('sha256'), budget = {bytes: MAX_BYTES};
  let count = 0;
  function walk(directory, relative = '') {
    const info = fs.lstatSync(directory);
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('unavailable');
    for (const entry of fs.readdirSync(directory, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      if (++count > MAX_FILES) throw new Error('unavailable');
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      const filename = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('unavailable');
      if (entry.isDirectory()) walk(filename, name);
      else {
        const content = fileBytes(filename, budget);
        hash.update(name); hash.update('\0'); hash.update(String(content.length)); hash.update('\0'); hash.update(content); hash.update('\0');
      }
    }
  }
  walk(root);
  return hash.digest('hex');
}

function observe(pluginRoot, modulePath) {
  let pluginSha256 = null, openclawModuleSha256 = null;
  try { pluginSha256 = pluginTreeDigest(pluginRoot); } catch { /* unknown, never log private filenames */ }
  try { openclawModuleSha256 = digest(fileBytes(modulePath, {bytes: MAX_BYTES})); } catch { /* unknown */ }
  return {pluginSha256, openclawModuleSha256};
}

export function createRuntimeIdentity({pluginRoot, modulePath, openclawVersion, now = () => new Date()}) {
  const initial = observe(pluginRoot, modulePath);
  const schemas = new Map();
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  // Never replace initialization observations with a later disk snapshot. A
  // hot-swapped tree may belong to a newer release than this running process.
  const read = () => {
    const current = observe(pluginRoot, modulePath);
    const changed = Object.keys(initial).some(key => initial[key] !== null && current[key] !== null && current[key] !== initial[key]);
    const available = initial.pluginSha256 !== null || initial.openclawModuleSha256 !== null;
    return {
      schemaVersion: 1,
      state: changed ? 'mismatch' : available ? 'partial' : 'unavailable',
      diskComparison: changed ? 'mismatch' : Object.keys(initial).every(key => initial[key] !== null && current[key] !== null) ? 'match' : 'unavailable',
      runtimeMatchesRelease: changed ? false : null,
      reasonCode: changed ? 'runtime-files-changed' : available ? 'release-binding-unavailable' : 'runtime-identity-unavailable',
      observedAt: now().toISOString(),
      boundary: 'initialization-files-not-evaluated-code-or-release-proof',
      identities: {
        odsReleaseCommit: null, pixelSourceRevision: null, ...initial,
        openclawVersion: typeof openclawVersion === 'string' && /^[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?$/.test(openclawVersion) ? openclawVersion : null,
        previewImageDigest: null,
      },
      toolSchemas: {
        boundary: 'latest-created-plugin-tools-not-offered-surface',
        registeredPluginToolCount: schemas.size,
        registeredPluginToolSchemasSha256: schemas.size ? digest(JSON.stringify([...schemas].sort(([a], [b]) => a.localeCompare(b)))) : null,
        offeredToolCount: null, offeredToolSchemasSha256: null,
      },
    };
  };
  read.observeTool = tool => {
    try {
      if (!tool || typeof tool.name !== 'string' || !/^pixel_ods_[a-z0-9_]{1,64}$/.test(tool.name)) return;
      schemas.delete(tool.name);
      if (!tool.parameters || typeof tool.parameters !== 'object') return;
      const encoded = JSON.stringify(canonical(tool.parameters));
      if (encoded.length <= 256 * 1024 && schemas.size < 64) schemas.set(tool.name, digest(encoded));
    } catch { /* diagnostics must not affect creation of a tool */ }
    return tool;
  };
  return read;
}

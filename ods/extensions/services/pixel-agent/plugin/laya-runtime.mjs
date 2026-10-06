// The managed extension setup writes this private connection record. Its
// Compose marker is checked again at use time, including cached descriptors.
import {constants, openSync, closeSync, fstatSync, readFileSync, lstatSync, realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import {join, dirname, basename, isAbsolute, parse} from 'node:path';
import {createLayaClient} from './laya-client.mjs';
import {createLayaTool} from './laya-tool.mjs';

export const LAYA_CONNECTION_FILE = join(homedir(), '.config', 'ods', 'laya-portal.json');
const FLAGS = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0);
const TOKEN = /^[A-Za-z0-9_-]{32,256}$/;
const HASH = /^[a-f0-9]{64}$/;

export function trustedLayaParent(path, uid, sharedDataDirectory) {
  let parent = dirname(path);
  const parents = [];
  for (;;) {
    const info = lstatSync(parent);
    // The installer's data directory is shared with containers. This exception
    // applies only to the public Compose marker, never credentials or config.
    const modeMask = parent === sharedDataDirectory && info.uid === uid ? 0o002 : 0o022;
    if (!info.isDirectory() || info.isSymbolicLink() || ![0, uid].includes(info.uid) || (info.mode & modeMask)) {
      throw new Error('Unsafe Laya connection parent.');
    }
    parents.push({path: parent, dev: info.dev, ino: info.ino});
    if (parent === parse(parent).root) return parents;
    parent = dirname(parent);
  }
}

export function readOwnedLayaFile(path, uid, maxBytes, privateFile, sharedDataDirectory) {
  const parents = trustedLayaParent(path, uid, privateFile ? undefined : sharedDataDirectory);
  const fd = openSync(path, FLAGS);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.nlink !== 1 || info.uid !== uid
        || (info.mode & (privateFile ? 0o077 : 0o022)) || info.size > maxBytes) {
      throw new Error('Unsafe Laya connection file.');
    }
    const content = readFileSync(fd);
    if (content.length > maxBytes) throw new Error('Oversized Laya connection file.');
    for (const parent of parents) {
      const current = lstatSync(parent.path);
      if (current.dev !== parent.dev || current.ino !== parent.ino) {
        throw new Error('Unsafe Laya connection parent changed.');
      }
    }
    const current = lstatSync(path);
    if (current.dev !== info.dev || current.ino !== info.ino || current.isSymbolicLink()) {
      throw new Error('Unsafe Laya connection file changed.');
    }
    return content;
  } finally { closeSync(fd); }
}

export function validateLayaConnection(value) {
  const keys = ['schemaVersion', 'installRoot', 'port', 'token', 'composeSha256'];
  if (value?.schemaVersion === 2) keys.push('composeFile');
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== keys.length || !keys.every(key => Object.hasOwn(value, key))
      || ![1, 2].includes(value.schemaVersion) || typeof value.installRoot !== 'string'
      || !isAbsolute(value.installRoot) || /[\r\n\0]/.test(value.installRoot)
      || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535
      || typeof value.token !== 'string' || !TOKEN.test(value.token)
      || typeof value.composeSha256 !== 'string' || !HASH.test(value.composeSha256)
      || value.schemaVersion === 2 && (typeof value.composeFile !== 'string'
        || !isAbsolute(value.composeFile) || /[\r\n\0]/.test(value.composeFile)
        || basename(value.composeFile) !== 'compose.yaml'
        || basename(dirname(value.composeFile)) !== 'laya')) {
    throw new Error('Invalid Laya connection record.');
  }
  return value;
}

export function readLayaConnection(path = LAYA_CONNECTION_FILE, {uid = process.getuid?.()} = {}) {
  // The product's Windows route runs this code in WSL. Native Windows ACLs
  // are not POSIX custody and must not be silently treated as equivalent.
  if (!Number.isSafeInteger(uid) || uid < 0) return undefined;
  try {
    const value = validateLayaConnection(JSON.parse(readOwnedLayaFile(path, uid, 4096, true).toString('utf8')));
    if (realpathSync(value.installRoot) !== value.installRoot) return undefined;
    // Version 1 records retain their original binding. New setup records the
    // actual installed recipe, including owner-configured extension locations.
    const marker = value.schemaVersion === 2 ? value.composeFile
      : join(value.installRoot, 'extensions', 'user', 'laya', 'compose.yaml');
    const bytes = readOwnedLayaFile(marker, uid, 512 * 1024, false, join(value.installRoot, 'data'));
    if (createHash('sha256').update(bytes).digest('hex') !== value.composeSha256) return undefined;
    return value;
  } catch (error) {
    // Do not expose parse diagnostics: they can contain the service token.
    if (error instanceof SyntaxError || error instanceof Error && (
      /^(Unsafe|Oversized|Invalid) Laya connection/.test(error.message)
      || ['ENOENT', 'EACCES', 'EPERM', 'ELOOP', 'ENOTDIR'].includes(error.code))) return undefined;
    throw error;
  }
}

export function createLayaRuntime({readConnection = readLayaConnection, clientFactory = createLayaClient} = {}) {
  let binding, client;
  function currentClient() {
    const connection = readConnection();
    if (!connection) { binding = undefined; client = undefined; return undefined; }
    const identity = JSON.stringify(connection);
    if (identity !== binding) {
      client = clientFactory({port: connection.port, token: connection.token});
      binding = identity;
    }
    return client;
  }
  const tool = createLayaTool({resolveClient: currentClient});
  return {
    tool,
    client: currentClient,
    offered() { return currentClient() ? tool : null; },
    promptHint() {
      return currentClient() ? 'Laya is enabled for optional text decisions. Before the first Laya call, read pixel_ods_skill with {"topic":"laya"} for exact examples or discover its complete tool schema. Do not guess argument names. Existing CSV/TSV/JSON/JSONL datasets use pixel_ods_laya_batch with source:{path,textColumn,idColumn}, questions and outputDirectory (workspace-relative paths). Portal handles batching and verified reports; no row copying or report transcription. A few texts already in context use pixel_ods_laya with items and questions instead. Ordinary conversation, direct edits and website creation need no Laya call.' : '';
    },
  };
}

// The managed extension setup writes this private connection record. Its
// Compose marker is checked again at use time, including cached descriptors.
import {constants, openSync, closeSync, fstatSync, readFileSync, lstatSync, realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {homedir} from 'node:os';
import {join, dirname, isAbsolute, parse} from 'node:path';
import {createLayaClient} from './laya-client.mjs';
import {createLayaTool} from './laya-tool.mjs';

export const LAYA_CONNECTION_FILE = join(homedir(), '.config', 'ods', 'laya-portal.json');
const FLAGS = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0);
const TOKEN = /^[A-Za-z0-9_-]{32,256}$/;
const HASH = /^[a-f0-9]{64}$/;

export function trustedLayaParent(path, uid) {
  let parent = dirname(path);
  for (;;) {
    const info = lstatSync(parent);
    if (!info.isDirectory() || info.isSymbolicLink() || ![0, uid].includes(info.uid) || (info.mode & 0o022)) {
      throw new Error('Unsafe Laya connection parent.');
    }
    if (parent === parse(parent).root) return;
    parent = dirname(parent);
  }
}

export function readOwnedLayaFile(path, uid, maxBytes, privateFile) {
  trustedLayaParent(path, uid);
  const fd = openSync(path, FLAGS);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.nlink !== 1 || info.uid !== uid
        || (info.mode & (privateFile ? 0o077 : 0o022)) || info.size > maxBytes) {
      throw new Error('Unsafe Laya connection file.');
    }
    const content = readFileSync(fd);
    if (content.length > maxBytes) throw new Error('Oversized Laya connection file.');
    return content;
  } finally { closeSync(fd); }
}

export function validateLayaConnection(value) {
  const keys = ['schemaVersion', 'installRoot', 'port', 'token', 'composeSha256'];
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== keys.length || !keys.every(key => Object.hasOwn(value, key))
      || value.schemaVersion !== 1 || typeof value.installRoot !== 'string'
      || !isAbsolute(value.installRoot) || /[\r\n\0]/.test(value.installRoot)
      || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535
      || typeof value.token !== 'string' || !TOKEN.test(value.token)
      || typeof value.composeSha256 !== 'string' || !HASH.test(value.composeSha256)) {
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
    const marker = join(value.installRoot, 'extensions', 'user', 'laya', 'compose.yaml');
    const bytes = readOwnedLayaFile(marker, uid, 512 * 1024, false);
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
    offered() { return currentClient() ? tool : null; },
    promptHint() {
      return currentClient() ? 'Laya is enabled as an optional local decision capability. Discover pixel_ods_laya for bounded classification, scores or yes/no probabilities over relevant task evidence. Read the laya guide with pixel_ods_skill when needed. Use the result to continue the task; ordinary conversation and direct edits need no Laya call.' : '';
    },
  };
}

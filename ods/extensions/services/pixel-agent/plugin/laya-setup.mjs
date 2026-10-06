// Host-side extension lifecycle. Called as the ODS owner, never by a model.
// The container reads its dedicated key file; no shared .env is rewritten.
import {mkdirSync, openSync, closeSync, writeFileSync, fsyncSync, renameSync,
  unlinkSync, rmdirSync, lstatSync, realpathSync, fchmodSync, constants} from 'node:fs';
import {join, dirname, isAbsolute} from 'node:path';
import {randomBytes, createHash} from 'node:crypto';
import {LAYA_CONNECTION_FILE, trustedLayaParent, readOwnedLayaFile,
  validateLayaConnection} from './laya-runtime.mjs';

function privateDirectory(path, uid) {
  trustedLayaParent(path, uid);
  try { mkdirSync(path, {mode: 0o700}); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  // Check the directory itself as well as its parents, without following links.
  trustedLayaParent(join(path, 'entry'), uid);
  const info = lstatSync(path);
  if (info.uid !== uid) throw new Error('Laya setup directory must belong to the ODS owner.');
}

function existingPrivate(path, uid, limit) {
  try { return readOwnedLayaFile(path, uid, limit, true); }
  catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
}

function atomicPrivate(path, content, uid, mode = 0o600) {
  // Refuse unsafe existing files instead of silently repairing their custody.
  if (mode === 0o600) existingPrivate(path, uid, 4096);
  const temporary = join(dirname(path), `.laya-${randomBytes(12).toString('hex')}.tmp`);
  const fd = openSync(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL, mode);
  try {
    try { writeFileSync(fd, content); fchmodSync(fd, mode); fsyncSync(fd); }
    finally { closeSync(fd); }
    renameSync(temporary, path);
  }
  catch (error) { unlinkSync(temporary); throw error; }
}

export function configureLayaPortal({installRoot, port = 8017,
  connectionFile = LAYA_CONNECTION_FILE} = {}) {
  const uid = process.getuid?.();
  if (!Number.isSafeInteger(uid) || uid <= 0) {
    throw new Error('Run Laya setup as the ODS owner on Linux, WSL or macOS, without sudo.');
  }
  if (typeof installRoot !== 'string' || !isAbsolute(installRoot)
      || realpathSync(installRoot) !== installRoot) {
    throw new Error('Laya setup requires the canonical ODS installation directory.');
  }
  const marker = join(installRoot, 'extensions', 'user', 'laya', 'compose.yaml');
  const compose = readOwnedLayaFile(marker, uid, 512 * 1024, false);
  // Validate options before creating any files. The placeholder is never saved.
  const base = validateLayaConnection({schemaVersion: 1, installRoot, port,
    token: '0'.repeat(64), composeSha256: createHash('sha256').update(compose).digest('hex')});
  privateDirectory(join(installRoot, 'config'), uid);
  const configDirectory = join(installRoot, 'config', 'laya');
  privateDirectory(configDirectory, uid);
  if (lstatSync(configDirectory).mode & 0o077) {
    throw new Error('Laya key directory must be private to the ODS owner (mode 700).');
  }
  // ~/.config may be absent on a first macOS installation.
  privateDirectory(dirname(dirname(connectionFile)), uid);
  privateDirectory(dirname(connectionFile), uid);
  const lock = join(dirname(connectionFile), '.laya-setup-lock');
  // An interrupted setup leaves a visible lock. Never steal it on a timer.
  mkdirSync(lock, {mode: 0o700});
  try {
    const tokenFile = join(configDirectory, 'api-key');
    let stored;
    try {
      stored = readOwnedLayaFile(tokenFile, uid, 257, false);
      if (![0o444, 0o600].includes(lstatSync(tokenFile).mode & 0o777)) {
        throw new Error('Unsafe Laya connection file.');
      }
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    let token = stored?.toString('utf8').trim();
    if (token !== undefined && !/^[A-Za-z0-9_-]{32,256}$/.test(token)) {
      throw new Error('Invalid existing Laya key; configuration was preserved.');
    }
    if (token === undefined) {
      token = randomBytes(32).toString('hex');
    }
    // The mode-700 parent protects this file on the host. A single-file,
    // read-only container mount must work across rootless UID mappings too.
    // No other part of config/laya or the owner's home is mounted.
    if (!stored || (lstatSync(tokenFile).mode & 0o777) !== 0o444) {
      atomicPrivate(tokenFile, `${token}\n`, uid, 0o444);
    }
    atomicPrivate(connectionFile, `${JSON.stringify({...base, token})}\n`, uid);
    return {state: 'configured', port, tokenFile, connectionFile};
  } finally { rmdirSync(lock); }
}

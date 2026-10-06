import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, realpathSync,
  statSync, chmodSync, renameSync, rmSync, symlinkSync, linkSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {configureLayaPortal} from '../plugin/laya-setup.mjs';
import {readLayaConnection, createLayaRuntime} from '../plugin/laya-runtime.mjs';

const posixOwner = {skip: !(process.getuid?.() > 0) && 'requires a non-root Linux/WSL/macOS owner'};
function fixture(t) {
  // /tmp is deliberately not a trusted credential ancestor. Use an isolated
  // private owner directory, as a real ODS installation does.
  const root = mkdtempSync(join(realpathSync(homedir()), '.ods-laya-test-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const installRoot = join(root, 'ods');
  mkdirSync(join(installRoot, 'data', 'user-extensions', 'laya'), {recursive: true, mode: 0o700});
  const marker = join(installRoot, 'data', 'user-extensions', 'laya', 'compose.yaml');
  writeFileSync(marker, 'services:\n  laya: {}\n', {mode: 0o600});
  const connectionFile = join(root, '.config', 'ods', 'laya-portal.json');
  return {installRoot, connectionFile, marker};
}

test('setup refuses native Windows/root instead of creating unprotected credentials', () => {
  if (process.getuid?.() > 0) return;
  assert.throws(() => configureLayaPortal({installRoot: process.cwd()}), /without sudo/);
});

test('setup is idempotent and activation changes reach an already discovered tool', posixOwner, async t => {
  const options = fixture(t);
  const originalMask = process.umask(0o077);
  let first;
  try { first = configureLayaPortal(options); }
  finally { process.umask(originalMask); }
  const saved = readLayaConnection(options.connectionFile);
  assert.equal(saved.schemaVersion, 2);
  assert.equal(saved.composeFile, options.marker);
  const key = readFileSync(first.tokenFile, 'utf8').trim();
  assert.match(key, /^[a-f0-9]{64}$/);
  assert.equal(saved.token, key);
  assert.equal(statSync(options.connectionFile).mode & 0o777, 0o600);
  assert.equal(statSync(first.tokenFile).mode & 0o777, 0o444);
  assert.equal(statSync(join(first.tokenFile, '..')).mode & 0o777, 0o700);
  const record = readFileSync(options.connectionFile, 'utf8');
  configureLayaPortal(options);
  assert.equal(readFileSync(options.connectionFile, 'utf8'), record);
  assert.equal(readFileSync(first.tokenFile, 'utf8').trim(), key);

  let calls = 0;
  const runtime = createLayaRuntime({readConnection: () => readLayaConnection(options.connectionFile),
    clientFactory: () => ({decide: async () => { calls++; return {items: []}; }})});
  const cached = runtime.offered();
  await cached.execute('active', {});
  renameSync(options.marker, `${options.marker}.disabled`);
  assert.equal(runtime.offered(), null);
  assert.equal((await cached.execute('disabled', {})).details.status, 'disabled');
  renameSync(`${options.marker}.disabled`, options.marker);
  await cached.execute('reenabled', {});
  writeFileSync(options.marker, 'services:\n  laya: {changed: true}\n');
  assert.equal(runtime.offered(), null, 'changed definitions need a new setup binding');
  assert.equal(calls, 2);
});

test('owner-configured extension directory controls activation without a legacy marker', posixOwner, t => {
  const options = fixture(t);
  const directory = join(options.installRoot, '..', 'custom-data', 'laya');
  mkdirSync(directory, {recursive: true, mode: 0o700});
  const composeFile = join(realpathSync(directory), 'compose.yaml');
  renameSync(options.marker, composeFile);
  configureLayaPortal({...options, composeFile});
  const saved = readLayaConnection(options.connectionFile);
  assert.equal(saved.composeFile, composeFile);
  renameSync(composeFile, `${composeFile}.disabled`);
  assert.equal(readLayaConnection(options.connectionFile), undefined);
  renameSync(`${composeFile}.disabled`, composeFile);
  assert.equal(readLayaConnection(options.connectionFile).token, saved.token);
});

test('legacy connection keeps its original marker until setup migrates it', posixOwner, t => {
  const options = fixture(t);
  configureLayaPortal(options);
  const saved = readLayaConnection(options.connectionFile);
  const legacy = join(options.installRoot, 'extensions', 'user', 'laya', 'compose.yaml');
  mkdirSync(join(legacy, '..'), {recursive: true, mode: 0o700});
  writeFileSync(legacy, readFileSync(options.marker), {mode: 0o600});
  const {composeFile, ...record} = saved;
  writeFileSync(options.connectionFile, JSON.stringify({...record, schemaVersion: 1}));
  assert.equal(readLayaConnection(options.connectionFile).token, saved.token);
  renameSync(legacy, `${legacy}.disabled`);
  assert.equal(readLayaConnection(options.connectionFile), undefined);
  configureLayaPortal(options);
  assert.equal(readLayaConnection(options.connectionFile).composeFile, options.marker);
  assert.equal(readLayaConnection(options.connectionFile).token, saved.token);
});

test('configuration refuses invalid ports before writing credentials', posixOwner, t => {
  const options = fixture(t);
  for (const port of [0, 65536, '8017', NaN]) {
    assert.throws(() => configureLayaPortal({...options, port}), /Invalid Laya connection/);
  }
  assert.equal(readLayaConnection(options.connectionFile), undefined);
});

test('unsafe key permissions and hard links are preserved and rejected', posixOwner, t => {
  const options = fixture(t);
  const {tokenFile} = configureLayaPortal(options);
  const original = readFileSync(tokenFile, 'utf8');
  chmodSync(tokenFile, 0o644);
  assert.throws(() => configureLayaPortal(options), /Unsafe Laya connection file/);
  assert.equal(readFileSync(tokenFile, 'utf8'), original);
  chmodSync(tokenFile, 0o600);
  linkSync(tokenFile, `${tokenFile}.linked`);
  assert.throws(() => configureLayaPortal(options), /Unsafe Laya connection file/);
});

test('symlinked connection and Compose files cannot activate Laya', posixOwner, t => {
  const options = fixture(t);
  configureLayaPortal(options);
  renameSync(options.connectionFile, `${options.connectionFile}.original`);
  symlinkSync(`${options.connectionFile}.original`, options.connectionFile);
  assert.equal(readLayaConnection(options.connectionFile), undefined);
  assert.throws(() => configureLayaPortal(options));
  rmSync(options.connectionFile);
  renameSync(`${options.connectionFile}.original`, options.connectionFile);
  renameSync(options.marker, `${options.marker}.original`);
  symlinkSync(`${options.marker}.original`, options.marker);
  assert.equal(readLayaConnection(options.connectionFile), undefined);
  assert.throws(() => configureLayaPortal(options));
});

test('another live setup lock is never stolen', posixOwner, t => {
  const options = fixture(t);
  configureLayaPortal(options);
  const original = readFileSync(options.connectionFile, 'utf8');
  const lock = join(options.connectionFile, '..', '.laya-setup-lock');
  mkdirSync(lock, {mode: 0o700});
  assert.throws(() => configureLayaPortal(options), {code: 'EEXIST'});
  assert.equal(readFileSync(options.connectionFile, 'utf8'), original);
  assert.ok(statSync(lock).isDirectory());
});

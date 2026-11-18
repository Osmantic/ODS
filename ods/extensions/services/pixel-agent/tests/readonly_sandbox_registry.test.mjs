import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, statSync, realpathSync, symlinkSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { createReadonlySandboxRegistry } from '../plugin/readonly-sandbox-registry.mjs';

const require = createRequire(import.meta.url);
let DatabaseSync;
try { ({ DatabaseSync } = require('node:sqlite')); } catch { DatabaseSync = null; }
const skip = process.platform === 'win32' ? 'POSIX SDK host verification' : DatabaseSync ? false : 'node:sqlite unavailable';
const isWin = process.platform === 'win32';

const KEY = 'agent:pixel:ods-readonly:' + 'a'.repeat(32);
const KEY2 = 'agent:pixel:ods-readonly:' + 'b'.repeat(32);

function makeDb(dir, { withTable = true, corrupt = false } = {}) {
  const p = join(dir, 'openclaw.sqlite');
  if (corrupt) { writeFileSync(p, 'not a database'); return p; }
  const db = new DatabaseSync(p);
  if (withTable) {
    db.exec(`CREATE TABLE sandbox_registry_entries (
      registry_kind TEXT NOT NULL,
      container_name TEXT NOT NULL,
      session_key TEXT NOT NULL,
      backend_id TEXT NOT NULL,
      image TEXT NOT NULL,
      created_at_ms INTEGER NOT NULL,
      entry_json TEXT
    )`);
  }
  db.close();
  return p;
}

function insert(p, rows) {
  const db = new DatabaseSync(p);
  const stmt = db.prepare('INSERT INTO sandbox_registry_entries (registry_kind,container_name,session_key,backend_id,image,created_at_ms,entry_json) VALUES (?,?,?,?,?,?,?)');
  for (const r of rows) stmt.run(r.registry_kind, r.container_name, r.session_key, r.backend_id, r.image, r.created_at_ms, '{}');
  db.close();
}

function tmp() { return realpathSync(mkdtempSync(join(tmpdir(), 'rsr-'))); }

function expectUnavailable(fn) {
  assert.throws(fn, (e) => e && e.message === 'readonly-registry-unavailable');
}

test('exact-session filtering returns only matching rows', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    insert(p, [
      { registry_kind: 'container', container_name: 'c1', session_key: KEY, backend_id: 'b1', image: 'img1', created_at_ms: 1 },
      { registry_kind: 'browser', container_name: 'c2', session_key: KEY, backend_id: 'b2', image: 'img2', created_at_ms: 2 },
      { registry_kind: 'container', container_name: 'c3', session_key: KEY2, backend_id: 'b3', image: 'img3', created_at_ms: 3 },
    ]);
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    const rows = reg.readSession(KEY);
    assert.equal(rows.length, 2);
    assert.deepEqual(rows.map(r => r.container_name).sort(), ['c1','c2']);
    for (const r of rows) {
      assert.deepEqual(Object.keys(r).sort(), ['backend_id','container_name','created_at_ms','image','registry_kind','session_key']);
      assert.equal(r.session_key, KEY);
    }
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('valid empty when database and schema healthy', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    assert.deepEqual(reg.readSession(KEY), []);
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('missing database throws unavailable', { skip }, () => {
  const d = tmp();
  try {
    const p = join(d, 'openclaw.sqlite');
    expectUnavailable(() => createReadonlySandboxRegistry({ databasePath: p }));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('corrupt database throws unavailable', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d, { corrupt: true });
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    expectUnavailable(() => reg.readSession(KEY));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('missing table throws unavailable', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d, { withTable: false });
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    expectUnavailable(() => reg.readSession(KEY));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('invalid session key is rejected without querying', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    let opened = 0;
    class Spy extends DatabaseSync { constructor(...a) { super(...a); opened++; } }
    const reg = createReadonlySandboxRegistry({ databasePath: p, DatabaseSync: Spy });
    for (const bad of ['', 'agent:pixel:ods-readonly:XYZ', 'agent:pixel:ods-readonly:' + 'a'.repeat(31), 'agent:pixel:ods-readonly:' + 'A'.repeat(32), null, 42]) {
      expectUnavailable(() => reg.readSession(bad));
    }
    assert.equal(opened, 0);
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('readonly query leaves db and WAL bytes unchanged when idle', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    insert(p, [{ registry_kind: 'container', container_name: 'c1', session_key: KEY, backend_id: 'b1', image: 'img1', created_at_ms: 1 }]);
    const before = readFileSync(p);
    const wal = p + '-wal';
    let walBefore = null;
    try { walBefore = readFileSync(wal); } catch {}
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    reg.readSession(KEY);
    reg.readSession(KEY2);
    assert.deepEqual(readFileSync(p), before);
    if (walBefore) assert.deepEqual(readFileSync(wal), walBefore);
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('symlink database path rejected', { skip: skip || isWin }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    const link = join(d, 'link.sqlite');
    symlinkSync(p, link);
    expectUnavailable(() => createReadonlySandboxRegistry({ databasePath: link }));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('group/other writable file rejected', { skip: skip || isWin }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    chmodSync(p, 0o666);
    expectUnavailable(() => createReadonlySandboxRegistry({ databasePath: p }));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

test('relative path rejected', { skip }, () => {
  expectUnavailable(() => createReadonlySandboxRegistry({ databasePath: 'openclaw.sqlite' }));
});

test('unexpected registry_kind rejected', { skip }, () => {
  const d = tmp();
  try {
    const p = makeDb(d);
    insert(p, [{ registry_kind: 'weird', container_name: 'c1', session_key: KEY, backend_id: 'b1', image: 'img1', created_at_ms: 1 }]);
    const reg = createReadonlySandboxRegistry({ databasePath: p });
    expectUnavailable(() => reg.readSession(KEY));
  } finally { rmSync(d, { recursive: true, force: true }); }
});

import { createRequire } from 'node:module';
import { lstatSync, realpathSync } from 'node:fs';
import {posix} from 'node:path';

const KEY_RE = /^agent:pixel:ods-readonly:[0-9a-f]{32}$/;
const UNAVAILABLE = 'readonly-registry-unavailable';
const COLUMNS = ['registry_kind','container_name','session_key','backend_id','image','created_at_ms'];

function fail() { throw Object.assign(new Error(UNAVAILABLE),{code:UNAVAILABLE}); }

function validatePath(databasePath) {
  if (typeof databasePath !== 'string' || !posix.isAbsolute(databasePath) || posix.normalize(databasePath) !== databasePath || /[\\\x00-\x1f\x7f]/.test(databasePath)) fail();
  let st;
  try { st = lstatSync(databasePath); } catch { fail(); }
  if (!st.isFile() || st.isSymbolicLink()) fail();
  if (st.nlink !== 1) fail();
  if (typeof process.getuid === 'function' && st.uid !== process.getuid()) fail();
  if ((st.mode & 0o022) !== 0) fail();
  let real;
  try { real = realpathSync(databasePath); } catch { fail(); }
  if (real !== databasePath) fail();
  return databasePath;
}

function loadDatabaseSync(injected) {
  if (injected) return injected;
  try {
    const require = createRequire(import.meta.url);
    return require('node:sqlite').DatabaseSync;
  } catch { fail(); }
}

export function createReadonlySandboxRegistry({ databasePath, DatabaseSync } = {}) {
  const validated = validatePath(databasePath);
  const Ctor = loadDatabaseSync(DatabaseSync);
  if (typeof Ctor !== 'function') fail();

  function readSession(sessionKey) {
    if (typeof sessionKey !== 'string' || !KEY_RE.test(sessionKey)) fail();
    let db;
    try {
      validatePath(validated);
      db = new Ctor(validated, { readOnly: true });
      db.exec('PRAGMA busy_timeout=1000');
      db.exec('PRAGMA query_only=ON');
      const stmt = db.prepare(
        'SELECT registry_kind, container_name, session_key, backend_id, image, created_at_ms ' +
        'FROM sandbox_registry_entries WHERE session_key = ?'
      );
      const raw = stmt.all(sessionKey);
      if (!Array.isArray(raw)) fail();
      const rows = [];
      for (const r of raw) {
        if (!r || typeof r !== 'object') fail();
        const out = {};
        for (const c of COLUMNS) {
          if (!(c in r)) fail();
          out[c] = r[c];
        }
        if (out.session_key !== sessionKey) fail();
        if (out.registry_kind !== 'container' && out.registry_kind !== 'browser') fail();
        if (typeof out.container_name !== 'string' || out.container_name.length === 0) fail();
        if (out.backend_id !== null && (typeof out.backend_id !== 'string' || out.backend_id.length === 0)) fail();
        if (typeof out.image !== 'string' || out.image.length === 0) fail();
        if (!Number.isSafeInteger(out.created_at_ms) || out.created_at_ms < 0) fail();
        rows.push(out);
      }
      return rows;
    } catch {
      fail();
    } finally {
      if (db) { try { db.close(); } catch { /* ignore */ } }
    }
  }

  return Object.freeze({ readSession });
}

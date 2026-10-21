// ODS start-up step for the n8n container. n8n-entrypoint.sh runs it before
// n8n starts, as the container user, with n8n's user folder mounted.
//
// 1. n8n migrates its SQLite database when a new version starts, and those
//    migrations cannot be undone. When the installed n8n version differs from
//    the one that last started here, the database is copied aside first.
// 2. n8n's owner account comes from .env (N8N_USER, N8N_PASS) wherever no
//    owner exists yet: new installs, and installs where nobody completed n8n's
//    first-run screen, which until then anything that can reach n8n could
//    claim. An owner someone already created is left alone.
//
// n8n rewrites an env-managed owner at every start and signs everyone out when
// the stored password hash changes, so the hash is generated once and kept
// beside the database. Prints "env" when the owner comes from .env, otherwise
// "existing".
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { pathToFileURL } from "node:url";

export const OWNER_HASH_FILE = ".ods-owner-password.bcrypt";
const OWNER_FROM_ENV_MARKER = ".ods-owner-from-env";
const VERSION_MARKER = ".ods-n8n-version";
const BACKUP_DIR = "ods-backups";
const BACKUPS_KEPT = 2;
// Files are written under this suffix and renamed into place when complete.
const PARTIAL = ".partial";
// The shape n8n accepts for N8N_INSTANCE_OWNER_PASSWORD_HASH.
const BCRYPT_HASH = /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/;

export function packageVersion(packageDir) {
  return JSON.parse(fs.readFileSync(path.join(packageDir, "package.json"), "utf8")).version;
}

// A start-up stopped part-way through a backup leaves *.partial copies, or
// write-ahead files whose database copy never landed. Neither is a backup.
function removeIncompleteCopies(directory) {
  for (const name of fs.readdirSync(directory)) {
    const sidecar = name.match(/^(.*\.sqlite)-(?:wal|shm)$/);
    if (name.endsWith(PARTIAL) || (sidecar && !fs.existsSync(path.join(directory, sidecar[1])))) {
      fs.rmSync(path.join(directory, name), { force: true });
    }
  }
}

function copyIntoPlace(source, target) {
  if (fs.existsSync(target)) {
    throw new Error(`refusing to replace the existing backup ${target}`);
  }
  fs.copyFileSync(source, target + PARTIAL, fs.constants.COPYFILE_EXCL);
  const fd = fs.openSync(target + PARTIAL, "r+");
  try {
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(target + PARTIAL, target);
}

function writeAtomically(file, text) {
  fs.writeFileSync(file + PARTIAL, text);
  fs.renameSync(file + PARTIAL, file);
}

function pruneBackups(directory) {
  const stamps = [...new Set(fs.readdirSync(directory)
    .filter((name) => name.endsWith(".sqlite"))
    .map((name) => name.slice(0, -".sqlite".length)))].sort();
  for (const stamp of stamps.slice(0, -BACKUPS_KEPT)) {
    for (const suffix of [".sqlite", ".sqlite-wal", ".sqlite-shm"]) {
      fs.rmSync(path.join(directory, stamp + suffix), { force: true });
    }
  }
}

/** Copy the database aside before a different n8n version migrates it. */
export function backupBeforeUpgrade(folder, version, now = new Date()) {
  const marker = path.join(folder, VERSION_MARKER);
  const previous = fs.existsSync(marker) ? fs.readFileSync(marker, "utf8").trim() : "";
  if (previous === version) {
    return null;
  }
  const database = path.join(folder, "database.sqlite");
  let saved = null;
  if (fs.existsSync(database)) {
    const directory = path.join(folder, BACKUP_DIR);
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    removeIncompleteCopies(directory);
    // ISO time first, so names sort by age; then the version being replaced.
    const name = `${now.toISOString().replace(/[:.]/g, "-")}-n8n-${previous || "unrecorded"}`;
    // A crash can leave committed pages in the write-ahead log, so copy it too.
    // The database copy lands last: a set is a backup only once it exists.
    for (const suffix of ["-wal", "-shm", ""]) {
      if (fs.existsSync(database + suffix)) {
        copyIntoPlace(database + suffix, path.join(directory, `${name}.sqlite${suffix}`));
      }
    }
    saved = path.join(directory, `${name}.sqlite`);
    pruneBackups(directory);
  }
  writeAtomically(marker, `${version}\n`);
  return saved;
}

/** true or false from n8n's own setting; null when the database cannot say. */
export function ownerIsSetUp(database) {
  let db;
  try {
    db = new DatabaseSync(database, { readOnly: true });
    const row = db.prepare(
      "SELECT value FROM settings WHERE key = 'userManagement.isInstanceOwnerSetUp'").get();
    return row ? row.value === "true" : null;
  } catch (error) {
    // Locked, damaged or not yet migrated: leave the owner as it is.
    if (error.code === "ERR_SQLITE_ERROR") {
      return null;
    }
    throw error;
  } finally {
    db?.close();
  }
}

/** Whether n8n's owner should come from .env on this install. */
export function ownerFromEnv(folder, warn) {
  if (fs.existsSync(path.join(folder, OWNER_FROM_ENV_MARKER))) {
    return true;
  }
  const database = path.join(folder, "database.sqlite");
  if (!fs.existsSync(database)) {
    return true;
  }
  const setUp = ownerIsSetUp(database);
  if (setUp === null) {
    warn("ODS: could not read whether n8n has an owner; leaving the owner account as it is");
    return false;
  }
  return !setUp;
}

/** Keep a bcrypt hash of the password, regenerated only when it changes. */
export function ensureOwnerHash(file, password, bcrypt) {
  if (!password) {
    throw new Error("N8N_PASS is empty, so n8n's owner cannot be set from .env");
  }
  let current = "";
  try {
    current = fs.readFileSync(file, "utf8").trim();
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
  }
  if (BCRYPT_HASH.test(current) && bcrypt.compareSync(password, current)) {
    return false;
  }
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, bcrypt.hashSync(password, 10), { mode: 0o600 });
  fs.renameSync(temporary, file);
  return true;
}

export function main(env = process.env, warn = (message) => console.error(message)) {
  const folder = env.ODS_N8N_USER_FOLDER || "/tmp/.n8n";
  const packageDir = env.ODS_N8N_PACKAGE || "/usr/local/lib/node_modules/n8n";
  const version = packageVersion(packageDir);
  const saved = backupBeforeUpgrade(folder, version);
  if (saved) {
    warn(`ODS: saved n8n's database to ${saved} before n8n ${version} migrates it`);
  }
  if (!ownerFromEnv(folder, warn)) {
    return "existing";
  }
  // bcryptjs is a direct dependency of the n8n package, which hashes with it.
  const bcrypt = createRequire(path.join(packageDir, "package.json"))("bcryptjs");
  ensureOwnerHash(path.join(folder, OWNER_HASH_FILE), env.ODS_N8N_OWNER_PASSWORD || "", bcrypt);
  fs.writeFileSync(path.join(folder, OWNER_FROM_ENV_MARKER), "");
  return "env";
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.stdout.write(`${main()}\n`);
}

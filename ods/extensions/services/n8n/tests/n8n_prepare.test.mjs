// Contracts for n8n-prepare.mjs, the start-up step that backs up n8n's
// database before an upgrade and sets n8n's owner from .env where none exists.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  OWNER_HASH_FILE,
  backupBeforeUpgrade,
  ensureOwnerHash,
  main,
  ownerIsSetUp,
} from "../n8n-prepare.mjs";

const SERVICE = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PREPARE = path.join(SERVICE, "n8n-prepare.mjs");
const BCRYPT_SHAPE = /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/;

// A stand-in with bcryptjs's API and hash shape: salted, deterministic per salt.
const STUB_BCRYPT = `
const crypto = require("node:crypto");
const ALPHABET = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const encode = (bytes, length) => Array.from(bytes, (b) => ALPHABET[b % 64]).join("").slice(0, length);
const digest = (salt, password) => encode(crypto.createHash("sha512").update(salt + password).digest(), 31);
exports.hashSync = (password, rounds) => {
  const salt = encode(crypto.randomBytes(22), 22);
  return "$2a$" + String(rounds).padStart(2, "0") + "$" + salt + digest(salt, password);
};
exports.compareSync = (password, hash) => hash.slice(29) === digest(hash.slice(7, 29), password);
`;

function install(version = "2.41.6") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ods-n8n-prepare-"));
  const packageDir = path.join(root, "n8n");
  fs.mkdirSync(path.join(packageDir, "node_modules", "bcryptjs"), { recursive: true });
  fs.writeFileSync(path.join(packageDir, "package.json"), JSON.stringify({ name: "n8n", version }));
  fs.writeFileSync(path.join(packageDir, "node_modules", "bcryptjs", "index.js"), STUB_BCRYPT);
  const folder = path.join(root, ".n8n");
  fs.mkdirSync(folder);
  return {
    root,
    folder,
    packageDir,
    env: { ODS_N8N_USER_FOLDER: folder, ODS_N8N_PACKAGE: packageDir, ODS_N8N_OWNER_PASSWORD: "first-pass" },
  };
}

function database(folder, ownerSetUp) {
  const db = new DatabaseSync(path.join(folder, "database.sqlite"));
  db.exec("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, loadOnStartup INTEGER)");
  db.prepare("INSERT INTO settings VALUES ('userManagement.isInstanceOwnerSetUp', ?, 1)")
    .run(ownerSetUp ? "true" : "false");
  db.close();
}

function quietMain(env) {
  const warnings = [];
  return { result: main(env, (message) => warnings.push(message)), warnings };
}

function backups(folder) {
  const directory = path.join(folder, "ods-backups");
  return fs.existsSync(directory) ? fs.readdirSync(directory).sort() : [];
}

test("a new install takes its owner from .env and keeps one hash", () => {
  const { folder, env } = install();
  assert.equal(quietMain(env).result, "env");
  const hashFile = path.join(folder, OWNER_HASH_FILE);
  const first = fs.readFileSync(hashFile, "utf8");
  assert.match(first, BCRYPT_SHAPE);
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(hashFile).mode & 0o777, 0o600);
  }
  assert.deepEqual(backups(folder), [], "there is no database to back up yet");
  assert.equal(fs.readFileSync(path.join(folder, ".ods-n8n-version"), "utf8"), "2.41.6\n");
  // A restart with the same password keeps the hash, so sessions survive.
  assert.equal(quietMain(env).result, "env");
  assert.equal(fs.readFileSync(hashFile, "utf8"), first);
});

test("a changed N8N_PASS replaces the hash", () => {
  const { folder, env } = install();
  quietMain(env);
  const hashFile = path.join(folder, OWNER_HASH_FILE);
  const before = fs.readFileSync(hashFile, "utf8");
  quietMain({ ...env, ODS_N8N_OWNER_PASSWORD: "second-pass" });
  const after = fs.readFileSync(hashFile, "utf8");
  assert.notEqual(after, before);
  assert.match(after, BCRYPT_SHAPE);
});

test("an owner someone already created is left alone", () => {
  const { folder, env } = install();
  database(folder, true);
  const { result } = quietMain(env);
  assert.equal(result, "existing");
  assert.equal(fs.existsSync(path.join(folder, OWNER_HASH_FILE)), false);
  assert.equal(fs.existsSync(path.join(folder, ".ods-owner-from-env")), false);
});

test("an unclaimed owner is set from .env, closing the first-run window", () => {
  const { folder, env } = install();
  database(folder, false);
  assert.equal(quietMain(env).result, "env");
  assert.match(fs.readFileSync(path.join(folder, OWNER_HASH_FILE), "utf8"), BCRYPT_SHAPE);
});

test("an install whose owner came from .env stays that way after n8n records the owner", () => {
  const { folder, env } = install();
  database(folder, false);
  quietMain(env);
  // n8n marks the owner as set up when it applies the env-managed owner.
  const db = new DatabaseSync(path.join(folder, "database.sqlite"));
  db.exec("UPDATE settings SET value = 'true' WHERE key = 'userManagement.isInstanceOwnerSetUp'");
  db.close();
  assert.equal(quietMain(env).result, "env");
});

test("an unreadable database leaves the owner alone and says so", () => {
  const { folder, env } = install();
  fs.writeFileSync(path.join(folder, "database.sqlite"), "not a database");
  assert.equal(ownerIsSetUp(path.join(folder, "database.sqlite")), null);
  const { result, warnings } = quietMain(env);
  assert.equal(result, "existing");
  assert.ok(warnings.some((message) => message.includes("could not read whether n8n has an owner")));
});

test("the database is copied aside, with its write-ahead log, before each new version", () => {
  const { folder, env } = install("2.41.6");
  database(folder, true);
  fs.writeFileSync(path.join(folder, "database.sqlite-wal"), "pending pages");
  quietMain(env);
  const first = backups(folder);
  assert.equal(first.length, 2);
  assert.match(first[0], /-n8n-unrecorded\.sqlite$/);
  assert.match(first[1], /-n8n-unrecorded\.sqlite-wal$/);
  assert.equal(fs.readFileSync(path.join(folder, "ods-backups", first[1]), "utf8"), "pending pages");
  // The same version starting again makes no new copy.
  quietMain(env);
  assert.deepEqual(backups(folder), first);
});

test("each upgrade is named after the version it replaces and only two sets are kept", () => {
  const { folder } = install();
  database(folder, true);
  const times = ["2026-10-01T00:00:00.000Z", "2026-10-02T00:00:00.000Z", "2026-10-03T00:00:00.000Z"];
  backupBeforeUpgrade(folder, "2.6.4", new Date(times[0]));
  backupBeforeUpgrade(folder, "2.41.6", new Date(times[1]));
  backupBeforeUpgrade(folder, "2.42.0", new Date(times[2]));
  assert.deepEqual(backups(folder), [
    "2026-10-02T00-00-00-000Z-n8n-2.6.4.sqlite",
    "2026-10-03T00-00-00-000Z-n8n-2.41.6.sqlite",
  ]);
});

test("a backup stopped part-way leaves nothing that looks like a backup", () => {
  const { folder } = install();
  database(folder, true);
  fs.writeFileSync(path.join(folder, "database.sqlite-wal"), "pending pages");
  const copy = fs.copyFileSync;
  // Stop while the database itself is being copied, after its log landed.
  fs.copyFileSync = (source, target, mode) => {
    if (source.endsWith("database.sqlite")) {
      fs.writeFileSync(target, "half a database");
      throw new Error("stopped");
    }
    return copy(source, target, mode);
  };
  try {
    assert.throws(() => backupBeforeUpgrade(folder, "2.41.6", new Date("2026-10-01T00:00:00.000Z")), /stopped/);
  } finally {
    fs.copyFileSync = copy;
  }
  assert.ok(!backups(folder).some((name) => name.endsWith(".sqlite")), "no database copy may look complete");
  assert.equal(fs.existsSync(path.join(folder, ".ods-n8n-version")), false, "the version is recorded after the backup");
  // The next start backs up again and clears what the stopped one left.
  backupBeforeUpgrade(folder, "2.41.6", new Date("2026-10-02T00:00:00.000Z"));
  assert.deepEqual(backups(folder), [
    "2026-10-02T00-00-00-000Z-n8n-unrecorded.sqlite",
    "2026-10-02T00-00-00-000Z-n8n-unrecorded.sqlite-wal",
  ]);
});

test("an empty N8N_PASS refuses to set an owner", () => {
  const { folder } = install();
  assert.throws(
    () => ensureOwnerHash(path.join(folder, OWNER_HASH_FILE), "", { hashSync() {}, compareSync() {} }),
    /N8N_PASS is empty/);
});

test("run as a script it prints only the owner source on stdout", () => {
  const { env } = install();
  const run = spawnSync(process.execPath, [PREPARE], { env: { ...process.env, ...env }, encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.stdout, "env\n");
});

test("the entrypoint and compose file match the script", () => {
  const entrypoint = fs.readFileSync(path.join(SERVICE, "n8n-entrypoint.sh"), "utf8");
  const compose = fs.readFileSync(path.join(SERVICE, "compose.yaml"), "utf8");
  assert.ok(entrypoint.includes(`N8N_INSTANCE_OWNER_PASSWORD_HASH_FILE=/tmp/.n8n/${OWNER_HASH_FILE}`));
  assert.ok(entrypoint.includes('owner_source="$(node /opt/ods/n8n-prepare.mjs)"'));
  // The plaintext password never reaches n8n, its task runners or PID 1:
  // tini becomes PID 1 only after the unset.
  const unset = entrypoint.indexOf("unset ODS_N8N_OWNER_PASSWORD");
  assert.ok(unset > 0 && unset < entrypoint.indexOf("exec tini -- /docker-entrypoint.sh"));
  assert.ok(compose.includes('entrypoint: ["/bin/sh", "/opt/ods/n8n-entrypoint.sh"]'));
  // Until then the script is PID 1, so it handles stop signals itself.
  assert.ok(entrypoint.indexOf("trap 'exit 143' TERM") < entrypoint.indexOf("owner_source="));
  assert.ok(compose.includes("./extensions/services/n8n/n8n-prepare.mjs:/opt/ods/n8n-prepare.mjs:ro"));
  assert.ok(!compose.includes("N8N_DEFAULT_ADMIN_"), "n8n has no such settings; they only exposed the password");
  assert.ok(!/^\s+- N8N_[A-Z_]*=\$\{N8N_PASS/m.test(compose), "N8N_PASS must not be passed to n8n directly");
});

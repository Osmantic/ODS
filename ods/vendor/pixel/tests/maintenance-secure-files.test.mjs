import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  WorkMaintenanceSecureFileError,
  readOwnerPrivateBoundedFile,
  writeOwnerPrivateCreateNoClobber,
} from "../deploy/work-controller/maintenance-secure-files.mjs";

const uid = process.geteuid?.() ?? 1000;
const linux = process.platform !== "win32";
const sha256 = (v) => createHash("sha256").update(v).digest("hex");

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-secure-files-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const target = join(root, "artifact.json");
  return { root, target };
}

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
}

test("no-clobber create publishes a fresh owner-private singular artifact", async (t) => {
  const { target } = await fixture(t);
  const value = { schemaVersion: 1, nonce: "a".repeat(64) };
  await writeOwnerPrivateCreateNoClobber(target, value, uid);
  const info = await lstat(target);
  assert.equal(info.isFile(), true);
  assert.equal(info.isSymbolicLink(), false);
  assert.equal(info.nlink, 1);
  if (linux) {
    assert.equal(info.uid, uid);
    assert.equal(info.mode & 0o077, 0);
  }
  const parsed = JSON.parse(await readFile(target, "utf8"));
  assert.deepEqual(parsed, value);
});

test("no-clobber create refuses a preexisting regular target and leaves attacker bytes unchanged", async (t) => {
  const { target } = await fixture(t);
  await privateWrite(target, { attacker: true });
  const before = await readFile(target, "utf8");
  await assert.rejects(writeOwnerPrivateCreateNoClobber(target, { victim: true }, uid), WorkMaintenanceSecureFileError);
  const after = await readFile(target, "utf8");
  assert.equal(after, before, "attacker bytes must remain unchanged");
  assert.deepEqual(JSON.parse(after), { attacker: true });
});

test("no-clobber create refuses a symlink target and leaves the symlink intact", async (t) => {
  const { root, target } = await fixture(t);
  const real = join(root, "real.json");
  await privateWrite(real, { attacker: "real" });
  await symlink(real, target);
  await assert.rejects(writeOwnerPrivateCreateNoClobber(target, { victim: true }, uid), WorkMaintenanceSecureFileError);
  const info = await lstat(target);
  assert.equal(info.isSymbolicLink(), true, "the symlink target must not be replaced");
  assert.deepEqual(JSON.parse(await readFile(real, "utf8")), { attacker: "real" });
});

test("no-clobber create refuses a hardlink target and leaves the linked bytes unchanged", async (t) => {
  const { root, target } = await fixture(t);
  const original = join(root, "original.json");
  await privateWrite(original, { attacker: "hardlink" });
  await writeFile(target, ""); // placeholder then replace via link
  await rm(target);
  const { link } = await import("node:fs/promises");
  await link(original, target);
  const before = await readFile(original, "utf8");
  await assert.rejects(writeOwnerPrivateCreateNoClobber(target, { victim: true }, uid), WorkMaintenanceSecureFileError);
  const after = await readFile(original, "utf8");
  assert.equal(after, before, "hardlinked bytes must remain unchanged");
  assert.equal((await lstat(target)).nlink, 2, "the hardlink must remain intact");
});

test("no-clobber create refuses a concurrent publication race (second writer loses)", async (t) => {
  const { target } = await fixture(t);
  const value = { schemaVersion: 1, winner: "first" };
  await writeOwnerPrivateCreateNoClobber(target, value, uid);
  await assert.rejects(writeOwnerPrivateCreateNoClobber(target, { winner: "second" }, uid), WorkMaintenanceSecureFileError);
  const parsed = JSON.parse(await readFile(target, "utf8"));
  assert.deepEqual(parsed, value, "the first concurrent publication must win unchanged");
});

test("readOwnerPrivateBoundedFile proves owner, private mode, and nlink=1 from the open handle", async (t) => {
  const { target } = await fixture(t);
  const value = { schemaVersion: 1, payload: "x".repeat(100) };
  await writeOwnerPrivateCreateNoClobber(target, value, uid);
  const record = await readOwnerPrivateBoundedFile(target, 4096, "artifact", uid);
  assert.deepEqual(JSON.parse(record.bytes.toString("utf8")), value);
  assert.equal(record.details.nlink, 1);
  if (linux) {
    assert.equal(record.details.uid, uid);
    assert.equal(record.details.mode & 0o077, 0);
  }
});

test("readOwnerPrivateBoundedFile rejects a symlink target", async (t) => {
  const { root, target } = await fixture(t);
  const real = join(root, "real.json");
  await privateWrite(real, { attacker: "real" });
  await symlink(real, target);
  await assert.rejects(readOwnerPrivateBoundedFile(target, 4096, "artifact", uid), WorkMaintenanceSecureFileError);
});

test("readOwnerPrivateBoundedFile rejects an oversized file", async (t) => {
  const { target } = await fixture(t);
  await writeOwnerPrivateCreateNoClobber(target, { payload: "y".repeat(2000) }, uid);
  await assert.rejects(readOwnerPrivateBoundedFile(target, 100, "artifact", uid), WorkMaintenanceSecureFileError);
});

// ---------------------------------------------------------------------------
// P1 direct primitive tests: Buffer publication and a genuinely parallel
// two-writer Promise.allSettled race (exactly one succeeds and the winning
// bytes remain coherent).
// ---------------------------------------------------------------------------

test("no-clobber create publishes an exact Buffer with coherent bytes", async (t) => {
  const { target } = await fixture(t);
  const bytes = Buffer.from([0x00, 0x01, 0xfe, 0xff, 0x42, 0x43]);
  await writeOwnerPrivateCreateNoClobber(target, bytes, uid);
  const read = await readOwnerPrivateBoundedFile(target, 4096, "artifact", uid);
  assert.deepEqual(read.bytes, bytes, "the published Buffer bytes must round-trip exactly");
});

test("genuinely parallel two-writer Promise.allSettled race: exactly one wins and the winning bytes remain coherent", async (t) => {
  const { target } = await fixture(t);
  const first = Buffer.from("first-writer-payload-".repeat(200));
  const second = Buffer.from("second-writer-payload-".repeat(200));
  const results = await Promise.allSettled([
    writeOwnerPrivateCreateNoClobber(target, first, uid),
    writeOwnerPrivateCreateNoClobber(target, second, uid),
  ]);
  const fulfilled = results.filter((r) => r.status === "fulfilled");
  const rejected = results.filter((r) => r.status === "rejected");
  assert.equal(fulfilled.length, 1, "exactly one concurrent writer must win");
  assert.equal(rejected.length, 1, "exactly one concurrent writer must lose");
  assert.ok(rejected[0].reason instanceof WorkMaintenanceSecureFileError, "the loser must fail with the no-clobber error");
  const read = await readOwnerPrivateBoundedFile(target, 8192, "artifact", uid);
  const winner = read.bytes.equals(first) ? first : second;
  assert.ok(read.bytes.equals(first) || read.bytes.equals(second), "the winning bytes must be one of the two coherent payloads");
  assert.deepEqual(read.bytes, winner, "the winning bytes must remain coherent and complete");
  assert.equal(sha256(read.bytes), sha256(winner), "the winning bytes hash must match exactly one writer");
});

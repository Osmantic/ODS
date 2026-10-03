import assert from "node:assert/strict";
import { chmod, link, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { modelBackendCoordinationContract, withModelBackendCoordination } from "../deploy/work-runner/model-backend-coordination.mjs";

async function root(t) {
  const value = await mkdtemp(join(tmpdir(), "pixel-model-coordinate-"));
  if (process.platform !== "win32") await chmod(value, 0o700);
  t.after(() => rm(value, { recursive: true, force: true }));
  return value;
}

test("coordination serializes critical sections and releases after success", async (t) => {
  const stateRoot = await root(t), events = [];
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const first = withModelBackendCoordination(stateRoot, async () => { events.push("first-enter"); await held; events.push("first-exit"); });
  while (!events.length) await new Promise((resolve) => setTimeout(resolve, 1));
  const second = withModelBackendCoordination(stateRoot, async () => { events.push("second-enter"); });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(events, ["first-enter"]);
  release(); await Promise.all([first, second]);
  assert.deepEqual(events, ["first-enter", "first-exit", "second-enter"]);
  await assert.rejects(readFile(join(stateRoot, modelBackendCoordinationContract.lockName)), { code: "ENOENT" });
});

test("coordination releases its exact lock when the protected operation fails", async (t) => {
  const stateRoot = await root(t);
  await assert.rejects(withModelBackendCoordination(stateRoot, async () => { throw new Error("fixture failure"); }), /fixture failure/u);
  assert.equal(await withModelBackendCoordination(stateRoot, async () => "recovered"), "recovered");
});

test("coordination recovers a private single-link lock owned by a dead process", async (t) => {
  const stateRoot = await root(t), lockPath = join(stateRoot, modelBackendCoordinationContract.lockName);
  await writeFile(lockPath, `${JSON.stringify({ schemaVersion: 1, pid: 424242, createdAt: "2026-08-12T12:00:00.000Z", token: "a".repeat(64) })}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  assert.equal(await withModelBackendCoordination(stateRoot, async () => "recovered", { pidAlive: () => false }), "recovered");
});

test("coordination times out rather than stealing a live lock", async (t) => {
  const stateRoot = await root(t), lockPath = join(stateRoot, modelBackendCoordinationContract.lockName);
  await writeFile(lockPath, `${JSON.stringify({ schemaVersion: 1, pid: process.pid, createdAt: "2026-08-12T12:00:00.000Z", token: "b".repeat(64) })}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  let tick = 0;
  await assert.rejects(withModelBackendCoordination(stateRoot, async () => {}, {
    timeoutMilliseconds: 2, pollMilliseconds: 1, monotonic: () => tick++, sleeper: async () => {}, pidAlive: () => true,
  }), /held by another live operation/u);
});

test("coordination refuses linked or symbolic lock substitution", async (t) => {
  const linkedRoot = await root(t), linked = join(linkedRoot, modelBackendCoordinationContract.lockName), peer = join(linkedRoot, "peer");
  await writeFile(linked, `${JSON.stringify({ schemaVersion: 1, pid: process.pid, createdAt: "2026-08-12T12:00:00.000Z", token: "c".repeat(64) })}\n`, { mode: 0o600 });
  await link(linked, peer);
  await assert.rejects(withModelBackendCoordination(linkedRoot, async () => {}), /private and single-link/u);
  const symlinkRoot = await root(t), target = join(symlinkRoot, "target"), symbolic = join(symlinkRoot, modelBackendCoordinationContract.lockName);
  await writeFile(target, "fixture", { mode: 0o600 });
  try { await symlink(target, symbolic); }
  catch (error) { if (process.platform === "win32" && error?.code === "EPERM") return; throw error; }
  await assert.rejects(withModelBackendCoordination(symlinkRoot, async () => {}), /cannot be read safely/u);
});

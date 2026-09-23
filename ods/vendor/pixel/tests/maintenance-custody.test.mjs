import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, rename, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import {
  inspectMaintenanceCustodyLock,
  validateMaintenanceCustodyBinding,
  withMaintenanceCustody,
} from "../deploy/work-controller/maintenance-custody.mjs";

const execute = promisify(execFile);

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-maintenance-custody-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const lockPath = join(root, "guardian.lock");
  await writeFile(lockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  return { lockPath, binding: { kind: "advisory-flock", lockPath, acquireTimeoutSeconds: 30 } };
}

test("maintenance custody binds one singular owner-held advisory lock", async (t) => {
  const value = await fixture(t);
  assert.equal(validateMaintenanceCustodyBinding(value.binding).lockPath, value.lockPath);
  const inspected = await inspectMaintenanceCustodyLock(value.binding, process.geteuid?.() ?? 1000);
  assert.match(inspected.identitySha256, /^[a-f0-9]{64}$/u);
  assert.throws(() => validateMaintenanceCustodyBinding({ ...value.binding, acquireTimeoutSeconds: 0 }));
  assert.throws(() => validateMaintenanceCustodyBinding({ ...value.binding, lockPath: `${value.lockPath}\nshadow` }));
});

test("maintenance custody holds the reviewed identity for the whole operation and releases once", async (t) => {
  const value = await fixture(t);
  const calls = [];
  const result = await withMaintenanceCustody(value.binding, process.geteuid?.() ?? 1000, async (identitySha256) => {
    calls.push(["operation", identitySha256]);
    return "complete";
  }, {
    async acquireCustody(binding) {
      calls.push(["acquire", binding.lockPath]);
      return async () => { calls.push("release"); };
    },
  });
  assert.equal(result, "complete");
  assert.deepEqual(calls.map((entry) => Array.isArray(entry) ? entry[0] : entry), ["acquire", "operation", "release"]);
});

test("maintenance custody refuses lock replacement after acquisition and still releases", async (t) => {
  const value = await fixture(t);
  let released = false;
  await assert.rejects(withMaintenanceCustody(value.binding, process.geteuid?.() ?? 1000, async () => "unreachable", {
    async acquireCustody() {
      const replacementPath = `${value.lockPath}.replacement`;
      await writeFile(replacementPath, "", { mode: 0o600 });
      if (process.platform !== "win32") await chmod(replacementPath, 0o600);
      else await unlink(value.lockPath);
      await rename(replacementPath, value.lockPath);
      return async () => { released = true; };
    },
  }), /changed during acquisition/u);
  assert.equal(released, true);
});

test("default Linux custody holds the advisory flock for the complete operation and releases it", { skip: process.platform !== "linux" }, async (t) => {
  const value = await fixture(t);
  let conflicted = false;
  await withMaintenanceCustody(value.binding, process.geteuid(), async () => {
    try {
      await execute("/usr/bin/flock", ["--nonblock", value.lockPath, "/bin/true"], { timeout: 5000 });
    } catch (error) {
      conflicted = error?.code === 1;
    }
    assert.equal(conflicted, true);
  });
  await execute("/usr/bin/flock", ["--nonblock", value.lockPath, "/bin/true"], { timeout: 5000 });
});

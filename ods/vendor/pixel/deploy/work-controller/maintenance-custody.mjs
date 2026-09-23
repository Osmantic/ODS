import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";

const MAX_SIGNAL_BYTES = 4096;
const LOCKED_SIGNAL = "PIXEL_MAINTENANCE_CUSTODY_LOCKED\n";
const FIXED_ENV = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" });

export class WorkMaintenanceCustodyError extends Error {}

function fail(message) { throw new WorkMaintenanceCustodyError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail("maintenance custody binding shape is invalid");
}

export function validateMaintenanceCustodyBinding(value) {
  exactKeys(value, ["kind", "lockPath", "acquireTimeoutSeconds"]);
  if (value.kind !== "advisory-flock" || typeof value.lockPath !== "string" || !isAbsolute(value.lockPath) || resolve(value.lockPath) !== value.lockPath || value.lockPath.includes("\0") || /[\r\n]/u.test(value.lockPath)) fail("maintenance custody lock path is invalid");
  if (!Number.isSafeInteger(value.acquireTimeoutSeconds) || value.acquireTimeoutSeconds < 1 || value.acquireTimeoutSeconds > 300) fail("maintenance custody timeout is invalid");
  return structuredClone(value);
}

export async function inspectMaintenanceCustodyLock(value, expectedOwnerUid) {
  const binding = validateMaintenanceCustodyBinding(value);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("maintenance custody owner is invalid");
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(binding.lockPath), lstat(binding.lockPath, { bigint: true })]); }
  catch { fail("maintenance custody lock could not be inspected safely"); }
  if (!samePath(actual, binding.lockPath) || !info.isFile() || info.isSymbolicLink() || info.nlink !== 1n) fail("maintenance custody lock is not a singular real file");
  if (process.platform !== "win32" && (info.uid !== BigInt(expectedOwnerUid) || (info.mode & 0o003n) !== 0n)) fail("maintenance custody lock ownership or mode is unsafe");
  const identity = {
    kind: binding.kind,
    lockPath: binding.lockPath,
    dev: String(info.dev),
    ino: String(info.ino),
    uid: String(info.uid),
    gid: String(info.gid),
    mode: String(info.mode & 0o7777n),
  };
  return Object.freeze({ binding, identitySha256: createHash("sha256").update(canonical(identity)).digest("hex") });
}

function waitForExit(child, timeoutMilliseconds) {
  return new Promise((resolve_, reject) => {
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve_();
    };
    const timer = setTimeout(() => { child.kill("SIGKILL"); finish(new WorkMaintenanceCustodyError("maintenance custody lock holder did not exit")); }, timeoutMilliseconds);
    child.once("error", () => finish(new WorkMaintenanceCustodyError("maintenance custody lock holder failed")));
    child.once("exit", (code, signal) => finish(code === 0 && signal === null ? null : new WorkMaintenanceCustodyError("maintenance custody lock holder exited unexpectedly")));
  });
}

async function acquireDefault(binding, dependencies = {}) {
  const spawnProcess = dependencies.spawnProcess ?? spawn;
  const flockPath = dependencies.flockPath ?? "/usr/bin/flock";
  const shellPath = dependencies.shellPath ?? "/bin/sh";
  const child = spawnProcess(flockPath, ["--exclusive", "--wait", String(binding.acquireTimeoutSeconds), "--conflict-exit-code", "75", binding.lockPath, shellPath, "-c", `printf '${LOCKED_SIGNAL}'; read _ || true`], {
    cwd: "/", env: FIXED_ENV, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "", stderrBytes = 0, acquired = false;
  const acquisition = new Promise((resolve_, reject) => {
    const timeout = setTimeout(() => { child.kill("SIGKILL"); reject(new WorkMaintenanceCustodyError("maintenance custody lock acquisition timed out")); }, (binding.acquireTimeoutSeconds + 5) * 1000);
    const failAcquisition = () => { clearTimeout(timeout); reject(new WorkMaintenanceCustodyError("maintenance custody lock could not be acquired")); };
    child.once("error", failAcquisition);
    child.once("exit", failAcquisition);
    child.stderr.on("data", (chunk) => { stderrBytes += chunk.length; if (stderrBytes > MAX_SIGNAL_BYTES) { child.kill("SIGKILL"); failAcquisition(); } });
    child.stdout.on("data", (chunk) => {
      if (acquired) return;
      stdout += chunk.toString("utf8");
      if (Buffer.byteLength(stdout, "utf8") > MAX_SIGNAL_BYTES || !LOCKED_SIGNAL.startsWith(stdout) && stdout !== LOCKED_SIGNAL) { child.kill("SIGKILL"); failAcquisition(); return; }
      if (stdout === LOCKED_SIGNAL) { acquired = true; clearTimeout(timeout); resolve_(); }
    });
  });
  await acquisition;
  return async () => {
    child.stdin.end();
    await waitForExit(child, 5000);
  };
}

export async function withMaintenanceCustody(value, expectedOwnerUid, operation, dependencies = {}) {
  if (typeof operation !== "function") fail("maintenance custody operation is invalid");
  const before = await inspectMaintenanceCustodyLock(value, expectedOwnerUid);
  const acquire = dependencies.acquireCustody ?? acquireDefault;
  const release = await acquire(before.binding, dependencies);
  if (typeof release !== "function") fail("maintenance custody acquisition did not return a release function");
  let operationFailure = null;
  try {
    const after = await inspectMaintenanceCustodyLock(value, expectedOwnerUid);
    if (after.identitySha256 !== before.identitySha256) fail("maintenance custody lock changed during acquisition");
    return await operation(before.identitySha256);
  } catch (error) {
    operationFailure = error;
    throw error;
  } finally {
    try { await release(); }
    catch (releaseError) { if (!operationFailure) throw releaseError; }
  }
}

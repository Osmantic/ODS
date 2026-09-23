import { constants } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { link, lstat, open, realpath, stat, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";

const TOKEN_RE = /^[a-f0-9]{64}$/u;
const LOCK_NAME = "model-backend.coordination.lock";
const MAX_LOCK_BYTES = 4096;

export class ModelBackendCoordinationError extends Error {}
function fail(message) { throw new ModelBackendCoordinationError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function token() { return createHash("sha256").update(randomBytes(32)).digest("hex"); }

async function privateRoot(stateRoot, expectedOwnerUid) {
  if (typeof stateRoot !== "string" || !isAbsolute(stateRoot) || resolve(stateRoot) !== stateRoot || dirname(stateRoot) === stateRoot) fail("model backend coordination root is invalid");
  let details, actual;
  try { [details, actual] = await Promise.all([lstat(stateRoot), realpath(stateRoot)]); }
  catch { fail("model backend coordination root is unavailable"); }
  if (!details.isDirectory() || details.isSymbolicLink() || !samePath(actual, stateRoot)) fail("model backend coordination root is not a real directory");
  if (process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail("model backend coordination root is not owner-private");
  return join(stateRoot, LOCK_NAME);
}

export async function inspectModelBackendCoordinationRoot(stateRoot, expectedOwnerUid = process.geteuid?.() ?? 0) {
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("model backend coordination owner is invalid");
  await privateRoot(stateRoot, expectedOwnerUid);
  return true;
}

function exactLock(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["createdAt", "pid", "schemaVersion", "token"].sort())
    || value.schemaVersion !== 1 || !Number.isSafeInteger(value.pid) || value.pid < 1
    || !TOKEN_RE.test(value.token ?? "") || typeof value.createdAt !== "string"
    || !Number.isSafeInteger(Date.parse(value.createdAt))) fail("model backend coordination lock is malformed");
  return value;
}

async function readExactLock(lockPath, expectedOwnerUid) {
  let record;
  try { record = await readBoundedRegularFile(lockPath, MAX_LOCK_BYTES, "model backend coordination lock"); }
  catch (error) {
    if (error?.code === "ENOENT") return null;
    fail("model backend coordination lock cannot be read safely");
  }
  if (record.details.nlink !== 1 || process.platform !== "win32" && (record.details.uid !== expectedOwnerUid || (record.details.mode & 0o077) !== 0)) fail("model backend coordination lock is not private and single-link");
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail("model backend coordination lock is not strict UTF-8");
  let value;
  try { value = parseStrictJson(text, "model backend coordination lock"); }
  catch { fail("model backend coordination lock is not strict JSON"); }
  return { value: exactLock(value), details: record.details };
}

function processAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error?.code !== "ESRCH"; }
}

async function detachExactLock(lockPath, expectedToken, expectedOwnerUid, suffix) {
  const shadow = join(dirname(lockPath), `.${LOCK_NAME}.${suffix}.${process.pid}.${randomBytes(8).toString("hex")}`);
  try { await link(lockPath, shadow); }
  catch (error) {
    if (error?.code === "ENOENT") return false;
    fail("model backend coordination lock could not be isolated safely");
  }
  try {
    const [source, copy, record] = await Promise.all([stat(lockPath), stat(shadow), readBoundedRegularFile(shadow, MAX_LOCK_BYTES, "isolated model backend coordination lock")]);
    if (source.dev !== copy.dev || source.ino !== copy.ino || source.nlink !== 2 || copy.nlink !== 2
      || process.platform !== "win32" && (source.uid !== expectedOwnerUid || (source.mode & 0o077) !== 0)) fail("model backend coordination lock changed while being isolated");
    const text = record.bytes.toString("utf8");
    let value;
    try { value = exactLock(parseStrictJson(text, "isolated model backend coordination lock")); }
    catch { fail("isolated model backend coordination lock is invalid"); }
    if (value.token !== expectedToken) fail("model backend coordination lock ownership changed");
    await unlink(lockPath);
    await unlink(shadow);
    return true;
  } catch (error) {
    await unlink(shadow).catch(() => {});
    throw error;
  }
}

async function createLock(lockPath, value) {
  const flags = constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | (constants.O_NOFOLLOW ?? 0);
  let handle;
  try { handle = await open(lockPath, flags, 0o600); }
  catch (error) {
    if (error?.code === "EEXIST") return false;
    fail("model backend coordination lock could not be created");
  }
  try { await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8"); await handle.sync(); }
  catch (error) { await handle.close().catch(() => {}); await unlink(lockPath).catch(() => {}); throw error; }
  await handle.close();
  return true;
}

export async function withModelBackendCoordination(stateRoot, operation, {
  expectedOwnerUid = process.geteuid?.() ?? 0, timeoutMilliseconds = 30000, pollMilliseconds = 50,
  sleeper = delay, monotonic = () => performance.now(), pidAlive = processAlive, now = () => new Date(),
} = {}) {
  if (typeof operation !== "function" || !Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0
    || !Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > 300000
    || !Number.isSafeInteger(pollMilliseconds) || pollMilliseconds < 1 || pollMilliseconds > 1000
    || typeof sleeper !== "function" || typeof monotonic !== "function" || typeof pidAlive !== "function" || typeof now !== "function") fail("model backend coordination options are invalid");
  const lockPath = await privateRoot(stateRoot, expectedOwnerUid), lockToken = token();
  const started = monotonic();
  if (!Number.isFinite(started) || started < 0) fail("model backend coordination clock is invalid");
  const deadline = started + timeoutMilliseconds;
  for (;;) {
    const instant = now();
    if (!(instant instanceof Date) || !Number.isSafeInteger(instant.getTime())) fail("model backend coordination wall clock is invalid");
    const value = { schemaVersion: 1, pid: process.pid, createdAt: instant.toISOString(), token: lockToken };
    if (await createLock(lockPath, value)) {
      try { return await operation(); }
      finally {
        const current = await readExactLock(lockPath, expectedOwnerUid);
        if (!current || current.value.token !== lockToken) fail("model backend coordination lock was lost during the protected operation");
        await detachExactLock(lockPath, lockToken, expectedOwnerUid, "release");
      }
    }
    const current = await readExactLock(lockPath, expectedOwnerUid);
    if (current && !pidAlive(current.value.pid)) {
      await detachExactLock(lockPath, current.value.token, expectedOwnerUid, "stale");
      continue;
    }
    const observed = monotonic();
    if (!Number.isFinite(observed) || observed < started) fail("model backend coordination clock is invalid");
    if (observed >= deadline) fail("model backend coordination lock is held by another live operation");
    await sleeper(Math.min(pollMilliseconds, deadline - observed));
  }
}

export const modelBackendCoordinationContract = Object.freeze({ lockName: LOCK_NAME });

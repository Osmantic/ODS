/**
 * Internal implementation module for provider credential ingress.
 *
 * This module contains the actual implementation of the exclusive credential
 * file write. It is NOT imported by production code — production imports only
 * the three-argument facade from provider-credential-ingress-core.mjs.
 *
 * Test code imports the implementation here through the test wrapper
 * (provider-credential-ingress-test.mjs) to exercise seams.
 *
 * Test-only seams (seams object):
 *   - forceCloseFail: if true, the close after publication will fail.
 *   - forceHardlinkBeforeWrite: if true, a hard link is created on the
 *     staging inode before the nlink check; if the hardlink cannot be
 *     created the operation fails immediately.
 *   - forceProcUnavailable: if true, the /proc/self/fd access check in
 *     createViaDirFd is forced to fail, simulating proc unavailability.
 *
 * @module
 */
import { constants, open as fsOpenCb, read as fsRead, write as fsWrite, fsync as fsyncCb, fdatasync as fdatasyncCb, close as fsCloseCb, fstat as fsfstatCb, ftruncate as fsftruncateCb } from "node:fs";
import { webcrypto as crypto } from "node:crypto";
import { lstat, realpath, access as fsAccess } from "node:fs/promises";
import { basename, parse, resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { spawnSync } from "node:child_process";

const open = promisify(fsOpenCb);
const read = promisify(fsRead);
const write = promisify(fsWrite);
const fsyncP = promisify(fsyncCb);
const fdatasyncP = promisify(fdatasyncCb);
const closeP = promisify(fsCloseCb);
const fstatP = promisify(fsfstatCb);
const ftruncateP = promisify(fsftruncateCb);

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------
export class ProviderCredentialIngressCoreError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProviderCredentialIngressCoreError";
  }
}

function fail(message) {
  throw new ProviderCredentialIngressCoreError(message);
}

// ---------------------------------------------------------------------------
// Closed credential filename registry
// ---------------------------------------------------------------------------
const ALLOWED_FINAL_NAMES = new Set([
  "moonshot-kimi-key",
  "openai-key",
  "anthropic-key",
]);

// ---------------------------------------------------------------------------
// Directory validation — standalone, path-based
// ---------------------------------------------------------------------------
export async function assertPrivateDirectory(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory()) fail("credential directory does not exist");
  if (info.isSymbolicLink()) fail("credential directory must not be a symlink");
  if (await realpath(path).catch(() => null) !== resolve(path)) {
    fail("credential directory must be a real absolute path");
  }
  if (process.platform !== "win32") {
    if (info.uid !== process.geteuid()) fail("credential directory must be owned by the invoking user");
    if ((info.mode & 0o077) !== 0) fail("credential directory must have 0700 permissions");
  }
}

// ---------------------------------------------------------------------------
// Internal: pinned directory handle validator
// Uses static/promisified fd APIs. Ownership of both descriptors is explicit
// on every path. No dynamic imports.
// Returns { fd, stat } or fails closed.
// ---------------------------------------------------------------------------
async function bindDirectoryHandle(dirPath) {
  const dirLstat = await lstat(dirPath).catch(() => null);
  if (!dirLstat?.isDirectory()) fail("credential directory does not exist");
  if (dirLstat.isSymbolicLink()) fail("credential directory must not be a symlink");
  if (await realpath(dirPath).catch(() => null) !== resolve(dirPath)) {
    fail("credential directory must be a real absolute path");
  }
  if (process.platform !== "win32") {
    if (dirLstat.uid !== process.geteuid()) fail("credential directory must be owned by the invoking user");
    if ((dirLstat.mode & 0o077) !== 0) fail("credential directory must have 0700 permissions");
  }

  let dirFd = -1;
  try {
    dirFd = await open(dirPath, constants.O_DIRECTORY | constants.O_NOFOLLOW);
  } catch {
    fail("credential directory cannot be opened exclusively");
  }

  let dirFstat;
  try {
    dirFstat = await fstatP(dirFd);
  } catch {
    await closeP(dirFd).catch(() => {});
    fail("credential directory fstat failed");
  }

  if (dirFstat.dev !== dirLstat.dev || dirFstat.ino !== dirLstat.ino) {
    await closeP(dirFd).catch(() => {});
    fail("credential directory was replaced between lstat and open");
  }

  return { fd: dirFd, stat: dirFstat };
}

// ---------------------------------------------------------------------------
// Internal: write all bytes to fd with bounded loop
// Accepts any bounded 0 < bytesWritten <= requested as progress.
// Fails immediately on zero bytes written or invalid (negative) result.
// Defensible maximum-iteration bound: 4096 iterations for up to 8KB secrets.
// ---------------------------------------------------------------------------
const WRITE_ALL_MAX_ITERATIONS = 4096;

async function writeAll(fd, data) {
  const total = data.byteLength;
  let offset = 0;
  let iterations = 0;

  while (offset < total) {
    iterations++;
    if (iterations > WRITE_ALL_MAX_ITERATIONS) {
      throw new Error(`write-all exceeded ${WRITE_ALL_MAX_ITERATIONS} iterations for ${total} bytes`);
    }

    const result = await write(fd, data, offset, total - offset, offset);
    const written = result.bytesWritten;

    // Fail on zero or invalid progress
    if (written <= 0) {
      throw new Error(`write-all: zero or invalid progress at offset ${offset}/${total}`);
    }

    offset += written;
  }

  return total;
}

// ---------------------------------------------------------------------------
// Internal: read all bytes from a non-seekable fd (pipe)
// Uses position: null for non-seekable descriptors; tracks offset only
// for the destination buffer.
// ---------------------------------------------------------------------------
async function readAllFromFd(fd, maxBytes) {
  const buffer = Buffer.alloc(maxBytes);
  let bytesRead = 0;
  let eof = false;

  while (bytesRead < maxBytes && !eof) {
    const result = await read(fd, buffer, bytesRead, maxBytes - bytesRead, null);
    bytesRead += result.bytesRead;
    if (result.bytesRead === 0) eof = true;
  }

  return buffer.subarray(0, bytesRead);
}

// ---------------------------------------------------------------------------
// Internal: bounded exact read from fd at explicit position offsets
// Repeated short positive reads are accepted; zero progress before exact
// completion is rejected.
// ---------------------------------------------------------------------------
const READ_ALL_MAX_ITERATIONS = 4096;

async function readAllExact(fd, expectedSize) {
  const buffer = Buffer.alloc(expectedSize);
  let offset = 0;
  let iterations = 0;

  while (offset < expectedSize) {
    iterations++;
    if (iterations > READ_ALL_MAX_ITERATIONS) {
      throw new Error(`read-all-exceeded ${READ_ALL_MAX_ITERATIONS} iterations for ${expectedSize} bytes`);
    }

    const result = await read(fd, buffer, offset, expectedSize - offset, offset);
    const b = result.bytesRead;

    // Zero progress before exact completion = failure
    if (b <= 0) {
      throw new Error(`read-back: zero progress at offset ${offset}/${expectedSize}`);
    }

    offset += b;
  }

  return buffer;
}

// ---------------------------------------------------------------------------
// Internal: safe fd-relative cleanup (no pathname unlink)
// Zero the credential and truncate to 0 via the held fd, fdatasync after
// each mutation. Returns whether cleanup succeeded.
// ---------------------------------------------------------------------------
async function zeroTruncateFd(fd) {
  try {
    await write(fd, Buffer.alloc(1), 0, 1, 0);
    await fdatasyncP(fd);
    await ftruncateP(fd, 0);
    await fdatasyncP(fd);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Internal: cleanup on failure using pinned file fd
// Zero/truncate the held fd, then close it. Never unlinks a pathname.
// Returns -1 (closed) only when both zero/truncate and close succeed.
// If zero/truncate fails, close the fd and throw "uncertain custody"
// even when close succeeds — a credential-bearing staging inode may remain.
// If close fails, always throw "uncertain custody".
//
// `seams` (test-only): if seams.forceZeroTruncateFail is true, the
// zero-truncate step is forced to fail (simulating an I/O error during
// sanitization). The fd is still closed, and "uncertain custody" is thrown.
// ---------------------------------------------------------------------------
async function cleanupCreatedFd(fd, seams) {
  let zeroed;
  if (seams?.forceZeroTruncateFail) {
    zeroed = false;
  } else {
    zeroed = await zeroTruncateFd(fd);
  }
  let closeFailed = false;
  try {
    await closeP(fd);
  } catch {
    closeFailed = true;
  }
  // If zero/truncate failed, the staging inode may still hold credential
  // bytes. Surface this as uncertain custody regardless of close outcome.
  if (!zeroed) {
    fail("uncertain custody: zero-truncate failed on staging inode");
  }
  // Zeroing succeeded but close failed — the inode is sanitized but the
  // kernel handle state is ambiguous.
  if (closeFailed) {
    fail("uncertain custody: close failed on staging inode after zero-truncate");
  }
  return -1;
}

// ---------------------------------------------------------------------------
// Internal: create file through pinned directory descriptor (Linux only)
// Uses /proc/self/fd/<dirfd>/<filename> to ensure the file is created in the
// exact directory we pinned, not in a replacement.
// Fail closed on non-Linux or unavailable /proc/self/fd.
//
// `seams` (test-only): if seams.forceProcUnavailable is true, skip the real
// access check and fail as if /proc/self/fd were unavailable.
// ---------------------------------------------------------------------------
async function createViaDirFd(dirFd, fileName, seams) {
  if (process.platform !== "linux") {
    fail("credential file creation via directory fd requires Linux");
  }

  // Test seam: force proc unavailability
  if (seams?.forceProcUnavailable) {
    fail(`/proc/self/fd/${dirFd} unavailable — fail closed`);
  }

  // Verify /proc/self/fd/<dirfd> is accessible
  const procLink = `/proc/self/fd/${dirFd}`;
  try {
    await fsAccess(procLink);
  } catch {
    fail(`/proc/self/fd/${dirFd} unavailable — fail closed`);
  }

  // Linux: use /proc/self/fd/<dirfd>/<filename> — fail closed on any error
  const fdPath = `/proc/self/fd/${dirFd}/${fileName}`;
  const fd = await open(
    fdPath,
    constants.O_RDWR | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0),
    0o600,
  );
  return fd;
}

// ---------------------------------------------------------------------------
// Internal: rename via renameat2 with RENAME_NOREPLACE
// On Linux, calls the dedicated helper which invokes renameat2(2) with
// RENAME_NOREPLACE against the pinned directory fd.  No fallback that
// weakens the no-replace boundary.
// On non-Linux, fails closed.
// ---------------------------------------------------------------------------
async function renameViaDirFd(dirFd, oldName, newName) {
  if (process.platform !== "linux") {
    fail("credential publication via renameat2 requires Linux");
  }

  // Resolve the helper script relative to this module
  const thisDir = dirname(fileURLToPath(import.meta.url));
  const helperPath = join(thisDir, "renameat2_noreplace.py");

  // Map the parent directory fd to a fixed child fd (fd 3) via stdio.
  // The helper expects exactly fd 3 and will fstat it to confirm it is
  // a real directory.  We pass oldName and newName as the only argv args.
  const result = spawnSync("python3", [helperPath, oldName, newName], {
    timeout: 5000,
    stdio: ["pipe", "pipe", "pipe", dirFd],
  });

  if (result.status !== 0) {
    const errText = result.stderr?.toString() ?? "unknown renameat2 error";
    // EEXIST from RENAME_NOREPLACE means target already exists
    if (errText.includes("already exists") || errText.includes("EEXIST")) {
      fail("credential publication failed: target file already exists (RENAME_NOREPLACE)");
    }
    fail(`credential publication failed: ${errText.trim()}`);
  }
}

// ---------------------------------------------------------------------------
// Internal: re-fstat the directory binding to prove it hasn't been swapped
// ---------------------------------------------------------------------------
async function reverifyDirectoryBinding(dirFd, originalStat) {
  const currentStat = await fstatP(dirFd).catch(() => null);
  if (!currentStat) fail("credential directory handle lost during write");
  if (currentStat.dev !== originalStat.dev || currentStat.ino !== originalStat.ino) {
    fail("credential directory was replaced during write operation");
  }
}

// ---------------------------------------------------------------------------
// Internal: generate a unique temp filename in the target directory
// Uses a bounded-random suffix to avoid collisions while being non-predictable.
// ---------------------------------------------------------------------------
function makeTempFileName() {
  const suffix = Array.from(crypto.getRandomValues(new Uint8Array(8)))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return `.credential-staging-${suffix}`;
}

// ---------------------------------------------------------------------------
// Internal: assert inode identity at a security boundary
// Enforces regular file, dev/ino match, exact uid/gid/mode, nlink=1, bounded size.
// ---------------------------------------------------------------------------
function assertInodeIdentity(stat, dev, ino, expectedSize) {
  if (!stat.isFile()) fail("credential target is not a regular file");
  if (stat.dev !== dev || stat.ino !== ino) fail("credential file descriptor inode drift");
  if (stat.nlink !== 1) fail("credential file must have nlink=1");
  if (process.platform !== "win32") {
    if (stat.uid !== process.geteuid()) fail("credential file must be owned by the invoking user");
    if (stat.gid !== process.getegid()) fail("credential file must be owned by the invoking group");
    if ((stat.mode & 0o077) !== 0) fail("credential file must have 0600 permissions");
  }
  if (stat.size !== expectedSize) fail("credential file size mismatch");
}

// ---------------------------------------------------------------------------
// Core implementation — internal, accepts boundary callback + seams (test only)
//
// `seams` (test-only object, not used in production):
//   - seams.forceCloseFail: if true, the close after publication will fail.
//     The file is classified as published-but-unconfirmed and preserved.
//   - seams.forceHardlinkBeforeWrite: if true, a hard link is created on the
//     staging inode before the nlink check. If the hardlink cannot be
//     created the operation fails immediately.
//   - seams.forceProcUnavailable: if true, the /proc/self/fd access check
//     in createViaDirFd is forced to fail.
// ---------------------------------------------------------------------------
export async function writeCredentialExclusiveImpl(dirPath, fileName, secret, onPostDirFsync, seams) {
  const fullPath = resolve(dirPath, fileName);
  if (basename(fullPath) !== fileName) fail("credential filename escaped its custody directory");

  // Validate fileName is in the closed registry
  if (!ALLOWED_FINAL_NAMES.has(fileName)) {
    fail(`filename ${fileName} is not in the closed credential registry`);
  }

  // No symlink, no pre-existing file at the FINAL path
  const existing = await lstat(fullPath).catch(() => null);
  if (existing) {
    if (existing.isSymbolicLink()) fail("credential path is a symlink");
    fail("credential file already exists; create-only policy prevents overwrite");
  }

  // Bind pinned directory handle (O_DIRECTORY|O_NOFOLLOW + fstat binding)
  const dirBinding = await bindDirectoryHandle(dirPath);
  const dirFd = dirBinding.fd;
  const dirOriginalStat = dirBinding.stat;

  const secretBytes = Buffer.from(secret, "utf8");
  let fileFd = -1;
  let tempName = "";
  let published = false;

  try {
    // Create a temp staging file via pinned directory fd
    tempName = makeTempFileName();
    fileFd = await createViaDirFd(dirFd, tempName, seams);

    // Test seam: hard-link the staging inode before nlink check (simulates race)
    if (seams?.forceHardlinkBeforeWrite) {
      const { link } = await import("node:fs/promises");
      const srcPath = `/proc/self/fd/${dirFd}/${tempName}`;
      const tgtPath = `/proc/self/fd/${dirFd}/.hardlink-target-${tempName}`;
      await link(srcPath, tgtPath);
      // If link throws, the operation fails — do not silently continue.
    }

    // SECURITY BOUNDARY #1: immediately after creation — prove inode identity
    const createdStat = await fstatP(fileFd);
    if (!createdStat.isFile()) {
      fileFd = await cleanupCreatedFd(fileFd, seams);
      fail("credential target is not a regular file after creation");
    }
    if (createdStat.nlink !== 1) {
      fileFd = await cleanupCreatedFd(fileFd, seams);
      fail("credential file must have nlink=1 after creation");
    }
    if (process.platform !== "win32") {
      if (createdStat.uid !== process.geteuid()) {
        fileFd = await cleanupCreatedFd(fileFd, seams);
        fail("credential file must be owned by the invoking user after creation");
      }
      if (createdStat.gid !== process.getegid()) {
        fileFd = await cleanupCreatedFd(fileFd, seams);
        fail("credential file must be owned by the invoking group after creation");
      }
      if ((createdStat.mode & 0o077) !== 0) {
        fileFd = await cleanupCreatedFd(fileFd, seams);
        fail("credential file must have 0600 permissions after creation");
      }
    }

    const createdDev = createdStat.dev;
    const createdIno = createdStat.ino;

    // Bounded write-all loop
    await writeAll(fileFd, secretBytes);
    await fdatasyncP(fileFd);

    // SECURITY BOUNDARY #2: after write — re-verify inode identity
    const postWriteStat = await fstatP(fileFd);
    assertInodeIdentity(postWriteStat, createdDev, createdIno, secretBytes.byteLength);

    // Bounded exact read-back at position offsets
    const readBuffer = await readAllExact(fileFd, postWriteStat.size);
    if (!readBuffer.equals(secretBytes)) {
      fileFd = await cleanupCreatedFd(fileFd, seams);
      fail("credential file content mismatch after write");
    }
    // Zero the read buffer immediately
    readBuffer.fill(0);

    // Re-verify directory binding before publication
    await reverifyDirectoryBinding(dirFd, dirOriginalStat);

    // Fsync the custody directory (via pinned handle)
    await fsyncP(dirFd);

    // Deterministic race seam (test-only, before publication)
    if (typeof onPostDirFsync === "function") {
      await onPostDirFsync();
    }

    // SECURITY BOUNDARY #3: final check on staging file before publication
    const finalFdStat = await fstatP(fileFd);
    assertInodeIdentity(finalFdStat, createdDev, createdIno, secretBytes.byteLength);

    // Publish: atomic no-replace rename within pinned directory
    await renameViaDirFd(dirFd, tempName, fileName);
    published = true;

    // Close staging fd AFTER successful publication but BEFORE verified=true
    try {
      // Test seam: force close failure after publication
      if (seams?.forceCloseFail) {
        throw new Error("test seam: forced close failure after publication");
      }
      await closeP(fileFd);
    } catch {
      // Close error after successful rename: the file is on disk at the final
      // path but the fd close failure is a durable ambiguity. The file is
      // classified as published-but-unconfirmed and preserved for reconciliation.
      fail("credential file descriptor close failed after publication: published-but-unconfirmed");
    }
    fileFd = -1;

    // Post-publish verification: pathname matches fd-bound identity
    const finalStat = await lstat(fullPath);
    if (!finalStat.isFile()) fail("credential file is not a regular file after publish");
    if (finalStat.dev !== createdDev || finalStat.ino !== createdIno) {
      fail("credential file was replaced after publish");
    }
    if (finalStat.nlink !== 1) fail("credential file must be a single-link regular file after publish");
    if (process.platform !== "win32") {
      if (finalStat.uid !== process.geteuid()) fail("credential file owner mismatch after publish");
      if (finalStat.gid !== process.getegid()) fail("credential file group mismatch after publish");
      if ((finalStat.mode & 0o777) !== 0o600) fail("credential file mode must be exactly 0600 after publish");
    }
    if (finalStat.size !== secretBytes.byteLength) fail("credential file size mismatch after publish");

    // Cross-check: fd-bound stat must match pathname stat
    if (finalFdStat.dev !== finalStat.dev || finalFdStat.ino !== finalStat.ino) {
      fail("credential file fd and pathname dev/ino mismatch after publish");
    }

    // Final directory fsync after rename
    await fsyncP(dirFd);

  } catch (err) {
    // Pre-publication failure: sanitize the staging inode via held fd
    if (!published && fileFd >= 0) {
      fileFd = await cleanupCreatedFd(fileFd, seams);
    }
    throw err;
  } finally {
    try { secretBytes.fill(0); } catch { /* best effort */ }

    // Close any still-open file fd (shouldn't happen on success, but safe)
    if (fileFd >= 0) {
      try { await closeP(fileFd); } catch { /* best effort */ }
      fileFd = -1;
    }

    // Close directory handle
    try { await closeP(dirFd); } catch { /* best effort */ }
  }
}

// ---------------------------------------------------------------------------
// Production facade — exactly three arguments, no injection surface
// ---------------------------------------------------------------------------
export async function writeCredentialExclusive(dirPath, fileName, secret) {
  return writeCredentialExclusiveImpl(dirPath, fileName, secret);
}

// ---------------------------------------------------------------------------
// Credential validation
// ---------------------------------------------------------------------------
export function validateCredentialSecret(value) {
  if (typeof value !== "string") fail("credential must be a string");
  if (value.length < 20) fail("credential is below the minimum length");
  if (value.length > 8192) fail("credential exceeds the maximum length");
  if (/[^!-~]/u.test(value)) fail("credential must contain only printable graphic ASCII characters");
}

// ---------------------------------------------------------------------------
// Pipe reading: read all bytes from a non-seekable fd
// Exported for use by the ingress adapter.
// ---------------------------------------------------------------------------
export { readAllFromFd };

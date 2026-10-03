import { constants } from "node:fs";
import { link, lstat, open, unlink } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

// Shared owner-private secure file primitives for the campaign child launcher
// and supervisor. These guarantee create-only no-clobber durable publication and
// owner-private bounded reads that prove exact owner, private mode, and
// nlink === 1 from the already-open handle metadata (no path race).
export class WorkMaintenanceSecureFileError extends Error {}

export async function fsyncDirectory(directory) {
  if (process.platform === "win32") return;
  let handle;
  try { handle = await open(directory, constants.O_RDONLY | (constants.O_DIRECTORY ?? 0)); await handle.sync(); }
  finally { if (handle) await handle.close(); }
}

export function tempPathFor(targetPath) {
  return join(dirname(targetPath), `.${resolve(targetPath).split(/[\\/]/u).pop()}.tmp-${process.pid}-${Date.now()}-${Math.floor(Math.random() * 0xffffffff).toString(16)}`);
}

export async function lstatExact(path) {
  try { return await lstat(path); } catch (error) { if (error?.code === "ENOENT") return null; throw error; }
}

// Create-only no-clobber durable publication. A hardened same-directory
// temporary file is written, then linked to the target with link() (which fails
// with EEXIST if the target already exists as a regular file, symlink, hardlink,
// or a concurrent publication race), the exact temporary path is unlinked, the
// directory is fsynced, and the exact target is re-proved to be a real
// owner-private singular file with nlink === 1. An existing target is never
// overwritten or replaced.
export async function writeOwnerPrivateCreateNoClobber(targetPath, value, expectedOwnerUid) {
  const directory = dirname(targetPath);
  const tempPath = tempPathFor(targetPath);
  let handle;
  try {
    handle = await open(tempPath, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_CLOEXEC ?? 0), 0o600);
    const bytes = Buffer.isBuffer(value)
      ? value
      : Buffer.from(typeof value === "string" ? value : `${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    if (handle) await handle.close();
  }
  try {
    await link(tempPath, targetPath);
  } catch (error) {
    await unlink(tempPath).catch(() => {});
    if (error?.code === "EEXIST") {
      throw new WorkMaintenanceSecureFileError(`${targetPath} already exists; refusing to replace an existing target`);
    }
    throw error;
  }
  await unlink(tempPath).catch(() => {});
  await fsyncDirectory(directory);
  if (process.platform === "win32") return;
  const info = await lstatExact(targetPath);
  if (!info || !info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0) {
    throw new WorkMaintenanceSecureFileError(`${targetPath} is not an owner-private singular durable artifact`);
  }
}

// Owner-private bounded read. Uses the already-open handle metadata to avoid a
// path race: proves no-follow, real regular file, bounded size, exact owner,
// private mode, and nlink === 1.
export async function readOwnerPrivateBoundedFile(path, maxBytes, label, expectedOwnerUid) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new WorkMaintenanceSecureFileError("maximum file size is invalid");
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0)); }
  catch { throw new WorkMaintenanceSecureFileError(`${label} could not be opened safely`); }
  try {
    const details = await handle.stat();
    if (!details.isFile() || details.isSymbolicLink() || details.size < 0 || details.size > maxBytes) {
      throw new WorkMaintenanceSecureFileError(`${label} is not a bounded singular real file`);
    }
    if (process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0 || details.nlink !== 1)) {
      throw new WorkMaintenanceSecureFileError(`${label} is not owner-private and singular`);
    }
    const chunks = [];
    let total = 0;
    while (total <= maxBytes) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maxBytes + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      chunks.push(buffer.subarray(0, bytesRead));
      total += bytesRead;
    }
    if (total > maxBytes) throw new WorkMaintenanceSecureFileError(`${label} exceeded its size limit`);
    return { bytes: Buffer.concat(chunks, total), details };
  } finally {
    await handle.close();
  }
}

import { constants } from "node:fs";
import { open } from "node:fs/promises";

// INTERNAL descriptor-bound workspace effect used by the production operational
// runtime and exposed to tests ONLY through the test-support module. It is never
// part of the public production export surface: the production module imports it
// here but does not re-export it, so no traversal-capable seam is exported from
// production source.
export class CapabilityOperationalV2Error extends Error {}
export function fail(message) { throw new CapabilityOperationalV2Error(message); }

// Descriptor-bound create-only effect. Instead of validating the parent path with
// lstat/realpath and then creating through that same path (which a same-UID actor
// could swap between check and effect), this opens the parent directory with
// O_DIRECTORY|O_NOFOLLOW and holds the descriptor open, binds its fstat to the
// pre-open path identity (dev,ino), creates the one filename relative to that held
// descriptor through a carefully validated Linux /proc/self/fd/<fd>/<name> magic
// link, then re-proves the same inode. A swapped parent can never redirect the
// write because the descriptor pins the directory inode for the whole
// check-and-effect window.
//
// `expectedIdentity` is the trusted (dev,ino) captured before this call; for the
// single-level production claim it is the validated workspace root identity, so a
// swap of the root itself is rejected.
export async function openPinnedDirectory(parentPath, expectedIdentity, label) {
  let handle;
  try {
    handle = await open(parentPath, constants.O_RDONLY | (constants.O_DIRECTORY ?? 0) | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0));
  } catch (error) {
    fail(`${label} could not be pinned as a real directory`);
  }
  try {
    const info = await handle.stat();
    if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
    if (expectedIdentity && (info.dev !== expectedIdentity.dev || info.ino !== expectedIdentity.ino)) fail(`${label} changed identity between validation and open`);
    return handle;
  } catch (error) {
    await handle.close().catch(() => {});
    throw error;
  }
}

// Create one bounded file relative to the pinned parent directory descriptor.
// Enforces a single create-only filename (no path separators, no traversal) so
// this can never be used as a traversal-capable seam.
export async function createFileInWorkspace(parentPath, expectedIdentity, targetName, content, maxBytes) {
  if (typeof content !== "string" || Buffer.byteLength(content, "utf8") > maxBytes) fail("operational v2 workspace write content is invalid or exceeds its ceiling");
  if (typeof targetName !== "string" || targetName.length === 0 || targetName.length > 255 || targetName === "." || targetName === ".." ||
      targetName.includes("/") || targetName.includes("\\") || targetName.includes("\0")) fail("operational v2 workspace write target is not a single filename component");
  const dirHandle = await openPinnedDirectory(parentPath, expectedIdentity, "operational v2 workspace parent");
  try {
    const target = `/proc/self/fd/${dirHandle.fd}/${targetName}`;
    let written;
    try {
      written = await open(target, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0), 0o600);
    } catch (error) {
      if (error?.code === "EEXIST" || error?.code === "ELOOP" || error?.code === "ENOTDIR") fail("operational v2 workspace write target already exists (create-only, no overwrite)");
      throw error;
    }
    let writtenStat;
    try {
      await written.writeFile(content);
      await written.sync();
      writtenStat = await written.stat();
    } finally {
      await written.close();
    }
    // The effect must land in the exact pinned directory: fsync it, then re-open
    // the filename relative to the same held descriptor and prove the SAME inode
    // and a singular owner-private real file (exact read-back/identity).
    await dirHandle.sync();
    // The held parent dirfd, O_NOFOLLOW, and exact written-inode comparison below make a
    // replacement fail closed.
    // codeql[js/file-system-race]
    const check = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0));
    let checkStat;
    try {
      checkStat = await check.stat();
      const read = await check.readFile();
      if (Buffer.compare(Buffer.from(content, "utf8"), read) !== 0) fail("operational v2 workspace write content read-back mismatch");
    } finally {
      await check.close();
    }
    if (checkStat.dev !== writtenStat.dev || checkStat.ino !== writtenStat.ino) fail("operational v2 workspace write inode identity mismatch");
    if (!checkStat.isFile() || checkStat.isSymbolicLink() || checkStat.nlink !== 1) fail("operational v2 workspace write is not a singular real file");
    if (process.platform !== "win32" && (checkStat.uid !== process.geteuid() || (checkStat.mode & 0o077) !== 0)) fail("operational v2 workspace write is not owner-private");
    return { stat: checkStat };
  } finally {
    await dirHandle.close();
  }
}

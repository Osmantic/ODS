import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { constants } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { isAbsolute, posix, relative, resolve, sep } from "node:path";

import { canonical, validateWorkModelArtifactManifest } from "../../scripts/lib/work-contract.mjs";

const MAX_FILES = 100000;
const MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024 * 1024;
const authority = Object.freeze({ grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.";

export class WorkModelArtifactError extends Error {}
function fail(message) { throw new WorkModelArtifactError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function sha(value) { return createHash("sha256").update(value).digest("hex"); }

function safeRelative(root, path) {
  const value = relative(root, path).split(sep).join("/");
  if (!value || value.startsWith("/") || value.split("/").some((part) => !/^[A-Za-z0-9._+@=-]+$/u.test(part) || part === "." || part === "..") || posix.normalize(value) !== value) fail("model artifact contains an unsafe relative path");
  return value;
}

function stableStat(before, after) {
  return before.dev === after.dev && before.ino === after.ino && before.size === after.size
    && before.mtimeNs === after.mtimeNs && before.ctimeNs === after.ctimeNs;
}

function checkOwnership(info, expectedOwnerUid, label) {
  if (process.platform === "win32") return;
  if (![0n, BigInt(expectedOwnerUid)].includes(info.uid) || (info.mode & 0o022n) !== 0n) fail(`${label} is not owner/root-held and non-writable by group or others`);
}

function checkReader(info, readerUid, readerGid, directory, label) {
  if (process.platform === "win32" || readerUid === null || readerGid === null) return;
  const read = info.uid === BigInt(readerUid) ? 0o400n : info.gid === BigInt(readerGid) ? 0o040n : 0o004n;
  const search = info.uid === BigInt(readerUid) ? 0o100n : info.gid === BigInt(readerGid) ? 0o010n : 0o001n;
  if ((info.mode & read) === 0n || directory && (info.mode & search) === 0n) fail(`${label} is not readable by the configured container identity`);
}

async function hashRegular(path, expectedOwnerUid, readerUid, readerGid) {
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch { fail("model artifact file could not be opened without following links"); }
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n || before.size < 0n || before.size > BigInt(MAX_TOTAL_BYTES)) fail("model artifact entry is not one single-link bounded regular file");
    checkOwnership(before, expectedOwnerUid, "model artifact file");
    checkReader(before, readerUid, readerGid, false, "model artifact file");
    const digest = createHash("sha256");
    for await (const chunk of createReadStream(path, { fd: handle.fd, autoClose: false, start: 0 })) digest.update(chunk);
    const after = await handle.stat({ bigint: true });
    if (!stableStat(before, after)) fail("model artifact file changed while it was measured");
    return { bytes: Number(before.size), sha256: digest.digest("hex"), identity: before };
  } finally { await handle.close(); }
}

async function inspectRoot(sourcePath, expectedOwnerUid) {
  if (typeof sourcePath !== "string" || !isAbsolute(sourcePath) || resolve(sourcePath) !== sourcePath) fail("model artifact source must be an absolute canonical path");
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(sourcePath), lstat(sourcePath, { bigint: true })]); }
  catch { fail("model artifact source is unavailable"); }
  if (!samePath(actual, sourcePath) || info.isSymbolicLink()) fail("model artifact source must be real and cannot be a link");
  checkOwnership(info, expectedOwnerUid, "model artifact source");
  return info;
}

export async function buildModelArtifactManifest({ sourcePath, kind, expectedOwnerUid = process.geteuid?.() ?? 0, readerUid = null, readerGid = null }) {
  if (!["file", "directory"].includes(kind) || !Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0 || ![readerUid, readerGid].every((value) => value === null || Number.isSafeInteger(value) && value >= 0)) fail("model artifact measurement request is invalid");
  if ((readerUid === null) !== (readerGid === null)) fail("model artifact reader identity is incomplete");
  const root = await inspectRoot(sourcePath, expectedOwnerUid);
  const files = [], measuredFiles = [], measuredDirectories = [];
  if (kind === "file") {
    if (!root.isFile()) fail("single-file model source is not a regular file");
    checkReader(root, readerUid, readerGid, false, "model artifact source");
    const measured = await hashRegular(sourcePath, expectedOwnerUid, readerUid, readerGid);
    files.push({ relativePath: "model", bytes: measured.bytes, sha256: measured.sha256 });
    measuredFiles.push({ path: sourcePath, identity: measured.identity });
  } else {
    if (!root.isDirectory()) fail("directory model source is not a directory");
    const pending = [sourcePath];
    while (pending.length) {
      const directory = pending.pop();
      const directoryInfo = await lstat(directory, { bigint: true });
      if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink()) fail("model artifact directory changed or contains a link");
      checkOwnership(directoryInfo, expectedOwnerUid, "model artifact directory");
      checkReader(directoryInfo, readerUid, readerGid, true, "model artifact directory");
      const actual = await realpath(directory);
      if (!samePath(actual, directory) || !(samePath(actual, sourcePath) || actual.startsWith(`${sourcePath}${sep}`))) fail("model artifact directory escapes its selected root");
      const entries = await readdir(directory, { withFileTypes: true });
      entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
      measuredDirectories.push({ path: directory, identity: directoryInfo, entries: entries.map((entry) => `${entry.name}:${entry.isDirectory() ? "d" : entry.isFile() ? "f" : entry.isSymbolicLink() ? "l" : "o"}`) });
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const entry = entries[index];
        const path = resolve(directory, entry.name);
        safeRelative(sourcePath, path);
        if (entry.isSymbolicLink()) fail("model artifact directory contains a symbolic link");
        if (entry.isDirectory()) pending.push(path);
        else if (entry.isFile()) {
          if (files.length >= MAX_FILES) fail("model artifact exceeds the file-count ceiling");
          const measured = await hashRegular(path, expectedOwnerUid, readerUid, readerGid);
          files.push({ relativePath: safeRelative(sourcePath, path), bytes: measured.bytes, sha256: measured.sha256 });
          measuredFiles.push({ path, identity: measured.identity });
        } else fail("model artifact directory contains a non-file entry");
      }
    }
    files.sort((left, right) => left.relativePath.localeCompare(right.relativePath, "en"));
    if (files.length === 0) fail("model artifact directory is empty");
  }
  for (const measured of measuredFiles) {
    let info, actual;
    try { [info, actual] = await Promise.all([lstat(measured.path, { bigint: true }), realpath(measured.path)]); }
    catch { fail("model artifact changed after its bytes were measured"); }
    if (!info.isFile() || info.isSymbolicLink() || !stableStat(measured.identity, info) || !samePath(actual, measured.path)) fail("model artifact changed after its bytes were measured");
  }
  for (const measured of measuredDirectories) {
    let info, actual, entries;
    try { [info, actual, entries] = await Promise.all([lstat(measured.path, { bigint: true }), realpath(measured.path), readdir(measured.path, { withFileTypes: true })]); }
    catch { fail("model artifact directory changed while its tree was measured"); }
    entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
    const shape = entries.map((entry) => `${entry.name}:${entry.isDirectory() ? "d" : entry.isFile() ? "f" : entry.isSymbolicLink() ? "l" : "o"}`);
    if (!info.isDirectory() || info.isSymbolicLink() || !stableStat(measured.identity, info) || !samePath(actual, measured.path) || canonical(shape) !== canonical(measured.entries)) fail("model artifact directory changed while its tree was measured");
  }
  const totalBytes = files.reduce((total, file) => total + file.bytes, 0);
  if (!Number.isSafeInteger(totalBytes) || totalBytes < 1 || totalBytes > MAX_TOTAL_BYTES) fail("model artifact total byte size is invalid");
  const artifactSha256 = kind === "file" ? files[0].sha256 : sha(canonical({ schemaVersion: 1, kind, files }));
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1,
    kind, artifactSha256, fileCount: files.length, totalBytes, files,
    authority: { ...authority }, boundary,
  };
  const errors = validateWorkModelArtifactManifest(manifest);
  if (errors.length) fail(`model artifact manifest is invalid: ${errors[0]}`);
  return Object.freeze(manifest);
}

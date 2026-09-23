#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  chmod, copyFile, link, lstat, mkdir, open, readdir, rename, rm, rmdir, unlink,
} from "node:fs/promises";
import { basename, dirname, join, posix, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const SEGMENT_RE = /^[A-Za-z0-9._@-]{1,255}$/;
const MAX_ENTRIES = 100000;
const MAX_PATCH_BYTES = 256 * 1024 * 1024;
const READ_BUFFER_BYTES = 1024 * 1024;
const PATCH_BOUNDARY = "Local deterministic patch artifact only. Applying it requires a separate verified candidate and explicit operator action.";

export class BuilderVolumeError extends Error {}

function fail(message) {
  throw new BuilderVolumeError(message);
}

function boundedInteger(value, minimum, maximum, label) {
  const parsed = typeof value === "string" && /^(?:0|[1-9][0-9]*)$/.test(value) ? Number(value) : value;
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) fail(`${label} is invalid`);
  return parsed;
}

function safeRoot(path, label) {
  if (typeof path !== "string" || !posix.isAbsolute(path) || posix.normalize(path) !== path || path.includes("//") || !/^\/[A-Za-z0-9._/-]+$/.test(path)) {
    fail(`${label} is not a canonical container path`);
  }
  return path;
}

function safeRelative(root, path) {
  const value = relative(root, path).split(sep).join("/");
  if (!value || value.startsWith("../") || posix.isAbsolute(value) || value.split("/").some((segment) => !SEGMENT_RE.test(segment) || segment === "." || segment === "..")) {
    fail("Builder workspace contains an unsafe path");
  }
  return value;
}

async function checkedDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  return info;
}

async function checkedPrivateDirectory(path, label, empty = false) {
  const info = await checkedDirectory(path, label);
  if (process.platform !== "win32") {
    if ((info.mode & 0o077) !== 0) fail(`${label} is not private`);
    if (typeof process.getuid === "function" && info.uid !== process.getuid()) fail(`${label} has the wrong owner`);
  }
  if (empty && (await readdir(path)).length !== 0) fail(`${label} is not empty`);
  return info;
}

function rootsOverlap(left, right) {
  return left === right || left.startsWith(`${right}${sep}`) || right.startsWith(`${left}${sep}`);
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) {
    fail(`${label} shape is invalid`);
  }
}

function safePatchPath(value) {
  if (typeof value !== "string" || !value || value.length > 4096 || posix.isAbsolute(value) || posix.normalize(value) !== value) fail("Builder patch contains an unsafe path");
  const segments = value.split("/");
  if (segments.some((segment) => !SEGMENT_RE.test(segment) || segment === "." || segment === "..")) fail("Builder patch contains an unsafe path");
  return value;
}

function decodedContent(change) {
  if (typeof change.contentBase64 !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(change.contentBase64)) {
    fail("Builder patch content is not canonical base64");
  }
  const content = Buffer.from(change.contentBase64, "base64");
  if (content.toString("base64") !== change.contentBase64 || content.length !== change.afterBytes || createHash("sha256").update(content).digest("hex") !== change.afterSha256) {
    fail("Builder patch content differs from its declared digest");
  }
  return content;
}

function protectedPath(path, prefixes) {
  return prefixes.some((prefix) => {
    const normalized = prefix.endsWith("/") ? prefix.slice(0, -1) : prefix;
    return path === normalized || path.startsWith(`${normalized}/`);
  });
}

export function validateBuilderPatchForApplication(patch, binding, limits = {}) {
  exactKeys(patch, ["schemaVersion", "format", "jobId", "claimId", "planSha256", "workspaceSha256", "changes", "summary", "authority", "boundary"], "Builder patch");
  if (
    patch.schemaVersion !== 1 || patch.format !== "pixel-file-patch-v1" || patch.jobId !== binding?.jobId
    || patch.claimId !== binding?.claimId || patch.planSha256 !== binding?.planSha256 || patch.workspaceSha256 !== binding?.workspaceSha256
  ) fail("Builder patch binding is invalid");
  exactKeys(patch.authority, ["sourceMutation", "merge", "deploy", "externalEffects"], "Builder patch authority");
  if (Object.values(patch.authority).some((value) => value !== false) || patch.boundary !== PATCH_BOUNDARY) fail("Builder patch carries unsupported authority");
  exactKeys(patch.summary, ["added", "modified", "deleted", "beforeBytes", "afterBytes"], "Builder patch summary");
  for (const field of ["added", "modified", "deleted", "beforeBytes", "afterBytes"]) {
    if (!Number.isSafeInteger(patch.summary[field]) || patch.summary[field] < 0 || patch.summary[field] > 1099511627776) fail("Builder patch summary is invalid");
  }
  if (!Array.isArray(patch.changes) || patch.changes.length > MAX_ENTRIES) fail("Builder patch change count is invalid");
  const maxTreeBytes = boundedInteger(limits.maxTreeBytes ?? 1099511627776, 1, 1099511627776, "Builder tree byte ceiling");
  const maxArtifactBytes = boundedInteger(limits.maxArtifactBytes ?? MAX_PATCH_BYTES, 1, MAX_PATCH_BYTES, "Builder artifact byte ceiling");
  const immutablePathPrefixes = limits.immutablePathPrefixes ?? [];
  if (!Array.isArray(immutablePathPrefixes) || immutablePathPrefixes.length > 64) fail("Builder immutable path policy is invalid");
  for (const prefix of immutablePathPrefixes) safePatchPath(prefix.endsWith("/") ? prefix.slice(0, -1) : prefix);
  let previous = null;
  const counts = { add: 0, modify: 0, delete: 0 };
  for (const change of patch.changes) {
    exactKeys(change, ["path", "operation", "beforeSha256", "beforeBytes", "afterSha256", "afterBytes", "contentBase64"], "Builder patch change");
    const path = safePatchPath(change.path);
    if (previous !== null && path <= previous) fail("Builder patch changes are not uniquely sorted");
    previous = path;
    if (!Object.hasOwn(counts, change.operation)) fail("Builder patch operation is invalid");
    counts[change.operation] += 1;
    if (protectedPath(path, immutablePathPrefixes)) fail("Builder patch changes an immutable path");
    const hasBefore = SHA_RE.test(change.beforeSha256 ?? "") && Number.isSafeInteger(change.beforeBytes) && change.beforeBytes >= 0 && change.beforeBytes <= maxTreeBytes;
    const hasAfter = SHA_RE.test(change.afterSha256 ?? "") && Number.isSafeInteger(change.afterBytes) && change.afterBytes >= 0 && change.afterBytes <= maxTreeBytes;
    if (
      (change.operation === "add" && (change.beforeSha256 !== null || change.beforeBytes !== null || !hasAfter))
      || (change.operation === "modify" && (!hasBefore || !hasAfter))
      || (change.operation === "delete" && (!hasBefore || change.afterSha256 !== null || change.afterBytes !== null || change.contentBase64 !== null))
    ) fail("Builder patch change state is invalid");
    if (change.operation !== "delete") decodedContent(change);
  }
  if (patch.summary.added !== counts.add || patch.summary.modified !== counts.modify || patch.summary.deleted !== counts.delete) fail("Builder patch summary differs from its changes");
  if (patch.summary.beforeBytes > maxTreeBytes || patch.summary.afterBytes > maxTreeBytes) fail("Builder patch tree exceeds its byte ceiling");
  return { maxTreeBytes, maxArtifactBytes, immutablePathPrefixes: [...immutablePathPrefixes] };
}

async function hashOpenedFile(handle) {
  const digest = createHash("sha256");
  const buffer = Buffer.allocUnsafe(READ_BUFFER_BYTES);
  while (true) {
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
    if (bytesRead === 0) break;
    digest.update(buffer.subarray(0, bytesRead));
  }
  return digest.digest("hex");
}

async function walk(root, limits) {
  await checkedDirectory(root, "Builder tree root");
  const files = new Map();
  let entries = 0;
  let totalBytes = 0;
  async function visit(directory) {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const child of children) {
      entries += 1;
      if (entries > MAX_ENTRIES) fail("Builder workspace exceeds its entry ceiling");
      const path = join(directory, child.name);
      const name = safeRelative(root, path);
      const info = await lstat(path);
      if (info.isSymbolicLink()) fail("Builder workspace contains a symbolic link");
      if (info.isDirectory()) {
        await visit(path);
        continue;
      }
      if (!info.isFile() || info.nlink !== 1) fail("Builder workspace contains a special or hard-linked file");
      if (info.size > limits.maxFileBytes) fail("Builder workspace contains an oversized file");
      totalBytes += info.size;
      if (totalBytes > limits.maxTreeBytes) fail("Builder workspace exceeds its disk budget");
      const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
      try {
        const opened = await handle.stat();
        if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail("Builder file changed during export");
        const sha256 = await hashOpenedFile(handle);
        const after = await handle.stat();
        if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs) fail("Builder file changed during export");
        files.set(name, { bytes: info.size, sha256, path });
      } finally {
        await handle.close();
      }
    }
  }
  await visit(root);
  return { files, entries, totalBytes };
}

export async function initializeBuilderVolume(sourceRoot, workspaceRoot, options = {}) {
  const source = resolve(sourceRoot);
  const workspace = resolve(workspaceRoot);
  if (source === workspace || workspace.startsWith(`${source}${sep}`) || source.startsWith(`${workspace}${sep}`)) fail("Builder source and workspace overlap");
  await checkedDirectory(source, "normalized source");
  const workspaceInfo = await checkedPrivateDirectory(workspace, "disposable workspace", true);
  const maximumBytes = boundedInteger(options.maxBytes ?? 1099511627776, 1, 1099511627776, "Builder initialization byte ceiling");
  let copiedBytes = 0;
  let copiedEntries = 0;
  async function copyDirectory(from, to) {
    const children = await readdir(from, { withFileTypes: true });
    children.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const child of children) {
      copiedEntries += 1;
      if (copiedEntries > MAX_ENTRIES || !SEGMENT_RE.test(child.name) || child.name === "." || child.name === "..") fail("normalized source has an unsafe entry");
      const sourcePath = join(from, child.name);
      const destinationPath = join(to, child.name);
      const info = await lstat(sourcePath);
      if (info.isSymbolicLink()) fail("normalized source contains a symbolic link");
      if (info.isDirectory()) {
        await mkdir(destinationPath, { mode: 0o700 });
        await copyDirectory(sourcePath, destinationPath);
      } else if (info.isFile() && info.nlink === 1) {
        copiedBytes += info.size;
        if (copiedBytes > maximumBytes) fail("normalized source exceeds the Builder volume budget");
        await copyFile(sourcePath, destinationPath, constants.COPYFILE_EXCL);
        await chmod(destinationPath, 0o600);
      } else fail("normalized source contains a special or hard-linked file");
    }
  }
  try {
    await copyDirectory(source, workspace);
  } catch (error) {
    const current = await lstat(workspace).catch(() => null);
    if (!current?.isDirectory() || current.isSymbolicLink() || current.dev !== workspaceInfo.dev || current.ino !== workspaceInfo.ino) {
      fail("Builder initialization failed and workspace cleanup could not be verified");
    }
    await Promise.all((await readdir(workspace)).map((name) => rm(join(workspace, name), { recursive: true, force: true })));
    throw error;
  }
  return { copiedBytes, copiedEntries };
}

async function readValidatedContent(record) {
  const before = await lstat(record.path).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size !== record.bytes) fail("Builder file changed during export");
  const handle = await open(record.path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== record.bytes) fail("Builder file changed during export");
    const content = await handle.readFile();
    const after = await handle.stat();
    const digest = createHash("sha256").update(content).digest("hex");
    if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs || digest !== record.sha256) fail("Builder file changed during export");
    return content;
  } finally {
    await handle.close();
  }
}

function sameInventory(left, right) {
  if (left.totalBytes !== right.totalBytes || left.files.size !== right.files.size) return false;
  for (const [name, record] of left.files) {
    const candidate = right.files.get(name);
    if (!candidate || candidate.bytes !== record.bytes || candidate.sha256 !== record.sha256) return false;
  }
  return true;
}

async function atomicPrivateJson(path, serialized) {
  const directory = dirname(path);
  await checkedDirectory(directory, "Builder output directory");
  if (await lstat(path).catch(() => null)) fail("Builder patch output already exists");
  const temporary = join(directory, `.builder-patch-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(serialized, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, path);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directoryHandle = await open(directory, constants.O_RDONLY);
      try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}

export async function exportBuilderPatch(sourceRoot, workspaceRoot, outputRoot, binding, limits) {
  const source = resolve(sourceRoot);
  const workspace = resolve(workspaceRoot);
  const output = resolve(outputRoot);
  if (rootsOverlap(source, workspace) || rootsOverlap(source, output) || rootsOverlap(workspace, output)) fail("Builder export roots overlap");
  await checkedPrivateDirectory(output, "Builder output directory", true);
  if (!JOB_RE.test(binding?.jobId ?? "") || !CLAIM_RE.test(binding?.claimId ?? "") || !SHA_RE.test(binding?.planSha256 ?? "") || !SHA_RE.test(binding?.workspaceSha256 ?? "")) {
    fail("Builder export binding is invalid");
  }
  const maxTreeBytes = boundedInteger(limits?.maxTreeBytes, 1, 1099511627776, "Builder tree byte ceiling");
  const maxArtifactBytes = boundedInteger(limits?.maxArtifactBytes, 1, MAX_PATCH_BYTES, "Builder artifact byte ceiling");
  const maxFileBytes = maxTreeBytes;
  const before = await walk(source, { maxTreeBytes, maxFileBytes });
  const after = await walk(workspace, { maxTreeBytes, maxFileBytes });
  const names = [...new Set([...before.files.keys(), ...after.files.keys()])].sort();
  const changes = [];
  for (const path of names) {
    const left = before.files.get(path);
    const right = after.files.get(path);
    if (left?.sha256 === right?.sha256 && left?.bytes === right?.bytes) continue;
    let contentBase64 = null;
    if (right) {
      const anticipatedBytes = 4 * Math.ceil(right.bytes / 3);
      if (anticipatedBytes > maxArtifactBytes) fail("Builder patch exceeds its artifact budget");
      contentBase64 = (await readValidatedContent(right)).toString("base64");
    }
    changes.push({
      path,
      operation: left ? (right ? "modify" : "delete") : "add",
      beforeSha256: left?.sha256 ?? null,
      beforeBytes: left?.bytes ?? null,
      afterSha256: right?.sha256 ?? null,
      afterBytes: right?.bytes ?? null,
      contentBase64,
    });
  }
  const patch = {
    schemaVersion: 1,
    format: "pixel-file-patch-v1",
    jobId: binding.jobId,
    claimId: binding.claimId,
    planSha256: binding.planSha256,
    workspaceSha256: binding.workspaceSha256,
    changes,
    summary: {
      added: changes.filter((change) => change.operation === "add").length,
      modified: changes.filter((change) => change.operation === "modify").length,
      deleted: changes.filter((change) => change.operation === "delete").length,
      beforeBytes: before.totalBytes,
      afterBytes: after.totalBytes,
    },
    authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
    boundary: PATCH_BOUNDARY,
  };
  const finalBefore = await walk(source, { maxTreeBytes, maxFileBytes });
  const finalAfter = await walk(workspace, { maxTreeBytes, maxFileBytes });
  if (!sameInventory(before, finalBefore) || !sameInventory(after, finalAfter)) fail("Builder tree changed during export");
  const serialized = `${JSON.stringify(patch, null, 2)}\n`;
  const encodedBytes = Buffer.byteLength(serialized, "utf8");
  if (encodedBytes > maxArtifactBytes) fail("Builder patch exceeds its artifact budget");
  const path = join(output, "builder-patch.json");
  if (basename(path) !== "builder-patch.json") fail("Builder output path is invalid");
  await atomicPrivateJson(path, serialized);
  return { path, bytes: encodedBytes, sha256: createHash("sha256").update(serialized).digest("hex"), changes: changes.length };
}

async function currentFile(path, expectedBytes, expectedSha256) {
  const before = await lstat(path).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size !== expectedBytes) fail("Builder patch preimage differs from normalized source");
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) fail("Builder patch preimage changed during validation");
    const digest = await hashOpenedFile(handle);
    const after = await handle.stat();
    if (digest !== expectedSha256 || after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs) {
      fail("Builder patch preimage differs from normalized source");
    }
  } finally {
    await handle.close();
  }
}

async function ensureParent(root, relativePath) {
  const segments = relativePath.split("/").slice(0, -1);
  let current = root;
  for (const segment of segments) {
    current = join(current, segment);
    const info = await lstat(current).catch(() => null);
    if (info) {
      if (!info.isDirectory() || info.isSymbolicLink()) fail("Builder patch parent is not a real directory");
    } else {
      await mkdir(current, { mode: 0o700 });
    }
  }
}

async function removeEmptyParents(root, path) {
  let current = dirname(path);
  while (current !== root && current.startsWith(`${root}${sep}`)) {
    try { await rmdir(current); } catch (error) {
      if (["ENOTEMPTY", "EEXIST"].includes(error?.code)) return;
      throw error;
    }
    current = dirname(current);
  }
}

async function removeEmptyTree(path) {
  const info = await lstat(path).catch(() => null);
  if (!info) return;
  if (!info.isDirectory() || info.isSymbolicLink()) fail("Builder patch directory replacement is unsafe");
  for (const name of await readdir(path)) await removeEmptyTree(join(path, name));
  await rmdir(path);
}

async function writePatchedFile(path, content, replace) {
  const directory = dirname(path);
  const temporary = join(directory, `.pixel-verify-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(content);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    if (replace) await rename(temporary, path);
    else {
      await link(temporary, path);
      await unlink(temporary);
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}

export async function applyBuilderPatch(sourceRoot, workspaceRoot, patch, binding, limits = {}) {
  const source = resolve(sourceRoot);
  const workspace = resolve(workspaceRoot);
  if (rootsOverlap(source, workspace)) fail("Builder source and verification workspace overlap");
  const checkedLimits = validateBuilderPatchForApplication(patch, binding, limits);
  await initializeBuilderVolume(source, workspace, { maxBytes: checkedLimits.maxTreeBytes });
  const before = await walk(workspace, { maxTreeBytes: checkedLimits.maxTreeBytes, maxFileBytes: checkedLimits.maxTreeBytes });
  if (before.totalBytes !== patch.summary.beforeBytes) fail("Builder patch before-size differs from normalized source");
  const deleted = new Set(patch.changes.filter((change) => change.operation === "delete").map((change) => change.path));
  const replacedDirectories = [];
  for (const change of patch.changes) {
    const destination = join(workspace, ...change.path.split("/"));
    if (change.operation === "add") {
      const existing = await lstat(destination).catch(() => null);
      if (existing) {
        if (!existing.isDirectory() || existing.isSymbolicLink()) fail("Builder patch add target already exists");
        const descendants = [...before.files.keys()].filter((path) => path.startsWith(`${change.path}/`));
        if (descendants.some((path) => !deleted.has(path))) fail("Builder patch directory replacement leaves undeclared content");
        replacedDirectories.push(destination);
      }
    } else await currentFile(destination, change.beforeBytes, change.beforeSha256);
  }
  for (const change of [...patch.changes].sort((left, right) => right.path.length - left.path.length || right.path.localeCompare(left.path))) {
    if (change.operation !== "delete") continue;
    const destination = join(workspace, ...change.path.split("/"));
    await unlink(destination);
    await removeEmptyParents(workspace, destination);
  }
  for (const directory of replacedDirectories.sort((left, right) => right.length - left.length)) await removeEmptyTree(directory);
  for (const change of patch.changes) {
    const destination = join(workspace, ...change.path.split("/"));
    if (change.operation === "delete") continue;
    if (change.operation === "add") {
      await ensureParent(workspace, change.path);
      await writePatchedFile(destination, decodedContent(change), false);
      continue;
    }
    await writePatchedFile(destination, decodedContent(change), true);
  }
  const after = await walk(workspace, { maxTreeBytes: checkedLimits.maxTreeBytes, maxFileBytes: checkedLimits.maxTreeBytes });
  if (after.totalBytes !== patch.summary.afterBytes) fail("Builder patch after-size differs from its summary");
  const inventory = [...after.files].map(([path, record]) => ({ path, bytes: record.bytes, sha256: record.sha256 }));
  return {
    changes: patch.changes.length,
    files: inventory.length,
    bytes: after.totalBytes,
    candidateSha256: createHash("sha256").update(JSON.stringify(inventory)).digest("hex"),
  };
}

async function main(argv = process.argv.slice(2)) {
  const [operation, ...args] = argv;
  if (operation === "init" && args.length === 3) {
    const result = await initializeBuilderVolume(safeRoot(args[0], "source root"), safeRoot(args[1], "workspace root"), { maxBytes: args[2] });
    process.stdout.write(`${JSON.stringify({ schemaVersion: 1, operation: "builder-volume-init", ...result })}\n`);
    return;
  }
  if (operation === "export" && args.length === 9) {
    const [source, workspace, output, maxTreeBytes, maxArtifactBytes, jobId, claimId, planSha256, workspaceSha256] = args;
    const result = await exportBuilderPatch(safeRoot(source, "source root"), safeRoot(workspace, "workspace root"), safeRoot(output, "output root"), { jobId, claimId, planSha256, workspaceSha256 }, { maxTreeBytes, maxArtifactBytes });
    process.stdout.write(`${JSON.stringify({ schemaVersion: 1, operation: "builder-volume-export", bytes: result.bytes, sha256: result.sha256, changes: result.changes })}\n`);
    return;
  }
  if (operation === "apply" && args.length === 11) {
    const [source, workspace, patchPath, expectedPatchSha256, maxTreeBytes, maxArtifactBytes, jobId, claimId, planSha256, workspaceSha256, prefixesBase64] = args;
    const maximum = boundedInteger(maxArtifactBytes, 1, MAX_PATCH_BYTES, "Builder artifact byte ceiling");
    const { text } = await readBoundedRegularText(safeRoot(patchPath, "patch path"), maximum, "Builder patch");
    if (!SHA_RE.test(expectedPatchSha256 ?? "") || createHash("sha256").update(text, "utf8").digest("hex") !== expectedPatchSha256) {
      fail("Builder patch differs from its expected artifact digest");
    }
    let patch;
    let immutablePathPrefixes;
    try {
      patch = JSON.parse(text);
      const encoded = Buffer.from(prefixesBase64, "base64url");
      if (encoded.toString("base64url") !== prefixesBase64) fail("Builder immutable path policy encoding is invalid");
      immutablePathPrefixes = JSON.parse(encoded.toString("utf8"));
    } catch (error) {
      if (error instanceof BuilderVolumeError) throw error;
      fail("Builder verification input is not valid JSON");
    }
    const result = await applyBuilderPatch(safeRoot(source, "source root"), safeRoot(workspace, "workspace root"), patch, { jobId, claimId, planSha256, workspaceSha256 }, { maxTreeBytes, maxArtifactBytes, immutablePathPrefixes });
    process.stdout.write(`${JSON.stringify({ schemaVersion: 1, operation: "builder-volume-apply", ...result })}\n`);
    return;
  }
  fail("Usage: builder-volume.mjs <init SOURCE WORKSPACE MAX_BYTES | export SOURCE WORKSPACE OUTPUT MAX_TREE_BYTES MAX_ARTIFACT_BYTES JOB CLAIM PLAN_SHA WORKSPACE_SHA | apply SOURCE WORKSPACE PATCH PATCH_SHA MAX_TREE_BYTES MAX_ARTIFACT_BYTES JOB CLAIM PLAN_SHA WORKSPACE_SHA IMMUTABLE_PREFIXES_BASE64URL>");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-builder-volume: ${error instanceof BuilderVolumeError ? error.message : "failed"}\n`);
    process.exitCode = 1;
  });
}

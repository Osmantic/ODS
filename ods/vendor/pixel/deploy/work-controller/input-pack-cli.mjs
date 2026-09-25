#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  lstat, mkdir, open, readdir, realpath, rename, rm, unlink,
} from "node:fs/promises";
import {
  basename, dirname, isAbsolute, join, resolve, sep,
} from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkInputCatalog, validateWorkInputPack, validateWorkInputSelection,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { safeTarInternals } from "../work-runner/safe-tar.mjs";

const MAX_SELECTION_BYTES = 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const WINDOWS_RESERVED_RE = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/iu;
const SELECTION_BOUNDARY = "Explicit owner-selected local directories only. Packing may read exactly those directory trees into inert content-addressed snapshots but grants no execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const CATALOG_BOUNDARY = "Owner-selected content-addressed local input references only. The catalog grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const PACK_BOUNDARY = "Private inert content-addressed input pack only. Source paths are omitted; packed control files remain untrusted data; the pack grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const authority = Object.freeze({
  grantsRead: false, grantsExecution: false, grantsLease: false, grantsNetwork: false,
  grantsCredentials: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false,
});
const ZERO_BLOCK = Buffer.alloc(512);

export class InputPackCliError extends Error {}

function fail(message) { throw new InputPackCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 4) fail("Usage: input-pack-cli.mjs --selection FILE --output NEW_PRIVATE_INPUT_BUNDLE");
  const allowed = new Set(["--selection", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("input packing arguments are invalid, unknown, or duplicated");
    values[key] = resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`input packing is missing ${key}`);
  return { selectionPath: values["--selection"], outputPath: values["--output"] };
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
}

function samePath(left, right) {
  return process.platform === "win32" ? left.toLocaleLowerCase("en-US") === right.toLocaleLowerCase("en-US") : left === right;
}

function within(root, path) {
  if (samePath(root, path)) return true;
  const prefix = root.endsWith(sep) ? root : `${root}${sep}`;
  return process.platform === "win32" ? path.toLocaleLowerCase("en-US").startsWith(prefix.toLocaleLowerCase("en-US")) : path.startsWith(prefix);
}

function validateArchivePath(value) {
  if (value.length < 1 || value.length > 1024 || value.startsWith("/") || value.includes("\\") || value.includes(":") || CONTROL_RE.test(value) || value.normalize("NFC") !== value) {
    fail("selected input contains an empty, absolute, non-canonical, or unsafe path");
  }
  for (const component of value.split("/")) {
    if (!component || component === "." || component === ".." || component.length > 255 || component.endsWith(".") || component.endsWith(" ") || WINDOWS_RESERVED_RE.test(component)) {
      fail("selected input contains an unsafe path component");
    }
  }
}

function registerPath(paths, value, kind, label) {
  const components = value.split("/");
  for (let index = 1; index <= components.length; index += 1) {
    const candidate = components.slice(0, index).join("/"), folded = candidate.toLocaleLowerCase("en-US");
    const existing = paths.get(folded), last = index === components.length;
    if (existing && existing.path !== candidate) fail(`${label} has a case-colliding path component`);
    if (!last) {
      if (existing?.kind === "file") fail(`${label} places an entry beneath a file`);
      if (!existing) paths.set(folded, { path: candidate, kind: "directory", explicit: false });
    } else if (existing) {
      if (existing.explicit || kind === "file" || existing.kind === "file") fail(`${label} has a duplicate or file/directory collision`);
      existing.explicit = true;
    } else {
      paths.set(folded, { path: candidate, kind, explicit: true });
    }
  }
}

function identity(info) {
  return { dev: info.dev, ino: info.ino, size: info.size, mtimeMs: info.mtimeMs };
}

function sameIdentity(info, expected, includeSize = true) {
  return info.dev === expected.dev && info.ino === expected.ino && info.mtimeMs === expected.mtimeMs && (!includeSize || info.size === expected.size);
}

function byteOrder(left, right) { return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")); }

async function inventorySource(sourceDirectory, limits) {
  if (!isAbsolute(sourceDirectory) || resolve(sourceDirectory) !== sourceDirectory) fail("selected source directory must be an absolute canonical path");
  const canonicalSource = await realpath(sourceDirectory).catch(() => null);
  if (!canonicalSource || !samePath(canonicalSource, sourceDirectory)) fail("selected source directory contains a missing or symbolic path component");
  const rootInfo = await lstat(sourceDirectory).catch(() => null);
  if (!rootInfo?.isDirectory() || rootInfo.isSymbolicLink()) fail("selected source must be a real directory");
  const entries = [], directories = [], archivePaths = new Map(), projectedPaths = new Map();
  let extractedBytes = 0;
  const walk = async (directory, relative) => {
    const before = await lstat(directory);
    if (!before.isDirectory() || before.isSymbolicLink()) fail("selected source directory changed during inventory");
    directories.push({ absolutePath: directory, expected: identity(before) });
    const names = await readdir(directory);
    names.sort(byteOrder);
    for (const name of names) {
      if (name !== name.normalize("NFC") || CONTROL_RE.test(name)) fail("selected input contains a non-canonical filename");
      const archivePath = relative ? `${relative}/${name}` : name;
      validateArchivePath(archivePath);
      const absolutePath = join(directory, name), info = await lstat(absolutePath).catch(() => null);
      if (!info || info.isSymbolicLink() || !info.isDirectory() && !info.isFile()) fail("selected input contains a link, device, socket, or unsupported entry");
      const kind = info.isDirectory() ? "directory" : "file";
      if (kind === "file" && info.nlink !== 1) fail("selected input contains a hard-linked file");
      registerPath(archivePaths, archivePath, kind, "selected input");
      registerPath(projectedPaths, safeTarInternals.projectedPath(archivePath), kind, "safe input projection");
      entries.push({ archivePath, absolutePath, kind, expected: identity(info) });
      if (entries.length > limits.maxEntries) fail("selected input exceeds its entry ceiling");
      if (kind === "file") {
        if (info.size > limits.maxFileBytes) fail("selected input contains a file above its byte ceiling");
        extractedBytes += info.size;
        if (!Number.isSafeInteger(extractedBytes) || extractedBytes > limits.maxTotalBytes) fail("selected input exceeds its total byte ceiling");
      } else {
        await walk(absolutePath, archivePath);
      }
    }
  };
  await walk(sourceDirectory, "");
  if (entries.length === 0) fail("selected source directory is empty");
  entries.sort((left, right) => byteOrder(left.archivePath, right.archivePath));
  const archiveBytes = entries.reduce((total, entry) => total + 512 + (entry.kind === "file" ? Math.ceil(entry.expected.size / 512) * 512 : 0), 1024);
  if (!Number.isSafeInteger(archiveBytes) || archiveBytes > limits.maxArchiveBytes) fail("selected input exceeds its archive byte ceiling");
  return { sourceDirectory, entries, directories, archiveBytes, extractedBytes };
}

function splitUstarPath(path) {
  const raw = path;
  if (Buffer.byteLength(raw, "utf8") <= 100) return { name: raw, prefix: "" };
  for (let index = raw.lastIndexOf("/"); index > 0; index = raw.lastIndexOf("/", index - 1)) {
    const prefix = raw.slice(0, index), name = raw.slice(index + 1);
    if (name && Buffer.byteLength(name, "utf8") <= 100 && Buffer.byteLength(prefix, "utf8") <= 155) return { name, prefix };
  }
  fail("selected input contains a path that POSIX ustar cannot represent");
}

function writeText(buffer, offset, length, value, label) {
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length > length) fail(`${label} exceeds its POSIX ustar field`);
  bytes.copy(buffer, offset);
}

function writeOctal(buffer, offset, length, value, label) {
  const text = value.toString(8);
  if (text.length > length - 1) fail(`${label} exceeds its POSIX ustar field`);
  buffer.write(text.padStart(length - 1, "0"), offset, length - 1, "ascii");
  buffer[offset + length - 1] = 0;
}

function ustarHeader(entry) {
  const header = Buffer.alloc(512), path = splitUstarPath(entry.archivePath);
  writeText(header, 0, 100, path.name, "archive name");
  writeOctal(header, 100, 8, entry.kind === "directory" ? 0o700 : 0o600, "archive mode");
  writeOctal(header, 108, 8, 0, "archive uid");
  writeOctal(header, 116, 8, 0, "archive gid");
  writeOctal(header, 124, 12, entry.kind === "file" ? entry.expected.size : 0, "archive size");
  writeOctal(header, 136, 12, 0, "archive modification time");
  header.fill(0x20, 148, 156);
  header[156] = entry.kind === "directory" ? 0x35 : 0x30;
  writeText(header, 257, 6, "ustar\0", "archive magic");
  writeText(header, 263, 2, "00", "archive version");
  writeText(header, 345, 155, path.prefix, "archive prefix");
  let checksum = 0;
  for (const byte of header) checksum += byte;
  const checksumText = checksum.toString(8);
  if (checksumText.length > 6) fail("archive checksum exceeds its POSIX ustar field");
  header.write(checksumText.padStart(6, "0"), 148, 6, "ascii");
  header[154] = 0; header[155] = 0x20;
  return header;
}

async function writeExactly(handle, bytes) {
  let offset = 0;
  while (offset < bytes.length) {
    const { bytesWritten } = await handle.write(bytes, offset, bytes.length - offset, null);
    if (bytesWritten === 0) fail("input pack write made no progress");
    offset += bytesWritten;
  }
}

async function packInventory(inventory, temporaryPath) {
  const handle = await open(temporaryPath, "wx", 0o600), archiveDigest = createHash("sha256"), tree = [];
  let written = 0;
  const write = async (bytes) => {
    if (written + bytes.length > inventory.archiveBytes) fail("input archive exceeded its precomputed byte ceiling");
    await writeExactly(handle, bytes); archiveDigest.update(bytes); written += bytes.length;
  };
  try {
    for (const entry of inventory.entries) {
      await write(ustarHeader(entry));
      if (entry.kind === "directory") {
        tree.push({ path: entry.archivePath, kind: "directory", bytes: 0 });
        continue;
      }
      const before = await lstat(entry.absolutePath).catch(() => null);
      if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || !sameIdentity(before, entry.expected)) fail("selected input file changed after inventory");
      const input = await open(entry.absolutePath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail("selected input file could not be opened safely"));
      const contentDigest = createHash("sha256");
      try {
        const opened = await input.stat();
        if (!opened.isFile() || opened.nlink !== 1 || !sameIdentity(opened, entry.expected)) fail("selected input file changed while opening");
        let remaining = entry.expected.size, position = 0;
        const buffer = Buffer.alloc(Math.min(1024 * 1024, Math.max(1, remaining)));
        while (remaining > 0) {
          const length = Math.min(buffer.length, remaining), { bytesRead } = await input.read(buffer, 0, length, position);
          if (bytesRead === 0) fail("selected input file ended during packing");
          const chunk = buffer.subarray(0, bytesRead);
          await write(chunk); contentDigest.update(chunk); remaining -= bytesRead; position += bytesRead;
        }
        const after = await input.stat();
        if (!sameIdentity(after, entry.expected)) fail("selected input file changed during packing");
      } finally {
        await input.close();
      }
      const padding = (512 - (entry.expected.size % 512)) % 512;
      if (padding) await write(Buffer.alloc(padding));
      tree.push({ path: entry.archivePath, kind: "file", bytes: entry.expected.size, sha256: contentDigest.digest("hex") });
    }
    await write(ZERO_BLOCK); await write(ZERO_BLOCK);
    if (written !== inventory.archiveBytes) fail("input archive byte count differs from its precomputed inventory");
    for (const directory of inventory.directories) {
      const observed = await lstat(directory.absolutePath).catch(() => null);
      if (!observed?.isDirectory() || observed.isSymbolicLink() || !sameIdentity(observed, directory.expected, false)) fail("selected source directory changed during packing");
    }
    await handle.sync();
    return {
      archiveSha256: archiveDigest.digest("hex"), archiveBytes: written, extractedBytes: inventory.extractedBytes,
      files: tree.filter((entry) => entry.kind === "file").length,
      directories: tree.filter((entry) => entry.kind === "directory").length,
      treeSha256: sha(tree), tree,
    };
  } finally {
    await handle.close();
  }
}

async function hashRegularFile(path, expectedBytes) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size !== expectedBytes) fail("existing content-addressed object is unsafe or inconsistent");
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  const digest = createHash("sha256");
  try {
    const buffer = Buffer.alloc(1024 * 1024); let position = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, position);
      if (bytesRead === 0) break;
      digest.update(buffer.subarray(0, bytesRead)); position += bytesRead;
    }
    const after = await handle.stat();
    if (!after.isFile() || after.nlink !== 1 || !sameIdentity(after, identity(info))) fail("existing content-addressed object changed while reading");
  } finally { await handle.close(); }
  return digest.digest("hex");
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function compileInputPack({ selection, stagingRoot, now = new Date(), nonce = randomBytes(16).toString("hex") } = {}) {
  const errors = validateWorkInputSelection(selection);
  if (errors.length || selection?.boundary !== SELECTION_BOUNDARY) fail(`private input selection is invalid: ${errors[0] ?? "boundary differs"}`);
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0 || !/^[a-f0-9]{32}$/u.test(nonce)) fail("input pack identity is invalid");
  if (Date.parse(selection.createdAt) > now.getTime()) fail("private input selection was created in the future");
  const objects = join(stagingRoot, "objects");
  await mkdir(objects, { mode: 0o700 });
  const packed = [];
  for (let index = 0; index < selection.entries.length; index += 1) {
    const selected = selection.entries[index], inventory = await inventorySource(selected.sourceDirectory, selected.limits);
    const temporary = join(objects, `.pixel-input-object-${index}-${randomBytes(8).toString("hex")}.tar`);
    const evidence = await packInventory(inventory, temporary);
    const treeByPath = new Map(evidence.tree.map((entry) => [entry.path, entry]));
    const datasets = selected.kind === "dataset" ? selected.datasets.map((dataset) => {
      if (safeTarInternals.projectedPath(dataset.relativePath) !== dataset.relativePath) fail(`dataset ${dataset.datasetId} resolves to an inert control path`);
      const file = treeByPath.get(dataset.relativePath);
      if (!file || file.kind !== "file" || file.bytes < 1 || !/^[a-f0-9]{64}$/u.test(file.sha256 ?? "")) fail(`dataset ${dataset.datasetId} is missing, empty, or not a regular selected file`);
      return { ...dataset, contentSha256: file.sha256, bytes: file.bytes };
    }) : undefined;
    const objectName = `${evidence.archiveSha256}.tar`, destination = join(objects, objectName);
    const existing = await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (existing) {
      if (await hashRegularFile(destination, evidence.archiveBytes) !== evidence.archiveSha256) fail("content-addressed object collision is inconsistent");
      await unlink(temporary);
    } else {
      await rename(temporary, destination);
    }
    const { tree: _tree, ...publicEvidence } = evidence;
    packed.push({ selected, objectName, ...publicEvidence, ...(datasets ? { datasets } : {}) });
  }
  const epoch = String(now.getTime()).padStart(13, "0"), createdAt = now.toISOString();
  const catalog = {
    $schema: "https://osmantic.com/pixel/schemas/work-input-catalog-v1.schema.json", schemaVersion: 1,
    catalogId: `inputcatalog-${epoch}-${sha(`${nonce}:catalog`).slice(0, 12)}`, createdAt,
    entries: packed.map(({ selected, objectName, archiveSha256, archiveBytes, datasets }) => ({
      id: selected.id, kind: selected.kind, objectName, contentSha256: archiveSha256,
      bytes: archiveBytes, classification: selected.classification, mountMode: "read-only",
      ...(datasets ? { datasets: datasets.map((dataset) => ({ ...dataset })) } : {}),
    })), boundary: CATALOG_BOUNDARY,
  };
  const catalogErrors = validateWorkInputCatalog(catalog);
  if (catalogErrors.length) fail(`generated private input catalog is invalid: ${catalogErrors[0]}`);
  const manifestEntries = packed.map(({ selected, objectName, datasets, ...evidence }) => ({
    id: selected.id, kind: selected.kind, objectName, ...evidence,
    ...(datasets ? { datasets: datasets.map((dataset) => ({ ...dataset })) } : {}),
  }));
  const uniqueArchiveBytes = [...new Map(manifestEntries.map((entry) => [entry.objectName, entry.archiveBytes])).values()].reduce((sum, bytes) => sum + bytes, 0);
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-input-pack-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-input-pack", packId: `inputpack-${epoch}-${sha(`${nonce}:pack`).slice(0, 12)}`, createdAt,
    selectionSha256: sha(selection), catalogSha256: sha(catalog), entries: manifestEntries,
    totals: {
      inputs: manifestEntries.length, objects: new Set(manifestEntries.map((entry) => entry.objectName)).size,
      archiveBytes: uniqueArchiveBytes,
      extractedBytes: manifestEntries.reduce((sum, entry) => sum + entry.extractedBytes, 0),
      files: manifestEntries.reduce((sum, entry) => sum + entry.files, 0),
      directories: manifestEntries.reduce((sum, entry) => sum + entry.directories, 0),
      datasets: manifestEntries.reduce((sum, entry) => sum + (entry.datasets?.length ?? 0), 0),
    }, authority: { ...authority }, boundary: PACK_BOUNDARY,
  };
  const manifestErrors = validateWorkInputPack(manifest);
  if (manifestErrors.length) fail(`generated private input pack is invalid: ${manifestErrors[0]}`);
  await writePrivate(join(stagingRoot, "input-catalog.json"), catalog);
  await writePrivate(join(stagingRoot, "input-pack.json"), manifest);
  await syncDirectory(objects); await syncDirectory(stagingRoot);
  return Object.freeze({ catalog: Object.freeze(catalog), manifest: Object.freeze(manifest) });
}

export async function runInputPackCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("input packing owner is invalid");
  const outputName = basename(options.outputPath), outputParent = dirname(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(outputParent, outputName) !== options.outputPath) fail("input pack output directory name is invalid");
  await privateDirectory(outputParent, "input pack output parent", expectedOwnerUid);
  const canonicalOutputParent = await realpath(outputParent).catch(() => null);
  if (!canonicalOutputParent || !samePath(canonicalOutputParent, outputParent)) fail("input pack output parent contains a symbolic path component");
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("input pack output already exists");
  const selection = await readPrivateJson(options.selectionPath, MAX_SELECTION_BYTES, "private input selection", expectedOwnerUid);
  const selectionErrors = validateWorkInputSelection(selection);
  if (selectionErrors.length) fail(`private input selection is invalid: ${selectionErrors[0]}`);
  for (const entry of selection.entries) {
    const source = resolve(entry.sourceDirectory);
    if (within(source, outputParent)) fail(`input ${entry.id} contains the output parent and cannot be snapshotted safely`);
  }
  const staging = join(outputParent, `.pixel-input-pack-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    const compiled = await compileInputPack({ selection, stagingRoot: staging, now: dependencies.now ?? new Date(), ...(dependencies.nonce ? { nonce: dependencies.nonce } : {}) });
    await rename(staging, options.outputPath); await syncDirectory(outputParent);
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-work-input-pack", status: "packed", packId: compiled.manifest.packId,
      packSha256: sha(compiled.manifest), catalogSha256: compiled.manifest.catalogSha256,
      inputs: compiled.manifest.totals.inputs, objects: compiled.manifest.totals.objects,
      archiveBytes: compiled.manifest.totals.archiveBytes, authority: { ...authority },
      boundary: "Content-free local input-pack receipt only; no source path, filename, content, credential, provider, execution, network, external-effect, or completion authority.",
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY") fail("input pack output already exists");
    throw error;
  }
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runInputPackCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-input-pack: ${error instanceof InputPackCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

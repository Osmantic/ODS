import { createHash } from "node:crypto";
import { constants } from "node:fs";
import {
  lstat, mkdir, open, rm,
} from "node:fs/promises";
import { basename, dirname, join, resolve, sep } from "node:path";

const BLOCK_BYTES = 512;
const ZERO_BLOCK = Buffer.alloc(BLOCK_BYTES);
const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const WINDOWS_RESERVED_RE = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/iu;

export class SafeTarError extends Error {}

function fail(message) {
  throw new SafeTarError(message);
}

function field(buffer, start, length, label) {
  const bytes = buffer.subarray(start, start + length);
  const nul = bytes.indexOf(0);
  const end = nul === -1 ? bytes.length : nul;
  if (nul !== -1 && bytes.subarray(nul).some((byte) => byte !== 0)) fail(`${label} has data after NUL`);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, end));
  } catch {
    fail(`${label} is not UTF-8`);
  }
}

function octal(buffer, start, length, label) {
  const bytes = buffer.subarray(start, start + length);
  if (bytes[0] & 0x80) fail(`${label} uses unsupported base-256 encoding`);
  const text = bytes.toString("ascii").replace(/\0.*$/s, "").trim();
  if (text === "") return 0;
  if (!/^[0-7]+$/.test(text)) fail(`${label} is not strict octal`);
  const value = Number.parseInt(text, 8);
  if (!Number.isSafeInteger(value)) fail(`${label} exceeds the safe integer range`);
  return value;
}

function checkedPath(rawName, type) {
  let value = rawName;
  if (type === "directory") value = value.replace(/\/+$/, "");
  if (
    value.length === 0
    || value.length > 1024
    || value.startsWith("/")
    || value.includes("\\")
    || value.includes(":")
    || CONTROL_RE.test(value)
    || value.normalize("NFC") !== value
  ) fail("archive path is empty, absolute, non-canonical, or unsafe");
  const components = value.split("/");
  for (const component of components) {
    if (
      component === ""
      || component === "."
      || component === ".."
      || component.length > 255
      || component.endsWith(".")
      || component.endsWith(" ")
      || WINDOWS_RESERVED_RE.test(component)
    ) fail("archive path contains an unsafe component");
  }
  return { value, components };
}

function inertPath(path) {
  const components = path.split("/");
  const lowered = components.map((component) => component.toLocaleLowerCase("en-US"));
  const first = lowered[0];
  const last = lowered.at(-1);
  return (
    first === ".omp"
    || first === ".pi"
    || first === ".claude"
    || first === ".codex"
    || first === ".openclaw"
    || first === ".gemini"
    || first === ".cursor"
    || first === ".windsurf"
    || first === ".agent"
    || first === ".agents"
    || first === ".git"
    || first === ".github"
    || first === ".vscode"
    || first === ".idea"
    || first === "node_modules"
    || first === "vendor"
    || lowered.some((component) => component === ".env" || component.startsWith(".env."))
    || last === "agents.md"
    || last === "claude.md"
    || last === "gemini.md"
    || last === ".mcp.json"
    || last === "mcp.json"
    || last === ".cursorrules"
    || last === ".windsurfrules"
  );
}

function projectedPath(path) {
  return inertPath(path) ? `__pixel_inert__/${path}` : path;
}

function registerPath(map, path, type, label) {
  const components = path.split("/");
  for (let index = 1; index <= components.length; index += 1) {
    const candidate = components.slice(0, index).join("/");
    const folded = candidate.toLocaleLowerCase("en-US");
    const existing = map.get(folded);
    const last = index === components.length;
    if (existing && existing.path !== candidate) fail(`${label} contains a case-colliding path component`);
    if (!last) {
      if (existing?.type === "file") fail(`${label} places an entry beneath a file`);
      if (!existing) map.set(folded, { path: candidate, type: "directory", explicit: false });
      continue;
    }
    if (existing) {
      if (existing.explicit || type === "file" || existing.type === "file") fail(`${label} contains a duplicate path or replaces a directory with a file`);
      existing.explicit = true;
    } else {
      map.set(folded, { path: candidate, type, explicit: true });
    }
  }
}

async function readExactly(handle, buffer, position, digest) {
  let offset = 0;
  while (offset < buffer.length) {
    const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, position + offset);
    if (bytesRead === 0) fail("archive ended unexpectedly");
    offset += bytesRead;
  }
  digest?.update(buffer);
}

async function writeExactly(handle, buffer) {
  let offset = 0;
  while (offset < buffer.length) {
    const { bytesWritten } = await handle.write(buffer, offset, buffer.length - offset, null);
    if (bytesWritten === 0) fail("archive output write made no progress");
    offset += bytesWritten;
  }
}

function verifyHeader(header) {
  const expected = octal(header, 148, 8, "header checksum");
  const copy = Buffer.from(header);
  copy.fill(0x20, 148, 156);
  let observed = 0;
  for (const byte of copy) observed += byte;
  if (observed !== expected) fail("archive header checksum is invalid");
  if (header.subarray(257, 263).toString("latin1") !== "ustar\0" || header.subarray(263, 265).toString("ascii") !== "00") {
    fail("archive must use POSIX ustar format");
  }
}

async function privateNewDirectory(path) {
  const absolute = resolve(path);
  const parent = dirname(absolute);
  const parentInfo = await lstat(parent).catch(() => null);
  if (!parentInfo?.isDirectory() || parentInfo.isSymbolicLink()) fail("materialization parent must be a real directory");
  if (process.platform !== "win32" && (parentInfo.uid !== process.geteuid() || (parentInfo.mode & 0o077) !== 0)) {
    fail("materialization parent must be owner-only");
  }
  if (await lstat(absolute).then(() => true, () => false)) fail("materialization destination already exists");
  await mkdir(absolute, { mode: 0o700 });
  return absolute;
}

function within(root, path) {
  return path === root || path.startsWith(`${root}${sep}`);
}

export async function materializeUstar(archivePath, destination, limits) {
  const required = ["maxArchiveBytes", "maxExtractedBytes", "maxEntries", "maxFileBytes"];
  for (const key of required) {
    if (!Number.isSafeInteger(limits?.[key]) || limits[key] < 1) fail(`invalid ${key}`);
  }
  if (!/^[a-f0-9]{64}$/.test(limits?.expectedSha256 ?? "")) fail("invalid expectedSha256");
  const archiveInfo = await lstat(archivePath).catch(() => null);
  if (
    !archiveInfo?.isFile()
    || archiveInfo.isSymbolicLink()
    || archiveInfo.nlink !== 1
    || archiveInfo.size < BLOCK_BYTES * 3
    || archiveInfo.size > limits.maxArchiveBytes
    || archiveInfo.size % BLOCK_BYTES !== 0
  ) fail("archive must be a bounded, block-aligned, single-link regular file");
  if (process.platform !== "win32" && (archiveInfo.uid !== process.geteuid() || (archiveInfo.mode & 0o077) !== 0)) {
    fail("archive must be owner-only");
  }

  const root = await privateNewDirectory(destination);
  const handle = await open(archivePath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => {
    fail("archive could not be opened safely");
  });
  const entries = [];
  const archiveNames = new Map();
  const projectedNames = new Map();
  let extractedBytes = 0;
  let position = 0;
  let zeroBlocks = 0;
  const archiveDigest = createHash("sha256");
  try {
    const opened = await handle.stat();
    if (
      !opened.isFile()
      || opened.nlink !== 1
      || opened.size !== archiveInfo.size
      || opened.dev !== archiveInfo.dev
      || opened.ino !== archiveInfo.ino
    ) fail("archive changed during validation");
    while (position < archiveInfo.size) {
      const header = Buffer.alloc(BLOCK_BYTES);
      await readExactly(handle, header, position, archiveDigest);
      position += BLOCK_BYTES;
      if (header.equals(ZERO_BLOCK)) {
        zeroBlocks += 1;
        if (zeroBlocks < 2) continue;
        while (position < archiveInfo.size) {
          const trailing = Buffer.alloc(BLOCK_BYTES);
          await readExactly(handle, trailing, position, archiveDigest);
          position += BLOCK_BYTES;
          if (!trailing.equals(ZERO_BLOCK)) fail("archive has data after its end marker");
        }
        break;
      }
      if (zeroBlocks !== 0) fail("archive has an interrupted end marker");
      verifyHeader(header);
      const typeByte = header[156];
      const type = typeByte === 0 || typeByte === 0x30 ? "file" : typeByte === 0x35 ? "directory" : null;
      if (!type) fail("archive contains a link, device, extension, or unsupported entry type");
      if (field(header, 157, 100, "link name") !== "") fail("archive entry has a link target");
      const name = field(header, 0, 100, "entry name");
      const prefix = field(header, 345, 155, "entry prefix");
      const checked = checkedPath(prefix ? `${prefix}/${name}` : name, type);
      registerPath(archiveNames, checked.value, type, "archive");

      const outputName = projectedPath(checked.value);
      registerPath(projectedNames, outputName, type, "safe projection");
      const outputPath = resolve(root, ...outputName.split("/"));
      if (!within(root, outputPath)) fail("archive output escapes the materialization root");
      const size = octal(header, 124, 12, "entry size");
      if (type === "directory" && size !== 0) fail("archive directory has file data");
      if (size > limits.maxFileBytes || extractedBytes + size > limits.maxExtractedBytes) fail("archive exceeds an extracted byte ceiling");
      if (entries.length + 1 > limits.maxEntries) fail("archive exceeds its entry ceiling");

      if (type === "directory") {
        await mkdir(outputPath, { recursive: true, mode: 0o700 });
        entries.push({ path: outputName, sourcePath: checked.value, kind: "directory", bytes: 0, inert: outputName !== checked.value });
      } else {
        await mkdir(dirname(outputPath), { recursive: true, mode: 0o700 });
        const output = await open(outputPath, "wx", 0o600).catch(() => fail("archive output path could not be created safely"));
        const digest = createHash("sha256");
        let remaining = size;
        try {
          const buffer = Buffer.alloc(Math.min(1024 * 1024, Math.max(1, size)));
          while (remaining > 0) {
            const length = Math.min(buffer.length, remaining);
            const chunk = buffer.subarray(0, length);
            await readExactly(handle, chunk, position, archiveDigest);
            await writeExactly(output, chunk);
            digest.update(chunk);
            position += length;
            remaining -= length;
          }
          await output.sync();
        } finally {
          await output.close();
        }
        const padding = (BLOCK_BYTES - (size % BLOCK_BYTES)) % BLOCK_BYTES;
        if (padding > 0) {
          const paddingBytes = Buffer.alloc(padding);
          await readExactly(handle, paddingBytes, position, archiveDigest);
          if (paddingBytes.some((byte) => byte !== 0)) fail("archive file padding is not zero-filled");
          position += padding;
        }
        extractedBytes += size;
        entries.push({ path: outputName, sourcePath: checked.value, kind: "file", bytes: size, sha256: digest.digest("hex"), inert: outputName !== checked.value });
      }
    }
    if (zeroBlocks < 2) fail("archive is missing its two-block end marker");
    const archiveSha256 = archiveDigest.digest("hex");
    if (archiveSha256 !== limits.expectedSha256) fail("archive SHA-256 differs from the immutable input binding");
    const treeSha256 = createHash("sha256").update(JSON.stringify(entries)).digest("hex");
    return { archiveBytes: archiveInfo.size, archiveSha256, extractedBytes, entries, treeSha256 };
  } catch (error) {
    await rm(root, { recursive: true, force: true });
    throw error;
  } finally {
    await handle.close();
  }
}

export const safeTarInternals = Object.freeze({ projectedPath });

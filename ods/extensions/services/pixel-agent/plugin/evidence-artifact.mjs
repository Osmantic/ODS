import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fchmodSync,
  ftruncateSync,
  fsyncSync,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readSync,
  realpathSync,
  writeSync,
} from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const MAX_ARTIFACT_BYTES = 256 * 1024;
const SAFE_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const WRITE_FLAGS =
  constants.O_RDWR |
  constants.O_CREAT |
  (constants.O_NOFOLLOW ?? 0) |
  (constants.O_CLOEXEC ?? 0);

function assertOwnedDirectory(value, owner) {
  const details = lstatSync(value);
  if (
    !details.isDirectory() ||
    details.isSymbolicLink() ||
    (owner !== undefined && details.uid !== owner) ||
    (process.platform !== "win32" && (details.mode & 0o022) !== 0)
  ) {
    throw new Error("unsafe Pixel workspace directory");
  }
}

function safeParts(relativePath) {
  if (typeof relativePath !== "string" || relativePath.length > 1024) {
    throw new Error("invalid Pixel evidence path");
  }
  const parts = relativePath.split("/");
  if (
    parts.length < 1 ||
    parts.length > 16 ||
    parts.some((part) => !SAFE_COMPONENT.test(part) || part === "." || part === "..")
  ) {
    throw new Error("invalid Pixel evidence path");
  }
  return parts;
}

export function createEvidenceArtifactWriter({
  workspaceRoot = process.env.PIXEL_WORKSPACE_ROOT ??
    path.join(homedir(), ".openclaw", `workspace-${process.env.PIXEL_AGENT_ID ?? "pixel"}`),
} = {}) {
  const root = path.resolve(workspaceRoot);

  return ({ relativePath, content }) => {
    const parts = safeParts(relativePath);
    if (typeof content !== "string" || content.length < 1 || content.includes("\0")) {
      throw new Error("invalid Pixel evidence content");
    }
    const bytes = Buffer.from(content, "utf8");
    if (bytes.length > MAX_ARTIFACT_BYTES) {
      throw new Error("Pixel evidence artifact is too large");
    }

    const owner = typeof process.getuid === "function" ? process.getuid() : undefined;
    assertOwnedDirectory(root, owner);
    if (realpathSync(root) !== root) throw new Error("unsafe Pixel workspace root");

    let parent = root;
    for (const component of parts.slice(0, -1)) {
      parent = path.join(parent, component);
      try {
        mkdirSync(parent, { mode: 0o700 });
      } catch (error) {
        if (error?.code !== "EEXIST") throw error;
      }
      assertOwnedDirectory(parent, owner);
      if (realpathSync(parent) !== parent) throw new Error("unsafe Pixel workspace parent");
    }

    const destination = path.join(parent, parts.at(-1));
    const relative = path.relative(root, destination);
    const portableRelative = relative.split(path.sep).join("/");
    if (
      portableRelative !== relativePath ||
      relative.startsWith("..") ||
      path.isAbsolute(relative)
    ) {
      throw new Error("Pixel evidence path escaped its workspace");
    }

    let handle;
    try {
      handle = openSync(destination, WRITE_FLAGS, 0o600);
      const details = fstatSync(handle);
      if (
        !details.isFile() ||
        details.nlink !== 1 ||
        (owner !== undefined && details.uid !== owner)
      ) {
        throw new Error("unsafe Pixel evidence destination");
      }
      fchmodSync(handle, 0o600);
      // Validate the opened inode before changing any bytes. In particular,
      // O_TRUNC at open time would destroy a multiply-linked destination even
      // though the guard subsequently rejects it.
      ftruncateSync(handle, 0);
      let written = 0;
      while (written < bytes.length) {
        written += writeSync(handle, bytes, written, bytes.length - written, written);
      }
      fsyncSync(handle);
      const observed = Buffer.alloc(bytes.length);
      let read = 0;
      while (read < observed.length) {
        const count = readSync(handle, observed, read, observed.length - read, read);
        if (count === 0) break;
        read += count;
      }
      if (read !== bytes.length || !observed.equals(bytes)) {
        throw new Error("Pixel evidence readback mismatch");
      }
    } finally {
      if (handle !== undefined) closeSync(handle);
    }

    return {
      relativePath,
      bytes: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      readbackVerified: true,
    };
  };
}

export const testing = Object.freeze({ safeParts });

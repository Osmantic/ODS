import { randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, open, rename, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";

export const GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth";
export const GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token";
const MAX_CLIENT_BYTES = 1024 * 1024;
const READ_FLAGS = constants.O_RDONLY
  | (constants.O_NOFOLLOW ?? 0)
  | (constants.O_CLOEXEC ?? 0)
  | (constants.O_NONBLOCK ?? 0);

function exactGoogleEndpoint(value, expected, label) {
  const candidate = value ?? expected;
  if (typeof candidate !== "string" || new URL(candidate).href !== expected) {
    throw new Error(`${label} must use Pixel's fixed Google OAuth endpoint`);
  }
  return expected;
}

function boundedCredential(value, label, maximum) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) {
    throw new Error(`${label} is missing or invalid`);
  }
  return value;
}

export function installedGoogleClient(value) {
  const source = value?.installed ?? value?.web ?? value;
  if (!source || typeof source !== "object" || Array.isArray(source)) throw new Error("OAuth client credentials are missing");
  return {
    client_id: boundedCredential(source.client_id, "OAuth client ID", 2048),
    client_secret: boundedCredential(source.client_secret, "OAuth client secret", 8192),
    auth_uri: exactGoogleEndpoint(source.auth_uri, GOOGLE_AUTH_URI, "OAuth authorization URI"),
    token_uri: exactGoogleEndpoint(source.token_uri, GOOGLE_TOKEN_URI, "OAuth token URI"),
  };
}

async function readBounded(handle, maximum) {
  const chunks = [];
  let total = 0;
  while (total <= maximum) {
    const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maximum + 1 - total));
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
    if (bytesRead === 0) break;
    chunks.push(buffer.subarray(0, bytesRead));
    total += bytesRead;
  }
  if (total > maximum) throw new Error("OAuth credential file exceeds its size limit");
  return Buffer.concat(chunks, total);
}

export async function readPrivateJson(path) {
  const handle = await open(path, READ_FLAGS);
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > MAX_CLIENT_BYTES) {
      throw new Error("OAuth credential file is not a bounded regular file");
    }
    if (process.platform !== "win32" && (info.mode & 0o077) !== 0) throw new Error("OAuth credential file is not private");
    if (process.platform !== "win32" && typeof process.geteuid === "function" && info.uid !== process.geteuid()) {
      throw new Error("OAuth credential file belongs to another user");
    }
    return JSON.parse((await readBounded(handle, MAX_CLIENT_BYTES)).toString("utf8"));
  } finally {
    await handle.close();
  }
}

export async function writePrivateJson(path, value) {
  const parent = dirname(path);
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const parentInfo = await lstat(parent);
  if (!parentInfo.isDirectory() || parentInfo.isSymbolicLink()) throw new Error("OAuth credential directory is unsafe");
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const temporary = join(parent, `.${randomBytes(16).toString("hex")}.token.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

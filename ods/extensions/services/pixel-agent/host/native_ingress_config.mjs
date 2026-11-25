// Owner-validated bytes arrive on stdin, never in the container environment.
// Immutable snapshots let an existing Compose/activation rollback select its
// previous digest without overwriting the credential used by another instance.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { readGatewayConfiguration } from "./pixel_ingress.mjs";

const LIMIT = 2 * 1024 * 1024;
const digest = (body) => crypto.createHash("sha256").update(body).digest("hex");

export function stageConfiguration(directory, body, expected) {
  const uid = process.geteuid();
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== uid || (info.mode & 0o077)) {
    throw new Error("private configuration directory required");
  }
  if (!Buffer.isBuffer(body) || body.length > LIMIT || !/^[a-f0-9]{64}$/.test(expected)
      || digest(body) !== expected || !body.toString("utf8").trimStart().startsWith("{")) {
    throw new Error("bounded JSON configuration snapshot required");
  }
  const destination = path.join(directory, expected + ".json");
  const verify = () => {
    if (fs.lstatSync(destination).size > LIMIT) throw new Error("configuration snapshot too large");
    // Preserve the production token reader's owner, mode, link and token checks.
    readGatewayConfiguration(destination);
    if (digest(fs.readFileSync(destination)) !== expected) {
      throw new Error("configuration snapshot digest mismatch");
    }
  };
  if (fs.existsSync(destination)) {
    verify();
    return { sha256: expected, reused: true };
  }
  const temporary = path.join(directory, ".stage-" + crypto.randomBytes(16).toString("hex"));
  let fd;
  try {
    fd = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT
      | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    fs.writeFileSync(fd, body);
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = undefined;
    readGatewayConfiguration(temporary);
    // link is exclusive; an existing digest is verified, never replaced.
    try { fs.linkSync(temporary, destination); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    verify();
    const parent = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
    try { fs.fsyncSync(parent); } finally { fs.closeSync(parent); }
    return { sha256: expected, reused: false };
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    let length = 0;
    const chunks = [];
    for await (const chunk of process.stdin) {
      length += chunk.length;
      if (length > LIMIT) throw new Error("configuration too large");
      chunks.push(chunk);
    }
    const result = stageConfiguration("/config", Buffer.concat(chunks), process.argv[2]);
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch {
    // Never echo configuration bytes, JSON parser messages, or credentials.
    process.stderr.write("Native ingress configuration staging failed.\n");
    process.exitCode = 1;
  }
}

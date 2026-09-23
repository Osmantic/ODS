import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { mkdir, open, readFile, rm } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import {
  buildCredentialFreeCodexCliCommand, FIXED_CODEX_WORK_INSTRUCTION,
} from "./codex-cli-qualification.mjs";
import { decodeWorkCodexContainerFrame } from "./container-protocol.mjs";

const MAX_FRAME_BYTES = 2 * 1024 * 1024;
const DEFAULT_CODEX_BINARY = "/usr/local/bin/codex";

export class WorkCodexContainerEntrypointError extends Error {}
function fail(message) { throw new WorkCodexContainerEntrypointError(message); }

async function readBoundedStream(stream, maximumBytes) {
  const chunks = []; let bytes = 0;
  for await (const chunk of stream) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.length; if (bytes > maximumBytes) fail("Codex container input exceeded its byte ceiling"); chunks.push(value);
  }
  return Buffer.concat(chunks);
}

async function writeNew(path, value, mode = 0o600) {
  const handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), mode).catch(() => fail("Codex container private file could not be created once"));
  try { await handle.writeFile(value); await handle.sync(); } finally { await handle.close(); }
}

function childEnvironment(paths, proxyUrl) {
  const match = /^http:\/\/([0-9]{1,3}(?:\.[0-9]{1,3}){3}):([0-9]{4,5})$/u.exec(proxyUrl ?? ""), parts = match?.[1].split(".").map(Number) ?? [];
  const privateAddress = parts.length === 4 && parts.every((part) => Number.isInteger(part) && part >= 0 && part <= 255) && (parts[0] === 10 || parts[0] === 192 && parts[1] === 168 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31);
  if (!match || !privateAddress || Number(match[2]) < 1024 || Number(match[2]) > 65535) fail("Codex container proxy URL is invalid");
  return Object.freeze({
    HOME: paths.home, USERPROFILE: paths.home, CODEX_HOME: paths.codexHome,
    TMPDIR: paths.tmp, TMP: paths.tmp, TEMP: paths.tmp, LANG: "C.UTF-8", LC_ALL: "C.UTF-8", NO_COLOR: "1", PATH: "",
    HTTP_PROXY: proxyUrl, HTTPS_PROXY: proxyUrl, ALL_PROXY: proxyUrl, NO_PROXY: "127.0.0.1,localhost",
  });
}

async function runChild({ executable, args, environment, stdin, stdout = "ignore", stderr = "ignore" }) {
  return new Promise((resolvePromise, rejectPromise) => {
    let child;
    try { child = spawn(executable, args, { env: environment, stdio: ["pipe", "pipe", "pipe"], shell: false }); }
    catch { rejectPromise(new WorkCodexContainerEntrypointError("Codex container child could not start")); return; }
    if (stdout !== "ignore") child.stdout.on("data", (chunk) => stdout.write(chunk));
    if (stderr !== "ignore") child.stderr.on("data", (chunk) => stderr.write(chunk));
    child.on("error", () => rejectPromise(new WorkCodexContainerEntrypointError("Codex container child could not start")));
    child.on("close", (code, signal) => code === 0 && signal === null ? resolvePromise() : rejectPromise(new WorkCodexContainerEntrypointError("Codex container child exited unsuccessfully")));
    child.stdin.on("error", () => { try { child.kill("SIGKILL"); } catch { /* already gone */ } });
    child.stdin.end(stdin);
  });
}

export async function runWorkCodexContainerEntrypoint({
  input = process.stdin, executablePath = DEFAULT_CODEX_BINARY, bootstrapArguments = [],
  root = "/run/pixel", proxyUrl = process.env.PIXEL_CODEX_PROXY_URL,
  output = process.stdout, diagnostic = process.stderr,
} = {}) {
  if (typeof executablePath !== "string" || !isAbsolute(executablePath) || !Array.isArray(bootstrapArguments) || bootstrapArguments.length > 1 || bootstrapArguments.some((value) => typeof value !== "string" || !isAbsolute(value))) fail("Codex container executable inputs are invalid");
  const frame = await readBoundedStream(input, MAX_FRAME_BYTES), decoded = decodeWorkCodexContainerFrame(frame); frame.fill(0);
  const paths = { root: resolve(root), home: resolve(root, "home"), codexHome: resolve(root, "codex-home"), tmp: resolve(root, "tmp"), out: resolve(root, "out") };
  for (const path of [paths.home, paths.codexHome, paths.tmp]) await mkdir(path, { mode: 0o700, recursive: false });
  const environment = childEnvironment(paths, proxyUrl), authPath = resolve(paths.codexHome, "auth.json"), schemaPath = resolve(paths.root, "output-schema.json"), outputPath = resolve(paths.out, "last-message.json"), refreshPath = resolve(paths.out, "refreshed-auth.json");
  try {
    await writeNew(schemaPath, `${canonical(decoded.payload.outputSchema)}\n`, 0o400);
    if (decoded.authMode === "chatgpt") await writeNew(authPath, decoded.credential, 0o600);
    else await runChild({ executable: executablePath, args: [...bootstrapArguments, "login", "--with-api-key"], environment, stdin: decoded.credential });
    decoded.credential.fill(0);
    const command = buildCredentialFreeCodexCliCommand({ executablePath, bootstrapArguments, schemaPath, outputPath, model: decoded.payload.provider.model });
    const envelope = `${canonical({ schemaVersion: 1, instruction: FIXED_CODEX_WORK_INSTRUCTION, planSha256: decoded.payload.planSha256, capsule: decoded.payload.capsule })}\n`;
    if (Buffer.byteLength(envelope, "utf8") > decoded.payload.limits.maxInputBytes) fail("Codex container task envelope exceeded its exact plan limit");
    await runChild({ executable: command.executable, args: command.args, environment, stdin: envelope, stdout: output, stderr: diagnostic });
    if (decoded.authMode === "chatgpt") {
      const refreshed = await readFile(authPath);
      if (refreshed.length < 1 || refreshed.length > 262144 || refreshed.includes(0)) fail("Codex container refreshed auth cache is invalid");
      await writeNew(refreshPath, refreshed, 0o600); refreshed.fill(0);
    }
  } finally {
    decoded.credential.fill(0); await rm(paths.home, { recursive: true, force: true }).catch(() => {}); await rm(paths.codexHome, { recursive: true, force: true }).catch(() => {}); await rm(paths.tmp, { recursive: true, force: true }).catch(() => {});
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  runWorkCodexContainerEntrypoint().catch(() => { process.stderr.write("Pixel Codex container entrypoint failed closed.\n"); process.exitCode = 70; });
}

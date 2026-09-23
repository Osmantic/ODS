#!/usr/bin/env node
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { posix } from "node:path";
import { fileURLToPath } from "node:url";

import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const SHA_RE = /^[a-f0-9]{64}$/;
const ID_RE = /^[a-z][a-z0-9-]{0,62}$/;
const RELATIVE_RE = /^[A-Za-z0-9._-]{1,255}(?:\/[A-Za-z0-9._-]{1,255})*$/;
const EXECUTABLE_RE = /^\/(?:[A-Za-z0-9._+-]+\/)*[A-Za-z0-9._+-]+$/;
const BOUNDARY = "Content-free independent command receipt. It records only fixed identifiers, counters, digests, and pass/fail state and grants no action authority.";

export class VerifierCommandError extends Error {}

function fail(message) {
  throw new VerifierCommandError(message);
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
}

export function validateVerifierCommandCheck(check) {
  if (!exactKeys(check, ["id", "kind", "criterionIndexes", "workingDirectory", "argv", "timeoutSeconds", "maxOutputBytes"])) fail("verifier command shape is invalid");
  if (!ID_RE.test(check.id ?? "") || check.kind !== "command") fail("verifier command identity is invalid");
  if (
    !Array.isArray(check.criterionIndexes) || check.criterionIndexes.length < 1 || check.criterionIndexes.length > 32
    || new Set(check.criterionIndexes).size !== check.criterionIndexes.length
    || check.criterionIndexes.some((value) => !Number.isSafeInteger(value) || value < 0 || value > 31)
  ) fail("verifier criterion mapping is invalid");
  if (!RELATIVE_RE.test(check.workingDirectory ?? "") || check.workingDirectory.split("/").some((segment) => segment === "." || segment === "..")) fail("verifier working directory is invalid");
  if (
    !Array.isArray(check.argv) || check.argv.length < 1 || check.argv.length > 64
    || check.argv.some((value) => typeof value !== "string" || value.length < 1 || value.length > 4096 || /[\u0000-\u001f\u007f]/.test(value))
    || !EXECUTABLE_RE.test(check.argv[0]) || check.argv[0].includes("//")
  ) fail("verifier argument vector is invalid");
  if (!Number.isSafeInteger(check.timeoutSeconds) || check.timeoutSeconds < 1 || check.timeoutSeconds > 3600) fail("verifier timeout is invalid");
  if (!Number.isSafeInteger(check.maxOutputBytes) || check.maxOutputBytes < 1 || check.maxOutputBytes > 16777216) fail("verifier output ceiling is invalid");
  return true;
}

function digestStream() {
  return { hash: createHash("sha256"), bytes: 0 };
}

function finishDigest(value) {
  return { bytes: value.bytes, sha256: value.hash.digest("hex") };
}

function terminate(child, signal) {
  if (!child.pid) return;
  try {
    if (process.platform === "win32") child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch { /* process already exited */ }
}

export async function runVerifierCommand(check, options) {
  validateVerifierCommandCheck(check);
  if (!SHA_RE.test(options?.planSha256 ?? "") || !SHA_RE.test(options?.candidateSha256 ?? "")) fail("verifier digest binding is invalid");
  const workspaceRoot = options?.workspaceRoot;
  if (typeof workspaceRoot !== "string" || !posix.isAbsolute(workspaceRoot) || posix.normalize(workspaceRoot) !== workspaceRoot) fail("verifier workspace root is invalid");
  const cwd = posix.join(workspaceRoot, check.workingDirectory);
  if (!cwd.startsWith(`${workspaceRoot}/`)) fail("verifier working directory escaped the candidate");
  const stdout = digestStream();
  const stderr = digestStream();
  let aggregate = 0;
  let outputLimitExceeded = false;
  let timedOut = false;
  let spawnFailed = false;
  const started = process.hrtime.bigint();
  const outcome = await new Promise((resolve) => {
    let settled = false;
    const child = spawn(check.argv[0], check.argv.slice(1), {
      cwd,
      env: { HOME: "/tmp/home", LANG: "C.UTF-8", PATH: "/opt/node/bin:/usr/bin:/bin", TMPDIR: "/tmp" },
      shell: false,
      windowsHide: true,
      detached: process.platform !== "win32",
      stdio: ["ignore", "pipe", "pipe"],
    });
    const consume = (target, chunk) => {
      target.bytes += chunk.length;
      target.hash.update(chunk);
      aggregate += chunk.length;
      if (aggregate > check.maxOutputBytes && !outputLimitExceeded) {
        outputLimitExceeded = true;
        terminate(child, "SIGKILL");
      }
    };
    child.stdout.on("data", (chunk) => consume(stdout, chunk));
    child.stderr.on("data", (chunk) => consume(stderr, chunk));
    child.on("error", () => { spawnFailed = true; });
    const timeout = setTimeout(() => {
      timedOut = true;
      terminate(child, "SIGKILL");
    }, check.timeoutSeconds * 1000);
    child.on("close", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolve({ code: Number.isSafeInteger(code) ? code : null, signal: typeof signal === "string" ? signal : null });
    });
  });
  const durationMilliseconds = Number((process.hrtime.bigint() - started) / 1000000n);
  const passed = !spawnFailed && !timedOut && !outputLimitExceeded && outcome.code === 0 && outcome.signal === null;
  return {
    schemaVersion: 1,
    operation: "pixel-independent-command-check",
    checkId: check.id,
    kind: "command",
    criterionIndexes: [...check.criterionIndexes],
    planSha256: options.planSha256,
    candidateSha256: options.candidateSha256,
    status: passed ? "pass" : "fail",
    exitCode: outcome.code,
    signal: outcome.signal,
    timedOut,
    outputLimitExceeded,
    spawnFailed,
    durationMilliseconds,
    stdout: finishDigest(stdout),
    stderr: finishDigest(stderr),
    boundary: BOUNDARY,
  };
}

async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 4 || argv[0] !== "run") fail("invalid verifier invocation");
  const { text } = await readBoundedRegularText(argv[1], 512 * 1024, "verifier command specification");
  let check;
  try { check = JSON.parse(text); } catch { fail("verifier command specification is not JSON"); }
  const result = await runVerifierCommand(check, { workspaceRoot: "/workspace", planSha256: argv[2], candidateSha256: argv[3] });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch(() => {
    process.stderr.write("pixel-work-verifier: failed closed\n");
    process.exitCode = 1;
  });
}

export const verifierCommandContract = Object.freeze({ boundary: BOUNDARY });

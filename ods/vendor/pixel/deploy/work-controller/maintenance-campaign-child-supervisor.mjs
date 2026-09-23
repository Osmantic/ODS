import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import {
  readOwnerPrivateBoundedFile,
  writeOwnerPrivateCreateNoClobber,
} from "./maintenance-secure-files.mjs";
import { WorkMaintenanceSecureFileError } from "./maintenance-secure-files.mjs";

// M5 bounded campaign-child supervisor. This module is the exact Node process
// that a nonce-named transient user service runs as its MainPID. It is the ONLY
// authority that launches the inner Python campaign executable: it does NOT use
// systemd file redirection and it is NOT a post-hoc checker. It spawns the
// exact python with shell:false, a fixed env/cwd, no stdin, bounded pipes, and
// a hard timeout; caps bytes before growth can exceed the bound; terminates the
// whole POSIX process group on overflow/timeout; and writes only strict
// owner-private create-only result/diagnostic/receipt files. It is fail-closed
// and content-free: the receipt leaks no stdout/stderr content, only hashes and
// byte counts. The child deliberately retains fixed Docker-mediated campaign
// authority via the Docker socket; that socket authority is retained by design
// and is not claimed to be network-isolated.
export class WorkCampaignChildSupervisorError extends Error {}

function fail(message) { throw new WorkCampaignChildSupervisorError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function sha256(value) { return createHash("sha256").update(value).digest("hex"); }

const NONCE_RE = /^[a-f0-9]{64}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const CONTRACT_KIND = "pixel-campaign-child-supervisor-contract";
const RECEIPT_KIND = "pixel-campaign-child-supervisor-receipt";
const MAX_CONTRACT_BYTES = 1024 * 1024;
const MAX_RECEIPT_BYTES = 1024 * 1024;
const MAX_OUTCOME_BYTES = 4096;
const STARTED_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u;

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function nonEmptyString(value, maxLength, label) {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength) fail(`${label} is invalid`);
  return value;
}

// ---------------------------------------------------------------------------
// Contract validation: the exact private launch contract the controller writes
// for a single nonce. It carries the immutable campaign operation hash, the
// exact inner python identity/contract, and the owner-private artifact paths.
// ---------------------------------------------------------------------------
export function validateCampaignChildContract(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "nonce", "campaignOperationSha256", "innerPython", "innerIdentity", "expectedOwnerUid", "resultPath", "diagnosticPath", "receiptPath", "supervisor"], "campaign child supervisor contract");
  if (value.schemaVersion !== 1 || value.kind !== CONTRACT_KIND || value.operation !== "pixel-work-model-campaign-maintenance") fail("campaign child supervisor contract kind or schema is invalid");
  if (!NONCE_RE.test(value.nonce ?? "") || value.nonce === "0".repeat(64)) fail("campaign child supervisor contract nonce is invalid");
  if (!SHA_RE.test(value.campaignOperationSha256 ?? "") || value.campaignOperationSha256 === "0".repeat(64)) fail("campaign child supervisor contract operation hash is invalid");
  if (!Number.isSafeInteger(value.expectedOwnerUid) || value.expectedOwnerUid < 1 || value.expectedOwnerUid !== expectedOwnerUid) fail("campaign child supervisor contract owner is invalid or mismatched");
  exactKeys(value.innerPython, ["path", "argv", "cwd", "env", "timeoutMilliseconds", "maxStdoutBytes", "maxStderrBytes"], "campaign child supervisor inner python");
  absolutePath(value.innerPython.path, "campaign child supervisor inner python path");
  if (!Array.isArray(value.innerPython.argv) || value.innerPython.argv.length < 1 || value.innerPython.argv.length > 256 || value.innerPython.argv.some((entry) => typeof entry !== "string" || entry.includes("\0") || entry.length === 0)) fail("campaign child supervisor inner python argv is invalid");
  absolutePath(value.innerPython.cwd, "campaign child supervisor inner python cwd");
  if (!value.innerPython.env || typeof value.innerPython.env !== "object" || Array.isArray(value.innerPython.env)) fail("campaign child supervisor inner python env is invalid");
  integer(value.innerPython.timeoutMilliseconds, 1, 2147483647, "campaign child supervisor inner python timeout");
  integer(value.innerPython.maxStdoutBytes, 1, 2147483647, "campaign child supervisor inner python stdout bound");
  integer(value.innerPython.maxStderrBytes, 1, 2147483647, "campaign child supervisor inner python stderr bound");
  exactKeys(value.innerIdentity, ["executableSha256", "argvSha256", "contractSha256"], "campaign child supervisor inner identity");
  for (const key of ["executableSha256", "argvSha256", "contractSha256"]) {
    if (!SHA_RE.test(value.innerIdentity[key] ?? "") || value.innerIdentity[key] === "0".repeat(64)) fail(`campaign child supervisor inner ${key} is invalid`);
  }
  for (const key of ["resultPath", "diagnosticPath", "receiptPath"]) absolutePath(value[key], `campaign child supervisor ${key}`);
  exactKeys(value.supervisor, ["path", "sha256", "unitName"], "campaign child supervisor identity");
  absolutePath(value.supervisor.path, "campaign child supervisor path");
  if (!SHA_RE.test(value.supervisor.sha256 ?? "") || value.supervisor.sha256 === "0".repeat(64)) fail("campaign child supervisor content hash is invalid");
  if (typeof value.supervisor.unitName !== "string" || !/^pixel-campaign-child-([a-f0-9]{64})\.service$/u.test(value.supervisor.unitName)) fail("campaign child supervisor unit name is invalid");
  if (value.supervisor.unitName !== `pixel-campaign-child-${value.nonce}.service`) fail("campaign child supervisor unit name is not bound to the contract nonce");
  return structuredClone(value);
}

// The deterministic inner-python identity recomputed by the supervisor from the
// exact bytes/argv/contract it actually launches. The controller binds these
// into the contract; the supervisor must reproduce them exactly to prove no
// executable/argv/config substitution before launching.
export async function deriveInnerIdentity(innerPython) {
  let bytes;
  try { bytes = await readFile(innerPython.path); }
  catch { fail("campaign child supervisor inner python executable could not be proven"); }
  const executableSha256 = sha256(bytes);
  const argvSha256 = sha256(canonical(innerPython.argv));
  const contractSha256 = sha256(canonical(innerPython));
  return Object.freeze({ executableSha256, argvSha256, contractSha256 });
}

async function readOwnerPrivateJson(path, maxBytes, label, expectedOwnerUid, validator) {
  let record;
  try {
    record = await readOwnerPrivateBoundedFile(path, maxBytes, label, expectedOwnerUid);
  } catch (error) {
    if (error instanceof WorkMaintenanceSecureFileError) fail(error.message);
    throw error;
  }
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  let value;
  try { value = parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
  return validator(value, expectedOwnerUid);
}

// Create-only no-clobber durable publication (shared primitive). The receipt
// target is never overwritten or replaced: an existing regular file, symlink,
// hardlink, or concurrent publication always fails closed.
async function writeOwnerPrivateAtomic(targetPath, value, expectedOwnerUid) {
  try {
    await writeOwnerPrivateCreateNoClobber(targetPath, value, expectedOwnerUid);
  } catch (error) {
    if (error instanceof WorkMaintenanceSecureFileError) fail(error.message);
    throw error;
  }
}

// Strict durable receipt validation. Binds nonce, campaign operation hash,
// exact inner identity/contract, outcome, and content-free stdout/stderr
// hashes and byte counts without leaking any output content.
export function validateCampaignChildReceipt(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "nonce", "campaignOperationSha256", "innerIdentity", "outcome", "stdout", "stderr", "terminalTime"], "campaign child supervisor receipt");
  if (value.schemaVersion !== 1 || value.kind !== RECEIPT_KIND || value.operation !== "pixel-work-model-campaign-maintenance") fail("campaign child supervisor receipt kind or schema is invalid");
  if (!NONCE_RE.test(value.nonce ?? "") || value.nonce === "0".repeat(64)) fail("campaign child supervisor receipt nonce is invalid");
  if (!SHA_RE.test(value.campaignOperationSha256 ?? "") || value.campaignOperationSha256 === "0".repeat(64)) fail("campaign child supervisor receipt operation hash is invalid");
  exactKeys(value.innerIdentity, ["executableSha256", "argvSha256", "contractSha256"], "campaign child supervisor receipt inner identity");
  for (const key of ["executableSha256", "argvSha256", "contractSha256"]) {
    if (!SHA_RE.test(value.innerIdentity[key] ?? "") || value.innerIdentity[key] === "0".repeat(64)) fail(`campaign child supervisor receipt inner ${key} is invalid`);
  }
  exactKeys(value.outcome, ["exitCode", "signal", "timedOut", "outputOverflow", "spawnError"], "campaign child supervisor receipt outcome");
  if (value.outcome.exitCode !== null && !Number.isSafeInteger(value.outcome.exitCode)) fail("campaign child supervisor receipt exit code is invalid");
  if (value.outcome.signal !== null && (typeof value.outcome.signal !== "string" || value.outcome.signal.length === 0 || value.outcome.signal.length > 64)) fail("campaign child supervisor receipt signal is invalid");
  for (const key of ["timedOut", "spawnError"]) {
    if (typeof value.outcome[key] !== "boolean") fail(`campaign child supervisor receipt ${key} is invalid`);
  }
  if (value.outcome.outputOverflow !== null && value.outcome.outputOverflow !== "stdout" && value.outcome.outputOverflow !== "stderr") fail("campaign child supervisor receipt outputOverflow is invalid");
  // Receipt outcome coherence (P1): reject contradictory outcomes. Success means
  // exit code zero, no signal, no timeout, no overflow, and no spawn error.
  // Timeout, overflow, spawn error, signal, and exit-code combinations must obey
  // one explicit mutually coherent contract; classification may never normalize
  // contradictions.
  const { exitCode, signal, timedOut, outputOverflow, spawnError } = value.outcome;
  if (spawnError === true) {
    if (exitCode !== null || signal !== null || timedOut === true || outputOverflow !== null) fail("campaign child supervisor receipt outcome is contradictory (spawn error)");
  } else if (timedOut === true) {
    if (exitCode !== null || signal !== null || outputOverflow !== null || spawnError !== false) fail("campaign child supervisor receipt outcome is contradictory (timeout)");
  } else if (outputOverflow !== null) {
    if (exitCode !== null || signal !== null || timedOut !== false || spawnError !== false) fail("campaign child supervisor receipt outcome is contradictory (overflow)");
  } else if (signal !== null) {
    if (exitCode !== null || timedOut !== false || outputOverflow !== null || spawnError !== false) fail("campaign child supervisor receipt outcome is contradictory (signal)");
  } else {
    if (exitCode === null || timedOut !== false || outputOverflow !== null || spawnError !== false) fail("campaign child supervisor receipt outcome is contradictory (exit)");
  }
  for (const stream of ["stdout", "stderr"]) {
    exactKeys(value[stream], ["bytes", "sha256"], `campaign child supervisor receipt ${stream}`);
    integer(value[stream].bytes, 0, 2147483647, `campaign child supervisor receipt ${stream} bytes`);
    if (!SHA_RE.test(value[stream].sha256 ?? "")) fail(`campaign child supervisor receipt ${stream} hash is invalid`);
  }
  if (!STARTED_RE.test(value.terminalTime ?? "") || !Number.isFinite(Date.parse(value.terminalTime))) fail("campaign child supervisor receipt terminal time is invalid");
  return structuredClone(value);
}

// ---------------------------------------------------------------------------
// Bounded child launch: spawn the exact python executable with shell:false, a
// fixed env/cwd, no stdin, bounded pipes, and a hard timeout. On POSIX the child
// is launched in its own process group (detached) so a timeout or overflow can
// SIGKILL the whole group, including descendants that inherited the pipes. After
// a short fixed kill grace the supervisor destroys its pipe handles so
// completion never depends on descendant-held descriptors, and it resolves
// exactly once with a truthful timeout/overflow outcome even if `close` never
// arrives. The process-group SIGKILL only reaches descendants that remain in
// the inner child's process group; it does NOT kill an arbitrary descendant
// that calls setsid and leaves that group. The transient unit's systemd
// KillMode=control-group plus RuntimeMaxSec is the cgroup backstop that can
// reach such a detached descendant; that backstop requires later real-systemd
// qualification and is not claimed by this supervisor alone.
// ---------------------------------------------------------------------------
export function launchBoundedChild(innerPython, spawnChild = spawn, options = {}) {
  const killGraceMs = options.killGraceMs ?? 500;
  return new Promise((resolvePromise) => {
    const stdoutChunks = [];
    const stderrChunks = [];
    const stdoutRef = { value: 0 };
    const stderrRef = { value: 0 };
    let force = null; // null | { timedOut: true } | { overflow: "stdout" | "stderr" }
    let timer = null;
    let graceTimer = null;
    let settled = false;
    let child = null;

    // Initialize all state before calling spawnChild (P1): spawnChild may throw
    // synchronously, and finish() must never reference uninitialized refs.
    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (graceTimer) clearTimeout(graceTimer);
      const stdout = Buffer.concat(stdoutChunks, stdoutRef.value);
      const stderr = Buffer.concat(stderrChunks, stderrRef.value);
      resolvePromise(Object.freeze({
        ...outcome,
        stdoutBytes: stdoutRef.value,
        stderrBytes: stderrRef.value,
        stdoutSha256: sha256(stdout),
        stderrSha256: sha256(stderr),
        stdout,
        stderr,
      }));
    };

    // Canonical first-cause forced outcome: a forced termination (timeout or
    // overflow) always resolves to exitCode=null, signal=null, exactly one of
    // timedOut/outputOverflow, and spawnError=false, regardless of the later
    // close/error event. The first cause wins and competing timers are cleared.
    const canonicalForced = () => {
      if (!force) return null;
      return {
        exitCode: null,
        signal: null,
        timedOut: force.timedOut === true,
        overflow: force.overflow ?? null,
        spawnError: false,
      };
    };

    const killProcessGroup = () => {
      if (!child || !child.pid) return;
      try {
        if (process.platform !== "win32") process.kill(-child.pid, "SIGKILL");
        else child.kill("SIGKILL");
      } catch {}
    };

    // At the bounded grace deadline, destroy the supervisor's pipe handles and
    // resolve once with the canonical first-cause forced outcome if close has
    // not arrived. This timer is intentionally NOT unref'd: it is the only timer
    // required to prove bounded completion independent of a close event.
    const armGrace = () => {
      if (graceTimer) return;
      graceTimer = setTimeout(() => {
        try { if (child?.stdout) child.stdout.destroy(); } catch {}
        try { if (child?.stderr) child.stderr.destroy(); } catch {}
        const forced = canonicalForced();
        if (forced) finish(forced);
      }, killGraceMs);
    };

    const boundStream = (chunks, maxBytes, streamName, byteRef) => (chunk) => {
      if (force) return;
      if (byteRef.value >= maxBytes) {
        force = { overflow: streamName };
        killProcessGroup();
        armGrace();
        return;
      }
      const take = Math.min(chunk.length, maxBytes - byteRef.value);
      chunks.push(chunk.subarray(0, take));
      byteRef.value += take;
      if (chunk.length > take) {
        force = { overflow: streamName };
        killProcessGroup();
        armGrace();
      }
    };

    try {
      child = spawnChild(innerPython.path, innerPython.argv, {
        cwd: innerPython.cwd,
        env: innerPython.env,
        stdio: ["ignore", "pipe", "pipe"],
        shell: false,
        windowsHide: true,
        detached: process.platform !== "win32",
      });
    } catch (error) {
      finish({ exitCode: null, signal: null, timedOut: false, overflow: null, spawnError: true });
      return;
    }

    if (child.stdout) child.stdout.on("data", boundStream(stdoutChunks, innerPython.maxStdoutBytes, "stdout", stdoutRef));
    if (child.stderr) child.stderr.on("data", boundStream(stderrChunks, innerPython.maxStderrBytes, "stderr", stderrRef));
    child.on("error", (error) => {
      const forced = canonicalForced();
      if (forced) { finish(forced); return; }
      finish({ exitCode: null, signal: null, timedOut: false, overflow: null, spawnError: true });
    });
    child.on("close", (code, signal) => {
      const forced = canonicalForced();
      if (forced) { finish(forced); return; }
      finish({ exitCode: code, signal, timedOut: false, overflow: null, spawnError: false });
    });

    timer = setTimeout(() => {
      // The first cause wins. An output overflow that happened before the
      // wall-clock timeout must remain the canonical cause even if the timeout
      // fires before close or the grace settlement; never overwrite an
      // existing forced cause with a later timeout.
      if (!force) force = { timedOut: true };
      killProcessGroup();
      armGrace();
    }, innerPython.timeoutMilliseconds);
  });
}

// ---------------------------------------------------------------------------
// Durable artifact writer for a single byte buffer (result = bounded stdout,
// diagnostic = bounded stderr). Only the bounded prefix is ever persisted.
// ---------------------------------------------------------------------------
async function writeBoundedArtifact(path, bytes, maxBytes, label, expectedOwnerUid) {
  const bounded = bytes.subarray(0, Math.min(bytes.length, maxBytes));
  try {
    await writeOwnerPrivateCreateNoClobber(path, bounded, expectedOwnerUid);
  } catch (error) {
    if (error instanceof WorkMaintenanceSecureFileError) fail(error.message);
    throw error;
  }
}

// The exact supervisor outcome shape written into the durable receipt. It never
// carries stdout/stderr content.
function outcomeRecord(result) {
  return Object.freeze({
    exitCode: result.exitCode,
    signal: result.signal,
    timedOut: result.timedOut,
    outputOverflow: result.overflow,
    spawnError: result.spawnError === true,
  });
}

// ---------------------------------------------------------------------------
// Orchestration: prove the exact inner identity, launch the bounded child, and
// write the durable result/diagnostic/receipt. Returns the receipt value.
// ---------------------------------------------------------------------------
export async function superviseCampaignChild(argv, options = {}) {
  const parsed = parseSupervisorArgs(argv);
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  const contract = await readOwnerPrivateJson(parsed.contract, MAX_CONTRACT_BYTES, "campaign child supervisor contract", expectedOwnerUid, (value) => validateCampaignChildContract(value, expectedOwnerUid));
  if (contract.nonce !== parsed.nonce) fail("campaign child supervisor nonce does not match the contract");
  if (!samePath(contract.receiptPath, parsed.receipt)) fail("campaign child supervisor receipt path does not match the contract");
  const runningModulePath = fileURLToPath(import.meta.url);
  let runningReal;
  try { runningReal = await (await import("node:fs/promises")).realpath(runningModulePath); } catch { runningReal = null; }
  if (!runningReal || !samePath(runningReal, contract.supervisor.path)) fail("campaign child supervisor identity is substituted");
  let supervisorBytes;
  try { supervisorBytes = await readFile(runningReal); } catch { supervisorBytes = null; }
  if (!supervisorBytes || sha256(supervisorBytes) !== contract.supervisor.sha256) fail("campaign child supervisor content hash is substituted");
  const derived = await deriveInnerIdentity(contract.innerPython);
  if (derived.executableSha256 !== contract.innerIdentity.executableSha256) fail("campaign child supervisor inner executable identity is substituted");
  if (derived.argvSha256 !== contract.innerIdentity.argvSha256) fail("campaign child supervisor inner argv identity is substituted");
  if (derived.contractSha256 !== contract.innerIdentity.contractSha256) fail("campaign child supervisor inner contract identity is substituted");
  const result = await launchBoundedChild(contract.innerPython, options.spawnChild);
  const terminalTime = new Date().toISOString();
  const receipt = Object.freeze({
    schemaVersion: 1,
    kind: RECEIPT_KIND,
    operation: "pixel-work-model-campaign-maintenance",
    nonce: contract.nonce,
    campaignOperationSha256: contract.campaignOperationSha256,
    innerIdentity: structuredClone(contract.innerIdentity),
    outcome: outcomeRecord(result),
    stdout: Object.freeze({ bytes: result.stdoutBytes, sha256: result.stdoutSha256 }),
    stderr: Object.freeze({ bytes: result.stderrBytes, sha256: result.stderrSha256 }),
    terminalTime,
  });
  if (!STARTED_RE.test(terminalTime) || !Number.isFinite(Date.parse(terminalTime))) fail("campaign child supervisor terminal time is invalid");
  // P0: validate the constructed receipt against the strict coherence contract
  // BEFORE publishing any result/diagnostic/receipt artifact. A contradictory or
  // non-canonical outcome must never be durably published.
  validateCampaignChildReceipt(receipt, expectedOwnerUid);
  await writeBoundedArtifact(contract.resultPath, result.stdout, contract.innerPython.maxStdoutBytes, "campaign child supervisor result", expectedOwnerUid);
  await writeBoundedArtifact(contract.diagnosticPath, result.stderr, contract.innerPython.maxStderrBytes, "campaign child supervisor diagnostic", expectedOwnerUid);
  await writeOwnerPrivateAtomic(contract.receiptPath, receipt, expectedOwnerUid);
  return Object.freeze({ receipt, contract });
}

export function parseSupervisorArgs(argv = []) {
  if (!Array.isArray(argv) || argv.length !== 7 || argv[0] !== "run" || argv[1] !== "--nonce" || argv[3] !== "--contract" || argv[5] !== "--receipt") {
    fail("Usage: maintenance-campaign-child-supervisor.mjs run --nonce NONCE --contract CONTRACT --receipt RECEIPT");
  }
  const [nonce, contract, receipt] = [argv[2], argv[4], argv[6]];
  if (!NONCE_RE.test(nonce ?? "")) fail("campaign child supervisor nonce is invalid");
  if (contract === "" || receipt === "") fail("campaign child supervisor paths are empty");
  return { nonce, contract, receipt };
}

export async function main(argv = process.argv.slice(2)) {
  const receipt = await superviseCampaignChild(argv);
  process.stdout.write(`${JSON.stringify(receipt.receipt)}\n`);
  return 0;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-campaign-child-supervisor: ${error instanceof WorkCampaignChildSupervisorError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, readFile, realpath, readdir } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { promisify } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { deriveProcessIdentity, isProcessAlive, readCurrentBootId, requireLiveCampaignGuardianLease } from "./maintenance-recovery-guardian-lease.mjs";
import { systemdShowUnit } from "./maintenance-recovery-guardian-supervisor.mjs";
import { resolveTrustedExecutable } from "./maintenance-recovery-guardian-systemd.mjs";
import { advanceCampaignRecoveryJournal } from "./maintenance-recovery-journal.mjs";
import { deriveInnerIdentity, validateCampaignChildContract, validateCampaignChildReceipt } from "./maintenance-campaign-child-supervisor.mjs";
import {
  readOwnerPrivateBoundedFile,
  writeOwnerPrivateCreateNoClobber,
} from "./maintenance-secure-files.mjs";

const execute = promisify(execFile);

// M5 campaign-child launch orchestration. This module is the controller-side
// authority that durably advances the real campaign recovery journal through
// campaign-child-authorized -> campaign-child-active -> comparison-cleanup-pending
// while starting a nonce-named transient user service exactly once. It treats
// every ambiguity as fail-closed: after any launch request or ambiguous effect
// it never cancels the journal and never relaunches automatically.
export class WorkCampaignChildLaunchError extends Error {}

function fail(message) { throw new WorkCampaignChildLaunchError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function sha256(value) { return createHash("sha256").update(value).digest("hex"); }

const NONCE_RE = /^[a-f0-9]{64}$/u;
const UNIT_RE = /^pixel-campaign-child-[a-f0-9]{64}\.service$/u;
const MAX_CONTRACT_BYTES = 1024 * 1024;
const MAX_RECEIPT_BYTES = 1024 * 1024;
const RECEIPT_WAIT_MS = 5 * 60 * 1000;
const RECEIPT_POLL_MS = 250;

// The exact validated static property set applied to every transient campaign
// child unit. No invented option, no second ExecStart, no LogsSizeMax, no
// StandardOutputTruncate. The unit is a Type=exec service with control-group
// descendant cleanup, no auto-restart, bounded stop behavior, and
// no-new-privileges. Namespace/filesystem hardening that would break the real
// Docker-mediated campaign comparisons is deliberately NOT claimed.
export const CAMPAIGN_CHILD_UNIT_PROPERTIES = Object.freeze([
  "Type=exec",
  "KillMode=control-group",
  "NoNewPrivileges=yes",
  "Restart=no",
  "TimeoutStopSec=15",
]);

// Fixed grace added to the inner-python bound to derive the transient unit
// RuntimeMaxSec, so a stuck supervisor (or a supervisor whose descendant keeps
// the pipes open) cannot live forever even if its own kill grace fails.
// KillMode=control-group + RuntimeMaxSec is the cgroup backstop that can reach
// a detached descendant that calls setsid and leaves the inner child's process
// group; that backstop requires later real-systemd qualification and is not
// claimed by the supervisor's process-group SIGKILL alone.
export const CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS = 30;

export function deriveCampaignChildRuntimeMaxSec(timeoutMilliseconds) {
  if (!Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1) fail("campaign child runtime bound is invalid");
  return Math.ceil(timeoutMilliseconds / 1000) + CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS;
}

export function validateCampaignChildNonce(nonce) {
  if (typeof nonce !== "string" || !NONCE_RE.test(nonce) || nonce === "0".repeat(64)) fail("campaign child launch nonce is not the exact nonzero 64-hex identity");
  return nonce;
}

export function deriveCampaignChildUnitName(nonce) {
  validateCampaignChildNonce(nonce);
  return `pixel-campaign-child-${nonce}.service`;
}

export function defaultCampaignChildNonce() {
  return randomBytes(32).toString("hex");
}

function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}

function parseMainPid(value) {
  const pid = Number(value);
  return Number.isSafeInteger(pid) && pid >= 1 ? pid : 0;
}

// ---------------------------------------------------------------------------
// systemd-run argv construction. Builds argv as an array and resolves trusted
// absolute executables; never shell-interpolates.
// ---------------------------------------------------------------------------
export function buildSystemdRunArgv(unitName, contract, ctx) {
  if (!UNIT_RE.test(unitName ?? "")) fail("campaign child unit name is invalid");
  const argv = [
    "--user",
    `--unit=${unitName}`,
  ];
  for (const property of CAMPAIGN_CHILD_UNIT_PROPERTIES) argv.push(`--property=${property}`);
  argv.push(`--property=RuntimeMaxSec=${deriveCampaignChildRuntimeMaxSec(ctx.innerPython.timeoutMilliseconds)}`);
  argv.push(`--working-directory=${ctx.innerPython.cwd}`);
  for (const [key, value] of Object.entries(ctx.innerPython.env)) {
    if (key.includes("=") || key.includes("\0") || value.includes("\0") || /[\r\n]/u.test(value)) fail("campaign child unit fixed environment is unsafe");
    argv.push(`--setenv=${key}=${value}`);
  }
  argv.push("--", ctx.nodePath, ctx.supervisorPath, "run", "--nonce", contract.nonce, "--contract", contract.contractPath, "--receipt", contract.receiptPath);
  return argv;
}

async function runSystemdRunDefault(unitName, contract, ctx) {
  const systemdRunPath = await resolveTrustedExecutable(ctx.systemdRunPath ?? "/usr/bin/systemd-run", { expectedOwnerUid: ctx.expectedOwnerUid });
  const argv = buildSystemdRunArgv(unitName, contract, ctx);
  const { stdout, stderr } = await execute(systemdRunPath, argv, { encoding: "utf8", timeout: 20000, maxBuffer: 1024 * 1024, env: ctx.systemdEnv });
  return { code: 0, stdout: stdout ?? "", stderr: stderr ?? "" };
}

// ---------------------------------------------------------------------------
// Owner-private durable write helpers.
// ---------------------------------------------------------------------------
// Create-only no-clobber durable publication (shared primitive). The contract
// target is never overwritten or replaced: an existing regular file, symlink,
// hardlink, or concurrent publication always fails closed.
async function writeOwnerPrivateCreate(targetPath, value, expectedOwnerUid) {
  await writeOwnerPrivateCreateNoClobber(targetPath, value, expectedOwnerUid);
}

// Create and prove an owner-private state directory for the contract, receipt,
// result, and diagnostic artifacts. Never trusts an ambient/attacker path.
async function secureOutDir(outDir, expectedOwnerUid) {
  absolutePath(outDir, "campaign child state directory");
  await mkdir(outDir, { recursive: true });
  if (process.platform === "win32") return;
  await chmod(outDir, 0o700);
  let real, info;
  try { [real, info] = await Promise.all([realpath(outDir), lstat(outDir, { bigint: true })]); }
  catch { fail("campaign child state directory could not be proven"); }
  if (!samePath(real, outDir)) fail("campaign child state directory is a substituted symlink");
  if (!info.isDirectory() || info.isSymbolicLink()) fail("campaign child state directory is not a real directory");
  if (Number(info.uid) !== expectedOwnerUid) fail("campaign child state directory is not owned by the expected caller");
  if ((info.mode & 0o022n) !== 0n) fail("campaign child state directory is group/world writable");
}

async function lstatExact(path) {
  try { return await lstat(path); } catch (error) { if (error?.code === "ENOENT") return null; throw error; }
}

async function readOwnerPrivateJson(path, maxBytes, label, expectedOwnerUid, validator) {
  const record = await readOwnerPrivateBoundedFile(path, maxBytes, label, expectedOwnerUid);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  let parsed;
  try { parsed = parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
  return { bytes: record.bytes, text, value: validator(parsed, expectedOwnerUid) };
}

// ---------------------------------------------------------------------------
// Journal + identity + facts proofs.
// ---------------------------------------------------------------------------
async function proveUnitAbsent(unitName, deps) {
  const show = await deps.showUnit(unitName, deps.systemdDeps);
  if (!show.ok) return { absent: false, error: "campaign child unit existence could not be proven" };
  if (show.facts.LoadState === "not-found") return { absent: true };
  return { absent: false, error: "campaign child unit already exists under the exact nonce name" };
}

async function captureActiveIdentity(unitName, ctx, deps) {
  const show = await deps.showUnit(unitName, deps.systemdDeps);
  if (!show.ok) fail("campaign child unit could not be shown after start");
  const facts = show.facts;
  if (facts.LoadState !== "loaded" || facts.ActiveState !== "active" || facts.SubState !== "running") fail("campaign child unit is not active/running after start");
  const mainPid = parseMainPid(facts.MainPID);
  if (mainPid === 0) fail("campaign child unit MainPID is invalid after start");
  if (typeof facts.InvocationID !== "string" || !/^[0-9a-f]{32}$/u.test(facts.InvocationID)) fail("campaign child unit invocation id is invalid");
  if (typeof facts.ControlGroup !== "string" || !facts.ControlGroup.endsWith(".service") || facts.ControlGroup.includes("\0") || /[\r\n]/u.test(facts.ControlGroup)) fail("campaign child unit control group is invalid");
  const processIdentity = await deps.deriveProcessIdentity(mainPid);
  return { facts, processIdentity, mainPid };
}

async function verifyActiveIdentity(active, ctx, contract, nonce, deps = {}) {
  const identity = active.processIdentity;
  if (identity.pid !== active.mainPid) fail("campaign child supervisor MainPID does not match /proc identity");
  if (identity.bootId !== ctx.expectedBootId) fail("campaign child supervisor boot id does not match the current boot");
  if (identity.uid !== String(ctx.expectedOwnerUid)) fail("campaign child supervisor owner is mismatched");
  if (!samePath(identity.exe, ctx.nodePath)) fail("campaign child supervisor executable is not the trusted node");
  const expectedArgv = [ctx.nodePath, ctx.supervisorPath, "run", "--nonce", nonce, "--contract", contract.contractPath, "--receipt", contract.receiptPath];
  if (canonical(identity.argv) !== canonical(expectedArgv)) fail("campaign child supervisor argv does not match the exact launch contract");
  const cgroupMatched = identity.cgroup.some((line) => exactCgroupLineMatches(line, active.facts.ControlGroup));
  if (!cgroupMatched) fail("campaign child supervisor cgroup does not match the unit control group");
  const readInvocationId = deps.readInvocationId ?? depsReadInvocationId;
  const invocationId = await readInvocationId(active.mainPid);
  if (invocationId && invocationId !== active.facts.InvocationID) fail("campaign child supervisor invocation id does not match the unit");
  if (contract.supervisor.sha256 !== ctx.supervisorSha256) fail("campaign child supervisor module hash is not bound to the launch contract");
  if (contract.supervisor.unitName !== `pixel-campaign-child-${nonce}.service`) fail("campaign child supervisor unit name is not bound to the launch contract");
}

async function depsReadInvocationId(pid) {
  try {
    const environ = await readFile(`/proc/${pid}/environ`, "utf8");
    for (const part of environ.split("\0")) {
      const idx = part.indexOf("=");
      if (idx > 0 && part.slice(0, idx) === "INVOCATION_ID" && part.length > idx + 1) return part.slice(idx + 1);
    }
  } catch {}
  return null;
}

// Exact cgroup membership proof (P1): normalize and compare the exact cgroup
// path, never substring membership. A process belongs to the unit only when its
// cgroup line equals the exact unit ControlGroup path.
export function exactCgroupLineMatches(line, cgroup) {
  if (typeof line !== "string") return false;
  // Format: hierarchy-ID:controller-list:path
  const first = line.indexOf(":");
  if (first < 0) return false;
  const second = line.indexOf(":", first + 1);
  if (second < 0) return false;
  const path = line.slice(second + 1);
  return path === cgroup;
}

async function cgroupContainsLiveProcess(cgroup) {
  if (!cgroup) return true;
  let entries;
  try { entries = await readdir("/proc"); } catch { return true; }
  for (const entry of entries) {
    if (!/^[0-9]+$/u.test(entry)) continue;
    const pid = Number(entry);
    if (!Number.isSafeInteger(pid) || pid < 1 || !isProcessAlive(pid)) continue;
    try {
      const text = await readFile(`/proc/${entry}/cgroup`, "utf8");
      if (text.split("\n").some((line) => exactCgroupLineMatches(line, cgroup))) return true;
    } catch {}
  }
  return false;
}

async function proveCgroupEmptyDefault(cgroup) {
  if (await cgroupContainsLiveProcess(cgroup)) fail("campaign child unit cgroup still contains a live descendant");
}

// Boolean exact-empty proof for the M6 recovery guardian. Returns true only when
// no live /proc process is a member of the exact unit control group. Missing or
// unreadable /proc fails closed (returns false), never inferring emptiness.
export async function proveCampaignChildCgroupEmpty(cgroup) {
  return !(await cgroupContainsLiveProcess(cgroup));
}

// Strict terminal fact contract (P1): after receipt, require either the same
// loaded unit with the same InvocationID and exact ControlGroup in an accepted
// terminal state, or a narrowly defined not-found case backed by the
// already-recorded identity plus an exact empty cgroup proof. Any inconsistent
// LoadState, ActiveState, SubState, InvocationID, ControlGroup, or MainPID
// reuse/substitution is ambiguous and holds production.
export function isAcceptedTerminalState(show) {
  const facts = show.facts;
  if (facts.LoadState === "loaded") {
    return facts.ActiveState === "inactive" && facts.SubState === "dead";
  }
  return false;
}

async function proveTerminal(active, unitName, ctx, deps) {
  const recordedPid = active.processIdentity.pid;
  if (await deps.isProcessAlive(recordedPid)) {
    const now = await deps.deriveProcessIdentity(recordedPid);
    if (canonical(now) === canonical(active.processIdentity)) fail("campaign child supervisor is still running under the exact recorded identity");
  }
  await deps.proveCgroupEmpty(active.facts.ControlGroup);
  const show = await deps.showUnit(unitName, deps.systemdDeps);
  if (!show.ok) fail("campaign child unit identity could not be re-proven after completion");
  if (show.facts.LoadState === "not-found") {
    // Narrowly defined not-found case: backed by the already-recorded identity
    // and the exact empty cgroup proof already performed above.
    return;
  }
  if (show.facts.LoadState !== "loaded") fail("campaign child unit LoadState is inconsistent after completion");
  if (show.facts.InvocationID !== active.facts.InvocationID) fail("campaign child unit InvocationID was substituted after completion");
  if (show.facts.ControlGroup !== active.facts.ControlGroup) fail("campaign child unit ControlGroup was substituted after completion");
  if (show.facts.MainPID !== "0" && show.facts.MainPID !== active.facts.MainPID) fail("campaign child unit MainPID was substituted after completion");
  if (!isAcceptedTerminalState(show)) fail("campaign child unit is not in an accepted terminal state after completion");
}

async function receiptIsAbsent(receiptPath) {
  return (await lstatExact(receiptPath)) === null;
}

async function waitForReceipt(receiptPath, expectedOwnerUid, deps) {
  const deadline = Date.now() + deps.receiptWaitMs;
  while (true) {
    if (await receiptIsAbsent(receiptPath)) {
      if (Date.now() >= deadline) fail("campaign child supervisor receipt did not arrive within the bounded window");
      await new Promise((r) => setTimeout(r, deps.receiptPollMs));
      continue;
    }
    // The receipt path now exists. Only retry while it is provably absent; once a
    // path exists, malformed JSON, symlink, hardlink, wrong owner/mode, oversize,
    // or schema/coherence failure is permanent tamper/ambiguity and must fail
    // immediately rather than being hidden until the deadline (P1).
    const record = await readOwnerPrivateJson(receiptPath, MAX_RECEIPT_BYTES, "campaign child supervisor receipt", expectedOwnerUid, (value) => validateCampaignChildReceipt(value, expectedOwnerUid));
    return record.value;
  }
}

// ---------------------------------------------------------------------------
// Public orchestration entry point.
// ---------------------------------------------------------------------------
export async function launchCampaignChildSupervised(ctx, deps = {}) {
  const expectedOwnerUid = ctx.expectedOwnerUid;
  const advanceJournal = deps.advanceJournal ?? advanceCampaignRecoveryJournal;
  const requireLease = deps.requireLease ?? requireLiveCampaignGuardianLease;
  const showUnit = deps.showUnit ?? systemdShowUnit;
  const deriveIdentity = deps.deriveProcessIdentity ?? deriveProcessIdentity;
  const runSystemdRun = deps.runSystemdRun ?? runSystemdRunDefault;
  const systemdDeps = deps.systemdDeps ?? { expectedOwnerUid, env: ctx.systemdEnv };
  const isAlive = deps.isProcessAlive ?? isProcessAlive;
  const proveCgroupEmpty = deps.proveCgroupEmpty ?? proveCgroupEmptyDefault;
  const classifySupervised = deps.classifySupervised;
  if (typeof classifySupervised !== "function") fail("campaign child supervisor classification dependency is missing");
  const nonce = validateCampaignChildNonce(await (deps.randomNonce ?? defaultCampaignChildNonce)());
  const unitName = deriveCampaignChildUnitName(nonce);
  const expectedBootId = ctx.expectedBootId ?? await readCurrentBootId();
  if (!expectedBootId) fail("campaign child boot identity cannot be proven; refusing to launch");
  await secureOutDir(ctx.outDir, expectedOwnerUid);
  const contractPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`);
  const receiptPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`);
  const resultPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.result`);
  const diagnosticPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`);

  // P0: every exception raised before the exact comparison-cleanup-pending
  // advance is converted to a content-free WorkCampaignChildLaunchError
  // regardless of the original class (WorkMaintenanceSecureFileError, ordinary
  // Error, guardian/journal/systemd errors, supervisor validation errors, or any
  // other unexpected class). The controller therefore holds production and never
  // restores on any pre-terminal or unexpected failure. This content-free catch
  // covers every operation through the exact durable comparison-cleanup-pending
  // transition and nothing after it. Classification is deliberately performed
  // OUTSIDE this pre-terminal try/catch so that no error can be preserved or
  // restored by name alone; only the controller's `instanceof CampaignFailure`
  // mechanical gate may take the restoration path.
  let supervised;
  let active;
  let receipt;
  try {
    // 1. Durable authorize BEFORE any transient unit or worker exists.
    await advanceJournal(ctx.custodyLockPath, expectedOwnerUid, "campaign-child-authorized", ctx.journalIdentity, { campaignChildNonce: nonce });

    // 2. Reassert the exact live guardian lease AND the exact same authorized
    //    journal immediately before starting the unit. Any failure -> no launch.
    await requireLease(ctx.custodyLockPath, expectedOwnerUid, ctx.configSha256, {
      maxAgeMs: ctx.leaseMaxAgeMs, bootId: ctx.leaseBootId, unit: ctx.guardianUnit,
      configPath: ctx.configPath, custodyIdentitySha256: ctx.custodyIdentitySha256,
      guardianModuleSha256: ctx.guardianModuleSha256, guardianModulePath: ctx.guardianModulePath,
      expectedUnitSha256: ctx.guardianUnitSha256, expectedNodePath: ctx.nodePath,
      campaignOperationSha256: ctx.campaignOperationSha256, journalIdentitySha256: ctx.journalIdentitySha256,
    });
    await advanceJournal(ctx.custodyLockPath, expectedOwnerUid, "campaign-child-authorized", ctx.journalIdentity, { campaignChildNonce: nonce });

    // 3. Prove the full-nonce unit name is absent under a strict fact contract.
    const absent = await proveUnitAbsent(unitName, { showUnit, systemdDeps });
    if (!absent.absent) fail(absent.error || "campaign child unit is not provably absent");

    // 4. Write the exact owner-private launch contract.
    const innerIdentity = await deriveInnerIdentity(ctx.innerPython);
    const contract = Object.freeze({
      schemaVersion: 1,
      kind: "pixel-campaign-child-supervisor-contract",
      operation: "pixel-work-model-campaign-maintenance",
      nonce,
      campaignOperationSha256: ctx.campaignOperationSha256,
      innerPython: structuredClone(ctx.innerPython),
      innerIdentity,
      expectedOwnerUid,
      resultPath,
      diagnosticPath,
      receiptPath,
      supervisor: Object.freeze({ path: ctx.supervisorPath, sha256: ctx.supervisorSha256, unitName }),
    });
    validateCampaignChildContract(contract, expectedOwnerUid);
    await writeOwnerPrivateCreate(contractPath, contract, expectedOwnerUid);

    // 5. Start the transient unit exactly once.
    const start = await runSystemdRun(unitName, { ...contract, contractPath, receiptPath }, ctx);
    if (start.code !== 0) fail("campaign child transient unit could not be started");

    // 6. Obtain strict unit facts + full /proc identity of the supervisor MainPID.
    active = await captureActiveIdentity(unitName, ctx, { showUnit, systemdDeps, deriveProcessIdentity: deriveIdentity });
    await verifyActiveIdentity(active, { ...ctx, expectedBootId }, { ...contract, contractPath, receiptPath }, nonce, { readInvocationId: deps.readInvocationId });

    // 7. Durable advance to campaign-child-active with the exact live identity.
    await advanceJournal(ctx.custodyLockPath, expectedOwnerUid, "campaign-child-active", ctx.journalIdentity, { campaignChildNonce: nonce, campaignChild: active.processIdentity });

    // 8. Wait for the supervisor's strict durable receipt.
    receipt = await waitForReceipt(receiptPath, expectedOwnerUid, { receiptWaitMs: deps.receiptWaitMs ?? RECEIPT_WAIT_MS, receiptPollMs: deps.receiptPollMs ?? RECEIPT_POLL_MS });

    // 9. Read the separately bounded private stdout and diagnostic files and
    //    prove receipt/hash/byte coherence with the actual artifact bytes. Each
    //    artifact is validated owner-private (exact owner, private mode, nlink ===
    //    1) from the already-open handle metadata to avoid a path race (P1).
    const stdoutRecord = await readOwnerPrivateBoundedFile(resultPath, ctx.innerPython.maxStdoutBytes, "campaign child supervisor result", expectedOwnerUid);
    const diagnosticRecord = await readOwnerPrivateBoundedFile(diagnosticPath, ctx.innerPython.maxStderrBytes, "campaign child supervisor diagnostic", expectedOwnerUid);
    if (stdoutRecord.bytes.length !== receipt.stdout.bytes || sha256(stdoutRecord.bytes) !== receipt.stdout.sha256) fail("campaign child supervisor stdout is not coherent with its receipt");
    if (diagnosticRecord.bytes.length !== receipt.stderr.bytes || sha256(diagnosticRecord.bytes) !== receipt.stderr.sha256) fail("campaign child supervisor stderr is not coherent with its receipt");
    if (receipt.nonce !== nonce || receipt.campaignOperationSha256 !== ctx.campaignOperationSha256) fail("campaign child supervisor receipt is not bound to the exact nonce and operation");
    supervised = Object.freeze({
      receipt,
      stdoutBytes: stdoutRecord.bytes,
      stderrBytes: diagnosticRecord.bytes,
      exitCode: receipt.outcome.exitCode,
      signal: receipt.outcome.signal,
      timedOut: receipt.outcome.timedOut,
      outputOverflow: receipt.outcome.outputOverflow,
      spawnError: receipt.outcome.spawnError,
    });

    // 10. Prove the exact recorded supervisor is gone/terminal, the cgroup has no
    //     live descendants, and the unit identity was not substituted.
    await proveTerminal(active, unitName, ctx, { showUnit, systemdDeps, deriveProcessIdentity: deriveIdentity, isProcessAlive: isAlive, proveCgroupEmpty });

    // 11. Only then durably advance to comparison-cleanup-pending.
    await advanceJournal(ctx.custodyLockPath, expectedOwnerUid, "comparison-cleanup-pending", ctx.journalIdentity, { campaignChildNonce: nonce, campaignChild: active.processIdentity });

  } catch (error) {
    // No error class, including one that merely spoofs the CampaignFailure name,
    // may pass through this pre-terminal boundary. Every class raised before the
    // exact durable comparison-cleanup-pending advance is converted to a
    // content-free WorkCampaignChildLaunchError so the controller holds
    // production and never restores.
    throw new WorkCampaignChildLaunchError("campaign child launch did not reach the proven terminal boundary");
  }

  // 12. Classify AFTER the positive terminal/cgroup proof and the durable
  //     cleanup-pending transition, and OUTSIDE the pre-terminal try/catch. An
  //     explicitly classified CampaignFailure thrown here is the ONLY error that
  //     may restore production; the controller's `instanceof CampaignFailure`
  //     mechanical gate remains the final authority.
  const classified = await classifySupervised(supervised);
  if (classified && classified.ok) {
    return Object.freeze({ result: classified.result, receipt, childIdentity: active.processIdentity, unitName, nonce, failure: null });
  }
  if (classified && classified.failure) throw classified.failure;
  fail("campaign child supervisor classification returned no outcome");
}

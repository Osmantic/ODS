import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, rm, rmdir } from "node:fs/promises";
import { posix } from "node:path";

import { canonical, validatePlanVerificationEvidence } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { validateBuilderPatchForApplication } from "./builder-volume.mjs";
import {
  buildVerifierApplyDockerCommand,
  buildVerifierCommandDockerCommand,
  buildVerifierKeeperDockerCommand,
  buildVerifierVolumeCreate,
  validateVerifierApplyReceipt,
  validateVerifierCommandReceipt,
  validateVerifierKeeperInspect,
  validateVerifierVolumeInspect,
} from "./docker-verifier.mjs";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._\/-]+$/;
const IMAGE_RE = /^(?:sha256:[a-f0-9]{64}|[a-z0-9][a-z0-9._/:+-]{1,255}@sha256:[a-f0-9]{64})$/;
const ID_RE = /^[a-f0-9]{64}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const EVIDENCE_BOUNDARY = "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.";

export class VerifierSupervisorError extends Error {}

function fail(message) {
  throw new VerifierSupervisorError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

function safePath(value, label) {
  if (typeof value !== "string" || !SAFE_PATH_RE.test(value) || !posix.isAbsolute(value) || posix.normalize(value) !== value || value.includes("//")) fail(`${label} is invalid`);
  return value;
}

function under(root, path, label) {
  if (path === root || !path.startsWith(`${root}/`)) fail(`${label} escapes the verifier state root`);
  return path;
}

function runtimeFor(prepared, claim, check, index, patchArtifact, options) {
  const dockerPath = safePath(options?.dockerPath, "Docker client path");
  const stateRoot = safePath(options?.stateRoot, "verifier state root");
  if (!Number.isSafeInteger(index) || index < 0 || index >= prepared.plan.verification.checks.length) fail("verifier check index is invalid");
  const suffix = claim.claimId.slice(-12);
  const token = `${String(index).padStart(2, "0")}-${check.id}`;
  const runRoot = posix.join(stateRoot, "verifier-runs", claim.claimId, token);
  const runtime = {
    dockerPath,
    stateRoot,
    runRoot,
    checkPath: posix.join(runRoot, "check.json"),
    keeperCidFile: posix.join(runRoot, "keeper.cid"),
    commandCidFile: posix.join(runRoot, "command.cid"),
    patchPath: safePath(patchArtifact.path, "Builder patch path"),
    patchSha256: patchArtifact.sha256,
    patchBytes: patchArtifact.bytes,
    patchChanges: patchArtifact.changes,
    volumeName: `pixel-work-verify-${suffix}-${String(index).padStart(2, "0")}`,
    keeperName: `pixel-work-verify-keep-${suffix}-${String(index).padStart(2, "0")}`,
    applyName: `pixel-work-verify-apply-${suffix}-${String(index).padStart(2, "0")}`,
    commandName: `pixel-work-verify-run-${suffix}-${String(index).padStart(2, "0")}`,
    volumeSizeBytes: Math.floor(prepared.lease.budgets.maxDiskBytes / 2),
    imageIdentifier: options.imageIdentifier ?? prepared.policy.runner.imageRef,
    uid: options.uid,
    gid: options.gid,
  };
  if (!IMAGE_RE.test(runtime.imageIdentifier ?? "")) fail("verifier image identifier is invalid");
  for (const [label, path] of [["run root", runRoot], ["check path", runtime.checkPath], ["keeper CID", runtime.keeperCidFile], ["command CID", runtime.commandCidFile]]) under(stateRoot, path, label);
  buildVerifierVolumeCreate(prepared, claim, check, runtime);
  return Object.freeze(runtime);
}

async function invoke(executor, command, args, options) {
  const value = await executor(command, args, options);
  if (!value || typeof value.stdout !== "string" || typeof value.stderr !== "string") fail("Docker executor returned an invalid verifier result");
  return value;
}

async function inspectOne(executor, dockerPath, kind, target) {
  const result = await invoke(executor, dockerPath, [kind, "inspect", target], { timeoutMs: 30000, maxBuffer: 4 * 1024 * 1024 });
  let value;
  try { value = JSON.parse(result.stdout); } catch { fail(`Docker ${kind} inspection is invalid`); }
  if (!Array.isArray(value) || value.length !== 1 || !value[0] || typeof value[0] !== "object") fail(`Docker ${kind} inspection is not singular`);
  return value[0];
}

async function inspectMaybe(executor, dockerPath, kind, target) {
  try { return await inspectOne(executor, dockerPath, kind, target); } catch (error) {
    if (error?.dockerExitCode === 1 && /no such (?:object|container|volume|image)/i.test(error?.dockerStderr ?? "")) return null;
    throw error;
  }
}

function exactLabels(object, claim, check, role) {
  const labels = object?.Config?.Labels ?? object?.Labels ?? {};
  return labels["com.osmantic.pixel.work-claim"] === claim.claimId
    && labels["com.osmantic.pixel.work-job"] === claim.jobId
    && labels["com.osmantic.pixel.work-check"] === check.id
    && labels["com.osmantic.pixel.work-role"] === role;
}

async function removeContainer(executor, runtime, claim, check, target, role) {
  const value = await inspectMaybe(executor, runtime.dockerPath, "container", target);
  if (!value) return;
  if (!ID_RE.test(value.Id ?? "") || !exactLabels(value, claim, check, role)) fail(`refusing to remove an unbound ${role} container`);
  await invoke(executor, runtime.dockerPath, ["container", "rm", "--force", value.Id], { timeoutMs: 15000, maxBuffer: 65536 });
}

async function cleanup(boundary) {
  const { executor, runtime, prepared, claim, check } = boundary;
  const failures = [];
  for (const [target, role] of [[runtime.commandName, "verifier-command"], [runtime.applyName, "verifier-apply"], [runtime.keeperName, "verifier-keeper"]]) {
    try { await removeContainer(executor, runtime, claim, check, target, role); } catch (error) { failures.push(error); }
  }
  try {
    const volume = await inspectMaybe(executor, runtime.dockerPath, "volume", runtime.volumeName);
    if (volume) {
      validateVerifierVolumeInspect(volume, prepared, claim, check, runtime);
      const attached = await invoke(executor, runtime.dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `volume=${runtime.volumeName}`], { timeoutMs: 15000, maxBuffer: 65536 });
      if (attached.stdout.trim()) fail("refusing to remove an attached verifier volume");
      await invoke(executor, runtime.dockerPath, ["volume", "rm", runtime.volumeName], { timeoutMs: 15000, maxBuffer: 65536 });
    }
  } catch (error) { failures.push(error); }
  if (failures.length === 0) {
    const expected = posix.join(runtime.stateRoot, "verifier-runs", claim.claimId, `${String(boundary.index).padStart(2, "0")}-${check.id}`);
    if (runtime.runRoot !== expected) fail("refusing to remove an unrecognized verifier run root");
    await rm(runtime.runRoot, { recursive: true, force: true });
  }
  if (failures.length) throw new VerifierSupervisorError(`Verifier cleanup failed closed at ${failures.length} boundary operation(s)`);
}

async function privateDirectory(path, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail("verifier private directory is invalid");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("verifier private directory is not owner-only");
}

async function prepareRuntime(runtime, check) {
  await privateDirectory(runtime.stateRoot);
  const roots = [posix.join(runtime.stateRoot, "verifier-runs"), posix.join(runtime.stateRoot, "verifier-runs", runtime.runRoot.split("/").at(-2))];
  for (const root of roots) {
    await mkdir(root, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
    await privateDirectory(root);
  }
  await privateDirectory(runtime.runRoot, true);
  if (check.kind === "command") {
    const handle = await open(runtime.checkPath, "wx", 0o600);
    try { await handle.writeFile(`${JSON.stringify(check)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
  } else {
    const handle = await open(runtime.checkPath, "wx", 0o600);
    try { await handle.writeFile("{}\n", "utf8"); await handle.sync(); } finally { await handle.close(); }
  }
}

async function verifyPatchArtifact(prepared, claim, patch, patchArtifact) {
  const { text, details } = await readBoundedRegularText(patchArtifact.path, prepared.lease.budgets.maxArtifactBytes, "Builder patch artifact");
  if (details.nlink !== 1 || details.size !== patchArtifact.bytes || sha(Buffer.from(text, "utf8")) !== patchArtifact.sha256) fail("Builder patch artifact changed before verification");
  if (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail("Builder patch artifact is not private");
  let decoded;
  try { decoded = JSON.parse(text); } catch { fail("Builder patch artifact is not JSON"); }
  if (canonical(decoded) !== canonical(patch)) fail("Builder patch object differs from its retained artifact");
  validateBuilderPatchForApplication(decoded, { jobId: claim.jobId, claimId: claim.claimId, planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256 }, {
    maxTreeBytes: Math.floor(prepared.lease.budgets.maxDiskBytes / 2),
    maxArtifactBytes: prepared.lease.budgets.maxArtifactBytes,
    immutablePathPrefixes: prepared.plan.verification.immutablePathPrefixes,
  });
}

function parseReceipt(text, label) {
  if (Buffer.byteLength(text, "utf8") > 1024 * 1024 || text.trim().split(/\r?\n/).length !== 1) fail(`${label} output is not one bounded receipt`);
  try { return JSON.parse(text.trim()); } catch { fail(`${label} output is not JSON`); }
}

function patchIntegrityStatus(applied) {
  if (!Number.isSafeInteger(applied?.changes) || applied.changes < 0) fail("verifier candidate change count is invalid");
  return applied.changes > 0 ? "pass" : "fail";
}

async function verifyOne(prepared, claim, check, index, patchArtifact, options) {
  if (typeof options?.executor !== "function") fail("independent verifier requires an explicit Docker executor");
  const executor = options.executor;
  const runtime = runtimeFor(prepared, claim, check, index, patchArtifact, options);
  const boundary = { executor, runtime, prepared, claim, check, index };
  let result;
  let primaryError;
  try {
    await prepareRuntime(runtime, check);
    const image = await inspectOne(executor, runtime.dockerPath, "image", runtime.imageIdentifier);
    if (image.Id !== prepared.plan.isolation.runnerImageDigest) fail("verifier runner image differs from the immutable digest");
    const volumeCommand = buildVerifierVolumeCreate(prepared, claim, check, runtime);
    await invoke(executor, volumeCommand.command, volumeCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    validateVerifierVolumeInspect(await inspectOne(executor, runtime.dockerPath, "volume", runtime.volumeName), prepared, claim, check, runtime);
    const keeperCommand = buildVerifierKeeperDockerCommand(prepared, claim, check, runtime);
    await invoke(executor, keeperCommand.command, keeperCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    validateVerifierKeeperInspect(await inspectOne(executor, runtime.dockerPath, "container", runtime.keeperName), image, prepared, claim, check, runtime);
    const applyCommand = buildVerifierApplyDockerCommand(prepared, claim, check, runtime);
    const applied = parseReceipt((await invoke(executor, applyCommand.command, applyCommand.args, { timeoutMs: 120000, maxBuffer: 1024 * 1024 })).stdout, "verifier candidate");
    validateVerifierApplyReceipt(applied, prepared, claim, check, runtime);
    let receipt = null;
    let status = check.kind === "patch-integrity" ? patchIntegrityStatus(applied) : "pass";
    if (check.kind === "command") {
      const command = buildVerifierCommandDockerCommand(prepared, claim, check, runtime, applied.candidateSha256);
      receipt = parseReceipt((await invoke(executor, command.command, command.args, { timeoutMs: check.timeoutSeconds * 1000 + 30000, maxBuffer: 1024 * 1024 })).stdout, "verifier command");
      validateVerifierCommandReceipt(receipt, prepared, claim, check, runtime, applied.candidateSha256);
      status = receipt.status;
    }
    result = {
      id: check.id, kind: check.kind, criterionIndexes: [...check.criterionIndexes], status,
      candidateSha256: applied.candidateSha256,
      evidenceSha256: sha(receipt ?? applied),
      ...(receipt ? {
        runtimeMilliseconds: receipt.durationMilliseconds,
        exitCode: receipt.exitCode,
        signal: receipt.signal,
        timedOut: receipt.timedOut,
        outputLimitExceeded: receipt.outputLimitExceeded,
        spawnFailed: receipt.spawnFailed,
        stdoutBytes: receipt.stdout.bytes,
        stdoutSha256: receipt.stdout.sha256,
        stderrBytes: receipt.stderr.bytes,
        stderrSha256: receipt.stderr.sha256,
      } : { changes: applied.changes, files: applied.files, bytes: applied.bytes }),
    };
  } catch (error) {
    primaryError = error;
  }
  try { await cleanup(boundary); } catch (error) { if (!primaryError) primaryError = error; }
  if (primaryError) throw primaryError;
  return result;
}

async function writeEvidence(path, evidence) {
  const serialized = `${JSON.stringify(evidence, null, 2)}\n`;
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  if (process.platform !== "win32") {
    const directory = await open(posix.dirname(path), constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }
  return { path, bytes: Buffer.byteLength(serialized), sha256: sha(serialized) };
}

export async function runIndependentBuilderVerification(prepared, claim, patch, patchArtifact, options) {
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("independent Docker verifier requires Linux");
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail("independent verifier requires an owner-only process umask");
  await verifyPatchArtifact(prepared, claim, patch, patchArtifact);
  const checks = [];
  let candidateSha256 = null;
  for (let index = 0; index < prepared.plan.verification.checks.length; index += 1) {
    const result = await verifyOne(prepared, claim, prepared.plan.verification.checks[index], index, patchArtifact, options);
    if (candidateSha256 !== null && candidateSha256 !== result.candidateSha256) fail("independent verifier rebuilt inconsistent candidates");
    candidateSha256 = result.candidateSha256;
    checks.push(result);
  }
  const criteria = prepared.plan.acceptanceCriteria.map((_, index) => {
    const relevant = checks.filter((check) => check.criterionIndexes.includes(index));
    return { index, status: relevant.length > 0 && relevant.every((check) => check.status === "pass") ? "pass" : "fail", checkIds: relevant.map((check) => check.id) };
  });
  const evidence = {
    schemaVersion: 1,
    format: "pixel-independent-verification-v1",
    jobId: claim.jobId,
    claimId: claim.claimId,
    planSha256: claim.planSha256,
    patchSha256: patchArtifact.sha256,
    candidateSha256,
    status: criteria.every((criterion) => criterion.status === "pass") ? "pass" : "fail",
    checks,
    criteria,
    network: "none",
    workerSelectedChecks: false,
    externalEffects: false,
    boundary: EVIDENCE_BOUNDARY,
  };
  const evidenceErrors = validatePlanVerificationEvidence(prepared.plan, evidence);
  if (evidenceErrors.length) fail(`independent verification evidence is invalid: ${evidenceErrors[0]}`);
  const outputRoot = posix.dirname(patchArtifact.path);
  await privateDirectory(outputRoot);
  const artifact = await writeEvidence(posix.join(outputRoot, "independent-verification.json"), evidence);
  const claimRuntimeRoot = posix.join(safePath(options.stateRoot, "verifier state root"), "verifier-runs", claim.claimId);
  await rmdir(claimRuntimeRoot).catch((error) => { if (error.code !== "ENOENT") throw error; });
  return { evidence, artifact };
}

export async function recoverIndependentBuilderVerification(prepared, claim, patchArtifact) {
  const path = posix.join(posix.dirname(safePath(patchArtifact?.path, "Builder patch path")), "independent-verification.json");
  let result;
  try {
    result = await readBoundedRegularText(path, prepared.lease.budgets.maxArtifactBytes, "independent verification artifact");
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  const { text, details } = result;
  if (
    details.nlink !== 1 || details.size !== Buffer.byteLength(text, "utf8")
    || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))
  ) fail("independent verification artifact is not private and single-link");
  let evidence;
  try { evidence = JSON.parse(text); } catch { fail("independent verification artifact is not JSON"); }
  const errors = validatePlanVerificationEvidence(prepared.plan, evidence);
  if (
    errors.length || evidence.jobId !== claim.jobId || evidence.claimId !== claim.claimId
    || evidence.planSha256 !== claim.planSha256 || evidence.patchSha256 !== patchArtifact.sha256
    || details.size + patchArtifact.bytes > prepared.lease.budgets.maxArtifactBytes
  ) fail(`recovered independent verification differs from the candidate${errors.length ? `: ${errors[0]}` : ""}`);
  return { evidence, artifact: { path, bytes: details.size, sha256: sha(text) } };
}

export const verifierSupervisorInternals = Object.freeze({ runtimeFor, parseReceipt, patchIntegrityStatus, verifyPatchArtifact });

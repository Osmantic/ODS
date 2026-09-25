import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import { canonical, validatePlanLease, validateWorkCheckpoint } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const CHECKPOINT_RE = /^workcheckpoint-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const FAILURE_STAGES = new Set(["profile-execution", "rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract", "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup", "authorization-expired", "interrupted"]);
const RECORD_RE = /^(0{0,6}[0-9]{1,7})\.json$/;
const MAX_CHECKPOINT_BYTES = 64 * 1024;
const usageBudget = Object.freeze({
  runtimeSeconds: "maxRuntimeSeconds",
  modelRequests: "maxModelRequests",
  inputTokens: "maxInputTokens",
  outputTokens: "maxOutputTokens",
  networkBytes: "maxNetworkBytes",
  artifactBytes: "maxArtifactBytes",
  failures: "maxFailures",
});
const transitions = Object.freeze({
  authorized: new Set(["running", "cancelled", "failed"]),
  running: new Set(["verifying", "waiting-authority", "cleanup-failed", "recovery-inconclusive", "failed", "cancelled", "budget-exhausted"]),
  verifying: new Set(["verified", "recovery-inconclusive", "failed", "cancelled", "budget-exhausted", "waiting-authority"]),
  verified: new Set(["running", "completed", "failed", "cancelled", "budget-exhausted", "no-progress", "waiting-authority"]),
  "waiting-authority": new Set(["running", "verified", "cancelled", "failed"]),
  "cleanup-failed": new Set(["failed", "recovery-inconclusive"]),
  "recovery-inconclusive": new Set(),
  completed: new Set(),
  failed: new Set(),
  cancelled: new Set(),
  "budget-exhausted": new Set(),
  "no-progress": new Set(),
});
const checkpointBoundary = "Private hash-bound recovery record. Contains only immutable digests, bounded counters, and transition facts. Cannot authorize or replay actions.";

export class WorkCheckpointError extends Error {}

function fail(message) {
  throw new WorkCheckpointError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
}

export function checkpointSha256(value) {
  return sha(value);
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`);
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return info;
}

function validatePlan(plan, lease) {
  const errors = validatePlanLease(plan, lease);
  if (errors.length) fail(`checkpoint plan/lease is invalid: ${errors[0]}`);
  if (!JOB_RE.test(plan.jobId ?? "") || plan.jobId !== lease.jobId) fail("checkpoint job binding is invalid");
  return {
    planSha256: sha(plan),
    inputSetSha256: plan.inputSetSha256,
    objectiveSha256: sha(plan.objective),
    acceptanceCriteriaSha256: sha(plan.acceptanceCriteria),
    criteriaTotal: plan.acceptanceCriteria.length,
  };
}

function validateUsage(usage, plan) {
  exactKeys(usage, Object.keys(usageBudget), "checkpoint usage");
  for (const [field, budget] of Object.entries(usageBudget)) {
    if (!Number.isSafeInteger(usage[field]) || usage[field] < 0 || usage[field] > plan.budgets[budget]) fail(`checkpoint usage exceeds ${field}`);
  }
}

function validateProgress(progress, plan) {
  const keys = ["criteriaTotal", "criteriaPassing", "criteriaFailing", "noProgressCount", "failureFingerprintSha256"];
  if (Object.hasOwn(progress ?? {}, "failureStage")) keys.push("failureStage");
  exactKeys(progress, keys, "checkpoint progress");
  if (
    progress.criteriaTotal !== plan.acceptanceCriteria.length
    || progress.criteriaPassing + progress.criteriaFailing !== progress.criteriaTotal
    || progress.noProgressCount > plan.budgets.noProgressLimit
    || (progress.failureFingerprintSha256 !== null && !SHA_RE.test(progress.failureFingerprintSha256))
    || (Object.hasOwn(progress, "failureStage") && progress.failureStage !== null && !FAILURE_STAGES.has(progress.failureStage))
  ) fail("checkpoint progress differs from the immutable plan");
}

function budgetReached(checkpoint, plan) {
  return checkpoint.iteration >= plan.budgets.maxIterations
    || Object.entries(usageBudget).some(([field, budget]) => checkpoint.usage[field] >= plan.budgets[budget]);
}

function executionBudgetReached(checkpoint, plan) {
  return checkpoint.iteration > plan.budgets.maxIterations
    || Object.entries(usageBudget).some(([field, budget]) => checkpoint.usage[field] >= plan.budgets[budget]);
}

function validateCheckpointAgainstPlan(checkpoint, plan, lease, expected) {
  const errors = validateWorkCheckpoint(checkpoint);
  if (errors.length) fail(`checkpoint contract is invalid: ${errors[0]}`);
  const binding = validatePlan(plan, lease);
  if (
    checkpoint.jobId !== plan.jobId
    || checkpoint.planSha256 !== binding.planSha256
    || checkpoint.inputSetSha256 !== binding.inputSetSha256
    || checkpoint.objectiveSha256 !== binding.objectiveSha256
    || checkpoint.acceptanceCriteriaSha256 !== binding.acceptanceCriteriaSha256
  ) fail("checkpoint immutable bindings differ from the plan");
  validateUsage(checkpoint.usage, plan);
  validateProgress(checkpoint.progress, plan);
  if (checkpoint.iteration > plan.budgets.maxIterations) fail("checkpoint iteration exceeds its budget");
  if (checkpoint.state === "budget-exhausted" && !budgetReached(checkpoint, plan)) fail("budget-exhausted checkpoint has remaining budget");
  if (checkpoint.state === "no-progress" && checkpoint.progress.noProgressCount < plan.budgets.noProgressLimit) fail("no-progress checkpoint has not reached its limit");
  if (["cleanup-failed", "recovery-inconclusive"].includes(checkpoint.state) && checkpoint.progress.failureFingerprintSha256 === null) fail("recovery checkpoint lacks a failure fingerprint");
  if (checkpoint.state === "completed" && (checkpoint.artifactManifestSha256 === null || checkpoint.workerSessionSha256 === null)) fail("completed checkpoint lacks worker and artifact evidence");
  if (["verifying", "verified", "completed"].includes(checkpoint.state) && (checkpoint.artifactManifestSha256 === null || checkpoint.workerSessionSha256 === null)) fail("verification state lacks worker and artifact evidence");
  if (["verified", "completed"].includes(checkpoint.state) && checkpoint.verificationEvidenceSha256 === null) fail("verified checkpoint lacks independent evidence");
  if (checkpoint.state === "authorized" && [checkpoint.artifactManifestSha256, checkpoint.workerSessionSha256, checkpoint.verificationEvidenceSha256].some((value) => value !== null)) fail("authorized checkpoint already carries execution evidence");
  if (checkpoint.state === "running" && (checkpoint.workerSessionSha256 === null || checkpoint.artifactManifestSha256 !== null || checkpoint.verificationEvidenceSha256 !== null)) fail("running checkpoint evidence is invalid");
  if (checkpoint.state === "cleanup-failed" && (checkpoint.workerSessionSha256 === null || checkpoint.artifactManifestSha256 !== null || checkpoint.verificationEvidenceSha256 !== null)) fail("cleanup-failed checkpoint evidence is invalid");
  if (checkpoint.state === "verifying" && checkpoint.verificationEvidenceSha256 !== null) fail("pending verification already carries verifier evidence");
  if (expected) {
    if (checkpoint.sequence !== expected.sequence || checkpoint.previousCheckpointSha256 !== expected.previousCheckpointSha256) fail("checkpoint chain position is invalid");
    if (checkpoint.checkpointId !== expected.checkpointId) fail("checkpoint identifier differs from the proposed append");
  }
  return true;
}

function monotonic(previous, next, plan) {
  if (!transitions[previous.state]?.has(next.state)) fail(`checkpoint transition ${previous.state}->${next.state} is not allowed`);
  if (Date.parse(next.createdAt) <= Date.parse(previous.createdAt)) fail("checkpoint time did not advance");
  if (next.iteration < previous.iteration) fail("checkpoint iteration moved backward");
  for (const field of Object.keys(usageBudget)) if (next.usage[field] < previous.usage[field]) fail(`checkpoint usage moved backward for ${field}`);
  if (previous.state === "authorized" && next.state === "running" && next.iteration !== 1) fail("first worker iteration must be one");
  if (previous.state === "running" && next.state === "verifying") {
    if (next.iteration !== previous.iteration) fail("verification must bind the worker iteration");
    if (canonical(next.progress) !== canonical(previous.progress)) fail("pending verification cannot claim progress");
  }
  if (previous.state === "verifying" && next.state === "verified") {
    if (next.iteration !== previous.iteration) fail("verified evidence must bind the worker iteration");
    const improved = next.progress.criteriaPassing > previous.progress.criteriaPassing;
    const expectedNoProgress = improved ? 0 : previous.progress.noProgressCount + 1;
    if (next.progress.noProgressCount !== expectedNoProgress) fail("verification no-progress accounting is invalid");
  }
  if (previous.state === "waiting-authority" && next.state === "verified") {
    if (next.iteration !== previous.iteration) fail("semantic acceptance must bind the candidate iteration");
    if (
      next.progress.criteriaPassing !== next.progress.criteriaTotal || next.progress.criteriaFailing !== 0
      || next.progress.noProgressCount !== previous.progress.noProgressCount || next.progress.failureFingerprintSha256 !== null
    ) fail("semantic acceptance did not pass every immutable criterion");
    if (
      next.workspaceSnapshotSha256 !== previous.workspaceSnapshotSha256
      || next.artifactManifestSha256 !== previous.artifactManifestSha256
      || next.workerSessionSha256 !== previous.workerSessionSha256
      || next.verificationEvidenceSha256 === previous.verificationEvidenceSha256
    ) fail("semantic acceptance changed candidate lineage or reused deterministic evidence");
  }
  if (previous.state === "verified" && next.state === "running") {
    if (next.iteration !== previous.iteration + 1) fail("continuation did not advance the iteration");
    if (next.progress.noProgressCount !== previous.progress.noProgressCount) fail("continuation changed verifier progress accounting");
    if (next.progress.noProgressCount >= plan.budgets.noProgressLimit) fail("continuation must stop at the no-progress limit");
    if (next.workerSessionSha256 === previous.workerSessionSha256) fail("continuation reused the prior worker session");
  } else if (
    next.state !== "no-progress"
    && !(previous.state === "verifying" && next.state === "verified" && next.progress.criteriaPassing > previous.progress.criteriaPassing)
    && next.progress.noProgressCount < previous.progress.noProgressCount
  ) {
    fail("checkpoint no-progress count moved backward without verified progress");
  }
  if (previous.state === "cleanup-failed" && next.state === "failed") {
    if (canonical({
      iteration: next.iteration, usage: next.usage, progress: next.progress,
      workspaceSnapshotSha256: next.workspaceSnapshotSha256, artifactManifestSha256: next.artifactManifestSha256,
      workerSessionSha256: next.workerSessionSha256, verificationEvidenceSha256: next.verificationEvidenceSha256,
      authorityExpansionObserved: next.authorityExpansionObserved,
      acceptanceCriteriaMutationObserved: next.acceptanceCriteriaMutationObserved,
      externalEffectsObserved: next.externalEffectsObserved,
    }) !== canonical({
      iteration: previous.iteration, usage: previous.usage, progress: previous.progress,
      workspaceSnapshotSha256: previous.workspaceSnapshotSha256, artifactManifestSha256: previous.artifactManifestSha256,
      workerSessionSha256: previous.workerSessionSha256, verificationEvidenceSha256: previous.verificationEvidenceSha256,
      authorityExpansionObserved: previous.authorityExpansionObserved,
      acceptanceCriteriaMutationObserved: previous.acceptanceCriteriaMutationObserved,
      externalEffectsObserved: previous.externalEffectsObserved,
    })) fail("successful cleanup changed prior attempt evidence");
  }
  if (next.state === "running" && executionBudgetReached(next, plan)) fail("worker cannot start after a budget is exhausted");
}

function checkpointId(now, suffix) {
  const milliseconds = now.getTime();
  if (!Number.isSafeInteger(milliseconds) || milliseconds < 0 || !/^[a-f0-9]{12}$/.test(suffix)) fail("checkpoint identity input is invalid");
  return `workcheckpoint-${String(milliseconds).padStart(13, "0")}-${suffix}`;
}

function recordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000) fail("checkpoint sequence is invalid");
  return `${String(sequence).padStart(7, "0")}.json`;
}

async function writeRecord(root, checkpoint) {
  const records = join(root, "records");
  const temporaryRoot = join(root, "tmp");
  await privateDirectory(records, "checkpoint records");
  await privateDirectory(temporaryRoot, "checkpoint temporary directory");
  const temporary = join(temporaryRoot, `.checkpoint-${checkpoint.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(records, recordName(checkpoint.sequence));
  const serialized = `${JSON.stringify(checkpoint, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_CHECKPOINT_BYTES) fail("checkpoint exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(serialized, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(records, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("checkpoint sequence was already appended");
    throw error;
  }
  return { checkpoint, sha256: checkpointSha256(checkpoint), path: destination };
}

async function crashOrphanTwin(temporaryRoot, details) {
  // A committed record can reach nlink=2 only when an interrupted publish crashed between its
  // hardlink and the removal of its staging twin: writeRecord is the sole creator of tmp/ entries,
  // and the ledger is owner-private (0700), so no other actor can add a link. A tmp/ staging file
  // that shares this record's inode therefore proves that exact crash orphan — and lets recovery
  // read the durable record instead of wedging the ledger permanently — while an alias anywhere
  // else (no matching tmp/ twin) stays rejected as tampering. This only reads: it repairs nothing,
  // so it never races a concurrent writer's own staging cleanup.
  if (!temporaryRoot) return false;
  for (const entry of await readdir(temporaryRoot).catch(() => [])) {
    const info = await lstat(join(temporaryRoot, entry)).catch(() => null);
    if (info?.isFile() && info.ino === details.ino && info.dev === details.dev) return true;
  }
  return false;
}

async function readRecord(path, sequence, temporaryRoot) {
  const { text, details } = await readBoundedRegularText(path, MAX_CHECKPOINT_BYTES, "work checkpoint");
  const ownerPrivate = process.platform === "win32" || (details.uid === process.geteuid() && (details.mode & 0o077) === 0);
  const singleLink = details.nlink === 1 || (details.nlink === 2 && await crashOrphanTwin(temporaryRoot, details));
  if (!ownerPrivate || !singleLink) fail("checkpoint record is not private and single-link");
  let value;
  try { value = JSON.parse(text); } catch { fail("checkpoint record is not JSON"); }
  if (value.sequence !== sequence) fail("checkpoint filename and sequence differ");
  return value;
}

export async function initializeCheckpointLedger({ stateRoot, plan, lease, workspaceSnapshotSha256, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const binding = validatePlan(plan, lease);
  if (!SHA_RE.test(workspaceSnapshotSha256 ?? "")) fail("initial workspace snapshot digest is invalid");
  const root = resolve(stateRoot);
  await privateDirectory(root, "checkpoint state root");
  const checkpoints = join(root, "checkpoints");
  await mkdir(checkpoints, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(checkpoints, "checkpoint ledger root");
  const jobRoot = join(checkpoints, plan.jobId);
  const stagingRoot = join(checkpoints, `.init-${plan.jobId}-${randomBytes(8).toString("hex")}`);
  const initial = {
    $schema: "https://osmantic.com/pixel/schemas/work-checkpoint-v1.schema.json",
    schemaVersion: 1,
    checkpointId: checkpointId(now, suffix),
    jobId: plan.jobId,
    sequence: 0,
    createdAt: now.toISOString(),
    previousCheckpointSha256: null,
    planSha256: binding.planSha256,
    inputSetSha256: binding.inputSetSha256,
    objectiveSha256: binding.objectiveSha256,
    acceptanceCriteriaSha256: binding.acceptanceCriteriaSha256,
    state: "authorized",
    iteration: 0,
    usage: { runtimeSeconds: 0, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 0 },
    progress: { criteriaTotal: binding.criteriaTotal, criteriaPassing: 0, criteriaFailing: binding.criteriaTotal, noProgressCount: 0, failureFingerprintSha256: null },
    workspaceSnapshotSha256,
    artifactManifestSha256: null,
    workerSessionSha256: null,
    verificationEvidenceSha256: null,
    authorityExpansionObserved: false,
    acceptanceCriteriaMutationObserved: false,
    externalEffectsObserved: false,
    boundary: checkpointBoundary,
  };
  validateCheckpointAgainstPlan(initial, plan, lease, { sequence: 0, previousCheckpointSha256: null, checkpointId: initial.checkpointId });
  try {
    await privateDirectory(stagingRoot, "staged checkpoint ledger", true);
    await privateDirectory(join(stagingRoot, "records"), "checkpoint records", true);
    await privateDirectory(join(stagingRoot, "tmp"), "checkpoint temporary directory", true);
    await privateDirectory(join(stagingRoot, "leases"), "checkpoint continuation leases", true);
    const record = await writeRecord(stagingRoot, initial);
    try {
      await rename(stagingRoot, jobRoot);
    } catch (error) {
      if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) fail("checkpoint ledger was already initialized");
      throw error;
    }
    if (process.platform !== "win32") {
      const directory = await open(checkpoints, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
    return { jobRoot, checkpoint: record.checkpoint, sha256: record.sha256, path: join(jobRoot, "records", recordName(0)) };
  } finally {
    await rm(stagingRoot, { recursive: true, force: true });
  }
}

export async function recoverCheckpointLedger({ stateRoot, plan, lease }) {
  const root = resolve(stateRoot);
  await privateDirectory(root, "checkpoint state root");
  const jobRoot = join(root, "checkpoints", plan.jobId);
  await privateDirectory(jobRoot, "job checkpoint ledger");
  const recordsRoot = join(jobRoot, "records");
  await privateDirectory(recordsRoot, "checkpoint records");
  await privateDirectory(join(jobRoot, "tmp"), "checkpoint temporary directory");
  await privateDirectory(join(jobRoot, "leases"), "checkpoint continuation leases");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length < 1 || names.length > 1000001) fail("checkpoint record count is invalid");
  const checkpoints = [];
  const ids = new Set();
  let previousHash = null;
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    const expectedName = recordName(sequence);
    if (names[sequence] !== expectedName || !RECORD_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("checkpoint record sequence is incomplete or unsafe");
    const checkpoint = await readRecord(join(recordsRoot, names[sequence]), sequence, join(jobRoot, "tmp"));
    if (!CHECKPOINT_RE.test(checkpoint.checkpointId ?? "") || ids.has(checkpoint.checkpointId)) fail("checkpoint identifier is invalid or reused");
    ids.add(checkpoint.checkpointId);
    validateCheckpointAgainstPlan(checkpoint, plan, lease, { sequence, previousCheckpointSha256: previousHash, checkpointId: checkpoint.checkpointId });
    if (sequence > 0) monotonic(checkpoints.at(-1), checkpoint, plan);
    checkpoints.push(checkpoint);
    previousHash = checkpointSha256(checkpoint);
  }
  const head = checkpoints.at(-1);
  return { jobRoot, checkpoints, head, headSha256: previousHash, action: decideCheckpointAction(head, plan) };
}

export async function appendCheckpoint({ stateRoot, plan, lease, previousCheckpointSha256, update, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  exactKeys(update, ["state", "iteration", "usage", "progress", "workspaceSnapshotSha256", "artifactManifestSha256", "workerSessionSha256", "verificationEvidenceSha256", "authorityExpansionObserved", "acceptanceCriteriaMutationObserved", "externalEffectsObserved"], "checkpoint update");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan, lease });
  if (previousCheckpointSha256 !== recovered.headSha256) fail("checkpoint append is based on a stale head");
  const sequence = recovered.head.sequence + 1;
  const next = {
    $schema: "https://osmantic.com/pixel/schemas/work-checkpoint-v1.schema.json",
    schemaVersion: 1,
    checkpointId: checkpointId(now, suffix),
    jobId: plan.jobId,
    sequence,
    createdAt: now.toISOString(),
    previousCheckpointSha256: recovered.headSha256,
    planSha256: recovered.head.planSha256,
    inputSetSha256: recovered.head.inputSetSha256,
    objectiveSha256: recovered.head.objectiveSha256,
    acceptanceCriteriaSha256: recovered.head.acceptanceCriteriaSha256,
    ...update,
    boundary: checkpointBoundary,
  };
  validateCheckpointAgainstPlan(next, plan, lease, { sequence, previousCheckpointSha256: recovered.headSha256, checkpointId: next.checkpointId });
  monotonic(recovered.head, next, plan);
  return writeRecord(recovered.jobRoot, next);
}

export function decideCheckpointAction(checkpoint, plan) {
  if (!transitions[checkpoint?.state]) fail("checkpoint state is unknown");
  if (transitions[checkpoint.state].size === 0) return { action: "terminal", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "waiting-authority") return { action: "wait-for-explicit-authority", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "cleanup-failed") return { action: "fail-interrupted-worker-after-cleanup", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "verified" && checkpoint.progress.criteriaPassing === checkpoint.progress.criteriaTotal) return { action: "record-completed", state: checkpoint.state, replayLease: false };
  if (checkpoint.progress.noProgressCount >= plan.budgets.noProgressLimit) return { action: "record-no-progress", state: checkpoint.state, replayLease: false };
  if (budgetReached(checkpoint, plan)) return { action: "record-budget-exhausted", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "authorized") return { action: "dispatch-worker", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "running") return { action: "fail-interrupted-worker-after-cleanup", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "verifying") return { action: "resume-independent-verifier", state: checkpoint.state, replayLease: false };
  if (checkpoint.state === "verified") return { action: "issue-continuation-lease", state: checkpoint.state, replayLease: false };
  return { action: "fail-closed", state: checkpoint.state, replayLease: false };
}

export const checkpointContract = Object.freeze({ boundary: checkpointBoundary, transitions });

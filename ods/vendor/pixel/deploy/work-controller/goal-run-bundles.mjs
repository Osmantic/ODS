import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical, validateJobPlanLease, validateWorkGoal, validateWorkGoalRunBundle, validateWorkJob,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { compileDurableGoalRefresh, verifyInputObjects } from "../work-broker/broker.mjs";
import { inspectLeaseDisposition, recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import { checkpointSha256, recoverCheckpointLedger } from "./checkpoints.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const RECORD_RE = /^(0{0,3}[0-9]{1,4})\.json$/u;
const MAX_BUNDLE_BYTES = 512 * 1024;
const MAX_RECORDS = 1001;
const boundary = "Private capability-custody record containing one exact expiring child lease. It adds no authority beyond that lease and grants no replay, scope expansion, external effect, or completion authority.";
const authority = Object.freeze({
  containsExactLease: true, grantsBeyondEmbeddedLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});

export class GoalRunBundleError extends Error {}

function fail(message) { throw new GoalRunBundleError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function stableRefreshPlan(plan) {
  const value = structuredClone(plan);
  delete value.planId;
  delete value.compiledAt;
  return value;
}

function stableRefreshLease(lease) {
  const value = structuredClone(lease);
  delete value.leaseId;
  delete value.issuedAt;
  delete value.expiresAt;
  delete value.planSha256;
  return value;
}

function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}

function suffix(value) {
  if (!SUFFIX_RE.test(value ?? "")) fail("goal run bundle suffix is invalid");
  return value;
}

function recordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence >= MAX_RECORDS) fail("goal run bundle sequence is invalid");
  return `${String(sequence).padStart(4, "0")}.json`;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  return resolve(path);
}

function checkedGoalJob(goal, jobs, jobId) {
  const goalErrors = validateWorkGoal(goal);
  if (goalErrors.length || !Array.isArray(jobs) || jobs.length !== goal.milestones.length) fail("goal run bundle binding is invalid");
  const byId = new Map();
  for (const job of jobs) {
    if (validateWorkJob(job).length || byId.has(job.jobId)) fail("goal run bundle job registry is invalid");
    byId.set(job.jobId, job);
  }
  for (const milestone of goal.milestones) {
    const milestoneJob = byId.get(milestone.jobId);
    if (!milestoneJob || milestone.jobSha256 !== sha(milestoneJob) || milestone.profile !== milestoneJob.profile || milestoneJob.dataClassification !== goal.dataClassification) fail("goal run bundle registry differs from the immutable goal");
  }
  const job = byId.get(jobId);
  const milestone = goal.milestones.find((value) => value.jobId === jobId);
  if (!job || !milestone || milestone.jobSha256 !== sha(job) || milestone.profile !== job.profile) fail("goal run bundle child is not in the immutable goal");
  return { job, milestone, goalSha256: sha(goal), jobSha256: sha(job) };
}

function checkedBinding(goal, jobs, jobId, plan, lease, workspaceSnapshotSha256) {
  const binding = checkedGoalJob(goal, jobs, jobId);
  const { job } = binding;
  if (!SHA_RE.test(workspaceSnapshotSha256 ?? "")) fail("goal run bundle workspace binding is invalid");
  if (validateJobPlanLease(job, plan, lease).length) fail("goal run bundle job/plan/lease is invalid");
  if (
    plan.jobId !== job.jobId || plan.requestSha256 !== sha(job) || plan.profile !== job.profile
    || plan.dataClassification !== job.dataClassification || canonical(plan.objective) !== canonical(job.objective)
    || canonical(plan.acceptanceCriteria) !== canonical(job.acceptanceCriteria)
  ) fail("goal run bundle plan differs from the immutable job");
  return binding;
}

function recordFor({ goal, binding, plan, lease, workspaceSnapshotSha256, purpose, sequence, previousBundleSha256, now, recordSuffix }) {
  const created = timestamp(now, "goal run bundle time");
  const bundle = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-run-bundle-v1.schema.json",
    schemaVersion: 1,
    bundleId: `workgoalrun-${String(created.getTime()).padStart(13, "0")}-${suffix(recordSuffix)}`,
    goalId: goal.goalId,
    milestoneId: binding.milestone.milestoneId,
    jobId: binding.job.jobId,
    sequence,
    createdAt: created.toISOString(),
    previousBundleSha256,
    goalSha256: binding.goalSha256,
    jobSha256: binding.jobSha256,
    purpose,
    plan: structuredClone(plan),
    lease: structuredClone(lease),
    workspaceSnapshotSha256,
    authority: { ...authority },
    boundary,
  };
  const errors = validateWorkGoalRunBundle(bundle);
  if (errors.length) fail(`goal run bundle contract is invalid: ${errors[0]}`);
  return bundle;
}

function checkRecord(bundle, goal, jobs, binding, expected) {
  const errors = validateWorkGoalRunBundle(bundle);
  if (errors.length) fail(`stored goal run bundle is invalid: ${errors[0]}`);
  if (
    bundle.goalId !== goal.goalId || bundle.milestoneId !== binding.milestone.milestoneId
    || bundle.jobId !== binding.job.jobId || bundle.goalSha256 !== binding.goalSha256
    || bundle.jobSha256 !== binding.jobSha256 || bundle.sequence !== expected.sequence
    || bundle.previousBundleSha256 !== expected.previousBundleSha256
  ) fail("stored goal run bundle differs from its immutable lineage");
  checkedBinding(goal, jobs, binding.job.jobId, bundle.plan, bundle.lease, bundle.workspaceSnapshotSha256);
}

async function writeRecord(jobRoot, bundle) {
  const records = join(jobRoot, "records");
  const temporaryRoot = join(jobRoot, "tmp");
  await privateDirectory(records, "goal run bundle records");
  await privateDirectory(temporaryRoot, "goal run bundle temporary directory");
  const temporary = join(temporaryRoot, `.bundle-${bundle.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(records, recordName(bundle.sequence));
  const serialized = `${JSON.stringify(bundle, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_BUNDLE_BYTES) fail("goal run bundle exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination); await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(records, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("goal run bundle sequence was already appended");
    throw error;
  }
  return { bundle, sha256: sha(bundle), path: destination };
}

async function crashOrphanTwin(temporaryRoot, details) {
  // A committed record can reach nlink=2 only when an interrupted publish crashed between its
  // hardlink and the removal of its staging twin (writeRecord is the sole creator of tmp/ entries
  // in this owner-private ledger). A tmp/ staging file that shares this record's inode proves that
  // crash orphan and lets recovery read the durable record instead of wedging the ledger
  // permanently, while an alias anywhere else stays rejected as tampering. Read-only: it never
  // races a concurrent writer's own staging cleanup.
  if (!temporaryRoot) return false;
  for (const entry of await readdir(temporaryRoot).catch(() => [])) {
    const info = await lstat(join(temporaryRoot, entry)).catch(() => null);
    if (info?.isFile() && info.ino === details.ino && info.dev === details.dev) return true;
  }
  return false;
}

async function readRecord(path, sequence, temporaryRoot) {
  const { text, details } = await readBoundedRegularText(path, MAX_BUNDLE_BYTES, "goal run bundle");
  const ownerPrivate = process.platform === "win32" || (details.uid === process.geteuid() && (details.mode & 0o077) === 0);
  const singleLink = details.nlink === 1 || (details.nlink === 2 && await crashOrphanTwin(temporaryRoot, details));
  if (!ownerPrivate || !singleLink) fail("goal run bundle is not private and single-link");
  let value;
  try { value = JSON.parse(text); } catch { fail("goal run bundle is not JSON"); }
  if (value.sequence !== sequence) fail("goal run bundle filename and sequence differ");
  return value;
}

async function exactClaimAbsent(stateRoot, lease) {
  const claimsRoot = join(resolve(stateRoot), "claims");
  const rootInfo = await lstat(claimsRoot).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (rootInfo === null) return;
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink() || process.platform !== "win32" && (rootInfo.uid !== process.geteuid() || (rootInfo.mode & 0o077) !== 0)) fail("goal run claim root is unsafe");
  const path = join(claimsRoot, `${lease.leaseId}.json`);
  const info = await lstat(path).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (info !== null) fail("superseded goal run lease was already consumed");
}

async function exactAdmissionDisposition(stateRoot, plan, lease) {
  const disposition = await inspectLeaseDisposition(stateRoot, lease);
  if (disposition === null) return;
  if (
    disposition.kind === "revoked" && disposition.record.jobId === plan.jobId
    && disposition.record.planSha256 === sha(plan) && disposition.record.leaseSha256 === sha(lease)
  ) return;
  fail("goal run admission lease was already consumed or has an invalid revocation");
}

async function exactHistoricalRefreshDisposition(stateRoot, plan, lease) {
  const disposition = await inspectLeaseDisposition(stateRoot, lease);
  if (disposition === null) return;
  if (
    disposition.kind === "revoked" && disposition.record.jobId === plan.jobId
    && disposition.record.planSha256 === sha(plan) && disposition.record.leaseSha256 === sha(lease)
  ) return;
  fail("superseded goal run lease was consumed or has an invalid revocation");
}

async function childLedgerInfo(stateRoot, jobId) {
  const checkpointsRoot = join(resolve(stateRoot), "checkpoints");
  const rootInfo = await lstat(checkpointsRoot).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (rootInfo === null) return null;
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink() || process.platform !== "win32" && (rootInfo.uid !== process.geteuid() || (rootInfo.mode & 0o077) !== 0)) fail("goal run checkpoint root is unsafe");
  const path = join(checkpointsRoot, jobId);
  const info = await lstat(path).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (info && (!info.isDirectory() || info.isSymbolicLink())) fail("goal run child checkpoint path is unsafe");
  return info;
}

async function validateTransition(previous, next, stateRoot, childLedger = null) {
  if (Date.parse(next.createdAt) <= Date.parse(previous.createdAt)) fail("goal run bundle time did not advance");
  if (next.workspaceSnapshotSha256 !== previous.workspaceSnapshotSha256) fail("goal run bundle changed the immutable input snapshot");
  if (next.purpose === "pre-admission-refresh") {
    if (!["initial", "pre-admission-refresh"].includes(previous.purpose)) fail("goal run bundle cannot refresh after durable child admission");
    if (previous.lease.iteration !== 1 || Date.parse(next.createdAt) < Date.parse(previous.lease.expiresAt)) fail("goal run bundle refresh occurred before prior authority expired");
    if (
      canonical(stableRefreshPlan(next.plan)) !== canonical(stableRefreshPlan(previous.plan))
      || canonical(stableRefreshLease(next.lease)) !== canonical(stableRefreshLease(previous.lease))
    ) fail("goal run bundle refresh changed immutable plan or lease authority");
    await exactHistoricalRefreshDisposition(stateRoot, previous.plan, previous.lease);
    return;
  }
  if (next.purpose === "admission") {
    if (
      !["initial", "pre-admission-refresh"].includes(previous.purpose)
      || canonical(next.plan) !== canonical(previous.plan)
      || canonical(next.lease) !== canonical(previous.lease)
    ) fail("goal run bundle admission differs from the exact current child authority");
    if (!childLedger) await exactAdmissionDisposition(stateRoot, previous.plan, previous.lease);
    return;
  }
  if (
    next.purpose !== "continuation" || !["admission", "continuation"].includes(previous.purpose)
    || canonical(next.plan) !== canonical(previous.plan) || next.lease.iteration !== previous.lease.iteration + 1
  ) fail("goal run bundle continuation lineage is invalid");
  if (!childLedger) fail("goal run bundle continuation lacks a child checkpoint ledger");
  const checkpoint = childLedger.checkpoints.find((value) => checkpointSha256(value) === next.lease.continuation?.previousCheckpointSha256);
  if (!checkpoint || checkpoint.state !== "verified" || checkpoint.iteration !== previous.lease.iteration || checkpoint.planSha256 !== sha(next.plan)) fail("goal run bundle continuation differs from verified child evidence");
  const claim = await recoverLeaseConsumption(stateRoot, previous.lease);
  if (
    next.lease.continuation.previousConsumptionSha256 !== sha(claim)
    || next.lease.continuation.cumulativeUsageSha256 !== sha(checkpoint.usage)
  ) fail("goal run bundle continuation differs from prior claim or cumulative usage");
}

async function roots(stateRoot, goalId, jobId, create = false) {
  const root = await privateDirectory(resolve(stateRoot), "goal run state root");
  const runsRoot = join(root, "goal-runs");
  if (create) await mkdir(runsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runsRoot, "goal run root");
  const goalRoot = join(runsRoot, goalId);
  if (create) await mkdir(goalRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(goalRoot, "goal run goal root");
  return { root, runsRoot, goalRoot, jobRoot: join(goalRoot, jobId) };
}

export async function initializeGoalRunBundle({
  stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256,
  now = new Date(), suffix: recordSuffix = randomBytes(6).toString("hex"),
}) {
  const binding = checkedBinding(goal, jobs, jobId, plan, lease, workspaceSnapshotSha256);
  await exactClaimAbsent(stateRoot, lease);
  if (await childLedgerInfo(stateRoot, jobId)) fail("goal run bundle cannot initialize after child admission");
  const { goalRoot, jobRoot } = await roots(stateRoot, goal.goalId, jobId, true);
  const bundle = recordFor({ goal, binding, plan, lease, workspaceSnapshotSha256, purpose: "initial", sequence: 0, previousBundleSha256: null, now, recordSuffix });
  const stagingRoot = join(goalRoot, `.init-${jobId}-${randomBytes(8).toString("hex")}`);
  try {
    await privateDirectory(stagingRoot, "staged goal run bundle ledger", true);
    await privateDirectory(join(stagingRoot, "records"), "goal run bundle records", true);
    await privateDirectory(join(stagingRoot, "tmp"), "goal run bundle temporary directory", true);
    const record = await writeRecord(stagingRoot, bundle);
    try { await rename(stagingRoot, jobRoot); } catch (error) {
      if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) fail("goal run bundle ledger was already initialized");
      throw error;
    }
    if (process.platform !== "win32") {
      const directory = await open(goalRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
    return { bundle: record.bundle, sha256: record.sha256, path: join(jobRoot, "records", recordName(0)) };
  } finally {
    await rm(stagingRoot, { recursive: true, force: true });
  }
}

export async function recoverGoalRunBundles({ stateRoot, goal, jobs, jobId }) {
  const binding = checkedGoalJob(goal, jobs, jobId);
  const { jobRoot } = await roots(stateRoot, goal.goalId, jobId, false);
  await privateDirectory(jobRoot, "goal run bundle ledger");
  const recordsRoot = join(jobRoot, "records");
  await privateDirectory(recordsRoot, "goal run bundle records");
  await privateDirectory(join(jobRoot, "tmp"), "goal run bundle temporary directory");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length < 1 || names.length > MAX_RECORDS) fail("goal run bundle record count is invalid");
  const bundles = [];
  const ids = new Set();
  let previousHash = null;
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    const expectedName = recordName(sequence);
    if (names[sequence] !== expectedName || !RECORD_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("goal run bundle sequence is incomplete or unsafe");
    const bundle = await readRecord(join(recordsRoot, names[sequence]), sequence, join(jobRoot, "tmp"));
    if (ids.has(bundle.bundleId)) fail("goal run bundle identifier was reused");
    ids.add(bundle.bundleId);
    checkRecord(bundle, goal, jobs, binding, { sequence, previousBundleSha256: previousHash });
    bundles.push(bundle);
    previousHash = sha(bundle);
  }
  const head = bundles.at(-1);
  const childInfo = await childLedgerInfo(stateRoot, jobId);
  const childLedger = childInfo ? await recoverCheckpointLedger({ stateRoot, plan: head.plan, lease: head.lease }) : null;
  for (let index = 1; index < bundles.length; index += 1) await validateTransition(bundles[index - 1], bundles[index], stateRoot, childLedger);
  if (childLedger && !bundles.some((bundle) => bundle.purpose === "admission")) fail("goal run child admission has no durable custody marker");
  const now = Date.now();
  const authorityState = now < Date.parse(head.lease.issuedAt) ? "not-yet-valid" : now >= Date.parse(head.lease.expiresAt) ? "expired" : "current";
  return {
    jobRoot, bundles, head, headSha256: previousHash, childLedgerPresent: childLedger !== null,
    action: { action: "use-exact-bundle", authorityState, containsExactLease: true, addsAuthority: false, grantsReplay: false },
  };
}

async function appendBundle({ stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256, purpose, now, recordSuffix }) {
  const recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  if (purpose === "pre-admission-refresh" && recovered.childLedgerPresent) fail("goal run bundle cannot refresh after child admission");
  if (purpose === "admission" && recovered.childLedgerPresent) fail("goal run bundle cannot admit an existing child");
  if (purpose === "continuation" && !recovered.childLedgerPresent) fail("goal run continuation has no admitted child");
  const binding = checkedBinding(goal, jobs, jobId, plan, lease, workspaceSnapshotSha256);
  const bundle = recordFor({
    goal, binding, plan, lease, workspaceSnapshotSha256, purpose,
    sequence: recovered.head.sequence + 1, previousBundleSha256: recovered.headSha256, now, recordSuffix,
  });
  await validateTransition(recovered.head, bundle, stateRoot, recovered.childLedgerPresent ? await recoverCheckpointLedger({ stateRoot, plan: bundle.plan, lease: bundle.lease }) : null);
  return writeRecord(recovered.jobRoot, bundle);
}

export async function refreshGoalRunBundle({
  stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256,
  now = new Date(), suffix: recordSuffix = randomBytes(6).toString("hex"), beforeAppend = null,
}) {
  if (beforeAppend !== null && typeof beforeAppend !== "function") fail("goal run refresh before-append callback is invalid");
  const recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  if (recovered.childLedgerPresent || recovered.head.lease.iteration !== 1) fail("goal run bundle cannot refresh after child admission");
  await exactClaimAbsent(stateRoot, recovered.head.lease);
  if (beforeAppend) await beforeAppend({ plan: recovered.head.plan, lease: recovered.head.lease });
  return appendBundle({ stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256, purpose: "pre-admission-refresh", now, recordSuffix });
}

export async function refreshExpiredGoalRunBundle({
  stateRoot, goal, jobs, jobId, policy, objectStore, now = new Date(),
  compilerSuffix = randomBytes(6).toString("hex"), suffix: recordSuffix = randomBytes(6).toString("hex"),
}) {
  const observed = timestamp(now, "expired goal run refresh time");
  let recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  if (recovered.childLedgerPresent || recovered.head.purpose === "admission") return { refreshed: false, recovered };
  if (observed.getTime() < Date.parse(recovered.head.lease.expiresAt)) return { refreshed: false, recovered };
  const binding = checkedGoalJob(goal, jobs, jobId);
  if (typeof objectStore !== "string" || resolve(objectStore) !== objectStore) fail("expired goal run refresh object store is invalid");
  await verifyInputObjects(recovered.head.plan.inputs, objectStore, binding.job);
  const compiled = compileDurableGoalRefresh(binding.job, policy, recovered.head.plan.inputs, { now: observed, suffix: compilerSuffix });
  try {
    await refreshGoalRunBundle({
      stateRoot, goal, jobs, jobId, plan: compiled.plan, lease: compiled.lease,
      workspaceSnapshotSha256: recovered.head.workspaceSnapshotSha256, now: observed, suffix: recordSuffix,
    });
  } catch (error) {
    const raced = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
    const equivalentCurrentWinner = raced.head.sequence > recovered.head.sequence
      && raced.head.plan.planId !== recovered.head.plan.planId
      && Date.parse(raced.head.lease.issuedAt) <= observed.getTime()
      && Date.parse(raced.head.lease.expiresAt) > observed.getTime()
      && canonical(stableRefreshPlan(raced.head.plan)) === canonical(stableRefreshPlan(recovered.head.plan))
      && canonical(stableRefreshLease(raced.head.lease)) === canonical(stableRefreshLease(recovered.head.lease));
    if (!equivalentCurrentWinner) throw error;
    recovered = raced;
    return { refreshed: true, recovered };
  }
  recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  return { refreshed: true, recovered };
}

export async function appendContinuationGoalRunBundle({
  stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256,
  now = new Date(), suffix: recordSuffix = randomBytes(6).toString("hex"),
}) {
  const recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  if (!recovered.childLedgerPresent) fail("goal run continuation has no admitted child");
  return appendBundle({ stateRoot, goal, jobs, jobId, plan, lease, workspaceSnapshotSha256, purpose: "continuation", now, recordSuffix });
}

export async function admitGoalRunBundle({
  stateRoot, goal, jobs, jobId,
  now = new Date(), suffix: recordSuffix = randomBytes(6).toString("hex"),
}) {
  const recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId });
  if (recovered.childLedgerPresent) fail("goal run bundle cannot admit an existing child");
  if (recovered.head.purpose === "admission") return { bundle: recovered.head, sha256: recovered.headSha256, path: join(recovered.jobRoot, "records", recordName(recovered.head.sequence)) };
  return appendBundle({
    stateRoot, goal, jobs, jobId, plan: recovered.head.plan, lease: recovered.head.lease,
    workspaceSnapshotSha256: recovered.head.workspaceSnapshotSha256, purpose: "admission", now, recordSuffix,
  });
}

export async function resolveGoalRunBundle(options) {
  let recovered = await recoverGoalRunBundles(options);
  if (!recovered.childLedgerPresent && recovered.head.purpose !== "admission") {
    try {
      await admitGoalRunBundle(options);
    } catch (error) {
      recovered = await recoverGoalRunBundles(options);
      if (recovered.head.purpose !== "admission") throw error;
    }
    recovered = await recoverGoalRunBundles(options);
  }
  return Object.freeze({
    plan: structuredClone(recovered.head.plan),
    lease: structuredClone(recovered.head.lease),
    workspaceSnapshotSha256: recovered.head.workspaceSnapshotSha256,
  });
}

export const goalRunBundleBoundary = boundary;

import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical, validateJobPlanLease, validateWorkCheckpoint, validateWorkGoal, validateWorkGoalCheckpoint, validateWorkJob,
  validateWorkLeaseRevocation,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { inspectLeaseDisposition } from "../work-runner/runner-core.mjs";
import { checkpointSha256, recoverCheckpointLedger } from "./checkpoints.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const CHECKPOINT_RE = /^workgoalcheckpoint-[0-9]{13}-[a-f0-9]{12}$/u;
const RECORD_RE = /^(0{0,6}[0-9]{1,7})\.json$/u;
const MAX_CHECKPOINT_BYTES = 128 * 1024;
const usageBudgets = Object.freeze({
  runtimeSeconds: "maxRuntimeSeconds",
  modelRequests: "maxModelRequests",
  inputTokens: "maxInputTokens",
  outputTokens: "maxOutputTokens",
  networkBytes: "maxNetworkBytes",
  artifactBytes: "maxArtifactBytes",
  failures: "maxFailures",
});
const terminalChildStates = new Set(["completed", "failed", "cancelled", "budget-exhausted", "no-progress", "recovery-inconclusive"]);
const activeStates = new Set(["running", "waiting-authority"]);
const transitions = Object.freeze({
  ready: new Set(["running", "paused", "completed", "cancelled"]),
  running: new Set(["ready", "waiting-authority", "paused", "failed", "cancelled", "budget-exhausted", "no-progress", "recovery-inconclusive"]),
  "waiting-authority": new Set(["ready", "running", "paused", "failed", "cancelled", "budget-exhausted", "no-progress", "recovery-inconclusive"]),
  paused: new Set(["ready", "running", "cancelled"]),
  completed: new Set(), failed: new Set(), cancelled: new Set(), "budget-exhausted": new Set(), "no-progress": new Set(), "recovery-inconclusive": new Set(),
});
const boundary = "Private hash-chained goal recovery record. It records exact child evidence and aggregate progress but grants no execution, retry, lease, scope expansion, external effect, or completion authority.";

export class WorkGoalError extends Error {}

function fail(message) { throw new WorkGoalError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
export function goalSha256(value) { return sha(value); }
export function goalCheckpointSha256(value) { return sha(value); }

function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime()) || value.getTime() < 0) fail(`${label} is invalid`);
  return value;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
}

function checkedBindings(goal, jobs) {
  const goalErrors = validateWorkGoal(goal);
  if (goalErrors.length) fail(`goal contract is invalid: ${goalErrors[0]}`);
  if (!Array.isArray(jobs) || jobs.length !== goal.milestones.length) fail("goal child job set is incomplete or widened");
  const byId = new Map();
  for (const job of jobs) {
    const errors = validateWorkJob(job);
    if (errors.length) fail(`goal child job is invalid: ${errors[0]}`);
    if (byId.has(job.jobId)) fail("goal child job set contains a duplicate");
    byId.set(job.jobId, job);
  }
  const byMilestone = new Map();
  for (const milestone of goal.milestones) {
    const job = byId.get(milestone.jobId);
    if (!job || sha(job) !== milestone.jobSha256) fail(`goal milestone ${milestone.milestoneId} differs from its exact child job`);
    if (job.profile !== milestone.profile || job.dataClassification !== goal.dataClassification) fail(`goal milestone ${milestone.milestoneId} changes profile or classification`);
    if (Date.parse(job.createdAt) > Date.parse(goal.createdAt)) fail(`goal milestone ${milestone.milestoneId} was created after the immutable goal`);
    byMilestone.set(milestone.milestoneId, { milestone, job });
  }
  for (const [usageField, goalBudget] of Object.entries(usageBudgets)) {
    const childBudget = goalBudget;
    const total = jobs.reduce((sum, job) => sum + job.budgets[childBudget], 0);
    if (!Number.isSafeInteger(total) || total > goal.budgets[goalBudget]) fail(`goal child jobs exceed aggregate ${usageField} budget`);
  }
  return { byId, byMilestone, goalSha256: sha(goal) };
}

function checkpointId(now, suffix) {
  const created = timestamp(now, "goal checkpoint time");
  if (!SUFFIX_RE.test(suffix ?? "")) fail("goal checkpoint suffix is invalid");
  return `workgoalcheckpoint-${String(created.getTime()).padStart(13, "0")}-${suffix}`;
}

function recordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000) fail("goal checkpoint sequence is invalid");
  return `${String(sequence).padStart(7, "0")}.json`;
}

function zeroUsage() {
  return { runtimeSeconds: 0, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 0 };
}

function addUsage(current, addition, goal) {
  const result = {};
  for (const [field, budget] of Object.entries(usageBudgets)) {
    const value = current[field] + addition[field];
    if (!Number.isSafeInteger(value) || value < current[field] || value > goal.budgets[budget]) fail(`goal aggregate ${field} budget was exceeded`);
    result[field] = value;
  }
  return result;
}

function readyMilestone(goal, completedMilestones) {
  const completed = new Set(completedMilestones);
  return goal.milestones.find((milestone) => !completed.has(milestone.milestoneId) && milestone.dependsOn.every((dependency) => completed.has(dependency))) ?? null;
}

function validateCheckpointAgainstGoal(checkpoint, goal, bindings, expected) {
  const errors = validateWorkGoalCheckpoint(checkpoint);
  if (errors.length) fail(`goal checkpoint contract is invalid: ${errors[0]}`);
  if (checkpoint.goalId !== goal.goalId || checkpoint.goalSha256 !== bindings.goalSha256) fail("goal checkpoint immutable binding differs from the goal");
  if (checkpoint.progress.milestonesTotal !== goal.milestones.length || checkpoint.progress.jobsStarted > goal.budgets.maxJobs) fail("goal checkpoint progress differs from the immutable goal");
  const completed = new Set(checkpoint.completedMilestones);
  for (const milestoneId of checkpoint.completedMilestones) {
    const value = bindings.byMilestone.get(milestoneId);
    if (!value || value.milestone.dependsOn.some((dependency) => !completed.has(dependency))) fail("goal checkpoint completed milestones violate the dependency graph");
  }
  for (const [field, budget] of Object.entries(usageBudgets)) if (checkpoint.usage[field] > goal.budgets[budget]) fail(`goal checkpoint exceeds ${field}`);
  if (checkpoint.state === "ready" && checkpoint.progress.jobsStarted !== checkpoint.completedMilestones.length) fail("ready goal has an unaccounted child job");
  if (activeStates.has(checkpoint.state) && checkpoint.progress.jobsStarted !== checkpoint.completedMilestones.length + 1) fail("active goal child count is invalid");
  if (
    checkpoint.state === "paused"
    && checkpoint.progress.jobsStarted !== checkpoint.completedMilestones.length + (checkpoint.active === null ? 0 : 1)
  ) fail("paused goal child count is invalid");
  if (checkpoint.active) {
    const bound = bindings.byMilestone.get(checkpoint.active.milestoneId);
    if (!bound || canonical(checkpoint.active) !== canonical({ milestoneId: bound.milestone.milestoneId, jobId: bound.milestone.jobId, jobSha256: bound.milestone.jobSha256 }) || completed.has(checkpoint.active.milestoneId)) fail("active goal milestone differs from its exact child job");
  }
  if (checkpoint.observation) {
    const bound = bindings.byMilestone.get(checkpoint.observation.milestoneId);
    if (!bound || checkpoint.observation.jobId !== bound.milestone.jobId || checkpoint.observation.jobSha256 !== bound.milestone.jobSha256) fail("goal child observation differs from its milestone");
  }
  if (checkpoint.state === "completed" && completed.size !== goal.milestones.length) fail("goal completion omitted a milestone");
  if (expected && (checkpoint.sequence !== expected.sequence || checkpoint.previousCheckpointSha256 !== expected.previousCheckpointSha256 || checkpoint.checkpointId !== expected.checkpointId)) fail("goal checkpoint chain position is invalid");
}

function sameUsage(left, right) { return Object.keys(usageBudgets).every((field) => left[field] === right[field]); }
function sameProgress(left, right) { return canonical(left) === canonical(right); }
function sameControlFacts(left, right) {
  return canonical({
    completedMilestones: left.completedMilestones, active: left.active, observation: left.observation,
    usage: left.usage, progress: left.progress, failureFingerprintSha256: left.failureFingerprintSha256,
    authorityExpansionObserved: left.authorityExpansionObserved, externalEffectsObserved: left.externalEffectsObserved,
  }) === canonical({
    completedMilestones: right.completedMilestones, active: right.active, observation: right.observation,
    usage: right.usage, progress: right.progress, failureFingerprintSha256: right.failureFingerprintSha256,
    authorityExpansionObserved: right.authorityExpansionObserved, externalEffectsObserved: right.externalEffectsObserved,
  });
}

function validateTransition(previous, next, goal, bindings) {
  if (!transitions[previous.state]?.has(next.state)) fail(`goal checkpoint transition ${previous.state}->${next.state} is not allowed`);
  if (Date.parse(next.createdAt) <= Date.parse(previous.createdAt)) fail("goal checkpoint time did not advance");
  for (const field of Object.keys(usageBudgets)) if (next.usage[field] < previous.usage[field]) fail(`goal usage moved backward for ${field}`);
  if (next.progress.jobsStarted < previous.progress.jobsStarted || next.progress.failures < previous.progress.failures) fail("goal progress moved backward");
  const previousCompleted = new Set(previous.completedMilestones);
  if (previous.completedMilestones.some((milestoneId) => !next.completedMilestones.includes(milestoneId))) fail("goal completion history moved backward");

  if (previous.state === "ready" && next.state === "running") {
    const selected = readyMilestone(goal, previous.completedMilestones);
    const expectedActive = selected && { milestoneId: selected.milestoneId, jobId: selected.jobId, jobSha256: selected.jobSha256 };
    if (!selected || canonical(next.active) !== canonical(expectedActive) || next.observation !== null || canonical(next.completedMilestones) !== canonical(previous.completedMilestones) || !sameUsage(next.usage, previous.usage) || next.progress.jobsStarted !== previous.progress.jobsStarted + 1 || next.progress.failures !== previous.progress.failures) fail("goal dispatch is not the deterministic next milestone");
    return;
  }
  if (previous.state === "ready" && next.state === "completed") {
    if (previous.completedMilestones.length !== goal.milestones.length || next.observation !== null || canonical(next.completedMilestones) !== canonical(previous.completedMilestones) || !sameUsage(next.usage, previous.usage) || !sameProgress(next.progress, previous.progress)) fail("goal completion is not fully evidence-bound");
    return;
  }
  if (["ready", "running", "waiting-authority"].includes(previous.state) && next.state === "paused") {
    if (!sameControlFacts(previous, next)) fail("goal pause changed child, evidence, progress, usage, or safety facts");
    return;
  }
  if (previous.state === "paused" && ["ready", "running"].includes(next.state)) {
    const expectedState = previous.active === null ? "ready" : "running";
    if (next.state !== expectedState) fail("goal resume state differs from paused child custody");
    if (!sameControlFacts(previous, next)) fail("goal resume changed child, evidence, progress, usage, or safety facts");
    return;
  }
  if (["running", "waiting-authority"].includes(previous.state) && ["running", "waiting-authority"].includes(next.state)) {
    const observedStates = next.state === "waiting-authority" ? ["waiting-authority"] : ["running", "verifying", "verified"];
    if (canonical(next.active) !== canonical(previous.active) || canonical(next.completedMilestones) !== canonical(previous.completedMilestones) || !sameUsage(next.usage, previous.usage) || !sameProgress(next.progress, previous.progress) || !next.observation || next.observation.milestoneId !== previous.active.milestoneId || !observedStates.includes(next.observation.childState)) fail("goal child wait/resume observation is invalid");
    return;
  }
  if (["running", "waiting-authority"].includes(previous.state) && next.state === "ready") {
    if (!next.observation || next.observation.childState !== "completed" || next.observation.milestoneId !== previous.active.milestoneId || next.active !== null || next.completedMilestones.length !== previous.completedMilestones.length + 1 || !next.completedMilestones.includes(previous.active.milestoneId) || next.progress.jobsStarted !== previous.progress.jobsStarted || next.progress.failures !== next.usage.failures) fail("goal milestone completion transition is invalid");
    const expectedUsage = addUsage(previous.usage, next.observation.childUsage, goal);
    if (!sameUsage(next.usage, expectedUsage)) fail("goal milestone completion usage is invalid");
    return;
  }
  if ((["running", "waiting-authority"].includes(previous.state) || previous.state === "paused" && previous.active !== null) && next.state === "cancelled") {
    if (
      !next.observation || !["failed", "cancelled"].includes(next.observation.childState)
      || next.observation.milestoneId !== previous.active.milestoneId || next.active !== null
      || canonical(next.completedMilestones) !== canonical(previous.completedMilestones)
    ) fail("goal cancellation lacks terminal child cleanup evidence");
    const expectedUsage = addUsage(previous.usage, next.observation.childUsage, goal);
    const expectedProgress = { ...previous.progress, failures: expectedUsage.failures };
    const expectedFingerprint = next.observation.childState === "failed" ? sha({
      operation: "cancel-after-supervised-cleanup", milestoneId: next.observation.milestoneId,
      childCheckpointSha256: next.observation.childCheckpointSha256,
    }) : sha({
      milestoneId: next.observation.milestoneId, childState: next.observation.childState,
      childCheckpointSha256: next.observation.childCheckpointSha256,
    });
    if (
      !sameUsage(next.usage, expectedUsage) || !sameProgress(next.progress, expectedProgress)
      || next.failureFingerprintSha256 !== expectedFingerprint
    ) fail("goal cancellation child usage, progress, or fingerprint is invalid");
    return;
  }
  if (["running", "waiting-authority"].includes(previous.state) && transitions[next.state].size === 0) {
    const expectedChildState = next.state;
    if (!next.observation || next.observation.childState !== expectedChildState || next.observation.milestoneId !== previous.active.milestoneId || next.active !== null || canonical(next.completedMilestones) !== canonical(previous.completedMilestones)) fail("goal terminal child observation is invalid");
    const expectedUsage = addUsage(previous.usage, next.observation.childUsage, goal);
    if (!sameUsage(next.usage, expectedUsage) || next.progress.failures !== next.usage.failures) fail("goal terminal child usage is invalid");
    return;
  }
  if (next.state === "cancelled" && (previous.state === "ready" || previous.state === "paused" && previous.active === null)) {
    if (!sameControlFacts(previous, next)) fail("goal cancellation changed evidence, progress, usage, or safety facts");
    return;
  }
  fail("goal checkpoint transition has no exact semantic rule");
}

async function writeRecord(goalRoot, checkpoint) {
  const records = join(goalRoot, "records");
  const temporaryRoot = join(goalRoot, "tmp");
  await privateDirectory(records, "goal checkpoint records");
  await privateDirectory(temporaryRoot, "goal checkpoint temporary directory");
  const temporary = join(temporaryRoot, `.goal-checkpoint-${checkpoint.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(records, recordName(checkpoint.sequence));
  const serialized = `${JSON.stringify(checkpoint, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_CHECKPOINT_BYTES) fail("goal checkpoint exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination); await unlink(temporary);
    if (process.platform !== "win32") { const directory = await open(records, constants.O_RDONLY); try { await directory.sync(); } finally { await directory.close(); } }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("goal checkpoint sequence was already appended");
    throw error;
  }
  return { checkpoint, sha256: sha(checkpoint), path: destination };
}

async function crashOrphanTwin(temporaryRoot, details) {
  // A committed record can reach nlink=2 only when an interrupted publish crashed between its
  // hardlink and the removal of its staging twin: writeRecord is the sole creator of tmp/ entries,
  // and the ledger is owner-private (0700), so no other actor can add a link. A tmp/ staging file
  // that shares this record's inode therefore proves that exact crash orphan — and lets recovery
  // read the durable record instead of wedging the goal ledger permanently — while an alias
  // anywhere else (no matching tmp/ twin) stays rejected as tampering. This only reads: it repairs
  // nothing, so it never races a concurrent writer's own staging cleanup.
  if (!temporaryRoot) return false;
  for (const entry of await readdir(temporaryRoot).catch(() => [])) {
    const info = await lstat(join(temporaryRoot, entry)).catch(() => null);
    if (info?.isFile() && info.ino === details.ino && info.dev === details.dev) return true;
  }
  return false;
}

async function readRecord(path, sequence, temporaryRoot) {
  const { text, details } = await readBoundedRegularText(path, MAX_CHECKPOINT_BYTES, "goal checkpoint");
  const ownerPrivate = process.platform === "win32" || (details.uid === process.geteuid() && (details.mode & 0o077) === 0);
  const singleLink = details.nlink === 1 || (details.nlink === 2 && await crashOrphanTwin(temporaryRoot, details));
  if (!ownerPrivate || !singleLink) fail("goal checkpoint is not private and single-link");
  let value;
  try { value = JSON.parse(text); } catch { fail("goal checkpoint is not JSON"); }
  if (value.sequence !== sequence) fail("goal checkpoint filename and sequence differ");
  return value;
}

function baseCheckpoint(goal, goalHash, now, suffix) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-checkpoint-v1.schema.json",
    schemaVersion: 1,
    checkpointId: checkpointId(now, suffix),
    goalId: goal.goalId,
    sequence: 0,
    createdAt: now.toISOString(),
    previousCheckpointSha256: null,
    goalSha256: goalHash,
    state: "ready",
    completedMilestones: [],
    active: null,
    observation: null,
    usage: zeroUsage(),
    progress: { milestonesTotal: goal.milestones.length, milestonesCompleted: 0, jobsStarted: 0, failures: 0 },
    failureFingerprintSha256: null,
    authorityExpansionObserved: false,
    externalEffectsObserved: false,
    boundary,
  };
}

export async function initializeGoalLedger({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const bindings = checkedBindings(goal, jobs);
  const created = timestamp(now, "goal ledger initialization time");
  if (created.getTime() < Date.parse(goal.createdAt)) fail("goal ledger predates the immutable goal");
  const root = resolve(stateRoot);
  await privateDirectory(root, "goal state root");
  const goalsRoot = join(root, "goal-checkpoints");
  await mkdir(goalsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(goalsRoot, "goal checkpoint root");
  const goalRoot = join(goalsRoot, goal.goalId);
  const checkpoint = baseCheckpoint(goal, bindings.goalSha256, created, suffix);
  validateCheckpointAgainstGoal(checkpoint, goal, bindings, { sequence: 0, previousCheckpointSha256: null, checkpointId: checkpoint.checkpointId });
  const stagingRoot = join(goalsRoot, `.init-${goal.goalId}-${randomBytes(8).toString("hex")}`);
  try {
    await privateDirectory(stagingRoot, "staged goal ledger", true);
    await privateDirectory(join(stagingRoot, "records"), "goal checkpoint records", true);
    await privateDirectory(join(stagingRoot, "tmp"), "goal checkpoint temporary directory", true);
    const record = await writeRecord(stagingRoot, checkpoint);
    try {
      await rename(stagingRoot, goalRoot);
    } catch (error) {
      if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) fail("goal ledger was already initialized");
      throw error;
    }
    if (process.platform !== "win32") {
      const directory = await open(goalsRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
    return { goalRoot, checkpoint: record.checkpoint, sha256: record.sha256, path: join(goalRoot, "records", recordName(0)) };
  } finally {
    await rm(stagingRoot, { recursive: true, force: true });
  }
}

export async function recoverGoalLedger({ stateRoot, goal, jobs }) {
  const bindings = checkedBindings(goal, jobs);
  const goalRoot = join(resolve(stateRoot), "goal-checkpoints", goal.goalId);
  await privateDirectory(resolve(stateRoot), "goal state root");
  await privateDirectory(goalRoot, "goal checkpoint ledger");
  const recordsRoot = join(goalRoot, "records");
  await privateDirectory(recordsRoot, "goal checkpoint records");
  await privateDirectory(join(goalRoot, "tmp"), "goal checkpoint temporary directory");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length < 1 || names.length > 1000001) fail("goal checkpoint record count is invalid");
  const checkpoints = [];
  const ids = new Set();
  let previousHash = null;
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    const expectedName = recordName(sequence);
    if (names[sequence] !== expectedName || !RECORD_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("goal checkpoint sequence is incomplete or unsafe");
    const checkpoint = await readRecord(join(recordsRoot, names[sequence]), sequence, join(goalRoot, "tmp"));
    if (!CHECKPOINT_RE.test(checkpoint.checkpointId ?? "") || ids.has(checkpoint.checkpointId)) fail("goal checkpoint identifier is invalid or reused");
    ids.add(checkpoint.checkpointId);
    validateCheckpointAgainstGoal(checkpoint, goal, bindings, { sequence, previousCheckpointSha256: previousHash, checkpointId: checkpoint.checkpointId });
    if (sequence > 0) validateTransition(checkpoints.at(-1), checkpoint, goal, bindings);
    checkpoints.push(checkpoint);
    previousHash = sha(checkpoint);
  }
  const head = checkpoints.at(-1);
  return { goalRoot, checkpoints, head, headSha256: previousHash, action: decideGoalAction(head, goal) };
}

async function append({ stateRoot, goal, jobs, previousCheckpointSha256, update, now, suffix }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (recovered.headSha256 !== previousCheckpointSha256) fail("goal checkpoint append is based on a stale head");
  const created = timestamp(now, "goal checkpoint append time");
  const checkpoint = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-checkpoint-v1.schema.json",
    schemaVersion: 1,
    checkpointId: checkpointId(created, suffix),
    goalId: goal.goalId,
    sequence: recovered.head.sequence + 1,
    createdAt: created.toISOString(),
    previousCheckpointSha256: recovered.headSha256,
    goalSha256: recovered.head.goalSha256,
    ...update,
    boundary,
  };
  const bindings = checkedBindings(goal, jobs);
  validateCheckpointAgainstGoal(checkpoint, goal, bindings, { sequence: checkpoint.sequence, previousCheckpointSha256: recovered.headSha256, checkpointId: checkpoint.checkpointId });
  validateTransition(recovered.head, checkpoint, goal, bindings);
  return writeRecord(recovered.goalRoot, checkpoint);
}

function unchanged(head, overrides = {}) {
  return {
    state: overrides.state ?? head.state,
    completedMilestones: overrides.completedMilestones ?? head.completedMilestones,
    active: Object.hasOwn(overrides, "active") ? overrides.active : head.active,
    observation: Object.hasOwn(overrides, "observation") ? overrides.observation : head.observation,
    usage: overrides.usage ?? head.usage,
    progress: overrides.progress ?? head.progress,
    failureFingerprintSha256: Object.hasOwn(overrides, "failureFingerprintSha256") ? overrides.failureFingerprintSha256 : head.failureFingerprintSha256,
    authorityExpansionObserved: overrides.authorityExpansionObserved ?? false,
    externalEffectsObserved: overrides.externalEffectsObserved ?? false,
  };
}

export function decideGoalAction(checkpoint, goal) {
  const errors = validateWorkGoal(goal);
  if (errors.length || validateWorkGoalCheckpoint(checkpoint).length || checkpoint.goalId !== goal.goalId || checkpoint.goalSha256 !== sha(goal)) fail("goal action inputs are invalid");
  if (transitions[checkpoint.state].size === 0) return { action: "terminal", state: checkpoint.state, replaysChild: false };
  if (checkpoint.state === "ready") {
    if (checkpoint.completedMilestones.length === goal.milestones.length) return { action: "record-completed", state: checkpoint.state, replaysChild: false };
    const milestone = readyMilestone(goal, checkpoint.completedMilestones);
    if (!milestone) return { action: "fail-closed", state: checkpoint.state, replaysChild: false };
    return { action: "dispatch-child", state: checkpoint.state, milestoneId: milestone.milestoneId, jobId: milestone.jobId, replaysChild: false };
  }
  if (checkpoint.state === "running") return { action: "recover-or-continue-child", state: checkpoint.state, milestoneId: checkpoint.active.milestoneId, jobId: checkpoint.active.jobId, replaysChild: false };
  if (checkpoint.state === "waiting-authority") return { action: "wait-for-child-authority", state: checkpoint.state, milestoneId: checkpoint.active.milestoneId, jobId: checkpoint.active.jobId, replaysChild: false };
  if (checkpoint.state === "paused") return { action: "paused", state: checkpoint.state, replaysChild: false };
  return { action: "fail-closed", state: checkpoint.state, replaysChild: false };
}

export async function dispatchGoalMilestone({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  const decision = decideGoalAction(recovered.head, goal);
  if (decision.action !== "dispatch-child") fail("goal is not ready to dispatch a child job");
  const milestone = goal.milestones.find((value) => value.milestoneId === decision.milestoneId);
  const active = { milestoneId: milestone.milestoneId, jobId: milestone.jobId, jobSha256: milestone.jobSha256 };
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, {
      state: "running", active, observation: null, failureFingerprintSha256: null,
      progress: { ...recovered.head.progress, jobsStarted: recovered.head.progress.jobsStarted + 1 },
    }),
  });
}

export async function pauseGoal({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (!["ready", "running", "waiting-authority"].includes(recovered.head.state)) fail("only a schedulable goal can be paused");
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, { state: "paused" }),
  });
}

export async function resumeGoal({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (recovered.head.state !== "paused") fail("only a paused goal can be resumed");
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, { state: recovered.head.active === null ? "ready" : "running" }),
  });
}

export async function cancelReadyGoal({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (!["ready", "paused"].includes(recovered.head.state) || recovered.head.active !== null) fail("active goal cancellation requires supervised child cleanup");
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, { state: "cancelled" }),
  });
}

function checkedChildEvidence(goal, jobs, active, childPlan, childLease, childCheckpoint) {
  const bindings = checkedBindings(goal, jobs);
  const bound = bindings.byMilestone.get(active?.milestoneId);
  if (!bound) fail("goal child evidence is invalid");
  const planErrors = validateJobPlanLease(bound.job, childPlan, childLease);
  const checkpointErrors = validateWorkCheckpoint(childCheckpoint);
  if (planErrors.length || checkpointErrors.length) fail("goal child evidence is invalid");
  if (
    active.jobId !== bound.job.jobId || active.jobSha256 !== sha(bound.job)
    || childPlan.jobId !== bound.job.jobId || childPlan.requestSha256 !== sha(bound.job)
    || childPlan.profile !== bound.job.profile || childPlan.dataClassification !== bound.job.dataClassification
    || canonical(childPlan.objective) !== canonical(bound.job.objective)
    || canonical(childPlan.acceptanceCriteria) !== canonical(bound.job.acceptanceCriteria)
    || childCheckpoint.jobId !== childPlan.jobId || childCheckpoint.planSha256 !== sha(childPlan)
    || childCheckpoint.inputSetSha256 !== childPlan.inputSetSha256
    || childCheckpoint.objectiveSha256 !== sha(childPlan.objective)
    || childCheckpoint.acceptanceCriteriaSha256 !== sha(childPlan.acceptanceCriteria)
  ) fail("goal child evidence differs from the immutable milestone job and plan");
  return {
    milestoneId: active.milestoneId,
    jobId: active.jobId,
    jobSha256: active.jobSha256,
    childCheckpointSha256: sha(childCheckpoint),
    childState: childCheckpoint.state,
    childUsage: structuredClone(childCheckpoint.usage),
    criteriaTotal: childCheckpoint.progress.criteriaTotal,
    criteriaPassing: childCheckpoint.progress.criteriaPassing,
    verificationEvidenceSha256: childCheckpoint.verificationEvidenceSha256,
  };
}

export async function cancelGoalAfterCleanup({
  stateRoot, goal, jobs, childPlan, childLease,
  now = new Date(), suffix = randomBytes(6).toString("hex"),
}) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (!["running", "paused"].includes(recovered.head.state) || !recovered.head.active) fail("goal cancellation has no supervised active child");
  if (!["scout", "builder", "researcher", "data-lab"].includes(childPlan?.profile)) fail("goal cancellation requires a supported supervised child");
  const childLedger = await recoverCheckpointLedger({ stateRoot, plan: childPlan, lease: childLease });
  const observation = checkedChildEvidence(goal, jobs, recovered.head.active, childPlan, childLease, childLedger.head);
  if (observation.childState !== "failed") fail("active goal cancellation requires failed child cleanup evidence");
  const usage = addUsage(recovered.head.usage, observation.childUsage, goal);
  const progress = { ...recovered.head.progress, failures: usage.failures };
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, {
      state: "cancelled", active: null, observation, usage, progress,
      failureFingerprintSha256: sha({
        operation: "cancel-after-supervised-cleanup", milestoneId: observation.milestoneId,
        childCheckpointSha256: observation.childCheckpointSha256,
      }),
      authorityExpansionObserved: childLedger.head.authorityExpansionObserved,
      externalEffectsObserved: childLedger.head.externalEffectsObserved,
    }),
  });
}

export async function cancelGoalAfterLeaseRevocation({
  stateRoot, goal, jobs, childPlan, childLease, leaseRevocation,
  now = new Date(), suffix = randomBytes(6).toString("hex"),
}) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (!["running", "paused"].includes(recovered.head.state) || !recovered.head.active) fail("goal revocation cancellation has no supervised active child");
  const revocationErrors = validateWorkLeaseRevocation(leaseRevocation);
  if (revocationErrors.length) fail("goal revocation cancellation evidence is invalid");
  const disposition = await inspectLeaseDisposition(stateRoot, childLease);
  if (disposition?.kind !== "revoked" || canonical(disposition.record) !== canonical(leaseRevocation)) {
    fail("goal revocation cancellation evidence is not the exact durable lease disposition");
  }
  const revocationParent = recovered.checkpoints.find((checkpoint) => goalCheckpointSha256(checkpoint) === leaseRevocation.goalCheckpointSha256);
  if (
    !revocationParent || !sameControlFacts(revocationParent, recovered.head)
    || leaseRevocation.goalId !== goal.goalId || leaseRevocation.jobId !== recovered.head.active.jobId
    || leaseRevocation.leaseId !== childLease?.leaseId || leaseRevocation.planSha256 !== sha(childPlan)
    || leaseRevocation.leaseSha256 !== sha(childLease)
  ) fail("goal revocation cancellation differs from the exact active child custody");
  const childLedger = await recoverCheckpointLedger({ stateRoot, plan: childPlan, lease: childLease });
  const observation = checkedChildEvidence(goal, jobs, recovered.head.active, childPlan, childLease, childLedger.head);
  const revocationSha256 = sha(leaseRevocation);
  const reviewedChild = leaseRevocation.reviewedChildCheckpointSha256 === null ? null
    : childLedger.checkpoints.find((checkpoint) => checkpointSha256(checkpoint) === leaseRevocation.reviewedChildCheckpointSha256);
  if (
    observation.childState !== "cancelled" || childLedger.head.iteration !== 0
    || Object.values(childLedger.head.usage).some((value) => value !== 0)
    || childLedger.head.progress.criteriaPassing !== 0
    || childLedger.head.progress.failureFingerprintSha256 !== revocationSha256
    || childLedger.head.artifactManifestSha256 !== null || childLedger.head.workerSessionSha256 !== null
    || childLedger.head.verificationEvidenceSha256 !== null
    || (leaseRevocation.reviewedChildCheckpointSha256 !== null && reviewedChild?.state !== "authorized")
  ) fail("goal revocation cancellation lacks an exact unlaunched cancelled child");
  const usage = addUsage(recovered.head.usage, observation.childUsage, goal);
  return append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, {
      state: "cancelled", active: null, observation, usage,
      failureFingerprintSha256: sha({
        milestoneId: observation.milestoneId, childState: observation.childState,
        childCheckpointSha256: observation.childCheckpointSha256,
      }),
      authorityExpansionObserved: childLedger.head.authorityExpansionObserved,
      externalEffectsObserved: childLedger.head.externalEffectsObserved,
    }),
  });
}

export async function observeGoalMilestone({ stateRoot, goal, jobs, childPlan, childLease, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (!["running", "waiting-authority"].includes(recovered.head.state)) fail("goal has no observable active child");
  const childLedger = await recoverCheckpointLedger({ stateRoot, plan: childPlan, lease: childLease });
  const childCheckpoint = childLedger.head;
  const observation = checkedChildEvidence(goal, jobs, recovered.head.active, childPlan, childLease, childCheckpoint);
  if (childCheckpoint.state === "authorized") return { action: "start-exact-child", checkpoint: recovered.head, appended: false };
  if (["running", "verifying", "verified"].includes(childCheckpoint.state)) {
    if (recovered.head.state === "running") return { action: "continue-or-recover-child", checkpoint: recovered.head, childCheckpointSha256: observation.childCheckpointSha256, appended: false };
    const result = await append({ stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix, update: unchanged(recovered.head, { state: "running", observation }) });
    return { action: "continue-or-recover-child", ...result, appended: true };
  }
  if (childCheckpoint.state === "waiting-authority") {
    if (recovered.head.state === "waiting-authority" && recovered.head.observation?.childCheckpointSha256 === observation.childCheckpointSha256) return { action: "wait-for-child-authority", checkpoint: recovered.head, appended: false };
    const result = await append({ stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix, update: unchanged(recovered.head, { state: "waiting-authority", observation }) });
    return { action: "wait-for-child-authority", ...result, appended: true };
  }
  if (!terminalChildStates.has(childCheckpoint.state)) fail("goal child state cannot be observed");
  const usage = addUsage(recovered.head.usage, childCheckpoint.usage, goal);
  if (childCheckpoint.state === "completed") {
    const completedMilestones = [...recovered.head.completedMilestones, recovered.head.active.milestoneId].sort();
    const progress = { ...recovered.head.progress, milestonesCompleted: completedMilestones.length, failures: usage.failures };
    const result = await append({ stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix, update: unchanged(recovered.head, { state: "ready", completedMilestones, active: null, observation, usage, progress, failureFingerprintSha256: null }) });
    return { action: completedMilestones.length === goal.milestones.length ? "record-completed" : "dispatch-next-child", ...result, appended: true };
  }
  const state = childCheckpoint.state;
  const progress = { ...recovered.head.progress, failures: usage.failures };
  const failureFingerprintSha256 = sha({ milestoneId: observation.milestoneId, childState: observation.childState, childCheckpointSha256: observation.childCheckpointSha256 });
  const result = await append({
    stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix,
    update: unchanged(recovered.head, {
      state, active: null, observation, usage, progress, failureFingerprintSha256,
      authorityExpansionObserved: childCheckpoint.authorityExpansionObserved,
      externalEffectsObserved: childCheckpoint.externalEffectsObserved,
    }),
  });
  return { action: state, ...result, appended: true };
}

export async function completeGoal({ stateRoot, goal, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const recovered = await recoverGoalLedger({ stateRoot, goal, jobs });
  if (decideGoalAction(recovered.head, goal).action !== "record-completed") fail("goal lacks complete independently verified milestone evidence");
  return append({ stateRoot, goal, jobs, previousCheckpointSha256: recovered.headSha256, now, suffix, update: unchanged(recovered.head, { state: "completed", observation: null }) });
}

export const goalContract = Object.freeze({ boundary, transitions, usageBudgets });

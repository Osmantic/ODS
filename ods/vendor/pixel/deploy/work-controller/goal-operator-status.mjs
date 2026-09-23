import { randomBytes } from "node:crypto";
import { lstat } from "node:fs/promises";
import { join, resolve } from "node:path";

import { recoverCheckpointLedger } from "./checkpoints.mjs";
import { inspectGoalCapabilityAttempt, validateGoalCapabilityBindings } from "./goal-capability-runtime.mjs";
import { validateGoalKnowledgeBindings } from "./goal-knowledge-runtime.mjs";
import { recoverGoalRunBundles } from "./goal-run-bundles.mjs";
import { decideGoalAction, recoverGoalLedger } from "./goals.mjs";
import { buildWorkOperatorStatus, publishWorkOperatorStatus } from "./operator-status.mjs";
import { recoverLeaseConsumption } from "../work-runner/runner-core.mjs";

const stateEvents = Object.freeze({
  authorized: ["controller", "authorized", "succeeded"],
  running: ["workspace", "work-started", "pending"],
  verifying: ["verification", "verification-started", "pending"],
  verified: ["verification", "verification-passed", "succeeded"],
  "waiting-authority": ["controller", "boundary-requested", "pending"],
  "cleanup-failed": ["controller", "cleanup-failed", "failed"],
  "recovery-inconclusive": ["controller", "recovery-inconclusive", "failed"],
  completed: ["controller", "session-completed", "succeeded"],
  failed: ["controller", "session-failed", "failed"],
  cancelled: ["controller", "session-cancelled", "stopped"],
  "budget-exhausted": ["controller", "budget-exhausted", "stopped"],
  "no-progress": ["controller", "no-progress", "stopped"],
});
const blockedStates = new Set(["cleanup-failed", "recovery-inconclusive", "failed", "cancelled", "budget-exhausted", "no-progress"]);
const busyStates = new Set(["authorized", "running", "verifying", "verified", "cleanup-failed"]);
const degradedGoalStates = new Set(["recovery-inconclusive", "failed", "budget-exhausted", "no-progress"]);
const artifactKinds = new Set(["finding-report", "patch", "test-evidence", "dataset", "document", "visualization"]);

export class GoalOperatorStatusError extends Error {}

function fail(message) { throw new GoalOperatorStatusError(message); }

function projectionTime(now, sessions, goalUpdatedAt) {
  const observed = now instanceof Date ? now.getTime() : Number(now);
  if (!Number.isFinite(observed)) fail("goal operator projection time is invalid");
  const goalTime = Date.parse(goalUpdatedAt);
  if (!Number.isFinite(goalTime)) fail("goal operator checkpoint time is invalid");
  const latest = sessions.reduce((value, session) => Math.max(value, Date.parse(session.updatedAt)), goalTime);
  return new Date(Math.max(Math.trunc(observed), latest));
}

function activity(checkpoints) {
  return checkpoints.slice(-20).reverse().map((checkpoint) => {
    const event = stateEvents[checkpoint.state];
    if (!event) fail("goal operator projection encountered an unknown checkpoint state");
    return {
      identity: checkpoint.checkpointId, at: checkpoint.createdAt,
      category: event[0], summaryCode: event[1], outcome: event[2],
    };
  });
}

function artifacts(job, checkpoint) {
  if (checkpoint.artifactManifestSha256 === null) return { count: 0, totalBytes: 0, kinds: [] };
  const required = job.outputs?.requiredKinds;
  if (!Array.isArray(required) || required.length < 1 || required.some((kind) => !artifactKinds.has(kind))) fail("goal operator projection encountered invalid artifact kinds");
  const kinds = required.map((kind) => ({ kind, count: 1 }));
  return { count: kinds.length, totalBytes: checkpoint.usage.artifactBytes, kinds };
}

function verification(checkpoint) {
  if (checkpoint.state === "verifying") return "pending";
  if (checkpoint.verificationEvidenceSha256 !== null) return "pass";
  if (["failed", "budget-exhausted", "no-progress"].includes(checkpoint.state) && checkpoint.artifactManifestSha256 !== null) return "fail";
  return "not-started";
}

function boundaryState(checkpoint) {
  if (checkpoint.state === "waiting-authority") return { state: "waiting-approval", requestedExpansion: "scope" };
  if (blockedStates.has(checkpoint.state)) return { state: "blocked", requestedExpansion: "none" };
  return { state: "within-authority", requestedExpansion: "none" };
}

function session(job, run, child, current, capability) {
  const checkpoint = child.head;
  return {
    jobId: job.jobId, current, mode: job.profile, state: checkpoint.state,
    startedAt: child.checkpoints[0].createdAt, updatedAt: checkpoint.createdAt,
    progress: {
      criteriaTotal: checkpoint.progress.criteriaTotal,
      criteriaPassing: checkpoint.progress.criteriaPassing,
      criteriaFailing: checkpoint.progress.criteriaFailing,
      iteration: checkpoint.iteration,
      maxIterations: run.head.plan.budgets.maxIterations,
      noProgressCount: checkpoint.progress.noProgressCount,
      failureStage: checkpoint.progress.failureStage ?? null,
    },
    usage: {
      runtimeSeconds: checkpoint.usage.runtimeSeconds,
      modelRequests: checkpoint.usage.modelRequests,
      inputTokens: checkpoint.usage.inputTokens,
      outputTokens: checkpoint.usage.outputTokens,
      networkBytes: checkpoint.usage.networkBytes,
      artifactBytes: checkpoint.usage.artifactBytes,
      failures: checkpoint.usage.failures,
    },
    artifacts: artifacts(job, checkpoint), verification: verification(checkpoint), capability,
    boundaryState: boundaryState(checkpoint), activity: activity(child.checkpoints),
  };
}

function capabilityProjection(state, toolCount) {
  return Object.freeze({ state, toolCount, singleUseCalls: true, networkAccess: false, externalEffects: false });
}

async function capabilityFor({ stateRoot, binding, policy, run, child }) {
  if (!binding) return capabilityProjection("not-configured", 0);
  if (child.head.state === "authorized") return capabilityProjection("configured", binding.tools.length);
  let consumption;
  try { consumption = await recoverLeaseConsumption(stateRoot, run.head.lease); }
  catch { return capabilityProjection("recovery-required", binding.tools.length); }
  const checkpoint = child.checkpoints.findLast((value) => value.state === "running" && value.iteration === run.head.lease.iteration);
  if (!checkpoint) return capabilityProjection("recovery-required", binding.tools.length);
  const retained = await inspectGoalCapabilityAttempt({
    stateRoot, binding, policy, plan: run.head.plan, lease: run.head.lease, consumption, checkpoint,
  });
  return retained === null ? capabilityProjection("recovery-required", binding.tools.length) : retained;
}

async function admittedSessions({ stateRoot, goal, jobs, goalLedger, capabilityRuntime, capabilityPolicy }) {
  const milestoneIds = new Map(goalLedger.head.completedMilestones.map((milestoneId) => [milestoneId, true]));
  if (goalLedger.head.active && !milestoneIds.has(goalLedger.head.active.milestoneId)) milestoneIds.set(goalLedger.head.active.milestoneId, false);
  if (goalLedger.head.observation) milestoneIds.set(goalLedger.head.observation.milestoneId, true);
  const byMilestone = new Map(goal.milestones.map((milestone) => [milestone.milestoneId, milestone]));
  const byJob = new Map(jobs.map((job) => [job.jobId, job]));
  const bindings = new Map((capabilityRuntime?.bindings ?? []).map((binding) => [binding.jobId, binding]));
  const sessions = [];
  let activeUsage = null;
  for (const [milestoneId, required] of milestoneIds) {
    const milestone = byMilestone.get(milestoneId);
    const job = milestone && byJob.get(milestone.jobId);
    if (!milestone || !job) fail("goal operator projection differs from the immutable goal graph");
    const custodyPath = join(resolve(stateRoot), "goal-runs", goal.goalId, job.jobId);
    const custody = await lstat(custodyPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (custody === null) {
      if (required) fail("goal operator projection lacks required run custody");
      continue;
    }
    const run = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId: job.jobId });
    if (!run.childLedgerPresent) {
      if (required) fail("goal operator projection lacks admitted child custody");
      continue;
    }
    const child = await recoverCheckpointLedger({ stateRoot, plan: run.head.plan, lease: run.head.lease });
    if (goalLedger.head.active?.jobId === job.jobId) activeUsage = { ...child.head.usage };
    const capability = await capabilityFor({ stateRoot, binding: bindings.get(job.jobId), policy: capabilityPolicy, run, child });
    sessions.push(session(job, run, child, goalLedger.head.active?.jobId === job.jobId, capability));
  }
  return {
    sessions: sessions.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || a.jobId.localeCompare(b.jobId)).slice(0, 20),
    activeUsage,
  };
}

export async function publishGoalOperatorStatus({
  stateRoot, goal, jobs, capabilityRuntime = null, capabilityPolicy = null,
  knowledgeRuntime = null,
  now = new Date(), secret = randomBytes(32), suffix,
} = {}) {
  if (Boolean(capabilityRuntime) !== Boolean(capabilityPolicy)) fail("goal operator capability configuration is incomplete");
  if (capabilityRuntime) validateGoalCapabilityBindings({ capabilityRuntime, jobs, policy: capabilityPolicy });
  if (knowledgeRuntime || jobs.some((job) => job?.knowledge !== undefined)) validateGoalKnowledgeBindings({ config: { knowledgeRuntime }, jobs });
  const goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
  const admitted = await admittedSessions({ stateRoot, goal, jobs, goalLedger, capabilityRuntime, capabilityPolicy });
  const { sessions, activeUsage } = admitted;
  const generated = projectionTime(now, sessions, goalLedger.head.createdAt);
  const decision = decideGoalAction(goalLedger.head, goal);
  const capabilityState = !capabilityRuntime ? "disabled"
    : sessions.some((value) => value.capability.state === "recovery-required") ? "degraded"
      : sessions.some((value) => value.current && value.capability.state === "authorized") ? "busy" : "ready";
  const controllerState = degradedGoalStates.has(goalLedger.head.state) || capabilityState === "degraded" ? "degraded"
    : goalLedger.head.state === "running" && sessions.some((value) => busyStates.has(value.state)) ? "busy" : "ready";
  const limits = {
    jobs: goal.budgets.maxJobs, runtimeSeconds: goal.budgets.maxRuntimeSeconds,
    modelRequests: goal.budgets.maxModelRequests, inputTokens: goal.budgets.maxInputTokens,
    outputTokens: goal.budgets.maxOutputTokens, networkBytes: goal.budgets.maxNetworkBytes,
    artifactBytes: goal.budgets.maxArtifactBytes, failures: goal.budgets.maxFailures,
  };
  const used = { jobs: goalLedger.head.progress.jobsStarted, ...goalLedger.head.usage };
  if (activeUsage) for (const field of Object.keys(activeUsage)) used[field] += activeUsage[field];
  const remaining = Object.fromEntries(Object.keys(limits).map((field) => [field, limits[field] - used[field]]));
  if (Object.values(remaining).some((value) => !Number.isSafeInteger(value) || value < 0)) fail("goal operator projection encountered exhausted arithmetic beyond its immutable budget");
  const operatorGoal = {
    state: goalLedger.head.state, updatedAt: goalLedger.head.createdAt,
    progress: { ...goalLedger.head.progress }, usage: { ...goalLedger.head.usage },
    budgets: { accounting: "settled-plus-active-observed", used, limits, remaining },
    continuity: {
      checkpointSequence: goalLedger.head.sequence, restartSafe: true,
      completionRequiresIndependentVerification: true, progressModel: "durable-events", watchdogRole: "liveness-only",
    },
    nextAction: decision.action,
  };
  const status = buildWorkOperatorStatus({
    goal: operatorGoal, sessions, services: [
      { id: "controller", state: controllerState, observedAt: generated.toISOString() },
      { id: "capability-adapter", state: capabilityState, observedAt: generated.toISOString() },
      ...(knowledgeRuntime ? [{ id: "knowledge-vault", state: "ready", observedAt: generated.toISOString() }] : []),
    ],
    controllerState, secret, now: generated, ...(suffix ? { suffix } : {}),
  });
  const published = await publishWorkOperatorStatus({ stateRoot, status });
  return Object.freeze({ status, path: published.path, projectionId: published.projectionId });
}

export const goalOperatorStatusBoundary = "Authoritative content-free heartbeat derived only from immutable goal, run-custody, and checkpoint state. It exposes no work content or control authority and cannot schedule, resume, retry, approve, or complete work.";

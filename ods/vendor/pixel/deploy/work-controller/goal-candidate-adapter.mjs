import { createHash, randomBytes } from "node:crypto";
import { resolve } from "node:path";

import {
  canonical, validateJobPlanLease, validateWorkCheckpoint, validateWorkGoal, validateWorkGoalCheckpoint,
  validateWorkJob, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import {
  discardPreparedRun, prepareDataLabCleanupRecovery, prepareDataLabRun, prepareResearcherCleanupRecovery,
  prepareResearcherRun, prepareScoutCleanupRecovery, prepareScoutRun, recoverLeaseConsumption,
} from "../work-runner/runner-core.mjs";
import {
  executeCandidateWaitAttempt, recordExpiredAuthorizationFailure, recordInterruptedCandidateFailure,
} from "./candidate-wait-loop.mjs";
import { checkpointSha256, decideCheckpointAction } from "./checkpoints.mjs";
import { recoverGoalRunBundles, refreshExpiredGoalRunBundle, resolveGoalRunBundle } from "./goal-run-bundles.mjs";
import { goalSha256 } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const supportedProfiles = new Set(["scout", "researcher", "data-lab"]);
const contextKeys = Object.freeze(["job", "plan", "lease", "workspaceSnapshotSha256", "goalCheckpoint", "childCheckpoint", "childAction"]);

export class GoalCandidateAdapterError extends Error {}

function fail(message) { throw new GoalCandidateAdapterError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

function absolutePath(value, label) {
  if (typeof value !== "string" || !value || resolve(value) !== value) fail(`${label} must be an absolute normalized path`);
  return value;
}

function observedNow(clock, label) {
  const value = clock();
  const milliseconds = value instanceof Date ? value.getTime() : Number(value);
  if (!Number.isFinite(milliseconds)) fail(`${label} clock is invalid`);
  return new Date(Math.trunc(milliseconds));
}

function nextSuffix(source) {
  const value = source?.() ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(value)) fail("goal candidate suffix is invalid");
  return value;
}

function checkedParentContext(context) {
  const keys = ["job", "goalCheckpoint", "childCheckpoint", "childAction"];
  exactKeys(context, keys, "goal candidate resolver context");
  if (validateWorkJob(context.job).length || validateWorkGoalCheckpoint(context.goalCheckpoint).length || !supportedProfiles.has(context.job.profile)) fail("goal candidate resolver parent context is invalid");
  if (context.goalCheckpoint.active?.jobId !== context.job.jobId || context.goalCheckpoint.active?.jobSha256 !== goalSha256(context.job)) fail("goal candidate resolver parent differs from the active child");
  if (context.childCheckpoint === null && context.childAction !== null) fail("goal candidate resolver action lacks a child checkpoint");
  if (context.childCheckpoint !== null && validateWorkCheckpoint(context.childCheckpoint).length) fail("goal candidate resolver child checkpoint is invalid");
  if (context.childAction !== null && (!context.childAction || typeof context.childAction !== "object" || Array.isArray(context.childAction))) fail("goal candidate resolver child action is invalid");
  return context;
}

export function createGoalCandidateResolver({ stateRoot, goal, jobs, policy, objectStore, clock = () => new Date(), suffix } = {}) {
  if (typeof stateRoot !== "string" || !stateRoot || !goal || !Array.isArray(jobs) || !policy || typeof objectStore !== "string" || !objectStore || typeof clock !== "function") fail("goal candidate resolver configuration is invalid");
  if (suffix !== undefined && typeof suffix !== "function") fail("goal candidate resolver suffix source is invalid");
  return async (rawContext) => {
    const context = checkedParentContext(rawContext);
    const refreshTime = observedNow(clock, "goal candidate resolver refresh");
    await refreshExpiredGoalRunBundle({
      stateRoot, goal, jobs, jobId: context.job.jobId, policy, objectStore, now: refreshTime,
      compilerSuffix: nextSuffix(suffix), suffix: nextSuffix(suffix),
    });
    const recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId: context.job.jobId });
    if (context.childCheckpoint !== null) {
      if (
        context.childCheckpoint.jobId !== context.job.jobId
        || canonical(context.childAction) !== canonical(decideCheckpointAction(context.childCheckpoint, recovered.head.plan))
      ) fail("goal candidate resolver child action differs from the authoritative checkpoint");
      if (!["dispatch-worker", "fail-interrupted-worker-after-cleanup", "wait-for-explicit-authority", "terminal"].includes(context.childAction.action)) fail("goal candidate resolver received an unsupported post-admission action");
    }
    const proposedAdmission = observedNow(clock, "goal candidate resolver admission");
    const admissionTime = new Date(Math.max(proposedAdmission.getTime(), Date.parse(recovered.head.createdAt) + 1));
    return resolveGoalRunBundle({
      stateRoot, goal, jobs, jobId: context.job.jobId,
      now: admissionTime, suffix: nextSuffix(suffix),
    });
  };
}

function checkedConfiguration({ stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits }) {
  if (validateWorkGoal(goal).length || !Array.isArray(jobs) || jobs.length !== goal?.milestones?.length || validateWorkPolicy(policy).length) fail("goal candidate preparer contracts are invalid");
  const ids = new Set();
  for (const job of jobs) {
    if (validateWorkJob(job).length || ids.has(job.jobId)) fail("goal candidate preparer job registry is invalid");
    ids.add(job.jobId);
  }
  exactKeys(archiveLimits, ["maxEntries", "maxFileBytes"], "goal candidate archive limits");
  if (
    !Number.isSafeInteger(archiveLimits.maxEntries) || archiveLimits.maxEntries < 1 || archiveLimits.maxEntries > 100000
    || !Number.isSafeInteger(archiveLimits.maxFileBytes) || archiveLimits.maxFileBytes < 1 || archiveLimits.maxFileBytes > 4294967296
  ) fail("goal candidate archive limits are invalid");
  return {
    stateRoot: absolutePath(stateRoot, "goal candidate state root"), objectStore: absolutePath(objectStore, "goal candidate object store"),
    workspaceRoot: absolutePath(workspaceRoot, "goal candidate workspace root"), executorPath: absolutePath(executorPath, "goal candidate executor"),
  };
}

function checkedPreparationRequest(raw, jobs) {
  exactKeys(raw, ["mode", "context"], "goal candidate preparation request");
  const { mode, context } = raw;
  if (!["execute", "cleanup"].includes(mode)) fail("goal candidate preparation mode is invalid");
  exactKeys(context, contextKeys, "goal candidate preparation context");
  if (
    validateWorkJob(context.job).length || validateJobPlanLease(context.job, context.plan, context.lease).length
    || validateWorkGoalCheckpoint(context.goalCheckpoint).length || validateWorkCheckpoint(context.childCheckpoint).length
    || !SHA_RE.test(context.workspaceSnapshotSha256 ?? "") || !supportedProfiles.has(context.job.profile)
  ) fail("goal candidate preparation context contract is invalid");
  const registered = jobs.find((job) => job.jobId === context.job.jobId);
  if (
    !registered || canonical(registered) !== canonical(context.job) || context.plan.profile !== context.job.profile
    || context.goalCheckpoint.active?.jobId !== context.job.jobId || context.goalCheckpoint.active?.jobSha256 !== goalSha256(context.job)
    || canonical(context.childAction) !== canonical(decideCheckpointAction(context.childCheckpoint, context.plan))
  ) fail("goal candidate preparation context differs from the authoritative child");
  const expectedMode = context.childAction.action === "dispatch-worker" ? "execute" : context.childAction.action === "fail-interrupted-worker-after-cleanup" ? "cleanup" : null;
  if (mode !== expectedMode) fail("goal candidate preparation mode differs from the authoritative child action");
  return { mode, context };
}

export function createGoalCandidatePreparer({
  stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits,
  clock = () => new Date(), researcherRun = prepareResearcherRun, dataLabRun = prepareDataLabRun,
  scoutRun = prepareScoutRun, researcherCleanup = prepareResearcherCleanupRecovery,
  dataLabCleanup = prepareDataLabCleanupRecovery, scoutCleanup = prepareScoutCleanupRecovery,
} = {}) {
  for (const [label, callback] of Object.entries({ clock, scoutRun, researcherRun, dataLabRun, scoutCleanup, researcherCleanup, dataLabCleanup })) if (typeof callback !== "function") fail(`goal candidate ${label} callback is invalid`);
  const paths = checkedConfiguration({ stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits });
  return async (raw) => {
    const { mode, context } = checkedPreparationRequest(raw, jobs);
    const recovered = await recoverGoalRunBundles({ stateRoot: paths.stateRoot, goal, jobs, jobId: context.job.jobId });
    if (
      canonical(recovered.head.plan) !== canonical(context.plan) || canonical(recovered.head.lease) !== canonical(context.lease)
      || recovered.head.workspaceSnapshotSha256 !== context.workspaceSnapshotSha256 || recovered.head.purpose !== "admission"
    ) fail("goal candidate preparation context differs from durable custody");
    const common = {
      stateRoot: paths.stateRoot, plan: context.plan, lease: context.lease, policy, objectStore: paths.objectStore,
      workspaceRoot: paths.workspaceRoot, executorPath: paths.executorPath, archiveLimits,
      now: observedNow(clock, "goal candidate preparer"),
    };
    if (mode === "execute") {
      if (context.childCheckpoint.state !== "authorized" || context.lease.iteration !== 1 || context.lease.continuation !== null) fail("goal candidate initial execution boundary is invalid");
      return context.job.profile === "scout" ? scoutRun(common) : context.job.profile === "researcher" ? researcherRun(common) : dataLabRun(common);
    }
    const consumption = await recoverLeaseConsumption(paths.stateRoot, context.lease);
    const options = { ...common, recovery: { consumption, checkpoint: context.childCheckpoint } };
    return context.job.profile === "scout" ? scoutCleanup(options) : context.job.profile === "researcher" ? researcherCleanup(options) : dataLabCleanup(options);
  };
}

function checkedDriverContext(context) {
  exactKeys(context, contextKeys, "goal candidate driver context");
  if (
    validateJobPlanLease(context.job, context.plan, context.lease).length || validateWorkCheckpoint(context.childCheckpoint).length
    || validateWorkGoalCheckpoint(context.goalCheckpoint).length || !SHA_RE.test(context.workspaceSnapshotSha256 ?? "")
    || !supportedProfiles.has(context.job.profile) || context.plan.profile !== context.job.profile
  ) fail("goal candidate driver context contract is invalid");
  if (
    context.plan.jobId !== context.job.jobId || context.plan.requestSha256 !== sha(context.job)
    || canonical(context.plan.objective) !== canonical(context.job.objective) || canonical(context.plan.acceptanceCriteria) !== canonical(context.job.acceptanceCriteria)
    || context.childCheckpoint.jobId !== context.job.jobId || context.childCheckpoint.planSha256 !== sha(context.plan)
    || context.childCheckpoint.sequence === 0 && context.childCheckpoint.workspaceSnapshotSha256 !== context.workspaceSnapshotSha256
    || context.goalCheckpoint.active?.jobId !== context.job.jobId || context.goalCheckpoint.active?.jobSha256 !== sha(context.job)
    || canonical(context.childAction) !== canonical(decideCheckpointAction(context.childCheckpoint, context.plan))
  ) fail("goal candidate driver context differs from its immutable child");
  return context;
}

function driverMode(context) {
  if (context.childAction.action === "dispatch-worker") return "execute";
  if (context.childAction.action === "fail-interrupted-worker-after-cleanup") return "cleanup";
  fail("goal candidate driver received a non-drivable child action");
}

function checkedPrepared(context, prepared, mode) {
  if (
    !prepared || canonical(prepared.plan) !== canonical(context.plan) || canonical(prepared.lease) !== canonical(context.lease)
    || prepared.workspace?.sha256 !== context.workspaceSnapshotSha256 || prepared.bindings?.planSha256 !== sha(context.plan)
    || prepared.bindings?.leaseSha256 !== sha(context.lease) || prepared.bindings?.policySha256 !== context.plan.policySha256
    || prepared.bindings?.inputSetSha256 !== context.plan.inputSetSha256
  ) fail("prepared candidate boundary differs from the goal child");
  if (mode === "execute" && (prepared.verificationOnly === true || prepared.cleanupOnly === true)) fail("goal candidate execution received a recovery-only preparation");
  if (mode === "cleanup" && (prepared.cleanupOnly !== true || !prepared.recoveryConsumption)) fail("goal candidate cleanup preparation is invalid");
  return prepared;
}

function contentFree(result) {
  const checkpoint = result?.checkpoint;
  if (!checkpoint || validateWorkCheckpoint(checkpoint).length) fail("goal candidate operation returned no valid checkpoint");
  const expected = checkpoint.state === "waiting-authority" ? "waiting-authority" : checkpoint.state === "failed" ? "failed" : null;
  if (!expected || result.action !== expected) fail("goal candidate operation action differs from its checkpoint");
  return { action: result.action, childState: checkpoint.state, childCheckpointSha256: checkpointSha256(checkpoint), grantsExecution: false, grantsCompletion: false, containsWorkerOutput: false };
}

export function createGoalCandidateDriver({
  stateRoot, prepare, lifecycleOptions = {}, clock = () => new Date(), suffixes = () => ({}),
  discard = discardPreparedRun, candidateRunner, cleanup,
} = {}) {
  if (typeof stateRoot !== "string" || !stateRoot || typeof prepare !== "function" || typeof clock !== "function" || typeof suffixes !== "function" || typeof discard !== "function") fail("goal candidate driver configuration is invalid");
  if (typeof lifecycleOptions !== "function" && (!lifecycleOptions || typeof lifecycleOptions !== "object" || Array.isArray(lifecycleOptions))) fail("goal candidate lifecycle options are invalid");
  if (candidateRunner !== undefined && typeof candidateRunner !== "function") fail("goal candidate runner callback is invalid");
  if (cleanup !== undefined && typeof cleanup !== "function") fail("goal candidate cleanup callback is invalid");
  return async (rawContext) => {
    const context = checkedDriverContext(rawContext);
    const mode = driverMode(context);
    if (mode === "execute") {
      const observed = observedNow(clock, "goal candidate driver").getTime();
      if (observed < Date.parse(context.lease.issuedAt)) fail("goal candidate execution lease is not current");
      if (observed >= Date.parse(context.lease.expiresAt)) {
        // The execution lease's authority window has elapsed. dispatch-worker is issued only for an
        // authorized head (the worker has not launched), so instead of refusing forever -- which,
        // once the lease has been consumed, also blocks operator-cancel and wedges the goal --
        // reconcile the unlaunched child to a terminal failed state so the goal can re-dispatch.
        // This grants no authority: it only closes an expired authorization whose worker never ran.
        const records = suffixes({ mode, context: structuredClone(context) });
        if (!records || typeof records !== "object" || Array.isArray(records)) fail("goal candidate checkpoint suffixes are invalid");
        return contentFree(await recordExpiredAuthorizationFailure({
          stateRoot, prepared: { plan: context.plan, lease: context.lease }, clock, checkpointSuffix: records.failed,
        }));
      }
    }
    const prepared = checkedPrepared(context, await prepare({ mode, context: structuredClone(context) }), mode);
    const options = typeof lifecycleOptions === "function" ? await lifecycleOptions({ mode, context: structuredClone(context) }) : lifecycleOptions;
    const records = suffixes({ mode, context: structuredClone(context) });
    if (!records || typeof records !== "object" || Array.isArray(records)) fail("goal candidate checkpoint suffixes are invalid");
    try {
      if (mode === "execute") return contentFree(await executeCandidateWaitAttempt({ stateRoot, prepared, lifecycleOptions: options, clock, suffixes: records, ...(candidateRunner ? { candidateRunner } : {}) }));
      return contentFree(await recordInterruptedCandidateFailure({ stateRoot, prepared, lifecycleOptions: options, clock, checkpointSuffix: records.failed, ...(cleanup ? { cleanup } : {}) }));
    } finally {
      if (prepared.cleanupOnly !== true) await discard(prepared);
    }
  };
}

export const goalCandidateAdapterBoundary = "Disk-derived Scout/Researcher/Data Lab goal bridge only. It may execute one exact admitted lease or clean one exact interrupted claim, and may publish only a non-completing semantic-acceptance candidate.";

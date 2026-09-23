import { createHash } from "node:crypto";

import { canonical, validateJobPlanLease, validateWorkCheckpoint, validateWorkGoalCheckpoint } from "../../scripts/lib/work-contract.mjs";
import { discardPreparedRun } from "../work-runner/runner-core.mjs";
import {
  executeBuilderIteration, recordInterruptedBuilderFailure, resumeBuilderVerification,
} from "./builder-loop.mjs";
import { checkpointSha256, decideCheckpointAction } from "./checkpoints.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const contextKeys = Object.freeze([
  "job", "plan", "lease", "workspaceSnapshotSha256", "goalCheckpoint", "childCheckpoint", "childAction",
]);

export class GoalBuilderDriverError extends Error {}

function fail(message) { throw new GoalBuilderDriverError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

function checkedContext(context) {
  exactKeys(context, contextKeys, "goal Builder context");
  const leaseErrors = validateJobPlanLease(context.job, context.plan, context.lease);
  const childErrors = validateWorkCheckpoint(context.childCheckpoint);
  const goalErrors = validateWorkGoalCheckpoint(context.goalCheckpoint);
  if (leaseErrors.length || childErrors.length || goalErrors.length || !SHA_RE.test(context.workspaceSnapshotSha256 ?? "")) fail("goal Builder context contract is invalid");
  if (
    context.job.profile !== "builder" || context.plan.profile !== "builder"
    || context.plan.jobId !== context.job.jobId || context.plan.requestSha256 !== sha(context.job)
    || context.plan.dataClassification !== context.job.dataClassification
    || canonical(context.plan.objective) !== canonical(context.job.objective)
    || canonical(context.plan.acceptanceCriteria) !== canonical(context.job.acceptanceCriteria)
    || context.childCheckpoint.jobId !== context.job.jobId
    || context.childCheckpoint.planSha256 !== sha(context.plan)
    || context.childCheckpoint.sequence === 0 && context.childCheckpoint.workspaceSnapshotSha256 !== context.workspaceSnapshotSha256
    || context.goalCheckpoint.active?.jobId !== context.job.jobId
    || context.goalCheckpoint.active?.jobSha256 !== sha(context.job)
  ) fail("goal Builder context differs from its immutable child");
  const expectedAction = decideCheckpointAction(context.childCheckpoint, context.plan);
  if (canonical(expectedAction) !== canonical(context.childAction)) fail("goal Builder action differs from the authoritative child checkpoint");
  return context;
}

function checkedPrepared(context, prepared, mode) {
  if (
    !prepared || canonical(prepared.plan) !== canonical(context.plan) || canonical(prepared.lease) !== canonical(context.lease)
    || prepared.workspace?.sha256 !== context.workspaceSnapshotSha256
    || prepared.bindings?.planSha256 !== sha(context.plan) || prepared.bindings?.leaseSha256 !== sha(context.lease)
    || prepared.bindings?.policySha256 !== context.plan.policySha256
    || prepared.bindings?.inputSetSha256 !== context.plan.inputSetSha256
  ) fail("prepared Builder boundary differs from the goal child");
  if (mode === "execute" && (prepared.verificationOnly === true || prepared.cleanupOnly === true)) fail("goal Builder execution received a recovery-only preparation");
  if (mode === "verify" && (prepared.verificationOnly !== true || !prepared.recoveryConsumption)) fail("goal Builder verifier recovery preparation is invalid");
  if (mode === "cleanup" && (prepared.cleanupOnly !== true || !prepared.recoveryConsumption)) fail("goal Builder cleanup preparation is invalid");
  return prepared;
}

function executionMode(context) {
  const action = context.childAction.action;
  if (action === "dispatch-worker") {
    if (context.childCheckpoint.state !== "authorized" || context.lease.iteration !== 1 || context.lease.continuation !== null) fail("goal Builder initial execution boundary is invalid");
    return "execute";
  }
  if (action === "issue-continuation-lease") {
    if (
      context.childCheckpoint.state !== "verified" || context.lease.iteration !== context.childCheckpoint.iteration + 1
      || context.lease.continuation?.previousCheckpointSha256 !== checkpointSha256(context.childCheckpoint)
    ) fail("goal Builder continuation boundary is invalid");
    return "execute";
  }
  if (action === "resume-independent-verifier") return "verify";
  if (action === "fail-interrupted-worker-after-cleanup") return "cleanup";
  fail("goal Builder driver received a non-drivable child action");
}

function contentFree(result) {
  const checkpoint = result?.checkpoint;
  if (!checkpoint || validateWorkCheckpoint(checkpoint).length) fail("goal Builder operation returned no valid checkpoint");
  const expectedAction = {
    completed: "completed", verified: "continue", failed: "failed", "no-progress": "no-progress", "budget-exhausted": "budget-exhausted",
  }[checkpoint.state];
  if (!expectedAction || result.action !== expectedAction) fail("goal Builder operation action differs from its checkpoint");
  return {
    action: result.action,
    childState: checkpoint.state,
    childCheckpointSha256: checkpointSha256(checkpoint),
    grantsExecution: false,
    grantsCompletion: false,
    containsWorkerOutput: false,
  };
}

export function createGoalBuilderDriver({
  stateRoot,
  prepare,
  lifecycleOptions = {},
  clock = () => new Date(),
  suffixes = () => ({}),
  discard = discardPreparedRun,
  candidateRunner,
  verifier,
  candidateRecovery,
  verificationRecovery,
  cleanup,
} = {}) {
  if (typeof stateRoot !== "string" || !stateRoot || typeof prepare !== "function" || typeof clock !== "function" || typeof suffixes !== "function" || typeof discard !== "function") fail("goal Builder driver configuration is invalid");
  if (typeof lifecycleOptions !== "function" && (!lifecycleOptions || typeof lifecycleOptions !== "object" || Array.isArray(lifecycleOptions))) fail("goal Builder lifecycle options are invalid");
  for (const [label, callback] of Object.entries({ candidateRunner, verifier, candidateRecovery, verificationRecovery, cleanup })) {
    if (callback !== undefined && typeof callback !== "function") fail(`goal Builder ${label} callback is invalid`);
  }
  return async (rawContext) => {
    const context = checkedContext(rawContext);
    const mode = executionMode(context);
    if (mode === "execute") {
      const observed = clock();
      const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
      if (!Number.isFinite(milliseconds) || milliseconds < Date.parse(context.lease.issuedAt) || milliseconds >= Date.parse(context.lease.expiresAt)) fail("goal Builder execution lease is not current");
    }
    const prepared = checkedPrepared(context, await prepare({ mode, context: structuredClone(context) }), mode);
    const options = typeof lifecycleOptions === "function" ? await lifecycleOptions({ mode, context: structuredClone(context) }) : lifecycleOptions;
    const records = suffixes({ mode, context: structuredClone(context) });
    if (!records || typeof records !== "object" || Array.isArray(records)) fail("goal Builder checkpoint suffixes are invalid");
    try {
      if (mode === "execute") {
        return contentFree(await executeBuilderIteration({
          stateRoot, prepared, lifecycleOptions: options, clock, suffixes: records,
          ...(candidateRunner ? { candidateRunner } : {}), ...(verifier ? { verifier } : {}),
        }));
      }
      if (mode === "verify") {
        return contentFree(await resumeBuilderVerification({
          stateRoot, prepared, lifecycleOptions: options, clock, suffixes: records,
          ...(candidateRecovery ? { candidateRecovery } : {}), ...(verifier ? { verifier } : {}),
          ...(verificationRecovery ? { verificationRecovery } : {}),
        }));
      }
      return contentFree(await recordInterruptedBuilderFailure({
        stateRoot, prepared, lifecycleOptions: options, clock, checkpointSuffix: records.failed,
        ...(cleanup ? { cleanup } : {}),
      }));
    } finally {
      if (prepared.cleanupOnly !== true) await discard(prepared);
    }
  };
}

export const goalBuilderDriverBoundary = "Internal exact-profile bridge only. Durable goals select an immutable Builder job; the existing one-use lease, disposable runner, cleanup boundary, and independent verifier retain all execution and completion authority.";

import { join, resolve } from "node:path";

import {
  canonical, validateJobPlanLease, validateWorkCheckpoint, validateWorkGoal,
  validateWorkGoalCheckpoint, validateWorkJob, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import {
  prepareBuilderCleanupRecovery, prepareBuilderRun, prepareBuilderVerificationRecovery,
  recoverLeaseConsumption,
} from "../work-runner/runner-core.mjs";
import { decideCheckpointAction } from "./checkpoints.mjs";
import { recoverGoalRunBundles } from "./goal-run-bundles.mjs";
import { goalSha256 } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const contextKeys = Object.freeze([
  "job", "plan", "lease", "workspaceSnapshotSha256", "goalCheckpoint", "childCheckpoint", "childAction",
]);

export class GoalBuilderPreparerError extends Error {}

function fail(message) { throw new GoalBuilderPreparerError(message); }

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

function absolutePath(value, label) {
  if (typeof value !== "string" || !value || resolve(value) !== value) fail(`${label} must be an absolute normalized path`);
  return value;
}

function observedNow(clock) {
  const value = clock();
  const milliseconds = value instanceof Date ? value.getTime() : Number(value);
  if (!Number.isFinite(milliseconds)) fail("goal Builder preparer clock is invalid");
  return new Date(Math.trunc(milliseconds));
}

function checkedConfiguration({ stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits }) {
  if (validateWorkGoal(goal).length || !Array.isArray(jobs) || jobs.length !== goal?.milestones?.length || validateWorkPolicy(policy).length) fail("goal Builder preparer contracts are invalid");
  const ids = new Set();
  for (const job of jobs) {
    if (validateWorkJob(job).length || ids.has(job.jobId)) fail("goal Builder preparer job registry is invalid");
    ids.add(job.jobId);
  }
  exactKeys(archiveLimits, ["maxEntries", "maxFileBytes"], "goal Builder archive limits");
  if (
    !Number.isSafeInteger(archiveLimits.maxEntries) || archiveLimits.maxEntries < 1 || archiveLimits.maxEntries > 100000
    || !Number.isSafeInteger(archiveLimits.maxFileBytes) || archiveLimits.maxFileBytes < 1 || archiveLimits.maxFileBytes > 4294967296
  ) fail("goal Builder archive limits are invalid");
  return {
    stateRoot: absolutePath(stateRoot, "goal Builder state root"),
    objectStore: absolutePath(objectStore, "goal Builder object store"),
    workspaceRoot: absolutePath(workspaceRoot, "goal Builder workspace root"),
    executorPath: absolutePath(executorPath, "goal Builder executor"),
  };
}

function checkedRequest(raw, jobs) {
  exactKeys(raw, ["mode", "context"], "goal Builder preparation request");
  const { mode, context } = raw;
  if (!new Set(["execute", "verify", "cleanup"]).has(mode)) fail("goal Builder preparation mode is invalid");
  exactKeys(context, contextKeys, "goal Builder preparation context");
  if (
    validateWorkJob(context.job).length || validateJobPlanLease(context.job, context.plan, context.lease).length
    || validateWorkGoalCheckpoint(context.goalCheckpoint).length || validateWorkCheckpoint(context.childCheckpoint).length
    || !SHA_RE.test(context.workspaceSnapshotSha256 ?? "")
  ) fail("goal Builder preparation context contract is invalid");
  const registered = jobs.find((job) => job.jobId === context.job.jobId);
  if (
    !registered || canonical(registered) !== canonical(context.job) || context.job.profile !== "builder"
    || context.goalCheckpoint.active?.jobId !== context.job.jobId
    || context.goalCheckpoint.active?.jobSha256 !== goalSha256(context.job)
    || canonical(context.childAction) !== canonical(decideCheckpointAction(context.childCheckpoint, context.plan))
  ) fail("goal Builder preparation context differs from the authoritative child");
  const expectedMode = ["dispatch-worker", "issue-continuation-lease"].includes(context.childAction.action) ? "execute"
    : context.childAction.action === "resume-independent-verifier" ? "verify"
      : context.childAction.action === "fail-interrupted-worker-after-cleanup" ? "cleanup" : null;
  if (mode !== expectedMode) fail("goal Builder preparation mode differs from the authoritative child action");
  return { mode, context };
}

function exactCustody(context, recovered) {
  if (
    canonical(recovered.head.plan) !== canonical(context.plan)
    || canonical(recovered.head.lease) !== canonical(context.lease)
    || recovered.head.workspaceSnapshotSha256 !== context.workspaceSnapshotSha256
  ) fail("goal Builder preparation context differs from durable custody");
  return recovered;
}

function previousIterationBundle(recovered, iteration) {
  if (recovered.head.purpose !== "continuation" || recovered.head.lease.iteration !== iteration) fail("goal Builder continuation custody head is invalid");
  const previous = recovered.bundles.at(-2);
  if (!previous || previous.lease.iteration !== iteration - 1 || !["admission", "continuation"].includes(previous.purpose)) fail("goal Builder continuation custody predecessor is invalid");
  return previous;
}

export function createGoalBuilderPreparer({
  stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits,
  clock = () => new Date(),
  builderRun = prepareBuilderRun,
  verificationRecovery = prepareBuilderVerificationRecovery,
  cleanupRecovery = prepareBuilderCleanupRecovery,
} = {}) {
  if (typeof clock !== "function" || typeof builderRun !== "function" || typeof verificationRecovery !== "function" || typeof cleanupRecovery !== "function") fail("goal Builder preparer callbacks are invalid");
  const paths = checkedConfiguration({ stateRoot, goal, jobs, policy, objectStore, workspaceRoot, executorPath, archiveLimits });
  return async (raw) => {
    const { mode, context } = checkedRequest(raw, jobs);
    const recovered = exactCustody(context, await recoverGoalRunBundles({
      stateRoot: paths.stateRoot, goal, jobs, jobId: context.job.jobId,
    }));
    const common = {
      stateRoot: paths.stateRoot, plan: context.plan, lease: context.lease, policy,
      objectStore: paths.objectStore, workspaceRoot: paths.workspaceRoot, executorPath: paths.executorPath,
      archiveLimits, now: observedNow(clock),
    };
    if (mode === "execute") {
      if (context.lease.iteration === 1) {
        if (recovered.head.purpose !== "admission" || context.childAction.action !== "dispatch-worker") fail("goal Builder initial preparation lacks durable admission");
        return builderRun(common);
      }
      if (context.childAction.action !== "issue-continuation-lease") fail("goal Builder continuation preparation action is invalid");
      const previous = previousIterationBundle(recovered, context.lease.iteration);
      const consumption = await recoverLeaseConsumption(paths.stateRoot, previous.lease);
      return builderRun({
        ...common,
        continuation: {
          previousLease: previous.lease,
          previousConsumption: consumption,
          checkpoint: context.childCheckpoint,
          patchPath: join(paths.stateRoot, "results", consumption.claimId, "builder-patch.json"),
        },
      });
    }
    const consumption = await recoverLeaseConsumption(paths.stateRoot, context.lease);
    const recovery = { consumption, checkpoint: context.childCheckpoint };
    if (mode === "verify") return verificationRecovery({ ...common, recovery });
    return cleanupRecovery({ ...common, recovery });
  };
}

export const goalBuilderPreparerBoundary = "Internal disk-derived Builder preparation only. Goal context must equal private custody; continuation and recovery claims and artifact paths are reconstructed from exact durable state, never accepted from a worker or caller.";

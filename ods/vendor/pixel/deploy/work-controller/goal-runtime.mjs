import { randomBytes } from "node:crypto";
import { lstat } from "node:fs/promises";
import { join, resolve } from "node:path";

import { canonical, validateJobPlanLease } from "../../scripts/lib/work-contract.mjs";
import {
  initializeCheckpointLedger, recoverCheckpointLedger,
} from "./checkpoints.mjs";
import {
  completeGoal, dispatchGoalMilestone, goalSha256, observeGoalMilestone, recoverGoalLedger,
} from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const childTerminalStates = new Set(["completed", "failed", "cancelled", "budget-exhausted", "no-progress", "recovery-inconclusive"]);

export class WorkGoalRuntimeError extends Error {}

function fail(message) { throw new WorkGoalRuntimeError(message); }

function exactRun(job, value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["lease", "plan", "workspaceSnapshotSha256"].sort())) fail("goal runtime child bundle shape is invalid");
  const bindingErrors = validateJobPlanLease(job, value.plan, value.lease);
  if (bindingErrors.length || !SHA_RE.test(value.workspaceSnapshotSha256 ?? "")) fail("goal runtime child bundle contract is invalid");
  if (
    value.plan.jobId !== job.jobId || value.plan.requestSha256 !== goalSha256(job)
    || value.plan.profile !== job.profile || value.plan.dataClassification !== job.dataClassification
    || canonical(value.plan.objective) !== canonical(job.objective)
    || canonical(value.plan.acceptanceCriteria) !== canonical(job.acceptanceCriteria)
  ) fail("goal runtime child bundle differs from the immutable job");
  return Object.freeze({ plan: value.plan, lease: value.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256 });
}

function nextNow(head, clock) {
  const observed = clock();
  const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
  if (!Number.isFinite(milliseconds)) fail("goal runtime clock is invalid");
  return new Date(Math.max(Date.parse(head.createdAt) + 1, Math.trunc(milliseconds)));
}

function nextSuffix(suffix) {
  const value = suffix?.() ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(value)) fail("goal runtime suffix is invalid");
  return value;
}

function authorityState(run, clock) {
  const observed = clock();
  const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
  if (!Number.isFinite(milliseconds)) fail("goal runtime clock is invalid");
  const issuedAt = Date.parse(run.lease.issuedAt);
  const expiresAt = Date.parse(run.lease.expiresAt);
  if (milliseconds < issuedAt) return "not-yet-valid";
  if (milliseconds >= expiresAt) return "expired";
  return "valid";
}

async function childLedgerExists(stateRoot, jobId) {
  const path = join(resolve(stateRoot), "checkpoints", jobId);
  const info = await lstat(path).catch((error) => {
    if (error?.code === "ENOENT") return null;
    throw error;
  });
  if (info === null) return false;
  if (!info.isDirectory() || info.isSymbolicLink()) fail("goal runtime child ledger path is unsafe");
  return true;
}

async function resolveRun({ resolveChildRun, job, goalCheckpoint, childCheckpoint = null, childAction = null }) {
  const value = await resolveChildRun({
    job: structuredClone(job),
    goalCheckpoint: structuredClone(goalCheckpoint),
    childCheckpoint: childCheckpoint && structuredClone(childCheckpoint),
    childAction: childAction && structuredClone(childAction),
  });
  return exactRun(job, value);
}

function contentFreeResult(goalLedger, action, extra = {}, durableProgress = false) {
  return {
    action,
    goalState: goalLedger.head.state,
    goalCheckpointSha256: goalLedger.headSha256,
    sequence: goalLedger.head.sequence,
    milestonesCompleted: goalLedger.head.progress.milestonesCompleted,
    milestonesTotal: goalLedger.head.progress.milestonesTotal,
    durableProgress,
    replaysChild: false,
    grantsExecution: false,
    ...extra,
  };
}

export async function runGoalCycle({
  stateRoot,
  goal,
  jobs,
  resolveChildRun,
  driveChild,
  maxControllerTransitions = 128,
  clock = () => new Date(),
  suffix,
}) {
  if (typeof resolveChildRun !== "function" || typeof driveChild !== "function") fail("goal runtime requires exact child resolver and driver boundaries");
  if (!Number.isSafeInteger(maxControllerTransitions) || maxControllerTransitions < 1 || maxControllerTransitions > 256) fail("goal runtime transition ceiling is invalid");
  const jobsById = new Map(jobs?.map((job) => [job.jobId, job]) ?? []);
  if (jobsById.size !== jobs?.length) fail("goal runtime child job registry is invalid");
  let durableProgress = false;

  for (let transition = 0; transition < maxControllerTransitions; transition += 1) {
    let goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
    const decision = goalLedger.action;
    if (decision.action === "terminal") return contentFreeResult(goalLedger, "terminal", {}, durableProgress);
    if (decision.action === "paused") return contentFreeResult(goalLedger, "paused", {}, durableProgress);
    if (decision.action === "record-completed") {
      await completeGoal({ stateRoot, goal, jobs, now: nextNow(goalLedger.head, clock), suffix: nextSuffix(suffix) });
      durableProgress = true;
      goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
      return contentFreeResult(goalLedger, "terminal", {}, durableProgress);
    }
    if (decision.action === "dispatch-child") {
      await dispatchGoalMilestone({ stateRoot, goal, jobs, now: nextNow(goalLedger.head, clock), suffix: nextSuffix(suffix) });
      durableProgress = true;
      continue;
    }
    if (!["recover-or-continue-child", "wait-for-child-authority"].includes(decision.action)) fail("goal runtime reached an unsupported controller action");
    const job = jobsById.get(decision.jobId);
    if (!job) fail("goal runtime active child is not in the immutable registry");

    let run = await resolveRun({ resolveChildRun, job, goalCheckpoint: goalLedger.head });
    if (!await childLedgerExists(stateRoot, job.jobId)) {
      const authority = authorityState(run, clock);
      if (authority !== "valid") return contentFreeResult(goalLedger, `child-authority-${authority}`, {}, durableProgress);
      try {
        await initializeCheckpointLedger({
          stateRoot, plan: run.plan, lease: run.lease, workspaceSnapshotSha256: run.workspaceSnapshotSha256,
          now: nextNow(goalLedger.head, clock), suffix: nextSuffix(suffix),
        });
        durableProgress = true;
      } catch (error) {
        if (!await childLedgerExists(stateRoot, job.jobId)) throw error;
      }
    }

    let childLedger = await recoverCheckpointLedger({ stateRoot, plan: run.plan, lease: run.lease });
    if (childLedger.head.state === "waiting-authority" || childTerminalStates.has(childLedger.head.state)) {
      const observed = await observeGoalMilestone({
        stateRoot, goal, jobs, childPlan: run.plan, childLease: run.lease,
        now: nextNow(goalLedger.head, clock), suffix: nextSuffix(suffix),
      });
      durableProgress = true;
      goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
      if (["dispatch-next-child", "record-completed"].includes(observed.action)) continue;
      return contentFreeResult(goalLedger, observed.action, {}, durableProgress);
    }

    run = await resolveRun({
      resolveChildRun, job, goalCheckpoint: goalLedger.head,
      childCheckpoint: childLedger.head, childAction: childLedger.action,
    });
    childLedger = await recoverCheckpointLedger({ stateRoot, plan: run.plan, lease: run.lease });
    const authority = authorityState(run, clock);
    if (authority !== "valid") return contentFreeResult(goalLedger, `child-authority-${authority}`, { childState: childLedger.head.state }, durableProgress);
    const justInTimeGoal = await recoverGoalLedger({ stateRoot, goal, jobs });
    if (justInTimeGoal.headSha256 !== goalLedger.headSha256) {
      durableProgress = true;
      if (justInTimeGoal.action.action === "paused") return contentFreeResult(justInTimeGoal, "paused", { childState: childLedger.head.state }, durableProgress);
      if (justInTimeGoal.action.action === "terminal") return contentFreeResult(justInTimeGoal, "terminal", { childState: childLedger.head.state }, durableProgress);
      continue;
    }
    const before = childLedger.headSha256;
    try {
      await driveChild({
        job: structuredClone(job), plan: structuredClone(run.plan), lease: structuredClone(run.lease),
        workspaceSnapshotSha256: run.workspaceSnapshotSha256,
        goalCheckpoint: structuredClone(goalLedger.head), childCheckpoint: structuredClone(childLedger.head),
        childAction: structuredClone(childLedger.action),
      });
    } catch (error) {
      const durable = await recoverCheckpointLedger({ stateRoot, plan: run.plan, lease: run.lease }).catch(() => null);
      if (!durable || !childTerminalStates.has(durable.head.state) || durable.headSha256 === before) throw error;
      durableProgress = true;
      continue;
    }
    const after = await recoverCheckpointLedger({ stateRoot, plan: run.plan, lease: run.lease });
    const afterDriveGoal = await recoverGoalLedger({ stateRoot, goal, jobs });
    if (afterDriveGoal.headSha256 !== goalLedger.headSha256) {
      durableProgress = true;
      if (afterDriveGoal.action.action === "paused") {
        return contentFreeResult(afterDriveGoal, "paused", { childState: after.head.state }, durableProgress);
      }
      if (afterDriveGoal.action.action === "terminal") {
        return contentFreeResult(afterDriveGoal, "terminal", { childState: after.head.state }, durableProgress);
      }
      continue;
    }
    if (after.headSha256 === before) {
      return contentFreeResult(afterDriveGoal, "child-in-progress", { childState: after.head.state }, durableProgress);
    }
    durableProgress = true;
    const observed = await observeGoalMilestone({
      stateRoot, goal, jobs, childPlan: run.plan, childLease: run.lease,
      now: nextNow(goalLedger.head, clock), suffix: nextSuffix(suffix),
    });
    durableProgress = true;
    goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
    if (["dispatch-next-child", "record-completed", "continue-or-recover-child"].includes(observed.action)) continue;
    return contentFreeResult(goalLedger, observed.action, {}, durableProgress);
  }
  const goalLedger = await recoverGoalLedger({ stateRoot, goal, jobs });
  return contentFreeResult(goalLedger, "controller-yield", {}, durableProgress);
}

export const goalRuntimeBoundary = "Restartable bounded controller loop. Child effects remain exclusively inside exact child plan/lease runners; runtime callbacks cannot alter goal scope, criteria, dependencies, classification, budgets, or completion evidence.";

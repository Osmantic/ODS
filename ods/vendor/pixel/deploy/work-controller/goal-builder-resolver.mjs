import { randomBytes } from "node:crypto";

import { canonical, validateWorkCheckpoint, validateWorkGoalCheckpoint, validateWorkJob } from "../../scripts/lib/work-contract.mjs";
import { readContinuationLease } from "./continuation.mjs";
import { checkpointSha256, decideCheckpointAction } from "./checkpoints.mjs";
import {
  appendContinuationGoalRunBundle, recoverGoalRunBundles, refreshExpiredGoalRunBundle, resolveGoalRunBundle,
} from "./goal-run-bundles.mjs";
import { recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import { goalSha256 } from "./goals.mjs";

export class GoalBuilderResolverError extends Error {}

function fail(message) { throw new GoalBuilderResolverError(message); }

function exactContext(context) {
  const keys = ["job", "goalCheckpoint", "childCheckpoint", "childAction"];
  if (!context || typeof context !== "object" || Array.isArray(context) || canonical(Object.keys(context).sort()) !== canonical(keys.sort())) fail("goal Builder resolver context shape is invalid");
  if (validateWorkJob(context.job).length || validateWorkGoalCheckpoint(context.goalCheckpoint).length) fail("goal Builder resolver parent context is invalid");
  if (
    context.goalCheckpoint.active?.jobId !== context.job.jobId
    || context.goalCheckpoint.active?.jobSha256 !== goalSha256(context.job)
  ) fail("goal Builder resolver parent differs from the active child");
  if (context.childCheckpoint === null && context.childAction !== null) fail("goal Builder resolver action lacks a child checkpoint");
  if (context.childCheckpoint !== null && validateWorkCheckpoint(context.childCheckpoint).length) fail("goal Builder resolver child checkpoint is invalid");
  if (context.childAction !== null && (!context.childAction || typeof context.childAction !== "object" || Array.isArray(context.childAction))) fail("goal Builder resolver child action is invalid");
  return context;
}

function observedNow(clock) {
  const value = clock();
  const milliseconds = value instanceof Date ? value.getTime() : Number(value);
  if (!Number.isFinite(milliseconds)) fail("goal Builder resolver clock is invalid");
  return new Date(Math.trunc(milliseconds));
}

function nextSuffix(suffix) {
  const value = suffix?.() ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(value)) fail("goal Builder resolver suffix is invalid");
  return value;
}

export function createGoalBuilderResolver({ stateRoot, goal, jobs, policy, objectStore, clock = () => new Date(), suffix } = {}) {
  if (typeof stateRoot !== "string" || !stateRoot || !goal || !Array.isArray(jobs) || !policy || typeof objectStore !== "string" || !objectStore || typeof clock !== "function") fail("goal Builder resolver configuration is invalid");
  if (suffix !== undefined && typeof suffix !== "function") fail("goal Builder resolver suffix source is invalid");

  return async (rawContext) => {
    const context = exactContext(rawContext);
    if (context.job.profile !== "builder") fail("goal Builder resolver accepts only Builder children");
    const refreshTime = observedNow(clock);
    await refreshExpiredGoalRunBundle({
      stateRoot, goal, jobs, jobId: context.job.jobId, policy, objectStore, now: refreshTime,
      compilerSuffix: nextSuffix(suffix), suffix: nextSuffix(suffix),
    });
    let recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId: context.job.jobId });
    if (context.childCheckpoint !== null) {
      if (
        context.childCheckpoint.jobId !== context.job.jobId
        || canonical(context.childAction) !== canonical(decideCheckpointAction(context.childCheckpoint, recovered.head.plan))
      ) fail("goal Builder resolver child action differs from the authoritative checkpoint");
    }
    if (context.childAction?.action === "issue-continuation-lease") {
      if (context.childCheckpoint.state !== "verified") fail("goal Builder continuation action lacks verified child evidence");
      const expectedIteration = context.childCheckpoint.iteration + 1;
      if (recovered.head.lease.iteration < expectedIteration) {
        if (recovered.head.lease.iteration !== context.childCheckpoint.iteration) fail("goal Builder custody skipped a continuation iteration");
        const previousConsumption = await recoverLeaseConsumption(stateRoot, recovered.head.lease);
        const observed = observedNow(clock);
        const persisted = await readContinuationLease({
          stateRoot, plan: recovered.head.plan, previousLease: recovered.head.lease, policy,
          previousConsumption, checkpoint: context.childCheckpoint, iteration: expectedIteration,
          now: observed, allowNotYetValid: true,
        });
        const publicationTime = new Date(Math.max(
          observed.getTime(), Date.parse(persisted.lease.issuedAt), Date.parse(recovered.head.createdAt) + 1,
        ));
        if (publicationTime.getTime() >= Date.parse(persisted.lease.expiresAt)) fail("goal Builder continuation expired before custody publication");
        try {
          await appendContinuationGoalRunBundle({
            stateRoot, goal, jobs, jobId: context.job.jobId, plan: recovered.head.plan, lease: persisted.lease,
            workspaceSnapshotSha256: recovered.head.workspaceSnapshotSha256,
            now: publicationTime, suffix: nextSuffix(suffix),
          });
        } catch (error) {
          recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId: context.job.jobId });
          if (recovered.head.lease.iteration !== expectedIteration || canonical(recovered.head.lease) !== canonical(persisted.lease)) throw error;
        }
        recovered = await recoverGoalRunBundles({ stateRoot, goal, jobs, jobId: context.job.jobId });
      }
      if (
        recovered.head.lease.iteration !== expectedIteration
        || recovered.head.lease.continuation?.previousCheckpointSha256 !== checkpointSha256(context.childCheckpoint)
      ) fail("goal Builder custody differs from the requested continuation");
    }
    const proposedAdmission = observedNow(clock);
    const admissionTime = new Date(Math.max(proposedAdmission.getTime(), Date.parse(recovered.head.createdAt) + 1));
    return resolveGoalRunBundle({
      stateRoot, goal, jobs, jobId: context.job.jobId,
      now: admissionTime, suffix: nextSuffix(suffix),
    });
  };
}

export const goalBuilderResolverBoundary = "Internal durable Builder resolver only. It may renew one expired unclaimed pre-admission lease with byte-equivalent authority or recover an already issued exact continuation lease, then append it to private capability custody; it cannot widen the job, policy, inputs, budgets, tools, model, acceptance criteria, external effects, or completion evidence.";

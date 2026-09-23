import { createHash, randomBytes } from "node:crypto";
import { lstat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { appendCheckpoint, checkpointSha256, initializeCheckpointLedger, recoverCheckpointLedger } from "./checkpoints.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";
import { admitGoalRunBundle, recoverGoalRunBundles } from "./goal-run-bundles.mjs";
import {
  cancelGoalAfterCleanup, cancelGoalAfterLeaseRevocation, cancelReadyGoal,
  goalCheckpointSha256, goalSha256, recoverGoalLedger,
} from "./goals.mjs";
import {
  createLeaseRevocation, inspectLeaseDisposition, revokeLease,
} from "../work-runner/runner-core.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsWorkerStop: false,
  grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Content-free exact-confirmed parent cancellation. An inactive goal may end directly; an unlaunched child requires an atomic lease revocation; a started child requires terminal cleanup evidence. The command cannot stop a live worker, launch work, replay a claim, widen scope, grant completion, or cause external effects.";

export class GoalCancelCliError extends Error {}

function fail(message) { throw new GoalCancelCliError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv)) fail("goal cancellation arguments are invalid");
  if (argv.length === 4 && argv[0] === "--config" && typeof argv[1] === "string" && argv[1] && argv[2] === "--confirm-goal-sha256" && SHA_RE.test(argv[3] ?? "")) {
    return { operation: "legacy-apply", configPath: resolve(argv[1]), confirmation: argv[3] };
  }
  if (argv.length === 3 && argv[0] === "review" && argv[1] === "--config" && typeof argv[2] === "string" && argv[2]) {
    return { operation: "review", configPath: resolve(argv[2]), confirmation: null };
  }
  if (argv.length === 5 && argv[0] === "apply" && argv[1] === "--config" && typeof argv[2] === "string" && argv[2] && argv[3] === "--confirm-review-sha256" && SHA_RE.test(argv[4] ?? "")) {
    return { operation: "review-apply", configPath: resolve(argv[2]), confirmation: argv[4] };
  }
  fail("Usage: goal-cancel-cli.mjs <review|apply> --config FILE [--confirm-review-sha256 HASH]");
}

function checkedDate(value, label) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() < 0) fail(`${label} is invalid`);
  return date;
}

function stageSuffix(dependencies, key) {
  const value = dependencies[key] ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(value)) fail(`goal cancellation ${key} is invalid`);
  return value;
}

function afterDate(requested, ...timestamps) {
  const latest = timestamps.reduce((value, timestamp) => Math.max(value, Date.parse(timestamp)), -1);
  return new Date(Math.max(requested.getTime(), latest + 1));
}

async function realDirectory(path, label) {
  const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (info === null) return false;
  if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label} is unsafe`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return true;
}

function reviewValue(goal, ledger, mode, childCheckpointSha256 = null, run = null) {
  const value = {
    schemaVersion: 1, operation: "pixel-work-goal-cancel", goalSha256: goalSha256(goal),
    goalCheckpointSha256: ledger.headSha256, mode, childCheckpointSha256,
  };
  if (mode === "unlaunched-child-revocation") {
    if (!run?.head) fail("goal cancellation review lacks exact child run custody");
    value.planSha256 = sha(run.head.plan);
    value.leaseSha256 = sha(run.head.lease);
  }
  return value;
}

function reviewSha256(goal, ledger, context) {
  return sha(reviewValue(goal, ledger, context.mode, context.childCheckpointSha256 ?? null, context.run ?? null));
}

function sameControlFacts(left, right) {
  const facts = (value) => ({
    completedMilestones: value.completedMilestones, active: value.active, observation: value.observation,
    usage: value.usage, progress: value.progress, failureFingerprintSha256: value.failureFingerprintSha256,
    authorityExpansionObserved: value.authorityExpansionObserved, externalEffectsObserved: value.externalEffectsObserved,
  });
  return canonical(facts(left)) === canonical(facts(right));
}

function checkedRevocation(goal, ledger, run, revocation) {
  const parent = ledger.checkpoints.find((checkpoint) => goalCheckpointSha256(checkpoint) === revocation.goalCheckpointSha256);
  if (
    !parent?.active || !sameControlFacts(parent, ledger.head)
    || revocation.goalId !== goal.goalId || revocation.jobId !== run.head.plan.jobId
    || revocation.leaseId !== run.head.lease.leaseId || revocation.planSha256 !== sha(run.head.plan)
    || revocation.leaseSha256 !== sha(run.head.lease)
  ) fail("goal cancellation lease revocation differs from active custody");
  const expectedReview = sha(reviewValue(
    goal, { head: parent, headSha256: revocation.goalCheckpointSha256 },
    "unlaunched-child-revocation", revocation.reviewedChildCheckpointSha256, run,
  ));
  if (revocation.cancellationReviewSha256 !== expectedReview) fail("goal cancellation lease revocation differs from its exact review");
  return parent;
}

async function cancellationContext(config, goal, jobs, ledger) {
  if (ledger.head.state === "cancelled") return { disposition: "already-cancelled", mode: null };
  if (["completed", "failed", "budget-exhausted", "no-progress", "recovery-inconclusive"].includes(ledger.head.state)) {
    return { disposition: "already-terminal", mode: null };
  }
  if (ledger.head.active === null) {
    if (!["ready", "paused"].includes(ledger.head.state)) return { disposition: "not-cancellable", mode: null };
    return { disposition: "cancellable", mode: "inactive-goal", childState: null, childCheckpointSha256: null };
  }
  const custodyPath = join(resolve(config.stateRoot), "goal-runs", goal.goalId, ledger.head.active.jobId);
  if (!await realDirectory(custodyPath, "goal cancellation child custody")) {
    return { disposition: "child-terminal-evidence-required", mode: null, childState: null };
  }
  const run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: ledger.head.active.jobId });
  const leaseDisposition = await inspectLeaseDisposition(config.stateRoot, run.head.lease);
  const child = run.childLedgerPresent
    ? await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease }) : null;
  if (child?.head.state === "failed") {
    if (leaseDisposition?.kind === "revoked") fail("a revoked unlaunched child cannot carry failed worker evidence");
    if (!["running", "paused"].includes(ledger.head.state)) return { disposition: "child-stop-cleanup-required", mode: null, childState: child.head.state };
    return {
      disposition: "cancellable", mode: "supervised-child-after-cleanup", childState: child.head.state,
      childCheckpointSha256: child.headSha256, run,
    };
  }
  if (leaseDisposition?.kind === "consumed") return { disposition: "child-stop-cleanup-required", mode: null, childState: child?.head.state ?? null };
  if (child && !["authorized", "cancelled"].includes(child.head.state)) {
    return { disposition: "child-stop-cleanup-required", mode: null, childState: child.head.state };
  }
  if (child?.head.state === "cancelled" && leaseDisposition?.kind !== "revoked") fail("cancelled child lacks an exact lease revocation");
  let reviewedChildCheckpointSha256 = child?.headSha256 ?? null;
  let revocation = null;
  if (leaseDisposition?.kind === "revoked") {
    revocation = leaseDisposition.record;
    checkedRevocation(goal, ledger, run, revocation);
    if (revocation.goalCheckpointSha256 === ledger.headSha256) reviewedChildCheckpointSha256 = revocation.reviewedChildCheckpointSha256;
  }
  return {
    disposition: "cancellable", mode: "unlaunched-child-revocation", childState: child?.head.state ?? null,
    childCheckpointSha256: reviewedChildCheckpointSha256, run, child, revocation,
  };
}

async function matchingCancellation(config, ledger, goal, jobs, confirmation) {
  for (let index = 1; index < ledger.checkpoints.length; index += 1) {
    const previous = ledger.checkpoints[index - 1], current = ledger.checkpoints[index];
    if (current.state !== "cancelled") continue;
    const mode = previous.active === null ? "inactive-goal"
      : current.observation?.childState === "cancelled" ? "unlaunched-child-revocation" : "supervised-child-after-cleanup";
    let childCheckpointSha256 = previous.active === null ? null : current.observation?.childCheckpointSha256 ?? null;
    let reviewedRun = null;
    const previousLedger = { head: previous, headSha256: goalCheckpointSha256(previous) };
    if (mode === "unlaunched-child-revocation") {
      reviewedRun = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: previous.active.jobId });
      const disposition = await inspectLeaseDisposition(config.stateRoot, reviewedRun.head.lease);
      const revocation = disposition?.kind === "revoked" ? disposition.record : null;
      if (
        !revocation || revocation.goalId !== goal.goalId || revocation.jobId !== previous.active.jobId
        || revocation.planSha256 !== sha(reviewedRun.head.plan) || revocation.leaseSha256 !== sha(reviewedRun.head.lease)
      ) continue;
      checkedRevocation(goal, { ...ledger, head: previous }, reviewedRun, revocation);
      const childHashes = revocation.goalCheckpointSha256 === previousLedger.headSha256
        ? [revocation.reviewedChildCheckpointSha256]
        : [null, ...(await recoverCheckpointLedger({
          stateRoot: config.stateRoot, plan: reviewedRun.head.plan, lease: reviewedRun.head.lease,
        })).checkpoints.map((checkpoint) => checkpointSha256(checkpoint))];
      if (childHashes.some((value) => sha(reviewValue(goal, previousLedger, mode, value, reviewedRun)) === confirmation)) return true;
      continue;
    }
    if (sha(reviewValue(goal, previousLedger, mode, childCheckpointSha256, reviewedRun)) === confirmation) return true;
  }
  return false;
}

function reviewReceipt(ledger, goal, context) {
  const cancellable = context.disposition === "cancellable";
  return {
    schemaVersion: 1, operation: "pixel-work-goal-cancel-review", status: ledger.head.state,
    action: cancellable ? "confirmation-required" : context.disposition,
    goalCheckpointSha256: ledger.headSha256, sequence: ledger.head.sequence,
    cancellation: { mode: context.mode, recordsTerminalState: cancellable, childState: context.childState ?? null },
    confirmation: cancellable ? { option: "--confirm-review-sha256", sha256: reviewSha256(goal, ledger, context) } : null,
    schedulingEffect: "none-until-confirmed", stopsWorker: false,
    authority: { ...authority }, boundary,
  };
}

function receipt(ledger, action, mode = null) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-cancel-cleaned", status: ledger.head.state,
    goalId: ledger.head.goalId, goalCheckpointSha256: ledger.headSha256, action,
    cancellationMode: mode, schedulingEffect: "terminal", stopsWorker: false,
    authority: { ...authority }, boundary,
  };
}

async function publish(config, goal, jobs, capabilityPolicy, dependencies) {
  await publishGoalOperatorStatus({
    stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy,
    ...(dependencies.operatorNow !== undefined ? { now: dependencies.operatorNow } : {}),
    ...(dependencies.secret !== undefined ? { secret: dependencies.secret } : {}),
    ...(dependencies.operatorSuffix !== undefined ? { suffix: dependencies.operatorSuffix } : {}),
  });
}

async function cancel(context, config, goal, jobs, dependencies) {
  const now = checkedDate(dependencies.now, "goal cancellation time");
  const suffix = stageSuffix(dependencies, "suffix");
  if (context.mode === "inactive-goal") return cancelReadyGoal({ stateRoot: config.stateRoot, goal, jobs, now, suffix });
  if (context.mode === "unlaunched-child-revocation") fail("unlaunched child cancellation requires the exact review path");
  return cancelGoalAfterCleanup({
    stateRoot: config.stateRoot, goal, jobs, childPlan: context.run.head.plan, childLease: context.run.head.lease, now, suffix,
  });
}

async function cancelUnlaunched(context, config, goal, jobs, ledger, confirmation, dependencies) {
  const requested = checkedDate(dependencies.now, "goal cancellation time");
  const parentSuffix = stageSuffix(dependencies, "suffix");
  let revocation = context.revocation;
  if (!revocation) {
    revocation = createLeaseRevocation({
      plan: context.run.head.plan, lease: context.run.head.lease, goalId: goal.goalId,
      goalCheckpointSha256: ledger.headSha256, reviewedChildCheckpointSha256: context.childCheckpointSha256,
      cancellationReviewSha256: confirmation,
    }, {
      now: afterDate(requested, ledger.head.createdAt, context.run.head.createdAt),
      suffix: stageSuffix(dependencies, "revocationSuffix"),
    });
    try {
      await revokeLease(config.stateRoot, revocation);
    } catch (error) {
      const winner = await inspectLeaseDisposition(config.stateRoot, context.run.head.lease);
      if (winner?.kind !== "revoked") throw error;
      revocation = winner.record;
      checkedRevocation(goal, ledger, context.run, revocation);
      if (revocation.goalCheckpointSha256 === ledger.headSha256 && revocation.cancellationReviewSha256 !== confirmation) throw error;
    }
  }

  let run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: context.run.head.plan.jobId });
  if (sha(run.head.plan) !== revocation.planSha256 || sha(run.head.lease) !== revocation.leaseSha256) {
    fail("goal cancellation child custody advanced after review");
  }
  if (run.head.purpose !== "admission") {
    try {
      await admitGoalRunBundle({
        stateRoot: config.stateRoot, goal, jobs, jobId: run.head.plan.jobId,
        now: afterDate(requested, run.head.createdAt, revocation.revokedAt),
        suffix: stageSuffix(dependencies, "admissionSuffix"),
      });
    } catch (error) {
      run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: run.head.plan.jobId });
      if (run.head.purpose !== "admission") throw error;
    }
    run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: run.head.plan.jobId });
  }

  const childRoot = join(resolve(config.stateRoot), "checkpoints", run.head.plan.jobId);
  if (!await realDirectory(childRoot, "goal cancellation child checkpoint custody")) {
    try {
      await initializeCheckpointLedger({
        stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease,
        workspaceSnapshotSha256: run.head.workspaceSnapshotSha256,
        now: afterDate(requested, run.head.createdAt, revocation.revokedAt),
        suffix: stageSuffix(dependencies, "childInitializeSuffix"),
      });
    } catch (error) {
      if (!await realDirectory(childRoot, "goal cancellation child checkpoint custody")) throw error;
    }
  }
  let child = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
  if (child.head.state === "authorized") {
    const update = {
      state: "cancelled", iteration: child.head.iteration, usage: { ...child.head.usage },
      progress: { ...child.head.progress, failureFingerprintSha256: sha(revocation) },
      workspaceSnapshotSha256: child.head.workspaceSnapshotSha256,
      artifactManifestSha256: child.head.artifactManifestSha256,
      workerSessionSha256: child.head.workerSessionSha256,
      verificationEvidenceSha256: child.head.verificationEvidenceSha256,
      authorityExpansionObserved: child.head.authorityExpansionObserved,
      acceptanceCriteriaMutationObserved: child.head.acceptanceCriteriaMutationObserved,
      externalEffectsObserved: child.head.externalEffectsObserved,
    };
    try {
      await appendCheckpoint({
        stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease,
        previousCheckpointSha256: child.headSha256, update,
        now: afterDate(requested, child.head.createdAt, revocation.revokedAt),
        suffix: stageSuffix(dependencies, "childCancelSuffix"),
      });
    } catch (error) {
      child = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
      if (child.head.state !== "cancelled" || child.head.progress.failureFingerprintSha256 !== sha(revocation)) throw error;
    }
    child = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
  }
  if (child.head.state !== "cancelled" || child.head.iteration !== 0 || child.head.progress.failureFingerprintSha256 !== sha(revocation)) {
    fail("goal cancellation lost the pre-launch lease race and requires supervised cleanup");
  }
  const parent = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  return cancelGoalAfterLeaseRevocation({
    stateRoot: config.stateRoot, goal, jobs, childPlan: run.head.plan, childLease: run.head.lease,
    leaseRevocation: revocation, now: afterDate(requested, parent.head.createdAt, child.head.createdAt), suffix: parentSuffix,
  });
}

export async function runGoalCancelCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal cancellation dependencies are invalid");
  const { operation, configPath, confirmation } = parseArguments(argv);
  const { config, goal, jobs, capabilityPolicy } = await loadGoalCycleConfiguration(configPath, { requireResearchRuntimeDirectory: false });
  let ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  let context = await cancellationContext(config, goal, jobs, ledger);
  if (operation === "review") return Object.freeze(reviewReceipt(ledger, goal, context));
  if (operation === "legacy-apply" && confirmation !== goalSha256(goal)) fail("goal cancellation confirmation differs from the immutable goal");
  if (ledger.head.state === "cancelled") {
    if (operation === "review-apply" && !await matchingCancellation(config, ledger, goal, jobs, confirmation)) fail("goal cancellation confirmation was not applied to this goal");
    await publish(config, goal, jobs, capabilityPolicy, dependencies);
    return receipt(ledger, "already-cancelled");
  }
  if (context.disposition !== "cancellable") fail(`goal cancellation is blocked: ${context.disposition}`);
  if (operation === "review-apply" && confirmation !== reviewSha256(goal, ledger, context)) {
    fail("goal cancellation confirmation differs from the current review");
  }
  let action = context.mode === "inactive-goal" ? "cancelled-inactive"
    : context.mode === "unlaunched-child-revocation" ? "cancelled-before-launch" : "cancelled-after-cleanup";
  try {
    if (context.mode === "unlaunched-child-revocation") {
      if (operation !== "review-apply") fail("unlaunched child cancellation requires checkpoint-bound review/apply");
      await cancelUnlaunched(context, config, goal, jobs, ledger, confirmation, dependencies);
    } else {
      await cancel(context, config, goal, jobs, dependencies);
    }
  } catch (error) {
    ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
    const won = operation === "legacy-apply" ? ledger.head.state === "cancelled" : await matchingCancellation(config, ledger, goal, jobs, confirmation);
    if (!won) throw error;
    action = "already-cancelled";
  }
  ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  if (ledger.head.state !== "cancelled") fail("goal cancellation was not durably recovered");
  await publish(config, goal, jobs, capabilityPolicy, dependencies);
  return receipt(ledger, action, context.mode);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalCancelCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-cancel: ${error instanceof GoalCancelCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalCancelBoundary = boundary;

import { lstat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { checkpointSha256, recoverCheckpointLedger } from "./checkpoints.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { createGoalProfileRouter } from "./goal-profile-router.mjs";
import { recoverGoalRunBundles } from "./goal-run-bundles.mjs";
import { recoverGoalLedger } from "./goals.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";

const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false });
const boundary = "Content-free supervised stop cleanup only. It may remove resources belonging to one exact consumed running Scout, Builder, Researcher, or Data Lab attempt and record that iteration failed; it cannot launch work, replay a claim, widen scope, grant completion, or cause external effects.";

export class GoalCleanupCliError extends Error {}

function fail(message) { throw new GoalCleanupCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) fail("Usage: goal-cleanup-cli.mjs --config FILE");
  return resolve(argv[1]);
}

async function realDirectory(path, label) {
  const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (info === null) return false;
  if (!info.isDirectory() || info.isSymbolicLink()) fail(`${label} is unsafe`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return true;
}

function receipt(goalLedger, action, childState = null, childCheckpointSha256 = null) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-stop-cleanup", status: goalLedger.head.state,
    goalId: goalLedger.head.goalId, goalCheckpointSha256: goalLedger.headSha256,
    action, childState, childCheckpointSha256,
    authority: { ...authority }, boundary,
  };
}

async function publishReceipt(config, goal, jobs, capabilityPolicy, value) {
  await publishGoalOperatorStatus({ stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy });
  return value;
}

export async function runGoalCleanupCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal cleanup dependencies are invalid");
  if (dependencies.cleanup !== undefined && typeof dependencies.cleanup !== "function") fail("goal cleanup callback is invalid");
  const configPath = parseArguments(argv);
  const { config, goal, jobs, policy, capabilityPolicy } = await loadGoalCycleConfiguration(configPath, { requireResearchRuntimeDirectory: false, requireCapabilityRuntime: false });
  let goalLedger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  const active = goalLedger.head.active;
  if (!active) return publishReceipt(config, goal, jobs, capabilityPolicy, receipt(goalLedger, "cleanup-not-required"));
  const job = jobs.find((value) => value.jobId === active.jobId);
  if (!job || !["scout", "builder", "researcher", "data-lab"].includes(job.profile)) fail("goal cleanup active child is invalid");
  const custodyPath = join(config.stateRoot, "goal-runs", goal.goalId, job.jobId);
  if (!await realDirectory(custodyPath, "goal cleanup custody")) return publishReceipt(config, goal, jobs, capabilityPolicy, receipt(goalLedger, "cleanup-not-required"));
  const run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: job.jobId });
  if (!run.childLedgerPresent) return publishReceipt(config, goal, jobs, capabilityPolicy, receipt(goalLedger, "cleanup-not-required"));
  const child = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
  if (child.action.action !== "fail-interrupted-worker-after-cleanup") {
    return publishReceipt(config, goal, jobs, capabilityPolicy, receipt(goalLedger, "cleanup-not-required", child.head.state, child.headSha256));
  }
  const router = createGoalProfileRouter({
    config, goal, jobs, policy, capabilityPolicy,
    ...(dependencies.cleanup ? {
      builderOverrides: { cleanup: dependencies.cleanup }, candidateOverrides: { cleanup: dependencies.cleanup },
    } : {}),
  });
  try {
    await router.driveChild({
      job, plan: run.head.plan, lease: run.head.lease, workspaceSnapshotSha256: run.head.workspaceSnapshotSha256,
      goalCheckpoint: goalLedger.head, childCheckpoint: child.head, childAction: child.action,
    });
  } catch (error) {
    await publishGoalOperatorStatus({ stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy }).catch(() => {});
    throw error;
  }
  const closed = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
  if (closed.head.state !== "failed" || closed.headSha256 === child.headSha256) fail("goal cleanup did not durably close the interrupted child");
  goalLedger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  return publishReceipt(config, goal, jobs, capabilityPolicy, receipt(goalLedger, "interrupted-child-cleaned", closed.head.state, checkpointSha256(closed.head)));
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalCleanupCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-cleanup: ${error instanceof GoalCleanupCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalCleanupBoundary = boundary;

import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { createGoalProfileRouter } from "./goal-profile-router.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";
import { runGoalCycle } from "./goal-runtime.mjs";

const MAX_CONTINUOUS_CYCLES = 256;
const MAX_ACTIVE_MILLISECONDS = 6 * 60 * 60 * 1000;
const stopActions = new Set([
  "terminal", "paused", "wait-for-child-authority", "child-authority-not-yet-valid",
  "child-authority-expired", "child-in-progress",
]);
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Content-free result of one bounded continuous reconciliation activation. Immediate continuation requires a newly durable checkpoint; terminal, paused, authority-waiting, no-progress, and safety states stop synchronously. Watchdog or path activation grants no execution, lease, replay, scope expansion, external effect, or completion authority.";

export class GoalContinuousCliError extends Error {}

function fail(message) { throw new GoalContinuousCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) fail("Usage: goal-continuous-cli.mjs --config FILE");
  return resolve(argv[1]);
}

function ceiling(value, maximum, label) {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) fail(`${label} is invalid`);
  return value;
}

function stopReason(result) {
  if (stopActions.has(result.action)) return result.action;
  if (result.durableProgress !== true) return "no-durable-progress";
  return null;
}

function receipt(result, goalId, cycles, progressCycles, reason) {
  const quiescent = ["terminal", "paused"].includes(reason);
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-goal-continuous",
    status: result.goalState,
    goalId,
    checkpointSha256: result.goalCheckpointSha256,
    sequence: result.sequence,
    progress: { milestonesCompleted: result.milestonesCompleted, milestonesTotal: result.milestonesTotal },
    reconciliations: cycles,
    durableProgressReconciliations: progressCycles,
    finalAction: result.action,
    stopReason: reason,
    childState: result.childState ?? null,
    schedulingEffect: quiescent ? "event-noop" : "event-or-watchdog-recovery",
    authority: { ...authority },
    boundary,
  });
}

export async function runGoalContinuousCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("continuous goal dependencies are invalid");
  const configPath = parseArguments(argv);
  const loadConfiguration = dependencies.loadConfiguration ?? loadGoalCycleConfiguration;
  const routerFactory = dependencies.routerFactory ?? createGoalProfileRouter;
  const cycleRunner = dependencies.cycleRunner ?? runGoalCycle;
  const statusPublisher = dependencies.statusPublisher ?? publishGoalOperatorStatus;
  const monotonicNow = dependencies.monotonicNow ?? (() => performance.now());
  for (const [label, callback] of Object.entries({ loadConfiguration, routerFactory, cycleRunner, statusPublisher, monotonicNow })) {
    if (typeof callback !== "function") fail(`continuous goal ${label} is invalid`);
  }
  const maxCycles = ceiling(dependencies.maxCycles ?? MAX_CONTINUOUS_CYCLES, MAX_CONTINUOUS_CYCLES, "continuous goal cycle ceiling");
  const maxActiveMilliseconds = ceiling(dependencies.maxActiveMilliseconds ?? MAX_ACTIVE_MILLISECONDS, MAX_ACTIVE_MILLISECONDS, "continuous goal active-time ceiling");
  const started = Number(monotonicNow());
  if (!Number.isFinite(started)) fail("continuous goal monotonic clock is invalid");
  const { config, goal, jobs, policy, capabilityPolicy = null } = await loadConfiguration(configPath);
  const router = routerFactory({
    config, goal, jobs, policy, capabilityPolicy,
    ...(dependencies.builderOverrides ? { builderOverrides: dependencies.builderOverrides } : {}),
    ...(dependencies.candidateOverrides ? { candidateOverrides: dependencies.candidateOverrides } : {}),
  });
  if (!router || typeof router.resolveChildRun !== "function" || typeof router.driveChild !== "function") fail("continuous goal router is invalid");

  let result = null;
  let progressCycles = 0;
  for (let cycle = 1; cycle <= maxCycles; cycle += 1) {
    result = await cycleRunner({
      stateRoot: config.stateRoot, goal, jobs,
      resolveChildRun: router.resolveChildRun, driveChild: router.driveChild,
      maxControllerTransitions: config.controller.maxTransitions,
      ...(dependencies.clock ? { clock: dependencies.clock } : {}),
      ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}),
    });
    if (!result || typeof result !== "object" || typeof result.action !== "string" || typeof result.durableProgress !== "boolean") fail("continuous goal cycle result is invalid");
    if (result.durableProgress) progressCycles += 1;
    await statusPublisher({
      stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy,
      ...(dependencies.operatorNow !== undefined ? { now: dependencies.operatorNow } : {}),
    });
    const reason = stopReason(result);
    if (reason !== null) return receipt(result, goal.goalId, cycle, progressCycles, reason);
    if (cycle === maxCycles) return receipt(result, goal.goalId, cycle, progressCycles, "cycle-ceiling");
    const observed = Number(monotonicNow());
    if (!Number.isFinite(observed) || observed < started) fail("continuous goal monotonic clock is invalid");
    if (observed - started >= maxActiveMilliseconds) return receipt(result, goal.goalId, cycle, progressCycles, "active-time-ceiling");
  }
  fail("continuous goal stopped without a result");
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalContinuousCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-continuous: ${error instanceof GoalContinuousCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalContinuousBoundary = boundary;

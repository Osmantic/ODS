import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";
import { pauseGoal, recoverGoalLedger } from "./goals.mjs";

const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsRetry: false, grantsResume: false,
  grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Content-free durable pause of future supervised goal steps. The pause grants no execution, lease, retry, resume, scope expansion, external effect, or completion authority. A bounded child step that won the start race before the pause record may finish and checkpoint; subsequent controller cycles remain no-ops.";

export class GoalPauseCliError extends Error {}

function fail(message) { throw new GoalPauseCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) {
    fail("Usage: goal-pause-cli.mjs --config FILE");
  }
  return resolve(argv[1]);
}

function checkedDate(value, label) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() < 0) fail(`${label} is invalid`);
  return date;
}

function receipt(ledger, action, hadActiveChild) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-pause", status: ledger.head.state, action,
    checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence,
    progress: {
      milestonesCompleted: ledger.head.progress.milestonesCompleted,
      milestonesTotal: ledger.head.progress.milestonesTotal,
      jobsStarted: ledger.head.progress.jobsStarted,
    },
    schedulingEffect: "future-controller-cycles-noop",
    currentBoundedStepMayFinish: hadActiveChild,
    authority: { ...authority }, boundary,
  };
}

export async function runGoalPauseCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal pause dependencies are invalid");
  const configPath = parseArguments(argv);
  const { config, goal, jobs, capabilityPolicy } = await loadGoalCycleConfiguration(configPath, { requireResearchRuntimeDirectory: false });
  let ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  const hadActiveChild = ledger.head.active !== null;
  let action = "already-paused";
  if (ledger.head.state !== "paused") {
    if (!["ready", "running", "waiting-authority"].includes(ledger.head.state)) fail("only a schedulable goal can be paused");
    const now = checkedDate(dependencies.now, "goal pause time");
    const suffix = dependencies.suffix ?? randomBytes(6).toString("hex");
    if (!/^[a-f0-9]{12}$/u.test(suffix)) fail("goal pause suffix is invalid");
    try {
      await pauseGoal({ stateRoot: config.stateRoot, goal, jobs, now, suffix });
      action = "paused";
    } catch (error) {
      ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
      if (ledger.head.state !== "paused") throw error;
      action = "already-paused";
    }
    ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  }
  await publishGoalOperatorStatus({
    stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy,
    ...(dependencies.operatorNow !== undefined ? { now: dependencies.operatorNow } : {}),
    ...(dependencies.secret !== undefined ? { secret: dependencies.secret } : {}),
    ...(dependencies.operatorSuffix !== undefined ? { suffix: dependencies.operatorSuffix } : {}),
  });
  if (ledger.head.state !== "paused") fail("goal pause was not durably recovered");
  return Object.freeze(receipt(ledger, action, hadActiveChild));
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalPauseCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-pause: ${error instanceof GoalPauseCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalPauseBoundary = boundary;

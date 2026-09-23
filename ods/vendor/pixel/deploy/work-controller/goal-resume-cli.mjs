import { createHash, randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";
import { decideGoalAction, goalCheckpointSha256, goalSha256, recoverGoalLedger, resumeGoal } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsRetry: false, grantsCancellation: false,
  grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Exact-review-confirmed scheduling resume only. It launches no work and grants no execution, lease, retry, cancellation, scope expansion, external effect, or completion authority. A later supervised controller cycle may continue only the same immutable goal and exact child custody.";

export class GoalResumeCliError extends Error {}

function fail(message) { throw new GoalResumeCliError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || ![3, 5].includes(argv.length) || !["review", "apply"].includes(argv[0]) || argv[1] !== "--config" || typeof argv[2] !== "string" || !argv[2]) {
    fail("Usage: goal-resume-cli.mjs <review|apply> --config FILE [--confirm-review-sha256 HASH]");
  }
  if (argv[0] === "review" && argv.length !== 3) fail("goal resume review does not accept a confirmation");
  if (argv[0] === "apply" && (argv.length !== 5 || argv[3] !== "--confirm-review-sha256" || !SHA_RE.test(argv[4] ?? ""))) {
    fail("goal resume apply requires the exact review SHA-256 confirmation");
  }
  return { operation: argv[0], configPath: resolve(argv[2]), confirmation: argv[4] ?? null };
}

function checkedDate(value, label) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() < 0) fail(`${label} is invalid`);
  return date;
}

function targetState(checkpoint) { return checkpoint.active === null ? "ready" : "running"; }

function reviewValue(goal, checkpoint) {
  if (checkpoint.state !== "paused") fail("only a paused goal can be reviewed for resume");
  return {
    schemaVersion: 1, operation: "pixel-work-goal-resume", goalSha256: goalSha256(goal),
    pausedCheckpointSha256: goalCheckpointSha256(checkpoint), from: "paused", to: targetState(checkpoint),
  };
}

function reviewSha256(goal, checkpoint) { return sha(reviewValue(goal, checkpoint)); }

function matchingResume(ledger, goal, confirmation) {
  for (let index = 1; index < ledger.checkpoints.length; index += 1) {
    const previous = ledger.checkpoints[index - 1], current = ledger.checkpoints[index];
    if (previous.state === "paused" && current.state === targetState(previous) && reviewSha256(goal, previous) === confirmation) return true;
  }
  return false;
}

function reviewReceipt(ledger, goal) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-resume-review", status: "paused", action: "confirmation-required",
    checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence,
    transition: { from: "paused", to: targetState(ledger.head), preservesExactActiveChild: ledger.head.active !== null },
    confirmation: { option: "--confirm-review-sha256", sha256: reviewSha256(goal, ledger.head) },
    schedulingEffect: "none-until-confirmed", startsWorkImmediately: false,
    authority: { ...authority }, boundary,
  };
}

function applyReceipt(ledger, goal, action) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-resume-apply", status: ledger.head.state, action,
    checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence,
    progress: {
      milestonesCompleted: ledger.head.progress.milestonesCompleted,
      milestonesTotal: ledger.head.progress.milestonesTotal,
      jobsStarted: ledger.head.progress.jobsStarted,
    },
    nextAction: decideGoalAction(ledger.head, goal).action,
    schedulingEffect: "future-controller-cycles-enabled", startsWorkImmediately: false,
    preservesExactActiveChild: ledger.head.active !== null,
    authority: { ...authority }, boundary,
  };
}

export async function runGoalResumeCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal resume dependencies are invalid");
  const { operation, configPath, confirmation } = parseArguments(argv);
  const { config, goal, jobs, capabilityPolicy } = await loadGoalCycleConfiguration(configPath, { requireResearchRuntimeDirectory: false });
  let ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  if (operation === "review") return Object.freeze(reviewReceipt(ledger, goal));

  let action = "resumed";
  if (ledger.head.state === "paused") {
    if (confirmation !== reviewSha256(goal, ledger.head)) fail("goal resume confirmation differs from the current paused review");
    const now = checkedDate(dependencies.now, "goal resume time");
    const suffix = dependencies.suffix ?? randomBytes(6).toString("hex");
    if (!/^[a-f0-9]{12}$/u.test(suffix)) fail("goal resume suffix is invalid");
    try {
      await resumeGoal({ stateRoot: config.stateRoot, goal, jobs, now, suffix });
    } catch (error) {
      ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
      if (!matchingResume(ledger, goal, confirmation)) throw error;
      action = "already-resumed";
    }
    ledger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  } else if (matchingResume(ledger, goal, confirmation)) {
    action = "already-resumed";
  } else {
    fail("only a currently paused goal can be resumed from this exact review");
  }
  if (ledger.head.state === "paused") fail("goal resume was superseded by a later pause");
  await publishGoalOperatorStatus({
    stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy,
    ...(dependencies.operatorNow !== undefined ? { now: dependencies.operatorNow } : {}),
    ...(dependencies.secret !== undefined ? { secret: dependencies.secret } : {}),
    ...(dependencies.operatorSuffix !== undefined ? { suffix: dependencies.operatorSuffix } : {}),
  });
  return Object.freeze(applyReceipt(ledger, goal, action));
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalResumeCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-resume: ${error instanceof GoalResumeCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalResumeBoundary = boundary;

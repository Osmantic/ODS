import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  cancelReadyGoal, completeGoal, dispatchGoalMilestone, goalCheckpointSha256, goalSha256,
  initializeGoalLedger, observeGoalMilestone, pauseGoal, recoverGoalLedger, resumeGoal,
} from "./goals.mjs";

const MAX_GOAL_BYTES = 512 * 1024;
const MAX_JOBS_BYTES = 8 * 1024 * 1024;
const MAX_PLAN_BYTES = 2 * 1024 * 1024;
const MAX_LEASE_BYTES = 2 * 1024 * 1024;
const commonOptions = new Set(["--state", "--goal", "--jobs"]);
const operations = new Set(["init", "status", "dispatch", "observe", "complete", "pause", "resume", "cancel"]);
const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsRetry: false, grantsScopeExpansion: false, grantsExternalEffects: false });
const boundary = "Content-free local goal-control receipt only. The command grants no child execution, lease, retry, scope expansion, external effect, or worker-selected completion authority.";

export class WorkGoalCliError extends Error {}

function fail(message) { throw new WorkGoalCliError(message); }

function parseArguments(argv) {
  const operation = argv[0];
  if (!operations.has(operation)) fail("Usage: goal-cli.mjs <init|status|dispatch|observe|complete|pause|resume|cancel> --state DIR --goal FILE --jobs FILE [--plan FILE --lease FILE] [--confirm-goal-sha256 HASH]");
  const allowed = operation === "observe" ? new Set([...commonOptions, "--plan", "--lease"])
    : ["resume", "cancel"].includes(operation) ? new Set([...commonOptions, "--confirm-goal-sha256"]) : commonOptions;
  const values = {};
  if ((argv.length - 1) % 2 !== 0) fail("goal-control arguments must be option/value pairs");
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(key) || !value || Object.hasOwn(values, key)) fail("goal-control arguments are invalid, unknown, or duplicated");
    values[key] = key === "--confirm-goal-sha256" ? value : resolve(value);
  }
  for (const key of commonOptions) if (!values[key]) fail(`goal-control is missing ${key}`);
  if (operation === "observe" && (!values["--plan"] || !values["--lease"])) fail("goal observe requires an exact child plan and lease");
  if (["resume", "cancel"].includes(operation) && !/^[a-f0-9]{64}$/u.test(values["--confirm-goal-sha256"] ?? "")) fail(`goal ${operation} requires the exact goal SHA-256 confirmation`);
  return { operation, values };
}

async function readPrivateJson(path, maximum, label) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

function receipt(operation, recovered, extras = {}) {
  const checkpoint = recovered.checkpoint ?? recovered.head;
  const action = recovered.action?.action ?? extras.action ?? null;
  return {
    schemaVersion: 1,
    operation: `pixel-work-goal-${operation}`,
    status: checkpoint.state,
    goalId: checkpoint.goalId,
    checkpointSha256: goalCheckpointSha256(checkpoint),
    sequence: checkpoint.sequence,
    progress: {
      milestonesTotal: checkpoint.progress.milestonesTotal,
      milestonesCompleted: checkpoint.progress.milestonesCompleted,
      jobsStarted: checkpoint.progress.jobsStarted,
      failures: checkpoint.progress.failures,
    },
    usage: structuredClone(checkpoint.usage),
    nextAction: action,
    schedulingEffect: ["pause", "resume", "cancel"].includes(operation) ? operation : "none",
    authority: { ...authority },
    boundary,
  };
}

export async function runGoalCommand(argv) {
  const { operation, values } = parseArguments(argv);
  const [goal, jobs] = await Promise.all([
    readPrivateJson(values["--goal"], MAX_GOAL_BYTES, "private work goal"),
    readPrivateJson(values["--jobs"], MAX_JOBS_BYTES, "private goal child jobs"),
  ]);
  const stateRoot = values["--state"];
  if (["resume", "cancel"].includes(operation) && values["--confirm-goal-sha256"] !== goalSha256(goal)) fail(`goal ${operation} confirmation differs from the immutable goal`);
  if (operation === "init") {
    const result = await initializeGoalLedger({ stateRoot, goal, jobs });
    return receipt(operation, result, { action: "dispatch-child" });
  }
  if (operation === "status") return receipt(operation, await recoverGoalLedger({ stateRoot, goal, jobs }));
  if (operation === "dispatch") {
    const result = await dispatchGoalMilestone({ stateRoot, goal, jobs });
    return receipt(operation, result, { action: "recover-or-start-child" });
  }
  if (operation === "pause") return receipt(operation, await pauseGoal({ stateRoot, goal, jobs }), { action: "paused" });
  if (operation === "resume") {
    const result = await resumeGoal({ stateRoot, goal, jobs });
    return receipt(operation, result, { action: result.checkpoint.active === null ? "dispatch-child" : "recover-or-continue-child" });
  }
  if (operation === "cancel") return receipt(operation, await cancelReadyGoal({ stateRoot, goal, jobs }), { action: "terminal" });
  if (operation === "observe") {
    const [childPlan, childLease] = await Promise.all([
      readPrivateJson(values["--plan"], MAX_PLAN_BYTES, "private child plan"),
      readPrivateJson(values["--lease"], MAX_LEASE_BYTES, "private child lease"),
    ]);
    const result = await observeGoalMilestone({ stateRoot, goal, jobs, childPlan, childLease });
    return receipt(operation, result, { action: result.action });
  }
  const result = await completeGoal({ stateRoot, goal, jobs });
  return receipt(operation, result, { action: "terminal" });
}

export async function main(argv = process.argv.slice(2)) {
  const value = await runGoalCommand(argv);
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal: ${error instanceof WorkGoalCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

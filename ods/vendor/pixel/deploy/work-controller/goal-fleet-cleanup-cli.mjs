import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { runGoalCleanupCommand } from "./goal-cleanup-cli.mjs";
import { loadGoalFleetCycleConfiguration } from "./goal-fleet-cycle-cli.mjs";
import { recoverGoalFleetLedger, settleGoalFleetTurn } from "./goal-fleet.mjs";
import { recoverGoalLedger } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const cleanupAuthority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false,
  grantsScopeExpansion: false, grantsExternalEffects: false,
});
const authority = Object.freeze({ ...cleanupAuthority, grantsCompletion: false });
const boundary = "Content-free fleet stop cleanup only. It reconciles the exact crash-held goal under the fleet process lock and releases that turn only after the ordinary goal cleanup boundary proves no interrupted worker remains.";

export class GoalFleetCleanupCliError extends Error {}

function fail(message) { throw new GoalFleetCleanupCliError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) fail("Usage: goal-fleet-cleanup-cli.mjs --config FILE");
  return resolve(argv[1]);
}

function checkedCleanupReceipt(value, goalId) {
  const keys = ["action", "authority", "boundary", "childCheckpointSha256", "childState", "goalCheckpointSha256", "goalId", "operation", "schemaVersion", "status"].sort();
  if (
    !value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys)
    || value.schemaVersion !== 1 || value.operation !== "pixel-work-goal-stop-cleanup" || value.goalId !== goalId
    || !SHA_RE.test(value.goalCheckpointSha256 ?? "") || canonical(value.authority) !== canonical(cleanupAuthority)
    || !["cleanup-not-required", "interrupted-child-cleaned"].includes(value.action) || typeof value.boundary !== "string"
    || value.childCheckpointSha256 !== null && !SHA_RE.test(value.childCheckpointSha256 ?? "")
  ) fail("goal cleanup returned an invalid or mismatched content-free receipt");
  return value;
}

function checkedSettlementTime(value, selectedAt) {
  const floor = Date.parse(selectedAt);
  const date = value === undefined ? new Date(Math.max(Date.now(), floor + 1)) : value instanceof Date ? value : new Date(value);
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() <= floor) fail("fleet cleanup settlement time is invalid or did not advance");
  return date;
}

function checkedSuffix(value) {
  if (value !== undefined && !/^[a-f0-9]{12}$/u.test(value)) fail("fleet cleanup settlement suffix is invalid");
  return value;
}

function receipt(action, bundle, ledger, settlement = null, cleanup = null, outcomeCode = null) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-stop-cleanup", action,
    fleetId: bundle.fleet.fleetId, goalId: ledger.head.active?.goalId ?? cleanup?.goalId ?? null,
    turnId: ledger.head.active?.turnId ?? ledger.head.lastOutcome?.turnId ?? null,
    cleanupAction: cleanup?.action ?? null, outcomeCode,
    checkpointSha256: settlement?.checkpointSha256 ?? ledger.headSha256, sequence: settlement?.sequence ?? ledger.head.sequence,
    authority: { ...authority }, boundary,
  });
}

export async function runGoalFleetCleanupCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("fleet cleanup dependencies are invalid");
  const bundle = await loadGoalFleetCycleConfiguration(parseArguments(argv), { ...(dependencies.loadOptions ?? {}), requireResearchRuntimeDirectory: false });
  const base = { stateRoot: bundle.config.stateRoot, fleet: bundle.fleet, registrations: bundle.registrations };
  const fleetLedger = await recoverGoalFleetLedger(base);
  if (fleetLedger.head.state === "ready") return receipt("cleanup-not-required", bundle, fleetLedger);
  const turn = fleetLedger.head.active;
  const entry = bundle.entries.find((candidate) => candidate.goal.goalId === turn.goalId);
  if (!entry) fail("crash-held fleet goal is not registered");
  const runner = dependencies.cleanupRunner ?? runGoalCleanupCommand;
  if (typeof runner !== "function") fail("fleet goal cleanup runner is invalid");
  const cleanup = checkedCleanupReceipt(await runner(["--config", entry.configPath], dependencies.goalCleanupDependencies ?? {}), entry.goal.goalId);
  const goalLedger = await recoverGoalLedger({ stateRoot: bundle.config.stateRoot, goal: entry.goal, jobs: entry.jobs });
  if (cleanup.goalCheckpointSha256 !== goalLedger.headSha256 || cleanup.status !== goalLedger.head.state) fail("goal cleanup receipt differs from the authoritative goal ledger");
  const outcomeCode = cleanup.action === "interrupted-child-cleaned" ? "cleanup-contained" : "cycle-failed-contained";
  const settlement = await settleGoalFleetTurn({
    ...base, turn, outcomeCode, resultSha256: sha(cleanup), now: checkedSettlementTime(dependencies.settlementNow, turn.selectedAt),
    ...(checkedSuffix(dependencies.settlementSuffix) ? { suffix: dependencies.settlementSuffix } : {}),
  });
  return receipt("cleanup-settled", bundle, fleetLedger, settlement, cleanup, outcomeCode);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalFleetCleanupCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-fleet-cleanup: ${error instanceof GoalFleetCleanupCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

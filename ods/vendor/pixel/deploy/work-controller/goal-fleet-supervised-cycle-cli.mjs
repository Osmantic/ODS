import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { runGoalFleetCycleCommand, GoalFleetCycleCliError } from "./goal-fleet-cycle-cli.mjs";
import { refreshGoalFleetHostEvidence, GoalFleetHostEvidenceCliError } from "./goal-fleet-host-evidence-cli.mjs";
import { GoalFleetHostEvidenceLedgerError } from "./goal-fleet-host-evidence-ledger.mjs";
import { WorkGoalFleetError } from "./goal-fleet.mjs";

const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});

export class GoalFleetSupervisedCycleCliError extends Error {}

function fail(message) { throw new GoalFleetSupervisedCycleCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) {
    fail("Usage: goal-fleet-supervised-cycle-cli.mjs --config FLEET_CONTROLLER");
  }
  return resolve(argv[1]);
}

export async function runGoalFleetSupervisedCycleCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("supervised fleet cycle dependencies are invalid");
  const configPath = parseArguments(argv);
  const evidence = await refreshGoalFleetHostEvidence(configPath, dependencies.hostEvidenceDependencies ?? {});
  const cycle = await runGoalFleetCycleCommand(["cycle", "--config", configPath], dependencies.goalFleetDependencies ?? {});
  if (cycle.fleetId !== evidence.fleetId) fail("refreshed host evidence and fleet cycle identities differ");
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-supervised-cycle", fleetId: cycle.fleetId,
    evidenceId: evidence.evidenceId, evidenceSha256: evidence.evidenceSha256, evidenceExpiresAt: evidence.expiresAt,
    cycleAction: cycle.action, goalId: cycle.goalId, turnId: cycle.turnId, outcomeCode: cycle.outcomeCode,
    checkpointSha256: cycle.checkpointSha256, sequence: cycle.sequence,
    authority: { ...authority },
    boundary: "Content-free result of one locked fleet service pass. Exact live host capacity is refreshed before one bounded reconciliation; neither step expands the immutable goal, lease, policy, or external-effect authority.",
  });
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalFleetSupervisedCycleCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalFleetSupervisedCycleCliError || error instanceof GoalFleetCycleCliError || error instanceof GoalFleetHostEvidenceCliError || error instanceof GoalFleetHostEvidenceLedgerError || error instanceof WorkGoalFleetError;
    process.stderr.write(`pixel-work-goal-fleet-supervised-cycle: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

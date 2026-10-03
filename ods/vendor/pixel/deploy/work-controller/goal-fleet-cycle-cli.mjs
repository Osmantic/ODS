import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical, validateWorkGoalFleetController, validateWorkGoalFleetHostProbe } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  claimGoalFleetTurn, initializeGoalFleetLedger, recoverGoalFleetLedger, settleGoalFleetTurn, validateGoalFleetHostEvidence, WorkGoalFleetError,
} from "./goal-fleet.mjs";
import { recoverGoalFleetHostEvidenceLedger, GoalFleetHostEvidenceLedgerError } from "./goal-fleet-host-evidence-ledger.mjs";
import { loadGoalCycleConfiguration, runGoalCycleCommand } from "./goal-cycle-cli.mjs";
import { recoverGoalLedger } from "./goals.mjs";

const MAX_CONFIG_BYTES = 256 * 1024;
const MAX_FLEET_BYTES = 2 * 1024 * 1024;
const MAX_HOST_PROBE_BYTES = 512 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const GOAL_RE = /^workgoal-[0-9]{13}-[a-f0-9]{12}$/u;
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});
const configBoundary = "Owner-private fleet wiring only. It binds one immutable fleet and exact live host probe to exact per-goal controller configurations and grants no execution, lease, replay, scope expansion, external effect, or completion authority.";
const receiptBoundary = "Content-free result of one serialized fleet reconciliation. The fleet turn only selects an exact goal controller; execution remains bounded by that goal's immutable jobs, leases, policy, and independent verification.";
const terminalStates = new Set(["completed", "failed", "cancelled", "budget-exhausted", "no-progress", "recovery-inconclusive"]);

export class GoalFleetCycleCliError extends Error {}

function fail(message) { throw new GoalFleetCycleCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 3 || !["init", "status", "cycle"].includes(argv[0]) || argv[1] !== "--config" || typeof argv[2] !== "string" || !argv[2]) {
    fail("Usage: goal-fleet-cycle-cli.mjs (init|status|cycle) --config FILE");
  }
  return { operation: argv[0], configPath: resolve(argv[2]) };
}

async function readJson(path, maximum, label, { ownerPrivate = true, expectedOwnerUid = process.geteuid?.() ?? 0 } = {}) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (
    details.nlink !== 1
    || ownerPrivate && process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)
  ) fail(`${label} is not ${ownerPrivate ? "owner-private and " : ""}single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

export async function loadGoalFleetCycleConfiguration(configPath, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("private fleet controller owner is invalid");
  const [config, schema] = await Promise.all([
    readJson(configPath, MAX_CONFIG_BYTES, "private fleet controller configuration", { expectedOwnerUid }),
    readJson(new URL("../../schemas/work-goal-fleet-controller-v2.schema.json", import.meta.url), MAX_CONFIG_BYTES, "installed fleet controller schema", { ownerPrivate: false }),
  ]);
  const errors = validateWorkGoalFleetController(config);
  if (errors.length || schema.$id !== config.$schema || config.boundary !== configBoundary) fail(`private fleet controller configuration is invalid: ${errors[0] ?? "installed schema binding differs"}`);
  for (const value of [config.stateRoot, config.fleetPath, config.hostProbePath, config.hostEvidenceDirectory, ...config.goals.map((goal) => goal.controllerConfigPath)]) {
    if (resolve(value) !== value) fail("private fleet controller paths must be absolute and normalized");
  }
  const [fleet, hostProbe, ...loadedGoals] = await Promise.all([
    readJson(config.fleetPath, MAX_FLEET_BYTES, "private immutable goal fleet", { expectedOwnerUid }),
    readJson(config.hostProbePath, MAX_HOST_PROBE_BYTES, "private fleet host probe", { expectedOwnerUid }),
    ...config.goals.map((goal) => loadGoalCycleConfiguration(goal.controllerConfigPath, {
      expectedOwnerUid, requireResearchRuntimeDirectory: options.requireResearchRuntimeDirectory ?? true,
    })),
  ]);
  const hostProbeErrors = validateWorkGoalFleetHostProbe(hostProbe);
  if (hostProbeErrors.length || sha(hostProbe) !== config.hostProbeSha256 || hostProbe.fleetPath !== config.fleetPath) fail(`private fleet host probe differs from its exact configuration binding: ${hostProbeErrors[0] ?? "digest or fleet path differs"}`);
  const requireCurrentHostEvidence = options.requireCurrentHostEvidence ?? true;
  if (typeof requireCurrentHostEvidence !== "boolean") fail("fleet host evidence currency option is invalid");
  const hostEvidenceLedger = await recoverGoalFleetHostEvidenceLedger({
    directory: config.hostEvidenceDirectory, genesisSha256: config.hostEvidenceGenesisSha256,
    fleet, probe: hostProbe, expectedOwnerUid, now: options.now ?? new Date(), requireCurrent: requireCurrentHostEvidence,
  });
  const hostEvidence = hostEvidenceLedger.head;
  if (requireCurrentHostEvidence) validateGoalFleetHostEvidence({ fleet, probe: hostProbe, evidence: hostEvidence, now: options.now ?? new Date() });
  if (fleet.goals?.length !== loadedGoals.length) fail("private fleet controller registration set is incomplete or widened");
  const entries = config.goals.map((source, index) => {
    const loaded = loadedGoals[index];
    if (loaded.goal.goalId !== source.goalId || loaded.config.stateRoot !== config.stateRoot) fail("private fleet controller goal identity or shared state root differs");
    return Object.freeze({ ...loaded, configPath: source.controllerConfigPath });
  });
  if (canonical(entries.map((entry) => entry.goal.goalId)) !== canonical(fleet.goals.map((goal) => goal.goalId))) fail("private fleet controller order differs from the immutable fleet");
  return Object.freeze({ config, fleet, hostProbe, hostEvidence, hostEvidenceLedger, entries, registrations: entries.map(({ goal, jobs, config: controller }) => ({ goal, jobs, config: controller })) });
}

function checkedDate(value, fallback, floor, label) {
  const candidate = value === undefined ? new Date(Math.max(fallback.getTime(), floor + 1)) : value;
  const date = candidate instanceof Date ? candidate : new Date(candidate);
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() <= floor) fail(`${label} is invalid or did not advance`);
  return date;
}

function checkedSuffix(value, label) {
  if (value !== undefined && !/^[a-f0-9]{12}$/u.test(value)) fail(`${label} is invalid`);
  return value;
}

function checkedCycleReceipt(value, entry) {
  const keys = ["action", "authority", "boundary", "checkpointSha256", "childState", "goalId", "operation", "progress", "schedulingEffect", "schemaVersion", "sequence", "status"].sort();
  if (
    !value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys)
    || value.schemaVersion !== 1 || value.operation !== "pixel-work-goal-cycle" || value.goalId !== entry.goal.goalId
    || !SHA_RE.test(value.checkpointSha256 ?? "") || !Number.isSafeInteger(value.sequence) || value.sequence < 0
    || !value.progress || !Number.isSafeInteger(value.progress.milestonesCompleted) || !Number.isSafeInteger(value.progress.milestonesTotal)
    || canonical(value.authority) !== canonical(authority) || typeof value.boundary !== "string"
  ) fail("goal cycle returned an invalid or mismatched content-free receipt");
  return value;
}

function outcomeFor(goalLedger, previousSha256) {
  if (goalLedger.action.action === "terminal" || terminalStates.has(goalLedger.head.state)) return "cycle-terminal";
  if (goalLedger.action.action === "paused" || goalLedger.headSha256 === previousSha256) return "cycle-noop";
  return "cycle-advanced";
}

function publicReceipt(action, fleet, claim = null, settlement = null, cycle = null, outcomeCode = null) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-cycle", action, fleetId: fleet.fleetId,
    goalId: claim?.turn?.goalId ?? null, turnId: claim?.turn?.turnId ?? null,
    cycleAction: cycle?.action ?? null, outcomeCode,
    checkpointSha256: settlement?.checkpointSha256 ?? claim?.checkpointSha256 ?? null,
    sequence: settlement?.sequence ?? claim?.sequence ?? null,
    authority: { ...authority }, boundary: receiptBoundary,
  });
}

function statusReceipt(bundle, fleetLedger, goalLedgers, now) {
  const observedAt = Date.parse(bundle.hostEvidence.observedAt), expiresAt = Date.parse(bundle.hostEvidence.expiresAt);
  const renewalAt = observedAt + Math.floor((expiresAt - observedAt) / 2), current = now.getTime();
  const evidenceState = current < observedAt ? "not-yet-valid" : current >= expiresAt ? "expired" : current >= renewalAt ? "renewal-due" : "current";
  const goals = { schedulable: 0, paused: 0, terminal: 0, failClosed: 0 };
  for (const ledger of goalLedgers) {
    if (ledger.action.action === "fail-closed") goals.failClosed += 1;
    else if (ledger.action.action === "paused") goals.paused += 1;
    else if (ledger.action.action === "terminal") goals.terminal += 1;
    else goals.schedulable += 1;
  }
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-status", action: "inspected", fleetId: bundle.fleet.fleetId,
    fleet: {
      state: fleetLedger.head.state, checkpointSequence: fleetLedger.head.sequence, checkpointSha256: fleetLedger.headSha256,
      turnsStarted: fleetLedger.head.turns.started, turnsSettled: fleetLedger.head.turns.settled,
      crashHeld: fleetLedger.head.state === "claimed", activeGoalId: fleetLedger.head.active?.goalId ?? null,
    },
    goals, hostEvidence: {
      state: evidenceState, sequence: bundle.hostEvidence.sequence, records: bundle.hostEvidenceLedger.records.length,
      observedAt: bundle.hostEvidence.observedAt, renewAt: new Date(renewalAt).toISOString(), expiresAt: bundle.hostEvidence.expiresAt,
    },
    authority: { ...authority },
    boundary: "Content-free read-only fleet orientation. Status validates immutable contracts and durable chains but grants no scheduling, execution, lease, cleanup, replay, scope, completion, or external-effect authority.",
  });
}

export async function runGoalFleetCycleCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("fleet cycle dependencies are invalid");
  const { operation, configPath } = parseArguments(argv);
  const statusNow = operation === "status" ? checkedDate(dependencies.statusNow, new Date(), -1, "fleet status time") : null;
  const loadOptions = operation === "status"
    ? { ...(dependencies.loadOptions ?? {}), now: statusNow, requireCurrentHostEvidence: false }
    : dependencies.loadOptions;
  const bundle = await loadGoalFleetCycleConfiguration(configPath, loadOptions);
  const base = { stateRoot: bundle.config.stateRoot, fleet: bundle.fleet, registrations: bundle.registrations };
  if (operation === "init") {
    const createdAt = checkedDate(dependencies.initializeNow, new Date(), Date.parse(bundle.fleet.createdAt) - 1, "fleet initialization time");
    const initialized = await initializeGoalFleetLedger({ ...base, now: createdAt, ...(checkedSuffix(dependencies.initializeSuffix, "fleet initialization suffix") ? { suffix: dependencies.initializeSuffix } : {}) });
    return publicReceipt("initialized", bundle.fleet, null, { checkpointSha256: initialized.sha256, sequence: initialized.checkpoint.sequence });
  }

  const fleetLedger = await recoverGoalFleetLedger(base);
  if (operation === "status") {
    const goalLedgers = await Promise.all(bundle.entries.map((entry) => recoverGoalLedger({ stateRoot: bundle.config.stateRoot, goal: entry.goal, jobs: entry.jobs })));
    return statusReceipt(bundle, fleetLedger, goalLedgers, statusNow);
  }
  const goalLedgers = new Map();
  const eligibleGoals = [];
  if (fleetLedger.head.state === "ready") {
    for (const entry of bundle.entries) {
      const goalLedger = await recoverGoalLedger({ stateRoot: bundle.config.stateRoot, goal: entry.goal, jobs: entry.jobs });
      goalLedgers.set(entry.goal.goalId, goalLedger);
      if (goalLedger.action.action === "fail-closed") fail("a registered goal has no safe controller action");
      if (!["terminal", "paused"].includes(goalLedger.action.action)) eligibleGoals.push({ goalId: entry.goal.goalId, goalCheckpointSha256: goalLedger.headSha256 });
    }
  }
  const claimNow = checkedDate(dependencies.claimNow, new Date(), Date.parse(fleetLedger.head.createdAt), "fleet claim time");
  const claim = await claimGoalFleetTurn({ ...base, eligibleGoals, now: claimNow, ...(checkedSuffix(dependencies.claimSuffix, "fleet claim suffix") ? { suffix: dependencies.claimSuffix } : {}) });
  if (claim.action === "idle") return publicReceipt("idle", bundle.fleet, claim);
  const entry = bundle.entries.find((candidate) => candidate.goal.goalId === claim.turn.goalId);
  if (!entry) fail("active fleet turn is not registered");
  let before = goalLedgers.get(entry.goal.goalId) ?? await recoverGoalLedger({ stateRoot: bundle.config.stateRoot, goal: entry.goal, jobs: entry.jobs });
  let cycle;
  let action = "cycled";
  if (before.headSha256 !== claim.turn.goalCheckpointSha256) {
    action = "reconciled-prior-turn";
    cycle = Object.freeze({
      schemaVersion: 1, operation: "pixel-work-goal-fleet-recovered-turn", goalId: entry.goal.goalId,
      checkpointSha256: before.headSha256, sequence: before.head.sequence, status: before.head.state,
      action: before.action.action, authority: { ...authority },
    });
  } else {
    const runner = dependencies.cycleRunner ?? runGoalCycleCommand;
    if (typeof runner !== "function") fail("fleet goal cycle runner is invalid");
    cycle = checkedCycleReceipt(await runner(["--config", entry.configPath], dependencies.goalCycleDependencies ?? {}), entry);
    before = await recoverGoalLedger({ stateRoot: bundle.config.stateRoot, goal: entry.goal, jobs: entry.jobs });
    if (before.headSha256 !== cycle.checkpointSha256 || before.head.sequence !== cycle.sequence || before.head.state !== cycle.status) fail("goal cycle receipt differs from the authoritative goal ledger");
  }
  const outcomeCode = outcomeFor(before, claim.turn.goalCheckpointSha256);
  const settlementNow = checkedDate(dependencies.settlementNow, new Date(), Date.parse(claim.turn.selectedAt), "fleet settlement time");
  const settlement = await settleGoalFleetTurn({
    ...base, turn: claim.turn, outcomeCode, resultSha256: sha(cycle), now: settlementNow,
    ...(checkedSuffix(dependencies.settlementSuffix, "fleet settlement suffix") ? { suffix: dependencies.settlementSuffix } : {}),
  });
  return publicReceipt(action, bundle.fleet, claim, settlement, cycle, outcomeCode);
}

export async function main(argv = process.argv.slice(2)) {
  const value = await runGoalFleetCycleCommand(argv);
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalFleetCycleCliError || error instanceof GoalFleetHostEvidenceLedgerError || error instanceof WorkGoalFleetError;
    process.stderr.write(`pixel-work-goal-fleet-cycle: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

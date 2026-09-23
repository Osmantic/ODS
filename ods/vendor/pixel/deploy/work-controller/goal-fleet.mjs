import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical, validateWorkGoal, validateWorkGoalController, validateWorkGoalFleet,
  validateWorkGoalFleetCheckpoint, validateWorkGoalFleetHostEvidence, validateWorkGoalFleetHostProbe, validateWorkJob,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const CHECKPOINT_RE = /^workfleetcheckpoint-[0-9]{13}-[a-f0-9]{12}$/u;
const TURN_RE = /^workturn-[0-9]{13}-[a-f0-9]{12}$/u;
const RECORD_RE = /^(0{0,6}[0-9]{1,7})\.json$/u;
const MAX_CHECKPOINT_BYTES = 64 * 1024;
const outcomes = new Set(["cycle-advanced", "cycle-noop", "cycle-terminal", "cleanup-contained", "cycle-failed-contained"]);
const resourceFields = Object.freeze({
  maxTurnSeconds: "maxRuntimeSeconds", maxCpuCores: "maxCpuCores",
  maxMemoryMiB: "maxMemoryMiB", maxDiskBytes: "maxDiskBytes",
});
const aggregateGoalBudgets = Object.freeze({
  maxRuntimeSeconds: "maxRuntimeSeconds", maxModelRequests: "maxModelRequests",
  maxInputTokens: "maxInputTokens", maxOutputTokens: "maxOutputTokens",
  maxNetworkBytes: "maxNetworkBytes", maxArtifactBytes: "maxArtifactBytes", maxFailures: "maxFailures",
});
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false,
  grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Private hash-chained host-admission record. One active turn blocks every other goal until exact recovery and settlement; the record grants no execution, lease, replay, scope expansion, external effect, or completion authority.";

export class WorkGoalFleetError extends Error {}

function fail(message) { throw new WorkGoalFleetError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
export function goalFleetSha256(value) { return sha(value); }
export function goalFleetCheckpointSha256(value) { return sha(value); }

function checkedDate(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime()) || value.getTime() < 0) fail(`${label} is invalid`);
  return value;
}

function checkedSuffix(value, label) {
  if (!SUFFIX_RE.test(value ?? "")) fail(`${label} is invalid`);
  return value;
}

function checkpointId(now, suffix) { return `workfleetcheckpoint-${String(now.getTime()).padStart(13, "0")}-${checkedSuffix(suffix, "fleet checkpoint suffix")}`; }
function turnId(now, suffix) { return `workturn-${String(now.getTime()).padStart(13, "0")}-${checkedSuffix(suffix, "fleet turn suffix")}`; }

function recordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000) fail("fleet checkpoint sequence is invalid");
  return `${String(sequence).padStart(7, "0")}.json`;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
}

function exactResourceEnvelope(goal, jobs) {
  const maxWorkerSeconds = Math.max(...jobs.map((job) => job.budgets.maxRuntimeSeconds));
  const maxVerifierSeconds = Math.max(...jobs.map((job) => job.verification?.maxRuntimeSeconds ?? 0));
  const maxCleanupSeconds = 300;
  return {
    maxTurnSeconds: maxWorkerSeconds + maxVerifierSeconds + maxCleanupSeconds,
    maxWorkerSeconds, maxVerifierSeconds, maxCleanupSeconds,
    maxRetainedArtifactBytes: goal.budgets.maxArtifactBytes,
    ...Object.fromEntries(Object.entries(resourceFields).filter(([field]) => field !== "maxTurnSeconds").map(([fleetField, jobField]) => [
      fleetField, Math.max(...jobs.map((job) => job.budgets[jobField])),
    ])),
  };
}

function checkedRegistrations(fleet, registrations, stateRoot) {
  const errors = validateWorkGoalFleet(fleet);
  if (errors.length) fail(`goal fleet contract is invalid: ${errors[0]}`);
  const root = resolve(stateRoot);
  if (!Array.isArray(registrations) || registrations.length !== fleet.goals.length) fail("goal fleet registration set is incomplete or widened");
  const byId = new Map();
  for (const registration of registrations) {
    if (!registration || typeof registration !== "object" || Array.isArray(registration)) fail("goal fleet registration is invalid");
    const { goal, jobs, config } = registration;
    const goalErrors = validateWorkGoal(goal);
    const configErrors = validateWorkGoalController(config);
    if (goalErrors.length || configErrors.length || !Array.isArray(jobs) || jobs.length !== goal?.milestones?.length) fail("goal fleet registration contracts are invalid");
    if (byId.has(goal.goalId)) fail("goal fleet registrations contain a duplicate goal");
    for (const value of [config.stateRoot, config.goalPath, config.jobsPath, config.policyPath, config.objectStore, config.workspaceRoot, config.executorPath, config.runtime.dockerPath]) {
      if (resolve(value) !== value) fail("goal fleet controller paths must be absolute and normalized");
    }
    if (resolve(config.stateRoot) !== root) fail("goal fleet registrations do not share the exact host state root");
    const jobsById = new Map();
    for (const job of jobs) {
      const jobErrors = validateWorkJob(job);
      if (jobErrors.length) fail(`goal fleet child job is invalid: ${jobErrors[0]}`);
      if (jobsById.has(job.jobId)) fail("goal fleet child jobs are duplicated");
      jobsById.set(job.jobId, job);
    }
    for (const milestone of goal.milestones) {
      const job = jobsById.get(milestone.jobId);
      if (!job || sha(job) !== milestone.jobSha256 || job.profile !== milestone.profile || job.dataClassification !== goal.dataClassification) fail("goal fleet milestone differs from its exact child job");
    }
    for (const [jobField, goalField] of Object.entries(aggregateGoalBudgets)) {
      const total = jobs.reduce((sum, job) => sum + job.budgets[jobField], 0);
      if (!Number.isSafeInteger(total) || total > goal.budgets[goalField]) fail(`goal fleet child jobs exceed aggregate ${goalField}`);
    }
    byId.set(goal.goalId, { goal, jobs, config });
  }
  for (const source of fleet.goals) {
    const registration = byId.get(source.goalId);
    if (!registration) fail("goal fleet source names an unregistered goal");
    if (
      source.goalSha256 !== sha(registration.goal) || source.jobsSha256 !== sha(registration.jobs)
      || source.controllerConfigSha256 !== sha(registration.config)
      || canonical(source.resourceEnvelope) !== canonical(exactResourceEnvelope(registration.goal, registration.jobs))
    ) fail("goal fleet registration differs from its exact goal, jobs, controller, or resource envelope");
    if (Date.parse(registration.goal.createdAt) > Date.parse(fleet.createdAt)) fail("goal fleet predates a registered immutable goal");
  }
  return { root, byId, fleetSha256: sha(fleet) };
}

export function validateGoalFleetRegistrationSet({ stateRoot, fleet, registrations }) {
  checkedRegistrations(fleet, registrations, stateRoot);
  return true;
}

export function validateGoalFleetHostEvidence({ fleet, probe, evidence, now = new Date() }) {
  const fleetErrors = validateWorkGoalFleet(fleet);
  const probeErrors = validateWorkGoalFleetHostProbe(probe);
  const evidenceErrors = validateWorkGoalFleetHostEvidence(evidence);
  if (fleetErrors.length || probeErrors.length || evidenceErrors.length) fail(`goal fleet host evidence is invalid: ${evidenceErrors[0] ?? probeErrors[0] ?? fleetErrors[0]}`);
  const observed = checkedDate(now, "goal fleet host evidence observation time").getTime();
  if (observed < Date.parse(evidence.observedAt) || observed >= Date.parse(evidence.expiresAt)) fail("goal fleet host evidence is not currently valid");
  if (evidence.fleetId !== fleet.fleetId || evidence.fleetSha256 !== sha(fleet)) fail("goal fleet host evidence differs from the immutable fleet");
  if (evidence.hostProbeSha256 !== sha(probe)) fail("goal fleet host evidence differs from the exact live probe");
  const expectedServices = probe.sharedServices.map(({ serviceId, unitName, maxCpuCores, maxMemoryMiB, maxDiskBytes }) => ({ serviceId, unitName, maxCpuCores, maxMemoryMiB, maxDiskBytes }));
  const actualServices = evidence.measurement.sharedServices.map(({ serviceId, unitName, maxCpuCores, maxMemoryMiB, maxDiskBytes }) => ({ serviceId, unitName, maxCpuCores, maxMemoryMiB, maxDiskBytes }));
  if (canonical(evidence.measurement.systemReserve) !== canonical(probe.systemReserve) || canonical(actualServices) !== canonical(expectedServices)) fail("goal fleet host evidence reserve set differs from the exact live probe");
  const admitted = evidence.measurement.admitted;
  if (fleet.hostCapacity.maxCpuCores > admitted.cpuCores || fleet.hostCapacity.maxMemoryMiB > admitted.memoryMiB) fail("goal fleet host CPU or memory capacity exceeds measured headroom");
  const concurrentAndRetainedDisk = fleet.hostCapacity.maxDiskBytes + fleet.hostCapacity.maxRetainedArtifactBytes;
  if (!Number.isSafeInteger(concurrentAndRetainedDisk) || concurrentAndRetainedDisk > admitted.disposableDiskBytes) fail("goal fleet disposable and retained disk ceilings exceed measured headroom");
  return true;
}

function validateCheckpoint(checkpoint, fleet, bindings, expected) {
  const errors = validateWorkGoalFleetCheckpoint(checkpoint);
  if (errors.length) fail(`goal fleet checkpoint contract is invalid: ${errors[0]}`);
  if (checkpoint.fleetId !== fleet.fleetId || checkpoint.fleetSha256 !== bindings.fleetSha256) fail("goal fleet checkpoint immutable binding differs from the fleet");
  if (checkpoint.nextIndex >= fleet.goals.length) fail("goal fleet cursor is outside the immutable registration set");
  if (checkpoint.active) {
    const registered = fleet.goals[checkpoint.active.goalIndex];
    if (!registered || canonical(checkpoint.active) !== canonical({
      turnId: checkpoint.active.turnId, goalIndex: checkpoint.active.goalIndex,
      goalId: registered.goalId, goalSha256: registered.goalSha256, jobsSha256: registered.jobsSha256,
      controllerConfigSha256: registered.controllerConfigSha256,
      goalCheckpointSha256: checkpoint.active.goalCheckpointSha256, resourceEnvelope: registered.resourceEnvelope,
      selectedAt: checkpoint.createdAt,
    })) fail("goal fleet active turn differs from its exact registration");
  }
  if (checkpoint.lastOutcome && !fleet.goals.some((goal) => goal.goalId === checkpoint.lastOutcome.goalId)) fail("goal fleet outcome names an unknown goal");
  if (expected && (
    checkpoint.sequence !== expected.sequence || checkpoint.previousCheckpointSha256 !== expected.previousCheckpointSha256
    || checkpoint.checkpointId !== expected.checkpointId
  )) fail("goal fleet checkpoint chain position is invalid");
}

function validateTransition(previous, next, fleet) {
  if (Date.parse(next.createdAt) <= Date.parse(previous.createdAt)) fail("goal fleet checkpoint time did not advance");
  if (previous.state === "ready" && next.state === "claimed") {
    if (
      !next.active || next.nextIndex !== previous.nextIndex || canonical(next.lastOutcome) !== canonical(previous.lastOutcome)
      || next.turns.started !== previous.turns.started + 1 || next.turns.settled !== previous.turns.settled
    ) fail("goal fleet claim changed cursor, outcome, or settled accounting");
    return;
  }
  if (previous.state === "claimed" && next.state === "ready") {
    const expectedIndex = (previous.active.goalIndex + 1) % fleet.goals.length;
    if (
      next.active !== null || next.nextIndex !== expectedIndex || next.turns.started !== previous.turns.started
      || next.turns.settled !== previous.turns.settled + 1 || !next.lastOutcome
      || next.lastOutcome.turnId !== previous.active.turnId || next.lastOutcome.goalId !== previous.active.goalId
      || next.lastOutcome.settledAt !== next.createdAt
    ) fail("goal fleet settlement did not close and rotate the exact active turn");
    return;
  }
  fail(`goal fleet checkpoint transition ${previous.state}->${next.state} is not allowed`);
}

async function writeRecord(fleetRoot, checkpoint) {
  const records = join(fleetRoot, "records");
  const temporaryRoot = join(fleetRoot, "tmp");
  await privateDirectory(records, "goal fleet checkpoint records");
  await privateDirectory(temporaryRoot, "goal fleet checkpoint temporary directory");
  const temporary = join(temporaryRoot, `.fleet-checkpoint-${checkpoint.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(records, recordName(checkpoint.sequence));
  const serialized = `${JSON.stringify(checkpoint, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_CHECKPOINT_BYTES) fail("goal fleet checkpoint exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(records, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("goal fleet checkpoint sequence was already appended");
    throw error;
  }
  return { checkpoint, sha256: sha(checkpoint), path: destination };
}

async function crashOrphanTwin(temporaryRoot, details) {
  // A committed record can reach nlink=2 only when an interrupted publish crashed between its
  // hardlink and the removal of its staging twin (writeRecord is the sole creator of tmp/ entries
  // in this owner-private ledger). A tmp/ staging file that shares this record's inode proves that
  // crash orphan and lets recovery read the durable record instead of wedging the ledger
  // permanently, while an alias anywhere else stays rejected as tampering. Read-only: it never
  // races a concurrent writer's own staging cleanup.
  if (!temporaryRoot) return false;
  for (const entry of await readdir(temporaryRoot).catch(() => [])) {
    const info = await lstat(join(temporaryRoot, entry)).catch(() => null);
    if (info?.isFile() && info.ino === details.ino && info.dev === details.dev) return true;
  }
  return false;
}

async function readRecord(path, sequence, temporaryRoot) {
  const { text, details } = await readBoundedRegularText(path, MAX_CHECKPOINT_BYTES, "goal fleet checkpoint");
  const ownerPrivate = process.platform === "win32" || (details.uid === process.geteuid() && (details.mode & 0o077) === 0);
  const singleLink = details.nlink === 1 || (details.nlink === 2 && await crashOrphanTwin(temporaryRoot, details));
  if (!ownerPrivate || !singleLink) fail("goal fleet checkpoint is not private and single-link");
  let value;
  try { value = JSON.parse(text); } catch { fail("goal fleet checkpoint is not JSON"); }
  if (value.sequence !== sequence) fail("goal fleet checkpoint filename and sequence differ");
  return value;
}

function baseCheckpoint(fleet, fleetHash, now, suffix) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-checkpoint-v1.schema.json", schemaVersion: 1,
    checkpointId: checkpointId(now, suffix), fleetId: fleet.fleetId, sequence: 0, createdAt: now.toISOString(),
    previousCheckpointSha256: null, fleetSha256: fleetHash, state: "ready", nextIndex: 0,
    active: null, lastOutcome: null, turns: { started: 0, settled: 0 }, authority: { ...authority }, boundary,
  };
}

export async function initializeGoalFleetLedger({ stateRoot, fleet, registrations, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const bindings = checkedRegistrations(fleet, registrations, stateRoot);
  const created = checkedDate(now, "goal fleet initialization time");
  if (created.getTime() < Date.parse(fleet.createdAt)) fail("goal fleet ledger predates the immutable fleet");
  await privateDirectory(bindings.root, "goal fleet state root");
  const fleetsRoot = join(bindings.root, "fleet-checkpoints");
  await mkdir(fleetsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(fleetsRoot, "goal fleet checkpoint root");
  const fleetRoot = join(fleetsRoot, fleet.fleetId);
  const checkpoint = baseCheckpoint(fleet, bindings.fleetSha256, created, suffix);
  validateCheckpoint(checkpoint, fleet, bindings, { sequence: 0, previousCheckpointSha256: null, checkpointId: checkpoint.checkpointId });
  const stagingRoot = join(fleetsRoot, `.init-${fleet.fleetId}-${randomBytes(8).toString("hex")}`);
  try {
    await privateDirectory(stagingRoot, "staged goal fleet ledger", true);
    await privateDirectory(join(stagingRoot, "records"), "goal fleet checkpoint records", true);
    await privateDirectory(join(stagingRoot, "tmp"), "goal fleet checkpoint temporary directory", true);
    const record = await writeRecord(stagingRoot, checkpoint);
    try { await rename(stagingRoot, fleetRoot); } catch (error) {
      if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) fail("goal fleet ledger was already initialized");
      throw error;
    }
    if (process.platform !== "win32") {
      const directory = await open(fleetsRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
    return { fleetRoot, checkpoint: record.checkpoint, sha256: record.sha256, path: join(fleetRoot, "records", recordName(0)) };
  } finally {
    await rm(stagingRoot, { recursive: true, force: true });
  }
}

export async function recoverGoalFleetLedger({ stateRoot, fleet, registrations }) {
  const bindings = checkedRegistrations(fleet, registrations, stateRoot);
  const fleetRoot = join(bindings.root, "fleet-checkpoints", fleet.fleetId);
  await privateDirectory(bindings.root, "goal fleet state root");
  await privateDirectory(fleetRoot, "goal fleet checkpoint ledger");
  const recordsRoot = join(fleetRoot, "records");
  await privateDirectory(recordsRoot, "goal fleet checkpoint records");
  await privateDirectory(join(fleetRoot, "tmp"), "goal fleet checkpoint temporary directory");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length < 1 || names.length > 1000001) fail("goal fleet checkpoint record count is invalid");
  const checkpoints = [];
  const checkpointIds = new Set();
  const turnIds = new Set();
  let previousHash = null;
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    if (names[sequence] !== recordName(sequence) || !RECORD_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("goal fleet checkpoint sequence is incomplete or unsafe");
    const checkpoint = await readRecord(join(recordsRoot, names[sequence]), sequence, join(fleetRoot, "tmp"));
    if (!CHECKPOINT_RE.test(checkpoint.checkpointId ?? "") || checkpointIds.has(checkpoint.checkpointId)) fail("goal fleet checkpoint identifier is invalid or reused");
    checkpointIds.add(checkpoint.checkpointId);
    if (checkpoint.active) {
      if (!TURN_RE.test(checkpoint.active.turnId) || turnIds.has(checkpoint.active.turnId)) fail("goal fleet turn identifier is invalid or reused");
      turnIds.add(checkpoint.active.turnId);
    }
    validateCheckpoint(checkpoint, fleet, bindings, { sequence, previousCheckpointSha256: previousHash, checkpointId: checkpoint.checkpointId });
    if (sequence > 0) validateTransition(checkpoints.at(-1), checkpoint, fleet);
    checkpoints.push(checkpoint);
    previousHash = sha(checkpoint);
  }
  return { fleetRoot, checkpoints, head: checkpoints.at(-1), headSha256: previousHash };
}

async function append({ stateRoot, fleet, registrations, previousCheckpointSha256, update, now, suffix }) {
  const recovered = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
  if (recovered.headSha256 !== previousCheckpointSha256) fail("goal fleet checkpoint append is based on a stale head");
  const created = checkedDate(now, "goal fleet checkpoint append time");
  const checkpoint = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-checkpoint-v1.schema.json", schemaVersion: 1,
    checkpointId: checkpointId(created, suffix), fleetId: fleet.fleetId, sequence: recovered.head.sequence + 1,
    createdAt: created.toISOString(), previousCheckpointSha256: recovered.headSha256,
    fleetSha256: recovered.head.fleetSha256, ...update, authority: { ...authority }, boundary,
  };
  const bindings = checkedRegistrations(fleet, registrations, stateRoot);
  validateCheckpoint(checkpoint, fleet, bindings, { sequence: checkpoint.sequence, previousCheckpointSha256: recovered.headSha256, checkpointId: checkpoint.checkpointId });
  validateTransition(recovered.head, checkpoint, fleet);
  return writeRecord(recovered.fleetRoot, checkpoint);
}

function activeReceipt(ledger, action) {
  return Object.freeze({
    action, fleetId: ledger.head.fleetId, checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence,
    turn: { ...ledger.head.active }, authority: { ...authority }, boundary,
  });
}

function checkedEligible(fleet, eligibleGoals) {
  if (!Array.isArray(eligibleGoals) || eligibleGoals.length > fleet.goals.length) fail("goal fleet eligible set is invalid");
  const goalIds = eligibleGoals.map((goal) => goal?.goalId);
  if (
    eligibleGoals.some((goal) => !goal || typeof goal !== "object" || Array.isArray(goal) || canonical(Object.keys(goal).sort()) !== canonical(["goalCheckpointSha256", "goalId"]))
    || eligibleGoals.some((goal) => !SHA_RE.test(goal.goalCheckpointSha256 ?? ""))
    || new Set(goalIds).size !== goalIds.length || canonical(goalIds) !== canonical([...goalIds].sort())
  ) fail("goal fleet eligible goals must be exact, uniquely sorted checkpoint bindings");
  const registered = new Set(fleet.goals.map((goal) => goal.goalId));
  if (goalIds.some((goalId) => !registered.has(goalId))) fail("goal fleet eligible set names an unregistered goal");
  return new Map(eligibleGoals.map((goal) => [goal.goalId, goal.goalCheckpointSha256]));
}

export async function claimGoalFleetTurn({ stateRoot, fleet, registrations, eligibleGoals, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const eligible = checkedEligible(fleet, eligibleGoals);
  let ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
  if (ledger.head.state === "claimed") return activeReceipt(ledger, "recover-active-turn");
  if (eligible.size === 0) return Object.freeze({ action: "idle", fleetId: fleet.fleetId, checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence, turn: null, authority: { ...authority }, boundary });
  let goalIndex = null;
  for (let offset = 0; offset < fleet.goals.length; offset += 1) {
    const candidate = (ledger.head.nextIndex + offset) % fleet.goals.length;
    if (eligible.has(fleet.goals[candidate].goalId)) { goalIndex = candidate; break; }
  }
  if (goalIndex === null) fail("goal fleet could not select an eligible registration");
  const selected = checkedDate(now, "goal fleet turn selection time");
  const registered = fleet.goals[goalIndex];
  const active = {
    turnId: turnId(selected, suffix), goalIndex, goalId: registered.goalId, goalSha256: registered.goalSha256,
    jobsSha256: registered.jobsSha256, controllerConfigSha256: registered.controllerConfigSha256,
    goalCheckpointSha256: eligible.get(registered.goalId),
    resourceEnvelope: { ...registered.resourceEnvelope }, selectedAt: selected.toISOString(),
  };
  try {
    await append({
      stateRoot, fleet, registrations, previousCheckpointSha256: ledger.headSha256, now: selected, suffix,
      update: { state: "claimed", nextIndex: ledger.head.nextIndex, active, lastOutcome: ledger.head.lastOutcome, turns: { started: ledger.head.turns.started + 1, settled: ledger.head.turns.settled } },
    });
  } catch (error) {
    ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
    if (ledger.head.state !== "claimed") throw error;
    return activeReceipt(ledger, "recover-active-turn");
  }
  ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
  return activeReceipt(ledger, "claimed");
}

function matchingOutcome(head, turn, outcomeCode, resultSha256) {
  return head.state === "ready" && head.lastOutcome?.turnId === turn.turnId && head.lastOutcome.goalId === turn.goalId
    && head.lastOutcome.code === outcomeCode && head.lastOutcome.resultSha256 === resultSha256;
}

export async function settleGoalFleetTurn({ stateRoot, fleet, registrations, turn, outcomeCode, resultSha256, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  if (!turn || !TURN_RE.test(turn.turnId ?? "") || !outcomes.has(outcomeCode) || !SHA_RE.test(resultSha256 ?? "")) fail("goal fleet settlement is invalid");
  let ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
  if (matchingOutcome(ledger.head, turn, outcomeCode, resultSha256)) return Object.freeze({ action: "already-settled", fleetId: fleet.fleetId, checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence, nextIndex: ledger.head.nextIndex, authority: { ...authority }, boundary });
  if (ledger.head.state !== "claimed" || canonical(ledger.head.active) !== canonical(turn)) fail("goal fleet settlement differs from the exact active turn");
  const settled = checkedDate(now, "goal fleet settlement time");
  const lastOutcome = { turnId: turn.turnId, goalId: turn.goalId, code: outcomeCode, resultSha256, settledAt: settled.toISOString() };
  try {
    await append({
      stateRoot, fleet, registrations, previousCheckpointSha256: ledger.headSha256, now: settled, suffix,
      update: { state: "ready", nextIndex: (turn.goalIndex + 1) % fleet.goals.length, active: null, lastOutcome, turns: { started: ledger.head.turns.started, settled: ledger.head.turns.settled + 1 } },
    });
  } catch (error) {
    ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
    if (!matchingOutcome(ledger.head, turn, outcomeCode, resultSha256)) throw error;
    return Object.freeze({ action: "already-settled", fleetId: fleet.fleetId, checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence, nextIndex: ledger.head.nextIndex, authority: { ...authority }, boundary });
  }
  ledger = await recoverGoalFleetLedger({ stateRoot, fleet, registrations });
  return Object.freeze({ action: "settled", fleetId: fleet.fleetId, checkpointSha256: ledger.headSha256, sequence: ledger.head.sequence, nextIndex: ledger.head.nextIndex, authority: { ...authority }, boundary });
}

export const goalFleetContract = Object.freeze({ boundary, authority, outcomes: Object.freeze([...outcomes]) });

import assert from "node:assert/strict";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { canonical, validateWorkGoalFleet, validateWorkGoalFleetCheckpoint, validateWorkGoalFleetHostEvidence } from "../scripts/lib/work-contract.mjs";
import {
  claimGoalFleetTurn, goalFleetCheckpointSha256, goalFleetSha256, initializeGoalFleetLedger,
  recoverGoalFleetLedger, settleGoalFleetTurn, validateGoalFleetHostEvidence,
} from "../deploy/work-controller/goal-fleet.mjs";
import { loadGoalFleetCycleConfiguration, runGoalFleetCycleCommand } from "../deploy/work-controller/goal-fleet-cycle-cli.mjs";
import { runGoalFleetCleanupCommand } from "../deploy/work-controller/goal-fleet-cleanup-cli.mjs";
import { renderGoalFleetServiceBundle } from "../deploy/work-controller/goal-fleet-service-cli.mjs";
import { inspectGoalFleetServiceBundle } from "../deploy/work-controller/goal-fleet-service-lifecycle.mjs";
import { runGoalFleetSupervisedCycleCommand } from "../deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs";
import { dispatchGoalMilestone, goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { createHash } from "node:crypto";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);
const hash = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const eligibleGoals = (ids) => ids.map((goalId, index) => ({ goalId, goalCheckpointSha256: hash(`goal-head-${index}`) }));

function job(index) {
  const marker = (0xabcde0000000n + BigInt(index)).toString(16);
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: `work-${1786366800000 + index}-${marker}`, createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder",
    objective: `Complete private fleet fixture ${index}.`, acceptanceCriteria: ["The exact fixture passes independent verification"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest(String(index + 1)), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60 + index, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: index + 1, maxMemoryMiB: 2048 + (512 * index), maxDiskBytes: 1048576 * (index + 1), maxArtifactBytes: 1048576, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    verification: { mode: "independent", checks: [{ id: "integrity", kind: "patch-integrity", criterionIndexes: [0] }], immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none", boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules." },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function goal(index, child) {
  const marker = (0xabcde0000000n + BigInt(index)).toString(16);
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${1786366800100 + index}-${marker}`, createdAt: "2026-08-10T13:00:00.100Z", requester: "pixel",
    objective: `Complete private fleet goal ${index}.`, dataClassification: "internal",
    milestones: [{ milestoneId: "build", jobId: child.jobId, jobSha256: goalSha256(child), profile: child.profile, dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: child.budgets.maxRuntimeSeconds, maxModelRequests: child.budgets.maxModelRequests, maxInputTokens: child.budgets.maxInputTokens, maxOutputTokens: child.budgets.maxOutputTokens, maxNetworkBytes: child.budgets.maxNetworkBytes, maxArtifactBytes: child.budgets.maxArtifactBytes, maxFailures: child.budgets.maxFailures },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

function config(root, index) {
  const privateRoot = join(root, `private-${index}`);
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot: join(root, "state"), goalPath: join(privateRoot, "goal.json"), jobsPath: join(privateRoot, "jobs.json"), policyPath: join(privateRoot, "policy.json"),
    objectStore: join(root, `objects-${index}`), workspaceRoot: join(root, `workspace-${index}`), executorPath: join(root, "executor"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 },
    runtime: { dockerPath: join(root, "docker"), backendNetworkName: `pixel-fleet-${index}`, backendContainerName: `pixel-model-${index}`, networkSubnet: `172.30.${index}.0/29`, workerIp: `172.30.${index}.2`, proxyIp: `172.30.${index}.3`, uid: 1000, gid: 1000 },
    controller: { maxTransitions: 1 },
  };
}

// The test fixture exercises ProtectHome, so its temp root must never live under a private
// root (/home, /root, /run/user). A host that sets TMPDIR under /home would otherwise trip
// the renderer's ProtectHome guard for an environment reason, so fall back to /tmp to keep
// the fleet-service gate reproducibly green regardless of TMPDIR.
const forbiddenPrivateRoots = ["/home", "/root", "/run/user"];
function fixtureBaseDir() {
  const candidate = tmpdir();
  if (forbiddenPrivateRoots.some((root) => candidate === root || candidate.startsWith(`${root}/`))) {
    return "/tmp";
  }
  return candidate;
}

async function fixture(t, label = "fleet") {
  const root = await mkdtemp(join(fixtureBaseDir(), `pixel-${label}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") { await chmod(root, 0o700); await chmod(stateRoot, 0o700); }
  const registrations = Array.from({ length: 3 }, (_, index) => {
    const child = job(index), parent = goal(index, child), controller = config(root, index);
    return { goal: parent, jobs: [child], config: controller };
  });
  const goals = registrations.map(({ goal: parent, jobs, config: controller }) => ({
    goalId: parent.goalId, goalSha256: hash(parent), jobsSha256: hash(jobs), controllerConfigSha256: hash(controller),
    resourceEnvelope: {
      maxTurnSeconds: jobs[0].budgets.maxRuntimeSeconds + jobs[0].verification.maxRuntimeSeconds + 300,
      maxWorkerSeconds: jobs[0].budgets.maxRuntimeSeconds, maxVerifierSeconds: jobs[0].verification.maxRuntimeSeconds, maxCleanupSeconds: 300,
      maxCpuCores: jobs[0].budgets.maxCpuCores,
      maxMemoryMiB: jobs[0].budgets.maxMemoryMiB, maxDiskBytes: jobs[0].budgets.maxDiskBytes,
      maxRetainedArtifactBytes: parent.budgets.maxArtifactBytes,
    },
  })).sort((left, right) => left.goalId.localeCompare(right.goalId));
  const fleet = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-v1.schema.json", schemaVersion: 1,
    fleetId: "workfleet-1786366801000-abcdef123456", createdAt: "2026-08-10T13:00:01.000Z",
    discipline: { mode: "durable-round-robin", maxConcurrentTurns: 1, crashPolicy: "recover-active-before-next" },
    hostCapacity: { maxTurnSeconds: 3600, maxCpuCores: 4, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, maxRetainedArtifactBytes: 16777216 },
    goals,
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable host-fleet schedule. It serializes bounded goal turns against exact resource envelopes but grants no job execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  registrations.sort((left, right) => left.goal.goalId.localeCompare(right.goal.goalId));
  return { root, stateRoot, fleet, registrations };
}

async function installedFixture(t, label) {
  const value = await fixture(t, label);
  const fleetPath = join(value.root, "fleet.json");
  await writeFile(fleetPath, `${JSON.stringify(value.fleet)}\n`, { mode: 0o600 });
  const goals = [];
  for (let index = 0; index < value.registrations.length; index += 1) {
    const registration = value.registrations[index];
    const privateRoot = join(value.root, `installed-${index}`);
    await mkdir(privateRoot, { mode: 0o700 });
    registration.config.goalPath = join(privateRoot, "goal.json");
    registration.config.jobsPath = join(privateRoot, "jobs.json");
    registration.config.policyPath = join(privateRoot, "policy.json");
    const source = value.fleet.goals.find((goal) => goal.goalId === registration.goal.goalId);
    source.controllerConfigSha256 = hash(registration.config);
    const controllerConfigPath = join(privateRoot, "controller.json");
    await Promise.all([
      writeFile(registration.config.goalPath, `${JSON.stringify(registration.goal)}\n`, { mode: 0o600 }),
      writeFile(registration.config.jobsPath, `${JSON.stringify(registration.jobs)}\n`, { mode: 0o600 }),
      writeFile(registration.config.policyPath, "{}\n", { mode: 0o600 }),
      writeFile(controllerConfigPath, `${JSON.stringify(registration.config)}\n`, { mode: 0o600 }),
    ]);
    goals.push({ goalId: registration.goal.goalId, controllerConfigPath });
    await initializeGoalLedger({ stateRoot: value.stateRoot, goal: registration.goal, jobs: registration.jobs, now: new Date(baseTime + 500), suffix: (0x710000000000n + BigInt(index)).toString(16) });
  }
  await writeFile(fleetPath, `${JSON.stringify(value.fleet)}\n`, { mode: 0o600 });
  goals.sort((left, right) => left.goalId.localeCompare(right.goalId));
  const hostProbe = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-probe-v1.schema.json", schemaVersion: 1,
    fleetPath, disposableDiskPath: value.root,
    systemReserve: { cpuCores: 2, memoryMiB: 4096, diskBytes: 1073741824 },
    sharedServices: [{ serviceId: "local-model", unitName: "pixel-model.service", maxCpuCores: 2, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, diskLimitEvidenceSha256: digest("c") }],
    evidenceLifetimeSeconds: 3600,
    boundary: "Owner-reviewed live host probe configuration. It names exact shared services and conservative reserves but grants no execution, lease, service mutation, scope expansion, external effect, or completion authority.",
  };
  const hostProbePath = join(value.root, "host-probe.json");
  await writeFile(hostProbePath, `${JSON.stringify(hostProbe)}\n`, { mode: 0o600 });
  const observedAt = new Date(Date.now() - 1000);
  const hostEvidence = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-evidence-v2.schema.json", schemaVersion: 2,
    sequence: 0, previousEvidenceSha256: null,
    evidenceId: `workhostevidence-${String(observedAt.getTime()).padStart(13, "0")}-abcdef123456`,
    fleetId: value.fleet.fleetId, fleetSha256: hash(value.fleet), hostProbeSha256: hash(hostProbe), observedAt: observedAt.toISOString(), expiresAt: new Date(observedAt.getTime() + 3600000).toISOString(),
    hostFingerprintSha256: hash("stable-private-host"),
    measurement: {
      source: "reviewed-linux-capacity-and-service-limits",
      physical: { cpuCores: 16, memoryMiB: 32768, disposableDiskBytes: 107374182400 },
      systemReserve: { cpuCores: 2, memoryMiB: 4096, diskBytes: 1073741824 },
      sharedServices: [{ serviceId: "local-model", unitName: "pixel-model.service", limitEvidenceSha256: digest("e"), maxCpuCores: 2, maxMemoryMiB: 4096, maxDiskBytes: 1073741824 }],
      admitted: { cpuCores: 12, memoryMiB: 24576, disposableDiskBytes: 105226698752 },
    },
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Short-lived hash-chained host-capacity evidence. It reserves operating-system and shared-service ceilings before fleet admission and grants no execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const hostEvidenceDirectory = join(value.stateRoot, "host-evidence");
  await mkdir(hostEvidenceDirectory, { mode: 0o700 });
  const hostEvidencePath = join(hostEvidenceDirectory, "0000000.json");
  await writeFile(hostEvidencePath, `${JSON.stringify(hostEvidence)}\n`, { mode: 0o600 });
  const fleetController = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-controller-v2.schema.json", schemaVersion: 2, enabled: true,
    stateRoot: value.stateRoot, fleetPath, hostProbePath, hostProbeSha256: hash(hostProbe),
    hostEvidenceDirectory, hostEvidenceGenesisSha256: hash(hostEvidence), goals,
    boundary: "Owner-private fleet wiring only. It binds one immutable fleet and exact live host probe to exact per-goal controller configurations and grants no execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const fleetControllerPath = join(value.root, "fleet-controller.json");
  await writeFile(fleetControllerPath, `${JSON.stringify(fleetController)}\n`, { mode: 0o600 });
  return { ...value, fleetPath, hostProbe, hostProbePath, hostEvidence, hostEvidenceDirectory, hostEvidencePath, fleetController, fleetControllerPath };
}

function cycleReceipt(goalLedger, goalId, action = "controller-yield") {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-cycle", status: goalLedger.head.state, goalId,
    checkpointSha256: goalLedger.headSha256, sequence: goalLedger.head.sequence,
    progress: { milestonesCompleted: goalLedger.head.progress.milestonesCompleted, milestonesTotal: goalLedger.head.progress.milestonesTotal },
    action, childState: null, schedulingEffect: "event-or-watchdog-recovery",
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Content-free supervised goal cycle receipt.",
  };
}

function cleanupReceipt(goalLedger, goalId, action = "cleanup-not-required") {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-stop-cleanup", status: goalLedger.head.state, goalId,
    goalCheckpointSha256: goalLedger.headSha256, action, childState: null, childCheckpointSha256: null,
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false },
    boundary: "Content-free supervised stop cleanup receipt.",
  };
}

test("fleet contract exactly binds every goal, controller, child set, and host resource envelope", async (t) => {
  const value = await fixture(t, "fleet-contract");
  assert.deepEqual(validateWorkGoalFleet(value.fleet), []);
  assert.match(goalFleetSha256(value.fleet), /^[a-f0-9]{64}$/u);
  const initialized = await initializeGoalFleetLedger({ ...value, now: new Date(baseTime + 1001), suffix: "000000000001" });
  assert.deepEqual(validateWorkGoalFleetCheckpoint(initialized.checkpoint), []);
  assert.deepEqual(Object.values(initialized.checkpoint.authority), [false, false, false, false, false, false]);
  assert.equal(initialized.checkpoint.state, "ready");
  const wrongIdentityTime = structuredClone(value.fleet);
  wrongIdentityTime.createdAt = "2026-08-10T13:00:01.001Z";
  assert.ok(validateWorkGoalFleet(wrongIdentityTime).some((error) => /identity time/u.test(error)));

  const understated = structuredClone(value.fleet);
  understated.goals[1].resourceEnvelope.maxMemoryMiB -= 1;
  await assert.rejects(initializeGoalFleetLedger({ ...value, fleet: understated, now: new Date(baseTime + 1002), suffix: "000000000002" }), /exact goal, jobs, controller, or resource envelope/);
  const overHost = structuredClone(value.fleet);
  overHost.hostCapacity.maxCpuCores = 1;
  assert.ok(validateWorkGoalFleet(overHost).some((error) => /host capacity/u.test(error)));
  const retentionOverHost = structuredClone(value.fleet);
  retentionOverHost.hostCapacity.maxRetainedArtifactBytes = 2 * 1048576;
  assert.ok(validateWorkGoalFleet(retentionOverHost).some((error) => /aggregate registered retention ceiling/u.test(error)));
  const moved = structuredClone(value.registrations);
  moved[0].config.stateRoot = join(value.root, "other-state");
  await assert.rejects(initializeGoalFleetLedger({ ...value, registrations: moved, now: new Date(baseTime + 1003), suffix: "000000000003" }), /share the exact host state root/);

  const widenedRegistrations = structuredClone(value.registrations);
  widenedRegistrations[0].jobs[0].budgets.maxRuntimeSeconds += 1;
  widenedRegistrations[0].goal.milestones[0].jobSha256 = hash(widenedRegistrations[0].jobs[0]);
  const widenedFleet = structuredClone(value.fleet);
  widenedFleet.goals[0].goalSha256 = hash(widenedRegistrations[0].goal);
  widenedFleet.goals[0].jobsSha256 = hash(widenedRegistrations[0].jobs);
  widenedFleet.goals[0].resourceEnvelope.maxWorkerSeconds += 1;
  widenedFleet.goals[0].resourceEnvelope.maxTurnSeconds += 1;
  await assert.rejects(initializeGoalFleetLedger({ ...value, fleet: widenedFleet, registrations: widenedRegistrations, now: new Date(baseTime + 1004), suffix: "000000000004" }), /exceed aggregate maxRuntimeSeconds/);
});

test("fleet initialization publishes one complete private ledger under contention", async (t) => {
  const value = await fixture(t, "fleet-init");
  const attempts = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => initializeGoalFleetLedger({
    ...value, now: new Date(baseTime + 1001), suffix: index.toString(16).padStart(12, "0"),
  })));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  assert.equal(attempts.filter((attempt) => attempt.status === "rejected").every((attempt) => /already initialized/.test(attempt.reason.message)), true);
  const recovered = await recoverGoalFleetLedger(value);
  assert.equal(recovered.checkpoints.length, 1);
  assert.deepEqual(await readdir(join(value.stateRoot, "fleet-checkpoints")), [value.fleet.fleetId]);
});

test("durable round robin gives every eligible goal one bounded turn before repeating", async (t) => {
  const value = await fixture(t, "fleet-fair");
  await initializeGoalFleetLedger({ ...value, now: new Date(baseTime + 1001), suffix: "100000000001" });
  const eligible = eligibleGoals(value.fleet.goals.map((goal) => goal.goalId).sort());
  const idle = await claimGoalFleetTurn({ ...value, eligibleGoals: [], now: new Date(baseTime + 1002), suffix: "110000000001" });
  assert.equal(idle.action, "idle");
  assert.equal((await recoverGoalFleetLedger(value)).checkpoints.length, 1);
  const selected = [];
  let tick = baseTime + 1003;
  for (let index = 0; index < 4; index += 1) {
    const claimed = await claimGoalFleetTurn({ ...value, eligibleGoals: eligible, now: new Date(tick++), suffix: (0x200000000000n + BigInt(index)).toString(16) });
    assert.equal(claimed.action, "claimed");
    selected.push(claimed.turn.goalId);
    const recovered = await claimGoalFleetTurn({ ...value, eligibleGoals: [eligible[(index + 1) % eligible.length]], now: new Date(tick++), suffix: (0x300000000000n + BigInt(index)).toString(16) });
    assert.equal(recovered.action, "recover-active-turn");
    assert.deepEqual(recovered.turn, claimed.turn);
    const settled = await settleGoalFleetTurn({ ...value, turn: claimed.turn, outcomeCode: "cycle-advanced", resultSha256: hash(`result-${index}`), now: new Date(tick++), suffix: (0x400000000000n + BigInt(index)).toString(16) });
    assert.equal(settled.action, "settled");
  }
  assert.deepEqual(selected, [eligible[0].goalId, eligible[1].goalId, eligible[2].goalId, eligible[0].goalId]);
  const ledger = await recoverGoalFleetLedger(value);
  assert.equal(ledger.head.state, "ready");
  assert.deepEqual(ledger.head.turns, { started: 4, settled: 4 });
  assert.equal(ledger.checkpoints.length, 9);
});

test("competing fleet controllers recover one crash-held turn and settle it once", async (t) => {
  const value = await fixture(t, "fleet-race");
  await initializeGoalFleetLedger({ ...value, now: new Date(baseTime + 1001), suffix: "500000000001" });
  const eligible = eligibleGoals(value.fleet.goals.map((goal) => goal.goalId).sort());
  const claims = await Promise.all(Array.from({ length: 24 }, (_, index) => claimGoalFleetTurn({
    ...value, eligibleGoals: eligible, now: new Date(baseTime + 1002), suffix: (0x510000000000n + BigInt(index)).toString(16),
  })));
  assert.equal(claims.filter((claim) => claim.action === "claimed").length, 1);
  assert.equal(new Set(claims.map((claim) => claim.turn.turnId)).size, 1);
  const turn = claims[0].turn;
  const crashRecovery = await claimGoalFleetTurn({ ...value, eligibleGoals: [eligible[2]], now: new Date(baseTime + 1003), suffix: "520000000000" });
  assert.equal(crashRecovery.action, "recover-active-turn");
  assert.deepEqual(crashRecovery.turn, turn);

  const substituted = structuredClone(turn);
  substituted.goalId = eligible[2].goalId;
  await assert.rejects(settleGoalFleetTurn({ ...value, turn: substituted, outcomeCode: "cleanup-contained", resultSha256: digest("b"), now: new Date(baseTime + 1004), suffix: "530000000000" }), /differs from the exact active turn/);
  const settlements = await Promise.all(Array.from({ length: 16 }, (_, index) => settleGoalFleetTurn({
    ...value, turn, outcomeCode: "cleanup-contained", resultSha256: digest("c"), now: new Date(baseTime + 1004), suffix: (0x540000000000n + BigInt(index)).toString(16),
  })));
  assert.equal(settlements.filter((settlement) => settlement.action === "settled").length, 1);
  assert.equal(settlements.filter((settlement) => settlement.action === "already-settled").length, 15);
  const ledger = await recoverGoalFleetLedger(value);
  assert.deepEqual(ledger.head.turns, { started: 1, settled: 1 });
  assert.equal(ledger.head.nextIndex, 1);
  assert.equal(ledger.head.lastOutcome.turnId, turn.turnId);
});

test("fleet recovery rejects checkpoint tampering, gaps, and hard links", async (t) => {
  const tampered = await fixture(t, "fleet-tampered");
  const initialized = await initializeGoalFleetLedger({ ...tampered, now: new Date(baseTime + 1001), suffix: "600000000001" });
  const changed = JSON.parse(await readFile(initialized.path, "utf8"));
  changed.nextIndex = 2;
  await writeFile(initialized.path, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
  await assert.rejects(recoverGoalFleetLedger(tampered), /initial fleet checkpoint|contract/u);

  // A committed record hardlinked into the ledger's own tmp/ is an interrupted-publish crash
  // orphan (writeRecord's link() completed but its unlink() did not); recovery reads the durable
  // record instead of wedging, whereas the external alias below stays rejected.
  const orphan = await fixture(t, "fleet-orphan");
  const orphanInitial = await initializeGoalFleetLedger({ ...orphan, now: new Date(baseTime + 1001), suffix: "600000000009" });
  await link(orphanInitial.path, join(orphanInitial.path, "..", "..", "tmp", ".fleet-checkpoint-0-orphan"));
  await assert.doesNotReject(recoverGoalFleetLedger(orphan));

  const linked = await fixture(t, "fleet-linked");
  const linkedInitial = await initializeGoalFleetLedger({ ...linked, now: new Date(baseTime + 1001), suffix: "600000000002" });
  await link(linkedInitial.path, join(linked.root, "checkpoint-copy"));
  await assert.rejects(recoverGoalFleetLedger(linked), /private and single-link/);

  const gap = await fixture(t, "fleet-gap");
  const gapInitial = await initializeGoalFleetLedger({ ...gap, now: new Date(baseTime + 1001), suffix: "600000000003" });
  await rename(gapInitial.path, join(gapInitial.fleetRoot, "records", "0000001.json"));
  await assert.rejects(recoverGoalFleetLedger(gap), /sequence is incomplete/);
  assert.match(goalFleetCheckpointSha256(linkedInitial.checkpoint), /^[a-f0-9]{64}$/u);
});

test("fleet cycle bridge admits one authoritative goal at a time and rotates fairly", async (t) => {
  const value = await installedFixture(t, "fleet-cycle");
  const initialized = await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "720000000001" });
  assert.equal(initialized.action, "initialized");
  const selected = [];
  for (let index = 0; index < 4; index += 1) {
    const result = await runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], {
      claimNow: new Date(baseTime + 1002 + index * 2), claimSuffix: (0x730000000000n + BigInt(index)).toString(16),
      settlementNow: new Date(baseTime + 1003 + index * 2), settlementSuffix: (0x740000000000n + BigInt(index)).toString(16),
      cycleRunner: async ([, controllerPath]) => {
        const goalId = value.fleetController.goals.find((goal) => goal.controllerConfigPath === controllerPath).goalId;
        const exact = value.registrations.find((candidate) => candidate.goal.goalId === goalId);
        const ledger = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: exact.goal, jobs: exact.jobs });
        selected.push(exact.goal.goalId);
        return cycleReceipt(ledger, exact.goal.goalId);
      },
    });
    assert.equal(result.action, "cycled");
    assert.equal(result.outcomeCode, "cycle-noop");
    assert.deepEqual(Object.values(result.authority), [false, false, false, false, false, false]);
  }
  const expected = value.fleet.goals.map((goal) => goal.goalId);
  assert.deepEqual(selected, [expected[0], expected[1], expected[2], expected[0]]);
  assert.deepEqual((await recoverGoalFleetLedger(value)).head.turns, { started: 4, settled: 4 });
});

test("fleet status is read-only and diagnoses due or expired capacity without hiding durable state", async (t) => {
  const value = await installedFixture(t, "fleet-status");
  await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "742000000001" });
  const observedAt = Date.parse(value.hostEvidence.observedAt), before = await recoverGoalFleetLedger(value);
  const current = await runGoalFleetCycleCommand(["status", "--config", value.fleetControllerPath], { statusNow: new Date(observedAt + 1000) });
  assert.equal(current.action, "inspected"); assert.equal(current.fleet.state, "ready"); assert.equal(current.fleet.crashHeld, false);
  assert.deepEqual(current.goals, { schedulable: 3, paused: 0, terminal: 0, failClosed: 0 });
  assert.equal(current.hostEvidence.state, "current"); assert.equal(current.hostEvidence.records, 1);
  assert.deepEqual(Object.values(current.authority), [false, false, false, false, false, false]);
  const expiredAt = new Date(value.hostEvidence.expiresAt);
  const expired = await runGoalFleetCycleCommand(["status", "--config", value.fleetControllerPath], { statusNow: expiredAt });
  assert.equal(expired.hostEvidence.state, "expired");
  await assert.rejects(runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], { loadOptions: { now: expiredAt } }), /not currently valid/);
  const after = await recoverGoalFleetLedger(value);
  assert.equal(after.headSha256, before.headSha256); assert.equal(after.checkpoints.length, before.checkpoints.length);

  const goalLedgers = await Promise.all(value.registrations.map((registration) => recoverGoalLedger({ stateRoot: value.stateRoot, goal: registration.goal, jobs: registration.jobs })));
  await claimGoalFleetTurn({
    ...value, eligibleGoals: goalLedgers.map((ledger) => ({ goalId: ledger.head.goalId, goalCheckpointSha256: ledger.headSha256 })).sort((left, right) => left.goalId.localeCompare(right.goalId)),
    now: new Date(baseTime + 1002), suffix: "742000000002",
  });
  const heldBefore = await recoverGoalFleetLedger(value);
  const held = await runGoalFleetCycleCommand(["status", "--config", value.fleetControllerPath], { statusNow: new Date(observedAt + 1001) });
  assert.equal(held.fleet.crashHeld, true); assert.equal(held.fleet.turnsStarted, 1); assert.equal(held.fleet.turnsSettled, 0);
  assert.equal((await recoverGoalFleetLedger(value)).headSha256, heldBefore.headSha256);
});

test("supervised fleet cadence renews chained evidence across virtual days without configuration drift", async (t) => {
  const value = await installedFixture(t, "fleet-multi-day");
  await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "745000000001" });
  const controllerBefore = await readFile(value.fleetControllerPath, "utf8");
  const selected = [];
  const observedStart = Date.parse(value.hostEvidence.observedAt);
  for (let index = 0; index < 8; index += 1) {
    const now = new Date(observedStart + (index + 1) * 86400000);
    const result = await runGoalFleetSupervisedCycleCommand(["--config", value.fleetControllerPath], {
      hostEvidenceDependencies: {
        platform: "linux", now, suffix: (0x746000000000n + BigInt(index)).toString(16),
        hostObserver: async () => ({ cpuCores: 16, memoryMiB: 32768, disposableDiskBytes: 107374182400, fingerprintSource: "stable-private-host" }),
        systemctlRunner: async () => ({ code: 0, stdout: "LoadState=loaded\nActiveState=active\nCPUQuotaPerSecUSec=2s\nMemoryMax=4294967296\n", stderr: "" }),
      },
      goalFleetDependencies: {
        loadOptions: { now }, claimNow: new Date(now.getTime() + 1), claimSuffix: (0x747000000000n + BigInt(index)).toString(16),
        settlementNow: new Date(now.getTime() + 2), settlementSuffix: (0x748000000000n + BigInt(index)).toString(16),
        cycleRunner: async ([, controllerPath]) => {
          const goalId = value.fleetController.goals.find((goal) => goal.controllerConfigPath === controllerPath).goalId;
          const exact = value.registrations.find((candidate) => candidate.goal.goalId === goalId);
          const ledger = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: exact.goal, jobs: exact.jobs });
          return cycleReceipt(ledger, goalId);
        },
      },
    });
    selected.push(result.goalId);
    assert.equal(result.cycleAction, "cycled");
  }
  const expected = value.fleet.goals.map((goal) => goal.goalId);
  assert.deepEqual(selected, [expected[0], expected[1], expected[2], expected[0], expected[1], expected[2], expected[0], expected[1]]);
  assert.equal(await readFile(value.fleetControllerPath, "utf8"), controllerBefore);
  assert.equal((await readdir(value.hostEvidenceDirectory)).length, 9);
  assert.deepEqual((await recoverGoalFleetLedger(value)).head.turns, { started: 8, settled: 8 });
});

test("fleet host evidence reserves system, shared-service, disposable, and retained capacity", async (t) => {
  const value = await installedFixture(t, "fleet-host-evidence");
  assert.deepEqual(validateWorkGoalFleetHostEvidence(value.hostEvidence), []);
  const observedAt = Date.parse(value.hostEvidence.observedAt);
  assert.equal(validateGoalFleetHostEvidence({ fleet: value.fleet, probe: value.hostProbe, evidence: value.hostEvidence, now: new Date(observedAt + 1000) }), true);
  const loaded = await loadGoalFleetCycleConfiguration(value.fleetControllerPath, { now: new Date(observedAt + 1000) });
  assert.equal(loaded.hostEvidence.hostFingerprintSha256, value.hostEvidence.hostFingerprintSha256);

  const understatedMemory = structuredClone(value.hostEvidence);
  understatedMemory.measurement.physical.memoryMiB = 10240;
  understatedMemory.measurement.admitted.memoryMiB = 2048;
  assert.deepEqual(validateWorkGoalFleetHostEvidence(understatedMemory), []);
  assert.throws(() => validateGoalFleetHostEvidence({ fleet: value.fleet, probe: value.hostProbe, evidence: understatedMemory, now: new Date(observedAt + 1000) }), /CPU or memory capacity exceeds/);
  const understatedDisk = structuredClone(value.hostEvidence);
  understatedDisk.measurement.physical.disposableDiskBytes = 3221225472;
  understatedDisk.measurement.admitted.disposableDiskBytes = 1073741824;
  assert.deepEqual(validateWorkGoalFleetHostEvidence(understatedDisk), []);
  assert.throws(() => validateGoalFleetHostEvidence({ fleet: value.fleet, probe: value.hostProbe, evidence: understatedDisk, now: new Date(observedAt + 1000) }), /disk ceilings exceed/);
  assert.throws(() => validateGoalFleetHostEvidence({ fleet: value.fleet, probe: value.hostProbe, evidence: value.hostEvidence, now: new Date(value.hostEvidence.expiresAt) }), /not currently valid/);

  const tampered = structuredClone(value.hostEvidence);
  tampered.hostFingerprintSha256 = digest("f");
  await writeFile(value.hostEvidencePath, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
  await assert.rejects(loadGoalFleetCycleConfiguration(value.fleetControllerPath, { now: new Date(observedAt + 1000) }), /genesis differs from its immutable controller binding/);
});

test("fleet cycle bridge settles an already-advanced crash-held turn without running it twice", async (t) => {
  const value = await installedFixture(t, "fleet-crash-window");
  await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "750000000001" });
  const goalLedgers = await Promise.all(value.registrations.map((registration) => recoverGoalLedger({ stateRoot: value.stateRoot, goal: registration.goal, jobs: registration.jobs })));
  const claim = await claimGoalFleetTurn({
    ...value, eligibleGoals: goalLedgers.map((ledger) => ({ goalId: ledger.head.goalId, goalCheckpointSha256: ledger.headSha256 })).sort((left, right) => left.goalId.localeCompare(right.goalId)),
    now: new Date(baseTime + 1002), suffix: "750000000002",
  });
  const selected = value.registrations.find((registration) => registration.goal.goalId === claim.turn.goalId);
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: selected.goal, jobs: selected.jobs, now: new Date(baseTime + 1003), suffix: "750000000003" });
  let calls = 0;
  const result = await runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], {
    claimNow: new Date(baseTime + 1004), settlementNow: new Date(baseTime + 1005), settlementSuffix: "750000000004",
    cycleRunner: async () => { calls += 1; throw new Error("must not run"); },
  });
  assert.equal(calls, 0);
  assert.equal(result.action, "reconciled-prior-turn");
  assert.equal(result.outcomeCode, "cycle-advanced");
  assert.equal((await recoverGoalFleetLedger(value)).head.state, "ready");
});

test("fleet cycle bridge leaves a failed turn crash-held and rejects substituted receipts", async (t) => {
  const value = await installedFixture(t, "fleet-failure");
  await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "760000000001" });
  await assert.rejects(runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], {
    claimNow: new Date(baseTime + 1002), claimSuffix: "760000000002",
    cycleRunner: async () => { throw new Error("simulated crash"); },
  }), /simulated crash/);
  const held = await recoverGoalFleetLedger(value);
  assert.equal(held.head.state, "claimed");
  await assert.rejects(runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], {
    claimNow: new Date(baseTime + 1003), settlementNow: new Date(baseTime + 1004), settlementSuffix: "760000000003",
    cycleRunner: async () => ({ schemaVersion: 1, operation: "pixel-work-goal-cycle", goalId: held.head.active.goalId, checkpointSha256: digest("f") }),
  }), /invalid or mismatched content-free receipt/);
  assert.equal((await recoverGoalFleetLedger(value)).head.state, "claimed");
});

test("fleet stop cleanup releases only the exact crash-held turn after ordinary cleanup", async (t) => {
  const value = await installedFixture(t, "fleet-cleanup");
  await runGoalFleetCycleCommand(["init", "--config", value.fleetControllerPath], { initializeNow: new Date(baseTime + 1001), initializeSuffix: "770000000001" });
  await assert.rejects(runGoalFleetCycleCommand(["cycle", "--config", value.fleetControllerPath], {
    claimNow: new Date(baseTime + 1002), claimSuffix: "770000000002", cycleRunner: async () => { throw new Error("simulated stop"); },
  }), /simulated stop/);
  const held = await recoverGoalFleetLedger(value);
  const active = value.registrations.find((registration) => registration.goal.goalId === held.head.active.goalId);
  let cleanedGoalId = null;
  const cleaned = await runGoalFleetCleanupCommand(["--config", value.fleetControllerPath], {
    settlementNow: new Date(baseTime + 1003), settlementSuffix: "770000000003",
    cleanupRunner: async ([, controllerPath]) => {
      cleanedGoalId = value.fleetController.goals.find((goal) => goal.controllerConfigPath === controllerPath).goalId;
      const ledger = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: active.goal, jobs: active.jobs });
      return cleanupReceipt(ledger, active.goal.goalId);
    },
  });
  assert.equal(cleanedGoalId, held.head.active.goalId);
  assert.equal(cleaned.action, "cleanup-settled");
  assert.equal(cleaned.outcomeCode, "cycle-failed-contained");
  assert.equal((await recoverGoalFleetLedger(value)).head.state, "ready");
  const noop = await runGoalFleetCleanupCommand(["--config", value.fleetControllerPath], { cleanupRunner: async () => { throw new Error("must not run"); } });
  assert.equal(noop.action, "cleanup-not-required");
});

test("fleet service render publishes one private hash-bound inspectable bundle", { skip: process.platform !== "linux" }, async (t) => {
  const value = await installedFixture(t, "fleet-service-render");
  const output = join(value.root, "rendered-fleet-service");
  const rendered = await renderGoalFleetServiceBundle([
    "render", "--config", value.fleetControllerPath, "--output", output, "--install-root", "/opt/pixel",
    "--node", "/usr/bin/node", "--user", "daemon", "--group", "daemon", "--docker-group", "daemon",
  ]);
  assert.equal(rendered.fleetId, value.fleet.fleetId);
  assert.equal(rendered.outputName, "rendered-fleet-service");
  assert.deepEqual(Object.values(rendered.authority), [false, false, false, false, false]);
  const inspected = await inspectGoalFleetServiceBundle(output, { expectedOwnerUid: process.geteuid() });
  assert.equal(inspected.manifestSha256, rendered.manifestSha256);
  assert.deepEqual(inspected.manifest.legacyUnitNames, value.fleet.goals.flatMap((goal) => [`pixel-work-${goal.goalId}.service`, `pixel-work-${goal.goalId}.timer`]));
});

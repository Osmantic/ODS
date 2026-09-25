import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  runCampaignRecoveryGuardian,
  watchCampaignRecoveryGuardian,
  campaignRecoveryGuardianStatuses,
  campaignRecoveryGuardianManualStatuses,
  campaignRecoveryGuardianBoundaries,
  probeProductionReadinessDefault,
} from "../deploy/work-controller/maintenance-campaign-recovery-guardian.mjs";
import {
  advanceCampaignRecoveryJournal,
  createCampaignRecoveryJournal,
  deriveCampaignJournalIdentity,
  deriveCampaignJournalIdentitySha256,
  deriveCampaignOutcome,
  deriveCampaignErrorOutcome,
  deriveCampaignRecoveryJournalPath,
  deriveCampaignRecoveryReceiptPath,
  inspectActiveCampaignRecoveryJournal,
  settleCampaignRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { loadCampaignRecoveryBinding, modelCampaignMaintenanceBoundaries } from "../deploy/work-controller/model-campaign-maintenance.mjs";
import { inspectMaintenanceCustodyLock } from "../deploy/work-controller/maintenance-custody.mjs";

const digest = (character) => character.repeat(64);
const byteHash = (value) => createHash("sha256").update(value).digest("hex");
const uid = process.geteuid?.() ?? 1000;
const linux = process.platform !== "win32";

async function privateWrite(path, value) {
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
  await writeFile(path, bytes, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
  return bytes;
}
async function privateDirectory(path) {
  await mkdir(path, { mode: 0o700 });
  if (linux) await chmod(path, 0o700);
}

const NONCE = "1".repeat(64);
const UNIT_NAME = `pixel-campaign-child-${NONCE}.service`;
const CONTROL_GROUP = `/user.slice/user-1000.slice/user@1000.service/app.slice/${UNIT_NAME}`;
const INVOCATION_ID = "0123456789abcdef0123456789abcdef";
const CHILD_PID = 4242;
function childIdentity(pid = CHILD_PID) {
  return {
    bootId: "01234567-89ab-cdef-0123-456789abcdef",
    pid,
    startTicks: 1000,
    exe: "/usr/bin/node",
    argv: ["/usr/bin/node", "maintenance-campaign-child-supervisor.mjs", "run", "--nonce", NONCE, "--contract", "/tmp/c", "--receipt", "/tmp/r"],
    cgroup: [`0::${CONTROL_GROUP}`],
    uid: String(uid),
  };
}

async function fixture(t, profile = "builder") {
  const root = await mkdtemp(join(tmpdir(), "pixel-m6-campaign-recovery-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const sourceRoot = join(root, "source");
  const scripts = join(sourceRoot, "scripts");
  const materializationRoot = join(root, "materialization");
  await privateDirectory(sourceRoot);
  await privateDirectory(scripts);
  await privateDirectory(materializationRoot);
  const scriptPath = join(scripts, "portal_outcome_battery_campaign.py");
  await writeFile(scriptPath, "#!/usr/bin/env python3\n", { mode: 0o700 });
  const pythonPath = join(root, "python3");
  const dockerPath = join(root, "docker");
  const custodyLockPath = join(root, "guardian.lock");
  await writeFile(pythonPath, "fixture\n", { mode: 0o700 });
  await writeFile(dockerPath, "fixture\n", { mode: 0o700 });
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (linux) await Promise.all([chmod(scriptPath, 0o700), chmod(pythonPath, 0o700), chmod(dockerPath, 0o700)]);
  const materialization = {
    schemaVersion: 1,
    operation: "pixel-portal-outcome-battery-materialization",
    profile,
    evaluationRegime: "matched-budget",
    modelContractSha256: digest("3"),
    inferenceContractSha256: digest("4"),
    tasks: [
      { batteryTaskId: "battery-one", partition: "tuning", taskSha256: digest("5") },
    ],
  };
  await privateWrite(join(materializationRoot, "materialization.json"), materialization);
  const preflightPath = join(root, "preflight.json");
  const pairPath = join(root, "pair.json");
  const pair = {
    $schema: "https://osmantic.com/pixel/schemas/portal-outcome-pair-system-v1.schema.json",
    schemaVersion: 1,
    candidateSourceArchiveSha256: digest("7"),
    pixelSystemConfigPath: join(root, "pixel-system.json"),
    codexRunnerImage: `sha256:${digest("8")}`,
    codexBoundaryImage: `sha256:${digest("9")}`,
    codexSurfaceQualificationPath: join(root, "codex-surface.json"),
    modelArtifactPath: join(root, "model-artifact"),
    modelArtifactManifestPath: join(root, "model-artifact-manifest.json"),
    launchArgumentsPath: join(root, "launch-arguments.json"),
    capabilityRetentionEvidencePath: join(root, "capability-retention.json"),
    preflightPath,
    boundary: modelCampaignMaintenanceBoundaries.pairConfiguration,
  };
  const pairBytes = await privateWrite(pairPath, pair);
  await privateWrite(preflightPath, {
    schemaVersion: 1,
    operation: "pixel-portal-outcome-pair-preflight",
    status: "ready",
    configurationSha256: byteHash(pairBytes),
    profile: materialization.profile,
    modelContractSha256: materialization.modelContractSha256,
    inferenceContractSha256: materialization.inferenceContractSha256,
  });
  const production = {
    containerName: "pixel-production-model",
    containerId: digest("1"),
    imageDigest: `sha256:${digest("2")}`,
    expectedStartedAt: "2026-08-13T12:00:00.123456789Z",
    expectedRestartCount: 0,
    stopTimeoutSeconds: 120,
    restoreTimeoutSeconds: 3600,
    probeIntervalMilliseconds: 1000,
    readinessOrigin: "http://127.0.0.1:8000",
    readinessModelId: "DeepSeek-V4-Flash-0731",
    maxResponseBytes: 1048576,
  };
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-campaign-maintenance-v1.schema.json",
    schemaVersion: 1,
    pythonPath,
    dockerPath,
    sourceRoot,
    sourceCommit: "8".repeat(40),
    candidateSourceArchiveSha256: pair.candidateSourceArchiveSha256,
    materializationRoot,
    pairConfigurationPath: pairPath,
    outputRoot: join(root, "campaign-output"),
    campaign: { maxPairs: 1, partition: "tuning", freezeTuning: false, runtimeCondition: "cold-first-request", timeoutSeconds: 3600 },
    custody: { kind: "advisory-flock", lockPath: custodyLockPath, acquireTimeoutSeconds: 30 },
    production,
    boundary: modelCampaignMaintenanceBoundaries.configuration,
  };
  const configPath = join(root, "maintenance.json");
  await privateWrite(configPath, configuration);
  const binding = await loadCampaignRecoveryBinding(configPath, { expectedOwnerUid: uid, inspectSource: async () => ({ commit: configuration.sourceCommit, clean: true }) });
  const custodyIdentity = (await inspectMaintenanceCustodyLock(configuration.custody, uid)).identitySha256;
  return { root, configPath, configuration, production, custodyLockPath, binding, custodyIdentity, pairPath };
}

function productionContainer(value, { running = true, startedAt = value.production.expectedStartedAt, restartCount = value.production.expectedRestartCount } = {}) {
  return {
    Id: value.production.containerId,
    Name: `/${value.production.containerName}`,
    Image: value.production.imageDigest,
    RestartCount: restartCount,
    State: { Running: running, Paused: false, Restarting: false, Dead: false, StartedAt: startedAt },
  };
}

function cleanInventory(runningIds = []) {
  return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: runningIds };
}

// Build the exact durable journal identity from the loaded binding and create a
// journal at the requested phase with the requested consequence fields.
async function buildJournal(fx, phase, fields = {}) {
  const b = fx.binding;
  const identity = {
    maintenanceOperationSha256: digest("a"),
    campaignOperationSha256: b.campaignOperationSha256,
    custodyIdentitySha256: b.custody.identitySha256,
    configurationSha256: b.configBytesSha,
    ownerUid: fx.binding.expectedOwnerUid,
    production: {
      containerName: fx.production.containerName,
      containerId: fx.production.containerId,
      imageDigest: fx.production.imageDigest,
      expectedStartedAt: fx.production.expectedStartedAt,
      expectedRestartCount: fx.production.expectedRestartCount,
    },
    comparison: {
      materializationSha256: b.bindings.materializationSha256,
      pairConfigurationSha256: b.bindings.pairConfigurationSha256,
      preflightSha256: b.bindings.preflightSha256,
      acceleratorStateSha256: digest("4"),
    },
  };
  const base = {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-journal",
    operation: "pixel-work-model-campaign-maintenance",
    campaignChildNonce: null,
    campaignChild: null,
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    readinessEvidence: null,
    campaignOutcome: null,
    ...identity,
  };
  const created = await createCampaignRecoveryJournal(fx.custodyLockPath, uid, base);
  const journalIdentity = deriveCampaignJournalIdentity(created.journal);
  // The guardian recovery path skips campaign-outcome-bound (only the
  // controller writes that phase with a durably bound outcome), so the helper
  // mirrors comparison-cleanup-pending -> isolation-clean directly.
  const ORDER = { prepared: 0, "production-stop-authorized": 1, "production-stopped": 2, "campaign-child-authorized": 3, "campaign-child-active": 4, "comparison-cleanup-pending": 5, "isolation-clean": 6, "production-start-authorized": 7 };
  const phaseOrder = ORDER[phase];
  let current = created.journal;
  for (let order = 1; order <= phaseOrder; order += 1) {
    const targetPhase = Object.entries(ORDER).find(([, o]) => o === order)[0];
    let targetFields = {};
    if (order === 3) targetFields = { campaignChildNonce: NONCE };
    if (order >= 4) targetFields = { campaignChildNonce: NONCE, campaignChild: childIdentity() };
    if (order === phaseOrder) targetFields = { ...targetFields, ...fields };
    const advanced = await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, targetPhase, journalIdentity, targetFields);
    current = advanced.journal;
  }
  return current;
}

function guardianDeps(fx, overrides = {}) {
  const state = {
    running: true,
    startedAt: fx.production.expectedStartedAt,
    restartCount: fx.production.expectedRestartCount,
    inventory: { prefixContainers: [], prefixNetworks: [], prefixVolumes: [] },
    leaseOk: true,
    leaseCalls: 0,
    stopCalls: 0,
    child: {
      alive: false,
      runningUnit: false,
      terminalUnit: false,
      notFound: false,
      emptyCgroup: true,
      clearedTerminal: false,
      identity: childIdentity(),
      invocation: INVOCATION_ID,
    },
  };
  const deps = {
    expectedOwnerUid: uid,
    expectedNodePath: process.execPath,
    guardianModuleSha256: digest("2"),
    expectedUnitSha256: digest("3"),
    async withCustody(_binding, _uid, operation) {
      return operation(fx.custodyIdentity);
    },
    async inspectSource() { return { commit: fx.configuration.sourceCommit, clean: true }; },
    async requireLease(_path, _uid, _configSha, _opts) {
      state.leaseCalls += 1;
      if (!state.leaseOk) throw new Error("campaign guardian lease is not ready or is stale");
      return { pid: process.pid, ready: true };
    },
    async inspectContainer(target) {
      return target === fx.production.containerId ? productionContainer(fx, { running: state.running, startedAt: state.startedAt, restartCount: state.restartCount }) : null;
    },
    async inventory() { return { ...state.inventory, runningGpuContainerIds: state.running ? [fx.production.containerId] : [] }; },
    async stopProduction() { state.stopCalls += 1; state.running = false; },
    async showUnit(unitName) {
      const c = state.child;
      if (c.notFound) return { ok: true, facts: { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "", ControlGroup: "" } };
      if (c.runningUnit) return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(c.identity.pid), InvocationID: c.invocation, ControlGroup: CONTROL_GROUP } };
      if (c.terminalUnit) return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: c.clearedTerminal ? "" : c.invocation, ControlGroup: c.clearedTerminal ? "" : CONTROL_GROUP } };
      return { ok: false, error: "unit unknown" };
    },
    async stopUnit() { return { ok: true }; },
    async proveCgroupEmpty() { return state.child.emptyCgroup; },
    isProcessAlive() { return state.child.alive; },
    async deriveProcessIdentity() { return state.child.identity; },
    async readInvocationId() { return state.child.invocation; },
  };
  return { deps: { ...deps, ...overrides }, state };
}

// ---------------------------------------------------------------------------

test("M6 exposes a closed content-free status vocabulary and content-free boundaries", () => {
  assert.ok(campaignRecoveryGuardianStatuses.includes("no-active-recovery"));
  assert.ok(campaignRecoveryGuardianStatuses.includes("campaign-recovery-isolation-clean"));
  for (const status of campaignRecoveryGuardianManualStatuses) assert.ok(campaignRecoveryGuardianStatuses.includes(status));
  assert.match(campaignRecoveryGuardianBoundaries.result, /never fabricates pass|receipt-first-then-unlink|derived mechanically from that outcome/u);
  assert.match(campaignRecoveryGuardianBoundaries.attention, /manual-attention|no ambiguous or substituted mutation/u);
});

test("M6 returns no-active-recovery with no effect when no campaign journal exists", async (t) => {
  const fx = await fixture(t);
  const { deps, state } = guardianDeps(fx);
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "no-active-recovery");
  assert.equal(result.manualAttentionRequired, false);
  assert.equal(result.journalPhase, null);
  assert.equal(state.stopCalls, 0);
  assert.equal(state.leaseCalls, 0);
});

test("M6 cancels an inert prepared journal only after mechanical proof and never starts production", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "prepared");
  const { deps, state } = guardianDeps(fx);
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-prepared-cancelled");
  assert.equal(result.manualAttentionRequired, false);
  assert.equal(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), null, "inert prepared journal must be cancelled");
  assert.equal(state.stopCalls, 0, "prepared cancellation must never stop production");
});

test("M6 refuses to cancel a prepared journal when production is not running exactly (identity mismatch)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "prepared");
  const { deps, state } = guardianDeps(fx);
  deps.inspectContainer = async () => productionContainer(fx, { running: false });
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-production-substituted");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "journal must be retained");
  assert.equal(state.stopCalls, 0);
});

test("M6 refuses to cancel a prepared journal when comparison isolation is not clean", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "prepared");
  const { deps, state } = guardianDeps(fx);
  state.inventory = { prefixContainers: ["pixel-outcome-leaked"], prefixNetworks: [], prefixVolumes: [] };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-isolation-not-clean");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "journal must be retained");
});

test("M6 stops production from production-stop-authorized, advances once to production-stopped, and returns progress", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-progress");
  assert.equal(result.progress, true);
  assert.equal(result.manualAttentionRequired, false);
  assert.equal(state.stopCalls, 1, "the exact pre-maintenance production must be stopped");
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(journal.phase, "production-stopped", "the journal advances monotonically to production-stopped, never fabricating a child");
});

test("M6 advances an already-stopped production-stop-authorized journal to production-stopped and returns progress", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-progress");
  assert.equal(state.stopCalls, 0, "already-stopped production must not be stopped again");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stopped");
});

test("M6 advances a production-stopped journal with no child ever authorized to isolation-clean as an M7 progress phase", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stopped");
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(result.progress, true, "isolation-clean is an M7 progress phase, not a terminal hold");
  assert.equal(result.holdForM8, false);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
  assert.equal(state.stopCalls, 0);
});

test("M6 advances campaign-child-authorized with an authoritative not-found unit to isolation-clean", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-authorized", { campaignChildNonce: NONCE });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  state.child.notFound = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(result.progress, true, "isolation-clean is an M7 progress phase, not a terminal hold");
  assert.equal(result.holdForM8, false);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M6 fails closed at campaign-child-authorized when a child unit exists without a recorded identity", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-authorized", { campaignChildNonce: NONCE });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  state.child.runningUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-authorized");
});

test("M6 advances campaign-child-active with a terminal child to isolation-clean without stopping", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.terminalUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(stopCalled, 0, "the observation-only last-moment reproof must never call stopUnit");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M6 stops only the exact nonce-derived child after recorded identity proof and reproves terminality", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.alive = false; state.child.runningUnit = false; state.child.terminalUnit = true; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(stopCalled, 1, "the exact child unit must be stopped once");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M6 stops the exact child from comparison-cleanup-pending and advances to isolation-clean", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "comparison-cleanup-pending", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.alive = false; state.child.runningUnit = false; state.child.terminalUnit = true; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(stopCalled, 1);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M6 fails closed on a substituted child MainPID and never stops", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async showUnit() { return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: "99999", InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } }; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active");
});

test("M6 fails closed on a substituted child InvocationID", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async readInvocationId() { return "ffffffffffffffffffffffffffffffff"; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0);
});

test("M6 fails closed on a child whose recorded cgroup does not match the unit control group", async (t) => {
  const fx = await fixture(t);
  const wrong = { ...childIdentity(), cgroup: ["0::/user.slice/other.service"] };
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: wrong });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async deriveProcessIdentity() { return wrong; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0);
});

test("M6 fails closed when a recorded child is still alive under a now-missing unit (missing metadata is never terminality)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.notFound = true;
  state.child.alive = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0);
});

test("M6 accepts the narrow not-found terminal case only when the recorded child is gone and the exact cgroup is empty", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  state.child.notFound = true;
  state.child.alive = false;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
});

test("M6 fails closed on live cgroup descendants (child not terminal)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  state.child.terminalUnit = true;
  state.child.emptyCgroup = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-terminal");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active");
});

// P1: a live recorded child with the same immutable incarnation (bootId, pid,
// startTicks) that moved out of its old unit cgroup is still the recorded child
// and must never be declared terminal. The changed cgroup makes the full
// identity differ and can leave the old cgroup empty, so M6 must fail closed on
// the immutable incarnation proof rather than persist isolation-clean.
test("M6 fails closed on a live same-incarnation child that moved out of its unit cgroup (never terminal, never stopped)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const moved = { ...childIdentity(), cgroup: ["0::/user.slice/somewhere-else.service"] };
  const { deps, state } = guardianDeps(fx, {
    async deriveProcessIdentity() { return moved; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.terminalUnit = true;
  state.child.alive = true;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0, "the live recorded child must never be stopped");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "the journal must be retained and never advanced");
});

test("M6 fails closed on a live same-incarnation child whose unit is not-found", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const moved = { ...childIdentity(), cgroup: ["0::/user.slice/somewhere-else.service"] };
  const { deps, state } = guardianDeps(fx, {
    async deriveProcessIdentity() { return moved; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.notFound = true;
  state.child.alive = true;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0, "the live recorded child must never be stopped");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "the journal must be retained and never advanced");
});

test("M6 rejects malformed terminal MainPID instead of collapsing it to zero", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async showUnit() { return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "not-a-pid", InvocationID: "", ControlGroup: "" } }; },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.alive = false;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-terminal");
  assert.equal(stopCalled, 0, "malformed terminal metadata must never authorize a stop");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "malformed terminal metadata must never advance the journal");
});

// Real systemd clears InvocationID and ControlGroup on a persistent loaded
// oneshot after terminal exit. A post-stop loaded/inactive/dead unit with
// cleared metadata is a legitimate terminal shape and succeeds only when the
// recorded process incarnation is gone and the exact recorded cgroup is empty.
test("M6 post-stop loaded/inactive/dead unit with cleared InvocationID/ControlGroup succeeds when the recorded process is gone and recorded cgroup is empty", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.alive = false; state.child.runningUnit = false; state.child.terminalUnit = true; state.child.clearedTerminal = true; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean");
  assert.equal(stopCalled, 1, "the exact child unit must be stopped once");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M6 fails closed on a post-stop cleared terminal unit when the recorded child is still alive under the same incarnation", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.runningUnit = false; state.child.terminalUnit = true; state.child.clearedTerminal = true; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  state.child.emptyCgroup = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 1, "the exact child unit must be stopped once");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "the journal must be retained and never advanced");
});

test("M6 fails closed on a post-stop terminal unit when the recorded cgroup is nonempty", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.alive = false; state.child.runningUnit = false; state.child.terminalUnit = true; state.child.clearedTerminal = true; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  state.child.emptyCgroup = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-terminal");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "the journal must be retained and never advanced");
});

test("M6 fails closed on foreign isolation resources at comparison-cleanup-pending", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "comparison-cleanup-pending", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  state.child.terminalUnit = true;
  state.inventory = { prefixContainers: [], prefixNetworks: [], prefixVolumes: ["pixel-work-foreign"] };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-isolation-not-clean");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "comparison-cleanup-pending");
});

test("M6 fails closed and takes no effect when the live campaign guardian lease is absent or stale", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  state.leaseOk = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.equal(state.stopCalls, 0, "no production stop without a current exact lease");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stop-authorized");
});

test("M6 fails closed on a journal whose configuration identity does not match the current config", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const b = fx.binding;
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  const substituted = {
    ...journal,
    configurationSha256: digest("f"),
  };
  await writeFile(join(fx.root, "journal-rewrite.json"), "ignored");
  const path = deriveCampaignRecoveryJournalPath(fx.custodyLockPath);
  await writeFile(path, `${JSON.stringify(substituted, null, 2)}\n`, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
  const { deps, state } = guardianDeps(fx);
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-config-changed");
  assert.equal(state.stopCalls, 0);
});

test("M6 fails closed on a journal whose campaign operation identity is substituted", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  const substituted = { ...journal, campaignOperationSha256: digest("f") };
  const path = deriveCampaignRecoveryJournalPath(fx.custodyLockPath);
  await writeFile(path, `${JSON.stringify(substituted, null, 2)}\n`, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
  const { deps, state } = guardianDeps(fx);
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-identity-changed");
  assert.equal(state.stopCalls, 0);
});

test("M7 authorizes a production start from isolation-clean (nonce + endpoint only, never starts)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const ENDPOINT_ID = digest("f");
  let startCalls = 0;
  const { deps, state } = guardianDeps(fx, {
    secureSocketIdentity: async () => ENDPOINT_ID,
    async startProduction() { startCalls += 1; },
  });
  state.running = false;
  state.child.terminalUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-production-start-authorized");
  assert.equal(result.progress, true);
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(journal.phase, "production-start-authorized");
  assert.match(journal.guardianStartNonce, /^[a-f0-9]{64}$/u);
  assert.notEqual(journal.guardianStartNonce, "0".repeat(64));
  assert.equal(journal.guardianStartEndpointIdentity, ENDPOINT_ID);
  assert.equal(journal.guardianStartReceipt, null, "no start may occur in the authorization pass");
  assert.equal(startCalls, 0, "the authorization pass must never start production");
  assert.equal(state.stopCalls, 0);
});

test("M7 replay at production-start-authorized with production already running and no attributable receipt is manual attention, never success or retry", async (t) => {
  const fx = await fixture(t);
  const startFields = { campaignChildNonce: NONCE, campaignChild: childIdentity(), guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("d") };
  await buildJournal(fx, "production-start-authorized", startFields);
  let startCalls = 0;
  const { deps, state } = guardianDeps(fx, {
    async startProduction() { startCalls += 1; },
  });
  // Production is already running (externally started) with no exact attributable receipt.
  state.running = true;
  state.child.terminalUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.manualAttentionRequired, true);
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(journal.phase, "production-start-authorized", "an already-running production with no receipt must not advance and must not retry");
  assert.equal(startCalls, 0, "M7 must never silently re-start an already-running production");
});

test("M6 one bundle from production-stop-authorized performs no start/readiness/settlement and M7 grants only minimal start/readiness authority", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  let startProduction = 0;
  let readiness = 0;
  let settle = 0;
  const { deps, state } = guardianDeps(fx, {
    async startProduction() { startProduction += 1; },
    async waitForReadiness() { readiness += 1; return true; },
    async settleJournal() { settle += 1; },
  });
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-progress");
  assert.equal(startProduction, 0, "M6 must never start production");
  assert.equal(readiness, 0, "M6 must never probe readiness");
  assert.equal(settle, 0, "M6 must never settle an active journal");
  // This M6 stop result performed no start/readiness operation, so it must not
  // grant a per-result start/readiness authority grant; M7's standing capability
  // is represented separately and truthfully.
  assert.equal(result.authority.grantsProductionStart, false, "a stop result grants no per-result production-start authority");
  assert.equal(result.authority.grantsReadiness, false, "a stop result grants no per-result readiness authority");
  assert.equal(result.authority.grantsSettlement, false);
  assert.equal(result.authority.grantsCredentials, false);
  assert.equal(result.authority.grantsExternalNetwork, false);
  assert.equal(result.authority.grantsExternalEffects, false);
  assert.equal(result.authority.grantsDeployment, false);
  assert.equal(result.authority.grantsCompletion, false);
  assert.equal(result.capability.canRestartBoundProduction, true, "M7 capability to restart the bound production is preserved separately");
  assert.equal(result.capability.canProbeBoundReadiness, true, "M7 capability to probe the bound readiness is preserved separately");
});

test("M6 recovery is idempotent across repeated crash replays (no ambiguous replayed effect)", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "comparison-cleanup-pending", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  const ENDPOINT_ID = digest("f");
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; state.child.alive = false; state.child.runningUnit = false; state.child.terminalUnit = true; return { ok: true }; },
    secureSocketIdentity: async () => ENDPOINT_ID,
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  const first = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(first.status, "campaign-recovery-isolation-clean");
  const second = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(second.status, "campaign-recovery-production-start-authorized", "the next pass authorizes a production start");
  assert.equal(stopCalled, 1, "the child unit must be stopped exactly once across replays");
});

test("M6 fails closed at campaign-child-authorized when the exact unit absence is ambiguous", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-authorized", { campaignChildNonce: NONCE });
  const { deps, state } = guardianDeps(fx, {
    async showUnit() { return { ok: false, error: "systemctl show failed" }; },
  });
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-found");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-authorized", "ambiguous absence must hold without advancing");
});

test("M6 performs exactly one identity-bound phase transition per pass", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  const first = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(first.status, "campaign-recovery-progress");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stopped", "the first pass must stop at exactly production-stopped");
  const second = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(second.status, "campaign-recovery-isolation-clean");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean", "a later pass advances to isolation-clean");
});

test("M6 takes no effect under a prior journal lease after a journal advance", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stopped");
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  deps.assertCurrentAuthority = async () => { throw new Error("stale lease bound to a prior journal identity"); };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stopped", "no advance under a prior journal lease");
  assert.equal(state.stopCalls, 0);
});

test("M6 last-moment stale lease prevents the production stop", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  deps.assertCurrentAuthority = async () => { throw new Error("stale lease"); };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.equal(state.stopCalls, 0, "production must not stop under a stale last-moment authority");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stop-authorized");
});

test("M6 last-moment stale lease prevents the child unit stop", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  deps.assertCurrentAuthority = async () => { throw new Error("stale lease"); };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.equal(stopCalled, 0, "child unit must not stop under a stale last-moment authority");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active");
});

test("M6 last-moment journal substitution prevents the prepared cancellation", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "prepared");
  const { deps, state } = guardianDeps(fx);
  deps.assertCurrentAuthority = async () => { throw new Error("journal substituted"); };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the inert prepared journal must be retained when authority is not current");
});

test("M6 last-moment config substitution prevents the isolation-clean advance", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stopped");
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  deps.assertCurrentAuthority = async () => { throw new Error("config drifted"); };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stopped", "no advance under last-moment config substitution");
});

test("M6 unit substitution in the pre-stop re-show prevents the child stop", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let showCalls = 0;
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async showUnit() {
      showCalls += 1;
      if (showCalls === 1) return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
      // The pre-stop re-show reveals a substituted unit identity.
      return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: "ffffffffffffffffffffffffffffffff", ControlGroup: CONTROL_GROUP } };
    },
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.runningUnit = true;
  state.child.alive = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal(stopCalled, 0, "the child unit must not stop when the pre-stop re-show reveals substitution");
  assert.ok(showCalls >= 2, "the unit must be re-shown immediately before stop");
});

test("M6 watcher immediately loops on progress and refreshes against the fresh under-lock binding and current journal", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps: baseDeps } = guardianDeps(fx);
  let runs = 0;
  let waitCalls = 0;
  const refreshCalls = [];
  const ac = new AbortController();
  const waitForTrigger = async () => { waitCalls += 1; ac.abort(); };
  const runRecovery = async () => {
    runs += 1;
    if (runs === 1) {
      // The first pass advances the journal to production-stopped (one transition).
      const j = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
      await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "production-stopped", deriveCampaignJournalIdentity(j), {});
      return { status: "campaign-recovery-progress", progress: true, manualAttentionRequired: false };
    }
    ac.abort();
    return { status: "campaign-recovery-isolation-clean", manualAttentionRequired: false };
  };
  const refreshLease = async (...args) => { refreshCalls.push(args); return { ok: true }; };
  const removeLease = async () => {};
  await watchCampaignRecoveryGuardian(fx.configPath, {
    expectedOwnerUid: uid,
    signal: ac.signal,
    runRecovery,
    waitForTrigger,
    sleep: async () => {},
    refreshLease,
    removeLease,
    withCustody: baseDeps.withCustody,
    inspectSource: async () => ({ commit: fx.configuration.sourceCommit, clean: true }),
  });
  assert.equal(waitCalls, 0, "progress must loop immediately without the ordinary trigger");
  assert.ok(runs >= 2, "the watcher must immediately run another pass after progress");
  assert.equal(refreshCalls.length, 2, "the watcher must refresh the lease on every iteration");
  const secondOpts = refreshCalls[1][3];
  const current = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(secondOpts.journalIdentitySha256, deriveCampaignJournalIdentitySha256(current), "the post-progress refresh must bind the exact current (advanced) journal identity");
  assert.equal(secondOpts.custodyIdentitySha256, fx.custodyIdentity, "the refresh must use the freshly loaded under-custody binding identity");
});

test("M6 watcher runs recovery passes, refreshes the lease, and removes its own lease on abort", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps: baseDeps, state } = guardianDeps(fx);
  let runs = 0;
  const refreshCalls = [];
  const removeCalls = [];
  const ac = new AbortController();
  const waitForTrigger = async () => { ac.abort(); };
  const runRecovery = async () => { runs += 1; return { status: "campaign-recovery-isolation-clean", manualAttentionRequired: false }; };
  const refreshLease = async (...args) => { refreshCalls.push(args); return { ok: true }; };
  const removeLease = async (...args) => { removeCalls.push(args); };
  const inspectJournal = async () => ({ journal: { phase: "production-stop-authorized", campaignOperationSha256: digest("b"), campaignChildNonce: null, campaignChild: null } });
  await watchCampaignRecoveryGuardian(fx.configPath, {
    expectedOwnerUid: uid,
    signal: ac.signal,
    runRecovery,
    waitForTrigger,
    sleep: async () => {},
    refreshLease,
    removeLease,
    withCustody: baseDeps.withCustody,
    inspectJournal,
    inspectSource: async () => ({ commit: fx.configuration.sourceCommit, clean: true }),
  });
  assert.ok(runs >= 1, "the watcher must run at least one recovery pass");
  assert.ok(refreshCalls.length >= 1, "the watcher must publish/refresh the campaign lease");
  assert.ok(removeCalls.length >= 1, "the watcher must remove its own lease on abort");
});

test("M6 watcher durably removes its own lease after settlement/no-active but stays alive for a later journal", async (t) => {
  for (const status of ["campaign-recovery-settled", "no-active-recovery"]) {
    const fx = await fixture(t);
    const { deps: baseDeps } = guardianDeps(fx);
    const removeCalls = [];
    const ac = new AbortController();
    let runs = 0;
    const waitForTrigger = async () => { ac.abort(); };
    const runRecovery = async () => { runs += 1; return { status, manualAttentionRequired: false }; };
    const refreshLease = async () => ({ ok: true });
    const removeLease = async (...args) => { removeCalls.push(args); };
    const inspectJournal = async () => null;
    await watchCampaignRecoveryGuardian(fx.configPath, {
      expectedOwnerUid: uid,
      signal: ac.signal,
      runRecovery,
      waitForTrigger,
      sleep: async () => {},
      refreshLease,
      removeLease,
      withCustody: baseDeps.withCustody,
      inspectJournal,
      inspectSource: async () => ({ commit: fx.configuration.sourceCommit, clean: true }),
    });
    assert.ok(removeCalls.length >= 1, `after ${status} the watcher must durably remove its own lease`);
    assert.ok(runs >= 1, `the watcher must run a recovery pass for ${status}`);
  }
});

test("M6 last-moment production restart after the stop prevents the production-stopped advance without re-stopping", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stop-authorized");
  const { deps, state } = guardianDeps(fx);
  state.running = true;
  let inspectCalls = 0;
  deps.inspectContainer = async (target) => {
    inspectCalls += 1;
    if (inspectCalls === 1) return productionContainer(fx, { running: true });
    if (inspectCalls === 2) return productionContainer(fx, { running: false });
    // The last-moment transition reproof observes the exact bound production
    // restarted/substituted after the earlier stopped-state proof.
    return productionContainer(fx, { running: true, startedAt: "2026-08-14T00:00:00.000000000Z", restartCount: 1 });
  };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-production-substituted");
  assert.equal(state.stopCalls, 1, "the exact pre-maintenance production is stopped once; the last-moment reproof never re-stops it");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stop-authorized", "a restart after the stop must prevent the advance");
});

test("M6 last-moment comparison/GPU resource appearance after the initial clean proof prevents the isolation-clean advance", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-stopped");
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  let inventoryCalls = 0;
  deps.inventory = async () => {
    inventoryCalls += 1;
    if (inventoryCalls === 1) return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [] };
    // A late GPU/comparison resource appears after the initial clean isolation proof.
    return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [digest("9")] };
  };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-isolation-not-clean");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-stopped", "a late resource appearance must prevent the advance");
});

test("M6 last-moment authorized-unit appearance after the initial not-found proof prevents the isolation-clean advance", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-authorized", { campaignChildNonce: NONCE });
  const { deps, state } = guardianDeps(fx);
  state.running = false;
  let showCalls = 0;
  deps.showUnit = async () => {
    showCalls += 1;
    if (showCalls === 1) return { ok: true, facts: { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "", ControlGroup: "" } };
    // The exact nonce-derived unit appears at the last moment.
    return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
  };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-substituted");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-authorized", "an appearing authorized unit must prevent the advance");
});

test("M6 last-moment child cgroup regression after the initial terminal proof prevents the isolation-clean advance", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.terminalUnit = true;
  let cgroupCalls = 0;
  deps.proveCgroupEmpty = async () => {
    cgroupCalls += 1;
    // The initial terminal proof sees an empty cgroup; the last-moment reproof
    // observes a live descendant regression in the exact unit cgroup.
    return cgroupCalls === 1;
  };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-terminal");
  assert.equal(stopCalled, 0, "the observation-only reproof must not stop the unit");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "a child cgroup regression must prevent the advance");
});

test("M6 last-moment child terminal regression is observation-only and never calls stopUnit", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "campaign-child-active", { campaignChildNonce: NONCE, campaignChild: childIdentity() });
  let stopCalled = 0;
  const { deps, state } = guardianDeps(fx, {
    async stopUnit() { stopCalled += 1; return { ok: true }; },
  });
  state.running = false;
  state.child.terminalUnit = true;
  let showCalls = 0;
  deps.showUnit = async () => {
    showCalls += 1;
    if (showCalls === 1) return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
    // The exact child regresses to running at the last moment.
    return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
  };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-child-not-terminal");
  assert.equal(stopCalled, 0, "the last-moment child reproof is observation-only and must not stop the unit");
  assert.ok(showCalls >= 2, "the unit must be freshly re-shown immediately before the advance");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "campaign-child-active", "a terminal regression must prevent the advance");
});

// ---------------------------------------------------------------------------
// M7: extend the single guardian from isolation-clean through production-start-
// authorized, production-started, readiness-proven, and the M8-required hold.
// ---------------------------------------------------------------------------

// M7 dependencies helper: provides the exact secure socket identity, a fail-
// closed 204 start, and a bounded readiness probe, with the campaign child
// already terminal/absent as required after isolation-clean.
function m7Deps(fx, overrides = {}) {
  const ENDPOINT_ID = overrides.endpointId ?? digest("f");
  const POST_START_AT = overrides.postStartAt ?? "2026-08-14T00:00:00.000000000Z";
  const { deps, state } = guardianDeps(fx, {
    secureSocketIdentity: overrides.secureSocketIdentity ?? (async () => ENDPOINT_ID),
    startProduction: overrides.startProduction ?? (async () => {
      state.startCalls = (state.startCalls ?? 0) + 1;
      state.running = true;
      state.startedAt = POST_START_AT;
      state.restartCount = 0;
      return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: fx.production.containerId, endpointIdentity: ENDPOINT_ID };
    }),
    probeReadiness: overrides.probeReadiness ?? (async () => ({ status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:05.000000000Z", responseSha256: digest("c") })),
    ...overrides,
  });
  state.child.terminalUnit = true;
  return { deps, state, ENDPOINT_ID, POST_START_AT };
}

// Build a journal at production-started with a durable 204 receipt and the
// exact post-start StartedAt.
async function buildProductionStarted(fx, opts = {}) {
  const ENDPOINT_ID = opts.endpointId ?? digest("f");
  const POST_START_AT = opts.postStartAt ?? "2026-08-14T00:00:00.000000000Z";
  const journal = await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: ENDPOINT_ID });
  const identity = deriveCampaignJournalIdentity(journal);
  const receipt = { nonce: digest("e"), containerId: fx.production.containerId, endpointIdentity: ENDPOINT_ID, status: 204, startedAt: POST_START_AT };
  const advanced = await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "production-started", identity, { guardianStartedAt: POST_START_AT, guardianStartReceipt: receipt });
  return { journal: advanced.journal, ENDPOINT_ID, POST_START_AT };
}

function outcomeFor(status = "pass", char = "a") {
  if (status === "error") {
    return deriveCampaignErrorOutcome({ failureClass: "child-exit-2", diagnosticSha256: digest("d"), completedPairs: 0, requiredPairs: 8 });
  }
  const requiredPairs = status === "tuning-frozen" ? 1 : 8;
  const completedPairs = status === "pass" ? requiredPairs : 1;
  const exitCode = status === "pass" || status === "tuning-frozen" ? 0 : 3;
  return deriveCampaignOutcome({ status, campaignId: "outcomebattery-" + char.repeat(24), resultSha256: digest("9"), exitCode, completedPairs, requiredPairs });
}

// The fresh M8 settlement probe must occur strictly after the recorded M7
// readiness (00:00:05), so a real fresh probe returns a later provenAt.
const FRESH_M8_PROBE = async () => ({ status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:07.000000000Z", responseSha256: digest("c") });

// Build a journal at readiness-proven carrying a durably bound campaign outcome.
async function buildReadinessProven(fx, outcome = null, opts = {}) {
  const { journal, POST_START_AT } = await buildProductionStarted(fx, opts);
  const identity = deriveCampaignJournalIdentity(journal);
  const fields = {
    readinessEvidence: { productionStartedAt: journal.guardianStartedAt, provenAt: "2026-08-14T00:00:05.000000000Z", status: 200, latencyMilliseconds: 7, responseSha256: digest("c") },
  };
  if (outcome) fields.campaignOutcome = outcome;
  const advanced = await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "readiness-proven", identity, fields);
  return { journal: advanced.journal, POST_START_AT };
}

test("M7 happy path progresses exactly one phase per pass through readiness-proven and then holds for M8", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const { deps, state, ENDPOINT_ID, POST_START_AT } = m7Deps(fx);
  state.running = false;
  const p1 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p1.status, "campaign-recovery-production-start-authorized");
  assert.equal(p1.progress, true);
  assert.equal(state.startCalls ?? 0, 0, "the authorization pass must not start production");
  const p2 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p2.status, "campaign-recovery-production-started");
  assert.equal(p2.progress, true);
  assert.equal(state.startCalls, 1, "exactly one 204 start request in the start pass");
  const started = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(started.phase, "production-started");
  assert.equal(started.guardianStartReceipt.status, 204);
  assert.equal(started.guardianStartReceipt.startedAt, POST_START_AT);
  assert.equal(started.guardianStartReceipt.endpointIdentity, ENDPOINT_ID);
  const p3 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p3.status, "campaign-recovery-readiness-proven");
  assert.equal(p3.progress, true);
  const ready = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(ready.phase, "readiness-proven");
  assert.equal(ready.readinessEvidence.status, 200);
  assert.equal(ready.readinessEvidence.productionStartedAt, POST_START_AT);
  assert.equal(ready.readinessEvidence.responseSha256, digest("c"));
  assert.ok(Number.isSafeInteger(ready.readinessEvidence.latencyMilliseconds) && ready.readinessEvidence.latencyMilliseconds >= 0);
  // No child outcome was durably bound in this recovery-driven path, so M8 must
  // hold safely/manual and never fabricate pass from readiness/phase alone.
  const p4 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p4.status, "manual-attention-campaign-no-outcome");
  assert.equal(p4.holdForM8, false);
  assert.equal(p4.progress, false);
  assert.equal(p4.manualAttentionRequired, true);
  assert.equal(p4.settled, false);
  // Repeated M8 invocations are idempotent with no settlement effect.
  const p5 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p5.status, "manual-attention-campaign-no-outcome");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "readiness-proven");
});

test("M7 at readiness-proven performs no settlement, cancel, or removal and grants no unintended authority", async (t) => {
  const fx = await fixture(t);
  const { journal } = await buildProductionStarted(fx);
  await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "readiness-proven", deriveCampaignJournalIdentity(journal), {
    readinessEvidence: { productionStartedAt: journal.guardianStartedAt, provenAt: "2026-08-14T00:00:06.000000000Z", status: 200, latencyMilliseconds: 7, responseSha256: digest("c") },
  });
  let cancelCalls = 0;
  let settleCalls = 0;
  let removeCalls = 0;
  const { deps, state, POST_START_AT } = m7Deps(fx, {
    async cancelJournal() { cancelCalls += 1; },
    async settleJournal() { settleCalls += 1; },
    async removeLease() { removeCalls += 1; },
  });
  deps.cancelJournal = async () => { cancelCalls += 1; };
  // Production is already running with the fresh post-start identity bound to
  // the durable 204 receipt, exactly as the readiness-proven advance requires.
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-no-outcome");
  assert.equal(result.holdForM8, false);
  assert.equal(cancelCalls, 0, "M8 must never cancel/remove the active journal");
  assert.equal(settleCalls, 0, "a journal with no durably bound outcome must never settle");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained");
  assert.equal(result.authority.grantsSettlement, false);
  assert.equal(result.authority.grantsCompletion, false);
  assert.equal(result.authority.grantsDeployment, false);
  assert.equal(result.authority.grantsCredentials, false);
  assert.equal(result.authority.grantsExternalNetwork, false);
});

// ---------------------------------------------------------------------------
// M8: exact-once campaign settlement from readiness-proven with a bound outcome.
// ---------------------------------------------------------------------------

test("M8 settles exactly once from readiness-proven, deriving the terminal status from the bound outcome", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx, { probeReadiness: FRESH_M8_PROBE });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-settled");
  assert.equal(result.settled, true);
  assert.equal(result.progress, false);
  assert.equal(result.manualAttentionRequired, false);
  assert.equal(result.settledCampaignStatus, "campaign-pass-production-restored");
  assert.equal(result.campaignOutcomeSha256, outcome.outcomeSha256);
  assert.equal(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), null, "the active journal must be removed after settlement");
  const receiptPath = deriveCampaignRecoveryReceiptPath(fx.custodyLockPath, "campaign-pass-production-restored", digest("a"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.status, "campaign-pass-production-restored");
  assert.equal(receipt.campaignOutcomeSha256, outcome.outcomeSha256);
  assert.equal(receipt.campaignId, outcome.campaignId);
  assert.equal(receipt.settled, true);
  // A second invocation sees no active journal and never re-settles.
  const again = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(again.status, "no-active-recovery");
});

test("M8 settlement maps every terminal outcome category mechanically and never lets readiness imply pass", async (t) => {
  for (const [status, terminal, char] of [
    ["pass", "campaign-pass-production-restored", "a"],
    ["in-progress", "campaign-in-progress-production-restored", "b"],
    ["blocked", "campaign-blocked-production-restored", "c"],
    ["error", "campaign-error-production-restored", "d"],
    ["tuning-frozen", "tuning-frozen-production-restored", "e"],
  ]) {
    const fx = await fixture(t);
    const outcome = outcomeFor(status, char);
    await buildReadinessProven(fx, outcome);
    const { deps, state, POST_START_AT } = m7Deps(fx, { probeReadiness: FRESH_M8_PROBE });
    state.running = true;
    state.startedAt = POST_START_AT;
    state.restartCount = 0;
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.equal(result.status, "campaign-recovery-settled", status);
    assert.equal(result.settledCampaignStatus, terminal, status);
    assert.equal(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), null, `${status} must settle`);
  }
});

test("M8 settlement performs exactly one effect bundle and is idempotent on identical replay", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("in-progress", "b");
  await buildReadinessProven(fx, outcome);
  let settleCalls = 0;
  const { deps, state, POST_START_AT } = m7Deps(fx, {
    probeReadiness: FRESH_M8_PROBE,
    async settleJournal(...args) {
      settleCalls += 1;
      const real = await settleCampaignRecoveryJournal(...args);
      return real;
    },
  });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-settled");
  assert.equal(result.settledCampaignStatus, "campaign-in-progress-production-restored");
  assert.equal(settleCalls, 1, "exactly one settlement effect bundle per guardian invocation");
  // Build an identical journal and re-settle: identical replay is idempotent.
  const fx2 = await fixture(t);
  await buildReadinessProven(fx2, outcome);
  const { deps: d2, state: s2, POST_START_AT: P2 } = m7Deps(fx2, { probeReadiness: FRESH_M8_PROBE });
  s2.running = true;
  s2.startedAt = P2;
  s2.restartCount = 0;
  const result2 = await runCampaignRecoveryGuardian(fx2.configPath, d2);
  assert.equal(result2.settledCampaignStatus, "campaign-in-progress-production-restored");
});

test("M8 holds manual attention and settles nothing when the live reproof fails at the settlement seam", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx);
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  // Dirty comparison isolation after readiness must hold manual attention with no settlement.
  deps.inventory = async () => { state.dirtyIsolation = true; return { prefixContainers: ["pixel-outcome-leaked"], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [fx.production.containerId] }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.ok(result.manualAttentionRequired, "dirty isolation at the settlement seam must hold manual attention");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0, "a failed live reproof must never settle");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained on a failed settlement reproof");
});

test("M8 rejects a substituted/restarted production at the settlement seam and never settles", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx);
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 1; // restartCount drifted after readiness
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.ok(result.manualAttentionRequired, "a restarted production must hold manual attention at the settlement seam");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0);
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained");
});

test("M8 re-runs exact authority immediately before settlement; config/journal/lease drift between the first proof and settlement holds manual with no settle", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx, { probeReadiness: FRESH_M8_PROBE });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  // The first assertCurrent (before the fresh readiness probe) succeeds; the
  // last-moment re-run immediately before settlement fails closed.
  let assertCalls = 0;
  deps.assertCurrentAuthority = async () => {
    assertCalls += 1;
    if (assertCalls >= 2) throw new Error("config/custody/journal/lease drift after the fresh proof");
    return { ok: true };
  };
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error", "last-moment authority drift must hold manual attention");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0, "authority drift immediately before settlement must never settle");
  assert.ok(assertCalls >= 2, "the exact authority must be re-run after the fresh proof and before settlement");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained on authority drift");
});

test("M8 injected-race regression: journal/lease drift at the FINAL authority seam (immediately before settlement) produces no settlement call, no receipt, and no journal unlink", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  const built = await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx, { probeReadiness: FRESH_M8_PROBE });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  // The final exact authority recheck is the last async operation before
  // settlement. Inject a journal/lease drift that is observed ONLY at that
  // final seam (after the fresh probe and the full last live reproof), so the
  // guardian must fail closed with no settlement effect at all.
  let assertCalls = 0;
  deps.assertCurrentAuthority = async () => {
    assertCalls += 1;
    if (assertCalls >= 2) throw new Error("journal/lease drift at the final settlement seam");
    return { ok: true };
  };
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-error", "drift at the final seam must hold manual attention");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0, "a journal/lease drift at the final seam must never call settlement");
  assert.ok(assertCalls >= 2, "the final authority recheck must run after the fresh probe and last live reproof, immediately before settlement");
  // No terminal receipt may be durably created and no active journal may be
  // unlinked at the final seam under drift.
  const receiptPath = deriveCampaignRecoveryReceiptPath(fx.custodyLockPath, "campaign-pass-production-restored", built.journal.maintenanceOperationSha256);
  await assert.rejects(lstat(receiptPath), /ENOENT/u, "drift at the final seam must not create a settlement receipt");
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained (no unlink) on drift at the final seam");
});

test("M8 requires a fresh readiness probe after the recorded M7 readiness; a stale/early probe holds manual with no settle", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  // buildReadinessProven records M7 readiness at 00:00:05; a fresh settlement
  // probe that is not strictly after it must fail closed.
  const { deps, state, POST_START_AT } = m7Deps(fx, {
    probeReadiness: async () => ({ status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:05.000000000Z", responseSha256: digest("c") }),
  });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-readiness-failed", "a stale/early fresh probe must hold manual attention");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0);
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained");
});

test("M8 holds manual attention with no settle when production restarts during the fresh settlement readiness probe", async (t) => {
  const fx = await fixture(t);
  const outcome = outcomeFor("pass");
  await buildReadinessProven(fx, outcome);
  const { deps, state, POST_START_AT } = m7Deps(fx, {
    async probeReadiness() { state.restartCount = 1; return { status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:07.000000000Z", responseSha256: digest("c") }; },
  });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  let settleCalls = 0;
  deps.settleJournal = async () => { settleCalls += 1; return { status: "campaign-pass-production-restored" }; };
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-readiness-unstable", "a restart during the fresh probe must hold manual attention");
  assert.equal(result.settled, false);
  assert.equal(settleCalls, 0);
  assert.ok(await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid), "the active journal must be retained");
});

test("M8 a comparison-cleanup-pending journal with no bound outcome (crash window) restores production safely and never fabricates pass", async (t) => {
  // A controller crash between the child finishing (comparison-cleanup-pending)
  // and binding the outcome (campaign-outcome-bound) leaves a null-outcome
  // journal. The guardian must still restore production, advancing through the
  // recovery phases, and must never fabricate a pass outcome.
  const fx = await fixture(t);
  await buildJournal(fx, "comparison-cleanup-pending");
  const { deps, state } = m7Deps(fx);
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-isolation-clean", "the guardian must restore production from a null-outcome crash window");
  assert.equal(result.campaignChildTerminal, true);
  const now = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(now.phase, "isolation-clean");
  assert.equal(now.campaignOutcome, null, "no fabricated outcome may be bound");
  // A null-outcome journal that does reach readiness-proven holds manual and
  // never passes (covered by the existing no-outcome settlement hold).
});

test("M7 rejects a 304 already-started result as manual attention and never advances or retries", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  let startCalls = 0;
  const { deps, state } = m7Deps(fx, {
    async startProduction() {
      startCalls += 1;
      return { ok: false, transition: false, ambiguous: false, status: 304, body: "", containerId: fx.production.containerId, endpointIdentity: digest("f") };
    },
  });
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-start-rejected");
  assert.equal(result.manualAttentionRequired, true);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-start-authorized");
  assert.equal(startCalls, 1);
});

test("M7 rejects a malformed/extra-key start result and an ambiguous start result as manual attention", async (t) => {
  for (const bad of [
    { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: digest("1"), endpointIdentity: digest("f"), extra: 1 },
    { ok: true, transition: true, ambiguous: true, status: 204, body: "", containerId: digest("1"), endpointIdentity: digest("f") },
    { ok: false, transition: null, ambiguous: true, status: null, body: null, containerId: digest("1"), endpointIdentity: null, reason: "timeout" },
  ]) {
    const fx = await fixture(t);
    await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
    const { deps, state } = m7Deps(fx, { async startProduction() { return bad; } });
    state.running = false;
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.equal(result.manualAttentionRequired, true, `malformed/ambiguous start must hold manual attention: ${JSON.stringify(bad)}`);
    assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-start-authorized", "a rejected start must retain the durable intent");
  }
});

test("M7 rejects a throwing start attempt and a substituted container id as manual attention", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const { deps, state } = m7Deps(fx, { async startProduction() { throw new Error("docker engine unreachable"); } });
  state.running = false;
  const thrown = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(thrown.manualAttentionRequired, true);
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-start-authorized");

  const fx2 = await fixture(t);
  await buildJournal(fx2, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const sub = m7Deps(fx2, { async startProduction() { return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: digest("9"), endpointIdentity: digest("f") }; } });
  sub.state.running = false;
  const substituted = await runCampaignRecoveryGuardian(fx2.configPath, sub.deps);
  assert.equal(substituted.status, "manual-attention-campaign-production-substituted");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx2.custodyLockPath, uid)).journal.phase, "production-start-authorized");
});

test("M7 never authorizes or starts without an exact endpoint identity, and holds on endpoint change before/during start", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const missing = m7Deps(fx, { secureSocketIdentity: async () => null });
  missing.state.running = false;
  const noEndpoint = await runCampaignRecoveryGuardian(fx.configPath, missing.deps);
  assert.equal(noEndpoint.status, "manual-attention-campaign-endpoint-missing");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean", "no intent may be recorded without an exact endpoint identity");

  const fx2 = await fixture(t);
  await buildJournal(fx2, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const changed = m7Deps(fx2, { secureSocketIdentity: async () => digest("9") });
  changed.state.running = false;
  const changedResult = await runCampaignRecoveryGuardian(fx2.configPath, changed.deps);
  assert.equal(changedResult.status, "manual-attention-campaign-endpoint-missing");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx2.custodyLockPath, uid)).journal.phase, "production-start-authorized", "an endpoint identity change immediately before start must hold manual attention");

  const fx3 = await fixture(t);
  await buildJournal(fx3, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const during = m7Deps(fx3, { async startProduction() { return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: digest("1"), endpointIdentity: digest("9") }; } });
  during.state.running = false;
  const duringResult = await runCampaignRecoveryGuardian(fx3.configPath, during.deps);
  assert.equal(duringResult.manualAttentionRequired, true, "an endpoint substitution during the start must hold manual attention");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx3.custodyLockPath, uid)).journal.phase, "production-start-authorized");
});

test("M7 holds on config/custody/journal/lease drift at every authorization/effect/advance seam", async (t) => {
  for (const phase of ["isolation-clean", "production-start-authorized", "production-started"]) {
    const fx = await fixture(t);
    let journal;
    if (phase === "isolation-clean") {
      journal = await buildJournal(fx, "isolation-clean");
    } else if (phase === "production-start-authorized") {
      journal = await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
    } else {
      journal = (await buildProductionStarted(fx)).journal;
    }
    const { deps, state } = m7Deps(fx);
    deps.assertCurrentAuthority = async () => { throw new Error("config/custody/journal/lease drift"); };
    state.running = phase === "production-started";
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.equal(result.status, "manual-attention-campaign-error", `drift at ${phase} must fail closed`);
    const now = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
    assert.equal(now.phase, phase, `drift at ${phase} must not advance the journal`);
  }
});

test("M7 holds manual attention on missing or substituted production at the authorize seam", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const missing = m7Deps(fx, { async inspectContainer() { return null; } });
  missing.state.running = false;
  const missingResult = await runCampaignRecoveryGuardian(fx.configPath, missing.deps);
  assert.equal(missingResult.status, "manual-attention-campaign-production-missing");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "isolation-clean");

  const fx2 = await fixture(t);
  await buildJournal(fx2, "isolation-clean");
  const sub = m7Deps(fx2, { async inspectContainer() { return { Id: digest("9"), Name: "/other", Image: `sha256:${digest("2")}`, RestartCount: 0, State: { Running: false, Paused: false, Restarting: false, Dead: false, StartedAt: fx2.production.expectedStartedAt } }; } });
  sub.state.running = false;
  const subResult = await runCampaignRecoveryGuardian(fx2.configPath, sub.deps);
  assert.equal(subResult.status, "manual-attention-campaign-production-substituted");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx2.custodyLockPath, uid)).journal.phase, "isolation-clean");
});

test("M7 holds on wrong restart count or unchanged (wrong) StartedAt after a definite 204", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const wrongRestart = m7Deps(fx, {
    async startProduction() {
      // The 204 is returned, but the post-start running production has an
      // incorrect restart count, which must not be adopted.
      stateRef.running = true;
      stateRef.startedAt = "2026-08-14T00:00:00.000000000Z";
      stateRef.restartCount = 5;
      return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: fx.production.containerId, endpointIdentity: digest("f") };
    },
  });
  const stateRef = wrongRestart.state;
  wrongRestart.state.running = false;
  const wrongRestartResult = await runCampaignRecoveryGuardian(fx.configPath, wrongRestart.deps);
  assert.equal(wrongRestartResult.status, "manual-attention-campaign-start-not-proven");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-start-authorized");

  const fx2 = await fixture(t);
  await buildJournal(fx2, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const unchangedStarted = m7Deps(fx2, {
    async startProduction() {
      stateRef2.running = true;
      // startedAt intentionally left unchanged at the reviewed pre-maintenance value.
      return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: fx2.production.containerId, endpointIdentity: digest("f") };
    },
  });
  const stateRef2 = unchangedStarted.state;
  unchangedStarted.state.running = false;
  const unchangedResult = await runCampaignRecoveryGuardian(fx2.configPath, unchangedStarted.deps);
  assert.equal(unchangedResult.status, "manual-attention-campaign-start-not-proven", "an unchanged/unchanged StartedAt must not be adopted as a successful restart");
});

test("M7 replay at production-started requires the exact durable receipt fingerprint; stopped/substituted production is manual attention and never re-starts", async (t) => {
  const fx = await fixture(t);
  const { journal, POST_START_AT } = await buildProductionStarted(fx);
  const stopped = m7Deps(fx);
  stopped.state.running = false; // production stopped after the durable receipt
  const stoppedResult = await runCampaignRecoveryGuardian(fx.configPath, stopped.deps);
  assert.equal(stoppedResult.manualAttentionRequired, true, "a stopped production after the durable receipt must hold manual attention");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started");
  assert.equal(stopped.state.startCalls ?? 0, 0, "M7 must never re-start production after a durable receipt");

  const fx2 = await fixture(t);
  const sub = await buildProductionStarted(fx2, { postStartAt: POST_START_AT });
  const substituted = m7Deps(fx2);
  substituted.state.running = true;
  substituted.state.startedAt = "2026-08-15T00:00:00.000000000Z"; // different startedAt than receipt
  const subResult = await runCampaignRecoveryGuardian(fx2.configPath, substituted.deps);
  assert.equal(subResult.status, "manual-attention-campaign-production-substituted", "a StartedAt that differs from the durable receipt must hold manual attention");
});

test("M7 detects a restarted or changed production after readiness and holds without advance", async (t) => {
  const fx = await fixture(t);
  const { journal, POST_START_AT } = await buildProductionStarted(fx);
  const restarted = m7Deps(fx);
  restarted.state.running = true;
  restarted.state.startedAt = POST_START_AT;
  let inspectCalls = 0;
  restarted.deps.inspectContainer = async (target) => {
    inspectCalls += 1;
    // Stable before the readiness probe; restarted/substituted after it.
    if (inspectCalls === 1) return productionContainer(fx, { running: true, startedAt: POST_START_AT, restartCount: 0 });
    return productionContainer(fx, { running: true, startedAt: "2026-08-16T00:00:00.000000000Z", restartCount: 1 });
  };
  const restartedResult = await runCampaignRecoveryGuardian(fx.configPath, restarted.deps);
  assert.equal(restartedResult.status, "manual-attention-campaign-readiness-unstable");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started");
});

test("M7 holds manual attention on dirty comparison isolation or nonterminal/ambiguous child at every M7 phase", async (t) => {
  for (const [phase, running] of [["isolation-clean", false], ["production-start-authorized", false], ["production-started", true]]) {
    const fx = await fixture(t);
    if (phase === "isolation-clean") await buildJournal(fx, "isolation-clean");
    else if (phase === "production-start-authorized") await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
    else await buildProductionStarted(fx);
    const dirty = m7Deps(fx);
    dirty.state.running = running;
    if (phase === "production-started") dirty.state.startedAt = "2026-08-14T00:00:00.000000000Z";
    dirty.deps.inventory = async () => ({ prefixContainers: ["pixel-comparison-foo"], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: running ? [fx.production.containerId] : [] });
    const dirtyResult = await runCampaignRecoveryGuardian(fx.configPath, dirty.deps);
    assert.equal(dirtyResult.status, "manual-attention-campaign-isolation-not-clean", `dirty isolation at ${phase} must fail closed`);
    assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, phase);

    const fx2 = await fixture(t);
    if (phase === "isolation-clean") await buildJournal(fx2, "isolation-clean");
    else if (phase === "production-start-authorized") await buildJournal(fx2, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
    else await buildProductionStarted(fx2);
    const childBad = m7Deps(fx2);
    childBad.state.running = running;
    if (phase === "production-started") childBad.state.startedAt = "2026-08-14T00:00:00.000000000Z";
    childBad.state.child.terminalUnit = false;
    childBad.state.child.notFound = false;
    childBad.state.child.runningUnit = false;
    const childResult = await runCampaignRecoveryGuardian(fx2.configPath, childBad.deps);
    assert.equal(childResult.manualAttentionRequired, true, `nonterminal/ambiguous child at ${phase} must fail closed`);
    assert.equal((await inspectActiveCampaignRecoveryJournal(fx2.custodyLockPath, uid)).journal.phase, phase);
  }
});

test("M7 readiness probe false/throw/wrong-status/oversize holds manual attention with no advance", async (t) => {
  for (const [label, probe] of [
    ["false", async () => null],
    ["throw", async () => { throw new Error("probe failed"); }],
    ["wrong-status", async () => ({ status: 503, latencyMilliseconds: 5, provenAt: "2026-08-14T00:00:05.000000000Z", responseSha256: digest("c") })],
    ["oversize", async () => null],
  ]) {
    const fx = await fixture(t);
    await buildProductionStarted(fx);
    const bad = m7Deps(fx, { probeReadiness: probe });
    bad.state.running = true;
    bad.state.startedAt = "2026-08-14T00:00:00.000000000Z";
    const result = await runCampaignRecoveryGuardian(fx.configPath, bad.deps);
    assert.equal(result.status, "manual-attention-campaign-readiness-failed", `${label} probe must hold manual attention`);
    assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started");
  }
});

test("M7 readiness result binds a stable bounded response SHA and latency and records no raw content", async (t) => {
  const fx = await fixture(t);
  const { journal, POST_START_AT } = await buildProductionStarted(fx);
  const sha = digest("c");
  const provenAt = "2026-08-14T00:00:05.000000000Z";
  const probeResult = m7Deps(fx, { probeReadiness: async () => ({ status: 200, latencyMilliseconds: 12, provenAt, responseSha256: sha }) });
  probeResult.state.running = true;
  probeResult.state.startedAt = POST_START_AT;
  const result = await runCampaignRecoveryGuardian(fx.configPath, probeResult.deps);
  assert.equal(result.status, "campaign-recovery-readiness-proven");
  assert.equal(result.boundary.startsWith("Content-free"), true);
  assert.equal(result.boundary.includes("readiness"), true);
  const ready = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(ready.readinessEvidence.responseSha256, sha);
  assert.equal(ready.readinessEvidence.latencyMilliseconds, 12);
  assert.equal(ready.readinessEvidence.provenAt, provenAt);
  assert.equal(ready.readinessEvidence.productionStartedAt, POST_START_AT);
  // No raw readiness content may be present on the result or in the journal.
  assert.equal(Object.prototype.hasOwnProperty.call(result, "responseBody"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(ready.readinessEvidence, "body"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(ready.readinessEvidence, "response"), false);
});

test("M7 crash before the start boundary: replay at production-start-authorized with production still stopped performs exactly one 204 and advances", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
  const { deps, state } = m7Deps(fx);
  state.running = false;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-production-started");
  assert.equal(state.startCalls, 1);
  const journal = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(journal.phase, "production-started");
  assert.equal(journal.guardianStartReceipt.status, 204);
});

test("M7 one transition/effect bundle per invocation across the M7 window", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const { deps, state } = m7Deps(fx);
  state.running = false;
  // Pass 1 authorizes only; the journal must not jump to production-started.
  const p1 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p1.status, "campaign-recovery-production-start-authorized");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-start-authorized");
  assert.equal(state.startCalls ?? 0, 0);
  // Pass 2 starts and advances to production-started only.
  const p2 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p2.status, "campaign-recovery-production-started");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started");
  assert.equal(state.startCalls, 1);
  // Pass 3 probes readiness and advances to readiness-proven only.
  const p3 = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(p3.status, "campaign-recovery-readiness-proven");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "readiness-proven");
});

test("M7 watcher immediately loops on each M7 progress and refreshes the lease against the fresh journal identity", async (t) => {
  const fx = await fixture(t);
  await buildJournal(fx, "isolation-clean");
  const { deps: baseDeps } = guardianDeps(fx);
  let runs = 0;
  let waitCalls = 0;
  const refreshCalls = [];
  const ac = new AbortController();
  const waitForTrigger = async () => { waitCalls += 1; ac.abort(); };
  const runRecovery = async () => {
    runs += 1;
    const j = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
    if (j.phase === "isolation-clean") {
      await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "production-start-authorized", deriveCampaignJournalIdentity(j), { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: digest("f") });
      return { status: "campaign-recovery-production-start-authorized", progress: true, manualAttentionRequired: false };
    }
    if (j.phase === "production-start-authorized") {
      await advanceCampaignRecoveryJournal(fx.custodyLockPath, uid, "production-started", deriveCampaignJournalIdentity(j), { guardianStartedAt: "2026-08-14T00:00:00.000000000Z", guardianStartReceipt: { nonce: digest("e"), containerId: fx.production.containerId, endpointIdentity: digest("f"), status: 204, startedAt: "2026-08-14T00:00:00.000000000Z" } });
      return { status: "campaign-recovery-production-started", progress: true, manualAttentionRequired: false };
    }
    ac.abort();
    return { status: "campaign-recovery-readiness-proven", progress: true, manualAttentionRequired: false };
  };
  const refreshLease = async (...args) => { refreshCalls.push(args); return { ok: true }; };
  const removeLease = async () => {};
  await watchCampaignRecoveryGuardian(fx.configPath, {
    expectedOwnerUid: uid,
    signal: ac.signal,
    runRecovery,
    waitForTrigger,
    sleep: async () => {},
    refreshLease,
    removeLease,
    withCustody: baseDeps.withCustody,
    inspectSource: async () => ({ commit: fx.configuration.sourceCommit, clean: true }),
  });
  assert.equal(waitCalls, 0, "M7 progress must loop immediately without the ordinary trigger");
  assert.ok(runs >= 3, "the watcher must loop through the M7 progress phases");
  assert.ok(refreshCalls.length >= 3, "the watcher must refresh the lease on every M7 iteration");
  const finalOpts = refreshCalls[refreshCalls.length - 1][3];
  const current = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
  assert.equal(finalOpts.journalIdentitySha256, deriveCampaignJournalIdentitySha256(current), "each refresh must bind the exact current (advanced) journal identity");
});

// ---------------------------------------------------------------------------
// M7 repair pass 2 regressions (defects 1-8)
// ---------------------------------------------------------------------------

// A deterministic fake HTTP response with a streaming body for the default
// bounded readiness probe, without any live endpoint.
function streamResponse(chunks, { status = 200, contentLength } = {}) {
  const headers = new Headers();
  if (contentLength !== undefined) headers.set("content-length", String(contentLength));
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return new Response(stream, { status, headers });
}
function throwingStream() {
  return new ReadableStream({ start(controller) { controller.error(new Error("boom")); } });
}
function probeProduction() {
  return { readinessOrigin: "http://127.0.0.1:8000", readinessModelId: "DeepSeek-V4-Flash-0731", probeIntervalMilliseconds: 1000, maxResponseBytes: 64 };
}
function readinessOkBody() { return JSON.stringify({ data: [{ id: "DeepSeek-V4-Flash-0731" }] }); }

test("M7 default readiness probe is truly byte-bounded and rejects invalid length metadata (no Content-Length oversize, lying Content-Length, exact boundary, premature/throw, no raw body escape)", async () => {
  const production = probeProduction();
  const exact = readinessOkBody();
  assert.ok(exact.length <= 64, "exact body must fit within the test cap");
  // No Content-Length, oversize stream body: must be rejected, never allocated unbounded.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from("x".repeat(65))]) }), null, "no-CL oversize must fail");
  // Lying Content-Length declaring a size over the cap: rejected up front.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: 1000 }) }), null, "lying oversize Content-Length must fail");
  // Exact-boundary success (body exactly within cap) is still accepted.
  const ok = await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)]) });
  assert.ok(ok && ok.status === 200, "exact-boundary body must succeed");
  assert.ok(ok.responseSha256 && /^[a-f0-9]{64}$/u.test(ok.responseSha256), "response SHA must be bound to the bounded body");
  assert.ok(!("body" in ok) && !("bytes" in ok) && !("raw" in ok), "no raw body may escape the probe result");
  // Premature/throwing stream: rejected.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => new Response(throwingStream(), { status: 200 }) }), null, "throwing stream must fail");
  // Invalid / negative / conflicting length metadata: rejected.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: "abc" }) }), null, "non-numeric length must fail");
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: -5 }) }), null, "negative length must fail");
});

test("M7 default readiness probe uses Pixel's strict parser: duplicate keys and invalid UTF-8 never become readiness proof", async () => {
  const production = probeProduction();
  // Duplicate keys must be rejected by the strict parser.
  const dup = '{"data":[{"id":"DeepSeek-V4-Flash-0731","id":"DeepSeek-V4-Flash-0731"}]}';
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(dup)]) }), null, "duplicate object keys must fail");
  // Invalid UTF-8 must be rejected by the strict round-trip check.
  const invalid = Buffer.from([0x7b, 0x22, 0x64, 0x61, 0x74, 0x61, 0x22, 0x3a, 0x5b, 0x7b, 0x22, 0x69, 0x64, 0x22, 0x3a, 0x22, 0xff, 0xfe, 0x22, 0x7d, 0x5d, 0x7d]);
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([invalid]) }), null, "invalid UTF-8 must fail");
  // Malformed JSON and an ambiguous/absent model shape must fail too.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from('{"data":')]) }), null, "malformed JSON must fail");
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from('{"data":[{"id":"OTHER"}]}')]) }), null, "wrong model id must fail");
});

// A fake response whose body records cancellation so we can observe that early
// rejection paths release the reader/connection instead of pinning the guardian
// across repeated manual-attention loops.
function cancellableStream(chunks, { onCancel } = {}) {
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
    cancel() {
      cancelled = true;
      if (onCancel) onCancel();
    },
  });
  return { stream, wasCancelled: () => cancelled };
}

// An adversarial stream that yields an endless sequence of nonterminal
// zero-length `{done:false, value: empty}` chunks and never closes. The bounded
// reader must reject promptly instead of pinning the guardian indefinitely.
function emptyChunkLoopStream() {
  let cancelled = false;
  const stream = new ReadableStream({
    pull(controller) {
      controller.enqueue(new Uint8Array(0));
    },
    cancel() {
      cancelled = true;
    },
  });
  return { stream, wasCancelled: () => cancelled };
}

test("M7 strict Content-Length/body coherence: a declared in-range length that differs from the completed stream length fails closed", async () => {
  const production = probeProduction();
  const exact = readinessOkBody();
  // Both declared values are in-range (below the 64-byte cap) but contradict the
  // actual completed body length; each must be rejected at clean EOF.
  assert.ok(exact.length < 64, "test body must be below the cap");
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: exact.length - 1 }) }), null, "declared-short mismatch must fail closed");
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: exact.length + 1 }) }), null, "declared-long mismatch must fail closed");
  // An exact declared length equal to the completed stream length still succeeds.
  const ok = await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: exact.length }) });
  assert.ok(ok && ok.status === 200, "exact Content-Length equal to the completed body must succeed");
});

test("M7 rejects an adversarial nonterminal zero-length stream and releases the body", async () => {
  const production = probeProduction();
  const loop = emptyChunkLoopStream();
  const outcome = await probeProductionReadinessDefault(production, { fetchImpl: async () => new Response(loop.stream, { status: 200 }) });
  assert.equal(outcome, null, "an endless empty-chunk stream must never become readiness proof");
  assert.equal(loop.wasCancelled(), true, "the adversarial stream body must be cancelled/released on rejection");
});

test("M7 releases the response body on every early rejection path (non-200, invalid length, oversize-declared, zero-length chunk)", async () => {
  const production = probeProduction();
  const exact = readinessOkBody();
  // Non-200 status: the body must be cancelled even though it is never read.
  const non200 = cancellableStream([Buffer.from(exact)]);
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => new Response(non200.stream, { status: 503, headers: { "content-length": String(exact.length) } }) }), null, "non-200 must fail");
  assert.equal(non200.wasCancelled(), true, "non-200 body must be released");
  // Invalid (non-numeric) Content-Length: cancelled before any read.
  const badLen = cancellableStream([Buffer.from(exact)]);
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => new Response(badLen.stream, { status: 200, headers: { "content-length": "abc" } }) }), null, "non-numeric length must fail");
  assert.equal(badLen.wasCancelled(), true, "invalid-length body must be released");
  // Oversize-declared length (above the cap): cancelled before any read.
  const oversize = cancellableStream([Buffer.from(exact)]);
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => new Response(oversize.stream, { status: 200, headers: { "content-length": "1000" } }) }), null, "oversize-declared length must fail");
  assert.equal(oversize.wasCancelled(), true, "oversize-declared body must be released");
  // Declared-short mismatch: the body is fully drained to EOF (so the
  // connection is released) then rejected with no readiness proof.
  assert.equal(await probeProductionReadinessDefault(production, { fetchImpl: async () => streamResponse([Buffer.from(exact)], { contentLength: exact.length - 1 }) }), null, "declared-short mismatch must fail");
});


test("M7 default startProduction closure binds the closed-over socket path and exercises the injectable Docker-start seam", async (t) => {
  const fx = await fixture(t);
  const ENDPOINT_ID = digest("f");
  const START_NONCE = digest("e");
  await buildJournal(fx, "production-start-authorized", { guardianStartNonce: START_NONCE, guardianStartEndpointIdentity: ENDPOINT_ID });
  let engineCalls = 0;
  let engineSocketPath = null;
  let engineReviewed = null;
  let engineContainerId = null;
  let engineTimeoutMs = null;
  let engineReceivedNonce = false;
  const { deps, state } = guardianDeps(fx, {
    secureSocketIdentity: async () => ENDPOINT_ID,
    // Do NOT inject startProduction; inject the lower-level Docker-start seam so
    // the real default closure path is exercised without live Docker. The seam
    // simulates the real 204 effect by leaving the exact container running with
    // a fresh post-start identity and restartCount 0.
    async dockerEngineStart(options) {
      engineCalls += 1;
      engineSocketPath = options.socketPath;
      engineReviewed = options.reviewedIdentity;
      engineContainerId = options.containerId;
      engineTimeoutMs = options.timeoutMs;
      // The lower-level engine has no nonce parameter; the default closure must
      // never forward a caller-provided nonce that would falsely attribute it to
      // Docker. A nonce belongs only to durable journal attribution.
      engineReceivedNonce = Object.prototype.hasOwnProperty.call(options, "nonce") && options.nonce !== undefined;
      const containerId = options.containerId;
      const reviewedIdentity = options.reviewedIdentity;
      state.running = true;
      state.startedAt = "2026-08-14T00:00:00.000000000Z";
      state.restartCount = 0;
      return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId, endpointIdentity: reviewedIdentity };
    },
  });
  state.running = false;
  state.child.terminalUnit = true;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "campaign-recovery-production-started");
  assert.equal(engineCalls, 1, "the default startProduction closure must invoke the injectable Docker-start seam exactly once");
  assert.equal(engineReviewed, ENDPOINT_ID, "the default closure must pass the exact reviewed endpoint identity");
  assert.equal(engineContainerId, fx.production.containerId, "the default closure must pass the exact bound container id");
  assert.equal(engineTimeoutMs, 60000, "the default closure must pass the exact 60s production start timeout");
  assert.equal(engineReceivedNonce, false, "the caller-provided nonce must never be presented as Docker attribution");
  assert.ok(typeof engineSocketPath === "string" && engineSocketPath.length > 0, "the default closure must resolve the exact closed-over socket path (not a self-reference)");
  // The durable nonce belongs to journal attribution only: the durable receipt
  // records it without the lower-level engine ever consuming it.
  const advanced = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.ok(advanced && advanced.journal.guardianStartReceipt, "the durable start receipt must be recorded");
  assert.equal(advanced.journal.guardianStartReceipt.nonce, START_NONCE, "the nonce is retained as durable journal attribution, not Docker attribution");
  assert.equal(advanced.journal.guardianStartReceipt.endpointIdentity, ENDPOINT_ID, "the receipt binds the exact reviewed endpoint identity");
});

test("M7 pre-start last-moment races: child/isolation/endpoint dirtied after the first proof prevent the start (startCalls 0)", async (t) => {
  const cases = [
    ["child", (fx, ENDPOINT_ID) => {
      let showCalls = 0;
      return {
        async showUnit() {
          showCalls += 1;
          if (showCalls === 1) return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
          return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
        },
      };
    }],
    ["isolation", (fx, ENDPOINT_ID) => {
      let invCalls = 0;
      return {
        async inventory() {
          invCalls += 1;
          if (invCalls === 1) return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [] };
          return { prefixContainers: ["foreign"], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [] };
        },
      };
    }],
    ["endpoint", (fx, ENDPOINT_ID) => {
      let epCalls = 0;
      return {
        async secureSocketIdentity() { epCalls += 1; return epCalls === 1 ? ENDPOINT_ID : digest("9"); },
      };
    }],
  ];
  for (const [label, build] of cases) {
    const fx = await fixture(t);
    const ENDPOINT_ID = digest("f");
    await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: ENDPOINT_ID });
    let startCalls = 0;
    const { deps, state } = guardianDeps(fx, {
      secureSocketIdentity: async () => ENDPOINT_ID,
      async startProduction() { startCalls += 1; return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: fx.production.containerId, endpointIdentity: ENDPOINT_ID }; },
      ...build(fx, ENDPOINT_ID),
    });
    state.running = false;
    state.child.terminalUnit = true;
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.equal(startCalls, 0, `${label} race must keep startCalls at zero`);
    assert.ok(result.manualAttentionRequired, `${label} race must hold manual attention`);
    const j = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
    assert.equal(j.phase, "production-start-authorized", `${label} race must not advance the journal`);
  }
});

test("M7 post-204 last-moment races: restartCount/image/container/start-identity drift, dirty isolation, nonterminal child, and endpoint substitution hold manual attention without a receipt", async (t) => {
  const ENDPOINT_ID = digest("f");
  const POST_START_AT = "2026-08-14T00:00:00.000000000Z";
  const cases = [
    ["restartCount", (fx) => {
      let inspCalls = 0;
      return {
        async inspectContainer(target) {
          inspCalls += 1;
          if (target !== fx.production.containerId) return null;
          if (inspCalls <= 2) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: fx.production.expectedRestartCount, State: { Running: false, Paused: false, Restarting: false, Dead: false, StartedAt: fx.production.expectedStartedAt } };
          if (inspCalls === 3) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
          return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 1, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
        },
      };
    }],
    ["image", (fx) => {
      let inspCalls = 0;
      return {
        async inspectContainer(target) {
          inspCalls += 1;
          if (target !== fx.production.containerId) return null;
          if (inspCalls <= 2) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: fx.production.expectedRestartCount, State: { Running: false, Paused: false, Restarting: false, Dead: false, StartedAt: fx.production.expectedStartedAt } };
          if (inspCalls === 3) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
          return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: `sha256:${digest("7")}`, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
        },
      };
    }],
    ["container", (fx) => {
      let inspCalls = 0;
      return {
        async inspectContainer(target) {
          inspCalls += 1;
          if (inspCalls <= 2) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: fx.production.expectedRestartCount, State: { Running: false, Paused: false, Restarting: false, Dead: false, StartedAt: fx.production.expectedStartedAt } };
          if (inspCalls === 3) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
          return { Id: digest("a"), Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
        },
      };
    }],
    ["startIdentity", (fx) => {
      let inspCalls = 0;
      return {
        async inspectContainer(target) {
          inspCalls += 1;
          if (inspCalls <= 2) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: fx.production.expectedRestartCount, State: { Running: false, Paused: false, Restarting: false, Dead: false, StartedAt: fx.production.expectedStartedAt } };
          if (inspCalls === 3) return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: POST_START_AT } };
          return { Id: fx.production.containerId, Name: `/${fx.production.containerName}`, Image: fx.production.imageDigest, RestartCount: 0, State: { Running: true, Paused: false, Restarting: false, Dead: false, StartedAt: "2026-08-14T00:01:00.000000000Z" } };
        },
      };
    }],
    ["isolation", (fx) => {
      let invCalls = 0;
      return {
        async inventory() {
          invCalls += 1;
          if (invCalls <= 2) return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [] };
          return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: ["foreign"] };
        },
      };
    }],
    ["child", (fx) => {
      let showCalls = 0;
      return {
        async showUnit() {
          showCalls += 1;
          if (showCalls <= 2) return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
          return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } };
        },
      };
    }],
    ["endpoint", (fx) => {
      let epCalls = 0;
      return {
        async secureSocketIdentity() { epCalls += 1; return epCalls <= 2 ? ENDPOINT_ID : digest("9"); },
      };
    }],
  ];
  for (const [label, build] of cases) {
    const fx = await fixture(t);
    await buildJournal(fx, "production-start-authorized", { guardianStartNonce: digest("e"), guardianStartEndpointIdentity: ENDPOINT_ID });
    let startCalls = 0;
    const { deps, state } = guardianDeps(fx, {
      secureSocketIdentity: async () => ENDPOINT_ID,
      async startProduction() { startCalls += 1; state.running = true; return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: fx.production.containerId, endpointIdentity: ENDPOINT_ID }; },
      ...build(fx),
    });
    state.running = false;
    state.child.terminalUnit = true;
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.equal(startCalls, 1, `${label} post-204 race must still perform exactly one 204 request`);
    assert.ok(result.manualAttentionRequired, `${label} post-204 race must hold manual attention`);
    const j = (await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal;
    assert.equal(j.phase, "production-start-authorized", `${label} post-204 race must not advance the journal`);
    assert.equal(j.guardianStartReceipt, null, `${label} post-204 race must not write a durable receipt`);
  }
});

test("M7 readiness: only restartCount changing while StartedAt stays equal is detected as unstable across before/probe/after", async (t) => {
  const fx = await fixture(t);
  const { ENDPOINT_ID, POST_START_AT } = await buildProductionStarted(fx);
  const { deps, state } = m7Deps(fx, {
    async probeReadiness() { state.restartCount = 1; return { status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:05.000000000Z", responseSha256: digest("c") }; },
  });
  state.running = true;
  state.startedAt = POST_START_AT;
  state.restartCount = 0;
  const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
  assert.equal(result.status, "manual-attention-campaign-readiness-unstable");
  assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started");
});

test("M7 final readiness advance seam: dirty isolation, child revival, and endpoint substitution after the probe hold manual attention with no advance", async (t) => {
  const ENDPOINT_ID = digest("f");
  const POST_START_AT = "2026-08-14T00:00:00.000000000Z";
  const cases = [
    ["isolation", (fx) => {
      let invCalls = 0;
      return { async inventory() { invCalls += 1; return invCalls === 1 ? { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [fx.production.containerId] } : { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: ["foreign"] }; } };
    }],
    ["child", (fx) => {
      let showCalls = 0;
      return { async showUnit() { showCalls += 1; if (showCalls === 1) return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } }; return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(CHILD_PID), InvocationID: INVOCATION_ID, ControlGroup: CONTROL_GROUP } }; } };
    }],
    ["endpoint", (fx) => {
      let epCalls = 0;
      return { async secureSocketIdentity() { epCalls += 1; return epCalls === 1 ? ENDPOINT_ID : digest("9"); } };
    }],
  ];
  for (const [label, build] of cases) {
    const fx = await fixture(t);
    const { ENDPOINT_ID: ep, POST_START_AT: psa } = await buildProductionStarted(fx);
    const { deps, state } = m7Deps(fx, {
      secureSocketIdentity: async () => ep,
      probeReadiness: async () => ({ status: 200, latencyMilliseconds: 7, provenAt: "2026-08-14T00:00:05.000000000Z", responseSha256: digest("c") }),
      ...build(fx),
    });
    state.running = true;
    state.startedAt = psa;
    state.restartCount = 0;
    const result = await runCampaignRecoveryGuardian(fx.configPath, deps);
    assert.ok(result.manualAttentionRequired, `${label} after-probe race must hold manual attention`);
    assert.equal(result.status, label === "endpoint" ? "manual-attention-campaign-endpoint-missing" : (label === "child" ? "manual-attention-campaign-child-not-terminal" : "manual-attention-campaign-isolation-not-clean"), `${label} status`);
    assert.equal((await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid)).journal.phase, "production-started", `${label} after-probe race must not advance to readiness-proven`);
  }
});

test("M7 result authority is truthful per result and capability is represented separately", async (t) => {
  // no-active-recovery grants no start/readiness authority.
  const fx0 = await fixture(t);
  const none = await runCampaignRecoveryGuardian(fx0.configPath, guardianDeps(fx0).deps);
  assert.equal(none.status, "no-active-recovery");
  assert.equal(none.authority.grantsProductionStart, false, "no-active must not grant production-start authority");
  assert.equal(none.authority.grantsReadiness, false, "no-active must not grant readiness authority");
  assert.equal(none.capability.canRestartBoundProduction, true, "capability is preserved separately");
  assert.equal(none.capability.canProbeBoundReadiness, true, "capability is preserved separately");

  // prepared-cancelled grants no start/readiness authority.
  const fx1 = await fixture(t);
  await buildJournal(fx1, "prepared");
  const cancelled = await runCampaignRecoveryGuardian(fx1.configPath, guardianDeps(fx1).deps);
  assert.equal(cancelled.status, "campaign-recovery-prepared-cancelled");
  assert.equal(cancelled.authority.grantsProductionStart, false, "prepared-cancelled must not grant production-start authority");
  assert.equal(cancelled.authority.grantsReadiness, false, "prepared-cancelled must not grant readiness authority");

  // manual-attention results grant no start/readiness authority.
  const fx2 = await fixture(t);
  await buildJournal(fx2, "production-stop-authorized");
  const manualRes = await runCampaignRecoveryGuardian(fx2.configPath, guardianDeps(fx2, { async stopProduction() {} }).deps);
  assert.ok(manualRes.manualAttentionRequired || manualRes.progress, "production-stop-authorized must progress or hold");
  const fx2b = await fixture(t);
  await buildJournal(fx2b, "prepared");
  const manualHold = await runCampaignRecoveryGuardian(fx2b.configPath, guardianDeps(fx2b, { async inspectContainer() { return null; } }).deps);
  assert.equal(manualHold.status, "manual-attention-campaign-production-missing");
  assert.equal(manualHold.authority.grantsProductionStart, false, "manual-attention must not grant production-start authority");
  assert.equal(manualHold.authority.grantsReadiness, false, "manual-attention must not grant readiness authority");

  // A readiness-proven journal with no bound outcome holds manual, granting no
  // start/readiness authority but retaining the standing capability.
  const fx3 = await fixture(t);
  await buildProductionStarted(fx3);
  await advanceCampaignRecoveryJournal(fx3.custodyLockPath, uid, "readiness-proven", deriveCampaignJournalIdentity((await inspectActiveCampaignRecoveryJournal(fx3.custodyLockPath, uid)).journal), {
    readinessEvidence: { productionStartedAt: "2026-08-14T00:00:00.000000000Z", provenAt: "2026-08-14T00:00:06.000000000Z", status: 200, latencyMilliseconds: 7, responseSha256: digest("c") },
  });
  const { deps: d3, state: s3, POST_START_AT: POST3 } = m7Deps(fx3);
  s3.running = true;
  s3.startedAt = POST3;
  s3.restartCount = 0;
  const hold = await runCampaignRecoveryGuardian(fx3.configPath, d3);
  assert.equal(hold.status, "manual-attention-campaign-no-outcome");
  assert.equal(hold.authority.grantsProductionStart, false, "no-outcome hold must not grant production-start authority");
  assert.equal(hold.authority.grantsReadiness, false, "no-outcome hold must not grant readiness authority");
  assert.equal(hold.capability.canRestartBoundProduction, true, "the no-outcome hold retains the standing M7 capability separately");

  // Results that actually authorize/performed the operation grant scoped authority.
  const fx4 = await fixture(t);
  await buildJournal(fx4, "isolation-clean");
  const { deps: d4, state: s4 } = m7Deps(fx4);
  s4.running = false;
  const auth = await runCampaignRecoveryGuardian(fx4.configPath, d4);
  assert.equal(auth.status, "campaign-recovery-production-start-authorized");
  assert.equal(auth.authority.grantsProductionStart, true, "the durable start-authorized result grants the scoped start authority");
  assert.equal(auth.authority.grantsReadiness, false, "an authorization-only result does not grant readiness authority");
  const started = await runCampaignRecoveryGuardian(fx4.configPath, d4);
  assert.equal(started.status, "campaign-recovery-production-started");
  assert.equal(started.authority.grantsProductionStart, true, "the production-started result grants the scoped start authority");
  const ready = await runCampaignRecoveryGuardian(fx4.configPath, d4);
  assert.equal(ready.status, "campaign-recovery-readiness-proven");
  assert.equal(ready.authority.grantsProductionStart, true, "readiness-proven stays within the scoped start authority");
  assert.equal(ready.authority.grantsReadiness, true, "readiness-proven grants the scoped readiness authority");
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  CampaignFailure,
  campaignArguments,
  executeCampaignChild,
  modelCampaignMaintenanceBoundaries,
  reviewModelCampaignMaintenance,
  runModelCampaignMaintenance,
  validateCampaignPairConfiguration,
  validateCampaignResult,
  validateModelCampaignMaintenanceConfiguration,
} from "../deploy/work-controller/model-campaign-maintenance.mjs";
import {
  advanceCampaignRecoveryJournal,
  createCampaignRecoveryJournal,
  deriveCampaignRecoveryJournalPath,
  deriveCampaignJournalIdentity,
  deriveCampaignJournalIdentitySha256,
  inspectActiveCampaignRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { renderCampaignGuardianUnit } from "../deploy/work-controller/maintenance-campaign-recovery-guardian-unit.mjs";
import { CAMPAIGN_RECOVERY_ENGINE_READY } from "../deploy/work-controller/maintenance-campaign-recovery-guardian.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const byteHash = (value) => createHash("sha256").update(value).digest("hex");
const TEST_OWNER_UID = process.geteuid?.() ?? 0;

async function privateWrite(path, value) {
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
  await writeFile(path, bytes, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
  return bytes;
}

async function privateDirectory(path) {
  await mkdir(path, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(path, 0o700);
}

async function fixture(t, profile = "builder", { expectedRestartCount = 0 } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-campaign-maintenance-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
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
  if (process.platform !== "win32") await Promise.all([chmod(scriptPath, 0o700), chmod(pythonPath, 0o700), chmod(dockerPath, 0o700)]);
  const materialization = {
    schemaVersion: 1,
    operation: "pixel-portal-outcome-battery-materialization",
    profile,
    evaluationRegime: "matched-budget",
    modelContractSha256: digest("3"),
    inferenceContractSha256: digest("4"),
    tasks: [
      { batteryTaskId: "battery-one", partition: "tuning", taskSha256: digest("5") },
      { batteryTaskId: "heldout-one", partition: "held-out", taskSha256: digest("6") },
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
    expectedRestartCount,
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
  return { root, configPath, configuration, production, materialization, pair, pairPath };
}

function productionContainer(value, { running = true, startedAt = value.production.expectedStartedAt, image = value.production.imageDigest, restartCount = value.production.expectedRestartCount } = {}) {
  return {
    Id: value.production.containerId,
    Name: `/${value.production.containerName}`,
    Image: image,
    RestartCount: restartCount,
    State: { Running: running, Paused: false, Restarting: false, Dead: false, StartedAt: startedAt },
  };
}

function fakeChildIdentity(ownerUid) {
  return {
    bootId: "12345678-1234-1234-1234-123456789abc",
    pid: 4242,
    startTicks: 128000,
    exe: "/usr/bin/node",
    argv: ["/usr/bin/node", "maintenance-campaign-child-supervisor.mjs", "run", "--nonce", digest("b"), "--contract", "/tmp/c", "--receipt", "/tmp/r"],
    cgroup: ["0::/system.slice/pixel-campaign-child-" + digest("b") + ".scope"],
    uid: String(ownerUid),
  };
}

// Simulate the real supervised launcher's durable journal advancement through
// campaign-child-authorized -> campaign-child-active -> comparison-cleanup-pending
// with a durable child identity, so the controller can durably bind the outcome.
async function advanceFakeCampaignChild(loaded) {
  const lockPath = loaded.configuration.custody.lockPath;
  const ownerUid = loaded.expectedOwnerUid;
  const inspected = await inspectActiveCampaignRecoveryJournal(lockPath, ownerUid);
  const identity = deriveCampaignJournalIdentity(inspected.journal);
  const nonce = digest("b");
  const child = fakeChildIdentity(ownerUid);
  await advanceCampaignRecoveryJournal(lockPath, ownerUid, "campaign-child-authorized", identity, { campaignChildNonce: nonce });
  await advanceCampaignRecoveryJournal(lockPath, ownerUid, "campaign-child-active", identity, { campaignChild: child });
  await advanceCampaignRecoveryJournal(lockPath, ownerUid, "comparison-cleanup-pending", identity, { campaignChild: child });
  return child;
}

function dependencyState(value, overrides = {}) {
  let running = true;
  let startedAt = value.production.expectedStartedAt;
  let restartCount = value.production.expectedRestartCount;
  let leaked = false;
  let competing = false;
  let leaseCalls = 0;
  let leaseAlive = true;
  let leaseFresh = true;
  let leaseFailOn = null;
  let lastLeaseOpts = null;
  const calls = [];
  const base = {
    expectedOwnerUid: TEST_OWNER_UID,
    async withCustody(_binding, operation) { calls.push("custody"); return operation(); },
    // M8 default in tests: the single guardian settles, so the controller may
    // return a truthful terminal result derived from the bound outcome.
    async waitForCampaignSettlement() { calls.push("guardian-settlement"); return { settled: true, status: null }; },
    async inspectSource() { calls.push("source"); return { commit: value.configuration.sourceCommit, clean: true }; },
    async requireCampaignLease(_path, _uid, _configSha, _opts) {
      lastLeaseOpts = _opts;
      leaseCalls += 1;
      calls.push(["guardian-lease", leaseCalls]);
      if (leaseFailOn !== null && leaseCalls >= leaseFailOn) throw new Error("campaign guardian lease process is not alive");
      if (!leaseAlive) throw new Error("campaign guardian lease process is not alive");
      if (!leaseFresh) throw new Error("campaign guardian lease is stale");
      return { pid: process.pid, unit: "pixel-campaign-maintenance-recovery.service", refreshedAt: new Date().toISOString() };
    },
    async inspectContainer(target) {
      calls.push(["inspect", target]);
      return target === value.production.containerId ? productionContainer(value, { running, startedAt, restartCount }) : null;
    },
    async inventory() {
      calls.push("inventory");
      return {
        prefixContainers: leaked ? ["pixel-outcome-leaked"] : [],
        prefixNetworks: [],
        prefixVolumes: [],
        runningGpuContainerIds: competing ? [digest("9")] : running ? [value.production.containerId] : [],
      };
    },
    async stopProduction(target) { calls.push(["stop", target]); running = false; },
    async startProduction(target) { calls.push(["start", target]); running = true; startedAt = "2026-08-13T14:00:00.000000000Z"; restartCount = 0; },
    async waitForReadiness() { calls.push("readiness"); return true; },
    async runCampaign(loaded) {
      calls.push("campaign");
      await advanceFakeCampaignChild(loaded);
      return { campaignId: "outcomebattery-" + "a".repeat(24), campaignStatus: "pass", exitCode: 0, resultSha256: digest("a"), completedPairs: 1, requiredPairs: 1 };
    },
  };
  return {
    dependencies: { ...base, ...overrides }, calls, leaseCalls: () => leaseCalls, lastLeaseOpts: () => lastLeaseOpts,
    leak() { leaked = true; }, compete() { competing = true; },
    setLeaseDead() { leaseAlive = false; },
    setLeaseStale() { leaseFresh = false; },
    setLeaseFailOn(n) { leaseFailOn = n; },
    setStopped() { running = false; },
    restartUnexpectedly() { restartCount += 1; startedAt = "2026-08-13T14:00:30.000000000Z"; },
  };
}

test("campaign maintenance review binds exact source, batch, preflight, production, and exclusive GPUs", async (t) => {
  const value = await fixture(t);
  assert.equal(validateModelCampaignMaintenanceConfiguration(value.configuration).campaign.maxPairs, 1);
  assert.equal(validateCampaignPairConfiguration(value.pair, value.configuration.candidateSourceArchiveSha256).preflightPath, value.pair.preflightPath);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  assert.equal(review.status, "confirmation-required");
  assert.equal(review.profile, "builder");
  assert.equal(review.runtimeCondition, "cold-first-request");
  assert.equal(review.changes.requiresExclusiveAcceleratorCustody, true);
  assert.equal(review.changes.acquiresExactCustodyLock, true);
  assert.equal(review.authority.grantsUnboundedExecution, false);
});

test("campaign maintenance rejects the legacy invented pair operation instead of accepting a non-schema fixture", async (t) => {
  const value = await fixture(t);
  await privateWrite(value.pairPath, { ...value.pair, operation: "pixel-portal-outcome-pair-system" });
  const state = dependencyState(value);
  await assert.rejects(reviewModelCampaignMaintenance(value.configPath, state.dependencies), /campaign pair configuration shape is invalid/u);
});

test("campaign maintenance accepts an exact Assistant materialization and matching preflight", async (t) => {
  const value = await fixture(t, "assistant"), state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  assert.equal(review.profile, "assistant");
  assert.equal(review.status, "confirmation-required");
});

test("bounded campaign stops production, runs once, binds the outcome, and hands off to the guardian", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(result.productionReady, true);
  assert.equal(result.comparisonIsolationAbsent, true);
  // Truthful post-release: the controller releases custody to the single guardian, so it never claims exclusiveAcceleratorCustody.
  assert.equal(result.exclusiveAcceleratorCustody, false);
  // M8: the controller stops production but never restarts it directly.
  assert.deepEqual(state.calls.filter((entry) => Array.isArray(entry) && ["stop", "start"].includes(entry[0])).map((entry) => entry[0]), ["stop"]);
  assert.equal(state.calls.filter((entry) => entry === "campaign").length, 1);
});

test("campaign restoration accepts Docker's planned restart-count normalization", async (t) => {
  const value = await fixture(t, "builder", { expectedRestartCount: 4 });
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(result.productionReady, true);
  // Truthful post-release: the controller releases custody to the single guardian, so it never claims exclusiveAcceleratorCustody.
  assert.equal(result.exclusiveAcceleratorCustody, false);
});

test("M8 normal completion no longer performs its own Docker restart/readiness shortcut", async (t) => {
  const value = await fixture(t, "builder", { expectedRestartCount: 4 });
  const state = dependencyState(value);
  let readinessCalls = 0;
  state.dependencies.waitForReadiness = async () => { readinessCalls += 1; return true; };
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(startCalls, 0, "the controller must not start production directly; it hands off to the single guardian");
  assert.equal(readinessCalls, 0, "the controller must not perform its own readiness probe; the guardian proves readiness");
});

test("bounded in-progress batch is restored cleanly without claiming completion", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async runCampaign(loaded) { state.calls.push("campaign"); await advanceFakeCampaignChild(loaded); return { campaignId: "outcomebattery-" + "b".repeat(24), campaignStatus: "in-progress", exitCode: 3, resultSha256: digest("b"), completedPairs: 1, requiredPairs: 8 }; },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-in-progress-production-restored");
  assert.equal(result.completedPairs, 1);
  assert.equal(result.requiredPairs, 8);
});

test("all five campaign outcome categories bind a truthful outcome and a mechanically derived terminal result", async (t) => {
  for (const [status, terminal, char] of [
    ["pass", "campaign-pass-production-restored", "a"],
    ["in-progress", "campaign-in-progress-production-restored", "b"],
    ["blocked", "campaign-blocked-production-restored", "c"],
    ["tuning-frozen", "tuning-frozen-production-restored", "d"],
  ]) {
    const value = await fixture(t);
    const state = dependencyState(value, {
      async runCampaign(loaded) { await advanceFakeCampaignChild(loaded); return { campaignId: "outcomebattery-" + char.repeat(24), campaignStatus: status, exitCode: status === "pass" || status === "tuning-frozen" ? 0 : 3, resultSha256: digest(char), completedPairs: status === "pass" ? 8 : 1, requiredPairs: status === "tuning-frozen" ? 1 : 8 }; },
    });
    const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
    const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
    assert.equal(result.status, terminal, status);
    assert.equal(result.campaignStatus, status, status);
  }
});

test("an unexpected non-CampaignFailure error holds production even when comparison isolation is clean", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, { async runCampaign() { throw new Error("synthetic campaign failure"); } });
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.productionReady, false);
  assert.equal(startCalls, 0, "an unexpected error class must never restore production");
});

test("M8 releases custody before waiting for the single guardian to settle", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  let custodyHeld = false;
  let custodyReleasedBeforeWait = null;
  const realWithCustody = state.dependencies.withCustody;
  state.dependencies.withCustody = async (_binding, operation) => {
    custodyHeld = true;
    const result = await realWithCustody(_binding, operation);
    custodyHeld = false;
    return result;
  };
  state.dependencies.waitForCampaignSettlement = async () => {
    custodyReleasedBeforeWait = custodyHeld === false;
    return { settled: true, status: null };
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(custodyReleasedBeforeWait, true, "custody must be released before the controller waits for the guardian");
});

test("M8 returns a precise nonterminal timeout result that cannot be mistaken for success when the guardian does not settle", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async waitForCampaignSettlement() { return { settled: false, status: "campaign-maintenance-handoff-timeout" }; },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-maintenance-handoff-timeout");
  assert.equal(result.productionRestored, false);
  assert.equal(result.productionReady, false);
  assert.equal(result.exclusiveAcceleratorCustody, false);
  assert.ok(!result.status.endsWith("-production-restored"), "a nonterminal timeout must never look like campaign success");
});

test("M8 returns a nonterminal result on a forged/invalid settlement receipt and never claims success from a bare receipt file", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async waitForCampaignSettlement() { return { settled: false, status: "campaign-maintenance-receipt-invalid" }; },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-maintenance-receipt-invalid");
  assert.equal(result.productionRestored, false);
  assert.equal(result.productionReady, false);
  assert.equal(result.exclusiveAcceleratorCustody, false);
  assert.ok(!result.status.endsWith("-production-restored"), "a forged/malformed receipt must never look like campaign success");
});

test("M8 controller never performs its own readiness; restart/readiness drift is the guardian's job", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  let readinessCalls = 0;
  state.dependencies.waitForReadiness = async () => { readinessCalls += 1; state.restartUnexpectedly(); return true; };
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(readinessCalls, 0, "the controller must not run its own readiness probe");
  assert.equal(startCalls, 0, "the controller must not restart production directly");
});

test("M8 controller binds the outcome and hands off even when comparison isolation is dirty (guardian enforces isolation)", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async runCampaign(loaded) {
      state.calls.push("campaign");
      state.leak();
      await advanceFakeCampaignChild(loaded);
      return { campaignId: "outcomebattery-" + "c".repeat(24), campaignStatus: "pass", exitCode: 0, resultSha256: digest("c"), completedPairs: 1, requiredPairs: 1 };
    },
  });
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(startCalls, 0, "the controller must never restart production directly");
});

test("M8 controller binds the outcome and hands off even when a competing GPU appears (guardian enforces custody)", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async runCampaign(loaded) {
      state.compete();
      await advanceFakeCampaignChild(loaded);
      return { campaignId: "outcomebattery-" + "d".repeat(24), campaignStatus: "pass", exitCode: 0, resultSha256: digest("d"), completedPairs: 1, requiredPairs: 1 };
    },
  });
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(startCalls, 0, "the controller must never restart production directly");
});

test("wrong confirmation and source drift refuse before production stop", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const state = dependencyState(value, { async stopProduction() { stopped = true; } });
  await assert.rejects(runModelCampaignMaintenance(value.configPath, digest("f"), state.dependencies), /confirmation differs/u);
  assert.equal(stopped, false);
  const dirty = dependencyState(value, { async inspectSource() { return { commit: value.configuration.sourceCommit, clean: false }; }, async stopProduction() { stopped = true; } });
  await assert.rejects(reviewModelCampaignMaintenance(value.configPath, dirty.dependencies), /exact clean reviewed commit/u);
  assert.equal(stopped, false);
});

test("campaign command is fixed and held-out cannot request a tuning freeze", async (t) => {
  const value = await fixture(t);
  assert.deepEqual(campaignArguments(value.configuration).slice(-6), ["--max-pairs", "1", "--partition", "tuning", "--runtime-condition", "cold-first-request"]);
  value.configuration.campaign = { ...value.configuration.campaign, partition: "held-out", freezeTuning: true };
  assert.throws(() => validateModelCampaignMaintenanceConfiguration(value.configuration), /cannot rewrite/u);
});

test("campaign progress cannot forge profile, condition, completion, or exit status", async (t) => {
  const value = await fixture(t);
  const loaded = { configuration: value.configuration, materialization: value.materialization };
  const progress = {
    operation: "pixel-portal-outcome-battery-campaign-progress",
    campaignId: "outcomebattery-" + "e".repeat(24),
    profile: "builder", evaluationRegime: "matched-budget", partition: "tuning",
    runtimeCondition: "cold-first-request", status: "pass", requiredPairs: 1, completedPairs: 1,
    tuningBaselineFrozen: false, tuningBaselineFreezeSha256: null,
  };
  assert.equal(validateCampaignResult(progress, 0, loaded).campaignStatus, "pass");
  for (const mutate of [
    (candidate) => { candidate.profile = "researcher"; },
    (candidate) => { candidate.runtimeCondition = "warm-neutral-probe"; },
    (candidate) => { candidate.completedPairs = 0; },
  ]) {
    const candidate = structuredClone(progress);
    mutate(candidate);
    assert.throws(() => validateCampaignResult(candidate, 0, loaded), /invalid|inconsistent/u);
  }
  assert.throws(() => validateCampaignResult(progress, 3, loaded), /inconsistent/u);
});

test("campaign result requires a pre-disclosure commitment and exact freeze binding", async (t) => {
  const value = await fixture(t);
  const freezeConfiguration = structuredClone(value.configuration);
  freezeConfiguration.campaign.freezeTuning = true;
  const freezeLoaded = { configuration: freezeConfiguration, materialization: value.materialization };
  const freeze = {
    operation: "pixel-portal-outcome-tuning-baseline-freeze",
    campaignId: "outcomebattery-" + "f".repeat(24), tuningTasks: [{}],
    heldOutTaskSetSha256: byteHash(canonical([{
      batteryTaskId: value.materialization.tasks[1].batteryTaskId,
      taskSha256: value.materialization.tasks[1].taskSha256,
      taskAdmissionSha256: value.materialization.tasks[1].taskAdmissionSha256,
      sourceSnapshotSha256: value.materialization.tasks[1].sourceSnapshotSha256,
      verifierSha256: value.materialization.tasks[1].verifierSha256,
      researchFixtureSha256: value.materialization.tasks[1].researchFixtureSha256,
    }])), heldOutTaskCount: 1, heldOutTaskBytesOpened: false,
  };
  assert.equal(validateCampaignResult(freeze, 0, freezeLoaded).campaignStatus, "tuning-frozen");
  for (const mutate of [
    (candidate) => { candidate.heldOutTaskBytesOpened = true; },
    (candidate) => { candidate.heldOutTaskCount = 0; },
    (candidate) => { candidate.heldOutTaskSetSha256 = byteHash("different-valid-commitment"); },
  ]) {
    const candidate = structuredClone(freeze);
    mutate(candidate);
    assert.throws(() => validateCampaignResult(candidate, 0, freezeLoaded), /freeze is invalid/u);
  }

  const heldOutConfiguration = structuredClone(value.configuration);
  heldOutConfiguration.campaign.partition = "held-out";
  const heldOutLoaded = { configuration: heldOutConfiguration, materialization: value.materialization };
  const progress = {
    operation: "pixel-portal-outcome-battery-campaign-progress",
    campaignId: "outcomebattery-" + "a".repeat(24), profile: "builder",
    evaluationRegime: "matched-budget", partition: "held-out", runtimeCondition: "cold-first-request",
    status: "pass", requiredPairs: 1, completedPairs: 1,
    tuningBaselineFrozen: true, tuningBaselineFreezeSha256: digest("8"),
  };
  assert.equal(validateCampaignResult(progress, 0, heldOutLoaded).campaignStatus, "pass");
  progress.tuningBaselineFreezeSha256 = null;
  assert.throws(() => validateCampaignResult(progress, 0, heldOutLoaded), /inconsistent/u);
});

function progressStdout(value, overrides = {}) {
  return JSON.stringify({
    operation: "pixel-portal-outcome-battery-campaign-progress",
    campaignId: "outcomebattery-" + "a".repeat(24),
    profile: "builder", evaluationRegime: "matched-budget", partition: "tuning",
    runtimeCondition: "cold-first-request", status: "pass",
    requiredPairs: 1, completedPairs: 1,
    tuningBaselineFrozen: false, tuningBaselineFreezeSha256: null,
    ...overrides,
  });
}

function loadedFor(value) {
  return { configuration: value.configuration, materialization: value.materialization };
}

async function runChild(loaded, run) {
  try { return await executeCampaignChild(loaded, run); }
  catch (error) {
    if (!(error instanceof CampaignFailure)) throw error;
    return { failureClass: error.failureClass, diagnosticSha256: error.diagnosticSha256 };
  }
}

test("successful campaign exposes null failure metadata and unchanged restoration fields", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(result.campaignFailureClass, null);
  assert.equal(result.campaignFailureDiagnosticSha256, null);
  assert.equal(result.campaignFailureDiagnosticAvailable, null);
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  assert.equal(result.comparisonIsolationAbsent, true);
  // Truthful post-release: the controller releases custody to the single guardian, so it never claims exclusiveAcceleratorCustody.
  assert.equal(result.exclusiveAcceleratorCustody, false);
});

test("campaign child failure classes classify deterministically from bounded stderr", async (t) => {
  const value = await fixture(t);
  const loaded = loadedFor(value);
  assert.deepEqual(await runChild(loaded, async () => { throw Object.assign(new Error("boom"), { killed: true, stderr: "timeout bytes" }); }), { failureClass: "timeout", diagnosticSha256: byteHash("timeout bytes") });
  assert.deepEqual(await runChild(loaded, async () => { throw Object.assign(new Error("boom"), { code: 2, stderr: "exit2 bytes" }); }), { failureClass: "child-exit-2", diagnosticSha256: byteHash("exit2 bytes") });
  assert.deepEqual(await runChild(loaded, async () => { throw Object.assign(new Error("boom"), { code: 1, stderr: "other bytes" }); }), { failureClass: "child-exit-other", diagnosticSha256: byteHash("other bytes") });
  assert.deepEqual(await runChild(loaded, async () => ({ stdout: "not strict json", stderr: "parse bytes" })), { failureClass: "invalid-strict-result", diagnosticSha256: byteHash("parse bytes") });
  assert.throws(() => new CampaignFailure("secret-derived-class", { stderr: "x" }), /failure class is invalid/u);
});

test("campaign child validates the strict progress result and hashes empty stderr", async (t) => {
  const value = await fixture(t);
  const loaded = loadedFor(value);
  const good = await executeCampaignChild(loaded, async () => ({ stdout: progressStdout(value), stderr: "" }));
  assert.equal(good.campaignStatus, "pass");
  assert.equal(good.exitCode, 0);
  assert.match(good.resultSha256, /^[a-f0-9]{64}$/u);
  const noStderr = await runChild(loaded, async () => { throw Object.assign(new Error("boom"), { code: 2 }); });
  assert.equal(noStderr.failureClass, "child-exit-2");
  assert.equal(noStderr.diagnosticSha256, byteHash(Buffer.alloc(0)));
  const validButInconsistent = await runChild(loaded, async () => ({ stdout: progressStdout(value, { status: "in-progress", completedPairs: 1, requiredPairs: 1 }), stderr: "x" }));
  assert.equal(validButInconsistent.failureClass, "invalid-strict-result");
});

test("campaign failure metadata never leaks raw stderr or sentinel content", async (t) => {
  const value = await fixture(t);
  const sentinel = "SECRET-RAW-STDERR-9f8d2c";
  const state = dependencyState(value, {
    async runCampaign(loaded) { await advanceFakeCampaignChild(loaded); throw new CampaignFailure("timeout", { stderr: `trace\n${sentinel}\nboom` }); },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-error-production-restored");
  assert.equal(result.campaignFailureClass, "timeout");
  assert.equal(result.campaignFailureDiagnosticSha256, byteHash(`trace\n${sentinel}\nboom`));
  assert.equal(result.campaignFailureDiagnosticAvailable, true);
  const diagnosticPath = join(
    value.root,
    `.pixel-campaign-failure-${review.maintenanceOperationSha256}-${result.campaignFailureDiagnosticSha256}.stderr`,
  );
  assert.equal(await readFile(diagnosticPath, "utf8"), `trace\n${sentinel}\nboom`);
  if (process.platform !== "win32") assert.equal((await lstat(diagnosticPath)).mode & 0o077, 0);
  const serialized = JSON.stringify(result);
  assert.equal(serialized.includes(sentinel), false);
  assert.equal(serialized.includes("trace\\n"), false);
  assert.match(result.campaignFailureDiagnosticSha256, /^[a-f0-9]{64}$/u);
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  // Truthful post-release: the controller releases custody to the single guardian, so it never claims exclusiveAcceleratorCustody.
  assert.equal(result.exclusiveAcceleratorCustody, false);
});

test("campaign diagnostic persistence accepts only an identical owner-private existing artifact", async (t) => {
  for (const [suffix, expectedAvailable] of [["", true], ["-forged", false]]) {
    const value = await fixture(t);
    const stderr = "idempotent diagnostic bytes";
    const state = dependencyState(value, {
      async runCampaign(loaded) { await advanceFakeCampaignChild(loaded); throw new CampaignFailure("child-exit-2", { stderr }); },
    });
    const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
    const diagnosticSha256 = byteHash(stderr);
    const diagnosticPath = join(
      value.root,
      `.pixel-campaign-failure-${review.maintenanceOperationSha256}-${diagnosticSha256}.stderr`,
    );
    await writeFile(diagnosticPath, `${stderr}${suffix}`, { mode: 0o600 });
    if (process.platform !== "win32") await chmod(diagnosticPath, 0o600);
    const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
    assert.equal(result.campaignFailureDiagnosticAvailable, expectedAvailable);
    assert.equal(await readFile(diagnosticPath, "utf8"), `${stderr}${suffix}`);
  }
});

test("controller error classifies without content and holds production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async runCampaign() { throw new Error("unexpected controller failure"); },
  });
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.campaignFailureClass, "controller-error");
  assert.equal(result.campaignFailureDiagnosticSha256, byteHash(Buffer.alloc(0)));
  assert.equal(result.campaignFailureDiagnosticAvailable, false);
  assert.equal(JSON.stringify(result).includes("unexpected controller failure"), false);
  assert.equal(result.productionRestored, false);
  assert.equal(result.productionReady, false);
  assert.equal(startCalls, 0, "an unexpected controller error must never restore production");
});

test("pre-existing Pixel work resources or another GPU consumer refuse review", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const resources = dependencyState(value, {
    async inventory() { return { prefixContainers: [], prefixNetworks: ["pixel-work-net-stale"], prefixVolumes: [], runningGpuContainerIds: [value.production.containerId] }; },
    async stopProduction() { stopped = true; },
  });
  await assert.rejects(reviewModelCampaignMaintenance(value.configPath, resources.dependencies), /exclusive clean comparison window/u);
  const competing = dependencyState(value, {
    async inventory() { return { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [value.production.containerId, digest("9")].sort() }; },
    async stopProduction() { stopped = true; },
  });
  await assert.rejects(reviewModelCampaignMaintenance(value.configPath, competing.dependencies), /exclusive clean comparison window/u);
  assert.equal(stopped, false);
});

// ---------------------------------------------------------------------------
// Layer 2C M4: campaign controller journal / lease / stop-authorization wiring
// ---------------------------------------------------------------------------

const GUARDIAN_MODULE_PATH = fileURLToPath(new URL("../deploy/work-controller/maintenance-campaign-recovery-guardian.mjs", import.meta.url));

test("M4 run derives the accepted campaign journal identity, requires the guardian lease twice in exact order, and advances the real journal durably before the stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.equal(state.leaseCalls(), 2, "exactly two identity-bound guardian lease checks are required");
  const relevant = state.calls.filter((entry) => entry === "custody" || (Array.isArray(entry) && ["guardian-lease", "stop"].includes(entry[0])));
  assert.deepEqual(relevant.map((entry) => Array.isArray(entry) ? entry[0] : entry), ["custody", "guardian-lease", "guardian-lease", "stop"]);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "an active campaign recovery journal must remain for recovery");
  assert.equal(active.journal.phase, "campaign-outcome-bound", "the controller must durably bind the outcome before handing off");
  const opts = state.lastLeaseOpts();
  assert.equal(opts.unit, "pixel-campaign-maintenance-recovery.service");
  assert.match(opts.campaignOperationSha256, /^[a-f0-9]{64}$/u);
  assert.equal(opts.journalIdentitySha256, deriveCampaignJournalIdentitySha256(active.journal), "the lease must bind the exact immutable journal identity");
  assert.equal(opts.expectedNodePath, await realpath(process.execPath));
  const rendered = await renderCampaignGuardianUnit({ configPath: value.configPath, nodePath: await realpath(process.execPath), guardianPath: GUARDIAN_MODULE_PATH, expectedOwnerUid: TEST_OWNER_UID });
  assert.equal(opts.expectedUnitSha256, rendered.renderSha256, "the lease must bind the exact rendered campaign guardian unit bytes");
});

test("M4 never stops before a durable prepared journal is created and the first identity-bound lease is proven", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, { async requireCampaignLease() { throw new Error("first lease not proven"); } });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /not proven/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false, "stop must never run before the first identity-bound lease is proven");
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "the inert prepared journal must be cancelled when the first lease is never proven");
});

test("loadMaintenance refuses an active campaign recovery journal before any new review or run", async (t) => {
  const value = await fixture(t);
  const lockPath = value.configuration.custody.lockPath;
  const record = {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-journal",
    operation: "pixel-work-model-campaign-maintenance",
    phase: "prepared",
    maintenanceOperationSha256: digest("a"),
    campaignOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: TEST_OWNER_UID,
    production: {
      containerName: value.production.containerName,
      containerId: value.production.containerId,
      imageDigest: value.production.imageDigest,
      expectedStartedAt: value.production.expectedStartedAt,
      expectedRestartCount: value.production.expectedRestartCount,
    },
    comparison: { materializationSha256: digest("1"), pairConfigurationSha256: digest("2"), preflightSha256: digest("3"), acceleratorStateSha256: digest("4") },
    campaignChildNonce: null,
    campaignChild: null,
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    readinessEvidence: null,
    campaignOutcome: null,
  };
  await createCampaignRecoveryJournal(lockPath, TEST_OWNER_UID, record);
  const state = dependencyState(value, { async stopProduction() { throw new Error("must never stop"); } });
  await assert.rejects(reviewModelCampaignMaintenance(value.configPath, state.dependencies), /active campaign maintenance recovery journal already exists; recovery is required/u);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, digest("f"), state.dependencies), /active campaign maintenance recovery journal already exists; recovery is required/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("a guardian lease bound to a wrong campaign operation or journal identity prevents the stop and cancels the inert prepared journal", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async requireCampaignLease() { throw new Error("campaign guardian lease is bound to a different immutable campaign journal identity"); },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /different immutable campaign journal identity/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "a wrong-identity lease before stop authorization must cancel the inert prepared journal");
});

test("a dead guardian lease on the first check cancels the inert prepared journal and never stops production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  state.setLeaseFailOn(1);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /not alive/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "first-check lease death must cancel the inert prepared journal");
});

test("a dead guardian lease on the second check retains the production-stop-authorized journal and never stops production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  state.setLeaseFailOn(2);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /not alive/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "after production-stop-authorized the journal must be retained for recovery");
  assert.equal(active.journal.phase, "production-stop-authorized");
});

test("a campaign journal deleted during the second lease seam fails the immediate same-phase reassertion and never stops", async (t) => {
  const value = await fixture(t);
  const journalPath = deriveCampaignRecoveryJournalPath(value.configuration.custody.lockPath);
  const state = dependencyState(value);
  const baseLease = state.dependencies.requireCampaignLease;
  state.dependencies.requireCampaignLease = async (...args) => {
    const result = await baseLease(...args);
    if (state.leaseCalls() === 2) await rm(journalPath);
    return result;
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /no active campaign recovery journal exists to advance/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "the deleted journal cannot be resurrected by the immediate reassertion");
});

test("an authorized journal substituted during the second lease seam with a different valid identity fails reassertion, never stops, and is not unlinked", async (t) => {
  const value = await fixture(t);
  const lockPath = value.configuration.custody.lockPath;
  const journalPath = deriveCampaignRecoveryJournalPath(lockPath);
  const state = dependencyState(value);
  const baseLease = state.dependencies.requireCampaignLease;
  state.dependencies.requireCampaignLease = async (...args) => {
    const result = await baseLease(...args);
    if (state.leaseCalls() === 2) {
      const current = JSON.parse(await readFile(journalPath, "utf8"));
      const substituted = { ...current, comparison: { ...current.comparison, materializationSha256: digest("9") } };
      await privateWrite(journalPath, substituted);
    }
    return result;
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /identity does not match the current operation/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(lockPath, TEST_OWNER_UID);
  assert.ok(active, "the substituted authorized journal must remain on disk");
  assert.equal(active.journal.comparison.materializationSha256, digest("9"));
});

test("a prepared journal substituted before the first advance is refused by identity-checked cancellation, never unlinked, and never allows a stop", async (t) => {
  const value = await fixture(t);
  const lockPath = value.configuration.custody.lockPath;
  const journalPath = deriveCampaignRecoveryJournalPath(lockPath);
  const state = dependencyState(value);
  const baseLease = state.dependencies.requireCampaignLease;
  state.dependencies.requireCampaignLease = async (...args) => {
    const result = await baseLease(...args);
    if (state.leaseCalls() === 1) {
      const current = JSON.parse(await readFile(journalPath, "utf8"));
      const substituted = { ...current, comparison: { ...current.comparison, pairConfigurationSha256: digest("9") } };
      await privateWrite(journalPath, substituted);
    }
    return result;
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /identity does not match the current operation/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(lockPath, TEST_OWNER_UID);
  assert.ok(active, "the substituted prepared journal must not be unlinked");
  assert.equal(active.journal.phase, "prepared");
  assert.equal(active.journal.comparison.pairConfigurationSha256, digest("9"));
});

test("the same-phase journal reassertion before the stop is idempotent with no phase regression or mutation on the happy path", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const advances = [];
  state.dependencies.advanceCampaignRecoveryJournal = async (lockPath, uid, phase, identity, fields) => {
    const result = await advanceCampaignRecoveryJournal(lockPath, uid, phase, identity, fields);
    advances.push({ phase, advanced: result.advanced });
    return result;
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-pass-production-restored");
  assert.deepEqual(advances.map((entry) => entry.phase), ["production-stop-authorized", "production-stop-authorized", "production-stopped", "campaign-outcome-bound"]);
  assert.equal(advances[0].advanced, true, "the first advance must move the prepared journal to production-stop-authorized");
  assert.equal(advances[1].advanced, false, "the same-phase reassertion must be idempotent and mutate nothing");
  assert.equal(advances[2].advanced, true, "the advance must move to production-stopped");
  assert.equal(advances[3].advanced, true, "the outcome-binding advance must move to campaign-outcome-bound");
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active.journal.phase, "campaign-outcome-bound");
});

test("a confirmed-stopped race before journal creation preserves the original pre-stop failure and never fabricates a journal", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  let inspections = 0;
  state.dependencies.inspectContainer = async (target) => {
    if (target !== value.production.containerId) return null;
    inspections += 1;
    if (inspections === 7) throw new Error("pre-stop validation exploded");
    if (inspections === 8) return productionContainer(value, { running: false });
    return productionContainer(value, { running: true, startedAt: value.production.expectedStartedAt });
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /pre-stop validation exploded/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "no journal may be fabricated for a pre-journal confirmed-stopped race");
});

test("materialization drift detected under the custody lock refuses before any journal creation or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  state.dependencies.withCustody = async (_binding, operation) => {
    await privateWrite(join(value.configuration.materializationRoot, "materialization.json"), { ...value.materialization, tasks: [...value.materialization.tasks, { batteryTaskId: "battery-extra", partition: "tuning", taskSha256: digest("f") }] });
    return operation();
  };
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "no journal may be created after materialization drift under the lock");
});

test("pair configuration drift detected under the custody lock refuses before any journal creation or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  state.dependencies.withCustody = async (_binding, operation) => {
    const newPair = { ...value.pair, codexRunnerImage: `sha256:${digest("e")}` };
    const newPairBytes = await privateWrite(value.pairPath, newPair);
    const preflight = JSON.parse(await readFile(value.pair.preflightPath, "utf8"));
    await privateWrite(value.pair.preflightPath, { ...preflight, configurationSha256: byteHash(newPairBytes) });
    return operation();
  };
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "no journal may be created after pair configuration drift under the lock");
});

test("preflight drift detected under the custody lock refuses before any journal creation or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  state.dependencies.withCustody = async (_binding, operation) => {
    const preflight = JSON.parse(await readFile(value.pair.preflightPath, "utf8"));
    await privateWrite(value.pair.preflightPath, { ...preflight, extraBindingMarker: true });
    return operation();
  };
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "no journal may be created after preflight drift under the lock");
});

test("raw configuration bytes drift detected under the custody lock refuses before any journal creation or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  state.dependencies.withCustody = async (_binding, operation) => {
    await privateWrite(value.configPath, { ...value.configuration, campaign: { ...value.configuration.campaign, maxPairs: 2 } });
    return operation();
  };
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /configuration raw bytes changed while acquiring custody/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "no journal may be created after under-lock raw configuration drift");
});

test("campaign script (material binding) drift detected under the custody lock refuses before any stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  state.dependencies.withCustody = async (_binding, operation) => {
    const scriptPath = join(value.root, "source", "scripts", "portal_outcome_battery_campaign.py");
    await writeFile(scriptPath, "#!/usr/bin/env python3\n# tampered\n", { mode: 0o700 });
    if (process.platform !== "win32") await chmod(scriptPath, 0o700);
    return operation();
  };
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("production fingerprint drift detected under the custody lock refuses before any stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  let inspections = 0;
  state.dependencies.inspectContainer = async (target) => {
    if (target !== value.production.containerId) return null;
    inspections += 1;
    return productionContainer(value, { startedAt: inspections >= 5 ? "2026-08-13T13:00:00.000000000Z" : value.production.expectedStartedAt });
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /start identity changed|changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("accelerator or comparison-resource (inventory) drift detected under the custody lock refuses before any stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  let inventoryCalls = 0;
  state.dependencies.inventory = async () => {
    inventoryCalls += 1;
    return inventoryCalls >= 5
      ? { prefixContainers: ["pixel-outcome-leaked"], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [value.production.containerId] }
      : { prefixContainers: [], prefixNetworks: [], prefixVolumes: [], runningGpuContainerIds: [value.production.containerId] };
  };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /exclusive clean comparison window/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("custody identity drift is refused before the stop", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  state.dependencies.withCustody = async (_binding, operation) => operation("0".repeat(64));
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /custody identity differs from the reviewed operation/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("a journal creation failure fails closed before the stop with no residual journal", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async createCampaignRecoveryJournal() { throw new Error("durable journal creation failed"); },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /durable journal creation failed/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
});

test("a journal advance failure fails closed, cancels the inert prepared journal, and never stops production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async advanceCampaignRecoveryJournal() { throw new Error("durable journal advance failed"); },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /durable journal advance failed/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "advance failure before stop authorization must cancel the inert prepared journal");
});

test("a stop throw with a confirmed stopped container retains and durably records production-stopped", async (t) => {
  const value = await fixture(t);
  let stopCalls = 0;
  const state = dependencyState(value, {
    async stopProduction() {
      stopCalls += 1;
      state.setStopped();
      throw new Error("docker stop returned an unexpected exit");
    },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(stopCalls, 1);
  assert.equal(result.status, "campaign-pass-production-restored");
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "a confirmed stopped container must retain the journal");
  assert.equal(active.journal.phase, "campaign-outcome-bound");
});

test("an ambiguous stop outcome (production later running) retains the journal and never claims no destructive action", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async stopProduction() {
      state.dependencies.inspectContainer = async (target) => target === value.production.containerId ? productionContainer(value, { running: true }) : null;
      throw new Error("stop unconfirmed");
    },
  });
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /campaign production stop outcome is unconfirmed or production restarted; campaign recovery journal retained/u);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "an ambiguous stop must retain the journal for recovery");
});

test("prepared-only cancellation uses the exact identity-checked durable primitive and retains every post-authorized journal", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  state.setLeaseDead();
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /not alive/u);
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.equal(active, null, "prepared-only journal must be cancelled");
  // A post-production-stop-authorized journal must never be cancelled.
  const second = await fixture(t);
  const secondState = dependencyState(second);
  secondState.setLeaseFailOn(2);
  const secondReview = await reviewModelCampaignMaintenance(second.configPath, secondState.dependencies);
  await assert.rejects(runModelCampaignMaintenance(second.configPath, secondReview.maintenanceOperationSha256, secondState.dependencies), /not alive/u);
  const retained = await inspectActiveCampaignRecoveryJournal(second.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(retained, "post-production-stop-authorized journal must be retained");
  assert.equal(retained.journal.phase, "production-stop-authorized");
});

test("the distinct campaign operation hash is deterministic and sensitive to argv, python path, runtime condition, and output authority", async (t) => {
  // Determinism and single-factor sensitivity must be compared on one fixture so
  // the absolute paths embedded in the exact child contract stay identical; the
  // active journal is removed between runs so each observation is a fresh review.
  const value = await fixture(t);
  const lockPath = value.configuration.custody.lockPath;
  const journalPath = deriveCampaignRecoveryJournalPath(lockPath);
  const runOnce = async () => {
    const state = dependencyState(value);
    const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
    await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
    const active = await inspectActiveCampaignRecoveryJournal(lockPath, TEST_OWNER_UID);
    assert.ok(active, "a completed M4 run must leave an active campaign recovery journal");
    const hash = active.journal.campaignOperationSha256;
    await rm(journalPath);
    return hash;
  };
  const baseline = await runOnce();
  assert.equal(await runOnce(), baseline, "unchanged inputs must produce the identical campaign operation hash");
  value.configuration.campaign = { ...value.configuration.campaign, maxPairs: 2 };
  await privateWrite(value.configPath, value.configuration);
  assert.notEqual(await runOnce(), baseline, "an argv change must change the campaign operation hash");
  value.configuration.campaign = { ...value.configuration.campaign, maxPairs: 1 };
  const alt = join(value.root, "python3-alt");
  await writeFile(alt, "fixture\n", { mode: 0o700 });
  if (process.platform !== "win32") await chmod(alt, 0o700);
  value.configuration.pythonPath = alt;
  await privateWrite(value.configPath, value.configuration);
  assert.notEqual(await runOnce(), baseline, "a python path change must change the campaign operation hash");
  value.configuration.pythonPath = join(value.root, "python3");
  value.configuration.campaign = { ...value.configuration.campaign, runtimeCondition: "warm-neutral-probe" };
  await privateWrite(value.configPath, value.configuration);
  assert.notEqual(await runOnce(), baseline, "a runtime condition change must change the campaign operation hash");
  value.configuration.campaign = { ...value.configuration.campaign, runtimeCondition: "cold-first-request" };
  value.configuration.outputRoot = join(value.root, "campaign-output-2");
  await privateWrite(value.configPath, value.configuration);
  assert.notEqual(await runOnce(), baseline, "an output authority change must change the campaign operation hash");
});

test("M4 adds no campaign operation hash to review/CLI/result and M6 flips the campaign recovery engine to ready", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  assert.equal(review.operation, "pixel-work-model-campaign-maintenance-review");
  assert.equal(Object.hasOwn(review, "campaignOperationSha256"), false, "review must not leak the distinct campaign operation hash");
  assert.equal(review.confirmation.sha256, review.maintenanceOperationSha256, "outer confirmation must remain the reviewed maintenance operation");
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(Object.hasOwn(result, "campaignOperationSha256"), false, "result must not leak the distinct campaign operation hash");
  assert.equal(CAMPAIGN_RECOVERY_ENGINE_READY, true, "M6 campaign recovery engine readiness must be true");
  const rendered = await renderCampaignGuardianUnit({ configPath: value.configPath, nodePath: await realpath(process.execPath), guardianPath: GUARDIAN_MODULE_PATH, expectedOwnerUid: TEST_OWNER_UID });
  assert.equal(state.lastLeaseOpts().expectedUnitSha256, rendered.renderSha256, "M6 campaign unit render bytes must be unchanged and exactly bound by the lease");
});

// ---------------------------------------------------------------------------
// Layer 2C M5 P0 repair: ambiguous/non-terminal child-launch failure must hold
// production (startProduction call count zero, journal retained at the truthful
// last phase, explicit manual-attention-production-held result).
// ---------------------------------------------------------------------------

const CHILD_LAUNCH_ERROR = "campaign child launch ambiguous";

function launchErrorDependencyState(value, overrides = {}) {
  const state = dependencyState(value, overrides);
  // Force the default M5 supervised launcher path to throw a
  // WorkCampaignChildLaunchError (the real launcher does this on any ambiguity).
  state.dependencies.runCampaign = async () => {
    state.calls.push("campaign");
    const { WorkCampaignChildLaunchError } = await import("../deploy/work-controller/maintenance-campaign-child-launcher.mjs");
    throw new WorkCampaignChildLaunchError(CHILD_LAUNCH_ERROR);
  };
  return state;
}

test("P0 pre-terminal child-launch failure holds production and never calls startProduction", async (t) => {
  const value = await fixture(t);
  const state = launchErrorDependencyState(value);
  let startCalls = 0;
  state.dependencies.startProduction = async (target) => { startCalls += 1; state.calls.push(["start", target]); };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.productionRestored, false);
  assert.equal(result.productionReady, false);
  assert.equal(result.campaignFailureClass, "controller-error");
  assert.equal(startCalls, 0, "startProduction must never be called after an ambiguous child launch");
  assert.equal(JSON.stringify(result).includes(CHILD_LAUNCH_ERROR), false, "no raw launch error content may leak");
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "the recovery journal must be retained for recovery");
  assert.ok(["campaign-child-authorized", "campaign-child-active", "production-stopped"].includes(active.journal.phase), `journal retained at truthful phase: ${active.journal.phase}`);
});

test("P0 ambiguous child launch never advances cleanup-pending and holds production", async (t) => {
  const value = await fixture(t);
  const state = launchErrorDependencyState(value);
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.productionRestored, false);
  assert.equal(startCalls, 0);
  const active = await inspectActiveCampaignRecoveryJournal(value.configPath ? value.configuration.custody.lockPath : "", TEST_OWNER_UID);
  assert.ok(active);
  assert.notEqual(active.journal.phase, "comparison-cleanup-pending", "cleanup-pending must never be claimed for an ambiguous launch");
});

test("P0 proven terminal classified campaign failure binds an error outcome and hands off to the guardian", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value, {
    async runCampaign(loaded) { await advanceFakeCampaignChild(loaded); throw new CampaignFailure("child-exit-2", { stderr: "exit-2-diagnostic" }); },
  });
  let startCalls = 0;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "campaign-error-production-restored");
  assert.equal(result.campaignFailureClass, "child-exit-2");
  assert.equal(result.productionRestored, true);
  // A classified error never fabricates a campaign identity/result/exit code;
  // the terminal result must be truthful and content-free.
  assert.equal(result.campaignId, null, "a classified error must never fabricate a campaign identity");
  assert.equal(result.campaignExitCode, null, "a classified error must never fabricate an exit code");
  assert.equal(result.campaignResultSha256, null, "a classified error must never fabricate a result identity");
  assert.equal(startCalls, 0, "a proven terminal classified failure is handed off to the guardian; the controller never restarts directly");
});

// ---------------------------------------------------------------------------
// P0 adversarial: a real non-launcher error class thrown by the supervised
// child launch (before the proven terminal boundary) must hold production with
// startProduction count zero and a truthful journal phase. The controller only
// restores on an explicitly classified CampaignFailure.
// ---------------------------------------------------------------------------

async function assertHoldsProduction(value, state, runCampaign, { phase } = {}) {
  let startCalls = 0;
  state.dependencies.runCampaign = runCampaign;
  state.dependencies.startProduction = async () => { startCalls += 1; };
  const review = await reviewModelCampaignMaintenance(value.configPath, state.dependencies);
  const result = await runModelCampaignMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.productionRestored, false);
  assert.equal(result.campaignFailureClass, "controller-error");
  assert.equal(startCalls, 0, "startProduction must never be called for an unexpected pre-terminal error class");
  const active = await inspectActiveCampaignRecoveryJournal(value.configuration.custody.lockPath, TEST_OWNER_UID);
  assert.ok(active, "the recovery journal must be retained");
  if (phase) assert.equal(active.journal.phase, phase, `journal must be truthful at ${phase}`);
  return result;
}

test("P0 ordinary Error from the supervised launch holds production (startProduction count zero)", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const result = await assertHoldsProduction(value, state, async () => { throw new Error("ordinary unexpected error"); });
  assert.equal(JSON.stringify(result).includes("ordinary unexpected error"), false, "no raw error content may leak");
});

test("P0 WorkMaintenanceSecureFileError from the supervised launch holds production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const { WorkMaintenanceSecureFileError } = await import("../deploy/work-controller/maintenance-secure-files.mjs");
  const result = await assertHoldsProduction(value, state, async () => { throw new WorkMaintenanceSecureFileError("artifact publication failed"); });
  assert.equal(JSON.stringify(result).includes("artifact publication failed"), false, "no raw error content may leak");
});

test("P0 supervisor validation error from the supervised launch holds production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const { WorkCampaignChildSupervisorError } = await import("../deploy/work-controller/maintenance-campaign-child-supervisor.mjs");
  const result = await assertHoldsProduction(value, state, async () => { throw new WorkCampaignChildSupervisorError("receipt contradictory"); });
  assert.equal(JSON.stringify(result).includes("receipt contradictory"), false, "no raw error content may leak");
});

test("P0 guardian/journal/systemd error from the supervised launch holds production", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const result = await assertHoldsProduction(value, state, async () => { throw new Error("systemd unit start failed"); });
  assert.equal(JSON.stringify(result).includes("systemd unit start failed"), false, "no raw error content may leak");
});

// ---------------------------------------------------------------------------
// P0: a name-spoofed ordinary Error cannot restore production. The controller's
// mechanical gate is `error instanceof CampaignFailure`; an ordinary Error whose
// `name` is set to "CampaignFailure" must NOT take the restoration path.
// ---------------------------------------------------------------------------
test("P0 name-spoofed ordinary Error cannot restore production (instanceof gate only)", async (t) => {
  const value = await fixture(t);
  const state = dependencyState(value);
  const result = await assertHoldsProduction(value, state, async () => {
    const spoofed = new Error("spoofed campaign failure content");
    spoofed.name = "CampaignFailure";
    throw spoofed;
  });
  assert.equal(result.campaignFailureClass, "controller-error", "a spoofed name must not be classified as a restorable campaign failure");
  assert.equal(JSON.stringify(result).includes("spoofed campaign failure content"), false, "no raw error content may leak");
});

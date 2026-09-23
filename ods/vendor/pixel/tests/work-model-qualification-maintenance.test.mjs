import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { acquireGuardianUnitLock, releaseGuardianUnitLock } from "./fixtures/guardian-unit-lock.mjs";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";

import {
  modelQualificationMaintenanceBoundaries,
  reviewModelQualificationMaintenance,
  runModelQualificationMaintenance,
  runModelQualificationMaintenanceCli,
  validateModelQualificationMaintenanceConfiguration,
} from "../deploy/work-controller/model-qualification-maintenance.mjs";
import {
  hasActiveRecoveryJournal,
  inspectActiveRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { deriveGuardianLeasePath } from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";

const digest = (character) => character.repeat(64);
const TEST_OWNER_UID = process.geteuid?.() ?? 0;

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t, { expectedRestartCount = 0 } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-qualification-maintenance-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const qualificationPath = join(root, "qualification-docker.json");
  const configPath = join(root, "maintenance.json");
  const custodyLockPath = join(root, "guardian.lock");
  // Fully valid transitive authority-bearing configs are required so the real
  // guardian lease proof can derive the reviewed expected concrete unit render
  // under custody (config -> docker qualification -> backend -> environment).
  const backendConfigPath = join(root, "backend.json");
  const environmentPath = join(root, "environment.json");
  const stateRoot = join(root, "state");
  const dockerQual = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json",
    schemaVersion: 1,
    backendConfigPath,
    qualificationConfigPath: join(root, "qualification-config.json"),
    runnerImageDigest: `sha256:${digest("3")}`,
    evidenceRoot: join(root, "evidence"),
    boundary: "Owner-private exact local-model qualification inputs only. Configuration grants no model start, container, network, device, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.",
  };
  const backend = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
    schemaVersion: 1,
    environmentPath,
    modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" },
    runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 },
    publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 10, probeIntervalMilliseconds: 5000 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json",
    schemaVersion: 1,
    stateRoot,
    policyPath: join(root, "policy.json"),
    objectStore: join(root, "objects"),
    workspaceRoot: join(root, "workspaces"),
    executorPath: "/opt/pixel-work/bin/omp",
    modelBackendLaunchPath: join(root, "backend-launch.json"),
    archiveLimits: { maxEntries: 10000, maxFileBytes: 1073741824 },
    runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: 10001, gid: 10001 },
    boundary: "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.",
  };
  await privateWrite(qualificationPath, dockerQual);
  await privateWrite(backendConfigPath, backend);
  await privateWrite(environmentPath, environment);
  await import("node:fs/promises").then((fs) => fs.mkdir(stateRoot, { recursive: true }));
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(custodyLockPath, 0o600);
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
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-maintenance-v1.schema.json",
    schemaVersion: 1,
    qualificationDockerConfigPath: qualificationPath,
    custody: { kind: "advisory-flock", lockPath: custodyLockPath, acquireTimeoutSeconds: 30 },
    production,
    boundary: modelQualificationMaintenanceBoundaries.configuration,
  };
  await privateWrite(configPath, configuration);
  const qualification = {
    schemaVersion: 1,
    qualificationOperationSha256: digest("3"),
    dockerPath: process.platform === "win32" ? "C:\\Program Files\\Docker\\docker.exe" : "/usr/bin/docker",
    backendContainerName: "pixel-qualification-backend",
    backendNetworkName: "pixel-qualification-network",
    qualificationContainerName: "pixel-model-qualification-333333333333",
  };
  return { root, qualificationPath, configPath, custodyLockPath, configuration, production, qualification };
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

function dependencies(value, overrides = {}) {
  let running = true;
  let startedAt = value.production.expectedStartedAt;
  let restartCount = value.production.expectedRestartCount;
  let backendRemaining = false;
  let leaseAlive = true;
  let leaseFresh = true;
  let leaseConfigOk = true;
  let leaseFailOn = null;
  let leaseCalls = 0;
  const calls = [];
  const base = {
    expectedOwnerUid: TEST_OWNER_UID,
    async withCustody(_binding, operation) { calls.push("custody"); return operation(); },
    async requireGuardianLease(_path, _uid, _configSha, _opts) {
      leaseCalls += 1;
      calls.push(["guardian-lease", leaseCalls]);
      if (leaseFailOn !== null && leaseCalls >= leaseFailOn) throw new Error("guardian lease process is not alive");
      if (!leaseAlive) throw new Error("guardian lease process is not alive");
      if (!leaseFresh) throw new Error("guardian lease is stale");
      if (!leaseConfigOk) throw new Error("guardian lease is bound to a different maintenance configuration");
      return { pid: process.pid, unit: "pixel-maintenance-recovery.service", refreshedAt: new Date().toISOString() };
    },
    async prepareQualification() { calls.push("prepare"); return value.qualification; },
    async inspectContainer(target) {
      calls.push(["inspect-container", target]);
      if (target === value.production.containerId) return productionContainer(value, { running, startedAt, restartCount });
      if (backendRemaining && target === value.qualification.backendContainerName) return { Id: digest("4") };
      return null;
    },
    async inspectNetwork(target) { calls.push(["inspect-network", target]); return null; },
    async stopProduction(target) { calls.push(["stop", target]); running = false; },
    async startProduction(target) { calls.push(["start", target]); running = true; startedAt = "2026-08-13T14:00:00.000000000Z"; restartCount = 0; },
    async runQualification(_path, confirmation, options) {
      assert.equal(options.reviewedMaintenancePreparation, value.qualification);
      calls.push(["qualify", confirmation]);
      return { status: "qualified", receiptSha256: digest("5") };
    },
    async waitForReadiness() { calls.push("readiness"); return true; },
  };
  const merged = { ...base, ...overrides };
  return {
    dependencies: merged,
    calls,
    setLeaseDead() { leaseAlive = false; },
    setLeaseStale() { leaseFresh = false; },
    setLeaseWrongConfig() { leaseConfigOk = false; },
    setLeaseFailOn(n) { leaseFailOn = n; },
    setBackendRemaining(value_) { backendRemaining = value_; },
    restartProduction() {
      running = true;
      startedAt = "2026-08-13T13:59:54.000000000Z";
    },
    restartUnexpectedly() {
      restartCount += 1;
      startedAt = "2026-08-13T14:00:30.000000000Z";
    },
  };
}

test("maintenance configuration and review bind one exact running production container", async (t) => {
  const value = await fixture(t);
  assert.equal(validateModelQualificationMaintenanceConfiguration(value.configuration).production.readinessOrigin, "http://127.0.0.1:8000");
  const state = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  assert.equal(review.status, "confirmation-required");
  assert.equal(review.qualificationOperationSha256, value.qualification.qualificationOperationSha256);
  assert.equal(review.changes.usesExternalNetwork, false);
  assert.equal(review.changes.acquiresExactCustodyLock, true);
  assert.equal(review.authority.grantsProductionMutation, false);
});

test("qualified maintenance stops, qualifies, proves isolation absent, restores, and probes", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualified-production-restored");
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  assert.equal(result.qualificationIsolationAbsent, true);
  assert.equal(result.qualificationReceiptSha256, digest("5"));
  assert.equal(result.qualificationFailureStage, "none");
  assert.equal(result.qualificationDiagnosticAvailable, false);
  assert.equal(result.qualificationDiagnosticSha256, null);
  assert.equal(result.productionCustodyMaintained, true);
  assert.deepEqual(state.calls.filter((entry) => Array.isArray(entry) && ["stop", "qualify", "start"].includes(entry[0])).map((entry) => entry[0]), ["stop", "qualify", "start"]);
});

test("a planned Docker stop-start may normalize a prior restart count without losing custody", async (t) => {
  const value = await fixture(t, { expectedRestartCount: 3 });
  const state = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualified-production-restored");
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  assert.equal(result.productionCustodyMaintained, true);
});

test("an unexpected restart after planned restart-count normalization still invalidates custody", async (t) => {
  const value = await fixture(t, { expectedRestartCount: 3 });
  const state = dependencies(value);
  state.dependencies.waitForReadiness = async () => { state.restartUnexpectedly(); return true; };
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-custody-changed");
  assert.equal(result.productionCustodyMaintained, false);
});

test("qualification error still restores production after exact isolation cleanup", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value, { async runQualification() { state.calls.push("qualification-error"); throw new Error("synthetic failure"); } });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualification-error-production-restored");
  assert.equal(result.qualificationStatus, "error");
  assert.equal(result.qualificationFailureStage, "qualification-preflight");
  assert.equal(result.qualificationReceiptSha256, null);
  assert.equal(result.productionReady, true);
});

test("an unexpected restart during readiness invalidates restored production custody", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.dependencies.waitForReadiness = async () => { state.restartUnexpectedly(); return true; };
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-custody-changed");
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  assert.equal(result.productionCustodyMaintained, false);
  assert.equal(result.qualificationFailureStage, "production-custody");
});

test("maintenance reports a retained private backend-start diagnostic without exposing its content", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value, {
    async runQualification() {
      const error = new Error("private backend startup failure");
      error.qualificationFailureStage = "backend-start";
      error.qualificationDiagnosticSha256 = digest("7");
      throw error;
    },
  });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualification-error-production-restored");
  assert.equal(result.qualificationFailureStage, "backend-start");
  assert.equal(result.qualificationDiagnosticAvailable, true);
  assert.equal(result.qualificationDiagnosticSha256, digest("7"));
  assert.doesNotMatch(JSON.stringify(result), /private backend startup failure/u);
});

test("remaining qualification isolation holds production instead of creating a GPU collision", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setBackendRemaining(true);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(result.productionRestored, false);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
});

test("guardian restart with residual isolation is reported truthfully and never as production held", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setBackendRemaining(true);
  state.dependencies.runQualification = async (_path, _confirmation, options) => {
    state.restartProduction();
    await options.preStartGuard();
    return { status: "qualified", receiptSha256: digest("5") };
  };
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-isolation-present-production-running");
  assert.equal(result.qualificationStatus, "error");
  assert.equal(result.qualificationFailureStage, "production-custody");
  assert.equal(result.qualificationReceiptSha256, null);
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, false);
  assert.equal(result.qualificationIsolationAbsent, false);
  assert.equal(result.productionCustodyMaintained, false);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
});

test("production identity drift refuses maintenance before any stop", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const state = dependencies(value, {
    async inspectContainer(target) {
      if (target === value.production.containerId) return productionContainer(value, { image: `sha256:${digest("9")}` });
      return null;
    },
    async stopProduction() { stopped = true; },
  });
  await assert.rejects(reviewModelQualificationMaintenance(value.configPath, state.dependencies), /differs from the exact maintenance binding/u);
  assert.equal(stopped, false);
});

test("wrong confirmation refuses maintenance before any stop", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const state = dependencies(value, { async stopProduction() { stopped = true; } });
  await assert.rejects(runModelQualificationMaintenance(value.configPath, digest("8"), state.dependencies), /confirmation differs/u);
  assert.equal(stopped, false);
});

test("production restart drift at the final pre-stop check refuses mutation", async (t) => {
  const value = await fixture(t);
  const stable = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, stable.dependencies);
  let inspections = 0;
  let stopped = false;
  const runState = dependencies(value, {
    async inspectContainer(target) {
      if (target !== value.production.containerId) return null;
      inspections += 1;
      return productionContainer(value, { startedAt: inspections >= 3 ? "2026-08-13T13:00:00.000000000Z" : value.production.expectedStartedAt });
    },
    async stopProduction() { stopped = true; },
  });
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, runState.dependencies), /start identity changed/u);
  assert.equal(stopped, false);
});


test("an unexpected exception after production stops still attempts exact isolation inspection and restores only when isolation is absent", async (t) => {
  const value = await fixture(t);
  let networkCalls = 0;
  const state = dependencies(value, {
    async inspectNetwork(target) {
      networkCalls += 1;
      if (networkCalls === 1) throw new Error("transient isolation inspection failure");
      return null;
    },
  });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /transient isolation inspection failure/u);
  assert.equal(networkCalls >= 2, true, "exact isolation inspection must be re-attempted after the unexpected error");
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), true, "production must be restored only when isolation is proven absent");
});

test("an unexpected exception with residual isolation holds production and never restarts", async (t) => {
  const value = await fixture(t);
  let networkCalls = 0;
  const state = dependencies(value, {
    async inspectNetwork(target) {
      networkCalls += 1;
      if (networkCalls === 1) throw new Error("transient isolation inspection failure");
      return null;
    },
  });
  state.setBackendRemaining(true);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /transient isolation inspection failure/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "must never restart production while any bound qualification backend or network remains");
});

test("a catchable termination signal is latched and cleanup completes before a nonzero exit", async (t) => {
  const value = await fixture(t);
  const originalExitCode = process.exitCode;
  const state = dependencies(value, {
    async runQualification(path, confirmation, options) {
      process.emit("SIGINT");
      state.calls.push(["qualify-signal", confirmation]);
      return { status: "qualified", receiptSha256: digest("5") };
    },
  });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  let outcome;
  try {
    outcome = await runModelQualificationMaintenanceCli(["run", "--config", value.configPath, "--confirm-maintenance-operation-sha256", review.maintenanceOperationSha256], state.dependencies);
  } finally {
    process.exitCode = originalExitCode;
  }
  assert.equal(outcome.terminationRequested, true, "first signal must latch termination intent");
  assert.equal(outcome.exitCode, 1, "CLI must exit nonzero after cleanup on termination");
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), true, "the operation's own cleanup/restoration must complete before the nonzero exit");
});

test("imported-module tests leave process signal listeners unchanged", async (t) => {
  const value = await fixture(t);
  const signals = ["SIGINT", "SIGTERM", "SIGHUP"];
  const before = signals.map((signal) => process.listenerCount(signal));
  const state = dependencies(value);
  await runModelQualificationMaintenanceCli(["review", "--config", value.configPath], state.dependencies);
  const after = signals.map((signal) => process.listenerCount(signal));
  assert.deepEqual(after, before, "CLI signal handlers must be removed after the CLI settles");
});

test("normal pass, qualification-error, and manual-attention semantics remain unchanged", async (t) => {
  const value = await fixture(t);
  const passState = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, passState.dependencies);
  const pass = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, passState.dependencies);
  assert.equal(pass.status, "qualified-production-restored");
  assert.equal(pass.productionRestored, true);
  assert.equal(pass.productionReady, true);

  const errorState = dependencies(value, { async runQualification() { throw new Error("synthetic qualification failure"); } });
  const error = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, errorState.dependencies);
  assert.equal(error.status, "qualification-error-production-restored");
  assert.equal(error.productionRestored, true);

  const heldState = dependencies(value);
  heldState.setBackendRemaining(true);
  const held = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, heldState.dependencies);
  assert.equal(held.status, "manual-attention-production-held");
  assert.equal(held.productionRestored, false);
  assert.equal(heldState.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
});

test("a completed maintenance run creates, advances, and settles the recovery journal", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualified-production-restored");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false, "a settled operation must leave no active journal");
});

test("manual-attention with production held retains an active recovery journal at restore-pending", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setBackendRemaining(true);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "manual-attention-production-held");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), true, "unsettled manual-attention must retain active recovery state");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID);
  assert.equal(inspected.journal.phase, "restore-pending");
});

test("an active nonterminal journal fails closed for a new review and run", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setBackendRemaining(true);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), true);
  await assert.rejects(reviewModelQualificationMaintenance(value.configPath, state.dependencies), /recovery is required/u);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /recovery is required/u);
});

test("guardian lease is absent: the supported run fails closed before any journal or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value, { requireGuardianLease: undefined });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /guardian lease/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false, "production must never be stopped without a live guardian lease");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false, "no journal may be created without a live guardian lease");
});

test("a dead guardian lease fails closed before production is stopped", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setLeaseDead();
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /guardian lease process is not alive/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false);
});

test("a stale guardian lease fails closed before production is stopped", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setLeaseStale();
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /guardian lease is stale/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false);
});

test("a substituted wrong-config guardian lease fails closed before production is stopped", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.setLeaseWrongConfig();
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /bound to a different maintenance configuration/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false);
});

test("a race-to-death guardian lease fails closed immediately before the stop", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  // The guardian is alive for the first check (before journal creation) but
  // dies before the second check (immediately before the stop).
  state.setLeaseFailOn(2);
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /guardian lease process is not alive/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false, "production must not be stopped when the guardian dies before the stop");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false, "the journal must not be left active on a failed guardian proof");
});

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const GUARDIAN_MODULE = join(ROOT, "deploy/work-controller/maintenance-recovery-guardian.mjs");

const GUARDIAN_SYSTEMD_UNIT = "pixel-maintenance-recovery.service";
const GUARDIAN_SYSTEMD_DIR = join(homedir(), ".config/systemd/user");
function userSystemdAvailable() {
  if (process.platform !== "linux") return false;
  try { execFileSync("systemctl", ["--user", "is-system-running"], { stdio: "ignore", timeout: 10000 }); return true; }
  catch { return false; }
}
const hasUserSystemd = userSystemdAvailable();
async function cleanupThrowawayGuardianUnit() {
  try {
    await import("node:fs/promises").then((fs) => execFileSync("systemctl", ["--user", "stop", GUARDIAN_SYSTEMD_UNIT], { stdio: "ignore", timeout: 20000 }));
  } catch {}
  await import("node:fs/promises").then((fs) => rm(join(GUARDIAN_SYSTEMD_DIR, GUARDIAN_SYSTEMD_UNIT), { force: true })).catch(() => {});
  try { execFileSync("systemctl", ["--user", "daemon-reload"], { stdio: "ignore", timeout: 20000 }); } catch {}
}

async function startRealGuardian(t, configPath, custodyLockPath) {
  if (!hasUserSystemd) return null;
  await acquireGuardianUnitLock();
  try {
    await cleanupThrowawayGuardianUnit();
    const { renderGuardianUnit } = await import("../deploy/work-controller/maintenance-recovery-guardian-unit.mjs");
    const { realpathSync } = await import("node:fs");
    const rendered = await renderGuardianUnit({ configPath, nodePath: realpathSync(process.execPath), guardianPath: GUARDIAN_MODULE, expectedOwnerUid: TEST_OWNER_UID });
    await writeFile(join(GUARDIAN_SYSTEMD_DIR, GUARDIAN_SYSTEMD_UNIT), rendered.unit, { mode: 0o600 });
    execFileSync("systemctl", ["--user", "daemon-reload"], { stdio: "ignore", timeout: 20000 });
    execFileSync("systemctl", ["--user", "start", GUARDIAN_SYSTEMD_UNIT], { stdio: "ignore", timeout: 30000 });
  } catch (error) { await releaseGuardianUnitLock(); throw error; }
  t.after(async () => { try { await cleanupThrowawayGuardianUnit(); } finally { await releaseGuardianUnitLock(); } });
  const leasePath = deriveGuardianLeasePath(custodyLockPath);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      const lease = JSON.parse(await readFile(leasePath, "utf8"));
      if (lease.ready === true) return true;
    } catch {}
    await delay(50);
  }
  throw new Error("real guardian did not publish a ready lease under user systemd");
}

test("the supported run path uses the real guardian lease proof and under-lock reread (no injected lease shortcut)", { skip: process.platform !== "linux" || !hasUserSystemd ? "real user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const value = await fixture(t);
  // The real guardian watcher process runs under a live user systemd unit and
  // publishes its own lease from its live /proc + systemd invocation identity
  // for the exact custody lock. The maintenance run must accept it through the
  // real default requireLiveGuardianLease path, with no injected lease stub,
  // and must reread config under the lock before any mutation.
  await startRealGuardian(t, value.configPath, value.custodyLockPath);
  const state = dependencies(value, { requireGuardianLease: undefined });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  const result = await runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies);
  assert.equal(result.status, "qualified-production-restored");
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), true, "production must be stopped under a real live guardian lease");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false, "a settled operation must leave no active journal");
});

test("qualification binding mutated under the lock refuses before any journal or stop", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value, { requireGuardianLease: undefined });
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  // prepareMaintenance is invoked once for the pre-lock review binding and again
  // under the lock. Force the under-lock re-preparation to diverge so the exact
  // qualification identity comparison refuses before any journal or Docker stop.
  let prepares = 0;
  state.dependencies.prepareQualification = async () => {
    prepares += 1;
    if (prepares === 2) return { ...value.qualification, qualificationOperationSha256: digest("9") };
    return value.qualification;
  };
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /qualification operation identity changed under the lock|qualification .* changed under the lock/u);
  assert.equal(state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop"), false, "no stop may commit when the qualification binding mutated under the lock");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), false, "no journal may be created when the qualification binding mutated under the lock");
});

test("a stop whose immediate inspection fails retains the journal and never infers no destructive action", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.dependencies.stopProduction = async (target) => {
    state.calls.push(["stop", target]);
    // The stop commits, but the immediate post-stop inspection fails (docker
    // daemon hiccup). This must never be treated as "no destructive action".
    state.dependencies.inspectContainer = async () => { throw new Error("inspect failed"); };
  };
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /unconfirmed|restarted/u);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), true, "an unconfirmed stop must retain the journal as honest mutation state");
});

test("a production that restarts after the stop retains the journal and never infers no destructive action", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  state.dependencies.stopProduction = async (target) => {
    state.calls.push(["stop", target]);
    state.restartUnexpectedly(); // an external restart policy brings it back up
  };
  const review = await reviewModelQualificationMaintenance(value.configPath, state.dependencies);
  await assert.rejects(runModelQualificationMaintenance(value.configPath, review.maintenanceOperationSha256, state.dependencies), /unconfirmed|restarted/u);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, TEST_OWNER_UID), true, "a restarted stop must never be inferred as no destructive action");
});

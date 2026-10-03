import assert from "node:assert/strict";
import http from "node:http";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";

import { modelQualificationMaintenanceBoundaries } from "../deploy/work-controller/model-qualification-maintenance.mjs";
import { dockerQualificationOperationSha256, modelQualificationDockerBoundaries } from "../deploy/work-controller/model-qualification-docker.mjs";
import {
  RECOVERY_PHASES,
  advanceRecoveryJournal,
  createRecoveryJournal,
  deriveRecoveryReceiptPath,
  hasActiveRecoveryJournal,
  inspectActiveRecoveryJournal,
  settleRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import {
  runMaintenanceRecoveryGuardian,
  guardianActions,
  watchMaintenanceRecoveryGuardian,
  maintenanceRecoveryGuardianBoundaries,
  maintenanceRecoveryGuardianStatuses,
  buildRecoveryQualification,
  defaultWaitForTrigger,
} from "../deploy/work-controller/maintenance-recovery-guardian.mjs";
import { DOCKER_SOCKET_PATH, secureSocketIdentity } from "../deploy/work-controller/docker-engine-start.mjs";
import { reviewModelQualificationMaintenance } from "../deploy/work-controller/model-qualification-maintenance.mjs";

const digest = (character) => character.repeat(64);
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const ENDPOINT_ID = digest("e");
const OWNER_UID = typeof process.geteuid === "function" ? process.geteuid() : 1000;

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-maintenance-recovery-guardian-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const qualificationPath = join(root, "qualification-docker.json");
  const configPath = join(root, "maintenance.json");
  const custodyLockPath = join(root, "guardian.lock");
  const backendConfigPath = join(root, "backend.json");
  // Fully valid transitive authority-bearing configs are required so the real
  // guardian watch proof can derive the reviewed expected concrete unit render
  // under custody (config -> docker qualification -> backend -> environment).
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
    expectedRestartCount: 0,
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
    dockerPath: "/usr/bin/docker",
    backendContainerName: "pixel-qualification-backend",
    backendNetworkName: "pixel-qualification-network",
    qualificationContainerName: `pixel-model-qualification-${digest("3").slice(0, 12)}`,
    runnerImageDigest: `sha256:${digest("8")}`,
  };
  const prepared = { launchBundleSha256: digest("9") };
  const configSha = sha(await readFile(configPath));
  return { root, qualificationPath, configPath, custodyLockPath, configuration, production, qualification, prepared, backendConfigPath, configSha };
}

function journalRecord(value, configSha, overrides = {}) {
  return {
    schemaVersion: 1,
    kind: "pixel-maintenance-recovery-journal",
    operation: "pixel-work-model-qualification-maintenance",
    maintenanceOperationSha256: digest("a"),
    qualificationOperationSha256: value.qualification.qualificationOperationSha256,
    custodyIdentitySha256: digest("c"),
    configurationSha256: configSha,
    ownerUid: OWNER_UID,
    production: {
      containerName: value.configuration.production.containerName,
      containerId: value.configuration.production.containerId,
      imageDigest: value.configuration.production.imageDigest,
      expectedStartedAt: value.configuration.production.expectedStartedAt,
      expectedRestartCount: value.configuration.production.expectedRestartCount,
    },
    qualification: {
      backendContainerName: value.qualification.backendContainerName,
      backendNetworkName: value.qualification.backendNetworkName,
      qualificationContainerName: value.qualification.qualificationContainerName,
    },
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    ...overrides,
  };
}

function journalIdentity(value, configSha) {
  const record = journalRecord(value, configSha);
  return {
    maintenanceOperationSha256: record.maintenanceOperationSha256,
    qualificationOperationSha256: record.qualificationOperationSha256,
    custodyIdentitySha256: record.custodyIdentitySha256,
    configurationSha256: record.configurationSha256,
    ownerUid: record.ownerUid,
    production: record.production,
    qualification: record.qualification,
  };
}

async function seedJournal(value, phase) {
  const record = journalRecord(value, value.configSha);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = journalIdentity(value, value.configSha);
  const order = Object.keys(RECOVERY_PHASES);
  const nonce = digest("b");
  for (const target of order.slice(1, order.indexOf(phase) + 1)) {
    if (target === "production-start-authorized") {
      await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, target, identity, { guardianStartNonce: nonce, guardianStartEndpointIdentity: ENDPOINT_ID });
    } else if (target === "production-started") {
      const startedAt = "2026-08-13T14:00:00.000000000Z";
      await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, target, identity, {
        guardianStartedAt: startedAt,
        guardianStartReceipt: { nonce, containerId: value.configuration.production.containerId, endpointIdentity: ENDPOINT_ID, status: 204, startedAt },
      });
    } else {
      await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, target, identity);
    }
  }
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

function runnerContainer(value, { label = value.qualification.qualificationOperationSha256, image = value.qualification.runnerImageDigest, networkMode = value.qualification.backendNetworkName } = {}) {
  return {
    Id: digest("4"),
    Name: `/${value.qualification.qualificationContainerName}`,
    Image: image,
    Config: { Labels: { "com.osmantic.pixel.work-model-qualification.operation-sha256": label } },
    HostConfig: { NetworkMode: networkMode },
  };
}

function dependencies(value, overrides = {}) {
  const state = {
    prodRunning: false,
    prodMissing: false,
    prodStartedAt: value.production.expectedStartedAt,
    prodRestartCount: value.production.expectedRestartCount,
    runner: null,
    backend: null,
    network: null,
    calls: [],
  };
  const base = {
    expectedOwnerUid: OWNER_UID,
    async withCustody(_binding, _uid, operation) { state.calls.push("custody"); return operation(digest("c")); },
    async prepareQualificationForRecovery() { state.calls.push("prepare"); return buildRecoveryQualification(value.qualification, value.prepared, value.backendConfigPath); },
    async inspectContainer(target) {
      state.calls.push(["inspect-container", target]);
      if (target === value.production.containerId) return state.prodMissing ? null : productionContainer(value, { running: state.prodRunning, startedAt: state.prodStartedAt, restartCount: state.prodRestartCount });
      if (state.runner && target === value.qualification.qualificationContainerName) return state.runner;
      if (state.backend && target === value.qualification.backendContainerName) return state.backend;
      return null;
    },
    async inspectNetwork(target) {
      state.calls.push(["inspect-network", target]);
      return state.network ? state.network : null;
    },
    async removeContainer(target) {
      state.calls.push(["rm-container", target]);
      if (state.runner && (target === value.qualification.qualificationContainerName || target === state.runner.Id)) state.runner = null;
      if (state.backend && (target === value.qualification.backendContainerName || target === state.backend.Id)) state.backend = null;
    },
    async removeNetwork(name) {
      state.calls.push(["rm-network", name]);
      state.network = null;
    },
    async startProduction(target, options = {}) {
      state.calls.push(["start", target]);
      state.prodRunning = true;
      state.prodStartedAt = "2026-08-13T14:00:00.000000000Z";
      state.prodRestartCount = 0;
      return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: target, endpointIdentity: options.reviewedIdentity ?? ENDPOINT_ID };
    },
    async secureSocketIdentity() { state.calls.push("secure-socket-identity"); return ENDPOINT_ID; },
    async stopModelBackend() {
      state.calls.push(["stop-model-backend"]);
      const backend = state.backend;
      const network = state.network;
      const exactBackend = !backend || (backend.Id === digest("5") && backend.Name === `/${value.qualification.backendContainerName}`);
      const exactNetwork = !network || (network.Id === digest("6") && network.Name === value.qualification.backendNetworkName);
      if (!exactBackend || !exactNetwork) throw new Error("foreign qualification backend/network");
      if (backend) state.backend = null;
      if (network) state.network = null;
      return { state: "removed" };
    },
    async waitForReadiness() { state.calls.push("readiness"); return true; },
  };
  const merged = { ...base, ...overrides };
  return {
    state,
    dependencies: merged,
    setRunner(overridesRunner = {}) { state.runner = runnerContainer(value, overridesRunner); },
    setBackend(overridesBackend = {}) { state.backend = { Id: digest("5"), Name: `/${value.qualification.backendContainerName}`, ...overridesBackend }; },
    setNetwork(overridesNetwork = {}) { state.network = { Id: digest("6"), Name: value.qualification.backendNetworkName, ...overridesNetwork }; },
  };
}

test("no active journal returns no-active-recovery without destructive action", async (t) => {
  const value = await fixture(t);
  const state = dependencies(value);
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "no-active-recovery");
  assert.equal(result.manualAttentionRequired, false);
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && ["rm-container", "rm-network", "start"].includes(entry[0])), false);
});

test("SIGKILL-equivalent abandoned phase restores exact production and settles", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "production-stopped");
  const state = dependencies(value);
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, true);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false, "a settled guardian must clear the active journal");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), true, "guardian must start the exact stopped production container");
});

test("SIGKILL during qualification removes only exact-bound runner/backend/network then settles", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  const state = dependencies(value);
  state.setRunner();
  state.setBackend();
  state.setNetwork();
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(result.qualificationIsolationAbsent, true);
  assert.equal(state.state.runner, null);
  assert.equal(state.state.backend, null);
  assert.equal(state.state.network, null);
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "stop-model-backend"), true, "guardian must reuse the exact backend lifecycle cleanup");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("a substituted qualification runner with a mismatched label is left untouched with manual attention", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  const state = dependencies(value);
  state.setRunner({ label: digest("f") });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-substituted-resource");
  assert.equal(result.manualAttentionRequired, true);
  assert.notEqual(state.state.runner, null, "substituted runner must never be removed");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "manual-attention must retain the active journal");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "rm-container"), false);
});

test("a same-name foreign qualification backend is NEVER removed", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  const state = dependencies(value);
  // Same exact name but a foreign identity (different Id).
  state.setBackend({ Id: digest("aa") });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.notEqual(state.state.backend, null, "a foreign same-name backend must never be removed");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "manual attention must retain the journal");
  assert.equal(result.manualAttentionRequired, true);
});

test("a same-name foreign qualification network is NEVER removed", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  const state = dependencies(value);
  // Same exact name but a foreign identity (different Id).
  state.setNetwork({ Id: digest("bb") });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.notEqual(state.state.network, null, "a foreign same-name network must never be removed");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "manual attention must retain the journal");
  assert.equal(result.manualAttentionRequired, true);
});

test("a substituted production container is left untouched with manual attention", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value);
  state.state.prodRunning = true;
  state.state.prodStartedAt = value.production.expectedStartedAt;
  state.state.prodRestartCount = 7; // ambiguous restart count not matching the bound value
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("an unrelated production restart with no guardian authorization is manual-attention (no false custody)", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value);
  // Production is running with a changed StartedAt (unrelated restart) and no
  // guardian authorization phase has been recorded.
  state.state.prodRunning = true;
  state.state.prodStartedAt = "2026-08-13T13:00:00.000000000Z";
  state.state.prodRestartCount = 0;
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  assert.equal(result.manualAttentionRequired, true);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "an unrelated restart must not be claimed as guardian custody");
});

test("a pre-maintenance fingerprint still running is safe and settles", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value);
  state.state.prodRunning = true;
  state.state.prodStartedAt = value.production.expectedStartedAt;
  state.state.prodRestartCount = value.production.expectedRestartCount;
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("a missing exact production container is reported manual-attention and never synthesized", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value);
  state.state.prodMissing = true;
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-missing");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("a reformatted config (same parsed value, different bytes) is rejected as config-changed", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const parsed = JSON.parse(await readFile(value.configPath, "utf8"));
  await writeFile(value.configPath, JSON.stringify(parsed, null, 4) + "\n", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(value.configPath, 0o600);
  const state = dependencies(value);
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-config-changed");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("readiness failure retains the journal as manual-attention and holds production", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value, { async waitForReadiness() { state.state.calls.push("readiness"); return false; } });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-not-ready");
  assert.equal(result.productionRestored, true);
  assert.equal(result.productionReady, false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("crash during recovery retains the journal and guardian restart converges and settles", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  const crashed = dependencies(value);
  crashed.setRunner();
  crashed.setBackend();
  crashed.setNetwork();
  crashed.dependencies.removeContainer = async (target) => {
    crashed.state.calls.push(["rm-container", target]);
    if (crashed.state.runner && (target === value.qualification.qualificationContainerName || target === crashed.state.runner.Id)) crashed.state.runner = null;
    throw new Error("SIGKILL-equivalent crash during recovery");
  };
  const first = await runMaintenanceRecoveryGuardian(value.configPath, crashed.dependencies);
  assert.equal(first.status, "manual-attention-recovery-error");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "a crash during recovery must retain the active journal");
  assert.equal(crashed.state.runner, null, "the exact-bound runner removal may have committed before the crash");

  const restarted = dependencies(value);
  restarted.setBackend();
  restarted.setNetwork();
  const second = await runMaintenanceRecoveryGuardian(value.configPath, restarted.dependencies);
  assert.equal(second.status, "guardian-production-restored");
  assert.equal(restarted.state.backend, null);
  assert.equal(restarted.state.network, null);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false, "guardian restart must converge and settle");
});

test("crash after production-start authorization before start is idempotent and not a second effect", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  // First pass advances to production-start-authorized then the start crashes.
  const firstDeps = dependencies(value);
  firstDeps.dependencies.startProduction = async (target) => {
    firstDeps.state.calls.push(["start", target]);
    throw new Error("SIGKILL after authorization before start");
  };
  const secondDeps = dependencies(value);
  secondDeps.dependencies.startProduction = async (target) => {
    secondDeps.state.calls.push(["start", target]);
    secondDeps.state.prodRunning = true;
    secondDeps.state.prodStartedAt = "2026-08-13T14:00:00.000000000Z";
    secondDeps.state.prodRestartCount = 0;
    return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: target, endpointIdentity: ENDPOINT_ID };
  };
  const first = await runMaintenanceRecoveryGuardian(value.configPath, firstDeps.dependencies);
  assert.equal(first.status, "manual-attention-recovery-production-not-ready");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.phase, "production-start-authorized", "authorization must be durably recorded before start");
  // Second pass: authorization already durable, so the guardian must NOT
  // re-authorize; it just completes the exact start.
  const second = await runMaintenanceRecoveryGuardian(value.configPath, secondDeps.dependencies);
  assert.equal(second.status, "guardian-production-restored");
  assert.equal(secondDeps.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), true);
  const finalized = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(finalized, null, "a completed guardian recovery must settle the journal");
});

test("an external start between authorization and the guardian start holds manual attention", async (t) => {
  const value = await fixture(t);
  // Production is durably authorized (production-start-authorized) but the
  // guardian never completed and recorded its own start (production-started was
  // never advanced). Production is now running with a changed StartedAt: that is
  // an external start that happened after authorization and before the guardian
  // inspection. It must hold manual attention, never be accepted as the
  // guardian's own start merely because the phase number is high enough.
  const record = journalRecord(value, value.configSha);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = journalIdentity(value, value.configSha);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "isolation-clean", identity);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-start-authorized", identity, { guardianStartNonce: digest("b"), guardianStartEndpointIdentity: ENDPOINT_ID });
  const state = dependencies(value);
  state.state.prodRunning = true;
  state.state.prodStartedAt = "2026-08-13T14:00:00.000000000Z";
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "the guardian must not start over an externally-started container");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "an unresolved external-start must retain the active journal");
});

test("a guardian production-started fingerprint must match exactly and never accept an arbitrary changed StartedAt", async (t) => {
  const value = await fixture(t);
  const record = journalRecord(value, value.configSha);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = journalIdentity(value, value.configSha);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "isolation-clean", identity);
  // The guardian records its own exact start (production-started) with a bound
  // StartedAt.
  const startedAt = "2026-08-13T14:00:00.000000000Z";
  const nonce = digest("b");
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-start-authorized", identity, { guardianStartNonce: nonce, guardianStartEndpointIdentity: ENDPOINT_ID });
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-started", identity, { guardianStartedAt: startedAt, guardianStartReceipt: { nonce, containerId: value.configuration.production.containerId, endpointIdentity: ENDPOINT_ID, status: 204, startedAt } });
  const state = dependencies(value);
  state.state.prodRunning = true;
  state.state.prodStartedAt = "2026-08-13T14:05:00.000000000Z"; // different StartedAt
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted", "a changed StartedAt must never be accepted solely because production-started is high enough");
});

test("a 304 already-started Docker Engine response holds manual attention (external race)", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.dependencies.startProduction = async (target) => ({ ok: false, transition: false, ambiguous: false, status: 304, body: "", containerId: target, endpointIdentity: ENDPOINT_ID });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  assert.equal(result.manualAttentionRequired, true);
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.guardianStartReceipt, null, "a 304 must never record a 204 success receipt");
  assert.equal(inspected.journal.phase, "production-start-authorized", "the durable nonce intent must be preserved");
});

test("an unexpected Docker Engine start response holds manual attention", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.dependencies.startProduction = async (target) => ({ ok: false, transition: null, ambiguous: true, status: 500, body: "oops", containerId: target, endpointIdentity: ENDPOINT_ID });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.guardianStartReceipt, null, "an unexpected response must not fabricate a 204 receipt");
});

test("a changed container id in the start result is never adopted", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.dependencies.startProduction = async (target) => ({ ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: digest("ff"), endpointIdentity: ENDPOINT_ID });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted", "a 204 for a different container id must not be attributed to the bound production");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.guardianStartReceipt, null);
});

test("the authorized endpoint identity is durable before start; a changed reviewed identity on replay holds manual without a start", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  // First pass records the durable production-start-authorized intent (nonce +
  // endpoint identity) then the start crashes before any 204 receipt.
  const firstDeps = dependencies(value);
  firstDeps.dependencies.startProduction = async () => { throw new Error("crash after authorization"); };
  const first = await runMaintenanceRecoveryGuardian(value.configPath, firstDeps.dependencies);
  assert.equal(first.status, "manual-attention-recovery-production-not-ready");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.phase, "production-start-authorized");
  assert.equal(inspected.journal.guardianStartEndpointIdentity, ENDPOINT_ID, "the authorized endpoint identity must be durably recorded before start");
  assert.equal(inspected.journal.guardianStartReceipt, null, "no receipt is written before a proven 204");
  // Replay with a different reviewed socket identity: manual attention, no
  // start, and never a production-started receipt.
  const replay = dependencies(value);
  replay.dependencies.secureSocketIdentity = async () => digest("f");
  const result = await runMaintenanceRecoveryGuardian(value.configPath, replay.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted", "a changed reviewed identity on replay must hold manual attention without a start");
  assert.equal(replay.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "a changed reviewed identity must never issue a start");
  const after = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(after.journal.guardianStartReceipt, null, "a changed endpoint must never write a production-started receipt");
  assert.equal(after.journal.phase, "production-start-authorized");
});

test("a 204 whose endpoint identity differs from the authorized one never writes a production-started receipt", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.dependencies.startProduction = async (target) => {
    state.state.prodRunning = true;
    state.state.prodStartedAt = "2026-08-13T14:00:00.000000000Z";
    state.state.prodRestartCount = 0;
    return { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: target, endpointIdentity: digest("f") };
  };
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted", "a 204 with a mismatched endpoint identity must not authorize a receipt");
  const inspected = await inspectActiveRecoveryJournal(value.custodyLockPath, OWNER_UID);
  assert.equal(inspected.journal.guardianStartReceipt, null, "a mismatched endpoint identity must never write a 204 receipt");
});

test("a 204 followed by a crash before the durable receipt is an honest ambiguous manual state", async (t) => {
  const value = await fixture(t);
  // Durable intent is recorded, the 204 start committed, but the durable 204
  // receipt was never written before the crash. Production is running with the
  // new StartedAt; the guardian must never silently adopt it.
  const record = journalRecord(value, value.configSha);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = journalIdentity(value, value.configSha);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "isolation-clean", identity);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-start-authorized", identity, { guardianStartNonce: digest("b"), guardianStartEndpointIdentity: ENDPOINT_ID });
  const state = dependencies(value);
  state.state.prodRunning = true;
  state.state.prodStartedAt = "2026-08-13T14:00:00.000000000Z";
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted", "a running production with only intent and no durable 204 receipt is ambiguous manual");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "the guardian must not start again over the already-running container");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true, "the ambiguous state must preserve the journal");
});

test("a durable 204 receipt with production observed stopped refuses a second effect", async (t) => {
  const value = await fixture(t);
  const record = journalRecord(value, value.configSha);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = journalIdentity(value, value.configSha);
  const nonce = digest("b");
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "isolation-clean", identity);
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-start-authorized", identity, { guardianStartNonce: nonce, guardianStartEndpointIdentity: ENDPOINT_ID });
  const startedAt = "2026-08-13T14:00:00.000000000Z";
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-started", identity, { guardianStartedAt: startedAt, guardianStartReceipt: { nonce, containerId: value.configuration.production.containerId, endpointIdentity: ENDPOINT_ID, status: 204, startedAt } });
  // Production is observed stopped despite a durable 204 receipt: replay with a
  // receipt must never silently start again.
  const state = dependencies(value);
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-production-substituted");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "a replayed receipt must not produce a second start effect");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("receipt replay: a terminal receipt that already exists is accepted idempotently and the journal clears", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const identity = journalIdentity(value, value.configSha);
  const first = await settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity });
  assert.equal(first.replayed, false);
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, journalRecord(value, value.configSha));
  const second = await settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity });
  assert.equal(second.replayed, true, "replaying an already-settled receipt must be idempotent");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("a tampered pre-existing receipt at the same status path fails closed", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const identity = journalIdentity(value, value.configSha);
  await settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity });
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, journalRecord(value, value.configSha));
  const receiptPath = deriveRecoveryReceiptPath(value.custodyLockPath, "guardian-production-restored", identity.maintenanceOperationSha256);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  receipt.configurationSha256 = digest("e");
  await privateWrite(receiptPath, receipt);
  await assert.rejects(settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity }), /different status or identity/u);
});

test("admission while recovery is active fails closed for a new review", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "qualification-active");
  await assert.rejects(reviewModelQualificationMaintenance(value.configPath, { expectedOwnerUid: OWNER_UID }), /recovery is required/u);
  const state = dependencies(value);
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("guardian boundary strings are content-free and never grant destructive authority", async (t) => {
  assert.match(maintenanceRecoveryGuardianBoundaries.result, /removes only the exact-bound qualification runner\/backend\/network/u);
  assert.match(maintenanceRecoveryGuardianBoundaries.result, /grants no credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority/u);
  assert.match(maintenanceRecoveryGuardianBoundaries.attention, /makes no ambiguous or substituted mutation and performs no additional destructive action after the unresolved condition/u);
  assert.match(maintenanceRecoveryGuardianBoundaries.attention, /cleanup that committed before the unresolved condition is reported as a content-free boolean/u);
});

test("every emitted guardian status belongs to the closed validator-bound set", async (t) => {
  const value = await fixture(t);
  const closed = maintenanceRecoveryGuardianStatuses;
  const identity = journalIdentity(value, value.configSha);

  await seedJournal(value, "restore-pending");
  const missing = dependencies(value);
  missing.state.prodMissing = true;
  const missingResult = await runMaintenanceRecoveryGuardian(value.configPath, missing.dependencies);
  assert.ok(closed.includes(missingResult.status), `emitted status ${missingResult.status} must be in the closed set`);
  await settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity });

  await seedJournal(value, "restore-pending");
  const parsed = JSON.parse(await readFile(value.configPath, "utf8"));
  await writeFile(value.configPath, JSON.stringify(parsed, null, 4) + "\n", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(value.configPath, 0o600);
  const configResult = await runMaintenanceRecoveryGuardian(value.configPath, dependencies(value).dependencies);
  assert.ok(closed.includes(configResult.status), `emitted status ${configResult.status} must be in the closed set`);
  await settleRecoveryJournal(value.custodyLockPath, OWNER_UID, { status: "guardian-production-restored", expectedIdentity: identity });
});

test("the persistent watcher stays alive before any journal and later reacts to a change", async (t) => {
  const value = await fixture(t);
  let recoveryCalls = 0;
  let releaseNext = null;
  const controller = new AbortController();
  const watchPromise = watchMaintenanceRecoveryGuardian(value.configPath, {
    expectedOwnerUid: OWNER_UID,
    signal: controller.signal,
    runRecovery: async () => { recoveryCalls += 1; return { status: "no-active-recovery", manualAttentionRequired: false }; },
    refreshLease: async () => {},
    removeLease: async () => {},
    waitForTrigger: async (_dir, signal) => {
      if (signal?.aborted) return true;
      return new Promise((resolveTrigger) => {
        const onAbort = () => resolveTrigger(true);
        signal?.addEventListener("abort", onAbort, { once: true });
        releaseNext = () => { signal?.removeEventListener("abort", onAbort); resolveTrigger(true); };
      });
    },
    sleep: async () => {},
  });
  // Give the watcher time to run its initial pass and reach the waiting state.
  for (let i = 0; i < 50 && releaseNext === null; i += 1) await delay(5);
  assert.ok(recoveryCalls >= 1, "the watcher must run an initial recovery pass and stay alive");
  assert.notEqual(releaseNext, null, "the watcher must be waiting for a journal/config change");
  releaseNext();
  for (let i = 0; i < 50 && recoveryCalls < 2; i += 1) await delay(5);
  assert.ok(recoveryCalls >= 2, "the watcher must react to a change while staying alive");
  controller.abort();
  await watchPromise;
});

test("manual-attention does not cause a mutation loop and waits on a bounded backoff", async (t) => {
  const value = await fixture(t);
  let recoveryCalls = 0;
  const sleepMs = [];
  const controller = new AbortController();
  const watchPromise = watchMaintenanceRecoveryGuardian(value.configPath, {
    expectedOwnerUid: OWNER_UID,
    signal: controller.signal,
    manualBackoffMs: 25000,
    runRecovery: async () => { recoveryCalls += 1; return { status: "manual-attention-recovery-error", manualAttentionRequired: true }; },
    refreshLease: async () => {},
    removeLease: async () => {},
    waitForTrigger: async (_dir, signal) => {
      if (signal?.aborted) return true;
      return new Promise((resolveTrigger) => signal?.addEventListener("abort", () => resolveTrigger(true), { once: true }));
    },
    sleep: async (ms) => {
      sleepMs.push(ms);
      await new Promise((resolveSleep) => controller.signal.addEventListener("abort", () => resolveSleep(true), { once: true }));
    },
  });
  await delay(40);
  assert.equal(recoveryCalls, 1, "a stable manual-attention condition must not immediately re-run recovery");
  assert.ok(sleepMs.length >= 1 && sleepMs[0] === 25000, "manual attention must wait on the bounded manual backoff, not busy-loop");
  controller.abort();
  await watchPromise;
});

test("default-path recovery uses real private config readers and does not freeze before its prepared binding", async (t) => {
  const value = await fixture(t);
  const runnerImageDigest = `sha256:${digest("8")}`;
  const model = {
    provider: "vllm", id: "DeepSeek-V4-Flash-0731", modelArtifactSha256: digest("a"),
    backendImageDigest: `sha256:${digest("b")}`, backendVersion: "0.11.2.fixture",
    acceleratorClass: "nvidia-cuda", promptContractSha256: digest("c"),
    toolSchemaSha256: digest("d"), contextWindow: 1048576, supportsVision: false,
  };
  const prepared = {
    configuration: { publishLoopbackPort: null, restartPolicy: "no", accelerator: { class: "nvidia-cuda" } },
    artifactManifest: { artifactSha256: model.modelArtifactSha256 },
    policy: {
      runner: { imageDigest: runnerImageDigest, imageRef: `local/pixel-work-runner@${runnerImageDigest}` },
      localModel: { prepared: false, ...model, imageDigest: model.backendImageDigest, imageRef: `local/pixel-dsv4@${model.backendImageDigest}` },
    },
    environment: { stateRoot: value.root, runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-qualification-network", backendContainerName: "pixel-qualification-backend" } },
    launchBundleSha256: digest("9"),
  };
  delete prepared.policy.localModel.backendImageDigest;
  const qualification = { schemaVersion: 1, backendOrigin: "http://127.0.0.1:18081", model, timeoutMs: 300000, maxResponseBytes: 1048576, qualificationLifetimeSeconds: 604800 };
  const backendConfigPath = join(value.root, "backend.json");
  const qualificationConfigPath = join(value.root, "qualification.json");
  const evidenceRoot = join(value.root, "evidence");
  await privateWrite(backendConfigPath, { fixture: true });
  await privateWrite(qualificationConfigPath, qualification);
  await privateWrite(value.qualificationPath, {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json",
    schemaVersion: 1, backendConfigPath, qualificationConfigPath, runnerImageDigest, evidenceRoot,
    boundary: modelQualificationDockerBoundaries.configuration,
  });
  const dockerConfigSha = sha(await readFile(value.qualificationPath));
  const qualConfigSha = sha(await readFile(qualificationConfigPath));
  const operationSha = dockerQualificationOperationSha256(prepared, dockerConfigSha, qualConfigSha, runnerImageDigest);
  const record = journalRecord(value, value.configSha);
  record.qualificationOperationSha256 = operationSha;
  record.qualification = {
    backendContainerName: "pixel-qualification-backend",
    backendNetworkName: "pixel-qualification-network",
    qualificationContainerName: `pixel-model-qualification-${operationSha.slice(0, 12)}`,
  };
  await createRecoveryJournal(value.custodyLockPath, OWNER_UID, record);
  const identity = { ...journalIdentity(value, value.configSha), qualificationOperationSha256: operationSha, qualification: record.qualification };
  await advanceRecoveryJournal(value.custodyLockPath, OWNER_UID, "production-stopped", identity);
  const state = dependencies(value, {
    prepareQualificationForRecovery: undefined,
    prepareModelBackendLaunch: async () => prepared,
  });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("isolation-clean is history: a reappearing same-name foreign backend/network is never cleaned and production never starts", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.setBackend({ Id: digest("aa") });
  state.setNetwork({ Id: digest("bb") });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.manualAttentionRequired, true);
  assert.notEqual(state.state.backend, null, "a foreign reappearing backend must never be removed");
  assert.notEqual(state.state.network, null, "a foreign reappearing network must never be removed");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), false, "production must never start while isolation is not proven absent");
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("isolation-clean is history: a newly reappeared exact backend/network is safely reconciled and production restores", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "isolation-clean");
  const state = dependencies(value);
  state.setBackend();
  state.setNetwork();
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "guardian-production-restored");
  assert.equal(state.state.backend, null, "the reappeared exact backend must be reconciled and removed");
  assert.equal(state.state.network, null, "the reappeared exact network must be reconciled and removed");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && entry[0] === "start"), true);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), false);
});

test("config mutated between load and custody lock is rejected as config-changed with zero Docker mutation", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value, {
    async withCustody(_binding, _uid, operation) {
      const parsed = JSON.parse(await readFile(value.configPath, "utf8"));
      parsed.production.restoreTimeoutSeconds = 7200;
      await privateWrite(value.configPath, parsed);
      return operation(digest("c"));
    },
  });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-config-changed");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && ["rm-container", "rm-network", "start"].includes(entry[0])), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("path substitution between load and custody lock is rejected with zero Docker mutation", async (t) => {
  const value = await fixture(t);
  await seedJournal(value, "restore-pending");
  const state = dependencies(value, {
    async withCustody(_binding, _uid, operation) {
      // Substitute the config to point at an entirely different qualification
      // path before the guarded operation runs.
      const parsed = JSON.parse(await readFile(value.configPath, "utf8"));
      parsed.qualificationDockerConfigPath = join(value.root, "other-docker.json");
      await privateWrite(value.configPath, parsed);
      return operation(digest("c"));
    },
  });
  const result = await runMaintenanceRecoveryGuardian(value.configPath, state.dependencies);
  assert.equal(result.status, "manual-attention-recovery-config-changed");
  assert.equal(state.state.calls.some((entry) => Array.isArray(entry) && ["rm-container", "rm-network", "start"].includes(entry[0])), false);
  assert.equal(await hasActiveRecoveryJournal(value.custodyLockPath, OWNER_UID), true);
});

test("watch startup fails nonzero when a durable lease write fails (never reports a usable guardian)", async (t) => {
  const value = await fixture(t);
  let writes = 0;
  await assert.rejects(watchMaintenanceRecoveryGuardian(value.configPath, {
    expectedOwnerUid: OWNER_UID,
    refreshLease: async () => { writes += 1; if (writes === 2) throw new Error("ready lease write failed"); },
    removeLease: async () => {},
    runRecovery: async () => ({ status: "no-active-recovery", manualAttentionRequired: false }),
    waitForTrigger: async () => {},
    sleep: async () => {},
  }), /ready lease write failed/u);
  assert.equal(writes, 2, "the ready lease write is attempted exactly once after the non-ready proof");
});

test("watchMaintenanceRecoveryGuardian fails closed when the first post-ready lease refresh throws: rejects, removes the lease, and stops the recovery/refresh loop", async (t) => {
  const value = await fixture(t);
  let refreshCalls = 0;
  let recoveryCalls = 0;
  let removed = 0;
  const refreshLease = async () => {
    refreshCalls += 1;
    if (refreshCalls === 3) throw new Error("post-ready refresh failed");
  };
  await assert.rejects(watchMaintenanceRecoveryGuardian(value.configPath, {
    expectedOwnerUid: OWNER_UID,
    refreshLease,
    removeLease: async () => { removed += 1; },
    runRecovery: async () => { recoveryCalls += 1; return { status: "no-active-recovery", manualAttentionRequired: false }; },
    waitForTrigger: async () => {},
    sleep: async () => {},
  }), /post-ready refresh failed/u);
  assert.equal(refreshCalls, 3, "exactly the two init lease writes plus the first post-ready refresh are attempted");
  assert.ok(removed >= 1, "the watcher must remove its own exact lease in finally on a post-ready refresh failure");
  assert.equal(recoveryCalls, 1, "no additional recovery/refresh loop may continue after the failed post-ready refresh");
});

test("watch installs abort/SIGTERM handlers and durably removes only its own exact lease before exiting", async (t) => {
  const value = await fixture(t);
  const controller = new AbortController();
  const removed = [];
  const watchPromise = watchMaintenanceRecoveryGuardian(value.configPath, {
    expectedOwnerUid: OWNER_UID,
    signal: controller.signal,
    refreshLease: async () => {},
    removeLease: async () => { removed.push("removed"); },
    runRecovery: async () => { return { status: "no-active-recovery", manualAttentionRequired: false }; },
    waitForTrigger: async (_dir, signal) => new Promise((resolveTrigger) => signal?.addEventListener("abort", () => resolveTrigger(true), { once: true })),
    sleep: async () => {},
  });
  await new Promise((resolve) => setTimeout(resolve, 50));
  controller.abort();
  const result = await watchPromise;
  assert.equal(result.stopped, true);
  assert.ok(removed.length >= 1, "the watcher must durably remove its own exact lease on abort before exiting");
});

test("a journal change event does not leave a long watcher timer alive", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-watcher-timer-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const timeoutCount = () => process.getActiveResourcesInfo().filter((name) => name === "Timeout").length;
  const baseline = timeoutCount();
  const started = Date.now();
  const pending = defaultWaitForTrigger(root, null, 60000);
  await writeFile(join(root, "trigger.txt"), "change", { mode: 0o600 });
  await pending;
  assert.ok(Date.now() - started < 5000, "a change event must resolve the wait before the long timeout");
  // The long reconcile timeout must be cleared on the change event; otherwise a
  // leftover 60s timer would keep the process alive.
  await delay(50);
  assert.ok(timeoutCount() <= baseline, "the change event must not leave a long watcher timer alive");
});

test("guardian/default contract: the Docker socket default is /run/docker.sock and cannot drift back to /var/run", async (t) => {
  // The production goal service units gate on the real canonical endpoint
  // /run/docker.sock (ConditionPathExists + BindReadOnlyPaths). The exported
  // constant is the single immutable source of that default.
  assert.equal(DOCKER_SOCKET_PATH, "/run/docker.sock");
  assert.notEqual(DOCKER_SOCKET_PATH, "/var/run/docker.sock");
  // The guardian action defaults must select the exported constant (actual
  // dependency/default wiring, not a comment or duplicate string).
  const defaults = guardianActions({}, "/usr/bin/docker");
  assert.equal(defaults.dockerSocketPath, DOCKER_SOCKET_PATH);
  assert.equal(defaults.dockerSocketPath, "/run/docker.sock");
  assert.notEqual(defaults.dockerSocketPath, "/var/run/docker.sock");
  // Prove the default startProduction wires its socket through
  // actions.dockerSocketPath (the same constant) rather than a hardcoded
  // /var/run path: inject a fake owner-private socket as the configured
  // dockerSocketPath and start without an explicit socketPath option.
  const root = await mkdtemp(join(tmpdir(), "pixel-guardian-default-socket-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const socketPath = join(root, "docker.sock");
  const server = http.createServer((req, res) => { res.writeHead(204); res.end(); });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(socketPath, resolve); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const identity = await secureSocketIdentity(socketPath);
  assert.ok(identity, "the fake injected socket must yield a proven endpoint identity");
  const injected = guardianActions({ dockerSocketPath: socketPath }, "/usr/bin/docker");
  const result = await injected.startProduction(digest("1"), { reviewedIdentity: identity.identitySha256 });
  assert.equal(result.status, 204, "the default startProduction must reach the configured dockerSocketPath endpoint");
  assert.equal(result.ambiguous, false);
});

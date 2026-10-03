import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { watch } from "node:fs";
import { readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { inspectMaintenanceCustodyLock, withMaintenanceCustody } from "./maintenance-custody.mjs";
import {
  GUARDIAN_UNIT,
  refreshGuardianLease,
  removeGuardianLease,
} from "./maintenance-recovery-guardian-lease.mjs";
import { renderGuardianUnit } from "./maintenance-recovery-guardian-unit.mjs";
import {
  RECOVERY_PHASES,
  advanceRecoveryJournal,
  inspectActiveRecoveryJournal,
  settleRecoveryJournal,
} from "./maintenance-recovery-journal.mjs";
import {
  productionMaintenancePrimitives,
  validateModelQualificationMaintenanceConfiguration,
} from "./model-qualification-maintenance.mjs";
import {
  dockerQualificationOperationSha256,
  validateDockerQualificationBinding,
  validateDockerQualificationConfiguration,
} from "./model-qualification-docker.mjs";
import { prepareModelBackendLaunch } from "./model-backend-cli.mjs";
import { modelBackendLifecycleSha256, stopModelBackend } from "./model-backend-lifecycle.mjs";
import { classifyStartResult, dockerEngineStart, DOCKER_SOCKET_PATH, newStartNonce, secureSocketIdentity } from "./docker-engine-start.mjs";

const execute = promisify(execFile);
const SHA_RE = /^[a-f0-9]{64}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/u;
const DOCKER_ID_RE = /^[a-f0-9]{64}$/u;
const OPERATION = "pixel-work-model-qualification-maintenance-recovery";
const GUARDIAN_RESULT_BOUNDARY = "Content-free recovery/guardian result for one exact local-model qualification maintenance operation. It restarts the bound production container, removes only the exact-bound qualification runner/backend/network, performs one credential-free loopback readiness probe and a stable-fingerprint check, then archives the bound journal with a terminal content-free receipt. It never acts on substituted or ambiguous identities, grants no credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority, and performs no destructive action unless every bound identity is revalidated.";
const GUARDIAN_ATTENTION_BOUNDARY = "Content-free manual-attention result for an unresolved maintenance recovery. It retains the active recovery journal and records only that an identity, resource, production, readiness, or config binding could not be proven exact. It makes no ambiguous or substituted mutation and performs no additional destructive action after the unresolved condition; any exact-bound cleanup that committed before the unresolved condition is reported as a content-free boolean. It grants no credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const ZERO_SHA256 = "0".repeat(64);
const SETTLED_GUARDIAN_STATUS = "guardian-production-restored";
const NO_ACTIVE_STATUS = "no-active-recovery";
const MANUAL_STATUSES = Object.freeze([
  "manual-attention-recovery-config-changed",
  "manual-attention-recovery-identity-changed",
  "manual-attention-recovery-substituted-resource",
  "manual-attention-recovery-production-missing",
  "manual-attention-recovery-production-substituted",
  "manual-attention-recovery-production-not-ready",
  "manual-attention-recovery-error",
]);
const GUARDIAN_STATUSES = Object.freeze([NO_ACTIVE_STATUS, SETTLED_GUARDIAN_STATUS, ...MANUAL_STATUSES]);
const PREPARED_RECOVERY_OPERATION = Symbol("pixel-maintenance-recovery-prepared");
const DEFAULT_RECONCILE_BACKOFF_MS = 60000;
const DEFAULT_MANUAL_BACKOFF_MS = 60000;
const RUNNER_OPERATION_LABEL = "com.osmantic.pixel.work-model-qualification.operation-sha256";

export class WorkMaintenanceRecoveryGuardianError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoveryGuardianError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function boundedMs(value, fallback, minimum, maximum, label) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is out of range`);
  return value;
}

function result(status, journal, state = {}) {
  if (!GUARDIAN_STATUSES.includes(status)) fail(`guardian status is not in the closed set: ${status}`);
  return Object.freeze({
    schemaVersion: 1,
    operation: OPERATION,
    status,
    journalPhase: journal?.phase ?? null,
    maintenanceOperationSha256: journal?.maintenanceOperationSha256 ?? null,
    qualificationOperationSha256: journal?.qualificationOperationSha256 ?? null,
    productionRestored: state.productionRestored ?? null,
    productionReady: state.productionReady ?? null,
    qualificationIsolationAbsent: state.qualificationIsolationAbsent ?? null,
    qualificationCleanupCommitted: state.qualificationCleanupCommitted ?? (state.qualificationIsolationAbsent === true),
    manualAttentionRequired: status !== SETTLED_GUARDIAN_STATUS && status !== NO_ACTIVE_STATUS,
    credentialsUsed: false,
    externalNetworkUsed: false,
    authority: { grantsExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false },
    boundary: state.attention ? GUARDIAN_ATTENTION_BOUNDARY : GUARDIAN_RESULT_BOUNDARY,
  });
}

function manual(journal, status, state = {}) {
  return result(status, journal, { ...state, attention: true });
}

function productionMatchesJournal(production, journalProduction) {
  return Boolean(production && journalProduction
    && production.containerName === journalProduction.containerName
    && production.containerId === journalProduction.containerId
    && production.imageDigest === journalProduction.imageDigest
    && production.expectedStartedAt === journalProduction.expectedStartedAt
    && production.expectedRestartCount === journalProduction.expectedRestartCount);
}

function qualificationMatchesJournal(qualification, journalQualification, journalQualificationOperationSha256) {
  return Boolean(qualification && journalQualification
    && qualification.qualificationOperationSha256 === journalQualificationOperationSha256
    && qualification.backendContainerName === journalQualification.backendContainerName
    && qualification.backendNetworkName === journalQualification.backendNetworkName
    && qualification.qualificationContainerName === journalQualification.qualificationContainerName);
}

async function prepareQualificationForRecoveryDefault(qualificationDockerConfigPath, expectedOwnerUid, dependencies = {}) {
  absolutePath(qualificationDockerConfigPath, "qualification Docker configuration path");
  const read = productionMaintenancePrimitives.readPrivateJsonRecord;
  const dockerLoaded = await read(qualificationDockerConfigPath, "private Docker qualification configuration", expectedOwnerUid);
  const configuration = validateDockerQualificationConfiguration(dockerLoaded.value);
  const prepare = dependencies.prepareModelBackendLaunch ?? prepareModelBackendLaunch;
  const prepared = await prepare(configuration.backendConfigPath, expectedOwnerUid, dependencies.prepareDependencies ?? {});
  const qualificationLoaded = await read(configuration.qualificationConfigPath, "private model qualification configuration", expectedOwnerUid);
  validateDockerQualificationBinding(prepared, qualificationLoaded.value);
  if (prepared.policy.runner.imageDigest !== configuration.runnerImageDigest) fail("qualification runner differs from the exact Work runner policy");
  const dockerConfigSha256 = sha(dockerLoaded.bytes);
  const qualificationConfigSha256 = sha(qualificationLoaded.bytes);
  const qualificationOperationSha256 = dockerQualificationOperationSha256(prepared, dockerConfigSha256, qualificationConfigSha256, configuration.runnerImageDigest);
  const qualification = {
    qualificationOperationSha256,
    dockerPath: prepared.environment.runtime.dockerPath,
    backendContainerName: prepared.environment.runtime.backendContainerName,
    backendNetworkName: prepared.environment.runtime.backendNetworkName,
    qualificationContainerName: `pixel-model-qualification-${qualificationOperationSha256.slice(0, 12)}`,
    runnerImageDigest: configuration.runnerImageDigest,
  };
  Object.defineProperty(qualification, PREPARED_RECOVERY_OPERATION, {
    value: Object.freeze({ prepared, backendConfigPath: configuration.backendConfigPath, prepareDependencies: Object.freeze({ ...(dependencies.prepareDependencies ?? {}) }) }),
  });
  return Object.freeze(qualification);
}

async function loadGuardian(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("guardian requires a non-root service identity");
  absolutePath(configPath, "maintenance configuration path");
  const configRecord = await productionMaintenancePrimitives.readPrivateJsonRecord(configPath, "private qualification maintenance configuration", expectedOwnerUid);
  const configuration = validateModelQualificationMaintenanceConfiguration(configRecord.value);
  return Object.freeze({ expectedOwnerUid, configuration, configBytesSha: sha(configRecord.bytes) });
}

async function prepareQualificationUnderLock(loaded, dependencies = {}) {
  const prepareQualification = dependencies.prepareQualificationForRecovery ?? prepareQualificationForRecoveryDefault;
  const qualification = await prepareQualification(loaded.configuration.qualificationDockerConfigPath, loaded.expectedOwnerUid, dependencies);
  if (!qualification || typeof qualification !== "object" || !SHA_RE.test(qualification.qualificationOperationSha256 ?? "") || qualification.qualificationOperationSha256 === ZERO_SHA256 || typeof qualification.dockerPath !== "string" || !NAME_RE.test(qualification.backendContainerName ?? "") || !NAME_RE.test(qualification.backendNetworkName ?? "") || !NAME_RE.test(qualification.qualificationContainerName ?? "") || !/^sha256:[a-f0-9]{64}$/u.test(qualification.runnerImageDigest ?? "") || !qualification[PREPARED_RECOVERY_OPERATION]) fail("guardian qualification identity is invalid");
  absolutePath(qualification.dockerPath, "guardian Docker client path");
  return Object.freeze(qualification);
}

export function guardianActions(dependencies, dockerPath, prepareDependencies = {}) {
  const inspectContainer = dependencies.inspectContainer ?? productionMaintenancePrimitives.dockerInspectMaybe;
  const actions = Object.freeze({
    dockerPath,
    prepareDependencies,
    inspectContainer,
    inspectNetwork: dependencies.inspectNetwork ?? ((target, path) => productionMaintenancePrimitives.dockerInspectMaybe(path, "network", target)),
    removeContainer: dependencies.removeContainer ?? ((target, path) => execute(path, ["container", "rm", "--force", target], { encoding: "utf8", windowsHide: true, timeout: 60000, maxBuffer: 1024 * 1024, env: productionMaintenancePrimitives.DOCKER_ENV })),
    removeNetwork: dependencies.removeNetwork ?? ((name, path) => execute(path, ["network", "rm", name], { encoding: "utf8", windowsHide: true, timeout: 60000, maxBuffer: 1024 * 1024, env: productionMaintenancePrimitives.DOCKER_ENV })),
    dockerSocketPath: dependencies.dockerSocketPath ?? DOCKER_SOCKET_PATH,
    secureSocketIdentity: dependencies.secureSocketIdentity ?? (async (socketPath) => {
      // Independently prove the exact Docker socket identity under custody
      // before any start intent. Returns the exact 64-hex identity or null if
      // the endpoint cannot be proven.
      const identity = await secureSocketIdentity(socketPath);
      return identity?.identitySha256 ?? null;
    }),
    startProduction: dependencies.startProduction ?? (async (target, options = {}) => {
      const socketPath = options.socketPath ?? actions.dockerSocketPath;
      const reviewedIdentity = options.reviewedIdentity;
      if (typeof reviewedIdentity !== "string" || !SHA_RE.test(reviewedIdentity)) fail("guardian start requires the exact reviewed endpoint identity");
      return dockerEngineStart({ containerId: target, socketPath, timeoutMs: 60000, reviewedIdentity });
    }),
    stopModelBackend: dependencies.stopModelBackend ?? stopModelBackend,
    waitForReadiness: dependencies.waitForReadiness ?? productionMaintenancePrimitives.defaultWaitForReadiness,
  });
  return actions;
}

function exactQualificationRunner(runner, q) {
  return Boolean(runner && DOCKER_ID_RE.test(runner.Id ?? "")
    && runner.Name === `/${q.qualificationContainerName}`
    && runner.Image === q.runnerImageDigest
    && runner.Config?.Labels?.[RUNNER_OPERATION_LABEL] === q.qualificationOperationSha256
    && runner.HostConfig?.NetworkMode === q.backendNetworkName);
}

async function convergeQualificationIsolation(loaded, actions) {
  const q = loaded.qualification;
  const runner = await actions.inspectContainer(q.qualificationContainerName, actions.dockerPath);
  if (runner) {
    if (!exactQualificationRunner(runner, q)) return { ok: false, status: "manual-attention-recovery-substituted-resource", resource: "qualification runner" };
    await actions.removeContainer(runner.Id, actions.dockerPath);
    if (await actions.inspectContainer(q.qualificationContainerName, actions.dockerPath) !== null) return { ok: false, status: "manual-attention-recovery-error", resource: "qualification runner" };
  }
  const recovery = q[PREPARED_RECOVERY_OPERATION];
  if (!recovery || !recovery.prepared || typeof recovery.prepared.launchBundleSha256 !== "string") return { ok: false, status: "manual-attention-recovery-error", resource: "qualification backend" };
  try {
    const confirmation = modelBackendLifecycleSha256("stop", recovery.prepared.launchBundleSha256);
    const postflight = () => prepareModelBackendLaunch(recovery.backendConfigPath, loaded.expectedOwnerUid, recovery.prepareDependencies ?? {});
    const receipt = await actions.stopModelBackend(recovery.prepared, {
      confirmation,
      postflight,
      coordinationOptions: { expectedOwnerUid: loaded.expectedOwnerUid },
    });
    if (!["removed", "already-absent"].includes(receipt?.state)) return { ok: false, status: "manual-attention-recovery-error", resource: "qualification backend" };
  } catch {
    return { ok: false, status: "manual-attention-recovery-error", resource: "qualification backend" };
  }
  return { ok: true, isolationAbsent: true };
}

async function convergeProduction(loaded, actions, journal, identity) {
  const production = loaded.configuration.production;
  const observed = await actions.inspectContainer(production.containerId, actions.dockerPath);
  if (observed === null) return { ok: false, status: "manual-attention-recovery-production-missing" };
  const phaseOrder = RECOVERY_PHASES[journal.phase];
  const authorize = RECOVERY_PHASES["production-start-authorized"];
  let running = null;
  try { running = productionMaintenancePrimitives.validateProduction(observed, production, { running: true, requireReviewedStart: false }); } catch {}
  if (running) {
    const preMaintenance = running.startedAt === production.expectedStartedAt && running.restartCount === production.expectedRestartCount;
    if (preMaintenance) return { ok: true, fingerprint: running };
    // Production was stopped by maintenance and must only be running again as
    // the result of this guardian's own nonce-bound 204 Docker Engine start,
    // which alone records a durable receipt immediately after its exact start
    // attempt. A running container with only intent (a nonce) and no durable 204
    // receipt — or an external start before the guardian's call — must hold
    // manual attention: it is never adopted merely because the phase number is
    // high enough.
    if (journal.phase !== "production-started" || journal.guardianStartReceipt === null) {
      return { ok: false, status: "manual-attention-recovery-production-substituted" };
    }
    if (journal.guardianStartReceipt.startedAt !== running.startedAt) {
      return { ok: false, status: "manual-attention-recovery-production-substituted" };
    }
    return { ok: true, fingerprint: running };
  }
  let stopped;
  try { stopped = productionMaintenancePrimitives.validateProduction(observed, production, { running: false, requireReviewedStart: false }); }
  catch { return { ok: false, status: "manual-attention-recovery-production-substituted" }; }
  // A replay with a durable receipt must never silently start again: if a 204
  // receipt already exists but production is observed stopped, that is an
  // inconsistent manual state.
  if (journal.guardianStartReceipt !== null) {
    return { ok: false, status: "manual-attention-recovery-production-substituted" };
  }
  // Independently prove the exact reviewed Docker socket identity under custody
  // BEFORE recording the durable start intent. An absent/unprovable identity
  // holds manual attention and never records an intent or starts.
  const reviewedIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof reviewedIdentity !== "string" || !SHA_RE.test(reviewedIdentity)) return { ok: false, status: "manual-attention-recovery-production-substituted" };
  // On replay the current reviewed identity must equal the durable
  // journal-authorized identity (recorded at production-start-authorized) or
  // recovery holds manual attention without a start.
  if (journal.phase === "production-start-authorized" && journal.guardianStartEndpointIdentity !== null && reviewedIdentity !== journal.guardianStartEndpointIdentity) {
    return { ok: false, status: "manual-attention-recovery-production-substituted" };
  }
  // Record the durable intent (cryptographically random nonce plus the exact
  // authorized endpoint identity) before the exact Docker Engine start attempt.
  let current = journal;
  if (phaseOrder < authorize || (phaseOrder === authorize && (journal.guardianStartNonce === null || journal.guardianStartEndpointIdentity === null))) {
    const nonce = newStartNonce();
    const advanced = await advanceRecoveryJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid, "production-start-authorized", identity, { guardianStartNonce: nonce, guardianStartEndpointIdentity: reviewedIdentity });
    current = advanced.journal;
  }
  // The exact, fail-closed Docker Engine start attempt distinguishes an actual
  // transition by this request (204) from an already-running no-op (304).
  let startResult;
  try {
    startResult = await actions.startProduction(production.containerId, { socketPath: actions.dockerSocketPath, nonce: current.guardianStartNonce, reviewedIdentity: current.guardianStartEndpointIdentity });
  } catch {
    return { ok: false, status: "manual-attention-recovery-production-not-ready" };
  }
  const classification = classifyStartResult(startResult);
  if (!classification.ok || startResult.containerId !== production.containerId) {
    // 304 (already started / external race), an unexpected status/body, or a
    // changed container id holds manual attention and preserves the durable
    // journal (the nonce intent).
    return { ok: false, status: "manual-attention-recovery-production-substituted" };
  }
  // 204: this request started it. Inspect the exact post-start StartedAt and
  // durably record the receipt bound to nonce, exact container id, endpoint
  // identity, status 204, and StartedAt.
  const restarted = await actions.inspectContainer(production.containerId, actions.dockerPath);
  let fingerprint;
  try { fingerprint = productionMaintenancePrimitives.validateProduction(restarted, production, { running: true, requireReviewedStart: false }); }
  catch { return { ok: false, status: "manual-attention-recovery-production-not-ready" }; }
  // After a 204 the returned receipt endpoint identity must equal the
  // journal-authorized identity and be exact 64-hex before writing
  // production-started; a merely truthy path string is never accepted.
  if (startResult.endpointIdentity !== current.guardianStartEndpointIdentity || !SHA_RE.test(startResult.endpointIdentity ?? "")) return { ok: false, status: "manual-attention-recovery-production-substituted" };
  const receipt = {
    nonce: current.guardianStartNonce,
    containerId: production.containerId,
    endpointIdentity: current.guardianStartEndpointIdentity,
    status: 204,
    startedAt: fingerprint.startedAt,
  };
  await advanceRecoveryJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid, "production-started", identity, { guardianStartedAt: fingerprint.startedAt, guardianStartReceipt: receipt });
  return { ok: true, fingerprint };
}


async function recover(loaded, journal, actions, custodyIdentitySha256) {
  if (custodyIdentitySha256 !== journal.custodyIdentitySha256) return manual(journal, "manual-attention-recovery-identity-changed", {});
  if (loaded.configBytesSha !== journal.configurationSha256) return manual(journal, "manual-attention-recovery-config-changed", {});
  if (!productionMatchesJournal(loaded.configuration.production, journal.production)) return manual(journal, "manual-attention-recovery-identity-changed", {});
  if (!qualificationMatchesJournal(loaded.qualification, journal.qualification, journal.qualificationOperationSha256)) return manual(journal, "manual-attention-recovery-identity-changed", {});
  const identity = journalIdentityFromJournal(journal);
  let current = journal;
  // The durable phase is history, not the current resource state. Every pass
  // must freshly prove runner/backend/network absent or run the exact cleanup
  // lifecycle before any production start or readiness. Phase only prevents
  // duplicate intent, never replaces live inspection.
  const cleanup = await convergeQualificationIsolation(loaded, actions);
  if (!cleanup.ok) return manual(current, cleanup.status, { qualificationIsolationAbsent: false, qualificationCleanupCommitted: false });
  const cleanupCommitted = cleanup.isolationAbsent === true;
  if (RECOVERY_PHASES[current.phase] < RECOVERY_PHASES["isolation-clean"]) {
    const advanced = await advanceRecoveryJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid, "isolation-clean", identity);
    current = advanced.journal;
  }
  const converged = await convergeProduction(loaded, actions, current, identity);
  if (!converged.ok) return manual(current, converged.status, { qualificationIsolationAbsent: true, qualificationCleanupCommitted: cleanupCommitted });
  const productionReady = await actions.waitForReadiness(loaded.configuration.production).catch(() => false);
  if (!productionReady) return manual(current, "manual-attention-recovery-production-not-ready", { productionRestored: true, productionReady: false, qualificationIsolationAbsent: true, qualificationCleanupCommitted: cleanupCommitted });
  const postReadiness = await actions.inspectContainer(loaded.configuration.production.containerId, actions.dockerPath).catch(() => null);
  let stableRestore = false;
  try {
    stableRestore = canonical(productionMaintenancePrimitives.validateProduction(postReadiness, loaded.configuration.production, { running: true, requireReviewedStart: false })) === canonical(converged.fingerprint);
  } catch {}
  if (!stableRestore) return manual(current, "manual-attention-recovery-production-not-ready", { productionRestored: true, productionReady: true, qualificationIsolationAbsent: true, qualificationCleanupCommitted: cleanupCommitted });
  const inspected = await inspectActiveRecoveryJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid);
  const finalJournal = inspected ? inspected.journal : current;
  const settled = await settleRecoveryJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid, { status: SETTLED_GUARDIAN_STATUS, expectedIdentity: identity });
  return result(SETTLED_GUARDIAN_STATUS, finalJournal, { productionRestored: true, productionReady: true, qualificationIsolationAbsent: true, qualificationCleanupCommitted: cleanupCommitted, settled, attention: false });
}

function journalIdentityFromJournal(journal) {
  return {
    maintenanceOperationSha256: journal.maintenanceOperationSha256,
    qualificationOperationSha256: journal.qualificationOperationSha256,
    custodyIdentitySha256: journal.custodyIdentitySha256,
    configurationSha256: journal.configurationSha256,
    ownerUid: journal.ownerUid,
    production: journal.production,
    qualification: journal.qualification,
  };
}

function noActiveResult() {
  return Object.freeze({ schemaVersion: 1, operation: OPERATION, status: NO_ACTIVE_STATUS, journalPhase: null, manualAttentionRequired: false, credentialsUsed: false, externalNetworkUsed: false, authority: { grantsExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false }, boundary: GUARDIAN_RESULT_BOUNDARY });
}

export async function runMaintenanceRecoveryGuardian(configPath, dependencies = {}) {
  const preLock = await loadGuardian(configPath, dependencies);
  const withCustody = dependencies.withCustody ?? ((binding, uid, operation) => withMaintenanceCustody(binding, uid, operation));
  return withCustody(preLock.configuration.custody, preLock.expectedOwnerUid, async (custodyIdentitySha256) => {
    const inspected = await inspectActiveRecoveryJournal(preLock.configuration.custody.lockPath, preLock.expectedOwnerUid);
    if (!inspected) return noActiveResult();
    // Reread the private configuration after custody is acquired and compare to
    // the pre-lock routing binding. A mutation between load and lock (or a path
    // substitution) must never authorize actions derived from a stale config.
    let underLock;
    try {
      underLock = await loadGuardian(configPath, dependencies);
    } catch (error) {
      return manual(inspected.journal, "manual-attention-recovery-config-changed", {});
    }
    if (underLock.configBytesSha !== preLock.configBytesSha) return manual(inspected.journal, "manual-attention-recovery-config-changed", {});
    let qualification;
    try {
      qualification = await prepareQualificationUnderLock(underLock, dependencies);
    } catch (error) {
      return manual(inspected.journal, "manual-attention-recovery-error", {});
    }
    const full = Object.freeze({ ...underLock, qualification });
    const actions = guardianActions(dependencies, qualification.dockerPath, dependencies.prepareDependencies ?? {});
    try {
      return await recover(full, inspected.journal, actions, custodyIdentitySha256);
    } catch (error) {
      return manual(inspected.journal, "manual-attention-recovery-error", {});
    }
  });
}

async function defaultSleep(milliseconds, signal = null) {
  return new Promise((resolveSleep) => {
    if (signal?.aborted) return resolveSleep();
    let timer = null;
    const finish = () => {
      if (signal) signal.removeEventListener("abort", finish);
      if (timer) clearTimeout(timer);
      resolveSleep();
    };
    timer = setTimeout(finish, milliseconds);
    if (signal) signal.addEventListener("abort", finish, { once: true });
  });
}

export async function defaultWaitForTrigger(directory, signal = null, timeoutMs = DEFAULT_RECONCILE_BACKOFF_MS) {
  return new Promise((resolveTrigger) => {
    if (signal?.aborted) return resolveTrigger();
    let watcher = null;
    let settled = false;
    let timer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      try { watcher?.close(); } catch {}
      if (signal) signal.removeEventListener("abort", finish);
      resolveTrigger();
    };
    try {
      watcher = watch(directory, { persistent: true });
      watcher.on("change", finish);
      watcher.on("error", finish);
    } catch {
      timer = setTimeout(finish, timeoutMs);
      if (signal) signal.addEventListener("abort", finish, { once: true });
      return;
    }
    timer = setTimeout(finish, timeoutMs);
    if (signal) signal.addEventListener("abort", finish, { once: true });
  });
}

export async function watchMaintenanceRecoveryGuardian(configPath, dependencies = {}) {
  const signal = dependencies.signal ?? null;
  const runRecovery = dependencies.runRecovery ?? ((path, deps) => runMaintenanceRecoveryGuardian(path, deps));
  const recoveryDependencies = dependencies.recoveryDependencies ?? {};
  const waitForTrigger = dependencies.waitForTrigger ?? defaultWaitForTrigger;
  const sleep = dependencies.sleep ?? defaultSleep;
  const refreshLease = dependencies.refreshLease ?? refreshGuardianLease;
  const removeLease = dependencies.removeLease ?? removeGuardianLease;
  const withCustody = dependencies.withCustody ?? ((binding, uid, operation) => withMaintenanceCustody(binding, uid, operation));
  const reconcileBackoffMs = boundedMs(dependencies.reconcileBackoffMs, DEFAULT_RECONCILE_BACKOFF_MS, 5000, 3600000, "guardian reconcile backoff");
  const manualBackoffMs = boundedMs(dependencies.manualBackoffMs, DEFAULT_MANUAL_BACKOFF_MS, 5000, 86400000, "guardian manual backoff");
  const loaded = await loadGuardian(configPath, { expectedOwnerUid: dependencies.expectedOwnerUid });
  const journalDir = dirname(loaded.configuration.custody.lockPath);
  const expectedOwnerUid = loaded.expectedOwnerUid;
  const custodyLockPath = loaded.configuration.custody.lockPath;
  const configSha256 = loaded.configBytesSha;
  const guardianUnit = dependencies.guardianUnit ?? GUARDIAN_UNIT;
  // The watcher derives its own exact guardian code/unit contract facts from the
  // live custody lock and its own module bytes. These are not forgeable through
  // production-path options.
  const guardianModulePath = fileURLToPath(import.meta.url);
  const guardianModuleSha256 = sha(await readFile(guardianModulePath));
  // Watch mode installs SIGTERM/SIGINT/abort handlers that durably remove only
  // the watcher's own exact lease before exiting.
  let shutdown = false;
  const removeOwnLease = async () => {
    try { await removeLease(custodyLockPath, expectedOwnerUid); } catch { /* best-effort durable removal */ }
  };
  const onSignal = () => {
    if (shutdown) return;
    shutdown = true;
    removeOwnLease().finally(() => process.exit(0));
  };
  const onAbort = () => {
    if (shutdown) return;
    shutdown = true;
    removeOwnLease().finally(() => {});
  };
  process.on("SIGTERM", onSignal);
  process.on("SIGINT", onSignal);
  if (signal) signal.addEventListener("abort", onAbort, { once: true });
  try {
    // Readiness is fail-closed and observable. Under the Type=exec readiness
    // contract there is no sd_notify stream: the guardian is usable only when a
    // securely published durable ready lease is conjoined with the exact live
    // systemd unit/process identity. The complete initialization critical
    // section runs under the SAME maintenance custody primitive: under lock the
    // watcher securely rereads the config (hash equality), proves the custody
    // identity, the exact guardian bytes, the exact Node realpath, the exact
    // expected render, and durably publishes the non-ready -> ready lease. The
    // lock is released before the long watch loop; it is never held for the
    // lifetime of the watcher.
    const published = await withCustody(loaded.configuration.custody, expectedOwnerUid, async (custodyIdentitySha256) => {
      let underLock;
      try { underLock = await loadGuardian(configPath, { expectedOwnerUid }); }
      catch { fail("guardian config could not be reread under custody"); }
      if (underLock.configBytesSha !== configSha256) fail("guardian config changed after custody acquisition");
      const custodyIdentity = await inspectMaintenanceCustodyLock(loaded.configuration.custody, expectedOwnerUid);
      if (custodyIdentity.identitySha256 !== custodyIdentitySha256) fail("guardian custody identity changed during initialization");
      const guardianModuleSha256UnderLock = sha(await readFile(guardianModulePath));
      if (guardianModuleSha256UnderLock !== guardianModuleSha256) fail("guardian module bytes changed during initialization");
      const expectedNodePath = await realpath(process.execPath);
      const rendered = await renderGuardianUnit({ configPath, nodePath: expectedNodePath, guardianPath: guardianModulePath, expectedOwnerUid });
      const expectedUnitSha256 = rendered.renderSha256;
      const leaseOptions = {
        unit: guardianUnit,
        custodyIdentitySha256: custodyIdentity.identitySha256,
        guardianModuleSha256: guardianModuleSha256UnderLock,
        expectedUnitSha256,
        expectedNodePath,
      };
      // Durable false -> true lease publication. A failed durable write must
      // abort nonzero so systemd never reports a usable guardian; a lease-write
      // failure is never swallowed into "ready".
      await refreshLease(custodyLockPath, expectedOwnerUid, configSha256, { ...leaseOptions, ready: false });
      await refreshLease(custodyLockPath, expectedOwnerUid, configSha256, { ...leaseOptions, ready: true });
      return Object.freeze({
        custodyIdentitySha256: custodyIdentity.identitySha256,
        guardianModuleSha256: guardianModuleSha256UnderLock,
        expectedUnitSha256,
        expectedNodePath,
      });
    });
    let ready = true;
    while (signal ? !signal.aborted : true) {
      let result = null;
      try { result = await runRecovery(configPath, recoveryDependencies); }
      catch { result = null; }
      if (signal?.aborted) break;
      if (result && result.manualAttentionRequired) {
        await sleep(manualBackoffMs, signal);
        continue;
      }
      await waitForTrigger(journalDir, signal, reconcileBackoffMs);
      // Fail closed on EVERY lease refresh, not only startup: a failed refresh
      // durably removes the watcher's own lease (so an old ready lease can never
      // continue authorizing) and exits nonzero so systemd restarts it.
      await refreshLease(custodyLockPath, expectedOwnerUid, configSha256, {
        unit: guardianUnit,
        custodyIdentitySha256: published.custodyIdentitySha256,
        guardianModuleSha256: published.guardianModuleSha256,
        expectedUnitSha256: published.expectedUnitSha256,
        expectedNodePath: published.expectedNodePath,
        ready,
      });
    }
  } finally {
    await removeOwnLease();
    process.removeListener("SIGTERM", onSignal);
    process.removeListener("SIGINT", onSignal);
    if (signal) signal.removeEventListener("abort", onAbort);
  }
  return { stopped: true, ready: true };
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 3 || !["recover", "watch"].includes(argv[0]) || argv[1] !== "--config" || !argv[2]) fail("Usage: maintenance-recovery-guardian.mjs recover|watch --config PRIVATE_JSON");
  return { command: argv[0], configPath: resolve(argv[2]) };
}

export async function main(argv = process.argv.slice(2), dependencies = {}) {
  const options = parseArguments(argv);
  if (options.command === "watch") {
    await watchMaintenanceRecoveryGuardian(options.configPath, dependencies);
    return;
  }
  const value = await runMaintenanceRecoveryGuardian(options.configPath, dependencies);
  process.stdout.write(`${JSON.stringify(value)}\n`);
  if (value.status !== NO_ACTIVE_STATUS && value.status !== SETTLED_GUARDIAN_STATUS) process.exitCode = 3;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-qualification-maintenance-recovery: ${error instanceof WorkMaintenanceRecoveryGuardianError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const maintenanceRecoveryGuardianBoundaries = Object.freeze({ result: GUARDIAN_RESULT_BOUNDARY, attention: GUARDIAN_ATTENTION_BOUNDARY });
export const maintenanceRecoveryGuardianManualStatuses = MANUAL_STATUSES;
export const maintenanceRecoveryGuardianStatuses = GUARDIAN_STATUSES;

export function buildRecoveryQualification(qualification, prepared, backendConfigPath, prepareDependencies = {}) {
  if (!qualification || typeof qualification !== "object" || Array.isArray(qualification)) fail("recovery qualification is invalid");
  if (!Object.hasOwn(qualification, PREPARED_RECOVERY_OPERATION)) {
    Object.defineProperty(qualification, PREPARED_RECOVERY_OPERATION, { value: Object.freeze({ prepared, backendConfigPath, prepareDependencies: Object.freeze({ ...(prepareDependencies ?? {}) }) }) });
  }
  return qualification;
}

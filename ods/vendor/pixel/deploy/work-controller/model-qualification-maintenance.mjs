import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, realpath, unlink } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  prepareDockerModelQualificationMaintenance,
  runDockerModelQualification,
} from "./model-qualification-docker.mjs";
import { inspectMaintenanceCustodyLock, validateMaintenanceCustodyBinding, withMaintenanceCustody } from "./maintenance-custody.mjs";
import { GUARDIAN_UNIT, requireLiveGuardianLease } from "./maintenance-recovery-guardian-lease.mjs";
import { renderGuardianUnit } from "./maintenance-recovery-guardian-unit.mjs";
import {
  SETTLED_MAINTENANCE_STATUSES,
  advanceRecoveryJournal,
  assertNoActiveRecoveryJournal,
  cancelRecoveryJournal,
  createRecoveryJournal,
  settleRecoveryJournal,
} from "./maintenance-recovery-journal.mjs";

const execute = promisify(execFile);
const MAX_JSON_BYTES = 1024 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/u;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/u;
const STARTED_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u;
const ORIGIN_RE = /^http:\/\/127\.0\.0\.1:([0-9]{4,5})$/u;
const CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/work-model-qualification-maintenance-v1.schema.json";
const ZERO_SHA256 = "0".repeat(64);
const CONFIG_BOUNDARY = "Owner-private exact maintenance binding for one production container and one local-model qualification operation. Configuration grants no stop, start, inference, credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const REVIEW_BOUNDARY = "Content-free review of one exact production maintenance and local-model qualification operation. Confirmation may stop only the bound running container, execute the bound internal-only qualification, prove temporary isolation absent, restart the same container, and perform one credential-free loopback readiness probe; it grants no other execution, network, credential, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const RESULT_BOUNDARY = "Content-free result for one exact local-model qualification maintenance operation. It records restoration and isolation state without exposing model content, prompts, responses, paths, or credentials and grants no further execution, network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const DOCKER_ENV = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" });
const QUALIFICATION_FAILURE_STAGES = new Set(["none", "qualification-preflight", "pre-start-guard", "backend-start", "qualification-runner", "evidence-verification", "qualification-cleanup", "production-custody"]);

export class WorkModelQualificationMaintenanceError extends Error {}

function fail(message) { throw new WorkModelQualificationMaintenanceError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}

async function readPrivateJsonRecord(path, label, expectedOwnerUid) {
  absolutePath(path, label);
  let actual, record;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, MAX_JSON_BYTES, label), realpath(path)]); }
  catch { fail(`${label} could not be opened safely`); }
  const info = record.details;
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || !samePath(actual, path) || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private, singular, and real`);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  try { return { value: parseStrictJson(text, label), bytes: record.bytes }; } catch { fail(`${label} is not strict JSON`); }
}

async function readPrivateJson(path, label, expectedOwnerUid) { return (await readPrivateJsonRecord(path, label, expectedOwnerUid)).value; }

export function validateProductionMaintenanceBinding(production, label = "maintenance") {
  exactKeys(production, ["containerName", "containerId", "imageDigest", "expectedStartedAt", "expectedRestartCount", "stopTimeoutSeconds", "restoreTimeoutSeconds", "probeIntervalMilliseconds", "readinessOrigin", "readinessModelId", "maxResponseBytes"], `${label} production binding`);
  const origin = ORIGIN_RE.exec(production.readinessOrigin ?? "");
  if (!NAME_RE.test(production.containerName ?? "") || !SHA_RE.test(production.containerId ?? "") || production.containerId === ZERO_SHA256 || !IMAGE_RE.test(production.imageDigest ?? "") || production.imageDigest === `sha256:${ZERO_SHA256}` || !STARTED_RE.test(production.expectedStartedAt ?? "") || !Number.isFinite(Date.parse(production.expectedStartedAt)) || !origin || Number(origin[1]) > 65535 || !MODEL_RE.test(production.readinessModelId ?? "")) fail("maintenance production identity is invalid");
  integer(production.expectedRestartCount, 0, 2147483647, "maintenance expected restart count");
  integer(production.stopTimeoutSeconds, 10, 600, "maintenance stop timeout");
  integer(production.restoreTimeoutSeconds, 60, 7200, "maintenance restore timeout");
  integer(production.probeIntervalMilliseconds, 250, 10000, "maintenance probe interval");
  integer(production.maxResponseBytes, 1024, 1048576, "maintenance readiness response limit");
  return structuredClone(production);
}

export function validateModelQualificationMaintenanceConfiguration(value) {
  exactKeys(value, ["$schema", "schemaVersion", "qualificationDockerConfigPath", "custody", "production", "boundary"], "maintenance configuration");
  if (value.$schema !== CONFIG_SCHEMA || value.schemaVersion !== 1 || value.boundary !== CONFIG_BOUNDARY) fail("maintenance configuration contract is invalid");
  absolutePath(value.qualificationDockerConfigPath, "maintenance qualificationDockerConfigPath");
  validateMaintenanceCustodyBinding(value.custody);
  validateProductionMaintenanceBinding(value.production);
  return structuredClone(value);
}

function productionFingerprint(container) {
  return {
    containerId: container.Id,
    imageDigest: container.Image,
    startedAt: container.State.StartedAt,
    restartCount: container.RestartCount,
  };
}

function validateProduction(container, production, { running, requireReviewedStart }) {
  const restartCountMatches = container?.RestartCount === production.expectedRestartCount
    || requireReviewedStart === false && container?.RestartCount === 0;
  // Docker resets RestartCount to zero when an operator intentionally stops
  // and starts the same container.  Accept that planned normalization only
  // after the reviewed stop.  The resulting fingerprint is captured before
  // readiness and compared again afterward, so a restart during recovery is
  // still detected even when it happens to equal the pre-maintenance count.
  if (!container || typeof container !== "object" || Array.isArray(container) || container.Id !== production.containerId || container.Name !== `/${production.containerName}` || container.Image !== production.imageDigest || !restartCountMatches || typeof container.State?.Running !== "boolean" || container.State.Paused === true || container.State.Restarting === true || container.State.Dead === true) fail("production container differs from the exact maintenance binding");
  if (container.State.Running !== running) fail(running ? "production container is not exactly running" : "production container did not stop exactly");
  if (requireReviewedStart && container.State.StartedAt !== production.expectedStartedAt) fail("production start identity changed after maintenance review");
  return productionFingerprint(container);
}

function sameProductionIdentityRunning(container, production) {
  return Boolean(container && typeof container === "object" && !Array.isArray(container)
    && container.Id === production.containerId && container.Name === `/${production.containerName}`
    && container.Image === production.imageDigest && container.State?.Running === true
    && container.State?.Paused !== true && container.State?.Restarting !== true && container.State?.Dead !== true);
}

async function dockerInspectMaybe(dockerPath, kind, target) {
  let stdout;
  try { ({ stdout } = await execute(dockerPath, [kind, "inspect", target], { encoding: "utf8", windowsHide: true, timeout: 15000, maxBuffer: 4 * 1024 * 1024, env: DOCKER_ENV })); }
  catch (error) {
    if (error?.code === 1 && /(?:no such (?:object|container|network)|(?:container|network) .* not found)/iu.test(error?.stderr ?? "")) return null;
    throw error;
  }
  let value;
  try { value = parseStrictJson(stdout, "maintenance Docker inspection"); } catch { fail("maintenance Docker inspection is invalid"); }
  if (!Array.isArray(value) || value.length !== 1) fail("maintenance Docker inspection is invalid");
  return value[0];
}

async function defaultWaitForReadiness(production) {
  const deadline = Date.now() + production.restoreTimeoutSeconds * 1000;
  const endpoint = `${production.readinessOrigin}/v1/models`;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(endpoint, { method: "GET", redirect: "error", credentials: "omit", signal: AbortSignal.timeout(Math.min(10000, production.probeIntervalMilliseconds * 4)) });
      const declared = Number(response.headers.get("content-length"));
      if (response.status === 200 && (!Number.isFinite(declared) || declared <= production.maxResponseBytes)) {
        const bytes = Buffer.from(await response.arrayBuffer());
        if (bytes.length <= production.maxResponseBytes) {
          const body = parseStrictJson(bytes.toString("utf8"), "production readiness response");
          if (Array.isArray(body?.data) && body.data.some((entry) => entry?.id === production.readinessModelId)) return true;
        }
      }
    } catch {}
    await new Promise((resolve_) => setTimeout(resolve_, production.probeIntervalMilliseconds));
  }
  return false;
}

function maintenanceOperation(configuration, qualification, fingerprint, custodyIdentitySha256) {
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-qualification-maintenance",
    qualificationOperationSha256: qualification.qualificationOperationSha256,
    productionBindingSha256: sha({ production: configuration.production, fingerprint }),
    custodyIdentitySha256,
  });
}

function recoveryJournalIdentity(loaded) {
  return {
    maintenanceOperationSha256: loaded.operationSha256,
    qualificationOperationSha256: loaded.qualification.qualificationOperationSha256,
    custodyIdentitySha256: loaded.custody.identitySha256,
    configurationSha256: sha(loaded.configBytes),
    ownerUid: loaded.expectedOwnerUid,
    production: {
      containerName: loaded.configuration.production.containerName,
      containerId: loaded.configuration.production.containerId,
      imageDigest: loaded.configuration.production.imageDigest,
      expectedStartedAt: loaded.configuration.production.expectedStartedAt,
      expectedRestartCount: loaded.configuration.production.expectedRestartCount,
    },
    qualification: {
      backendContainerName: loaded.qualification.backendContainerName,
      backendNetworkName: loaded.qualification.backendNetworkName,
      qualificationContainerName: loaded.qualification.qualificationContainerName,
    },
  };
}

function recoveryJournalRecord(loaded) {
  return {
    schemaVersion: 1,
    kind: "pixel-maintenance-recovery-journal",
    operation: "pixel-work-model-qualification-maintenance",
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    ...recoveryJournalIdentity(loaded),
  };
}

async function settleOrRetainRecoveryJournal(loaded, resultValue, journalLockPath, journalIdentity) {
  if (SETTLED_MAINTENANCE_STATUSES.includes(resultValue.status)) {
    await settleRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, { status: resultValue.status, expectedIdentity: journalIdentity });
  }
}

// The maintenance configuration and every transitive authority-bearing input
// (Docker qualification config, backend config, environment, qualification
// config, and the live production inspection) are prepared fresh each call. The
// pre-lock preparation is used only as a routing/review binding; the run path
// re-prepares everything after custody is acquired and refuses any divergence.
async function prepareMaintenance(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("maintenance requires a non-root service identity");
  const configRecord = await readPrivateJsonRecord(configPath, "private qualification maintenance configuration", expectedOwnerUid);
  const configuration = validateModelQualificationMaintenanceConfiguration(configRecord.value);
  // Refuse while any active (nonterminal) recovery journal exists before any
  // production inspection or mutation.
  await assertNoActiveRecoveryJournal(configuration.custody.lockPath, expectedOwnerUid);
  const prepare = dependencies.prepareQualification ?? prepareDockerModelQualificationMaintenance;
  const inspectContainer = dependencies.inspectContainer ?? ((target, dockerPath) => dockerInspectMaybe(dockerPath, "container", target));
  const [qualification, custody] = await Promise.all([
    prepare(configuration.qualificationDockerConfigPath, { expectedOwnerUid, ...(dependencies.qualificationDependencies ?? {}) }),
    inspectMaintenanceCustodyLock(configuration.custody, expectedOwnerUid),
  ]);
  if (!qualification || !SHA_RE.test(qualification.qualificationOperationSha256 ?? "") || qualification.qualificationOperationSha256 === ZERO_SHA256 || typeof qualification.dockerPath !== "string" || !NAME_RE.test(qualification.backendContainerName ?? "") || !NAME_RE.test(qualification.backendNetworkName ?? "") || !NAME_RE.test(qualification.qualificationContainerName ?? "")) fail("qualification maintenance binding is invalid");
  absolutePath(qualification.dockerPath, "qualification Docker client path");
  const before = await inspectContainer(configuration.production.containerId, qualification.dockerPath);
  const fingerprint = validateProduction(before, configuration.production, { running: true, requireReviewedStart: true });
  const after = await inspectContainer(configuration.production.containerId, qualification.dockerPath);
  if (canonical(validateProduction(after, configuration.production, { running: true, requireReviewedStart: true })) !== canonical(fingerprint)) fail("production changed while preparing maintenance review");
  const operation = maintenanceOperation(configuration, qualification, fingerprint, custody.identitySha256);
  return { expectedOwnerUid, configuration, configBytes: configRecord.bytes, qualification, custody, operation, operationSha256: sha(operation), fingerprint, inspectContainer, prepare, configPath };
}

async function loadMaintenance(configPath, dependencies = {}) {
  return prepareMaintenance(configPath, dependencies);
}

// Re-prepare the exact qualification and production under the acquired custody
// lock, then compare every authority-bearing identity to the reviewed pre-lock
// snapshot. Any mutation or path substitution refuses before journal creation or
// Docker mutation.
async function prepareMaintenanceUnderCustody(configPath, dependencies, preLock, custodyIdentitySha256) {
  const current = await prepareMaintenance(configPath, dependencies);
  if (sha(current.configBytes) !== sha(preLock.configBytes)) fail("maintenance configuration raw bytes changed while acquiring custody; refusing");
  if (current.custody.identitySha256 !== custodyIdentitySha256) fail("maintenance custody identity changed under the lock");
  if (current.qualification.qualificationOperationSha256 !== preLock.qualification.qualificationOperationSha256) fail("qualification operation identity changed under the lock");
  if (!samePath(current.qualification.dockerPath, preLock.qualification.dockerPath)) fail("qualification executable path changed under the lock");
  if (current.qualification.backendContainerName !== preLock.qualification.backendContainerName || current.qualification.backendNetworkName !== preLock.qualification.backendNetworkName || current.qualification.qualificationContainerName !== preLock.qualification.qualificationContainerName) fail("qualification identity changed under the lock");
  if (canonical(current.operation) !== canonical(preLock.operation)) fail("maintenance operation changed under the lock");
  if (current.operationSha256 !== preLock.operationSha256) fail("maintenance operation hash changed under the lock");
  if (canonical(current.fingerprint) !== canonical(preLock.fingerprint)) fail("production binding changed under the lock");
  return current;
}

export async function reviewModelQualificationMaintenance(configPath, dependencies = {}) {
  const loaded = await loadMaintenance(configPath, dependencies);
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-qualification-maintenance-review",
    status: "confirmation-required",
    maintenanceOperationSha256: loaded.operationSha256,
    qualificationOperationSha256: loaded.qualification.qualificationOperationSha256,
    confirmation: { option: "--confirm-maintenance-operation-sha256", sha256: loaded.operationSha256 },
    changes: { acquiresExactCustodyLock: true, stopsExactProductionContainer: true, runsExactQualification: true, requiresQualificationIsolationAbsentBeforeRestore: true, restartsSameProductionContainer: true, performsCredentialFreeLoopbackReadinessProbe: true, usesExternalNetwork: false, usesCredentials: false, deploysCode: false },
    authority: { grantsExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false },
    boundary: REVIEW_BOUNDARY,
  });
}

function result(loaded, status, qualificationStatus, qualificationReceiptSha256, qualificationFailureStage, qualificationDiagnosticSha256, state) {
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-qualification-maintenance-result",
    status,
    maintenanceOperationSha256: loaded.operationSha256,
    qualificationOperationSha256: loaded.qualification.qualificationOperationSha256,
    qualificationStatus,
    qualificationReceiptSha256,
    qualificationFailureStage,
    qualificationDiagnosticAvailable: qualificationDiagnosticSha256 !== null,
    qualificationDiagnosticSha256,
    productionRestored: state.productionRestored,
    productionReady: state.productionReady,
    qualificationIsolationAbsent: state.qualificationIsolationAbsent,
    productionCustodyMaintained: state.productionCustodyMaintained,
    credentialsUsed: false,
    externalNetworkUsed: false,
    authority: { grantsExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false },
    boundary: RESULT_BOUNDARY,
  });
}

const GUARDIAN_MODULE_URL = new URL("./maintenance-recovery-guardian.mjs", import.meta.url);
const GUARDIAN_MODULE_PATH = fileURLToPath(GUARDIAN_MODULE_URL);
async function readGuardianModuleSha() { return sha(await readFile(GUARDIAN_MODULE_PATH)); }

export async function runModelQualificationMaintenance(configPath, confirmation, dependencies = {}) {
  const preLock = await loadMaintenance(configPath, dependencies);
  if (!SHA_RE.test(confirmation ?? "") || confirmation !== preLock.operationSha256) fail("confirmation differs from the exact maintenance operation");
  const withCustody = dependencies.withCustody ?? ((binding, operation) => withMaintenanceCustody(binding, preLock.expectedOwnerUid, operation));
  return withCustody(preLock.configuration.custody, async (custodyIdentitySha256 = preLock.custody.identitySha256) => {
  if (custodyIdentitySha256 !== preLock.custody.identitySha256) fail("maintenance custody identity differs from the reviewed operation");
  // The pre-lock load is only a routing/review binding. Securely reread the raw
  // maintenance configuration and every transitive authority-bearing input,
  // reprepare the exact qualification, and re-inspect production under the lock.
  // Every action, journal record, and lease check below derives from this
  // under-lock load.
  const loaded = await prepareMaintenanceUnderCustody(configPath, dependencies, preLock, custodyIdentitySha256);
  const dockerPath = loaded.qualification.dockerPath;
  const actions = maintenanceActions(loaded, dependencies, dockerPath);
  const state = maintenanceState();
  const journalLockPath = loaded.configuration.custody.lockPath;
  const journalIdentity = recoveryJournalIdentity(loaded);
  const requireGuardianLease = dependencies.requireGuardianLease ?? requireLiveGuardianLease;
  const configSha256 = sha(loaded.configBytes);
  const guardianModuleSha256 = await readGuardianModuleSha();
  // Bind the lease to the REVIEWED EXPECTED concrete unit render and the exact
  // real Node executable, derived under the custody lock from the same config/
  // node/guardian/owner inputs reviewed by the supervise render path. A
  // substituted unit whose lease and live systemd facts agree with each other
  // but disagree with this reviewed expected render is refused.
  const expectedNodePath = await realpath(process.execPath);
  const rendered = await renderGuardianUnit({ configPath, nodePath: expectedNodePath, guardianPath: GUARDIAN_MODULE_PATH, expectedOwnerUid: loaded.expectedOwnerUid });
  const expectedUnitSha256 = rendered.renderSha256;
  const guardLease = () => requireGuardianLease(journalLockPath, loaded.expectedOwnerUid, configSha256, {
    maxAgeMs: dependencies.guardianLeaseMaxAgeMs,
    bootId: dependencies.guardianLeaseBootId,
    unit: dependencies.guardianUnit ?? GUARDIAN_UNIT,
    configPath,
    custodyIdentitySha256: loaded.custody.identitySha256,
    guardianModuleSha256,
    guardianModulePath: GUARDIAN_MODULE_PATH,
    expectedUnitSha256,
    expectedNodePath,
  });
  let journalCreated = false;
  let stopInvoked = false;
  try {
    const preStop = await actions.inspectContainer(loaded.configuration.production.containerId, dockerPath);
    validateProduction(preStop, loaded.configuration.production, { running: true, requireReviewedStart: true });
    // The supported run path is supervised: require a fresh, config-bound live
    // guardian lease immediately before journal creation and again immediately
    // before the stop, so an absent, dead, stale, or substituted guardian fails
    // closed before production is ever touched.
    await guardLease();
    await createRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, recoveryJournalRecord(loaded));
    journalCreated = true;
    await guardLease();
    stopInvoked = true;
    await actions.stopProduction(loaded.configuration.production.containerId);
    const stopped = await actions.inspectContainer(loaded.configuration.production.containerId, dockerPath);
    validateProduction(stopped, loaded.configuration.production, { running: false, requireReviewedStart: true });
    state.productionStopped = true;
  } catch (error) {
    const observed = await actions.inspectContainer(loaded.configuration.production.containerId, dockerPath).catch(() => null);
    let confirmedStopped = false;
    try { validateProduction(observed, loaded.configuration.production, { running: false, requireReviewedStart: true }); confirmedStopped = true; } catch {}
    if (confirmedStopped) {
      state.productionStopped = true;
    } else if (stopInvoked) {
      // A stop was invoked but its result could not be confirmed (inspection
      // failed) or production has since restarted. We must never infer "no
      // destructive action" from the final running state: the journal is
      // retained as honest, recoverable mutation state.
      const ambiguous = new WorkModelQualificationMaintenanceError("production stop outcome is unconfirmed or production restarted; recovery journal retained");
      Object.defineProperty(ambiguous, "qualificationFailureStage", { value: "production-custody" });
      throw ambiguous;
    } else {
      // Production was never stopped and no stop was invoked. If we created a
      // journal but committed no destructive action (for example the guardian
      // died between the two lease checks), cancel that inert prepared journal
      // through the identity-checked durable cancellation primitive so a failed
      // guardian proof does not leave a blocking active journal and no
      // substituted path is ever unlinked.
      if (journalCreated && !state.productionStopped) {
        await cancelRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, journalIdentity);
      }
      throw error;
    }
  }
  if (state.productionStopped) await advanceRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, "production-stopped", journalIdentity);
  const assertProductionStopped = async () => {
    const observed = await actions.inspectContainer(loaded.configuration.production.containerId, dockerPath);
    try { validateProduction(observed, loaded.configuration.production, { running: false, requireReviewedStart: true }); }
    catch {
      const error = new WorkModelQualificationMaintenanceError("production custody changed during qualification");
      Object.defineProperty(error, "qualificationFailureStage", { value: "production-custody" });
      throw error;
    }
  };
  await advanceRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, "qualification-active", journalIdentity);
  try {
    const qualificationResult = await actions.runQualification(loaded.configuration.qualificationDockerConfigPath, loaded.qualification.qualificationOperationSha256, {
      expectedOwnerUid: loaded.expectedOwnerUid,
      ...(dependencies.qualificationDependencies ?? {}),
      preStartGuard: assertProductionStopped,
      reviewedMaintenancePreparation: loaded.qualification,
    });
    state.qualificationStatus = ["qualified", "degraded", "failed"].includes(qualificationResult?.status) ? qualificationResult.status : "error";
    state.qualificationReceiptSha256 = SHA_RE.test(qualificationResult?.receiptSha256 ?? "") ? qualificationResult.receiptSha256 : null;
  } catch (error) {
    state.qualificationStatus = "error";
    state.qualificationFailureStage = QUALIFICATION_FAILURE_STAGES.has(error?.qualificationFailureStage) ? error.qualificationFailureStage : "qualification-preflight";
    state.qualificationDiagnosticSha256 = SHA_RE.test(error?.qualificationDiagnosticSha256 ?? "") ? error.qualificationDiagnosticSha256 : null;
    if (state.qualificationFailureStage === "production-custody") state.productionCustodyMaintained = false;
  }
  try { await assertProductionStopped(); }
  catch {
    state.productionCustodyMaintained = false;
    state.qualificationStatus = "error";
    state.qualificationReceiptSha256 = null;
    state.qualificationFailureStage = "production-custody";
  }
  await advanceRecoveryJournal(journalLockPath, loaded.expectedOwnerUid, "restore-pending", journalIdentity);
  try {
    const resultValue = await restoreQualificationProduction(loaded, actions, state);
    await settleOrRetainRecoveryJournal(loaded, resultValue, journalLockPath, journalIdentity);
    return resultValue;
  } catch (primaryError) {
    return await restorationAfterError(loaded, actions, state, primaryError);
  }
  });
}

function maintenanceState() {
  return {
    productionStopped: false,
    qualificationStatus: "not-run",
    qualificationReceiptSha256: null,
    qualificationFailureStage: "none",
    qualificationDiagnosticSha256: null,
    productionCustodyMaintained: true,
  };
}

function maintenanceActions(loaded, dependencies, dockerPath) {
  return {
    dockerPath,
    inspectContainer: loaded.inspectContainer,
    inspectNetwork: dependencies.inspectNetwork ?? ((target, path) => dockerInspectMaybe(path, "network", target)),
    stopProduction: dependencies.stopProduction ?? ((target) => execute(dockerPath, ["container", "stop", "--time", String(loaded.configuration.production.stopTimeoutSeconds), target], { encoding: "utf8", windowsHide: true, timeout: (loaded.configuration.production.stopTimeoutSeconds + 30) * 1000, maxBuffer: 1024 * 1024, env: DOCKER_ENV })),
    startProduction: dependencies.startProduction ?? ((target) => execute(dockerPath, ["container", "start", target], { encoding: "utf8", windowsHide: true, timeout: 60000, maxBuffer: 1024 * 1024, env: DOCKER_ENV })),
    runQualification: dependencies.runQualification ?? runDockerModelQualification,
    waitForReadiness: dependencies.waitForReadiness ?? defaultWaitForReadiness,
  };
}

async function restoreQualificationProduction(loaded, actions, state) {
  const [backend, runner, network] = await Promise.all([
    actions.inspectContainer(loaded.qualification.backendContainerName, actions.dockerPath),
    actions.inspectContainer(loaded.qualification.qualificationContainerName, actions.dockerPath),
    actions.inspectNetwork(loaded.qualification.backendNetworkName, actions.dockerPath),
  ]);
  const qualificationIsolationAbsent = backend === null && runner === null && network === null;
  if (!qualificationIsolationAbsent) {
    let productionRunning = false;
    try {
      const observed = await actions.inspectContainer(loaded.configuration.production.containerId, actions.dockerPath);
      validateProduction(observed, loaded.configuration.production, { running: true, requireReviewedStart: false });
      productionRunning = true;
    } catch {}
    return result(loaded, productionRunning ? "manual-attention-isolation-present-production-running" : "manual-attention-production-held", state.qualificationStatus, state.qualificationReceiptSha256, state.qualificationFailureStage, state.qualificationDiagnosticSha256, { productionRestored: productionRunning, productionReady: false, qualificationIsolationAbsent: false, productionCustodyMaintained: state.productionCustodyMaintained });
  }
  if (!state.productionStopped) fail("production stop state was not established");
  let startFailed = false;
  try { await actions.startProduction(loaded.configuration.production.containerId); }
  catch { startFailed = true; }
  let restored, restoredFingerprint;
  try {
    restored = await actions.inspectContainer(loaded.configuration.production.containerId, actions.dockerPath);
    restoredFingerprint = validateProduction(restored, loaded.configuration.production, { running: true, requireReviewedStart: false });
  }
  catch { return result(loaded, "manual-attention-production-not-ready", state.qualificationStatus, state.qualificationReceiptSha256, state.qualificationFailureStage, state.qualificationDiagnosticSha256, { productionRestored: false, productionReady: false, qualificationIsolationAbsent: true, productionCustodyMaintained: state.productionCustodyMaintained }); }
  const productionReady = await actions.waitForReadiness(loaded.configuration.production).catch(() => false);
  if (!productionReady || startFailed) return result(loaded, "manual-attention-production-not-ready", state.qualificationStatus, state.qualificationReceiptSha256, state.qualificationFailureStage, state.qualificationDiagnosticSha256, { productionRestored: true, productionReady, qualificationIsolationAbsent: true, productionCustodyMaintained: state.productionCustodyMaintained });
  const postReadiness = await actions.inspectContainer(loaded.configuration.production.containerId, actions.dockerPath).catch(() => null);
  let stableRestore = false;
  try {
    stableRestore = canonical(validateProduction(postReadiness, loaded.configuration.production, { running: true, requireReviewedStart: false })) === canonical(restoredFingerprint);
  } catch {}
  if (!stableRestore) return result(loaded, "manual-attention-production-custody-changed", state.qualificationStatus, state.qualificationReceiptSha256, "production-custody", state.qualificationDiagnosticSha256, { productionRestored: sameProductionIdentityRunning(postReadiness, loaded.configuration.production), productionReady: true, qualificationIsolationAbsent: true, productionCustodyMaintained: false });
  const status = state.qualificationStatus === "qualified" ? "qualified-production-restored" : state.qualificationStatus === "degraded" ? "degraded-production-restored" : state.qualificationStatus === "failed" ? "failed-production-restored" : "qualification-error-production-restored";
  return result(loaded, status, state.qualificationStatus, state.qualificationReceiptSha256, state.qualificationFailureStage, state.qualificationDiagnosticSha256, { productionRestored: true, productionReady: true, qualificationIsolationAbsent: true, productionCustodyMaintained: state.productionCustodyMaintained });
}

async function restorationAfterError(loaded, actions, state, primaryError) {
  try {
    await restoreQualificationProduction(loaded, actions, state);
  } catch {
    const error = new WorkModelQualificationMaintenanceError("maintenance restoration failed after an unexpected error");
    Object.defineProperty(error, "cause", { value: primaryError });
    Object.defineProperty(error, "restorationFailed", { value: true });
    throw error;
  }
  throw primaryError;
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["review", "run"].includes(argv[0])) fail("Usage: model-qualification-maintenance.mjs review --config PRIVATE_JSON | run --config PRIVATE_JSON --confirm-maintenance-operation-sha256 HASH");
  if (argv[0] === "review" && argv.length === 3 && argv[1] === "--config" && argv[2]) return { operation: "review", configPath: resolve(argv[2]), confirmation: null };
  if (argv[0] === "run" && argv.length === 5 && argv[1] === "--config" && argv[2] && argv[3] === "--confirm-maintenance-operation-sha256" && argv[4]) return { operation: "run", configPath: resolve(argv[2]), confirmation: argv[4] };
  fail("qualification maintenance arguments are incomplete, unknown, or duplicated");
}

export async function main(argv = process.argv.slice(2), dependencies = {}) {
  const options = parseArguments(argv);
  const value = options.operation === "review" ? await reviewModelQualificationMaintenance(options.configPath, dependencies) : await runModelQualificationMaintenance(options.configPath, options.confirmation, dependencies);
  process.stdout.write(`${JSON.stringify(value)}\n`);
  if (value.operation.endsWith("-result") && value.status !== "qualified-production-restored") process.exitCode = value.status.startsWith("manual-attention") ? 3 : 2;
}

const CLI_SIGNALS = Object.freeze(["SIGINT", "SIGTERM", "SIGHUP"]);

export async function runModelQualificationMaintenanceCli(argv = process.argv.slice(2), dependencies = {}) {
  let terminationRequested = false;
  const onSignal = () => {
    if (!terminationRequested) terminationRequested = true;
  };
  for (const signal of CLI_SIGNALS) process.on(signal, onSignal);
  let failure = null;
  try {
    await main(argv, dependencies);
  } catch (error) {
    failure = error;
  } finally {
    for (const signal of CLI_SIGNALS) process.removeListener(signal, onSignal);
  }
  if (failure) {
    process.stderr.write(`pixel-work-model-qualification-maintenance: ${failure instanceof WorkModelQualificationMaintenanceError ? failure.message : "unexpected failure"}\n`);
    if (!process.exitCode) process.exitCode = 1;
  }
  if (terminationRequested && !process.exitCode) process.exitCode = 1;
  return { terminationRequested, exitCode: process.exitCode, error: failure };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runModelQualificationMaintenanceCli().catch((error) => {
    process.stderr.write(`pixel-work-model-qualification-maintenance: ${error instanceof WorkModelQualificationMaintenanceError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const modelQualificationMaintenanceBoundaries = Object.freeze({ configuration: CONFIG_BOUNDARY, review: REVIEW_BOUNDARY, result: RESULT_BOUNDARY });

// Campaign maintenance uses the same production-identity and restoration
// boundary.  Keep these primitives internal to the work-controller modules so
// qualification and campaign windows cannot quietly diverge on what counts as
// the exact production container or a successful readiness restoration.
export const productionMaintenancePrimitives = Object.freeze({
  DOCKER_ENV,
  readPrivateJson,
  readPrivateJsonRecord,
  productionFingerprint,
  validateProduction,
  dockerInspectMaybe,
  defaultWaitForReadiness,
  sameProductionIdentityRunning,
  validateProductionMaintenanceBinding,
});

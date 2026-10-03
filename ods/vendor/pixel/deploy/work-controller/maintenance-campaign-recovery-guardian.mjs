import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { watch } from "node:fs";
import { readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { inspectMaintenanceCustodyLock, withMaintenanceCustody } from "./maintenance-custody.mjs";
import {
  deriveProcessIdentity,
  isProcessAlive,
  refreshCampaignGuardianLease,
  removeCampaignGuardianLease,
  requireLiveCampaignGuardianLease,
} from "./maintenance-recovery-guardian-lease.mjs";
import { renderCampaignGuardianUnit } from "./maintenance-campaign-recovery-guardian-unit.mjs";
import {
  CAMPAIGN_RECOVERY_PHASES,
  advanceCampaignRecoveryJournal,
  cancelCampaignRecoveryJournal,
  deriveCampaignJournalIdentity,
  deriveCampaignJournalIdentitySha256,
  inspectActiveCampaignRecoveryJournal,
  settleCampaignRecoveryJournal,
} from "./maintenance-recovery-journal.mjs";
import {
  loadCampaignRecoveryBinding,
  requireCleanInventory,
  validateModelCampaignMaintenanceConfiguration,
} from "./model-campaign-maintenance.mjs";
import { productionMaintenancePrimitives } from "./model-qualification-maintenance.mjs";
import {
  classifyStartResult,
  dockerEngineStart,
  DOCKER_SOCKET_PATH,
  newStartNonce,
  secureSocketIdentity,
} from "./docker-engine-start.mjs";
import {
  deriveCampaignChildUnitName,
  exactCgroupLineMatches,
  isAcceptedTerminalState,
  proveCampaignChildCgroupEmpty,
} from "./maintenance-campaign-child-launcher.mjs";
import {
  readInvocationIdFromProc,
  resolveTrustedSystemctl,
  systemdShowUnit,
} from "./maintenance-recovery-guardian-systemd.mjs";

const execute = promisify(execFile);
const SHA_RE = /^[a-f0-9]{64}$/u;
const ZERO_SHA256 = "0".repeat(64);
const PROVEN_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u;
const OPERATION = "pixel-work-model-campaign-maintenance-recovery";
const CAMPAIGN_GUARDIAN_UNIT = "pixel-campaign-maintenance-recovery.service";
const GUARDIAN_RESULT_BOUNDARY = "Content-free campaign recovery/guardian result for one exact campaign maintenance operation. Each invocation performs at most one identity-bound phase transition/effect bundle and then returns, so no effect ever runs under a stale lease. It may cancel an inert prepared journal, stop the exact bound production container (only from the durable production-stop-authorized phase), stop only the exact nonce-derived campaign child unit after recorded process identity proof, verify exact isolation, advance the journal monotonically to isolation-clean, authorize the exact bound production start with a one-time nonce and exact Docker socket identity, perform exactly one fail-closed Docker Engine start request (only a definite 204 for the exact container/endpoint is attributable success), perform one bounded credential-free loopback readiness probe before the durable readiness-proven phase, and, only from readiness-proven with a durably bound campaign outcome, settle exactly once via a content-free receipt-first-then-unlink terminal receipt whose status is derived mechanically from that outcome. It never removes foreign resources, relaunches a campaign child, or claims campaign success/completion beyond a mechanically derived terminal outcome; a journal with no durably bound outcome holds safely/manual and never fabricates pass. It grants only the minimum exact local authority needed to restart and probe the already-bound production container, and no credential, external network, provider call, external effect, deployment, publication, promotion, completion, or settlement authority.";
const GUARDIAN_ATTENTION_BOUNDARY = "Content-free manual-attention result for an unresolved campaign recovery. It retains the active campaign recovery journal and records only that an identity, config, production, child, isolation, endpoint, start, or readiness binding could not be proven exact. It makes no ambiguous or substituted mutation and performs no additional destructive action after the unresolved condition. It grants no credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const NO_ACTIVE_STATUS = "no-active-recovery";
const PREPARED_CANCELLED_STATUS = "campaign-recovery-prepared-cancelled";
const CAMPAIGN_RECOVERY_PROGRESS_STATUS = "campaign-recovery-progress";
const ISOLATION_CLEAN_STATUS = "campaign-recovery-isolation-clean";
const PRODUCTION_START_AUTHORIZED_STATUS = "campaign-recovery-production-start-authorized";
const PRODUCTION_STARTED_STATUS = "campaign-recovery-production-started";
const READINESS_PROVEN_STATUS = "campaign-recovery-readiness-proven";
const M8_HOLD_STATUS = "campaign-recovery-m8-hold";
const CAMPAIGN_SETTLED_STATUS = "campaign-recovery-settled";
const MANUAL_STATUSES = Object.freeze([
  "manual-attention-campaign-config-changed",
  "manual-attention-campaign-identity-changed",
  "manual-attention-campaign-production-missing",
  "manual-attention-campaign-production-substituted",
  "manual-attention-campaign-endpoint-missing",
  "manual-attention-campaign-start-rejected",
  "manual-attention-campaign-start-not-proven",
  "manual-attention-campaign-production-not-running",
  "manual-attention-campaign-readiness-failed",
  "manual-attention-campaign-readiness-unstable",
  "manual-attention-campaign-child-not-found",
  "manual-attention-campaign-child-substituted",
  "manual-attention-campaign-child-not-terminal",
  "manual-attention-campaign-child-never-recorded",
  "manual-attention-campaign-no-outcome",
  "manual-attention-campaign-isolation-not-clean",
  "manual-attention-campaign-error",
]);
const GUARDIAN_STATUSES = Object.freeze([NO_ACTIVE_STATUS, PREPARED_CANCELLED_STATUS, CAMPAIGN_RECOVERY_PROGRESS_STATUS, ISOLATION_CLEAN_STATUS, PRODUCTION_START_AUTHORIZED_STATUS, PRODUCTION_STARTED_STATUS, READINESS_PROVEN_STATUS, M8_HOLD_STATUS, CAMPAIGN_SETTLED_STATUS, ...MANUAL_STATUSES]);
// Non-manual statuses that represent forward progress and make the watcher loop
// immediately against the fresh journal identity for the next pass. The M8 hold
// is the only non-manual terminal status that stops the loop.
const PROGRESS_STATUSES = Object.freeze([CAMPAIGN_RECOVERY_PROGRESS_STATUS, ISOLATION_CLEAN_STATUS, PRODUCTION_START_AUTHORIZED_STATUS, PRODUCTION_STARTED_STATUS, READINESS_PROVEN_STATUS]);
const DEFAULT_RECONCILE_BACKOFF_MS = 60000;
const DEFAULT_MANUAL_BACKOFF_MS = 60000;

export class WorkCampaignRecoveryGuardianError extends Error {}

function fail(message) { throw new WorkCampaignRecoveryGuardianError(message); }
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

// Standing M7 capability, represented separately from any per-result authority
// grant. The guardian retains the exact local capability to restart and probe
// the already-bound production container, but a content-free result must never
// itself become an action-authority grant. Only results that actually durably
// scoped or performed the corresponding operation grant that per-result
// authority; no-active, manual-attention, prepared-cancelled, and M8-hold
// results never grant an unscoped start/readiness operation.
const GUARDIAN_CAPABILITY = Object.freeze({
  canCancelPreparedJournal: true,
  canStopBoundProduction: true,
  canStopBoundChild: true,
  canVerifyIsolation: true,
  canAuthorizeProductionStart: true,
  canRestartBoundProduction: true,
  canProbeBoundReadiness: true,
});

function resultAuthority(status) {
  const startScoped = status === PRODUCTION_START_AUTHORIZED_STATUS || status === PRODUCTION_STARTED_STATUS || status === READINESS_PROVEN_STATUS;
  const readinessScoped = status === READINESS_PROVEN_STATUS;
  return Object.freeze({
    grantsExecution: false,
    grantsProductionMutation: false,
    grantsCredentials: false,
    grantsExternalNetwork: false,
    grantsExternalEffects: false,
    grantsDeployment: false,
    grantsCompletion: false,
    grantsSettlement: false,
    grantsProductionStart: startScoped,
    grantsReadiness: readinessScoped,
  });
}

function result(status, journal, state = {}) {
  if (!GUARDIAN_STATUSES.includes(status)) fail(`campaign guardian status is not in the closed set: ${status}`);
  const attention = state.attention === true || MANUAL_STATUSES.includes(status);
  return Object.freeze({
    schemaVersion: 1,
    operation: OPERATION,
    status,
    journalPhase: journal?.phase ?? null,
    maintenanceOperationSha256: journal?.maintenanceOperationSha256 ?? null,
    campaignOperationSha256: journal?.campaignOperationSha256 ?? null,
    campaignChildTerminal: state.campaignChildTerminal ?? null,
    comparisonIsolationAbsent: state.comparisonIsolationAbsent ?? null,
    progress: PROGRESS_STATUSES.includes(status),
    holdForM7: false,
    holdForM8: status === M8_HOLD_STATUS,
    settled: status === CAMPAIGN_SETTLED_STATUS,
    settledCampaignStatus: status === CAMPAIGN_SETTLED_STATUS ? (state.settledCampaignStatus ?? null) : null,
    campaignOutcomeSha256: status === CAMPAIGN_SETTLED_STATUS ? (state.campaignOutcomeSha256 ?? null) : null,
    manualAttentionRequired: attention,
    credentialsUsed: false,
    externalNetworkUsed: false,
    authority: resultAuthority(status),
    capability: GUARDIAN_CAPABILITY,
    boundary: attention ? GUARDIAN_ATTENTION_BOUNDARY : GUARDIAN_RESULT_BOUNDARY,
  });
}

function manual(journal, status, state = {}) {
  return result(status, journal, { ...state, attention: true });
}

function noActiveResult() {
  return Object.freeze({ schemaVersion: 1, operation: OPERATION, status: NO_ACTIVE_STATUS, journalPhase: null, progress: false, holdForM7: false, holdForM8: false, manualAttentionRequired: false, credentialsUsed: false, externalNetworkUsed: false, authority: resultAuthority(NO_ACTIVE_STATUS), capability: GUARDIAN_CAPABILITY, boundary: GUARDIAN_RESULT_BOUNDARY });
}

async function loadGuardian(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("campaign guardian requires a non-root service identity");
  absolutePath(configPath, "campaign maintenance configuration path");
  const binding = await loadCampaignRecoveryBinding(configPath, { expectedOwnerUid, inventory: dependencies.inventory, inspectContainer: dependencies.inspectContainer, inspectSource: dependencies.inspectSource });
  return Object.freeze({ expectedOwnerUid, configuration: binding.configuration, configBytesSha: binding.configBytesSha, binding });
}

// Exact config/journal identity revalidation (runtime independent). Every
// immutable identity field carried by the journal must equal the freshly derived
// binding; any mismatch fails closed before any recovery effect.
function revalidateIdentity(journal, binding, custodyIdentitySha256) {
  if (binding.configBytesSha !== journal.configurationSha256) return { ok: false, status: "manual-attention-campaign-config-changed" };
  if (binding.campaignOperationSha256 !== journal.campaignOperationSha256) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  if (binding.custody.identitySha256 !== journal.custodyIdentitySha256) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  if (binding.expectedOwnerUid !== journal.ownerUid) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  if (binding.custody.identitySha256 !== custodyIdentitySha256) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  const configProduction = binding.configuration.production;
  const configProductionIdentity = {
    containerName: configProduction.containerName,
    containerId: configProduction.containerId,
    imageDigest: configProduction.imageDigest,
    expectedStartedAt: configProduction.expectedStartedAt,
    expectedRestartCount: configProduction.expectedRestartCount,
  };
  if (canonical(configProductionIdentity) !== canonical(journal.production)) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  if (binding.bindings.materializationSha256 !== journal.comparison.materializationSha256
    || binding.bindings.pairConfigurationSha256 !== journal.comparison.pairConfigurationSha256
    || binding.bindings.preflightSha256 !== journal.comparison.preflightSha256) return { ok: false, status: "manual-attention-campaign-identity-changed" };
  return { ok: true };
}

// Last-moment authority seam used immediately before every cancel, production
// stop, child-unit stop, and journal advance. Under the still-held custody lock
// it re-loads the exact recovery binding/config, re-inspects the active journal,
// compares the exact expected journal identity, revalidates config/custody/
// operation/production bindings, and requires a live campaign guardian lease
// bound to that same journal identity. A stale lease, config drift, journal
// substitution, or missing journal throws (fail closed) and prevents the effect;
// every failure is content-free.
async function assertCurrentAuthority(loaded, expectedJournal, dependencies, custodyIdentitySha256) {
  let underLock;
  try {
    underLock = await loadCampaignRecoveryBinding(loaded.configPath, {
      expectedOwnerUid: loaded.expectedOwnerUid,
      inventory: dependencies.inventory,
      inspectContainer: dependencies.inspectContainer,
      inspectSource: dependencies.inspectSource,
    });
  } catch {
    throw new WorkCampaignRecoveryGuardianError("current campaign authority config could not be re-proven");
  }
  let inspected;
  try {
    inspected = await inspectActiveCampaignRecoveryJournal(underLock.configuration.custody.lockPath, underLock.expectedOwnerUid);
  } catch {
    throw new WorkCampaignRecoveryGuardianError("current campaign authority journal could not be re-read");
  }
  if (!inspected) throw new WorkCampaignRecoveryGuardianError("current campaign authority journal is missing");
  if (deriveCampaignJournalIdentitySha256(inspected.journal) !== deriveCampaignJournalIdentitySha256(expectedJournal)) {
    throw new WorkCampaignRecoveryGuardianError("current campaign authority journal identity changed");
  }
  const identityCheck = revalidateIdentity(inspected.journal, underLock, custodyIdentitySha256);
  if (!identityCheck.ok) throw new WorkCampaignRecoveryGuardianError("current campaign authority binding changed");
  const guardianModulePath = fileURLToPath(import.meta.url);
  const guardianModuleSha256 = dependencies.guardianModuleSha256 ?? sha(await readFile(guardianModulePath));
  const expectedNodePath = dependencies.expectedNodePath ?? await realpath(process.execPath);
  const expectedUnitSha256 = dependencies.expectedUnitSha256 ?? (await renderCampaignGuardianUnit({ configPath: loaded.configPath, nodePath: expectedNodePath, guardianPath: guardianModulePath, expectedOwnerUid: underLock.expectedOwnerUid })).renderSha256;
  const requireLease = dependencies.requireLease ?? requireLiveCampaignGuardianLease;
  await requireLease(underLock.configuration.custody.lockPath, underLock.expectedOwnerUid, underLock.configBytesSha, {
    unit: dependencies.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT,
    configPath: loaded.configPath,
    custodyIdentitySha256: underLock.custody.identitySha256,
    guardianModuleSha256,
    expectedUnitSha256,
    expectedNodePath,
    campaignOperationSha256: inspected.journal.campaignOperationSha256,
    journalIdentitySha256: deriveCampaignJournalIdentitySha256(inspected.journal),
    maxAgeMs: dependencies.guardianLeaseMaxAgeMs,
    bootId: dependencies.guardianLeaseBootId,
  });
  return { ok: true };
}

function parseMainPid(value) {
  const pid = Number(value);
  return Number.isSafeInteger(pid) && pid >= 1 ? pid : 0;
}

// Best-effort release of a response body so an early rejection can never leave
// the underlying connection/reader pinned across repeated manual-attention
// loops. A throwing cancel must never change the fail-closed outcome.
async function cancelResponseBody(response) {
  try {
    const body = response?.body;
    if (body && typeof body.cancel === "function") await body.cancel();
  } catch {}
}

// Read the full response body through a truly bounded streaming reader. The
// declared Content-Length is validated first (only a non-negative integer is
// accepted, and any declared length over the cap is rejected up front), then
// chunks are drained one at a time and the reader is cancelled immediately the
// instant the accumulated byte count would exceed the cap, so an absent or
// lying Content-Length can never allocate an unbounded body. A premature close,
// a throwing stream, a nonterminal zero-length chunk, or any other read failure
// fails closed and returns no bytes. Every rejection path releases the body so
// the guardian never leaks a connection/resource. When Content-Length is
// present, a clean EOF must carry exactly the declared byte count: a declared
// length that differs from the actual completed stream length fails closed.
async function readBoundedResponse(response, maxBytes) {
  const failClosed = async (reason) => { await cancelResponseBody(response); return { ok: false, reason }; };
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) return failClosed("invalid-cap");
  const declared = response?.headers?.get?.("content-length");
  let declaredLength = null;
  if (declared !== null && declared !== undefined && declared !== "") {
    if (!/^[0-9]+$/u.test(declared)) return failClosed("invalid-length");
    declaredLength = Number(declared);
    if (!Number.isSafeInteger(declaredLength) || declaredLength < 0) return failClosed("invalid-length");
    if (declaredLength > maxBytes) return failClosed("oversize-declared");
  }
  if (!response?.body || typeof response.body.getReader !== "function") return failClosed("no-body");
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch {
      await reader.cancel().catch(() => {});
      return { ok: false, reason: "stream-error" };
    }
    if (chunk?.done) break;
    const piece = Buffer.isBuffer(chunk?.value) ? chunk.value : Buffer.from(chunk?.value ?? []);
    // A nonterminal zero-length chunk is a marker of an unbounded/adversarial
    // stream; reject it immediately so a broken peer can never pin the guardian
    // with an endless sequence of empty `{done:false, value: empty}` chunks.
    if (piece.length === 0) {
      await reader.cancel().catch(() => {});
      return { ok: false, reason: "zero-length-chunk" };
    }
    total += piece.length;
    if (total > maxBytes) {
      await reader.cancel().catch(() => {});
      return { ok: false, reason: "oversize-stream" };
    }
    chunks.push(piece);
  }
  // Strict Content-Length/body coherence: a declared in-range length must equal
  // the exact completed stream length at clean EOF. A declared-short or
  // declared-long mismatch fails closed; a missing Content-Length is untouched.
  if (declaredLength !== null && total !== declaredLength) return { ok: false, reason: "length-mismatch" };
  return { ok: true, bytes: Buffer.concat(chunks, total) };
}


// One bounded credential-free loopback readiness probe through the existing
// production readiness mechanism. It records no raw content: on success it
// returns only the exact HTTP 200 status, bounded latency, provenAt, and a
// SHA-256 of the bounded response. The bounded response must still satisfy the
// existing readiness contract (the readiness model id is present under a strict
// parser with strict UTF-8). Any non-200 status, oversized body, timeout, throw,
// body ambiguity, invalid length metadata, malformed JSON, duplicate key,
// invalid UTF-8, or premature/throwing stream returns null so the caller holds
// manual attention with no journal advance. The raw body bytes never escape.
async function probeProductionReadinessDefault(production, options = {}) {
  const endpoint = `${production.readinessOrigin}/v1/models`;
  const started = Date.now();
  const fetchImpl = options.fetchImpl ?? fetch;
  let response;
  try {
    response = await fetchImpl(endpoint, { method: "GET", redirect: "error", credentials: "omit", signal: AbortSignal.timeout(Math.min(10000, production.probeIntervalMilliseconds * 4)) });
  } catch {
    return null;
  }
  const latencyMilliseconds = Date.now() - started;
  if (response.status !== 200) {
    await cancelResponseBody(response);
    return null;
  }
  const bounded = await readBoundedResponse(response, production.maxResponseBytes);
  if (!bounded.ok) return null;
  const bytes = bounded.bytes;
  const text = bytes.toString("utf8");
  // Reject invalid UTF-8 by requiring an exact lossless round-trip; a
  // replacement-character decode must never become readiness proof.
  if (!Buffer.from(text, "utf8").equals(bytes)) return null;
  let body;
  try {
    body = parseStrictJson(text, "campaign readiness response");
  } catch {
    return null;
  }
  // Exact readiness contract: a non-empty data array containing an entry whose
  // id is exactly the bound readiness model id. Ambiguous or partial shapes
  // never become readiness proof.
  if (!Array.isArray(body?.data) || body.data.length === 0 || !body.data.some((entry) => entry !== null && typeof entry === "object" && !Array.isArray(entry) && entry.id === production.readinessModelId)) return null;
  return Object.freeze({
    status: 200,
    latencyMilliseconds,
    provenAt: new Date().toISOString(),
    responseSha256: createHash("sha256").update(bytes).digest("hex"),
  });
}

// Extract the exact unit control-group path from a recorded /proc cgroup line
// set. The ControlGroup is the path prefix ending at the exact unit name.
function extractUnitControlGroup(cgroupLines, unitName) {
  if (!Array.isArray(cgroupLines)) return null;
  const needle = unitName;
  for (const line of cgroupLines) {
    if (typeof line !== "string") continue;
    const first = line.indexOf(":");
    if (first < 0) continue;
    const second = line.indexOf(":", first + 1);
    if (second < 0) continue;
    const path = line.slice(second + 1);
    const idx = path.indexOf(`/${needle}`);
    if (idx < 0) continue;
    return path.slice(0, idx + 1 + needle.length);
  }
  return null;
}

function campaignGuardianActions(dependencies, binding) {
  const production = binding.configuration.production;
  const stopProduction = dependencies.stopProduction ?? ((target, dockerPath) => execute(dockerPath, ["container", "stop", "--time", String(production.stopTimeoutSeconds), target], { encoding: "utf8", windowsHide: true, timeout: (production.stopTimeoutSeconds + 30) * 1000, maxBuffer: 1024 * 1024, env: productionMaintenancePrimitives.DOCKER_ENV }));
  const stopUnit = dependencies.stopUnit ?? (async (unitName, systemdDeps = {}) => {
    const systemctlPath = await resolveTrustedSystemctl(systemdDeps);
    try {
      await execute(systemctlPath, ["--user", "stop", unitName], { encoding: "utf8", timeout: 60000, maxBuffer: 1024 * 1024, env: systemdDeps.env });
      return { ok: true };
    } catch {
      return { ok: false };
    }
  });
  const showUnit = dependencies.showUnit ?? (async (unitName, systemdDeps = {}) => {
    try {
      const facts = await systemdShowUnit(unitName, systemdDeps);
      return { ok: true, facts };
    } catch (error) {
      return { ok: false, error: error?.message ?? "systemctl show failed" };
    }
  });
  const dockerSocketPath = dependencies.dockerSocketPath ?? DOCKER_SOCKET_PATH;
  // Lower-level injectable seams so the real default Docker-start and readiness
  // paths can be exercised deterministically without a live Docker socket. The
  // default start closure binds the exact closed-over dockerSocketPath (never a
  // self-reference into the actions object) and delegates to the injectable
  // engine, while the default readiness probe uses an injectable fetch.
  const startEngine = dependencies.dockerEngineStart ?? dockerEngineStart;
  return Object.freeze({
    dockerPath: binding.configuration.dockerPath,
    production,
    inspectContainer: dependencies.inspectContainer ?? binding.inspectContainer,
    inventory: dependencies.inventory ?? binding.inventory,
    requireCleanInventory: dependencies.requireCleanInventory ?? requireCleanInventory,
    validateProduction: dependencies.validateProduction ?? productionMaintenancePrimitives.validateProduction,
    stopProduction,
    dockerSocketPath,
    secureSocketIdentity: dependencies.secureSocketIdentity ?? (async (socketPath) => {
      const identity = await secureSocketIdentity(socketPath);
      return identity?.identitySha256 ?? null;
    }),
    startProduction: dependencies.startProduction ?? (async (target, options = {}) => {
      const socketPath = options.socketPath ?? dockerSocketPath;
      const reviewedIdentity = options.reviewedIdentity;
      if (typeof reviewedIdentity !== "string" || !SHA_RE.test(reviewedIdentity)) fail("campaign guardian start requires the exact reviewed endpoint identity");
      return startEngine({ containerId: target, socketPath, timeoutMs: 60000, reviewedIdentity });
    }),
    probeReadiness: dependencies.probeReadiness ?? (async (production) => probeProductionReadinessDefault(production, { fetchImpl: dependencies.fetchImpl })),
    requireLease: dependencies.requireLease ?? requireLiveCampaignGuardianLease,
    advanceJournal: dependencies.advanceJournal ?? advanceCampaignRecoveryJournal,
    cancelJournal: dependencies.cancelJournal ?? cancelCampaignRecoveryJournal,
    settleJournal: dependencies.settleJournal ?? settleCampaignRecoveryJournal,
    deriveProcessIdentity: dependencies.deriveProcessIdentity ?? deriveProcessIdentity,
    isProcessAlive: dependencies.isProcessAlive ?? isProcessAlive,
    proveCgroupEmpty: dependencies.proveCgroupEmpty ?? proveCampaignChildCgroupEmpty,
    showUnit,
    stopUnit,
    readInvocationId: dependencies.readInvocationId ?? readInvocationIdFromProc,
    systemdDeps: dependencies.systemdDeps ?? {},
  });
}

// Verify exact isolation: no comparison (prefix) resources and no running GPU
// container. M6 never removes any resource; a non-clean window fails closed.
async function convergeIsolation(loaded, actions, expectedGpuIds) {
  let inventoryValue;
  try { inventoryValue = await actions.inventory(actions.dockerPath); }
  catch { return { ok: false, status: "manual-attention-campaign-isolation-not-clean" }; }
  try { actions.requireCleanInventory(inventoryValue, expectedGpuIds, "campaign recovery isolation inventory"); }
  catch { return { ok: false, status: "manual-attention-campaign-isolation-not-clean" }; }
  return { ok: true, isolationAbsent: true };
}

// Prove or enforce exact child terminality. Never infers terminality from
// cleared or missing unit metadata alone; stops only the exact nonce-derived
// unit after strict InvocationID/control-group/MainPID and complete recorded
// process identity proof, then reproves terminality via a gone recorded
// incarnation and an exact empty recorded-cgroup proof.

// Narrow recorded-process-incarnation proof based only on the immutable
// incarnation keys (exact bootId, pid, startTicks). Mutable cgroup/exe/argv/uid
// fields are deliberately ignored. If the recorded pid is alive and its current
// identity cannot be derived, fail closed (not terminal). If it is alive under
// the same immutable incarnation it is still the recorded child regardless of
// changed cgroup/exe/argv/uid and must never be declared terminal. Only a dead
// pid or a true PID reuse (different bootId/startTicks) permits subsequent
// cgroup terminality proof.
async function proveRecordedChildIncarnationGone(recorded, actions) {
  if (!actions.isProcessAlive(recorded.pid)) return { ok: true, reason: "dead" };
  let now;
  try {
    now = await actions.deriveProcessIdentity(recorded.pid);
  } catch {
    return { ok: false, status: "manual-attention-campaign-child-not-terminal", reason: "identity-unproven" };
  }
  const sameIncarnation = now.bootId === recorded.bootId && now.pid === recorded.pid && now.startTicks === recorded.startTicks;
  if (sameIncarnation) return { ok: false, status: "manual-attention-campaign-child-substituted", aliveSameIncarnation: true, reason: "same-incarnation" };
  return { ok: true, reason: "pid-reused" };
}

// Shared exact recorded-child terminality proof used by convergeCampaignChild and
// the observation-only last-moment transition reproof. Only two terminal unit
// shapes are candidates: an authoritative not-found unit, or a
// loaded+inactive+dead unit with MainPID 0. For either shape it proves the
// recorded process incarnation is gone and that the exact recorded unit cgroup
// (derived from recorded.cgroup plus the exact unit name) is empty. It never
// infers terminality from cleared metadata alone and never requires a terminal
// InvocationID or current ControlGroup to equal live pre-stop values (real
// systemd clears them). A live same-incarnation pid, nonempty recorded cgroup,
// nonterminal state, different loaded state, or ambiguous show/read fails closed.
async function proveChildTerminal(recorded, unitName, show, actions) {
  const facts = show.facts;
  const terminalShape = facts.LoadState === "not-found"
    || (facts.LoadState === "loaded" && facts.ActiveState === "inactive" && facts.SubState === "dead" && facts.MainPID === "0");
  if (!terminalShape) return { ok: false, status: "manual-attention-campaign-child-not-terminal" };
  const gone = await proveRecordedChildIncarnationGone(recorded, actions);
  if (!gone.ok) return { ok: false, status: gone.status };
  const cgroup = extractUnitControlGroup(recorded.cgroup, unitName);
  if (!cgroup) return { ok: false, status: "manual-attention-campaign-child-substituted" };
  if (!(await actions.proveCgroupEmpty(cgroup))) return { ok: false, status: "manual-attention-campaign-child-not-terminal" };
  return { ok: true };
}

async function convergeCampaignChild(loaded, journal, actions, assertCurrent) {
  const nonce = journal.campaignChildNonce;
  if (!nonce) return { ok: true, terminal: true };
  const unitName = deriveCampaignChildUnitName(nonce);
  const recorded = journal.campaignChild;

  // campaign-child-authorized: a nonce is durable but no process identity was
  // ever recorded. M6 must not stop any unit without a recorded process identity
  // and must not infer terminality from missing metadata alone. Only a provably
  // absent unit (authoritative not-found) plus the absence of any recorded child
  // establishes that the child never started.
  if (!recorded) {
    const show = await actions.showUnit(unitName, actions.systemdDeps);
    if (!show.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
    if (show.facts.LoadState !== "not-found") return { ok: false, status: "manual-attention-campaign-child-substituted" };
    return { ok: true, terminal: true };
  }

  const show = await actions.showUnit(unitName, actions.systemdDeps);
  if (!show.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
  const facts = show.facts;

  if (facts.LoadState === "not-found") {
    // Narrow not-found terminal case backed by a proven gone recorded
    // incarnation plus an exact empty recorded-cgroup proof (never missing
    // metadata alone).
    const terminal = await proveChildTerminal(recorded, unitName, show, actions);
    if (!terminal.ok) return { ok: false, status: terminal.status };
    return { ok: true, terminal: true };
  }

  if (facts.LoadState !== "loaded") return { ok: false, status: "manual-attention-campaign-child-substituted" };

  // A loaded unit already in an accepted terminal state (inactive+dead with
  // MainPID 0) needs no stop. Prove exact terminality without stopping via the
  // shared proof: the recorded process incarnation is gone and the exact
  // recorded unit cgroup is empty. This is never inferred from cleared unit
  // metadata alone and does not require terminal InvocationID/ControlGroup to
  // equal live pre-stop values (real systemd clears them).
  if (isAcceptedTerminalState(show) && parseMainPid(facts.MainPID) === 0) {
    const terminal = await proveChildTerminal(recorded, unitName, show, actions);
    if (!terminal.ok) return { ok: false, status: terminal.status };
    return { ok: true, terminal: true };
  }

  // Exact recorded-identity proof for a live unit: MainPID, live InvocationID
  // in the recorded process environment, and the recorded cgroup must all match
  // the shown unit.
  if (parseMainPid(facts.MainPID) !== recorded.pid) return { ok: false, status: "manual-attention-campaign-child-substituted" };
  const procInvocation = await actions.readInvocationId(recorded.pid).catch(() => null);
  if (procInvocation === null || procInvocation !== facts.InvocationID) return { ok: false, status: "manual-attention-campaign-child-substituted" };
  if (!recorded.cgroup.some((line) => exactCgroupLineMatches(line, facts.ControlGroup))) return { ok: false, status: "manual-attention-campaign-child-substituted" };

  // If the exact recorded child is still running, stop ONLY this exact unit.
  if (actions.isProcessAlive(recorded.pid)) {
    const now = await actions.deriveProcessIdentity(recorded.pid).catch(() => null);
    if (now && canonical(now) === canonical(recorded)) {
      // Last-moment authority: the current lease must still bind the exact
      // journal identity and the current binding must revalidate before any stop.
      await assertCurrent(journal);
      // Immediately before stop, re-show the unit and re-derive/recompare the
      // complete recorded process identity. If InvocationID, ControlGroup,
      // MainPID, process identity, or terminal state changed, do not stop.
      const preShow = await actions.showUnit(unitName, actions.systemdDeps);
      if (!preShow.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
      if (preShow.facts.LoadState !== "loaded") return { ok: false, status: "manual-attention-campaign-child-substituted" };
      if (preShow.facts.InvocationID !== facts.InvocationID) return { ok: false, status: "manual-attention-campaign-child-substituted" };
      if (preShow.facts.ControlGroup !== facts.ControlGroup) return { ok: false, status: "manual-attention-campaign-child-substituted" };
      if (parseMainPid(preShow.facts.MainPID) !== recorded.pid) return { ok: false, status: "manual-attention-campaign-child-substituted" };
      if (isAcceptedTerminalState(preShow)) return { ok: false, status: "manual-attention-campaign-child-substituted" };
      const preIdentity = await actions.deriveProcessIdentity(recorded.pid).catch(() => null);
      if (!preIdentity || canonical(preIdentity) !== canonical(recorded)) return { ok: false, status: "manual-attention-campaign-child-substituted" };
      const stop = await actions.stopUnit(unitName, actions.systemdDeps);
      if (!stop.ok) return { ok: false, status: "manual-attention-campaign-child-not-terminal" };
    }
  }

  // Reprove terminality after stop: the recorded process incarnation must be
  // gone and the exact recorded unit cgroup empty, under a proven terminal unit
  // shape (authoritative not-found or loaded+inactive+dead+MainPID 0). Cleared
  // terminal InvocationID/ControlGroup are permitted because real systemd
  // clears them; terminality is never inferred from that metadata alone.
  const postShow = await actions.showUnit(unitName, actions.systemdDeps);
  if (!postShow.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
  const terminal = await proveChildTerminal(recorded, unitName, postShow, actions);
  if (!terminal.ok) return { ok: false, status: terminal.status };
  return { ok: true, terminal: true };
}

// Prove production is stopped with the exact bound identity.
async function proveProductionStopped(loaded, actions) {
  const observed = await actions.inspectContainer(actions.production.containerId, actions.dockerPath);
  if (observed === null) return { ok: false, status: "manual-attention-campaign-production-missing" };
  try {
    actions.validateProduction(observed, actions.production, { running: false, requireReviewedStart: true });
    return { ok: true };
  } catch {
    return { ok: false, status: "manual-attention-campaign-production-substituted" };
  }
}

// Idempotent stop from the durable production-stop-authorized phase: the durable
// authorization plus the exact current bound production identity may stop the
// exact pre-maintenance production and prove it stopped.
async function convergeProductionStopped(loaded, actions, assertCurrent, journal) {
  const observed = await actions.inspectContainer(actions.production.containerId, actions.dockerPath);
  if (observed === null) return { ok: false, status: "manual-attention-campaign-production-missing" };
  try {
    actions.validateProduction(observed, actions.production, { running: false, requireReviewedStart: true });
    return { ok: true };
  } catch {}
  let running;
  try { running = actions.validateProduction(observed, actions.production, { running: true, requireReviewedStart: false }); }
  catch { return { ok: false, status: "manual-attention-campaign-production-substituted" }; }
  if (!(running.startedAt === actions.production.expectedStartedAt && running.restartCount === actions.production.expectedRestartCount)) return { ok: false, status: "manual-attention-campaign-production-substituted" };
  await assertCurrent(journal);
  await actions.stopProduction(actions.production.containerId, actions.dockerPath);
  const after = await actions.inspectContainer(actions.production.containerId, actions.dockerPath).catch(() => null);
  try {
    actions.validateProduction(after, actions.production, { running: false, requireReviewedStart: true });
    return { ok: true };
  } catch {
    return { ok: false, status: "manual-attention-campaign-production-substituted" };
  }
}

// Observation-only exact child terminality re-proof for the last-moment
// transition seam. It never stops a unit and performs no destructive effect.
// It re-shows the exact nonce-derived unit and re-proves the recorded child is
// terminal via the shared proof (a proven terminal unit shape plus a gone
// recorded incarnation and an exact empty recorded-cgroup proof). A live
// same-incarnation child, a nonterminal state, a nonempty recorded cgroup, or
// an ambiguous show/read fails closed.
async function proveChildTerminalObservationOnly(loaded, journal, actions) {
  const nonce = journal.campaignChildNonce;
  const unitName = deriveCampaignChildUnitName(nonce);
  const recorded = journal.campaignChild;
  if (!recorded) {
    const show = await actions.showUnit(unitName, actions.systemdDeps);
    if (!show.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
    if (show.facts.LoadState !== "not-found") return { ok: false, status: "manual-attention-campaign-child-substituted" };
    return { ok: true };
  }
  const show = await actions.showUnit(unitName, actions.systemdDeps);
  if (!show.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
  return proveChildTerminal(recorded, unitName, show, actions);
}

// M7 child terminality re-proof under the existing exact M6 proof contract.
// When no campaign child was ever authorized (nonce null) the child is absent
// by construction; otherwise re-prove exact recorded-child terminality/cgroup
// emptiness observation-only, never stopping a unit.
async function proveM7ChildTerminality(loaded, journal, actions) {
  if (journal.campaignChildNonce === null) return { ok: true };
  return proveChildTerminalObservationOnly(loaded, journal, actions);
}

// Prove the exact bound production is running and return its immutable
// fingerprint (containerId, imageDigest, startedAt, restartCount) under the
// exact maintenance identity binding.
async function proveProductionRunning(loaded, actions) {
  const observed = await actions.inspectContainer(actions.production.containerId, actions.dockerPath);
  if (observed === null) return { ok: false, status: "manual-attention-campaign-production-missing" };
  try {
    const fingerprint = actions.validateProduction(observed, actions.production, { running: true, requireReviewedStart: false });
    return { ok: true, fingerprint };
  } catch {
    return { ok: false, status: "manual-attention-campaign-production-substituted" };
  }
}

// Prove the exact bound production is running with correct post-manual-start
// restart semantics: after an attributed 204 the intentional stop/start resets
// Docker RestartCount to zero, so the post-start count must be exactly 0 and
// never merely the shared validator's general-recovery allowance for the
// pre-maintenance expectedRestartCount. Returns the full immutable fingerprint.
async function provePostStartRunning(loaded, actions) {
  const observed = await actions.inspectContainer(actions.production.containerId, actions.dockerPath);
  if (observed === null) return { ok: false, status: "manual-attention-campaign-production-missing" };
  if (observed.RestartCount !== 0) return { ok: false, status: "manual-attention-campaign-start-not-proven" };
  try {
    const fingerprint = actions.validateProduction(observed, actions.production, { running: true, requireReviewedStart: false });
    if (fingerprint.restartCount !== 0) return { ok: false, status: "manual-attention-campaign-start-not-proven" };
    return { ok: true, fingerprint };
  } catch {
    return { ok: false, status: "manual-attention-campaign-production-substituted" };
  }
}

// Last-moment full live reproof immediately before the Docker start request
// (defect 4): the guardian must not rely on a stale child/isolation/endpoint
// proof taken earlier in the invocation. It re-proves exact stopped production,
// campaign-child terminal/absent, clean comparison/GPU isolation, and the secure
// endpoint identity equal to the durable authorized identity.
async function reprovePreStartLive(loaded, journal, actions, authorizedIdentity) {
  const stopped = await proveProductionStopped(loaded, actions);
  if (!stopped.ok) return stopped;
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return child;
  const isolation = await convergeIsolation(loaded, actions, []);
  if (!isolation.ok) return isolation;
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity) || endpointIdentity !== authorizedIdentity) return { ok: false, status: "manual-attention-campaign-endpoint-missing" };
  return { ok: true };
}

// Last-moment full live reproof before a post-204 production-started or
// readiness-proven advance. It re-proves the exact running production with full
// immutable fingerprint equal to the durable 204 receipt and restartCount 0,
// terminal/absent child, clean comparison/GPU isolation with only the bound
// production allowed, and the secure socket identity equal to the receipt.
async function reprovePostStartLive(loaded, journal, actions, receipt) {
  const running = await provePostStartRunning(loaded, actions);
  if (!running.ok) return running;
  if (running.fingerprint.startedAt !== receipt.startedAt) return { ok: false, status: "manual-attention-campaign-start-not-proven" };
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return child;
  const isolation = await convergeIsolation(loaded, actions, [actions.production.containerId]);
  if (!isolation.ok) return isolation;
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity) || endpointIdentity !== receipt.endpointIdentity) return { ok: false, status: "manual-attention-campaign-endpoint-missing" };
  return { ok: true, fingerprint: running.fingerprint };
}

// M7 phase A: isolation-clean -> production-start-authorized. Freshly prove the
// exact bound production exists and is stopped, comparison/GPU isolation is
// clean, and any recorded campaign child is terminal/absent; prove the exact
// secure Docker socket endpoint identity; then generate one nonzero 64-hex
// start nonce and durably advance ONLY to production-start-authorized with the
// nonce + endpoint identity. It never starts production in this invocation.
async function authorizeProductionStart(loaded, journal, actions, assertTransitionReady) {
  const stopped = await proveProductionStopped(loaded, actions);
  if (!stopped.ok) return manual(journal, stopped.status, {});
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return manual(journal, child.status, {});
  const isolation = await convergeIsolation(loaded, actions, []);
  if (!isolation.ok) return manual(journal, isolation.status, {});
  // Last-moment authority + fresh live-state reproof before the durable
  // production-start authorization advance.
  const transition = await assertTransitionReady("production-start-authorized", journal);
  if (!transition.ok) return manual(journal, transition.status, {});
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity)) return manual(journal, "manual-attention-campaign-endpoint-missing", {});
  const nonce = newStartNonce();
  if (!SHA_RE.test(nonce) || nonce === ZERO_SHA256) return manual(journal, "manual-attention-campaign-error", {});
  const advanced = await actions.advanceJournal(loaded.configuration.custody.lockPath, loaded.expectedOwnerUid, "production-start-authorized", deriveCampaignJournalIdentity(journal), { guardianStartNonce: nonce, guardianStartEndpointIdentity: endpointIdentity });
  return result(PRODUCTION_START_AUTHORIZED_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
}

// M7 phase B: production-start-authorized -> production-started. Re-prove exact
// authority, stopped production, clean isolation/child terminality, and the
// current secure socket identity equal to the durable authorized identity, then
// make exactly one fail-closed Docker Engine start request. Only an exact
// definite 204 for the exact container/endpoint is attributable success. After
// a definite 204, inspect the exact running production, bind StartedAt and the
// durable 204 receipt, reassert authority/live facts at the last seam, and
// advance only to production-started. A 304, malformed/extra-key, timeout/
// error/ambiguous response, endpoint substitution, external start, or
// possible-commit ambiguity retains production-start-authorized with manual
// attention; never a success and never a retry.
async function performProductionStart(loaded, journal, actions, assertCurrent) {
  const lockPath = loaded.configuration.custody.lockPath;
  const expectedOwnerUid = loaded.expectedOwnerUid;
  const production = actions.production;
  const authorizedIdentity = journal.guardianStartEndpointIdentity;
  if (typeof authorizedIdentity !== "string" || !SHA_RE.test(authorizedIdentity)) return manual(journal, "manual-attention-campaign-endpoint-missing", {});
  const stopped = await proveProductionStopped(loaded, actions);
  if (!stopped.ok) return manual(journal, stopped.status, {});
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return manual(journal, child.status, {});
  const isolation = await convergeIsolation(loaded, actions, []);
  if (!isolation.ok) return manual(journal, isolation.status, {});
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity) || endpointIdentity !== authorizedIdentity) return manual(journal, "manual-attention-campaign-endpoint-missing", {});
  // Immediately before the Docker start attempt, re-load under custody and
  // require byte-identical config binding, exact active journal identity, exact
  // custody identity, and a live guardian lease bound to that current journal.
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-error", {}); }
  // Last-moment full live reproof immediately before the effect: re-prove exact
  // stopped production, campaign-child terminal/absent, clean comparison/GPU
  // isolation, and the secure endpoint identity equal to the durable authorized
  // identity. The guardian must never start from a stale child/isolation/
  // endpoint proof taken earlier in the invocation.
  const preStart = await reprovePreStartLive(loaded, journal, actions, authorizedIdentity);
  if (!preStart.ok) return manual(journal, preStart.status, {});
  // Exactly one fail-closed Docker Engine start request.
  let startResult;
  try {
    startResult = await actions.startProduction(production.containerId, { socketPath: actions.dockerSocketPath, nonce: journal.guardianStartNonce, reviewedIdentity: authorizedIdentity });
  } catch {
    return manual(journal, "manual-attention-campaign-start-rejected", {});
  }
  const classification = classifyStartResult(startResult);
  const exact204 = classification.ok && classification.status === "started"
    && startResult?.containerId === production.containerId
    && startResult?.endpointIdentity === authorizedIdentity;
  if (!exact204) {
    if (startResult && typeof startResult.containerId === "string" && startResult.containerId !== production.containerId) return manual(journal, "manual-attention-campaign-production-substituted", {});
    return manual(journal, "manual-attention-campaign-start-rejected", {});
  }
  // Definite 204 for the exact container/endpoint: inspect the exact running
  // production with correct post-manual-start restart semantics and bind
  // StartedAt + the durable 204 receipt. The post-start count must be exactly 0
  // (an intentional Docker stop/start resets RestartCount to zero), never the
  // pre-maintenance expectedRestartCount merely because the shared validator
  // allows it for general recovery.
  let running;
  try { running = await provePostStartRunning(loaded, actions); }
  catch { return manual(journal, "manual-attention-campaign-start-not-proven", {}); }
  if (!running.ok) return manual(journal, running.status, {});
  // Correct restart semantics: this request actually restarted the exact
  // container, so the post-start StartedAt must be a fresh timestamp, not the
  // reviewed pre-maintenance identity. A wrong/unchanged StartedAt holds manual
  // attention with the durable intent preserved.
  if (running.fingerprint.startedAt === production.expectedStartedAt) return manual(journal, "manual-attention-campaign-start-not-proven", {});
  const receipt = {
    nonce: journal.guardianStartNonce,
    containerId: production.containerId,
    endpointIdentity: authorizedIdentity,
    status: 204,
    startedAt: running.fingerprint.startedAt,
  };
  // Last-moment authority + full live reproof before the production-started
  // advance: exact running production with the full immutable fingerprint equal
  // to the durable receipt and restartCount 0, terminal/absent child, clean
  // comparison/GPU isolation with only the bound production allowed, and the
  // secure endpoint identity equal to the authorized identity. A drift in any
  // fact after the definite 204 preserves intent and holds manual attention
  // without writing a receipt.
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-start-not-proven", {}); }
  const last = await reprovePostStartLive(loaded, journal, actions, receipt);
  if (!last.ok) return manual(journal, last.status, {});
  if (canonical(last.fingerprint) !== canonical(running.fingerprint)) return manual(journal, "manual-attention-campaign-start-not-proven", {});
  const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "production-started", deriveCampaignJournalIdentity(journal), { guardianStartedAt: running.fingerprint.startedAt, guardianStartReceipt: receipt });
  return result(PRODUCTION_STARTED_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
}

// M7 phase C: production-started -> readiness-proven. Re-prove exact authority,
// clean isolation/child terminality, and the exact running production
// fingerprint equal to the durable 204 receipt (container, endpoint, nonce,
// StartedAt and expected restart semantics), perform one bounded credential-free
// loopback readiness probe recording no content, then re-inspect production and
// require the exact stable fingerprint and same start identity before advancing
// durably to readiness-proven with journal-valid readinessEvidence. Any
// false/throw/timeout/oversize/body ambiguity, stop/restart/substitution,
// response tamper, or identity drift holds manual attention with no advance.
async function proveReadiness(loaded, journal, actions, assertCurrent) {
  const lockPath = loaded.configuration.custody.lockPath;
  const expectedOwnerUid = loaded.expectedOwnerUid;
  const receipt = journal.guardianStartReceipt;
  if (!receipt) return manual(journal, "manual-attention-campaign-start-not-proven", {});
  const production = actions.production;
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-error", {}); }
  let before;
  try { before = await provePostStartRunning(loaded, actions); }
  catch { return manual(journal, "manual-attention-campaign-production-not-running", {}); }
  if (!before.ok) return manual(journal, before.status === "manual-attention-campaign-production-missing" ? "manual-attention-campaign-production-not-running" : before.status, {});
  if (before.fingerprint.startedAt !== receipt.startedAt || before.fingerprint.containerId !== receipt.containerId) return manual(journal, "manual-attention-campaign-production-substituted", {});
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return manual(journal, child.status, {});
  // The exact bound production is the only running GPU container in the clean
  // comparison window during readiness; any other comparison/GPU resource fails closed.
  const isolation = await convergeIsolation(loaded, actions, [actions.production.containerId]);
  if (!isolation.ok) return manual(journal, isolation.status, {});
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity) || endpointIdentity !== receipt.endpointIdentity) return manual(journal, "manual-attention-campaign-endpoint-missing", {});
  // One bounded credential-free loopback readiness probe; no raw content recorded.
  let probe;
  try { probe = await actions.probeReadiness(production); }
  catch { probe = null; }
  if (!probe || probe.status !== 200 || !Number.isSafeInteger(probe.latencyMilliseconds) || probe.latencyMilliseconds < 0 || typeof probe.provenAt !== "string" || !PROVEN_RE.test(probe.provenAt) || typeof probe.responseSha256 !== "string" || !SHA_RE.test(probe.responseSha256) || probe.responseSha256 === ZERO_SHA256) {
    return manual(journal, "manual-attention-campaign-readiness-failed", {});
  }
  // Immediately re-inspect production: require the exact canonical full
  // fingerprint stability (container, image, fresh StartedAt, restartCount 0)
  // before vs after the probe.
  let after;
  try { after = await provePostStartRunning(loaded, actions); }
  catch { return manual(journal, "manual-attention-campaign-readiness-unstable", {}); }
  if (!after.ok || canonical(after.fingerprint) !== canonical(before.fingerprint)) return manual(journal, "manual-attention-campaign-readiness-unstable", {});
  // Last-moment authority + full live reproof before the readiness-proven
  // advance: re-prove exact full production fingerprint equal to the receipt,
  // restartCount 0, terminal/absent child, clean comparison/GPU isolation with
  // only the bound production allowed, and the secure socket identity equal to
  // the receipt. A drift in any fact after the probe holds manual attention.
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-readiness-unstable", {}); }
  const last = await reprovePostStartLive(loaded, journal, actions, receipt);
  if (!last.ok) return manual(journal, last.status === "manual-attention-campaign-production-missing" ? "manual-attention-campaign-readiness-unstable" : last.status, {});
  if (canonical(last.fingerprint) !== canonical(before.fingerprint)) return manual(journal, "manual-attention-campaign-readiness-unstable", {});
  const readinessEvidence = {
    productionStartedAt: journal.guardianStartedAt,
    provenAt: probe.provenAt,
    status: 200,
    latencyMilliseconds: probe.latencyMilliseconds,
    responseSha256: probe.responseSha256,
  };
  const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "readiness-proven", deriveCampaignJournalIdentity(journal), { readinessEvidence });
  return result(READINESS_PROVEN_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
}

// Last-moment transition reproof seam: fresh phase-specific live facts
// immediately before a journal advance, in addition to the exact config/
// custody/journal/lease authority recheck. Observation-only: it never stops
// production and never stops a unit. Any stale, ambiguous, or substituted live
// fact fails closed so the durable journal advance never runs on evidence
// proven earlier in the invocation.
async function reproveTransitionLiveState(targetPhase, loaded, journal, actions) {
  if (targetPhase === "production-stopped") {
    // production-stop-authorized -> production-stopped: the exact bound
    // production must still be stopped. Never stop it here.
    return proveProductionStopped(loaded, actions);
  }
  if (targetPhase === "production-start-authorized") {
    // isolation-clean -> production-start-authorized: stopped production,
    // clean comparison/GPU isolation, and M6-contract child terminality.
    const stopped = await proveProductionStopped(loaded, actions);
    if (!stopped.ok) return stopped;
    const child = await proveM7ChildTerminality(loaded, journal, actions);
    if (!child.ok) return child;
    return convergeIsolation(loaded, actions, []);
  }
  if (targetPhase === "readiness-proven") {
    // production-started -> readiness-proven: exact running production still
    // equal to the durable 204 receipt, clean isolation, and child terminality.
    const receipt = journal.guardianStartReceipt;
    if (!receipt) return { ok: false, status: "manual-attention-campaign-start-not-proven" };
    const running = await proveProductionRunning(loaded, actions);
    if (!running.ok) return running;
    if (running.fingerprint.startedAt !== receipt.startedAt || running.fingerprint.containerId !== receipt.containerId) return { ok: false, status: "manual-attention-campaign-production-substituted" };
    const child = await proveM7ChildTerminality(loaded, journal, actions);
    if (!child.ok) return child;
    return convergeIsolation(loaded, actions, [actions.production.containerId]);
  }
  if (targetPhase !== "isolation-clean") return { ok: true };
  // Every pre-isolation transition into isolation-clean re-proves exact
  // production stopped and exact comparison/GPU isolation clean.
  const stopped = await proveProductionStopped(loaded, actions);
  if (!stopped.ok) return stopped;
  if (journal.phase === "campaign-child-authorized") {
    // Re-show the exact nonce-derived unit as authoritatively not-found.
    const show = await actions.showUnit(deriveCampaignChildUnitName(journal.campaignChildNonce), actions.systemdDeps);
    if (!show.ok) return { ok: false, status: "manual-attention-campaign-child-not-found" };
    if (show.facts.LoadState !== "not-found") return { ok: false, status: "manual-attention-campaign-child-substituted" };
  } else if (journal.phase === "campaign-child-active" || journal.phase === "comparison-cleanup-pending" || journal.phase === "campaign-outcome-bound") {
    // Re-prove exact recorded child terminality/cgroup emptiness, observation-only.
    const child = await proveChildTerminalObservationOnly(loaded, journal, actions);
    if (!child.ok) return child;
  }
  const isolation = await convergeIsolation(loaded, actions, []);
  if (!isolation.ok) return isolation;
  return { ok: true };
}

// M8 exact-once campaign settlement at readiness-proven. Freshly re-proves the
// exact live authority and production/readiness/isolation/child/endpoint facts
// immediately before the settlement effect (one effect bundle per invocation),
// then settles through the exact receipt-first-then-unlink campaign primitive,
// which derives the terminal status mechanically from the durably bound outcome.
// If the journal reached readiness-proven without a usable child outcome, it
// holds safely/manual and never fabricates pass.
async function settleCampaign(loaded, journal, actions, assertCurrent) {
  const lockPath = loaded.configuration.custody.lockPath;
  const expectedOwnerUid = loaded.expectedOwnerUid;
  const receipt = journal.guardianStartReceipt;
  if (!receipt) return manual(journal, "manual-attention-campaign-start-not-proven", {});
  // Outcome authority: settlement is forbidden without a durably bound outcome.
  // A recovery path that never acquired a usable child outcome holds safely/
  // manual rather than fabricating pass from readiness/phase alone.
  if (journal.campaignOutcome === null) return manual(journal, "manual-attention-campaign-no-outcome", { comparisonIsolationAbsent: true, campaignChildTerminal: true });
  // Last-moment authority + exact live reproof before the fresh readiness probe.
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-error", {}); }
  let before;
  try { before = await provePostStartRunning(loaded, actions); }
  catch { return manual(journal, "manual-attention-campaign-readiness-unstable", {}); }
  if (!before.ok) return manual(journal, before.status === "manual-attention-campaign-production-missing" ? "manual-attention-campaign-readiness-unstable" : before.status, {});
  if (before.fingerprint.startedAt !== receipt.startedAt || before.fingerprint.containerId !== receipt.containerId) return manual(journal, "manual-attention-campaign-production-substituted", {});
  const child = await proveM7ChildTerminality(loaded, journal, actions);
  if (!child.ok) return manual(journal, child.status, {});
  const isolation = await convergeIsolation(loaded, actions, [actions.production.containerId]);
  if (!isolation.ok) return manual(journal, isolation.status, {});
  const endpointIdentity = await actions.secureSocketIdentity(actions.dockerSocketPath);
  if (typeof endpointIdentity !== "string" || !SHA_RE.test(endpointIdentity) || endpointIdentity !== receipt.endpointIdentity) return manual(journal, "manual-attention-campaign-endpoint-missing", {});
  // Fresh bounded credential-free loopback readiness probe at the settlement
  // seam bound to the exact model/body/Content-Length/strict UTF-8/strict JSON.
  // A stale M7 readiness record is not enough. The fresh probe must occur
  // strictly after the recorded M7 readiness and at/after the exact fresh
  // production start.
  let probe;
  try { probe = await actions.probeReadiness(actions.production); }
  catch { probe = null; }
  if (!probe || probe.status !== 200 || !Number.isSafeInteger(probe.latencyMilliseconds) || probe.latencyMilliseconds < 0 || typeof probe.provenAt !== "string" || !PROVEN_RE.test(probe.provenAt) || typeof probe.responseSha256 !== "string" || !SHA_RE.test(probe.responseSha256) || probe.responseSha256 === ZERO_SHA256) {
    return manual(journal, "manual-attention-campaign-readiness-failed", {});
  }
  const m7ProvenMs = journal.readinessEvidence?.provenAt ? Date.parse(journal.readinessEvidence.provenAt) : NaN;
  const freshStartMs = Date.parse(receipt.startedAt);
  if (!Number.isFinite(freshStartMs) || !Number.isFinite(m7ProvenMs) || Date.parse(probe.provenAt) < freshStartMs || Date.parse(probe.provenAt) <= m7ProvenMs) {
    return manual(journal, "manual-attention-campaign-readiness-failed", {});
  }
  // Immediately re-inspect production: require the exact canonical full
  // fingerprint stability (container, image, fresh StartedAt, restartCount 0)
  // before vs after the fresh probe.
  let after;
  try { after = await provePostStartRunning(loaded, actions); }
  catch { return manual(journal, "manual-attention-campaign-readiness-unstable", {}); }
  if (!after.ok || canonical(after.fingerprint) !== canonical(before.fingerprint)) return manual(journal, "manual-attention-campaign-readiness-unstable", {});
  // Full last live reproof after the stable post-probe fingerprint: re-prove
  // the exact running production, terminal/absent child, clean isolation, and
  // secure endpoint identity.
  const last = await reprovePostStartLive(loaded, journal, actions, receipt);
  if (!last.ok) return manual(journal, last.status === "manual-attention-campaign-production-missing" ? "manual-attention-campaign-readiness-unstable" : last.status, {});
  if (canonical(last.fingerprint) !== canonical(before.fingerprint)) return manual(journal, "manual-attention-campaign-readiness-unstable", {});
  // FINAL exact authority recheck immediately before the settlement effect.
  // This must be the last async operation before settlement: no further async
  // proof is performed between this recheck and the settlement, so a
  // journal/lease drift at this last seam can never produce a settlement
  // call/receipt/unlink.
  try { await assertCurrent(journal); }
  catch { return manual(journal, "manual-attention-campaign-error", {}); }
  let settled;
  try {
    settled = await actions.settleJournal(lockPath, expectedOwnerUid, { expectedIdentity: deriveCampaignJournalIdentity(journal) });
  } catch {
    return manual(journal, "manual-attention-campaign-error", { comparisonIsolationAbsent: true, campaignChildTerminal: true });
  }
  return result(CAMPAIGN_SETTLED_STATUS, journal, {
    comparisonIsolationAbsent: true,
    campaignChildTerminal: true,
    attention: false,
    settledCampaignStatus: settled.status,
    campaignOutcomeSha256: journal.campaignOutcome.outcomeSha256,
  });
}

async function recover(loaded, journal, actions, custodyIdentitySha256, dependencies) {
  const phaseOrder = CAMPAIGN_RECOVERY_PHASES[journal.phase];
  const lockPath = loaded.configuration.custody.lockPath;
  const expectedOwnerUid = loaded.expectedOwnerUid;
  // Each invocation performs at most one identity-bound phase transition/effect
  // bundle and then returns. A watcher may immediately loop on a non-manual
  // progress status. No effect after a journal advance may use the prior lease:
  // assertCurrent re-loads the exact binding and active journal and requires a
  // live lease bound to that same journal identity immediately before every
  // cancel, production stop, child-unit stop, and journal advance.
  const assertCurrent = (expectedJournal) => (dependencies.assertCurrentAuthority ?? assertCurrentAuthority)(loaded, expectedJournal, dependencies, custodyIdentitySha256);

  // Last-moment transition seam: every journal advance is preceded by both the
  // exact config/custody/journal/lease authority recheck (assertCurrent) and a
  // fresh phase-specific live-state reproof (reproveTransitionLiveState) so the
  // durable phase is never persisted from stale live evidence.
  const assertTransitionReady = async (targetPhase, expectedJournal) => {
    await assertCurrent(expectedJournal);
    const live = await reproveTransitionLiveState(targetPhase, loaded, expectedJournal, actions);
    if (!live.ok) return { ok: false, status: live.status };
    return { ok: true };
  };

  // prepared: cancel only after mechanically proving inert. One bundle, then return.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES.prepared) {
    if (journal.campaignChildNonce !== null || journal.campaignChild !== null) return manual(journal, "manual-attention-campaign-error", {});
    const observed = await actions.inspectContainer(actions.production.containerId, actions.dockerPath);
    if (observed === null) return manual(journal, "manual-attention-campaign-production-missing", {});
    let running;
    try { running = actions.validateProduction(observed, actions.production, { running: true, requireReviewedStart: false }); }
    catch { return manual(journal, "manual-attention-campaign-production-substituted", {}); }
    if (!(running.startedAt === actions.production.expectedStartedAt && running.restartCount === actions.production.expectedRestartCount)) return manual(journal, "manual-attention-campaign-production-substituted", {});
    const isolation = await convergeIsolation(loaded, actions, [actions.production.containerId]);
    if (!isolation.ok) return manual(journal, isolation.status, { comparisonIsolationAbsent: false, campaignChildTerminal: true });
    await assertCurrent(journal);
    await actions.cancelJournal(lockPath, expectedOwnerUid, deriveCampaignJournalIdentity(journal));
    return result(PREPARED_CANCELLED_STATUS, journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
  }

  // readiness-proven: M8 exact-once settlement. Freshly re-proves the exact
  // live production/readiness/isolation/child/endpoint facts immediately before
  // the settlement effect and settles once (receipt-first-then-unlink). A
  // journal with no durably bound outcome holds safely/manual; it never
  // fabricates pass from readiness/phase alone.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["readiness-proven"]) {
    return settleCampaign(loaded, journal, actions, assertCurrent);
  }

  // M7 phases (isolation-clean and beyond). Each pass performs at most one
  // identity-bound transition/effect bundle and returns.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["isolation-clean"]) {
    return authorizeProductionStart(loaded, journal, actions, assertTransitionReady);
  }
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["production-start-authorized"]) {
    return performProductionStart(loaded, journal, actions, assertCurrent);
  }
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["production-started"]) {
    return proveReadiness(loaded, journal, actions, assertCurrent);
  }

  // production-stop-authorized: one bundle stops the exact production and
  // advances to production-stopped, then returns a non-manual progress status
  // so a watcher loops immediately for the next pass.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["production-stop-authorized"]) {
    const stopped = await convergeProductionStopped(loaded, actions, assertCurrent, journal);
    if (!stopped.ok) return manual(journal, stopped.status, {});
    const transition = await assertTransitionReady("production-stopped", journal);
    if (!transition.ok) return manual(journal, transition.status, {});
    const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "production-stopped", deriveCampaignJournalIdentity(journal), {});
    return result(CAMPAIGN_RECOVERY_PROGRESS_STATUS, advanced.journal, { comparisonIsolationAbsent: false, campaignChildTerminal: false, attention: false });
  }

  // production-stopped: no child was ever authorized. After exact stopped-
  // production proof and clean isolation, advance directly to isolation-clean
  // and return the M7 hold.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["production-stopped"]) {
    const stopped = await proveProductionStopped(loaded, actions);
    if (!stopped.ok) return manual(journal, stopped.status, {});
    const isolation = await convergeIsolation(loaded, actions, []);
    if (!isolation.ok) return manual(journal, isolation.status, { comparisonIsolationAbsent: false, campaignChildTerminal: false });
    const transition = await assertTransitionReady("isolation-clean", journal);
    if (!transition.ok) return manual(journal, transition.status, {});
    const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "isolation-clean", deriveCampaignJournalIdentity(journal), {});
    return result(ISOLATION_CLEAN_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
  }

  // campaign-child-authorized: a nonce exists but no child identity was
  // recorded. If the exact nonce-derived unit is authoritatively not-found and
  // isolation is clean, advance to isolation-clean without claiming campaign
  // completion. If the unit exists or its absence is ambiguous, fail closed.
  // Never launch/relaunch or fabricate a child identity.
  if (phaseOrder === CAMPAIGN_RECOVERY_PHASES["campaign-child-authorized"]) {
    const stopped = await proveProductionStopped(loaded, actions);
    if (!stopped.ok) return manual(journal, stopped.status, {});
    const show = await actions.showUnit(deriveCampaignChildUnitName(journal.campaignChildNonce), actions.systemdDeps);
    if (!show.ok) return manual(journal, "manual-attention-campaign-child-not-found", {});
    if (show.facts.LoadState !== "not-found") return manual(journal, "manual-attention-campaign-child-substituted", {});
    const isolation = await convergeIsolation(loaded, actions, []);
    if (!isolation.ok) return manual(journal, isolation.status, { comparisonIsolationAbsent: false, campaignChildTerminal: false });
    const transition = await assertTransitionReady("isolation-clean", journal);
    if (!transition.ok) return manual(journal, transition.status, {});
    const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "isolation-clean", deriveCampaignJournalIdentity(journal), {});
    return result(ISOLATION_CLEAN_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
  }

  // campaign-child-active / comparison-cleanup-pending: prove/enforce exact
  // child terminality, prove isolation, then advance once to isolation-clean.
  const stopped = await proveProductionStopped(loaded, actions);
  if (!stopped.ok) return manual(journal, stopped.status, {});
  const child = await convergeCampaignChild(loaded, journal, actions, assertCurrent);
  if (!child.ok) return manual(journal, child.status, { comparisonIsolationAbsent: false, campaignChildTerminal: false });
  const isolation = await convergeIsolation(loaded, actions, []);
  if (!isolation.ok) return manual(journal, isolation.status, { comparisonIsolationAbsent: false, campaignChildTerminal: child.terminal });
  const transition = await assertTransitionReady("isolation-clean", journal);
  if (!transition.ok) return manual(journal, transition.status, {});
  const advanced = await actions.advanceJournal(lockPath, expectedOwnerUid, "isolation-clean", deriveCampaignJournalIdentity(journal), {});
  return result(ISOLATION_CLEAN_STATUS, advanced.journal, { comparisonIsolationAbsent: true, campaignChildTerminal: true, attention: false });
}

export async function runCampaignRecoveryGuardian(configPath, dependencies = {}) {
  const preLock = await loadGuardian(configPath, dependencies);
  const withCustody = dependencies.withCustody ?? ((binding, uid, operation) => withMaintenanceCustody(binding, uid, operation));
  return withCustody(preLock.configuration.custody, preLock.expectedOwnerUid, async (custodyIdentitySha256) => {
    const inspected = await inspectActiveCampaignRecoveryJournal(preLock.configuration.custody.lockPath, preLock.expectedOwnerUid);
    if (!inspected) return noActiveResult();
    let underLock;
    try {
      underLock = await loadCampaignRecoveryBinding(configPath, { expectedOwnerUid: preLock.expectedOwnerUid, inventory: dependencies.inventory, inspectContainer: dependencies.inspectContainer, inspectSource: dependencies.inspectSource });
    } catch (error) {
      return manual(inspected.journal, "manual-attention-campaign-config-changed", {});
    }
    if (underLock.configBytesSha !== preLock.configBytesSha) return manual(inspected.journal, "manual-attention-campaign-config-changed", {});
    const identityCheck = revalidateIdentity(inspected.journal, underLock, custodyIdentitySha256);
    if (!identityCheck.ok) return manual(inspected.journal, identityCheck.status, {});
    const actions = campaignGuardianActions(dependencies, underLock);
    const guardianModulePath = fileURLToPath(import.meta.url);
    const guardianModuleSha256 = dependencies.guardianModuleSha256 ?? sha(await readFile(guardianModulePath));
    const expectedNodePath = dependencies.expectedNodePath ?? await realpath(process.execPath);
    const expectedUnitSha256 = dependencies.expectedUnitSha256 ?? (await renderCampaignGuardianUnit({ configPath, nodePath: expectedNodePath, guardianPath: guardianModulePath, expectedOwnerUid: underLock.expectedOwnerUid })).renderSha256;
    try {
      await actions.requireLease(underLock.configuration.custody.lockPath, underLock.expectedOwnerUid, underLock.configBytesSha, {
        unit: dependencies.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT,
        configPath,
        custodyIdentitySha256: underLock.custody.identitySha256,
        guardianModuleSha256,
        expectedUnitSha256,
        expectedNodePath,
        campaignOperationSha256: inspected.journal.campaignOperationSha256,
        journalIdentitySha256: deriveCampaignJournalIdentitySha256(inspected.journal),
        maxAgeMs: dependencies.guardianLeaseMaxAgeMs,
        bootId: dependencies.guardianLeaseBootId,
      });
    } catch (error) {
      return manual(inspected.journal, "manual-attention-campaign-error", {});
    }
    try {
      return await recover({ ...underLock, configPath }, inspected.journal, actions, custodyIdentitySha256, dependencies);
    } catch (error) {
      return manual(inspected.journal, "manual-attention-campaign-error", {});
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

export async function watchCampaignRecoveryGuardian(configPath, dependencies = {}) {
  const signal = dependencies.signal ?? null;
  const runRecovery = dependencies.runRecovery ?? ((path, deps) => runCampaignRecoveryGuardian(path, deps));
  const recoveryDependencies = dependencies.recoveryDependencies ?? {};
  const waitForTrigger = dependencies.waitForTrigger ?? defaultWaitForTrigger;
  const sleep = dependencies.sleep ?? defaultSleep;
  const refreshLease = dependencies.refreshLease ?? refreshCampaignGuardianLease;
  const removeLease = dependencies.removeLease ?? removeCampaignGuardianLease;
  const withCustody = dependencies.withCustody ?? ((binding, uid, operation) => withMaintenanceCustody(binding, uid, operation));
  const inspectJournal = dependencies.inspectJournal ?? inspectActiveCampaignRecoveryJournal;
  const reconcileBackoffMs = boundedMs(dependencies.reconcileBackoffMs, DEFAULT_RECONCILE_BACKOFF_MS, 5000, 3600000, "campaign guardian reconcile backoff");
  const manualBackoffMs = boundedMs(dependencies.manualBackoffMs, DEFAULT_MANUAL_BACKOFF_MS, 5000, 86400000, "campaign guardian manual backoff");
  const loaded = await loadGuardian(configPath, { expectedOwnerUid: dependencies.expectedOwnerUid, inspectSource: dependencies.inspectSource, inventory: dependencies.inventory, inspectContainer: dependencies.inspectContainer });
  const journalDir = dirname(loaded.configuration.custody.lockPath);
  const expectedOwnerUid = loaded.expectedOwnerUid;
  const custodyLockPath = loaded.configuration.custody.lockPath;
  const configSha256 = loaded.configBytesSha;
  const guardianUnit = dependencies.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT;
  const guardianModulePath = fileURLToPath(import.meta.url);
  const guardianModuleSha256 = dependencies.guardianModuleSha256 ?? sha(await readFile(guardianModulePath));
  const expectedNodePath = dependencies.expectedNodePath ?? await realpath(process.execPath);
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
  const publishCurrentLease = async () => {
    // Publish/refresh only from a freshly loaded under-custody binding and the
    // exact current journal; never from the startup-time loaded binding after
    // config/journal changes. A failed load, config drift, or absent journal
    // publishes nothing and fails closed.
    let leasePublished = null;
    try {
      leasePublished = await withCustody(loaded.configuration.custody, expectedOwnerUid, async (custodyIdentitySha256) => {
        let underLock;
        try { underLock = await loadCampaignRecoveryBinding(configPath, { expectedOwnerUid, inventory: dependencies.inventory, inspectContainer: dependencies.inspectContainer, inspectSource: dependencies.inspectSource }); }
        catch { return null; }
        if (underLock.configBytesSha !== configSha256) return null;
        const inspected = await inspectJournal(custodyLockPath, expectedOwnerUid);
        if (!inspected) return null;
        const expectedUnitSha256 = dependencies.expectedUnitSha256 ?? (await renderCampaignGuardianUnit({ configPath, nodePath: expectedNodePath, guardianPath: guardianModulePath, expectedOwnerUid })).renderSha256;
        await refreshLease(custodyLockPath, expectedOwnerUid, underLock.configBytesSha, {
          unit: guardianUnit,
          custodyIdentitySha256: underLock.custody.identitySha256,
          guardianModuleSha256,
          expectedUnitSha256,
          expectedNodePath,
          campaignOperationSha256: inspected.journal.campaignOperationSha256,
          journalIdentitySha256: deriveCampaignJournalIdentitySha256(inspected.journal),
          ready: true,
        });
        return Object.freeze({ journalIdentitySha256: deriveCampaignJournalIdentitySha256(inspected.journal) });
      });
    } catch {
      leasePublished = null;
    }
    return leasePublished;
  };

  try {
    while (signal ? !signal.aborted : true) {
      // Every pass publishes/refreshes a ready lease from a freshly loaded
      // under-custody binding and the exact current journal before recovery.
      await publishCurrentLease();
      let resultValue = null;
      try { resultValue = await runRecovery(configPath, recoveryDependencies); }
      catch { resultValue = null; }
      if (signal?.aborted) break;
      // After an exact settlement or no-active state, durably remove only the
      // guardian's own lease so no dormant stale lease claims authority, while
      // the watcher itself stays alive to publish a fresh lease for a later new
      // journal (it must never be disabled). Both statuses mean the active
      // journal is gone, so removing the lease cannot break a current recovery.
      if (resultValue && (resultValue.status === NO_ACTIVE_STATUS || resultValue.status === CAMPAIGN_SETTLED_STATUS)) {
        await removeOwnLease();
      }
      if (resultValue && resultValue.manualAttentionRequired) {
        await sleep(manualBackoffMs, signal);
        continue;
      }
      // Non-manual progress: immediately perform another publish-lease/recovery
      // iteration instead of sleeping for the ordinary trigger.
      if (resultValue && resultValue.progress) {
        continue;
      }
      await waitForTrigger(journalDir, signal, reconcileBackoffMs);
      // After the trigger, fail closed on every lease refresh: refresh only
      // from a freshly loaded under-custody binding and the exact current
      // journal. A failed refresh means the watcher cannot maintain a current
      // ready lease and recovery must not proceed.
      await publishCurrentLease();
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
  if (!Array.isArray(argv) || argv.length !== 3 || !["recover", "watch"].includes(argv[0]) || argv[1] !== "--config" || !argv[2]) fail("Usage: maintenance-campaign-recovery-guardian.mjs recover|watch --config PRIVATE_JSON");
  return { command: argv[0], configPath: resolve(argv[2]) };
}

export async function main(argv = process.argv.slice(2), dependencies = {}) {
  const options = parseArguments(argv);
  if (options.command === "watch") {
    await watchCampaignRecoveryGuardian(options.configPath, dependencies);
    return;
  }
  const value = await runCampaignRecoveryGuardian(options.configPath, dependencies);
  process.stdout.write(`${JSON.stringify(value)}\n`);
  if (value.status !== NO_ACTIVE_STATUS && value.status !== PREPARED_CANCELLED_STATUS && value.status !== M8_HOLD_STATUS && value.status !== CAMPAIGN_SETTLED_STATUS) process.exitCode = 3;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-campaign-maintenance-recovery: ${error instanceof WorkCampaignRecoveryGuardianError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const campaignRecoveryGuardianBoundaries = Object.freeze({ result: GUARDIAN_RESULT_BOUNDARY, attention: GUARDIAN_ATTENTION_BOUNDARY });

// Testable helper exporting the exact default bounded readiness probe so the
// stream-bound and strict-parsing behavior can be verified deterministically
// with an injected fetch implementation and no live endpoint.
export { probeProductionReadinessDefault };
export const campaignRecoveryGuardianManualStatuses = MANUAL_STATUSES;
export const campaignRecoveryGuardianStatuses = GUARDIAN_STATUSES;
export const CAMPAIGN_RECOVERY_ENGINE_READY = true;

export function campaignRecoveryEngineState() {
  return Object.freeze({ ready: CAMPAIGN_RECOVERY_ENGINE_READY });
}

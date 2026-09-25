import { constants } from "node:fs";
import { createHash } from "node:crypto";
import { link, lstat, open, realpath, rename, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateProcessIdentity } from "./maintenance-recovery-guardian-lease.mjs";

const MAX_JOURNAL_BYTES = 256 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const ZERO_SHA256 = "0".repeat(64);
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/u;
const IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const STARTED_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u;
const STATUS_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/u;

const OPERATION = "pixel-work-model-qualification-maintenance";
const JOURNAL_KIND = "pixel-maintenance-recovery-journal";
const RECEIPT_KIND = "pixel-maintenance-recovery-receipt";
const CAMPAIGN_OPERATION = "pixel-work-model-campaign-maintenance";
const CAMPAIGN_JOURNAL_KIND = "pixel-campaign-maintenance-recovery-journal";
const CAMPAIGN_RECEIPT_KIND = "pixel-campaign-maintenance-recovery-receipt";

export const RECOVERY_PHASES = Object.freeze({
  prepared: 0,
  "production-stopped": 1,
  "qualification-active": 2,
  "restore-pending": 3,
  "isolation-clean": 4,
  "production-start-authorized": 5,
  "production-started": 6,
});

export const SETTLED_MAINTENANCE_STATUSES = Object.freeze([
  "qualified-production-restored",
  "degraded-production-restored",
  "failed-production-restored",
  "qualification-error-production-restored",
  "guardian-production-restored",
]);

// The active campaign recovery phases. Settlement is a durable terminal
// receipt (settled: true) published before the active journal is removed; it is
// not itself an active-journal phase.
export const CAMPAIGN_RECOVERY_PHASES = Object.freeze({
  prepared: 0,
  "production-stop-authorized": 1,
  "production-stopped": 2,
  "campaign-child-authorized": 3,
  "campaign-child-active": 4,
  "comparison-cleanup-pending": 5,
  "campaign-outcome-bound": 6,
  "isolation-clean": 7,
  "production-start-authorized": 8,
  "production-started": 9,
  "readiness-proven": 10,
});

export const SETTLED_CAMPAIGN_STATUSES = Object.freeze([
  "campaign-pass-production-restored",
  "campaign-in-progress-production-restored",
  "campaign-blocked-production-restored",
  "campaign-error-production-restored",
  "tuning-frozen-production-restored",
]);

// The exact closed set of semantic campaign outcome categories that may be
// durably bound and later mechanically mapped to a terminal settlement status.
// pass is never inferred from readiness/phase; it must be produced by the
// campaign child and strictly validated before it may be recorded.
export const CAMPAIGN_OUTCOME_STATUSES = Object.freeze([
  "pass",
  "in-progress",
  "blocked",
  "error",
  "tuning-frozen",
]);

// The exact closed set of statuses that constitute a genuine campaign result
// produced and validated by the campaign child. Every other status is a
// classified error.
const GENUINE_CAMPAIGN_OUTCOME_STATUSES = Object.freeze([
  "pass",
  "in-progress",
  "blocked",
  "tuning-frozen",
]);

// The exact allowlist of classified error classes that may be durably bound as
// an error outcome. These are the only failure classes the campaign child and
// controller can produce; anything else must fail closed rather than be
// recorded as an outcome.
export const CAMPAIGN_OUTCOME_FAILURE_CLASSES = Object.freeze([
  "timeout",
  "child-exit-2",
  "child-exit-other",
  "invalid-strict-result",
  "controller-error",
]);

// The canonical content-free campaignOutcome shape. It is an exact
// discriminated union selected by `kind`:
//   - kind "genuine" (status pass/in-progress/blocked/tuning-frozen) carries a
//     real validated campaignId, a content-free result identity, the real
//     exitCode, and truthful bounded pair counts; the failure fields are
//     exactly null.
//   - kind "error" (status error) carries campaignId/resultSha256/exitCode
//     exactly null unless truly observed, an explicit allowlisted
//     failureClass, a content-free diagnostic identity, and truthful bounded
//     pair counts.
// Never raw prompts, model output, stderr, diagnostic text, secrets, or other
// sensitive/semantic content. outcomeSha256 is the deterministic canonical
// SHA-256 of every field except itself.
const CAMPAIGN_OUTCOME_KEYS = Object.freeze([
  "schemaVersion",
  "kind",
  "status",
  "campaignId",
  "resultSha256",
  "exitCode",
  "completedPairs",
  "requiredPairs",
  "failureClass",
  "diagnosticSha256",
  "outcomeSha256",
]);
const CAMPAIGN_ID_RE = /^outcomebattery-[a-f0-9]{24}$/u;
const OUTCOME_SCHEMA_VERSION = 1;

function outcomeContentHash(partial) {
  return createHash("sha256").update(canonical(partial), "utf8").digest("hex");
}

export function validateCampaignOutcome(value, label = "campaign recovery journal campaign outcome") {
  if (value === null) return null;
  exactKeys(value, CAMPAIGN_OUTCOME_KEYS, label);
  if (value.schemaVersion !== OUTCOME_SCHEMA_VERSION) fail(`${label} schema is invalid`);
  if (!CAMPAIGN_OUTCOME_STATUSES.includes(value.status ?? "")) fail(`${label} status is invalid`);
  if (value.kind !== "genuine" && value.kind !== "error") fail(`${label} kind is invalid`);
  const genuine = value.kind === "genuine";
  if (genuine && value.status === "error") fail(`${label} genuine outcome cannot carry an error status`);
  if (!genuine && value.status !== "error") fail(`${label} error outcome must carry the error status`);
  // Pair counts are always truthful bounded counts; the genuine statuses
  // enforce their exact completeness invariants below.
  integer(value.requiredPairs, 1, 500, `${label} required pair count`);
  integer(value.completedPairs, 0, value.requiredPairs, `${label} completed pair count`);
  if (genuine) {
    // A genuine result carries the exact validated campaign identity, a real
    // content-free result identity, and the real exit code; failure fields are
    // exactly null.
    if (!CAMPAIGN_ID_RE.test(value.campaignId ?? "")) fail(`${label} campaign identity is invalid`);
    if (!SHA_RE.test(value.resultSha256 ?? "") || value.resultSha256 === ZERO_SHA256) fail(`${label} result identity is invalid`);
    if (!Number.isSafeInteger(value.exitCode)) fail(`${label} exit code is invalid`);
    if (value.failureClass !== null) fail(`${label} genuine outcome must not carry a failure class`);
    if (value.diagnosticSha256 !== null) fail(`${label} genuine outcome must not carry a diagnostic identity`);
    // The durable genuine union enforces the same truthful invariants as the
    // source campaign result: pass/tuning-frozen are complete at exit 0,
    // in-progress is incomplete at exit 3, and blocked is exit 3 with any
    // already-valid bounded count (including equal to requiredPairs).
    if (value.status === "pass" || value.status === "tuning-frozen") {
      if (value.exitCode !== 0) fail(`${label} ${value.status} outcome exit code is invalid`);
      if (value.completedPairs !== value.requiredPairs) fail(`${label} ${value.status} outcome pair counts are inconsistent`);
    } else if (value.status === "in-progress") {
      if (value.exitCode !== 3) fail(`${label} in-progress outcome exit code is invalid`);
      if (value.completedPairs >= value.requiredPairs) fail(`${label} in-progress outcome pair counts are inconsistent`);
    } else if (value.status === "blocked") {
      if (value.exitCode !== 3) fail(`${label} blocked outcome exit code is invalid`);
    }
  } else {
    // A classified error never fabricates a campaign identity/result/exit code:
    // they are exactly null unless truly observed. The failure class must be
    // allowlisted and a content-free diagnostic identity is required.
    if (value.campaignId !== null && !CAMPAIGN_ID_RE.test(value.campaignId ?? "")) fail(`${label} campaign identity is invalid`);
    if (value.resultSha256 !== null && (!SHA_RE.test(value.resultSha256 ?? "") || value.resultSha256 === ZERO_SHA256)) fail(`${label} result identity is invalid`);
    if (!CAMPAIGN_OUTCOME_FAILURE_CLASSES.includes(value.failureClass ?? "")) fail(`${label} failure class is invalid`);
    if (!SHA_RE.test(value.diagnosticSha256 ?? "") || value.diagnosticSha256 === ZERO_SHA256) fail(`${label} diagnostic identity is invalid`);
    // A classified error never invents an observation: an exit code is only
    // coherent when the failure class truthfully carries one, and timeout /
    // controller-error must keep exitCode null.
    if (value.exitCode !== null) {
      if (!Number.isSafeInteger(value.exitCode)) fail(`${label} exit code is invalid`);
      if (value.failureClass === "child-exit-2" && value.exitCode !== 2) fail(`${label} child-exit-2 outcome exit code is incoherent`);
      if (value.failureClass === "child-exit-other" && (value.exitCode === 0 || value.exitCode === 2 || value.exitCode === 3)) fail(`${label} child-exit-other outcome exit code is incoherent`);
      if (value.failureClass === "invalid-strict-result" && value.exitCode !== 0 && value.exitCode !== 3) fail(`${label} invalid-strict-result outcome exit code is incoherent`);
    }
    if ((value.failureClass === "timeout" || value.failureClass === "controller-error") && value.exitCode !== null) fail(`${label} ${value.failureClass} outcome must keep a null exit code`);
  }
  if (!SHA_RE.test(value.outcomeSha256 ?? "") || value.outcomeSha256 === ZERO_SHA256) fail(`${label} outcome identity is invalid`);
  const { outcomeSha256, ...content } = value;
  if (value.outcomeSha256 !== outcomeContentHash(content)) fail(`${label} is not bound to its deterministic canonical hash`);
  return structuredClone(value);
}

// Derive the canonical content-free genuine campaignOutcome from an
// already-validated genuine status/campaign identity, a content-free result
// identity, the real exit code, and truthful pair counts. Never accepts or
// forwards raw semantic bytes. pass requires exitCode 0 and complete pairs.
export function deriveCampaignOutcome({ status, campaignId, resultSha256, exitCode, completedPairs, requiredPairs }) {
  if (!GENUINE_CAMPAIGN_OUTCOME_STATUSES.includes(status ?? "")) fail("campaign outcome genuine status is invalid");
  if (!CAMPAIGN_ID_RE.test(campaignId ?? "")) fail("campaign outcome campaign identity is invalid");
  if (!SHA_RE.test(resultSha256 ?? "") || resultSha256 === ZERO_SHA256) fail("campaign outcome result identity is invalid");
  const base = {
    schemaVersion: OUTCOME_SCHEMA_VERSION,
    kind: "genuine",
    status,
    campaignId,
    resultSha256,
    exitCode,
    completedPairs,
    requiredPairs,
    failureClass: null,
    diagnosticSha256: null,
  };
  return validateCampaignOutcome({ ...base, outcomeSha256: outcomeContentHash(base) });
}

// Derive the canonical content-free classified error campaignOutcome. A
// classified error never fabricates a campaign identity/result/exit code:
// those fields stay null unless truly observed, and the failure class must be
// allowlisted.
export function deriveCampaignErrorOutcome({ failureClass, diagnosticSha256, completedPairs, requiredPairs, campaignId = null, resultSha256 = null, exitCode = null }) {
  if (!CAMPAIGN_OUTCOME_FAILURE_CLASSES.includes(failureClass ?? "")) fail("campaign outcome failure class is invalid");
  if (!SHA_RE.test(diagnosticSha256 ?? "") || diagnosticSha256 === ZERO_SHA256) fail("campaign outcome diagnostic identity is invalid");
  const base = {
    schemaVersion: OUTCOME_SCHEMA_VERSION,
    kind: "error",
    status: "error",
    campaignId,
    resultSha256,
    exitCode,
    completedPairs,
    requiredPairs,
    failureClass,
    diagnosticSha256,
  };
  return validateCampaignOutcome({ ...base, outcomeSha256: outcomeContentHash(base) });
}

export function deriveCampaignOutcomeSha256(outcome) {
  return validateCampaignOutcome(outcome).outcomeSha256;
}

// The exact terminal settlement status is always derived mechanically from the
// durably bound outcome status; a caller can never choose or mismatch it.
const TERMINAL_STATUS_FROM_OUTCOME = Object.freeze({
  "pass": "campaign-pass-production-restored",
  "in-progress": "campaign-in-progress-production-restored",
  "blocked": "campaign-blocked-production-restored",
  "error": "campaign-error-production-restored",
  "tuning-frozen": "tuning-frozen-production-restored",
});

export function deriveTerminalCampaignStatusFromOutcome(outcome) {
  const validated = validateCampaignOutcome(outcome);
  return TERMINAL_STATUS_FROM_OUTCOME[validated.status];
}

export class WorkMaintenanceRecoveryJournalError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoveryJournalError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function validateCustodyLockPath(custodyLockPath) {
  if (typeof custodyLockPath !== "string" || !isAbsolute(custodyLockPath) || resolve(custodyLockPath) !== custodyLockPath || custodyLockPath.includes("\0") || /[\r\n]/u.test(custodyLockPath)) fail("recovery journal custody lock path is noncanonical or unsafe");
}

async function lstatExact(path) {
  try { return await lstat(path); } catch (error) { if (error && error.code === "ENOENT") return null; throw error; }
}

// Shared hardened identity validation for the exact production container that
// both qualification and campaign windows must agree on.
function validateProductionIdentity(production, label) {
  exactKeys(production, ["containerName", "containerId", "imageDigest", "expectedStartedAt", "expectedRestartCount"], `${label} production`);
  if (!NAME_RE.test(production.containerName ?? "") || !SHA_RE.test(production.containerId ?? "") || production.containerId === ZERO_SHA256 || !IMAGE_RE.test(production.imageDigest ?? "") || production.imageDigest === `sha256:${ZERO_SHA256}` || !STARTED_RE.test(production.expectedStartedAt ?? "") || !Number.isFinite(Date.parse(production.expectedStartedAt))) fail(`${label} production identity is invalid`);
  integer(production.expectedRestartCount, 0, 2147483647, `${label} production restart count`);
}

// Shared production-start authorization/receipt/started coherence. At
// production-start-authorized a nonce+endpoint contract is recorded without a
// receipt; at/after production-started a durable bound 204 receipt and the
// exact post-start StartedAt must be present.
function validateGuardianStartFields(value, label, phases) {
  if (value.guardianStartedAt !== null && (!STARTED_RE.test(value.guardianStartedAt ?? "") || !Number.isFinite(Date.parse(value.guardianStartedAt)))) fail(`${label} guardian start is invalid`);
  if (value.guardianStartNonce !== null && !SHA_RE.test(value.guardianStartNonce ?? "")) fail(`${label} guardian start nonce is invalid`);
  if (value.guardianStartEndpointIdentity !== null && (!SHA_RE.test(value.guardianStartEndpointIdentity ?? "") || value.guardianStartEndpointIdentity === ZERO_SHA256)) fail(`${label} guardian start endpoint identity is invalid`);
  if (value.guardianStartReceipt !== null) {
    exactKeys(value.guardianStartReceipt, ["nonce", "containerId", "endpointIdentity", "status", "startedAt"], `${label} guardian start receipt`);
    if (value.guardianStartReceipt.nonce !== value.guardianStartNonce) fail(`${label} guardian start receipt is not bound to the recorded nonce`);
    if (value.guardianStartReceipt.containerId !== value.production.containerId) fail(`${label} guardian start receipt is not bound to the exact production container id`);
    if (value.guardianStartReceipt.status !== 204) fail(`${label} guardian start receipt must record an actual 204 transition by this request`);
    if (!SHA_RE.test(value.guardianStartReceipt.endpointIdentity ?? "") || value.guardianStartReceipt.endpointIdentity !== value.guardianStartEndpointIdentity) fail(`${label} guardian start receipt endpoint identity is invalid or not bound to the authorized endpoint`);
    if (value.guardianStartReceipt.startedAt !== value.guardianStartedAt || !STARTED_RE.test(value.guardianStartReceipt.startedAt ?? "") || !Number.isFinite(Date.parse(value.guardianStartReceipt.startedAt))) fail(`${label} guardian start receipt is not bound to the exact post-start StartedAt`);
  }
  const phaseOrder = phases[value.phase];
  const startedOrder = phases["production-started"];
  const authorizedOrder = phases["production-start-authorized"];
  if (phaseOrder < startedOrder && value.guardianStartedAt !== null) fail(`${label} guardian start must be null before production-started`);
  if (phaseOrder >= startedOrder && value.guardianStartedAt === null) fail(`${label} guardian start must be recorded at or after production-started`);
  if (phaseOrder < authorizedOrder && value.guardianStartNonce !== null) fail(`${label} guardian start nonce must be null before production-start-authorized`);
  if (phaseOrder < authorizedOrder && value.guardianStartEndpointIdentity !== null) fail(`${label} guardian start endpoint identity must be null before production-start-authorized`);
  if (phaseOrder === authorizedOrder && (value.guardianStartNonce === null || value.guardianStartReceipt !== null)) fail(`${label} guardian start intent must be recorded without a receipt at production-start-authorized`);
  if (phaseOrder === authorizedOrder && value.guardianStartEndpointIdentity === null) fail(`${label} authorized endpoint identity must be recorded at production-start-authorized`);
  if (phaseOrder >= startedOrder && (value.guardianStartNonce === null || value.guardianStartReceipt === null)) fail(`${label} guardian start must carry a durable 204 receipt at production-started`);
  if (phaseOrder >= startedOrder && value.guardianStartEndpointIdentity === null) fail(`${label} guardian start must carry the durable authorized endpoint identity at production-started`);
}

// Shared advance-time guard for the guardian start fields (preserves the exact
// qualification error wording, parameterized by label).
function validateGuardianStartAdvanceFields(fields, label) {
  if (Object.hasOwn(fields, "guardianStartedAt") && fields.guardianStartedAt !== null && (!STARTED_RE.test(fields.guardianStartedAt ?? "") || !Number.isFinite(Date.parse(fields.guardianStartedAt)))) fail(`${label} advance guardian start is invalid`);
  if (Object.hasOwn(fields, "guardianStartNonce") && fields.guardianStartNonce !== null && !SHA_RE.test(fields.guardianStartNonce ?? "")) fail(`${label} advance guardian start nonce is invalid`);
  if (Object.hasOwn(fields, "guardianStartEndpointIdentity") && fields.guardianStartEndpointIdentity !== null && (!SHA_RE.test(fields.guardianStartEndpointIdentity ?? "") || fields.guardianStartEndpointIdentity === ZERO_SHA256)) fail(`${label} advance guardian start endpoint identity is invalid`);
}

// ---------------------------------------------------------------------------
// Qualification (maintenance) recovery journal and receipt validation. This is
// the exact serialized shape and accepted semantics that must remain compatible
// with every existing qualification caller and test; no migration is allowed.
// ---------------------------------------------------------------------------
export function validateRecoveryJournal(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "phase", "maintenanceOperationSha256", "qualificationOperationSha256", "custodyIdentitySha256", "configurationSha256", "ownerUid", "production", "qualification", "guardianStartedAt", "guardianStartNonce", "guardianStartEndpointIdentity", "guardianStartReceipt"], "recovery journal");
  if (value.schemaVersion !== 1 || value.kind !== JOURNAL_KIND || value.operation !== OPERATION) fail("recovery journal kind or schema is invalid");
  if (!Object.prototype.hasOwnProperty.call(RECOVERY_PHASES, value.phase)) fail("recovery journal phase is invalid");
  if (!SHA_RE.test(value.maintenanceOperationSha256 ?? "") || value.maintenanceOperationSha256 === ZERO_SHA256) fail("recovery journal maintenance operation hash is invalid");
  if (!SHA_RE.test(value.qualificationOperationSha256 ?? "") || value.qualificationOperationSha256 === ZERO_SHA256) fail("recovery journal qualification operation hash is invalid");
  if (!SHA_RE.test(value.custodyIdentitySha256 ?? "")) fail("recovery journal custody identity hash is invalid");
  if (!SHA_RE.test(value.configurationSha256 ?? "") || value.configurationSha256 === ZERO_SHA256) fail("recovery journal configuration hash is invalid");
  if (!Number.isSafeInteger(value.ownerUid) || value.ownerUid < 1 || value.ownerUid !== expectedOwnerUid) fail("recovery journal owner is invalid or mismatched");
  validateProductionIdentity(value.production, "recovery journal");
  exactKeys(value.qualification, ["backendContainerName", "backendNetworkName", "qualificationContainerName"], "recovery journal qualification");
  if (!NAME_RE.test(value.qualification.backendContainerName ?? "") || !NAME_RE.test(value.qualification.backendNetworkName ?? "") || !NAME_RE.test(value.qualification.qualificationContainerName ?? "")) fail("recovery journal qualification identity is invalid");
  validateGuardianStartFields(value, "recovery journal", RECOVERY_PHASES);
  return structuredClone(value);
}

export function validateRecoveryReceipt(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "status", "settled", "maintenanceOperationSha256", "qualificationOperationSha256", "custodyIdentitySha256", "configurationSha256", "ownerUid"], "recovery receipt");
  if (value.schemaVersion !== 1 || value.kind !== RECEIPT_KIND || value.operation !== OPERATION || value.settled !== true) fail("recovery receipt kind or schema is invalid");
  if (!SETTLED_MAINTENANCE_STATUSES.includes(value.status ?? "")) fail("recovery receipt status is invalid");
  if (!SHA_RE.test(value.maintenanceOperationSha256 ?? "") || !SHA_RE.test(value.qualificationOperationSha256 ?? "") || !SHA_RE.test(value.custodyIdentitySha256 ?? "") || !SHA_RE.test(value.configurationSha256 ?? "")) fail("recovery receipt identity is invalid");
  if (!Number.isSafeInteger(value.ownerUid) || value.ownerUid < 1 || value.ownerUid !== expectedOwnerUid) fail("recovery receipt owner is invalid or mismatched");
  return structuredClone(value);
}

function qualificationJournalIdentity(journal) {
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

export function assertRecoveryJournalIdentity(journal, expectedIdentity) {
  if (!expectedIdentity || typeof expectedIdentity !== "object" || canonical(qualificationJournalIdentity(journal)) !== canonical(expectedIdentity)) fail("recovery journal identity does not match the current operation");
}

// ---------------------------------------------------------------------------
// Campaign recovery journal and receipt validation. The campaign journal
// separates immutable operation identity (grounded in the actual validated
// campaign inputs) from mutable consequence state (the campaign child live
// identity, guardian production-start state, and readiness evidence), which
// must never be part of the identity asserted before it is authorized.
// ---------------------------------------------------------------------------
export function validateCampaignRecoveryJournal(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "phase", "maintenanceOperationSha256", "campaignOperationSha256", "custodyIdentitySha256", "configurationSha256", "ownerUid", "production", "comparison", "campaignChildNonce", "campaignChild", "guardianStartedAt", "guardianStartNonce", "guardianStartEndpointIdentity", "guardianStartReceipt", "readinessEvidence", "campaignOutcome"], "campaign recovery journal");
  if (value.schemaVersion !== 1 || value.kind !== CAMPAIGN_JOURNAL_KIND || value.operation !== CAMPAIGN_OPERATION) fail("campaign recovery journal kind or schema is invalid");
  if (!Object.prototype.hasOwnProperty.call(CAMPAIGN_RECOVERY_PHASES, value.phase)) fail("campaign recovery journal phase is invalid");
  if (!SHA_RE.test(value.maintenanceOperationSha256 ?? "") || value.maintenanceOperationSha256 === ZERO_SHA256) fail("campaign recovery journal maintenance operation hash is invalid");
  if (!SHA_RE.test(value.campaignOperationSha256 ?? "") || value.campaignOperationSha256 === ZERO_SHA256) fail("campaign recovery journal campaign operation hash is invalid");
  if (!SHA_RE.test(value.custodyIdentitySha256 ?? "") || value.custodyIdentitySha256 === ZERO_SHA256) fail("campaign recovery journal custody identity hash is invalid");
  if (!SHA_RE.test(value.configurationSha256 ?? "") || value.configurationSha256 === ZERO_SHA256) fail("campaign recovery journal configuration hash is invalid");
  if (!Number.isSafeInteger(value.ownerUid) || value.ownerUid < 1 || value.ownerUid !== expectedOwnerUid) fail("campaign recovery journal owner is invalid or mismatched");
  validateProductionIdentity(value.production, "campaign recovery journal");
  exactKeys(value.comparison, ["materializationSha256", "pairConfigurationSha256", "preflightSha256", "acceleratorStateSha256"], "campaign recovery journal comparison");
  if (!SHA_RE.test(value.comparison.materializationSha256 ?? "") || value.comparison.materializationSha256 === ZERO_SHA256) fail("campaign recovery journal comparison materialization identity is invalid");
  if (!SHA_RE.test(value.comparison.pairConfigurationSha256 ?? "") || value.comparison.pairConfigurationSha256 === ZERO_SHA256) fail("campaign recovery journal comparison pair configuration identity is invalid");
  if (!SHA_RE.test(value.comparison.preflightSha256 ?? "") || value.comparison.preflightSha256 === ZERO_SHA256) fail("campaign recovery journal comparison preflight identity is invalid");
  if (!SHA_RE.test(value.comparison.acceleratorStateSha256 ?? "") || value.comparison.acceleratorStateSha256 === ZERO_SHA256) fail("campaign recovery journal comparison accelerator state identity is invalid");
  if (value.campaignChildNonce !== null && (!SHA_RE.test(value.campaignChildNonce ?? "") || value.campaignChildNonce === ZERO_SHA256)) fail("campaign recovery journal child launch nonce is invalid");
  if (value.campaignChild !== null) {
    validateProcessIdentity(value.campaignChild);
    // A structurally valid foreign-UID process must never satisfy the custody
    // journal: the campaign child must be owned by the exact expected owner.
    if (value.campaignChild.uid !== String(expectedOwnerUid)) fail("campaign recovery journal child owner is invalid or mismatched");
  }
  validateGuardianStartFields(value, "campaign recovery journal", CAMPAIGN_RECOVERY_PHASES);
  if (value.readinessEvidence !== null) {
    exactKeys(value.readinessEvidence, ["productionStartedAt", "provenAt", "status", "latencyMilliseconds", "responseSha256"], "campaign recovery journal readiness evidence");
    if (value.readinessEvidence.productionStartedAt !== value.guardianStartedAt || !STARTED_RE.test(value.readinessEvidence.productionStartedAt ?? "") || !Number.isFinite(Date.parse(value.readinessEvidence.productionStartedAt))) fail("campaign recovery journal readiness evidence is not bound to the exact post-start production start");
    if (!STARTED_RE.test(value.readinessEvidence.provenAt ?? "") || !Number.isFinite(Date.parse(value.readinessEvidence.provenAt))) fail("campaign recovery journal readiness evidence proof time is invalid");
    if (Date.parse(value.readinessEvidence.provenAt) < Date.parse(value.readinessEvidence.productionStartedAt)) fail("campaign recovery journal readiness evidence proof time precedes production start");
    if (value.readinessEvidence.status !== 200) fail("campaign recovery journal readiness evidence must record a successful probe");
    integer(value.readinessEvidence.latencyMilliseconds, 0, 2147483647, "campaign recovery journal readiness evidence latency");
    if (!SHA_RE.test(value.readinessEvidence.responseSha256 ?? "") || value.readinessEvidence.responseSha256 === ZERO_SHA256) fail("campaign recovery journal readiness response identity is invalid");
  }
  validateCampaignOutcome(value.campaignOutcome);
  const phaseOrder = CAMPAIGN_RECOVERY_PHASES[value.phase];
  const childAuthOrder = CAMPAIGN_RECOVERY_PHASES["campaign-child-authorized"];
  const childActiveOrder = CAMPAIGN_RECOVERY_PHASES["campaign-child-active"];
  const isolationCleanOrder = CAMPAIGN_RECOVERY_PHASES["isolation-clean"];
  const readinessOrder = CAMPAIGN_RECOVERY_PHASES["readiness-proven"];
  if (phaseOrder < childAuthOrder && value.campaignChildNonce !== null) fail("campaign recovery journal child launch nonce must be null before campaign-child-authorized");
  if (phaseOrder < childAuthOrder && value.campaignChild !== null) fail("campaign recovery journal child identity must be null before campaign-child-authorized");
  if (phaseOrder === childAuthOrder && value.campaignChildNonce === null) fail("campaign recovery journal child launch nonce must be recorded at campaign-child-authorized");
  if (phaseOrder === childAuthOrder && value.campaignChild !== null) fail("campaign recovery journal child authorization must not claim a live pid");
  if (phaseOrder >= childActiveOrder && phaseOrder < isolationCleanOrder && (value.campaignChildNonce === null || value.campaignChild === null)) fail("campaign recovery journal child must carry the exact durable live identity at campaign-child-active");
  // From isolation-clean onward the campaign child is optional: a journal may
  // legitimately reach isolation-clean without any recorded child when no child
  // was ever authorized (production-stopped) or when the exact nonce-derived
  // unit was authoritatively never launched (campaign-child-authorized). M6
  // never launches or fabricates a child identity.
  if (phaseOrder >= isolationCleanOrder && value.campaignChild !== null && value.campaignChildNonce === null) fail("campaign recovery journal child identity requires a recorded launch nonce");
  if (phaseOrder < readinessOrder && value.readinessEvidence !== null) fail("campaign recovery journal readiness evidence must be null before readiness-proven");
  if (phaseOrder >= readinessOrder && value.readinessEvidence === null) fail("campaign recovery journal readiness evidence must be recorded at readiness-proven");
  const outcomeBoundOrder = CAMPAIGN_RECOVERY_PHASES["campaign-outcome-bound"];
  // Outcome phase exactness: a campaign outcome may be durably bound only at the
  // exact campaign-outcome-bound boundary (after the campaign child is proven
  // terminal at comparison-cleanup-pending). Earlier phases forbid one, and the
  // bound phase itself requires a non-null valid outcome so a caller can never
  // reach the named bound phase with null. Later phases must preserve exactly
  // that outcome (enforced by the advance-coherence check in advanceJournal), so
  // a caller can never replace it on a later advance. Recovery paths that never
  // acquired a usable child outcome skip campaign-outcome-bound entirely and may
  // therefore legitimately carry a null outcome through readiness-proven; they
  // hold safely/manual and never fabricate pass.
  if (phaseOrder < outcomeBoundOrder && value.campaignOutcome !== null) fail("campaign recovery journal campaign outcome must be null before campaign-outcome-bound");
  if (phaseOrder === outcomeBoundOrder && value.campaignOutcome === null) fail("campaign recovery journal campaign outcome is required at campaign-outcome-bound");
  return structuredClone(value);
}

export function validateCampaignRecoveryReceipt(value, expectedOwnerUid) {
  exactKeys(value, ["schemaVersion", "kind", "operation", "status", "settled", "maintenanceOperationSha256", "campaignOperationSha256", "custodyIdentitySha256", "configurationSha256", "ownerUid", "campaignId", "campaignOutcomeSha256"], "campaign recovery receipt");
  if (value.schemaVersion !== 1 || value.kind !== CAMPAIGN_RECEIPT_KIND || value.operation !== CAMPAIGN_OPERATION || value.settled !== true) fail("campaign recovery receipt kind or schema is invalid");
  if (!SETTLED_CAMPAIGN_STATUSES.includes(value.status ?? "")) fail("campaign recovery receipt status is invalid");
  if (!SHA_RE.test(value.maintenanceOperationSha256 ?? "") || value.maintenanceOperationSha256 === ZERO_SHA256 || !SHA_RE.test(value.campaignOperationSha256 ?? "") || value.campaignOperationSha256 === ZERO_SHA256 || !SHA_RE.test(value.custodyIdentitySha256 ?? "") || value.custodyIdentitySha256 === ZERO_SHA256 || !SHA_RE.test(value.configurationSha256 ?? "") || value.configurationSha256 === ZERO_SHA256 || !SHA_RE.test(value.campaignOutcomeSha256 ?? "") || value.campaignOutcomeSha256 === ZERO_SHA256) fail("campaign recovery receipt identity is invalid");
  // The receipt carries the bound outcome's campaignId: nullable for a
  // classified error outcome (which never fabricates a campaign identity),
  // otherwise the exact validated genuine campaign identity.
  if (value.campaignId !== null && !CAMPAIGN_ID_RE.test(value.campaignId ?? "")) fail("campaign recovery receipt campaign identity is invalid");
  if (!Number.isSafeInteger(value.ownerUid) || value.ownerUid < 1 || value.ownerUid !== expectedOwnerUid) fail("campaign recovery receipt owner is invalid or mismatched");
  return structuredClone(value);
}

function campaignJournalIdentity(journal) {
  return {
    maintenanceOperationSha256: journal.maintenanceOperationSha256,
    campaignOperationSha256: journal.campaignOperationSha256,
    custodyIdentitySha256: journal.custodyIdentitySha256,
    configurationSha256: journal.configurationSha256,
    ownerUid: journal.ownerUid,
    // Return independent deterministic deep clones of the nested identity
    // objects so mutating the returned projection can never mutate the source
    // journal or a later identity/hash calculation.
    production: structuredClone(journal.production),
    comparison: structuredClone(journal.comparison),
  };
}

// Deterministic accessors for the immutable campaign recovery-journal
// identity. This is the exact same accepted identity projection asserted by the
// campaign journal engine (see assertIdentity/campaignJournalIdentity), and it
// excludes every mutable consequence field (phase, campaign child, guardian
// start, readiness). Callers must never invent a second identity definition.
export function deriveCampaignJournalIdentity(journal) {
  return campaignJournalIdentity(journal);
}

export function deriveCampaignJournalIdentitySha256(journal) {
  return createHash("sha256").update(canonical(campaignJournalIdentity(journal)), "utf8").digest("hex");
}

// ---------------------------------------------------------------------------
// Descriptors: each operation window shares the same hardened storage
// primitives but carries its own phases, serialized shapes, identity, and
// phase-specific coherence rules.
// ---------------------------------------------------------------------------
function advanceFieldKeys(descriptor) { return descriptor.advanceFields; }

const QUALIFICATION_DESCRIPTOR = Object.freeze({
  operation: OPERATION,
  journalKind: JOURNAL_KIND,
  receiptKind: RECEIPT_KIND,
  journalSuffix: "maintenance-recovery",
  journalLabel: "recovery journal",
  activeLabel: "active recovery journal",
  existingLabel: "an active maintenance recovery journal",
  receiptLabel: "recovery receipt",
  settlementConflictLabel: "recovery settlement receipt already exists with a different status or identity",
  phases: RECOVERY_PHASES,
  settledStatuses: SETTLED_MAINTENANCE_STATUSES,
  advanceFields: ["guardianStartedAt", "guardianStartNonce", "guardianStartEndpointIdentity", "guardianStartReceipt"],
  validateJournal: validateRecoveryJournal,
  validateReceipt: validateRecoveryReceipt,
  identityOf: qualificationJournalIdentity,
  assertAdvanceCoherence: () => {},
  validateAdvanceFields: (fields) => validateGuardianStartAdvanceFields(fields, "recovery journal"),
  buildReceipt: (current, status) => ({
    schemaVersion: 1,
    kind: RECEIPT_KIND,
    operation: OPERATION,
    status,
    settled: true,
    maintenanceOperationSha256: current.maintenanceOperationSha256,
    qualificationOperationSha256: current.qualificationOperationSha256,
    custodyIdentitySha256: current.custodyIdentitySha256,
    configurationSha256: current.configurationSha256,
    ownerUid: current.ownerUid,
  }),
});

const CAMPAIGN_DESCRIPTOR = Object.freeze({
  operation: CAMPAIGN_OPERATION,
  journalKind: CAMPAIGN_JOURNAL_KIND,
  receiptKind: CAMPAIGN_RECEIPT_KIND,
  journalSuffix: "campaign-maintenance-recovery",
  journalLabel: "campaign recovery journal",
  activeLabel: "active campaign recovery journal",
  existingLabel: "an active campaign maintenance recovery journal",
  receiptLabel: "campaign recovery receipt",
  settlementConflictLabel: "campaign recovery settlement receipt already exists with a different status or identity",
  phases: CAMPAIGN_RECOVERY_PHASES,
  settledStatuses: SETTLED_CAMPAIGN_STATUSES,
  advanceFields: ["campaignChildNonce", "campaignChild", "guardianStartedAt", "guardianStartNonce", "guardianStartEndpointIdentity", "guardianStartReceipt", "readinessEvidence", "campaignOutcome"],
  validateJournal: validateCampaignRecoveryJournal,
  validateReceipt: validateCampaignRecoveryReceipt,
  identityOf: campaignJournalIdentity,
  assertAdvanceCoherence: (current, next) => {
    // Once a campaign outcome is durably bound, every later advance must
    // preserve exactly that outcome; a caller can never replace or null it.
    if (current.campaignOutcome !== null && canonical(next.campaignOutcome) !== canonical(current.campaignOutcome)) fail("campaign recovery journal campaign outcome cannot be replaced on a later advance");
  },
  validateAdvanceFields: (fields) => {
    if (Object.hasOwn(fields, "campaignChildNonce") && fields.campaignChildNonce !== null && (!SHA_RE.test(fields.campaignChildNonce ?? "") || fields.campaignChildNonce === ZERO_SHA256)) fail("campaign recovery journal advance child launch nonce is invalid");
    if (Object.hasOwn(fields, "campaignChild") && fields.campaignChild !== null && (!fields.campaignChild || typeof fields.campaignChild !== "object" || Array.isArray(fields.campaignChild))) fail("campaign recovery journal advance child identity is invalid");
    if (Object.hasOwn(fields, "readinessEvidence") && fields.readinessEvidence !== null && (!fields.readinessEvidence || typeof fields.readinessEvidence !== "object" || Array.isArray(fields.readinessEvidence))) fail("campaign recovery journal advance readiness evidence is invalid");
    if (Object.hasOwn(fields, "campaignOutcome")) validateCampaignOutcome(fields.campaignOutcome);
    validateGuardianStartAdvanceFields(fields, "campaign recovery journal");
  },
  buildReceipt: (current, status) => {
    const outcome = validateCampaignOutcome(current.campaignOutcome);
    return {
      schemaVersion: 1,
      kind: CAMPAIGN_RECEIPT_KIND,
      operation: CAMPAIGN_OPERATION,
      status,
      settled: true,
      maintenanceOperationSha256: current.maintenanceOperationSha256,
      campaignOperationSha256: current.campaignOperationSha256,
      custodyIdentitySha256: current.custodyIdentitySha256,
      configurationSha256: current.configurationSha256,
      ownerUid: current.ownerUid,
      campaignId: outcome.campaignId,
      campaignOutcomeSha256: outcome.outcomeSha256,
    };
  },
});

async function fsyncDirectory(directory) {
  if (process.platform === "win32") return;
  let handle;
  try { handle = await open(directory, constants.O_RDONLY | (constants.O_DIRECTORY ?? 0)); await handle.sync(); }
  finally { if (handle) await handle.close(); }
}

function durableFlags() {
  return constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_CLOEXEC ?? 0);
}

function tempPathFor(targetPath) {
  return join(dirname(targetPath), `.${basename(targetPath)}.tmp-${process.pid}-${Date.now()}-${Math.floor(Math.random() * 0xffffffff).toString(16)}`);
}

async function writeTempFile(tempPath, value) {
  let handle;
  try {
    handle = await open(tempPath, durableFlags(), 0o600);
    await handle.writeFile(Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8"));
    await handle.sync();
  } finally {
    if (handle) await handle.close();
  }
}

async function writeDurableReplace(targetPath, value) {
  const directory = dirname(targetPath);
  const tempPath = tempPathFor(targetPath);
  await writeTempFile(tempPath, value);
  await rename(tempPath, targetPath);
  await fsyncDirectory(directory);
}

async function writeDurableCreate(targetPath, value) {
  const directory = dirname(targetPath);
  const tempPath = tempPathFor(targetPath);
  await writeTempFile(tempPath, value);
  if (process.platform === "win32") {
    try { await rename(tempPath, targetPath); }
    catch (error) { await unlink(tempPath).catch(() => {}); throw error; }
  } else {
    try {
      await link(tempPath, targetPath);
      await unlink(tempPath);
    } catch (error) {
      await unlink(tempPath).catch(() => {});
      throw error;
    }
  }
  await fsyncDirectory(directory);
}

async function readOwnerPrivateJson(path, maxBytes, label, expectedOwnerUid, validator) {
  let actual, record;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, maxBytes, label), realpath(path)]); }
  catch { fail(`${label} could not be opened safely`); }
  const info = record.details;
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || !samePath(actual, path) || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private, singular, and real`);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  let value;
  try { value = parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
  return validator(value, expectedOwnerUid);
}

async function readJournalFile(descriptor, path, expectedOwnerUid) {
  return readOwnerPrivateJson(path, MAX_JOURNAL_BYTES, descriptor.journalLabel, expectedOwnerUid, descriptor.validateJournal);
}

async function readReceiptFile(descriptor, path, expectedOwnerUid) {
  return readOwnerPrivateJson(path, MAX_JOURNAL_BYTES, descriptor.receiptLabel, expectedOwnerUid, descriptor.validateReceipt);
}

function journalPathFor(descriptor, custodyLockPath) {
  validateCustodyLockPath(custodyLockPath);
  return join(dirname(custodyLockPath), `.${basename(custodyLockPath)}.${descriptor.journalSuffix}.json`);
}

function receiptPathFor(descriptor, custodyLockPath, status, maintenanceOperationSha256) {
  const active = journalPathFor(descriptor, custodyLockPath);
  if (!STATUS_RE.test(status ?? "") || !SHA_RE.test(maintenanceOperationSha256 ?? "")) fail(`${descriptor.receiptLabel} status or operation hash is invalid`);
  return join(dirname(active), `${basename(active)}.receipt-${status}-${maintenanceOperationSha256}.json`);
}

function assertIdentity(descriptor, journal, expectedIdentity) {
  if (!expectedIdentity || typeof expectedIdentity !== "object" || canonical(descriptor.identityOf(journal)) !== canonical(expectedIdentity)) fail(`${descriptor.journalLabel} identity does not match the current operation`);
}

async function inspectActiveJournal(descriptor, custodyLockPath, expectedOwnerUid) {
  const path = journalPathFor(descriptor, custodyLockPath);
  // Distinguish a genuinely absent journal (ENOENT -> null) from a
  // permission/I/O/substitution error, which must fail closed instead of being
  // silently treated as absence.
  const info = await lstatExact(path);
  if (!info) return null;
  if (!info.isFile() || info.isSymbolicLink()) fail(`${descriptor.activeLabel} is not a singular real file`);
  const journal = await readJournalFile(descriptor, path, expectedOwnerUid);
  return Object.freeze({ path, journal });
}

async function hasActiveJournal(descriptor, custodyLockPath, expectedOwnerUid) {
  const path = journalPathFor(descriptor, custodyLockPath);
  const info = await lstatExact(path);
  if (!info) return false;
  if (!info.isFile() || info.isSymbolicLink()) fail(`${descriptor.activeLabel} is not a singular real file`);
  return true;
}

async function assertNoActiveJournal(descriptor, custodyLockPath, expectedOwnerUid) {
  const inspected = await inspectActiveJournal(descriptor, custodyLockPath, expectedOwnerUid);
  if (inspected) fail(`${descriptor.existingLabel} already exists; recovery is required before any new review or run`);
}

async function createJournal(descriptor, custodyLockPath, expectedOwnerUid, record) {
  const validated = descriptor.validateJournal({ ...record, phase: "prepared" }, expectedOwnerUid);
  if (validated.phase !== "prepared") fail(`${descriptor.journalLabel} must be created in the prepared phase`);
  const path = journalPathFor(descriptor, custodyLockPath);
  const info = await lstatExact(path);
  if (info) fail(`${descriptor.existingLabel} already exists; recovery is required`);
  await writeDurableCreate(path, validated);
  return Object.freeze({ path, journal: validated });
}

async function advanceJournal(descriptor, custodyLockPath, expectedOwnerUid, phase, expectedIdentity, fields = {}) {
  if (!Object.prototype.hasOwnProperty.call(descriptor.phases, phase)) fail(`${descriptor.journalLabel} target phase is invalid`);
  if (!fields || typeof fields !== "object" || Array.isArray(fields) || Object.keys(fields).some((key) => !advanceFieldKeys(descriptor).includes(key))) fail(`${descriptor.journalLabel} advance fields are invalid`);
  descriptor.validateAdvanceFields(fields);
  const path = journalPathFor(descriptor, custodyLockPath);
  let info;
  try { info = await lstat(path); } catch { fail(`no ${descriptor.activeLabel} exists to advance`); }
  if (!info.isFile() || info.isSymbolicLink()) fail(`${descriptor.activeLabel} is not a singular real file`);
  const current = await readJournalFile(descriptor, path, expectedOwnerUid);
  assertIdentity(descriptor, current, expectedIdentity);
  const currentOrder = descriptor.phases[current.phase];
  const targetOrder = descriptor.phases[phase];
  if (targetOrder < currentOrder) fail(`${descriptor.journalLabel} phase regression is not permitted`);
  const next = descriptor.validateJournal({ ...current, phase, ...fields }, expectedOwnerUid);
  descriptor.assertAdvanceCoherence(current, next);
  if (targetOrder === currentOrder) {
    if (canonical(next) !== canonical(current)) fail(`${descriptor.journalLabel} repeated same-phase update is not idempotent`);
    return Object.freeze({ path, journal: current, advanced: false });
  }
  await writeDurableReplace(path, next);
  return Object.freeze({ path, journal: next, advanced: true });
}

async function settleJournal(descriptor, custodyLockPath, expectedOwnerUid, { status, expectedIdentity }) {
  if (!descriptor.settledStatuses.includes(status)) fail(`${descriptor.journalLabel} settlement requires a definitively settled maintenance status`);
  const path = journalPathFor(descriptor, custodyLockPath);
  const current = await readJournalFile(descriptor, path, expectedOwnerUid);
  assertIdentity(descriptor, current, expectedIdentity);
  const receipt = descriptor.buildReceipt(current, status);
  const receiptPath = receiptPathFor(descriptor, custodyLockPath, status, current.maintenanceOperationSha256);
  let receiptInfo = null;
  try { receiptInfo = await lstat(receiptPath); } catch {}
  let replayed = false;
  if (receiptInfo) {
    const existing = await readReceiptFile(descriptor, receiptPath, expectedOwnerUid);
    if (canonical(existing) !== canonical(receipt)) fail(descriptor.settlementConflictLabel);
    replayed = true;
  } else {
    await writeDurableCreate(receiptPath, receipt);
  }
  await unlink(path).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  await fsyncDirectory(dirname(path));
  return Object.freeze({ receiptPath, journal: current, replayed });
}

// M8 exact-once campaign settlement. Unlike the generic maintenance settle
// primitive, campaign settlement is authority-gated on the exact readiness-
// proven phase and a durably bound campaignOutcome, and the terminal status is
// ALWAYS derived mechanically from that outcome (never chosen or mismatched by
// a caller). It keeps the receipt-first-then-unlink ordering: the terminal
// receipt (which binds the outcome hash plus the campaign/config/runtime
// identities) is durably created before the active journal is removed, with
// idempotent identical replay and conflict rejection at every boundary.
async function settleCampaignJournal(custodyLockPath, expectedOwnerUid, { expectedIdentity }) {
  const descriptor = CAMPAIGN_DESCRIPTOR;
  const path = journalPathFor(descriptor, custodyLockPath);
  const current = await readJournalFile(descriptor, path, expectedOwnerUid);
  assertIdentity(descriptor, current, expectedIdentity);
  // Phase authority: campaign settlement is forbidden before readiness-proven.
  if (current.phase !== "readiness-proven") fail("campaign recovery journal settlement requires the exact readiness-proven phase");
  // Outcome authority: campaign settlement is forbidden without a durably bound
  // outcome. A caller can never fabricate pass from readiness/phase alone.
  if (current.campaignOutcome === null) fail("campaign recovery journal settlement requires a durably bound campaign outcome");
  const status = deriveTerminalCampaignStatusFromOutcome(current.campaignOutcome);
  const receipt = descriptor.buildReceipt(current, status);
  const receiptPath = receiptPathFor(descriptor, custodyLockPath, status, current.maintenanceOperationSha256);
  let receiptInfo = null;
  try { receiptInfo = await lstat(receiptPath); } catch {}
  let replayed = false;
  if (receiptInfo) {
    const existing = await readReceiptFile(descriptor, receiptPath, expectedOwnerUid);
    if (canonical(existing) !== canonical(receipt)) fail(descriptor.settlementConflictLabel);
    replayed = true;
  } else {
    await writeDurableCreate(receiptPath, receipt);
  }
  await unlink(path).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  await fsyncDirectory(dirname(path));
  return Object.freeze({ receiptPath, journal: current, status, replayed });
}

// M8 secure settlement-receipt verification for the controller's post-custody
// settlement wait. This is the ONLY authoritative success check the controller
// may use after the guardian settles: it never trusts a bare lstat file-exists.
// It securely reads the exact terminal receipt through the shared owner-private
// bounded/no-symlink/strict-UTF-8/strict-JSON machinery and strictly validates
// its shape, owner, status, operation/config/custody/campaign identities and
// campaignOutcomeSha256, requiring it to equal the exact expected terminal
// outcome/status. Missing (returns null so the caller may keep polling),
// malformed, wrong-mode/owner, symlinked, oversized, or mismatched/forged
// receipts fail closed rather than ever producing a terminal result.
export async function readCampaignSettlementReceipt(custodyLockPath, expectedOwnerUid, expected) {
  const descriptor = CAMPAIGN_DESCRIPTOR;
  if (!expected || typeof expected !== "object" || Array.isArray(expected)) fail("campaign settlement receipt expected binding is invalid");
  const status = expected.status;
  const maintenanceOperationSha256 = expected.maintenanceOperationSha256;
  const receiptPath = receiptPathFor(descriptor, custodyLockPath, status, maintenanceOperationSha256);
  const info = await lstatExact(receiptPath);
  if (!info) return null;
  if (!info.isFile() || info.isSymbolicLink()) fail(`${descriptor.receiptLabel} is not a singular real file`);
  const receipt = await readReceiptFile(descriptor, receiptPath, expectedOwnerUid);
  const expectedOutcome = validateCampaignOutcome(expected.campaignOutcome);
  if (receipt.status !== status) fail(`${descriptor.receiptLabel} status does not equal the exact expected terminal status`);
  if (receipt.maintenanceOperationSha256 !== maintenanceOperationSha256) fail(`${descriptor.receiptLabel} maintenance operation identity does not equal the exact expected operation`);
  if (receipt.campaignOperationSha256 !== expected.campaignOperationSha256) fail(`${descriptor.receiptLabel} campaign operation identity does not equal the exact expected operation`);
  if (receipt.custodyIdentitySha256 !== expected.custodyIdentitySha256) fail(`${descriptor.receiptLabel} custody identity does not equal the exact expected custody`);
  if (receipt.configurationSha256 !== expected.configurationSha256) fail(`${descriptor.receiptLabel} configuration identity does not equal the exact expected configuration`);
  if (receipt.campaignId !== expectedOutcome.campaignId) fail(`${descriptor.receiptLabel} campaign identity does not equal the exact expected outcome`);
  if (receipt.campaignOutcomeSha256 !== expectedOutcome.outcomeSha256) fail(`${descriptor.receiptLabel} campaign outcome identity does not equal the exact expected outcome`);
  return receipt;
}

// Identity-checked durable cancellation of an inert (prepared-phase) recovery
// journal. This is the only path that may remove a journal before settlement: it
// refuses to touch any substituted or non-prepared journal, and it fsyncs the
// directory so the cancellation is durable. It is only safe to call when no
// destructive action has committed; callers must never infer "no destructive
// action" solely from a later running state.
async function cancelJournal(descriptor, custodyLockPath, expectedOwnerUid, expectedIdentity) {
  const path = journalPathFor(descriptor, custodyLockPath);
  const current = await readJournalFile(descriptor, path, expectedOwnerUid);
  if (current.phase !== "prepared") fail(`refusing to cancel a ${descriptor.journalLabel} that is no longer inert`);
  assertIdentity(descriptor, current, expectedIdentity);
  await unlink(path).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  await fsyncDirectory(dirname(path));
  return Object.freeze({ path, journal: current, cancelled: true });
}

// ---------------------------------------------------------------------------
// Qualification public API (unchanged call signatures and serialized shapes).
// ---------------------------------------------------------------------------
export function deriveRecoveryJournalPath(custodyLockPath) { return journalPathFor(QUALIFICATION_DESCRIPTOR, custodyLockPath); }
export function deriveRecoveryReceiptPath(custodyLockPath, status, maintenanceOperationSha256) { return receiptPathFor(QUALIFICATION_DESCRIPTOR, custodyLockPath, status, maintenanceOperationSha256); }
export async function inspectActiveRecoveryJournal(custodyLockPath, expectedOwnerUid) { return inspectActiveJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function hasActiveRecoveryJournal(custodyLockPath, expectedOwnerUid) { return hasActiveJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function assertNoActiveRecoveryJournal(custodyLockPath, expectedOwnerUid) { return assertNoActiveJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function createRecoveryJournal(custodyLockPath, expectedOwnerUid, record) { return createJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid, record); }
export async function advanceRecoveryJournal(custodyLockPath, expectedOwnerUid, phase, expectedIdentity, fields = {}) { return advanceJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid, phase, expectedIdentity, fields); }
export async function settleRecoveryJournal(custodyLockPath, expectedOwnerUid, options) { return settleJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid, options); }
export async function cancelRecoveryJournal(custodyLockPath, expectedOwnerUid, expectedIdentity) { return cancelJournal(QUALIFICATION_DESCRIPTOR, custodyLockPath, expectedOwnerUid, expectedIdentity); }

// ---------------------------------------------------------------------------
// Campaign public API (same shared hardened primitives, operation-specific
// wrapper). Not yet wired into model-campaign-maintenance.mjs.
// ---------------------------------------------------------------------------
export function deriveCampaignRecoveryJournalPath(custodyLockPath) { return journalPathFor(CAMPAIGN_DESCRIPTOR, custodyLockPath); }
export function deriveCampaignRecoveryReceiptPath(custodyLockPath, status, maintenanceOperationSha256) { return receiptPathFor(CAMPAIGN_DESCRIPTOR, custodyLockPath, status, maintenanceOperationSha256); }
export async function inspectActiveCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid) { return inspectActiveJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function hasActiveCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid) { return hasActiveJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function assertNoActiveCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid) { return assertNoActiveJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid); }
export async function createCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid, record) { return createJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid, record); }
export async function advanceCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid, phase, expectedIdentity, fields = {}) { return advanceJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid, phase, expectedIdentity, fields); }
export async function settleCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid, options) { return settleCampaignJournal(custodyLockPath, expectedOwnerUid, options); }
export async function cancelCampaignRecoveryJournal(custodyLockPath, expectedOwnerUid, expectedIdentity) { return cancelJournal(CAMPAIGN_DESCRIPTOR, custodyLockPath, expectedOwnerUid, expectedIdentity); }

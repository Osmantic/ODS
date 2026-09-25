import { createHash, randomBytes } from "node:crypto";

import {
  canonical, validateWorkDataArtifactManifest, validateWorkDataReport, validateWorkDataVerification,
  validateWorkResearchReport, validateWorkResearchVerification, validateWorkScoutReport, validateWorkScoutVerification,
} from "../../scripts/lib/work-contract.mjs";
import { cleanupInterruptedProfileAttempt, runDataLabDockerLifecycle, runResearcherDockerLifecycle, runScoutDockerLifecycle } from "../work-runner/docker-supervisor.mjs";
import { claimLease, createLeaseConsumption, recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import { appendCheckpoint, checkpointSha256, recoverCheckpointLedger } from "./checkpoints.mjs";
import { resolveAttemptLifecycleOptions } from "./goal-capability-runtime.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const supportedProfiles = new Set(["scout", "researcher", "data-lab"]);
const failureStages = new Set([
  "profile-execution", "rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract",
  "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup", "authorization-expired", "interrupted",
]);
export const SEMANTIC_ACCEPTANCE_RESERVE_BYTES = 64 * 1024;

export class CandidateWaitLoopError extends Error {}

function fail(message) { throw new CandidateWaitLoopError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function boundedFailureStage(value, fallback = "profile-execution") {
  return failureStages.has(value) ? value : fallback;
}

function suffix(value) {
  const result = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(result)) fail("candidate wait loop suffix is invalid");
  return result;
}

function nonnegative(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${label} is invalid`);
  return value;
}

function makeClock(head, clock) {
  let last = Date.parse(head.createdAt);
  return () => {
    const observed = clock();
    const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
    if (!Number.isFinite(milliseconds)) fail("candidate wait loop clock is invalid");
    last = Math.max(last + 1, Math.trunc(milliseconds));
    return new Date(last);
  };
}

function update(head, state, overrides = {}) {
  const field = (name) => Object.hasOwn(overrides, name) ? overrides[name] : head[name];
  return {
    state,
    iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) },
    progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: field("workspaceSnapshotSha256"),
    artifactManifestSha256: field("artifactManifestSha256"),
    workerSessionSha256: field("workerSessionSha256"),
    verificationEvidenceSha256: field("verificationEvidenceSha256"),
    authorityExpansionObserved: overrides.authorityExpansionObserved ?? false,
    acceptanceCriteriaMutationObserved: overrides.acceptanceCriteriaMutationObserved ?? false,
    externalEffectsObserved: overrides.externalEffectsObserved ?? false,
  };
}

async function append(stateRoot, prepared, head, state, nextTime, recordSuffix, overrides = {}) {
  return appendCheckpoint({
    stateRoot, plan: prepared.plan, lease: prepared.lease,
    previousCheckpointSha256: checkpointSha256(head), update: update(head, state, overrides),
    now: nextTime(), suffix: suffix(recordSuffix),
  });
}

function exactRecoveredClaim(prepared, claim) {
  if (
    claim?.status !== "consumed" || claim.externalEffects !== false
    || claim.jobId !== prepared.plan.jobId || claim.leaseId !== prepared.lease.leaseId
    || claim.planSha256 !== prepared.bindings.planSha256 || claim.leaseSha256 !== prepared.bindings.leaseSha256
    || claim.policySha256 !== prepared.bindings.policySha256 || claim.inputSetSha256 !== prepared.bindings.inputSetSha256
    || claim.workspaceSha256 !== prepared.workspace.sha256 || canonical(claim.executor) !== canonical(prepared.plan.executor)
    || canonical(claim.model) !== canonical(prepared.plan.model) || claim.runnerImageDigest !== prepared.plan.isolation.runnerImageDigest
  ) fail("recovered candidate claim differs from the exact prepared boundary");
  return claim;
}

function artifact(value, label) {
  if (!value || !SHA_RE.test(value.sha256 ?? "") || typeof value.path !== "string" || !value.path) fail(`${label} artifact is invalid`);
  nonnegative(value.bytes, `${label} artifact bytes`);
  return value;
}

function artifactReference(value, label) {
  const checked = artifact(value, label);
  return { bytes: checked.bytes, sha256: checked.sha256 };
}

export function candidateArtifactSetSha256(profile, candidate) {
  if (!supportedProfiles.has(profile) || !candidate || typeof candidate !== "object" || Array.isArray(candidate)) fail("candidate artifact set is invalid");
  const record = {
    schemaVersion: 1, format: "pixel-semantic-candidate-artifact-set-v1", profile,
    report: artifactReference(candidate.artifacts?.report, "candidate report"),
    verification: artifactReference(candidate.artifacts?.verification, "candidate verification"),
    evidence: artifactReference(candidate.artifacts?.evidence, "candidate evidence"),
    ...(profile === "data-lab" ? {
      manifest: artifactReference(candidate.artifacts?.manifest, "candidate manifest"),
      recipe: artifactReference(candidate.artifacts?.recipe, "candidate recipe"),
      replayInventory: {
        bytes: nonnegative(candidate.inventoryRecord?.bytes, "candidate replay inventory bytes"),
        sha256: SHA_RE.test(candidate.inventoryRecord?.sha256 ?? "") ? candidate.inventoryRecord.sha256 : fail("candidate replay inventory digest is invalid"),
      },
    } : {}),
  };
  return sha(record);
}

function commonCandidate(prepared, claim, candidate) {
  if (!candidate || candidate.cleanupComplete !== true || !candidate.execution || !candidate.proxyReceipt || !candidate.artifacts) fail("profile candidate receipt is incomplete or cleanup is unproven");
  nonnegative(candidate.execution.durationMilliseconds, "profile candidate runtime");
  for (const field of ["modelRequests", "inputTokens", "outputTokens", "networkBytes"]) nonnegative(candidate.proxyReceipt[field], `profile candidate ${field}`);
  if (candidate.execution.durationMilliseconds > prepared.lease.budgets.maxRuntimeSeconds * 1000) fail("profile candidate runtime exceeds its lease");
  for (const [field, budget] of [["modelRequests", "maxModelRequests"], ["inputTokens", "maxInputTokens"], ["outputTokens", "maxOutputTokens"], ["networkBytes", "maxNetworkBytes"]]) {
    if (candidate.proxyReceipt[field] > prepared.lease.budgets[budget]) fail(`profile candidate ${field} exceeds its lease`);
  }
  const reportArtifact = artifact(candidate.artifacts.report, "profile report");
  const verificationArtifact = artifact(candidate.artifacts.verification, "profile verification");
  return { reportArtifact, verificationArtifact };
}

function checkedResearcherCandidate(prepared, claim, candidate) {
  const common = commonCandidate(prepared, claim, candidate);
  const reportErrors = validateWorkResearchReport(candidate.report);
  const verificationErrors = validateWorkResearchVerification(candidate.verification);
  if (reportErrors.length || verificationErrors.length) fail(`Researcher candidate contract is invalid: ${reportErrors[0] ?? verificationErrors[0]}`);
  if (
    candidate.report.jobId !== claim.jobId || candidate.report.claimId !== claim.claimId || candidate.report.planSha256 !== claim.planSha256
    || candidate.verification.jobId !== claim.jobId || candidate.verification.claimId !== claim.claimId || candidate.verification.planSha256 !== claim.planSha256
    || candidate.verification.reportSha256 !== sha(candidate.report) || candidate.verification.status !== "evidence-pass"
    || candidate.verification.semanticEntailmentVerified !== false || candidate.verification.independent !== true
  ) fail("Researcher candidate differs from its exact evidence-only boundary");
  const evidenceArtifact = artifact(candidate.artifacts.evidence, "Researcher evidence");
  const totalBytes = common.reportArtifact.bytes + common.verificationArtifact.bytes + evidenceArtifact.bytes;
  if (candidate.artifacts.totalBytes !== totalBytes) fail("Researcher retained artifact total differs from its inventory");
  return { artifactManifestSha256: candidateArtifactSetSha256("researcher", candidate), verificationEvidenceSha256: common.verificationArtifact.sha256, artifactBytes: totalBytes };
}

function checkedScoutCandidate(prepared, claim, candidate) {
  const common = commonCandidate(prepared, claim, candidate);
  const reportErrors = validateWorkScoutReport(candidate.report);
  const verificationErrors = validateWorkScoutVerification(candidate.verification);
  if (reportErrors.length || verificationErrors.length) fail(`Scout candidate contract is invalid: ${reportErrors[0] ?? verificationErrors[0]}`);
  if (
    candidate.report.jobId !== claim.jobId || candidate.report.claimId !== claim.claimId || candidate.report.planSha256 !== claim.planSha256 || candidate.report.workspaceSha256 !== claim.workspaceSha256
    || candidate.verification.jobId !== claim.jobId || candidate.verification.claimId !== claim.claimId || candidate.verification.planSha256 !== claim.planSha256 || candidate.verification.workspaceSha256 !== claim.workspaceSha256
    || candidate.verification.reportSha256 !== sha(candidate.report) || candidate.verification.status !== "evidence-pass"
    || candidate.verification.semanticEntailmentVerified !== false || candidate.verification.independent !== true
    || candidate.report.dataClassification !== prepared.plan.dataClassification
    || candidate.report.privateDataIncluded !== (prepared.plan.dataClassification !== "public")
    || candidate.verification.privateDataIncluded !== candidate.report.privateDataIncluded
  ) fail("Scout candidate differs from its exact evidence-only boundary");
  const expectedFindings = candidate.report.findings.map((finding) => ({
    findingId: finding.findingId, status: "pass",
    evidence: finding.evidence.map(({ inputId, path, quoteSha256, fileSha256, offset, bytes }) => ({ inputId, path, quoteSha256, fileSha256, offset, bytes, status: "present" })),
  }));
  if (canonical(candidate.verification.findings) !== canonical(expectedFindings)) fail("Scout verification evidence differs from the retained report");
  const evidenceArtifact = artifact(candidate.artifacts.evidence, "Scout evidence");
  const totalBytes = common.reportArtifact.bytes + common.verificationArtifact.bytes + evidenceArtifact.bytes;
  if (candidate.artifacts.totalBytes !== totalBytes) fail("Scout retained artifact total differs from its inventory");
  return { artifactManifestSha256: candidateArtifactSetSha256("scout", candidate), verificationEvidenceSha256: common.verificationArtifact.sha256, artifactBytes: totalBytes };
}

function checkedDataLabCandidate(prepared, claim, candidate) {
  const common = commonCandidate(prepared, claim, candidate);
  const manifestErrors = validateWorkDataArtifactManifest(candidate.manifest);
  const reportErrors = validateWorkDataReport(candidate.report);
  const verificationErrors = validateWorkDataVerification(candidate.verification);
  if (manifestErrors.length || reportErrors.length || verificationErrors.length) fail(`Data Lab candidate contract is invalid: ${manifestErrors[0] ?? reportErrors[0] ?? verificationErrors[0]}`);
  if (
    candidate.manifest.jobId !== claim.jobId || candidate.manifest.claimId !== claim.claimId || candidate.manifest.planSha256 !== claim.planSha256 || candidate.manifest.workspaceSha256 !== claim.workspaceSha256
    || candidate.report.jobId !== claim.jobId || candidate.report.claimId !== claim.claimId || candidate.report.planSha256 !== claim.planSha256 || candidate.report.workspaceSha256 !== claim.workspaceSha256
    || candidate.verification.jobId !== claim.jobId || candidate.verification.claimId !== claim.claimId || candidate.verification.planSha256 !== claim.planSha256
    || candidate.report.manifestSha256 !== candidate.manifestRecord?.sha256 || candidate.verification.manifestSha256 !== candidate.manifestRecord?.sha256
    || candidate.report.recipeSha256 !== candidate.manifest.recipe.sha256 || candidate.verification.recipeSha256 !== candidate.manifest.recipe.sha256
    || candidate.verification.status !== "exact-replay-pass" || candidate.verification.semanticAccuracyVerified !== false
  ) fail("Data Lab candidate differs from its exact replay-only boundary");
  const evidenceArtifact = artifact(candidate.artifacts.evidence, "Data Lab evidence");
  const manifestArtifact = artifact(candidate.artifacts.manifest, "Data Lab manifest");
  const recipeArtifact = artifact(candidate.artifacts.recipe, "Data Lab recipe");
  nonnegative(candidate.inventoryRecord?.bytes, "Data Lab replay inventory bytes");
  if (!SHA_RE.test(candidate.inventoryRecord?.sha256 ?? "") || manifestArtifact.sha256 !== candidate.manifestRecord.sha256 || manifestArtifact.bytes !== candidate.manifestRecord.bytes || recipeArtifact.sha256 !== candidate.manifest.recipe.sha256 || recipeArtifact.bytes !== candidate.manifest.recipe.bytes) fail("Data Lab retained inventory differs from its verified records");
  const totalBytes = candidate.manifest.totals.bytes + recipeArtifact.bytes + manifestArtifact.bytes + candidate.inventoryRecord.bytes
    + common.reportArtifact.bytes + common.verificationArtifact.bytes + evidenceArtifact.bytes;
  if (candidate.artifacts.totalBytes !== totalBytes) fail("Data Lab retained artifact total differs from its inventory");
  return { artifactManifestSha256: candidateArtifactSetSha256("data-lab", candidate), verificationEvidenceSha256: common.verificationArtifact.sha256, artifactBytes: totalBytes };
}

function checkedCandidate(prepared, claim, candidate) {
  const result = prepared.plan.profile === "scout" ? checkedScoutCandidate(prepared, claim, candidate)
    : prepared.plan.profile === "researcher" ? checkedResearcherCandidate(prepared, claim, candidate) : checkedDataLabCandidate(prepared, claim, candidate);
  if (result.artifactBytes > prepared.lease.budgets.maxArtifactBytes - SEMANTIC_ACCEPTANCE_RESERVE_BYTES) fail("profile candidate artifacts leave no semantic acceptance reserve");
  return result;
}

function candidateUsage(head, candidate, checked) {
  return {
    runtimeSeconds: head.usage.runtimeSeconds + Math.ceil(candidate.execution.durationMilliseconds / 1000),
    modelRequests: head.usage.modelRequests + candidate.proxyReceipt.modelRequests,
    inputTokens: head.usage.inputTokens + candidate.proxyReceipt.inputTokens,
    outputTokens: head.usage.outputTokens + candidate.proxyReceipt.outputTokens,
    networkBytes: head.usage.networkBytes + candidate.proxyReceipt.networkBytes,
    artifactBytes: head.usage.artifactBytes + checked.artifactBytes,
    failures: head.usage.failures,
  };
}

function failedUsage(head, lease, observed) {
  const value = {
    runtimeSeconds: observed?.runtimeSeconds ?? lease.budgets.maxRuntimeSeconds,
    modelRequests: observed?.modelRequests ?? lease.budgets.maxModelRequests,
    inputTokens: observed?.inputTokens ?? lease.budgets.maxInputTokens,
    outputTokens: observed?.outputTokens ?? lease.budgets.maxOutputTokens,
    networkBytes: observed?.networkBytes ?? lease.budgets.maxNetworkBytes,
    artifactBytes: observed?.artifactBytes ?? 0,
    failures: observed?.failures ?? 1,
  };
  for (const [field, budget] of [["runtimeSeconds", "maxRuntimeSeconds"], ["modelRequests", "maxModelRequests"], ["inputTokens", "maxInputTokens"], ["outputTokens", "maxOutputTokens"], ["networkBytes", "maxNetworkBytes"], ["artifactBytes", "maxArtifactBytes"], ["failures", "maxFailures"]]) {
    nonnegative(value[field], `failed profile ${field}`);
    if (value[field] > lease.budgets[budget]) fail(`failed profile ${field} exceeds its lease`);
  }
  return Object.fromEntries(Object.keys(head.usage).map((field) => [field, head.usage[field] + value[field]]));
}

async function appendCleanupBlock({ stateRoot, prepared, head, nextTime, recordSuffix, error }) {
  const state = error?.workRecoveryInconclusive === true ? "recovery-inconclusive" : "cleanup-failed";
  if (head.state === "cleanup-failed" && state === "cleanup-failed") return null;
  const firstBlock = head.state === "running";
  return append(stateRoot, prepared, head, state, nextTime, recordSuffix, {
    usage: firstBlock ? failedUsage(head, prepared.lease, error?.workUsage) : head.usage,
    progress: {
      ...head.progress,
      noProgressCount: firstBlock ? Math.min(prepared.plan.budgets.noProgressLimit, head.progress.noProgressCount + 1) : head.progress.noProgressCount,
      failureStage: "cleanup",
      failureFingerprintSha256: sha({
        stage: "candidate-cleanup", state, name: error?.name ?? "Error", message: error?.message ?? "failed",
      }),
    },
  });
}

async function failClosedAttempt({ stateRoot, prepared, running, nextTime, recordSuffix, error, observedUsage, failureStage }) {
  const failed = await append(stateRoot, prepared, running, "failed", nextTime, recordSuffix, {
    usage: failedUsage(running, prepared.lease, observedUsage),
    progress: {
      noProgressCount: running.progress.noProgressCount + 1,
      failureStage: boundedFailureStage(failureStage),
      failureFingerprintSha256: sha({ stage: "candidate", name: error?.name ?? "Error", message: error?.message ?? "failed" }),
    },
  });
  const failure = new CandidateWaitLoopError("profile candidate failed and the attempt was durably closed");
  failure.cause = error;
  failure.checkpoint = failed.checkpoint;
  throw failure;
}

export async function executeCandidateWaitAttempt({
  stateRoot, prepared, lifecycleOptions, clock = () => new Date(), suffixes = {}, candidateRunner,
}) {
  if (!supportedProfiles.has(prepared?.plan?.profile) || prepared.lease?.iteration !== 1 || prepared.lease?.continuation !== null || prepared.verificationOnly === true || prepared.cleanupOnly === true) fail("candidate wait loop requires one initial Scout, Researcher, or Data Lab execution boundary");
  const runner = candidateRunner ?? (prepared.plan.profile === "scout" ? runScoutDockerLifecycle : prepared.plan.profile === "researcher" ? runResearcherDockerLifecycle : runDataLabDockerLifecycle);
  if (typeof runner !== "function") fail("candidate wait loop runner is invalid");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  if (recovered.head.state !== "authorized" || recovered.head.iteration !== 0) fail("candidate wait loop head is not authorized for its initial lease");
  const nextTime = makeClock(recovered.head, clock);
  let claim = createLeaseConsumption(prepared, { now: nextTime(), suffix: suffix(suffixes.claim) });
  try { await claimLease(stateRoot, claim); } catch (error) {
    try { claim = exactRecoveredClaim(prepared, await recoverLeaseConsumption(stateRoot, prepared.lease)); } catch { throw error; }
  }
  const runningRecord = await append(stateRoot, prepared, recovered.head, "running", nextTime, suffixes.running, {
    iteration: 1, artifactManifestSha256: null, workerSessionSha256: sha(claim), verificationEvidenceSha256: null,
  });
  const running = runningRecord.checkpoint;
  let candidate;
  let failureStage = "profile-execution";
  try {
    const attemptOptions = await resolveAttemptLifecycleOptions(lifecycleOptions, {
      mode: "execute", prepared, claim, checkpoint: running,
    });
    candidate = await runner(prepared, claim, attemptOptions);
    failureStage = "candidate-contract";
    const checked = checkedCandidate(prepared, claim, candidate);
    const waiting = await append(stateRoot, prepared, running, "waiting-authority", nextTime, suffixes.waiting, {
      usage: candidateUsage(running, candidate, checked),
      artifactManifestSha256: checked.artifactManifestSha256,
      verificationEvidenceSha256: checked.verificationEvidenceSha256,
    });
    return { action: "waiting-authority", claim, candidate, checkpoint: waiting.checkpoint, semanticAcceptanceVerified: false };
  } catch (error) {
    if (candidate?.cleanupComplete !== true && error?.workCleanupComplete !== true) {
      const blocked = await appendCleanupBlock({
        stateRoot, prepared, head: running, nextTime, recordSuffix: suffixes.failed, error,
      });
      const failure = new CandidateWaitLoopError(`profile candidate failed before cleanup was proven; the attempt is ${blocked.checkpoint.state}`);
      failure.cause = error;
      failure.checkpoint = blocked.checkpoint;
      throw failure;
    }
    return failClosedAttempt({
      stateRoot, prepared, running, nextTime, recordSuffix: suffixes.failed, error, observedUsage: error?.workUsage,
      failureStage: boundedFailureStage(error?.workFailureStage, failureStage),
    });
  }
}

export async function recordExpiredAuthorizationFailure({ stateRoot, prepared, clock = () => new Date(), checkpointSuffix }) {
  // Terminally close an authorized child whose initial execution lease expired before the worker
  // launched. dispatch-worker is issued only for an authorized head, and the runner launches only
  // after the running checkpoint, so an authorized head proves the worker never started — there is
  // no external effect to clean up. Reconciling it to failed (self-healing the crash window where a
  // lease was consumed but the running checkpoint was never written) lets the goal re-dispatch,
  // instead of the child wedging forever: post-expiry the lease can no longer be consumed to
  // launch, and a consumed lease also blocks operator-cancel. This grants no authority; it only
  // closes an expired authorization.
  if (!supportedProfiles.has(prepared?.plan?.profile) || prepared.lease?.iteration !== 1 || prepared.verificationOnly === true || prepared.cleanupOnly === true) fail("expired-authorization reconciliation requires one initial execution boundary");
  const observed = clock();
  const observedMs = observed instanceof Date ? observed.getTime() : Number(observed);
  if (!Number.isFinite(observedMs) || observedMs < Date.parse(prepared.lease.expiresAt)) fail("expired-authorization reconciliation requires an elapsed lease window");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  if (recovered.head.state !== "authorized" || recovered.head.iteration !== 0) fail("expired-authorization reconciliation head is not an unlaunched authorization");
  const nextTime = makeClock(recovered.head, clock);
  const failed = await append(stateRoot, prepared, recovered.head, "failed", nextTime, checkpointSuffix, {
    progress: {
      noProgressCount: recovered.head.progress.noProgressCount + 1,
      failureStage: "authorization-expired",
      failureFingerprintSha256: sha({ stage: "candidate-authorization", name: "LeaseExpired", message: "lease-authority-expired-before-launch" }),
    },
  });
  return { action: "failed", checkpoint: failed.checkpoint };
}

export async function recordInterruptedCandidateFailure({
  stateRoot, prepared, lifecycleOptions, clock = () => new Date(), checkpointSuffix, cleanup = cleanupInterruptedProfileAttempt,
}) {
  if (!supportedProfiles.has(prepared?.plan?.profile) || prepared.cleanupOnly !== true || !prepared.recoveryConsumption) fail("interrupted candidate recovery requires one exact Scout, Researcher, or Data Lab cleanup boundary");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  if (!["running", "cleanup-failed"].includes(recovered.head.state) || recovered.head.iteration !== 1) fail("interrupted candidate recovery head is not cleanup-eligible");
  const claim = exactRecoveredClaim(prepared, await recoverLeaseConsumption(stateRoot, prepared.lease));
  if (sha(claim) !== sha(prepared.recoveryConsumption) || recovered.head.workerSessionSha256 !== sha(claim)) fail("interrupted candidate claim differs from the checkpoint or recovery preparation");
  const nextTime = makeClock(recovered.head, clock);
  const runningCheckpoint = [...recovered.checkpoints].reverse().find((checkpoint) => checkpoint.state === "running" && checkpoint.iteration === 1);
  if (!runningCheckpoint || runningCheckpoint.workerSessionSha256 !== sha(claim)) fail("interrupted candidate running checkpoint is unavailable or differs from its claim");
  let cleanupResult;
  try {
    const attemptOptions = await resolveAttemptLifecycleOptions(lifecycleOptions, {
      mode: "cleanup", prepared, claim, checkpoint: runningCheckpoint,
    });
    cleanupResult = await cleanup(prepared, claim, attemptOptions);
    if (cleanupResult?.resourcesRemoved !== true || !cleanupResult.usage) fail("interrupted candidate resources were not proven removed");
  } catch (error) {
    const blocked = await appendCleanupBlock({
      stateRoot, prepared, head: recovered.head, nextTime, recordSuffix: checkpointSuffix, error,
    });
    const failure = new CandidateWaitLoopError(blocked
      ? `profile cleanup could not safely close the attempt; the attempt is ${blocked.checkpoint.state}`
      : "profile cleanup remains unproven; the cleanup-failed checkpoint is unchanged");
    failure.cause = error;
    failure.checkpoint = blocked?.checkpoint ?? recovered.head;
    throw failure;
  }
  const alreadyCharged = recovered.head.state === "cleanup-failed";
  const failed = await append(stateRoot, prepared, recovered.head, "failed", nextTime, checkpointSuffix, {
    usage: alreadyCharged ? recovered.head.usage : failedUsage(recovered.head, prepared.lease, cleanupResult.usage),
    progress: alreadyCharged ? recovered.head.progress : {
      ...recovered.head.progress,
      noProgressCount: Math.min(prepared.plan.budgets.noProgressLimit, recovered.head.progress.noProgressCount + 1),
      failureStage: "interrupted",
      failureFingerprintSha256: sha({ stage: "candidate", failure: "interrupted" }),
    },
  });
  return { action: "failed", claim, checkpoint: failed.checkpoint, cleanup: cleanupResult };
}

export const candidateWaitLoopBoundary = "Durable one-shot Scout, Researcher, and Data Lab candidate bridge. Deterministic evidence/replay checks may retain an artifact and pause for independent semantic acceptance; they can never declare an immutable goal criterion complete.";

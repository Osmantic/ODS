import { createHash, randomBytes } from "node:crypto";

import { canonical, validatePlanVerificationEvidence } from "../../scripts/lib/work-contract.mjs";
import {
  cleanupInterruptedBuilderAttempt, recoverBuilderCandidate, runBuilderCandidateLifecycle, verifyBuilderCandidate,
} from "../work-runner/docker-supervisor.mjs";
import { recoverIndependentBuilderVerification } from "../work-runner/verifier-supervisor.mjs";
import { claimLease, createLeaseConsumption, recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import {
  appendCheckpoint, checkpointSha256, decideCheckpointAction, recoverCheckpointLedger,
} from "./checkpoints.mjs";
import { issueContinuationLease } from "./continuation.mjs";
import { createFailureDiagnostic, retainFailureDiagnostic } from "./failure-diagnostics.mjs";
import { resolveAttemptLifecycleOptions } from "./goal-capability-runtime.mjs";

const SHA_RE = /^[a-f0-9]{64}$/;
const FAILURE_STAGES = new Set([
  "profile-execution", "rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract",
  "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup",
  "authorization-expired", "interrupted",
]);

export class BuilderLoopError extends Error {}

function fail(message) {
  throw new BuilderLoopError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
}

function suffix(value) {
  const result = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/.test(result)) fail("Builder loop suffix is invalid");
  return result;
}

function exactNonnegative(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${label} is invalid`);
  return value;
}

function boundedFailureStage(value, fallback = "profile-execution") {
  return FAILURE_STAGES.has(value) ? value : fallback;
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

function candidateUsage(head, candidate) {
  const proxy = candidate?.proxyReceipt;
  if (
    !candidate?.patch || !candidate?.evidence || !proxy || !SHA_RE.test(candidate.patch.sha256 ?? "")
    || !Number.isSafeInteger(candidate.durationMilliseconds) || candidate.durationMilliseconds < 0
  ) fail("Builder candidate receipt is incomplete");
  for (const field of ["bytes", "changes"]) exactNonnegative(candidate.patch[field], `Builder patch ${field}`);
  exactNonnegative(candidate.evidence.bytes, "Builder evidence bytes");
  for (const field of ["modelRequests", "inputTokens", "outputTokens", "networkBytes"]) exactNonnegative(proxy[field], `Builder proxy ${field}`);
  return {
    runtimeSeconds: head.usage.runtimeSeconds + Math.ceil(candidate.durationMilliseconds / 1000),
    modelRequests: head.usage.modelRequests + proxy.modelRequests,
    inputTokens: head.usage.inputTokens + proxy.inputTokens,
    outputTokens: head.usage.outputTokens + proxy.outputTokens,
    networkBytes: head.usage.networkBytes + proxy.networkBytes,
    artifactBytes: head.usage.artifactBytes + candidate.patch.bytes + candidate.evidence.bytes,
    failures: head.usage.failures,
  };
}

function failedWorkerUsage(head, lease, error) {
  const observed = error?.workUsage;
  const value = {
    runtimeSeconds: observed?.runtimeSeconds ?? lease.budgets.maxRuntimeSeconds,
    modelRequests: observed?.modelRequests ?? lease.budgets.maxModelRequests,
    inputTokens: observed?.inputTokens ?? lease.budgets.maxInputTokens,
    outputTokens: observed?.outputTokens ?? lease.budgets.maxOutputTokens,
    networkBytes: observed?.networkBytes ?? lease.budgets.maxNetworkBytes,
    artifactBytes: observed?.artifactBytes ?? 0,
    failures: observed?.failures ?? 1,
  };
  for (const [field, budget] of [
    ["runtimeSeconds", "maxRuntimeSeconds"], ["modelRequests", "maxModelRequests"], ["inputTokens", "maxInputTokens"],
    ["outputTokens", "maxOutputTokens"], ["networkBytes", "maxNetworkBytes"], ["artifactBytes", "maxArtifactBytes"], ["failures", "maxFailures"],
  ]) {
    exactNonnegative(value[field], `failed Builder ${field}`);
    if (value[field] > lease.budgets[budget]) fail(`failed Builder ${field} exceeds its iteration lease`);
  }
  return Object.fromEntries(Object.keys(head.usage).map((field) => [field, head.usage[field] + value[field]]));
}

async function appendCleanupBlock({ stateRoot, prepared, head, nextTime, recordSuffix, error }) {
  const state = error?.workRecoveryInconclusive === true ? "recovery-inconclusive" : "cleanup-failed";
  if (head.state === "cleanup-failed" && state === "cleanup-failed") return null;
  const firstBlock = head.state === "running";
  const usage = firstBlock ? failedWorkerUsage(head, prepared.lease, error) : head.usage;
  const diagnostic = createFailureDiagnostic({
    jobId: prepared.plan.jobId,
    iteration: prepared.lease.iteration,
    stage: "worker-cleanup",
    failureStage: "cleanup",
    error,
    usage,
  });
  const retained = await retainFailureDiagnostic({ stateRoot, diagnostic });
  return append(stateRoot, prepared, head, state, nextTime, recordSuffix, {
    usage,
    progress: {
      ...head.progress,
      noProgressCount: firstBlock ? Math.min(prepared.plan.budgets.noProgressLimit, head.progress.noProgressCount + 1) : head.progress.noProgressCount,
      failureFingerprintSha256: retained.fingerprint,
      failureStage: "cleanup",
    },
  });
}

function verifiedUsage(verifying, verification) {
  if (!verification?.artifact || !SHA_RE.test(verification.artifact.sha256 ?? "")) fail("Builder verifier artifact is incomplete");
  exactNonnegative(verification.artifact.bytes, "Builder verifier artifact bytes");
  const runtimeMilliseconds = verification.evidence.checks.reduce((total, check) => total + (check.runtimeMilliseconds ?? 0), 0);
  exactNonnegative(runtimeMilliseconds, "Builder verifier runtime");
  return {
    ...verifying.usage,
    runtimeSeconds: verifying.usage.runtimeSeconds + Math.ceil(runtimeMilliseconds / 1000),
    artifactBytes: verifying.usage.artifactBytes + verification.artifact.bytes,
    failures: verifying.usage.failures + (verification.evidence.status === "pass" ? 0 : 1),
  };
}

function verifiedProgress(previous, plan, verification) {
  const errors = validatePlanVerificationEvidence(plan, verification?.evidence);
  if (errors.length) fail(`Builder verifier evidence is invalid: ${errors[0]}`);
  const passing = verification.evidence.criteria.filter((criterion) => criterion.status === "pass").length;
  const failed = verification.evidence.checks.filter((check) => check.status === "fail").map((check) => ({ id: check.id, evidenceSha256: check.evidenceSha256 }));
  return {
    criteriaTotal: plan.acceptanceCriteria.length,
    criteriaPassing: passing,
    criteriaFailing: plan.acceptanceCriteria.length - passing,
    noProgressCount: passing > previous.progress.criteriaPassing ? 0 : previous.progress.noProgressCount + 1,
    failureFingerprintSha256: failed.length ? sha(failed) : null,
  };
}

function makeClock(head, clock) {
  let last = Date.parse(head.createdAt);
  return () => {
    const observed = clock();
    const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
    if (!Number.isFinite(milliseconds)) fail("Builder loop clock is invalid");
    last = Math.max(last + 1, Math.trunc(milliseconds));
    return new Date(last);
  };
}

function exactRecoveredClaim(prepared, claim) {
  if (
    claim?.status !== "consumed" || claim.externalEffects !== false
    || claim.jobId !== prepared.plan.jobId || claim.leaseId !== prepared.lease.leaseId
    || claim.planSha256 !== prepared.bindings.planSha256 || claim.leaseSha256 !== prepared.bindings.leaseSha256
    || claim.policySha256 !== prepared.bindings.policySha256 || claim.inputSetSha256 !== prepared.bindings.inputSetSha256
    || claim.workspaceSha256 !== prepared.workspace.sha256
    || canonical(claim.executor) !== canonical(prepared.plan.executor)
    || canonical(claim.model) !== canonical(prepared.plan.model)
    || claim.runnerImageDigest !== prepared.plan.isolation.runnerImageDigest
  ) fail("recovered Builder claim differs from the exact prepared boundary");
  return claim;
}

async function append(stateRoot, prepared, head, state, nextTime, recordSuffix, overrides = {}) {
  return appendCheckpoint({
    stateRoot,
    plan: prepared.plan,
    lease: prepared.lease,
    previousCheckpointSha256: checkpointSha256(head),
    update: update(head, state, overrides),
    now: nextTime(),
    suffix: suffix(recordSuffix),
  });
}

async function finishVerified({ stateRoot, prepared, claim, candidate, verification, verified, nextTime, suffixes }) {
  const decision = decideCheckpointAction(verified.checkpoint, prepared.plan);
  if (decision.action === "record-completed") {
    const terminal = await append(stateRoot, prepared, verified.checkpoint, "completed", nextTime, suffixes.terminal);
    return { action: "completed", claim, candidate, verification, checkpoint: terminal.checkpoint, nextLease: null };
  }
  if (decision.action === "record-no-progress" || decision.action === "record-budget-exhausted") {
    const state = decision.action === "record-no-progress" ? "no-progress" : "budget-exhausted";
    const terminal = await append(stateRoot, prepared, verified.checkpoint, state, nextTime, suffixes.terminal);
    return { action: state, claim, candidate, verification, checkpoint: terminal.checkpoint, nextLease: null };
  }
  if (decision.action !== "issue-continuation-lease") fail("Builder iteration ended in an unsupported controller state");
  const nextLease = await issueContinuationLease({
    stateRoot,
    plan: prepared.plan,
    previousLease: prepared.lease,
    policy: prepared.policy,
    previousConsumption: claim,
    checkpoint: verified.checkpoint,
    now: nextTime(),
    suffix: suffix(suffixes.continuation),
  });
  return { action: "continue", claim, candidate, verification, checkpoint: verified.checkpoint, nextLease };
}

export async function executeBuilderIteration({
  stateRoot,
  prepared,
  lifecycleOptions,
  clock = () => new Date(),
  suffixes = {},
  candidateRunner = runBuilderCandidateLifecycle,
  verifier = verifyBuilderCandidate,
}) {
  if (
    prepared?.plan?.profile !== "builder" || prepared.lease?.iteration < 1
    || prepared.verificationOnly === true || prepared.cleanupOnly === true
  ) fail("Builder loop requires one execution-capable prepared Builder iteration");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  const expectedState = prepared.lease.iteration === 1 ? "authorized" : "verified";
  if (recovered.head.state !== expectedState || recovered.head.iteration !== prepared.lease.iteration - 1) fail("Builder loop head does not match the prepared lease iteration");
  if (
    (prepared.lease.iteration === 1 && prepared.lease.continuation !== null)
    || (prepared.lease.iteration > 1 && prepared.lease.continuation?.previousCheckpointSha256 !== recovered.headSha256)
  ) fail("Builder loop lease does not bind the recovered checkpoint head");
  const nextTime = makeClock(recovered.head, clock);
  const claim = createLeaseConsumption(prepared, { now: nextTime(), suffix: suffix(suffixes.claim) });
  let consumed = claim;
  try {
    await claimLease(stateRoot, claim);
  } catch (error) {
    try {
      consumed = exactRecoveredClaim(prepared, await recoverLeaseConsumption(stateRoot, prepared.lease));
    } catch {
      throw error;
    }
  }
  const running = await append(stateRoot, prepared, recovered.head, "running", nextTime, suffixes.running, {
    iteration: prepared.lease.iteration,
    artifactManifestSha256: null,
    workerSessionSha256: checkpointSha256(consumed),
    verificationEvidenceSha256: null,
  });

  let candidate;
  try {
    const attemptOptions = await resolveAttemptLifecycleOptions(lifecycleOptions, {
      mode: "execute", prepared, claim: consumed, checkpoint: running.checkpoint,
    });
    candidate = await candidateRunner(prepared, consumed, attemptOptions);
  } catch (error) {
    if (error?.workCleanupComplete !== true) {
      const blocked = await appendCleanupBlock({
        stateRoot, prepared, head: running.checkpoint, nextTime, recordSuffix: suffixes.failed, error,
      });
      const failure = new BuilderLoopError(`Builder worker failed before cleanup was proven; the job is ${blocked.checkpoint.state}`);
      failure.cause = error;
      failure.checkpoint = blocked.checkpoint;
      throw failure;
    }
    const usage = failedWorkerUsage(running.checkpoint, prepared.lease, error);
    const failureStage = boundedFailureStage(error?.workFailureStage);
    const diagnostic = createFailureDiagnostic({
      jobId: prepared.plan.jobId,
      iteration: prepared.lease.iteration,
      stage: "worker",
      failureStage,
      error,
      usage,
    });
    const retained = await retainFailureDiagnostic({ stateRoot, diagnostic });
    const failed = await append(stateRoot, prepared, running.checkpoint, "failed", nextTime, suffixes.failed, {
      usage,
      progress: {
        noProgressCount: running.checkpoint.progress.noProgressCount + 1,
        failureFingerprintSha256: retained.fingerprint,
        failureStage,
      },
    });
    const failure = new BuilderLoopError("Builder worker failed and the job was durably closed");
    failure.cause = error;
    failure.checkpoint = failed.checkpoint;
    throw failure;
  }
  const candidateArtifactBytes = candidate.patch.bytes + candidate.evidence.bytes;
  if (candidateArtifactBytes > prepared.lease.budgets.maxArtifactBytes) fail("Builder candidate exceeds its iteration artifact budget");
  const verifying = await append(stateRoot, prepared, running.checkpoint, "verifying", nextTime, suffixes.verifying, {
    usage: candidateUsage(running.checkpoint, candidate),
    artifactManifestSha256: candidate.patch.sha256,
  });

  const verification = await verifier(prepared, consumed, candidate, lifecycleOptions);
  if (verification.evidence.patchSha256 !== candidate.patch.sha256 || verification.evidence.claimId !== consumed.claimId) {
    fail("Builder verifier evidence differs from the retained candidate");
  }
  const progress = verifiedProgress(verifying.checkpoint, prepared.plan, verification);
  const verified = await append(stateRoot, prepared, verifying.checkpoint, "verified", nextTime, suffixes.verified, {
    usage: verifiedUsage(verifying.checkpoint, verification),
    progress,
    workspaceSnapshotSha256: verification.evidence.candidateSha256,
    verificationEvidenceSha256: verification.artifact.sha256,
  });

  return finishVerified({ stateRoot, prepared, claim: consumed, candidate, verification, verified, nextTime, suffixes });
}

export async function resumeBuilderVerification({
  stateRoot,
  prepared,
  lifecycleOptions,
  clock = () => new Date(),
  suffixes = {},
  candidateRecovery = recoverBuilderCandidate,
  verifier = verifyBuilderCandidate,
  verificationRecovery = recoverIndependentBuilderVerification,
}) {
  if (prepared?.verificationOnly !== true || !prepared.recoveryConsumption) fail("Builder verification recovery requires a verification-only preparation");
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  if (
    recovered.head.state !== "verifying" || recovered.head.iteration !== prepared.lease.iteration
    || recovered.head.workerSessionSha256 !== sha(prepared.recoveryConsumption)
  ) fail("Builder verifier recovery head differs from the consumed iteration");
  const nextTime = makeClock(recovered.head, clock);
  const claim = prepared.recoveryConsumption;
  const candidate = await candidateRecovery(prepared, claim, lifecycleOptions);
  if (candidate.patch.sha256 !== recovered.head.artifactManifestSha256) fail("recovered Builder candidate differs from the checkpoint artifact");
  let verification = await verificationRecovery(prepared, claim, candidate.patch);
  if (candidate.verificationArtifactPresent && verification === null) fail("retained verifier artifact could not be recovered");
  if (verification === null) verification = await verifier(prepared, claim, candidate, lifecycleOptions);
  if (verification.evidence.patchSha256 !== candidate.patch.sha256 || verification.evidence.claimId !== claim.claimId) {
    fail("recovered Builder verifier evidence differs from the retained candidate");
  }
  const progress = verifiedProgress(recovered.head, prepared.plan, verification);
  const verified = await append(stateRoot, prepared, recovered.head, "verified", nextTime, suffixes.verified, {
    usage: verifiedUsage(recovered.head, verification),
    progress,
    workspaceSnapshotSha256: verification.evidence.candidateSha256,
    verificationEvidenceSha256: verification.artifact.sha256,
  });
  return finishVerified({ stateRoot, prepared, claim, candidate, verification, verified, nextTime, suffixes });
}

export async function recordInterruptedBuilderFailure({
  stateRoot,
  prepared,
  lifecycleOptions,
  clock = () => new Date(),
  checkpointSuffix,
  cleanup = cleanupInterruptedBuilderAttempt,
}) {
  if (prepared?.plan?.profile !== "builder" || prepared.cleanupOnly !== true || !prepared.recoveryConsumption) {
    fail("interrupted worker recovery requires a cleanup-only prepared Builder boundary");
  }
  const recovered = await recoverCheckpointLedger({ stateRoot, plan: prepared.plan, lease: prepared.lease });
  if (!["running", "cleanup-failed"].includes(recovered.head.state) || recovered.head.iteration !== prepared.lease.iteration) fail("interrupted worker recovery head is not cleanup-eligible");
  const claim = await recoverLeaseConsumption(stateRoot, prepared.lease);
  if (
    claim.jobId !== prepared.plan.jobId || claim.planSha256 !== prepared.bindings.planSha256
    || claim.policySha256 !== prepared.bindings.policySha256 || claim.inputSetSha256 !== prepared.bindings.inputSetSha256
    || claim.workspaceSha256 !== prepared.workspace.sha256 || recovered.head.workerSessionSha256 !== sha(claim)
    || sha(claim) !== sha(prepared.recoveryConsumption)
  ) fail("interrupted worker claim differs from the checkpoint and prepared boundary");
  const nextTime = makeClock(recovered.head, clock);
  const runningCheckpoint = [...recovered.checkpoints].reverse().find((checkpoint) => checkpoint.state === "running" && checkpoint.iteration === prepared.lease.iteration);
  if (!runningCheckpoint || runningCheckpoint.workerSessionSha256 !== sha(claim)) fail("interrupted worker running checkpoint is unavailable or differs from its claim");
  let cleanupResult;
  try {
    const attemptOptions = await resolveAttemptLifecycleOptions(lifecycleOptions, {
      mode: "cleanup", prepared, claim, checkpoint: runningCheckpoint,
    });
    cleanupResult = await cleanup(prepared, claim, attemptOptions);
    if (cleanupResult?.resourcesRemoved !== true || !cleanupResult.usage) fail("interrupted worker resources were not proven removed");
  } catch (error) {
    const blocked = await appendCleanupBlock({
      stateRoot, prepared, head: recovered.head, nextTime, recordSuffix: checkpointSuffix, error,
    });
    const failure = new BuilderLoopError(blocked
      ? `Builder cleanup could not safely close the attempt; the job is ${blocked.checkpoint.state}`
      : "Builder cleanup remains unproven; the cleanup-failed checkpoint is unchanged");
    failure.cause = error;
    failure.checkpoint = blocked?.checkpoint ?? recovered.head;
    throw failure;
  }
  const alreadyCharged = recovered.head.state === "cleanup-failed";
  const usage = alreadyCharged ? recovered.head.usage : failedWorkerUsage(recovered.head, prepared.lease, { workUsage: cleanupResult.usage });
  let progress = recovered.head.progress;
  if (!alreadyCharged) {
    const diagnostic = createFailureDiagnostic({
      jobId: prepared.plan.jobId,
      iteration: prepared.lease.iteration,
      stage: "worker",
      failureStage: "interrupted",
      error: { name: "Error", message: "interrupted worker attempt" },
      usage,
    });
    const retained = await retainFailureDiagnostic({ stateRoot, diagnostic });
    progress = {
      ...recovered.head.progress,
      noProgressCount: Math.min(prepared.plan.budgets.noProgressLimit, recovered.head.progress.noProgressCount + 1),
      failureFingerprintSha256: retained.fingerprint,
      failureStage: "interrupted",
    };
  }
  const failed = await append(stateRoot, prepared, recovered.head, "failed", nextTime, checkpointSuffix, {
    usage,
    progress,
  });
  return { action: "failed", claim, checkpoint: failed.checkpoint, cleanup: cleanupResult };
}

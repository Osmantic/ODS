import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  link, lstat, mkdir, open, realpath, rm, unlink,
} from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical,
  validatePlanLease,
  validateWorkCheckpoint,
  validateWorkConsumption,
  validateWorkLeaseRevocation,
  validateWorkPlan,
  validateWorkPolicy,
  validateWorkLease,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { requireQualifiedModel } from "../work-controller/model-qualification.mjs";
import { validateBuilderPatchForApplication } from "./builder-volume.mjs";
import { materializeUstar } from "./safe-tar.mjs";

const authority = Object.freeze({
  hostAccess: false,
  ambientCredentials: false,
  arbitraryNetwork: false,
  externalEffects: false,
  merge: false,
  deploy: false,
  publish: false,
  purchase: false,
  policyMutation: false,
  acceptanceCriteriaMutation: false,
  leaseExpansion: false,
});
const consumptionBoundary = "Private immutable tombstone proving one lease was consumed once. It grants no replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.";
const revocationBoundary = "Private immutable tombstone proving one exact lease was revoked before launch. It grants no execution, replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.";
const budgetFields = Object.freeze([
  "maxRuntimeSeconds", "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxModelRequests",
  "maxInputTokens", "maxOutputTokens", "maxCpuCores", "maxMemoryMiB", "maxDiskBytes",
  "maxArtifactBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit",
]);
const cumulativeBudgets = Object.freeze({
  runtimeSeconds: "maxRuntimeSeconds",
  modelRequests: "maxModelRequests",
  inputTokens: "maxInputTokens",
  outputTokens: "maxOutputTokens",
  networkBytes: "maxNetworkBytes",
  artifactBytes: "maxArtifactBytes",
  failures: "maxFailures",
});

export class WorkRunnerError extends Error {}

function fail(message) {
  throw new WorkRunnerError(message);
}

function sha(value) {
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(typeof value === "string" ? value : canonical(value), "utf8");
  return createHash("sha256").update(bytes).digest("hex");
}

function schema(label, errors) {
  if (errors.length > 0) fail(`${label} failed validation: ${errors.join("; ")}`);
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) {
    fail(`${label} shape is invalid`);
  }
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) {
    fail(`${label} must be owner-only`);
  }
  return resolve(path);
}

async function validateStoredConsumption(stateRoot, lease, consumption) {
  const state = await privateDirectory(stateRoot, "private runner state");
  const claimsRoot = await privateDirectory(join(state, "claims"), "private lease claims");
  const { text, details } = await readBoundedRegularText(join(claimsRoot, `${lease.leaseId}.json`), 64 * 1024, "previous lease claim");
  if (details.nlink !== 1 || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))) {
    fail("previous lease claim is not private and single-link");
  }
  let stored;
  try { stored = JSON.parse(text); } catch { fail("previous lease claim is not JSON"); }
  if (canonical(stored) !== canonical(consumption)) fail("previous lease was not consumed by this runner state");
  return state;
}

async function digestFile(path, maximumBytes, label, executable = false) {
  const before = await lstat(path).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size < 1 || before.size > maximumBytes) {
    fail(`${label} must be a bounded single-link regular file`);
  }
  if (process.platform !== "win32") {
    if (before.uid !== process.geteuid() || (before.mode & 0o077) !== 0) fail(`${label} must be runner-owned and owner-only`);
    if (executable && (before.mode & 0o100) === 0) fail(`${label} is not owner-executable`);
  }
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail(`${label} could not be opened safely`));
  try {
    const opened = await handle.stat();
    if (
      !opened.isFile()
      || opened.nlink !== 1
      || opened.size !== before.size
      || opened.dev !== before.dev
      || opened.ino !== before.ino
    ) fail(`${label} changed during validation`);
    const digest = createHash("sha256");
    const buffer = Buffer.alloc(1024 * 1024);
    let total = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      total += bytesRead;
      if (total > maximumBytes) fail(`${label} exceeded its byte ceiling`);
      digest.update(buffer.subarray(0, bytesRead));
    }
    if (total !== before.size) fail(`${label} changed size during validation`);
    return { bytes: total, sha256: digest.digest("hex") };
  } finally {
    await handle.close();
  }
}

function verifyProfileBindings(plan, lease, policy, expectedProfile, options = {}) {
  schema("work plan", validateWorkPlan(plan));
  schema("work lease", validateWorkLease(lease));
  schema("private work policy", validateWorkPolicy(policy));
  schema("plan/lease", validatePlanLease(plan, lease));
  if (!["scout", "builder", "data-lab", "researcher"].includes(expectedProfile) || plan.profile !== expectedProfile) fail(`the v1 runner did not receive a ${expectedProfile} plan`);
  const profile = policy.profiles[expectedProfile === "data-lab" ? "dataLab" : expectedProfile];
  const label = expectedProfile === "scout" ? "Scout" : expectedProfile === "builder" ? "Builder" : expectedProfile === "data-lab" ? "Data Lab" : "Researcher";
  if (!policy.enabled || !policy.runner.prepared || !policy.localModel.prepared || !profile.enabled) fail(`${label} execution is disabled or unprepared`);
  if (plan.isolation.mode !== "hardened-container") fail(`the v1 runner accepts only hardened-container ${label} plans`);
  if (!policy.runner.allowedIsolation.includes(plan.isolation.mode)) fail("plan isolation is not allowed by private policy");
  if (
    canonical(plan.grantedCapabilities.tools) !== canonical(profile.tools)
    || canonical(plan.grantedCapabilities.network.services) !== canonical(profile.services)
    || canonical(plan.outputGate.allowedKinds) !== canonical(profile.outputKinds)
    || plan.isolation.workspaceMount !== profile.workspaceMount
  ) fail(`plan capabilities differ from private ${label} policy`);
  for (const field of budgetFields) {
    if (plan.budgets[field] > profile.maxBudgets[field]) fail(`plan exceeds private ${label} budget ${field}`);
  }
  if (sha(policy) !== plan.policySha256) fail("private policy hash differs from the compiled plan");
  if (sha(plan.inputs) !== plan.inputSetSha256) fail("input set hash differs from the compiled plan");
  const policyExecutor = {
    id: policy.executor.id,
    version: policy.executor.version,
    sourceCommit: policy.executor.sourceCommit,
    license: policy.executor.license,
    artifactSha256: policy.executor.sha256,
    rpcProtocolVersion: policy.executor.rpcProtocolVersion,
  };
  if (canonical(policyExecutor) !== canonical(plan.executor)) fail("executor differs from private policy");
  const policyModel = {
    route: "local-only",
    provider: policy.localModel.provider,
    id: policy.localModel.id,
    backendImageDigest: policy.localModel.imageDigest,
    contextWindow: policy.localModel.maxRequestContextTokens,
    supportsVision: policy.localModel.supportsVision,
    endpointContract: "llama.cpp-discovery-and-openai-stream-v1",
  };
  if (canonical(policyModel) !== canonical(plan.model)) fail("local model differs from private policy");
  if (plan.isolation.runnerImageDigest !== policy.runner.imageDigest) fail("runner image differs from private policy");
  if (expectedProfile === "researcher") {
    const backend = {
      adapter: profile.backend.adapter,
      endpointContract: profile.backend.endpointContract,
      queryLogging: profile.backend.queryLogging,
      contentRetention: profile.backend.contentRetention,
      webCourierRequired: profile.backend.webCourierRequired,
    };
    if (
      plan.dataClassification !== "public" || profile.backend.prepared !== true
      || canonical(plan.researchBackend) !== canonical(backend)
      || canonical(plan.researchBackend) !== canonical(lease.researchBackend)
      || canonical(plan.research) !== canonical(lease.research)
    ) fail("Researcher backend or egress policy differs from private policy");
    for (const field of ["maxQueries", "maxResultsPerQuery", "maxSources", "maxSourceBytes", "maxTotalSourceBytes"]) {
      if (plan.research[field] > profile.maxResearch[field]) fail(`plan exceeds private Researcher limit ${field}`);
    }
    const allowed = new Set(profile.maxResearch.allowedSourceTypes);
    if (plan.research.sourceTypes.some((sourceType) => !allowed.has(sourceType))) fail("plan exceeds private Researcher source types");
  }
  if (expectedProfile === "data-lab") {
    if (canonical(plan.data) !== canonical(lease.data) || canonical(plan.dataRuntime) !== canonical(profile.runtime) || canonical(plan.dataRuntime) !== canonical(lease.dataRuntime)) fail("Data Lab runtime or data contract differs from private policy");
    const limits = profile.maxData;
    if (plan.data.datasets.length > limits.maxDatasets || plan.data.maxArtifactFiles > limits.maxArtifactFiles || plan.data.maxArtifactBytes > limits.maxArtifactBytes) fail("plan exceeds private Data Lab limits");
    const inputFormats = new Set(limits.allowedInputFormats);
    const artifactFormats = new Set(limits.allowedArtifactFormats);
    for (const dataset of plan.data.datasets) {
      if (dataset.maxBytes > limits.maxDatasetBytes || !inputFormats.has(dataset.format)) fail(`plan exceeds private Data Lab dataset ${dataset.datasetId}`);
    }
    if (plan.data.allowedArtifactFormats.some((format) => !artifactFormats.has(format))) fail("plan exceeds private Data Lab artifact formats");
  }
  const now = options.now ?? new Date();
  const nowMs = now.getTime();
  const issuedAt = Date.parse(lease.issuedAt);
  const expiresAt = Date.parse(lease.expiresAt);
  const compiledAt = Date.parse(plan.compiledAt);
  if (!Number.isFinite(nowMs) || nowMs < issuedAt || nowMs >= expiresAt) fail("lease is not currently valid for this exact plan");
  if (
    (lease.iteration === 1 && compiledAt !== issuedAt)
    || (lease.iteration > 1 && issuedAt <= compiledAt)
  ) fail("lease issuance does not match its immutable plan iteration");
  return {
    planSha256: sha(plan),
    leaseSha256: sha(lease),
    policySha256: sha(policy),
    inputSetSha256: sha(plan.inputs),
  };
}

async function admitQualifiedModel(policy, plan, profile, now) {
  const binding = policy.localModel.qualification;
  const path = resolve(binding.receiptPath);
  if (path !== binding.receiptPath) fail("model qualification receipt path is not absolute and normalized");
  const [before, actualPath] = await Promise.all([lstat(path).catch(() => null), realpath(path).catch(() => null)]);
  if (
    !before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || actualPath !== path
    || process.platform !== "win32" && (before.uid !== process.geteuid() || (before.mode & 0o077) !== 0)
  ) fail("model qualification receipt is not owner-private, single-link, and real");
  let record;
  try { record = await readBoundedRegularFile(path, 1024 * 1024, "model qualification receipt"); }
  catch { fail("model qualification receipt could not be opened safely"); }
  if (
    record.details.dev !== before.dev || record.details.ino !== before.ino || record.details.size !== before.size
    || record.details.nlink !== 1
  ) fail("model qualification receipt changed during validation");
  let receipt;
  try { receipt = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(record.bytes)); }
  catch { fail("model qualification receipt is not strict JSON"); }
  const model = {
    provider: policy.localModel.provider,
    id: policy.localModel.id,
    modelArtifactSha256: policy.localModel.modelArtifactSha256,
    backendImageDigest: policy.localModel.imageDigest,
    backendVersion: policy.localModel.backendVersion,
    acceleratorClass: policy.localModel.acceleratorClass,
    promptContractSha256: policy.localModel.promptContractSha256,
    toolSchemaSha256: policy.localModel.toolSchemaSha256,
    contextWindow: policy.localModel.contextWindow,
    supportsVision: policy.localModel.supportsVision,
  };
  try {
    return Object.freeze(requireQualifiedModel(receipt, {
      expectedReceiptSha256: binding.receiptSha256,
      expectedCasesSha256: binding.casesSha256,
      expectedEvaluatorSha256: binding.evaluatorSha256,
      model, profile,
      requiredContextTokens: plan.model.contextWindow,
      requiredOutputTokens: policy.localModel.maxRequestOutputTokens,
      now,
    }));
  } catch { fail("local model is not currently qualified for this exact profile and envelope"); }
}

export function verifyScoutBindings(plan, lease, policy, options = {}) {
  return verifyProfileBindings(plan, lease, policy, "scout", options);
}

export function verifyBuilderBindings(plan, lease, policy, options = {}) {
  return verifyProfileBindings(plan, lease, policy, "builder", options);
}

export function verifyResearcherBindings(plan, lease, policy, options = {}) {
  return verifyProfileBindings(plan, lease, policy, "researcher", options);
}

export function verifyDataLabBindings(plan, lease, policy, options = {}) {
  return verifyProfileBindings(plan, lease, policy, "data-lab", options);
}

async function prepareProfileRun({ plan, lease, policy, objectStore, workspaceRoot, executorPath, archiveLimits, now }, expectedProfile) {
  const bindings = verifyProfileBindings(plan, lease, policy, expectedProfile, { now });
  const modelQualification = await admitQualifiedModel(policy, plan, expectedProfile, now);
  if (!archiveLimits || !Number.isSafeInteger(archiveLimits.maxEntries) || !Number.isSafeInteger(archiveLimits.maxFileBytes)) {
    fail("private archive limits are invalid");
  }
  if (archiveLimits.maxEntries < 1 || archiveLimits.maxEntries > 100000 || archiveLimits.maxFileBytes < 1 || archiveLimits.maxFileBytes > 4294967296) {
    fail("private archive limits exceed the runner's hard ceiling");
  }
  const objects = await privateDirectory(objectStore, "private input object store");
  const workspaces = await privateDirectory(workspaceRoot, "private workspace root");
  const executor = await digestFile(executorPath, 1024 * 1024 * 1024, "pinned executor", true);
  if (executor.sha256 !== plan.executor.artifactSha256) fail("pinned executor SHA-256 differs from the lease");

  const staging = join(workspaces, `.prepare-${plan.jobId}-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  const sourcePath = expectedProfile === "scout" ? staging : join(staging, "source");
  if (sourcePath !== staging) await mkdir(sourcePath, { mode: 0o700 });
  const materializedInputs = [];
  let remainingBytes = expectedProfile === "scout" ? lease.budgets.maxDiskBytes : Math.floor(lease.budgets.maxDiskBytes / 2);
  let remainingEntries = archiveLimits.maxEntries;
  try {
    for (const input of plan.inputs) {
      if (basename(input.objectName) !== input.objectName) fail("input object name is unsafe");
      if (remainingBytes < 1 || remainingEntries < 1) fail("workspace materialization exhausted its aggregate ceiling");
      const objectPath = join(objects, input.objectName);
      const inputRoot = join(sourcePath, input.id);
      const result = await materializeUstar(objectPath, inputRoot, {
        expectedSha256: input.contentSha256,
        maxArchiveBytes: input.bytes,
        maxExtractedBytes: remainingBytes,
        maxEntries: remainingEntries,
        maxFileBytes: Math.min(archiveLimits.maxFileBytes, remainingBytes),
      });
      if (result.archiveBytes !== input.bytes) fail(`input ${input.id} byte length differs from the plan`);
      remainingBytes -= result.extractedBytes;
      remainingEntries -= result.entries.length;
      materializedInputs.push({
        id: input.id,
        archiveSha256: result.archiveSha256,
        treeSha256: result.treeSha256,
        extractedBytes: result.extractedBytes,
        entries: result.entries.length,
        inertEntries: result.entries.filter((entry) => entry.inert).length,
      });
    }
    const workspaceSha256 = sha(materializedInputs);
    let workspacePath = sourcePath;
    if (expectedProfile !== "scout") {
      workspacePath = join(staging, "workspace");
      await mkdir(workspacePath, { mode: 0o700 });
    }
    return {
      plan,
      lease,
      policy,
      bindings,
      modelQualification,
      executor: { path: resolve(executorPath), ...executor },
      workspace: {
        root: workspaces, path: workspacePath, originalPath: sourcePath, discardPath: staging,
        disposable: expectedProfile !== "scout", storage: expectedProfile === "scout" ? "host-read-only" : "docker-tmpfs-volume",
        sha256: workspaceSha256, inputs: materializedInputs,
      },
    };
  } catch (error) {
    await rm(staging, { recursive: true, force: true });
    throw error;
  }
}

export async function prepareScoutRun(options) {
  return prepareProfileRun(options, "scout");
}

function remainingContinuationBudgets(plan, checkpoint) {
  const budgets = { ...plan.budgets, maxIterations: plan.budgets.maxIterations - checkpoint.iteration };
  for (const [usage, budget] of Object.entries(cumulativeBudgets)) budgets[budget] = plan.budgets[budget] - checkpoint.usage[usage];
  return budgets;
}

async function prepareBuilderContinuation(prepared, options) {
  const { plan, lease } = prepared;
  const context = options.continuation;
  if (lease.iteration === 1) {
    if (context !== undefined && context !== null) fail("an initial Builder lease cannot inherit a continuation candidate");
    return null;
  }
  exactKeys(context, ["previousLease", "previousConsumption", "checkpoint", "patchPath"], "Builder continuation context");
  const { previousLease, previousConsumption, checkpoint } = context;
  schema("previous work lease", validateWorkLease(previousLease));
  schema("previous plan/lease", validatePlanLease(plan, previousLease));
  schema("previous lease consumption", validateWorkConsumption(previousConsumption));
  schema("verified continuation checkpoint", validateWorkCheckpoint(checkpoint));
  if (
    previousLease.iteration + 1 !== lease.iteration || checkpoint.iteration !== previousLease.iteration
    || checkpoint.state !== "verified" || checkpoint.progress.criteriaPassing >= checkpoint.progress.criteriaTotal
    || checkpoint.progress.noProgressCount >= plan.budgets.noProgressLimit
  ) fail("Builder continuation does not follow one incomplete verified iteration");
  if (
    checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== prepared.bindings.planSha256
    || checkpoint.inputSetSha256 !== prepared.bindings.inputSetSha256 || checkpoint.objectiveSha256 !== sha(plan.objective)
    || checkpoint.acceptanceCriteriaSha256 !== sha(plan.acceptanceCriteria)
    || checkpoint.workerSessionSha256 !== sha(previousConsumption) || checkpoint.verificationEvidenceSha256 === null
    || checkpoint.authorityExpansionObserved || checkpoint.acceptanceCriteriaMutationObserved || checkpoint.externalEffectsObserved
  ) fail("Builder continuation checkpoint differs from the immutable work");
  if (
    previousConsumption.status !== "consumed" || previousConsumption.externalEffects !== false
    || previousConsumption.jobId !== plan.jobId || previousConsumption.leaseId !== previousLease.leaseId
    || previousConsumption.leaseSha256 !== sha(previousLease) || previousConsumption.planSha256 !== prepared.bindings.planSha256
    || previousConsumption.policySha256 !== prepared.bindings.policySha256 || previousConsumption.inputSetSha256 !== prepared.bindings.inputSetSha256
    || previousConsumption.workspaceSha256 !== prepared.workspace.sha256
  ) fail("Builder continuation consumption differs from the prior lease");
  if (
    lease.continuation?.previousCheckpointSha256 !== sha(checkpoint)
    || lease.continuation?.previousConsumptionSha256 !== sha(previousConsumption)
    || lease.continuation?.cumulativeUsageSha256 !== sha(checkpoint.usage)
    || canonical(lease.budgets) !== canonical(remainingContinuationBudgets(plan, checkpoint))
  ) fail("Builder continuation lease differs from cumulative verified state");
  if (Date.parse(lease.issuedAt) <= Date.parse(checkpoint.createdAt) || Date.parse(checkpoint.createdAt) < Date.parse(previousConsumption.claimedAt)) {
    fail("Builder continuation chronology is invalid");
  }

  const stateRoot = await validateStoredConsumption(options.stateRoot, previousLease, previousConsumption);

  const resultsRoot = await privateDirectory(join(stateRoot, "results"), "private Builder results");
  const resultRoot = await privateDirectory(join(resultsRoot, previousConsumption.claimId), "previous Builder result");
  const patchPath = resolve(context.patchPath);
  if (patchPath !== join(resultRoot, "builder-patch.json")) fail("previous Builder patch path differs from its consumed claim");
  const { text, details } = await readBoundedRegularText(patchPath, previousLease.budgets.maxArtifactBytes, "previous Builder patch");
  if (
    details.nlink !== 1 || details.size !== Buffer.byteLength(text, "utf8")
    || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)
    )
  ) fail("previous Builder patch is not private, single-link, and stable");
  const patchSha256 = sha(text);
  if (patchSha256 !== checkpoint.artifactManifestSha256) fail("previous Builder patch differs from the verified checkpoint");
  let patch;
  try { patch = JSON.parse(text); } catch { fail("previous Builder patch is not JSON"); }
  validateBuilderPatchForApplication(patch, {
    jobId: plan.jobId,
    claimId: previousConsumption.claimId,
    planSha256: prepared.bindings.planSha256,
    workspaceSha256: prepared.workspace.sha256,
  }, {
    maxTreeBytes: Math.floor(plan.budgets.maxDiskBytes / 2),
    maxArtifactBytes: previousLease.budgets.maxArtifactBytes,
    immutablePathPrefixes: plan.verification.immutablePathPrefixes,
  });
  return Object.freeze({
    path: patchPath,
    sha256: patchSha256,
    bytes: details.size,
    changes: patch.changes.length,
    claimId: previousConsumption.claimId,
    artifactLimitBytes: previousLease.budgets.maxArtifactBytes,
  });
}

export async function prepareBuilderRun(options) {
  const prepared = await prepareProfileRun(options, "builder");
  try {
    const continuationBase = await prepareBuilderContinuation(prepared, options);
    return { ...prepared, continuationBase };
  } catch (error) {
    await rm(prepared.workspace.discardPath, { recursive: true, force: true });
    throw error;
  }
}

export async function prepareResearcherRun(options) {
  return prepareProfileRun(options, "researcher");
}

export async function prepareDataLabRun(options) {
  const prepared = await prepareProfileRun(options, "data-lab");
  try {
    const datasets = [];
    for (const dataset of prepared.plan.data.datasets) {
      const root = resolve(prepared.workspace.originalPath, dataset.inputId);
      const path = resolve(root, ...dataset.relativePath.split("/"));
      if (path === root || !path.startsWith(`${root}\\`) && !path.startsWith(`${root}/`)) fail(`Data Lab dataset ${dataset.datasetId} escaped its declared input`);
      const observed = await digestFile(path, dataset.maxBytes, `Data Lab dataset ${dataset.datasetId}`);
      if (observed.sha256 !== dataset.contentSha256) fail(`Data Lab dataset ${dataset.datasetId} differs from its immutable hash`);
      datasets.push(Object.freeze({ ...dataset, path, bytes: observed.bytes }));
    }
    return { ...prepared, datasets: Object.freeze(datasets) };
  } catch (error) {
    await rm(prepared.workspace.discardPath, { recursive: true, force: true });
    throw error;
  }
}

export async function prepareBuilderVerificationRecovery(options) {
  const context = options?.recovery;
  exactKeys(context, ["consumption", "checkpoint"], "Builder verification recovery context");
  const { consumption, checkpoint } = context;
  schema("recovery lease consumption", validateWorkConsumption(consumption));
  schema("recovery checkpoint", validateWorkCheckpoint(checkpoint));
  const issuedAt = Date.parse(options.lease?.issuedAt);
  if (!Number.isFinite(issuedAt)) fail("Builder recovery lease issuance is invalid");
  const prepared = await prepareProfileRun({ ...options, now: new Date(issuedAt) }, "builder");
  try {
    if (
      checkpoint.state !== "verifying" || checkpoint.iteration !== prepared.lease.iteration
      || checkpoint.jobId !== prepared.plan.jobId || checkpoint.planSha256 !== prepared.bindings.planSha256
      || checkpoint.inputSetSha256 !== prepared.bindings.inputSetSha256 || checkpoint.objectiveSha256 !== sha(prepared.plan.objective)
      || checkpoint.acceptanceCriteriaSha256 !== sha(prepared.plan.acceptanceCriteria)
      || checkpoint.workerSessionSha256 !== sha(consumption) || checkpoint.artifactManifestSha256 === null
      || checkpoint.verificationEvidenceSha256 !== null || checkpoint.authorityExpansionObserved
      || checkpoint.acceptanceCriteriaMutationObserved || checkpoint.externalEffectsObserved
    ) fail("Builder recovery checkpoint differs from the pending verified boundary");
    if (
      consumption.status !== "consumed" || consumption.externalEffects !== false || consumption.jobId !== prepared.plan.jobId
      || consumption.leaseId !== prepared.lease.leaseId || consumption.leaseSha256 !== prepared.bindings.leaseSha256
      || consumption.planSha256 !== prepared.bindings.planSha256 || consumption.policySha256 !== prepared.bindings.policySha256
      || consumption.inputSetSha256 !== prepared.bindings.inputSetSha256 || consumption.workspaceSha256 !== prepared.workspace.sha256
      || Date.parse(consumption.claimedAt) > Date.parse(checkpoint.createdAt)
    ) fail("Builder recovery consumption differs from the pending iteration");
    await validateStoredConsumption(options.stateRoot, prepared.lease, consumption);
    return { ...prepared, continuationBase: null, verificationOnly: true, recoveryConsumption: structuredClone(consumption) };
  } catch (error) {
    await rm(prepared.workspace.discardPath, { recursive: true, force: true });
    throw error;
  }
}

async function prepareProfileCleanupRecovery(options, expectedProfile) {
  const label = expectedProfile === "builder" ? "Builder" : expectedProfile === "researcher" ? "Researcher" : expectedProfile === "data-lab" ? "Data Lab" : "Scout";
  const context = options?.recovery;
  exactKeys(context, ["consumption", "checkpoint"], `${label} cleanup recovery context`);
  const { consumption, checkpoint } = context;
  schema("recovery lease consumption", validateWorkConsumption(consumption));
  schema("recovery checkpoint", validateWorkCheckpoint(checkpoint));
  const issuedAt = Date.parse(options.lease?.issuedAt);
  if (!Number.isFinite(issuedAt)) fail(`${label} recovery lease issuance is invalid`);
  const { plan, lease, policy } = options;
  const bindings = verifyProfileBindings(plan, lease, policy, expectedProfile, { now: new Date(issuedAt) });
  if (
    !["running", "cleanup-failed"].includes(checkpoint.state) || checkpoint.iteration !== lease.iteration
    || expectedProfile !== "builder" && lease.iteration !== 1
    || checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== bindings.planSha256
    || checkpoint.inputSetSha256 !== bindings.inputSetSha256 || checkpoint.objectiveSha256 !== sha(plan.objective)
    || checkpoint.acceptanceCriteriaSha256 !== sha(plan.acceptanceCriteria)
    || checkpoint.workerSessionSha256 !== sha(consumption) || checkpoint.artifactManifestSha256 !== null
    || checkpoint.verificationEvidenceSha256 !== null || checkpoint.authorityExpansionObserved
    || checkpoint.acceptanceCriteriaMutationObserved || checkpoint.externalEffectsObserved
  ) fail(`${label} cleanup recovery checkpoint differs from the interrupted worker boundary`);
  if (
    consumption.status !== "consumed" || consumption.externalEffects !== false || consumption.jobId !== plan.jobId
    || consumption.leaseId !== lease.leaseId || consumption.leaseSha256 !== bindings.leaseSha256
    || consumption.planSha256 !== bindings.planSha256 || consumption.policySha256 !== bindings.policySha256
    || consumption.inputSetSha256 !== bindings.inputSetSha256
    || Date.parse(consumption.claimedAt) > Date.parse(checkpoint.createdAt)
  ) fail(`${label} cleanup recovery consumption differs from the interrupted iteration`);
  await validateStoredConsumption(options.stateRoot, lease, consumption);
  return {
    plan,
    lease,
    policy,
    bindings,
    workspace: { sha256: consumption.workspaceSha256 },
    ...(expectedProfile === "builder" ? { continuationBase: null } : {}),
    cleanupOnly: true,
    recoveryConsumption: structuredClone(consumption),
  };
}

export async function prepareBuilderCleanupRecovery(options) {
  return prepareProfileCleanupRecovery(options, "builder");
}

export async function prepareScoutCleanupRecovery(options) {
  return prepareProfileCleanupRecovery(options, "scout");
}

export async function prepareResearcherCleanupRecovery(options) {
  return prepareProfileCleanupRecovery(options, "researcher");
}

export async function prepareDataLabCleanupRecovery(options) {
  return prepareProfileCleanupRecovery(options, "data-lab");
}

export function createLeaseConsumption(prepared, options = {}) {
  if (prepared?.verificationOnly === true || prepared?.cleanupOnly === true) fail("recovery-only preparation cannot consume a lease");
  const now = options.now ?? new Date();
  const suffix = options.suffix ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/.test(suffix)) fail("claim suffix is invalid");
  const nowMs = now.getTime();
  if (nowMs < Date.parse(prepared.lease.issuedAt) || nowMs >= Date.parse(prepared.lease.expiresAt)) fail("lease expired before consumption");
  const claim = {
    $schema: "https://osmantic.com/pixel/schemas/work-lease-consumption-v1.schema.json",
    schemaVersion: 1,
    claimId: `workclaim-${String(nowMs).padStart(13, "0")}-${suffix}`,
    jobId: prepared.plan.jobId,
    leaseId: prepared.lease.leaseId,
    claimedAt: now.toISOString().replace(/\.000Z$/, "Z"),
    planSha256: prepared.bindings.planSha256,
    leaseSha256: prepared.bindings.leaseSha256,
    policySha256: prepared.bindings.policySha256,
    inputSetSha256: prepared.bindings.inputSetSha256,
    workspaceSha256: prepared.workspace.sha256,
    executor: { ...prepared.plan.executor },
    model: { ...prepared.plan.model },
    runnerImageDigest: prepared.plan.isolation.runnerImageDigest,
    singleUse: true,
    status: "consumed",
    externalEffects: false,
    authority: { ...authority },
    boundary: consumptionBoundary,
  };
  schema("lease consumption", validateWorkConsumption(claim));
  return claim;
}

export function createLeaseRevocation({
  plan, lease, goalId, goalCheckpointSha256, reviewedChildCheckpointSha256 = null, cancellationReviewSha256,
}, options = {}) {
  schema("work plan", validateWorkPlan(plan));
  schema("work lease", validateWorkLease(lease));
  schema("plan/lease", validatePlanLease(plan, lease));
  const now = options.now ?? new Date();
  const suffix = options.suffix ?? randomBytes(6).toString("hex");
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < Date.parse(lease.issuedAt) || !/^[a-f0-9]{12}$/.test(suffix)) fail("lease revocation identity input is invalid");
  const revocation = {
    $schema: "https://osmantic.com/pixel/schemas/work-lease-revocation-v1.schema.json",
    schemaVersion: 1,
    revocationId: `workrevocation-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    goalId, jobId: plan.jobId, leaseId: lease.leaseId,
    revokedAt: now.toISOString().replace(/\.000Z$/, "Z"),
    goalCheckpointSha256, reviewedChildCheckpointSha256, cancellationReviewSha256,
    planSha256: sha(plan), leaseSha256: sha(lease), reason: "operator-cancel-before-launch",
    singleUse: true, status: "revoked", externalEffects: false,
    authority: { ...authority }, boundary: revocationBoundary,
  };
  schema("lease revocation", validateWorkLeaseRevocation(revocation));
  return revocation;
}

async function claimLeaseDisposition(stateRoot, leaseId, value) {
  const state = await privateDirectory(stateRoot, "private runner state");
  const claimsPath = join(state, "claims");
  await mkdir(claimsPath, { mode: 0o700 }).catch((error) => {
    if (error.code !== "EEXIST") throw error;
  });
  await privateDirectory(claimsPath, "private lease claims");
  const destination = join(claimsPath, `${leaseId}.json`);
  const temporary = join(claimsPath, `.claim-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, destination).catch((error) => {
      if (error.code === "EEXIST") fail("lease was already consumed or revoked");
      throw error;
    });
  } finally {
    await unlink(temporary).catch(() => {});
  }
  if (process.platform !== "win32") {
    const directory = await open(claimsPath, constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }
  return destination;
}

export async function claimLease(stateRoot, claim) {
  schema("lease consumption", validateWorkConsumption(claim));
  return claimLeaseDisposition(stateRoot, claim.leaseId, claim);
}

export async function revokeLease(stateRoot, revocation) {
  schema("lease revocation", validateWorkLeaseRevocation(revocation));
  return claimLeaseDisposition(stateRoot, revocation.leaseId, revocation);
}

export async function inspectLeaseDisposition(stateRoot, lease) {
  schema("work lease", validateWorkLease(lease));
  const state = await privateDirectory(stateRoot, "private runner state");
  const claimsPath = join(state, "claims");
  const claimsInfo = await lstat(claimsPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (claimsInfo === null) return null;
  await privateDirectory(claimsPath, "private lease claims");
  const path = join(claimsPath, `${lease.leaseId}.json`);
  const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (info === null) return null;
  const { text, details } = await readBoundedRegularText(path, 64 * 1024, "lease disposition tombstone");
  if (details.nlink !== 1 || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))) {
    fail("lease disposition tombstone is not private and single-link");
  }
  let disposition;
  try { disposition = JSON.parse(text); } catch { fail("lease disposition tombstone is not JSON"); }
  if (disposition?.$schema === "https://osmantic.com/pixel/schemas/work-lease-consumption-v1.schema.json") {
    schema("lease consumption", validateWorkConsumption(disposition));
    if (disposition.leaseId !== lease.leaseId || disposition.leaseSha256 !== sha(lease) || disposition.status !== "consumed") fail("lease consumption tombstone differs from the exact lease");
    return Object.freeze({ kind: "consumed", record: disposition });
  }
  schema("lease revocation", validateWorkLeaseRevocation(disposition));
  if (disposition.leaseId !== lease.leaseId || disposition.jobId !== lease.jobId || disposition.leaseSha256 !== sha(lease) || disposition.status !== "revoked") fail("lease revocation tombstone differs from the exact lease");
  return Object.freeze({ kind: "revoked", record: disposition });
}

export async function recoverLeaseConsumption(stateRoot, lease) {
  const disposition = await inspectLeaseDisposition(stateRoot, lease);
  if (disposition?.kind !== "consumed") fail(`lease consumption tombstone is ${disposition?.kind ?? "absent"}`);
  return disposition.record;
}

export async function recoverLeaseRevocation(stateRoot, lease) {
  const disposition = await inspectLeaseDisposition(stateRoot, lease);
  if (disposition?.kind !== "revoked") fail(`lease revocation tombstone is ${disposition?.kind ?? "absent"}`);
  return disposition.record;
}

export async function discardPreparedRun(prepared) {
  const path = resolve(prepared?.workspace?.discardPath ?? prepared?.workspace?.path ?? "");
  const root = resolve(prepared?.workspace?.root ?? "");
  if (resolve(path, "..") !== root || !basename(path).startsWith(".prepare-work-")) fail("refusing to discard an unrecognized workspace");
  await privateDirectory(root, "private workspace root");
  await rm(path, { recursive: true, force: true });
}

export const runnerInternals = Object.freeze({ sha });

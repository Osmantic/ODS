import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  validateJobLease,
  validateWorkCheckpoint,
  validateWorkJob,
  validateWorkLease,
  validateWorkConsumption,
  validateWorkPlan,
  validateWorkPolicy,
  validateWorkResult,
} from "../scripts/lib/work-contract.mjs";

const hash = (character) => character.repeat(64);

function budgets(overrides = {}) {
  return {
    maxRuntimeSeconds: 3600,
    maxIterations: 20,
    maxToolCalls: 2000,
    maxConcurrentSubagents: 4,
    maxModelRequests: 200,
    maxInputTokens: 1000000,
    maxOutputTokens: 200000,
    maxCpuCores: 4,
    maxMemoryMiB: 8192,
    maxDiskBytes: 10737418240,
    maxArtifactBytes: 1073741824,
    maxNetworkBytes: 104857600,
    maxFailures: 5,
    noProgressLimit: 3,
    ...overrides,
  };
}

function job() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json",
    schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456",
    createdAt: "2026-08-10T13:00:00Z",
    requester: "pixel",
    profile: "builder",
    objective: "Repair the fixture and make its acceptance suite pass.",
    acceptanceCriteria: ["The fixture tests pass", "The returned patch contains only workspace changes"],
    verification: {
      mode: "independent",
      checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [1] },
        { id: "fixture-tests", kind: "command", criterionIndexes: [0], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 300, maxOutputBytes: 1048576 },
      ],
      immutablePathPrefixes: [], maxRuntimeSeconds: 300, maxOutputBytes: 1048576, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    dataClassification: "internal",
    inputs: [{
      id: "source",
      kind: "repository-snapshot",
      mountMode: "read-only",
      contentSha256: hash("a"),
      maxBytes: 104857600,
      classification: "internal",
    }],
    requestedCapabilities: {
      filesystem: "disposable-read-write",
      tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model", "package-courier"] },
      modelRoute: "local-only",
      hostAccess: false,
      ambientCredentials: false,
      externalEffects: false,
      mergeAuthority: false,
      deployAuthority: false,
      policyMutation: false,
    },
    budgets: budgets(),
    outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function lease() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v1.schema.json",
    schemaVersion: 1,
    leaseId: "worklease-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z",
    expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true,
    iteration: 1,
    continuation: null,
    planSha256: hash("b"),
    inputSetSha256: hash("c"),
    policySha256: hash("d"),
    executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: hash("e"), rpcProtocolVersion: 2 },
    model: { route: "local-only", provider: "llama.cpp", id: "assistant-model", backendImageDigest: `sha256:${hash("9")}`, contextWindow: 131072, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
    isolation: {
      mode: "ephemeral-vm",
      runnerImageDigest: `sha256:${hash("f")}`,
      freshHome: true,
      inheritEnvironment: false,
      inheritFileDescriptors: false,
      inputMount: "read-only",
      workspaceMount: "disposable-read-write",
      artifactMount: "write-only-staging",
      destroyAfterRun: true,
    },
    grantedCapabilities: {
      tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model", "package-courier"] },
    },
    budgets: budgets(),
    authority: {
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
    },
    outputGate: {
      allowedKinds: ["patch", "test-evidence"],
      independentVerificationRequired: true,
      automaticMerge: false,
      automaticDeployment: false,
    },
    boundary: "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.",
  };
}

function checkpoint() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-checkpoint-v1.schema.json",
    schemaVersion: 1,
    checkpointId: "workcheckpoint-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    sequence: 0,
    createdAt: "2026-08-10T13:00:00Z",
    previousCheckpointSha256: null,
    planSha256: hash("b"),
    inputSetSha256: hash("c"),
    objectiveSha256: hash("d"),
    acceptanceCriteriaSha256: hash("e"),
    state: "authorized",
    iteration: 0,
    usage: { runtimeSeconds: 0, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 0 },
    progress: { criteriaTotal: 2, criteriaPassing: 0, criteriaFailing: 2, noProgressCount: 0, failureFingerprintSha256: null },
    workspaceSnapshotSha256: hash("f"),
    artifactManifestSha256: null,
    workerSessionSha256: null,
    verificationEvidenceSha256: null,
    authorityExpansionObserved: false,
    acceptanceCriteriaMutationObserved: false,
    externalEffectsObserved: false,
    boundary: "Private hash-bound recovery record. Contains only immutable digests, bounded counters, and transition facts. Cannot authorize or replay actions.",
  };
}

function plan() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-plan-v1.schema.json",
    schemaVersion: 1,
    planId: "workplan-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    compiledAt: "2026-08-10T13:00:00Z",
    requestSha256: hash("a"),
    policySha256: hash("b"),
    inputSetSha256: hash("c"),
    profile: "scout",
    dataClassification: "internal",
    objective: "Inspect the fixture without changing it.",
    acceptanceCriteria: ["Return a finding report"],
    inputs: [{ id: "source", kind: "repository-snapshot", objectName: `${hash("d")}.tar`, contentSha256: hash("d"), bytes: 4096, classification: "internal", mountMode: "read-only" }],
    executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: hash("e"), rpcProtocolVersion: 2 },
    model: { route: "local-only", provider: "llama.cpp", id: "assistant-model", backendImageDigest: `sha256:${hash("9")}`, contextWindow: 131072, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
    isolation: {
      mode: "hardened-container", runnerImageDigest: `sha256:${hash("f")}`,
      freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false,
      inputMount: "read-only", workspaceMount: "read-only", artifactMount: "write-only-staging", destroyAfterRun: true,
    },
    grantedCapabilities: { tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] } },
    budgets: budgets(),
    outputGate: { allowedKinds: ["finding-report"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false },
    authority: {
      hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false,
      merge: false, deploy: false, publish: false, purchase: false, policyMutation: false,
      acceptanceCriteriaMutation: false, leaseExpansion: false,
    },
    status: "compiled",
    boundary: "Private immutable Deep Work plan. It authorizes no execution by itself and contains no credential, host path, provider secret, merge, deployment, publication, purchase, production, or external-effect authority.",
  };
}

function result() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-result-v1.schema.json",
    schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456",
    completedAt: "2026-08-10T13:30:00Z",
    status: "pass",
    planSha256: hash("b"),
    inputSetSha256: hash("c"),
    finalCheckpointSha256: hash("d"),
    workspaceSha256: hash("e"),
    acceptance: { criteriaSha256: hash("f"), total: 2, passing: 2, failing: 0, independentlyVerified: true, evidenceSha256: hash("a") },
    artifacts: [{ id: "change", kind: "patch", sha256: hash("b"), bytes: 100, mediaType: "text/x-diff" }],
    usage: { runtimeSeconds: 1800, iterations: 4, modelRequests: 20, inputTokens: 100000, outputTokens: 20000, networkBytes: 1000, artifactBytes: 100, failures: 1 },
    safety: {
      hostEscapeObserved: false,
      credentialAccessObserved: false,
      directNetworkObserved: false,
      externalEffectObserved: false,
      policyMutationObserved: false,
      acceptanceMutationObserved: false,
      deniedAttemptCount: 2,
      evidenceSha256: hash("c"),
    },
    authority: { mergePerformed: false, deploymentPerformed: false, publicationPerformed: false, purchasePerformed: false, externalMessagesSent: false },
    boundary: "Untrusted worker output plus independent content-free verification evidence. This result grants no merge, deployment, publication, purchase, external-message, credential, production, or policy authority.",
  };
}

function consumption() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-lease-consumption-v1.schema.json",
    schemaVersion: 1,
    claimId: "workclaim-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    leaseId: "worklease-1786366800000-abcdef123456",
    claimedAt: "2026-08-10T13:00:00Z",
    planSha256: hash("a"),
    leaseSha256: hash("b"),
    policySha256: hash("c"),
    inputSetSha256: hash("d"),
    workspaceSha256: hash("e"),
    executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: hash("f"), rpcProtocolVersion: 2 },
    model: { route: "local-only", provider: "llama.cpp", id: "assistant-model", backendImageDigest: `sha256:${hash("9")}`, contextWindow: 131072, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
    runnerImageDigest: `sha256:${hash("a")}`,
    singleUse: true,
    status: "consumed",
    externalEffects: false,
    authority: {
      hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false,
      merge: false, deploy: false, publish: false, purchase: false, policyMutation: false,
      acceptanceCriteriaMutation: false, leaseExpansion: false,
    },
    boundary: "Private immutable tombstone proving one lease was consumed once. It grants no replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.",
  };
}

test("Deep Work job contract preserves broad local capability without authority", () => {
  const value = job();
  assert.deepEqual(validateWorkJob(value), []);
  for (const mutate of [
    (copy) => { copy.requestedCapabilities.hostAccess = true; },
    (copy) => { copy.requestedCapabilities.ambientCredentials = true; },
    (copy) => { copy.requestedCapabilities.externalEffects = true; },
    (copy) => { copy.requestedCapabilities.network = { mode: "none", services: ["local-model"] }; },
    (copy) => { copy.dataClassification = "restricted"; copy.requestedCapabilities.modelRoute = "explicit-remote"; },
    (copy) => { copy.inputs[0].classification = "restricted"; },
    (copy) => { copy.inputs[0].path = "/home/operator/private"; },
    (copy) => { copy.requestedCapabilities.filesystem = "read-only-workspace"; },
    (copy) => { copy.inputs.push({ ...copy.inputs[0], contentSha256: hash("b") }); },
    (copy) => { copy.outputs.requiredKinds = []; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkJob(hostile).length > 0, JSON.stringify(hostile));
  }
  const scout = structuredClone(value);
  scout.profile = "scout";
  scout.requestedCapabilities.filesystem = "read-only-workspace";
  scout.requestedCapabilities.tools = ["read", "search"];
  scout.requestedCapabilities.network = { mode: "brokered", services: ["local-model"] };
  scout.outputs = { mode: "analysis", requiredKinds: ["finding-report"] };
  assert.deepEqual(validateWorkJob(scout), []);
  scout.requestedCapabilities.tools.push("bash");
  assert.ok(validateWorkJob(scout).length > 0);
  scout.requestedCapabilities.tools.pop();
  scout.requestedCapabilities.network.services.push("research-broker");
  assert.ok(validateWorkJob(scout).length > 0);
});

test("Builder verification recipe covers every criterion and cannot escape declared inputs", () => {
  const value = job();
  assert.deepEqual(validateWorkJob(value), []);
  for (const mutate of [
    (copy) => { copy.verification.checks = copy.verification.checks.filter((check) => check.kind !== "patch-integrity"); },
    (copy) => { copy.verification.checks[1].id = copy.verification.checks[0].id; },
    (copy) => { copy.verification.checks[0].criterionIndexes = [1]; copy.verification.checks[1].criterionIndexes = [1]; },
    (copy) => { copy.verification.checks[1].workingDirectory = "undeclared"; },
    (copy) => { copy.verification.immutablePathPrefixes = ["outside/"]; },
    (copy) => { copy.verification.checks[1].argv[1] = "bad\nargument"; },
    (copy) => { copy.verification.maxRuntimeSeconds = 1; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkJob(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Researcher contract is public-only, bounded, citation-verified, and brokered", () => {
  const value = job();
  value.profile = "researcher";
  value.dataClassification = "public";
  value.inputs[0].classification = "public";
  value.requestedCapabilities = {
    filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
    network: { mode: "brokered", services: ["local-model", "research-broker"] }, modelRoute: "local-only",
    hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
    deployAuthority: false, policyMutation: false,
  };
  value.outputs = { mode: "artifacts", requiredKinds: ["finding-report", "document"] };
  delete value.verification;
  value.research = {
    mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 10, maxResultsPerQuery: 10,
    maxSources: 50, maxSourceBytes: 1048576, maxTotalSourceBytes: 16777216, safeSearch: "strict",
    allowedDomains: ["example.com"], deniedDomains: ["tracking.example"],
    sourceTypes: ["web", "news", "academic", "forum"], citationVerification: true, retention: "job-only",
    boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
  };
  assert.deepEqual(validateWorkJob(value), []);
  for (const mutate of [
    (copy) => { copy.dataClassification = "internal"; },
    (copy) => { copy.requestedCapabilities.network.services = ["local-model"]; },
    (copy) => { copy.requestedCapabilities.tools = ["read", "search"]; },
    (copy) => { copy.research.maxSources = 101; },
    (copy) => { copy.research.maxTotalSourceBytes = 1024; },
    (copy) => { copy.research.deniedDomains = ["example.com"]; },
    (copy) => { copy.research.allowedDomains = ["z.example", "a.example"]; },
    (copy) => { copy.research.sourceTypes = ["forum", "web"]; },
    (copy) => { copy.research.queryPolicy = "arbitrary"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkJob(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Data Lab contract keeps exact local datasets read-only and derived outputs replay-gated", () => {
  const value = job();
  value.profile = "data-lab";
  value.inputs[0].kind = "dataset";
  value.requestedCapabilities = {
    filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
    network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
    hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
    deployAuthority: false, policyMutation: false,
  };
  value.outputs = { mode: "artifacts", requiredKinds: ["finding-report", "dataset", "document", "visualization"] };
  delete value.verification;
  value.data = {
    mode: "local-reproducible",
    datasets: [{ datasetId: "sales", inputId: "source", relativePath: "sales.csv", format: "csv", contentSha256: hash("b"), maxBytes: 1048576 }],
    engines: ["duckdb", "polars", "python", "sqlite"], maxArtifactFiles: 32, maxArtifactBytes: 16777216,
    allowedArtifactFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"],
    replayVerification: true, retention: "job-only",
    boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
  };
  assert.deepEqual(validateWorkJob(value), []);
  for (const mutate of [
    (copy) => { copy.inputs[0].kind = "document"; },
    (copy) => { copy.data.datasets[0].relativePath = "../private.csv"; },
    (copy) => { copy.data.datasets[0].inputId = "missing"; },
    (copy) => { copy.data.datasets[0].format = "xlsx"; },
    (copy) => { copy.data.maxArtifactBytes = copy.budgets.maxArtifactBytes + 1; },
    (copy) => { copy.data.replayVerification = false; },
    (copy) => { copy.requestedCapabilities.network.services.push("frontier-work-provider"); },
    (copy) => { copy.verification = job().verification; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkJob(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Deep Work lease is exact, single-use, isolated, and never self-expanding", () => {
  const value = lease();
  assert.deepEqual(validateWorkLease(value), []);
  assert.deepEqual(validateJobLease(job(), value), []);
  for (const mutate of [
    (copy) => { copy.singleUse = false; },
    (copy) => { copy.authority.hostAccess = true; },
    (copy) => { copy.authority.externalEffects = true; },
    (copy) => { copy.isolation.inheritEnvironment = true; },
    (copy) => { copy.outputGate.automaticMerge = true; },
    (copy) => { copy.iteration = 2; },
    (copy) => { copy.continuation = { previousCheckpointSha256: hash("a"), previousConsumptionSha256: hash("b"), cumulativeUsageSha256: hash("c") }; },
    (copy) => { copy.grantedCapabilities.network = { mode: "none", services: ["local-model"] }; },
    (copy) => { copy.expiresAt = "2026-08-18T13:00:00Z"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkLease(hostile).length > 0, JSON.stringify(hostile));
  }
  const widenedTool = lease();
  const narrowJob = job();
  narrowJob.requestedCapabilities.tools = ["read", "search"];
  assert.ok(validateJobLease(narrowJob, widenedTool).some((error) => error.includes("widens tool")));
  const widenedBudget = lease();
  widenedBudget.budgets.maxRuntimeSeconds += 1;
  assert.ok(validateJobLease(job(), widenedBudget).some((error) => error.includes("widens budget")));
});

test("Deep Work lease consumption is an immutable no-authority replay tombstone", () => {
  const value = consumption();
  assert.deepEqual(validateWorkConsumption(value), []);
  for (const mutate of [
    (copy) => { copy.singleUse = false; },
    (copy) => { copy.status = "available"; },
    (copy) => { copy.externalEffects = true; },
    (copy) => { copy.authority.leaseExpansion = true; },
    (copy) => { copy.retryAllowed = true; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkConsumption(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Deep Work private policy is disabled by default and binds exact OMP supply chain", async () => {
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  assert.deepEqual(validateWorkPolicy(policy), []);
  const localExactImages = structuredClone(policy);
  localExactImages.runner.imageRef = localExactImages.runner.imageDigest;
  localExactImages.localModel.imageRef = localExactImages.localModel.imageDigest;
  assert.deepEqual(validateWorkPolicy(localExactImages), []);
  const unprepared = structuredClone(policy);
  unprepared.enabled = true;
  unprepared.profiles.scout.enabled = true;
  assert.ok(validateWorkPolicy(unprepared).length > 0);
  const weakened = structuredClone(policy);
  weakened.security.projectEnvDiscovery = true;
  assert.ok(validateWorkPolicy(weakened).length > 0);
  const mismatchedRelease = structuredClone(policy);
  mismatchedRelease.executor.version = "17.2.11";
  assert.ok(validateWorkPolicy(mismatchedRelease).some((error) => error.includes("release URL")));
  const mismatchedImage = structuredClone(policy);
  mismatchedImage.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@sha256:${hash("f")}`;
  assert.ok(validateWorkPolicy(mismatchedImage).some((error) => error.includes("image reference")));
  const mismatchedModel = structuredClone(policy);
  mismatchedModel.localModel.imageDigest = `sha256:${hash("f")}`;
  assert.ok(validateWorkPolicy(mismatchedModel).some((error) => error.includes("localModel.imageRef")));
  const oversizedBuilderPatch = structuredClone(policy);
  oversizedBuilderPatch.profiles.builder.maxBudgets.maxArtifactBytes = 268435457;
  assert.ok(validateWorkPolicy(oversizedBuilderPatch).some((error) => error.includes("1..256 MiB")));
  const starvedBuilderPatch = structuredClone(policy);
  starvedBuilderPatch.profiles.builder.maxBudgets.maxArtifactBytes = 1048575;
  assert.ok(validateWorkPolicy(starvedBuilderPatch).some((error) => error.includes("1..256 MiB")));
  const unverifiedBuilder = structuredClone(policy);
  unverifiedBuilder.enabled = true;
  unverifiedBuilder.profiles.scout.enabled = true;
  unverifiedBuilder.profiles.builder.enabled = true;
  assert.ok(validateWorkPolicy(unverifiedBuilder).some((error) => error.includes("verifier.enabled")));
});

test("Deep Work Scout plans contain logical immutable inputs and no host path", () => {
  const value = plan();
  assert.deepEqual(validateWorkPlan(value), []);
  for (const mutate of [
    (copy) => { copy.inputs[0].objectName = `${hash("a")}.tar`; },
    (copy) => { copy.inputs[0].hostPath = "/srv/client/private"; },
    (copy) => { copy.inputs[0].classification = "restricted"; },
    (copy) => { copy.grantedCapabilities.tools.push("bash"); },
    (copy) => { copy.authority.externalEffects = true; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkPlan(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Deep Work checkpoints cannot rewrite goals or counterfeit completion", () => {
  const value = checkpoint();
  assert.deepEqual(validateWorkCheckpoint(value), []);
  const resumed = structuredClone(value);
  resumed.sequence = 1;
  resumed.iteration = 1;
  resumed.previousCheckpointSha256 = hash("a");
  resumed.state = "running";
  resumed.workerSessionSha256 = hash("9");
  assert.deepEqual(validateWorkCheckpoint(resumed), []);
  for (const mutate of [
    (copy) => { copy.authorityExpansionObserved = true; },
    (copy) => { copy.acceptanceCriteriaMutationObserved = true; },
    (copy) => { copy.previousCheckpointSha256 = hash("a"); },
    (copy) => { copy.progress.criteriaPassing = 1; },
    (copy) => { copy.state = "completed"; },
    (copy) => { copy.prompt = "hidden worker transcript"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkCheckpoint(hostile).length > 0, JSON.stringify(hostile));
  }
});

test("Deep Work results require independent evidence and carry no action authority", () => {
  const value = result();
  assert.deepEqual(validateWorkResult(value), []);
  for (const mutate of [
    (copy) => { copy.acceptance.independentlyVerified = false; },
    (copy) => { copy.acceptance.failing = 1; },
    (copy) => { copy.safety.credentialAccessObserved = true; },
    (copy) => { copy.authority.mergePerformed = true; },
    (copy) => { copy.artifacts = []; },
    (copy) => { copy.usage.artifactBytes = 99; },
    (copy) => { copy.artifacts.push({ ...copy.artifacts[0], sha256: hash("d") }); },
    (copy) => { copy.workerOutput = "unbounded content"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.ok(validateWorkResult(hostile).length > 0, JSON.stringify(hostile));
  }
  const incident = result();
  incident.status = "failed";
  incident.acceptance = { ...incident.acceptance, passing: 1, failing: 1, independentlyVerified: false };
  incident.safety.credentialAccessObserved = true;
  assert.deepEqual(validateWorkResult(incident), []);
});

test("Deep Work threat catalog covers every release-blocking boundary", async () => {
  const assurance = JSON.parse(await readFile(new URL("../security-evals/assurance/cases.json", import.meta.url), "utf8"));
  const ids = new Set(assurance.cases.map((item) => item.id));
  const required = [
    "work.request-authority", "work.lease-replay", "work.input-toctou",
    "work.runner-sterile", "work.filesystem-escape", "work.config-injection",
    "work.credential-canary", "work.network-exfiltration", "work.resource-abuse",
    "work.cancel-orphans", "work.checkpoint-recovery", "work.verifier-independence",
    "work.artifact-gate", "work.research-boundary", "work.remote-egress",
    "work.capability-retention",
  ];
  for (const id of required) assert.ok(ids.has(id), `missing ${id}`);
  assert.equal(ids.size, assurance.cases.length, "threat IDs must be unique");
});

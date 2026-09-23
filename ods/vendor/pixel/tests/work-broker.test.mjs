import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir, mkdtemp, readFile, symlink, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

import { compileBuilder, compileDataLab, compileResearcher, compileScout, WorkBrokerError } from "../deploy/work-broker/broker.mjs";
import { validateJobLease, validatePlanLease, validateWorkLease, validateWorkPlan } from "../scripts/lib/work-contract.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const broker = resolve(import.meta.dirname, "../deploy/work-broker/broker.mjs");
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

function budgets(overrides = {}) {
  return {
    maxRuntimeSeconds: 600,
    maxIterations: 5,
    maxToolCalls: 200,
    maxConcurrentSubagents: 1,
    maxModelRequests: 20,
    maxInputTokens: 100000,
    maxOutputTokens: 20000,
    maxCpuCores: 2,
    maxMemoryMiB: 2048,
    maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216,
    maxNetworkBytes: 10485760,
    maxFailures: 2,
    noProgressLimit: 2,
    ...overrides,
  };
}

function request(contentSha256, bytes) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json",
    schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456",
    createdAt: "2026-08-10T13:00:00Z",
    requester: "pixel",
    profile: "scout",
    objective: "Inspect the supplied fixture and report its invariant.",
    acceptanceCriteria: ["Return a finding report"],
    dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: 1048576, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace",
      tools: ["read", "search"],
      network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only",
      hostAccess: false,
      ambientCredentials: false,
      externalEffects: false,
      mergeAuthority: false,
      deployAuthority: false,
      policyMutation: false,
    },
    budgets: budgets(),
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

async function policy() {
  const value = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  value.enabled = true;
  value.runner.prepared = true;
  value.localModel.prepared = true;
  value.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  value.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${value.runner.imageDigest}`;
  value.profiles.scout.enabled = true;
  return value;
}

function manifest(job, contentSha256, bytes) {
  return {
    schemaVersion: 1,
    jobId: job.jobId,
    entries: [{ id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256, bytes, classification: "internal", mountMode: "read-only" }],
  };
}

function researchPolicy() {
  return {
    mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 10, maxResultsPerQuery: 10,
    maxSources: 50, maxSourceBytes: 1048576, maxTotalSourceBytes: 16777216, safeSearch: "strict",
    allowedDomains: [], deniedDomains: [], sourceTypes: ["web", "news", "academic", "forum"],
    citationVerification: true, retention: "job-only",
    boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
  };
}

function dataRuntime() {
  return {
    contract: "pixel-local-data-runtime-v1",
    engines: {
      duckdb: { version: "1.5.5", entrypoint: "/usr/bin/python3" },
      polars: { version: "1.43.2", entrypoint: "/usr/bin/python3" },
      python: { version: "3.11.2", entrypoint: "/usr/bin/python3" },
      sqlite: { version: "3.40.1", entrypoint: "/usr/bin/sqlite3" },
    },
  };
}

function enableDataLab(privatePolicy) {
  privatePolicy.profiles.dataLab = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "dataset", "document", "visualization"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
    runtime: dataRuntime(),
    maxData: {
      maxDatasets: 8, maxDatasetBytes: 1048576, maxArtifactFiles: 16, maxArtifactBytes: 8388608,
      allowedInputFormats: ["csv", "json", "jsonl", "parquet", "sqlite"],
      allowedArtifactFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"],
    },
  };
}

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-work-broker-"));
  const objects = join(root, "objects");
  const output = join(root, "output");
  await mkdir(objects, { mode: 0o700 });
  await mkdir(output, { mode: 0o700 });
  const bytes = Buffer.from("safe-scout-input\n", "utf8");
  const contentSha256 = hash(bytes);
  const job = request(contentSha256, bytes.length);
  const privatePolicy = await policy();
  const inputs = manifest(job, contentSha256, bytes.length);
  await writeFile(join(objects, `${contentSha256}.tar`), bytes, { mode: 0o600 });
  return { root, objects, output, bytes, contentSha256, job, privatePolicy, inputs };
}

test("Scout compiler produces an exact non-widening plan and lease", async () => {
  const value = await fixture();
  const compiled = compileScout(value.job, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  });
  assert.deepEqual(validateWorkPlan(compiled.plan), []);
  assert.deepEqual(validateWorkLease(compiled.lease), []);
  assert.deepEqual(validateJobLease(value.job, compiled.lease), []);
  assert.deepEqual(validatePlanLease(compiled.plan, compiled.lease), []);
  assert.equal(compiled.plan.executor.version, "17.2.12");
  assert.deepEqual(compiled.plan.model, {
    route: "local-only", provider: "llama.cpp", id: "assistant-model",
    backendImageDigest: "sha256:8b7f05d7d14d040463ef9191b24da93724574abd6bfea9bf12899100f62e98db",
    contextWindow: 32768, supportsVision: false,
    endpointContract: "llama.cpp-discovery-and-openai-stream-v1",
  });
  assert.deepEqual(compiled.lease.model, compiled.plan.model);
  const vllmPolicy = structuredClone(value.privatePolicy);
  vllmPolicy.localModel.provider = "vllm";
  vllmPolicy.localModel.imageRef = `local/pixel-vllm@${vllmPolicy.localModel.imageDigest}`;
  vllmPolicy.localModel.inference = fixtureVllmInferencePolicy();
  const vllmCompiled = compileScout(value.job, vllmPolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "abcdef123456",
  });
  assert.deepEqual(validateWorkPlan(vllmCompiled.plan), []);
  assert.deepEqual(validateWorkLease(vllmCompiled.lease), []);
  assert.equal(vllmCompiled.plan.model.provider, "vllm");
  assert.deepEqual(vllmCompiled.lease.model, vllmCompiled.plan.model);
  const largePhysicalContextPolicy = structuredClone(vllmPolicy);
  largePhysicalContextPolicy.localModel.contextWindow = 1048576;
  largePhysicalContextPolicy.localModel.maxRequestContextTokens = 32768;
  const largePhysicalContextCompiled = compileScout(value.job, largePhysicalContextPolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "fedcba654321",
  });
  assert.equal(largePhysicalContextCompiled.plan.model.contextWindow, 32768);
  assert.equal(largePhysicalContextCompiled.lease.model.contextWindow, 32768);
  const widenedRequestContextPolicy = structuredClone(largePhysicalContextPolicy);
  widenedRequestContextPolicy.localModel.maxRequestContextTokens = 1048577;
  assert.throws(() => compileScout(value.job, widenedRequestContextPolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "fedcba654322",
  }), /request context exceeds the physical backend context window/u);
  const reducedLease = structuredClone(compiled.lease);
  reducedLease.budgets.maxRuntimeSeconds -= 1;
  reducedLease.budgets.maxIterations -= 1;
  assert.deepEqual(validatePlanLease(compiled.plan, reducedLease), []);
  const widenedLease = structuredClone(compiled.lease);
  widenedLease.budgets.maxRuntimeSeconds += 1;
  assert.ok(validatePlanLease(compiled.plan, widenedLease).some((error) => error.includes("widens budget maxRuntimeSeconds")));
  assert.equal(compiled.plan.isolation.workspaceMount, "read-only");
  assert.deepEqual(compiled.plan.grantedCapabilities.tools, ["read", "search"]);
  assert.deepEqual(compiled.plan.grantedCapabilities.network.services, ["local-model"]);
  assert.equal(compiled.lease.expiresAt, "2026-08-10T13:10:00Z");
  assert.doesNotMatch(JSON.stringify(compiled), /(?:\/home\/|\/srv\/|docker\.sock|SSH_AUTH_SOCK|sk-[A-Za-z0-9_-]{20,})/);
  const substitutedPlan = structuredClone(compiled.plan);
  substitutedPlan.objective = "Substituted after compilation";
  assert.ok(validatePlanLease(substitutedPlan, compiled.lease).some((error) => error.includes("plan hash")));
  const substitutedModelLease = structuredClone(compiled.lease);
  substitutedModelLease.model.id = "substituted-model";
  assert.ok(validatePlanLease(compiled.plan, substitutedModelLease).some((error) => error.includes("model differs")));
});

test("Scout compiler refuses disabled, widened, and over-budget work", async () => {
  const value = await fixture();
  const disabled = structuredClone(value.privatePolicy);
  disabled.enabled = false;
  assert.throws(() => compileScout(value.job, disabled, value.inputs.entries), WorkBrokerError);
  const overBudget = structuredClone(value.job);
  overBudget.budgets.maxRuntimeSeconds = value.privatePolicy.profiles.scout.maxBudgets.maxRuntimeSeconds + 1;
  assert.throws(() => compileScout(overBudget, value.privatePolicy, value.inputs.entries), /exceeds Scout budget/);
  const underMemory = structuredClone(value.job);
  underMemory.budgets.maxMemoryMiB = 1535;
  assert.throws(() => compileScout(underMemory, value.privatePolicy, value.inputs.entries), /Scout requires at least 1536 MiB of memory/);
  const widened = structuredClone(value.job);
  widened.requestedCapabilities.tools.push("bash");
  assert.throws(() => compileScout(widened, value.privatePolicy, value.inputs.entries), /work job failed validation/);
  const alteredManifest = structuredClone(value.inputs.entries);
  alteredManifest[0].objectName = `${"a".repeat(64)}.tar`;
  assert.throws(() => compileScout(value.job, value.privatePolicy, alteredManifest, { now: new Date("2026-08-10T13:00:00Z") }), /differs from the request/);
  const stale = structuredClone(value.job);
  stale.createdAt = "2026-08-10T11:00:00Z";
  assert.throws(() => compileScout(stale, value.privatePolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /stale/);
  const future = structuredClone(value.job);
  future.createdAt = "2026-08-10T13:05:01Z";
  assert.throws(() => compileScout(future, value.privatePolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /future/);
});

test("Builder compiler grants a disposable coding workspace but no action authority", async () => {
  const value = await fixture();
  value.job.profile = "builder";
  value.job.objective = "Repair the disposable fixture and return a patch.";
  value.job.acceptanceCriteria = ["The returned patch changes only the disposable workspace"];
  value.job.verification = {
    mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }],
    immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
  value.job.requestedCapabilities.filesystem = "disposable-read-write";
  value.job.requestedCapabilities.tools = ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"];
  value.job.outputs = { mode: "patch", requiredKinds: ["patch", "test-evidence"] };
  value.privatePolicy.profiles.builder.enabled = true;
  value.privatePolicy.verifier.enabled = true;
  const compiled = compileBuilder(value.job, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  });
  assert.deepEqual(validateWorkPlan(compiled.plan), []);
  assert.deepEqual(validateWorkLease(compiled.lease), []);
  assert.deepEqual(validateJobLease(value.job, compiled.lease), []);
  assert.deepEqual(validatePlanLease(compiled.plan, compiled.lease), []);
  assert.equal(compiled.plan.profile, "builder");
  assert.equal(compiled.plan.isolation.workspaceMount, "disposable-read-write");
  assert.deepEqual(compiled.plan.grantedCapabilities.tools, ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"]);
  assert.deepEqual(compiled.plan.outputGate.allowedKinds, ["patch", "test-evidence"]);
  assert.deepEqual(compiled.plan.verification, value.job.verification);
  assert.ok(Object.values(compiled.plan.authority).every((value_) => value_ === false));
  const underMemoryFloor = structuredClone(value.job);
  underMemoryFloor.budgets.maxMemoryMiB = 1024;
  assert.throws(() => compileBuilder(underMemoryFloor, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  }), /requires at least 2048 MiB of memory/);
  const courier = structuredClone(value.job);
  courier.requestedCapabilities.network.services.push("package-courier");
  assert.throws(() => compileBuilder(courier, value.privatePolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /Builder services differ/);
  const unapprovedExecutable = structuredClone(value.job);
  unapprovedExecutable.verification.checks.push({
    id: "python-test", kind: "command", criterionIndexes: [0], workingDirectory: "source",
    argv: ["/usr/bin/python3", "-m", "pytest"], timeoutSeconds: 60, maxOutputBytes: 65536,
  });
  unapprovedExecutable.verification.maxRuntimeSeconds = 120;
  unapprovedExecutable.verification.maxOutputBytes = 131072;
  assert.throws(() => compileBuilder(unapprovedExecutable, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  }), /not allowed/);
});

test("Researcher compiler grants only public sanitized brokered research", async () => {
  const value = await fixture();
  value.job.profile = "researcher";
  value.job.objective = "Research the public history of the fixture pattern with citations.";
  value.job.acceptanceCriteria = ["Return a source-backed report", "Every material claim has a verified citation"];
  value.job.dataClassification = "public";
  value.job.inputs[0].classification = "public";
  value.inputs.entries[0].classification = "public";
  value.job.requestedCapabilities = {
    filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
    network: { mode: "brokered", services: ["local-model", "research-broker"] }, modelRoute: "local-only",
    hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
    deployAuthority: false, policyMutation: false,
  };
  value.job.outputs = { mode: "artifacts", requiredKinds: ["finding-report", "document"] };
  value.job.research = researchPolicy();
  value.privatePolicy.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 20, maxResultsPerQuery: 20, maxSources: 200, maxSourceBytes: 2097152,
      maxTotalSourceBytes: 33554432, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  const compiled = compileResearcher(value.job, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  });
  assert.deepEqual(validateWorkPlan(compiled.plan), []);
  assert.deepEqual(validateWorkLease(compiled.lease), []);
  assert.deepEqual(validateJobLease(value.job, compiled.lease), []);
  assert.deepEqual(validatePlanLease(compiled.plan, compiled.lease), []);
  assert.equal(compiled.plan.profile, "researcher");
  assert.deepEqual(compiled.plan.researchBackend, {
    adapter: "reference", endpointContract: "pixel-public-research-broker-v1",
    queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
  });
  assert.equal(Object.hasOwn(compiled.plan.researchBackend, "prepared"), false);
  assert.deepEqual(compiled.lease.researchBackend, compiled.plan.researchBackend);
  assert.deepEqual(compiled.plan.research, value.job.research);
  assert.deepEqual(compiled.lease.research, value.job.research);
  assert.deepEqual(compiled.plan.grantedCapabilities.network.services, ["local-model", "research-broker"]);
  assert.ok(Object.values(compiled.plan.authority).every((value_) => value_ === false));

  const underMemory = structuredClone(value.job);
  underMemory.budgets.maxMemoryMiB = 1535;
  assert.throws(() => compileResearcher(underMemory, value.privatePolicy, value.inputs.entries), /Researcher requires at least 1536 MiB of memory/);

  const privateJob = structuredClone(value.job);
  privateJob.dataClassification = "internal";
  assert.throws(() => compileResearcher(privateJob, value.privatePolicy, value.inputs.entries), /work job failed validation/);
  const overLimit = structuredClone(value.job);
  overLimit.research.maxQueries = 21;
  assert.throws(() => compileResearcher(overLimit, value.privatePolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /exceeds Researcher maxQueries/);
  const disallowed = structuredClone(value.job);
  disallowed.research.sourceTypes = ["forum"];
  const restrictedPolicy = structuredClone(value.privatePolicy);
  restrictedPolicy.profiles.researcher.maxResearch.allowedSourceTypes = ["web", "news", "academic"];
  assert.throws(() => compileResearcher(disallowed, restrictedPolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /not allowed/);
});

test("Data Lab compiler binds read-only datasets and exact local replay runtime", async () => {
  const value = await fixture();
  const datasetSha256 = hash(Buffer.from("region,revenue\nwest,42\n", "utf8"));
  value.job.profile = "data-lab";
  value.job.objective = "Reproducibly summarize the local revenue dataset.";
  value.job.acceptanceCriteria = ["Return a replay-verified summary and derived dataset"];
  value.job.inputs[0].kind = "dataset";
  value.inputs.entries[0].kind = "dataset";
  value.job.requestedCapabilities = {
    filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
    network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
    hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
    deployAuthority: false, policyMutation: false,
  };
  value.job.outputs = { mode: "artifacts", requiredKinds: ["finding-report", "dataset", "document", "visualization"] };
  value.job.data = {
    mode: "local-reproducible",
    datasets: [{ datasetId: "revenue", inputId: "source", relativePath: "sales.csv", format: "csv", contentSha256: datasetSha256, maxBytes: 65536 }],
    engines: ["duckdb", "polars", "python", "sqlite"], maxArtifactFiles: 8, maxArtifactBytes: 4194304,
    allowedArtifactFormats: ["csv", "json", "markdown", "parquet", "png"], replayVerification: true, retention: "job-only",
    boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
  };
  enableDataLab(value.privatePolicy);
  const compiled = compileDataLab(value.job, value.privatePolicy, value.inputs.entries, {
    now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef",
  });
  assert.deepEqual(validateWorkPlan(compiled.plan), []);
  assert.deepEqual(validateWorkLease(compiled.lease), []);
  assert.deepEqual(validateJobLease(value.job, compiled.lease), []);
  assert.deepEqual(validatePlanLease(compiled.plan, compiled.lease), []);
  assert.equal(compiled.plan.profile, "data-lab");
  assert.deepEqual(compiled.plan.data, value.job.data);
  assert.deepEqual(compiled.plan.dataRuntime, dataRuntime());
  assert.deepEqual(compiled.lease.dataRuntime, compiled.plan.dataRuntime);
  assert.deepEqual(compiled.plan.grantedCapabilities.network.services, ["local-model"]);
  assert.ok(Object.values(compiled.plan.authority).every((value_) => value_ === false));

  const underMemory = structuredClone(value.job);
  underMemory.budgets.maxMemoryMiB = 1535;
  assert.throws(() => compileDataLab(underMemory, value.privatePolicy, value.inputs.entries), /Data Lab requires at least 1536 MiB of memory/);

  const widened = structuredClone(value.job);
  widened.data.maxArtifactFiles = 17;
  assert.throws(() => compileDataLab(widened, value.privatePolicy, value.inputs.entries, { now: new Date("2026-08-10T13:00:00Z") }), /exceeds Data Lab artifact limits/);
  const missingDataset = structuredClone(value.job);
  missingDataset.data.datasets[0].inputId = "missing";
  assert.throws(() => compileDataLab(missingDataset, value.privatePolicy, value.inputs.entries), /work job failed validation/);
  const remote = structuredClone(value.job);
  remote.requestedCapabilities.network.services.push("frontier-work-provider");
  assert.throws(() => compileDataLab(remote, value.privatePolicy, value.inputs.entries), /work job failed validation|Data Lab services differ/);
});

test("Work Broker CLI verifies content-addressed objects and publishes only a private bundle", async () => {
  const value = await fixture();
  value.job.createdAt = new Date().toISOString();
  const requestPath = join(value.root, "request.json");
  const policyPath = join(value.root, "policy.json");
  const inputsPath = join(value.root, "inputs.json");
  await writeFile(requestPath, `${JSON.stringify(value.job)}\n`, { mode: 0o600 });
  await writeFile(policyPath, `${JSON.stringify(value.privatePolicy)}\n`, { mode: 0o600 });
  await writeFile(inputsPath, `${JSON.stringify(value.inputs)}\n`, { mode: 0o600 });
  const arguments_ = [broker, "compile-scout", "--request", requestPath, "--policy", policyPath, "--inputs", inputsPath, "--object-store", value.objects, "--output", value.output];
  const first = spawnSync(process.execPath, arguments_, { encoding: "utf8", windowsHide: true });
  assert.equal(first.status, 0, first.stderr);
  const receipt = JSON.parse(first.stdout);
  assert.deepEqual(Object.keys(receipt).sort(), ["boundary", "externalEffects", "inputSetSha256", "jobId", "leaseId", "operation", "planSha256", "schemaVersion", "status"]);
  assert.equal(receipt.externalEffects, false);
  const plan = JSON.parse(await readFile(join(value.output, value.job.jobId, "plan.json"), "utf8"));
  const lease = JSON.parse(await readFile(join(value.output, value.job.jobId, "lease.json"), "utf8"));
  assert.deepEqual(validateWorkPlan(plan), []);
  assert.deepEqual(validateWorkLease(lease), []);
  const duplicate = spawnSync(process.execPath, arguments_, { encoding: "utf8", windowsHide: true });
  assert.notEqual(duplicate.status, 0);
  assert.match(duplicate.stderr, /already compiled/);
});

test("Work Broker CLI rejects substituted and linked input objects", async (context) => {
  const value = await fixture();
  value.job.createdAt = new Date().toISOString();
  const requestPath = join(value.root, "request.json");
  const policyPath = join(value.root, "policy.json");
  const inputsPath = join(value.root, "inputs.json");
  await writeFile(requestPath, `${JSON.stringify(value.job)}\n`, { mode: 0o600 });
  await writeFile(policyPath, `${JSON.stringify(value.privatePolicy)}\n`, { mode: 0o600 });
  await writeFile(inputsPath, `${JSON.stringify(value.inputs)}\n`, { mode: 0o600 });
  await writeFile(join(value.objects, `${value.contentSha256}.tar`), "substituted\n");
  const arguments_ = [broker, "compile-scout", "--request", requestPath, "--policy", policyPath, "--inputs", inputsPath, "--object-store", value.objects, "--output", value.output];
  const substituted = spawnSync(process.execPath, arguments_, { encoding: "utf8", windowsHide: true });
  assert.notEqual(substituted.status, 0);
  assert.match(substituted.stderr, /missing, linked, replaced, or outside/);
  if (process.platform === "win32") return context.skip("symlink creation is not generally available on Windows");
  const linked = await fixture();
  linked.job.createdAt = new Date().toISOString();
  const real = join(linked.root, "real.tar");
  await writeFile(real, linked.bytes, { mode: 0o600 });
  await writeFile(join(linked.root, "request.json"), `${JSON.stringify(linked.job)}\n`, { mode: 0o600 });
  await writeFile(join(linked.root, "policy.json"), `${JSON.stringify(linked.privatePolicy)}\n`, { mode: 0o600 });
  await writeFile(join(linked.root, "inputs.json"), `${JSON.stringify(linked.inputs)}\n`, { mode: 0o600 });
  await writeFile(join(linked.objects, `${linked.contentSha256}.tar`), "remove-me", { mode: 0o600 });
  await import("node:fs/promises").then(({ unlink }) => unlink(join(linked.objects, `${linked.contentSha256}.tar`)));
  await symlink(real, join(linked.objects, `${linked.contentSha256}.tar`));
  const symlinked = spawnSync(process.execPath, [broker, "compile-scout", "--request", join(linked.root, "request.json"), "--policy", join(linked.root, "policy.json"), "--inputs", join(linked.root, "inputs.json"), "--object-store", linked.objects, "--output", linked.output], { encoding: "utf8" });
  assert.notEqual(symlinked.status, 0);
  assert.match(symlinked.stderr, /missing, linked, replaced, or outside/);
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  access, chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder, compileDataLab, compileScout } from "../deploy/work-broker/broker.mjs";
import { issueContinuationLease } from "../deploy/work-controller/continuation.mjs";
import {
  appendCheckpoint, checkpointSha256, initializeCheckpointLedger,
} from "../deploy/work-controller/checkpoints.mjs";
import { exportBuilderPatch, initializeBuilderVolume } from "../deploy/work-runner/builder-volume.mjs";
import {
  claimLease, createLeaseConsumption, createLeaseRevocation, discardPreparedRun, inspectLeaseDisposition,
  prepareBuilderRun, prepareDataLabRun, prepareScoutRun, recoverLeaseConsumption, recoverLeaseRevocation, revokeLease,
  prepareBuilderCleanupRecovery, prepareBuilderVerificationRecovery, prepareScoutCleanupRecovery,
  verifyBuilderBindings, verifyDataLabBindings, verifyScoutBindings,
} from "../deploy/work-runner/runner-core.mjs";
import { validateWorkConsumption, validateWorkLeaseRevocation } from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

function octal(value, length) {
  return `${value.toString(8).padStart(length - 1, "0")}\0`;
}

function header(name, type, size) {
  const value = Buffer.alloc(512);
  value.write(name, 0, 100, "utf8");
  value.write(octal(type === "directory" ? 0o755 : 0o644, 8), 100, 8, "ascii");
  value.write(octal(0, 8), 108, 8, "ascii");
  value.write(octal(0, 8), 116, 8, "ascii");
  value.write(octal(size, 12), 124, 12, "ascii");
  value.write(octal(0, 12), 136, 12, "ascii");
  value.fill(0x20, 148, 156);
  value[156] = type === "directory" ? 0x35 : 0x30;
  value.write("ustar\0", 257, 6, "latin1");
  value.write("00", 263, 2, "ascii");
  let checksum = 0;
  for (const byte of value) checksum += byte;
  value.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  return value;
}

function archive(items) {
  const blocks = [];
  for (const item of items) {
    const content = Buffer.from(item.content ?? "", "utf8");
    blocks.push(header(item.name, item.type ?? "file", content.length));
    if (content.length > 0) {
      blocks.push(content);
      const padding = (512 - (content.length % 512)) % 512;
      if (padding) blocks.push(Buffer.alloc(padding));
    }
  }
  blocks.push(Buffer.alloc(1024));
  return Buffer.concat(blocks);
}

function budgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 5, maxToolCalls: 200, maxConcurrentSubagents: 1, maxModelRequests: 20,
    maxInputTokens: 100000, maxOutputTokens: 20000, maxCpuCores: 2, maxMemoryMiB: 2048,
    maxDiskBytes: 1073741824, maxArtifactBytes: 16777216, maxNetworkBytes: 10485760,
    maxFailures: 2, noProgressLimit: 2,
  };
}

async function fixture({ provider = "llama.cpp" } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-work-runner-"));
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const stateRoot = join(root, "state");
  await Promise.all([objectStore, workspaceRoot, stateRoot].map((path) => mkdir(path, { mode: 0o700 })));
  const bytes = archive([
    { name: "src/", type: "directory" },
    { name: "src/main.js", content: "export const answer = 42;\n" },
    { name: ".omp/tools/escape.js", content: "throw new Error('inert');\n" },
  ]);
  const contentSha256 = hash(bytes);
  await writeFile(join(objectStore, `${contentSha256}.tar`), bytes, { mode: 0o600 });
  const executorBytes = Buffer.from("fixture executor\n", "utf8");
  const executorPath = join(root, "omp-linux-x64");
  await writeFile(executorPath, executorBytes, { mode: 0o500 });
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.localModel.provider = provider;
  if (provider === "vllm") {
    policy.localModel.imageRef = `local/pixel-vllm@${policy.localModel.imageDigest}`;
    policy.localModel.inference = fixtureVllmInferencePolicy();
  }
  policy.profiles.scout.enabled = true;
  policy.executor.sha256 = hash(executorBytes);
  const now = new Date("2026-08-10T13:00:00Z");
  await installFixtureModelQualification(policy, root, now);
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json",
    schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456",
    createdAt: "2026-08-10T13:00:00Z",
    requester: "pixel",
    profile: "scout",
    objective: "Inspect the supplied fixture and report its invariant.",
    acceptanceCriteria: ["Return a finding report"],
    dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes.length, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
      deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(),
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
    bytes: bytes.length, classification: "internal", mountMode: "read-only",
  }];
  const compiled = compileScout(request, policy, entries, { now, suffix: "123456abcdef" });
  return { root, objectStore, workspaceRoot, stateRoot, executorPath, policy, request, entries, now, ...compiled };
}

test("Scout runner revalidates bindings and materializes only a normalized workspace", async () => {
  const value = await fixture();
  const prepared = await prepareScoutRun({
    ...value,
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
  });
  assert.equal(await readFile(join(prepared.workspace.path, "source/src/main.js"), "utf8"), "export const answer = 42;\n");
  assert.equal(await readFile(join(prepared.workspace.path, "source/__pixel_inert__/.omp/tools/escape.js"), "utf8"), "throw new Error('inert');\n");
  assert.equal(prepared.workspace.inputs[0].inertEntries, 1);
  assert.match(prepared.workspace.sha256, /^[a-f0-9]{64}$/);
  await discardPreparedRun(prepared);
  await assert.rejects(access(prepared.workspace.path));
});

test("vLLM policy, qualification, plan, lease, and fresh worker admission remain exactly bound", async () => {
  const value = await fixture({ provider: "vllm" });
  assert.equal(value.policy.localModel.provider, "vllm");
  assert.equal(value.plan.model.provider, "vllm");
  assert.equal(value.lease.model.provider, "vllm");
  const prepared = await prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  assert.equal(prepared.modelQualification.profile, "scout");
  assert.equal(prepared.plan.model.provider, "vllm");
  await discardPreparedRun(prepared);
});

test("Scout runner rejects expired, substituted, and unpinned execution", async () => {
  const value = await fixture();
  assert.throws(() => verifyScoutBindings(value.plan, value.lease, value.policy, { now: new Date("2026-08-10T13:10:00Z") }), /not currently valid/);
  const substituted = structuredClone(value.plan);
  substituted.objective = "substituted";
  assert.throws(() => verifyScoutBindings(substituted, value.lease, value.policy, { now: value.now }), /plan hash/);
  const substitutedModel = structuredClone(value.policy);
  substitutedModel.localModel.id = "substituted-model";
  assert.throws(() => verifyScoutBindings(value.plan, value.lease, substitutedModel, { now: value.now }), /plan hash|local model|policy hash/);
  await chmod(value.executorPath, 0o600);
  await writeFile(value.executorPath, "substituted executor\n");
  await chmod(value.executorPath, 0o500);
  await assert.rejects(prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } }), /executor SHA-256/);
});

test("fresh worker admission requires the exact private model qualification and measured envelope", async () => {
  const missing = await fixture();
  await rm(missing.policy.localModel.qualification.receiptPath);
  await assert.rejects(prepareScoutRun({ ...missing, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } }), /qualification receipt/);

  const tampered = await fixture();
  await writeFile(tampered.policy.localModel.qualification.receiptPath, "{}\n", { mode: 0o600 });
  await assert.rejects(prepareScoutRun({ ...tampered, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } }), /not currently qualified/);

  const widened = await fixture();
  widened.policy.localModel.maxRequestOutputTokens += 1;
  const compiled = compileScout(widened.request, widened.policy, widened.entries, { now: widened.now, suffix: "abcdef123456" });
  await assert.rejects(prepareScoutRun({
    ...widened, ...compiled, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
  }), /not currently qualified/);
});

test("Scout interrupted cleanup rehydrates only exact consumed authority and no launch workspace", async () => {
  const value = await fixture();
  const prepared = await prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(value.now.getTime() + 1), suffix: "000000000061",
  });
  const claim = createLeaseConsumption(prepared, { now: new Date(value.now.getTime() + 2), suffix: "000000000062" });
  await claimLease(value.stateRoot, claim);
  const running = await appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease,
    previousCheckpointSha256: checkpointSha256(initialized.checkpoint),
    update: {
      state: "running", iteration: 1, usage: initialized.checkpoint.usage, progress: initialized.checkpoint.progress,
      workspaceSnapshotSha256: initialized.checkpoint.workspaceSnapshotSha256, artifactManifestSha256: null,
      workerSessionSha256: checkpointSha256(claim), verificationEvidenceSha256: null,
      authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
    },
    now: new Date(value.now.getTime() + 3), suffix: "000000000063",
  });
  const cleanup = await prepareScoutCleanupRecovery({
    ...value, recovery: { consumption: claim, checkpoint: running.checkpoint },
  });
  assert.equal(cleanup.cleanupOnly, true);
  assert.deepEqual(cleanup.workspace, { sha256: prepared.workspace.sha256 });
  assert.equal(Object.hasOwn(cleanup, "executor"), false);
  assert.throws(() => createLeaseConsumption(cleanup, { now: value.now, suffix: "000000000064" }), /recovery-only/);
  await discardPreparedRun(prepared);
});

test("Builder runner reserves a separate volume target without exceeding the aggregate disk lease", async () => {
  const value = await fixture();
  const request = structuredClone(value.request);
  request.profile = "builder";
  request.requestedCapabilities.filesystem = "disposable-read-write";
  request.requestedCapabilities.tools = ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"];
  request.outputs = { mode: "patch", requiredKinds: ["patch", "test-evidence"] };
  request.verification = {
    mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }],
    immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
  const policy = structuredClone(value.policy);
  policy.profiles.builder.enabled = true;
  policy.verifier.enabled = true;
  const compiled = compileBuilder(request, policy, value.entries, { now: value.now, suffix: "123456abcdef" });
  const prepared = await prepareBuilderRun({
    ...value, ...compiled, policy,
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
  });
  assert.equal(prepared.workspace.disposable, true);
  assert.equal(prepared.workspace.storage, "docker-tmpfs-volume");
  assert.notEqual(prepared.workspace.path, prepared.workspace.originalPath);
  assert.deepEqual(await readdir(prepared.workspace.path), []);
  assert.equal(await readFile(join(prepared.workspace.originalPath, "source/src/main.js"), "utf8"), "export const answer = 42;\n");
  assert.doesNotThrow(() => verifyBuilderBindings(compiled.plan, compiled.lease, policy, { now: value.now }));
  await discardPreparedRun(prepared);
  await assert.rejects(access(prepared.workspace.path));
  await assert.rejects(access(prepared.workspace.originalPath));
});

test("Data Lab preparation verifies exact dataset files and keeps raw inputs separate from scratch", async () => {
  const value = await fixture();
  const csv = Buffer.from("region,revenue\nwest,42\neast,37\n", "utf8");
  const bytes = archive([{ name: "sales.csv", content: csv.toString("utf8") }]);
  const contentSha256 = hash(bytes);
  await writeFile(join(value.objectStore, `${contentSha256}.tar`), bytes, { mode: 0o600 });
  const request = structuredClone(value.request);
  request.profile = "data-lab";
  request.inputs = [{ id: "records", kind: "dataset", mountMode: "read-only", contentSha256, maxBytes: bytes.length, classification: "internal" }];
  request.requestedCapabilities.filesystem = "disposable-read-write";
  request.requestedCapabilities.tools = ["read", "search", "write", "edit", "bash"];
  request.outputs = { mode: "artifacts", requiredKinds: ["finding-report", "dataset", "document", "visualization"] };
  request.data = {
    mode: "local-reproducible",
    datasets: [{ datasetId: "sales", inputId: "records", relativePath: "sales.csv", format: "csv", contentSha256: hash(csv), maxBytes: csv.length }],
    engines: ["duckdb", "polars", "python", "sqlite"], maxArtifactFiles: 8, maxArtifactBytes: 4194304,
    allowedArtifactFormats: ["csv", "json", "markdown", "parquet", "png"], replayVerification: true, retention: "job-only",
    boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
  };
  const entries = [{ id: "records", kind: "dataset", objectName: `${contentSha256}.tar`, contentSha256, bytes: bytes.length, classification: "internal", mountMode: "read-only" }];
  const policy = structuredClone(value.policy);
  policy.profiles.dataLab = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"], services: ["local-model"],
    workspaceMount: "disposable-read-write", outputKinds: ["finding-report", "dataset", "document", "visualization"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
    runtime: { contract: "pixel-local-data-runtime-v1", engines: {
      duckdb: { version: "1.5.5", entrypoint: "/usr/bin/python3" }, polars: { version: "1.43.2", entrypoint: "/usr/bin/python3" },
      python: { version: "3.11.2", entrypoint: "/usr/bin/python3" }, sqlite: { version: "3.40.1", entrypoint: "/usr/bin/sqlite3" },
    } },
    maxData: { maxDatasets: 8, maxDatasetBytes: 1048576, maxArtifactFiles: 16, maxArtifactBytes: 8388608,
      allowedInputFormats: ["csv", "json", "jsonl", "parquet", "sqlite"], allowedArtifactFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"] },
  };
  const compiled = compileDataLab(request, policy, entries, { now: value.now, suffix: "123456abcdef" });
  const prepared = await prepareDataLabRun({ ...value, ...compiled, request, policy, entries, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  assert.equal(await readFile(prepared.datasets[0].path, "utf8"), csv.toString("utf8"));
  assert.equal(prepared.datasets[0].bytes, csv.length);
  assert.deepEqual(await readdir(prepared.workspace.path), []);
  assert.notEqual(prepared.workspace.path, prepared.workspace.originalPath);
  assert.doesNotThrow(() => verifyDataLabBindings(compiled.plan, compiled.lease, policy, { now: value.now }));
  await discardPreparedRun(prepared);

  const substitutedRequest = structuredClone(request);
  substitutedRequest.data.datasets[0].contentSha256 = hash("substituted");
  const substituted = compileDataLab(substitutedRequest, policy, entries, { now: value.now, suffix: "abcdef123456" });
  await assert.rejects(prepareDataLabRun({ ...value, ...substituted, request: substitutedRequest, policy, entries, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } }), /differs from its immutable hash/);
});

test("continued Builder preparation inherits only a consumed independently verified patch", async () => {
  const value = await fixture();
  const request = structuredClone(value.request);
  request.profile = "builder";
  request.requestedCapabilities.filesystem = "disposable-read-write";
  request.requestedCapabilities.tools = ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"];
  request.outputs = { mode: "patch", requiredKinds: ["patch", "test-evidence"] };
  request.verification = {
    mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }],
    immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
  const policy = structuredClone(value.policy);
  policy.profiles.builder.enabled = true;
  policy.verifier.enabled = true;
  const compiled = compileBuilder(request, policy, value.entries, { now: value.now, suffix: "123456abcdef" });
  const initialPrepared = await prepareBuilderRun({
    ...value, ...compiled, policy, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
  });
  const previousConsumption = createLeaseConsumption(initialPrepared, { now: value.now, suffix: "000000000071" });
  await claimLease(value.stateRoot, previousConsumption);
  const resultRoot = join(value.stateRoot, "results", previousConsumption.claimId);
  await mkdir(join(value.stateRoot, "results"), { mode: 0o700 });
  await mkdir(resultRoot, { mode: 0o700 });
  await initializeBuilderVolume(initialPrepared.workspace.originalPath, initialPrepared.workspace.path, {
    maxBytes: Math.floor(compiled.plan.budgets.maxDiskBytes / 2),
  });
  await writeFile(join(initialPrepared.workspace.path, "source", "src", "main.js"), "export const answer = 43;\n", { mode: 0o600 });
  const patch = await exportBuilderPatch(
    initialPrepared.workspace.originalPath, initialPrepared.workspace.path, resultRoot,
    {
      jobId: compiled.plan.jobId, claimId: previousConsumption.claimId, planSha256: compiled.planSha256,
      workspaceSha256: initialPrepared.workspace.sha256,
    },
    { maxTreeBytes: Math.floor(compiled.plan.budgets.maxDiskBytes / 2), maxArtifactBytes: compiled.lease.budgets.maxArtifactBytes },
  );
  const baseTime = value.now.getTime();
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: initialPrepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000072",
  });
  const checkpointUpdate = (head, state, overrides = {}) => ({
    state, iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) },
    progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: overrides.workspaceSnapshotSha256 ?? head.workspaceSnapshotSha256,
    artifactManifestSha256: Object.hasOwn(overrides, "artifactManifestSha256") ? overrides.artifactManifestSha256 : head.artifactManifestSha256,
    workerSessionSha256: Object.hasOwn(overrides, "workerSessionSha256") ? overrides.workerSessionSha256 : head.workerSessionSha256,
    verificationEvidenceSha256: Object.hasOwn(overrides, "verificationEvidenceSha256") ? overrides.verificationEvidenceSha256 : head.verificationEvidenceSha256,
    authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
  });
  const append = (head, state, tick, suffix, overrides) => appendCheckpoint({
    stateRoot: value.stateRoot, plan: compiled.plan, lease: compiled.lease, previousCheckpointSha256: checkpointSha256(head),
    update: checkpointUpdate(head, state, overrides), now: new Date(baseTime + tick), suffix,
  });
  const running = await append(initialized.checkpoint, "running", 2, "000000000073", {
    iteration: 1, workerSessionSha256: checkpointSha256(previousConsumption),
  });
  const cleanupPrepared = await prepareBuilderCleanupRecovery({
    ...value, ...compiled, policy, objectStore: join(value.root, "missing-objects"),
    workspaceRoot: join(value.root, "missing-workspaces"), executorPath: join(value.root, "missing-executor"),
    now: new Date(baseTime + 7200000), archiveLimits: null,
    recovery: { consumption: previousConsumption, checkpoint: running.checkpoint },
  });
  assert.equal(cleanupPrepared.cleanupOnly, true);
  assert.deepEqual(cleanupPrepared.recoveryConsumption, previousConsumption);
  assert.throws(() => createLeaseConsumption(cleanupPrepared, { now: value.now, suffix: "000000000099" }), /recovery-only/);
  const verifying = await append(running.checkpoint, "verifying", 3, "000000000074", {
    usage: { runtimeSeconds: 10, modelRequests: 2, inputTokens: 100, outputTokens: 20, artifactBytes: patch.bytes },
    workspaceSnapshotSha256: hash("verified-candidate"), artifactManifestSha256: patch.sha256,
  });
  const recoveryPrepared = await prepareBuilderVerificationRecovery({
    ...value, ...compiled, policy, now: new Date(baseTime + 7200000),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    recovery: { consumption: previousConsumption, checkpoint: verifying.checkpoint },
  });
  assert.equal(recoveryPrepared.verificationOnly, true);
  assert.deepEqual(recoveryPrepared.recoveryConsumption, previousConsumption);
  await discardPreparedRun(recoveryPrepared);
  const verified = await append(verifying.checkpoint, "verified", 4, "000000000075", {
    progress: { noProgressCount: 1 }, verificationEvidenceSha256: hash("verification-evidence"),
  });
  const continuation = await issueContinuationLease({
    stateRoot: value.stateRoot, plan: compiled.plan, previousLease: compiled.lease, policy,
    previousConsumption, checkpoint: verified.checkpoint, now: new Date(baseTime + 5), suffix: "000000000076",
  });
  await discardPreparedRun(initialPrepared);
  const continued = await prepareBuilderRun({
    ...value, ...compiled, lease: continuation.lease, policy, now: new Date(baseTime + 6),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    continuation: { previousLease: compiled.lease, previousConsumption, checkpoint: verified.checkpoint, patchPath: patch.path },
  });
  assert.equal(continued.continuationBase.sha256, patch.sha256);
  assert.equal(continued.continuationBase.claimId, previousConsumption.claimId);
  assert.equal(continued.lease.iteration, 2);
  assert.deepEqual(await readdir(continued.workspace.path), []);
  assert.doesNotThrow(() => verifyBuilderBindings(compiled.plan, continuation.lease, policy, { now: new Date(baseTime + 6) }));
  await discardPreparedRun(continued);

  const substitutedCheckpoint = structuredClone(verified.checkpoint);
  substitutedCheckpoint.artifactManifestSha256 = hash("substituted-patch");
  await assert.rejects(prepareBuilderRun({
    ...value, ...compiled, lease: continuation.lease, policy, now: new Date(baseTime + 6),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    continuation: { previousLease: compiled.lease, previousConsumption, checkpoint: substitutedCheckpoint, patchPath: patch.path },
  }), /lease differs from cumulative verified state/);
  const retainedPatch = await readFile(patch.path);
  await writeFile(patch.path, Buffer.concat([retainedPatch, Buffer.from("\n")]));
  await assert.rejects(prepareBuilderRun({
    ...value, ...compiled, lease: continuation.lease, policy, now: new Date(baseTime + 6),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    continuation: { previousLease: compiled.lease, previousConsumption, checkpoint: verified.checkpoint, patchPath: patch.path },
  }), /patch differs from the verified checkpoint/);
  await writeFile(patch.path, retainedPatch);
  await rm(value.root, { recursive: true, force: true });
});

test("lease claim is durable, single-use, and race-safe", async () => {
  const value = await fixture();
  const prepared = await prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  const claim = createLeaseConsumption(prepared, { now: value.now, suffix: "abcdef123456" });
  assert.deepEqual(validateWorkConsumption(claim), []);
  const attempts = await Promise.allSettled([claimLease(value.stateRoot, claim), claimLease(value.stateRoot, claim)]);
  assert.equal(attempts.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(attempts.filter((item) => item.status === "rejected").length, 1);
  assert.match(attempts.find((item) => item.status === "rejected").reason.message, /already consumed/);
  const stored = JSON.parse(await readFile(join(value.stateRoot, "claims", `${claim.leaseId}.json`), "utf8"));
  assert.deepEqual(stored, claim);
  assert.doesNotMatch(JSON.stringify(stored), /(?:\\Users\\|\/home\/|docker\.sock|fixture invariant)/);
  await discardPreparedRun(prepared);
});

test("pre-launch lease revocation is durable, content-free, and blocks later consumption", async () => {
  const value = await fixture();
  const prepared = await prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  const claim = createLeaseConsumption(prepared, { now: value.now, suffix: "abcdef123450" });
  const revocation = createLeaseRevocation({
    plan: value.plan, lease: value.lease, goalId: "workgoal-1786366800001-abcdef123456",
    goalCheckpointSha256: "1".repeat(64), reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: "2".repeat(64),
  }, { now: value.now, suffix: "abcdef123451" });
  assert.deepEqual(validateWorkLeaseRevocation(revocation), []);
  await revokeLease(value.stateRoot, revocation);
  assert.deepEqual(await recoverLeaseRevocation(value.stateRoot, value.lease), revocation);
  assert.deepEqual(await inspectLeaseDisposition(value.stateRoot, value.lease), { kind: "revoked", record: revocation });
  await assert.rejects(claimLease(value.stateRoot, claim), /already consumed or revoked/);
  await assert.rejects(recoverLeaseConsumption(value.stateRoot, value.lease), /is revoked/);
  const stored = JSON.parse(await readFile(join(value.stateRoot, "claims", `${value.lease.leaseId}.json`), "utf8"));
  assert.deepEqual(stored, revocation);
  assert.doesNotMatch(JSON.stringify(stored), /(?:\\Users\\|\/home\/|docker\.sock|fixture invariant)/);
  await discardPreparedRun(prepared);
  await rm(value.root, { recursive: true, force: true });
});

test("lease consumption and revocation contend for exactly one immutable winner", async () => {
  const value = await fixture();
  const prepared = await prepareScoutRun({ ...value, archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 } });
  const claim = createLeaseConsumption(prepared, { now: value.now, suffix: "abcdef123452" });
  const revocation = createLeaseRevocation({
    plan: value.plan, lease: value.lease, goalId: "workgoal-1786366800001-abcdef123456",
    goalCheckpointSha256: "3".repeat(64), reviewedChildCheckpointSha256: "4".repeat(64),
    cancellationReviewSha256: "5".repeat(64),
  }, { now: value.now, suffix: "abcdef123453" });
  const attempts = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => (
    index % 2 === 0 ? claimLease(value.stateRoot, claim) : revokeLease(value.stateRoot, revocation)
  )));
  assert.equal(attempts.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(attempts.filter((item) => item.status === "rejected").length, 31);
  assert.ok(attempts.filter((item) => item.status === "rejected").every((item) => /already consumed or revoked/.test(item.reason.message)));
  const disposition = await inspectLeaseDisposition(value.stateRoot, value.lease);
  assert.ok(["consumed", "revoked"].includes(disposition.kind));
  assert.deepEqual(disposition.record, disposition.kind === "consumed" ? claim : revocation);
  assert.deepEqual((await readdir(join(value.stateRoot, "claims"))).sort(), [`${value.lease.leaseId}.json`]);
  if (disposition.kind === "consumed") await assert.rejects(recoverLeaseRevocation(value.stateRoot, value.lease), /is consumed/);
  else await assert.rejects(recoverLeaseConsumption(value.stateRoot, value.lease), /is revoked/);
  await discardPreparedRun(prepared);
  await rm(value.root, { recursive: true, force: true });
});

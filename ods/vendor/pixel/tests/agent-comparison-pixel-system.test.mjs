import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  PIXEL_PLANNED_REVIEW_BOUNDARY, PIXEL_POLICY_BINDING_BOUNDARY, PIXEL_REVIEW_BOUNDARY,
  PIXEL_RUNTIME_BOUNDARY, PIXEL_HARNESS_FILES, PixelSystemError, applyPixelSystemPolicyBinding, buildPixelRuntimeReceipt,
  loadPixelSystemConfig, pixelSystemDiagnostic, reviewPixelPlannedSystem, reviewPixelSystem, reviewPixelSystemPolicyBinding,
  main as runPixelSystemCli, runPixelSystem, startPixelSystem, stopPixelSystem, verifyPreparedComparison,
} from "../deploy/agent-comparison/pixel-system-cli.mjs";
import { WorkBrokerError, canonical } from "../deploy/work-broker/broker.mjs";
import { PixelArmError, pixelArmContract } from "../deploy/agent-comparison/pixel-arm.mjs";
import { VLLM_CACHE_SEED_BOOTSTRAP, VLLM_CACHE_SEED_BOOTSTRAP_NAME } from "../deploy/work-controller/model-backend-launch.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const hash = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");

test("system CLI exposes actionable known contract failures and masks unknown failures", () => {
  assert.equal(pixelSystemDiagnostic(new PixelSystemError("system contract failed")), "system contract failed");
  assert.equal(pixelSystemDiagnostic(new PixelArmError("arm contract failed")), "arm contract failed");
  assert.equal(pixelSystemDiagnostic(new WorkBrokerError("request exceeds Builder budget maxIterations")), "request exceeds Builder budget maxIterations");
  assert.equal(pixelSystemDiagnostic(new Error("private implementation detail")), "unexpected failure");
});

test("Pixel harness identity includes the portal Assistant, policy brokers, accounting proxy, and comparison adapters", () => {
  assert.equal(new Set(PIXEL_HARNESS_FILES).size, PIXEL_HARNESS_FILES.length);
  for (const required of [
    "control/server.py", "control/ui/app.js", "scripts/render-config.mjs",
    "scripts/portal_outcome_assistant_runtime.py", "scripts/portal_outcome_assistant_evidence.py",
    "scripts/portal_outcome_assistant_system.py", "deploy/agent-comparison/assistant-model-proxy.mjs",
    "schemas/portal-outcome-assistant-template-v1.schema.json", "deploy/agent-comparison/assistant-template.example.json",
    "scripts/portal_outcome_pixel_livesystem.py", "scripts/portal_outcome_pixel_orchestrate.py",
    "deploy/work-model-proxy/proxy.mjs", "deploy/work-model-proxy/inference-policy.mjs",
    "plugin/index.js", "plugin-ops/index.js", "plugin-frontier/index.js",
  ]) assert.ok(PIXEL_HARNESS_FILES.includes(required), required);
});

function fixture() {
  const inference = {
    sampling: {
      temperaturePermille: 1000, topPPermille: 950, topK: 0, minPPermille: 0,
      repeatPenaltyPermille: 1000, seed: 42, reasoningEffort: "high", reasoningVisibility: "hidden",
    },
    request: {
      wireApi: "openai-chat-completions", stream: true, maxOutputTokens: 8192,
      toolEncoding: "function", requestFieldPolicySha256: "", promptCachePolicy: "empty-at-run-start",
    },
  };
  const exactInference = {
    enforcement: "exact-request-boundary-v1", wireApi: "openai-chat-completions",
    temperaturePermille: 1000, topPPermille: 950, topK: 0, minPPermille: 0,
    repeatPenaltyPermille: 1000, seed: 42, reasoningEffort: "high", reasoningVisibility: "hidden",
  };
  inference.request.requestFieldPolicySha256 = hash({
    ...exactInference, stream: true, maxOutputTokens: 8192, toolEncoding: "function",
  });
  const imageDigest = `sha256:${"a".repeat(64)}`, artifactSha256 = "b".repeat(64);
  const serverArguments = [
    "/models/model", "--served-model-name", "DeepSeek-V4-Flash-0731", "--host", "0.0.0.0", "--port", "8080",
    "--max-model-len", "1048576", "--tensor-parallel-size", "2", "--max-num-seqs", "16",
    "--gpu-memory-utilization", "0.984", "--dtype", "auto", "--no-enable-log-requests", "--no-enable-log-outputs",
    "--no-enable-log-deltas", "--disable-uvicorn-access-log",
    "--reasoning-parser", "deepseek_v4", "--enable-auto-tool-choice", "--tool-call-parser", "deepseek_v4",
  ];
  const model = {
    modelId: "DeepSeek-V4-Flash-0731",
    artifact: { kind: "directory-manifest", sha256: artifactSha256, bytes: 156000000000, fileCount: 57 },
    runtime: {
      implementation: "vllm", imageDigest, launchArgumentsSha256: hash(serverArguments),
      contextWindow: 1048576, parallelSlots: 16, runtimeIsolation: "fresh-per-run", restartPolicy: "no", crossRunStateAllowed: false,
      resources: { acceleratorClass: "nvidia", acceleratorCount: 2, cpuCores: 48, memoryMiB: 196608, sharedMemoryMiB: 32768, tmpfsMiB: 1024, cacheMiB: 32768, pidsLimit: 4096 },
    },
  };
  const policy = {
    runner: { imageDigest: `sha256:${"c".repeat(64)}` },
    verifier: { imageDigest: `sha256:${"1".repeat(64)}` },
    localModel: {
      id: model.modelId, provider: "vllm", imageDigest, imageRef: `local/pixel-dsv4@${imageDigest}`,
      modelArtifactSha256: artifactSha256, contextWindow: 1048576, maxRequestOutputTokens: 8192,
      maxRequestContextTokens: 32768,
      inference: exactInference,
    },
  };
  const configuration = {
    restartPolicy: "no",
    providerOptions: {
      provider: "vllm", imageEntrypoint: "/usr/local/bin/pixel-dsv4-serve", tensorParallelSize: 2, maxSequences: 16,
      gpuMemoryUtilizationPermille: 984, dtype: "auto", enforceEager: false,
      reasoningParser: "deepseek_v4", toolCallParser: "deepseek_v4",
    },
    accelerator: { class: "nvidia-cuda", count: 2 },
    resources: { cpuCores: 48, memoryMiB: 196608, sharedMemoryMiB: 32768, tmpfsMiB: 1024, cacheMiB: 32768, pids: 4096 },
  };
  const prepared = {
    policy, configuration,
    launchBundleSha256: "2".repeat(64),
    artifactManifest: { kind: "directory", artifactSha256, fileCount: 57, totalBytes: 156000000000 },
    launch: {
      container: { args: [
        "container", "create", "--restart", "no", "--entrypoint", "/bin/sh", policy.localModel.imageRef,
        "-c", VLLM_CACHE_SEED_BOOTSTRAP, VLLM_CACHE_SEED_BOOTSTRAP_NAME, ...serverArguments,
      ] },
      bindings: { environmentSha256: "3".repeat(64) },
    },
  };
  return { prepared, model, inference, serverArguments };
}

test("DSV4 server argument contract matches the cross-language canonical hash golden", () => {
  assert.equal(hash(fixture().serverArguments), "26ea80e3376e700b0710635a4b60f2315c7be0e27bea4f4b8f2c79f06b65cec0");
});

test("concrete Pixel runtime receipt binds the exact DSV4 launch, resources, policy, and harness", () => {
  const value = fixture();
  assert.equal(verifyPreparedComparison(value.prepared, value.model, value.inference), true);
  const receipt = buildPixelRuntimeReceipt({
    id: "outcomerun-1786622400100-abcdefabcdef", ...value,
    modelSha256: "d".repeat(64), inferenceSha256: "e".repeat(64), harnessSha256: "f".repeat(64),
    environmentSha256: "4".repeat(64),
    qualificationReceiptSha256: "0".repeat(64),
  });
  assert.equal(receipt.modelId, "DeepSeek-V4-Flash-0731");
  assert.equal(receipt.backendFresh, true);
  assert.equal(receipt.runnerFresh, true);
  assert.equal(receipt.qwenProductModel, false);
  assert.equal(receipt.boundary, PIXEL_RUNTIME_BOUNDARY);
  assert.equal(receipt.workPolicySha256, hash(value.prepared.policy));
  assert.equal(receipt.environmentSha256, "4".repeat(64));
  assert.equal(receipt.runtimeEnvironmentSha256, "3".repeat(64));
  assert.equal(receipt.verifierImageDigest, value.prepared.policy.runner.imageDigest);
  assert.equal(receipt.qualificationReceiptSha256, "0".repeat(64));
  assert.equal(receipt.launchBundleSha256, value.prepared.launchBundleSha256);
});

test("concrete Pixel runtime refuses model, launch, parallelism, resource, and inference drift", () => {
  for (const mutate of [
    (value) => { value.model.modelId = "Qwen2.5-Coder-7B"; },
    (value) => { value.prepared.configuration.providerOptions.maxSequences = 8; },
    (value) => { value.prepared.configuration.providerOptions.imageEntrypoint = "/usr/local/bin/vllm"; },
    (value) => { value.prepared.configuration.providerOptions.gpuMemoryUtilizationPermille = 900; },
    (value) => { value.prepared.configuration.providerOptions.reasoningParser = "deepseek_r1"; },
    (value) => { value.prepared.configuration.providerOptions.toolCallParser = "deepseek_v3"; },
    (value) => { value.prepared.configuration.restartPolicy = "unless-stopped"; },
    (value) => { value.prepared.launch.container.args[value.prepared.launch.container.args.indexOf("no")] = "unless-stopped"; },
    (value) => { value.model.runtime.resources.memoryMiB -= 1; },
    (value) => { value.prepared.launch.container.args.push("--uncontracted"); },
    (value) => {
      const index = value.prepared.launch.container.args.indexOf(value.prepared.policy.localModel.imageRef);
      value.prepared.launch.container.args[index + 2] += " ";
    },
    (value) => {
      const index = value.prepared.launch.container.args.indexOf(value.prepared.policy.localModel.imageRef);
      value.prepared.launch.container.args[index + 3] = "pixel-vllm-cache-bootstrap-drift";
    },
    (value) => {
      const args = value.prepared.launch.container.args;
      const image = value.prepared.policy.localModel.imageRef;
      const server = args.slice(args.indexOf(VLLM_CACHE_SEED_BOOTSTRAP_NAME) + 1);
      value.prepared.launch.container.args = ["container", "create", "--restart", "no", image, ...server];
    },
    (value) => { value.inference.sampling.reasoningEffort = "low"; },
    (value) => { value.inference.request.stream = false; },
    (value) => { value.inference.request.requestFieldPolicySha256 = "0".repeat(64); },
  ]) {
    const value = structuredClone(fixture()); mutate(value);
    assert.throws(() => verifyPreparedComparison(value.prepared, value.model, value.inference), PixelSystemError);
  }
});

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

test("system start materializes a unique private run, starts only the exact prepared backend, and removes it", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-outcome-system-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const runtimeRoot = join(root, "runs"); await mkdir(runtimeRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(runtimeRoot, 0o700);
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  const value = fixture(), id = "outcomerun-1786622400100-123456abcdef";
  Object.assign(policy, { enabled: true });
  Object.assign(policy.runner, { prepared: true, imageDigest: value.prepared.policy.runner.imageDigest, imageRef: `local/pixel-runner@${value.prepared.policy.runner.imageDigest}` });
  Object.assign(policy.localModel, {
    prepared: true, provider: "vllm", id: value.model.modelId, imageDigest: value.prepared.policy.localModel.imageDigest,
    imageRef: value.prepared.policy.localModel.imageRef, modelArtifactSha256: value.model.artifact.sha256,
    backendVersion: "fixture-dsv4", acceleratorClass: "nvidia-cuda", contextWindow: value.model.runtime.contextWindow,
    maxRequestOutputTokens: value.inference.request.maxOutputTokens, inference: value.prepared.policy.localModel.inference,
  });
  policy.profiles.scout.enabled = true; policy.profiles.builder.enabled = true; policy.verifier.enabled = true;
  policy.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536,
    maxBudgets: structuredClone(policy.profiles.builder.maxBudgets),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 50, maxResultsPerQuery: 10, maxSources: 250, maxSourceBytes: 4194304,
      maxTotalSourceBytes: 268435456, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  const qualificationNow = new Date("2026-08-13T15:00:00.000Z");
  const qualification = await installFixtureModelQualification(policy, root, qualificationNow);
  const environment = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
  const researchQueue = join(root, "research-courier"); await mkdir(researchQueue, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(researchQueue, 0o700);
  environment.researchRuntime = { researchCourierQueueRoot: researchQueue, researchEndpoint: "http://127.0.0.1:8888" };
  const backend = JSON.parse(await readFile(new URL("../deploy/work-controller/model-backend-vllm.example.json", import.meta.url), "utf8"));
  backend.restartPolicy = "no";
  Object.assign(backend.providerOptions, value.prepared.configuration.providerOptions);
  const paths = { policy: join(root, "policy.json"), environment: join(root, "environment.json"), backend: join(root, "backend.json"), assistant: join(root, "assistant.json"), config: join(root, "system.json"), input: join(root, "start.json") };
  await privateWrite(paths.policy, policy); await privateWrite(paths.environment, environment); await privateWrite(paths.backend, backend);
  await privateWrite(paths.assistant, JSON.parse(await readFile(new URL("../deploy/agent-comparison/assistant-template.example.json", import.meta.url), "utf8")));
  await privateWrite(paths.config, {
    $schema: "https://osmantic.com/pixel/schemas/portal-outcome-pixel-system-v1.schema.json", schemaVersion: 1,
    runtimeRoot, policyTemplatePath: paths.policy, environmentTemplatePath: paths.environment, modelBackendTemplatePath: paths.backend, assistantTemplatePath: paths.assistant,
    boundary: "Owner-private templates and a disposable runtime root for exact Pixel outcome runs only. Configuration grants no model start, task execution, provider, credential, external-effect, merge, deployment, publication, completion, acceptance, or promotion authority.",
  });
  const enabledPolicyPath = join(root, "enabled-policy.json"); await privateWrite(enabledPolicyPath, policy);
  const policyBoundConfig = join(root, "system-policy-bound.json");
  const bindingReview = await reviewPixelSystemPolicyBinding(paths.config, enabledPolicyPath, policyBoundConfig, { now: qualificationNow });
  assert.equal(bindingReview.status, "confirmation-required");
  assert.equal(bindingReview.boundary, PIXEL_POLICY_BINDING_BOUNDARY);
  assert.deepEqual(bindingReview.enabledProfiles, ["assistant", "scout", "builder", "researcher"]);
  assert.deepEqual(bindingReview.comparisonProfiles, ["assistant", "builder", "controller", "researcher"]);
  assert.equal(bindingReview.changes.changesOnlyPolicyTemplatePath, true);
  assert.equal(bindingReview.changes.editsCurrentConfiguration, false);
  assert.equal(bindingReview.changes.startsModel, false);
  assert.equal(bindingReview.authority.grantsExecution, false);
  await assert.rejects(
    applyPixelSystemPolicyBinding(paths.config, enabledPolicyPath, policyBoundConfig, "0".repeat(64), { now: qualificationNow }),
    /confirmation differs/u,
  );
  const substitutedDestination = join(root, "substituted-system.json");
  await assert.rejects(
    applyPixelSystemPolicyBinding(paths.config, enabledPolicyPath, substitutedDestination, bindingReview.operationSha256, { now: qualificationNow }),
    /confirmation differs/u,
  );
  const driftedPolicy = structuredClone(policy); driftedPolicy.profiles.builder.maxBudgets.maxIterations -= 1;
  await privateWrite(enabledPolicyPath, driftedPolicy);
  await assert.rejects(
    applyPixelSystemPolicyBinding(paths.config, enabledPolicyPath, policyBoundConfig, bindingReview.operationSha256, { now: qualificationNow }),
    /confirmation differs/u,
  );
  await privateWrite(enabledPolicyPath, policy);
  await assert.rejects(
    reviewPixelSystemPolicyBinding(paths.config, enabledPolicyPath, join(root, "stale-system.json"), { now: new Date(qualification.receipt.expiresAt) }),
    /qualification is stale/u,
  );
  const bindingReceipt = await applyPixelSystemPolicyBinding(
    paths.config, enabledPolicyPath, policyBoundConfig, bindingReview.operationSha256, { now: qualificationNow },
  );
  assert.equal(bindingReceipt.status, "policy-bound-system-configuration-written");
  assert.equal(bindingReceipt.operationSha256, bindingReview.operationSha256);
  assert.equal((await loadPixelSystemConfig(paths.config)).policyTemplatePath, paths.policy);
  assert.equal((await loadPixelSystemConfig(policyBoundConfig)).policyTemplatePath, enabledPolicyPath);
  await assert.rejects(
    applyPixelSystemPolicyBinding(paths.config, enabledPolicyPath, policyBoundConfig, bindingReview.operationSha256, { now: qualificationNow }),
    /must be a new private file/u,
  );
  const cliPolicyBoundConfig = join(root, "system-policy-bound-cli.json"), cliReviewOutput = join(root, "system-policy-review-cli.json");
  await runPixelSystemCli([
    "policy-review", "--config", paths.config, "--policy", enabledPolicyPath,
    "--candidate", cliPolicyBoundConfig, "--output", cliReviewOutput,
  ], { now: qualificationNow });
  const cliReview = JSON.parse(await readFile(cliReviewOutput, "utf8"));
  assert.equal(cliReview.status, "confirmation-required");
  const cliApplyOutput = join(root, "system-policy-apply-cli.json");
  await runPixelSystemCli([
    "policy-apply", "--config", paths.config, "--policy", enabledPolicyPath,
    "--candidate", cliPolicyBoundConfig, "--confirm-operation-sha256", cliReview.operationSha256,
    "--output", cliApplyOutput,
  ], { now: qualificationNow });
  assert.equal(JSON.parse(await readFile(cliApplyOutput, "utf8")).status, "policy-bound-system-configuration-written");
  assert.equal((await loadPixelSystemConfig(cliPolicyBoundConfig)).policyTemplatePath, enabledPolicyPath);
  const originalConfig = paths.config;
  paths.config = cliPolicyBoundConfig;
  const modelBytes = Buffer.from(JSON.stringify(value.model)), inferenceBytes = Buffer.from(JSON.stringify(value.inference));
  const byteHash = (payload) => createHash("sha256").update(payload).digest("hex");
  await privateWrite(paths.input, {
    schemaVersion: 1, operation: "start", runId: id, profile: "builder",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const preparedFor = async (path) => {
    const [materializedPolicy, materializedEnvironment, materializedConfiguration] = await Promise.all([
      readFile(join(path, "..", "policy.json"), "utf8").then(JSON.parse),
      readFile(join(path, "..", "environment.json"), "utf8").then(JSON.parse), readFile(path, "utf8").then(JSON.parse),
    ]);
    return {
      ...value.prepared, policy: materializedPolicy, environment: materializedEnvironment, configuration: materializedConfiguration,
      launchBundleSha256: "9".repeat(64),
      launch: { ...value.prepared.launch, bindings: { policySha256: "1".repeat(64), environmentSha256: "2".repeat(64), configurationSha256: "3".repeat(64), artifactSha256: value.model.artifact.sha256 } },
    };
  };
  const calls = [], activeBackends = new Set();
  const dependencies = {
    prepareModelBackendLaunch: preparedFor, harnessContractSha256: async () => "8".repeat(64), now: qualificationNow,
    reviewPixelTaskCompatibility(options) {
      return {
        schemaVersion: 1, operation: "pixel-portal-outcome-task-compatibility", profile: options.admission.profile,
        taskAdmissionSha256: hash(options.admission), workRequestSha256: "4".repeat(64),
        workPlanSha256: "5".repeat(64), budgetsSha256: "6".repeat(64), capabilitiesSha256: "7".repeat(64),
        boundary: pixelArmContract.taskCompatibilityBoundary,
      };
    },
    reviewPixelPlannedTaskCompatibility(options) {
      return {
        schemaVersion: 1, operation: "pixel-portal-outcome-planned-task-compatibility", profile: options.admission.profile,
        readiness: options.workPolicy.enabled && options.workPolicy.localModel.prepared ? "policy-ready" : "qualification-required",
        taskAdmissionSha256: hash(options.admission), workRequestSha256: "4".repeat(64),
        workPolicySha256: hash(options.workPolicy), inputSetSha256: "5".repeat(64), budgetsSha256: "6".repeat(64),
        capabilitiesSha256: "7".repeat(64), outputsSha256: "9".repeat(64),
        boundary: pixelArmContract.plannedTaskCompatibilityBoundary,
      };
    },
    async startModelBackend(prepared) {
      calls.push(["start", prepared.environment.runtime.backendContainerName, prepared.environment.runtime.backendNetworkName]);
      const name = prepared.environment.runtime.backendContainerName;
      if (activeBackends.has(name)) return { state: "ready-already-running", checks: { readinessPassed: true } };
      activeBackends.add(name); return { state: "ready-started", checks: { readinessPassed: true } };
    },
    async stopModelBackend(prepared) { calls.push(["stop", prepared.environment.runtime.backendContainerName]); activeBackends.delete(prepared.environment.runtime.backendContainerName); return { state: "removed" }; },
    async runPixelProductArm(options) {
      calls.push(["run", options.admission.profile, options.lifecycleOptions.backendContainerName]);
      assert.equal(options.lifecycleOptions.requireExactModelBackendBindings, true);
      assert.equal(options.lifecycleOptions.modelBackendBindings.artifactSha256, value.model.artifact.sha256);
      if (options.admission.profile === "researcher") {
        assert.equal(options.lifecycleOptions.researchCourierQueueRoot, researchQueue);
        assert.equal(options.lifecycleOptions.researchEndpoint, "http://127.0.0.1:8888");
        assert.equal(options.lifecycleOptions.researchRevisionReview, true);
        assert.equal(options.lifecycleOptions.researchMaxRevisionRounds, 2);
        assert.equal(options.lifecycleOptions.modelContractSha256, byteHash(modelBytes));
        assert.equal(options.lifecycleOptions.inferenceContractSha256, byteHash(inferenceBytes));
      }
      assert.equal(await readFile(join(options.objectStore, `${byteHash(source)}.tar`), "utf8"), "source fixture");
      return {
        exitCode: 0, latencyMs: 42, finalMessage: `${options.admission.profile} done`,
        artifacts: [{ kind: "finding-report", relativePath: "artifact.md", payload: Buffer.from("result\n") }],
        independentVerification: { status: options.admission.profile === "researcher" ? "evidence-pass" : "pass" },
        usage: { modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 20, toolCalls: 1 },
        authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
      };
    },
  };
  const reviewId = "outcomerun-1786622400099-123456abcdef", reviewInput = join(root, "review.json");
  await privateWrite(reviewInput, {
    schemaVersion: 1, operation: "review", runId: reviewId, profile: "builder",
    now: qualificationNow.toISOString(), idSuffix: reviewId.slice(-12),
    admission: { profile: "builder" }, task: { profile: "builder" },
    requestPayloadBase64: Buffer.from("review work").toString("base64"),
    sourceReference: { mediaType: "application/x-tar", sha256: "3".repeat(64), bytes: 12 },
    environment: {}, toolPolicy: {}, verifierDefinition: {},
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const review = await reviewPixelSystem(paths.config, reviewInput, dependencies);
  assert.equal(review.status, "ready");
  assert.equal(review.profile, "builder");
  assert.equal(review.boundary, PIXEL_REVIEW_BOUNDARY);
  assert.equal(review.changes.temporaryPrivateReviewStateRemoved, true);
  assert.equal(review.changes.modelStarted, false);
  assert.equal(review.authority.grantsExecution, false);
  assert.equal(review.runnerImageDigest, value.prepared.policy.runner.imageDigest);
  assert.equal(review.verifierImageDigest, value.prepared.policy.runner.imageDigest);
  assert.equal(review.qualificationReceiptSha256, policy.localModel.qualification.receiptSha256);
  assert.match(review.taskCompatibilitySha256, /^[a-f0-9]{64}$/u);
  assert.match(review.verifierImageDigest, /^sha256:[a-f0-9]{64}$/u);
  assert.equal(calls.length, 0);
  await assert.rejects(readFile(join(runtimeRoot, reviewId, "config", "model-backend.json")), /ENOENT/u);

  const plannedPolicy = structuredClone(policy);
  plannedPolicy.enabled = false;
  plannedPolicy.localModel.prepared = false;
  await privateWrite(paths.policy, plannedPolicy);
  const plannedReviewId = "outcomerun-1786622400097-123456abcdef", plannedReviewInput = join(root, "planned-review.json");
  await privateWrite(plannedReviewInput, {
    ...JSON.parse(await readFile(reviewInput, "utf8")), operation: "planned-review", runId: plannedReviewId,
    idSuffix: plannedReviewId.slice(-12),
  });
  const plannedReview = await reviewPixelPlannedSystem(originalConfig, plannedReviewInput, dependencies);
  assert.equal(plannedReview.status, "structurally-compatible");
  assert.equal(plannedReview.readiness, "qualification-required");
  assert.equal(plannedReview.boundary, PIXEL_PLANNED_REVIEW_BOUNDARY);
  assert.equal(plannedReview.workPolicySha256, hash(plannedPolicy));
  assert.equal(plannedReview.harnessContractSha256, "8".repeat(64));
  assert.equal(plannedReview.runnerImageDigest, value.prepared.policy.runner.imageDigest);
  assert.equal(plannedReview.backendImageDigest, value.prepared.policy.localModel.imageDigest);
  assert.ok(Object.values(plannedReview.changes).every((changed) => changed === false));
  assert.ok(Object.values(plannedReview.authority).every((granted) => granted === false));
  assert.equal(calls.length, 0);
  await assert.rejects(reviewPixelPlannedSystem(originalConfig, plannedReviewInput, {
    ...dependencies, harnessContractSha256: async () => "not-a-digest",
  }), /harness contract digest/u);
  await assert.rejects(reviewPixelSystem(originalConfig, reviewInput, dependencies), /not prepared/u);
  await privateWrite(paths.policy, policy);

  const missingQualificationId = "outcomerun-1786622400098-123456abcdef";
  const missingQualificationInput = join(root, "missing-qualification-start.json");
  await privateWrite(missingQualificationInput, {
    schemaVersion: 1, operation: "start", runId: missingQualificationId, profile: "builder",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  await rm(qualification.path);
  await assert.rejects(startPixelSystem(paths.config, missingQualificationInput, dependencies), /qualification receipt is unavailable/u);
  assert.equal(calls.length, 0);
  await installFixtureModelQualification(policy, root, qualificationNow);

  const receipt = await startPixelSystem(paths.config, paths.input, dependencies);
  assert.equal(receipt.backendFresh, true);
  assert.equal(receipt.qualificationReceiptSha256, policy.localModel.qualification.receiptSha256);
  assert.equal(calls[0][1], `pixel-outcome-model-${id.slice(-12)}`);
  assert.equal(calls[0][2], `pixel-outcome-backend-${id.slice(-12)}`);
  const source = Buffer.from("source fixture"), sourcePath = join(root, "source.tar");
  await writeFile(sourcePath, source, { mode: 0o600 }); if (process.platform !== "win32") await chmod(sourcePath, 0o600);
  const runInput = join(root, "run.json");
  await privateWrite(runInput, {
    schemaVersion: 1, operation: "run", runId: id, now: "2026-08-13T15:00:00.000Z", idSuffix: id.slice(-12),
    admission: { profile: "builder" }, task: { profile: "builder" }, requestPayloadBase64: Buffer.from("do work").toString("base64"),
    sourcePath, sourceReference: { mediaType: "application/x-tar", sha256: byteHash(source), bytes: source.length },
    researchFixturePath: null, researchFixtureReference: null,
    environment: {}, toolPolicy: {}, verifierDefinition: {},
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const continuousStart = dependencies.startModelBackend;
  dependencies.startModelBackend = async () => ({ state: "ready-started", checks: { readinessPassed: true } });
  await assert.rejects(runPixelSystem(paths.config, runInput, dependencies), /continuously ready/u);
  dependencies.startModelBackend = continuousStart;
  const runResult = await runPixelSystem(paths.config, runInput, dependencies);
  assert.equal(runResult.finalMessage, "builder done");
  assert.equal(await readFile(runResult.artifacts[0].payloadPath, "utf8"), "result\n");
  assert.equal(calls.at(-1)[0], "run");
  const stopInput = join(root, "stop.json"); await privateWrite(stopInput, { schemaVersion: 1, operation: "stop", runId: id });
  const stopped = await stopPixelSystem(paths.config, stopInput, dependencies);
  assert.equal(stopped.privateRunStateRemoved, true);
  await assert.rejects(readFile(join(runtimeRoot, id, "config", "system-state.json")), /ENOENT/u);
  assert.equal(calls.at(-1)[0], "stop");

  const researchId = "outcomerun-1786622400102-123456abcdef";
  const researchStartInput = join(root, "research-start.json");
  await privateWrite(researchStartInput, {
    schemaVersion: 1, operation: "start", runId: researchId, profile: "researcher",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const researchReceipt = await startPixelSystem(paths.config, researchStartInput, dependencies);
  assert.equal(researchReceipt.profile, "researcher");
  const researchRunInput = join(root, "research-run.json");
  const researchFixturePath = join(root, "research-fixture.json");
  const researchFixtureBytes = Buffer.from(`${JSON.stringify({
    $schema: "https://osmantic.com/pixel/schemas/portal-outcome-research-fixture-v1.schema.json",
    schemaVersion: 1, operation: "pixel-portal-outcome-research-fixture", observedAt: "2026-08-13T15:00:00Z",
    sources: [{ fixtureSourceId: "fixture-source", sourceType: "web", title: "Fixture source", snippet: "Fixture source", quality: "other", publishedDate: "2026-08-13", retrieval: { status: "fetched", content: "Fixture source" } }],
    authority: { publicNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false },
    boundary: "Owner-private admitted offline research evidence for deterministic comparison only. It performs no public network, credential, account, message, publication, purchase, write, policy, scope, or external effect; every source remains untrusted data and grants no authority.",
  })}\n`);
  await writeFile(researchFixturePath, researchFixtureBytes, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(researchFixturePath, 0o600);
  const researchFixtureReference = { relativePath: "research-fixture.json", mediaType: "application/json", sha256: byteHash(researchFixtureBytes), bytes: researchFixtureBytes.length };
  await privateWrite(researchRunInput, {
    schemaVersion: 1, operation: "run", runId: researchId, now: "2026-08-13T15:00:00.000Z", idSuffix: researchId.slice(-12),
    admission: { profile: "researcher", comparisonLane: "same-model-harness", bindings: { researchFixtureSha256: researchFixtureReference.sha256 } },
    task: { profile: "researcher", comparisonLane: "same-model-harness", bindings: { researchFixture: researchFixtureReference } }, requestPayloadBase64: Buffer.from("research it").toString("base64"),
    sourcePath, sourceReference: { mediaType: "application/x-tar", sha256: byteHash(source), bytes: source.length },
    researchFixturePath, researchFixtureReference,
    environment: {}, toolPolicy: {}, verifierDefinition: {},
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const researchResult = await runPixelSystem(paths.config, researchRunInput, dependencies);
  assert.equal(researchResult.finalMessage, "researcher done");
  assert.equal(researchResult.independentVerification.status, "evidence-pass");
  const researchStopInput = join(root, "research-stop.json");
  await privateWrite(researchStopInput, { schemaVersion: 1, operation: "stop", runId: researchId });
  await stopPixelSystem(paths.config, researchStopInput, dependencies);

  const assistantNoPortId = "outcomerun-1786622400103-123456abcdef";
  const assistantNoPortInput = join(root, "assistant-no-port-start.json");
  await privateWrite(assistantNoPortInput, {
    schemaVersion: 1, operation: "start", runId: assistantNoPortId, profile: "assistant",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  await assert.rejects(
    startPixelSystem(paths.config, assistantNoPortInput, dependencies),
    /exact loopback-only model endpoint/u,
  );
  await assert.rejects(readFile(join(runtimeRoot, assistantNoPortId, "config", "system-state.json")), /ENOENT/u);

  backend.publishLoopbackPort = 45678;
  await privateWrite(paths.backend, backend);
  await installFixtureModelQualification(policy, root, qualificationNow, { failingProfile: "assistant" });
  await privateWrite(enabledPolicyPath, policy);
  const assistantUnqualifiedId = "outcomerun-1786622400105-123456abcdef";
  const assistantUnqualifiedInput = join(root, "assistant-unqualified-start.json");
  await privateWrite(assistantUnqualifiedInput, {
    schemaVersion: 1, operation: "start", runId: assistantUnqualifiedId, profile: "assistant",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  await assert.rejects(
    startPixelSystem(paths.config, assistantUnqualifiedInput, dependencies),
    /insufficient for the assistant profile/u,
  );
  await installFixtureModelQualification(policy, root, qualificationNow);
  await privateWrite(enabledPolicyPath, policy);
  const assistantId = "outcomerun-1786622400104-123456abcdef";
  const assistantStartInput = join(root, "assistant-start.json");
  await privateWrite(assistantStartInput, {
    schemaVersion: 1, operation: "start", runId: assistantId, profile: "assistant",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  const assistantReceipt = await startPixelSystem(paths.config, assistantStartInput, dependencies);
  assert.equal(assistantReceipt.profile, "assistant");
  assert.equal(assistantReceipt.qualificationReceiptSha256, policy.localModel.qualification.receiptSha256);
  const assistantStopInput = join(root, "assistant-stop.json");
  await privateWrite(assistantStopInput, { schemaVersion: 1, operation: "stop", runId: assistantId });
  await stopPixelSystem(paths.config, assistantStopInput, dependencies);

  const staleId = "outcomerun-1786622400101-123456abcdef", staleInput = join(root, "stale-start.json");
  await privateWrite(staleInput, {
    schemaVersion: 1, operation: "start", runId: staleId, profile: "builder",
    modelContractBase64: modelBytes.toString("base64"), modelContractSha256: byteHash(modelBytes),
    inferenceContractBase64: inferenceBytes.toString("base64"), inferenceContractSha256: byteHash(inferenceBytes),
  });
  await assert.rejects(startPixelSystem(paths.config, staleInput, {
    ...dependencies, startModelBackend: async () => ({ state: "ready-already-running", checks: { readinessPassed: true } }),
  }), /freshly started/u);
  await assert.rejects(readFile(join(runtimeRoot, staleId, "config", "system-state.json")), /ENOENT/u);
});

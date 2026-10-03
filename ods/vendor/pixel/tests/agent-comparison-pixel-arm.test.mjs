import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildPixelAssistantExecution, buildPixelBuilderJob, buildPixelControllerGoal, buildPixelResearcherJob, PixelArmError,
  reviewPixelProductCompatibility, reviewPixelPlannedCompatibility, runPixelBuilderArm, runPixelControllerArm, runPixelProductArm,
} from "../deploy/agent-comparison/pixel-arm.mjs";
import { canonical, compileBuilder, compileResearcher } from "../deploy/work-broker/broker.mjs";
import { buildResearchRevisionHistory, executeResearchRevisionReview } from "../deploy/work-controller/research-revision-review.mjs";
import { validateWorkResearchBatch } from "../scripts/lib/work-contract.mjs";

const sha = (value) => createHash("sha256").update(value).digest("hex");
const NOW = new Date("2026-08-13T12:00:00Z");
const SUFFIX = "abcdef123456";
const RUNNER_DIGEST = `sha256:${"e".repeat(64)}`;
const MODEL_DIGEST = `sha256:${"a".repeat(64)}`;
const MODEL_ARTIFACT = "b".repeat(64);

async function fixture() {
  const requestPayload = Buffer.from("Fix the bug and prove the implementation against fresh inputs.\n", "utf8");
  const sourceReference = { mediaType: "application/x-tar", sha256: "c".repeat(64), bytes: 4096 };
  const inference = {
    enforcement: "exact-request-boundary-v1", wireApi: "openai-chat-completions",
    temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
    repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default", reasoningVisibility: "hidden",
  };
  const modelContract = {
    modelId: "DeepSeek-V4-Flash-0731",
    artifact: { sha256: MODEL_ARTIFACT },
    runtime: { implementation: "vllm", imageDigest: MODEL_DIGEST, contextWindow: 131072 },
  };
  const inferenceContract = {
    sampling: {
      temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
      repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default", reasoningVisibility: "hidden",
    },
    request: {
      wireApi: "openai-chat-completions", stream: true, maxOutputTokens: 4096,
      toolEncoding: "function", requestFieldPolicySha256: "", promptCachePolicy: "empty-at-run-start",
    },
  };
  inferenceContract.request.requestFieldPolicySha256 = sha(canonical({
    ...inference, stream: true, maxOutputTokens: 4096, toolEncoding: "function",
  }));
  const workPolicy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  workPolicy.enabled = true;
  workPolicy.runner.prepared = true;
  workPolicy.runner.imageDigest = RUNNER_DIGEST;
  workPolicy.runner.imageRef = `local/pixel-work-runner@${RUNNER_DIGEST}`;
  Object.assign(workPolicy.localModel, {
    prepared: true, provider: "vllm", id: modelContract.modelId,
    imageDigest: MODEL_DIGEST, imageRef: `local/pixel-vllm@${MODEL_DIGEST}`,
    modelArtifactSha256: MODEL_ARTIFACT, backendVersion: "0.10.1",
    acceleratorClass: "nvidia-cuda", contextWindow: 131072, maxRequestOutputTokens: 4096,
    inference,
  });
  workPolicy.profiles.builder.enabled = true;
  workPolicy.profiles.scout.enabled = true;
  workPolicy.verifier.enabled = true;
  workPolicy.verifier.allowedExecutables = ["/usr/bin/python3"];
  const toolPolicy = {
    workspace: "disposable-read-write",
    tools: ["debug", "edit", "eval", "hub", "lsp", "read", "search", "shell", "task", "write"],
    brokeredServices: ["local-model"], maximumSubagents: 1,
  };
  const environment = {
    platform: { operatingSystem: "linux", architecture: "amd64", distribution: "debian-12", locale: "C.UTF-8", timeZone: "UTC" },
    isolation: { workspace: "fresh-disposable-read-write", controlFiles: "inert", directNetwork: false, packageInstallation: false, inheritedEnvironment: false, inheritedFileDescriptors: false, crossRunState: false },
    limits: { maxIterations: 20, maxToolCalls: 2000, maxConcurrentSubagents: 1, maxCpuCores: 4, maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxNetworkBytes: 10485760, maxFailures: 5, noProgressLimit: 3, maxPids: 1024 },
    verifier: { imageDigest: RUNNER_DIGEST, allowedExecutables: ["/usr/bin/python3"], maxChecks: 16, maxRuntimeSeconds: 900, maxOutputBytes: 1048576, network: "none" },
  };
  const workspaceVerification = {
    mode: "independent",
    checks: [
      { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
      { id: "semantic", kind: "command", criterionIndexes: [0], workingDirectory: "source", argv: ["/usr/bin/python3", "-c", "print('OK')"], timeoutSeconds: 60, maxOutputBytes: 65536 },
    ],
    immutablePathPrefixes: ["source/__pixel_inert__/"], maxRuntimeSeconds: 60,
    maxOutputBytes: 65536, network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
  const verifierDefinition = { acceptanceCriteria: ["Fresh semantic check passes"], workspaceVerification };
  const task = { profile: "builder", comparisonLane: "same-model-harness", createdAt: NOW.toISOString(), dataClass: "public" };
  const admission = {
    status: "admitted-inert", profile: "builder", comparisonLane: "same-model-harness",
    bindings: { userRequestSha256: sha(requestPayload), sourceSnapshotSha256: sourceReference.sha256 },
    budgets: { wallTimeSeconds: 600, modelRequests: 20, inputTokens: 100000, outputTokens: 20000, artifactBytes: 16777216 },
  };
  return {
    admission, task, requestPayload, sourceReference, environment, toolPolicy, verifierDefinition,
    modelContract, inferenceContract, workPolicy, now: NOW, idSuffix: SUFFIX,
  };
}

async function researcherFixture() {
  const value = await fixture();
  value.requestPayload = Buffer.from("Research the frozen public safety topic with cited evidence.\n", "utf8");
  value.task = { ...value.task, profile: "researcher", dataClass: "public" };
  value.admission = {
    ...value.admission, profile: "researcher",
    bindings: { ...value.admission.bindings, userRequestSha256: sha(value.requestPayload) },
  };
  value.toolPolicy = {
    workspace: "disposable-read-write",
    tools: ["edit", "public-research", "read", "search", "shell", "write"],
    brokeredServices: ["local-model", "public-research"], maximumSubagents: 1,
  };
  value.verifierDefinition = { acceptanceCriteria: ["Return independently citation-checked findings"], workspaceVerification: null };
  value.workPolicy.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536,
    maxBudgets: structuredClone(value.workPolicy.profiles.builder.maxBudgets),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 50, maxResultsPerQuery: 10, maxSources: 250, maxSourceBytes: 4194304,
      maxTotalSourceBytes: 268435456, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  return value;
}

async function assistantFixture() {
  const value = await fixture();
  value.requestPayload = Buffer.from("Inspect and improve the admitted workspace using only the exact local lease.\n", "utf8");
  value.task = { ...value.task, profile: "assistant" };
  value.admission = {
    ...value.admission, profile: "assistant",
    bindings: { ...value.admission.bindings, userRequestSha256: sha(value.requestPayload) },
  };
  return value;
}

async function controllerFixture() {
  const value = await fixture();
  value.requestPayload = Buffer.from("Pursue the admitted objective through durable supervised work until the independent checks pass.\n", "utf8");
  value.task = { ...value.task, profile: "controller" };
  value.admission = {
    ...value.admission, profile: "controller",
    bindings: { ...value.admission.bindings, userRequestSha256: sha(value.requestPayload) },
  };
  return value;
}

test("Pixel Builder adapter compiles the neutral contract through the real Work Broker without widening", async () => {
  const value = await fixture();
  const built = buildPixelBuilderJob(value);
  assert.equal(built.request.profile, "builder");
  assert.equal(built.request.objective, "Fix the bug and prove the implementation against fresh inputs.");
  assert.deepEqual(built.request.verification, value.verifierDefinition.workspaceVerification);
  assert.deepEqual(built.request.requestedCapabilities.tools, value.workPolicy.profiles.builder.tools);
  assert.deepEqual(built.request.requestedCapabilities.network.services, ["local-model"]);
  assert.equal(built.request.requestedCapabilities.hostAccess, false);
  assert.equal(built.request.requestedCapabilities.externalEffects, false);
  assert.equal(built.request.budgets.maxModelRequests, value.admission.budgets.modelRequests);
  assert.equal(built.request.budgets.maxConcurrentSubagents, value.toolPolicy.maximumSubagents);
  const compiled = compileBuilder(built.request, value.workPolicy, built.entries, { now: NOW, suffix: SUFFIX });
  assert.equal(compiled.plan.model.id, "DeepSeek-V4-Flash-0731");
  assert.equal(compiled.plan.model.provider, "vllm");
  assert.deepEqual(compiled.plan.verification, value.verifierDefinition.workspaceVerification);
  assert.ok(Object.values(compiled.plan.authority).every((flag) => flag === false));
});

test("Pixel Controller derives an immutable durable goal around a real DSV4 Builder child without relabeling the child", async () => {
  const value = await controllerFixture();
  const built = buildPixelControllerGoal(value);
  assert.equal(built.request.profile, "builder");
  assert.equal(built.goal.objective, value.requestPayload.toString("utf8").trim());
  assert.equal(built.goal.milestones.length, 1);
  assert.equal(built.goal.milestones[0].profile, "builder");
  assert.equal(built.goal.milestones[0].jobId, built.request.jobId);
  assert.equal(built.goal.milestones[0].jobSha256, sha(canonical(built.request)));
  assert.equal(built.goal.completion.workerClaimSufficient, false);
  assert.equal(built.goal.completion.independentVerificationRequired, true);
  assert.ok(Object.values(built.goal.authority).every((flag) => flag === false));
  const review = reviewPixelProductCompatibility(value);
  assert.equal(review.profile, "controller");
  assert.equal(review.taskAdmissionSha256, sha(canonical(value.admission)));
  assert.match(review.workRequestSha256, /^[a-f0-9]{64}$/u);
  assert.match(review.workPlanSha256, /^[a-f0-9]{64}$/u);
});

test("Pixel Assistant compiles a broad neutral lease into exact OpenClaw tools without wildcard authority", async () => {
  const value = await assistantFixture();
  const built = buildPixelAssistantExecution(value);
  assert.equal(built.request.profile, "assistant");
  assert.equal(built.request.externalEffects, false);
  assert.deepEqual(built.request.services, ["local-model"]);
  assert.deepEqual(built.request.tools, [
    "apply_patch", "debug", "edit", "eval", "exec", "glob", "grep", "hub", "lsp", "memory_get",
    "memory_search", "process", "read", "session_status", "sessions_history", "sessions_list", "sessions_send",
    "sessions_spawn", "sessions_yield", "subagents", "task", "todo", "write", "yield",
  ]);
  assert.equal(built.plan.limbs.email, false);
  assert.equal(built.plan.limbs.calendar, false);
  assert.equal(built.plan.limbs.operations, false);
  assert.equal(built.plan.limbs.web, false);
  const review = reviewPixelProductCompatibility(value);
  assert.equal(review.profile, "assistant");
  assert.equal(review.taskAdmissionSha256, sha(canonical(value.admission)));
  assert.match(review.workPlanSha256, /^[a-f0-9]{64}$/u);

  const brokered = structuredClone(value);
  brokered.toolPolicy.tools = ["calendar-read", "calendar-write", "email-read", "fleet-read", "public-research", "read"];
  brokered.toolPolicy.brokeredServices = ["calendar", "email", "fleet", "local-model", "public-research"];
  const leased = buildPixelAssistantExecution(brokered);
  assert.equal(leased.plan.limbs.calendar, true);
  assert.equal(leased.plan.limbs.email, true);
  assert.equal(leased.plan.limbs.operations, true);
  assert.equal(leased.plan.limbs.web, true);
  assert.ok(leased.request.tools.includes("pixel_calendar_propose_create"));
  assert.ok(leased.request.tools.includes("pixel_ops_inventory"));
  assert.ok(leased.request.tools.includes("web_search"));

  for (const mutate of [
    (candidate) => { candidate.toolPolicy.tools = ["github-write"]; candidate.toolPolicy.brokeredServices = ["github", "local-model"]; },
    (candidate) => { candidate.toolPolicy.brokeredServices.push("email"); },
    (candidate) => { candidate.environment.isolation.workspace = "fresh-read-only"; },
  ]) {
    const candidate = structuredClone(value); mutate(candidate);
    assert.throws(() => buildPixelAssistantExecution(candidate), PixelArmError);
  }
});

test("Pixel compatibility review refreshes immutable corpus time and refuses an undersized private policy", async () => {
  const value = await fixture();
  value.task.createdAt = "2026-08-01T00:00:00.000Z";
  value.environment.limits.maxIterations = value.workPolicy.profiles.builder.maxBudgets.maxIterations + 1;
  value.environment.limits.maxToolCalls = value.workPolicy.profiles.builder.maxBudgets.maxToolCalls + 1;
  assert.throws(() => reviewPixelProductCompatibility(value), /budget/u);
  value.workPolicy.profiles.builder.maxBudgets.maxIterations = value.environment.limits.maxIterations;
  value.workPolicy.profiles.builder.maxBudgets.maxToolCalls = value.environment.limits.maxToolCalls;
  const review = reviewPixelProductCompatibility(value);
  assert.equal(review.profile, "builder");
  assert.equal(review.taskAdmissionSha256, sha(canonical(value.admission)));
  assert.match(review.workPlanSha256, /^[a-f0-9]{64}$/u);
  const built = buildPixelBuilderJob(value);
  assert.equal(built.request.createdAt, NOW.toISOString());
  assert.notEqual(built.request.createdAt, value.task.createdAt);
});

test("planned compatibility proves the full envelope without fabricating readiness, a plan, or a lease", async () => {
  for (const value of [await fixture(), await researcherFixture(), await assistantFixture(), await controllerFixture()]) {
    value.workPolicy.enabled = false;
    value.workPolicy.localModel.prepared = false;
    assert.throws(() => reviewPixelProductCompatibility(value), /disabled or unprepared/u);
    const review = reviewPixelPlannedCompatibility(value);
    assert.equal(review.operation, "pixel-portal-outcome-planned-task-compatibility");
    assert.equal(review.profile, value.admission.profile);
    assert.equal(review.readiness, "qualification-required");
    assert.equal(review.taskAdmissionSha256, sha(canonical(value.admission)));
    assert.equal(review.workPolicySha256, sha(canonical(value.workPolicy)));
    assert.match(review.inputSetSha256, /^[a-f0-9]{64}$/u);
    assert.equal(Object.hasOwn(review, "workPlanSha256"), false);
    assert.equal(Object.hasOwn(review, "leaseSha256"), false);
  }
  const widened = await fixture();
  widened.workPolicy.enabled = false;
  widened.workPolicy.localModel.prepared = false;
  widened.environment.limits.maxToolCalls = widened.workPolicy.profiles.builder.maxBudgets.maxToolCalls + 1;
  assert.throws(() => reviewPixelPlannedCompatibility(widened), /budget/u);
});

test("Pixel Controller composes durable parent, custody, and child transitions around the actual Builder lifecycle", async () => {
  const value = await controllerFixture(), calls = [];
  const payloads = new Map([
    ["/private/patch.json", Buffer.from("{\"patch\":true}\n")],
    ["/private/evidence.json", Buffer.from("{\"worker\":true}\n")],
    ["/private/verification.json", Buffer.from("{\"status\":\"pass\"}\n")],
  ]);
  const record = (path) => ({ path, bytes: payloads.get(path).length, sha256: sha(payloads.get(path)) });
  let compiledPlan, terminalCheckpoint, custodyHead;
  const options = {
    ...value, objectStore: "/private/objects", workspaceRoot: "/private/workspaces",
    executorPath: "/private/omp", stateRoot: "/private/state", archiveLimits: {}, lifecycleOptions: {},
  };
  const dependencies = {
    compileBuilder(request, policy, entries, options) {
      calls.push("compile");
      const compiled = compileBuilder(request, policy, entries, options); compiledPlan = compiled.plan; return compiled;
    },
    async prepareBuilderRun(compiled) {
      calls.push("prepare");
      return { ...compiled, workspace: { discardPath: "/private/workspaces/.prepare-controller", root: "/private/workspaces", sha256: "a".repeat(64) } };
    },
    async initializeGoalRunBundle() { calls.push("custody-initialize"); },
    async admitGoalRunBundle() { calls.push("custody-admit"); },
    async initializeGoalLedger() { calls.push("goal-initialize"); },
    async dispatchGoalMilestone() { calls.push("goal-dispatch"); },
    async initializeCheckpointLedger() { calls.push("child-initialize"); },
    async executeBuilderIteration({ prepared }) {
      calls.push("child-execute");
      const claimId = "workclaim-1786600000000-abcdef123456", candidateSha256 = "9".repeat(64);
      const verificationEvidence = {
        schemaVersion: 1, format: "pixel-independent-verification-v1",
        jobId: prepared.plan.jobId, claimId, planSha256: sha(canonical(prepared.plan)),
        patchSha256: "8".repeat(64), candidateSha256, status: "pass",
        checks: [
          { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: "7".repeat(64), changes: 1, files: 2, bytes: 10 },
          { id: "semantic", kind: "command", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: "6".repeat(64), runtimeMilliseconds: 12, exitCode: 0, signal: null, timedOut: false, outputLimitExceeded: false, spawnFailed: false, stdoutBytes: 3, stdoutSha256: "5".repeat(64), stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)) },
        ],
        criteria: [{ index: 0, status: "pass", checkIds: ["patch-boundary", "semantic"] }],
        network: "none", workerSelectedChecks: false, externalEffects: false,
        boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
      };
      terminalCheckpoint = {
        state: "completed", iteration: 1,
        usage: { runtimeSeconds: 2, modelRequests: 3, inputTokens: 1000, outputTokens: 200, networkBytes: 4096, artifactBytes: 256, failures: 0 },
        verificationEvidenceSha256: sha(canonical(verificationEvidence)),
      };
      return {
        action: "completed", checkpoint: terminalCheckpoint,
        candidate: {
          text: "Completed through the durable goal.", toolCalls: 4,
          proxyReceipt: { modelRequests: 3, inputTokens: 1000, outputTokens: 200, networkBytes: 4096 },
          patch: record("/private/patch.json"), evidence: record("/private/evidence.json"),
        },
        verification: { artifact: record("/private/verification.json"), evidence: verificationEvidence },
      };
    },
    async observeGoalMilestone() { calls.push("goal-observe"); },
    async completeGoal() { calls.push("goal-complete"); },
    async discardPreparedRun() { calls.push("discard"); },
    async recoverGoalLedger({ goal }) {
      calls.push("goal-recover");
      const checkpoints = [
        { sequence: 0, state: "ready", previousCheckpointSha256: null },
        { sequence: 1, state: "running", previousCheckpointSha256: "1".repeat(64) },
        { sequence: 2, state: "ready", previousCheckpointSha256: "2".repeat(64) },
        { sequence: 3, state: "completed", previousCheckpointSha256: "3".repeat(64) },
      ];
      return { head: { state: "completed", active: null, progress: { milestonesTotal: 1, milestonesCompleted: 1, jobsStarted: 1 } }, checkpoints, headSha256: sha(canonical(checkpoints.at(-1))), goal };
    },
    async recoverCheckpointLedger() {
      calls.push("child-recover");
      return { head: terminalCheckpoint, checkpoints: [terminalCheckpoint], headSha256: sha(canonical(terminalCheckpoint)) };
    },
    async recoverGoalRunBundles({ goal, jobs }) {
      calls.push("custody-recover");
      const job = jobs[0];
      const common = {
        goalId: goal.goalId, jobId: job.jobId,
        goalSha256: sha(canonical(goal)), jobSha256: sha(canonical(job)),
        plan: compiledPlan,
        authority: {
          containsExactLease: true, grantsBeyondEmbeddedLease: false, grantsReplay: false,
          grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
        },
      };
      const initial = { ...common, sequence: 0, purpose: "initial", previousBundleSha256: null, lease: { iteration: 1 } };
      custodyHead = {
        ...common, sequence: 1, purpose: "admission", previousBundleSha256: sha(canonical(initial)), lease: { iteration: 1 },
      };
      return { head: custodyHead, bundles: [initial, custodyHead], headSha256: sha(canonical(custodyHead)) };
    },
    async readFile(path) { return payloads.get(path); },
    monotonic: (() => { const values = [100, 275]; return () => values.shift(); })(),
  };
  const result = await runPixelControllerArm(options, dependencies);
  assert.deepEqual(calls, [
    "compile", "prepare", "custody-initialize", "custody-admit", "goal-initialize", "goal-dispatch",
    "child-initialize", "child-execute", "goal-observe", "goal-complete", "discard",
    "goal-recover", "child-recover", "custody-recover",
  ]);
  assert.equal(result.exitCode, 0);
  assert.equal(result.controllerEvidence.status, "pass");
  assert.equal(result.controllerEvidence.parent.state, "completed");
  assert.equal(result.controllerEvidence.child.profile, "builder");
  assert.equal(result.controllerEvidence.cleanup.ephemeralPreparationComplete, true);
  assert.equal(result.controllerEvidence.authority.grantsCompletion, false);
  assert.equal(result.usage.toolCalls, 4);
  assert.equal(result.artifacts.at(-1).relativePath, "pixel-controller-evidence.json");
  await assert.rejects(
    runPixelControllerArm(options, {
      ...dependencies,
      async recoverGoalRunBundles(options) {
        const recovered = await dependencies.recoverGoalRunBundles(options);
        return { ...recovered, bundles: [recovered.head] };
      },
      monotonic: (() => { const values = [300, 475]; return () => values.shift(); })(),
    }),
    /custody record count/u,
  );
});

test("Pixel Builder adapter rejects model, tool, verifier, and source drift before compilation", async () => {
  const base = await fixture();
  for (const [label, mutate] of [
    ["model", (value) => { value.workPolicy.localModel.id = "fixture-qwen"; }],
    ["tool", (value) => { value.toolPolicy.tools = value.toolPolicy.tools.filter((tool) => tool !== "debug"); }],
    ["verifier", (value) => { value.environment.verifier.allowedExecutables = ["/bin/sh"]; }],
    ["source", (value) => { value.sourceReference.sha256 = "d".repeat(64); }],
  ]) {
    const value = structuredClone(base);
    mutate(value);
    assert.throws(() => buildPixelBuilderJob(value), PixelArmError, label);
  }
});

test("Pixel Builder adapter drives verifier-governed continuation leases to acceptance and records cumulative usage", async () => {
  const value = await fixture();
  const calls = [];
  const payloads = new Map([
    ["/private/patch.json", Buffer.from("{\"patch\":true}\n")],
    ["/private/evidence.json", Buffer.from("{\"worker\":true}\n")],
    ["/private/verification.json", Buffer.from("{\"status\":\"pass\"}\n")],
  ]);
  const record = (path) => ({ path, bytes: payloads.get(path).length, sha256: sha(payloads.get(path)) });
  const result = await runPixelBuilderArm({
    ...value, objectStore: "/private/objects", workspaceRoot: "/private/workspaces",
    executorPath: "/private/omp", stateRoot: "/private/state", archiveLimits: {}, lifecycleOptions: {},
  }, {
    compileBuilder(request, policy, entries, options) {
      calls.push("compile");
      return compileBuilder(request, policy, entries, options);
    },
    async prepareBuilderRun(compiled) {
      calls.push(compiled.continuation ? "prepare-continuation" : "prepare-initial");
      if (compiled.continuation) assert.equal(compiled.continuation.patchPath, "/private/patch.json");
      return { ...compiled, workspace: { discardPath: "/private/workspaces/.prepare-work-a", root: "/private/workspaces", sha256: "a".repeat(64) } };
    },
    async initializeCheckpointLedger(value) {
      calls.push("initialize");
      assert.equal(value.workspaceSnapshotSha256, "a".repeat(64));
    },
    async executeBuilderIteration({ prepared }) {
      const iteration = calls.filter((item) => item.startsWith("iterate-")).length + 1;
      calls.push(`iterate-${iteration}`);
      const claimId = iteration === 1 ? "workclaim-1786600000000-abcdef123456" : "workclaim-1786600001000-abcdef123456";
      const candidateSha256 = "9".repeat(64);
      const verificationEvidence = {
        schemaVersion: 1, format: "pixel-independent-verification-v1",
        jobId: prepared.plan.jobId, claimId,
        planSha256: sha(canonical(prepared.plan)),
        patchSha256: "8".repeat(64), candidateSha256, status: "pass",
        checks: [
          { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: "7".repeat(64), changes: 1, files: 2, bytes: 10 },
          { id: "semantic", kind: "command", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: "6".repeat(64), runtimeMilliseconds: 12, exitCode: 0, signal: null, timedOut: false, outputLimitExceeded: false, spawnFailed: false, stdoutBytes: 3, stdoutSha256: "5".repeat(64), stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)) },
        ],
        criteria: [{ index: 0, status: "pass", checkIds: ["patch-boundary", "semantic"] }],
        network: "none", workerSelectedChecks: false, externalEffects: false,
        boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
      };
      const candidate = {
        text: "Done", durationMilliseconds: 1200, toolCalls: 4,
        proxyReceipt: { modelRequests: 3, inputTokens: 1000, outputTokens: 200, networkBytes: 4096 },
        patch: record("/private/patch.json"), evidence: record("/private/evidence.json"),
      };
      const verification = { artifact: record("/private/verification.json"), evidence: verificationEvidence };
      const checkpoint = {
        state: iteration === 1 ? "verified" : "completed",
        usage: { modelRequests: iteration * 3, inputTokens: iteration * 1000, outputTokens: iteration * 200, networkBytes: iteration * 4096 },
      };
      if (iteration === 1) return {
        action: "continue", candidate, verification, checkpoint,
        claim: { claimId },
        nextLease: { lease: { ...prepared.lease, iteration: 2, issuedAt: "2026-08-13T12:00:01.000Z", expiresAt: "2026-08-13T12:10:01.000Z" } },
      };
      return { action: "completed", candidate, verification, checkpoint, claim: { claimId }, nextLease: null };
    },
    async readFile(path) { calls.push(`read:${path}`); return payloads.get(path); },
    async discardPreparedRun() { calls.push("discard"); },
    monotonic: (() => { const values = [1000, 2401]; return () => values.shift(); })(),
  });
  assert.deepEqual(calls.filter((item) => !item.startsWith("read:")), [
    "compile", "prepare-initial", "initialize", "iterate-1", "discard",
    "prepare-continuation", "iterate-2", "discard",
  ]);
  assert.equal(calls.filter((item) => item === "discard").length, 2);
  assert.equal(result.independentVerification.status, "pass");
  assert.equal(result.usage.modelRequests, 6);
  assert.equal(result.usage.toolCalls, 8);
  assert.equal(result.latencyMs, 1401);
  assert.deepEqual(result.artifacts.map((artifact) => artifact.kind), ["finding-report", "patch", "test-evidence", "test-evidence"]);
});

test("Pixel Builder adapter refuses forged completion and bounds endless continuation", async () => {
  const value = await fixture();
  value.environment.limits.maxIterations = 2;
  value.workPolicy.profiles.builder.maxBudgets.maxIterations = 2;
  const options = {
    ...value, objectStore: "/private/objects", workspaceRoot: "/private/workspaces",
    executorPath: "/private/omp", stateRoot: "/private/state", archiveLimits: {}, lifecycleOptions: {},
  };
  const base = {
    async prepareBuilderRun(compiled) {
      return { ...compiled, workspace: { discardPath: "/private/workspaces/.prepare-work-a", root: "/private/workspaces", sha256: "a".repeat(64) } };
    },
    async initializeCheckpointLedger() {}, async discardPreparedRun() {},
    monotonic: () => 1,
  };
  await assert.rejects(runPixelBuilderArm(options, {
    ...base,
    async executeBuilderIteration() {
      return {
        action: "completed", checkpoint: { state: "verified", usage: {} },
        candidate: { toolCalls: 0, patch: { path: "/private/patch.json" }, evidence: { path: "/private/evidence.json" } },
        verification: { artifact: { path: "/private/verification.json" }, evidence: {} },
      };
    },
  }), /missing retained artifacts/u);

  let iterations = 0;
  await assert.rejects(runPixelBuilderArm(options, {
    ...base,
    async executeBuilderIteration({ prepared }) {
      iterations += 1;
      return {
        action: "continue", candidate: { toolCalls: 0, patch: { path: "/private/patch.json" } },
        verification: {}, checkpoint: { state: "verified" }, claim: { claimId: `claim-${iterations}` },
        nextLease: { lease: { ...prepared.lease, iteration: prepared.lease.iteration + 1, issuedAt: `2026-08-13T12:00:0${iterations}.000Z`, expiresAt: `2026-08-13T12:10:0${iterations}.000Z` } },
      };
    },
  }), /iteration ceiling/u);
  assert.equal(iterations, 2);
});

test("Pixel Researcher adapter compiles the neutral public-research envelope through the real Work Broker", async () => {
  const value = await researcherFixture();
  const built = buildPixelResearcherJob(value);
  assert.equal(built.request.profile, "researcher");
  assert.equal(built.request.dataClassification, "public");
  assert.deepEqual(built.request.requestedCapabilities.tools, value.workPolicy.profiles.researcher.tools);
  assert.deepEqual(built.request.requestedCapabilities.network.services, ["local-model", "research-broker"]);
  assert.equal(built.request.research.queryPolicy, "public-sanitized");
  assert.equal(built.request.research.citationVerification, true);
  assert.equal(built.request.research.maxQueries, 20);
  assert.equal(built.request.requestedCapabilities.externalEffects, false);
  const compiled = compileResearcher(built.request, value.workPolicy, built.entries, { now: NOW, suffix: SUFFIX });
  assert.equal(compiled.plan.profile, "researcher");
  assert.equal(compiled.plan.researchBackend.webCourierRequired, true);
  assert.ok(Object.values(compiled.plan.authority).every((flag) => flag === false));

  for (const mutate of [
    (candidate) => { candidate.task.dataClass = "private"; },
    (candidate) => { candidate.toolPolicy.tools = candidate.toolPolicy.tools.filter((tool) => tool !== "public-research"); },
    (candidate) => { candidate.toolPolicy.brokeredServices = ["local-model"]; },
    (candidate) => { candidate.workPolicy.profiles.researcher.backend.prepared = false; },
  ]) {
    const candidate = structuredClone(value); mutate(candidate);
    assert.throws(() => buildPixelResearcherJob(candidate), PixelArmError);
  }
});

test("profile-aware product arm runs Researcher lifecycle and binds retained citation evidence", async () => {
  const value = await researcherFixture(), calls = [];
  let criticCandidate;
  const payloads = new Map();
  const record = (path, payload) => { payloads.set(path, payload); return { path, bytes: payload.length, sha256: sha(payload) }; };
  const result = await runPixelProductArm({
    ...value, objectStore: "/private/objects", workspaceRoot: "/private/workspaces",
    executorPath: "/private/omp", stateRoot: "/private/state", archiveLimits: {},
    lifecycleOptions: { researchRevisionReview: true },
  }, {
    compileResearcher(request, policy, entries, options) { calls.push("compile"); return compileResearcher(request, policy, entries, options); },
    async prepareResearcherRun(compiled) { calls.push("prepare"); return { ...compiled, workspace: { discardPath: "/private/workspaces/.prepare-work-r", root: "/private/workspaces" } }; },
    createLeaseConsumption(prepared) {
      calls.push("consume");
      return { claimId: "workclaim-1786600000000-abcdef123456", jobId: prepared.plan.jobId, planSha256: prepared.planSha256 };
    },
    async claimLease() { calls.push("claim"); },
    async runResearcherDockerLifecycle(prepared, claim) {
      calls.push("lifecycle");
      const evidence = Buffer.from("Public exact evidence", "utf8");
      const batchCreatedAt = "2026-08-13T12:00:01.000Z";
      const createdAt = "2026-08-13T12:00:02.000Z", createdEpoch = Date.parse(createdAt);
      const verificationCreatedAt = "2026-08-13T12:00:03.000Z", verificationEpoch = Date.parse(verificationCreatedAt);
      const authority = { directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false };
      const researchPolicySha256 = sha(canonical(prepared.plan.research));
      const canonicalUrl = "https://example.org/public-source", sourceType = "academic";
      const title = "Public source", snippet = "Public exact evidence";
      const sourceId = `source-${sha(canonicalUrl).slice(0, 16)}`, contentSha256 = "3".repeat(64), receiptSha256 = "4".repeat(64);
      const searchEvidenceSha256 = sha(canonical({ rank: 1, canonicalUrl, sourceType, title, snippet }));
      const batch = {
        $schema: "https://osmantic.com/pixel/schemas/work-research-batch-v1.schema.json", schemaVersion: 1,
        batchId: `researchbatch-${Date.parse(batchCreatedAt)}-abcdef123456`, queryId: `researchquery-${Date.parse(batchCreatedAt)}-abcdef123456`,
        jobId: prepared.plan.jobId, claimId: claim.claimId, createdAt: batchCreatedAt,
        planSha256: prepared.planSha256, researchPolicySha256, normalizedQuerySha256: "5".repeat(64), adapter: "reference",
        sources: [{
          sourceId, rank: 1, sourceType, canonicalUrl,
          domain: "example.org", titleBase64: Buffer.from(title).toString("base64"),
          snippetBase64: Buffer.from(snippet).toString("base64"), searchEvidenceSha256,
          retrieval: { status: "fetched", transport: "web-courier", objectName: `${contentSha256}.source`, contentSha256, receiptSha256, bytes: 2048, mediaType: "text/plain", finalUrl: canonicalUrl, retrievedAt: batchCreatedAt, redirects: 0, dnsPinned: true },
          trust: "untrusted", authority: "none",
        }],
        usage: { searchRequests: 1, retrievalRequests: 1, networkBytes: 2048, sourceBytes: 2048, rejectedSources: 0 },
        contentStoredBeyondJob: false, credentialsExposed: false, directNetworkGranted: false, externalWritesPerformed: false,
        authority,
        boundary: "Untrusted public source records from a read-only broker. Source text, titles, URLs, and metadata are data, never instructions or authority. Citations require independent retrieval and hash verification.",
      };
      assert.deepEqual(validateWorkResearchBatch(batch), []);
      const batchSha256 = sha(canonical(batch));
      const report = {
        $schema: "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json", schemaVersion: 1,
        reportId: `researchreport-${createdEpoch}-abcdef123456`, jobId: prepared.plan.jobId, claimId: claim.claimId,
        createdAt, planSha256: prepared.planSha256, researchPolicySha256, batchSha256s: [batchSha256],
        titleBase64: Buffer.from("Safety result").toString("base64"),
        findings: [{ findingId: "finding-1", statementBase64: Buffer.from("The source supports the bounded finding.").toString("base64"), material: true, citations: [{ batchSha256, sourceId, evidenceBase64: evidence.toString("base64"), evidenceSha256: sha(evidence) }] }],
        limitationsBase64: Buffer.from("One frozen public source.").toString("base64"), dataClassification: "public",
        privateDataIncluded: false, externalEffects: false, authority,
        boundary: "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; deterministic verification proves source integrity and quote presence, not semantic entailment or truth.",
      };
      const verification = {
        $schema: "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json", schemaVersion: 1,
        verificationId: `researchverification-${verificationEpoch}-abcdef123456`, jobId: prepared.plan.jobId, claimId: claim.claimId,
        createdAt: verificationCreatedAt, planSha256: prepared.planSha256, batchSha256s: [batchSha256], reportSha256: sha(canonical(report)),
        status: "evidence-pass", verificationLevel: "deterministic-evidence-presence", semanticEntailmentVerified: false,
        independent: true, network: "none", modelUsed: false,
        findings: [{ findingId: "finding-1", status: "pass", citations: [{ batchSha256, sourceId, contentSha256, receiptSha256, evidenceSha256: sha(evidence), evidenceBytes: evidence.length, offset: 0, status: "present" }] }],
        privateDataIncluded: false, externalEffects: false, authority,
        boundary: "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority and does not claim semantic entailment, source truth, completeness, or publication readiness.",
      };
      const reportPayload = Buffer.from(`${JSON.stringify(report)}\n`), verificationPayload = Buffer.from(`${JSON.stringify(verification)}\n`);
      const evidencePayload = Buffer.from('{"worker":"bounded"}\n');
      criticCandidate = { plan: prepared.plan, report, verification, batches: [batch] };
      const revisionReview = await executeResearchRevisionReview({
        ...criticCandidate, modelContractSha256: sha(canonical(value.modelContract)),
        inferenceContractSha256: sha(canonical(value.inferenceContract)), now: new Date("2026-08-13T12:00:04.000Z"),
        suffix: "abcdef123456",
        async criticRunner(request) {
          const number = request.role === "critic-a" ? 1 : 2;
          return {
            contextIsolationReceiptSha256: (number === 1 ? "6" : "7").repeat(64),
            inferenceReceiptSha256: (number === 1 ? "8" : "9").repeat(64),
            findings: [{ findingId: "finding-1", semanticSupport: "supported", sourcePreference: "primary-preferred", freshnessLabelAssessment: "label-correct", confidencePermille: 800, uncertaintyAcknowledged: false, requiredRevisionBase64: null }],
            disposition: "no-revision-requested",
          };
        },
      });
      const proposal = {
        $schema: "https://osmantic.com/pixel/schemas/work-research-report-proposal-v1.schema.json", schemaVersion: 1,
        title: "Safety result", batchSha256s: [batchSha256],
        findings: [{ statement: "The source supports the bounded finding.", citations: [{ batchSha256, sourceId, evidence: evidence.toString("utf8") }] }],
        limitations: "One frozen public source.", dataClassification: "public", privateDataIncluded: false, externalEffects: false,
        authority,
        boundary: "Untrusted public Researcher proposal only. It carries no verification, publication, action, policy, or completion authority and must be finalized and independently checked by Pixel.",
      };
      const revisionHistory = buildResearchRevisionHistory({
        status: "ready-for-independent-evaluation", maxRevisionRounds: 2, revisionRounds: 0, suffix: "abcdef123456",
        attempts: [{ round: 0, workerContextIsolationReceiptSha256: "d".repeat(64), proposal, report, verification, review: revisionReview }],
      });
      const revisionReviewPayload = Buffer.from(`${JSON.stringify(revisionReview)}\n`);
      const revisionHistoryPayload = Buffer.from(`${JSON.stringify(revisionHistory)}\n`);
      return {
        cleanupComplete: true, execution: { toolCalls: 3 },
        proxyReceipt: { modelRequests: 4, inputTokens: 900, outputTokens: 180, networkBytes: 2048 }, report, verification, revisionReview, revisionHistory, batches: [batch],
        artifacts: {
          report: record("/private/research-report.json", reportPayload),
          verification: record("/private/research-verification.json", verificationPayload),
          revisionReview: record("/private/research-revision-review.json", revisionReviewPayload),
          revisionHistory: record("/private/research-revision-history.json", revisionHistoryPayload),
          evidence: record("/private/research-evidence.json", evidencePayload),
        },
      };
    },
    async readFile(path) { calls.push(`read:${path}`); return payloads.get(path); },
    async discardPreparedRun() { calls.push("discard"); },
    monotonic: (() => { const values = [100, 325]; return () => values.shift(); })(),
  });
  assert.deepEqual(calls.slice(0, 5), ["compile", "prepare", "consume", "claim", "lifecycle"]);
  assert.equal(calls.at(-1), "discard");
  assert.match(result.finalMessage, /Safety result/u);
  assert.match(result.finalMessage, /1 citation/u);
  assert.equal(result.independentVerification.status, "evidence-pass");
  assert.equal(result.researchEvidence.batches[0].sources[0].sourceType, "academic");
  assert.equal(result.researchEvidence.batches[0].sources[0].canonicalUrlSha256, sha("https://example.org/public-source"));
  assert.equal(result.researchEvidence.semanticEntailmentVerified, false);
  assert.equal(JSON.stringify(result.researchEvidence).includes("example.org"), false);
  assert.equal(result.usage.toolCalls, 3);
  assert.deepEqual(result.artifacts.map((artifact) => artifact.relativePath), [
    "pixel-research-report.json", "pixel-research-verification.json", "pixel-research-evidence.json",
    "pixel-research-revision-review.json", "pixel-research-revision-history.json",
  ]);
  assert.equal(result.researchRevisionReview.outcome, "ready-for-independent-evaluation");
  assert.equal(result.researchRevisionHistory.attempts.length, 1);
  assert.equal(result.usage.modelRequests, 4);
  const criticCalls = [];
  const review = await executeResearchRevisionReview({
    ...criticCandidate, modelContractSha256: sha(canonical(value.modelContract)),
    inferenceContractSha256: sha(canonical(value.inferenceContract)), now: new Date("2026-08-13T12:00:04.000Z"),
    suffix: "abcdef123456",
    async criticRunner(request) {
      criticCalls.push(request);
      const number = request.role === "critic-a" ? 1 : 2;
      return {
        contextIsolationReceiptSha256: (number === 1 ? "6" : "7").repeat(64),
        inferenceReceiptSha256: (number === 1 ? "8" : "9").repeat(64),
        findings: [{
          findingId: "finding-1", semanticSupport: "supported", sourcePreference: "primary-preferred",
          freshnessLabelAssessment: "label-correct", confidencePermille: 800,
          uncertaintyAcknowledged: false, requiredRevisionBase64: null,
        }],
        disposition: "no-revision-requested",
      };
    },
  });
  assert.deepEqual(criticCalls.map((call) => call.role), ["critic-a", "critic-b"]);
  assert.ok(criticCalls.every((call) => call.input.workerTranscriptIncluded === false));
  assert.notEqual(criticCalls[0].inputSha256, criticCalls[1].inputSha256);
  assert.equal(review.outcome, "ready-for-independent-evaluation");
  assert.equal(review.consensus.independentlyScored, false);
  assert.equal(review.authority.grantsCompletion, false);
});

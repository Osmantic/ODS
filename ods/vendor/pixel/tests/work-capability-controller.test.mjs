import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  authorizeCapabilityToolRequest, CapabilityControllerError,
  compileCapabilityJobAuthorization, createCapabilityToolRequest,
} from "../deploy/work-controller/capability-controller.mjs";
import {
  createGoalCapabilityResolver, GoalCapabilityRuntimeError, inspectGoalCapabilityAttempt, validateGoalCapabilityBindings,
  verifyGoalCapabilityRuntimeReadiness,
} from "../deploy/work-controller/goal-capability-runtime.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { createWatchdogEvent } from "../deploy/work-controller/watchdog.mjs";
import {
  enqueueCapabilityToolRequest, initializeCapabilityToolQueue,
  processCapabilityToolQueue, readCapabilityToolResponse, statusCapabilityToolQueue,
} from "../deploy/work-controller/capability-tool-queue.mjs";
import {
  canonical, validateWorkCapabilityControllerPolicy, validateWorkCapabilityJobAuthorization,
  validateWorkCapabilityToolRequest,
} from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");
const boundary = {
  plan: "Private immutable Deep Work plan. It authorizes no execution by itself and contains no credential, host path, provider secret, merge, deployment, publication, purchase, production, or external-effect authority.",
  lease: "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.",
  consumption: "Private immutable tombstone proving one lease was consumed once. It grants no replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.",
  checkpoint: "Private hash-bound recovery record. Contains only immutable digests, bounded counters, and transition facts. Cannot authorize or replay actions.",
  pack: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  policy: "Private controller allowlist for exact signed local capability packs. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority.",
};
const authority = { hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false, merge: false, deploy: false, publish: false, purchase: false, policyMutation: false, acceptanceCriteriaMutation: false, leaseExpansion: false };
const budgets = { maxRuntimeSeconds: 3600, maxIterations: 20, maxToolCalls: 2000, maxConcurrentSubagents: 4, maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4, maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 1073741824, maxNetworkBytes: 104857600, maxFailures: 5, noProgressLimit: 3 };
const limits = { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 3000, maxCalls: 1, maxMemoryMiB: 96, maxCpuCores: 0.5, maxPids: 24, maxWorkspaceBytes: 0 };

function capabilityPack() {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools", description: "Deterministic credential-free local fixture tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 5000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: boundary.pack,
  };
}

function fixture() {
  const pack = capabilityPack(), packSha256 = capabilityPackSha256(pack);
  const executor = { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: digest("e"), rpcProtocolVersion: 2 };
  const model = { route: "local-only", provider: "llama.cpp", id: "assistant-model", backendImageDigest: `sha256:${digest("9")}`, contextWindow: 131072, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" };
  const isolation = { mode: "hardened-container", runnerImageDigest: `sha256:${digest("f")}`, freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, inputMount: "read-only", workspaceMount: "disposable-read-write", artifactMount: "write-only-staging", destroyAfterRun: true };
  const grantedCapabilities = { tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"], network: { mode: "brokered", services: ["local-model"] } };
  const outputGate = { allowedKinds: ["patch", "test-evidence"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false };
  const verification = { mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }], immutablePathPrefixes: [], maxRuntimeSeconds: 300, maxOutputBytes: 1048576, network: "none", boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules." };
  const plan = {
    $schema: "https://osmantic.com/pixel/schemas/work-plan-v1.schema.json", schemaVersion: 1, planId: "workplan-1786366800000-abcdef123456", jobId: "work-1786366800000-abcdef123456", compiledAt: "2026-08-10T13:00:00Z", requestSha256: digest("1"), policySha256: digest("2"), inputSetSha256: sha([]), profile: "builder", dataClassification: "internal", objective: "Repair the fixture until its exact checks pass.", acceptanceCriteria: ["The independently verified patch passes"], verification, inputs: [], executor, model, isolation, grantedCapabilities, budgets: { ...budgets }, outputGate, authority: { ...authority }, status: "compiled", boundary: boundary.plan,
  };
  const lease = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v1.schema.json", schemaVersion: 1, leaseId: "worklease-1786366800000-abcdef123456", jobId: plan.jobId, issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z", singleUse: true, iteration: 1, continuation: null, planSha256: sha(plan), inputSetSha256: plan.inputSetSha256, policySha256: plan.policySha256, executor: structuredClone(executor), model: structuredClone(model), isolation: structuredClone(isolation), grantedCapabilities: structuredClone(grantedCapabilities), budgets: { ...budgets }, authority: { ...authority }, outputGate: structuredClone(outputGate), boundary: boundary.lease,
  };
  const consumption = {
    $schema: "https://osmantic.com/pixel/schemas/work-lease-consumption-v1.schema.json", schemaVersion: 1, claimId: "workclaim-1786366860000-abcdef123456", jobId: plan.jobId, leaseId: lease.leaseId, claimedAt: "2026-08-10T13:01:00Z", planSha256: sha(plan), leaseSha256: sha(lease), policySha256: plan.policySha256, inputSetSha256: plan.inputSetSha256, workspaceSha256: digest("7"), executor: structuredClone(executor), model: structuredClone(model), runnerImageDigest: isolation.runnerImageDigest, singleUse: true, status: "consumed", externalEffects: false, authority: { ...authority }, boundary: boundary.consumption,
  };
  const checkpoint = {
    $schema: "https://osmantic.com/pixel/schemas/work-checkpoint-v1.schema.json", schemaVersion: 1, checkpointId: "workcheckpoint-1786366920000-abcdef123456", jobId: plan.jobId, sequence: 1, createdAt: "2026-08-10T13:02:00Z", previousCheckpointSha256: digest("8"), planSha256: sha(plan), inputSetSha256: plan.inputSetSha256, objectiveSha256: sha(plan.objective), acceptanceCriteriaSha256: sha(plan.acceptanceCriteria), state: "running", iteration: 1, usage: { runtimeSeconds: 0, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 0 }, progress: { criteriaTotal: 1, criteriaPassing: 0, criteriaFailing: 1, noProgressCount: 0, failureFingerprintSha256: null }, workspaceSnapshotSha256: digest("6"), artifactManifestSha256: null, workerSessionSha256: sha(consumption), verificationEvidenceSha256: null, authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false, boundary: boundary.checkpoint,
  };
  const policy = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v1.schema.json", schemaVersion: 1, policyId: "workcappolicy-1786363200000-abcdef123456", createdAt: "2026-08-10T12:00:00Z", expiresAt: "2026-08-10T15:00:00Z", profiles: ["builder"], packs: [{ id: pack.id, version: pack.version, packSha256, treeSha256: pack.provenance.treeSha256, tools: [{ name: "analyze", effectClass: "read-only" }], classifications: ["public", "internal"] }], limits: { maxSessionsPerLease: 8, maxGrantLifetimeMs: 30000, maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 5000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 }, watchdog: { maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 }, authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }, boundary: boundary.policy,
  };
  const selection = { tools: [{ name: "analyze", effectClass: "read-only" }], dataClassification: "internal", maxSessions: 4, grantLifetimeMs: 30000, limits: { ...limits } };
  return { plan, lease, consumption, checkpoint, pack, expectedPackSha256: packSha256, policy, selection };
}

function authorize(value, overrides = {}) {
  return compileCapabilityJobAuthorization({ ...value, now: new Date(baseTime + 180000), suffix: "000000000001", ...overrides });
}

test("controller compiles an exact consumed-lease authorization and one transient single-use call", () => {
  const value = fixture(), authorization = authorize(value);
  assert.deepEqual(validateWorkCapabilityControllerPolicy(value.policy), []);
  assert.deepEqual(validateWorkCapabilityJobAuthorization(authorization), []);
  assert.equal(authorization.authority.grantsToolCall, false);
  assert.equal(JSON.stringify(authorization).includes("private local input"), false);
  const request = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "private local input" }, now: new Date(baseTime + 180001), suffix: "000000000002" });
  assert.deepEqual(validateWorkCapabilityToolRequest(request), []);
  const result = authorizeCapabilityToolRequest({ ...value, authorization, request, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" });
  assert.equal(result.status, "continue");
  assert.equal(result.grant.singleUse, true);
  assert.deepEqual(result.grant.tools, ["analyze"]);
  assert.equal(result.grant.checkpointSha256, sha(value.checkpoint));
  assert.deepEqual(result.runtimeRequest, { toolName: "analyze", input: { text: "private local input" } });
  assert.equal(JSON.stringify(result.preflightDecision).includes("private local input"), false);
  assert.equal(result.authority.grantsToolCall, false);
});

test("controller rejects scope widening, substituted custody, and invalid signed inputs", () => {
  const value = fixture();
  assert.throws(() => authorize(value, { selection: { ...value.selection, dataClassification: "public" } }), /classification differs/);
  assert.throws(() => authorize(value, { selection: { ...value.selection, maxSessions: value.lease.budgets.maxToolCalls + 1 } }), /session budget/);
  assert.throws(() => authorize(value, { selection: { ...value.selection, limits: { ...limits, maxMemoryMiB: 4096 } } }), /widens maxMemoryMiB/);
  const authorization = authorize(value), baseRequest = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "safe" }, now: new Date(baseTime + 180001), suffix: "000000000002" });
  const forgedAuthorization = structuredClone(authorization); forgedAuthorization.maxSessions = value.policy.limits.maxSessionsPerLease + 1; forgedAuthorization.watchdog.maxToolCalls = forgedAuthorization.maxSessions;
  const forgedRequest = createCapabilityToolRequest({ authorization: forgedAuthorization, tool: "analyze", effectClass: "read-only", arguments: { text: "safe" }, now: new Date(baseTime + 180001), suffix: "000000000009" });
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization: forgedAuthorization, request: forgedRequest, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000010", watchdogSuffix: "000000000011" }), /widens its session budget/);
  for (const mutate of [
    (request) => { request.checkpointSha256 = digest("0"); },
    (request) => { request.effectClass = "workspace"; },
    (request) => { request.arguments = { text: "safe", command: "escape" }; },
    (request) => { request.limits.maxOutputBytes = 10000; },
  ]) {
    const request = structuredClone(baseRequest); mutate(request);
    assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization, request, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" }), CapabilityControllerError);
  }
  const cyclic = structuredClone(baseRequest); cyclic.arguments = {}; cyclic.arguments.self = cyclic.arguments;
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization, request: cyclic, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" }), /cycle/);
  const nonFinite = structuredClone(baseRequest); nonFinite.arguments.text = Number.NaN;
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization, request: nonFinite, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" }), /finite JSON/);
  const substituted = structuredClone(value.checkpoint); substituted.workspaceSnapshotSha256 = digest("0");
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, checkpoint: substituted, authorization, request: baseRequest, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" }), /immutable custody|checkpoint/);
});

test("repeated progress events stop grants; elapsed time alone never schedules work", () => {
  const value = fixture(), authorization = authorize(value);
  const firstRequest = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "same event" }, now: new Date(baseTime + 180001), suffix: "000000000002" });
  const first = authorizeCapabilityToolRequest({ ...value, authorization, request: firstRequest, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000003", watchdogSuffix: "000000000004" });
  const event = createWatchdogEvent({ decision: first.preflightDecision, outcome: "success", verifiedProgressEpoch: 0, observedAt: new Date(baseTime + 180003) });
  const secondRequest = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "same event" }, expectedEventHeadSha256: event.recordSha256, now: new Date(baseTime + 2400000), suffix: "000000000005" });
  const stopped = authorizeCapabilityToolRequest({ ...value, authorization, request: secondRequest, events: [event], now: new Date(baseTime + 2400001), grantSuffix: "000000000006", watchdogSuffix: "000000000007" });
  assert.equal(stopped.status, "stopped");
  assert.equal(stopped.grant, null);
  assert.equal(stopped.preflightDecision.reason, "repeated-equivalent-call");
  assert.equal(stopped.preflightDecision.observed.eventsWithoutVerifiedProgress, 2);
});

test("freshness clocks only invalidate stale authority and forged event heads fail closed", () => {
  const value = fixture(), authorization = authorize(value);
  const stale = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "safe" }, now: new Date(baseTime + 180001), suffix: "000000000002" });
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization, request: stale, events: [], now: new Date(baseTime + 400001), grantSuffix: "000000000003", watchdogSuffix: "000000000004" }), /stale/);
  const forged = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "safe" }, expectedEventHeadSha256: digest("0"), now: new Date(baseTime + 180001), suffix: "000000000005" });
  assert.throws(() => authorizeCapabilityToolRequest({ ...value, authorization, request: forged, events: [], now: new Date(baseTime + 180002), grantSuffix: "000000000006", watchdogSuffix: "000000000007" }), /trusted head/);
});

test("goal capability binding becomes durable only for the exact consumed running attempt", async (t) => {
  const value = fixture(), parent = await mkdtemp(join(tmpdir(), "pixel-goal-capability-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const binding = {
    jobId: value.plan.jobId,
    pack: { id: value.pack.id, version: value.pack.version, packSha256: value.expectedPackSha256, treeSha256: value.pack.provenance.treeSha256 },
    tools: ["analyze"], maxSessions: value.selection.maxSessions, grantLifetimeMs: value.selection.grantLifetimeMs,
    limits: { ...value.selection.limits },
  };
  const config = {
    stateRoot: parent,
    capabilityRuntime: {
      controllerPolicyPath: join(parent, "policy.json"), allowedSignersPath: join(parent, "allowed-signers"),
      sshKeygenPath: join(parent, "ssh-keygen"), dockerConfigPath: join(parent, "docker-config"),
      maxHealthAgeMs: 60000, bindings: [binding],
    },
  };
  const jobs = [{ jobId: value.plan.jobId, profile: value.plan.profile, dataClassification: value.plan.dataClassification, requestedCapabilities: { filesystem: "disposable-read-write", tools: [...value.plan.grantedCapabilities.tools] }, budgets: { ...value.plan.budgets } }];
  assert.equal(validateGoalCapabilityBindings({ capabilityRuntime: config.capabilityRuntime, jobs, policy: value.policy }), true);
  const readiness = await verifyGoalCapabilityRuntimeReadiness({
    config, jobs, policy: value.policy, now: new Date(baseTime + 180000), dependencies: {
      loadInstalledCapabilityPack: async () => ({ pack: value.pack }), loadPassingCapabilityHealth: async () => ({ receiptSha256: digest("9") }),
    },
  });
  assert.deepEqual(readiness, { state: "ready", jobCount: 1, packCount: 1, toolCount: 1 });
  await assert.rejects(verifyGoalCapabilityRuntimeReadiness({ config, jobs, policy: value.policy, now: new Date("2026-08-10T15:00:00Z"), dependencies: { loadInstalledCapabilityPack: async () => ({ pack: value.pack }), loadPassingCapabilityHealth: async () => ({}) } }), /outside its readiness lifetime/);
  await assert.rejects(verifyGoalCapabilityRuntimeReadiness({ config, jobs, policy: value.policy, now: new Date(baseTime + 180000), dependencies: { loadInstalledCapabilityPack: async () => ({ pack: { ...value.pack, provenance: { ...value.pack.provenance, treeSha256: digest("0") } } }), loadPassingCapabilityHealth: async () => ({}) } }), /differs from the exact goal binding/);
  let installedLoads = 0, healthLoads = 0;
  const resolver = createGoalCapabilityResolver({
    config, jobs, policy: value.policy, now: () => new Date(baseTime + 180002),
    dependencies: {
      loadInstalledCapabilityPack: async () => { installedLoads += 1; return { pack: value.pack }; },
      loadPassingCapabilityHealth: async () => { healthLoads += 1; return { receiptSha256: digest("9") }; },
    },
  });
  const prepared = { plan: value.plan, lease: value.lease };
  const active = await resolver({ mode: "execute", prepared, claim: value.consumption, checkpoint: value.checkpoint });
  assert.equal(installedLoads, 1); assert.equal(healthLoads, 1);
  assert.equal(active.authorization.checkpointSha256, sha(value.checkpoint));
  assert.deepEqual(active.catalog.tools.map((tool) => tool.name), ["analyze"]);
  const custodyPath = join(parent, "capability-job-authorizations", value.plan.jobId, value.consumption.claimId, "bundle.json");
  const custody = await readFile(custodyPath, "utf8");
  assert.equal(custody.includes(value.plan.objective), false);
  assert.equal(custody.includes("private local input"), false);
  assert.deepEqual(await inspectGoalCapabilityAttempt({ stateRoot: parent, binding, policy: value.policy, plan: value.plan, lease: value.lease, consumption: value.consumption, checkpoint: value.checkpoint }), {
    state: "authorized", toolCount: 1, singleUseCalls: true, networkAccess: false, externalEffects: false,
  });
  const recovered = await resolver({ mode: "cleanup", prepared, claim: value.consumption, checkpoint: value.checkpoint });
  assert.equal(installedLoads, 1); assert.equal(healthLoads, 1);
  assert.equal(sha(recovered.authorization), sha(active.authorization));
  assert.equal(sha(recovered.catalog), sha(active.catalog));
  const tampered = JSON.parse(custody); tampered.bindingSha256 = digest("0");
  await writeFile(custodyPath, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
  await assert.rejects(resolver({ mode: "cleanup", prepared, claim: value.consumption, checkpoint: value.checkpoint }), /custody metadata differs/);
});

test("goal capability bindings reject ambient, unknown, repeated, or widened job selection", () => {
  const value = fixture();
  const binding = {
    jobId: value.plan.jobId,
    pack: { id: value.pack.id, version: value.pack.version, packSha256: value.expectedPackSha256, treeSha256: value.pack.provenance.treeSha256 },
    tools: ["analyze"], maxSessions: 4, grantLifetimeMs: 30000, limits: { ...limits },
  };
  const runtime = { controllerPolicyPath: "/private/policy", allowedSignersPath: "/private/signers", sshKeygenPath: "/usr/bin/ssh-keygen", dockerConfigPath: "/private/docker", maxHealthAgeMs: 60000, bindings: [binding] };
  const jobs = [{ jobId: value.plan.jobId, profile: "builder", dataClassification: "internal", requestedCapabilities: { filesystem: "disposable-read-write", tools: [...value.plan.grantedCapabilities.tools] }, budgets: { ...value.plan.budgets } }];
  for (const mutate of [
    (candidate) => { candidate.bindings[0].jobId = "work-1786366800001-abcdef123456"; },
    (candidate) => { candidate.bindings.push(structuredClone(candidate.bindings[0])); },
    (candidate) => { candidate.bindings[0].tools = ["unknown"]; },
    (candidate) => { candidate.bindings[0].maxSessions = value.policy.limits.maxSessionsPerLease + 1; },
    (candidate) => { candidate.bindings[0].pack.packSha256 = digest("0"); },
  ]) {
    const candidate = structuredClone(runtime); mutate(candidate);
    assert.throws(() => validateGoalCapabilityBindings({ capabilityRuntime: candidate, jobs, policy: value.policy }), GoalCapabilityRuntimeError);
  }
});

function tickingClock(start = baseTime + 180002) { let tick = start; return () => new Date(tick++); }
async function queueFixture(t) {
  const value = fixture(), authorization = authorize(value), parent = await mkdtemp(join(tmpdir(), "pixel-capability-queue-"));
  t.after(() => rm(parent, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(parent, 0o700);
  const queueRoot = join(parent, "queue"); await initializeCapabilityToolQueue({ queueRoot, authorization });
  return { value, authorization, queueRoot };
}
function queuedRequest(authorization, { text = "private queue canary", head = null, offset = 0 } = {}) {
  return createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: { text }, expectedEventHeadSha256: head, now: new Date(baseTime + 180001 + offset), suffix: String(offset + 20).padStart(12, "0") });
}
function successRuntime(options, authorization) {
  const structuredContent = { length: options.input.text.length };
  return { schemaVersion: 1, operation: "pixel-work-capability-runtime-result", status: "succeeded-disabled", pack: { ...authorization.pack }, grantId: options.grant.grantId, receiptSha256: digest("3"), dataClassification: authorization.dataClassification, structuredContent, contentSha256: sha(structuredContent), enabled: false, authority: { grantsCompletion: false }, boundary: "Transient fixture result." };
}
function queueOptions(state, request, overrides = {}) {
  let executions = 0;
  const options = {
    queueRoot: state.queueRoot, authorization: state.authorization,
    requestContext: { ...state.value }, runtime: { id: state.value.pack.id, version: state.value.pack.version },
    clock: tickingClock(overrides.clockStart),
    suffixes: { grant: "000000000101", watchdog: "000000000102", claimed: "000000000103", authorized: "000000000104", launching: "000000000105", runtimeReturned: "000000000106", settled: "000000000107", stopped: "000000000108" },
    dependencies: {
      executeRuntime: async (runtime) => { executions += 1; return successRuntime(runtime, state.authorization); },
      statusRuntime: async () => ({ status: "disabled", latest: null }), recoverRuntime: async () => { throw new Error("recovery should not run without cleanup custody"); },
      ...(overrides.dependencies ?? {}),
    },
  };
  return { options, executions: () => executions, request };
}

test("durable queue settles one private result and keeps content out of custody records", async (t) => {
  const state = await queueFixture(t), request = queuedRequest(state.authorization); await enqueueCapabilityToolRequest({ ...state, request });
  const run = queueOptions(state, request), result = await processCapabilityToolQueue(run.options);
  assert.equal(result.status, "succeeded-disabled"); assert.deepEqual(result.response.structuredContent, { length: request.arguments.text.length }); assert.equal(run.executions(), 1);
  const status = await statusCapabilityToolQueue(state); assert.deepEqual({ status: status.status, queued: status.queued, settled: status.settled, events: status.events, active: status.active }, { status: "idle-disabled", queued: 0, settled: 1, events: 1, active: false });
  const custodyRoot = join(state.queueRoot, "history", request.requestId, "custody"), custody = await Promise.all((await readdir(custodyRoot)).map((name) => readFile(join(custodyRoot, name), "utf8")));
  assert.equal(custody.some((text) => text.includes("private queue canary") || text.includes("structuredContent")), false);
  const idle = await processCapabilityToolQueue({ ...run.options, clock: tickingClock(baseTime + 181000) }); assert.equal(idle.status, "idle-disabled"); assert.equal(run.executions(), 1);
});

test("a crash after launch burns the queue attempt and recovers uncertain without replay", async (t) => {
  const state = await queueFixture(t), request = queuedRequest(state.authorization); await enqueueCapabilityToolRequest({ ...state, request });
  let crashed = false; const run = queueOptions(state, request, { dependencies: { afterTransition: (phase) => { if (phase === "launching" && !crashed) { crashed = true; throw new Error("simulated launch crash"); } } } });
  await assert.rejects(() => processCapabilityToolQueue(run.options), /launch crash/); assert.equal(run.executions(), 0);
  const busy = await processCapabilityToolQueue({ ...run.options, clock: tickingClock(baseTime + 180500), dependencies: { ...run.options.dependencies, afterTransition: undefined } }); assert.equal(busy.status, "busy-disabled"); assert.equal(run.executions(), 0);
  const recovered = await processCapabilityToolQueue({ ...run.options, recoverActive: true, clock: tickingClock(baseTime + 181000), dependencies: { ...run.options.dependencies, afterTransition: undefined } });
  assert.equal(recovered.status, "uncertain-no-replay-disabled"); assert.equal(recovered.response.runtimeReceiptSha256, null); assert.equal(run.executions(), 0); assert.equal((await statusCapabilityToolQueue(state)).events, 1);
});

for (const crashPhase of ["authorized", "runtime-result-staged", "event-appended", "response-staged", "settled"]) {
  test(`a crash after ${crashPhase} recovers the exact result and event without a second call`, async (t) => {
    const state = await queueFixture(t), request = queuedRequest(state.authorization); await enqueueCapabilityToolRequest({ ...state, request });
    let crashed = false; const run = queueOptions(state, request, { dependencies: { afterTransition: (phase) => { if (phase === crashPhase && !crashed) { crashed = true; throw new Error(`simulated ${crashPhase} crash`); } } } });
    await assert.rejects(() => processCapabilityToolQueue(run.options), new RegExp(crashPhase)); assert.equal(run.executions(), crashPhase === "authorized" ? 0 : 1);
    const recovered = await processCapabilityToolQueue({ ...run.options, recoverActive: true, clock: tickingClock(baseTime + 181000), dependencies: { ...run.options.dependencies, afterTransition: undefined } });
    assert.equal(recovered.status, "succeeded-disabled"); assert.equal(run.executions(), 1); assert.equal((await statusCapabilityToolQueue(state)).events, 1);
  });
}

test("concurrent queue controllers have one runtime winner and one exact settlement", async (t) => {
  const state = await queueFixture(t), request = queuedRequest(state.authorization); await enqueueCapabilityToolRequest({ ...state, request });
  let executions = 0;
  const dependencies = { executeRuntime: async (options) => { executions += 1; await new Promise((resolve) => setTimeout(resolve, 25)); return successRuntime(options, state.authorization); }, statusRuntime: async () => ({ status: "disabled", latest: null }), recoverRuntime: async () => null };
  const left = queueOptions(state, request, { dependencies }), right = queueOptions(state, request, { dependencies });
  const outcomes = await Promise.allSettled([processCapabilityToolQueue(left.options), processCapabilityToolQueue(right.options)]);
  assert.equal(outcomes.filter((entry) => entry.status === "fulfilled").length, 2); assert.deepEqual(outcomes.map((entry) => entry.value.status).sort(), ["busy-disabled", "succeeded-disabled"]); assert.equal(executions, 1);
  const status = await statusCapabilityToolQueue(state); assert.equal(status.settled, 1); assert.equal(status.events, 1); assert.equal(status.active, false);
});

test("queue settlement stops an equivalent follow-up without invoking the runtime", async (t) => {
  const state = await queueFixture(t), firstRequest = queuedRequest(state.authorization); await enqueueCapabilityToolRequest({ ...state, request: firstRequest });
  const first = queueOptions(state, firstRequest); const completed = await processCapabilityToolQueue(first.options); assert.equal(first.executions(), 1);
  const secondRequest = queuedRequest(state.authorization, { head: completed.response.eventRecordSha256, offset: 1000 }); await enqueueCapabilityToolRequest({ ...state, request: secondRequest });
  const second = queueOptions(state, secondRequest, { clockStart: baseTime + 181005 }); const stopped = await processCapabilityToolQueue(second.options);
  assert.equal(stopped.status, "stopped-disabled"); assert.equal(stopped.response.reason, "repeated-equivalent-call"); assert.equal(second.executions(), 0); assert.equal((await statusCapabilityToolQueue(state)).events, 1);
  const thirdRequest = queuedRequest(state.authorization, { head: completed.response.eventRecordSha256, offset: 2000 });
  await assert.rejects(() => enqueueCapabilityToolRequest({ ...state, request: thirdRequest }), /terminal/);
});

test("queue authorization, event, and private response tampering fail closed", async (t) => {
  const metadataState = await queueFixture(t), metadataPath = join(metadataState.queueRoot, "authorization.json"), metadata = JSON.parse(await readFile(metadataPath, "utf8")); metadata.untrusted = true; await writeFile(metadataPath, `${JSON.stringify(metadata)}\n`);
  await assert.rejects(() => statusCapabilityToolQueue(metadataState), /authorization.*invalid/);

  const eventState = await queueFixture(t), request = queuedRequest(eventState.authorization); await enqueueCapabilityToolRequest({ ...eventState, request });
  const run = queueOptions(eventState, request), completed = await processCapabilityToolQueue(run.options), eventPath = join(eventState.queueRoot, "events", "0000000.json"), event = JSON.parse(await readFile(eventPath, "utf8"));
  event.outcome = "error"; await writeFile(eventPath, `${JSON.stringify(event)}\n`);
  const next = queuedRequest(eventState.authorization, { text: "different", head: completed.response.eventRecordSha256, offset: 1000 }); await enqueueCapabilityToolRequest({ ...eventState, request: next });
  await assert.rejects(() => processCapabilityToolQueue(queueOptions(eventState, next, { clockStart: baseTime + 181005 }).options), /event.*invalid/);

  const responsePath = join(eventState.queueRoot, "responses", `${request.requestId}.json`), response = JSON.parse(await readFile(responsePath, "utf8")); response.structuredContent.length += 1; await writeFile(responsePath, `${JSON.stringify(response)}\n`);
  await assert.rejects(() => readCapabilityToolResponse({ ...eventState, requestId: request.requestId }), /content hash differs/);
});

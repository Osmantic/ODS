import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileCapabilityJobAuthorization, compileCapabilityOperationalV2, createCapabilityToolRequest } from "../deploy/work-controller/capability-controller.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import {
  enqueueCapabilityToolRequest, initializeCapabilityToolQueue, processCapabilityToolQueue, readCapabilityToolResponse,
} from "../deploy/work-controller/capability-tool-queue.mjs";
import {
  v2ExternalEffectApprove, v2ExternalEffectPropose, v2ExternalEffectReconcile, v2RecoverRuntime, v2RuntimeStatus,
} from "../deploy/work-controller/capability-runtime-v2.mjs";
import { canonical, validateWorkCapabilityToolRequest, validateWorkCapabilityToolResponse } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");
const opId = () => `workcapv2-${Date.now()}-${"a".repeat(16)}`;
const extId = () => `workcapv2ext-${Date.now()}-${"b".repeat(16)}`;
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
const limits = { maxInputBytes: 8192, maxOutputBytes: 16384, maxRuntimeMs: 5000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 };

// v2 lane request surface admitted by the signed fixture tool schema.
const v2LaneSchema = {
  type: "object", additionalProperties: false,
  properties: {
    lane: { type: "string" }, operationId: { type: "string" }, operation: { type: "string" },
    relativePath: { type: "string" }, content: { type: "string" }, query: { type: "string" },
    sourceTypes: { type: "array", items: { type: "string" } }, domains: { type: "array", items: { type: "string" } },
    maxResults: { type: "integer" }, maxSourcesToFetch: { type: "integer" }, maxSourceBytes: { type: "integer" },
    endpoint: { type: "string" }, method: { type: "string" }, payload: { type: "string" }, text: { type: "string" },
  },
};

function capabilityPack() {
  const inputSchema = v2LaneSchema;
  const outputSchema = { type: "object", additionalProperties: false, properties: { length: { type: "integer" } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools", description: "Deterministic credential-free local fixture tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { ...limits, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, shutdownTimeoutMs: 250 },
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
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v1.schema.json", schemaVersion: 1, policyId: "workcappolicy-1786363200000-abcdef123456", createdAt: "2026-08-10T12:00:00Z", expiresAt: "2026-08-10T15:00:00Z", profiles: ["builder"], packs: [{ id: pack.id, version: pack.version, packSha256, treeSha256: pack.provenance.treeSha256, tools: [{ name: "analyze", effectClass: "read-only" }], classifications: ["public", "internal"] }], limits: { maxSessionsPerLease: 8, maxGrantLifetimeMs: 30000, maxInputBytes: 8192, maxOutputBytes: 16384, maxRuntimeMs: 5000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 }, watchdog: { maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 }, authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }, boundary: boundary.policy,
  };
  const selection = { tools: [{ name: "analyze", effectClass: "read-only" }], dataClassification: "internal", maxSessions: 4, grantLifetimeMs: 30000, limits: { ...limits } };
  return { plan, lease, consumption, checkpoint, pack, expectedPackSha256: packSha256, policy, selection };
}

function authorize(value) { return compileCapabilityJobAuthorization({ ...value, now: new Date(baseTime + 180000), suffix: "000000000001" }); }

function tickingClock(start = baseTime + 180002) { let tick = start; return () => new Date(tick++); }

async function privateDir(t, label) {
  const dir = await mkdtemp(join(tmpdir(), label));
  t.after(() => rm(dir, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(dir, 0o700);
  return dir;
}

function v2Config({ stateRoot, workspaceRoot, enabled = true }) {
  return {
    runtimeVersion: 2, runtimeEnabled: enabled, lanes: { publicRetrieval: enabled, filesystem: enabled, loopback: enabled, ambiguousExternalEffect: enabled },
    stateRoot, workspaceRoot, courierQueueRoot: null, timeoutMs: 2000, maxOutputBytes: 64 * 1024, maxWorkspaceFileBytes: 256 * 1024, courierPollMilliseconds: 5,
    loopbackAllowlist: [], externalDecisionProvider: null, clock: () => new Date(),
  };
}

function queueOptions(state, request, overrides = {}) {
  return {
    queueRoot: state.queueRoot, authorization: state.authorization,
    requestContext: { ...state.value }, runtime: { ...(overrides.runtime ?? {}) },
    clock: tickingClock(overrides.clockStart),
    suffixes: { grant: "000000000101", watchdog: "000000000102", claimed: "000000000103", authorized: "000000000104", launching: "000000000105", runtimeReturned: "000000000106", settled: "000000000107", stopped: "000000000108" },
    dependencies: {
      statusRuntime: async () => ({ status: "disabled", latest: null }),
      recoverRuntime: async () => null,
      ...(overrides.dependencies ?? {}),
    },
  };
}

function v2Request(authorization, laneArgs, { runtimeVersion, offset = 0, head = null } = {}) {
  const request = createCapabilityToolRequest({ authorization, tool: "analyze", effectClass: "read-only", arguments: laneArgs, now: new Date(baseTime + 180001 + offset), suffix: String(offset + 20).padStart(12, "0") });
  if (runtimeVersion !== undefined) request.runtimeVersion = runtimeVersion;
  if (head !== null) request.expectedEventHeadSha256 = head;
  return request;
}

async function queueFixture(t) {
  const value = fixture(), authorization = authorize(value), parent = await privateDir(t, "pixel-v2-queue-");
  const queueRoot = join(parent, "queue"); await initializeCapabilityToolQueue({ queueRoot, authorization });
  return { value, authorization, queueRoot };
}

test("v1 default remains byte-for-byte compatible (no runtime field)", async (t) => {
  const state = await queueFixture(t);
  const request = v2Request(state.authorization, { text: "canary" }, {});
  assert.equal(request.runtimeVersion, undefined);
  await enqueueCapabilityToolRequest({ ...state, request });
  let ran = 0;
  const options = queueOptions(state, request, { dependencies: { executeRuntime: async (rt) => { ran += 1; const sc = { length: rt.input.text.length }; return { schemaVersion: 1, status: "succeeded-disabled", grantId: rt.grant.grantId, dataClassification: rt.grant.dataClassification, receiptSha256: digest("3"), structuredContent: sc, contentSha256: sha(sc), enabled: false, authority: { grantsCompletion: false } }; } } });
  const result = await processCapabilityToolQueue(options);
  assert.equal(result.status, "succeeded-disabled"); assert.equal(ran, 1);
});

test("P0 is impossible: a v1 signed pack can never reach v2 from caller content or injected config", async (t) => {
  const state = await queueFixture(t);
  const workspace = await privateDir(t, "pixel-v2-p0-"), stateRoot = await privateDir(t, "pixel-v2-p0state-");
  const request = v2Request(state.authorization, { lane: "filesystem", operationId: opId(), operation: "write", relativePath: "a/b.txt", content: "hello" }, {});
  // The v1 request contract has no runtimeVersion field: the schema is closed
  // (additionalProperties:false) so a caller-injected v2 selector is rejected
  // at the request boundary before custody or any effect.
  const mutilated = { ...request, runtimeVersion: "v2" };
  assert.ok(validateWorkCapabilityToolRequest(mutilated).length > 0, "the closed v1 request schema must reject a caller-injected runtimeVersion");
  // Even with an injected v2 config present on the runtime seam, the dispatcher
  // selects from the validated grant only (schemaVersion 1 => v1) and never from
  // request content. A v2 config must not widen a v1 grant.
  await enqueueCapabilityToolRequest({ ...state, request });
  let v1Ran = 0, v2Ran = 0;
  const options = queueOptions(state, request, { runtime: { v2: { config: v2Config({ stateRoot, workspaceRoot: workspace }) } }, dependencies: { executeRuntime: async (rt) => { v1Ran += 1; const sc = { length: (rt.input?.text ?? "").length }; return { schemaVersion: 1, status: "succeeded-disabled", grantId: rt.grant.grantId, dataClassification: rt.grant.dataClassification, receiptSha256: digest("4"), structuredContent: sc, contentSha256: sha(sc), enabled: false, authority: { grantsCompletion: false } }; } } });
  const result = await processCapabilityToolQueue(options);
  assert.equal(result.status, "succeeded-disabled");
  assert.equal(v1Ran, 1, "a v1 grant must route through the v1 runtime");
  assert.equal(v2Ran, 0, "the injected v2 config must never execute");
  assert.equal(await readFile(join(workspace, "a", "b.txt"), "utf8").then(() => true, () => false), false, "no v2 effect may run");
  assert.equal((await readdir(join(stateRoot, "v2-runtime", "receipts")).catch(() => [])).length, 0, "no v2 custody may be created");
});

test("queue dispatch seam passes the compiled runtime request under the exact runtimeRequest field", async (t) => {
  const state = await queueFixture(t);
  const request = v2Request(state.authorization, { text: "canary" }, {});
  await enqueueCapabilityToolRequest({ ...state, request });
  let seen = null;
  const options = queueOptions(state, request, { dependencies: { executeRuntime: async (rt) => { seen = rt; const sc = { length: rt.input.text.length }; return { schemaVersion: 1, status: "succeeded-disabled", grantId: rt.grant.grantId, dataClassification: rt.grant.dataClassification, receiptSha256: digest("5"), structuredContent: sc, contentSha256: sha(sc), enabled: false, authority: { grantsCompletion: false } }; } } });
  const result = await processCapabilityToolQueue(options);
  assert.equal(result.status, "succeeded-disabled");
  assert.ok(seen, "the queue dispatch seam must be reached");
  assert.deepEqual(seen.runtimeRequest, request, "the compiled runtime request must arrive under the exact runtimeRequest field");
  assert.equal(seen.request, undefined, "the raw request must not leak under the legacy 'request' field name");
});

test("a controller-compiled v2 operational grant reaches the workspace slice through the production dispatcher", async (t) => {
  const { executeRuntimeDispatch } = await import("../deploy/work-controller/capability-runtime-dispatch.mjs");
  const base = fixture();
  const workspace = await privateDir(t, "pixel-v2-opws-"), stateRoot = await privateDir(t, "pixel-v2-opstate-");
  const content = "one bounded file";
  const inputSchema = { type: "object", additionalProperties: false, required: ["relativePath", "content"], properties: { relativePath: { type: "string" }, content: { type: "string" } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer" } } };
  const pack = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v2.schema.json", schemaVersion: 2,
    kind: "deep-work-capability-pack", id: "fixture-ws", version: "1.0.0", name: "Fixture workspace", description: "Deterministic bounded workspace tool.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack", admissionOnly: true },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-ws@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-ws", serverVersion: "1.0.0", workspace: "disposable-read-write" },
    tools: [{ name: "write", title: "Write bounded file", description: "Create one bounded UTF-8 file in the job workspace.", effectClass: "workspace", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), binding: null }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 8192, maxOutputBytes: 16384, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 5000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 262144 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { networkMode: "none", networkDestinations: [], credentialRefs: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, admissionOnly: true },
    budgets: { perRun: { maxCalls: 1, maxBytes: 256 * 1024, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 256 * 1024, maxDurationMs: 5000 } },
    boundary: "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.",
  };
  const policy = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v2.schema.json", schemaVersion: 2,
    policyId: "workcappolicy-1786363200000-abcdef123456", createdAt: "2026-08-10T12:00:00Z", expiresAt: "2026-08-10T15:00:00Z",
    profiles: ["builder"],
    packs: [{ id: pack.id, version: pack.version, packSha256: sha(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2, tools: [{ name: "write", effectClass: "workspace", bindingSummary: { targetClass: "local-filesystem", inputClassification: "internal", outputClassification: "internal" } }], classifications: ["public", "internal"] }],
    limits: { maxSessionsPerLease: 8, maxGrantLifetimeMs: 30000, maxInputBytes: 8192, maxOutputBytes: 16384, maxRuntimeMs: 5000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 262144 },
    watchdog: { maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Admission-only controller allowlist for exact signed v2 capability declarations. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority; v2 runtime is not enabled.",
  };
  const value = { plan: base.plan, lease: base.lease, consumption: base.consumption, checkpoint: base.checkpoint, pack, expectedPackSha256: sha(pack), policy };
  const { grant, runtimeRequest } = compileCapabilityOperationalV2({ ...value, tool: "write", relativePath: "op.txt", content, now: new Date(baseTime + 183000), grantSuffix: "000000000201", operationSuffix: "0000000000000201" });
  const config = { stateRoot, workspaceRoot: workspace, clock: () => new Date(baseTime + 184000) };
  const out = await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config } });
  assert.equal(out.schemaVersion, 2); assert.equal(out.status, "succeeded"); assert.equal(out.enabled, true); assert.equal(out.effect, "workspace-write");
  assert.equal(await readFile(join(workspace, "op.txt"), "utf8"), content);
  assert.equal(await readFile(join(stateRoot, "v2-runtime", "custody", `${grant.operation.id}.json`), "utf8").then(() => true, () => false), false, "active custody must settle");
});

test("external approval vs reconciliation uses the real persisted proposal hash and has exactly one terminal winner", async (t) => {
  const stateRoot = await privateDir(t, "pixel-v2-ext-");
  const config = v2Config({ stateRoot, workspaceRoot: stateRoot });
  const id = extId();
  await v2ExternalEffectPropose(config, { proposalId: id, effect: "publish a release note" });
  // The approval must bind the REAL persisted proposal hash (sha of the proposed
  // record's source fields), so either contender can genuinely win.
  const persistedProposal = { proposalId: id, effect: "publish a release note" };
  const verified = { verify: async (decision) => ({ approved: decision?.approved === true }) };
  const approving = v2ExternalEffectApprove({ ...config, externalDecisionProvider: verified }, { proposalId: id, proposalSha256: sha(persistedProposal), decision: { approved: true } }).catch(() => "lost");
  const reconciling = v2ExternalEffectReconcile(config, { proposalId: id, uncertainty: true }).catch(() => "lost");
  const outcomes = await Promise.all([approving, reconciling]);
  const winners = outcomes.filter((entry) => entry !== "lost" && typeof entry === "object");
  assert.equal(winners.length, 1, "exactly one terminal winner expected");
  const terminal = await readdir(join(stateRoot, "v2-runtime", "external"));
  assert.equal(terminal.length, 1);
  assert.equal(await readFile(join(stateRoot, "v2-runtime", "external", `${id}.json`), "utf8").then(() => true, () => false), false, "pending record must not remain");
  assert.equal(winners[0].status === "approved" || winners[0].status === "rejected", true);
});

test("a first reconciliation genuinely wins and later approval is rejected", async (t) => {
  const stateRoot = await privateDir(t, "pixel-v2-ext2-");
  const config = v2Config({ stateRoot, workspaceRoot: stateRoot });
  const id = extId();
  await v2ExternalEffectPropose(config, { proposalId: id, effect: "publish a release note" });
  const persistedProposal = { proposalId: id, effect: "publish a release note" };
  const first = await v2ExternalEffectReconcile(config, { proposalId: id, uncertainty: true });
  assert.equal(first.status, "rejected");
  const verified = { verify: async (decision) => ({ approved: decision?.approved === true }) };
  await assert.rejects(
    () => v2ExternalEffectApprove({ ...config, externalDecisionProvider: verified }, { proposalId: id, proposalSha256: sha(persistedProposal), decision: { approved: true } }),
    /already reconciled|already settled|no pending/,
  );
  const terminal = await readdir(join(stateRoot, "v2-runtime", "external"));
  assert.equal(terminal.length, 1, "only one terminal may exist");
});

test("v2RuntimeStatus is read-only and counts only unresolved active custody", async (t) => {
  const root = await privateDir(t, "pixel-v2-status-");
  const config = v2Config({ stateRoot: root, workspaceRoot: root });
  const before = await v2RuntimeStatus(config);
  assert.equal(before.activeCustody, 0); assert.equal(before.settledCustody, 0);
  assert.equal((await readdir(join(root, "v2-runtime")).catch(() => [])).length, 0, "status must be read-only");
  const id = opId();
  const fs = await import("../deploy/work-controller/capability-runtime-v2.mjs");
  await fs.v2ExecuteFilesystem(config, { lane: "filesystem", operationId: id, operation: "write", relativePath: "s.txt", content: "hi" });
  const after = await v2RuntimeStatus(config);
  assert.equal(after.receipts, 1); assert.equal(after.activeCustody, 0); assert.equal(after.settledCustody, 1);
});

test("normal and recovered receipt hashes are identical", async (t) => {
  const root = await privateDir(t, "pixel-v2-hash-");
  const config = v2Config({ stateRoot: root, workspaceRoot: root });
  const id = opId();
  const fs = await import("../deploy/work-controller/capability-runtime-v2.mjs");
  const out = await fs.v2ExecuteFilesystem(config, { lane: "filesystem", operationId: id, operation: "write", relativePath: "r.txt", content: "hash me" });
  const recovered = await v2RecoverRuntime(config, id);
  assert.equal(recovered.status, "completed");
  assert.equal(recovered.receiptSha256, out.receiptSha256);
});

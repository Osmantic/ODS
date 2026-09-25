import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  canonical,
  validateWorkCapabilityConsumptionV2,
  validateWorkCapabilityControllerPolicyV2,
  validateWorkCapabilityGrantV2,
  validateWorkCapabilityJobAuthorizationV2,
  validateWorkCapabilityLeaseV2,
  validateWorkCapabilityPackV2,
  validateWorkCapabilityRuntimeV2,
  validateWorkCapabilityToolCatalogV2,
  validateWorkCapabilityToolRequestV2,
} from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(canonical(value)).digest("hex");
const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string" }, secret: { type: "string" } } };
const outputSchema = { type: "object", additionalProperties: false, required: ["ok"], properties: { ok: { type: "boolean" } } };
const baseTime = "2026-08-10T14:00:00Z";

function binding(overrides = {}) {
  return {
    targetClass: "public-api",
    scope: { mode: "public", endpoints: ["https://api.example.com"], destinations: [] },
    egress: { mode: "public", destinations: [] },
    credentials: { refs: ["api-token"] },
    idempotency: { required: true, keys: ["text"] },
    approval: { required: true, mode: "operator-approval" },
    inputClassification: "internal",
    outputClassification: "internal",
    budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 1000 }, cumulative: { maxCalls: 10, maxBytes: 8192, maxDurationMs: 5000 } },
    receipt: { required: true, form: "pixel-receipt-v1" },
    neverEgress: ["secret"],
    ...overrides,
  };
}

function pack(toolOverrides = {}, bindingOverrides = {}, securityOverrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v2.schema.json",
    schemaVersion: 2, kind: "deep-work-capability-pack", id: "broad-tools", version: "1.0.0",
    name: "Broad tools", description: "v2 admission-only broad tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack", admissionOnly: true },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/o/m@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "m", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "A", description: "D", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "external", binding: binding(bindingOverrides), ...toolOverrides }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { networkMode: "public", networkDestinations: ["https://api.example.com"], credentialRefs: ["api-token"], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, admissionOnly: true, ...securityOverrides },
    boundary: "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.",
  };
}

function catalog() {
  const tool = { name: "analyze", exposedName: "pixel_cap_analyze", title: "A", description: "D", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" }, inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema) };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-catalog-v2.schema.json", schemaVersion: 2, operation: "pixel-work-capability-tool-catalog-v2",
    createdAt: baseTime, expiresAt: "2026-08-10T14:30:00Z", jobId: "work-1786366800000-abcdef123456",
    authorizationSha256: digest("1"), planSha256: digest("2"), leaseSha256: digest("3"), checkpointSha256: digest("4"),
    pack: { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), schemaVersion: 2 },
    dataClassification: "internal", maxSessions: 2, grantLifetimeMs: 60000,
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 1000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    initialEventHeadSha256: null, settledSessions: 0, tools: [tool],
    authority: { grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Admission-only job-scoped OMP catalog derived from one exact v2 authorization and signed v2 pack. It registers only namespaced tool declarations and grants no call; every request still requires controller and watchdog admission and v2 runtime is not enabled.",
  };
}

function policy() {
  const pk = { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), schemaVersion: 2, tools: [{ name: "analyze", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" } }], classifications: ["public", "internal"] };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v2.schema.json", schemaVersion: 2,
    policyId: `workcappolicy-${Date.parse(baseTime)}-abcdef123456`, createdAt: baseTime, expiresAt: "2026-08-11T14:00:00Z",
    profiles: ["builder", "researcher"], packs: [pk],
    limits: { maxSessionsPerLease: 2, maxGrantLifetimeMs: 60000, maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 1000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    watchdog: { maxFailures: 5, maxRepeatedEquivalent: 2, maxRepeatedFailure: 3, maxEventsWithoutVerifiedProgress: 10 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Admission-only controller allowlist for exact signed v2 capability declarations. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority; v2 runtime is not enabled.",
  };
}

function authorization() {
  const tool = { name: "analyze", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-job-authorization-v2.schema.json", schemaVersion: 2,
    authorizationId: `workcapauth-${Date.parse(baseTime)}-abcdef123456`, jobId: "work-1786366800000-abcdef123456",
    authorizedAt: baseTime, expiresAt: "2026-08-10T14:30:00Z", planSha256: digest("1"), leaseSha256: digest("2"),
    consumptionSha256: digest("3"), checkpointSha256: digest("4"), controllerPolicySha256: digest("7"),
    pack: { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), schemaVersion: 2 },
    tools: [tool], dataClassification: "internal", maxSessions: 2, grantLifetimeMs: 60000,
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 1000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    watchdog: { maxToolCalls: 2, maxFailures: 5, maxRepeatedEquivalent: 2, maxRepeatedFailure: 3, maxEventsWithoutVerifiedProgress: 10 },
    authority: { grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Exact admission-only v2 job, consumed lease, running checkpoint, signed pack, tool, classification, resource, and watchdog envelope. It grants no call by itself; each call still requires a fresh single-use grant and v2 runtime is not enabled.",
  };
}

function request() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-request-v2.schema.json", schemaVersion: 2,
    requestId: `workcaprequest-${Date.parse(baseTime)}-abcdef123456`, jobId: "work-1786366800000-abcdef123456", createdAt: baseTime,
    authorizationSha256: digest("1"), planSha256: digest("2"), leaseSha256: digest("3"), checkpointSha256: digest("4"),
    pack: { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), schemaVersion: 2 },
    tool: "analyze", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" },
    arguments: { text: "pixel" }, dataClassification: "internal",
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 1000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    expectedEventHeadSha256: null,
    authority: { directExecution: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Private untrusted request for one exact v2 broad-tools capability declaration. Arguments remain job-scoped data; this request grants no execution, credential, network, external-effect, scope-expansion, or completion authority and v2 runtime is not enabled.",
  };
}

function grant() {
  const tool = { name: "analyze", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-grant-v2.schema.json", schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456", jobId: "work-1786366800000-abcdef123456",
    issuedAt: baseTime, expiresAt: "2026-08-10T14:30:00Z", singleUse: true, checkpointSha256: digest("4"),
    pack: { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), schemaVersion: 2 },
    tools: [tool], dataClassification: "internal",
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 1000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    authority: { hostAccess: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Admission-only exact expiring single-use grant for one signed v2 capability declaration. It grants no call, host, credential, network, external-effect, scope-expansion, or completion authority and v2 runtime is not enabled.",
  };
}

function lease() {
  const tool = { name: "analyze", effectClass: "external", bindingSummary: { targetClass: "public-api", inputClassification: "internal", outputClassification: "internal" } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v2.schema.json", schemaVersion: 2,
    leaseId: "worklease-1786366800000-abcdef123456", jobId: "work-1786366800000-abcdef123456", issuedAt: baseTime, expiresAt: "2026-08-10T14:30:00Z",
    singleUse: true, iteration: 1, continuation: null, planSha256: digest("1"), inputSetSha256: digest("8"), policySha256: digest("9"),
    executor: { id: "omp", version: "1.0.0", sourceCommit: digest("e").slice(0, 40), license: "MIT", artifactSha256: digest("f"), rpcProtocolVersion: 2 },
    model: { route: "local-only", provider: "llama.cpp", id: "qwen", backendImageDigest: `sha256:${digest("a")}`, contextWindow: 32768, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
    isolation: { mode: "hardened-container", runnerImageDigest: `sha256:${digest("b")}`, freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, inputMount: "read-only", workspaceMount: "disposable-read-write", artifactMount: "write-only-staging", destroyAfterRun: true },
    grantedCapabilities: { tools: [tool], network: { mode: "public", services: [], destinations: ["api.example.com"] } },
    budgets: { maxRuntimeSeconds: 3600, maxIterations: 20, maxToolCalls: 2000, maxConcurrentSubagents: 4, maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4, maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 1073741824, maxNetworkBytes: 104857600, maxFailures: 5, noProgressLimit: 3 },
    authority: { hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false, merge: false, deploy: false, publish: false, purchase: false, policyMutation: false, acceptanceCriteriaMutation: false, leaseExpansion: false },
    outputGate: { allowedKinds: ["finding-report", "patch"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false },
    boundary: "Admission-only v2 lease for one signed broad-tools capability declaration. It grants no call, host, credential, network, external-effect, merge, deploy, publish, purchase, policy, or completion authority; v2 runtime is not enabled.",
  };
}

function runtime() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-runtime-v2.schema.json", schemaVersion: 2, operation: "pixel-work-capability-runtime-v2",
    pack: { id: "broad-tools", version: "1.0.0", packSha256: digest("5"), treeSha256: digest("6"), signerIdentity: "pixel-fixture", schemaVersion: 2 },
    admissionReceiptSha256: digest("1"), healthReceiptSha256: digest("2"),
    grant: { grantId: "workcapgrant-1786366800000-abcdef123456", grantSha256: digest("3"), jobId: "work-1786366800000-abcdef123456", checkpointSha256: digest("4"), dataClassification: "internal", tool: "analyze", effectClass: "external" },
    claim: { claimId: "workcapclaim-1786366800001-abcdef123456", claimSha256: digest("5") },
    operationCustody: { bindingSha256: digest("6"), token: digest("7") },
    request: { inputBytes: 128, watchdogDecisionSha256: digest("8") },
    startedAt: "2026-08-10T14:00:05Z", completedAt: "2026-08-10T14:00:06Z",
    result: { status: "failed", failureClass: "identity-or-schema", protocolVersion: null, toolSurfaceSha256: null, frames: 0, stdoutBytes: 0, stderrBytes: 0, stderrSha256: digest("9"), outputBytes: 0, externalEffects: false, authority: "none" },
    container: { reference: `sha256:${digest("b")}`, id: `sha256:${digest("d")}`, startState: "started", network: "none", removed: true, absenceProven: true },
    workspace: { used: false, removed: true, absenceProven: true },
    enabled: false,
    authority: { grantsFutureExecution: false, grantsReplay: false, grantsImagePull: false, grantsNetwork: false, grantsHostAccess: false, grantsCredentials: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsControllerRegistration: false, grantsCompletion: false },
    boundary: "Content-free admission-only v2 runtime evidence for one exact single-use capability grant. v2 runtime is not enabled; the receipt grants no replay, future execution, image pull, network, host access, credential, external effect, scope expansion, controller registration, or completion authority.",
  };
}

function consumption() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-consumption-v2.schema.json", schemaVersion: 2,
    claimId: "workcapclaim-1786366800001-abcdef123456", grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456", claimedAt: "2026-08-10T14:00:01Z", grantSha256: digest("b"), packSha256: digest("5"), checkpointSha256: digest("4"),
    status: "consumed", externalEffects: false,
    boundary: "Immutable replay tombstone for one admission-only v2 capability grant. It contains no tool arguments, result content, credential, network, external-effect, scope-expansion, or completion authority.",
  };
}

test("v2 pack validator accepts valid public API, private-LAN SSH, local filesystem, and browser declarations", () => {
  assert.deepEqual(validateWorkCapabilityPackV2(pack()), []);
  assert.deepEqual(validateWorkCapabilityPackV2(pack({}, { targetClass: "private-lan", scope: { mode: "private-allowlist", endpoints: [], destinations: ["10.0.0.5:22"] }, egress: { mode: "private-allowlist", destinations: ["10.0.0.5:22"] }, credentials: { refs: ["ssh-key"] } }, { networkMode: "private-allowlist", networkDestinations: ["10.0.0.5:22"], credentialRefs: ["ssh-key"] })), []);
  assert.deepEqual(validateWorkCapabilityPackV2(pack({}, { targetClass: "local-filesystem", scope: { mode: "private-allowlist", endpoints: [], destinations: ["/srv/data"] }, egress: { mode: "none", destinations: [] }, credentials: { refs: [] }, budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 1000 }, cumulative: { maxCalls: 10, maxBytes: 8192, maxDurationMs: 5000 } } }, { networkMode: "private-allowlist", networkDestinations: ["/srv/data"], credentialRefs: [] })), []);
  assert.deepEqual(validateWorkCapabilityPackV2(pack({}, { targetClass: "browser", scope: { mode: "public", endpoints: ["https://web.example.com"], destinations: [] }, egress: { mode: "public", destinations: [] } }, { networkMode: "public", networkDestinations: ["https://web.example.com"] })), []);
  assert.deepEqual(validateWorkCapabilityPackV2(pack({}, { targetClass: "mailbox", scope: { mode: "private-allowlist", endpoints: [], destinations: ["mail.internal:993"] }, egress: { mode: "private-allowlist", destinations: ["mail.internal:993"] } }, { networkMode: "private-allowlist", networkDestinations: ["mail.internal:993"] })), []);
});

test("v2 pack validator rejects malformed, wildcard, unsorted, duplicate, and ambiguous bindings", () => {
  const reject = (value) => assert.ok(validateWorkCapabilityPackV2(value).length > 0, JSON.stringify(value));
  reject(pack({}, { scope: { mode: "public", endpoints: ["https://*.example.com"], destinations: [] }, egress: { mode: "public", destinations: [] } }));
  reject(pack({}, { scope: { mode: "public", endpoints: ["https://b.example.com", "https://a.example.com"], destinations: [] }, egress: { mode: "public", destinations: [] } }, { networkMode: "public", networkDestinations: ["https://b.example.com", "https://a.example.com"] }));
  reject(pack({}, { credentials: { refs: ["api-token", "api-token"] } }));
  reject(pack({}, { scope: { mode: "private-allowlist", endpoints: [], destinations: ["10.0.0.5:22"] }, egress: { mode: "public", destinations: [] } }, { networkMode: "public", networkDestinations: [] }));
  reject(pack({}, { targetClass: "implicit" }));
  reject(pack({}, { idempotency: { required: false, keys: ["text"] } }));
  reject(pack({}, { neverEgress: ["not-a-field"] }));
  reject(pack({}, { budgets: { perRun: { maxCalls: 50, maxBytes: 4096, maxDurationMs: 1000 }, cumulative: { maxCalls: 10, maxBytes: 8192, maxDurationMs: 5000 } } }));
  reject(pack({}, { scope: { mode: "public", endpoints: ["https://api.example.com"], destinations: ["10.0.0.5:22"] }, egress: { mode: "public", destinations: [] } }));
  reject(pack({}, { receipt: { required: false, form: "pixel-receipt-v1" } }));
});

test("v2 pack validator rejects hash drift, unsorted tools, schema-version confusion, and non-admission-only claims", () => {
  const drift = pack(); drift.tools[0].inputSchemaSha256 = digest("9");
  assert.ok(validateWorkCapabilityPackV2(drift).length > 0);
  const unsortedTools = pack(); unsortedTools.tools.push({ ...unsortedTools.tools[0], name: "zzz" }); unsortedTools.tools.reverse();
  assert.ok(validateWorkCapabilityPackV2(unsortedTools).length > 0);
  const notAdmissionOnly = pack(); notAdmissionOnly.provenance.admissionOnly = false;
  assert.ok(validateWorkCapabilityPackV2(notAdmissionOnly).length > 0);
  const schemaConfusion = pack(); schemaConfusion.schemaVersion = 1; schemaConfusion.$schema = "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json";
  assert.ok(validateWorkCapabilityPackV2(schemaConfusion).length > 0);
  const mixedModes = pack(); mixedModes.tools.push({ name: "private", title: "P", description: "P", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "external", binding: binding({ targetClass: "private-lan", scope: { mode: "private-allowlist", endpoints: [], destinations: ["10.0.0.5:22"] }, egress: { mode: "private-allowlist", destinations: ["10.0.0.5:22"] } }) });
  assert.ok(validateWorkCapabilityPackV2(mixedModes).length > 0);
});

test("all nine v2 envelope validators accept their canonical declarations", () => {
  assert.deepEqual(validateWorkCapabilityPackV2(pack()), []);
  assert.deepEqual(validateWorkCapabilityToolCatalogV2(catalog()), []);
  assert.deepEqual(validateWorkCapabilityControllerPolicyV2(policy()), []);
  assert.deepEqual(validateWorkCapabilityJobAuthorizationV2(authorization()), []);
  assert.deepEqual(validateWorkCapabilityToolRequestV2(request()), []);
  assert.deepEqual(validateWorkCapabilityGrantV2(grant()), []);
  assert.deepEqual(validateWorkCapabilityLeaseV2(lease()), []);
  assert.deepEqual(validateWorkCapabilityRuntimeV2(runtime()), []);
  assert.deepEqual(validateWorkCapabilityConsumptionV2(consumption()), []);
});

test("v2 envelope validators enforce canonical order, unique lists, and lifecycle/time consistency", () => {
  const badCatalog = catalog(); badCatalog.tools.push({ ...badCatalog.tools[0], name: "zzz" }); badCatalog.tools.reverse();
  assert.ok(validateWorkCapabilityToolCatalogV2(badCatalog).length > 0);
  const badPolicy = policy(); badPolicy.expiresAt = badPolicy.createdAt;
  assert.ok(validateWorkCapabilityControllerPolicyV2(badPolicy).length > 0);
  const badAuth = authorization(); badAuth.watchdog.maxToolCalls = 99;
  assert.ok(validateWorkCapabilityJobAuthorizationV2(badAuth).length > 0);
  const badRequest = request(); badRequest.requestId = `workcaprequest-${Date.parse(baseTime) - 1}-abcdef123456`;
  assert.ok(validateWorkCapabilityToolRequestV2(badRequest).length > 0);
  const badGrant = grant(); badGrant.expiresAt = badGrant.issuedAt;
  assert.ok(validateWorkCapabilityGrantV2(badGrant).length > 0);
  const badLease = lease(); badLease.grantedCapabilities.network = { mode: "private-allowlist", services: [], destinations: [] };
  assert.ok(validateWorkCapabilityLeaseV2(badLease).length > 0);
  const badRuntime = runtime(); badRuntime.completedAt = "2026-08-10T13:59:00Z";
  assert.ok(validateWorkCapabilityRuntimeV2(badRuntime).length > 0);
  const enabledRuntime = runtime(); enabledRuntime.enabled = true;
  assert.ok(validateWorkCapabilityRuntimeV2(enabledRuntime).length > 0);
  const authorityRuntime = runtime(); authorityRuntime.authority = { ...authorityRuntime.authority, grantsNetwork: true };
  assert.ok(validateWorkCapabilityRuntimeV2(authorityRuntime).length > 0);
  const badConsumption = consumption(); badConsumption.status = "consumed";
  assert.deepEqual(validateWorkCapabilityConsumptionV2(badConsumption), []);
});

test("v2 binding summary rejects network targets for local effect classes", () => {
  const c = catalog(); c.tools[0].effectClass = "read-only"; c.tools[0].bindingSummary.targetClass = "public-api";
  assert.ok(validateWorkCapabilityToolCatalogV2(c).length > 0);
  const g = grant(); g.tools[0].effectClass = "workspace"; g.tools[0].bindingSummary.targetClass = "browser";
  assert.ok(validateWorkCapabilityGrantV2(g).length > 0);
});

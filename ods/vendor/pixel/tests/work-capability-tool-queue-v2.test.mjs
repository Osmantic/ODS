import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { signCapabilitySshApprovalV2 } from "../deploy/work-controller/capability-ssh-approval-v2.mjs";
import {
  capabilityToolQueueV2Boundary, capabilityToolQueueV2ConfigBoundary,
  enqueueCapabilityToolRequestV2, initializeCapabilityToolQueueV2, processCapabilityToolQueueV2,
  readCapabilityToolApprovalV2, readCapabilityToolResponseV2, statusCapabilityToolQueueV2,
  submitCapabilityToolApprovalSignatureV2,
} from "../deploy/work-controller/capability-tool-queue-v2.mjs";
import { processCapabilityToolQueueV2TestOnly, submitCapabilityToolApprovalSignatureV2TestOnly, writeClaimExclusiveTestOnly } from "../deploy/work-controller/capability-tool-queue-v2.test-support.mjs";
import { validateWorkCapabilityQueueRequestV2, validateWorkCapabilityToolRequest, canonical } from "../scripts/lib/work-contract.mjs";
import { executeOperationalV2 } from "../deploy/work-controller/capability-runtime-operational-v2.mjs";
import { publishResearchToolResponse } from "../deploy/work-research-broker/tool-queue.mjs";
import { buildRealCourierResponse, v2RetrievalPack, custody as retrievalCustody } from "./test-fixtures.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");
const compilerPath = fileURLToPath(new URL("../deploy/work-controller/capability-controller.mjs", import.meta.url));
const compilerSha256 = createHash("sha256").update(readFileSync(compilerPath)).digest("hex");
const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const sshDestinationAlias = "tower-one";
const sshCredentialRef = "tower-one-hostname-key";

const authority = { hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false, merge: false, deploy: false, publish: false, purchase: false, policyMutation: false, acceptanceCriteriaMutation: false, leaseExpansion: false };
const budgets = { maxRuntimeSeconds: 3600, maxIterations: 20, maxToolCalls: 2000, maxConcurrentSubagents: 4, maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4, maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 1073741824, maxNetworkBytes: 104857600, maxFailures: 5, noProgressLimit: 3 };
const boundary = {
  plan: "Private immutable Deep Work plan. It authorizes no execution by itself and contains no credential, host path, provider secret, merge, deployment, publication, purchase, production, or external-effect authority.",
  lease: "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.",
  consumption: "Private immutable tombstone proving one lease was consumed once. It grants no replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.",
  checkpoint: "Private hash-bound recovery record. Contains only immutable digests, bounded counters, and transition facts. Cannot authorize or replay actions.",
  pack: "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.",
  policy: "Admission-only controller allowlist for exact signed v2 capability declarations. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority; v2 runtime is not enabled.",
};
const REQ_BOUNDARY = "Private untrusted durable v2 queue request for one exact controller-admitted operation. The caller may name only a signed-pack tool and its bounded input; it cannot select a runtime version, lane, pack, policy, adapter, grant, workspace root, state root, approval, signature, credentials, network, external effect, budget, authority expansion, completion, or security mode; v2 runtime is not enabled.";
const SHA_RE = /^[a-f0-9]{64}$/u;

function v2Pack() {
  const inputSchema = { type: "object", additionalProperties: false, required: ["relativePath", "content"], properties: { relativePath: { type: "string" }, content: { type: "string" } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer" } } };
  return {
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
    boundary: boundary.pack,
  };
}

function sshPack() {
  const base = v2Pack();
  const inputSchema = {
    type: "object", additionalProperties: false,
    required: ["destinationAlias", "commandId", "requestId"],
    properties: {
      destinationAlias: { const: sshDestinationAlias }, commandId: { const: "hostname" },
      requestId: { type: "string" },
    },
  };
  const outputSchema = { type: "object", additionalProperties: false, required: ["hostname"], properties: { hostname: { type: "string" } } };
  const binding = {
    targetClass: "ssh",
    scope: { mode: "private-allowlist", endpoints: [], destinations: [sshDestinationAlias] },
    egress: { mode: "private-allowlist", destinations: [sshDestinationAlias] },
    credentials: { refs: [sshCredentialRef] },
    idempotency: { required: true, keys: ["requestId"] },
    approval: { required: true, mode: "operator-approval" },
    inputClassification: "internal", outputClassification: "internal",
    budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 } },
    receipt: { required: true, form: "content-free" }, neverEgress: ["requestId"],
  };
  return {
    ...base, id: "fixture-ssh", name: "Fixture SSH hostname", description: "One owner-signed forced-command hostname proof.",
    adapter: { ...base.adapter, serverName: "fixture-ssh", workspace: "none" },
    tools: [{
      name: "ssh_hostname", title: "SSH hostname proof", description: "Connect to one trusted alias whose key forces hostname.",
      effectClass: "read-only", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), binding,
    }],
    data: { acceptedClassifications: ["internal"], returnedClassifications: ["internal"], rawRetention: "job-only" },
    limits: { ...base.limits, maxInputBytes: 4096, maxOutputBytes: 4096, maxRuntimeMs: 5000, maxWorkspaceBytes: 0 },
    security: { ...base.security, networkMode: "private-allowlist", networkDestinations: [sshDestinationAlias], credentialRefs: [sshCredentialRef] },
    budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 } },
  };
}

function v2Policy(pack) {
  const packSha256 = capabilityPackSha256(pack);
  const declared = pack.tools[0];
  const bindingSummary = declared.binding === null
    ? { targetClass: "local-filesystem", inputClassification: "internal", outputClassification: "internal" }
    : { targetClass: declared.binding.targetClass, inputClassification: declared.binding.inputClassification, outputClassification: declared.binding.outputClassification };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v2.schema.json", schemaVersion: 2,
    policyId: "workcappolicy-1786363200000-abcdef123456", createdAt: "2026-08-10T12:00:00Z", expiresAt: "2026-08-10T15:00:00Z",
    profiles: ["builder"],
    packs: [{ id: pack.id, version: pack.version, packSha256, treeSha256: pack.provenance.treeSha256, schemaVersion: 2, tools: [{ name: declared.name, effectClass: declared.effectClass, bindingSummary }], classifications: ["public", "internal"] }],
    limits: { maxSessionsPerLease: 8, maxGrantLifetimeMs: 30000, maxInputBytes: pack.limits.maxInputBytes, maxOutputBytes: pack.limits.maxOutputBytes, maxRuntimeMs: pack.limits.maxRuntimeMs, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: pack.limits.maxWorkspaceBytes },
    watchdog: { maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: boundary.policy,
  };
}

function custody(pack) {
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
  return { plan, lease, consumption, checkpoint, pack, policy: v2Policy(pack), expectedPackSha256: capabilityPackSha256(pack) };
}

function authorizationFrom(value) {
  const packSha = value.expectedPackSha256;
  const declared = value.pack.tools[0];
  const bindingSummary = declared.binding === null
    ? { targetClass: "local-filesystem", inputClassification: "internal", outputClassification: "internal" }
    : { targetClass: declared.binding.targetClass, inputClassification: declared.binding.inputClassification, outputClassification: declared.binding.outputClassification };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-job-authorization-v2.schema.json", schemaVersion: 2,
    authorizationId: `workcapauth-${Date.parse("2026-08-10T12:30:00Z")}-abcdef123456`, jobId: value.plan.jobId,
    authorizedAt: "2026-08-10T12:30:00Z", expiresAt: "2026-08-10T15:00:00Z",
    planSha256: sha(value.plan), leaseSha256: sha(value.lease), consumptionSha256: sha(value.consumption), checkpointSha256: sha(value.checkpoint),
    controllerPolicySha256: sha(value.policy),
    pack: { id: value.pack.id, version: value.pack.version, packSha256: packSha, treeSha256: value.pack.provenance.treeSha256, schemaVersion: 2 },
    tools: [{ name: declared.name, effectClass: declared.effectClass, bindingSummary }],
    dataClassification: value.plan.dataClassification, maxSessions: 8, grantLifetimeMs: 30000,
    limits: { maxInputBytes: value.pack.limits.maxInputBytes, maxOutputBytes: value.pack.limits.maxOutputBytes, maxRuntimeMs: value.pack.limits.maxRuntimeMs, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: value.pack.limits.maxWorkspaceBytes },
    watchdog: { maxToolCalls: 8, maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Exact admission-only v2 job, consumed lease, running checkpoint, signed pack, tool, classification, resource, and watchdog envelope. It grants no call by itself; each call still requires a fresh single-use grant and v2 runtime is not enabled.",
  };
}

async function privateDir(t, label) {
  const dir = await mkdtemp(join(tmpdir(), label));
  t.after(() => rm(dir, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(dir, 0o700);
  return dir;
}

function configFor({ stateRoot, workspaceRoot, pack = v2Pack(), ...overrides }) {
  const value = custody(pack);
  const authorization = authorizationFrom(value);
  return {
    schemaVersion: 2, queueId: `workcapqueuev2-${baseTime}-abcdef123456`,
    authorization, ...value, workspaceRoot, stateRoot, compilerPath, compilerSha256,
    enabled: false, boundary: capabilityToolQueueV2ConfigBoundary, ...overrides,
  };
}

function retrievalConfigFor({ stateRoot, workspaceRoot, courierQueueRoot }) {
  const base = retrievalCustody();
  const expectedPackSha256 = capabilityPackSha256(base.pack);
  const value = { ...base, expectedPackSha256 };
  const authorization = authorizationFrom(value);
  return {
    schemaVersion: 2, queueId: `workcapqueuev2-${baseTime}-abcdef123456`,
    authorization, ...value, workspaceRoot, stateRoot, compilerPath, compilerSha256,
    enabled: false, boundary: capabilityToolQueueV2ConfigBoundary,
    research: { courierQueueRoot, timeoutMilliseconds: 5000, pollMilliseconds: 5 },
  };
}

let requestCounter = 0;
function queueRequest(cfg, { tool = "write", relativePath = "note.txt", content = "hello", at = baseTime + 179000 + (requestCounter += 1), ...overrides } = {}) {
  const date = new Date(at);
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-queue-request-v2.schema.json", schemaVersion: 2,
    requestId: `workcaprequest-${date.getTime()}-${String(requestCounter).padStart(2, "0").repeat(6).slice(0, 12)}`,
    jobId: cfg.authorization.jobId, createdAt: date.toISOString(),
    authorizationSha256: sha(cfg.authorization), planSha256: sha(cfg.plan), leaseSha256: sha(cfg.lease), checkpointSha256: sha(cfg.checkpoint), packSha256: cfg.expectedPackSha256,
    tool, relativePath, content, dataClassification: cfg.plan.dataClassification,
    authority: { directExecution: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: REQ_BOUNDARY, ...overrides,
  };
}

async function queueFixture(t) {
  const workspace = await privateDir(t, "pixel-v2q-ws-"), stateRoot = await privateDir(t, "pixel-v2q-state-");
  const parent = await privateDir(t, "pixel-v2q-root-");
  const config = configFor({ stateRoot, workspaceRoot: workspace });
  const queueRoot = join(parent, "queue");
  await initializeCapabilityToolQueueV2({ queueRoot, config });
  let clockBase = baseTime + 185000;
  const options = (overrides = {}) => {
    clockBase += 1000;
    let tick = clockBase;
    const clock = () => new Date(tick++);
    return {
      queueRoot, config, clock,
      suffixes: {
        grant: "000000000201", operation: "0000000000000201", claimed: "000000000301",
        approvalNonce: "1".repeat(64), awaitingOwnerSignature: "000000000306", authorized: "000000000302",
        launching: "000000000303", runtimeReturned: "000000000304", settled: "000000000305",
      },
      ...overrides,
    };
  };
  return { workspace, stateRoot, config, queueRoot, options };
}

function sshQueueRequest(cfg, overrides = {}) {
  const request = queueRequest(cfg, { tool: "ssh_hostname" });
  delete request.relativePath;
  delete request.content;
  return { ...request, destinationAlias: sshDestinationAlias, commandId: "hostname", ...overrides };
}

function publicRetrievalQueueRequest(cfg, overrides = {}) {
  const request = queueRequest(cfg, { tool: "research" });
  delete request.relativePath;
  delete request.content;
  return {
    ...request,
    query: "test query for retrieval",
    sourceTypes: ["web"],
    domains: [],
    maxResults: 3,
    maxSourcesToFetch: 2,
    maxSourceBytes: 16384,
    ...overrides,
  };
}

async function respondToNextCourierRequest(requestDir, responseDir) {
  const deadline = performance.now() + 5000;
  const requestRe = /^req-researchtool-[0-9]{13}-[a-f0-9]{32}\.json$/u;
  for (;;) {
    const entries = await readdir(requestDir);
    const match = entries.find((name) => requestRe.test(name));
    if (match) {
      const request = JSON.parse(await readFile(join(requestDir, match), "utf8"));
      const response = buildRealCourierResponse(request);
      await publishResearchToolResponse(responseDir, response);
      return request;
    }
    if (performance.now() >= deadline) {
      throw new Error("test courier did not observe the runtime request");
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

async function sshQueueFixture(t) {
  if (process.platform === "win32") { t.skip("owner-signed SSH queue execution is qualified on Linux"); return null; }
  const workspace = await privateDir(t, "pixel-v2q-ssh-ws-");
  const stateRoot = await privateDir(t, "pixel-v2q-ssh-state-");
  const parent = await privateDir(t, "pixel-v2q-ssh-root-");
  const signingKey = join(parent, "owner");
  const allowedSignersPath = join(parent, "allowed_signers");
  const identityFile = join(parent, "ssh-identity");
  const knownHostsFile = join(parent, "known_hosts");
  const sshBinary = join(parent, "ssh");
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", signingKey], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") { t.skip("ssh-keygen is unavailable"); return null; }
  assert.equal(generated.status, 0, generated.stderr);
  await chmod(signingKey, 0o600);
  await writeFile(allowedSignersPath, `pixel-owner ${(await readFile(`${signingKey}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await writeFile(identityFile, "fixture credential\n", { mode: 0o600 });
  await writeFile(knownHostsFile, "[192.0.2.10]:22 ssh-ed25519 AAAAFIXTURE\n", { mode: 0o600 });
  await writeFile(sshBinary, "#!/bin/sh\nprintf 'run\\n' >> \"$0.count\"\nprintf 'tower-one\\n'\n", { mode: 0o700 });
  const ssh = {
    sshBinary, allowedSignersPath, operatorIdentity: "pixel-owner",
    destinations: {
      [sshDestinationAlias]: {
        enabled: true, host: "192.0.2.10", port: 22, user: "pixel", credentialRef: sshCredentialRef,
        identityFile, knownHostsFile, commandId: "hostname", expectedHostname: "tower-one",
      },
    },
  };
  const config = configFor({ stateRoot, workspaceRoot: workspace, pack: sshPack(), ssh });
  const queueRoot = join(parent, "queue");
  await initializeCapabilityToolQueueV2({ queueRoot, config });
  let clockBase = baseTime + 185000;
  const options = (overrides = {}) => {
    clockBase += 1000;
    let tick = clockBase;
    return {
      queueRoot, config, clock: () => new Date(tick++),
      suffixes: {
        grant: "000000000211", operation: "0000000000000211", claimed: "000000000311",
        approvalNonce: "2".repeat(64), awaitingOwnerSignature: "000000000316", authorized: "000000000312",
        launching: "000000000313", runtimeReturned: "000000000314", settled: "000000000315",
      },
      ...overrides,
    };
  };
  return { workspace, stateRoot, config, queueRoot, signingKey, sshBinary, options };
}

async function retrievalQueueFixture(t, { enabled = false } = {}) {
  const workspace = await privateDir(t, "pixel-v2q-retr-ws-");
  const stateRoot = await privateDir(t, "pixel-v2q-retr-state-");
  const parent = await privateDir(t, "pixel-v2q-retr-root-");
  const courierQueueRoot = join(parent, "courier");
  const courierRequestDir = join(courierQueueRoot, "requests");
  const courierResponseDir = join(courierQueueRoot, "responses");
  await mkdir(courierRequestDir, { recursive: true, mode: 0o700 });
  await mkdir(courierResponseDir, { recursive: true, mode: 0o700 });
  const config = retrievalConfigFor({ stateRoot, workspaceRoot: workspace, courierQueueRoot });
  config.enabled = enabled;
  const queueRoot = join(parent, "queue");
  const initialized = await initializeCapabilityToolQueueV2({ queueRoot, config });
  let clockBase = baseTime + 185000;
  const options = (overrides = {}) => {
    clockBase += 1000;
    let tick = clockBase;
    const clock = () => new Date(tick++);
    return {
      queueRoot, config, clock,
      suffixes: {
        grant: "000000000221", operation: "0000000000000221", claimed: "000000000321",
        authorized: "000000000322", launching: "000000000323", runtimeReturned: "000000000324", settled: "000000000325",
      },
      ...overrides,
    };
  };
  return { workspace, stateRoot, config, queueRoot, courierQueueRoot, courierRequestDir, courierResponseDir, options, initialized };
}

async function expectFile(root, rel, content) {
  assert.equal(await readFile(join(root, rel), "utf8"), content);
}
async function noFile(root, rel) {
  assert.equal(await readFile(join(root, rel), "utf8").then(() => true, () => false), false, `expected no file at ${rel}`);
}

test("durable v2 queue: enqueue/process/read happy path with single-level workspace write", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "note.txt", content: "hello v2" });
  const enq = await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal(enq.status, "queued-disabled");
  const result = await processCapabilityToolQueueV2TestOnly(f.options());
  assert.equal(result.status, "succeeded");
  await expectFile(f.workspace, "note.txt", "hello v2");
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.schemaVersion, 2); assert.equal(response.status, "succeeded");
  assert.equal(response.requestId, request.requestId); assert.equal(response.jobId, f.config.authorization.jobId);
  assert.equal(response.authorizationSha256, sha(f.config.authorization)); assert.equal(response.checkpointSha256, sha(f.config.checkpoint));
  assert.ok(/^workcapv2-[0-9]{13}-[a-f0-9]{16}$/.test(response.operationId));
  assert.equal(response.structuredContent.path, "note.txt");
  assert.equal(response.outputSha256, sha("hello v2"));
  for (const [key, value] of Object.entries(response.authority)) assert.equal(value, false, `response grants ${key}`);
  const status = await statusCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(status.settled, 1); assert.equal(status.queued, 0);
});

test("public-retrieval queue executes the real Courier protocol once and publishes exact untrusted evidence", async (t) => {
  const f = await retrievalQueueFixture(t);
  const request = publicRetrievalQueueRequest(f.config);
  assert.deepEqual(validateWorkCapabilityQueueRequestV2(request), []);
  const enqueued = await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal(enqueued.status, "queued-disabled");
  const broker = respondToNextCourierRequest(f.courierRequestDir, f.courierResponseDir);
  const result = await processCapabilityToolQueueV2TestOnly(f.options());
  const observed = await broker;
  assert.equal(observed.query, request.query);
  assert.deepEqual(observed.sourceTypes, request.sourceTypes);
  assert.deepEqual(observed.domains, request.domains);
  assert.equal(observed.maxResults, request.maxResults);
  assert.equal(observed.maxSourcesToFetch, request.maxSourcesToFetch);
  assert.equal(observed.maxSourceBytes, request.maxSourceBytes);
  assert.equal(result.status, "succeeded");
  assert.equal(result.response.lane, "public-retrieval");
  assert.match(result.response.operationId, /^workcapv2-[0-9]{13}-[a-f0-9]{16}$/u);
  const structured = result.response.structuredContent;
  assert.equal(structured.operationId, result.response.operationId);
  assert.equal(structured.lane, "public-retrieval");
  assert.equal(structured.status, "succeeded");
  assert.equal(structured.evidenceWrapper.trust, "untrusted");
  assert.equal(structured.evidenceWrapper.authority, "none");
  assert.ok(typeof structured.evidenceWrapper.text === "string" && structured.evidenceWrapper.text.length > 0);
  assert.equal(structured.outputSha256, result.response.outputSha256);
  assert.equal(structured.outputBytes, result.response.outputBytes);
  assert.equal(structured.receiptSha256, result.response.receiptSha256);
  const wrapperJson = JSON.stringify(structured.evidenceWrapper);
  assert.equal(result.response.outputSha256, sha(wrapperJson));
  assert.equal(result.response.outputBytes, Buffer.byteLength(wrapperJson, "utf8"));
  const published = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.deepEqual(published, result.response);
  for (const [key, value] of Object.entries(published.authority)) assert.equal(value, false, `public retrieval response grants ${key}`);
  assert.equal((await processCapabilityToolQueueV2TestOnly(f.options())).status, "idle");
  assert.equal((await readdir(f.courierRequestDir)).filter((name) => name.startsWith("req-")).length, 0, "consumed Courier request must be cleaned after a successful response");
});
test("public production path executes an enabled retrieval through real Courier with no injected seam", async (t) => {
  let now = baseTime + 185000;
  t.mock.method(Date, "now", () => now++);
  const f = await retrievalQueueFixture(t, { enabled: true });
  assert.equal(f.initialized.status, "initialized-enabled");
  assert.equal(f.initialized.enabled, true);
  const request = publicRetrievalQueueRequest(f.config);
  const enqueued = await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal(enqueued.status, "queued-enabled");
  assert.equal(enqueued.enabled, true);
  const broker = respondToNextCourierRequest(f.courierRequestDir, f.courierResponseDir);
  const result = await processCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  const observed = await broker;
  assert.equal(observed.query, request.query);
  assert.deepEqual(observed.sourceTypes, request.sourceTypes);
  assert.deepEqual(observed.domains, request.domains);
  assert.equal(observed.maxResults, request.maxResults);
  assert.equal(observed.maxSourcesToFetch, request.maxSourcesToFetch);
  assert.equal(observed.maxSourceBytes, request.maxSourceBytes);
  assert.equal(result.status, "succeeded");
  assert.equal(result.enabled, true);
  assert.equal(result.response.lane, "public-retrieval");
  const structured = result.response.structuredContent;
  assert.equal(structured.evidenceWrapper.trust, "untrusted");
  assert.equal(structured.evidenceWrapper.authority, "none");
  const wrapperJson = JSON.stringify(structured.evidenceWrapper);
  assert.equal(result.response.outputSha256, sha(wrapperJson));
  assert.equal(result.response.outputBytes, Buffer.byteLength(wrapperJson, "utf8"));
  const status = await statusCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(status.status, "idle-enabled");
  assert.equal(status.enabled, true);
  assert.equal(status.queued, 0);
  assert.equal(status.settled, 1);
  const idle = await processCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(idle.status, "idle");
  assert.equal(idle.enabled, true);
  assert.equal((await readdir(f.courierRequestDir)).filter((name) => name.startsWith("req-")).length, 0);
});
test("public-retrieval queue rejects mixed shape and missing trusted research config before custody", async (t) => {
  const f = await retrievalQueueFixture(t);
  const mixed = publicRetrievalQueueRequest(f.config, { relativePath: "bad.txt", content: "bad" });
  assert.ok(validateWorkCapabilityQueueRequestV2(mixed).length > 0);
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: mixed }), /invalid/u);
  const config = structuredClone(f.config);
  delete config.research;
  const queueRoot = join(f.queueRoot, "..", "no-research-queue");
  await initializeCapabilityToolQueueV2({ queueRoot, config });
  const request = publicRetrievalQueueRequest(config);
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot, config, request }), /no protected research runtime configuration/u);
  await assert.rejects(() => readFile(join(queueRoot, "current.json"), "utf8"), /ENOENT/u);
  await assert.rejects(() => readdir(join(f.stateRoot, "v2-runtime")), /ENOENT/u);
});
test("public-retrieval queue crash after settled broker effect reconciles uncertain without replay", async (t) => {
  const f = await retrievalQueueFixture(t);
  const request = publicRetrievalQueueRequest(f.config);
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const broker = respondToNextCourierRequest(f.courierRequestDir, f.courierResponseDir);
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    dependencyOverrides: {
      executeRuntime: async ({ v2, grant, runtimeRequest }) => {
        await executeOperationalV2({ grant, runtimeRequest, config: v2.config });
        throw new Error("crash after settled retrieval effect");
      },
    },
  })), /recovery is required/u);
  const observed = await broker;
  assert.equal(observed.query, request.query);
  const recovered = await processCapabilityToolQueueV2TestOnly(f.options());
  assert.equal(recovered.status, "uncertain-no-replay");
  assert.equal(recovered.response.status, "uncertain-no-replay");
  assert.equal(recovered.response.lane, "public-retrieval");
  assert.equal(recovered.response.structuredContent, null);
  assert.equal(recovered.response.receiptSha256, null);
  assert.equal(recovered.response.outputSha256, null);
  assert.equal(recovered.response.outputBytes, null);
  for (const [key, value] of Object.entries(recovered.response.authority)) assert.equal(value, false, `uncertain retrieval response grants ${key}`);
  assert.equal((await processCapabilityToolQueueV2TestOnly(f.options())).status, "idle");
  assert.equal((await readdir(f.courierRequestDir)).filter((name) => name.startsWith("req-")).length, 0, "recovery must not replay the settled Courier effect");
});
test("public production restart reconciles a settled enabled retrieval without replay", async (t) => {
  let now = baseTime + 300000;
  t.mock.method(Date, "now", () => now++);
  const f = await retrievalQueueFixture(t, { enabled: true });
  const request = publicRetrievalQueueRequest(f.config);
  const enqueued = await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal(enqueued.status, "queued-enabled");
  const broker = respondToNextCourierRequest(f.courierRequestDir, f.courierResponseDir);
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    dependencyOverrides: {
      executeRuntime: async ({ v2, grant, runtimeRequest }) => {
        await executeOperationalV2({ grant, runtimeRequest, config: v2.config });
        throw new Error("crash after settled enabled retrieval effect");
      },
    },
  })), /recovery is required/u);
  const observed = await broker;
  assert.equal(observed.query, request.query);
  const recovered = await processCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(recovered.status, "uncertain-no-replay");
  assert.equal(recovered.response.status, "uncertain-no-replay");
  assert.equal(recovered.enabled, true);
  assert.equal(recovered.response.lane, "public-retrieval");
  assert.equal(recovered.response.structuredContent, null);
  assert.equal(recovered.response.receiptSha256, null);
  assert.equal(recovered.response.outputSha256, null);
  assert.equal(recovered.response.outputBytes, null);
  for (const [key, value] of Object.entries(recovered.response.authority)) assert.equal(value, false, `uncertain retrieval response grants ${key}`);
  const idle = await processCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(idle.status, "idle");
  assert.equal(idle.enabled, true);
  assert.equal((await readdir(f.courierRequestDir)).filter((name) => name.startsWith("req-")).length, 0, "recovery must not replay the settled Courier effect");
});
test("owner-signed SSH queue rejects bad approval, executes the forced hostname once, and publishes exact proof", async (t) => {
  const f = await sshQueueFixture(t); if (!f) return;
  const invalidConfig = structuredClone(f.config);
  invalidConfig.ssh.destinations[sshDestinationAlias].host = "tower1.local";
  await assert.rejects(() => initializeCapabilityToolQueueV2({ queueRoot: join(f.queueRoot, "..", "invalid-host-queue"), config: invalidConfig }), /destination endpoint is invalid/u);
  const request = sshQueueRequest(f.config);
  assert.deepEqual(validateWorkCapabilityQueueRequestV2(request), []);
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });

  const waiting = await processCapabilityToolQueueV2TestOnly(f.options());
  assert.equal(waiting.status, "awaiting-owner-signature");
  const approval = await readCapabilityToolApprovalV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(waiting.approvalSha256, sha(approval));
  assert.equal(approval.destinationAlias, sshDestinationAlias);
  assert.equal(approval.commandId, "hostname");
  assert.equal(approval.authority.grantsGenericShell, false);
  await assert.rejects(() => readdir(join(f.stateRoot, "v2-runtime")), /ENOENT/u);

  const signingClock = () => new Date(Date.parse(approval.issuedAt) + 1000);
  const signature = await signCapabilitySshApprovalV2({ approval, signingKeyPath: f.signingKey, clock: signingClock });
  const damaged = signature.replace(/[A-Za-z]/u, (letter) => letter === "A" ? "B" : "A");
  await assert.rejects(() => submitCapabilityToolApprovalSignatureV2({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature, clock: signingClock,
  }), /expired|valid/u);
  await assert.rejects(() => submitCapabilityToolApprovalSignatureV2TestOnly({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature: damaged, clock: signingClock,
  }), /signature|verify|valid/u);
  await assert.rejects(() => readdir(join(f.stateRoot, "v2-runtime")), /ENOENT/u);

  const submitted = await submitCapabilityToolApprovalSignatureV2TestOnly({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature, clock: signingClock,
  });
  assert.equal(submitted.status, "submitted");
  assert.equal((await submitCapabilityToolApprovalSignatureV2TestOnly({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature, clock: signingClock,
  })).status, "already-submitted");
  await assert.rejects(() => submitCapabilityToolApprovalSignatureV2TestOnly({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature: `${signature}\n`, clock: signingClock,
  }), /differs from the exact submitted/u);

  let tick = Date.parse(approval.issuedAt) + 2000;
  const result = await processCapabilityToolQueueV2TestOnly(f.options({ clock: () => new Date(tick++) }));
  assert.equal(result.status, "succeeded");
  assert.equal(result.response.lane, "ssh-hostname");
  assert.equal(result.response.structuredContent.hostname, "tower-one");
  assert.equal(result.response.structuredContent.destinationAlias, sshDestinationAlias);
  assert.equal(await readFile(`${f.sshBinary}.count`, "utf8"), "run\n");
  assert.equal((await processCapabilityToolQueueV2TestOnly(f.options())).status, "idle");
  assert.equal(await readFile(`${f.sshBinary}.count`, "utf8"), "run\n");

  const decision = await readFile(join(f.queueRoot, "history", request.requestId, "decision.json"), "utf8");
  assert.doesNotMatch(decision, /192\.0\.2\.10|identityFile|knownHostsFile/u);
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.outputSha256, sha("tower-one"));
  assert.equal(response.authority.grantsReplay, false);
});

test("SSH queue resumes a staged signed decision after a controller crash and opens one connection", async (t) => {
  const f = await sshQueueFixture(t); if (!f) return;
  const request = sshQueueRequest(f.config);
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal((await processCapabilityToolQueueV2TestOnly(f.options())).status, "awaiting-owner-signature");
  const approval = await readCapabilityToolApprovalV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  const signingClock = () => new Date(Date.parse(approval.issuedAt) + 1000);
  const signature = await signCapabilitySshApprovalV2({ approval, signingKeyPath: f.signingKey, clock: signingClock });
  await submitCapabilityToolApprovalSignatureV2TestOnly({
    queueRoot: f.queueRoot, config: f.config, requestId: request.requestId,
    approvalSha256: sha(approval), signature, clock: signingClock,
  });
  let tick = Date.parse(approval.issuedAt) + 2000;
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    clock: () => new Date(tick++),
    dependencyOverrides: { afterTransition: async (phase) => { if (phase === "decided") throw new Error("crash after signed decision"); } },
  })), /crash after signed decision/u);
  await assert.rejects(() => readFile(`${f.sshBinary}.count`, "utf8"), /ENOENT/u);
  const recovered = await processCapabilityToolQueueV2TestOnly(f.options({ clock: () => new Date(tick++) }));
  assert.equal(recovered.status, "succeeded");
  assert.equal(recovered.response.structuredContent.hostname, "tower-one");
  assert.equal(await readFile(`${f.sshBinary}.count`, "utf8"), "run\n");
});

test("SSH queue restart preserves the exact approval and expiry settles fail-closed without runtime custody", async (t) => {
  const f = await sshQueueFixture(t); if (!f) return;
  const request = sshQueueRequest(f.config);
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    dependencyOverrides: { afterTransition: async (phase) => { if (phase === "approval-proposed") throw new Error("crash after approval proposal"); } },
  })), /crash after approval proposal/u);
  await assert.rejects(() => readdir(join(f.stateRoot, "v2-runtime")), /ENOENT/u);

  const resumed = await processCapabilityToolQueueV2TestOnly(f.options());
  assert.equal(resumed.status, "awaiting-owner-signature");
  const approval = await readCapabilityToolApprovalV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(approval.nonce, "2".repeat(64));
  const expiredClock = () => new Date(Date.parse(approval.expiresAt) + 1);
  const expired = await processCapabilityToolQueueV2TestOnly(f.options({ clock: expiredClock }));
  assert.equal(expired.status, "failed-contained");
  assert.equal(expired.response.lane, "ssh-hostname");
  assert.equal(expired.response.structuredContent, null);
  await assert.rejects(() => readdir(join(f.stateRoot, "v2-runtime")), /ENOENT/u);
  await assert.rejects(() => readFile(`${f.sshBinary}.count`, "utf8"), /ENOENT/u);
});

test("caller content can never select a runtime version, pack, workspace, or policy", async (t) => {
  const f = await queueFixture(t);
  const base = queueRequest(f.config, {});
  for (const field of ["runtimeVersion", "pack", "policy", "workspaceRoot", "stateRoot", "grant", "adapter", "security"]) {
    const injected = { ...base, [field]: field === "runtimeVersion" ? "v2" : "x" };
    assert.ok(validateWorkCapabilityQueueRequestV2(injected).length > 0, `injected ${field} must be rejected`);
  }
  // A v1 request cannot be enqueued in the v2 queue (closed v2 schema) ...
  const v1Request = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcaprequest-${baseTime}-abcdef123456`, jobId: f.config.authorization.jobId, createdAt: new Date(baseTime).toISOString(),
    authorizationSha256: sha(f.config.authorization), planSha256: sha(f.config.plan), leaseSha256: sha(f.config.lease), checkpointSha256: sha(f.config.checkpoint),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: f.config.expectedPackSha256, treeSha256: digest("a") },
    tool: "write", effectClass: "workspace", arguments: { relativePath: "note.txt", content: "hi" }, dataClassification: "internal",
    limits: { maxInputBytes: 8192, maxOutputBytes: 16384, maxRuntimeMs: 5000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 262144 },
    expectedEventHeadSha256: null,
    authority: { directExecution: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Private untrusted request for one exact local capability tool call. Arguments remain job-scoped data; this request grants no execution, credential, network, external-effect, scope-expansion, or completion authority.",
  };
  assert.ok(validateWorkCapabilityToolRequest(v1Request).length === 0, "v1 request must stay valid for v1");
  assert.ok(validateWorkCapabilityQueueRequestV2(v1Request).length > 0, "v1 request must be rejected by the v2 queue schema");
  // ... and the v2 request is rejected by the v1 schema.
  const v2Request = queueRequest(f.config, {});
  const v1Validation = (await import("../scripts/lib/work-contract.mjs")).validateWorkCapabilityToolRequest(v2Request);
  assert.ok(v1Validation.length > 0, "v2 queue request must be rejected by the v1 schema");
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: v1Request }), /invalid/);
});

test("nested relative paths are rejected; only a single create-only filename is durable", async (t) => {
  const f = await queueFixture(t);
  const nested = queueRequest(f.config, { relativePath: "a/b.txt", content: "x" });
  assert.ok(validateWorkCapabilityQueueRequestV2(nested).length > 0, "nested relativePath must be rejected (single-level restriction)");
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: nested }), /invalid/);
  const traversal = queueRequest(f.config, { relativePath: "../escape.txt", content: "x" });
  assert.ok(validateWorkCapabilityQueueRequestV2(traversal).length > 0, "traversal must be rejected");
  // A single-level request creates the file directly in the trusted workspace root (no parent chain).
  const flat = queueRequest(f.config, { relativePath: "flat.txt", content: "flat" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: flat });
  await processCapabilityToolQueueV2TestOnly(f.options());
  await expectFile(f.workspace, "flat.txt", "flat");
  assert.equal(await readdir(f.workspace).then((names) => names.includes("a")), false, "no nested parent dirs may be created");
});

test("duplicate requests and content drift have one winner", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "dup.txt", content: "same" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const again = await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  assert.equal(again.status, "already-queued-disabled");
  // Same identity, different content must fail closed (no overwrite of the queued request).
  const drifted = queueRequest(f.config, { relativePath: "dup.txt", content: "different", at: Date.parse(request.createdAt) });
  drifted.requestId = request.requestId; drifted.createdAt = request.createdAt;
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: drifted }), /differs from the exact retry/);
  await processCapabilityToolQueueV2TestOnly(f.options());
  await expectFile(f.workspace, "dup.txt", "same");
  // The workspace effect is create-only: a distinct later request for the same
  // target file cannot overwrite the settled winner.
  const second = queueRequest(f.config, { relativePath: "dup.txt", content: "same", at: Date.parse(request.createdAt) + 1 });
  second.requestId = `workcaprequest-${Date.parse(second.createdAt)}-010101010101`;
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: second });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /recovery is required/);
  await expectFile(f.workspace, "dup.txt", "same");
});

test("crash after decision (authorized) recovers and completes exactly once", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "crash1.txt", content: "c1" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  let threw = false;
  try {
    await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } }));
  } catch { threw = true; }
  assert.equal(threw, true);
  await processCapabilityToolQueueV2TestOnly(f.options());
  await expectFile(f.workspace, "crash1.txt", "c1");
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "succeeded");
});

test("crash after the create-only decision stage resumes the same decision without effect replay", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "decision-stage.txt", content: "staged" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    dependencyOverrides: { afterTransition: async (phase) => { if (phase === "decided") throw new Error("crash after staged decision"); } },
  })), /crash after staged decision/u);
  await noFile(f.workspace, "decision-stage.txt");
  await processCapabilityToolQueueV2TestOnly(f.options());
  await expectFile(f.workspace, "decision-stage.txt", "staged");
  assert.equal((await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId })).status, "succeeded");
});

test("crash after durable settlement publishes the staged response on restart without another effect", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "settled-stage.txt", content: "settled" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options({
    dependencyOverrides: { afterTransition: async (phase) => { if (phase === "settled") throw new Error("crash after durable settlement"); } },
  })), /crash after durable settlement/u);
  await expectFile(f.workspace, "settled-stage.txt", "settled");
  await assert.rejects(() => readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId }), /unreadable/u);
  const recovered = await processCapabilityToolQueueV2TestOnly(f.options({ clock: () => new Date(baseTime + 3599000) }));
  assert.equal(recovered.status, "succeeded");
  await expectFile(f.workspace, "settled-stage.txt", "settled");
});

test("crash after runtime result staged recovers as completed-with-proof", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "crash2.txt", content: "c2" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  let threw = false;
  try {
    await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "runtime-result-staged") throw new Error("crash"); } } }));
  } catch { threw = true; }
  assert.equal(threw, true);
  let recoveryTick = baseTime + 3599000;
  await processCapabilityToolQueueV2TestOnly(f.options({ clock: () => new Date(recoveryTick++) }));
  await expectFile(f.workspace, "crash2.txt", "c2");
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "succeeded"); assert.ok(SHA_RE.test(response.receiptSha256));
});

test("crash during launch with no operational custody is a safe no-effect (never replayed)", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "crash3.txt", content: "c3" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  let threw = false;
  try {
    await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "launching") throw new Error("crash"); } } }));
  } catch { threw = true; }
  assert.equal(threw, true);
  await processCapabilityToolQueueV2TestOnly(f.options());
  await noFile(f.workspace, "crash3.txt");
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "failed-contained");
});

test("crash during launch with active custody but no receipt is uncertain-no-replay", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "crash4.txt", content: "c4" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  let operationId = null;
  try {
    await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "launching") throw new Error("crash"); } } }));
  } catch {}
  // Read the decision grant to learn the operation id, then plant operational
  // active custody with no receipt to simulate an effect that may have started.
  const decision = JSON.parse(await readFile(join(f.queueRoot, "active", "decision.json"), "utf8"));
  operationId = decision.grant.operation.id;
  const custodyDir = join(f.stateRoot, "v2-runtime", "custody");
  await mkdir(custodyDir, { recursive: true, mode: 0o700 });
  await writeFile(join(custodyDir, `${operationId}.json`), JSON.stringify({ schemaVersion: 1, operationId, lane: "filesystem", state: "in-progress" }));
  await processCapabilityToolQueueV2TestOnly(f.options());
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "uncertain-no-replay");
  await noFile(f.workspace, "crash4.txt");
});

test("forged receipt without custody is never converted into a completed terminal", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "forged.txt", content: "forged" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  try {
    await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "launching") throw new Error("crash"); } } }));
  } catch {}
  const decision = JSON.parse(await readFile(join(f.queueRoot, "active", "decision.json"), "utf8"));
  const operationId = decision.grant.operation.id;
  // Plant a standalone receipt whose content hash does NOT match the grant.
  const receiptsDir = join(f.stateRoot, "v2-runtime", "receipts");
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  const forged = { schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId, lane: "filesystem", status: "succeeded", outputSha256: digest("z"), outputBytes: 1, externalEffects: false, authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }, boundary: "x" };
  await writeFile(join(receiptsDir, `${operationId}.json`), JSON.stringify(forged));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /does not authenticate|invalid/);
  await noFile(f.workspace, "forged.txt");
});

test("active request content and checkpoint drift fail closed before any effect", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "drift.txt", content: "orig" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  try { await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } })); } catch {}
  // Tampering the active request content changes its request SHA, so the durable
  // custody chain can never re-authenticate it; the effect must not run.
  const reqPath = join(f.queueRoot, "active", "request.json");
  const req = JSON.parse(await readFile(reqPath, "utf8"));
  req.content = "changed";
  await writeFile(reqPath, JSON.stringify(req));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /differs from active work/);
  await noFile(f.workspace, "drift.txt");

  // Same for checkpoint/lease/job binding drift on a fresh fixture.
  const g = await queueFixture(t);
  const rg = queueRequest(g.config, { relativePath: "drift2.txt", content: "orig2" });
  await enqueueCapabilityToolRequestV2({ queueRoot: g.queueRoot, config: g.config, request: rg });
  try { await processCapabilityToolQueueV2TestOnly(g.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } })); } catch {}
  const rgPath = join(g.queueRoot, "active", "request.json");
  const rv = JSON.parse(await readFile(rgPath, "utf8"));
  rv.checkpointSha256 = digest("f");
  await writeFile(rgPath, JSON.stringify(rv));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(g.options()), /differs from active work|differs from its immutable queue configuration/);
  await noFile(g.workspace, "drift2.txt");
});

test("stale and future grants fail closed before any effect", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "clock.txt", content: "clock" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  await processCapabilityToolQueueV2TestOnly(f.options()); // completes normally first
  await expectFile(f.workspace, "clock.txt", "clock");

  // Now a fresh request whose decision grant is tampered to be stale or future.
  const f2 = await queueFixture(t);
  const r2 = queueRequest(f2.config, { relativePath: "clock2.txt", content: "clock2" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f2.queueRoot, config: f2.config, request: r2 });
  try {
    await processCapabilityToolQueueV2TestOnly(f2.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } }));
  } catch {}
  const decisionPath = join(f2.queueRoot, "active", "decision.json");
  const decision = JSON.parse(await readFile(decisionPath, "utf8"));
  decision.grant.expiresAt = new Date(Date.parse(decision.grant.issuedAt) + 1).toISOString(); // stale (already past at recovery clock)
  await writeFile(decisionPath, JSON.stringify(decision));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f2.options()), /expired/);
  await noFile(f2.workspace, "clock2.txt");

  const f3 = await queueFixture(t);
  const r3 = queueRequest(f3.config, { relativePath: "clock3.txt", content: "clock3" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f3.queueRoot, config: f3.config, request: r3 });
  try {
    await processCapabilityToolQueueV2TestOnly(f3.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } }));
  } catch {}
  const d3 = JSON.parse(await readFile(join(f3.queueRoot, "active", "decision.json"), "utf8"));
  d3.grant.issuedAt = "2026-08-10T15:00:00Z"; // future
  d3.grant.expiresAt = "2026-08-10T15:00:30Z";
  await writeFile(join(f3.queueRoot, "active", "decision.json"), JSON.stringify(d3));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f3.options()), /not yet valid/);
  await noFile(f3.workspace, "clock3.txt");
});

test("queue metadata tamper and pack/policy substitution fail closed", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "tamper.txt", content: "tamper" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const configPath = join(f.queueRoot, "config.json");
  const original = JSON.parse(await readFile(configPath, "utf8"));
  // Substituting the signed pack changes its hash, so the immutable root must reject it.
  const substituted = { ...original, pack: { ...original.pack, provenance: { ...original.pack.provenance, signerIdentity: "attacker" } }, authorization: { ...original.authorization, pack: { ...original.authorization.pack, packSha256: digest("99") } } };
  await writeFile(configPath, JSON.stringify(substituted));
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request }), /invalid/);
  await assert.rejects(() => processCapabilityToolQueueV2(f.options()), /invalid/);
  await noFile(f.workspace, "tamper.txt");
});

test("a differing config seam cannot switch lanes or widen authority", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "seam.txt", content: "seam" });
  const otherWorkspace = await privateDir(t, "pixel-v2q-otherws-");
  const otherConfig = configFor({ stateRoot: f.stateRoot, workspaceRoot: otherWorkspace });
  await assert.rejects(() => enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: otherConfig, request }), /differs from the immutable root/);
  await assert.rejects(() => processCapabilityToolQueueV2({ ...f.options(), config: otherConfig }), /differs from the immutable root/);
});

test("public production processing path fails closed while deployment is disabled", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "pub.txt", content: "pub" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const result = await processCapabilityToolQueueV2(f.options());
  assert.equal(result.status, "disabled");
  assert.equal(result.enabled, false);
  // The public production path must never execute the workspace effect.
  await noFile(f.workspace, "pub.txt");
  // The request stays queued: source availability is honest, deployment is not.
  const status = await statusCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(status.queued, 1); assert.equal(status.settled, 0);
});

test("public production path rejects caller runtime/transition injection", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "inj.txt", content: "inj" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  let executed = false;
  const result = await processCapabilityToolQueueV2({ ...f.options(), dependencyOverrides: { executeRuntime: async () => { executed = true; throw new Error("must not run"); }, afterTransition: async () => { executed = true; } } });
  assert.equal(result.status, "disabled");
  assert.equal(executed, false, "caller-injected runtime must never run on the public path");
  await noFile(f.workspace, "inj.txt");
});

test("static: public processing path has no dependencyOverrides/runtime seam", async () => {
  const src = await readFile(new URL("../deploy/work-controller/capability-tool-queue-v2.mjs", import.meta.url), "utf8");
  const match = src.match(/export async function processCapabilityToolQueueV2\(([^)]*)\)/);
  assert.ok(match, "processCapabilityToolQueueV2 export not found");
  assert.match(match[1], /queueRoot/, "public processing path must accept queueRoot");
  assert.doesNotMatch(match[1], /dependencyOverrides|executeRuntime|afterTransition/, "public processing path must not accept a caller runtime/transition seam");
});

test("static: production modules never import the v2 queue test-support runner", async () => {
  const root = new URL("../deploy/work-controller/", import.meta.url);
  const files = (await readdir(root, { recursive: true })).filter((name) => name.endsWith(".mjs") && !name.endsWith(".test-support.mjs"));
  assert.ok(files.length > 0, "expected production modules to scan");
  for (const file of files) {
    const content = await readFile(new URL(file, root), "utf8");
    assert.doesNotMatch(content, /capability-tool-queue-v2\.test-support\.mjs/, `${file} must not import the v2 queue test-support runner`);
  }
});

test("static: production module does not export the injected core processing seam", async () => {
  const src = await readFile(new URL("../deploy/work-controller/capability-tool-queue-v2.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(src, /processCapabilityToolQueueV2Core/, "the injected executor must never be exported (or reachable) from the production module");
  // The production surface must expose only the closed processing API, never the
  // injected-executor seam or a test-only runner.
  assert.match(src, /export async function processCapabilityToolQueueV2\(/, "production module must export the closed processing API");
  assert.doesNotMatch(src, /dependencyOverrides|executeRuntime|afterTransition/, "production module must not accept a caller runtime/transition seam");
  const productionModule = await import("../deploy/work-controller/capability-tool-queue-v2.mjs");
  assert.equal(
    Object.prototype.hasOwnProperty.call(productionModule, "processCapabilityToolQueueV2ProductionClosed"),
    false,
    "production module must not export the closed internal production function as a public export",
  );
});

test("static: the internal v2 queue core module is imported only by the production wrapper and test-support", async () => {
  const root = new URL("../deploy/work-controller/", import.meta.url);
  const files = (await readdir(root, { recursive: true })).filter((name) => name.endsWith(".mjs"));
  assert.ok(files.length > 0, "expected production modules to scan");
  for (const file of files) {
    if (file === "capability-tool-queue-v2.mjs" || file === "capability-tool-queue-v2.test-support.mjs") continue;
    const content = await readFile(new URL(file, root), "utf8");
    assert.doesNotMatch(content, /capability-tool-queue-v2-core\.mjs/, `${file} must not import the internal v2 queue core directly`);
  }
});

test("static: test-support reaches the injected executor only through the internal core module", async () => {
  const support = await readFile(new URL("../deploy/work-controller/capability-tool-queue-v2.test-support.mjs", import.meta.url), "utf8");
  assert.match(support, /capability-tool-queue-v2-core\.mjs/, "test-support must import the seam from the internal core module");
  assert.doesNotMatch(support, /from "\.\/capability-tool-queue-v2\.mjs"/, "test-support must not reach the seam through the production module");
});

test("static: the queue reuses the operational runtime immutable custody/receipt contract (no duplicated strings/shapes)", async () => {
  const core = await readFile(new URL("../deploy/work-controller/capability-tool-queue-v2-core.mjs", import.meta.url), "utf8");
  const runtime = await readFile(new URL("../deploy/work-controller/capability-runtime-operational-v2.mjs", import.meta.url), "utf8");
  // The queue must not re-declare the producer's boundary/operation strings.
  assert.doesNotMatch(core, /Content-free operational v2 receipt recorded only after a completed workspace effect/, "queue must not duplicate the runtime receipt boundary");
  assert.doesNotMatch(core, /Private content-free single-winner custody for one exact operational v2 workspace effect/, "queue must not duplicate the runtime custody boundary");
  assert.doesNotMatch(core, /pixel-work-capability-runtime-v2-receipt/, "queue must not duplicate the runtime receipt operation string");
  assert.doesNotMatch(core, /pixel-work-capability-runtime-v2-custody/, "queue must not duplicate the runtime custody operation string");
  // And the queue must consume the shared immutable constants/helpers from the
  // exact producer module rather than a copied shape.
  assert.match(core, /capabilityOperationalV2ReceiptOperation/, "queue must import the shared receipt contract");
  assert.match(core, /capabilityOperationalV2SettledCustodyKeys/, "queue must import the shared settled custody shape");
  assert.match(core, /operationalBindingSha256/, "queue must import the shared grant binding digest");
  // The producer must actually export the shared contract constants.
  assert.match(runtime, /export const capabilityOperationalV2ReceiptOperation/, "runtime must export the shared receipt operation");
  assert.match(runtime, /export const capabilityOperationalV2ReceiptKeys/, "runtime must export the shared receipt shape");
  assert.match(runtime, /export function operationalBindingSha256/, "runtime must export the shared binding digest");
});

test("compiler config drift fails closed", async (t) => {
  const f = await queueFixture(t);
  // A fresh queue root whose declared compiler SHA differs from the real module bytes.
  const root = join(await privateDir(t, "pixel-v2q-cdrift-"), "q");
  const wrong = configFor({ stateRoot: f.stateRoot, workspaceRoot: f.workspace, compilerSha256: digest("c") });
  await assert.rejects(() => initializeCapabilityToolQueueV2({ queueRoot: root, config: wrong }), /compiler byte identity differs/);
  // A persisted config tampered to a wrong compiler SHA must be rejected on read.
  const cfgPath = join(f.queueRoot, "config.json");
  const cfg = JSON.parse(await readFile(cfgPath, "utf8"));
  cfg.compilerSha256 = digest("c");
  await writeFile(cfgPath, JSON.stringify(cfg));
  await assert.rejects(() => processCapabilityToolQueueV2(f.options()), /compiler byte identity differs/);
  await noFile(f.workspace, "pub.txt");
});

test("invalid queue identity fails closed before custody or queue root creation", async (t) => {
  const f = await queueFixture(t);
  const root = join(await privateDir(t, "pixel-v2q-qid-"), "q");
  const wrong = { ...f.config, queueId: "workcapqueuev2-bad-queue-id" };
  await assert.rejects(() => initializeCapabilityToolQueueV2({ queueRoot: root, config: wrong }), /queue identity is invalid/);
  assert.equal(await lstat(root).then(() => true, () => false), false, "invalid queue identity must not create or accept a queue root");
});

test("compiler byte drift fails closed before any effect", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "byte.txt", content: "byte" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const original = await readFile(compilerPath);
  await writeFile(compilerPath, Buffer.concat([original, Buffer.from("\n// compiler drift\n")]));
  t.after(async () => { await writeFile(compilerPath, original); });
  try {
    await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /compiler byte identity differs/);
    await noFile(f.workspace, "byte.txt");
  } finally {
    await writeFile(compilerPath, original);
  }
});

test("torn current claim with no ready active request fails closed", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "torn.txt", content: "torn" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  // A claimant published current.json but the active request was never moved.
  await writeFile(join(f.queueRoot, "current.json"), JSON.stringify({ schemaVersion: 2, requestId: request.requestId, boundary: capabilityToolQueueV2Boundary }), { mode: 0o600 });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /torn|no ready active request/);
  await noFile(f.workspace, "torn.txt");
});

test("current claim that does not match the active request fails closed", async (t) => {
  const f = await queueFixture(t);
  const a = queueRequest(f.config, { relativePath: "mismatch-a.txt", content: "a" });
  const b = queueRequest(f.config, { relativePath: "mismatch-b.txt", content: "b" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: a });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: b });
  await writeFile(join(f.queueRoot, "current.json"), JSON.stringify({ schemaVersion: 2, requestId: a.requestId, boundary: capabilityToolQueueV2Boundary }), { mode: 0o600 });
  await mkdir(join(f.queueRoot, "active"), { recursive: true, mode: 0o700 });
  await writeFile(join(f.queueRoot, "active", "request.json"), JSON.stringify(b), { mode: 0o600 });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /does not match the ready active request/);
  await noFile(f.workspace, "mismatch-a.txt"); await noFile(f.workspace, "mismatch-b.txt");
});

test("a second concurrent claimant defers to the durable claim and settles once", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "conc.txt", content: "conc" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  // First claimant crashes after authorization, leaving a durable current claim.
  try { await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "authorized") throw new Error("crash"); } } })); } catch {}
  assert.ok(await readFile(join(f.queueRoot, "current.json"), "utf8").then(() => true, () => false), "first claimant must hold the current claim");
  const result = await processCapabilityToolQueueV2TestOnly(f.options());
  assert.equal(result.status, "succeeded");
  await expectFile(f.workspace, "conc.txt", "conc");
  const status = await statusCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(status.settled, 1);
  assert.equal(await readdir(f.workspace).then((names) => names.filter((name) => name === "conc.txt").length), 1, "exactly one durable winner");
});

test("a receipt without durable settled custody is never reported completed", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "rcpt.txt", content: "rcpt" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  try { await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "launching") throw new Error("crash"); } } })); } catch {}
  const decision = JSON.parse(await readFile(join(f.queueRoot, "active", "decision.json"), "utf8"));
  const operationId = decision.grant.operation.id;
  const now = new Date(baseTime + 200000).toISOString();
  const receiptsDir = join(f.stateRoot, "v2-runtime", "receipts");
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  const receipt = { schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId, lane: "filesystem", status: "succeeded", startedAt: now, completedAt: now, failureClass: null, outputSha256: decision.grant.contentSha256, outputBytes: 4, externalEffects: false, authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }, boundary: "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion." };
  await writeFile(join(receiptsDir, `${operationId}.json`), JSON.stringify(receipt), { mode: 0o600 });
  await processCapabilityToolQueueV2TestOnly(f.options());
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "uncertain-no-replay");
  await noFile(f.workspace, "rcpt.txt");
});

test("same-UID planted receipt and settled custody bound to a wrong grant fail closed", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "plant.txt", content: "plant" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  try { await processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { afterTransition: async (phase) => { if (phase === "launching") throw new Error("crash"); } } })); } catch {}
  const decision = JSON.parse(await readFile(join(f.queueRoot, "active", "decision.json"), "utf8"));
  const operationId = decision.grant.operation.id;
  const now = new Date(baseTime + 200000).toISOString();
  const forgedReceipt = { schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId, lane: "filesystem", status: "succeeded", startedAt: now, completedAt: now, failureClass: null, outputSha256: digest("z"), outputBytes: 1, externalEffects: false, authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }, boundary: "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion." };
  const forgedCustody = { schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-custody", operationId, lane: "filesystem", state: "settled", grantId: "wrong-grant", bindingSha256: digest("z"), claimedAt: now, receiptSha256: sha(forgedReceipt), settledAt: now, boundary: "Private content-free single-winner custody for one exact operational v2 workspace effect. It proves one claimant acquired the operation before any effect and grants no replay, future execution, credential, network, external effect, scope expansion, or completion." };
  const custodyDir = join(f.stateRoot, "v2-runtime", "custody");
  const receiptsDir = join(f.stateRoot, "v2-runtime", "receipts");
  await mkdir(custodyDir, { recursive: true, mode: 0o700 });
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  await writeFile(join(custodyDir, `${operationId}.settled.json`), JSON.stringify(forgedCustody), { mode: 0o600 });
  await writeFile(join(receiptsDir, `${operationId}.json`), JSON.stringify(forgedReceipt), { mode: 0o600 });
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /not bound to the (?:decision|expected) grant|binding differs/);
  await noFile(f.workspace, "plant.txt");
});

test("symlink, special-file, and root-replacement attacks fail closed at queue level", async (t) => {
  // A symlink planted at the target is never followed or replaced.
  const f = await queueFixture(t);
  const req = queueRequest(f.config, { relativePath: "victim.txt", content: "payload" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request: req });
  await mkdir(join(f.workspace, "real"));
  await writeFile(join(f.workspace, "real", "victim.txt"), "precious");
  await symlink(join(f.workspace, "real", "victim.txt"), join(f.workspace, "victim.txt"));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(f.options()), /already exists|no overwrite|recovery is required/);
  await expectFile(f.workspace, "real/victim.txt", "precious");

  // A directory (special file) planted at the target is never replaced.
  const g = await queueFixture(t);
  const rg = queueRequest(g.config, { relativePath: "dir.txt", content: "x" });
  await enqueueCapabilityToolRequestV2({ queueRoot: g.queueRoot, config: g.config, request: rg });
  await mkdir(join(g.workspace, "dir.txt"));
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(g.options()), /already exists|no overwrite|recovery is required/);

  // A workspace root replaced by a symlink is rejected before any effect.
  const h = await queueFixture(t);
  const rh = queueRequest(h.config, { relativePath: "esc.txt", content: "x" });
  await enqueueCapabilityToolRequestV2({ queueRoot: h.queueRoot, config: h.config, request: rh });
  const otherDir = await privateDir(t, "pixel-v2q-other-");
  await rm(h.workspace, { recursive: true, force: true });
  await symlink(otherDir, h.workspace);
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(h.options()), /recovery is required|not a real directory/);
  assert.equal(await readdir(otherDir).then((names) => names.includes("esc.txt")), false, "no escape write outside the trusted root");

  // A state root replaced by a symlink is rejected before any effect.
  const i = await queueFixture(t);
  const ri = queueRequest(i.config, { relativePath: "esc2.txt", content: "x" });
  await enqueueCapabilityToolRequestV2({ queueRoot: i.queueRoot, config: i.config, request: ri });
  const otherState = await privateDir(t, "pixel-v2q-otherstate-");
  await rm(i.stateRoot, { recursive: true, force: true });
  await symlink(otherState, i.stateRoot);
  await assert.rejects(() => processCapabilityToolQueueV2TestOnly(i.options()), /recovery is required|not a real directory/);
  await noFile(i.workspace, "esc2.txt");
});

test("deterministic simultaneous barrier: exactly one acquisition and one effect", async (t) => {
  const f = await queueFixture(t);
  const request = queueRequest(f.config, { relativePath: "barrier.txt", content: "barrier" });
  await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
  const N = 8;
  // A shared launch gate: every claimant must reach the barrier before any is
  // released, so all N attempt the single-winner atomic claim simultaneously.
  let arrived = 0; let release;
  const gate = new Promise((res) => { release = res; });
  const ready = [];
  for (let i = 0; i < N; i += 1) {
    ready.push((async () => {
      arrived += 1;
      if (arrived === N) release();
      await gate;
      return processCapabilityToolQueueV2TestOnly(f.options());
    })());
  }
  const results = await Promise.allSettled(ready);
  const succeeded = results.filter((r) => r.status === "fulfilled" && r.value?.status === "succeeded").length;
  assert.equal(succeeded, 1, "exactly one claimant may acquire and settle the single request");
  // The create-only claim/effect guarantee holds regardless of which contender
  // wins the race: exactly one durable workspace file and one settled response.
  assert.equal(await readFile(join(f.workspace, "barrier.txt"), "utf8"), "barrier");
  assert.equal(await readdir(f.workspace).then((names) => names.filter((name) => name === "barrier.txt").length), 1, "exactly one durable effect");
  const status = await statusCapabilityToolQueueV2({ queueRoot: f.queueRoot, config: f.config });
  assert.equal(status.settled, 1); assert.equal(status.queued, 0);
  assert.equal(await readFile(join(f.queueRoot, "current.json"), "utf8").then(() => true, () => false), false, "claim must be removed after settlement");
  const response = await readCapabilityToolResponseV2({ queueRoot: f.queueRoot, config: f.config, requestId: request.requestId });
  assert.equal(response.status, "succeeded");
});

test("injected claim write/fsync/read-back failure with a planted current claim is never a valid winner or effect", async (t) => {
  // The atomic claim may fail for reasons OTHER than a concurrent publish race
  // (write, fsync, permission, read-back, identity). A planted current.json must
  // never cause such a failure to be mistaken for a valid concurrent winner.
  const cases = [
    ["write", "injected write failure"],
    ["fsync", "injected fsync failure"],
    ["read-back", "injected read-back failure"],
  ];
  for (const [label, message] of cases) {
    const f = await queueFixture(t);
    const request = queueRequest(f.config, { relativePath: `inj-${label}.txt`, content: `inj-${label}` });
    await enqueueCapabilityToolRequestV2({ queueRoot: f.queueRoot, config: f.config, request });
    const injectedClaim = async (path) => {
      // Simulate the real race window: a planted current.json appears during the
      // claim, but the claim then fails for a NON-collision reason.
      await writeFile(path, JSON.stringify({ schemaVersion: 2, requestId: request.requestId, boundary: capabilityToolQueueV2Boundary }), { mode: 0o600 });
      throw new Error(message);
    };
    await assert.rejects(
      () => processCapabilityToolQueueV2TestOnly(f.options({ dependencyOverrides: { writeClaim: injectedClaim } })),
      (error) => error instanceof Error && error.message === message,
      `${label} failure must propagate, not be treated as a concurrent winner`
    );
    await noFile(f.workspace, `inj-${label}.txt`);
  }
});

test("deterministic same-UID inode swap between claim create and read-back fails closed", async (t) => {
  const dir = await privateDir(t, "pixel-v2q-inode-");
  const claimPath = join(dir, "current.json");
  const value = { schemaVersion: 2, requestId: "workcaprequest-0178636320000-abcdef123456", boundary: capabilityToolQueueV2Boundary };
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  // A same-UID actor unlinks the freshly-created claim and recreates identical
  // bytes at the same path between creation/fsync and the no-follow read-back.
  // The recreated file is a different inode, so identity validation fails closed.
  await assert.rejects(
    () => writeClaimExclusiveTestOnly(claimPath, value, async () => {
      await unlink(claimPath);
      await writeFile(claimPath, bytes, { mode: 0o600 });
    }),
    /read-back identity mismatch/
  );
  // A claim with NO swap (same inode) succeeds and proves the primitive is sound.
  const okPath = join(dir, "ok.json");
  const outcome = await writeClaimExclusiveTestOnly(okPath, value);
  assert.equal(outcome, null, "a successful claim must carry no collision outcome");
  assert.equal(await readFile(okPath, "utf8"), bytes.toString("utf8"));
});

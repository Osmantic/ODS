import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createCapabilityToolCatalog } from "../deploy/work-controller/capability-tool-catalog.mjs";
import {
  capabilityProfileServiceBoundary, cleanupCapabilityProfileService, initializeCapabilityProfileService, serveCapabilityProfileQueue,
  statusCapabilityProfileService,
} from "../deploy/work-controller/capability-profile-service.mjs";
import { createCapabilityToolRequest } from "../deploy/work-controller/capability-controller.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { enqueueCapabilityToolRequest } from "../deploy/work-controller/capability-tool-queue.mjs";
import { enqueueCapabilityToolRequestV2, readCapabilityToolResponseV2 } from "../deploy/work-controller/capability-tool-queue-v2.mjs";
import { publishResearchToolResponse } from "../deploy/work-research-broker/tool-queue.mjs";
import { capabilityWorkerCommand, deriveProfileCapabilityRuntime, ProfileCapabilityError } from "../deploy/work-runner/profile-capability.mjs";
import { dockerSupervisorInternals } from "../deploy/work-runner/docker-supervisor.mjs";
import { canonical, validateWorkCapabilityQueueRequestV2, validateWorkCapabilityToolResponse } from "../scripts/lib/work-contract.mjs";
import { buildRealCourierResponse, v2RetrievalQueueConfig } from "./test-fixtures.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const packBoundary = "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.";
const authorizationBoundary = "Exact job, consumed lease, running checkpoint, signed pack, tool, classification, resource, and watchdog envelope. It grants no call by itself; each call still requires a fresh single-use grant and continue decision.";
const responseBoundary = "Private job-scoped response for one durably settled local capability request. Structured content is untrusted data and grants no replay, future execution, credential, network, external-effect, scope-expansion, or completion authority.";
const v2RequestBoundary = "Private untrusted durable v2 queue request for one exact controller-admitted operation. The caller may name only a signed-pack tool and its bounded input; it cannot select a runtime version, lane, pack, policy, adapter, grant, workspace root, state root, approval, signature, credentials, network, external effect, budget, authority expansion, completion, or security mode; v2 runtime is not enabled.";
const authorizationAuthority = { grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false };
const responseAuthority = { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false };
const limits = { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 5000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 };

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
    limits: { ...limits, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, shutdownTimeoutMs: 250 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: packBoundary,
  };
}

function fixture(now = Date.now()) {
  const pack = capabilityPack(), expectedPackSha256 = capabilityPackSha256(pack), authorizedAt = new Date(now - 1000), expiresAt = new Date(now + 300000);
  const authorization = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-job-authorization-v1.schema.json", schemaVersion: 1,
    authorizationId: `workcapauth-${String(authorizedAt.getTime()).padStart(13, "0")}-abcdef123456`, jobId: "work-1786366800000-abcdef123456",
    authorizedAt: authorizedAt.toISOString(), expiresAt: expiresAt.toISOString(), planSha256: digest("1"), leaseSha256: digest("2"), consumptionSha256: digest("3"), checkpointSha256: digest("4"), controllerPolicySha256: digest("5"),
    pack: { id: pack.id, version: pack.version, packSha256: expectedPackSha256, treeSha256: pack.provenance.treeSha256 },
    tools: [{ name: "analyze", effectClass: "read-only" }], dataClassification: "internal", maxSessions: 4, grantLifetimeMs: 30000,
    limits: { ...limits }, watchdog: { maxToolCalls: 4, maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { ...authorizationAuthority }, boundary: authorizationBoundary,
  };
  const catalog = createCapabilityToolCatalog({ authorization, pack, expectedPackSha256, now: new Date(now) });
  return { authorization, catalog, pack, expectedPackSha256 };
}

async function roots(t, value) {
  const parent = await mkdtemp(join(tmpdir(), "pixel-capability-profile-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const serviceRoot = join(parent, "profile");
  await initializeCapabilityProfileService({ serviceRoot, ...value });
  return { serviceRoot, worker: join(serviceRoot, "worker"), controller: join(serviceRoot, "controller") };
}

function response(request) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-response-v1.schema.json", schemaVersion: 1,
    requestId: request.requestId, jobId: request.jobId, createdAt: new Date(Date.parse(request.createdAt) + 1).toISOString(), authorizationSha256: request.authorizationSha256, checkpointSha256: request.checkpointSha256,
    status: "stopped", runtimeReceiptSha256: null, eventRecordSha256: null, dataClassification: request.dataClassification,
    structuredContent: null, contentSha256: null, failureCode: "watchdog-stopped", terminalState: "no-progress", reason: "repeated-equivalent-call",
    authority: { ...responseAuthority }, boundary: responseBoundary,
  };
  assert.deepEqual(validateWorkCapabilityToolResponse(value), []);
  return value;
}

async function waitFor(path) {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    if (await readFile(path, "utf8").then(() => true, () => false)) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("capability profile service did not publish the expected response");
}

function v2RetrievalRequest(config) {
  const createdAt = new Date("2026-08-10T13:03:05Z");
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-queue-request-v2.schema.json", schemaVersion: 2,
    requestId: `workcaprequest-${createdAt.getTime()}-777777777777`, jobId: config.authorization.jobId, createdAt: createdAt.toISOString(),
    authorizationSha256: sha(config.authorization), planSha256: sha(config.plan), leaseSha256: sha(config.lease), checkpointSha256: sha(config.checkpoint), packSha256: config.expectedPackSha256,
    tool: "research", query: "profile service production retrieval", sourceTypes: ["web"], domains: [], maxResults: 3, maxSourcesToFetch: 2, maxSourceBytes: 16384,
    dataClassification: config.plan.dataClassification,
    authority: { directExecution: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: v2RequestBoundary,
  };
  assert.deepEqual(validateWorkCapabilityQueueRequestV2(value), []);
  return value;
}

async function respondToCourier(requestDirectory, responseDirectory) {
  for (let attempt = 0; attempt < 1000; attempt += 1) {
    const name = (await readdir(requestDirectory)).find((entry) => /^req-researchtool-[0-9]{13}-[a-f0-9]{32}\.json$/u.test(entry));
    if (name) {
      const request = JSON.parse(await readFile(join(requestDirectory, name), "utf8"));
      await publishResearchToolResponse(responseDirectory, buildRealCourierResponse(request));
      return request;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("profile v2 service did not publish a Courier request");
}

test("profile service initialization is exact, idempotent, content-free, and removable", async (t) => {
  const value = fixture(), state = await roots(t, value);
  const second = await initializeCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value });
  assert.deepEqual(second, {
    schemaVersion: 1, operation: "pixel-work-capability-profile-initialize", status: "initialized-disabled",
    authorizationSha256: sha(value.authorization), catalogSha256: sha(value.catalog), tools: value.catalog.tools.length,
    enabled: false,
    authority: { grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: capabilityProfileServiceBoundary,
  });
  assert.equal(JSON.stringify(second).includes("Deterministic credential-free"), false);
  const status = await statusCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value });
  assert.deepEqual(status, {
    schemaVersion: 1, operation: "pixel-work-capability-profile-status", status: "idle-disabled",
    authorizationSha256: sha(value.authorization), catalogSha256: sha(value.catalog), workerRequests: 0, workerResponses: 0,
    queued: 0, settled: 0, events: 0, active: false, terminal: false, enabled: false,
    authority: { grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: capabilityProfileServiceBoundary,
  });
  const cleaned = await cleanupCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value });
  assert.deepEqual(cleaned, {
    schemaVersion: 1, operation: "pixel-work-capability-profile-cleanup", status: "removed",
    authorizationSha256: sha(value.authorization), catalogSha256: sha(value.catalog), settled: 0, events: 0,
    terminal: false, contentRemoved: true, enabled: false,
    authority: { grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: capabilityProfileServiceBoundary,
  });
  assert.equal(await readFile(state.serviceRoot, "utf8").then(() => true, () => false), false);
});

test("profile service republishes an exact durable response and refuses premature cleanup", async (t) => {
  const value = fixture(), state = await roots(t, value);
  const request = createCapabilityToolRequest({ authorization: value.authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "private bridge canary" }, now: new Date(Date.now() + 1), suffix: "000000000021" });
  const name = `${request.requestId}.json`, workerRequest = join(state.worker, "requests", name), workerResponse = join(state.worker, "responses", name);
  await writeFile(workerRequest, `${JSON.stringify(request)}\n`, { mode: 0o600 });
  const durableResponse = response(request);
  await writeFile(join(state.controller, "responses", name), `${JSON.stringify(durableResponse)}\n`, { mode: 0o600 });
  await mkdir(join(state.controller, "history", request.requestId), { mode: 0o700 });
  await writeFile(join(state.controller, "terminal.json"), `${JSON.stringify({ schemaVersion: 1, requestId: request.requestId, authorizationSha256: sha(value.authorization), responseSha256: sha(durableResponse), terminalState: durableResponse.terminalState, reason: durableResponse.reason, boundary: "Content-free append-only custody for one private capability request. It records no arguments or result content and grants no replay, execution, credential, network, external-effect, scope-expansion, or completion authority." })}\n`, { mode: 0o600 });
  await assert.rejects(cleanupCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value }), /discard active or unsettled/);

  const controller = new AbortController();
  const serving = serveCapabilityProfileQueue({ serviceRoot: state.serviceRoot, ...value, requestContext: {}, runtime: {}, signal: controller.signal, pollMilliseconds: 2 });
  await waitFor(workerResponse);
  assert.deepEqual(JSON.parse(await readFile(workerResponse, "utf8")), durableResponse);
  controller.abort();
  const receipt = await serving;
  assert.deepEqual({ status: receipt.status, settled: receipt.settled, events: receipt.events, terminal: receipt.terminal }, { status: "stopped-disabled", settled: 1, events: 0, terminal: true });
  assert.equal(JSON.stringify(receipt).includes("private bridge canary"), false);
  const cleaned = await cleanupCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value });
  assert.equal(cleaned.settled, 1); assert.equal(cleaned.contentRemoved, true);
});

test("durable queue enqueue is idempotent only for the exact same request", async (t) => {
  const value = fixture(), state = await roots(t, value);
  const request = createCapabilityToolRequest({ authorization: value.authorization, tool: "analyze", effectClass: "read-only", arguments: { text: "exact retry" }, now: new Date(Date.now() + 1), suffix: "000000000022" });
  const first = await enqueueCapabilityToolRequest({ queueRoot: state.controller, authorization: value.authorization, request });
  const second = await enqueueCapabilityToolRequest({ queueRoot: state.controller, authorization: value.authorization, request });
  assert.equal(first.status, "queued-disabled"); assert.equal(second.status, "already-queued-disabled");
  const substituted = structuredClone(request); substituted.arguments.text = "substituted retry";
  await assert.rejects(enqueueCapabilityToolRequest({ queueRoot: state.controller, authorization: value.authorization, request: substituted }), /differs from the exact retry/);
});

test("profile capability runtime is bound to one consumed job and running checkpoint", () => {
  const value = fixture();
  const prepared = { plan: { jobId: value.authorization.jobId, dataClassification: "internal" }, lease: { leaseId: "worklease-1786366800000-abcdef123456" } };
  const claim = { jobId: prepared.plan.jobId, claimId: "workclaim-1786366800000-abcdef123456", status: "consumed", planSha256: digest("1"), leaseSha256: digest("2") };
  const checkpoint = { schemaVersion: 1, state: "running", sequence: 7 };
  value.authorization.consumptionSha256 = sha(claim);
  value.authorization.checkpointSha256 = sha(checkpoint);
  value.catalog = createCapabilityToolCatalog({ authorization: value.authorization, pack: value.pack, expectedPackSha256: value.expectedPackSha256, now: new Date() });
  const capability = {
    ...value,
    requestContext: { plan: prepared.plan, lease: prepared.lease, consumption: claim, checkpoint },
    runtime: { dockerPath: "/usr/bin/docker" },
  };
  const runRoot = `/var/lib/pixel-work/runs/${claim.claimId}`;
  const runtime = deriveProfileCapabilityRuntime(prepared, claim, runRoot, capability);
  assert.equal(runtime.serviceRoot, `${runRoot}/capability`);
  assert.deepEqual(runtime.exposedTools, value.catalog.tools.map((tool) => tool.exposedName));
  const command = capabilityWorkerCommand(runtime);
  assert.equal(command.mountArgs.length, 6);
  assert.deepEqual(command.extensionArgs, ["--trusted-extension", "/opt/pixel/deploy/work-runner/capability-tool.mjs"]);

  const changedCheckpoint = structuredClone(capability); changedCheckpoint.requestContext.checkpoint.sequence += 1;
  assert.throws(() => deriveProfileCapabilityRuntime(prepared, claim, runRoot, changedCheckpoint), ProfileCapabilityError);
  const changedClaim = structuredClone(claim); changedClaim.claimId = "workclaim-1786366800000-000000000099";
  assert.throws(() => deriveProfileCapabilityRuntime(prepared, changedClaim, runRoot, capability), ProfileCapabilityError);
  const changedPath = structuredClone(runtime); changedPath.responseDirectory = `${runtime.workerRoot}/requests`;
  assert.throws(() => capabilityWorkerCommand(changedPath), ProfileCapabilityError);
});

function stoppedReceipt(value) {
  return {
    schemaVersion: 1, operation: "pixel-work-capability-profile-service", status: "stopped-disabled",
    authorizationSha256: sha(value.authorization), catalogSha256: sha(value.catalog), settled: 1, events: 3,
    terminal: false, enabled: false,
    authority: { grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: capabilityProfileServiceBoundary,
  };
}

test("supervisor keeps the capability service alive for the worker and records only bounded lifecycle evidence", async () => {
  const value = fixture(), receipt = stoppedReceipt(value), observed = [];
  const boundary = {
    capability: { ...value, requestContext: {}, runtime: {} },
    runtime: { capability: { catalogSha256: sha(value.catalog) } },
    claim: { jobId: value.authorization.jobId }, executor: async () => assert.fail("cleanup was not expected"),
  };
  const result = await dockerSupervisorInternals.runRpcWithCapability(boundary, {
    capabilityService: async ({ signal }) => {
      observed.push(signal.aborted);
      await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
      observed.push(signal.aborted);
      return receipt;
    },
  }, "builder-worker", async () => ({ text: "worker result" }));
  assert.deepEqual(result, { execution: { text: "worker result" }, capabilityService: receipt });
  assert.deepEqual(observed, [false, true]);
  assert.equal(JSON.stringify(result.capabilityService).includes("worker result"), false);

  const hostile = structuredClone(receipt); hostile.authority.grantsExecution = true;
  await assert.rejects(dockerSupervisorInternals.runRpcWithCapability(boundary, {
    capabilityService: async ({ signal }) => {
      await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
      return hostile;
    },
  }, "builder-worker", async () => ({ text: "discarded" })), /lifecycle receipt/);
});

test("cleanup refuses orphaned capability custody without exact recovery authorization", async (t) => {
  const runRoot = await mkdtemp(join(tmpdir(), "pixel-orphan-capability-"));
  t.after(() => rm(runRoot, { recursive: true, force: true }));
  await mkdir(join(runRoot, "capability"), { mode: 0o700 });
  await assert.rejects(dockerSupervisorInternals.cleanupBoundary({
    profile: "scout", capability: null, runtime: { runRoot }, claim: {}, executor: async () => assert.fail("cleanup must stop before Docker changes"),
  }), /capability custody exists without its exact recovery authorization/);
});

test("orphan v2 custody is refused until the exact profile marker binds it", async (t) => {
  const value = fixture(), state = await roots(t, value);
  const v2 = join(state.controller, "v2");
  await mkdir(v2, { mode: 0o700 });
  await assert.rejects(statusCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value }), /v2 custody presence differs from the exact profile marker/);
  await assert.rejects(cleanupCapabilityProfileService({ serviceRoot: state.serviceRoot, ...value }), /v2 custody presence differs from the exact profile marker/);
  assert.equal((await lstat(state.serviceRoot)).isDirectory(), true);
  assert.equal((await lstat(v2)).isDirectory(), true);
});

test("v2-bound profile initialization is physical, idempotent, status-visible, and cleanup-safe", async (t) => {
  const value = fixture();
  const parent = await mkdtemp(join(tmpdir(), "pixel-v2-profile-bound-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const serviceRoot = join(parent, "profile"), stateRoot = join(parent, "state"), workspaceRoot = join(parent, "workspace");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(workspaceRoot, { mode: 0o700 });
  const v2Config = v2RetrievalQueueConfig({ stateRoot, workspaceRoot });
  const binding = { present: true, queueId: v2Config.queueId, configSha256: sha(v2Config) };

  const first = await initializeCapabilityProfileService({ serviceRoot, ...value, v2Config });
  assert.deepEqual(first.v2, binding);
  const markerPath = join(serviceRoot, "profile.json"), v2Root = join(serviceRoot, "controller", "v2");
  assert.deepEqual(JSON.parse(await readFile(markerPath, "utf8")).v2, binding);
  assert.deepEqual((await readdir(v2Root)).sort(), ["active", "config.json", "history", "requests", "responses"]);
  assert.deepEqual(JSON.parse(await readFile(join(v2Root, "config.json"), "utf8")), v2Config);

  const second = await initializeCapabilityProfileService({ serviceRoot, ...value, v2Config });
  assert.deepEqual(second, first);
  const status = await statusCapabilityProfileService({ serviceRoot, ...value, v2Config });
  assert.deepEqual({ status: status.v2.status, queued: status.v2.queued, settled: status.v2.settled, active: status.v2.active, terminal: status.v2.terminal },
    { status: "idle-disabled", queued: 0, settled: 0, active: false, terminal: false });

  await assert.rejects(cleanupCapabilityProfileService({ serviceRoot, ...value, v2Config }),
    /v2-bound capability profile cleanup requires a response acknowledgement contract/);
  assert.equal((await lstat(serviceRoot)).isDirectory(), true); assert.equal((await lstat(v2Root)).isDirectory(), true);

  const signal = new AbortController(); signal.abort();
  await assert.rejects(serveCapabilityProfileQueue({ serviceRoot, ...value, requestContext: {}, runtime: {}, signal: signal.signal }),
    /v2 custody presence differs from the exact profile marker/);
});

test("v2 profile binding forbids in-place upgrade and immutable config substitution", async (t) => {
  const value = fixture();
  const parent = await mkdtemp(join(tmpdir(), "pixel-v2-profile-drift-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const stateRoot = join(parent, "state"), workspaceRoot = join(parent, "workspace");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(workspaceRoot, { mode: 0o700 });
  const v2Config = v2RetrievalQueueConfig({ stateRoot, workspaceRoot });

  const legacyRoot = join(parent, "legacy");
  await initializeCapabilityProfileService({ serviceRoot: legacyRoot, ...value });
  await assert.rejects(initializeCapabilityProfileService({ serviceRoot: legacyRoot, ...value, v2Config }),
    /v2 custody presence differs from the exact profile marker/);
  assert.equal(await lstat(join(legacyRoot, "controller", "v2")).then(() => true, () => false), false);

  const boundRoot = join(parent, "bound");
  await initializeCapabilityProfileService({ serviceRoot: boundRoot, ...value, v2Config });
  const markerPath = join(boundRoot, "profile.json"), configPath = join(boundRoot, "controller", "v2", "config.json");
  const markerBefore = await readFile(markerPath, "utf8"), configBefore = await readFile(configPath, "utf8");
  const substituted = v2RetrievalQueueConfig({ stateRoot, workspaceRoot, queueId: "workcapqueuev2-1786366800000-000000000099" });
  await assert.rejects(initializeCapabilityProfileService({ serviceRoot: boundRoot, ...value, v2Config: substituted }),
    /another immutable configuration|marker differs from exact profile/);
  assert.equal(await readFile(markerPath, "utf8"), markerBefore); assert.equal(await readFile(configPath, "utf8"), configBefore);
});

test("profile service executes one enabled v2 public retrieval through the real production queue", async (t) => {
  let now = Date.parse("2026-08-10T13:03:05Z");
  t.mock.method(Date, "now", () => now++);
  const value = fixture(now);
  const parent = await mkdtemp(join(tmpdir(), "pixel-v2-profile-serve-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const serviceRoot = join(parent, "profile"), stateRoot = join(parent, "state"), workspaceRoot = join(parent, "workspace"), courierRoot = join(parent, "courier");
  const courierRequests = join(courierRoot, "requests"), courierResponses = join(courierRoot, "responses");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(workspaceRoot, { mode: 0o700 });
  await mkdir(courierRequests, { recursive: true, mode: 0o700 }); await mkdir(courierResponses, { recursive: true, mode: 0o700 });
  const v2Config = v2RetrievalQueueConfig({
    stateRoot, workspaceRoot, enabled: true,
    research: { courierQueueRoot: courierRoot, timeoutMilliseconds: 5000, pollMilliseconds: 5 },
  });
  await initializeCapabilityProfileService({ serviceRoot, ...value, v2Config });
  const request = v2RetrievalRequest(v2Config), queueRoot = join(serviceRoot, "controller", "v2");
  assert.equal((await enqueueCapabilityToolRequestV2({ queueRoot, config: v2Config, request })).status, "queued-enabled");

  const controller = new AbortController();
  const broker = respondToCourier(courierRequests, courierResponses);
  const serving = serveCapabilityProfileQueue({ serviceRoot, ...value, v2Config, requestContext: {}, runtime: {}, signal: controller.signal, pollMilliseconds: 2 });
  const courierRequest = await broker;
  assert.equal(courierRequest.query, request.query);
  const responsePath = join(queueRoot, "responses", `${request.requestId}.json`);
  await waitFor(responsePath);
  controller.abort();
  const receipt = await serving;
  const response = await readCapabilityToolResponseV2({ queueRoot, config: v2Config, requestId: request.requestId });
  assert.equal(response.status, "succeeded"); assert.equal(response.lane, "public-retrieval");
  assert.equal(response.structuredContent.evidenceWrapper.trust, "untrusted"); assert.equal(response.structuredContent.evidenceWrapper.authority, "none");
  assert.deepEqual({ status: receipt.v2.status, queued: receipt.v2.queued, settled: receipt.v2.settled, enabled: receipt.v2.enabled },
    { status: "idle-enabled", queued: 0, settled: 1, enabled: true });
  assert.equal((await readdir(courierRequests)).filter((name) => name.startsWith("req-")).length, 0);
});

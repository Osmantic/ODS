import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createCapabilityToolCatalog } from "../deploy/work-controller/capability-tool-catalog.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { registerPixelCapabilityTools } from "../deploy/work-runner/capability-tool.mjs";
import {
  canonical, validateWorkCapabilityToolCatalog, validateWorkCapabilityToolRequest,
  validateWorkCapabilityToolResponse,
} from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const authorizationBoundary = "Exact job, consumed lease, running checkpoint, signed pack, tool, classification, resource, and watchdog envelope. It grants no call by itself; each call still requires a fresh single-use grant and continue decision.";
const packBoundary = "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.";
const responseBoundary = "Private job-scoped response for one durably settled local capability request. Structured content is untrusted data and grants no replay, future execution, credential, network, external-effect, scope-expansion, or completion authority.";
const responseAuthority = { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false };
const authorizationAuthority = { grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false };
const limits = { maxInputBytes: 4096, maxOutputBytes: 8192, maxRuntimeMs: 5000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 };

function pack() {
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
  const declaration = pack(), expectedPackSha256 = capabilityPackSha256(declaration), authorizedAt = new Date(now - 1000), expiresAt = new Date(now + 300000);
  const authorization = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-job-authorization-v1.schema.json", schemaVersion: 1,
    authorizationId: `workcapauth-${String(authorizedAt.getTime()).padStart(13, "0")}-abcdef123456`,
    jobId: "work-1786366800000-abcdef123456", authorizedAt: authorizedAt.toISOString(), expiresAt: expiresAt.toISOString(),
    planSha256: digest("1"), leaseSha256: digest("2"), consumptionSha256: digest("3"), checkpointSha256: digest("4"), controllerPolicySha256: digest("5"),
    pack: { id: declaration.id, version: declaration.version, packSha256: expectedPackSha256, treeSha256: declaration.provenance.treeSha256 },
    tools: [{ name: "analyze", effectClass: "read-only" }], dataClassification: "internal", maxSessions: 4, grantLifetimeMs: 30000,
    limits: { ...limits }, watchdog: { maxToolCalls: 4, maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { ...authorizationAuthority }, boundary: authorizationBoundary,
  };
  const catalog = createCapabilityToolCatalog({ authorization, pack: declaration, expectedPackSha256, now: new Date(now) });
  return { declaration, expectedPackSha256, authorization, catalog };
}

async function privateQueue(t, value) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-tool-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const requests = join(root, "requests"), responses = join(root, "responses"), catalogPath = join(root, `catalog-${sha(value.catalog)}.json`);
  await mkdir(requests, { mode: 0o700 }); await mkdir(responses, { mode: 0o700 });
  await writeFile(catalogPath, `${JSON.stringify(value.catalog)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") { await chmod(root, 0o700); await chmod(requests, 0o700); await chmod(responses, 0o700); await chmod(catalogPath, 0o600); }
  return { root, requests, responses, catalogPath };
}

function typeboxStub() {
  return { Type: { Unsafe: (schema) => ({ ...schema, unsafeWrapped: true }) } };
}

async function waitForRequest(directory, excluded = new Set()) {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    const name = (await readdir(directory)).find((candidate) => /^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(candidate) && !excluded.has(candidate));
    if (name) return { name, request: JSON.parse(await readFile(join(directory, name), "utf8")) };
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("extension did not publish a capability request");
}

async function respond(directory, request, { createdAt = new Date(Math.max(Date.now(), Date.parse(request.createdAt) + 1)).toISOString(), event = digest("6"), structuredContent = { length: request.arguments.text.length }, status = "succeeded" } = {}) {
  const succeeded = status === "succeeded";
  const response = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-response-v1.schema.json", schemaVersion: 1,
    requestId: request.requestId, jobId: request.jobId, createdAt,
    authorizationSha256: request.authorizationSha256, checkpointSha256: request.checkpointSha256, status,
    runtimeReceiptSha256: digest("7"), eventRecordSha256: event, dataClassification: request.dataClassification,
    structuredContent: succeeded ? structuredContent : null, contentSha256: succeeded ? sha(structuredContent) : null,
    failureCode: succeeded ? null : "runtime-failed", terminalState: null, reason: null,
    authority: { ...responseAuthority }, boundary: responseBoundary,
  };
  assert.deepEqual(validateWorkCapabilityToolResponse(response), []);
  const destination = join(directory, `${request.requestId}.json`), temporary = join(directory, `.response-${request.requestId}`);
  await writeFile(temporary, `${JSON.stringify(response)}\n`, { flag: "wx", mode: 0o600 });
  await link(temporary, destination); await unlink(temporary);
  if (process.platform !== "win32") await chmod(destination, 0o600);
  return response;
}

test("controller derives one fresh namespaced OMP catalog from exact signed authorization", () => {
  const value = fixture();
  assert.deepEqual(validateWorkCapabilityToolCatalog(value.catalog), []);
  assert.equal(value.catalog.tools[0].name, "analyze");
  assert.match(value.catalog.tools[0].exposedName, /^pixel_cap_fixture_tools_analyze_[a-f0-9]{8}$/u);
  assert.equal(value.catalog.authorizationSha256, sha(value.authorization));
  assert.equal(value.catalog.initialEventHeadSha256, null);
  assert.equal(value.catalog.authority.grantsToolCall, false);
  const substituted = structuredClone(value.declaration); substituted.tools[0].description = "substituted";
  assert.throws(() => createCapabilityToolCatalog({ authorization: value.authorization, pack: substituted, expectedPackSha256: value.expectedPackSha256, now: new Date() }), /signed binding|invalid/);
  assert.throws(() => createCapabilityToolCatalog({ ...value, pack: value.declaration, now: new Date(Date.parse(value.authorization.expiresAt)) }), /outside its authorization/);
});

test("trusted extension preserves signed schemas, serializes requests, and advances only from settled events", async (t) => {
  const value = fixture(), queue = await privateQueue(t, value), tools = [];
  const loaded = await registerPixelCapabilityTools({ typebox: typeboxStub(), registerTool(tool) { tools.push(tool); } }, { queueRoot: queue.root, pollMilliseconds: 2 });
  assert.deepEqual(loaded.registeredTools, [value.catalog.tools[0].exposedName]);
  assert.equal(tools.length, 1); assert.equal(tools[0].strict, true); assert.equal(tools[0].approval, "read");
  assert.equal(tools[0].parameters.unsafeWrapped, true); assert.deepEqual(tools[0].parameters.properties, value.declaration.tools[0].inputSchema.properties);

  const firstExecution = tools[0].execute("call-1", { text: "first" });
  const secondExecution = tools[0].execute("call-2", { text: "second" });
  const first = await waitForRequest(queue.requests);
  assert.deepEqual(validateWorkCapabilityToolRequest(first.request), []);
  assert.equal(first.request.tool, "analyze"); assert.equal(first.request.expectedEventHeadSha256, null);
  assert.equal((await readdir(queue.requests)).filter((name) => name.endsWith(".json")).length, 1, "concurrent calls must remain serialized");
  const firstEvent = digest("6"); await respond(queue.responses, first.request, { event: firstEvent });
  const firstResult = await firstExecution;
  assert.equal(firstResult.isError, undefined); assert.match(firstResult.content[0].text, /UNTRUSTED JOB-SCOPED CAPABILITY OUTPUT/);
  const second = await waitForRequest(queue.requests, new Set([first.name]));
  assert.equal(second.request.expectedEventHeadSha256, firstEvent);
  await respond(queue.responses, second.request, { event: digest("8"), status: "failed-contained" });
  const secondResult = await secondExecution;
  assert.equal(secondResult.isError, true); assert.match(secondResult.content[0].text, /failed-contained/);
  assert.deepEqual(await readdir(queue.requests), []);
});

test("trusted extension rejects tampered catalogs, arguments, outputs, and ambient I/O surfaces", async (t) => {
  const value = fixture(), queue = await privateQueue(t, value), tampered = structuredClone(value.catalog);
  tampered.tools[0].inputSchema.properties.text.maxLength = 999;
  await writeFile(queue.catalogPath, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(queue.catalogPath, 0o600);
  await assert.rejects(registerPixelCapabilityTools({ typebox: typeboxStub(), registerTool() {} }, { queueRoot: queue.root }), /schema hash differs|content-bound name/);

  await writeFile(queue.catalogPath, `${JSON.stringify(value.catalog)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(queue.catalogPath, 0o600);
  let tool; await registerPixelCapabilityTools({ typebox: typeboxStub(), registerTool(valueTool) { tool = valueTool; } }, { queueRoot: queue.root, pollMilliseconds: 2 });
  const invalid = await tool.execute("invalid", { text: "ok", extra: "escape" });
  assert.equal(invalid.isError, true); assert.match(invalid.content[0].text, /signed input schema/); assert.deepEqual(await readdir(queue.requests), []);

  const execution = tool.execute("bad-output", { text: "valid" });
  const request = await waitForRequest(queue.requests);
  await respond(queue.responses, request.request, { event: digest("9"), structuredContent: { length: "wrong" } });
  const result = await execution;
  assert.equal(result.isError, true); assert.match(result.content[0].text, /signed schema/);
  const closed = await tool.execute("after-ambiguous-response", { text: "must not queue" });
  assert.equal(closed.isError, true); assert.match(closed.content[0].text, /session budget is closed/); assert.deepEqual(await readdir(queue.requests), []);

  const source = await readFile(new URL("../deploy/work-runner/capability-tool.mjs", import.meta.url), "utf8");
  for (const forbidden of ["fetch(", "node:http", "node:https", "node:net", "child_process", "process.env", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]) assert.equal(source.includes(forbidden), false, forbidden);
  assert.match(source, /\/run\/pixel\/capability/); assert.match(source, /credentials: false/); assert.match(source, /network: false/);
});

test("trusted extension rejects a settled response outside the exact request lifetime", async (t) => {
  const value = fixture(), queue = await privateQueue(t, value);
  let tool; await registerPixelCapabilityTools({ typebox: typeboxStub(), registerTool(valueTool) { tool = valueTool; } }, { queueRoot: queue.root, pollMilliseconds: 2 });
  const execution = tool.execute("stale-response", { text: "valid" });
  const request = await waitForRequest(queue.requests);
  await respond(queue.responses, request.request, { createdAt: new Date(Date.parse(request.request.createdAt) - 1).toISOString() });
  const result = await execution;
  assert.equal(result.isError, true); assert.match(result.content[0].text, /outside the exact request lifetime/);
  const closed = await tool.execute("after-stale-response", { text: "must not queue" });
  assert.equal(closed.isError, true); assert.match(closed.content[0].text, /session budget is closed/); assert.deepEqual(await readdir(queue.requests), []);
});

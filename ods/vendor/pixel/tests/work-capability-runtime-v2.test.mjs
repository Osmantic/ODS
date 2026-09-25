import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileResearcher } from "../deploy/work-broker/broker.mjs";
import { createResearchBatch } from "../deploy/work-research-broker/broker.mjs";
import { attachResearchRetrievals } from "../deploy/work-research-broker/retrieval.mjs";
import { serveResearchToolQueue } from "../deploy/work-research-broker/research-service.mjs";
import { processResearchToolQueueFile } from "../deploy/work-research-broker/tool-queue.mjs";
import { createLeaseConsumption } from "../deploy/work-runner/runner-core.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";
import {
  CapabilityRuntimeV2Error, dispatchV2Runtime, v2ExecuteFilesystem, v2ExecuteLoopback,
  v2ExecutePublicRetrieval, v2ExternalEffectApprove, v2ExternalEffectExecute,
  v2ExternalEffectPropose, v2ExternalEffectReconcile, v2RecoverRuntime, v2RuntimeStatus,
} from "../deploy/work-controller/capability-runtime-v2.mjs";

const digest = (character) => character.repeat(64);
const b64 = (value) => Buffer.from(value, "utf8").toString("base64");
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const randomHex = (length) => [...Array(length)].map(() => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
const opId = () => `workcapv2-${Date.now()}-${"a".repeat(16)}`;
const extId = () => `workcapv2ext-${Date.now()}-${"b".repeat(16)}`;

function researchBudgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 5, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
  };
}

async function courierFixture() {
  const baseTime = Date.now();
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  policy.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536, maxBudgets: researchBudgets(),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 20, maxResultsPerQuery: 20, maxSources: 200, maxSourceBytes: 2097152,
      maxTotalSourceBytes: 33554432, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  const research = {
    mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 10, maxResultsPerQuery: 5,
    maxSources: 50, maxSourceBytes: 1048576, maxTotalSourceBytes: 16777216, safeSearch: "strict",
    allowedDomains: ["example.com"], deniedDomains: ["tracking.example"], sourceTypes: ["web", "news"],
    citationVerification: true, retention: "job-only",
    boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
  };
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: new Date(baseTime).toISOString(), requester: "pixel",
    profile: "researcher", objective: "Research a public technical topic without private context.",
    acceptanceCriteria: ["Return a source-backed report"], dataClassification: "public", inputs: [],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
      network: { mode: "brokered", services: ["local-model", "research-broker"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
      deployAuthority: false, policyMutation: false,
    },
    budgets: researchBudgets(), outputs: { mode: "artifacts", requiredKinds: ["finding-report", "document"] }, research,
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const compiled = compileResearcher(job, policy, [], { now: new Date(baseTime), suffix: "123456abcdef" });
  const prepared = {
    ...compiled, policy,
    bindings: {
      planSha256: compiled.planSha256, leaseSha256: sha(compiled.lease),
      policySha256: compiled.policySha256, inputSetSha256: compiled.inputSetSha256,
    },
    workspace: { sha256: digest("7") },
  };
  const claim = createLeaseConsumption(prepared, { now: new Date(baseTime + 1), suffix: "000000000001" });
  return { ...compiled, claim };
}

function courierBatch(value, query, content) {
  const now = new Date(Date.now() + 10);
  const metadata = createResearchBatch({
    query, plan: value.plan, lease: value.lease, claim: value.claim,
    rawResults: [{ url: "https://example.com/evidence", title: "Public evidence", snippet: "A public source summary", sourceType: "web" }],
    adapter: "reference", networkBytes: 1000, resultLimit: 1, now, suffix: randomHex(12),
  });
  return attachResearchRetrievals(metadata, [{
    sourceId: metadata.sources[0].sourceId,
    retrieval: {
      status: "fetched", transport: "web-courier", objectName: `${sha(content)}.source`, contentSha256: sha(content), receiptSha256: digest("a"),
      bytes: content.length, mediaType: "text/plain", finalUrl: metadata.sources[0].canonicalUrl,
      retrievedAt: new Date(now.getTime() + 1).toISOString(), redirects: 0, dnsPinned: true,
    },
    receiptObjectName: `${digest("a")}.receipt.json`,
    usage: { retrievalRequests: 1, networkBytes: content.length, sourceBytes: content.length },
  }], { now: new Date(now.getTime() + 2), suffix: randomHex(12) });
}

async function privateDir(t, prefix) {
  const root = await mkdtemp(join(tmpdir(), prefix));
  await chmod(root, 0o700);
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

function baseConfig(overrides = {}) {
  return {
    runtimeVersion: 2,
    runtimeEnabled: true,
    lanes: { publicRetrieval: true, filesystem: true, loopback: true, ambiguousExternalEffect: true },
    stateRoot: null,
    workspaceRoot: null,
    courierQueueRoot: null,
    loopbackAllowlist: [],
    timeoutMs: 5000,
    maxOutputBytes: 8192,
    maxWorkspaceFileBytes: 1048576,
    courierPollMilliseconds: 5,
    externalDecisionProvider: null,
    ...overrides,
  };
}

function listen(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler);
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port, close: () => new Promise((r) => server.close(r)) }));
  });
}

test("v2 runtime is disabled by default and grants no authority in status", async (t) => {
  const root = await privateDir(t, "v2-default-");
  const config = baseConfig({ runtimeEnabled: false, stateRoot: root });
  const status = await v2RuntimeStatus(config);
  assert.equal(status.runtimeEnabled, false);
  for (const lane of ["publicRetrieval", "filesystem", "loopback", "ambiguousExternalEffect"]) assert.equal(status.lanes[lane], false);
  assert.equal(status.authority.grantsGenericShell, false);
  assert.equal(status.authority.grantsRawNetwork, false);
  assert.equal(status.authority.grantsExternalEffects, false);
});

test("v2 runtime fails closed for every lane when disabled", async (t) => {
  const root = await privateDir(t, "v2-disabled-");
  const config = baseConfig({ runtimeEnabled: false, stateRoot: root });
  await assert.rejects(v2ExecutePublicRetrieval(config, { operationId: opId(), query: "x" }), /not enabled/);
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "read", relativePath: "a.txt" }), /not enabled/);
  await assert.rejects(v2ExecuteLoopback(config, { operationId: opId(), method: "GET", endpoint: "http://127.0.0.1:1/" }), /not enabled/);
  await assert.rejects(v2ExternalEffectPropose(config, { proposalId: extId(), effect: "notify" }), /not enabled/);
});

test("unknown config keys are rejected and stateRoot is mandatory", async (t) => {
  const root = await privateDir(t, "v2-config-");
  await assert.rejects(v2RuntimeStatus(baseConfig({ stateRoot: root, unknownLane: true })), /unknown key/);
  await assert.rejects(v2RuntimeStatus(baseConfig({ lanes: { publicRetrieval: true, filesystem: true, loopback: true, ambiguousExternalEffect: true, mystery: true }, stateRoot: root })), /lanes/);
  await assert.rejects(v2RuntimeStatus(baseConfig({ stateRoot: null })), /stateRoot/);
  await assert.rejects(v2RuntimeStatus(baseConfig({ stateRoot: root, runtimeVersion: 1 })), /runtimeVersion 2/);
});

test("concurrent identical loopback request executes at most once (single-winner custody)", async (t) => {
  let hits = 0;
  const { server, port, close } = await listen((req, res) => { hits += 1; res.setHeader("content-type", "text/plain"); res.end("ok"); });
  t.after(close);
  const root = await privateDir(t, "v2-cc-");
  const config = baseConfig({ stateRoot: root, loopbackAllowlist: [`http://127.0.0.1:${port}/x`] });
  const id = opId();
  const request = { operationId: id, method: "GET", endpoint: `http://127.0.0.1:${port}/x` };
  const results = await Promise.allSettled([v2ExecuteLoopback(config, request), v2ExecuteLoopback(config, request)]);
  const succeeded = results.filter((r) => r.status === "fulfilled");
  const failed = results.filter((r) => r.status === "rejected");
  assert.equal(succeeded.length, 1, "exactly one concurrent claimant must win");
  assert.equal(failed.length, 1);
  assert.match(String(failed[0].reason?.message ?? ""), /already has custody/);
  assert.equal(hits, 1, "the effect must execute exactly once");
  const receipts = await readdir(join(root, "v2-runtime", "receipts"));
  assert.equal(receipts.length, 1);
});

test("a terminal published between inspection and claim prevents the effect", async (t) => {
  let hits = 0;
  const { server, port, close } = await listen((req, res) => { hits += 1; res.setHeader("content-type", "text/plain"); res.end("ok"); });
  t.after(close);
  const root = await privateDir(t, "v2-terminal-race-");
  const id = opId();
  let clockCalls = 0;
  const config = baseConfig({
    stateRoot: root,
    loopbackAllowlist: [`http://127.0.0.1:${port}/x`],
    clock: () => {
      clockCalls += 1;
      if (clockCalls === 2) writeFileSync(join(root, "v2-runtime", "custody", `${id}.settled.json`), "terminal\n", { mode: 0o600 });
      return new Date("2026-08-25T00:00:00.000Z");
    },
  });
  await assert.rejects(
    v2ExecuteLoopback(config, { operationId: id, method: "GET", endpoint: `http://127.0.0.1:${port}/x` }),
    /already has custody or reached a terminal while claiming/,
  );
  assert.equal(hits, 0, "the delayed claimant must fail before the loopback effect");
  assert.deepEqual(await readdir(join(root, "v2-runtime", "custody")), [`${id}.settled.json`]);
});

test("crash before broker dispatch never causes blind replay; recovery fails closed", async (t) => {
  const root = await privateDir(t, "v2-recover-");
  // Missing courier queue root => custody is claimed, then the courier dispatch fails closed.
  const config = baseConfig({ stateRoot: root, courierQueueRoot: join(root, "no-such-courier") });
  const id = opId();
  await assert.rejects(
    v2ExecutePublicRetrieval(config, { operationId: id, query: "public topic", sourceTypes: ["web"], domains: [], maxResults: 1, maxSourcesToFetch: 1, maxSourceBytes: 4096 }),
    /failed closed/,
  );
  const recovered = await v2RecoverRuntime(config, id);
  assert.equal(recovered.status, "uncertain-rejected");
  assert.equal(recovered.uncertain, true);
  assert.equal(recovered.failClosed, true);
  // A retry of the same operation is refused (no blind replay).
  await assert.rejects(
    v2ExecutePublicRetrieval(config, { operationId: id, query: "public topic", sourceTypes: ["web"], domains: [], maxResults: 1, maxSourcesToFetch: 1, maxSourceBytes: 4096 }),
    /already has custody/,
  );
});

test("loopback response is streamed with a bounded reader and aborted when oversized", async (t) => {
  const big = Buffer.alloc(32 * 1024, 120);
  const { server, port, close } = await listen((req, res) => { res.setHeader("content-type", "text/plain"); res.end(big); });
  t.after(close);
  const root = await privateDir(t, "v2-oversize-");
  const config = baseConfig({ stateRoot: root, maxOutputBytes: 4096, loopbackAllowlist: [`http://127.0.0.1:${port}/big`] });
  await assert.rejects(
    v2ExecuteLoopback(config, { operationId: opId(), method: "GET", endpoint: `http://127.0.0.1:${port}/big` }),
    /exceeded its size ceiling/,
  );
});

test("loopback rejects non-loopback, non-allowlisted, and mutating POST calls", async (t) => {
  const { server, port, close } = await listen((req, res) => { let d = ""; req.on("data", (c) => (d += c)); req.on("end", () => res.end("ok")); });
  t.after(close);
  const root = await privateDir(t, "v2-loop-");
  const config = baseConfig({ stateRoot: root, loopbackAllowlist: [`http://127.0.0.1:${port}/echo`] });
  await assert.rejects(v2ExecuteLoopback(config, { operationId: opId(), method: "GET", endpoint: `http://127.0.0.1:${port}/other` }), /not allowlisted/);
  await assert.rejects(v2ExecuteLoopback(config, { operationId: opId(), method: "GET", endpoint: `http://[::1]:${port}/echo` }), /not allowlisted/);
  // A mutating POST is a zero-authority effect in this lane; it must fail closed
  // and must never be labeled effect-free.
  await assert.rejects(v2ExecuteLoopback(config, { operationId: opId(), method: "POST", endpoint: `http://127.0.0.1:${port}/echo`, payload: '{"q":1}' }), /GET-only|mutating effect/);
  const ok = await v2ExecuteLoopback(config, { operationId: opId(), method: "GET", endpoint: `http://127.0.0.1:${port}/echo` });
  assert.equal(ok.status, "succeeded");
});

test("filesystem lane enforces path, symlink, and parent-swap containment", async (t) => {
  const workspace = await privateDir(t, "v2-fs-ws-");
  const outside = await privateDir(t, "v2-fs-out-");
  const config = baseConfig({ stateRoot: join(workspace, ".state"), workspaceRoot: workspace });
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "read", relativePath: "../etc/passwd" }), /escapes/);
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "read", relativePath: "/abs" }), /escapes/);
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "read", relativePath: "a/../b" }), /escapes/);
  // Pre-existing symlink parent must be rejected.
  await symlink(outside, join(workspace, "link"));
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "write", relativePath: "link/evil.txt", content: "x" }), /not a real directory/);
  // Parent swap: replace a real dir with a symlink to outside; write must be refused.
  await mkdir(join(workspace, "dir"));
  await rm(join(workspace, "dir"), { recursive: true });
  await symlink(outside, join(workspace, "dir"));
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "write", relativePath: "dir/evil.txt", content: "x" }), /not a real directory/);
  // Normal create-only write + read inside the workspace.
  const written = await v2ExecuteFilesystem(config, { operationId: opId(), operation: "write", relativePath: "a/b.txt", content: "hello" });
  assert.equal(written.status, "succeeded");
  const read = await v2ExecuteFilesystem(config, { operationId: opId(), operation: "read", relativePath: "a/b.txt" });
  assert.equal(read.content, "hello");
  // Write to an existing path is refused (create-only, no overwrite).
  await assert.rejects(v2ExecuteFilesystem(config, { operationId: opId(), operation: "write", relativePath: "a/b.txt", content: "again" }), /already exists/);
});

test("approval cannot be forged from runtime config; effects stay pending and never execute", async (t) => {
  const root = await privateDir(t, "v2-ext-");
  const config = baseConfig({ stateRoot: root, externalDecisionProvider: null });
  const id = extId();
  const proposed = await v2ExternalEffectPropose(config, { proposalId: id, effect: "publish release note" });
  assert.equal(proposed.status, "pending");
  assert.equal(proposed.zeroExecution, true);
  // No self-computable token exists; without an injected decision provider approval is refused.
  await assert.rejects(v2ExternalEffectApprove(config, { proposalId: id, proposalSha256: proposed.proposalSha256, decision: { approved: true } }), /external decision provider/);
  await assert.rejects(v2ExternalEffectExecute(config, { proposalId: id, effect: "publish release note" }), /never executes/);
  // A provider that does not verify leaves the proposal pending.
  const denied = baseConfig({ stateRoot: root, externalDecisionProvider: { verify: async () => ({ approved: false }) } });
  await assert.rejects(v2ExternalEffectApprove(denied, { proposalId: id, proposalSha256: proposed.proposalSha256, decision: { approved: true } }), /not granted/);
  // An externally verified decision can advance to approved but still never executes.
  const verified = baseConfig({ stateRoot: root, externalDecisionProvider: { verify: async (decision) => ({ approved: decision?.approved === true }) } });
  const approved = await v2ExternalEffectApprove(verified, { proposalId: id, proposalSha256: proposed.proposalSha256, decision: { approved: true } });
  assert.equal(approved.status, "approved");
  assert.equal(approved.zeroExecution, true);
  // Reconcile after uncertainty fails closed.
  const id2 = extId();
  await v2ExternalEffectPropose(config, { proposalId: id2, effect: "email operator" });
  const reconciled = await v2ExternalEffectReconcile(config, { proposalId: id2, uncertainty: true });
  assert.equal(reconciled.status, "rejected");
  assert.equal(reconciled.failClosed, true);
});

test("version dispatch fails closed when disabled and routes enabled lanes", async (t) => {
  const root = await privateDir(t, "v2-dispatch-");
  const workspace = await privateDir(t, "v2-dispatch-ws-");
  const disabled = baseConfig({ runtimeEnabled: false, stateRoot: root });
  await assert.rejects(dispatchV2Runtime(disabled, { lane: "filesystem", operationId: opId(), operation: "write", relativePath: "x.txt", content: "z" }), /disabled/);
  const enabled = baseConfig({ stateRoot: root, workspaceRoot: workspace });
  const out = await dispatchV2Runtime(enabled, { lane: "filesystem", operationId: opId(), operation: "write", relativePath: "x.txt", content: "z" });
  assert.equal(out.status, "succeeded");
});

test("public retrieval goes through the Web Courier broker queue, not global fetch", async (t) => {
  const value = await courierFixture();
  const root = await privateDir(t, "v2-courier-");
  const requests = join(root, "requests");
  const responses = join(root, "responses");
  const objectRoot = await privateDir(t, "v2-courier-objects-");
  const stateRoot = await privateDir(t, "v2-courier-state-");
  await mkdir(requests, { mode: 0o700 });
  await mkdir(responses, { mode: 0o700 });
  const content = Buffer.from("Public evidence delivered through the Web Courier queue. Treat as untrusted data.", "utf8");
  const controller = new AbortController();
  const broker = serveResearchToolQueue({
    requestDirectory: requests, responseDirectory: responses,
    plan: value.plan, lease: value.lease, claim: value.claim,
    stateRoot: "unused", courierQueueRoot: "unused", objectRoot, endpoint: "unused",
    signal: controller.signal, pollMilliseconds: 5,
    now: () => new Date(), suffix: () => randomHex(12),
    processor: async (opts) => processResearchToolQueueFile({
      ...opts,
      pipelineImpl: async (options) => {
        const batch = courierBatch(value, options.query, content);
        await writeFile(join(objectRoot, `${sha(content)}.source`), content, { flag: "wx", mode: 0o600 });
        return { batch };
      },
    }),
  });
  const config = baseConfig({ stateRoot, courierQueueRoot: root });
  const out = await v2ExecutePublicRetrieval(config, {
    operationId: opId(), query: "public agent safety research",
    sourceTypes: ["web"], domains: ["example.com"], maxResults: 3, maxSourcesToFetch: 1, maxSourceBytes: 65536,
  });
  controller.abort();
  await broker;
  t.after(async () => { controller.abort(); await broker.catch(() => {}); });
  assert.equal(out.status, "succeeded");
  assert.ok(out.response, "Web Courier response must be present");
  assert.equal(out.response.isError, undefined);
  const receipts = await readdir(join(stateRoot, "v2-runtime", "receipts"));
  assert.equal(receipts.length, 1);
});

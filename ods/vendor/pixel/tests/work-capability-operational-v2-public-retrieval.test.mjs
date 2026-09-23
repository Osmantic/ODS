import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, rm, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileCapabilityOperationalV2PublicRetrieval } from "../deploy/work-controller/capability-controller.mjs";
import { recoverOperationalV2, operationalBindingSha256, validateOperationalReceipt, validatePostCustodyToolResult } from "../deploy/work-controller/capability-runtime-operational-v2.mjs";
import { executeOperationalV2 } from "../deploy/work-controller/capability-runtime-operational-v2.mjs";
import { validateWorkCapabilityOperationalGrantV2, validateWorkCapabilityOperationalRuntimeRequestV2, validateWorkCapabilityOperationalRuntimeResultV2, canonical } from "../scripts/lib/work-contract.mjs";
import { buildRealCourierResponse, buildResearchToolRequest, v2RetrievalPack, custody, compile } from "./test-fixtures.mjs";

// ---------------------------------------------------------------------------
// Minimal helpers
// ---------------------------------------------------------------------------

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

function digest(seed) {
  return sha(typeof seed === "string" ? seed : JSON.stringify(seed));
}

async function respondToNextCourierRequest(requestDir, responseDir, responseOverrides = {}, mutate = (response) => response) {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    const names = await readdir(requestDir);
    const name = names.find((entry) => /^req-researchtool-[0-9]{13}-[a-f0-9]{32}\.json$/u.test(entry));
    if (name) {
      const request = JSON.parse(await readFile(join(requestDir, name), "utf8"));
      const response = mutate(buildRealCourierResponse(request, responseOverrides));
      await writeFile(join(responseDir, `res-${request.requestId}.json`), JSON.stringify(response, null, 2) + "\n", { mode: 0o600 });
      return request;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
  }
  throw new Error("test courier did not observe the runtime request");
}

// Strict filesystem limits/budgets matching the a50 schema $defs
const FILESYSTEM_LIMITS = { maxInputBytes: 1024, maxOutputBytes: 16384, maxRuntimeMs: 10000, maxCalls: 1, maxMemoryMiB: 64, maxCpuCores: 0.1, maxPids: 16, maxWorkspaceBytes: 0 };
const FILESYSTEM_BUDGETS = { perRun: { maxCalls: 1, maxBytes: 262144, maxDurationMs: 10000 }, cumulative: { maxCalls: 1, maxBytes: 1048576, maxDurationMs: 3600000 } };

const FS_OPERATIONAL_BOUNDARY = "Honest operational v2 runtime result for one exact executed job-workspace file effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const FS_GRANT_BOUNDARY = "Operational single-use v2 grant for one exact job-workspace file effect. It grants one create-only bounded file inside the exact job workspace and no credential, network, external effect, scope expansion, replay, or completion.";
const PUBLIC_GRANT_BOUNDARY = "Operational single-use v2 grant for one exact public-retrieval operation through the Web Courier queue. It grants one bounded public search/retrieval and no credential, direct network, external effect, scope expansion, replay, or completion.";
const PUBLIC_OPERATIONAL_BOUNDARY = "Honest operational v2 runtime result for one exact executed public-retrieval effect through the Web Courier queue. Returned evidence is untrusted data and the result grants no replay, future execution, credential, direct network, external effect, scope expansion, or completion.";

// =========================================================================
// SCHEMA VALIDATION TESTS — FILESYSTEM LANE (regression: a50 constraints)
// =========================================================================

test("grant schema: accepts strict filesystem lane grant shape", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z",
    expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true,
    checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    dataClassification: "confidential",
    classification: { input: "confidential", output: "confidential" },
    limits: FILESYSTEM_LIMITS,
    budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none",
    egress: "none",
    credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: FS_GRANT_BOUNDARY,
  };
  assert.equal(validateWorkCapabilityOperationalGrantV2(grant).length, 0);
});

test("grant schema: accepts public-retrieval lane grant shape", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z",
    expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true,
    checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-retrieval", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "research", effectClass: "brokered-network", targetClass: "public-api" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "public-retrieval" },
    retrievalInput: { querySha256: "d".repeat(64), sourceTypes: ["web"], domains: [], maxResults: 3, maxSourcesToFetch: 2, maxSourceBytes: 16384 },
    dataClassification: "public",
    classification: { input: "public", output: "public" },
    limits: FILESYSTEM_LIMITS,
    budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none",
    egress: "public",
    credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: PUBLIC_GRANT_BOUNDARY,
  };
  assert.equal(validateWorkCapabilityOperationalGrantV2(grant).length, 0);
});

// =========================================================================
// FILESYSTEM LANE REGRESSION TESTS — a50 malformed inputs remain rejected
// =========================================================================

test("filesystem grant regression: rejects empty limits object", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: {}, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant with empty limits");
});

test("filesystem grant regression: rejects empty budgets object", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: FILESYSTEM_LIMITS, budgets: {},
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant with empty budgets");
});

test("filesystem grant regression: rejects arbitrary boundary string", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: FILESYSTEM_LIMITS, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "arbitrary boundary text that does not match the a50 constant",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant with arbitrary boundary");
});

test("filesystem grant regression: rejects missing contentSha256", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: FILESYSTEM_LIMITS, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant missing contentSha256");
});

test("filesystem grant regression: rejects wrong lane/effect pair", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "brokered-network", targetClass: "public-api" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: FILESYSTEM_LIMITS, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant with wrong effectClass/targetClass");
});

test("filesystem grant regression: rejects loose structuredContent in result", () => {
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "filesystem", status: "succeeded", enabled: true, effect: "workspace-write",
    receiptSha256: "a".repeat(64), outputSha256: "b".repeat(64), outputBytes: 5,
    structuredContent: { anything: "allowed", extraKey: true },
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: FS_OPERATIONAL_BOUNDARY,
  };
  const errors = validateWorkCapabilityOperationalRuntimeResultV2(result);
  assert.ok(errors.length > 0, "should reject filesystem result with loose structuredContent");
});

test("filesystem grant regression: rejects arbitrary trustRoot in request", () => {
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "filesystem",
    binding: { action: "write", scope: "job-workspace", relativePath: "test.txt", contentSha256: "d".repeat(64), maxFileBytes: 1024 },
    content: "hello",
    trustRoot: "arbitrary-trust-root",
  };
  const errors = validateWorkCapabilityOperationalRuntimeRequestV2(request);
  assert.ok(errors.length > 0, "should reject filesystem request with arbitrary trustRoot");
});

test("filesystem grant regression: rejects retrievalInput on filesystem lane", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-ws", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "write", effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "filesystem", action: "write", scope: "job-workspace", relativePath: "test.txt" },
    contentSha256: "d".repeat(64),
    retrievalInput: { querySha256: "e".repeat(64), sourceTypes: ["web"], domains: [], maxResults: 3, maxSourcesToFetch: 2, maxSourceBytes: 16384 },
    dataClassification: "internal", classification: { input: "internal", output: "internal" },
    limits: FILESYSTEM_LIMITS, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject filesystem grant with retrievalInput");
});

test("grant schema: rejects public-retrieval with filesystem egress", () => {
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    jobId: "work-1786366800000-abcdef123456",
    issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z",
    singleUse: true, checkpointSha256: "a".repeat(64),
    pack: { id: "fixture-retrieval", version: "1.0.0", packSha256: "b".repeat(64), treeSha256: "c".repeat(64), schemaVersion: 2, signerIdentity: "signer" },
    tool: { name: "research", effectClass: "brokered-network", targetClass: "public-api" },
    operation: { id: "workcapv2-1786366800000-abcdef1234567890", lane: "public-retrieval" },
    retrievalInput: { querySha256: "d".repeat(64), sourceTypes: ["web"], domains: [], maxResults: 3, maxSourcesToFetch: 2, maxSourceBytes: 16384 },
    dataClassification: "public", classification: { input: "public", output: "public" },
    limits: FILESYSTEM_LIMITS, budgets: FILESYSTEM_BUDGETS,
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: "workcapv2-1786366800000-abcdef1234567890",
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "test boundary",
  };
  const errors = validateWorkCapabilityOperationalGrantV2(grant);
  assert.ok(errors.length > 0, "should reject public-retrieval with none egress");
});

test("runtime request schema: accepts public-retrieval request", () => {
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "public-retrieval",
    trustRoot: "courier",
    retrievalBinding: { querySha256: "d".repeat(64), sourceTypes: ["web"], domains: [], maxResults: 3, maxSourcesToFetch: 2, maxSourceBytes: 16384 },
    query: "test query",
  };
  assert.equal(validateWorkCapabilityOperationalRuntimeRequestV2(request).length, 0);
});

test("runtime request schema: accepts filesystem request", () => {
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "filesystem",
    binding: { action: "write", scope: "job-workspace", relativePath: "test.txt", contentSha256: "d".repeat(64), maxFileBytes: 1024 },
    content: "hello",
    trustRoot: "workspace",
  };
  assert.equal(validateWorkCapabilityOperationalRuntimeRequestV2(request).length, 0);
});

test("runtime result schema: accepts public-retrieval result", () => {
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "public-retrieval", status: "succeeded", enabled: true, effect: "public-retrieval",
    receiptSha256: "a".repeat(64), outputSha256: "b".repeat(64), outputBytes: 100,
    structuredContent: {
      operationId: "workcapv2-1786366800000-abcdef1234567890", lane: "public-retrieval", status: "succeeded",
      outputSha256: "b".repeat(64), outputBytes: 100, receiptSha256: "a".repeat(64),
      evidenceWrapper: { trust: "untrusted", authority: "none", text: "evidence" },
    },
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: PUBLIC_OPERATIONAL_BOUNDARY,
  };
  assert.equal(validateWorkCapabilityOperationalRuntimeResultV2(result).length, 0);
});

test("runtime result schema: rejects unknown lane", () => {
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "unknown", status: "succeeded", enabled: true, effect: "workspace-write",
    receiptSha256: "a".repeat(64), outputSha256: "b".repeat(64), outputBytes: 100,
    structuredContent: {},
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: FS_OPERATIONAL_BOUNDARY,
  };
  const errors = validateWorkCapabilityOperationalRuntimeResultV2(result);
  assert.ok(errors.length > 0, "should reject unknown lane");
});

test("runtime result schema: rejects public-retrieval evidence wrapper with extra keys", () => {
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "public-retrieval", status: "succeeded", enabled: true, effect: "public-retrieval",
    receiptSha256: "a".repeat(64), outputSha256: "b".repeat(64), outputBytes: 100,
    structuredContent: {
      operationId: "workcapv2-1786366800000-abcdef1234567890", lane: "public-retrieval", status: "succeeded",
      outputSha256: "b".repeat(64), outputBytes: 100, receiptSha256: "a".repeat(64),
      evidenceWrapper: { trust: "untrusted", authority: "none", text: "evidence", extraKey: "bad" },
    },
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: PUBLIC_OPERATIONAL_BOUNDARY,
  };
  const errors = validateWorkCapabilityOperationalRuntimeResultV2(result);
  assert.ok(errors.length > 0, "should reject evidence wrapper with extra keys");
});

test("runtime result schema: rejects empty evidence wrapper text", () => {
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2,
    grantId: "workcapgrant-1786366800000-abcdef123456",
    operationId: "workcapv2-1786366800000-abcdef1234567890",
    lane: "public-retrieval", status: "succeeded", enabled: true, effect: "public-retrieval",
    receiptSha256: "a".repeat(64), outputSha256: "b".repeat(64), outputBytes: 100,
    structuredContent: {
      operationId: "workcapv2-1786366800000-abcdef1234567890", lane: "public-retrieval", status: "succeeded",
      outputSha256: "b".repeat(64), outputBytes: 100, receiptSha256: "a".repeat(64),
      evidenceWrapper: { trust: "untrusted", authority: "none", text: "" },
    },
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: PUBLIC_OPERATIONAL_BOUNDARY,
  };
  const errors = validateWorkCapabilityOperationalRuntimeResultV2(result);
  assert.ok(errors.length > 0, "should reject empty evidence wrapper text");
});

// =========================================================================
// CONTROLLER COMPILER TESTS (using shared fixture)
// =========================================================================

test("public retrieval compiler: compiles a valid grant", () => {
  const { grant, runtimeRequest } = compile();
  assert.equal(grant.schemaVersion, 2);
  assert.equal(grant.operation.lane, "public-retrieval");
  assert.equal(grant.tool.effectClass, "brokered-network");
  assert.equal(grant.tool.targetClass, "public-api");
  assert.equal(grant.egress, "public");
  assert.equal(grant.dataClassification, "public");
  assert.ok(grant.retrievalInput);
  assert.equal(grant.approvalMode, "none");
  assert.equal(grant.credentialRefs.length, 0);
  assert.equal(grant.authority.grantsToolCall, true);
  assert.equal(grant.authority.grantsCompletion, false);
  assert.equal(runtimeRequest.lane, "public-retrieval");
  assert.equal(runtimeRequest.trustRoot, "courier");
});

test("public retrieval compiler: rejects wrong effect class", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], effectClass: "workspace", binding: null }] }) }), /workspace tool requires a disposable workspace adapter/);
});

test("public retrieval compiler: rejects direct network mode", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ security: { ...v2RetrievalPack().security, networkMode: "public" } }) }), /must be brokered/);
});

test("public retrieval compiler: rejects missing web-courier", () => {
  const base = v2RetrievalPack();
  const binding = { ...base.tools[0].binding, scope: { mode: "public", endpoints: ["https://other.example/search"], destinations: [] } };
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...base.tools[0], binding }] }) }), /Web Courier broker endpoint/);
});

test("public retrieval compiler: rejects extra network destinations", () => {
  const base = v2RetrievalPack();
  const binding = { ...base.tools[0].binding, scope: { mode: "public", endpoints: ["https://extra.example/search", "https://web-courier.local:8080/search"], destinations: [] } };
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...base.tools[0], binding }] }) }), /Web Courier broker endpoint/);
});

test("public retrieval compiler: rejects empty network destinations", () => {
  const base = v2RetrievalPack();
  const binding = { ...base.tools[0].binding, scope: { mode: "public", endpoints: [], destinations: [] } };
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...base.tools[0], binding }] }) }), /value must match exactly one allowed schema/);
});

test("public retrieval compiler: rejects credentials", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ security: { ...v2RetrievalPack().security, credentialRefs: ["t"] } }) }), /credential/);
});

test("public retrieval compiler: rejects external effects", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ security: { ...v2RetrievalPack().security, externalEffects: true } }) }), /externalEffects/);
});

test("public retrieval compiler: rejects non-public classification", () => {
  const base = custody();
  assert.throws(() => compile({ plan: { ...base.plan, dataClassification: "internal" } }), /public-only/);
});

test("public retrieval compiler: rejects invalid domain", () => {
  assert.throws(() => compile({ domains: ["INVALID"] }), /public DNS/);
});

test("public retrieval compiler: rejects oversized budgets", () => {
  assert.throws(() => compile({ maxResults: 21 }), /maxResults/);
  assert.throws(() => compile({ maxSourcesToFetch: 6 }), /maxSourcesToFetch/);
});

test("public retrieval compiler: rejects short query", () => {
  assert.throws(() => compile({ query: "ab" }), /query is not bounded/);
});

test("public retrieval compiler: rejects non-canonical query", () => {
  assert.throws(() => compile({ query: "  test  " }), /canonical/);
});

test("public retrieval compiler: sourceTypes must be canonical order", () => {
  assert.throws(() => compile({ sourceTypes: ["academic", "web"] }), /canonical order/);
});

test("public retrieval compiler: domains must be sorted", () => {
  assert.throws(() => compile({ domains: ["z.com", "a.com"] }), /sorted/);
});

test("public retrieval compiler: maxSourcesToFetch cannot exceed maxResults", () => {
  assert.throws(() => compile({ maxResults: 2, maxSourcesToFetch: 3 }), /maxSourcesToFetch/);
});

test("public retrieval compiler: rejects expired lease", () => {
  const base = custody();
  assert.throws(() => compile({ lease: { ...base.lease, expiresAt: "2026-08-10T13:02:30Z" } }), /lifetime/);
});

test("public retrieval compiler: binds pack tool targetClass", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, targetClass: "private-lan" } }] }) }), /brokered-network target class must be api or public-api/);
});

test("public retrieval compiler: binds pack tool inputClassification", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, inputClassification: "internal" } }] }) }), /inputClassification: outside the pack accepted classifications/);
});

test("public retrieval compiler: binds pack tool outputClassification", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, outputClassification: "restricted" } }] }) }), /outputClassification: outside the pack returned classifications/);
});

// =========================================================================
// RUNTIME TESTS — REAL WEB COURIER PROTOCOL
// =========================================================================

test("public retrieval runtime: real protocol end-to-end", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-real-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const broker = respondToNextCourierRequest(requestDir, responseDir);

    const config = {
      stateRoot: tmpRoot, courierQueueRoot,
      timeoutMilliseconds: 10000, pollMilliseconds: 5,
      clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };

    const result = await executeOperationalV2({ grant, runtimeRequest, config });
    await broker;

    assert.equal(result.lane, "public-retrieval");
    assert.equal(result.status, "succeeded");
    assert.equal(result.effect, "public-retrieval");
    assert.ok(result.receiptSha256);
    assert.ok(result.outputSha256);
    assert.ok(result.outputBytes > 0);
    assert.ok(/^[a-f0-9]{64}$/.test(result.receiptSha256));
    assert.ok(/^[a-f0-9]{64}$/.test(result.outputSha256));

    const sc = result.structuredContent;
    assert.ok(sc.evidenceWrapper);
    assert.equal(sc.evidenceWrapper.trust, "untrusted");
    assert.equal(sc.evidenceWrapper.authority, "none");
    assert.ok(typeof sc.evidenceWrapper.text === "string" && sc.evidenceWrapper.text.length > 0);
    assert.equal(sc.outputSha256, result.outputSha256);
    assert.equal(sc.outputBytes, result.outputBytes);

    const wrapperJson = JSON.stringify(sc.evidenceWrapper);
    const expectedSha = createHash("sha256").update(wrapperJson).digest("hex");
    assert.equal(result.outputSha256, expectedSha);
    const expectedBytes = Buffer.byteLength(wrapperJson, "utf8");
    assert.equal(result.outputBytes, expectedBytes);

    const resultErrors = validateWorkCapabilityOperationalRuntimeResultV2(result);
    assert.equal(resultErrors.length, 0, `result schema errors: ${resultErrors.join(", ")}`);
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

test("public retrieval runtime: rejects query drift", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-"));
  try {
    const { grant, runtimeRequest } = compile();
    const badRequest = { ...runtimeRequest, query: "different query" };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest: badRequest, config: { stateRoot: tmpRoot, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) } }),
      /differs from its grant binding/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("public retrieval runtime: rejects missing courierQueueRoot", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-"));
  try {
    const { grant, runtimeRequest } = compile();
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest, config: { stateRoot: tmpRoot, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) } }),
      /courierQueueRoot/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("public retrieval runtime: rejects wrong lane in request", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-"));
  try {
    const { grant, runtimeRequest } = compile();
    const badRequest = { ...runtimeRequest, lane: "filesystem" };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest: badRequest, config: { stateRoot: tmpRoot, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) } }),
      /missing required property binding/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("public retrieval runtime: rejects sourceTypes drift", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-"));
  try {
    const { grant, runtimeRequest } = compile();
    const badRequest = { ...runtimeRequest, retrievalBinding: { ...runtimeRequest.retrievalBinding, sourceTypes: ["academic"] } };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest: badRequest, config: { stateRoot: tmpRoot, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) } }),
      /sourceTypes differ/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

// =========================================================================
// ADVERSARIAL RUNTIME TESTS
// =========================================================================

test("adversarial: rejects timeout exceeding grant ceiling", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-adv-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    await mkdir(join(courierQueueRoot, "requests"), { mode: 0o700, recursive: true });
    await mkdir(join(courierQueueRoot, "responses"), { mode: 0o700, recursive: true });

    await assert.rejects(
      executeOperationalV2({
        grant, runtimeRequest,
        config: { stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 180000, pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) },
      }),
      /timeout exceeds grant runtime ceiling/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("adversarial: accepts timeout at grant ceiling", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-adv-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const broker = respondToNextCourierRequest(requestDir, responseDir);

    const result = await executeOperationalV2({
      grant, runtimeRequest,
      config: { stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 120000, pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) },
    });
    await broker;
    assert.equal(result.status, "succeeded");
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("adversarial: wrong real Courier response binding (bad requestId)", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-adv-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const broker = respondToNextCourierRequest(
      requestDir,
      responseDir,
      {},
      (response) => ({ ...response, requestId: "researchtool-0000000000000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
    );

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 10000,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest, config }),
      /returned an error; custody uncertain-rejected/
    );
    await broker;
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("adversarial: broker failure (no response file) marks uncertain-rejected", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-adv-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const toolRequest = buildResearchToolRequest(runtimeRequest.query);
    writeFileSync(join(requestDir, `req-${toolRequest.requestId}.json`), JSON.stringify(toolRequest, null, 2) + "\n", { mode: 0o600 });

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 200,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest, config }),
      /returned an error; custody uncertain-rejected/
    );
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

test("adversarial: oversized evidence rejected by budget", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-adv-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const oversizedContent = "x".repeat(200001);
    const broker = respondToNextCourierRequest(requestDir, responseDir, { sourceContent: oversizedContent, skipValidation: true });

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 10000,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };
    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest, config }),
      /returned an error; custody uncertain-rejected/
    );
    await broker;
  } finally { await rm(tmpRoot, { recursive: true, force: true }); }
});

// =========================================================================
// POST-CUSTODY TOOL RESULT VALIDATOR TESTS
// =========================================================================

test("post-custody validator: accepts valid single text block", () => {
  const toolResult = { content: [{ type: "text", text: "valid evidence" }] };
  assert.doesNotThrow(() => validatePostCustodyToolResult(toolResult));
});

test("post-custody validator: rejects missing tool result", () => {
  assert.throws(() => validatePostCustodyToolResult(null), /tool result is missing/);
});

test("post-custody validator: rejects non-object tool result", () => {
  assert.throws(() => validatePostCustodyToolResult("string"), /tool result is missing/);
});

test("post-custody validator: rejects missing content array", () => {
  assert.throws(() => validatePostCustodyToolResult({}), /no content array/);
});

test("post-custody validator: rejects extra content blocks", () => {
  const toolResult = {
    content: [
      { type: "text", text: "first block" },
      { type: "text", text: "extra block" },
    ],
  };
  assert.throws(() => validatePostCustodyToolResult(toolResult), /exactly one content block/);
});

test("post-custody validator: rejects empty content array", () => {
  const toolResult = { content: [] };
  assert.throws(() => validatePostCustodyToolResult(toolResult), /exactly one content block/);
});

test("post-custody validator: rejects non-text content block", () => {
  const toolResult = { content: [{ type: "image", data: "base64" }] };
  assert.throws(() => validatePostCustodyToolResult(toolResult), /must be text/);
});

test("post-custody validator: rejects empty text", () => {
  const toolResult = { content: [{ type: "text", text: "" }] };
  assert.throws(() => validatePostCustodyToolResult(toolResult), /non-empty text/);
});

test("post-custody validator: rejects missing text property", () => {
  const toolResult = { content: [{ type: "text" }] };
  assert.throws(() => validatePostCustodyToolResult(toolResult), /non-empty text/);
});

test("post-custody validator: rejects oversized text block", () => {
  const toolResult = { content: [{ type: "text", text: "12345" }] };
  assert.throws(() => validatePostCustodyToolResult(toolResult, 4), /exceeds its byte ceiling/);
});

// =========================================================================
// BUDGET TESTS — wrapper bytes, not raw text
// =========================================================================

test("budget: wrapper bytes checked against grant budget", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-budget-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    // The grant budget is perRun.maxBytes from the fixture pack (131072).
    // We build a response whose rendered text + wrapper overhead is within budget.
    const broker = respondToNextCourierRequest(requestDir, responseDir);

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 10000,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };
    const result = await executeOperationalV2({ grant, runtimeRequest, config });
    await broker;

    // The wrapper bytes must be within budget.
    const wrapperJson = JSON.stringify(result.structuredContent.evidenceWrapper);
    const wrapperBytes = Buffer.byteLength(wrapperJson, "utf8");
    assert.ok(wrapperBytes <= grant.budgets.perRun.maxBytes, "wrapper bytes must not exceed grant budget");
    assert.equal(result.outputBytes, wrapperBytes, "outputBytes must equal wrapper bytes");
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

// =========================================================================
// RECOVERY TESTS
// =========================================================================

test("public retrieval recovery: no custody returns absent without completion", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-rec-"));
  try {
    const { grant } = compile();
    const status = await recoverOperationalV2(
      { stateRoot: tmpRoot },
      grant.operation.id,
      undefined
    );
    assert.equal(status.status, "absent");
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

test("public retrieval binding: operationalBindingSha256 produces valid hash", () => {
  const { grant } = compile();
  const binding = operationalBindingSha256(grant);
  assert.ok(/^[a-f0-9]{64}$/.test(binding));
});

test("recovery: crash-before-receipt marks uncertain-rejected", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-crash-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    await mkdir(join(courierQueueRoot, "requests"), { mode: 0o700, recursive: true });
    await mkdir(join(courierQueueRoot, "responses"), { mode: 0o700, recursive: true });

    await assert.rejects(
      executeOperationalV2({
        grant, runtimeRequest,
        config: { stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 200, pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")) },
      }),
      /returned an error; custody uncertain-rejected/
    );

    const status = await recoverOperationalV2({ stateRoot: tmpRoot }, grant.operation.id, grant);
    assert.equal(status.status, "uncertain-rejected");
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

test("recovery: replay refusal — duplicate execution rejected", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-replay-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const broker = respondToNextCourierRequest(requestDir, responseDir);

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 10000,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };

    await executeOperationalV2({ grant, runtimeRequest, config });
    await broker;

    await assert.rejects(
      executeOperationalV2({ grant, runtimeRequest, config }),
      /already has custody/
    );
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

// =========================================================================
// EVIDENCE WRAPPER TRUST BOUNDARY TESTS
// =========================================================================

test("evidence wrapper: trust boundary is correct", async (t) => {
  const tmpRoot = await mkdtemp(join(tmpdir(), "operational-v2-pr-evidence-"));
  try {
    const { grant, runtimeRequest } = compile();
    const courierQueueRoot = join(tmpRoot, "courier");
    const requestDir = join(courierQueueRoot, "requests");
    const responseDir = join(courierQueueRoot, "responses");
    await mkdir(requestDir, { mode: 0o700, recursive: true });
    await mkdir(responseDir, { mode: 0o700, recursive: true });

    const broker = respondToNextCourierRequest(requestDir, responseDir);

    const config = {
      stateRoot: tmpRoot, courierQueueRoot, timeoutMilliseconds: 10000,
      pollMilliseconds: 5, clock: () => new Date(Date.parse("2026-08-10T13:05:00Z")),
    };

    const result = await executeOperationalV2({ grant, runtimeRequest, config });
    await broker;

    assert.equal(result.structuredContent.evidenceWrapper.trust, "untrusted");
    assert.equal(result.structuredContent.evidenceWrapper.authority, "none");

    const wrapperJson = JSON.stringify(result.structuredContent.evidenceWrapper);
    const wrapperSha = createHash("sha256").update(wrapperJson).digest("hex");
    assert.equal(wrapperSha, result.outputSha256);
    const wrapperBytes = Buffer.byteLength(wrapperJson, "utf8");
    assert.equal(wrapperBytes, result.outputBytes);
  } finally {
    await rm(tmpRoot, { recursive: true, force: true });
  }
});

// =========================================================================
// PACK/SECURITY DESTINATION ADVERSARIAL TESTS
// =========================================================================

test("adversarial: pack security binding drift — tool effectClass mismatch", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], effectClass: "workspace", binding: null }] }) }), /workspace tool requires a disposable workspace adapter/);
});

test("adversarial: pack security binding drift — binding targetClass mismatch", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, targetClass: "private-lan" } }] }) }), /brokered-network target class must be api or public-api/);
});

test("adversarial: pack security binding drift — input classification mismatch", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, inputClassification: "internal" } }] }) }), /inputClassification: outside the pack accepted classifications/);
});

test("adversarial: pack security binding drift — output classification mismatch", () => {
  assert.throws(() => compile({ pack: v2RetrievalPack({ tools: [{ ...v2RetrievalPack().tools[0], binding: { ...v2RetrievalPack().tools[0].binding, outputClassification: "restricted" } }] }) }), /outputClassification: outside the pack returned classifications/);
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdtemp, readFile, rm, stat, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, parse } from "node:path";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import {
  buildWorkCodexAuthorization, executeWorkCodexAuthorization, initializeWorkCodexStore, readWorkCodexResult,
  storeWorkCodexAuthorization, storeWorkCodexPreview, WorkCodexExecutionError,
} from "../deploy/work-codex-provider/execution.mjs";
import { canonical, validateWorkCodexAuthorization, validateWorkCodexExecutionClaim, validateWorkCodexResult } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T20:00:00Z"), hash = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const outputBoundary = "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority.";

function request(index) {
  const content = `Customer Acme North has a structural boundary at C:\\private\\acme${index}\\module.txt.`;
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-${index.toString(16).padStart(12, "0")}`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(),
    jobId: `work-${baseTime}-${(index + 2000).toString(16).padStart(12, "0")}`, checkpointSha256: hash(`checkpoint-${index}`), ownerId: "owner-one", clientId: "client-one",
    taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["customer-confidential", "structural"],
    localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: hash(`local-${index}`) },
    objective: "Review Acme North's boundary.", constraints: ["Do not expose Acme North or local paths."], acceptanceCriteria: ["Return one bounded structural recommendation."],
    sensitiveTerms: [{ kind: "customer", value: "Acme North" }], documents: [{ documentId: `architecture_${index}`, kind: "structure", language: "text", content, contentSha256: hash(content) }],
    maxOutputTokens: 1024, boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.",
  };
}

async function setup(t, index = 1) {
  const root = await mkdtemp(join(tmpdir(), `pixel-codex-execution-${index}-`)); t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true;
  const input = request(index), compiled = compileWorkCodexPreview({ request: input, policy, now: new Date(baseTime + 1000), suffix: index.toString(16).padStart(12, "0"), mappingNonce: hash(`mapping-${index}`) });
  await initializeWorkCodexStore({ root }); await storeWorkCodexPreview({ root, request: input, policy, ...compiled });
  const confirmation = { planInspected: true, dataOwnerConfirmed: true, billingAcknowledgment: "chatgpt-plan-or-credits", externalHumanAuthentication: true, authenticationEvidenceSha256: hash(`human-auth-${index}`) };
  const authorization = buildWorkCodexAuthorization({ plan: compiled.plan, confirmation, now: new Date(baseTime + 2000), expiresAt: new Date(baseTime + 10 * 60000), suffix: (index + 1000).toString(16).padStart(12, "0") });
  await storeWorkCodexAuthorization({ root, authorization, allowMockAuthorization: true });
  return { root, policy, input, ...compiled, authorization };
}

function providerOutput(value, overrides = {}) {
  const customer = value.privateMapping.entries.find((entry) => entry.original === "Acme North").placeholder;
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review",
    summary: `The ${customer} boundary needs an explicit invariant.`,
    findings: [{ severity: "medium", title: "Boundary is implicit", evidence: `DOC_001 names ${customer} without a check.`, recommendation: `Add one local check around ${customer}.` }],
    proposals: [{ targetDocumentId: "DOC_001", operation: "replace", rationale: "Make the boundary explicit.", content: `Document the ${customer} invariant.` }],
    verificationSuggestions: ["Run the local boundary test."], unresolvedRisks: ["The advice is not independently verified."], boundary: outputBoundary, ...overrides,
  };
}

function adapter(output, overrides = {}) {
  return {
    source: "mock",
    async run(input) {
      assert.ok(Object.isFrozen(input) && Object.isFrozen(input.capsule) && Object.isFrozen(input.outputSchema));
      const encoded = JSON.stringify(input); for (const forbidden of ["Acme North", "owner-one", "client-one", "architecture_1", "privateMapping", "authenticationEvidenceSha256"]) assert.equal(encoded.includes(forbidden), false);
      return { outputText: canonical(output), usage: { inputTokens: 600, outputTokens: 200 }, externalState: "not-invoked", ...overrides };
    },
  };
}

test("one exact external authorization runs only a mock capsule and safely rehydrates private output", async (t) => {
  const value = await setup(t), output = providerOutput(value), result = await executeWorkCodexAuthorization({
    root: value.root, authorizationId: value.authorization.authorizationId, adapter: adapter(output), allowMock: true,
    now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000111", resultSuffix: "000000000112",
  });
  assert.equal(result.receipt.status, "succeeded"); assert.equal(result.receipt.invocation.externalState, "not-invoked");
  assert.equal(result.receipt.output.storedPrivately, true); assert.deepEqual(validateWorkCodexResult(result.receipt), []);
  assert.match(result.output.summary, /Acme North/u); assert.equal(result.output.proposals[0].targetDocumentId, "architecture_1");
  assert.match(result.output.proposals[0].content, /Acme North/u); assert.doesNotMatch(JSON.stringify(result.receipt), /Acme North|architecture_1/u);
  assert.deepEqual(await readWorkCodexResult({ root: value.root, authorizationId: value.authorization.authorizationId }), result.receipt);
  const storedOutput = await readFile(join(value.root, "claims", value.authorization.authorizationId, "output.json"), "utf8");
  assert.match(storedOutput, /Acme North/u); assert.match(storedOutput, /PIXELWORK_CUSTOMER/u);
  if (process.platform !== "win32") {
    for (const path of [join(value.root, "plans", value.plan.planId, "bundle.json"), join(value.root, "authorizations", `${value.authorization.authorizationId}.json`), join(value.root, "claims", value.authorization.authorizationId, "output.json")]) {
      assert.equal((await stat(path)).mode & 0o077, 0);
    }
  }
});

test("authorization claim is single-winner under concurrency and replay never re-enters the adapter", async (t) => {
  const value = await setup(t, 2), output = providerOutput(value); let calls = 0;
  const slow = { source: "mock", async run() { calls += 1; await new Promise((resolve) => setTimeout(resolve, 30)); return { outputText: canonical(output), usage: { inputTokens: 600, outputTokens: 200 }, externalState: "not-invoked" }; } };
  const options = { root: value.root, authorizationId: value.authorization.authorizationId, adapter: slow, allowMock: true, now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000) };
  const attempts = await Promise.allSettled([
    executeWorkCodexAuthorization({ ...options, claimSuffix: "000000000121", resultSuffix: "000000000122" }),
    executeWorkCodexAuthorization({ ...options, claimSuffix: "000000000123", resultSuffix: "000000000124" }),
  ]);
  assert.equal(calls, 1); assert.equal(attempts.filter((entry) => entry.status === "fulfilled").length, 1); assert.equal(attempts.filter((entry) => entry.status === "rejected").length, 1);
  await assert.rejects(() => executeWorkCodexAuthorization({ ...options, claimSuffix: "000000000125", resultSuffix: "000000000126" }), /already consumed/); assert.equal(calls, 1);
});

test("hostile, widened, secret, malformed, noncanonical, and over-budget outputs fail closed", async (t) => {
  const cases = [
    (value) => canonical(providerOutput(value, { summary: "Use <PIXELWORK_CUSTOMER_999>." })),
    (value) => canonical(providerOutput(value, { summary: "Bearer abcdefghijklmnopqrstuvwxyz123456" })),
    (value) => `${canonical(providerOutput(value))}\n`,
    (value) => canonical({ ...providerOutput(value), deploy: true }),
    (value) => canonical(providerOutput(value, { taskClass: "failure-triage" })),
    (value) => { const output = providerOutput(value); output.proposals[0].targetDocumentId = "DOC_999"; return canonical(output); },
    (value) => canonical(providerOutput(value, { summary: "hidden\u200btext" })),
    (_value) => Buffer.from([0xff, 0xfe, 0xfd]),
  ];
  for (const [offset, makeOutput] of cases.entries()) {
    const value = await setup(t, 10 + offset), execution = await executeWorkCodexAuthorization({
      root: value.root, authorizationId: value.authorization.authorizationId,
      adapter: { source: "mock", async run() { return { outputText: makeOutput(value), usage: { inputTokens: 500, outputTokens: 100 }, externalState: "not-invoked" }; } },
      allowMock: true, now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: (300 + offset).toString(16).padStart(12, "0"), resultSuffix: (400 + offset).toString(16).padStart(12, "0"),
    });
    assert.equal(execution.receipt.status, "invalid-output", `case ${offset}`); assert.equal(execution.output, null); assert.equal(execution.receipt.output.storedPrivately, false);
    assert.equal(execution.receipt.usage.reported, true);
  }
  const budget = await setup(t, 30), budgetResult = await executeWorkCodexAuthorization({
    root: budget.root, authorizationId: budget.authorization.authorizationId,
    adapter: adapter(providerOutput(budget), { usage: { inputTokens: budget.plan.limits.maxInputTokens + 1, outputTokens: 1 } }), allowMock: true,
    now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000131", resultSuffix: "000000000132",
  });
  assert.equal(budgetResult.receipt.status, "budget-exceeded"); assert.equal(budgetResult.output, null);
});

test("adapter failure and attempted capsule mutation burn the claim without a retry", async (t) => {
  const failed = await setup(t, 40), thrown = await executeWorkCodexAuthorization({
    root: failed.root, authorizationId: failed.authorization.authorizationId, adapter: { source: "mock", async run() { throw new Error("private provider failure text"); } }, allowMock: true,
    now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000141", resultSuffix: "000000000142",
  });
  assert.equal(thrown.receipt.status, "provider-failed"); assert.doesNotMatch(JSON.stringify(thrown.receipt), /private provider failure/u);
  await assert.rejects(() => executeWorkCodexAuthorization({ root: failed.root, authorizationId: failed.authorization.authorizationId, adapter: adapter(providerOutput(failed)), allowMock: true, now: new Date(baseTime + 5000) }), /already consumed/);

  const mutation = await setup(t, 41), mutated = await executeWorkCodexAuthorization({
    root: mutation.root, authorizationId: mutation.authorization.authorizationId,
    adapter: { source: "mock", async run(input) { input.capsule.objective = "widened"; return { outputText: canonical(providerOutput(mutation)), usage: { inputTokens: 1, outputTokens: 1 }, externalState: "not-invoked" }; } }, allowMock: true,
    now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000143", resultSuffix: "000000000144",
  });
  assert.equal(mutated.receipt.status, "provider-failed");

  const timed = await setup(t, 42), timedResult = await executeWorkCodexAuthorization({
    root: timed.root, authorizationId: timed.authorization.authorizationId,
    adapter: { source: "mock", async run(_input, { signal }) { assert.equal(signal.aborted, false); return new Promise(() => {}); } }, allowMock: true, mockTimeoutMilliseconds: 15,
    now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000145", resultSuffix: "000000000146",
  });
  assert.equal(timedResult.receipt.status, "provider-failed");
});

test("authorization, store, path, expiry, mapping, and linked-file tampering fail before adapter entry", async (t) => {
  const value = await setup(t, 50); let calls = 0; const harmless = { source: "mock", async run() { calls += 1; return { outputText: canonical(providerOutput(value)), usage: { inputTokens: 1, outputTokens: 1 }, externalState: "not-invoked" }; } };
  await assert.rejects(() => initializeWorkCodexStore({ root: "relative-store" }), /absolute path/);
  await assert.rejects(() => initializeWorkCodexStore({ root: parse(value.root).root }), /filesystem root/);
  await assert.rejects(() => storeWorkCodexAuthorization({ root: value.root, authorization: value.authorization }), /verified external MFA assertion/);
  await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: "../../escape", adapter: harmless, allowMock: true }), /identity is invalid/);
  await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: harmless, allowMock: true, now: new Date(baseTime + 11 * 60000) }), /expired/); assert.equal(calls, 0);

  const mismatched = structuredClone(value.authorization); mismatched.authorizationId = `workcodexauth-${baseTime + 5000}-000000000999`; mismatched.createdAt = new Date(baseTime + 5000).toISOString(); mismatched.planSha256 = "0".repeat(64);
  assert.deepEqual(validateWorkCodexAuthorization(mismatched), []);
  await assert.rejects(() => storeWorkCodexAuthorization({ root: value.root, authorization: mismatched, allowMockAuthorization: true }), /planSha256 differs/);

  const mapTamper = structuredClone(value.privateMapping); mapTamper.entries[0].original = "different private value";
  const extraRoot = await mkdtemp(join(tmpdir(), "pixel-codex-map-tamper-")); t.after(() => rm(extraRoot, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(extraRoot, 0o700);
  await assert.rejects(() => storeWorkCodexPreview({ root: extraRoot, request: value.input, policy: value.policy, plan: value.plan, privateMapping: mapTamper }), /commitment differs|recompilation/);

  if (process.platform !== "win32") {
    const bundle = join(value.root, "plans", value.plan.planId, "bundle.json"), linked = join(value.root, "plans", value.plan.planId, "linked.json"); await link(bundle, linked);
    await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: harmless, allowMock: true, now: new Date(baseTime + 3000) }), /single-link/); await unlink(linked); assert.equal(calls, 0);
  }
  await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: { source: "codex-cli", run() {} }, allowMock: true }), /exactly one explicitly enabled adapter/u);
  await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: harmless, allowMock: false }), /exactly one explicitly enabled adapter/u);
  await assert.rejects(() => executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: { source: "codex-cli-isolated", run() {} }, allowIsolatedCodex: true, now: new Date(baseTime + 3000) }), /source cannot enter/u);
});

test("authorization, claim, and result semantic contracts reject billing and external-state lies", async (t) => {
  const value = await setup(t, 60); assert.deepEqual(validateWorkCodexAuthorization(value.authorization), []);
  const falseConfirmation = structuredClone(value.authorization.confirmation); falseConfirmation.planInspected = false;
  assert.throws(() => buildWorkCodexAuthorization({ plan: value.plan, confirmation: falseConfirmation, now: new Date(baseTime + 2000), expiresAt: new Date(baseTime + 10 * 60000), suffix: "000000000777" }), /external Codex work confirmation|failed validation/);
  const billingLie = structuredClone(value.authorization); billingLie.confirmation.billingAcknowledgment = "separately-billed-api-platform";
  assert.ok(validateWorkCodexAuthorization(billingLie).some((error) => /billing boundary/u.test(error)));
  const result = await executeWorkCodexAuthorization({ root: value.root, authorizationId: value.authorization.authorizationId, adapter: adapter(providerOutput(value)), allowMock: true, now: new Date(baseTime + 3000), resultNow: new Date(baseTime + 4000), claimSuffix: "000000000161", resultSuffix: "000000000162" });
  const externalLie = structuredClone(result.receipt); externalLie.invocation.externalState = "invoked-once";
  assert.ok(validateWorkCodexResult(externalLie).some((error) => /mock execution/u.test(error)));
  const partialUsage = structuredClone(result.receipt); partialUsage.status = "invalid-output"; partialUsage.usage = { reported: false, inputTokens: 10, outputTokens: null }; partialUsage.output = { storedPrivately: false, sanitizedSha256: null, rehydratedSha256: null, referencedPlaceholders: 0 };
  assert.ok(validateWorkCodexResult(partialUsage).some((error) => /unreported usage/u.test(error)));
  const failedHash = structuredClone(partialUsage); failedHash.usage.inputTokens = null; failedHash.output.sanitizedSha256 = "a".repeat(64);
  assert.ok(validateWorkCodexResult(failedHash).some((error) => /failed output must not retain/u.test(error)));
  const claim = JSON.parse(await readFile(join(value.root, "claims", value.authorization.authorizationId, "claim.json"), "utf8")); assert.deepEqual(validateWorkCodexExecutionClaim(claim), []);
  claim.externalProviderTurnAuthorized = true; assert.ok(validateWorkCodexExecutionClaim(claim).some((error) => /mock execution/u.test(error)));
});

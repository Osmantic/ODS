import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { compileWorkCodexPreview, WorkCodexCompileError } from "../deploy/work-codex-provider/compiler.mjs";
import { canonical, validateWorkCodexPlan, validateWorkCodexRequest } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T18:00:00Z");
const requestBoundary = "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.";
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const clone = (value) => structuredClone(value);
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));

function policy(taskClass = "structural-review") {
  const value = clone(policyTemplate); value.enabled = true; value.taskClasses[taskClass].enabled = true; return value;
}

function request(overrides = {}) {
  const documents = overrides.documents ?? [
    { documentId: "architecture", kind: "structure", language: "text", content: "Module Aurora sends review notices to lead@example.test and reads C:\\private\\acme\\layout.txt for Acme North." },
    { documentId: "test_report", kind: "test-failure", language: "text", content: "The boundary test fails when project Aurora receives a callback from 192.168.10.8." },
  ];
  const withHashes = documents.map((document) => ({ ...document, contentSha256: sha(document.content) }));
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-abcdef123456`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(),
    jobId: `work-${baseTime}-123456abcdef`, checkpointSha256: "c".repeat(64), ownerId: "owner-one", clientId: "client-one",
    taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["personal-identifiers", "structural"],
    localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: "d".repeat(64) },
    objective: "Review Aurora's structure for Acme North without changing the local project.",
    constraints: ["Do not expose owner-one, client-one, paths, contact data, or internal addresses."],
    acceptanceCriteria: ["Return a structural finding for Acme North with no private identifiers."],
    sensitiveTerms: [{ kind: "customer", value: "Acme North" }, { kind: "project", value: "Aurora" }],
    documents: withHashes, maxOutputTokens: 1024, boundary: requestBoundary, ...overrides,
  };
}

test("compiler creates a deterministic, content-minimized, non-executable ChatGPT preview", () => {
  const input = request(), privatePolicy = policy();
  const fixed = { request: input, policy: privatePolicy, now: new Date(baseTime + 1000), suffix: "000000000001", mappingNonce: "1".repeat(64) };
  const first = compileWorkCodexPreview(fixed);
  const second = compileWorkCodexPreview(fixed);
  assert.deepEqual(first, second);
  assert.deepEqual(validateWorkCodexRequest(input), []); assert.deepEqual(validateWorkCodexPlan(first.plan), []);
  assert.equal(first.plan.provider.authMode, "chatgpt");
  assert.equal(first.plan.provider.billingBoundary, "chatgpt-plan-or-credits-not-api-billing");
  assert.equal(first.plan.limits.maxEstimatedCostMicros, null);
  assert.equal(first.plan.providerInvoked, false); assert.equal(first.plan.credentialsProjected, false);
  assert.deepEqual(first.plan.approval, {
    required: true, external: true, singleUse: true, bindsExactPlanSha256: true,
    mode: "external-signed-mfa-assertion", issuer: "example-identity-provider", audience: "pixel-work-codex-authorization",
    trustedKeySha256: "0".repeat(64), signatureAlgorithm: "ed25519", primaryFactor: "password",
    allowedSecondFactors: ["totp-authenticator-app", "email-one-time-code"], maxAuthenticationAgeSeconds: 300,
    maxEvidenceLifetimeSeconds: 300, credentialsVisibleToPixel: false,
  });
  const capsuleText = JSON.stringify(first.plan.capsule), planText = JSON.stringify(first.plan);
  for (const privateValue of ["Acme North", "Aurora", "lead@example.test", "C:\\private\\acme\\layout.txt", "192.168.10.8", "owner-one", "client-one", "architecture", "test_report"]) {
    assert.doesNotMatch(capsuleText, new RegExp(privateValue.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "iu"));
    assert.doesNotMatch(planText, new RegExp(privateValue.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "iu"));
  }
  assert.match(capsuleText, /<PIXELWORK_(?:CUSTOMER|PROJECT|EMAIL|PATH|IP|IDENTIFIER)_\d{3}>/u);
  assert.ok(first.privateMapping.entries.some((entry) => entry.original === "Acme North"));
  assert.equal(first.plan.dlp.mappingSha256, sha(first.privateMapping));
  assert.equal(first.privateMapping.commitmentNonce, "1".repeat(64));
  assert.equal(planText.includes("1".repeat(64)), false);
  assert.deepEqual(first.privateMapping.documents, [{ syntheticId: "DOC_001", originalId: "architecture" }, { syntheticId: "DOC_002", originalId: "test_report" }]);
  assert.equal(first.plan.dlp.placeholderCount, first.privateMapping.entries.length);
  assert.equal(first.plan.capsule.documents[0].documentId, "DOC_001");
  assert.equal(first.plan.capsule.documents[1].documentId, "DOC_002");
  assert.equal(first.plan.capsule.responseLimits.schema, "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json");
  assert.match(first.plan.outputSchemaSha256, /^[a-f0-9]{64}$/u);
});

test("disabled routes, unnecessary remote work, classifications, and never-egress categories fail closed", () => {
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: policyTemplate, now: new Date(baseTime + 1) }), /provider is disabled/);
  const taskDisabled = policy(); taskDisabled.taskClasses["structural-review"].enabled = false;
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: taskDisabled, now: new Date(baseTime + 1) }), /task structural-review is disabled/);
  const sufficient = request({ localAttempt: { attempts: 1, outcome: "completed-sufficient", reasonCodes: ["local-sufficient"], receiptSha256: "d".repeat(64) } });
  assert.throws(() => compileWorkCodexPreview({ request: sufficient, policy: policy(), now: new Date(baseTime + 1) }), /already sufficient/);
  assert.throws(() => compileWorkCodexPreview({ request: request({ classification: "restricted" }), policy: policy(), now: new Date(baseTime + 1) }), /classification is not allowed/);
  assert.throws(() => compileWorkCodexPreview({ request: request({ dataCategories: ["credentials", "structural"] }), policy: policy(), now: new Date(baseTime + 1) }), /never-egress data/);
});

test("credentials, reserved placeholders, opaque tokens, controls, and expiry are rejected before preview", () => {
  const cases = [
    ["Bearer abcdefghijklmnopqrstuvwxyz123456", /credential-like secret/],
    ["sk-proj-abcdefghijklmnopqrstuvwxyz1234567890", /credential-like secret/],
    ["PIXELWORK_CUSTOMER_001", /reserved Pixel work placeholder/],
    ["zK9f2Lm8Qw4Rt7Yp3Vx6Bn1Cd5Hs0JaUeGiO", /high-entropy token/],
    ["bd73f1668ea8cd272d45d37937c55475537af98438b96c09c63f60723e538ea2", /high-entropy token/],
    ["unsafe\u0000text", /control characters/],
  ];
  for (const [content, expected] of cases) {
    const documents = [{ documentId: "architecture", kind: "structure", language: "text", content }];
    assert.throws(() => compileWorkCodexPreview({ request: request({ documents: documents.map((document) => ({ ...document, contentSha256: sha(document.content) })) }), policy: policy(), now: new Date(baseTime + 1) }), expected);
  }
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: policy(), now: new Date(baseTime + 21 * 60000) }), /expired/);
  const shortRetention = policy(); shortRetention.retention.privateRequestMinutes = 1;
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: shortRetention, now: new Date(baseTime + 2 * 60000) }), /private retention age/);
  const benignIdentifier = "CustomerAccountTransformerVersion2Alpha";
  const benignDocument = [{ documentId: "architecture", kind: "structure", language: "text", content: benignIdentifier, contentSha256: sha(benignIdentifier) }];
  assert.doesNotThrow(() => compileWorkCodexPreview({ request: request({ documents: benignDocument }), policy: policy(), now: new Date(baseTime + 1) }));
});

test("structural mode excludes raw work while owner-authorized code remains explicit and bounded", () => {
  const source = [{ documentId: "source", kind: "code-source", language: "javascript", content: "export function add(a, b) { return a + b; }" }];
  const structural = request({ documents: source.map((document) => ({ ...document, contentSha256: sha(document.content) })) });
  assert.throws(() => compileWorkCodexPreview({ request: structural, policy: policy(), now: new Date(baseTime + 1) }), /structural-only egress/);

  const authorized = request({
    taskClass: "patch-proposal", egressMode: "owner-authorized-content", dataCategories: ["proprietary-code", "source-derived"],
    documents: source.map((document) => ({ ...document, contentSha256: sha(document.content) })), sensitiveTerms: [],
  });
  const compiled = compileWorkCodexPreview({ request: authorized, policy: policy("patch-proposal"), now: new Date(baseTime + 1), suffix: "000000000002" });
  assert.equal(compiled.plan.dlp.rawContentAuthorized, true); assert.equal(compiled.plan.egressMode, "owner-authorized-content");
  const undeclared = clone(authorized); undeclared.dataCategories = ["source-derived"];
  assert.throws(() => compileWorkCodexPreview({ request: undeclared, policy: policy("patch-proposal"), now: new Date(baseTime + 1) }), /must declare proprietary-code/);
});

test("API-key mode is unmistakably metered and enforced by an exact cost ceiling", () => {
  const metered = policy();
  metered.provider.authMode = "api-key";
  metered.transport.allowedHosts = ["api.openai.com"];
  metered.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false };
  metered.provider.billing = {
    mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.",
    currency: "USD", inputMicrosPerMillionTokens: 2_000_000, outputMicrosPerMillionTokens: 8_000_000,
    source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 100_000,
  };
  const compiled = compileWorkCodexPreview({ request: request(), policy: metered, now: new Date(baseTime + 1), suffix: "000000000003" });
  assert.equal(compiled.plan.provider.billingBoundary, "separately-billed-api-platform");
  assert.ok(Number.isInteger(compiled.plan.limits.maxEstimatedCostMicros));
  const tooLow = clone(metered); tooLow.provider.billing.maxEstimatedCostMicros = 1;
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: tooLow, now: new Date(baseTime + 1) }), /estimated API cost exceeds/);
  const mislabeled = clone(metered); mislabeled.provider.billing.mode = "subscription";
  assert.throws(() => compileWorkCodexPreview({ request: request(), policy: mislabeled, now: new Date(baseTime + 1) }), WorkCodexCompileError);
});

test("hash, canonical order, lifetime, size, and semantic plan tampering are detected", () => {
  const badHash = request(); badHash.documents[0].contentSha256 = "0".repeat(64);
  assert.throws(() => compileWorkCodexPreview({ request: badHash, policy: policy(), now: new Date(baseTime + 1) }), /hash differs/);
  const badOrder = request(); badOrder.sensitiveTerms.reverse();
  assert.throws(() => compileWorkCodexPreview({ request: badOrder, policy: policy(), now: new Date(baseTime + 1) }), /canonically ordered/);
  const badLifetime = request({ expiresAt: new Date(baseTime + 31 * 60000).toISOString() });
  assert.throws(() => compileWorkCodexPreview({ request: badLifetime, policy: policy(), now: new Date(baseTime + 1) }), /at most 30 minutes/);
  const tiny = policy(); tiny.dataPolicy.maxDocumentBytes = 256; tiny.dataPolicy.maxTotalDocumentBytes = 1024;
  const largeText = "x".repeat(257), largeDocument = [{ documentId: "architecture", kind: "structure", language: "text", content: largeText, contentSha256: sha(largeText) }];
  assert.throws(() => compileWorkCodexPreview({ request: request({ documents: largeDocument }), policy: tiny, now: new Date(baseTime + 1) }), /document architecture exceeds/);

  const { plan } = compileWorkCodexPreview({ request: request(), policy: policy(), now: new Date(baseTime + 1), suffix: "000000000004" });
  const tampered = clone(plan); tampered.capsule.documents[0].content += " changed";
  assert.ok(validateWorkCodexPlan(tampered).some((error) => /capsuleSha256|estimatedInputTokens/u.test(error)));
  const leakedPlaceholder = clone(plan); leakedPlaceholder.capsule.objective += " PIXELWORK_FAKE"; leakedPlaceholder.capsuleSha256 = sha(leakedPlaceholder.capsule);
  assert.ok(validateWorkCodexPlan(leakedPlaceholder).some((error) => /malformed or unbound/u.test(error)));
  const wrongOutputSchema = clone(plan); wrongOutputSchema.outputSchemaSha256 = "0".repeat(64);
  assert.ok(validateWorkCodexPlan(wrongOutputSchema).some((error) => /structured-output contract/u.test(error)));
});

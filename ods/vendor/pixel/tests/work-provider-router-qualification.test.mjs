import assert from "node:assert/strict";
import test from "node:test";

import { capabilityCovers, estimatedCostMicros, providerQualified, qualificationCurrent, remoteAdmissible, validateQualification, WorkProviderRouterQualificationError } from "../deploy/work-provider-router/qualification.mjs";
import { validateWorkProviderRouterQualification } from "../scripts/lib/work-contract.mjs";
import { makeQualification, makeRequest, makePrivatePolicy, makeRouterPolicy, sha } from "./fixtures/work-provider-router.mjs";

const NOW = new Date("2026-08-21T12:00:00Z");

test("qualification validates against the hash-bound schema", () => {
  const q = makeQualification("moonshot-kimi");
  assert.deepEqual(validateWorkProviderRouterQualification(q), []);
  const frozen = validateQualification(q);
  assert.equal(Object.isFrozen(frozen.capability), true);
  assert.throws(() => validateQualification({ ...q, attestationKind: "nonsense" }), WorkProviderRouterQualificationError);
});

test("qualificationSha256 must equal the sha of the canonical qualification with the field omitted", () => {
  const q = makeQualification("moonshot-kimi");
  const { qualificationSha256, ...withoutSha } = q;
  assert.equal(q.qualificationSha256, sha(withoutSha));
  assert.throws(() => validateQualification({ ...q, qualificationSha256: "c".repeat(64) }, NOW), WorkProviderRouterQualificationError);
});

test("a qualification attested in the future is rejected", () => {
  const q = makeQualification("moonshot-kimi", { attestedAt: "2026-08-21T13:00:00Z", expiresAt: "2026-08-22T13:00:00Z" });
  assert.equal(qualificationCurrent(q, NOW, 86400), false);
  assert.throws(() => validateQualification(q, NOW), WorkProviderRouterQualificationError);
});

test("connectivity-smoke is not semantic capability", () => {
  const q = makeQualification("moonshot-kimi", { attestationKind: "connectivity-smoke" });
  const request = makeRequest();
  const policy = makeRouterPolicy();
  const result = remoteAdmissible({ providerId: "moonshot-kimi", qualification: q, request, policy, now: NOW, privatePolicy: makePrivatePolicy("moonshot-kimi") });
  assert.equal(result.admissible, false);
  assert.equal(result.reason, "remote-unqualified");
});

test("expired qualification fails closed and cannot select a remote", () => {
  const q = makeQualification("moonshot-kimi", { attestedAt: "2026-08-20T00:00:00Z", expiresAt: "2026-08-20T12:00:00Z" });
  assert.equal(qualificationCurrent(q, NOW, 86400), false);
  const result = remoteAdmissible({ providerId: "moonshot-kimi", qualification: q, request: makeRequest(), policy: makeRouterPolicy(), now: NOW, privatePolicy: makePrivatePolicy("moonshot-kimi") });
  assert.equal(result.admissible, false);
  assert.equal(result.reason, "remote-expired");
});

test("a qualification older than the policy max age fails closed", () => {
  const q = makeQualification("moonshot-kimi", { attestedAt: "2026-08-19T00:00:00Z", expiresAt: "2026-08-30T00:00:00Z" });
  assert.equal(qualificationCurrent(q, NOW, 86400), false);
});

test("capability envelope covers request across task, context, tools, vision, tokens, and cost", () => {
  const q = makeQualification("moonshot-kimi");
  const base = makeRequest();
  assert.equal(capabilityCovers(q, base), true);
  assert.equal(capabilityCovers(q, makeRequest({ taskClass: "failure-triage" })), true);
  assert.equal(capabilityCovers(q, makeRequest({ taskClass: "patch-proposal" })), true);
  assert.equal(capabilityCovers(q, makeRequest({ contextNeed: "codebase" })), true);
  assert.equal(capabilityCovers(q, makeRequest({ visionNeed: true })), true);
  assert.equal(capabilityCovers(q, makeRequest({ inputTokenEstimate: 7999999 })), true);
  assert.equal(capabilityCovers(q, makeRequest({ contextNeed: "codebase" })), true);
});

test("capability envelope rejects out-of-envelope requests", () => {
  const q = makeQualification("moonshot-kimi", { capability: { ...makeQualification("moonshot-kimi").capability, contextNeed: "workspace", toolsNeed: "standard", visionNeed: false, costMicrosPerRunMax: 1000000 } });
  const assertUncovered = (overrides) => assert.equal(capabilityCovers(q, makeRequest(overrides)), false);
  assertUncovered({ contextNeed: "codebase" });
  assertUncovered({ toolsNeed: "compute" });
  assertUncovered({ visionNeed: true });
  assertUncovered({ inputTokenEstimate: 8000001 });
  assertUncovered({ outputTokenEstimate: 2000001 });
  assertUncovered({ costCeilingMicros: 1000001 });
  assertUncovered({ taskClass: "patch-proposal", inputTokenEstimate: 8000001 });
});

test("remoteAdmissible requires current, semantic, and envelope-complete qualification", () => {
  const request = makeRequest();
  const policy = makeRouterPolicy();
  const privatePolicy = makePrivatePolicy("moonshot-kimi");
  assert.equal(remoteAdmissible({ providerId: "moonshot-kimi", qualification: makeQualification("moonshot-kimi"), request, policy, now: NOW, privatePolicy }).admissible, true);
  const noVision = makeQualification("moonshot-kimi", { capability: { ...makeQualification("moonshot-kimi").capability, visionNeed: false } });
  assert.equal(remoteAdmissible({ providerId: "moonshot-kimi", qualification: noVision, request: makeRequest({ visionNeed: true }), policy, now: NOW, privatePolicy }).admissible, false);
});

test("qualification providerId must exactly match the candidate provider", () => {
  const policy = makeRouterPolicy();
  const privatePolicy = makePrivatePolicy("moonshot-kimi");
  const q = makeQualification("openai");
  const result = remoteAdmissible({ providerId: "moonshot-kimi", qualification: q, request: makeRequest(), policy, now: NOW, privatePolicy });
  assert.equal(result.admissible, false);
  assert.equal(result.reason, "remote-unqualified");
});

test("policy semanticTaskClasses gates the request task class", () => {
  const policy = makeRouterPolicy({ qualification: { required: true, maxAgeSeconds: 86400, semanticTaskClasses: ["patch-proposal"] } });
  const request = makeRequest({ taskClass: "structural-review" });
  const result = providerQualified({ providerId: "moonshot-kimi", qualification: makeQualification("moonshot-kimi"), request, policy, now: NOW });
  assert.equal(result.qualified, false);
  assert.equal(result.reason, "task-class-not-attested");
  assert.equal(providerQualified({ providerId: "moonshot-kimi", qualification: makeQualification("moonshot-kimi"), request: makeRequest({ taskClass: "patch-proposal" }), policy, now: NOW }).qualified, true);
});

test("estimated cost is conservative, BigInt-safe, and bound by the request ceiling", () => {
  const q = makeQualification("moonshot-kimi");
  const request = makeRequest({ inputTokenEstimate: 10000, outputTokenEstimate: 2000 });
  const cost = estimatedCostMicros(q, request);
  assert.equal(cost, 10000n + 6000n + 50000n);
  assert.equal(cost <= BigInt(request.costCeilingMicros), true);
  // A request ceiling below the estimated cost fails closed even when the capability max is high.
  const over = providerQualified({ providerId: "moonshot-kimi", qualification: q, request: makeRequest({ costCeilingMicros: 1000 }), policy: makeRouterPolicy(), now: NOW });
  assert.equal(over.qualified, false);
  assert.equal(over.reason, "cost-ceiling-exceeded");
});

test("estimated cost at schema maximums never overflows", () => {
  const q = makeQualification("moonshot-kimi", {
    pricing: { inputMicrosPerMillionTokens: 1000000000, outputMicrosPerMillionTokens: 1000000000, fixedMicrosPerRun: 1000000000 },
    capability: { ...makeQualification("moonshot-kimi").capability, inputTokenMax: 100000000, outputTokenMax: 10000000, costMicrosPerRunMax: 1000000000 },
  });
  const request = makeRequest({ inputTokenEstimate: 100000000, outputTokenEstimate: 10000000, costCeilingMicros: 1000000000 });
  const cost = estimatedCostMicros(q, request);
  assert.equal(cost, 100000000000n + 10000000000n + 1000000000n);
  assert.equal(cost > BigInt(request.costCeilingMicros), true);
  const result = providerQualified({ providerId: "moonshot-kimi", qualification: q, request, policy: makeRouterPolicy(), now: NOW });
  assert.equal(result.qualified, false);
  assert.equal(result.reason, "cost-ceiling-exceeded");
});

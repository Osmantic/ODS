import assert from "node:assert/strict";
import test from "node:test";

import { evaluateModelQualification, modelCapabilityReceiptSha256, requireQualifiedModel, WorkModelQualificationError } from "../deploy/work-controller/model-qualification.mjs";
import { validateWorkModelCapabilityReceipt } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const model = () => ({
  provider: "llama.cpp", id: "local-capable-model", modelArtifactSha256: digest("a"),
  backendImageDigest: `sha256:${digest("b")}`, backendVersion: "b6123",
  acceleratorClass: "nvidia-cuda",
  promptContractSha256: digest("c"), toolSchemaSha256: digest("d"),
  contextWindow: 131072, supportsVision: false,
});
const categories = ["argument-fidelity", "context-retention", "recovery-discipline", "structured-output", "tool-selection", "usage-accounting"];
const cases = () => categories.map((category, index) => ({
  id: `case-${index + 1}`, category, profiles: ["assistant", "scout", "builder", "data-lab", "researcher"], caseSha256: digest(String(index + 1)), passed: true, failureClass: "pass", intentMutationObserved: false,
  inputTokens: 100 + index, outputTokens: 50 + index, usageSource: "backend-observed", latencyMs: 500 + index,
  contextTokensTested: category === "context-retention" ? 65536 : 4096,
  outputTokensRequired: category === "structured-output" ? 1024 : 128,
}));
const evaluate = (overrides = {}) => evaluateModelQualification({
  model: model(), cases: cases(), observedAt: new Date("2026-08-10T12:00:00Z"),
  expiresAt: new Date("2026-08-17T12:00:00Z"), evaluatorSha256: digest("e"), suffix: "abcdef123456", ...overrides,
});

function route(receipt, overrides = {}) {
  return requireQualifiedModel(receipt, {
    expectedReceiptSha256: modelCapabilityReceiptSha256(receipt),
    expectedCasesSha256: receipt.suite.casesSha256,
    expectedEvaluatorSha256: receipt.suite.evaluatorSha256,
    model: model(), profile: "builder", requiredContextTokens: 32768, requiredOutputTokens: 55,
    now: new Date("2026-08-11T12:00:00Z"), ...overrides,
  });
}

test("empirical model qualification binds one exact measured capability envelope", () => {
  const receipt = evaluate();
  assert.deepEqual(validateWorkModelCapabilityReceipt(receipt), []);
  assert.equal(receipt.status, "qualified");
  assert.deepEqual(receipt.envelope.eligibleProfiles, ["assistant", "scout", "builder", "data-lab", "researcher"]);
  assert.equal(receipt.observations.casesPassed, 6);
  assert.equal(receipt.observations.caseResults.length, 6);
  assert.ok(receipt.observations.caseResults.every((entry) => entry.failureClass === "pass"));
  assert.equal(receipt.observations.usageSource, "backend-observed");
  assert.deepEqual(receipt.observations.profilePass, { assistant: true, scout: true, builder: true, "data-lab": true, researcher: true });
  assert.equal(receipt.envelope.maxContextTokens, 65536);
  assert.equal(receipt.envelope.maxOutputTokens, 1024);
  const routed = route(receipt);
  assert.equal(routed.qualificationId, receipt.qualificationId);
  assert.equal(routed.exactUsage, true);
});

test("model names, parameter claims, and substituted artifacts cannot stand in for observations", () => {
  const giantName = model();
  giantName.id = "qwen-999b-tier-a-perfect";
  const failed = cases();
  failed[0].passed = false; failed[0].failureClass = "tool-arguments-mismatch";
  const receipt = evaluate({ model: giantName, cases: failed });
  assert.equal(receipt.status, "failed");
  assert.throws(() => route(receipt, { model: giantName, profile: "scout", requiredContextTokens: 1024, requiredOutputTokens: 1 }), WorkModelQualificationError);
  const qualified = evaluate();
  const substituted = model();
  substituted.modelArtifactSha256 = digest("e");
  assert.throws(() => route(qualified, { model: substituted, requiredContextTokens: 1024, requiredOutputTokens: 1 }), /identity differs/);
});

test("estimated usage, intent mutation, expiration, and suite gaps fail closed", () => {
  const estimated = cases();
  estimated.at(-1).usageSource = "estimated";
  const degraded = evaluate({ cases: estimated });
  assert.equal(degraded.status, "degraded");
  assert.deepEqual(degraded.envelope.eligibleProfiles, []);
  const mutated = cases();
  mutated[2].passed = false; mutated[2].failureClass = "content-mismatch"; mutated[2].intentMutationObserved = true;
  assert.equal(evaluate({ cases: mutated }).status, "failed");
  const qualified = evaluate();
  assert.throws(() => route(qualified, { requiredContextTokens: 1024, requiredOutputTokens: 1, now: new Date("2026-08-18T12:00:00Z") }), /not current/);
  assert.throws(() => evaluate({ cases: cases().slice(1) }), /incomplete|required category/);
});

test("tampered receipts and requests beyond the measured envelope are rejected", () => {
  const receipt = evaluate();
  const tampered = structuredClone(receipt);
  tampered.status = "degraded";
  assert.ok(validateWorkModelCapabilityReceipt(tampered).length > 0);
  const inventedProfile = structuredClone(receipt);
  inventedProfile.observations.profilePass.builder = false;
  assert.ok(validateWorkModelCapabilityReceipt(inventedProfile).some((error) => /eligibleProfiles/u.test(error)));
  const inventedEnvelope = structuredClone(receipt);
  inventedEnvelope.envelope.toolSelection = false;
  assert.ok(validateWorkModelCapabilityReceipt(inventedEnvelope).some((error) => /toolSelection/u.test(error)));
  const inventedCase = structuredClone(receipt);
  inventedCase.observations.caseResults[0].inputTokens += 1;
  assert.ok(validateWorkModelCapabilityReceipt(inventedCase).some((error) => /token totals/u.test(error)));
  const contradictoryCase = structuredClone(receipt);
  contradictoryCase.observations.caseResults[0].failureClass = "content-mismatch";
  assert.ok(validateWorkModelCapabilityReceipt(contradictoryCase).some((error) => /pass state/u.test(error)));
  const overlong = structuredClone(receipt);
  overlong.expiresAt = "2027-08-17T12:00:00.000Z";
  assert.ok(validateWorkModelCapabilityReceipt(overlong).some((error) => /30 days/u.test(error)));
  assert.throws(() => route(receipt, { requiredContextTokens: 131073, requiredOutputTokens: 1 }), /exceeds/);
  assert.throws(() => route(receipt, { expectedReceiptSha256: digest("0"), requiredContextTokens: 1024, requiredOutputTokens: 1 }), /trusted hash/);
});

test("qualification routes only profiles that passed the exact bound corpus", () => {
  const profiled = cases();
  profiled.push({ ...profiled[0], id: "builder-regression", profiles: ["builder"], caseSha256: digest("f"), passed: false, failureClass: "tool-arguments-mismatch" });
  const receipt = evaluate({ cases: profiled });
  assert.equal(receipt.status, "qualified");
  assert.deepEqual(receipt.observations.profilePass, { assistant: true, scout: true, builder: false, "data-lab": true, researcher: true });
  assert.deepEqual(receipt.envelope.eligibleProfiles, ["assistant", "scout", "data-lab", "researcher"]);
  assert.throws(() => route(receipt), /not qualified/);
  assert.equal(route(receipt, { profile: "scout" }).profile, "scout");
});

test("corpus, evaluator, accelerator, and per-case substitutions fail closed", () => {
  const receipt = evaluate();
  assert.throws(() => route(receipt, { expectedCasesSha256: digest("0") }), /corpus differs/);
  assert.throws(() => route(receipt, { expectedEvaluatorSha256: digest("0") }), /evaluator differs/);
  const changedAccelerator = model(); changedAccelerator.acceleratorClass = "cpu";
  assert.throws(() => route(receipt, { model: changedAccelerator }), /identity differs/);
  const rebound = cases(); rebound[0].caseSha256 = digest("0");
  assert.notEqual(evaluate({ cases: rebound }).suite.casesSha256, receipt.suite.casesSha256);
  const unordered = cases(); unordered[0].profiles = ["builder", "assistant"];
  assert.throws(() => evaluate({ cases: unordered }), /profiles are invalid/);
});

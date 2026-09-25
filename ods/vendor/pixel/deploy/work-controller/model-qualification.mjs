import { createHash, randomBytes } from "node:crypto";

import { canonical, validateWorkModelCapabilityReceipt } from "../../scripts/lib/work-contract.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/u;
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const acceleratorClasses = Object.freeze(["cpu", "nvidia-cuda", "amd-rocm", "intel-xpu", "apple-metal", "other-local"]);
const categories = Object.freeze([
  "argument-fidelity", "context-retention", "recovery-discipline",
  "structured-output", "tool-selection", "usage-accounting",
]);
const failureClasses = Object.freeze([
  "pass", "invocation-failed", "missing-response", "finish-reason-mismatch",
  "unexpected-tool-call", "content-type-mismatch", "content-mismatch",
  "json-parse-failed", "json-value-mismatch", "tool-call-shape-mismatch",
  "tool-arguments-parse-failed", "tool-name-mismatch", "tool-arguments-mismatch",
  "usage-missing", "output-underflow",
]);
// Assistant is a distinct product surface.  Its portal tool grammar and recovery
// behavior must be measured directly; Builder qualification is not a safe proxy.
const profileOrder = Object.freeze(["assistant", "scout", "builder", "data-lab", "researcher"]);
const boundary = "Content-free empirical capability evidence for one exact model/backend/prompt/tool contract. It grants no execution, data, credential, network, external-effect, or completion authority.";

export class WorkModelQualificationError extends Error {}

function fail(message) {
  throw new WorkModelQualificationError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
}

export function modelCapabilityReceiptSha256(value) { return sha(value); }

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}

function exactModel(model) {
  if (!model || typeof model !== "object" || Array.isArray(model)) fail("model identity is missing");
  const providers = ["llama.cpp", "ollama", "vllm", "openai-compatible-local"];
  if (!providers.includes(model.provider) || !ID_RE.test(model.id ?? "") || !SHA_RE.test(model.modelArtifactSha256 ?? "") || !IMAGE_RE.test(model.backendImageDigest ?? "") || !VERSION_RE.test(model.backendVersion ?? "") || !acceleratorClasses.includes(model.acceleratorClass) || !SHA_RE.test(model.promptContractSha256 ?? "") || !SHA_RE.test(model.toolSchemaSha256 ?? "")) fail("model identity is invalid");
  integer(model.contextWindow, 1024, 2000000, "model context window");
  if (typeof model.supportsVision !== "boolean") fail("model vision capability is invalid");
  return structuredClone(model);
}

function exactCases(cases, modelContextWindow) {
  if (!Array.isArray(cases) || cases.length < categories.length || cases.length > 256) fail("qualification cases are incomplete or unbounded");
  const ids = new Set();
  const seen = new Set();
  return cases.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry) || JSON.stringify(Object.keys(entry).sort()) !== JSON.stringify(["caseSha256", "category", "contextTokensTested", "failureClass", "id", "inputTokens", "intentMutationObserved", "latencyMs", "outputTokens", "outputTokensRequired", "passed", "profiles", "usageSource"].sort())) fail("qualification case shape is invalid");
    if (!/^[a-z][a-z0-9-]{0,63}$/u.test(entry.id ?? "") || ids.has(entry.id)) fail("qualification case identifier is invalid or duplicated");
    ids.add(entry.id);
    if (!categories.includes(entry.category)) fail("qualification case category is invalid");
    seen.add(entry.category);
    if (!SHA_RE.test(entry.caseSha256 ?? "")) fail("qualification case binding is invalid");
    if (!Array.isArray(entry.profiles) || entry.profiles.length < 1 || entry.profiles.length > profileOrder.length || new Set(entry.profiles).size !== entry.profiles.length || entry.profiles.some((profile) => !profileOrder.includes(profile)) || canonical(entry.profiles) !== canonical(profileOrder.filter((profile) => entry.profiles.includes(profile)))) fail("qualification case profiles are invalid");
    if (typeof entry.passed !== "boolean" || typeof entry.intentMutationObserved !== "boolean" || !["backend-observed", "estimated"].includes(entry.usageSource) || !failureClasses.includes(entry.failureClass)) fail("qualification case observation is invalid");
    if (entry.passed !== (entry.failureClass === "pass") || entry.passed && entry.intentMutationObserved) fail("qualification case result differs from its failure class");
    integer(entry.inputTokens, 0, 2000000000, "qualification input usage");
    integer(entry.outputTokens, 0, 500000000, "qualification output usage");
    integer(entry.contextTokensTested, 1, modelContextWindow, "qualification tested context");
    integer(entry.outputTokensRequired, 1, 500000000, "qualification required output");
    integer(entry.latencyMs, 0, 86400000, "qualification latency");
    return structuredClone(entry);
  }).map((entry, index, checked) => {
    if (index === checked.length - 1 && categories.some((category) => !seen.has(category))) fail("qualification suite omits a required category");
    return entry;
  });
}

function caseManifest(observations) {
  return observations.map((entry) => ({
    id: entry.id, category: entry.category, profiles: entry.profiles, caseSha256: entry.caseSha256,
  }));
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : Math.floor((sorted[middle - 1] + sorted[middle]) / 2);
}

export function evaluateModelQualification({ model, cases, evaluatorSha256, observedAt = new Date(), expiresAt, suffix = randomBytes(6).toString("hex") }) {
  const identity = exactModel(model);
  const observations = exactCases(cases, identity.contextWindow);
  const observed = timestamp(observedAt, "qualification observation time");
  const expiry = timestamp(expiresAt, "qualification expiry time");
  if (expiry.getTime() <= observed.getTime() || expiry.getTime() - observed.getTime() > 30 * 86400000) fail("qualification expiry is invalid");
  if (!SUFFIX_RE.test(suffix)) fail("qualification identity suffix is invalid");
  if (!SHA_RE.test(evaluatorSha256 ?? "")) fail("qualification evaluator binding is invalid");
  const categoryPass = Object.fromEntries(categories.map((category) => [category, observations.filter((entry) => entry.category === category).every((entry) => entry.passed && !entry.intentMutationObserved)]));
  const exactUsage = observations.every((entry) => entry.usageSource === "backend-observed") && categoryPass["usage-accounting"];
  const intentMutationObserved = observations.some((entry) => entry.intentMutationObserved);
  const profilePass = Object.fromEntries(profileOrder.map((profile) => {
    const relevant = observations.filter((entry) => entry.profiles.includes(profile));
    const represented = new Set(relevant.map((entry) => entry.category));
    return [profile, categories.every((category) => represented.has(category)) && relevant.every((entry) => entry.passed && !entry.intentMutationObserved)];
  }));
  const empiricallyPassingProfiles = profileOrder.filter((profile) => profilePass[profile]);
  const eligibleProfiles = exactUsage && !intentMutationObserved ? empiricallyPassingProfiles : [];
  const qualified = eligibleProfiles.length > 0;
  const degraded = !qualified && empiricallyPassingProfiles.length > 0;
  const contextByProfile = Object.fromEntries(eligibleProfiles.map((profile) => [profile, Math.max(...observations.filter((entry) => entry.profiles.includes(profile) && entry.category === "context-retention" && entry.passed).map((entry) => entry.contextTokensTested))]));
  const outputByProfile = Object.fromEntries(eligibleProfiles.map((profile) => [profile, Math.max(...observations.filter((entry) => entry.profiles.includes(profile) && entry.category === "structured-output" && entry.passed).map((entry) => entry.outputTokensRequired))]));
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-capability-receipt-v1.schema.json",
    schemaVersion: 1,
    qualificationId: `modelqual-${String(observed.getTime()).padStart(13, "0")}-${suffix}`,
    observedAt: observed.toISOString(),
    expiresAt: expiry.toISOString(),
    model: identity,
    suite: { id: "pixel-local-model-capability", version: 1, casesSha256: sha(caseManifest(observations)), evaluatorSha256, casesTotal: observations.length, requiredCategories: [...categories] },
    observations: {
      caseResults: observations,
      casesPassed: observations.filter((entry) => entry.passed).length,
      casesFailed: observations.filter((entry) => !entry.passed).length,
      inputTokens: observations.reduce((total, entry) => total + entry.inputTokens, 0),
      outputTokens: observations.reduce((total, entry) => total + entry.outputTokens, 0),
      usageSource: exactUsage ? "backend-observed" : "estimated",
      medianLatencyMs: median(observations.map((entry) => entry.latencyMs)),
      intentMutationObserved,
      categoryPass,
      profilePass,
    },
    envelope: {
      eligibleProfiles,
      maxContextTokens: qualified ? Math.min(...Object.values(contextByProfile)) : 0,
      maxOutputTokens: qualified ? Math.min(...Object.values(outputByProfile)) : 0,
      structuredOutput: categoryPass["structured-output"],
      toolSelection: categoryPass["tool-selection"],
      argumentFidelity: categoryPass["argument-fidelity"],
      contextRetention: categoryPass["context-retention"],
      recoveryDiscipline: categoryPass["recovery-discipline"] && !intentMutationObserved,
      exactUsage,
    },
    status: qualified ? "qualified" : degraded ? "degraded" : "failed",
    authority: { grantsExecution: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary,
  };
  const errors = validateWorkModelCapabilityReceipt(receipt);
  if (errors.length) fail(`qualification receipt is invalid: ${errors[0]}`);
  return receipt;
}

export function requireQualifiedModel(receipt, { expectedReceiptSha256, expectedCasesSha256, expectedEvaluatorSha256, model, profile, requiredContextTokens, requiredOutputTokens, now = new Date() }) {
  const errors = validateWorkModelCapabilityReceipt(receipt);
  if (errors.length) fail(`qualification receipt is invalid: ${errors[0]}`);
  if (!SHA_RE.test(expectedReceiptSha256 ?? "") || expectedReceiptSha256 !== sha(receipt)) fail("qualification receipt differs from its trusted hash");
  if (!SHA_RE.test(expectedCasesSha256 ?? "") || receipt.suite.casesSha256 !== expectedCasesSha256) fail("qualification corpus differs from its trusted hash");
  if (!SHA_RE.test(expectedEvaluatorSha256 ?? "") || receipt.suite.evaluatorSha256 !== expectedEvaluatorSha256) fail("qualification evaluator differs from its trusted hash");
  const identity = exactModel(model);
  const checkedNow = timestamp(now, "qualification check time");
  if (canonical(receipt.model) !== canonical(identity)) fail("model identity differs from its qualification receipt");
  if (!profileOrder.includes(profile) || !receipt.envelope.eligibleProfiles.includes(profile) || receipt.status !== "qualified") fail("model is not qualified for the requested profile");
  integer(requiredContextTokens, 1, 2000000, "required context tokens");
  integer(requiredOutputTokens, 1, 500000000, "required output tokens");
  if (checkedNow.getTime() < Date.parse(receipt.observedAt) || checkedNow.getTime() >= Date.parse(receipt.expiresAt)) fail("model qualification receipt is not current");
  if (requiredContextTokens > receipt.envelope.maxContextTokens || requiredOutputTokens > receipt.envelope.maxOutputTokens) fail("task exceeds the measured model capability envelope");
  return {
    qualificationId: receipt.qualificationId,
    receiptSha256: sha(receipt),
    casesSha256: receipt.suite.casesSha256,
    evaluatorSha256: receipt.suite.evaluatorSha256,
    profile,
    maxContextTokens: receipt.envelope.maxContextTokens,
    maxOutputTokens: receipt.envelope.maxOutputTokens,
    exactUsage: true,
  };
}

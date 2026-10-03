import { createHash } from "node:crypto";
import { canonical, validateWorkProviderRouterQualification } from "../../scripts/lib/work-contract.mjs";
import { resolveProviderModel, resolveWorkProvider } from "../work-provider/provider-registry.mjs";

const CONTEXT_RANK = Object.freeze({ none: 0, workspace: 1, codebase: 2 });
const TOOLS_RANK = Object.freeze({ none: 0, standard: 1, compute: 2 });
const CLASSIFICATION_TO_POLICY = Object.freeze({ public: "public", internal: "internal-source" });
// Request categories (auth-material) reconciled to the owner private-policy category names
// (authentication-material) at the router boundary.
const SENSITIVE_TO_PRIVATE = Object.freeze({
  "auth-material": "authentication-material",
  credentials: "credentials",
  "private-keys": "private-keys",
  "session-tokens": "session-tokens",
  "regulated-records": "regulated-records",
  "unapproved-owner-data": "unapproved-owner-data",
});

export class WorkProviderRouterQualificationError extends Error {}
function fail(message) { throw new WorkProviderRouterQualificationError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export function validateQualification(qualification, now) {
  const errors = validateWorkProviderRouterQualification(qualification);
  if (errors.length) fail(`provider router qualification failed validation: ${errors.join("; ")}`);
  const { qualificationSha256, ...withoutSha } = qualification;
  if (sha(withoutSha) !== qualificationSha256) fail("provider router qualification hash does not match its canonical content");
  if (now !== undefined && now !== null) {
    if (!(now instanceof Date) || Number.isNaN(now.getTime())) fail("provider router qualification time is invalid");
    if (Date.parse(qualification.attestedAt) > now.getTime()) fail("provider router qualification is attested in the future");
  }
  return deepFreeze(structuredClone(qualification));
}

export function qualificationCurrent(qualification, now, maxAgeSeconds) {
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) fail("provider router qualification time is invalid");
  const nowMs = now.getTime();
  const attested = Date.parse(qualification.attestedAt);
  const expires = Date.parse(qualification.expiresAt);
  if (expires <= nowMs) return false;
  if (attested > nowMs) return false;
  if (nowMs - attested > maxAgeSeconds * 1000) return false;
  if (expires <= attested) return false;
  return true;
}

export function isSemanticQualification(qualification) {
  return qualification.attestationKind === "semantic-capability";
}

export function capabilityCovers(qualification, request) {
  const capability = qualification.capability;
  if (!capability.taskClasses.includes(request.taskClass)) return false;
  if (CONTEXT_RANK[request.contextNeed] > CONTEXT_RANK[capability.contextNeed]) return false;
  if (TOOLS_RANK[request.toolsNeed] > TOOLS_RANK[capability.toolsNeed]) return false;
  if (request.visionNeed && !capability.visionNeed) return false;
  if (request.inputTokenEstimate > capability.inputTokenMax) return false;
  if (request.outputTokenEstimate > capability.outputTokenMax) return false;
  if (request.costCeilingMicros > capability.costMicrosPerRunMax) return false;
  return true;
}

// Conservative ceil cost in micros, BigInt-safe: each token-mileage term is rounded up
// before summing so the estimate never understates an expensive call.
export function estimatedCostMicros(qualification, request) {
  const { inputMicrosPerMillionTokens, outputMicrosPerMillionTokens, fixedMicrosPerRun } = qualification.pricing;
  const inputCost = (BigInt(request.inputTokenEstimate) * BigInt(inputMicrosPerMillionTokens) + 999999n) / 1000000n;
  const outputCost = (BigInt(request.outputTokenEstimate) * BigInt(outputMicrosPerMillionTokens) + 999999n) / 1000000n;
  return inputCost + outputCost + BigInt(fixedMicrosPerRun);
}

// The model bound for a routed semantic run must agree with the provider profile's closed
// model-selection policy (fixed = profile.defaultModel, owner-pinned = owner-private policy
// model, qualification-pinned = sealed qualification model). This is delegated to the single
// provider-registry authority so the rule is never scattered across call sites.
export function modelAgreement({ resolvedProvider, privatePolicy = null, qualification, model }) {
  if (!resolvedProvider?.profile || !qualification || qualification.model !== model) return false;
  try {
    return resolveProviderModel({ resolvedProvider, privatePolicy, qualification, model }) === model;
  } catch {
    return false;
  }
}

function classifiesRemote(request, privatePolicy) {
  if (request.dataClassification === "confidential" || request.dataClassification === "restricted") return false;
  const policyClass = CLASSIFICATION_TO_POLICY[request.dataClassification];
  return privatePolicy.dataPolicy.allowedClassifications.includes(policyClass);
}

function privatePolicyAccepts(request, privatePolicy) {
  return request.inputTokenEstimate <= privatePolicy.budgets.maxInputTokensPerRun
    && request.outputTokenEstimate <= privatePolicy.budgets.maxOutputTokensPerRun;
}

// providerQualified evaluates a candidate (local or remote) against its qualification envelope.
// Reasons are decision reason codes so callers can surface them directly.
export function providerQualified({ providerId, qualification, request, policy, now, privatePolicy = null }) {
  if (!qualification) return { qualified: false, reason: "remote-unqualified" };
  try {
    validateQualification(qualification, now);
  } catch {
    return { qualified: false, reason: "remote-unqualified" };
  }
  if (qualification.providerId !== providerId) return { qualified: false, reason: "remote-unqualified" };
  if (!isSemanticQualification(qualification)) return { qualified: false, reason: "remote-unqualified" };
  if (typeof qualification.model !== "string" || qualification.model.length < 1) return { qualified: false, reason: "remote-unqualified" };
  const resolved = resolveWorkProvider(providerId, { enabledRemoteProviders: providerId === "local" ? [] : [providerId] });
  if (!modelAgreement({ resolvedProvider: resolved, privatePolicy, qualification, model: qualification.model })) return { qualified: false, reason: "remote-unqualified" };
  if (!qualificationCurrent(qualification, now, policy.qualification.maxAgeSeconds)) return { qualified: false, reason: "remote-expired" };
  if (!policy.qualification.semanticTaskClasses.includes(request.taskClass)) return { qualified: false, reason: "task-class-not-attested" };
  if (!capabilityCovers(qualification, request)) return { qualified: false, reason: "envelope-incompatible" };
  const estimatedCost = estimatedCostMicros(qualification, request);
  if (estimatedCost > BigInt(request.costCeilingMicros)) return { qualified: false, reason: "cost-ceiling-exceeded" };
  return { qualified: true, reason: null, estimatedCost };
}

export function remoteAdmissible({ providerId, qualification, request, policy, now, privatePolicy }) {
  const qualified = providerQualified({ providerId, qualification, request, policy, now, privatePolicy });
  if (!qualified.qualified) return { admissible: false, reason: qualified.reason };
  if (!privatePolicy) return { admissible: false, reason: "remote-unqualified" };
  if (!classifiesRemote(request, privatePolicy)) return { admissible: false, reason: "classification-rejected" };
  if (!privatePolicyAccepts(request, privatePolicy)) return { admissible: false, reason: "envelope-incompatible" };
  if (qualified.estimatedCost > BigInt(privatePolicy.budgets.maxEstimatedCostMicrosPerRun)) return { admissible: false, reason: "cost-ceiling-exceeded" };
  return { admissible: true, reason: null };
}

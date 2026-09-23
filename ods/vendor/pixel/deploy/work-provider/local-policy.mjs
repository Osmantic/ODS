import { createHash } from "node:crypto";
import { canonical } from "../../scripts/lib/work-contract.mjs";

const BUDGET_FIELDS = ["maxRequestsPerRun", "maxInputTokensPerRun", "maxOutputTokensPerRun", "maxOutputTokensPerRequest", "maxNetworkBytesPerRun", "maxRequestSeconds", "maxEstimatedCostMicrosPerRun"];

export class WorkProviderLocalPolicyError extends Error {}
function fail(message) { throw new WorkProviderLocalPolicyError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export function defaultWorkProviderLocalPolicy(resolvedProvider) {
  const profile = resolvedProvider?.profile;
  if (!profile || profile.remote || profile.id !== "local") fail("default local policy requires the resolved local provider");
  return {
    providerId: "local",
    budgets: {
      maxRequestsPerRun: profile.budgets.maxRequestsPerRun,
      maxInputTokensPerRun: profile.budgets.maxInputTokensPerRun,
      maxOutputTokensPerRun: profile.budgets.maxOutputTokensPerRun,
      maxOutputTokensPerRequest: profile.budgets.maxOutputTokensPerRequest,
      maxNetworkBytesPerRun: 64 * 1024 * 1024,
      maxRequestSeconds: 120,
      maxEstimatedCostMicrosPerRun: 0,
    },
  };
}

export function bindWorkProviderLocalPolicy(resolvedProvider, policy) {
  const profile = resolvedProvider?.profile;
  if (!profile || profile.remote || profile.id !== "local") fail("local provider policy requires the resolved local provider");
  if (!policy || typeof policy !== "object" || Array.isArray(policy) || policy.providerId !== "local") fail("local provider policy is invalid");
  const budgets = policy.budgets;
  if (!budgets || typeof budgets !== "object" || Array.isArray(budgets)) fail("local provider policy budgets are invalid");
  for (const field of BUDGET_FIELDS) {
    const minimum = field === "maxEstimatedCostMicrosPerRun" ? 0 : 1;
    if (!Number.isSafeInteger(budgets[field]) || budgets[field] < minimum) fail(`local provider policy budget ${field} is invalid`);
  }
  if (budgets.maxRequestsPerRun > profile.budgets.maxRequestsPerRun) fail("local provider policy widens the request ceiling");
  if (budgets.maxInputTokensPerRun > profile.budgets.maxInputTokensPerRun) fail("local provider policy widens the input ceiling");
  if (budgets.maxOutputTokensPerRun > profile.budgets.maxOutputTokensPerRun) fail("local provider policy widens the output ceiling");
  if (budgets.maxOutputTokensPerRequest > profile.budgets.maxOutputTokensPerRequest) fail("local provider policy widens the per-request output ceiling");
  return deepFreeze(structuredClone(policy));
}

export function localPolicySha256(policy) { return sha(policy); }

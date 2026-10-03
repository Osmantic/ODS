import { canonical, validateWorkProviderPrivatePolicy } from "../../scripts/lib/work-contract.mjs";
import { validateOwnerPrivateModel } from "./provider-registry.mjs";

export class WorkProviderPrivatePolicyError extends Error {}
function fail(message) { throw new WorkProviderPrivatePolicyError(message); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export function bindWorkProviderPrivatePolicy(resolvedProvider, policy, { requireEnabled = false } = {}) {
  const errors = validateWorkProviderPrivatePolicy(policy);
  if (errors.length) fail(`private provider policy failed validation: ${errors.join("; ")}`);
  const profile = resolvedProvider?.profile;
  if (!profile?.remote || typeof resolvedProvider.profileSha256 !== "string") fail("private provider policy requires one resolved remote provider");
  if (policy.providerId !== profile.id) fail("private provider policy differs from the resolved provider");
  if (canonical(policy.transport.allowedHosts) !== canonical(profile.allowedHosts)) fail("private provider policy widens or changes the profile host allowlist");
  if (policy.budgets.maxRequestsPerRun > profile.budgets.maxRequestsPerRun
    || policy.budgets.maxInputTokensPerRun > profile.budgets.maxInputTokensPerRun
    || policy.budgets.maxOutputTokensPerRun > profile.budgets.maxOutputTokensPerRun) fail("private provider policy widens the public profile budget ceiling");
  if (requireEnabled && !policy.enabled) fail("private provider policy is paused");
  validateOwnerPrivateModel({ resolvedProvider, privatePolicy: policy });
  return deepFreeze(structuredClone(policy));
}

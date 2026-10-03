import { createHash } from "node:crypto";

import { canonical, validateWorkProviderPrivatePolicy, validateWorkProviderRouterPolicy } from "../../scripts/lib/work-contract.mjs";
import { bindWorkProviderPrivatePolicy, WorkProviderPrivatePolicyError } from "../work-provider/private-policy.mjs";
import { resolveWorkProvider } from "../work-provider/provider-registry.mjs";

const REMOTE_IDS = new Set(["anthropic", "fireworks", "groq", "moonshot-kimi", "openai", "openrouter", "together"]);

export class WorkProviderRouterPolicyError extends Error {}
function fail(message) { throw new WorkProviderRouterPolicyError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

function enabledMap(policy) {
  return new Map(policy.enabledRemoteProviders.map((entry) => [entry.providerId, entry.privatePolicySha256]));
}

export function bindWorkProviderRouterPolicy(policy) {
  const errors = validateWorkProviderRouterPolicy(policy);
  if (errors.length) fail(`owner-private router policy failed validation: ${errors.join("; ")}`);
  if (!policy.allowedModes.includes(policy.mode)) fail("router policy default mode is outside allowedModes");
  if (policy.preference[0] !== "local") fail("router policy preference must be local-first");
  if (policy.mode === "local-only") {
    if (policy.enabledRemoteProviders.length !== 0 || policy.preference.length !== 1 || policy.preference[0] !== "local") fail("local-only router policy may not enable or prefer a remote provider");
  }
  const enabled = enabledMap(policy);
  for (const providerId of enabled.keys()) {
    if (!policy.preference.includes(providerId)) fail(`enabled remote provider ${providerId} is absent from the router preference`);
  }
  return deepFreeze(Object.freeze({ policy: deepFreeze(structuredClone(policy)), policySha256: sha(policy) }));
}

export function bindRouterPrivatePolicies(policy, privatePolicies, { providerIds = null } = {}) {
  const { policy: boundPolicy } = bindWorkProviderRouterPolicy(policy);
  if (!privatePolicies || typeof privatePolicies !== "object" || Array.isArray(privatePolicies)) fail("enabled remote private policies must be an object keyed by provider id");
  const enabled = enabledMap(boundPolicy);
  const targets = providerIds === null ? [...enabled.keys()] : providerIds;
  if (!Array.isArray(targets) || new Set(targets).size !== targets.length
      || targets.some((providerId) => !enabled.has(providerId))) {
    fail("remote private-policy binding scope is invalid or outside the enabled owner policy");
  }
  const targetSet = new Set(targets);
  const bound = new Map();
  for (const providerId of targets) {
    const privatePolicy = privatePolicies[providerId];
    if (!privatePolicy) fail(`enabled remote provider ${providerId} has no private policy bound`);
    const errors = validateWorkProviderPrivatePolicy(privatePolicy);
    if (errors.length) fail(`enabled remote private policy for ${providerId} failed validation: ${errors.join("; ")}`);
    if (privatePolicy.providerId !== providerId) fail("enabled remote private policy differs from its provider id");
    const pinned = enabled.get(providerId);
    if (sha(privatePolicy) !== pinned) fail(`enabled remote private policy SHA for ${providerId} does not match the pinned owner policy`);
    // Reuse the standard private-policy binding so disabled policies, host changes, and
    // widened public budgets cannot route: requireEnabled forces an enabled private policy,
    // and the shared binding rejects host allowlist drift and budget widening past the profile.
    const resolved = resolveWorkProvider(providerId, { enabledRemoteProviders: [providerId] });
    let boundPolicyDoc;
    try {
      boundPolicyDoc = bindWorkProviderPrivatePolicy(resolved, privatePolicy, { requireEnabled: true });
    } catch (error) {
      if (error instanceof WorkProviderPrivatePolicyError) fail(error.message);
      throw error;
    }
    bound.set(providerId, deepFreeze(structuredClone(boundPolicyDoc)));
  }
  for (const providerId of Object.keys(privatePolicies)) {
    if (!targetSet.has(providerId)) fail(`private policy for ${providerId} is outside this exact routing decision and cannot enable it`);
  }
  return deepFreeze(bound);
}

export function isRemoteProviderId(id) { return REMOTE_IDS.has(id); }

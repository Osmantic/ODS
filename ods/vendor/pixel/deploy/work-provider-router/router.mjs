import { createHash } from "node:crypto";

import { canonical, validateWorkProviderRouterDecision, validateWorkProviderRouterRequest } from "../../scripts/lib/work-contract.mjs";
import { resolveWorkProvider } from "../work-provider/provider-registry.mjs";
import { bindRouterPrivatePolicies, bindWorkProviderRouterPolicy } from "./router-policy.mjs";
import { providerQualified, remoteAdmissible, validateQualification } from "./qualification.mjs";

// Every request sensitive category is treated as local/reject for this deferred-security
// release: credentials, private keys, auth material, session tokens, regulated records,
// unapproved owner data, proprietary code, PII, raw source bodies, and security findings.
const NEVER_EGRESS = new Set(["credentials", "private-keys", "auth-material", "session-tokens", "regulated-records", "unapproved-owner-data", "proprietary-code", "personally-identifiable", "raw-source-bodies", "security-findings"]);
const SUFFIX = /^[a-f0-9]{12}$/u;
const BOUNDARY = "Content-free local-first work-provider router decision. It binds the canonical request, policy, provider profile, private policy, and qualification hashes and exposes only reason codes and counters. A decision grants no execution, credential, network, merge, deploy, publish, external-message, security-testing, or rerouting authority; execution must later bind this exact decision and the router itself performs no call.";

export class WorkProviderRouterError extends Error {}
function fail(message) { throw new WorkProviderRouterError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}
function safeNow(now) { if (!(now instanceof Date) || Number.isNaN(now.getTime())) fail("provider router time is invalid"); return now; }
function validSuffix(suffix) { if (!SUFFIX.test(suffix ?? "")) fail("provider router suffix is invalid"); }

function computeCounters({ localQualified, remoteQualifiedCount, evaluatedCount, selected }) {
  const localQ = localQualified ? 1 : 0;
  const rejected = evaluatedCount - (selected ? 1 : 0);
  return {
    evaluatedProviders: evaluatedCount,
    localQualified: localQ,
    remoteQualified: remoteQualifiedCount,
    rejected,
  };
}

function decision({ now, suffix, mode, selectionKind, selectedProviderId, selectedModel, reasonCode, counters, requestSha256, policySha256, providerProfileSha256, privatePolicySha256, qualificationSha256 }) {
  validSuffix(suffix);
  const decisionDoc = {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-decision-v1.schema.json",
    schemaVersion: 1,
    decisionId: `workproviderdecision-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    recordedAt: iso(now),
    mode,
    selectionKind,
    selectedProviderId,
    selectedModel,
    reasonCode,
    counters,
    requestSha256,
    policySha256,
    providerProfileSha256,
    privatePolicySha256,
    qualificationSha256,
    authority: {
      execution: false, credential: false, network: false, merge: false, deploy: false,
      publish: false, externalMessages: false, securityTesting: false, reroutingOnUncertain: false,
    },
    boundary: BOUNDARY,
  };
  const errors = validateWorkProviderRouterDecision(decisionDoc);
  if (errors.length) fail(`provider router decision failed validation: ${errors.join("; ")}`);
  return deepFreeze(Object.freeze({ decision: decisionDoc, decisionSha256: sha(decisionDoc) }));
}

export function routeWorkProvider({ request, routerPolicy, enabledPrivatePolicies, qualifications = {}, now = new Date(), suffix }) {
  safeNow(now);
  const requestErrors = validateWorkProviderRouterRequest(request);
  if (requestErrors.length) fail(`provider router request failed validation: ${requestErrors.join("; ")}`);
  const { policy, policySha256 } = bindWorkProviderRouterPolicy(routerPolicy);

  const requestSha256 = sha(request);
  const mode = request.mode ?? policy.mode;
  if (request.mode && !policy.allowedModes.includes(request.mode)) {
    return decision({ now, suffix, mode, selectionKind: "reject", selectedProviderId: null, selectedModel: null, reasonCode: "policy-rejected",
      counters: { evaluatedProviders: 0, localQualified: 0, remoteQualified: 0, rejected: 0 },
      requestSha256, policySha256, providerProfileSha256: null, privatePolicySha256: null, qualificationSha256: null });
  }

  const forcedLocal = request.dataClassification === "confidential" || request.dataClassification === "restricted"
    || request.sensitiveCategories.some((category) => NEVER_EGRESS.has(category));

  // Bind remote private policies only when a remote can actually be selected. Local-only and
  // explicit-local operation never depend on a remote private-policy object being present.
  const remoteRequested = mode === "explicit-provider" ? request.explicitProviderId : null;
  let remoteCanBeSelected = false;
  if (!forcedLocal && mode !== "local-only") {
    if (mode === "explicit-provider") {
      remoteCanBeSelected = remoteRequested !== "local"
        && policy.enabledRemoteProviders.some((entry) => entry.providerId === remoteRequested);
    } else {
      remoteCanBeSelected = policy.enabledRemoteProviders.length > 0;
    }
  }
  const remoteCandidateIds = remoteCanBeSelected
    ? (mode === "explicit-provider"
      ? [remoteRequested]
      : policy.enabledRemoteProviders.map((entry) => entry.providerId))
    : [];
  const scopedPrivatePolicies = mode === "explicit-provider" && remoteCanBeSelected
    ? { [remoteRequested]: enabledPrivatePolicies?.[remoteRequested] }
    : enabledPrivatePolicies;
  const privatePolicies = remoteCanBeSelected
    ? bindRouterPrivatePolicies(policy, scopedPrivatePolicies, { providerIds: remoteCandidateIds })
    : null;

  // Local capability selection reuses the full qualification envelope: task class, context,
  // tools, vision, tokens, cost, expiry, and semantic evidence. No unqualified local claim.
  const local = resolveWorkProvider("local");
  const localQualifiedResult = providerQualified({ providerId: "local", qualification: qualifications["local"], request, policy, now });
  const localQualifiedValue = localQualifiedResult.qualified;

  const remoteAdmissibleIds = new Set();
  if (privatePolicies) {
    for (const providerId of remoteCandidateIds) {
      const result = remoteAdmissible({
        providerId,
        qualification: qualifications[providerId],
        request,
        policy,
        now,
        privatePolicy: privatePolicies.get(providerId),
      });
      if (result.admissible) remoteAdmissibleIds.add(providerId);
    }
  }

  const evaluatedCount = 1 + remoteCandidateIds.length;
  const remoteAdmissibleCount = remoteAdmissibleIds.size;

  const selectLocal = (forced) => {
    if (!localQualifiedValue) {
      return decision({ now, suffix, mode, selectionKind: "reject", selectedProviderId: null, selectedModel: null, reasonCode: "no-local-provider",
        counters: computeCounters({ localQualified: false, remoteQualifiedCount: remoteAdmissibleCount, evaluatedCount, selected: null }),
        requestSha256, policySha256, providerProfileSha256: null, privatePolicySha256: null, qualificationSha256: null });
    }
    return decision({ now, suffix, mode, selectionKind: "local", selectedProviderId: "local", selectedModel: qualifications["local"].model, reasonCode: forced ? "local-required" : "local-selected",
      counters: computeCounters({ localQualified: true, remoteQualifiedCount: remoteAdmissibleCount, evaluatedCount, selected: "local" }),
      requestSha256, policySha256, providerProfileSha256: local.profileSha256, privatePolicySha256: null,
      qualificationSha256: sha(qualifications["local"]) });
  };

  const reject = (reasonCode) => decision({ now, suffix, mode, selectionKind: "reject", selectedProviderId: null, selectedModel: null, reasonCode,
    counters: computeCounters({ localQualified: localQualifiedValue, remoteQualifiedCount: remoteAdmissibleCount, evaluatedCount, selected: null }),
    requestSha256, policySha256, providerProfileSha256: null, privatePolicySha256: null, qualificationSha256: null });

  const selectRemote = (providerId) => {
    const profile = resolveWorkProvider(providerId, { enabledRemoteProviders: [providerId] });
    const privatePolicy = privatePolicies.get(providerId);
    const qualification = qualifications[providerId];
    return decision({ now, suffix, mode, selectionKind: "remote", selectedProviderId: providerId, selectedModel: qualification.model, reasonCode: "remote-selected",
      counters: computeCounters({ localQualified: localQualifiedValue, remoteQualifiedCount: remoteAdmissibleCount, evaluatedCount, selected: providerId }),
      requestSha256, policySha256, providerProfileSha256: profile.profileSha256,
      privatePolicySha256: sha(privatePolicy), qualificationSha256: qualification ? sha(qualification) : null });
  };

  if (mode === "local-only") {
    return selectLocal(forcedLocal);
  }

  if (mode === "explicit-provider") {
    const requested = remoteRequested;
    if (requested === "local") return selectLocal(forcedLocal);
    if (forcedLocal) return reject("sensitive-data-forced-local");
    if (!policy.enabledRemoteProviders.some((entry) => entry.providerId === requested)) return reject("remote-not-enabled");
    const result = remoteAdmissible({
      providerId: requested,
      qualification: qualifications[requested],
      request,
      policy,
      now,
      privatePolicy: privatePolicies.get(requested),
    });
    if (!result.admissible) return reject(result.reason ?? "remote-unqualified");
    return decision({ now, suffix, mode, selectionKind: "remote", selectedProviderId: requested, selectedModel: qualifications[requested].model, reasonCode: "requested-provider-selected",
      counters: computeCounters({ localQualified: localQualifiedValue, remoteQualifiedCount: remoteAdmissibleCount, evaluatedCount, selected: requested }),
      requestSha256, policySha256, providerProfileSha256: resolveWorkProvider(requested, { enabledRemoteProviders: [requested] }).profileSha256,
      privatePolicySha256: sha(privatePolicies.get(requested)), qualificationSha256: sha(qualifications[requested]) });
  }

  // policy-router: local-first, deterministic preference order, no consensus or random scoring.
  if (forcedLocal) {
    return localQualifiedValue ? selectLocal(true) : reject("sensitive-data-forced-local");
  }
  if (localQualifiedValue) return selectLocal(false);
  for (const providerId of policy.preference) {
    if (providerId === "local") continue;
    if (!policy.enabledRemoteProviders.some((entry) => entry.providerId === providerId)) continue;
    if (!remoteAdmissibleIds.has(providerId)) continue;
    return selectRemote(providerId);
  }
  return reject(localQualifiedValue ? "remote-unqualified" : "local-incapable");
}

export function validateRouterRequest(request) {
  const errors = validateWorkProviderRouterRequest(request);
  if (errors.length) fail(`provider router request failed validation: ${errors.join("; ")}`);
  return deepFreeze(structuredClone(request));
}
export function validateRouterQualification(qualification, now) { return validateQualification(qualification, now); }

export function replayWorkProviderDecision({ decision: expected, request, routerPolicy, enabledPrivatePolicies, qualifications }) {
  const decisionErrors = validateWorkProviderRouterDecision(expected);
  if (decisionErrors.length) fail(`provider router replay decision failed validation: ${decisionErrors.join("; ")}`);
  const recordedAt = new Date(expected.recordedAt);
  if (Number.isNaN(recordedAt.getTime())) fail("provider router replay decision time is invalid");
  const suffix = expected.decisionId.slice(-12);
  const replayed = routeWorkProvider({ request, routerPolicy, enabledPrivatePolicies, qualifications, now: recordedAt, suffix });
  if (replayed.decisionSha256 !== sha(expected)) fail("provider router decision is not the deterministic result of its exact routing inputs");
  return replayed;
}

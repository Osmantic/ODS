import assert from "node:assert/strict";
import test from "node:test";

import { routeWorkProvider, WorkProviderRouterError } from "../deploy/work-provider-router/router.mjs";
import { WorkProviderRouterPolicyError } from "../deploy/work-provider-router/router-policy.mjs";
import { validateWorkProviderRouterDecision } from "../scripts/lib/work-contract.mjs";
import { makeLocalQualification, makePrivatePolicy, makeQualification, makeRequest, makeRouterPolicy, sha } from "./fixtures/work-provider-router.mjs";

const NOW = new Date("2026-08-21T12:00:00Z");
const SUFFIX = "abcdef123456";
const localBudget = { input: 5000000, output: 1000000 };

function route({ request, policy = null, privatePolicies = null, qualifications = null } = {}) {
  const routerPolicy = policy ?? makeRouterPolicy();
  const enabledPrivate = privatePolicies ?? { "moonshot-kimi": makePrivatePolicy("moonshot-kimi") };
  const quals = qualifications ?? { local: makeLocalQualification(), "moonshot-kimi": makeQualification("moonshot-kimi") };
  return routeWorkProvider({ request: request ?? makeRequest(), routerPolicy, enabledPrivatePolicies: enabledPrivate, qualifications: quals, now: NOW, suffix: SUFFIX });
}

test("local-only selects only the local provider", () => {
  const { decision } = route({ request: makeRequest({ mode: "local-only" }) });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.selectedProviderId, "local");
  assert.equal(decision.reasonCode, "local-selected");
  assert.equal(decision.mode, "local-only");
  assert.equal(decision.privatePolicySha256, null);
  assert.equal(decision.qualificationSha256, sha(makeLocalQualification()));
});

test("local-only with sensitive data still selects local and reports local-required", () => {
  const { decision } = route({ request: makeRequest({ mode: "local-only", sensitiveCategories: ["credentials"] }) });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.selectedProviderId, "local");
  assert.equal(decision.reasonCode, "local-required");
});

test("local-only is functional without any remote private-policy object", () => {
  const { decision } = routeWorkProvider({
    request: makeRequest({ mode: "local-only" }),
    routerPolicy: makeRouterPolicy({ mode: "local-only", allowedModes: ["local-only"], enabled: [], preference: ["local"] }),
    enabledPrivatePolicies: undefined,
    qualifications: { local: makeLocalQualification() },
    now: NOW, suffix: SUFFIX,
  });
  assert.equal(decision.selectionKind, "local");
});

test("explicit-provider local is functional without any remote private-policy object", () => {
  const { decision } = routeWorkProvider({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "local" }),
    routerPolicy: makeRouterPolicy(),
    enabledPrivatePolicies: undefined,
    qualifications: { local: makeLocalQualification() },
    now: NOW, suffix: SUFFIX,
  });
  assert.equal(decision.selectionKind, "local");
});

test("local-only rejects when local cannot meet the token envelope", () => {
  const { decision } = route({ request: makeRequest({ mode: "local-only", inputTokenEstimate: localBudget.input + 1 }) });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.selectedProviderId, null);
  assert.equal(decision.reasonCode, "no-local-provider");
});

test("local-only never claims local without a current semantic local qualification", () => {
  const { decision } = route({ request: makeRequest({ mode: "local-only" }), qualifications: {} });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "no-local-provider");
});

test("a connectivity-smoke local qualification never qualifies local", () => {
  const smoke = makeLocalQualification({ attestationKind: "connectivity-smoke" });
  const { decision } = route({ request: makeRequest({ mode: "local-only" }), qualifications: { local: smoke } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "no-local-provider");
});

test("local-only respects the full local capability envelope", () => {
  const noVision = makeLocalQualification({ capability: { visionNeed: false } });
  const { decision } = route({ request: makeRequest({ mode: "local-only", visionNeed: true }), qualifications: { local: noVision } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "no-local-provider");
});

test("explicit-provider selects exactly the requested remote provider", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }) });
  assert.equal(decision.selectionKind, "remote");
  assert.equal(decision.selectedProviderId, "moonshot-kimi");
  assert.equal(decision.reasonCode, "requested-provider-selected");
  assert.match(decision.providerProfileSha256, /^[a-f0-9]{64}$/u);
  assert.match(decision.privatePolicySha256, /^[a-f0-9]{64}$/u);
  assert.equal(decision.qualificationSha256, sha(makeQualification("moonshot-kimi")));
});

test("explicit-provider can select local", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "local" }) });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.selectedProviderId, "local");
});

test("explicit-provider rejects a remote that is not enabled (no fallback)", () => {
  const { decision } = route({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    policy: makeRouterPolicy({ enabled: [] }),
    privatePolicies: {},
    qualifications: {},
  });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-not-enabled");
});

test("explicit-provider rejects a remote with no qualification (fails closed)", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), qualifications: {} });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-unqualified");
});

test("explicit-provider rejects a semantic qualification for a model other than the pinned remote profile model", () => {
  const wrongModel = makeQualification("moonshot-kimi", { model: "kimi-k3-unpinned" });
  const { decision } = route({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    qualifications: { "moonshot-kimi": wrongModel },
  });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-unqualified");
});

test("explicit-provider rejects an expired qualification", () => {
  const expired = makeQualification("moonshot-kimi", { attestedAt: "2026-08-20T00:00:00Z", expiresAt: "2026-08-20T12:00:00Z" });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), qualifications: { "moonshot-kimi": expired } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-expired");
});

test("connectivity-smoke is never semantic task qualification", () => {
  const smoke = makeQualification("moonshot-kimi", { attestationKind: "connectivity-smoke" });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), qualifications: { "moonshot-kimi": smoke } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-unqualified");
});

test("sensitive data forces explicit-provider to reject a remote", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", sensitiveCategories: ["session-tokens"] }) });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "sensitive-data-forced-local");
});

test("every sensitive category forces local/reject and never routes remote", () => {
  const categories = ["credentials", "private-keys", "auth-material", "session-tokens", "regulated-records", "proprietary-code", "personally-identifiable", "unapproved-owner-data", "raw-source-bodies", "security-findings"];
  for (const category of categories) {
    const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", sensitiveCategories: [category] }) });
    assert.equal(decision.selectionKind, "reject", category);
    assert.equal(decision.reasonCode, "sensitive-data-forced-local", category);
  }
});

test("confidential and restricted data are forced to local/reject in every mode", () => {
  const { decision: conf } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", dataClassification: "confidential" }) });
  assert.equal(conf.selectionKind, "reject");
  assert.equal(conf.reasonCode, "sensitive-data-forced-local");
  const { decision: restricted } = route({ request: makeRequest({ dataClassification: "restricted" }) });
  assert.equal(restricted.selectionKind, "local");
  assert.equal(restricted.reasonCode, "local-required");
});

test("public data routes remote only when the private policy allows public", () => {
  const pp = makePrivatePolicy("moonshot-kimi");
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", dataClassification: "public" }), policy: makeRouterPolicy({ privatePolicy: pp }), privatePolicies: { "moonshot-kimi": pp } });
  assert.equal(decision.selectionKind, "remote");
});

test("internal data requires internal-source in the private policy", () => {
  const pp = makePrivatePolicy("moonshot-kimi", { dataPolicy: { ...makePrivatePolicy("moonshot-kimi").dataPolicy, allowedClassifications: ["public"] } });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", dataClassification: "internal" }), policy: makeRouterPolicy({ privatePolicy: pp }), privatePolicies: { "moonshot-kimi": pp } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "classification-rejected");
});

test("explicit-provider never falls back to another provider", () => {
  const otherEnabled = makeRouterPolicy({ providerId: "openai", enabled: [{ providerId: "openai", privatePolicySha256: sha(makePrivatePolicy("openai")) }] });
  const { decision } = route({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    policy: otherEnabled,
    privatePolicies: { "openai": makePrivatePolicy("openai") },
    qualifications: { "openai": makeQualification("openai") },
  });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-not-enabled");
});

test("explicit-provider binds and evaluates only the requested remote", () => {
  const moonshot = makePrivatePolicy("moonshot-kimi");
  const unusedOpenAi = makePrivatePolicy("openai");
  const policy = makeRouterPolicy({
    enabled: [
      { providerId: "moonshot-kimi", privatePolicySha256: sha(moonshot) },
      { providerId: "openai", privatePolicySha256: sha(unusedOpenAi) },
    ],
    preference: ["local", "moonshot-kimi", "openai"],
  });
  const { decision } = route({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    policy,
    privatePolicies: { "moonshot-kimi": moonshot },
  });
  assert.equal(decision.selectionKind, "remote");
  assert.equal(decision.selectedProviderId, "moonshot-kimi");
  assert.equal(decision.counters.evaluatedProviders, 2);
});

test("explicit-provider rejects a request whose estimated cost exceeds the request ceiling even when the provider cap is high", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", costCeilingMicros: 1000 }) });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "cost-ceiling-exceeded");
});

test("explicit-provider rejects an estimated cost above the owner private-policy per-run cap", () => {
  const pp = makePrivatePolicy("moonshot-kimi", { budgets: { ...makePrivatePolicy("moonshot-kimi").budgets, maxEstimatedCostMicrosPerRun: 100 } });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), policy: makeRouterPolicy({ privatePolicy: pp }), privatePolicies: { "moonshot-kimi": pp } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "cost-ceiling-exceeded");
});

test("explicit-provider rejects an incompatible capability envelope", () => {
  const noVision = makeQualification("moonshot-kimi", { capability: { ...makeQualification("moonshot-kimi").capability, visionNeed: false } });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", visionNeed: true }), qualifications: { "moonshot-kimi": noVision } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "envelope-incompatible");
});

test("policy semanticTaskClasses gates explicit-provider selection", () => {
  const policy = makeRouterPolicy({ qualification: { required: true, maxAgeSeconds: 86400, semanticTaskClasses: ["patch-proposal"] } });
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", taskClass: "structural-review" }), policy, privatePolicies: { "moonshot-kimi": makePrivatePolicy("moonshot-kimi") } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "task-class-not-attested");
});

test("a qualification bound under the wrong provider id is rejected", () => {
  const q = makeQualification("openai");
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), qualifications: { "moonshot-kimi": q } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-unqualified");
});

test("a decision binds the verified qualification hash for remote and local", () => {
  const q = makeQualification("moonshot-kimi");
  const lq = makeLocalQualification();
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), qualifications: { local: lq, "moonshot-kimi": q } });
  assert.equal(decision.qualificationSha256, sha(q));
  const { decision: localDecision } = route({ request: makeRequest({ mode: "local-only" }), qualifications: { local: lq, "moonshot-kimi": q } });
  assert.equal(localDecision.qualificationSha256, sha(lq));
});

test("a disabled remote private policy cannot route even when pinned", () => {
  const disabled = makePrivatePolicy("moonshot-kimi", { enabled: false });
  assert.throws(() => routeWorkProvider({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    routerPolicy: makeRouterPolicy({ privatePolicy: disabled }),
    enabledPrivatePolicies: { "moonshot-kimi": disabled },
    qualifications: { "moonshot-kimi": makeQualification("moonshot-kimi") },
    now: NOW, suffix: SUFFIX,
  }), WorkProviderRouterPolicyError);
});

test("a private policy that changes the egress host cannot route even when pinned", () => {
  const bad = makePrivatePolicy("moonshot-kimi", { transport: { ...makePrivatePolicy("moonshot-kimi").transport, allowedHosts: ["evil.example.com:443"] } });
  assert.throws(() => routeWorkProvider({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    routerPolicy: makeRouterPolicy({ privatePolicy: bad }),
    enabledPrivatePolicies: { "moonshot-kimi": bad },
    qualifications: { "moonshot-kimi": makeQualification("moonshot-kimi") },
    now: NOW, suffix: SUFFIX,
  }), WorkProviderRouterPolicyError);
});

test("a private policy that widens the public budget cannot route", () => {
  const wide = makePrivatePolicy("moonshot-kimi", { budgets: { ...makePrivatePolicy("moonshot-kimi").budgets, maxInputTokensPerRun: 5000000 } });
  assert.throws(() => routeWorkProvider({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    routerPolicy: makeRouterPolicy({ privatePolicy: wide }),
    enabledPrivatePolicies: { "moonshot-kimi": wide },
    qualifications: { "moonshot-kimi": makeQualification("moonshot-kimi") },
    now: NOW, suffix: SUFFIX,
  }), WorkProviderRouterPolicyError);
});

test("duplicate enabled remote provider ids are rejected even with different hashes", () => {
  const pp1 = makePrivatePolicy("moonshot-kimi");
  const pp2 = makePrivatePolicy("moonshot-kimi", { budgets: { ...pp1.budgets, maxRequestsPerRun: 5 } });
  const policy = makeRouterPolicy({ enabled: [{ providerId: "moonshot-kimi", privatePolicySha256: sha(pp1) }, { providerId: "moonshot-kimi", privatePolicySha256: sha(pp2) }] });
  assert.throws(() => routeWorkProvider({
    request: makeRequest(),
    routerPolicy: policy,
    enabledPrivatePolicies: { "moonshot-kimi": pp1 },
    qualifications: { local: makeLocalQualification(), "moonshot-kimi": makeQualification("moonshot-kimi") },
    now: NOW, suffix: SUFFIX,
  }), WorkProviderRouterPolicyError);
});

test("policy-router is local-first and never selects a remote when local qualifies", () => {
  const { decision } = route({ request: makeRequest() });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.selectedProviderId, "local");
  assert.equal(decision.reasonCode, "local-selected");
});

test("policy-router falls back to a remote only when local cannot meet the envelope", () => {
  const localNoVision = makeLocalQualification({ capability: { visionNeed: false } });
  const { decision } = route({ request: makeRequest({ visionNeed: true }), qualifications: { local: localNoVision, "moonshot-kimi": makeQualification("moonshot-kimi") } });
  assert.equal(decision.selectionKind, "remote");
  assert.equal(decision.selectedProviderId, "moonshot-kimi");
  assert.equal(decision.reasonCode, "remote-selected");
});

test("policy-router is deterministic with no random scoring", () => {
  const request = makeRequest({ visionNeed: true });
  const quals = { local: makeLocalQualification({ capability: { visionNeed: false } }), "moonshot-kimi": makeQualification("moonshot-kimi") };
  const first = route({ request, qualifications: quals });
  const second = route({ request, qualifications: quals });
  assert.equal(first.decisionSha256, second.decisionSha256);
  assert.equal(first.decision.selectionKind, "remote");
});

test("policy-router rejects rather than falling back to an unenabled or unqualified remote", () => {
  const { decision } = route({
    request: makeRequest({ inputTokenEstimate: localBudget.input + 1000000 }),
    policy: makeRouterPolicy({ enabled: [] }),
    privatePolicies: {},
    qualifications: {},
  });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "local-incapable");
});

test("policy-router with sensitive data forces local and never selects a remote", () => {
  const { decision } = route({ request: makeRequest({ sensitiveCategories: ["regulated-records"] }) });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.selectedProviderId, "local");
  assert.equal(decision.reasonCode, "local-required");
});

test("policy-router with sensitive data and no capable local rejects instead of going remote", () => {
  const { decision } = route({ request: makeRequest({ sensitiveCategories: ["private-keys"], inputTokenEstimate: localBudget.input + 1000000 }) });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "sensitive-data-forced-local");
});

test("remote providers are disabled by default and cannot be selected silently", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), policy: makeRouterPolicy({ enabled: [] }), privatePolicies: {}, qualifications: {} });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "remote-not-enabled");
});

test("decision counters count evaluated (including rejected) candidates truthfully", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }) });
  assert.equal(decision.selectionKind, "remote");
  assert.equal(decision.counters.evaluatedProviders, 2);
  assert.equal(decision.counters.localQualified, 1);
  assert.equal(decision.counters.remoteQualified, 1);
  assert.equal(decision.counters.rejected, 1);
});

test("decision counters include rejected candidates, not only admissible ones", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), policy: makeRouterPolicy({ enabled: [] }), privatePolicies: {}, qualifications: { local: makeLocalQualification() } });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.counters.evaluatedProviders, 1);
  assert.equal(decision.counters.localQualified, 1);
  assert.equal(decision.counters.rejected, 1);
});

test("policy-router counters count an admissible-but-unselected remote as evaluated and rejected", () => {
  const { decision } = route({ request: makeRequest() });
  assert.equal(decision.selectionKind, "local");
  assert.equal(decision.counters.evaluatedProviders, 2);
  assert.equal(decision.counters.remoteQualified, 1);
  assert.equal(decision.counters.rejected, 1);
});

test("a routing decision binds canonical hashes and grants no authority", () => {
  const request = makeRequest();
  const { decision, decisionSha256 } = route({ request });
  assert.equal(decision.requestSha256, sha(request));
  assert.equal(decision.requestSha256, sha(makeRequest()));
  assert.equal(decision.authority.execution, false);
  assert.equal(decision.authority.credential, false);
  assert.equal(decision.authority.network, false);
  assert.equal(decision.authority.merge, false);
  assert.equal(decision.authority.deploy, false);
  assert.equal(decision.authority.publish, false);
  assert.equal(decision.authority.externalMessages, false);
  assert.equal(decision.authority.securityTesting, false);
  assert.equal(decision.authority.reroutingOnUncertain, false);
  assert.deepEqual(validateWorkProviderRouterDecision(decision), []);
  assert.match(decisionSha256, /^[a-f0-9]{64}$/u);
});

function hasKey(value, key) {
  if (Array.isArray(value)) return value.some((item) => hasKey(item, key));
  if (value !== null && typeof value === "object") {
    if (Object.prototype.hasOwnProperty.call(value, key)) return true;
    return Object.values(value).some((item) => hasKey(item, key));
  }
  return false;
}

test("a routing decision is content-free", () => {
  const { decision } = route({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }) });
  const serialized = JSON.stringify(decision);
  for (const key of ["prompt", "response", "reasoning", "toolArguments", "objective", "acceptanceCriteria"]) {
    assert.equal(hasKey(decision, key), false, `decision must not carry a ${key} field`);
  }
  for (const forbidden of ["api.moonshot.ai", "sk-", "provider-key", "reasoning_content", "BEGIN PRIVATE KEY", "PIXEL_K3_SMOKE_OK", "secret"]) {
    assert.equal(serialized.toLowerCase().includes(forbidden), false, `decision must not contain ${forbidden}`);
  }
});

test("cost comparisons are overflow-safe at the schema maximum", () => {
  const request = makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi", inputTokenEstimate: 100000000, outputTokenEstimate: 10000000, costCeilingMicros: 1000000000 });
  const q = makeQualification("moonshot-kimi", {
    pricing: { inputMicrosPerMillionTokens: 1000000000, outputMicrosPerMillionTokens: 1000000000, fixedMicrosPerRun: 1000000000 },
    capability: { ...makeQualification("moonshot-kimi").capability, inputTokenMax: 100000000, outputTokenMax: 10000000, costMicrosPerRunMax: 1000000000 },
  });
  const { decision: over } = route({ request, qualifications: { local: makeLocalQualification(), "moonshot-kimi": q } });
  assert.equal(over.selectionKind, "reject");
  assert.equal(over.reasonCode, "cost-ceiling-exceeded");
});

test("a request mode outside the allowed modes is rejected by the policy", () => {
  const { decision } = route({
    request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }),
    policy: makeRouterPolicy({ allowedModes: ["local-only", "policy-router"] }),
  });
  assert.equal(decision.selectionKind, "reject");
  assert.equal(decision.reasonCode, "policy-rejected");
});

test("a malformed request fails closed rather than routing", () => {
  assert.throws(() => route({ request: makeRequest({ extraField: true }) }), WorkProviderRouterError);
});

test("a remote private policy whose SHA differs from the pinned owner value is rejected", () => {
  const pinned = makeRouterPolicy();
  const tampered = { ...makePrivatePolicy("moonshot-kimi"), budgets: { ...makePrivatePolicy("moonshot-kimi").budgets, maxEstimatedCostMicrosPerRun: 1 } };
  assert.throws(() => routeWorkProvider({ request: makeRequest({ mode: "explicit-provider", explicitProviderId: "moonshot-kimi" }), routerPolicy: pinned, enabledPrivatePolicies: { "moonshot-kimi": tampered }, qualifications: { "moonshot-kimi": makeQualification("moonshot-kimi") }, now: NOW, suffix: SUFFIX }), (error) => error instanceof WorkProviderRouterPolicyError || error instanceof WorkProviderRouterError);
});

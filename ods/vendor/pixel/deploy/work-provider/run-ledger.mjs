import { createHash } from "node:crypto";

import { canonical, validateWorkProviderRunLedger, validateWorkProviderRouterDecision } from "../../scripts/lib/work-contract.mjs";
import { replayWorkProviderDecision } from "../work-provider-router/router.mjs";
import { bindWorkProviderPrivatePolicy } from "./private-policy.mjs";
import { bindWorkProviderLocalPolicy, defaultWorkProviderLocalPolicy } from "./local-policy.mjs";
import { resolveProviderModel, WorkProviderRegistryError } from "./provider-registry.mjs";
import { laneModelFor, QUALIFICATION_LANES } from "./neutral-corpus.mjs";

const RUN_SUFFIX = /^[a-f0-9]{12}$/u;
const SHA = /^[a-f0-9]{64}$/u;
const DECISION_ID = /^workproviderdecision-[0-9]{13}-[a-f0-9]{12}$/u;
const SEMANTIC_BINDING_FIELDS = ["decisionId", "decisionSha256", "requestSha256", "policySha256", "providerProfileSha256", "privatePolicySha256", "qualificationSha256", "model"];
const SEMANTIC_ONLY_BINDING_FIELDS = ["decisionId", "decisionSha256", "policySha256", "providerProfileSha256", "privatePolicySha256", "qualificationSha256"];
const BOUNDARY = "Owner-private content-free provider usage and idempotency ledger. It records exact input hashes, claims, terminal state, provider identifiers, usage, and cost without prompt, response, reasoning, tool arguments, credential, external-effect, deployment, publication, completion, or security-testing authority; uncertain outcomes must be reconciled and are never automatically retried. A semantic run binds one exact deterministically replayed router decision; a connectivity-smoke run is bounded by an explicit non-semantic purpose and cannot qualify semantics; a qualification-trial run binds one exact versioned neutral corpus and lane, exercises only exact corpus cases, and can qualify semantics only when every required case passes.";

export class WorkProviderRunLedgerError extends Error {}
function fail(message) { throw new WorkProviderRunLedgerError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function validNow(now) { if (!(now instanceof Date) || Number.isNaN(now.getTime())) fail("provider ledger time is invalid"); return now; }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}
function totals(requests) {
  return {
    requests: requests.length,
    inputTokens: requests.reduce((sum, request) => sum + (request.inputTokens ?? 0), 0),
    outputTokens: requests.reduce((sum, request) => sum + (request.outputTokens ?? 0), 0),
    reportedCostMicros: requests.reduce((sum, request) => sum + (request.reportedCostMicros ?? 0), 0),
    uncertain: requests.filter((request) => request.state === "uncertain").length,
  };
}
function checked(value) {
  const errors = validateWorkProviderRunLedger(value);
  if (errors.length) fail(`provider run ledger failed validation: ${errors.join("; ")}`);
  return deepFreeze(value);
}
function copy(ledger) { return structuredClone(ledger); }
function requestIndex(ledger, idempotencyKey) {
  if (!SHA.test(idempotencyKey ?? "")) fail("provider idempotency key is invalid");
  const index = ledger.requests.findIndex((request) => request.idempotencyKey === idempotencyKey);
  if (index < 0) fail("provider idempotency key was not claimed");
  return index;
}
function updated(ledger, requests, now, status = null) {
  const computed = totals(requests);
  const nextStatus = computed.uncertain > 0 ? "uncertain" : status ?? ledger.status;
  return checked({ ...copy(ledger), updatedAt: iso(validNow(now)), status: nextStatus, requests, totals: computed });
}

function bindRunPolicy(resolvedProvider, policy) {
  if (resolvedProvider?.profile?.remote) return bindWorkProviderPrivatePolicy(resolvedProvider, policy, { requireEnabled: true });
  return bindWorkProviderLocalPolicy(resolvedProvider, policy ?? defaultWorkProviderLocalPolicy(resolvedProvider));
}

function validModel(model) {
  if (typeof model !== "string" || model.length < 1 || model.length > 256 || /[\r\n\u0000]/u.test(model)) fail("provider run model is invalid");
  return model;
}

function validResponseModel(responseModel, bindingKind, ledgerModel) {
  if (responseModel === null) {
    if (bindingKind === "semantic" || bindingKind === "qualification-trial") fail("successful semantic or qualification settlement requires an exact response model");
    return null;
  }
  if (typeof responseModel !== "string" || responseModel.length < 1 || responseModel.length > 256 || /[\r\n\u0000]/u.test(responseModel)) fail("provider response model is invalid");
  if (responseModel !== ledgerModel) fail("provider response model differs from the pinned run model");
  return responseModel;
}

function checkedProviderModel(resolvedProvider, privatePolicy, qualification, model) {
  try { return resolveProviderModel({ resolvedProvider, privatePolicy, qualification, model }); }
  catch (error) { if (error instanceof WorkProviderRegistryError) fail(error.message); throw error; }
}

export function validateWorkProviderRunBinding(binding, { resolvedProvider } = {}) {
  if (!binding || typeof binding !== "object" || Array.isArray(binding)) fail("provider run binding is invalid");
  if (!["semantic", "connectivity-smoke", "qualification-trial"].includes(binding.kind)) fail("provider run binding kind is invalid");
  if (binding.kind === "semantic") {
    for (const field of SEMANTIC_BINDING_FIELDS) {
      if (binding[field] === undefined) fail(`semantic provider run binding is incomplete (missing ${field})`);
      if (field === "privatePolicySha256" && binding[field] === null) continue;
      if (binding[field] === null) fail(`semantic provider run binding is incomplete (missing ${field})`);
    }
    if (!DECISION_ID.test(binding.decisionId)) fail("provider run decision id is invalid");
    if (!SHA.test(binding.decisionSha256) || !SHA.test(binding.requestSha256) || !SHA.test(binding.policySha256)
      || !SHA.test(binding.providerProfileSha256) || !SHA.test(binding.qualificationSha256)) fail("provider run semantic binding hash is invalid");
    if (binding.privatePolicySha256 !== null && !SHA.test(binding.privatePolicySha256)) fail("provider run private policy hash is invalid");
    validModel(binding.model);
    if (resolvedProvider?.profile?.remote && binding.privatePolicySha256 === null) fail("remote semantic provider run requires a bound private policy");
    if (resolvedProvider && !resolvedProvider.profile.remote && binding.privatePolicySha256 !== null) fail("local semantic provider run cannot bind a remote private policy");
  } else if (binding.kind === "connectivity-smoke") {
    if (typeof binding.purpose !== "string" || binding.purpose.length < 1 || binding.purpose.length > 256 || /[\r\n\u0000]/u.test(binding.purpose)) fail("provider run smoke purpose is invalid");
    if (!SHA.test(binding.requestSha256)) fail("provider run smoke request hash is invalid");
    validModel(binding.model);
    for (const field of SEMANTIC_ONLY_BINDING_FIELDS) {
      if (binding[field] !== undefined) fail(`connectivity-smoke provider run cannot carry the semantic field ${field}`);
    }
  } else if (binding.kind === "qualification-trial") {
    if (typeof binding.corpusVersion !== "string" || binding.corpusVersion.length < 1 || binding.corpusVersion.length > 32 || /[\r\n\u0000]/u.test(binding.corpusVersion)) fail("provider run qualification-trial corpus version is invalid");
    if (!SHA.test(binding.corpusSha256)) fail("provider run qualification-trial corpus hash is invalid");
    if (!QUALIFICATION_LANES.includes(binding.lane)) fail("provider run qualification-trial lane is invalid");
    if (!SHA.test(binding.providerProfileSha256)) fail("provider run qualification-trial provider profile hash is invalid");
    if (binding.privatePolicySha256 !== null && !SHA.test(binding.privatePolicySha256)) fail("provider run qualification-trial private policy hash is invalid");
    validModel(binding.model);
    if (resolvedProvider?.profile?.remote && binding.privatePolicySha256 === null) fail("remote qualification-trial provider run requires a bound private policy");
    if (resolvedProvider && !resolvedProvider.profile.remote && binding.privatePolicySha256 !== null) fail("local qualification-trial provider run cannot bind a remote private policy");
    for (const field of ["decisionId", "decisionSha256", "requestSha256", "policySha256", "qualificationSha256", "purpose"]) {
      if (binding[field] !== undefined) fail(`qualification-trial provider run cannot carry the semantic or smoke field ${field}`);
    }
  }
  return deepFreeze(structuredClone(binding));
}

export function createConnectivitySmokeBinding({ purpose, requestSha256, model }) {
  if (!/^[a-z][a-z0-9-]*-connectivity-smoke-v1$/u.test(purpose ?? "")) fail("provider connectivity-smoke purpose is not in the closed registry");
  const binding = { kind: "connectivity-smoke", purpose, requestSha256, model };
  return validateWorkProviderRunBinding(binding);
}

export function createQualificationTrialBinding({ corpusVersion, corpusSha256, lane, resolvedProvider, privatePolicy }) {
  if (!QUALIFICATION_LANES.includes(lane)) fail("provider qualification-trial lane is not in the closed registry");
  const binding = {
    kind: "qualification-trial",
    corpusVersion,
    corpusSha256,
    lane,
    providerProfileSha256: resolvedProvider.profileSha256,
    privatePolicySha256: resolvedProvider.profile.remote ? sha(privatePolicy) : null,
    model: laneModelFor(lane, resolvedProvider, privatePolicy),
  };
  return validateWorkProviderRunBinding(binding, { resolvedProvider });
}

export function createSemanticRunBinding({ decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, privatePolicy, qualification, model }) {
  const errors = validateWorkProviderRouterDecision(decision);
  if (errors.length) fail(`provider run decision failed validation: ${errors.join("; ")}`);
  replayWorkProviderDecision({ decision, request, routerPolicy, enabledPrivatePolicies, qualifications });
  if (qualifications?.[resolvedProvider.profile.id] !== qualification) fail("provider run selected qualification differs from the deterministic routing inputs");
  if (resolvedProvider.profile.remote && enabledPrivatePolicies?.[resolvedProvider.profile.id] !== privatePolicy) fail("provider run selected private policy differs from the deterministic routing inputs");
  const profileSha = resolvedProvider.profileSha256;
  const modelValue = validModel(model);
  checkedProviderModel(resolvedProvider, resolvedProvider.profile.remote ? privatePolicy : null, qualification, modelValue);
  const binding = {
    kind: "semantic",
    decisionId: decision.decisionId,
    decisionSha256: sha(decision),
    requestSha256: sha(request),
    policySha256: sha(routerPolicy),
    providerProfileSha256: profileSha,
    privatePolicySha256: resolvedProvider.profile.remote ? sha(privatePolicy) : null,
    qualificationSha256: sha(qualification),
    model: modelValue,
  };
  return validateWorkProviderRunBinding(binding, { resolvedProvider });
}

export function assertWorkProviderRunBound({ ledger, decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, privatePolicy, qualification }) {
  const base = checked(copy(ledger));
  const binding = base.binding;
  if (!binding || binding.kind !== "semantic") fail("semantic provider execution requires a complete immutable semantic run binding");
  const errors = validateWorkProviderRouterDecision(decision);
  if (errors.length) fail(`provider run decision failed validation: ${errors.join("; ")}`);
  replayWorkProviderDecision({ decision, request, routerPolicy, enabledPrivatePolicies, qualifications });
  if (qualifications?.[resolvedProvider.profile.id] !== qualification) fail("provider run selected qualification differs from the deterministic routing inputs");
  if (resolvedProvider.profile.remote && enabledPrivatePolicies?.[resolvedProvider.profile.id] !== privatePolicy) fail("provider run selected private policy differs from the deterministic routing inputs");
  if (binding.decisionId !== decision.decisionId) fail("provider run decision identity differs from the bound decision");
  if (binding.decisionSha256 !== sha(decision)) fail("provider run decision hash differs from the bound decision");
  if (binding.requestSha256 !== sha(request)) fail("provider run request hash differs from the bound request");
  if (binding.policySha256 !== sha(routerPolicy)) fail("provider run policy hash differs from the bound router policy");
  if (binding.providerProfileSha256 !== resolvedProvider.profileSha256) fail("provider run provider profile hash differs from the bound provider");
  if (binding.qualificationSha256 !== sha(qualification)) fail("provider run qualification hash differs from the bound qualification");
  if (decision.selectedProviderId !== resolvedProvider.profile.id) fail("provider run provider differs from the routing decision");
  if (base.providerId !== resolvedProvider.profile.id) fail("provider run provider differs from the resolved provider");
  if (base.model !== binding.model) fail("provider run model differs from its binding");
  if (base.model !== decision.selectedModel) fail("provider run model differs from the routing decision model");
  if (base.model !== qualification?.model) fail("provider run model differs from the qualified model");
  checkedProviderModel(resolvedProvider, resolvedProvider.profile.remote ? privatePolicy : null, qualification, base.model);
  if (resolvedProvider.profile.remote) {
    if (binding.privatePolicySha256 !== sha(privatePolicy)) fail("provider run private policy hash differs from the bound policy");
  } else if (binding.privatePolicySha256 !== null) fail("local provider run cannot bind a remote private policy");
  return deepFreeze(copy(base));
}

export function providerIdempotencyKey(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("provider idempotency input is invalid");
  return sha(value);
}

export function workProviderInputSha256(value) {
  return sha(value);
}

export function assertClaimedWorkProviderRequest({ ledger, resolvedProvider, policy: rawPolicy, idempotencyKey, input }) {
  const policy = bindRunPolicy(resolvedProvider, rawPolicy);
  const base = checked(copy(ledger)), index = requestIndex(base, idempotencyKey);
  if (!base.binding || !["semantic", "connectivity-smoke", "qualification-trial"].includes(base.binding.kind)) fail("provider transport call requires an explicitly bound run");
  if (base.providerId !== resolvedProvider.profile.id || base.profileSha256 !== resolvedProvider.profileSha256 || base.policySha256 !== sha(policy)) fail("claimed provider request differs from its run binding");
  if (base.requests[index].state !== "claimed") fail("provider request is not in a claimed pre-invocation state");
  if (base.requests[index].inputSha256 !== sha(input)) fail("claimed provider request differs from the exact private model input hash");
  return deepFreeze(copy(base.requests[index]));
}

export function createWorkProviderRunLedger({ resolvedProvider, policy, taskId, model, binding, now = new Date(), suffix }) {
  const boundPolicy = bindRunPolicy(resolvedProvider, policy);
  validNow(now);
  if (!RUN_SUFFIX.test(suffix ?? "") || typeof taskId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/u.test(taskId)) fail("provider run ledger inputs are invalid");
  const bound = validateWorkProviderRunBinding(binding, { resolvedProvider });
  const modelValue = validModel(model);
  if (modelValue !== bound.model) fail("provider run model differs from its binding");
  return checked({
    $schema: "https://osmantic.com/pixel/schemas/work-provider-run-ledger-v1.schema.json", schemaVersion: 1,
    runId: `workproviderrun-${String(now.getTime()).padStart(13, "0")}-${suffix}`, createdAt: iso(now), updatedAt: iso(now), taskId,
    providerId: resolvedProvider.profile.id, model: modelValue, profileSha256: resolvedProvider.profileSha256, policySha256: sha(boundPolicy),
    binding: bound,
    status: "open", requests: [], totals: totals([]), providerContentStored: false, credentialStored: false, boundary: BOUNDARY,
  });
}

export function claimWorkProviderRequest({ ledger, policy: rawPolicy, resolvedProvider, idempotencyKey, inputSha256, estimatedInputTokens, maxOutputTokens, maxEstimatedCostMicros, now = new Date() }) {
  const policy = bindRunPolicy(resolvedProvider, rawPolicy);
  const base = checked(copy(ledger)); validNow(now);
  if (!base.binding || !["semantic", "connectivity-smoke", "qualification-trial"].includes(base.binding.kind)) fail("provider request claim requires an explicitly bound run");
  if (base.providerId !== resolvedProvider.profile.id || base.profileSha256 !== resolvedProvider.profileSha256 || base.policySha256 !== sha(policy)) fail("provider request claim differs from its run binding");
  if (base.status !== "open") fail("provider run is not open for a new request");
  if (!SHA.test(idempotencyKey ?? "")) fail("provider idempotency key is invalid");
  if (!SHA.test(inputSha256 ?? "")) fail("provider request input hash is invalid");
  if (base.requests.some((request) => request.idempotencyKey === idempotencyKey)) fail("provider idempotency key already exists and cannot be retried automatically");
  for (const [value, maximum, label, allowZero] of [
    [estimatedInputTokens, policy.budgets.maxInputTokensPerRun, "estimated input tokens", false],
    [maxOutputTokens, policy.budgets.maxOutputTokensPerRun, "maximum output tokens", false],
    [maxEstimatedCostMicros, policy.budgets.maxEstimatedCostMicrosPerRun, "estimated cost", true],
  ]) if (!Number.isSafeInteger(value) || (allowZero ? value < 0 : value < 1) || value > maximum) fail(`${label} exceed the provider run policy`);
  const reservedInput = base.requests.reduce((sum, request) => sum + request.estimatedInputTokens, 0) + estimatedInputTokens;
  const reservedOutput = base.requests.reduce((sum, request) => sum + request.maxOutputTokens, 0) + maxOutputTokens;
  const reservedCost = base.requests.reduce((sum, request) => sum + request.maxEstimatedCostMicros, 0) + maxEstimatedCostMicros;
  if (base.requests.length + 1 > policy.budgets.maxRequestsPerRun || reservedInput > policy.budgets.maxInputTokensPerRun
    || reservedOutput > policy.budgets.maxOutputTokensPerRun || reservedCost > policy.budgets.maxEstimatedCostMicrosPerRun) fail("provider request would exceed a cumulative run budget");
  const requests = copy(base.requests);
  requests.push({
    idempotencyKey, inputSha256, claimedAt: iso(now), settledAt: null, state: "claimed", attempts: 1,
    estimatedInputTokens, maxOutputTokens, maxEstimatedCostMicros, inputTokens: null, outputTokens: null,
    reportedCostMicros: null, providerRequestId: null, responseDisclosed: false, providerContentStored: false, responseModel: null,
  });
  return updated(base, requests, now, "open");
}

export function settleWorkProviderRequest({ ledger, policy: rawPolicy, resolvedProvider, idempotencyKey, outcome, usage = null, reportedCostMicros = null, providerRequestId = null, responseModel = null, now = new Date() }) {
  const policy = bindRunPolicy(resolvedProvider, rawPolicy);
  const base = checked(copy(ledger)); const index = requestIndex(base, idempotencyKey); validNow(now);
  if (!base.binding || !["semantic", "connectivity-smoke", "qualification-trial"].includes(base.binding.kind)) fail("provider request settlement requires an explicitly bound run");
  if (base.providerId !== resolvedProvider.profile.id || base.profileSha256 !== resolvedProvider.profileSha256 || base.policySha256 !== sha(policy)) fail("provider request settlement differs from its run binding");
  if (base.requests[index].state !== "claimed" || !["succeeded", "failed-known", "uncertain"].includes(outcome)) fail("provider request settlement is invalid or already terminal");
  if (providerRequestId !== null && (typeof providerRequestId !== "string" || providerRequestId.length < 1 || providerRequestId.length > 256 || /[\r\n\u0000]/u.test(providerRequestId))) fail("provider request identifier is invalid");
  let inputTokens = null, outputTokens = null;
  if (outcome === "succeeded") {
    if (!usage || !Number.isSafeInteger(usage.inputTokens) || usage.inputTokens < 0 || !Number.isSafeInteger(usage.outputTokens) || usage.outputTokens < 0) fail("successful provider request requires exact usage");
    inputTokens = usage.inputTokens; outputTokens = usage.outputTokens;
  } else if (usage !== null) fail("non-success provider settlement cannot claim complete usage");
  const responseModelValue = outcome === "succeeded" ? validResponseModel(responseModel, base.binding.kind, base.model) : (responseModel === null ? null : fail("non-success provider settlement cannot claim a response model"));
  if (reportedCostMicros !== null && (!Number.isSafeInteger(reportedCostMicros) || reportedCostMicros < 0)) fail("provider reported cost is invalid");
  const projectedInput = base.totals.inputTokens + (inputTokens ?? 0), projectedOutput = base.totals.outputTokens + (outputTokens ?? 0);
  const projectedCost = base.totals.reportedCostMicros + (reportedCostMicros ?? 0);
  const overBudget = outcome === "succeeded" && (projectedInput > policy.budgets.maxInputTokensPerRun || projectedOutput > policy.budgets.maxOutputTokensPerRun || projectedCost > policy.budgets.maxEstimatedCostMicrosPerRun);
  const requests = copy(base.requests);
  requests[index] = {
    ...requests[index], settledAt: iso(now), state: overBudget ? "budget-exceeded" : outcome,
    inputTokens, outputTokens, reportedCostMicros, providerRequestId, responseDisclosed: false, responseModel: responseModelValue,
  };
  return updated(base, requests, now, overBudget ? "budget-exhausted" : "open");
}

export function markWorkProviderResponseDisclosed({ ledger, idempotencyKey, now = new Date() }) {
  const base = checked(copy(ledger)); const index = requestIndex(base, idempotencyKey); validNow(now);
  if (base.requests[index].state !== "succeeded" || base.requests[index].responseDisclosed) fail("provider response is not eligible for one disclosure");
  const requests = copy(base.requests); requests[index].responseDisclosed = true;
  return updated(base, requests, now, "open");
}

export function reconcileWorkProviderRequest({ ledger, policy: rawPolicy, resolvedProvider, idempotencyKey, outcome, usage = null, reportedCostMicros = null, providerRequestId = null, responseModel = null, now = new Date() }) {
  const policy = bindRunPolicy(resolvedProvider, rawPolicy);
  const base = checked(copy(ledger)); const index = requestIndex(base, idempotencyKey); validNow(now);
  if (!base.binding || !["semantic", "connectivity-smoke", "qualification-trial"].includes(base.binding.kind)) fail("provider request reconciliation requires an explicitly bound run");
  if (base.providerId !== resolvedProvider.profile.id || base.profileSha256 !== resolvedProvider.profileSha256 || base.policySha256 !== sha(policy)) fail("provider request reconciliation differs from its run binding");
  if (base.requests[index].state !== "uncertain" || !["succeeded", "failed-known"].includes(outcome)) fail("only one uncertain provider request can be reconciled to a known terminal state");
  if (providerRequestId !== null && (typeof providerRequestId !== "string" || providerRequestId.length < 1 || providerRequestId.length > 256 || /[\r\n\u0000]/u.test(providerRequestId))) fail("provider request identifier is invalid");
  let inputTokens = null, outputTokens = null;
  if (outcome === "succeeded") {
    if (!usage || !Number.isSafeInteger(usage.inputTokens) || usage.inputTokens < 0 || !Number.isSafeInteger(usage.outputTokens) || usage.outputTokens < 0) fail("successful provider reconciliation requires exact usage");
    inputTokens = usage.inputTokens; outputTokens = usage.outputTokens;
  } else if (usage !== null) fail("failed provider reconciliation cannot claim complete usage");
  const responseModelValue = outcome === "succeeded" ? validResponseModel(responseModel, base.binding.kind, base.model) : (responseModel === null ? null : fail("failed provider reconciliation cannot claim a response model"));
  if (reportedCostMicros !== null && (!Number.isSafeInteger(reportedCostMicros) || reportedCostMicros < 0)) fail("provider reconciled cost is invalid");
  const projectedInput = base.totals.inputTokens + (inputTokens ?? 0), projectedOutput = base.totals.outputTokens + (outputTokens ?? 0);
  const projectedCost = base.totals.reportedCostMicros + (reportedCostMicros ?? 0);
  const overBudget = outcome === "succeeded" && (projectedInput > policy.budgets.maxInputTokensPerRun || projectedOutput > policy.budgets.maxOutputTokensPerRun || projectedCost > policy.budgets.maxEstimatedCostMicrosPerRun);
  const requests = copy(base.requests);
  requests[index] = { ...requests[index], settledAt: iso(now), state: overBudget ? "budget-exceeded" : outcome, inputTokens, outputTokens, reportedCostMicros, providerRequestId, responseDisclosed: false, responseModel: responseModelValue };
  return updated(base, requests, now, overBudget ? "budget-exhausted" : "open");
}

export function closeWorkProviderRun({ ledger, now = new Date() }) {
  const base = checked(copy(ledger)); validNow(now);
  if (base.status !== "open" || base.requests.some((request) => request.state === "claimed")) fail("provider run cannot close with an unsettled or terminal blocking state");
  return updated(base, copy(base.requests), now, "completed");
}

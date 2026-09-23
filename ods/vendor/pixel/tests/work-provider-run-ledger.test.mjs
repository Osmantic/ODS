import assert from "node:assert/strict";
import test from "node:test";

import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import {
  claimWorkProviderRequest, closeWorkProviderRun, createConnectivitySmokeBinding, createWorkProviderRunLedger, markWorkProviderResponseDisclosed,
  createSemanticRunBinding, providerIdempotencyKey, reconcileWorkProviderRequest, settleWorkProviderRequest, WorkProviderRunLedgerError,
} from "../deploy/work-provider/run-ledger.mjs";
import { validateWorkProviderRunLedger } from "../scripts/lib/work-contract.mjs";
import { semanticSetup } from "./fixtures/work-provider-exec.mjs";

const boundary = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";
function policy() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
    policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: "moonshot-kimi", enabled: true,
    credentialCustody: { credentialId: "moonshot-test", fileName: "provider-key", maxBytes: 8192 },
    transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
    budgets: { maxRequestsPerRun: 4, maxInputTokensPerRun: 50000, maxOutputTokensPerRun: 10000, maxNetworkBytesPerRun: 16777216, maxRequestSeconds: 120, maxEstimatedCostMicrosPerRun: 5000000, maxEstimatedCostMicrosPerDay: 20000000 },
    fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true },
    verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
    authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false }, boundary,
  };
}
const provider = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
function ledger() {
  const binding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: "c".repeat(64), model: "kimi-k3" });
  return createWorkProviderRunLedger({ resolvedProvider: provider, policy: policy(), taskId: "pixel-k3-fixture", model: "kimi-k3", binding, now: new Date("2026-08-21T16:10:00Z"), suffix: "abcdef123456" });
}

test("provider ledger claims once, reconciles usage, discloses once, and closes", () => {
  const key = providerIdempotencyKey({ taskId: "pixel-k3-fixture", turn: 1, inputSha256: "a".repeat(64) });
  const claimed = claimWorkProviderRequest({ ledger: ledger(), policy: policy(), resolvedProvider: provider, idempotencyKey: key, inputSha256: "a".repeat(64), estimatedInputTokens: 1000, maxOutputTokens: 2000, maxEstimatedCostMicros: 50000, now: new Date("2026-08-21T16:10:01Z") });
  assert.deepEqual(validateWorkProviderRunLedger(claimed), []);
  assert.throws(() => claimWorkProviderRequest({ ledger: claimed, policy: policy(), resolvedProvider: provider, idempotencyKey: key, inputSha256: "a".repeat(64), estimatedInputTokens: 1, maxOutputTokens: 1, maxEstimatedCostMicros: 1 }), /cannot be retried/u);
  const settled = settleWorkProviderRequest({ ledger: claimed, policy: policy(), resolvedProvider: provider, idempotencyKey: key, outcome: "succeeded", usage: { inputTokens: 900, outputTokens: 1500 }, reportedCostMicros: 40000, providerRequestId: "request-fixture", now: new Date("2026-08-21T16:10:02Z") });
  assert.equal(settled.requests[0].responseDisclosed, false);
  const disclosed = markWorkProviderResponseDisclosed({ ledger: settled, idempotencyKey: key, now: new Date("2026-08-21T16:10:03Z") });
  assert.equal(disclosed.requests[0].responseDisclosed, true);
  assert.throws(() => markWorkProviderResponseDisclosed({ ledger: disclosed, idempotencyKey: key }), WorkProviderRunLedgerError);
  const closed = closeWorkProviderRun({ ledger: disclosed, now: new Date("2026-08-21T16:10:04Z") });
  assert.equal(closed.status, "completed"); assert.deepEqual(validateWorkProviderRunLedger(closed), []);
});

test("uncertain provider outcome blocks retry until explicit reconciliation", () => {
  const key = providerIdempotencyKey({ taskId: "pixel-k3-fixture", turn: 2, inputSha256: "b".repeat(64) });
  const claimed = claimWorkProviderRequest({ ledger: ledger(), policy: policy(), resolvedProvider: provider, idempotencyKey: key, inputSha256: "b".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 100, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:11:01Z") });
  const uncertain = settleWorkProviderRequest({ ledger: claimed, policy: policy(), resolvedProvider: provider, idempotencyKey: key, outcome: "uncertain", now: new Date("2026-08-21T16:11:02Z") });
  assert.equal(uncertain.status, "uncertain");
  assert.throws(() => closeWorkProviderRun({ ledger: uncertain }), /cannot close/u);
  assert.throws(() => claimWorkProviderRequest({ ledger: uncertain, policy: policy(), resolvedProvider: provider, idempotencyKey: providerIdempotencyKey({ turn: 3 }), inputSha256: "c".repeat(64), estimatedInputTokens: 1, maxOutputTokens: 1, maxEstimatedCostMicros: 1 }), /not open/u);
  const reconciled = reconcileWorkProviderRequest({ ledger: uncertain, policy: policy(), resolvedProvider: provider, idempotencyKey: key, outcome: "failed-known", providerRequestId: "request-fixture", now: new Date("2026-08-21T16:11:03Z") });
  assert.equal(reconciled.status, "open"); assert.equal(reconciled.requests[0].state, "failed-known");
});

test("cumulative reservation and measured overage fail closed", () => {
  const key = providerIdempotencyKey({ turn: 4 });
  const first = claimWorkProviderRequest({ ledger: ledger(), policy: policy(), resolvedProvider: provider, idempotencyKey: providerIdempotencyKey({ turn: 3 }), inputSha256: "d".repeat(64), estimatedInputTokens: 25000, maxOutputTokens: 1, maxEstimatedCostMicros: 1, now: new Date("2026-08-21T16:12:00Z") });
  assert.throws(() => claimWorkProviderRequest({ ledger: first, policy: policy(), resolvedProvider: provider, idempotencyKey: key, inputSha256: "e".repeat(64), estimatedInputTokens: 25001, maxOutputTokens: 1, maxEstimatedCostMicros: 1, now: new Date("2026-08-21T16:12:01Z") }), /cumulative/u);
  const claimed = claimWorkProviderRequest({ ledger: ledger(), policy: policy(), resolvedProvider: provider, idempotencyKey: key, inputSha256: "e".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 100, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:12:01Z") });
  const over = settleWorkProviderRequest({ ledger: claimed, policy: policy(), resolvedProvider: provider, idempotencyKey: key, outcome: "succeeded", usage: { inputTokens: 50001, outputTokens: 1 }, reportedCostMicros: 1, now: new Date("2026-08-21T16:12:02Z") });
  assert.equal(over.status, "budget-exhausted"); assert.equal(over.requests[0].responseDisclosed, false);
  assert.throws(() => markWorkProviderResponseDisclosed({ ledger: over, idempotencyKey: key, now: new Date("2026-08-21T16:12:03Z") }), /not eligible/u);
});

function semanticLedger() {
  const s = semanticSetup("moonshot-kimi");
  const binding = createSemanticRunBinding({ decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications, resolvedProvider: s.resolvedProvider, privatePolicy: s.privatePolicy, qualification: s.qualification, model: s.model });
  return { s, binding, led: createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "pixel-k3-response-model", model: s.model, binding, now: new Date("2026-08-21T16:20:00Z"), suffix: "abcdef123456" }) };
}

test("semantic success requires an exact response model equal to ledger.model", () => {
  const { s, led } = semanticLedger();
  const turn1 = providerIdempotencyKey({ taskId: "pixel-k3-response-model", turn: 1, inputSha256: "a".repeat(64) });
  const claimed = claimWorkProviderRequest({ ledger: led, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn1, inputSha256: "a".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 200, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:20:01Z") });
  assert.throws(() => settleWorkProviderRequest({ ledger: claimed, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn1, outcome: "succeeded", usage: { inputTokens: 10, outputTokens: 5 }, now: new Date("2026-08-21T16:20:02Z") }), /exact response model/u);
  const turn2 = providerIdempotencyKey({ taskId: "pixel-k3-response-model", turn: 2, inputSha256: "b".repeat(64) });
  const claimed2 = claimWorkProviderRequest({ ledger: led, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn2, inputSha256: "b".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 200, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:20:03Z") });
  assert.throws(() => settleWorkProviderRequest({ ledger: claimed2, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn2, outcome: "succeeded", usage: { inputTokens: 10, outputTokens: 5 }, responseModel: "wrong-model", now: new Date("2026-08-21T16:20:04Z") }), /differs from the pinned run model/u);
  const turn3 = providerIdempotencyKey({ taskId: "pixel-k3-response-model", turn: 3, inputSha256: "c".repeat(64) });
  const claimed3 = claimWorkProviderRequest({ ledger: led, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn3, inputSha256: "c".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 200, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:20:05Z") });
  const settled = settleWorkProviderRequest({ ledger: claimed3, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn3, outcome: "succeeded", usage: { inputTokens: 10, outputTokens: 5 }, responseModel: s.model, now: new Date("2026-08-21T16:20:06Z") });
  assert.equal(settled.requests[0].responseModel, s.model);
  assert.deepEqual(validateWorkProviderRunLedger(settled), []);
});

test("non-success settlements cannot claim a response model", () => {
  const { s, led } = semanticLedger();
  const turn = providerIdempotencyKey({ taskId: "pixel-k3-response-model", turn: 4, inputSha256: "d".repeat(64) });
  const claimed = claimWorkProviderRequest({ ledger: led, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn, inputSha256: "d".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 200, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:21:01Z") });
  assert.throws(() => settleWorkProviderRequest({ ledger: claimed, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn, outcome: "failed-known", responseModel: s.model, now: new Date("2026-08-21T16:21:02Z") }), /cannot claim a response model/u);
});

test("tampered response model in a ledger is rejected by validation", () => {
  const { s, led } = semanticLedger();
  const turn = providerIdempotencyKey({ taskId: "pixel-k3-response-model", turn: 5, inputSha256: "e".repeat(64) });
  const claimed = claimWorkProviderRequest({ ledger: led, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn, inputSha256: "e".repeat(64), estimatedInputTokens: 100, maxOutputTokens: 200, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:22:01Z") });
  const settled = settleWorkProviderRequest({ ledger: claimed, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: turn, outcome: "succeeded", usage: { inputTokens: 10, outputTokens: 5 }, responseModel: s.model, now: new Date("2026-08-21T16:22:02Z") });
  const tampered = structuredClone(settled);
  tampered.requests[0].responseModel = "tampered-model";
  assert.ok(validateWorkProviderRunLedger(tampered).some((error) => error.includes("response model")));
  const swapped = structuredClone(settled);
  swapped.requests[0].responseModel = null;
  assert.ok(validateWorkProviderRunLedger(swapped).some((error) => error.includes("response model")));
});

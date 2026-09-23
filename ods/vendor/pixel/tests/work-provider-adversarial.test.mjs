import assert from "node:assert/strict";
import test from "node:test";

import { executeProviderTurn, workProviderExecutorTestOnly } from "../deploy/work-provider/executor.mjs";
import { assertClosedTransport, WorkProviderTransportRegistryError } from "../deploy/work-provider/transport-registry.mjs";
import { createSemanticRunBinding, createWorkProviderRunLedger, claimWorkProviderRequest, providerIdempotencyKey, WorkProviderRunLedgerError } from "../deploy/work-provider/run-ledger.mjs";
import { NOW, SUFFIX, semanticSetup } from "./fixtures/work-provider-exec.mjs";

import { routeWorkProvider } from "../deploy/work-provider-router/router.mjs";
import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import { makeLocalQualification, makePrivatePolicy, makeQualification, makeRequest, makeRouterPolicy } from "./fixtures/work-provider-router.mjs";

function input(model, overrides = {}) {
  return { schemaVersion: 1, model, messages: [{ role: "user", content: "fixture" }], maxOutputTokens: 256, ...overrides };
}
function okTransport() {
  return async () => ({ assistant: { message: { role: "assistant", content: "ok" }, finishReason: "stop", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } }, providerRequestId: "adv", networkBytes: 10 });
}

test("adversarial: a tampered decision hash fails closed before any transport call", async () => {
  const s = semanticSetup("moonshot-kimi");
  const valid = createSemanticRunBinding({ decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications, resolvedProvider: s.resolvedProvider, privatePolicy: s.privatePolicy, qualification: s.qualification, model: s.model });
  const tampered = { ...valid, decisionSha256: "0".repeat(64) };
  const key = providerIdempotencyKey({ task: "adv-tamper", turn: 1 });
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-tamper", model: s.model, idempotencyKey: key, binding: tampered,
    input: input(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), /decision hash/u);
});

test("adversarial: a wrong model is refused by the run binding", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-model", turn: 1 });
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-model", model: "not-kimi-k3", idempotencyKey: key,
    input: input("not-kimi-k3"), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), WorkProviderRunLedgerError);
});

test("adversarial: a wrong provider is refused by the run binding", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-provider", turn: 1 });
  const local = semanticSetup("local");
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: local.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: local.policy, privatePolicy: null, qualification: local.qualification,
    taskId: "adv-provider", model: local.model, idempotencyKey: key,
    input: input(local.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 0,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), /provider differs|qualification differs/u);
});

test("adversarial: an unbound claim is impossible", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-unbound", turn: 1 });
  // A ledger without any binding fails schema validation at creation, and a smoke-bound
  // ledger cannot satisfy a semantic execution. Both must fail closed.
  await assert.rejects(async () => createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "adv-unbound", model: s.model, binding: undefined, now: NOW, suffix: SUFFIX }), WorkProviderRunLedgerError);
});

test("adversarial: arbitrary transport injection is confined to an explicit test seam", async () => {
  const arbitrary = async () => ({});
  assert.throws(() => assertClosedTransport(arbitrary), WorkProviderTransportRegistryError);
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-inject", turn: 1 });
  await assert.rejects(executeProviderTurn({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-inject", model: s.model, idempotencyKey: key,
    input: input(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    transport: arbitrary, testSeam: false, now: NOW, suffix: SUFFIX,
  }), /cannot accept a caller-supplied transport/u);
});

test("adversarial: a truncated K3 reasoning_content replay fails closed", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-truncate", turn: 1 });
  const truncatedReplay = {
    role: "assistant", content: null,
    toolCalls: [{ id: "c1", name: "read_file", arguments: "{}" }],
    providerState: { protocol: "openai-chat-completions", finishReason: "tool_calls", assistantMessage: { role: "assistant", content: null, reasoning_content: "", tool_calls: [{ id: "c1", type: "function", function: { name: "read_file", arguments: "{}" } }] } },
  };
  const badInput = { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "x" }, truncatedReplay, { role: "tool", toolCallId: "c1", content: "r" }], maxOutputTokens: 128, reasoningEffort: "low" };
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-truncate", model: s.model, idempotencyKey: key, input: badInput,
    estimatedInputTokens: 100, maxOutputTokens: 128, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), /reasoning_content replay is incomplete or truncated/u);
});

test("adversarial: an uncertain outcome blocks retry and reroute", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "adv-uncertain", turn: 1 });
  const transport = async () => { const e = new Error("ambiguous"); e.outcome = "uncertain"; throw e; };
  const first = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-uncertain", model: s.model, idempotencyKey: key,
    input: input(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, transport);
  assert.equal(first.status, "uncertain");
  assert.equal(first.ledger.status, "uncertain");
  // No automatic retry: a new claim on the same uncertain run is refused, and the single
  // attempt cannot be re-claimed.
  assert.throws(() => claimWorkProviderRequest({ ledger: first.ledger, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: providerIdempotencyKey({ task: "adv-uncertain", turn: 2 }), inputSha256: "a".repeat(64), estimatedInputTokens: 1, maxOutputTokens: 1, maxEstimatedCostMicros: 1, now: NOW }), /not open/u);
});

test("adversarial: a schema-valid forged decision is rejected by deterministic replay", () => {
  const s = semanticSetup("moonshot-kimi");
  const forged = { ...structuredClone(s.decision), reasonCode: "remote-selected" };
  assert.throws(() => createSemanticRunBinding({
    decision: forged, request: s.request, routerPolicy: s.routerPolicy,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    resolvedProvider: s.resolvedProvider, privatePolicy: s.privatePolicy,
    qualification: s.qualification, model: s.model,
  }), /not the deterministic result/u);
});

const ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929";

function anthropicSemanticSetup() {
  const resolvedProvider = resolveWorkProvider("anthropic", { enabledRemoteProviders: ["anthropic"] });
  const privatePolicy = makePrivatePolicy("anthropic", {
    transport: { allowedHosts: ["api.anthropic.com:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    budgets: { maxRequestsPerRun: 10, maxInputTokensPerRun: 1000000, maxOutputTokensPerRun: 200000, maxNetworkBytesPerRun: 1048576, maxRequestSeconds: 300, maxEstimatedCostMicrosPerRun: 1000000, maxEstimatedCostMicrosPerDay: 5000000 },
  });
  const routerPolicy = makeRouterPolicy({ providerId: "anthropic", privatePolicy });
  const qualification = makeQualification("anthropic", { model: ANTHROPIC_MODEL });
  const request = makeRequest({ mode: "explicit-provider", explicitProviderId: "anthropic" });
  const enabledPrivatePolicies = { anthropic: privatePolicy };
  const qualifications = { local: makeLocalQualification(), anthropic: qualification };
  const { decision } = routeWorkProvider({ request, routerPolicy, enabledPrivatePolicies, qualifications, now: NOW, suffix: SUFFIX });
  return { resolvedProvider, routerPolicy, qualification, qualifications, enabledPrivatePolicies, request, decision, privatePolicy, model: ANTHROPIC_MODEL };
}

function anthropicReplayInput(model, overrides = {}) {
  return {
    schemaVersion: 1, model,
    messages: [
      { role: "user", content: "first" },
      { role: "assistant", content: null, toolCalls: [{ id: "toolu_1", name: "read_file", arguments: "{\"path\":\"x\"}" }], providerState: { protocol: "anthropic-messages", content: [{ type: "thinking", thinking: "private", signature: "sig" }, { type: "tool_use", id: "toolu_1", name: "read_file", input: { path: "x" } }], stopReason: "tool_use" } },
      { role: "tool", toolCallId: "toolu_1", content: "result" },
    ],
    maxOutputTokens: 256,
    ...overrides,
  };
}

test("adversarial: a complete Anthropic tool-call replay proceeds across the turn", async () => {
  const s = anthropicSemanticSetup();
  const key = providerIdempotencyKey({ task: "adv-anthropic-ok", turn: 2 });
  const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    resolvedProvider: s.resolvedProvider, policy: s.privatePolicy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-anthropic-ok", model: s.model, idempotencyKey: key,
    input: anthropicReplayInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, async () => ({ assistant: { message: { role: "assistant", content: "ok" }, finishReason: "stop", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, providerModel: ANTHROPIC_MODEL }, providerRequestId: "adv-anthropic", networkBytes: 10 }));
  assert.equal(result.status, "succeeded");
});

test("adversarial: an Anthropic tool-call replay that omits its tool_use block fails closed", async () => {
  const s = anthropicSemanticSetup();
  const key = providerIdempotencyKey({ task: "adv-anthropic-truncate", turn: 2 });
  const truncated = anthropicReplayInput(s.model);
  truncated.messages[1].providerState.content = [{ type: "thinking", thinking: "private", signature: "sig" }];
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    resolvedProvider: s.resolvedProvider, policy: s.privatePolicy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-anthropic-truncate", model: s.model, idempotencyKey: key,
    input: truncated, estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), /omitted its tool_use block/u);
});

test("adversarial: Anthropic replay cannot select another provider-state protocol", async () => {
  const s = anthropicSemanticSetup();
  const key = providerIdempotencyKey({ task: "adv-anthropic-protocol", turn: 2 });
  const substituted = anthropicReplayInput(s.model);
  substituted.messages[1].providerState.protocol = "openai-chat-completions";
  substituted.messages[1].providerState.assistantMessage = { role: "assistant", content: null };
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    resolvedProvider: s.resolvedProvider, policy: s.privatePolicy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "adv-anthropic-protocol", model: s.model, idempotencyKey: key,
    input: substituted, estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, okTransport()), /protocol differs from the resolved provider/u);
});

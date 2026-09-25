import assert from "node:assert/strict";
import test from "node:test";

import { workProviderExecutorTestOnly } from "../deploy/work-provider/executor.mjs";
import { executeLocalChatTurn, localProviderTransportTestOnly, LocalWorkProviderTransportError, localTransportInternals } from "../deploy/work-provider/local-transport.mjs";
import { claimWorkProviderRequest, createSemanticRunBinding, createWorkProviderRunLedger, providerIdempotencyKey, workProviderInputSha256 } from "../deploy/work-provider/run-ledger.mjs";
import { NOW, SUFFIX, semanticSetup } from "./fixtures/work-provider-exec.mjs";

const LOCAL_RESPONSE = {
  id: "local-fixture", object: "chat.completion", created: 1, model: "DeepSeek-V4-Flash-0731",
  ec_transfer_params: null, kv_transfer_params: null, metrics: null, prompt_logprobs: null, prompt_text: null, prompt_token_ids: null,
  choices: [{ index: 0, finish_reason: "stop", logprobs: null, routed_experts: null, stop_reason: null, token_ids: null, message: {
    annotations: null, audio: null, role: "assistant", content: "hello from local", function_call: null,
    reasoning: "private local reasoning fixture", refusal: null,
  } }],
  usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8, prompt_tokens_details: { cached_tokens: 0 } },
};

test("local loopback transport is bounded to 127.0.0.1:8000 and binds the exact qualified model", async () => {
  const s = semanticSetup("local");
  const binding = createSemanticRunBinding({ decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications, resolvedProvider: s.resolvedProvider, privatePolicy: null, qualification: s.qualification, model: s.model });
  let ledger = createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "pixel-local", model: s.model, binding, now: NOW, suffix: SUFFIX });
  const key = providerIdempotencyKey({ task: "pixel-local", turn: 1 });
  const localInput = { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "hi" }], maxOutputTokens: 256 };
  ledger = claimWorkProviderRequest({ ledger, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: key, inputSha256: workProviderInputSha256(localInput), estimatedInputTokens: 10, maxOutputTokens: 256, maxEstimatedCostMicros: 0, now: NOW });
  let observed;
  const exchange = async (request) => {
    observed = { host: request.host, port: request.port, path: request.path, body: JSON.parse(request.body.toString("utf8")) };
    const body = Buffer.from(JSON.stringify(LOCAL_RESPONSE));
    return { statusCode: 200, headers: { "x-request-id": "local-req-fixture" }, body, networkBytes: request.body.length + body.length };
  };
  const result = await localProviderTransportTestOnly.executeLocalChatTurnWithExchange({ resolvedProvider: s.resolvedProvider, policy: s.policy, ledger, idempotencyKey: key, input: localInput }, exchange);
  assert.equal(observed.host, "127.0.0.1");
  assert.equal(observed.port, 8000);
  assert.equal(observed.path, "/v1/chat/completions");
  assert.equal(observed.body.model, "DeepSeek-V4-Flash-0731");
  assert.equal(result.assistant.message.content, "hello from local");
  assert.equal(Object.hasOwn(result.assistant.message.providerState.assistantMessage, "reasoning_content"), false);
  assert.equal(Object.hasOwn(result.assistant.message.providerState.assistantMessage, "reasoning"), false);
  assert.doesNotMatch(JSON.stringify(result), /private local reasoning fixture/u);
  assert.equal(localTransportInternals.LOOPBACK_HOST, "127.0.0.1");
  assert.equal(localTransportInternals.LOOPBACK_PORT, 8000);
});

test("local semantic lane runs through the executor without any remote private policy", async () => {
  const s = semanticSetup("local");
  const key = providerIdempotencyKey({ task: "pixel-local-exec", turn: 1 });
  const wrapped = async (args) => localProviderTransportTestOnly.executeLocalChatTurnWithExchange(args, async (request) => {
    const body = Buffer.from(JSON.stringify(LOCAL_RESPONSE));
    return { statusCode: 200, headers: { "x-request-id": "local-exec-fixture" }, body, networkBytes: request.body.length + body.length };
  });
  const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: null, qualification: s.qualification,
    taskId: "pixel-local-exec", model: s.model, idempotencyKey: key,
    input: { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "hi" }], maxOutputTokens: 256 },
    estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 0,
    now: NOW, suffix: SUFFIX,
  }, wrapped);
  assert.equal(result.status, "succeeded");
  assert.equal(result.ledger.binding.kind, "semantic");
  assert.equal(result.ledger.binding.privatePolicySha256, null);
  assert.equal(result.ledger.model, "DeepSeek-V4-Flash-0731");
  assert.equal(result.ledger.providerId, "local");
  assert.equal(result.assistant.message.content, "hello from local");
});

test("local transport fails known on a mismatched or missing provider model", async () => {
  const s = semanticSetup("local");
  const makeClaim = () => {
    const binding = createSemanticRunBinding({ decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications, resolvedProvider: s.resolvedProvider, privatePolicy: null, qualification: s.qualification, model: s.model });
    let ledger = createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "pixel-local-model", model: s.model, binding, now: NOW, suffix: SUFFIX });
    const key = providerIdempotencyKey({ task: "pixel-local-model", turn: 1 });
    const localInput = { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "hi" }], maxOutputTokens: 256 };
    ledger = claimWorkProviderRequest({ ledger, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: key, inputSha256: workProviderInputSha256(localInput), estimatedInputTokens: 10, maxOutputTokens: 256, maxEstimatedCostMicros: 0, now: NOW });
    return { ledger, key, input: localInput };
  };
  const respond = async (request, value) => { const body = Buffer.from(JSON.stringify(value)); return { statusCode: 200, headers: {}, body, networkBytes: request.body.length + body.length }; };
  const mismatchClaim = makeClaim();
  const alias = { ...LOCAL_RESPONSE, model: "other-alias" };
  await assert.rejects(localProviderTransportTestOnly.executeLocalChatTurnWithExchange({ resolvedProvider: s.resolvedProvider, policy: s.policy, ledger: mismatchClaim.ledger, idempotencyKey: mismatchClaim.key, input: mismatchClaim.input }, (r) => respond(r, alias)), (error) => error instanceof LocalWorkProviderTransportError && error.outcome === "failed-known" && /differs from the pinned/.test(error.message));
  const missingClaim = makeClaim();
  const missing = { id: "x", object: "chat.completion", created: 1, choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "hello" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } };
  await assert.rejects(localProviderTransportTestOnly.executeLocalChatTurnWithExchange({ resolvedProvider: s.resolvedProvider, policy: s.policy, ledger: missingClaim.ledger, idempotencyKey: missingClaim.key, input: missingClaim.input }, (r) => respond(r, missing)), (error) => error instanceof LocalWorkProviderTransportError && error.outcome === "failed-known" && /omitted the exact pinned model/.test(error.message));
});

test("local transport classifies deterministic response validation failures as failed-known", async () => {
  const s = semanticSetup("local");
  const binding = createSemanticRunBinding({ decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications, resolvedProvider: s.resolvedProvider, privatePolicy: null, qualification: s.qualification, model: s.model });
  let ledger = createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "pixel-local-invalid-response", model: s.model, binding, now: NOW, suffix: SUFFIX });
  const key = providerIdempotencyKey({ task: "pixel-local-invalid-response", turn: 1 });
  const input = { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "hi" }], maxOutputTokens: 256 };
  ledger = claimWorkProviderRequest({ ledger, policy: s.policy, resolvedProvider: s.resolvedProvider, idempotencyKey: key, inputSha256: workProviderInputSha256(input), estimatedInputTokens: 10, maxOutputTokens: 256, maxEstimatedCostMicros: 0, now: NOW });
  const exchange = async (request) => {
    const body = Buffer.from('{"choices":[],"choices":[]}');
    return { statusCode: 200, headers: {}, body, networkBytes: request.body.length + body.length };
  };
  await assert.rejects(localProviderTransportTestOnly.executeLocalChatTurnWithExchange({ resolvedProvider: s.resolvedProvider, policy: s.policy, ledger, idempotencyKey: key, input }, exchange), (error) => error instanceof LocalWorkProviderTransportError && error.outcome === "failed-known" && /strict JSON/u.test(error.message));
});

test("production local transport rejects a caller-supplied exchange and never invokes it", async () => {
  let invoked = false;
  await assert.rejects(() => executeLocalChatTurn({ resolvedProvider: {}, exchange: async () => { invoked = true; } }), /cannot accept a caller-supplied exchange/u);
  await assert.rejects(() => executeLocalChatTurn({ resolvedProvider: {}, testSeam: true }), /cannot accept a caller-supplied exchange/u);
  assert.equal(invoked, false);
});

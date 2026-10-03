import assert from "node:assert/strict";
import { chmod, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { closeProviderRun, executeProviderTurn, workProviderExecutorTestOnly } from "../deploy/work-provider/executor.mjs";
import { resolveWorkProviderTransport, WorkProviderTransportRegistryError } from "../deploy/work-provider/transport-registry.mjs";
import { MoonshotWorkProviderTransportError } from "../deploy/work-provider/moonshot-transport.mjs";
import { createConnectivitySmokeBinding, createWorkProviderRunLedger, providerIdempotencyKey, WorkProviderRunLedgerError } from "../deploy/work-provider/run-ledger.mjs";
import { readWorkProviderRunLedger } from "../deploy/work-provider/run-store.mjs";
import { NOW, SUFFIX, semanticSetup } from "./fixtures/work-provider-exec.mjs";

function baseInput(model, overrides = {}) {
  return { schemaVersion: 1, model, messages: [{ role: "user", content: "fixture" }], maxOutputTokens: 256, ...overrides };
}

function fakeTransport(overrides = {}) {
  return async () => ({
    assistant: { message: { role: "assistant", content: "ok" }, finishReason: "stop", usage: { inputTokens: 12, outputTokens: 6, totalTokens: 18 }, providerModel: "kimi-k3" },
    providerRequestId: "exec-fixture", networkBytes: 200,
    ...overrides,
  });
}

test("executor binds a complete semantic decision, claims once, settles, and closes", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "pixel-k3-exec", turn: 1 });
  const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "pixel-k3-exec", model: s.model, idempotencyKey: key,
    input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, fakeTransport());
  assert.equal(result.status, "succeeded");
  assert.equal(result.assistant.usage.outputTokens, 6);
  assert.equal(result.ledger.requests.length, 1);
  assert.equal(result.ledger.requests[0].state, "succeeded");
  assert.equal(result.ledger.requests[0].attempts, 1);
});

test("an unbound semantic run cannot execute (fail closed)", async () => {
  const s = semanticSetup("moonshot-kimi");
  const smokeBinding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: "f".repeat(64), model: s.model });
  const ledger = createWorkProviderRunLedger({ resolvedProvider: s.resolvedProvider, policy: s.policy, taskId: "pixel-k3-unbound", model: s.model, binding: smokeBinding, now: NOW, suffix: SUFFIX });
  const key = providerIdempotencyKey({ task: "pixel-k3-unbound", turn: 1 });
  await assert.rejects(workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "pixel-k3-unbound", model: s.model, idempotencyKey: key, ledger,
    input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, fakeTransport()), WorkProviderRunLedgerError);
});

test("transport injection requires an explicit test seam and cannot pass as production", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "pixel-k3-seam", turn: 1 });
  await assert.rejects(executeProviderTurn({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "pixel-k3-seam", model: s.model, idempotencyKey: key,
    input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    transport: fakeTransport(), testSeam: false, now: NOW, suffix: SUFFIX,
  }), /cannot accept a caller-supplied transport/u);
});

test("closed transport registry resolves only registered providers", () => {
  assert.equal(typeof resolveWorkProviderTransport("moonshot-kimi"), "function");
  assert.equal(typeof resolveWorkProviderTransport("local"), "function");
  for (const id of ["openai", "anthropic", "openrouter", "together", "fireworks", "groq"]) assert.equal(typeof resolveWorkProviderTransport(id), "function");
  assert.throws(() => resolveWorkProviderTransport("unknown"), WorkProviderTransportRegistryError);
});

test("an uncertain transport outcome settles uncertain and never retries or reroutes", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "pixel-k3-uncertain", turn: 1 });
  const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "pixel-k3-uncertain", model: s.model, idempotencyKey: key,
    input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, async () => { throw new MoonshotWorkProviderTransportError("ambiguous", "uncertain"); });
  assert.equal(result.status, "uncertain");
  assert.equal(result.ledger.status, "uncertain");
  assert.equal(result.ledger.requests[0].state, "uncertain");
  assert.equal(result.ledger.requests[0].attempts, 1);
});

test("durable execution stores the binding and claim before transport, then atomically stores settlement and closure", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-executor-store-"));
  await chmod(root, 0o700);
  try {
    const s = semanticSetup("moonshot-kimi");
    const key = providerIdempotencyKey({ task: "pixel-k3-durable", turn: 1 });
    const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransportAndStore({
      decision: s.decision, request: s.request, routerPolicy: s.routerPolicy,
      enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
      resolvedProvider: s.resolvedProvider, policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
      taskId: "pixel-k3-durable", model: s.model, idempotencyKey: key,
      input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
      runStoreRoot: root, now: NOW, suffix: SUFFIX,
    }, fakeTransport());
    assert.match(result.ledgerSha256, /^[a-f0-9]{64}$/u);
    let observed = await readWorkProviderRunLedger({ root, runId: result.ledger.runId });
    assert.equal(observed.sha256, result.ledgerSha256);
    assert.equal(observed.ledger.requests[0].state, "succeeded");
    assert.equal(observed.ledger.requests[0].inputSha256.length, 64);
    const closed = await closeProviderRun({ ledger: result.ledger, runStoreRoot: root, expectedLedgerSha256: result.ledgerSha256, now: NOW });
    observed = await readWorkProviderRunLedger({ root, runId: result.ledger.runId });
    assert.equal(observed.sha256, closed.ledgerSha256);
    assert.equal(observed.ledger.status, "completed");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("an invalid post-invocation transport result is durably uncertain instead of disappearing", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-executor-uncertain-"));
  await chmod(root, 0o700);
  try {
    const s = semanticSetup("moonshot-kimi");
    const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransportAndStore({
      decision: s.decision, request: s.request, routerPolicy: s.routerPolicy,
      enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
      resolvedProvider: s.resolvedProvider, policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
      taskId: "pixel-k3-invalid-result", model: s.model,
      idempotencyKey: providerIdempotencyKey({ task: "pixel-k3-invalid-result", turn: 1 }),
      input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
      runStoreRoot: root, now: NOW, suffix: "123456abcdef",
    }, async () => ({}));
    assert.equal(result.status, "uncertain");
    const observed = await readWorkProviderRunLedger({ root, runId: result.ledger.runId });
    assert.equal(observed.sha256, result.ledgerSha256);
    assert.equal(observed.ledger.status, "uncertain");
    assert.equal(observed.ledger.requests[0].state, "uncertain");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("executor settles a failed-known response-model mismatch truthfully into the ledger", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "pixel-k3-model", turn: 1 });
  const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
    decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
    taskId: "pixel-k3-model", model: s.model, idempotencyKey: key,
    input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
    now: NOW, suffix: SUFFIX,
  }, async () => { throw new MoonshotWorkProviderTransportError("provider returned a model that differs from the pinned run model", "failed-known"); });
  assert.equal(result.status, "failed-known");
  assert.equal(result.outcome, "failed-known");
  assert.equal(result.ledger.requests.length, 1);
  assert.equal(result.ledger.requests[0].state, "failed-known");
  assert.equal(result.ledger.requests[0].attempts, 1);
});

test("crash after settlement but before disclosure durably preserves the exact response model", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-executor-crash-"));
  await chmod(root, 0o700);
  try {
    const s = semanticSetup("moonshot-kimi");
    const key = providerIdempotencyKey({ task: "pixel-k3-crash", turn: 1 });
    const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransportAndStore({
      decision: s.decision, request: s.request, routerPolicy: s.routerPolicy,
      enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
      resolvedProvider: s.resolvedProvider, policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
      taskId: "pixel-k3-crash", model: s.model, idempotencyKey: key,
      input: baseInput(s.model), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000,
      runStoreRoot: root, now: NOW, suffix: "123456abcdef",
    }, fakeTransport());
    // Simulate a crash after atomic settlement but before any response disclosure: the
    // durable ledger must already carry the exact response model and remain undisclosed.
    const observed = await readWorkProviderRunLedger({ root, runId: result.ledger.runId });
    assert.equal(observed.ledger.requests[0].state, "succeeded");
    assert.equal(observed.ledger.requests[0].responseModel, s.model);
    assert.equal(observed.ledger.requests[0].responseDisclosed, false);
    const closed = await closeProviderRun({ ledger: result.ledger, runStoreRoot: root, expectedLedgerSha256: result.ledgerSha256, now: NOW });
    assert.equal(closed.ledger.status, "completed");
  } finally { await rm(root, { recursive: true, force: true }); }
});

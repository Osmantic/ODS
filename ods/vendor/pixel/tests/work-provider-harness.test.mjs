import assert from "node:assert/strict";
import test from "node:test";

import { workProviderExecutorTestOnly } from "../deploy/work-provider/executor.mjs";
import { workProviderHarnessTestOnly, NEUTRAL_TASK_MANIFEST } from "../deploy/work-provider/harness.mjs";
import { gradeDeterministic, NEUTRAL_TASK_MANIFEST as GRADER_MANIFEST } from "../deploy/work-provider/grading.mjs";
import { MoonshotWorkProviderTransportError } from "../deploy/work-provider/moonshot-transport.mjs";
import { providerIdempotencyKey } from "../deploy/work-provider/run-ledger.mjs";
import { NOW, SUFFIX, semanticSetup } from "./fixtures/work-provider-exec.mjs";

const SENTINEL = "LONG_CONTEXT_SENTINEL_9f8e7d6c";

test("the neutral task manifest lists the neutral non-security tasks", () => {
  assert.deepEqual([...NEUTRAL_TASK_MANIFEST], ["structured-output", "disposable-coding-patch", "structural-review", "patch-proposal", "failure-triage", "tool-choice-continuation", "long-context-sentinel", "output-capacity", "known-uncertain-recovery"]);
  assert.deepEqual([...NEUTRAL_TASK_MANIFEST], [...GRADER_MANIFEST]);
});

test("the deterministic grader never invokes a model and is a pure function", () => {
  const input = { task: "structured-output", actual: { a: 1 }, expected: { a: 1 } };
  const first = gradeDeterministic(input);
  const second = gradeDeterministic(input);
  assert.equal(first.pass, true);
  assert.deepEqual(first, second);
  // The grader is a pure local function over its rubric; it imports no adapter, no fetch,
  // and no network path, so it cannot self-grade by construction.
  assert.deepEqual(Object.keys(gradeDeterministic({ task: "long-context-sentinel", actual: "x SENTINEL y", expected: "SENTINEL" })).sort(), ["criteria", "pass", "score", "task"]);
});

test("the harness runs every neutral task through a bound lane and grades it independently", async () => {
  const s = semanticSetup("moonshot-kimi");
  const expectedObject = { title: "report", ok: true };
  const patchBase = "export function add(a, b) {\n  return a - b;\n}\n";
  const patchExpected = "export function add(a, b) {\n  return a + b;\n}\n";
  const patch = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";
  const cases = [
    {
      task: "structured-output",
      response: { content: JSON.stringify(expectedObject), finishReason: "stop", toolCalls: [], usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 } },
      expected: expectedObject,
    },
    {
      task: "disposable-coding-patch",
      response: { content: patch, finishReason: "stop", toolCalls: [], usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 } },
      base: patchBase,
      expected: patchExpected,
    },
    {
      task: "tool-choice-continuation",
      response: { content: "continuation text", finishReason: "tool_calls", toolCalls: [{ id: "c1", name: "edit_file", arguments: "{}" }], usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 } },
      expected: { chosenTool: "edit_file", continuation: "continuation text" },
    },
    {
      task: "long-context-sentinel",
      response: { content: `prefix ${SENTINEL} suffix`, finishReason: "stop", toolCalls: [], usage: { inputTokens: 50, outputTokens: 3, totalTokens: 53 } },
      expected: SENTINEL,
    },
  ];

  for (const c of cases) {
    const key = providerIdempotencyKey({ task: `harness-${c.task}`, turn: 1 });
    const transport = async () => ({
      assistant: { message: { role: "assistant", content: c.response.content, toolCalls: c.response.toolCalls ?? [] }, finishReason: c.response.finishReason, usage: c.response.usage, providerModel: s.model },
      providerRequestId: `harness-${c.task}`, networkBytes: 200,
    });
    const executeTurn = (options) => workProviderExecutorTestOnly.executeProviderTurnWithTransport(options, transport);
    const lane = await workProviderHarnessTestOnly.runNeutralLaneWithExecutor({
      task: c.task, decision: s.decision, request: s.request, routerPolicy: s.routerPolicy,
      enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
      resolvedProvider: s.resolvedProvider, policy: s.policy, privatePolicy: s.privatePolicy,
      qualification: s.qualification, model: s.model,
      input: { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "task" }], maxOutputTokens: 256 },
      idempotencyKey: key, now: NOW, suffix: SUFFIX, expected: c.expected, base: c.base,
    }, executeTurn);
    assert.equal(lane.status, "succeeded");
    assert.equal(lane.grade.pass, true, `${c.task} should pass the independent verifier`);
    assert.equal(lane.ledger.status, "completed");
  }
});

test("the harness proves known/uncertain recovery is not retried or rerouted", async () => {
  const s = semanticSetup("moonshot-kimi");
  const key = providerIdempotencyKey({ task: "harness-uncertain", turn: 1 });
  const transport = async () => { throw new MoonshotWorkProviderTransportError("ambiguous", "uncertain"); };
  const executeTurn = (options) => workProviderExecutorTestOnly.executeProviderTurnWithTransport(options, transport);
  const lane = await workProviderHarnessTestOnly.runNeutralLaneWithExecutor({
    task: "known-uncertain-recovery", decision: s.decision, request: s.request, routerPolicy: s.routerPolicy,
    enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
    resolvedProvider: s.resolvedProvider, policy: s.policy, privatePolicy: s.privatePolicy,
    qualification: s.qualification, model: s.model,
    input: { schemaVersion: 1, model: s.model, messages: [{ role: "user", content: "task" }], maxOutputTokens: 256 },
    idempotencyKey: key, now: NOW, suffix: SUFFIX, expected: undefined, base: undefined,
  }, executeTurn);
  assert.equal(lane.status, "uncertain");
  assert.equal(lane.grade.pass, true);
  assert.equal(lane.outcome.ledger.status, "uncertain");
  assert.equal(lane.outcome.ledger.requests[0].state, "uncertain");
  assert.equal(lane.outcome.ledger.requests[0].attempts, 1);
});

test("the harness derives a zero-cost local claim and carries closed remote custody and proxy routing", async () => {
  const local = semanticSetup("local");
  let observedLocalCost = null;
  const localExecute = (options) => {
    observedLocalCost = options.maxEstimatedCostMicros;
    return workProviderExecutorTestOnly.executeProviderTurnWithTransport(options, async () => ({
      assistant: { message: { role: "assistant", content: "{\"ok\":true}" }, finishReason: "stop", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, providerModel: local.model },
      providerRequestId: "local-harness", networkBytes: 1,
    }));
  };
  const localLane = await workProviderHarnessTestOnly.runNeutralLaneWithExecutor({
    task: "structured-output", decision: local.decision, request: local.request, routerPolicy: local.routerPolicy,
    enabledPrivatePolicies: local.enabledPrivatePolicies, qualifications: local.qualifications,
    resolvedProvider: local.resolvedProvider, policy: local.policy, privatePolicy: null, qualification: local.qualification,
    model: local.model, input: { schemaVersion: 1, model: local.model, messages: [{ role: "user", content: "task" }], maxOutputTokens: 256 },
    idempotencyKey: providerIdempotencyKey({ task: "harness-local-budget", turn: 1 }), now: NOW, suffix: SUFFIX, expected: { ok: true },
  }, localExecute);
  assert.equal(observedLocalCost, 0);
  assert.equal(localLane.grade.pass, true);

  const remote = semanticSetup("moonshot-kimi");
  const credentialHandle = Object.freeze({ opaque: "test-only-custody-handle" });
  let captured;
  const remoteExecute = (options) => workProviderExecutorTestOnly.executeProviderTurnWithTransport(options, async (transportOptions) => {
    captured = transportOptions;
    return { assistant: { message: { role: "assistant", content: "{\"ok\":true}" }, finishReason: "stop", usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, providerModel: remote.model }, providerRequestId: "remote-harness", networkBytes: 1 };
  });
  const remoteLane = await workProviderHarnessTestOnly.runNeutralLaneWithExecutor({
    task: "structured-output", decision: remote.decision, request: remote.request, routerPolicy: remote.routerPolicy,
    enabledPrivatePolicies: remote.enabledPrivatePolicies, qualifications: remote.qualifications,
    resolvedProvider: remote.resolvedProvider, policy: remote.policy, privatePolicy: remote.privatePolicy, qualification: remote.qualification,
    model: remote.model, input: { schemaVersion: 1, model: remote.model, messages: [{ role: "user", content: "task" }], maxOutputTokens: 256 },
    credentialHandle, proxyRoute: "loopback",
    idempotencyKey: providerIdempotencyKey({ task: "harness-remote-custody", turn: 1 }), now: NOW, suffix: SUFFIX, expected: { ok: true },
  }, remoteExecute);
  assert.equal(remoteLane.grade.pass, true);
  assert.equal(captured.credentialHandle, credentialHandle);
  assert.equal(captured.proxyHost, "127.0.0.1");
  assert.equal(captured.proxyPort, 3128);
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { canonical } from "../scripts/lib/work-contract.mjs";
import { workProviderEquivalenceTestSeam } from "../deploy/work-provider/equivalence-runner.mjs";
import { workProviderQualificationTestSeam } from "../deploy/work-provider/qualification-runner.mjs";
import {
  admitNeutralCorpusRequest, buildNeutralCorpus, findNeutralCorpusCase, laneReasoningEvidence,
  LONG_CONTEXT_MIN_INPUT_TOKENS, LONG_CONTEXT_REQUIRED_REPLY, NEUTRAL_CORPUS_VERSION, QUALIFICATION_LANES,
  OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS, OUTPUT_CAPACITY_REPLY, OUTPUT_CAPACITY_REPORTED_TOKENS,
  TOOL_CHOICE_FINAL_REPLY, TOOL_CHOICE_SYNTHETIC_RESULT, TOOL_CHOICE_TOOL_ARGUMENTS, TOOL_CHOICE_TOOL_NAME,
  validateNeutralToolChoiceReplay,
} from "../deploy/work-provider/neutral-corpus.mjs";
import { PATCH_SUBMISSION_TOOL, PATCH_SUBMISSION_TOOL_CHOICE, PATCH_SUBMISSION_TOOL_NAME } from "../deploy/work-provider/patch-extraction.mjs";
import { resolveWorkProvider, resolveProviderModel } from "../deploy/work-provider/provider-registry.mjs";

const REMOTE_LANES = ["openai", "anthropic", "openrouter"];
const LANE_MODELS = Object.freeze({ openai: "gpt-5.6", anthropic: "claude-sonnet-4-5-20250929", openrouter: "anthropic/claude-sonnet-4-5" });
const LANE_HOSTS = Object.freeze({ openai: ["api.openai.com:443"], anthropic: ["api.anthropic.com:443"], openrouter: ["openrouter.ai:443"] });
// Exact owner-private pricing binding the qualification promotion must read verbatim. These are
// closed owner-bound budget rates (never live/current price claims) that the promotion mirrors
// deterministically; a lane without such a binding fails closed on promotion.
const LANE_PRICING = Object.freeze({
  openai: { inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 4000000, fixedMicrosPerRun: 0 },
  anthropic: { inputMicrosPerMillionTokens: 3000000, outputMicrosPerMillionTokens: 15000000, fixedMicrosPerRun: 0 },
  openrouter: { inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 4000000, fixedMicrosPerRun: 0 },
});
const REASONING = "REMOTE_REASONING_VERBATIM_0123456789abcdef";
const LONG_CONTEXT_REPORTED_INPUT_TOKENS = 38000;
const PATCH = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";

const PRIVATE_BOUNDARY = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";

function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function withoutArtifactSha(doc) { const { artifactSha256: _ignored, ...rest } = doc; return rest; }

function privatePolicyFor(lane) {
  const model = LANE_MODELS[lane];
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
    policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: lane, enabled: true, model,
    credentialCustody: { credentialId: "remote-test", fileName: "provider-key", maxBytes: 8192 },
    transport: { allowedHosts: LANE_HOSTS[lane], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
    budgets: { maxRequestsPerRun: 40, maxInputTokensPerRun: 400000, maxOutputTokensPerRun: 40000, maxNetworkBytesPerRun: 2097152, maxRequestSeconds: 60, maxEstimatedCostMicrosPerRun: 1000000, maxEstimatedCostMicrosPerDay: 5000000 },
    pricing: LANE_PRICING[lane],
    fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true }, verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
    authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false },
    boundary: PRIVATE_BOUNDARY,
  };
}

async function remoteFixture(lane) {
  const root = await mkdtemp(join(tmpdir(), `pixel-qual-${lane}-`));
  const credentialDirectory = join(root, "credential"), credentialPath = join(credentialDirectory, "provider-key");
  const policyPath = join(root, "policy.json"), ledgerRoot = join(root, "ledger");
  await mkdir(credentialDirectory, { mode: 0o700 }); await mkdir(ledgerRoot, { mode: 0o700 });
  await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  const policy = privatePolicyFor(lane);
  const text = canonical(policy); await writeFile(policyPath, `${text}\n`, { mode: 0o600 });
  await chmod(root, 0o700); await chmod(credentialDirectory, 0o700); await chmod(credentialPath, 0o600); await chmod(policyPath, 0o600);
  const policySha256 = createHash("sha256").update(text).digest("hex");
  return { lane, root, ledgerRoot, policyPath, sha256: policySha256, credentialPath, model: LANE_MODELS[lane], policy };
}

function openAiToolState(callId, callName, callArguments) {
  return {
    protocol: "openai-responses",
    outputItems: [
      { type: "reasoning", id: "r1", summary: [], content: [{ type: "output_text", text: REASONING }] },
      { type: "function_call", id: callId, call_id: callId, name: callName, arguments: callArguments },
    ],
    responseState: { status: "completed", error: null, incompleteDetails: null },
  };
}
function anthropicToolState(callId, callName, callInput) {
  return {
    protocol: "anthropic-messages",
    content: [
      { type: "thinking", thinking: REASONING, signature: "sig-1" },
      { type: "tool_use", id: callId, name: callName, input: callInput },
    ],
    stopReason: "tool_use",
  };
}
function openRouterToolState(callId, callName, callArguments) {
  return {
    protocol: "openai-chat-completions",
    assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: callId, type: "function", function: { name: callName, arguments: callArguments } }], reasoning_content: REASONING },
    finishReason: "tool_calls",
  };
}
function toolProviderState(lane, callId, callName, callArguments) {
  if (lane === "openai") return openAiToolState(callId, callName, callArguments);
  if (lane === "anthropic") return anthropicToolState(callId, callName, JSON.parse(callArguments));
  return openRouterToolState(callId, callName, callArguments);
}
function normalizedToolCall(callId, name, argumentsText) {
  return { id: callId, name, arguments: argumentsText };
}

function toolChoiceFirstResponse(lane, model) {
  const callId = "remote_tool_0001";
  const call = normalizedToolCall(callId, TOOL_CHOICE_TOOL_NAME, TOOL_CHOICE_TOOL_ARGUMENTS);
  return {
    message: { role: "assistant", content: null, toolCalls: [call], providerState: toolProviderState(lane, callId, TOOL_CHOICE_TOOL_NAME, TOOL_CHOICE_TOOL_ARGUMENTS) },
    finishReason: "tool_calls", usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 }, providerModel: model,
  };
}
function patchResponse(lane, model) {
  const callId = "remote_patch_0001";
  const call = normalizedToolCall(callId, PATCH_SUBMISSION_TOOL_NAME, JSON.stringify({ patch: PATCH }));
  return {
    message: { role: "assistant", content: null, toolCalls: [call], providerState: toolProviderState(lane, callId, PATCH_SUBMISSION_TOOL_NAME, JSON.stringify({ patch: PATCH })) },
    finishReason: "tool_calls", usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 }, providerModel: model,
  };
}
function responseFor(lane, model, caseEntry) {
  switch (caseEntry.task) {
    case "structural-review":
      return { message: { role: "assistant", content: '{"exportCount":3,"defectFunction":"add","defectKind":"wrong-operator"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: model };
    case "failure-triage":
      return { message: { role: "assistant", content: '{"classification":"transient","retried":true,"resolution":"retry-succeeded"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: model };
    case "patch-proposal":
      return patchResponse(lane, model);
    case "long-context-sentinel":
      return { message: { role: "assistant", content: LONG_CONTEXT_REQUIRED_REPLY }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS, outputTokens: 3, totalTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS + 3 }, providerModel: model };
    case "output-capacity":
      return { message: { role: "assistant", content: OUTPUT_CAPACITY_REPLY }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_REPORTED_TOKENS, totalTokens: 40 + OUTPUT_CAPACITY_REPORTED_TOKENS }, providerModel: model };
    default:
      throw new Error("unknown single-request task");
  }
}
function passingTransport(lane, model) {
  return async ({ input }) => {
    const admitted = admitNeutralCorpusRequest({ lane, model, input });
    if (!admitted) throw new Error("transport received an input outside the neutral corpus");
    if (admitted.kind === "tool-choice-replay") {
      return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: model }, providerRequestId: "qual-tool-2", networkBytes: 200 };
    }
    const caseEntry = findNeutralCorpusCase({ lane, model, input });
    if (caseEntry.task === "tool-choice-continuation") {
      return { assistant: toolChoiceFirstResponse(lane, model), providerRequestId: "qual-tool-1", networkBytes: 200 };
    }
    return { assistant: responseFor(lane, model, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
  };
}

function fencedStructuredTransport(lane, model, { wrongStructural = false } = {}) {
  return async ({ input }) => {
    const admitted = admitNeutralCorpusRequest({ lane, model, input });
    if (!admitted) throw new Error("transport received an input outside the neutral corpus");
    if (admitted.kind === "tool-choice-replay") {
      return {
        assistant: {
          message: { role: "assistant", content: `\`\`\`json\n${TOOL_CHOICE_FINAL_REPLY}\n\`\`\`` },
          finishReason: "stop", usage: { inputTokens: 20, outputTokens: 8, totalTokens: 28 }, providerModel: model,
        },
        providerRequestId: "qual-tool-2-fenced", networkBytes: 240,
      };
    }
    const caseEntry = findNeutralCorpusCase({ lane, model, input });
    if (caseEntry.task === "tool-choice-continuation") {
      return { assistant: toolChoiceFirstResponse(lane, model), providerRequestId: "qual-tool-1-fenced", networkBytes: 200 };
    }
    const response = responseFor(lane, model, caseEntry);
    if (caseEntry.task === "structural-review") {
      const body = wrongStructural
        ? '{"exportCount":2,"defectFunction":"add","defectKind":"wrong-operator"}'
        : response.message.content;
      response.message.content = `\`\`\`json\n${body}\n\`\`\``;
    }
    if (caseEntry.task === "failure-triage") response.message.content = `\`\`\`json\n${response.message.content}\n\`\`\``;
    return { assistant: response, providerRequestId: `qual-${caseEntry.caseId}-fenced`, networkBytes: 240 };
  };
}
function remoteArgv(value, extra = []) {
  return ["--lane", value.lane, "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", value.policyPath, "--sha256", value.sha256, "--credential", value.credentialPath, "--proxy-route", "loopback", "--ledger-root", value.ledgerRoot, "--qualification-report", join(value.root, "qualification-artifact.json"), ...extra];
}

test("remote lanes are admitted with exact model binding, tool patch, and preserved reasoning", () => {
  for (const lane of REMOTE_LANES) {
    assert.ok(QUALIFICATION_LANES.includes(lane), `${lane} must be a qualification lane`);
    const resolved = resolveWorkProvider(lane, { enabledRemoteProviders: [lane] });
    assert.equal(resolved.profile.remote, true);
    assert.equal(resolved.profile.enabledByDefault, false, "remote providers stay disabled by default");
    const corpus = buildNeutralCorpus({ lane, model: LANE_MODELS[lane] });
    assert.equal(corpus.lane, lane);
    assert.equal(corpus.model, LANE_MODELS[lane]);
    for (const entry of corpus.cases) {
      const request = entry.requestCount === 1 ? entry.input : entry.firstRequest;
      assert.equal(Object.hasOwn(request, "temperature"), false, `${lane} must omit fixed temperature`);
    }
    const patch = corpus.cases.find((entry) => entry.task === "patch-proposal").input;
    assert.deepEqual(patch.tools, [PATCH_SUBMISSION_TOOL], `${lane} patch proof must present the closed submit_patch tool`);
    assert.equal(patch.toolChoice, PATCH_SUBMISSION_TOOL_CHOICE);
    const replay = buildNeutralCorpus({ lane, model: LANE_MODELS[lane] }).cases.find((entry) => entry.task === "tool-choice-continuation");
    assert.equal(replay.firstRequest.toolChoice, "auto");
    // Replay of the returned protocol-specific provider state must carry the evidence the lane's
    // adapter actually guarantees: reasoning for moonshot/openai/anthropic, and the complete
    // assistant message (tool calls) for provider-dependent openrouter.
    const first = toolChoiceFirstResponse(lane, LANE_MODELS[lane]);
    if (lane === "openrouter") {
      assert.equal(laneReasoningEvidence(lane, first.message), null, "openrouter must not claim provider-dependent reasoning");
    } else {
      assert.ok(laneReasoningEvidence(lane, first.message) !== null, `${lane} first tool response must carry reasoning evidence`);
    }
    const replayInput = {
      schemaVersion: 1, model: LANE_MODELS[lane],
      messages: [
        replay.firstRequest.messages[0],
        first.message,
        { role: "tool", toolCallId: first.message.toolCalls[0].id, content: TOOL_CHOICE_SYNTHETIC_RESULT },
        { role: "user", content: replay.followUpUser },
      ],
      maxOutputTokens: replay.firstRequest.maxOutputTokens,
      reasoningEffort: replay.firstRequest.reasoningEffort,
    };
    assert.equal(validateNeutralToolChoiceReplay({ lane, model: LANE_MODELS[lane], input: replayInput }), true, `${lane} replay must admit its protocol-correct provider state`);
    // A replay missing its guaranteed protocol evidence must be rejected.
    const stripped = structuredClone(replayInput);
    if (lane === "anthropic") stripped.messages[1].providerState.content = [{ type: "text", text: "no thinking" }];
    else if (lane === "openai") stripped.messages[1].providerState.outputItems = [{ type: "message", id: "m", role: "assistant", status: "completed", content: [] }];
    else if (lane === "openrouter") stripped.messages[1].providerState.assistantMessage = { role: "assistant", content: null, tool_calls: [] };
    else stripped.messages[1].providerState.assistantMessage = { role: "assistant", content: null, tool_calls: stripped.messages[1].providerState.assistantMessage.tool_calls };
    assert.equal(validateNeutralToolChoiceReplay({ lane, model: LANE_MODELS[lane], input: stripped }), false, `${lane} replay without its guaranteed evidence must be rejected`);
  }
  // openrouter is owner-pinned: the corpus accepts the exact owner-private model.
  assert.doesNotThrow(() => buildNeutralCorpus({ lane: "openrouter", model: "anthropic/claude-sonnet-4-5" }));
  assert.throws(() => buildNeutralCorpus({ lane: "openai", model: "some-other-model" }), /model differs from the bound lane/u);
  // Caller placeholders/defaults must never leak into an owner-pinned lane.
  for (const placeholder of ["provider-selected-model", "auto", "placeholder"]) {
    assert.throws(() => buildNeutralCorpus({ lane: "openrouter", model: placeholder }), /owner-pinned neutral corpus model is invalid/u);
  }
  assert.throws(() => buildNeutralCorpus({ lane: "bogus", model: "x" }), /lane is outside the closed registry/u);
});

test("openrouter replay requires the saved raw tool call to exactly match the normalized tool result", () => {
  const lane = "openrouter", model = LANE_MODELS.openrouter;
  const corpus = buildNeutralCorpus({ lane, model });
  const caseEntry = corpus.cases.find((entry) => entry.task === "tool-choice-continuation");
  const first = toolChoiceFirstResponse(lane, model);
  const baseReplay = {
    schemaVersion: 1, model,
    messages: [
      caseEntry.firstRequest.messages[0],
      first.message,
      { role: "tool", toolCallId: first.message.toolCalls[0].id, content: TOOL_CHOICE_SYNTHETIC_RESULT },
      { role: "user", content: caseEntry.followUpUser },
    ],
    maxOutputTokens: caseEntry.firstRequest.maxOutputTokens, reasoningEffort: caseEntry.firstRequest.reasoningEffort,
  };
  assert.equal(validateNeutralToolChoiceReplay({ lane, model, input: baseReplay }), true);
  assert.equal(admitNeutralCorpusRequest({ lane, model, input: baseReplay }).kind, "tool-choice-replay");
  // The saved raw assistant tool call must exactly correspond to the normalized toolCalls[0]
  // that drives the following tool result; any divergence fails closed before transport.
  const saved = () => baseReplay.messages[1].providerState.assistantMessage.tool_calls[0];
  const negated = (mutate) => {
    const tampered = structuredClone(baseReplay);
    mutate(tampered.messages[1].providerState.assistantMessage.tool_calls[0]);
    assert.equal(validateNeutralToolChoiceReplay({ lane, model, input: tampered }), false, "tampered saved tool call must be rejected");
    assert.equal(admitNeutralCorpusRequest({ lane, model, input: tampered }), null, "tampered saved tool call must fail before transport");
  };
  const correct = saved();
  negated((call) => { call.id = "wrong-id"; });
  negated((call) => { call.function.name = "wrong-name"; });
  negated((call) => { call.function.arguments = '{"path":"other.txt"}'; });
  negated((call) => { call.type = "other"; });
  const extra = structuredClone(baseReplay);
  extra.messages[1].providerState.assistantMessage.tool_calls.push({ id: "extra", type: "function", function: { name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS } });
  assert.equal(validateNeutralToolChoiceReplay({ lane, model, input: extra }), false, "an extra saved raw tool call must be rejected");
  assert.equal(admitNeutralCorpusRequest({ lane, model, input: extra }), null, "an extra saved raw tool call must fail before transport");
  assert.equal(validateNeutralToolChoiceReplay({ lane, model, input: baseReplay }), true, "the unchanged replay must remain admitted");
});

test("remote lane adapters bind the exact protocol request and parse the exact protocol response", () => {
  for (const lane of REMOTE_LANES) {
    const resolved = resolveWorkProvider(lane, { enabledRemoteProviders: [lane] });
    const model = LANE_MODELS[lane];
    const corpus = buildNeutralCorpus({ lane, model });
    const structural = corpus.cases.find((entry) => entry.task === "structural-review").input;
    const wire = resolved.adapter.buildRequest(structural, { outputTokenField: "max_tokens" });
    assert.equal(wire.model, model);
    if (lane === "openai") assert.equal(wire.input[0].role, "user");
    if (lane === "anthropic") assert.ok(Array.isArray(wire.messages) && wire.messages[0].role === "user" && wire.thinking);
    if (lane === "openrouter") assert.ok(Array.isArray(wire.messages) && wire.messages[0].role === "user");
    // A model-scoped owner-pinned request must not invent a placeholder model.
    if (lane === "openrouter") {
      const pinned = resolveProviderModel({ resolvedProvider: resolved, privatePolicy: { model }, model });
      assert.equal(pinned, model);
      assert.throws(() => resolveProviderModel({ resolvedProvider: resolved, privatePolicy: { model: "auto" }, model }), /requires an exact owner-private model/u);
    }
    // Protocol-correct response parses to the normalized assistant with exact model binding.
    const parsed = resolved.adapter.parseResponse(protocolResponse(lane, model));
    assert.equal(parsed.providerModel, model);
  }
});

test("anthropic corpus inputs always satisfy the extended-thinking adapter contract", () => {
  const resolved = resolveWorkProvider("anthropic", { enabledRemoteProviders: ["anthropic"] });
  const model = LANE_MODELS.anthropic;
  const corpus = buildNeutralCorpus({ lane: "anthropic", model });
  const reasoningCases = corpus.cases.filter((entry) => (entry.requestCount === 1 ? entry.input : entry.firstRequest).reasoningEffort && (entry.requestCount === 1 ? entry.input : entry.firstRequest).reasoningEffort !== "none");
  assert.ok(reasoningCases.length > 0, "anthropic lane must exercise extended thinking");
  for (const entry of corpus.cases) {
    for (const input of entry.requestCount === 1 ? [entry.input] : [entry.firstRequest]) {
      const wire = resolved.adapter.buildRequest(input, { outputTokenField: "max_tokens" });
      if (input.reasoningEffort && input.reasoningEffort !== "none") {
        assert.ok(wire.thinking && wire.thinking.type === "enabled", `anthropic ${entry.task} must keep thinking enabled`);
        assert.ok(Number.isSafeInteger(wire.max_tokens) && Number.isSafeInteger(wire.thinking.budget_tokens) && wire.thinking.budget_tokens < wire.max_tokens, `anthropic ${entry.task} must keep thinking budget strictly below max_tokens`);
      }
    }
  }
  // The adapter still fails closed when the budget cannot fit under max_tokens.
  assert.throws(() => resolved.adapter.buildRequest({ ...corpus.cases[0].input, maxOutputTokens: 1024, reasoningEffort: "low" }, { outputTokenField: "max_tokens" }), /strictly above.*1024/u);
});

function protocolResponse(lane, model) {
  if (lane === "openai") {
    return { id: "resp_fixture", object: "response", created_at: 1, completed_at: 2, status: "completed", error: null, incomplete_details: null, instructions: null, max_output_tokens: 256, model, output: [{ type: "message", id: "m1", role: "assistant", status: "completed", content: [{ type: "output_text", text: "ok" }] }], parallel_tool_calls: false, previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1, usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 }, metadata: {} };
  }
  if (lane === "anthropic") {
    return { id: "msg_fixture", type: "message", role: "assistant", model, content: [{ type: "text", text: "ok" }], stop_reason: "end_turn", stop_sequence: null, usage: { input_tokens: 10, output_tokens: 5 } };
  }
  return { id: "chatcmpl_fixture", object: "chat.completion", created: 1, model, choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "ok" } }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } };
}

async function produceRemoteArtifact(lane) {
  const value = await remoteFixture(lane);
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(remoteArgv(value), passingTransport(lane, value.model));
    assert.equal(receipt.status, "qualified");
    assert.ok(receipt.semanticQualification, `${lane} must promote a semantic qualification`);
    assert.equal(receipt.lane, lane);
    assert.equal(receipt.model, value.model);
    const artifact = JSON.parse(await readFile(join(value.root, "qualification-artifact.json"), "utf8"));
    assert.equal(artifact.status, "qualified");
    assert.equal(artifact.artifactSha256, sha(withoutArtifactSha(artifact)));
    return { value, artifact, artifactSha256: artifact.artifactSha256, receipt };
  } catch (error) {
    await rm(value.root, { recursive: true, force: true });
    throw error;
  }
}

test("a passing openai/anthropic/openrouter trial preserves per-protocol reasoning and promotes a qualification", async () => {
  for (const lane of REMOTE_LANES) {
    const produced = await produceRemoteArtifact(lane);
    try {
      const { receipt, value } = produced;
      assert.equal(receipt.passedRepetitions, 6 * 3);
      const toolChoices = receipt.attestation.perCase.filter((e) => e.task === "tool-choice-continuation");
      assert.equal(toolChoices.length, 3);
      for (const evidence of toolChoices) {
        if (lane === "openrouter") {
          assert.equal(evidence.reasoningContentSha256, null, "openrouter reasoning is provider-dependent and never attested");
        } else {
          assert.match(evidence.reasoningContentSha256, /^[a-f0-9]{64}$/u);
          const expected = sha(laneReasoningEvidence(lane, toolChoiceFirstResponse(lane, value.model).message));
          assert.equal(evidence.reasoningContentSha256, expected, `${lane} reasoning evidence must be preserved verbatim`);
        }
      }
      const qual = receipt.semanticQualification;
      assert.equal(qual.providerId, lane);
      assert.equal(qual.model, value.model);
    } finally { await rm(produced.value.root, { recursive: true, force: true }); }
  }
});

test("anthropic qualification accepts one exact JSON fence while preserving strict semantic grading", async () => {
  const passing = await remoteFixture("anthropic");
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(remoteArgv(passing), fencedStructuredTransport("anthropic", passing.model));
    assert.equal(receipt.status, "qualified");
    assert.equal(receipt.passedRepetitions, 18);
    assert.equal(receipt.failedRepetitions, 0);
  } finally { await rm(passing.root, { recursive: true, force: true }); }

  const failing = await remoteFixture("anthropic");
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(remoteArgv(failing), fencedStructuredTransport("anthropic", failing.model, { wrongStructural: true }));
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.failedRepetitions, 3);
    assert.equal(receipt.attestation.perCase.filter((entry) => entry.task === "structural-review").every((entry) => entry.status === "failed"), true);
    assert.equal(receipt.semanticQualification, null);
  } finally { await rm(failing.root, { recursive: true, force: true }); }
});

test("remote lanes fail closed on promotion when no exact owner-private pricing binding exists", async () => {
  const lane = "openai", model = LANE_MODELS[lane];
  const value = await remoteFixture(lane);
  try {
    const noPricing = structuredClone(value.policy);
    delete noPricing.pricing;
    const noPricingPath = join(value.root, "no-pricing-policy.json");
    await writeFile(noPricingPath, `${canonical(noPricing)}
`, { mode: 0o600 });
    const noPricingSha = createHash("sha256").update(canonical(noPricing)).digest("hex");
    const argv = ["--lane", lane, "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", noPricingPath, "--sha256", noPricingSha, "--credential", value.credentialPath, "--proxy-route", "loopback", "--ledger-root", value.ledgerRoot, "--qualification-report", join(value.root, "qualification-artifact.json")];
    const receipt = await workProviderQualificationTestSeam.runWithTransport(argv, passingTransport(lane, model));
    assert.equal(receipt.status, "qualified", "the trial itself still qualifies");
    assert.equal(receipt.semanticQualification, null, "no router qualification without a sealed pricing binding");
    await assert.rejects(readFile(join(value.root, "qualification-artifact.json"), "utf8"), /ENOENT/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("remote lane qualification artifact drives the equivalence runner through the router", async () => {
  for (const lane of REMOTE_LANES) {
    const produced = await produceRemoteArtifact(lane);
    try {
      const root = await mkdtemp(join(tmpdir(), `pixel-equiv-${lane}-`));
      await chmod(root, 0o700);
      const ledgerRoot = join(root, "ledger");
      await mkdir(ledgerRoot, { mode: 0o700 });
      try {
        const argv = ["--lane", lane, "--ledger-root", ledgerRoot, "--qualification-report", join(produced.value.root, "qualification-artifact.json"), "--qualification-sha256", produced.artifactSha256, "--policy", produced.value.policyPath, "--sha256", produced.value.sha256, "--credential", produced.value.credentialPath, "--proxy-route", "loopback"];
        const report = await workProviderEquivalenceTestSeam.runWithTransport(argv, passingTransport(lane, produced.value.model));
        assert.equal(report.providerId, lane);
        assert.deepEqual(report.counters, { passed: 3, failed: 0, uncertain: 0 });
        assert.deepEqual(report.cases, ["structural-review-1", "patch-proposal-1", "failure-triage-1"]);
        assert.equal(report.qualificationSha256, produced.artifact.semanticQualification.qualificationSha256);
      } finally { await rm(root, { recursive: true, force: true }); }
    } finally { await rm(produced.value.root, { recursive: true, force: true }); }
  }
});

test("remote lanes fail closed on long-context and output-capacity token floors", async () => {
  const lane = "openai", model = LANE_MODELS[lane];
  const value = await remoteFixture(lane);
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane, model, input });
      if (admitted?.kind === "tool-choice-replay") {
        return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: model }, providerRequestId: "t2", networkBytes: 100 };
      }
      const caseEntry = findNeutralCorpusCase({ lane, model, input });
      if (caseEntry.task === "long-context-sentinel") {
        return { assistant: { message: { role: "assistant", content: LONG_CONTEXT_REQUIRED_REPLY }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_MIN_INPUT_TOKENS - 1, outputTokens: 3, totalTokens: LONG_CONTEXT_MIN_INPUT_TOKENS + 2 }, providerModel: model }, providerRequestId: "lc-low", networkBytes: 100 };
      }
      if (caseEntry.task === "output-capacity") {
        return { assistant: { message: { role: "assistant", content: OUTPUT_CAPACITY_REPLY }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS - 1, totalTokens: 40 + OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS }, providerModel: model }, providerRequestId: "oc-low", networkBytes: 100 };
      }
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse(lane, model), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(lane, model, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(remoteArgv(value), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const lc = receipt.attestation.perCase.filter((e) => e.task === "long-context-sentinel");
    const oc = receipt.attestation.perCase.filter((e) => e.task === "output-capacity");
    for (const evidence of lc) assert.equal(evidence.status, "failed");
    for (const evidence of oc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("remote lanes reject mismatched provider, model, host, credential, and qualification", async () => {
  const lane = "openai", model = LANE_MODELS[lane];
  const value = await remoteFixture(lane);
  try {
    // Lane vs bound private-policy provider mismatch.
    const anthropicPolicy = privatePolicyFor("anthropic");
    const otherPath = join(value.root, "other-policy.json");
    await writeFile(otherPath, `${canonical(anthropicPolicy)}\n`, { mode: 0o600 });
    const otherSha = createHash("sha256").update(canonical(anthropicPolicy)).digest("hex");
    await assert.rejects(
      workProviderQualificationTestSeam.runWithTransport(["--lane", "openai", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", otherPath, "--sha256", otherSha, "--credential", value.credentialPath, "--proxy-route", "loopback", "--ledger-root", value.ledgerRoot, "--qualification-report", join(value.root, "a.json")], passingTransport(lane, model)),
      /lane differs from the bound private policy/u,
    );
    // Host widening beyond the exact profile allowlist is rejected.
    const widened = privatePolicyFor(lane);
    widened.transport.allowedHosts = ["api.openai.com:443", "evil.example.com:443"];
    const widenedPath = join(value.root, "widened-policy.json");
    await writeFile(widenedPath, `${canonical(widened)}\n`, { mode: 0o600 });
    const widenedSha = createHash("sha256").update(canonical(widened)).digest("hex");
    await assert.rejects(
      workProviderQualificationTestSeam.runWithTransport(["--lane", lane, "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", widenedPath, "--sha256", widenedSha, "--credential", value.credentialPath, "--proxy-route", "loopback", "--ledger-root", value.ledgerRoot, "--qualification-report", join(value.root, "a.json")], passingTransport(lane, model)),
      /host allowlist|widens or changes the profile host/u,
    );
    // Credential custody must match the exact owner-private fileName.
    await assert.rejects(
      workProviderQualificationTestSeam.runWithTransport(remoteArgv({ ...value, credentialPath: join(value.root, "credential", "wrong-name") }), passingTransport(lane, model)),
      /credential/u,
    );
    // resolveProviderModel rejects a mismatched/placeholder model.
    const resolved = resolveWorkProvider("openrouter", { enabledRemoteProviders: ["openrouter"] });
    assert.throws(() => resolveProviderModel({ resolvedProvider: resolved, privatePolicy: { model: "provider-selected-model" }, model: LANE_MODELS.openrouter }), /requires an exact owner-private model/u);
    assert.throws(() => resolveProviderModel({ resolvedProvider: resolved, privatePolicy: { model: LANE_MODELS.openrouter }, model: "some-other-model" }), /run model differs from the pinned owner-private model/u);
    // Equivalence rejects a qualification artifact with mismatched provider/model.
    const produced = await produceRemoteArtifact(lane);
    try {
      const root = await mkdtemp(join(tmpdir(), `pixel-equiv-reject-${lane}-`));
      await chmod(root, 0o700);
      await mkdir(join(root, "ledger"), { mode: 0o700 });
      try {
        const tampered = structuredClone(produced.artifact);
        tampered.providerId = "anthropic";
        tampered.artifactSha256 = sha(withoutArtifactSha(tampered));
        const badPath = join(value.root, "bad-provider.json");
        await writeFile(badPath, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
        await assert.rejects(
          workProviderEquivalenceTestSeam.runWithTransport(["--lane", lane, "--ledger-root", join(root, "ledger"), "--qualification-report", badPath, "--qualification-sha256", tampered.artifactSha256, "--policy", value.policyPath, "--sha256", value.sha256, "--credential", value.credentialPath, "--proxy-route", "loopback"], passingTransport(lane, model)),
          /provider differs/u,
        );
      } finally { await rm(root, { recursive: true, force: true }); }
    } finally { await rm(produced.value.root, { recursive: true, force: true }); }
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

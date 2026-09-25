import assert from "node:assert/strict";
import test from "node:test";

import { buildAnthropicMessagesRequest, parseAnthropicMessagesResponse } from "../deploy/work-provider/adapters/anthropic-messages.mjs";
import { buildLocalOpenAiRequest, parseLocalOpenAiResponse } from "../deploy/work-provider/adapters/local-openai.mjs";
import { buildOpenAiChatRequest, parseOpenAiChatResponse } from "../deploy/work-provider/adapters/openai-chat.mjs";
import { buildOpenAiResponsesRequest, parseOpenAiResponsesResponse } from "../deploy/work-provider/adapters/openai-responses.mjs";

const tool = { name: "read_file", description: "Read one file", parameters: { type: "object", additionalProperties: false, required: ["path"], properties: { path: { type: "string" } } }, strict: true };
const base = { schemaVersion: 1, model: "fixture-model", messages: [{ role: "system", content: "Bounded fixture" }, { role: "user", content: "Inspect the file" }], tools: [tool], maxOutputTokens: 2048 };

test("Kimi K3 replay preserves the complete assistant message and reasoning_content", () => {
  const first = parseOpenAiChatResponse({
    id: "chatcmpl-fixture", object: "chat.completion", created: 1, model: "kimi-k3",
    choices: [{ index: 0, finish_reason: "tool_calls", logprobs: null, message: { role: "assistant", content: null, reasoning_content: "private-provider-state", tool_calls: [{ id: "call_1", type: "function", function: { name: "read_file", arguments: "{\"path\":\"README.md\"}" } }] } }],
    usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 },
  });
  const request = buildOpenAiChatRequest({ ...base, model: "kimi-k3", reasoningEffort: "low", messages: [...base.messages, first.message, { role: "tool", toolCallId: "call_1", content: "fixture result" }] }, { outputTokenField: "max_completion_tokens" });
  assert.deepEqual(request.messages[2], { role: "assistant", content: null, reasoning_content: "private-provider-state", tool_calls: [{ id: "call_1", type: "function", function: { name: "read_file", arguments: "{\"path\":\"README.md\"}" } }] });
  assert.deepEqual(request.messages[3], { role: "tool", tool_call_id: "call_1", content: "fixture result" });
  assert.equal(request.max_completion_tokens, 2048);
  assert.equal(request.reasoning_effort, "low");
  assert.equal(Object.hasOwn(request, "max_tokens"), false);
  assert.equal(first.message.content, null);
  assert.equal(first.message.providerState.protocol, "openai-chat-completions");
  assert.equal(first.finishReason, "tool_calls");
  assert.equal(first.usage.totalTokens, 120);
  assert.equal(Object.isFrozen(first.message.providerState.assistantMessage), true);
  assert.throws(() => { first.message.providerState.protocol = "local-openai-compatible"; }, TypeError);
});

test("OpenAI Responses replay preserves ordered reasoning, message, and function-call output items", () => {
  const output = [
    { type: "reasoning", id: "rs_1", encrypted_content: "encrypted-fixture", summary: [] },
    { type: "message", id: "msg_1", role: "assistant", status: "completed", content: [{ type: "output_text", text: "Checking." }] },
    { type: "function_call", id: "fc_1", call_id: "call_1", name: "read_file", arguments: "{\"path\":\"README.md\"}", status: "completed" },
  ];
  const parsed = parseOpenAiResponsesResponse({ id: "resp_1", object: "response", created_at: 1, completed_at: 2, status: "completed", error: null, incomplete_details: null, instructions: null, max_output_tokens: 2048, model: "gpt-fixture", output, parallel_tool_calls: true, previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1, usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 }, metadata: {} });
  const next = buildOpenAiResponsesRequest({ ...base, messages: [...base.messages, parsed.message, { role: "tool", toolCallId: "call_1", content: "fixture result" }] });
  assert.deepEqual(next.input.slice(2, 5), output);
  assert.deepEqual(next.input[5], { type: "function_call_output", call_id: "call_1", output: "fixture result" });
  assert.deepEqual(next.include, ["reasoning.encrypted_content"]);
  assert.equal(next.store, false);
  assert.equal(parsed.finishReason, "tool_calls");
});

test("OpenAI Responses does not silently force strict tool semantics", () => {
  const request = buildOpenAiResponsesRequest({ ...base, tools: [{ name: "read_file", parameters: { type: "object" } }] });
  assert.equal(Object.hasOwn(request.tools[0], "strict"), false);
});

test("OpenAI Responses maps neutral reasoning effort to the official reasoning object", () => {
  for (const effort of ["none", "low", "high", "max"]) {
    const request = buildOpenAiResponsesRequest({ ...base, reasoningEffort: effort });
    assert.deepEqual(request.reasoning, { effort });
  }
  assert.equal(Object.hasOwn(buildOpenAiResponsesRequest(base), "reasoning"), false);
});

test("Anthropic replay preserves thinking blocks and exact tool-use identity", () => {
  const content = [{ type: "thinking", thinking: "private-provider-state", signature: "sig" }, { type: "tool_use", id: "toolu_1", name: "read_file", input: { path: "README.md" } }];
  const parsed = parseAnthropicMessagesResponse({ id: "msg_1", type: "message", role: "assistant", model: "claude-fixture", content, stop_reason: "tool_use", stop_sequence: null, usage: { input_tokens: 20, output_tokens: 8 } });
  const next = buildAnthropicMessagesRequest({ ...base, messages: [...base.messages, parsed.message, { role: "tool", toolCallId: "toolu_1", content: "fixture result" }] });
  assert.deepEqual(next.messages[1].content, content);
  assert.deepEqual(next.messages[2], { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_1", content: "fixture result" }] });
  assert.equal(next.system, "Bounded fixture");
  assert.equal(parsed.finishReason, "tool_calls");
});

test("Anthropic preserves current stop_details and validates refusal coupling", () => {
  const normal = parseAnthropicMessagesResponse({ id: "msg_normal", type: "message", role: "assistant", model: "claude-fixture", content: [{ type: "text", text: "done" }], stop_reason: "end_turn", stop_sequence: null, stop_details: null, usage: { input_tokens: 2, output_tokens: 1 } });
  assert.equal(normal.finishReason, "stop");
  assert.equal(normal.message.providerState.stopDetails, null);

  const details = { type: "refusal", category: "fixture", explanation: "fixture explanation" };
  const refusal = parseAnthropicMessagesResponse({ id: "msg_refusal", type: "message", role: "assistant", model: "claude-fixture", content: [], stop_reason: "refusal", stop_sequence: null, stop_details: details, usage: { input_tokens: 2, output_tokens: 0 } });
  assert.equal(refusal.finishReason, "content_filter");
  assert.equal(refusal.message.content, null);
  assert.deepEqual(refusal.message.providerState.stopDetails, details);

  assert.throws(() => parseAnthropicMessagesResponse({ id: "msg_bad", type: "message", role: "assistant", model: "claude-fixture", content: [{ type: "text", text: "done" }], stop_reason: "end_turn", stop_sequence: null, stop_details: details, usage: { input_tokens: 2, output_tokens: 1 } }), /valid only for refusal/u);
  assert.throws(() => parseAnthropicMessagesResponse({ id: "msg_bad", type: "message", role: "assistant", model: "claude-fixture", content: [], stop_reason: "refusal", stop_sequence: null, stop_details: null, usage: { input_tokens: 2, output_tokens: 0 } }), /must be an object/u);
  assert.throws(() => parseAnthropicMessagesResponse({ id: "msg_bad", type: "message", role: "assistant", model: "claude-fixture", content: [], stop_reason: "refusal", stop_sequence: null, stop_details: { ...details, surprise: true }, usage: { input_tokens: 2, output_tokens: 0 } }), /unsupported field surprise/u);
});

test("local adapter retains the existing hidden-reasoning boundary", () => {
  const safe = { id: "chatcmpl-local", object: "chat.completion", created: 1, model: "local", choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "done" } }], usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } };
  const parsed = parseLocalOpenAiResponse(safe);
  assert.equal(parsed.message.providerState.protocol, "local-openai-compatible");
  assert.equal(buildLocalOpenAiRequest({ ...base, messages: [...base.messages, parsed.message] }).messages[2].content, "done");
  const leaked = structuredClone(safe); leaked.choices[0].message.reasoning_content = "must-not-cross";
  assert.throws(() => parseLocalOpenAiResponse(leaked), /forbidden reasoning_content/u);
  for (const field of ["reasoning", "reasoning_content"]) {
    const state = { protocol: "local-openai-compatible", assistantMessage: { role: "assistant", content: "done", [field]: "must-not-replay" }, finishReason: "stop" };
    assert.throws(() => buildLocalOpenAiRequest({ ...base, messages: [...base.messages, { role: "assistant", content: "done", providerState: state }] }), /cannot expose provider reasoning/u);
  }
});

test("local adapter can explicitly disable hidden reasoning for usable-output controls", () => {
  const request = buildLocalOpenAiRequest({ ...base, reasoningEffort: "none" });
  assert.equal(request.reasoning_effort, "none");
});

test("local adapter accepts only the exact null-only vLLM metadata shape and discards private reasoning", () => {
  const liveShape = {
    id: "chatcmpl-local-vllm", object: "chat.completion", created: 1, model: "DeepSeek-V4-Flash-0731",
    ec_transfer_params: null, kv_transfer_params: null, metrics: null, prompt_logprobs: null, prompt_text: null, prompt_token_ids: null,
    service_tier: null, system_fingerprint: null,
    choices: [{ index: 0, finish_reason: "stop", logprobs: null, routed_experts: null, stop_reason: null, token_ids: null, message: {
      annotations: null, audio: null, content: "neutral fixture", function_call: null,
      reasoning: "private local reasoning fixture", refusal: null, role: "assistant",
    } }],
    usage: { prompt_tokens: 18, completion_tokens: 41, total_tokens: 59, prompt_tokens_details: { cached_tokens: 0 } },
  };
  const parsed = parseLocalOpenAiResponse(liveShape);
  assert.equal(parsed.providerModel, "DeepSeek-V4-Flash-0731");
  assert.deepEqual(parsed.usage, { inputTokens: 18, outputTokens: 41, totalTokens: 59 });
  assert.equal(parsed.message.content, "neutral fixture");
  assert.equal(parsed.message.providerState.protocol, "local-openai-compatible");
  for (const field of ["annotations", "audio", "function_call", "reasoning", "reasoning_content"]) {
    assert.equal(Object.hasOwn(parsed.message.providerState.assistantMessage, field), false);
  }
  assert.doesNotMatch(JSON.stringify(parsed), /private local reasoning fixture/u);

  for (const field of ["ec_transfer_params", "kv_transfer_params", "metrics", "prompt_logprobs", "prompt_text", "prompt_token_ids"]) {
    const invalid = structuredClone(liveShape); invalid[field] = field === "prompt_token_ids" ? [1] : "not-null";
    assert.throws(() => parseLocalOpenAiResponse(invalid), new RegExp(`${field} must be null`, "u"));
  }
  for (const field of ["annotations", "audio", "function_call"]) {
    const invalid = structuredClone(liveShape); invalid.choices[0].message[field] = {};
    assert.throws(() => parseLocalOpenAiResponse(invalid), new RegExp(`${field} must be null`, "u"));
  }
  for (const field of ["routed_experts", "stop_reason", "token_ids"]) {
    const invalid = structuredClone(liveShape); invalid.choices[0][field] = field === "token_ids" ? [1] : "not-null";
    assert.throws(() => parseLocalOpenAiResponse(invalid), new RegExp(`${field} must be null`, "u"));
  }
  const invalidReasoning = structuredClone(liveShape); invalidReasoning.choices[0].message.reasoning = { hidden: true };
  assert.throws(() => parseLocalOpenAiResponse(invalidReasoning), /reasoning is invalid/u);
  const leaked = structuredClone(liveShape); leaked.choices[0].message.reasoning_content = "must-not-cross";
  assert.throws(() => parseLocalOpenAiResponse(leaked), /forbidden reasoning_content/u);
  const unknown = structuredClone(liveShape); unknown.unexpected_vllm_extension = null;
  assert.throws(() => parseLocalOpenAiResponse(unknown), /unsupported field unexpected_vllm_extension/u);
});

test("adapters fail closed on unknown response fields and malformed tool bindings", () => {
  assert.throws(() => parseOpenAiChatResponse({ choices: [], unexpected: true }), /unsupported field/u);
  assert.throws(() => buildOpenAiChatRequest({ ...base, messages: [{ role: "tool", content: "missing id" }] }), /requires toolCallId/u);
  const invalid = structuredClone(base); invalid.messages[0].surprise = true;
  assert.throws(() => buildOpenAiResponsesRequest(invalid), /unsupported field surprise/u);
  const invalidAnthropic = structuredClone(base);
  invalidAnthropic.messages.push({ role: "assistant", content: null, toolCalls: [{ id: "call_1", name: "read_file", arguments: "{" }] });
  assert.throws(() => buildAnthropicMessagesRequest(invalidAnthropic), /not valid JSON/u);
});

test("Anthropic maps neutral tool choices without leaking OpenAI wire shapes", () => {
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, toolChoice: "auto" }).tool_choice, { type: "auto" });
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, toolChoice: "required" }).tool_choice, { type: "any" });
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, toolChoice: { type: "function", function: { name: "read_file" } } }).tool_choice, { type: "tool", name: "read_file" });
});

test("Anthropic maps neutral reasoning effort to legacy manual thinking and never silently drops it", () => {
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, reasoningEffort: "none" }).thinking, { type: "disabled" });
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, reasoningEffort: "low" }).thinking, { type: "enabled", budget_tokens: 1024 });
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, maxOutputTokens: 16384, reasoningEffort: "high" }).thinking, { type: "enabled", budget_tokens: 4096 });
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, maxOutputTokens: 16384, reasoningEffort: "max" }).thinking, { type: "enabled", budget_tokens: 8192 });
  assert.equal(Object.hasOwn(buildAnthropicMessagesRequest(base), "thinking"), false);
});

test("Anthropic extended thinking fails closed when the output budget cannot hold its required token budget", () => {
  // budget_tokens must be at least 1024 and strictly below max_tokens.
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, reasoningEffort: "low", maxOutputTokens: 1024 }), /strictly above.*1024/u);
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, reasoningEffort: "low", maxOutputTokens: 512 }), /strictly above.*1024/u);
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, reasoningEffort: "high", maxOutputTokens: 4096 }), /strictly above.*4096/u);
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, reasoningEffort: "max", maxOutputTokens: 8192 }), /strictly above.*8192/u);
  // The default max_tokens is 4096, so high/max cannot fit; low can.
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, maxOutputTokens: undefined, reasoningEffort: "high" }), /strictly above.*4096/u);
  assert.equal(buildAnthropicMessagesRequest({ ...base, maxOutputTokens: undefined, reasoningEffort: "low" }).thinking.budget_tokens, 1024);
  // One token of headroom satisfies the strict-below rule.
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, reasoningEffort: "low", maxOutputTokens: 1025 }).thinking, { type: "enabled", budget_tokens: 1024 });
});

test("Anthropic extended thinking fails closed on temperature conflicts but none remains temperature-free", () => {
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, reasoningEffort: "low", temperature: 0.7 }), /temperature omitted or exactly 1/u);
  assert.throws(() => buildAnthropicMessagesRequest({ ...base, maxOutputTokens: 16384, reasoningEffort: "max", temperature: 2 }), /temperature omitted or exactly 1/u);
  // Exactly 1 is allowed alongside enabled thinking.
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, maxOutputTokens: 16384, reasoningEffort: "max", temperature: 1 }).thinking, { type: "enabled", budget_tokens: 8192 });
  // Disabled thinking permits any temperature.
  assert.deepEqual(buildAnthropicMessagesRequest({ ...base, reasoningEffort: "none", temperature: 0.3 }).thinking, { type: "disabled" });
});

test("provider-native terminal states normalize without losing private response state", () => {
  const failed = parseOpenAiResponsesResponse({
    id: "resp_failed", object: "response", created_at: 1, completed_at: 2, status: "failed", error: { code: "provider_error", message: "fixture" },
    incomplete_details: null, instructions: null, max_output_tokens: 100, model: "gpt-fixture", output: [], parallel_tool_calls: false,
    previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1,
    usage: { input_tokens: 2, output_tokens: 0, total_tokens: 2 }, metadata: {},
  });
  assert.equal(failed.finishReason, "error");
  assert.equal(failed.message.providerState.responseState.status, "failed");
  assert.equal(failed.message.content, null);
  assert.throws(() => parseOpenAiResponsesResponse({
    id: "resp_queued", object: "response", created_at: 1, completed_at: null, status: "queued", error: null,
    incomplete_details: null, instructions: null, max_output_tokens: 100, model: "gpt-fixture", output: [], parallel_tool_calls: false,
    previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1,
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 }, metadata: {},
  }), /non-terminal/u);
});

test("adapters capture and bound the provider-reported model and reject a present-but-invalid one", () => {
  const chat = parseOpenAiChatResponse({ id: "chatcmpl-m", object: "chat.completion", created: 1, model: "kimi-k3", choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "x" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } });
  assert.equal(chat.providerModel, "kimi-k3");
  const responses = parseOpenAiResponsesResponse({
    id: "resp_m", object: "response", created_at: 1, completed_at: 2, status: "completed", error: null, incomplete_details: null,
    instructions: null, max_output_tokens: 100, model: "gpt-fixture", output: [{ type: "message", id: "m1", role: "assistant", status: "completed", content: [{ type: "output_text", text: "hi" }] }],
    parallel_tool_calls: false, previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1,
    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 }, metadata: {},
  });
  assert.equal(responses.providerModel, "gpt-fixture");
  const anthropic = parseAnthropicMessagesResponse({ id: "msg_m", type: "message", role: "assistant", model: "claude-fixture", content: [{ type: "text", text: "hi" }], stop_reason: "end_turn", stop_sequence: null, usage: { input_tokens: 1, output_tokens: 1 } });
  assert.equal(anthropic.providerModel, "claude-fixture");
  const local = parseLocalOpenAiResponse({ id: "chatcmpl-local", object: "chat.completion", created: 1, model: "local", choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "done" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } });
  assert.equal(local.providerModel, "local");
  // A missing model normalizes to null (the transport decides); a present-but-invalid one fails.
  const missing = { id: "chatcmpl-x", object: "chat.completion", created: 1, choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "x" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } };
  assert.equal(parseOpenAiChatResponse(missing).providerModel, null);
  assert.throws(() => parseOpenAiChatResponse({ ...missing, model: "" }), /provider model is invalid/u);
  assert.throws(() => parseOpenAiChatResponse({ ...missing, model: 123 }), /provider model is invalid/u);
});

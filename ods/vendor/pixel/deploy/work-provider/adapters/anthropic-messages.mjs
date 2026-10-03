import {
  WorkProviderAdapterError, assertPlainProviderObject, assertProviderKeys, cloneProviderJson,
  normalizedAssistant, validateNeutralRequest,
} from "../adapter-contract.mjs";

const RESPONSE_KEYS = ["content", "id", "model", "role", "stop_details", "stop_reason", "stop_sequence", "type", "usage"];
const BLOCK_TYPES = new Set(["redacted_thinking", "text", "thinking", "tool_use"]);

function fail(message) { throw new WorkProviderAdapterError(message); }
// Sonnet 4.5 exposes only legacy manual thinking: an enabled thinking block must
// carry a budget_tokens of at least 1024 that is strictly below max_tokens, and a
// thinking-enabled request must omit temperature or set it to exactly 1.  A
// disabled request uses thinking: { type: "disabled" }.  This mapping never lowers
// the caller's max_tokens ceiling; if a requested effort cannot fit under the
// effective ceiling with a valid budget it fails closed rather than silently
// dropping the neutral reasoning effort or inventing provider capability.
const THINKING_BUDGET_TOKENS = Object.freeze({ low: 1024, high: 4096, max: 8192 });

function anthropicThinking(reasoningEffort, maxTokens, temperature) {
  if (reasoningEffort === "none") return { type: "disabled" };
  const budgetTokens = THINKING_BUDGET_TOKENS[reasoningEffort];
  if (budgetTokens === undefined) fail("Anthropic reasoning effort is unsupported");
  if (budgetTokens >= maxTokens) {
    fail(`Anthropic extended thinking requires max_tokens strictly above its ${budgetTokens}-token budget; got ${maxTokens}`);
  }
  if (temperature !== undefined && temperature !== 1) {
    fail("Anthropic extended thinking requires temperature omitted or exactly 1");
  }
  return { type: "enabled", budget_tokens: budgetTokens };
}
function toolInput(argumentsText, label) {
  let value;
  try { value = JSON.parse(argumentsText); } catch { fail(`${label} arguments are not valid JSON`); }
  assertPlainProviderObject(value, `${label} arguments`);
  return value;
}
function anthropicToolChoice(value) {
  if (typeof value === "string") {
    if (value === "auto") return { type: "auto" };
    if (value === "required") return { type: "any" };
    if (value === "none") fail("Anthropic does not support tool choice none when tools are present");
    fail("Anthropic tool choice is invalid");
  }
  if (value?.type === "function" && typeof value.function?.name === "string") return { type: "tool", name: value.function.name };
  if (["any", "auto"].includes(value?.type)) return cloneProviderJson(value, "tool choice");
  if (value?.type === "tool" && typeof value.name === "string") return cloneProviderJson(value, "tool choice");
  fail("Anthropic tool choice is invalid");
}
function contentFor(message) {
  if (message.role === "assistant" && message.providerState !== undefined) {
    assertProviderKeys(message.providerState, ["content", "protocol", "stopDetails", "stopReason"], "Anthropic provider state");
    if (message.providerState.protocol !== "anthropic-messages" || !Array.isArray(message.providerState.content)) fail("Anthropic provider state is invalid");
    return cloneProviderJson(message.providerState.content, "Anthropic assistant content");
  }
  if (message.role === "tool") return [{ type: "tool_result", tool_use_id: message.toolCallId, content: typeof message.content === "string" ? message.content : JSON.stringify(message.content) }];
  if (message.role === "assistant" && message.toolCalls?.length) {
    const blocks = message.content ? [{ type: "text", text: message.content }] : [];
    blocks.push(...message.toolCalls.map((call, index) => ({ type: "tool_use", id: call.id, name: call.name, input: toolInput(call.arguments, `tool call ${index}`) })));
    return blocks;
  }
  return message.content;
}

export function buildAnthropicMessagesRequest(input) {
  const request = validateNeutralRequest(input);
  const maxTokens = request.maxOutputTokens ?? 4096;
  const systemMessages = request.messages.filter((message) => ["developer", "system"].includes(message.role));
  if (systemMessages.some((message) => typeof message.content !== "string")) fail("Anthropic system and developer messages must contain text");
  const system = systemMessages.map((message) => message.content).join("\n\n");
  const messages = request.messages.filter((message) => !["developer", "system"].includes(message.role)).map((message) => ({ role: message.role === "tool" ? "user" : message.role, content: contentFor(message) }));
  return {
    model: request.model,
    messages,
    max_tokens: maxTokens,
    ...(system ? { system } : {}),
    ...(request.tools === undefined ? {} : { tools: request.tools.map((tool) => ({ name: tool.name, description: tool.description ?? "", input_schema: cloneProviderJson(tool.parameters, "tool parameters") })) }),
    ...(request.toolChoice === undefined ? {} : { tool_choice: anthropicToolChoice(request.toolChoice) }),
    ...(request.temperature === undefined ? {} : { temperature: request.temperature }),
    ...(request.reasoningEffort === undefined ? {} : { thinking: anthropicThinking(request.reasoningEffort, maxTokens, request.temperature) }),
  };
}

function parseUsage(value) {
  assertPlainProviderObject(value, "Anthropic usage");
  if (!Number.isInteger(value.input_tokens) || value.input_tokens < 0 || !Number.isInteger(value.output_tokens) || value.output_tokens < 0) fail("Anthropic usage is invalid");
  return { inputTokens: value.input_tokens, outputTokens: value.output_tokens, totalTokens: value.input_tokens + value.output_tokens };
}

function finishReason(value) {
  if (["end_turn", "stop_sequence"].includes(value)) return "stop";
  if (value === "tool_use") return "tool_calls";
  if (["max_tokens", "model_context_window_exceeded"].includes(value)) return "length";
  if (value === "refusal") return "content_filter";
  if (value === "pause_turn") return "incomplete";
  return "other";
}

function stopDetails(reason, value) {
  if (reason !== "refusal") {
    if (value !== undefined && value !== null) fail("Anthropic stop_details is valid only for refusal");
    return null;
  }
  assertPlainProviderObject(value, "Anthropic refusal stop_details");
  assertProviderKeys(value, ["category", "explanation", "type"], "Anthropic refusal stop_details");
  if (value.type !== "refusal") fail("Anthropic refusal stop_details type is invalid");
  for (const [field, maximum] of [["category", 256], ["explanation", 8192]]) {
    const observed = value[field];
    if (observed !== null && (typeof observed !== "string" || Buffer.byteLength(observed, "utf8") > maximum || observed.includes("\u0000"))) {
      fail(`Anthropic refusal stop_details ${field} is invalid`);
    }
  }
  return cloneProviderJson(value, "Anthropic refusal stop_details");
}

export function parseAnthropicMessagesResponse(value) {
  assertPlainProviderObject(value, "Anthropic response");
  assertProviderKeys(value, RESPONSE_KEYS, "Anthropic response");
  if (value.role !== "assistant" || !Array.isArray(value.content) || value.content.length === 0 && value.stop_reason !== "refusal") fail("Anthropic response content is invalid");
  const parsedStopDetails = stopDetails(value.stop_reason, value.stop_details);
  const text = [], toolCalls = [];
  for (const [index, block] of value.content.entries()) {
    assertPlainProviderObject(block, `Anthropic content ${index}`);
    if (!BLOCK_TYPES.has(block.type)) fail(`Anthropic content ${index} has an unsupported type`);
    if (block.type === "text") {
      if (typeof block.text !== "string") fail(`Anthropic text block ${index} is invalid`);
      text.push(block.text);
    } else if (block.type === "tool_use") {
      if (typeof block.id !== "string" || typeof block.name !== "string" || !assertPlainProviderObject(block.input, `Anthropic tool input ${index}`)) fail(`Anthropic tool block ${index} is invalid`);
      toolCalls.push({ id: block.id, name: block.name, arguments: JSON.stringify(block.input) });
    }
  }
  return normalizedAssistant({
    content: text.length ? text.join("") : null,
    toolCalls,
    providerState: { protocol: "anthropic-messages", content: cloneProviderJson(value.content, "Anthropic content"), stopDetails: parsedStopDetails, stopReason: value.stop_reason ?? null },
    finishReason: finishReason(value.stop_reason),
    usage: parseUsage(value.usage),
    providerModel: value.model ?? null,
  });
}

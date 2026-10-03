import {
  WorkProviderAdapterError, assertPlainProviderObject, assertProviderKeys, cloneProviderJson,
  normalizedAssistant, validateNeutralRequest,
} from "../adapter-contract.mjs";

const ASSISTANT_KEYS = ["content", "reasoning_content", "refusal", "role", "tool_calls"];
const RESPONSE_KEYS = ["choices", "citations", "created", "id", "model", "object", "service_tier", "system_fingerprint", "usage", "x_groq"];
const CHOICE_KEYS = ["finish_reason", "index", "logprobs", "message"];
const TOOL_CALL_KEYS = ["function", "id", "index", "type"];
const FUNCTION_KEYS = ["arguments", "name"];

function fail(message) { throw new WorkProviderAdapterError(message); }
function assistantMessage(message) {
  const saved = message.providerState;
  if (saved !== undefined) {
    assertProviderKeys(saved, ["assistantMessage", "finishReason", "protocol"], "OpenAI chat provider state");
    if (saved.protocol !== "openai-chat-completions") fail("OpenAI chat provider state has the wrong protocol");
    const raw = cloneProviderJson(saved.assistantMessage, "OpenAI chat assistant message");
    assertProviderKeys(raw, ASSISTANT_KEYS, "OpenAI chat assistant message");
    if (raw.role !== "assistant") fail("OpenAI chat saved message has the wrong role");
    return raw;
  }
  const output = { role: "assistant", content: message.content };
  if (message.toolCalls?.length) output.tool_calls = message.toolCalls.map((call) => ({ id: call.id, type: "function", function: { name: call.name, arguments: call.arguments } }));
  return output;
}

function openAiTool(tool) {
  return { type: "function", function: { name: tool.name, description: tool.description ?? "", parameters: cloneProviderJson(tool.parameters, "tool parameters"), ...(tool.strict === undefined ? {} : { strict: tool.strict }) } };
}

export function buildOpenAiChatRequest(input, { outputTokenField = "max_tokens" } = {}) {
  if (!new Set(["max_completion_tokens", "max_tokens"]).has(outputTokenField)) fail("OpenAI chat output-token field is invalid");
  const request = validateNeutralRequest(input);
  const messages = request.messages.map((message) => {
    if (message.role === "assistant") return assistantMessage(message);
    if (message.role === "tool") return { role: "tool", tool_call_id: message.toolCallId, content: message.content ?? "" };
    return { role: message.role, content: message.content };
  });
  return {
    model: request.model,
    messages,
    ...(request.tools === undefined ? {} : { tools: request.tools.map(openAiTool) }),
    ...(request.toolChoice === undefined ? {} : { tool_choice: cloneProviderJson(request.toolChoice, "tool choice") }),
    ...(request.maxOutputTokens === undefined ? {} : { [outputTokenField]: request.maxOutputTokens }),
    ...(request.reasoningEffort === undefined ? {} : { reasoning_effort: request.reasoningEffort }),
    ...(request.temperature === undefined ? {} : { temperature: request.temperature }),
    stream: false,
  };
}

function parseToolCall(value, index) {
  assertPlainProviderObject(value, `OpenAI chat tool call ${index}`);
  assertProviderKeys(value, TOOL_CALL_KEYS, `OpenAI chat tool call ${index}`);
  if (value.type !== "function" || typeof value.id !== "string") fail(`OpenAI chat tool call ${index} is invalid`);
  assertPlainProviderObject(value.function, `OpenAI chat tool call ${index} function`);
  assertProviderKeys(value.function, FUNCTION_KEYS, `OpenAI chat tool call ${index} function`);
  if (typeof value.function.name !== "string" || typeof value.function.arguments !== "string") fail(`OpenAI chat tool call ${index} function is invalid`);
  return { id: value.id, name: value.function.name, arguments: value.function.arguments };
}

function parseUsage(value) {
  if (value === undefined) return null;
  assertPlainProviderObject(value, "OpenAI chat usage");
  const inputTokens = value.prompt_tokens, outputTokens = value.completion_tokens;
  if (!Number.isInteger(inputTokens) || inputTokens < 0 || !Number.isInteger(outputTokens) || outputTokens < 0) fail("OpenAI chat usage is invalid");
  return { inputTokens, outputTokens, totalTokens: Number.isInteger(value.total_tokens) ? value.total_tokens : inputTokens + outputTokens };
}

function finishReason(value) {
  if (value === "stop") return "stop";
  if (["tool_calls", "function_call"].includes(value)) return "tool_calls";
  if (value === "length") return "length";
  if (value === "content_filter") return "content_filter";
  return "other";
}

export function parseOpenAiChatResponse(value) {
  assertPlainProviderObject(value, "OpenAI chat response");
  assertProviderKeys(value, RESPONSE_KEYS, "OpenAI chat response");
  if (!Array.isArray(value.choices) || value.choices.length !== 1) fail("OpenAI chat response must contain exactly one choice");
  const choice = value.choices[0];
  assertPlainProviderObject(choice, "OpenAI chat choice");
  assertProviderKeys(choice, CHOICE_KEYS, "OpenAI chat choice");
  const message = choice.message;
  assertPlainProviderObject(message, "OpenAI chat assistant message");
  assertProviderKeys(message, ASSISTANT_KEYS, "OpenAI chat assistant message");
  if (message.role !== "assistant" || message.content !== null && typeof message.content !== "string") fail("OpenAI chat assistant message is invalid");
  if (message.reasoning_content !== undefined && typeof message.reasoning_content !== "string") fail("OpenAI chat reasoning_content is invalid");
  if (message.refusal !== undefined && message.refusal !== null && typeof message.refusal !== "string") fail("OpenAI chat refusal is invalid");
  if (message.tool_calls !== undefined && !Array.isArray(message.tool_calls)) fail("OpenAI chat tool calls are invalid");
  if (choice.finish_reason !== undefined && choice.finish_reason !== null && typeof choice.finish_reason !== "string") fail("OpenAI chat finish reason is invalid");
  const toolCalls = message.tool_calls === undefined ? [] : message.tool_calls.map(parseToolCall);
  return normalizedAssistant({
    content: message.content,
    toolCalls,
    providerState: { protocol: "openai-chat-completions", assistantMessage: cloneProviderJson(message, "OpenAI chat assistant message"), finishReason: choice.finish_reason ?? null },
    finishReason: finishReason(choice.finish_reason),
    usage: parseUsage(value.usage),
    providerModel: value.model ?? null,
  });
}

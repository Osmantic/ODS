const REQUEST_KEYS = new Set(["schemaVersion", "model", "messages", "tools", "toolChoice", "maxOutputTokens", "reasoningEffort", "temperature"]);
const MESSAGE_KEYS = new Set(["role", "content", "toolCalls", "toolCallId", "providerState"]);
const TOOL_KEYS = new Set(["name", "description", "parameters", "strict"]);
const TOOL_CALL_KEYS = new Set(["id", "name", "arguments"]);
const ROLES = new Set(["assistant", "developer", "system", "tool", "user"]);
const PROVIDER_PROTOCOLS = new Set(["anthropic-messages", "local-openai-compatible", "openai-chat-completions", "openai-responses"]);
const FINISH_REASONS = new Set(["content_filter", "error", "incomplete", "length", "other", "stop", "tool_calls"]);

export class WorkProviderAdapterError extends Error {}
function fail(message) { throw new WorkProviderAdapterError(message); }
function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function boundedText(value, label, maximum = 1048576, allowEmpty = true) {
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > maximum || !allowEmpty && value.length === 0 || /[\u0000]/u.test(value)) fail(`${label} is invalid`);
  return value;
}
function exactKeys(value, allowed, label) {
  if (!plain(value)) fail(`${label} must be an object`);
  for (const key of Object.keys(value)) if (!allowed.has(key) || key === "__proto__") fail(`${label} contains unsupported field ${key}`);
}
function jsonClone(value, label) {
  try { return JSON.parse(JSON.stringify(value)); } catch { fail(`${label} is not bounded JSON`); }
}
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}
function immutableJson(value, label) { return deepFreeze(jsonClone(value, label)); }

export function validateToolCall(value, label = "tool call") {
  exactKeys(value, TOOL_CALL_KEYS, label);
  boundedText(value.id, `${label}.id`, 256, false);
  boundedText(value.name, `${label}.name`, 256, false);
  boundedText(value.arguments, `${label}.arguments`, 1048576, true);
  return immutableJson(value, label);
}

export function validateNeutralRequest(value) {
  exactKeys(value, REQUEST_KEYS, "provider request");
  if (value.schemaVersion !== 1) fail("provider request schemaVersion must be 1");
  boundedText(value.model, "provider request model", 256, false);
  if (!Array.isArray(value.messages) || value.messages.length < 1 || value.messages.length > 4096) fail("provider request messages are invalid");
  for (const [index, message] of value.messages.entries()) {
    exactKeys(message, MESSAGE_KEYS, `message ${index}`);
    if (!ROLES.has(message.role)) fail(`message ${index} has an invalid role`);
    if (message.content !== null && typeof message.content !== "string" && !Array.isArray(message.content)) fail(`message ${index} content is invalid`);
    if (typeof message.content === "string") boundedText(message.content, `message ${index} content`);
    if (Array.isArray(message.content) && Buffer.byteLength(JSON.stringify(message.content), "utf8") > 2097152) fail(`message ${index} content is oversized`);
    if (message.toolCalls !== undefined) {
      if (message.role !== "assistant" || !Array.isArray(message.toolCalls) || message.toolCalls.length > 128) fail(`message ${index} tool calls are invalid`);
      message.toolCalls.forEach((call, callIndex) => validateToolCall(call, `message ${index} tool call ${callIndex}`));
    }
    if (message.toolCallId !== undefined) {
      if (message.role !== "tool") fail(`message ${index} toolCallId requires tool role`);
      boundedText(message.toolCallId, `message ${index} toolCallId`, 256, false);
    } else if (message.role === "tool") fail(`message ${index} tool role requires toolCallId`);
    if (message.providerState !== undefined) {
      if (message.role !== "assistant" || !plain(message.providerState) || !PROVIDER_PROTOCOLS.has(message.providerState.protocol)) fail(`message ${index} provider state is invalid`);
      if (Buffer.byteLength(JSON.stringify(message.providerState), "utf8") > 4194304) fail(`message ${index} provider state is oversized`);
    }
  }
  if (value.tools !== undefined) {
    if (!Array.isArray(value.tools) || value.tools.length > 128) fail("provider request tools are invalid");
    for (const [index, tool] of value.tools.entries()) {
      exactKeys(tool, TOOL_KEYS, `tool ${index}`);
      boundedText(tool.name, `tool ${index} name`, 256, false);
      if (tool.description !== undefined) boundedText(tool.description, `tool ${index} description`, 8192);
      if (!plain(tool.parameters)) fail(`tool ${index} parameters are invalid`);
      if (tool.strict !== undefined && typeof tool.strict !== "boolean") fail(`tool ${index} strict flag is invalid`);
    }
    if (new Set(value.tools.map((tool) => tool.name)).size !== value.tools.length) fail("provider request tool names must be unique");
  }
  if (value.toolChoice !== undefined && typeof value.toolChoice !== "string" && !plain(value.toolChoice)) fail("provider request toolChoice is invalid");
  if (value.maxOutputTokens !== undefined && (!Number.isInteger(value.maxOutputTokens) || value.maxOutputTokens < 1 || value.maxOutputTokens > 1000000)) fail("provider request maxOutputTokens is invalid");
  if (value.reasoningEffort !== undefined && !["none", "low", "high", "max"].includes(value.reasoningEffort)) fail("provider request reasoningEffort is invalid");
  if (value.temperature !== undefined && (typeof value.temperature !== "number" || !Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2)) fail("provider request temperature is invalid");
  return immutableJson(value, "provider request");
}

export function normalizedAssistant({ content = null, toolCalls = [], providerState, finishReason = null, usage = null, providerModel = null }) {
  if (content !== null) boundedText(content, "assistant content");
  if (finishReason !== null && !FINISH_REASONS.has(finishReason)) fail("assistant finish reason is invalid");
  if (providerModel !== null) boundedText(providerModel, "assistant provider model", 256, false);
  const message = { role: "assistant", content };
  if (toolCalls.length) message.toolCalls = toolCalls.map((call) => validateToolCall(call));
  if (providerState !== undefined) message.providerState = immutableJson(providerState, "provider state");
  let normalizedUsage = null;
  if (usage !== null) {
    exactKeys(usage, new Set(["inputTokens", "outputTokens", "totalTokens"]), "assistant usage");
    for (const field of ["inputTokens", "outputTokens", "totalTokens"]) {
      if (!Number.isInteger(usage[field]) || usage[field] < 0) fail(`assistant usage ${field} is invalid`);
    }
    normalizedUsage = immutableJson(usage, "assistant usage");
  }
  return deepFreeze({ message, finishReason, usage: normalizedUsage, providerModel });
}

export function cloneProviderJson(value, label = "provider value") { return jsonClone(value, label); }
export function assertPlainProviderObject(value, label = "provider value") { if (!plain(value)) fail(`${label} must be an object`); return value; }
export function assertProviderKeys(value, allowed, label) { exactKeys(value, new Set(allowed), label); return value; }

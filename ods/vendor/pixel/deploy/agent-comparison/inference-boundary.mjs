import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { isIP } from "node:net";
import { constants } from "node:fs";
import { lstat, open, rename, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { normalizeVllmChatRequest, validateExactInferencePolicy } from "../work-model-proxy/inference-policy.mjs";

const RUN_RE = /^outcomerun-[0-9]{13}-[a-f0-9]{12}$/u;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/u;
const TOOL_RE = /^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/u;
const CALL_ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/u;
const RESPONSES_REQUEST_KEYS = new Set([
  "model", "instructions", "input", "tools", "tool_choice", "parallel_tool_calls", "reasoning",
  "store", "stream", "include", "prompt_cache_key", "client_metadata", "max_output_tokens", "text",
]);
const CONFIG_KEYS = Object.freeze([
  "schemaVersion", "runId", "modelId", "backendOrigin", "listenHost", "listenPort", "receiptPath",
  "maxRequestBytes", "maxResponseBytes", "maxRequestSeconds", "maxModelRequests", "maxInputTokens",
  "maxOutputTokens", "ingressWireApi", "inference",
]);
const UTF8 = new TextDecoder("utf-8", { fatal: true });

class BoundaryError extends Error {
  constructor(code, message = code) { super(message); this.name = "BoundaryError"; this.code = code; }
}

function fail(code, message = code) { throw new BoundaryError(code, message); }

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail("invalid-config", `${label} is invalid`);
  return value;
}

function privateIpv4(value) {
  if (isIP(value) !== 4) return false;
  const parts = value.split(".").map(Number);
  return parts[0] === 10 || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 192 && parts[1] === 168);
}

function validateConfig(value, { allowEphemeralPort = false } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid-config");
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...CONFIG_KEYS].sort())) fail("invalid-config", "config has an unexpected field");
  if (value.schemaVersion !== 1 || !RUN_RE.test(value.runId ?? "") || !MODEL_RE.test(value.modelId ?? "")) fail("invalid-config", "identity is invalid");
  if (value.backendOrigin !== "http://pixel-local-backend:8080") fail("invalid-config", "backend origin is not the isolated model alias");
  if (value.ingressWireApi !== "openai-responses") fail("invalid-config", "ingress wire API is not the pinned Codex protocol");
  if (value.listenHost !== "0.0.0.0" || value.receiptPath !== "/run/pixel-outcome-output/inference-boundary-receipt.json") fail("invalid-config", "listener or receipt path is invalid");
  integer(value.listenPort, allowEphemeralPort ? 0 : 1, 65535, "listenPort");
  integer(value.maxRequestBytes, 1024, 268435456, "maxRequestBytes");
  integer(value.maxResponseBytes, 1024, 1073741824, "maxResponseBytes");
  integer(value.maxRequestSeconds, 1, 2592000, "maxRequestSeconds");
  integer(value.maxModelRequests, 1, 1000000, "maxModelRequests");
  integer(value.maxInputTokens, 1, 1000000000000, "maxInputTokens");
  integer(value.maxOutputTokens, 1, 1000000000000, "maxOutputTokens");
  let inference;
  try { inference = validateExactInferencePolicy(value.inference); } catch { fail("invalid-config", "exact inference policy is invalid"); }
  return Object.freeze({ ...value, inference: inference.policy, inferencePolicySha256: inference.sha256 });
}

function exactKeys(value, allowed, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid-request", `${label} is invalid`);
  for (const key of Object.keys(value)) if (!allowed.has(key)) fail("invalid-request", `${label} has an unsupported field`);
}

function responseMessageContent(item) {
  if (!Array.isArray(item.content) || item.content.length < 1) fail("invalid-request", "Responses message content is invalid");
  const expectedType = item.role === "assistant" ? "output_text" : "input_text";
  const parts = item.content.map((part) => {
    exactKeys(part, new Set(["type", "text", "annotations"]), "Responses text part");
    if (part.type !== expectedType || typeof part.text !== "string") fail("invalid-request", "Responses text part is unsupported");
    if (part.annotations !== undefined && (!Array.isArray(part.annotations) || part.annotations.length !== 0)) fail("invalid-request", "Responses text annotations are unsupported");
    return Object.freeze({ type: "text", text: part.text });
  });
  return parts.length === 1 ? parts[0].text : parts;
}

function sourceToolKey(type, namespace, name) { return `${type}\u0000${namespace ?? ""}\u0000${name}`; }

function responseToolsToChat(value) {
  if (value === undefined) return { tools: undefined, byFlat: new Map(), bySource: new Map() };
  if (!Array.isArray(value) || value.length > 256) fail("invalid-request", "Responses tools are invalid");
  const names = new Set(); const tools = []; const byFlat = new Map(); const bySource = new Map();
  const addFunction = (tool, namespace = null) => {
    exactKeys(tool, new Set(["type", "name", "description", "parameters", "strict"]), "Responses function tool");
    if (tool.type !== "function" || !TOOL_RE.test(tool.name ?? "")) fail("invalid-request", "Responses function tool is invalid");
    if (tool.description !== undefined && typeof tool.description !== "string") fail("invalid-request", "Responses tool description is invalid");
    if (!tool.parameters || typeof tool.parameters !== "object" || Array.isArray(tool.parameters)) fail("invalid-request", "Responses tool parameters are invalid");
    if (tool.strict !== undefined && typeof tool.strict !== "boolean") fail("invalid-request", "Responses tool strictness is invalid");
    const flat = namespace === null ? tool.name : `pixel_ns_${namespace}__${tool.name}`;
    const source = sourceToolKey("function", namespace, tool.name);
    if (!TOOL_RE.test(flat) || names.has(flat) || bySource.has(source)) fail("invalid-request", "Responses function tool names collide");
    const mapping = Object.freeze({ type: "function", namespace, name: tool.name, flat });
    names.add(flat); byFlat.set(flat, mapping); bySource.set(source, mapping);
    tools.push({
      type: "function",
      function: {
        name: flat, description: tool.description ?? "", parameters: tool.parameters,
        ...(tool.strict === undefined ? {} : { strict: tool.strict }),
      },
    });
  };
  for (const tool of value) {
    if (!tool || typeof tool !== "object" || Array.isArray(tool)) fail("invalid-request", "Responses tool is invalid");
    if (tool.type === "function") { addFunction(tool); continue; }
    if (tool.type === "custom") {
      exactKeys(tool, new Set(["type", "name", "description", "format"]), "Responses custom tool");
      exactKeys(tool.format, new Set(["type", "syntax", "definition"]), "Responses custom tool format");
      if (
        tool.name !== "apply_patch" || typeof tool.description !== "string"
        || tool.format.type !== "grammar" || tool.format.syntax !== "lark"
        || typeof tool.format.definition !== "string" || !tool.format.definition || tool.format.definition.length > 65536
      ) fail("invalid-request", "Responses custom tool is unsupported");
      const flat = "pixel_custom_apply_patch"; const source = sourceToolKey("custom", null, tool.name);
      if (names.has(flat) || bySource.has(source)) fail("invalid-request", "Responses custom tool names collide");
      const mapping = Object.freeze({ type: "custom", namespace: null, name: tool.name, flat });
      names.add(flat); byFlat.set(flat, mapping); bySource.set(source, mapping);
      tools.push({
        type: "function",
        function: {
          name: flat, description: tool.description,
          parameters: {
            type: "object", properties: { input: { type: "string", description: "Complete apply_patch input." } },
            required: ["input"], additionalProperties: false,
          }, strict: true,
        },
      });
      continue;
    }
    if (tool.type === "namespace") {
      exactKeys(tool, new Set(["type", "name", "description", "tools"]), "Responses namespace tool");
      if (
        tool.name !== "multi_agent_v1" || typeof tool.description !== "string"
        || !Array.isArray(tool.tools) || tool.tools.length < 1 || tool.tools.length > 8
      ) fail("invalid-request", "Responses namespace tool is unsupported");
      const allowed = new Set(["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"]);
      for (const nested of tool.tools) {
        if (!allowed.has(nested?.name)) fail("invalid-request", "Responses namespace function is unsupported");
        addFunction(nested, tool.name);
      }
      continue;
    }
    fail("invalid-request", "Responses hosted tool is unsupported");
  }
  if (tools.length > 256) fail("invalid-request", "Responses flattened tool count is too large");
  return { tools, byFlat, bySource };
}

function responseToolChoiceToChat(value, mappings) {
  if (value === undefined) return undefined;
  if (["auto", "none", "required"].includes(value)) return value;
  exactKeys(value, new Set(["type", "namespace", "name"]), "Responses tool choice");
  const namespace = value.namespace ?? null;
  const mapping = mappings.bySource.get(sourceToolKey(value.type, namespace, value.name));
  if (!mapping) fail("invalid-request", "Responses tool choice is invalid");
  return { type: "function", function: { name: mapping.flat } };
}

function responseFormatToChat(value) {
  if (value === undefined) return undefined;
  exactKeys(value, new Set(["format"]), "Responses text configuration");
  exactKeys(value.format, new Set(["type", "name", "description", "schema", "strict"]), "Responses text format");
  if (value.format.type === "text" && Object.keys(value.format).length === 1) return undefined;
  if (
    value.format.type !== "json_schema" || !TOOL_RE.test(value.format.name ?? "")
    || !value.format.schema || typeof value.format.schema !== "object" || Array.isArray(value.format.schema)
    || value.format.strict !== true
    || (value.format.description !== undefined && typeof value.format.description !== "string")
  ) fail("invalid-request", "Responses structured text format is invalid");
  return {
    type: "json_schema",
    json_schema: {
      name: value.format.name, schema: value.format.schema, strict: true,
      ...(value.format.description === undefined ? {} : { description: value.format.description }),
    },
  };
}

export function responsesRequestToChat(body, effectiveOutput) {
  exactKeys(body, RESPONSES_REQUEST_KEYS, "Responses request");
  if (typeof body.instructions !== "string" || !Array.isArray(body.input) || body.input.length < 1) {
    fail("invalid-request", "Responses instructions or input is invalid");
  }
  if (body.store !== false || body.stream !== true || body.background !== undefined) fail("invalid-request", "Responses retention or streaming policy is invalid");
  if (body.include !== undefined && (
    !Array.isArray(body.include) || body.include.length > 1
    || body.include.some((item) => item !== "reasoning.encrypted_content")
  )) fail("invalid-request", "Responses include surface is unsupported");
  if (body.prompt_cache_key !== undefined && typeof body.prompt_cache_key !== "string") fail("invalid-request", "Responses prompt cache key is invalid");
  if (body.client_metadata !== undefined && (!body.client_metadata || typeof body.client_metadata !== "object" || Array.isArray(body.client_metadata))) {
    fail("invalid-request", "Responses client metadata is invalid");
  }
  if (body.parallel_tool_calls !== undefined && typeof body.parallel_tool_calls !== "boolean") fail("invalid-request", "Responses parallel tool setting is invalid");
  // A JSON null reasoning is treated exactly like an omitted optional reasoning
  // request (the real DSV4 client serializes reasoning:null). Every non-null
  // value keeps the strict object/exact-key/enum validation below.
  if (body.reasoning !== undefined && body.reasoning !== null) {
    exactKeys(body.reasoning, new Set(["effort", "summary"]), "Responses reasoning request");
    if (body.reasoning.effort !== undefined && !["none", "minimal", "low", "medium", "high", "xhigh"].includes(body.reasoning.effort)) fail("invalid-request", "Responses reasoning effort is invalid");
    if (body.reasoning.summary !== undefined && !["auto", "concise", "detailed"].includes(body.reasoning.summary)) fail("invalid-request", "Responses reasoning summary is invalid");
  }
  const mappings = responseToolsToChat(body.tools);
  const messages = [{ role: "system", content: body.instructions }];
  let pendingCalls = null;
  const knownCalls = new Set();
  const flushCalls = () => {
    if (!pendingCalls) return;
    messages.push({ role: "assistant", content: null, tool_calls: pendingCalls });
    pendingCalls = null;
  };
  for (const item of body.input) {
    if (!item || typeof item !== "object" || Array.isArray(item)) fail("invalid-request", "Responses input item is invalid");
    if (item.type === "message") {
      exactKeys(item, new Set(["type", "id", "role", "content", "status"]), "Responses message item");
      flushCalls();
      if (!["developer", "system", "user", "assistant"].includes(item.role)) fail("invalid-request", "Responses message role is unsupported");
      messages.push({ role: ["developer", "system"].includes(item.role) ? "system" : item.role, content: responseMessageContent(item) });
      continue;
    }
    if (item.type === "function_call") {
      exactKeys(item, new Set(["type", "id", "call_id", "namespace", "name", "arguments", "status"]), "Responses function call item");
      if (!TOOL_RE.test(item.name ?? "") || !CALL_ID_RE.test(item.call_id ?? "") || typeof item.arguments !== "string") {
        fail("invalid-request", "Responses function call history is invalid");
      }
      const mapping = mappings.bySource.get(sourceToolKey("function", item.namespace ?? null, item.name));
      if (!mapping || knownCalls.has(item.call_id)) fail("invalid-request", "Responses function call has no exact admitted tool");
      let parsedArguments;
      try { parsedArguments = JSON.parse(item.arguments); } catch { fail("invalid-request", "Responses function call arguments are invalid JSON"); }
      if (!parsedArguments || typeof parsedArguments !== "object" || Array.isArray(parsedArguments)) fail("invalid-request", "Responses function call arguments are invalid");
      pendingCalls ??= [];
      pendingCalls.push({ id: item.call_id, type: "function", function: { name: mapping.flat, arguments: item.arguments } });
      knownCalls.add(item.call_id);
      continue;
    }
    if (item.type === "custom_tool_call") {
      exactKeys(item, new Set(["type", "id", "call_id", "namespace", "name", "input", "status"]), "Responses custom tool call item");
      const mapping = mappings.bySource.get(sourceToolKey("custom", item.namespace ?? null, item.name));
      if (!mapping || !CALL_ID_RE.test(item.call_id ?? "") || typeof item.input !== "string" || knownCalls.has(item.call_id)) {
        fail("invalid-request", "Responses custom tool call history is invalid");
      }
      pendingCalls ??= [];
      pendingCalls.push({ id: item.call_id, type: "function", function: { name: mapping.flat, arguments: JSON.stringify({ input: item.input }) } });
      knownCalls.add(item.call_id);
      continue;
    }
    flushCalls();
    if (!["function_call_output", "custom_tool_call_output"].includes(item.type)) {
      fail("invalid-request", "Responses input item is unsupported");
    }
    exactKeys(item, new Set(["type", "id", "call_id", "name", "output", "status"]), "Responses tool output item");
    if (!CALL_ID_RE.test(item.call_id ?? "") || typeof item.output !== "string" || !knownCalls.has(item.call_id)) {
      fail("invalid-request", "Responses function output history is unsupported");
    }
    messages.push({ role: "tool", tool_call_id: item.call_id, content: item.output });
  }
  flushCalls();
  const toolChoice = responseToolChoiceToChat(body.tool_choice, mappings);
  const responseFormat = responseFormatToChat(body.text);
  return {
    body: {
      model: body.model, messages, stream: true, max_tokens: effectiveOutput,
      ...(mappings.tools === undefined ? {} : { tools: mappings.tools }),
      ...(toolChoice === undefined ? {} : { tool_choice: toolChoice }),
      ...(body.parallel_tool_calls === undefined ? {} : { parallel_tool_calls: body.parallel_tool_calls }),
      ...(responseFormat === undefined ? {} : { response_format: responseFormat }),
    },
    response: Object.freeze({ model: body.model, toolMappings: mappings.byFlat }),
  };
}

function parseChatSse(bytes) {
  let text;
  try { text = UTF8.decode(bytes); } catch { fail("invalid-backend-response", "backend response is not UTF-8"); }
  let inputTokens; let outputTokens; let cachedTokens = 0; let reasoningTokens = 0;
  let responseId = null; let createdAt = null; let model = null; let finishReason = null; let done = false;
  const textParts = []; const toolCalls = [];
  for (const block of text.split(/\r?\n\r?\n/u)) {
    const data = block.split(/\r?\n/u).filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim()).join("\n");
    if (!data) continue;
    if (data === "[DONE]") { if (done) fail("invalid-backend-response", "backend repeated the SSE terminator"); done = true; continue; }
    if (done) fail("invalid-backend-response", "backend emitted data after the SSE terminator");
    let value;
    try { value = JSON.parse(data); } catch { fail("invalid-backend-response", "backend returned malformed SSE JSON"); }
    if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid-backend-response", "backend SSE event is invalid");
    if (value.id !== undefined) {
      if (typeof value.id !== "string" || !value.id || (responseId !== null && value.id !== responseId)) fail("invalid-backend-response", "backend response identity changed");
      responseId = value.id;
    }
    if (value.created !== undefined) {
      if (!Number.isSafeInteger(value.created) || value.created < 0 || (createdAt !== null && value.created !== createdAt)) fail("invalid-backend-response", "backend response timestamp changed");
      createdAt = value.created;
    }
    if (value.model !== undefined) {
      if (typeof value.model !== "string" || !value.model || (model !== null && value.model !== model)) fail("invalid-backend-response", "backend response model changed");
      model = value.model;
    }
    const usage = value.usage;
    if (usage !== undefined && usage !== null) {
      if (!usage || typeof usage !== "object" || Array.isArray(usage)) fail("invalid-backend-response", "backend usage is invalid");
      const input = usage.input_tokens ?? usage.prompt_tokens;
      const output = usage.output_tokens ?? usage.completion_tokens;
      if (!Number.isSafeInteger(input) || input < 0 || !Number.isSafeInteger(output) || output < 0) fail("invalid-backend-response", "backend usage is incomplete");
      inputTokens = input; outputTokens = output;
      const cached = usage.input_tokens_details?.cached_tokens ?? usage.prompt_tokens_details?.cached_tokens ?? 0;
      const reasoning = usage.output_tokens_details?.reasoning_tokens ?? usage.completion_tokens_details?.reasoning_tokens ?? 0;
      if (!Number.isSafeInteger(cached) || cached < 0 || !Number.isSafeInteger(reasoning) || reasoning < 0) fail("invalid-backend-response", "backend detailed usage is invalid");
      cachedTokens = cached; reasoningTokens = reasoning;
    }
    if (value.choices === undefined) continue;
    if (!Array.isArray(value.choices) || value.choices.length > 1) fail("invalid-backend-response", "backend returned an unsupported choice set");
    if (value.choices.length === 0) continue;
    const choice = value.choices[0];
    if (!choice || typeof choice !== "object" || choice.index !== 0) fail("invalid-backend-response", "backend choice is invalid");
    if (choice.finish_reason !== undefined && choice.finish_reason !== null) {
      if (!["stop", "tool_calls", "length"].includes(choice.finish_reason) || (finishReason !== null && finishReason !== choice.finish_reason)) fail("invalid-backend-response", "backend finish reason is unsupported");
      finishReason = choice.finish_reason;
    }
    const delta = choice.delta;
    if (delta === undefined || delta === null) continue;
    if (!delta || typeof delta !== "object" || Array.isArray(delta)) fail("invalid-backend-response", "backend delta is invalid");
    for (const key of Object.keys(delta)) if (!["role", "content", "tool_calls", "reasoning_content", "refusal"].includes(key)) fail("invalid-backend-response", "backend delta has an unsupported field");
    if (delta.role !== undefined && delta.role !== "assistant") fail("invalid-backend-response", "backend delta role is invalid");
    if (delta.content !== undefined && delta.content !== null) {
      if (typeof delta.content !== "string") fail("invalid-backend-response", "backend text delta is invalid");
      textParts.push(delta.content);
    }
    if ((delta.reasoning_content !== undefined && delta.reasoning_content !== null && delta.reasoning_content !== "") || (delta.refusal !== undefined && delta.refusal !== null && delta.refusal !== "")) {
      fail("invalid-backend-response", "backend exposed forbidden reasoning or refusal content");
    }
    if (delta.tool_calls !== undefined) {
      if (!Array.isArray(delta.tool_calls) || delta.tool_calls.length > 256) fail("invalid-backend-response", "backend tool-call delta is invalid");
      for (const fragment of delta.tool_calls) {
        if (!fragment || typeof fragment !== "object" || !Number.isSafeInteger(fragment.index) || fragment.index < 0 || fragment.index >= 256) fail("invalid-backend-response", "backend tool-call index is invalid");
        for (const key of Object.keys(fragment)) if (!["index", "id", "type", "function"].includes(key)) fail("invalid-backend-response", "backend tool-call delta has an unsupported field");
        const current = toolCalls[fragment.index] ?? { id: null, type: null, name: "", arguments: "" };
        if (fragment.id !== undefined && (typeof fragment.id !== "string" || !fragment.id || (current.id !== null && current.id !== fragment.id))) fail("invalid-backend-response", "backend tool-call identity changed");
        if (fragment.type !== undefined && (fragment.type !== "function" || (current.type !== null && current.type !== fragment.type))) fail("invalid-backend-response", "backend tool-call type changed");
        if (fragment.function !== undefined) {
          if (!fragment.function || typeof fragment.function !== "object" || Array.isArray(fragment.function)) fail("invalid-backend-response", "backend function-call delta is invalid");
          for (const key of Object.keys(fragment.function)) if (!["name", "arguments"].includes(key)) fail("invalid-backend-response", "backend function-call delta has an unsupported field");
          if (fragment.function.name !== undefined) {
            if (typeof fragment.function.name !== "string") fail("invalid-backend-response", "backend function name delta is invalid");
            current.name += fragment.function.name;
          }
          if (fragment.function.arguments !== undefined) {
            if (typeof fragment.function.arguments !== "string") fail("invalid-backend-response", "backend function argument delta is invalid");
            current.arguments += fragment.function.arguments;
          }
        }
        if (fragment.id !== undefined) current.id = fragment.id;
        if (fragment.type !== undefined) current.type = fragment.type;
        toolCalls[fragment.index] = current;
      }
    }
  }
  if (!done || inputTokens === undefined || outputTokens === undefined) fail("backend-usage-missing", "backend omitted the terminator or exact usage");
  if (finishReason === null || (textParts.length === 0 && toolCalls.length === 0)) fail("invalid-backend-response", "backend response is incomplete");
  if (finishReason === "stop" && toolCalls.length > 0) finishReason = "tool_calls";
  if ((finishReason === "tool_calls") !== (toolCalls.length > 0)) fail("invalid-backend-response", "backend finish reason disagrees with its tool calls");
  if (toolCalls.some((call) => call === undefined)) fail("invalid-backend-response", "backend tool-call indexes are sparse");
  for (const call of toolCalls) {
    if (!CALL_ID_RE.test(call.id ?? "") || call.type !== "function" || !TOOL_RE.test(call.name)) fail("invalid-backend-response", "backend function call is incomplete");
    let parsed;
    try { parsed = JSON.parse(call.arguments); } catch { fail("invalid-backend-response", "backend function arguments are invalid JSON"); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) fail("invalid-backend-response", "backend function arguments are invalid");
  }
  return { responseId, createdAt, model, finishReason, text: textParts.join(""), toolCalls, inputTokens, outputTokens, cachedTokens, reasoningTokens };
}

function responseIdentifier(prefix, value, fallback) {
  const normalized = typeof value === "string" ? value.replace(/[^A-Za-z0-9_-]/gu, "_").slice(0, 96) : "";
  return `${prefix}_${normalized || fallback}`;
}

export function chatSseToResponses(bytes, request) {
  const parsed = parseChatSse(bytes);
  const responseId = responseIdentifier("resp", parsed.responseId, randomBytes(12).toString("hex"));
  const createdAt = parsed.createdAt ?? Math.floor(Date.now() / 1000);
  const output = []; const events = [];
  const emit = (value) => events.push(value);
  emit({ type: "response.created", response: { id: responseId, object: "response", created_at: createdAt, status: "in_progress", output: [] } });
  if (parsed.text) {
    const item = { type: "message", id: responseIdentifier("msg", parsed.responseId, "text"), role: "assistant", status: "in_progress", content: [] };
    const outputIndex = output.length;
    emit({ type: "response.output_item.added", output_index: outputIndex, item });
    emit({ type: "response.content_part.added", output_index: outputIndex, item_id: item.id, content_index: 0, part: { type: "output_text", text: "", annotations: [] } });
    emit({ type: "response.output_text.delta", output_index: outputIndex, item_id: item.id, content_index: 0, delta: parsed.text });
    emit({ type: "response.output_text.done", output_index: outputIndex, item_id: item.id, content_index: 0, text: parsed.text });
    const part = { type: "output_text", text: parsed.text, annotations: [] };
    emit({ type: "response.content_part.done", output_index: outputIndex, item_id: item.id, content_index: 0, part });
    const completed = { ...item, status: "completed", content: [part] };
    emit({ type: "response.output_item.done", output_index: outputIndex, item: completed });
    output.push(completed);
  }
  for (const [index, call] of parsed.toolCalls.entries()) {
    const outputIndex = output.length;
    const mapping = request.toolMappings instanceof Map
      ? request.toolMappings.get(call.name)
      : { type: "function", namespace: null, name: call.name, flat: call.name };
    if (!mapping) fail("invalid-backend-response", "backend called a tool that was not exactly admitted");
    if (mapping.type === "function") {
      const item = {
        type: "function_call", id: responseIdentifier("fc", call.id, String(index)), call_id: call.id,
        ...(mapping.namespace === null ? {} : { namespace: mapping.namespace }),
        name: mapping.name, arguments: "", status: "in_progress",
      };
      emit({ type: "response.output_item.added", output_index: outputIndex, item });
      emit({ type: "response.function_call_arguments.delta", output_index: outputIndex, item_id: item.id, delta: call.arguments });
      emit({
        type: "response.function_call_arguments.done", output_index: outputIndex, item_id: item.id,
        ...(mapping.namespace === null ? {} : { namespace: mapping.namespace }),
        name: mapping.name, arguments: call.arguments,
      });
      const completed = { ...item, arguments: call.arguments, status: "completed" };
      emit({ type: "response.output_item.done", output_index: outputIndex, item: completed });
      output.push(completed);
      continue;
    }
    if (mapping.type !== "custom" || mapping.name !== "apply_patch" || mapping.namespace !== null) {
      fail("invalid-backend-response", "backend returned an unsupported mapped tool type");
    }
    let customArguments;
    try { customArguments = JSON.parse(call.arguments); } catch { fail("invalid-backend-response", "backend custom tool arguments are invalid JSON"); }
    if (
      !customArguments || typeof customArguments !== "object" || Array.isArray(customArguments)
      || Object.keys(customArguments).length !== 1 || typeof customArguments.input !== "string"
    ) fail("invalid-backend-response", "backend custom tool arguments are invalid");
    const item = {
      type: "custom_tool_call", id: responseIdentifier("ctc", call.id, String(index)), call_id: call.id,
      name: mapping.name, input: "", status: "in_progress",
    };
    emit({ type: "response.output_item.added", output_index: outputIndex, item });
    emit({ type: "response.custom_tool_call_input.delta", output_index: outputIndex, item_id: item.id, delta: customArguments.input });
    emit({
      type: "response.custom_tool_call_input.done", output_index: outputIndex, item_id: item.id,
      name: mapping.name, input: customArguments.input,
    });
    const completed = { ...item, input: customArguments.input, status: "completed" };
    emit({ type: "response.output_item.done", output_index: outputIndex, item: completed });
    output.push(completed);
  }
  if (parsed.cachedTokens > parsed.inputTokens || parsed.reasoningTokens > parsed.outputTokens) fail("invalid-backend-response", "backend detailed usage exceeds total usage");
  const incomplete = parsed.finishReason === "length";
  const usage = {
    input_tokens: parsed.inputTokens, output_tokens: parsed.outputTokens,
    total_tokens: parsed.inputTokens + parsed.outputTokens,
    input_tokens_details: { cached_tokens: parsed.cachedTokens },
    output_tokens_details: { reasoning_tokens: parsed.reasoningTokens },
  };
  emit({
    type: incomplete ? "response.incomplete" : "response.completed",
    response: {
      id: responseId, object: "response", created_at: createdAt, completed_at: Math.floor(Date.now() / 1000),
      status: incomplete ? "incomplete" : "completed", error: null,
      incomplete_details: incomplete ? { reason: "max_output_tokens" } : null,
      model: parsed.model ?? request.model, output, store: false, usage,
    },
  });
  events.push("[DONE]");
  const result = Buffer.from(`${events.map((value) => `data: ${typeof value === "string" ? value : JSON.stringify(value)}`).join("\n\n")}\n\n`, "utf8");
  return { bytes: result, usage: { inputTokens: parsed.inputTokens, outputTokens: parsed.outputTokens } };
}

async function readBoundedBody(request, maximum) {
  const chunks = []; let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > maximum) fail("request-too-large");
    chunks.push(chunk);
  }
  if (total < 2) fail("invalid-json");
  return Buffer.concat(chunks, total);
}

function validateJsonShape(value, limits, depth = 0) {
  if (depth > limits.maxDepth) fail("invalid-request", "JSON nesting is too deep");
  limits.nodes += 1;
  if (limits.nodes > limits.maxNodes) fail("invalid-request", "JSON has too many values");
  if (typeof value === "string") {
    if (Buffer.byteLength(value, "utf8") > limits.maxStringBytes) fail("invalid-request", "JSON string is too large");
    return;
  }
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("invalid-request", "JSON number is invalid");
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > limits.maxArrayItems) fail("invalid-request", "JSON array is too large");
    for (const item of value) validateJsonShape(item, limits, depth + 1);
    return;
  }
  if (!value || typeof value !== "object") fail("invalid-request", "JSON value is unsupported");
  const keys = Object.keys(value);
  if (keys.length > limits.maxObjectKeys) fail("invalid-request", "JSON object has too many fields");
  for (const key of keys) {
    if (["__proto__", "constructor", "prototype"].includes(key) || Buffer.byteLength(key, "utf8") > 256) {
      fail("invalid-request", "JSON object key is unsafe");
    }
    validateJsonShape(value[key], limits, depth + 1);
  }
}

function sendJson(response, status, value) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  response.writeHead(status, { "cache-control": "no-store", "content-type": "application/json", "content-length": String(bytes.length), "x-content-type-options": "nosniff", connection: "close" });
  response.end(bytes);
}

async function atomicReceipt(path, value) {
  const directoryPath = dirname(path);
  const directory = await lstat(directoryPath).catch(() => null);
  if (!directory?.isDirectory() || directory.isSymbolicLink()) fail("receipt-unavailable");
  const temporary = join(directoryPath, `.inference-boundary-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await rename(temporary, path);
    if (process.platform !== "win32") {
      const directoryHandle = await open(directoryPath, constants.O_RDONLY);
      try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
    }
  } catch (error) { await unlink(temporary).catch(() => {}); throw error; }
}

export function createInferenceBoundary(rawConfig, options = {}) {
  if (options.testOnlyAllowEphemeralPort !== undefined && typeof options.testOnlyAllowEphemeralPort !== "boolean") fail("invalid-runtime");
  const testOnlyAllowEphemeralPort = options.testOnlyAllowEphemeralPort === true;
  if (testOnlyAllowEphemeralPort && typeof options.reportWriter !== "function") fail("invalid-runtime");
  const config = validateConfig(rawConfig, { allowEphemeralPort: testOnlyAllowEphemeralPort });
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") fail("invalid-runtime");
  const state = { requests: 0, inputTokens: 0, outputTokens: 0, deniedRequests: 0, backendFailures: 0, responseBytes: 0, backendResponseBytes: 0, active: false, lastFailureCode: null };
  const report = () => Object.freeze({
    schemaVersion: 1, runId: config.runId, inferencePolicySha256: config.inferencePolicySha256,
    ingressWireApi: config.ingressWireApi, backendWireApi: "openai-chat-completions", adapter: "responses-to-chat-v2",
    requests: state.requests, inputTokens: state.inputTokens, outputTokens: state.outputTokens,
    deniedRequests: state.deniedRequests, backendFailures: state.backendFailures,
    responseBytes: state.responseBytes, backendResponseBytes: state.backendResponseBytes, active: state.active, lastFailureCode: state.lastFailureCode,
    contentStored: false, credentialsForwarded: false, arbitraryNetwork: false, externalEffects: false,
  });
  let writeQueue = Promise.resolve();
  const persist = () => { writeQueue = writeQueue.then(() => (options.reportWriter ?? atomicReceipt)(config.receiptPath, report())); return writeQueue; };
  const deny = (response, status, code) => { state.deniedRequests += 1; state.lastFailureCode = code; void persist(); sendJson(response, status, { error: { code, message: "Exact inference boundary denied the request." } }); };

  const server = createServer({ maxHeaderSize: 8192, requestTimeout: config.maxRequestSeconds * 1000, headersTimeout: 5000, keepAliveTimeout: 1000 }, async (request, response) => {
    response.shouldKeepAlive = false;
    try {
      const peer = (request.socket.remoteAddress ?? "").replace(/^::ffff:/u, "");
      const parsed = new URL(request.url ?? "", "http://pixel-outcome.invalid");
      if (parsed.search || parsed.hash || (!privateIpv4(peer) && peer !== "127.0.0.1")) return deny(response, 403, "peer-or-route-denied");
      if (parsed.pathname === "/healthz" && request.method === "GET") return sendJson(response, 200, { status: "ready", runId: config.runId });
      if (request.method !== "POST" || parsed.pathname !== "/v1/responses") return deny(response, 404, "route-denied");
      if (state.active) return deny(response, 409, "concurrent-inference-denied");
      if (state.requests >= config.maxModelRequests) return deny(response, 429, "model-request-budget-exhausted");
      if ((request.headers["content-type"] ?? "").split(";", 1)[0].trim().toLowerCase() !== "application/json") return deny(response, 415, "content-type-denied");
      const bytes = await readBoundedBody(request, config.maxRequestBytes);
      let body;
      try { body = JSON.parse(UTF8.decode(bytes)); } catch { return deny(response, 400, "invalid-json"); }
      if (!body || typeof body !== "object" || Array.isArray(body) || body.model !== config.modelId || body.stream !== true || body.store === true || body.background === true) return deny(response, 400, "request-contract-denied");
      validateJsonShape(body, { nodes: 0, maxDepth: 64, maxNodes: 100000, maxStringBytes: config.maxRequestBytes, maxArrayItems: 100000, maxObjectKeys: 10000 });
      const remainingInput = config.maxInputTokens - state.inputTokens;
      const remainingOutput = config.maxOutputTokens - state.outputTokens;
      if (remainingInput < 1 || remainingOutput < 1) return deny(response, 429, "token-budget-exhausted");
      const requested = body.max_output_tokens ?? config.inference.maxOutputTokens;
      if (!Number.isSafeInteger(requested) || requested < 1) return deny(response, 400, "output-limit-invalid");
      const effectiveOutput = Math.min(requested, config.inference.maxOutputTokens, remainingOutput);
      const adapted = responsesRequestToChat(body, effectiveOutput);
      const normalized = normalizeVllmChatRequest(adapted.body, config.inference, effectiveOutput).body;
      normalized.store = false;
      delete normalized.background;
      state.active = true; state.requests += 1;
      const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), config.maxRequestSeconds * 1000); timer.unref?.();
      try {
        const backend = await fetchImpl(`${config.backendOrigin}/v1/chat/completions`, {
          method: "POST", redirect: "error", signal: controller.signal,
          headers: { "content-type": "application/json", accept: "text/event-stream", "accept-encoding": "identity" },
          body: JSON.stringify(normalized),
        });
        if (backend.status !== 200 || !backend.body) fail("backend-status-denied");
        const type = (backend.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
        if (type !== "text/event-stream" || backend.headers.get("content-encoding")) fail("invalid-backend-response");
        const chunks = []; let total = 0;
        for await (const chunk of backend.body) {
          total += chunk.length;
          if (total > config.maxResponseBytes) fail("response-too-large");
          chunks.push(chunk);
        }
        const backendResult = Buffer.concat(chunks, total);
        const translated = chatSseToResponses(backendResult, { model: config.modelId, ...adapted.response });
        if (translated.bytes.length > config.maxResponseBytes) fail("response-too-large", "translated response exceeded its byte ceiling");
        const result = translated.bytes;
        const usage = translated.usage;
        state.inputTokens += usage.inputTokens; state.outputTokens += usage.outputTokens;
        state.backendResponseBytes += total; state.responseBytes += result.length;
        if (usage.inputTokens > remainingInput || usage.outputTokens > remainingOutput || usage.outputTokens > effectiveOutput) {
          fail("backend-usage-exceeded", "backend usage exceeded the comparison budget");
        }
        state.active = false; await persist();
        response.writeHead(200, { "cache-control": "no-store", "content-type": type, "content-length": String(result.length), "x-content-type-options": "nosniff", connection: "close" });
        response.end(result);
      } finally { clearTimeout(timer); state.active = false; }
    } catch (error) {
      const code = error instanceof BoundaryError ? error.code : "backend-failure";
      const clientStatus = { "invalid-request": 400, "invalid-inference-request": 400, "request-too-large": 413 }[code];
      state.active = false;
      if (clientStatus !== undefined && !response.headersSent) return deny(response, clientStatus, code);
      if (code.startsWith("backend") || code === "invalid-backend-response" || code === "response-too-large") state.backendFailures += 1;
      state.lastFailureCode = code; await persist().catch(() => {});
      if (!response.headersSent) sendJson(response, 502, { error: { code, message: "Exact inference boundary failed closed." } }); else response.destroy();
    }
  });
  server.maxRequestsPerSocket = 100008;
  server.on("upgrade", (_request, socket) => socket.destroy());
  server.on("connect", (_request, socket) => socket.destroy());
  server.on("clientError", (_error, socket) => {
    if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  });

  return Object.freeze({
    listen: async () => {
      await persist();
      await new Promise((resolve, reject) => { server.once("error", reject); server.listen(config.listenPort, config.listenHost, resolve); });
      const address = server.address();
      if (!address || typeof address === "string" || !Number.isSafeInteger(address.port) || address.port < 1 || address.port > 65535) fail("invalid-runtime");
      return address.port;
    },
    close: async () => { if (server.listening) await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())); await persist(); await writeQueue; },
    report,
  });
}

async function readConfig(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 65536) fail("invalid-config-file");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("invalid-config-file");
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail("invalid-config-file"));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail("invalid-config-file");
    try { return JSON.parse(await handle.readFile("utf8")); } catch { fail("invalid-config-file"); }
  } finally { await handle.close(); }
}

async function main() {
  if (process.argv.length !== 3) fail("usage");
  const boundary = createInferenceBoundary(await readConfig(process.argv[2]));
  const stop = async () => { await boundary.close(); process.exitCode = 0; };
  process.once("SIGINT", stop); process.once("SIGTERM", stop); await boundary.listen();
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => { process.stderr.write(`pixel-outcome-inference-boundary: ${error instanceof BoundaryError ? error.code : "startup-failed"}\n`); process.exitCode = 1; });
}

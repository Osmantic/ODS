import {
  WorkProviderAdapterError, assertPlainProviderObject, assertProviderKeys, cloneProviderJson,
  normalizedAssistant, validateNeutralRequest,
} from "../adapter-contract.mjs";

const RESPONSE_KEYS = ["background", "completed_at", "conversation", "created_at", "error", "id", "incomplete_details", "instructions", "max_output_tokens", "max_tool_calls", "metadata", "model", "object", "output", "parallel_tool_calls", "previous_response_id", "prompt_cache_key", "reasoning", "safety_identifier", "service_tier", "status", "store", "temperature", "text", "tool_choice", "tools", "top_logprobs", "top_p", "truncation", "usage"];
const OUTPUT_TYPES = new Set(["function_call", "message", "reasoning"]);

function fail(message) { throw new WorkProviderAdapterError(message); }
function toolDefinition(tool) {
  return { type: "function", name: tool.name, description: tool.description ?? "", parameters: cloneProviderJson(tool.parameters, "tool parameters"), ...(tool.strict === undefined ? {} : { strict: tool.strict }) };
}

function inputItems(message) {
  if (message.role === "assistant" && message.providerState !== undefined) {
    assertProviderKeys(message.providerState, ["outputItems", "protocol", "responseState"], "OpenAI Responses provider state");
    if (message.providerState.protocol !== "openai-responses" || !Array.isArray(message.providerState.outputItems)) fail("OpenAI Responses provider state is invalid");
    return cloneProviderJson(message.providerState.outputItems, "OpenAI Responses output items");
  }
  if (message.role === "tool") return [{ type: "function_call_output", call_id: message.toolCallId, output: typeof message.content === "string" ? message.content : JSON.stringify(message.content) }];
  if (message.role === "assistant" && message.toolCalls?.length) {
    const items = [];
    if (message.content) items.push({ type: "message", role: "assistant", content: [{ type: "output_text", text: message.content }] });
    items.push(...message.toolCalls.map((call) => ({ type: "function_call", call_id: call.id, name: call.name, arguments: call.arguments })));
    return items;
  }
  return [{ role: message.role, content: message.content }];
}

function finishReason(value, toolCalls, incompleteDetails) {
  if (value === "completed") return toolCalls.length ? "tool_calls" : "stop";
  if (value === "incomplete") return incompleteDetails?.reason === "content_filter" ? "content_filter" : incompleteDetails?.reason === "max_output_tokens" ? "length" : "incomplete";
  if (["failed", "cancelled", "expired"].includes(value)) return "error";
  return "incomplete";
}

export function buildOpenAiResponsesRequest(input) {
  const request = validateNeutralRequest(input);
  return {
    model: request.model,
    input: request.messages.flatMap(inputItems),
    ...(request.tools === undefined ? {} : { tools: request.tools.map(toolDefinition) }),
    ...(request.toolChoice === undefined ? {} : { tool_choice: cloneProviderJson(request.toolChoice, "tool choice") }),
    ...(request.maxOutputTokens === undefined ? {} : { max_output_tokens: request.maxOutputTokens }),
    ...(request.reasoningEffort === undefined ? {} : { reasoning: { effort: request.reasoningEffort } }),
    ...(request.temperature === undefined ? {} : { temperature: request.temperature }),
    store: false,
    include: ["reasoning.encrypted_content"],
  };
}

function parseUsage(value) {
  if (value === undefined || value === null) return null;
  assertPlainProviderObject(value, "OpenAI Responses usage");
  if (!Number.isInteger(value.input_tokens) || value.input_tokens < 0 || !Number.isInteger(value.output_tokens) || value.output_tokens < 0) fail("OpenAI Responses usage is invalid");
  return { inputTokens: value.input_tokens, outputTokens: value.output_tokens, totalTokens: Number.isInteger(value.total_tokens) ? value.total_tokens : value.input_tokens + value.output_tokens };
}

export function parseOpenAiResponsesResponse(value) {
  assertPlainProviderObject(value, "OpenAI Responses response");
  assertProviderKeys(value, RESPONSE_KEYS, "OpenAI Responses response");
  if (!Array.isArray(value.output) || !["cancelled", "completed", "expired", "failed", "incomplete"].includes(value.status)) fail("OpenAI Responses output is invalid or non-terminal");
  const text = [], toolCalls = [];
  for (const [index, item] of value.output.entries()) {
    assertPlainProviderObject(item, `OpenAI Responses output ${index}`);
    if (!OUTPUT_TYPES.has(item.type)) fail(`OpenAI Responses output ${index} has an unsupported type`);
    if (item.type === "message") {
      if (item.role !== "assistant" || !Array.isArray(item.content)) fail(`OpenAI Responses message ${index} is invalid`);
      for (const block of item.content) {
        assertPlainProviderObject(block, `OpenAI Responses message ${index} content`);
        if (block.type === "output_text" && typeof block.text === "string") text.push(block.text);
        else if (block.type !== "refusal") fail(`OpenAI Responses message ${index} contains an unsupported block`);
      }
    } else if (item.type === "function_call") {
      if (typeof item.call_id !== "string" || typeof item.name !== "string" || typeof item.arguments !== "string") fail(`OpenAI Responses function call ${index} is invalid`);
      toolCalls.push({ id: item.call_id, name: item.name, arguments: item.arguments });
    }
  }
  return normalizedAssistant({
    content: text.length ? text.join("") : null,
    toolCalls,
    providerState: {
      protocol: "openai-responses",
      outputItems: cloneProviderJson(value.output, "OpenAI Responses output"),
      responseState: cloneProviderJson({ status: value.status, error: value.error ?? null, incompleteDetails: value.incomplete_details ?? null }, "OpenAI Responses state"),
    },
    finishReason: finishReason(value.status, toolCalls, value.incomplete_details),
    usage: parseUsage(value.usage),
    providerModel: value.model ?? null,
  });
}

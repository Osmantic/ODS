import { assertPlainProviderObject, cloneProviderJson, normalizedAssistant, WorkProviderAdapterError } from "../adapter-contract.mjs";
import { buildOpenAiChatRequest, parseOpenAiChatResponse } from "./openai-chat.mjs";

const NULL_ONLY_RESPONSE_FIELDS = ["ec_transfer_params", "kv_transfer_params", "metrics", "prompt_logprobs", "prompt_text", "prompt_token_ids"];
const NULL_ONLY_CHOICE_FIELDS = ["routed_experts", "stop_reason", "token_ids"];
const NULL_ONLY_MESSAGE_FIELDS = ["annotations", "audio", "function_call"];

function hasPrivateReasoning(messages) {
  return messages.some((message) => ["local-openai-compatible", "openai-chat-completions"].includes(message.providerState?.protocol)
    && ["reasoning", "reasoning_content"].some((field) => Object.hasOwn(message.providerState.assistantMessage ?? {}, field)));
}

export function buildLocalOpenAiRequest(input) {
  if (hasPrivateReasoning(input.messages ?? [])) throw new WorkProviderAdapterError("Local OpenAI-compatible replay cannot expose provider reasoning");
  const compatible = structuredClone(input);
  for (const message of compatible.messages ?? []) {
    if (message.providerState?.protocol === "local-openai-compatible") message.providerState.protocol = "openai-chat-completions";
  }
  return buildOpenAiChatRequest(compatible);
}

export function parseLocalOpenAiResponse(value) {
  assertPlainProviderObject(value, "Local OpenAI-compatible response");
  const compatible = cloneProviderJson(value, "Local OpenAI-compatible response");
  for (const field of NULL_ONLY_RESPONSE_FIELDS) {
    if (!Object.hasOwn(compatible, field)) continue;
    if (compatible[field] !== null) throw new WorkProviderAdapterError(`Local OpenAI-compatible response ${field} must be null`);
    delete compatible[field];
  }
  if (Array.isArray(compatible.choices)) {
    for (const [index, choice] of compatible.choices.entries()) {
      if (choice === null || typeof choice !== "object" || Array.isArray(choice)) continue;
      for (const field of NULL_ONLY_CHOICE_FIELDS) {
        if (!Object.hasOwn(choice, field)) continue;
        if (choice[field] !== null) throw new WorkProviderAdapterError(`Local OpenAI-compatible choice ${index} ${field} must be null`);
        delete choice[field];
      }
      if (choice.message === null || typeof choice.message !== "object" || Array.isArray(choice.message)) continue;
      const message = choice.message;
      for (const field of NULL_ONLY_MESSAGE_FIELDS) {
        if (!Object.hasOwn(message, field)) continue;
        if (message[field] !== null) throw new WorkProviderAdapterError(`Local OpenAI-compatible assistant message ${index} ${field} must be null`);
        delete message[field];
      }
      if (Object.hasOwn(message, "reasoning_content")) throw new WorkProviderAdapterError("Local OpenAI-compatible response exposed forbidden reasoning_content");
      if (Object.hasOwn(message, "reasoning")) {
        if (message.reasoning !== null && typeof message.reasoning !== "string") throw new WorkProviderAdapterError(`Local OpenAI-compatible assistant message ${index} reasoning is invalid`);
        delete message.reasoning;
      }
    }
  }
  const parsed = parseOpenAiChatResponse(compatible);
  return normalizedAssistant({
    content: parsed.message.content,
    toolCalls: parsed.message.toolCalls ?? [],
    providerState: { ...parsed.message.providerState, protocol: "local-openai-compatible" },
    finishReason: parsed.finishReason,
    usage: parsed.usage,
    providerModel: parsed.providerModel,
  });
}

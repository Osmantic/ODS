import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { canonical, validateWorkProviderProfile, validateWorkProviderReceipt } from "../../scripts/lib/work-contract.mjs";
import { buildAnthropicMessagesRequest, parseAnthropicMessagesResponse } from "./adapters/anthropic-messages.mjs";
import { buildLocalOpenAiRequest, parseLocalOpenAiResponse } from "./adapters/local-openai.mjs";
import { buildOpenAiChatRequest, parseOpenAiChatResponse } from "./adapters/openai-chat.mjs";
import { buildOpenAiResponsesRequest, parseOpenAiResponsesResponse } from "./adapters/openai-responses.mjs";

const PROFILE_NAMES = Object.freeze(["anthropic", "fireworks", "groq", "local", "moonshot-kimi", "openai", "openrouter", "together"]);
const FORBIDDEN_MODEL_VALUES = new Set(["provider-selected-model", "auto", "placeholder"]);
const ADAPTERS = Object.freeze({
  "anthropic-messages": Object.freeze({ buildRequest: buildAnthropicMessagesRequest, parseResponse: parseAnthropicMessagesResponse }),
  "local-openai-compatible": Object.freeze({ buildRequest: buildLocalOpenAiRequest, parseResponse: parseLocalOpenAiResponse }),
  "openai-chat-completions": Object.freeze({ buildRequest: buildOpenAiChatRequest, parseResponse: parseOpenAiChatResponse }),
  "openai-responses": Object.freeze({ buildRequest: buildOpenAiResponsesRequest, parseResponse: parseOpenAiResponsesResponse }),
});

export class WorkProviderRegistryError extends Error {}
function fail(message) { throw new WorkProviderRegistryError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

// Pure predicate over the private, closed model-selection policy. The mutable Set is never
// exported, so no caller can obtain or mutate the policy collection; they can only query it.
export function isForbiddenModelValue(value) { return FORBIDDEN_MODEL_VALUES.has(value); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

function loadProfile(id) {
  const profile = JSON.parse(readFileSync(new URL(`./profiles/${id}.json`, import.meta.url), "utf8"));
  const errors = validateWorkProviderProfile(profile);
  if (errors.length) fail(`provider profile ${id} failed validation: ${errors.join("; ")}`);
  if (profile.id !== id) fail(`provider profile filename differs from id ${id}`);
  return deepFreeze(profile);
}

const PROFILES = Object.freeze(Object.fromEntries(PROFILE_NAMES.map((id) => [id, loadProfile(id)])));

export function listWorkProviderProfiles() {
  return PROFILE_NAMES.map((id) => deepFreeze({ ...structuredClone(PROFILES[id]), profileSha256: sha(PROFILES[id]) }));
}

export function resolveWorkProvider(id, { enabledRemoteProviders = [] } = {}) {
  if (typeof id !== "string" || !Object.hasOwn(PROFILES, id)) fail("provider id is outside the closed registry");
  if (!Array.isArray(enabledRemoteProviders) || enabledRemoteProviders.some((candidate) => typeof candidate !== "string" || !Object.hasOwn(PROFILES, candidate) || !PROFILES[candidate].remote)) fail("enabled remote-provider list is invalid");
  if (new Set(enabledRemoteProviders).size !== enabledRemoteProviders.length) fail("enabled remote-provider list contains duplicates");
  const profile = PROFILES[id];
  const enabled = profile.enabledByDefault || enabledRemoteProviders.includes(id);
  if (!enabled) fail(`remote provider ${id} is disabled until an owner-private policy explicitly enables it`);
  return Object.freeze({ profile, profileSha256: sha(profile), adapter: ADAPTERS[profile.protocol] });
}

export function createWorkProviderReceipt({ provider, phase, status, requestCount = 0, inputTokens = null, outputTokens = null, now = new Date(), suffix }) {
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !/^[a-f0-9]{12}$/u.test(suffix ?? "")) fail("provider receipt inputs are invalid");
  const resolved = typeof provider === "string" ? resolveWorkProvider(provider) : provider;
  if (!resolved?.profile || typeof resolved.profileSha256 !== "string") fail("provider receipt requires one resolved provider");
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-receipt-v1.schema.json",
    schemaVersion: 1,
    receiptId: `workprovider-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    recordedAt: now.toISOString().replace(/\.000Z$/u, "Z"),
    providerId: resolved.profile.id,
    protocol: resolved.profile.protocol,
    phase,
    profileSha256: resolved.profileSha256,
    requestCount,
    inputTokens,
    outputTokens,
    providerContentIncluded: false,
    credentialIncluded: false,
    status,
    boundary: "Content-free provider execution evidence only. It records provider identity, protocol, counters, and terminal state without prompt, response, reasoning, tool arguments, credential, account, path, external-effect authority, deployment authority, or security-testing authority.",
  };
  const errors = validateWorkProviderReceipt(receipt);
  if (errors.length) fail(`provider receipt failed validation: ${errors.join("; ")}`);
  return Object.freeze(receipt);
}

// Explicit, closed model-selection policy. Every provider belongs to exactly one mode:
//   fixed:               the model is exactly profile.defaultModel (Moonshot, OpenAI, Anthropic);
//   owner-pinned:        the model is the owner-private policy.model and never a placeholder
//                        (OpenRouter, Together, Fireworks, Groq);
//   qualification-pinned: the model is exactly the sealed qualification.model (local).
// This single helper is the only authority that resolves and cross-checks a run model so the
// rule is never scattered across call sites.
export function resolveProviderModel({ resolvedProvider, privatePolicy = null, qualification = null, model }) {
  if (!resolvedProvider?.profile) fail("provider model selection requires one resolved provider");
  const profile = resolvedProvider.profile;
  if (profile.modelSelection === "fixed") {
    if (model !== profile.defaultModel) fail(`fixed provider ${profile.id} run model differs from profile.defaultModel`);
    return model;
  }
  if (profile.modelSelection === "owner-pinned") {
    const pinned = privatePolicy?.model;
    if (typeof pinned !== "string" || pinned.length < 1 || pinned.length > 256 || isForbiddenModelValue(pinned) || /[\r\n\u0000]/u.test(pinned)) fail(`owner-pinned provider ${profile.id} requires an exact owner-private model`);
    if (model !== pinned) fail(`owner-pinned provider ${profile.id} run model differs from the pinned owner-private model`);
    return model;
  }
  if (profile.modelSelection === "qualification-pinned") {
    if (qualification?.model !== model) fail(`qualification-pinned provider ${profile.id} run model differs from the sealed qualification model`);
    return model;
  }
  fail(`provider ${profile.id} model selection mode is outside the closed registry`);
}

export function validateOwnerPrivateModel({ resolvedProvider, privatePolicy }) {
  if (!resolvedProvider?.profile) fail("provider model selection requires one resolved provider");
  const profile = resolvedProvider.profile;
  const pinned = privatePolicy?.model;
  if (profile.modelSelection === "owner-pinned") {
    if (typeof pinned !== "string" || pinned.length < 1 || pinned.length > 256 || isForbiddenModelValue(pinned) || /[\r\n\u0000]/u.test(pinned)) fail(`owner-pinned provider ${profile.id} requires an exact owner-private model`);
    return pinned;
  }
  if (profile.modelSelection === "fixed" && pinned !== undefined) {
    if (pinned !== profile.defaultModel) fail(`fixed provider ${profile.id} owner-private model must equal profile.defaultModel`);
  }
  return null;
}

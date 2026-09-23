import { createHash } from "node:crypto";

const EXACT_KEYS = Object.freeze([
  "enforcement", "wireApi", "temperaturePermille", "topPPermille", "topK", "minPPermille",
  "repeatPenaltyPermille", "seed", "reasoningEffort", "reasoningVisibility", "stream",
  "maxOutputTokens", "toolEncoding",
]);
const CLIENT_CONTROLLED_FIELDS = Object.freeze([
  "temperature", "top_p", "top_k", "min_p", "repetition_penalty", "seed", "reasoning_effort",
  "include_reasoning", "thinking_token_budget", "stream", "stream_options", "max_tokens", "max_completion_tokens",
  "presence_penalty", "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "prompt_logprobs",
  "n", "best_of", "use_beam_search", "length_penalty", "early_stopping", "stop", "stop_token_ids",
  "ignore_eos", "min_tokens", "bad_words", "allowed_token_ids", "truncate_prompt_tokens", "detokenize",
  "skip_special_tokens", "spaces_between_special_tokens", "echo", "chat_template", "chat_template_kwargs",
  "structured_outputs", "structured_output", "guided_json", "guided_regex", "guided_choice", "guided_grammar",
  "guided_whitespace_pattern", "guided_decoding_backend", "logits_processors", "priority", "request_id",
  "return_tokens_as_token_ids", "tokenization_kwargs", "mm_processor_kwargs", "kv_transfer_params", "extra_body",
]);

export class InferencePolicyError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = "InferencePolicyError";
    this.code = code;
  }
}

function fail(code, message = code) {
  throw new InferencePolicyError(code, message);
}

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail("invalid-inference-policy", `${label} is invalid`);
  return value;
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

export function canonicalInferencePolicy(value) {
  return JSON.stringify(canonical(value));
}

export function inferencePolicySha256(value) {
  return createHash("sha256").update(canonicalInferencePolicy(value), "utf8").digest("hex");
}

// Deterministic hidden-reasoning admission floor.  A hidden-reasoning request
// shares one max_tokens budget between reasoning and the final answer, so a
// budget too small to leave any final-answer room would let hidden reasoning
// exhaust the whole ceiling and surface as a truncated (length) completion.
// The floor only admits requests whose effective budget leaves a small reserve;
// it is an admission floor, not a guarantee of a complete final answer, and it
// never widens the client's ceiling (the proxy clamps to the policy/lease and
// qualification maximum).  Truncated or incomplete completions are still
// charged against the lease and withheld by the proxy.  Declared above the
// validators that consult it so configuration is rejected at load time rather
// than at the first request.
export const hiddenReasoningAdmissionContract = Object.freeze({
  minFinalAnswerTokens: 64,
  reasoningAllowanceTokens: Object.freeze({ low: 256, medium: 512, high: 1024, max: 2048 }),
});

export function minHiddenReasoningAdmissionBudget(reasoningEffort) {
  const allowance = hiddenReasoningAdmissionContract.reasoningAllowanceTokens[reasoningEffort] ?? 512;
  return allowance + hiddenReasoningAdmissionContract.minFinalAnswerTokens;
}

export function validateExactInferencePolicy(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid-inference-policy", "exact inference policy must be an object");
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...EXACT_KEYS].sort())) {
    fail("invalid-inference-policy", "exact inference policy has an unexpected field");
  }
  if (value.enforcement !== "exact-request-boundary-v1" || value.wireApi !== "openai-chat-completions") {
    fail("invalid-inference-policy", "exact inference policy has an unsupported enforcement or wire API");
  }
  integer(value.temperaturePermille, 0, 2000, "temperaturePermille");
  integer(value.topPPermille, 0, 1000, "topPPermille");
  integer(value.topK, 0, 1000000, "topK");
  integer(value.minPPermille, 0, 1000, "minPPermille");
  integer(value.repeatPenaltyPermille, 0, 10000, "repeatPenaltyPermille");
  integer(value.seed, 0, 4294967295, "seed");
  if (!["backend-default", "low", "medium", "high", "max"].includes(value.reasoningEffort)) {
    fail("invalid-inference-policy", "reasoningEffort is invalid");
  }
  if (value.reasoningVisibility !== "hidden" || value.stream !== true || value.toolEncoding !== "function") {
    fail("invalid-inference-policy", "exact inference response or tool policy is invalid");
  }
  integer(value.maxOutputTokens, 1, 500000000, "maxOutputTokens");
  const explicitReasoning = value.reasoningEffort !== "none" && value.reasoningEffort !== "backend-default";
  if (value.reasoningVisibility === "hidden" && explicitReasoning) {
    const admissionFloor = minHiddenReasoningAdmissionBudget(value.reasoningEffort);
    if (value.maxOutputTokens < admissionFloor) {
      fail("inference-budget-insufficient", `hidden reasoning admission requires an effective output budget of at least ${admissionFloor} tokens (an admission floor, not a completion guarantee)`);
    }
  }
  const frozen = Object.freeze({ ...value });
  return Object.freeze({ policy: frozen, sha256: inferencePolicySha256(frozen) });
}

export function normalizeVllmChatRequest(body, rawPolicy, outputCeiling) {
  const { policy, sha256 } = validateExactInferencePolicy(rawPolicy);
  integer(outputCeiling, 1, policy.maxOutputTokens, "effective output ceiling");
  if (!body || typeof body !== "object" || Array.isArray(body)) fail("invalid-inference-request", "model request must be an object");
  const normalized = { ...body };
  for (const field of CLIENT_CONTROLLED_FIELDS) delete normalized[field];
  normalized.temperature = policy.temperaturePermille / 1000;
  normalized.top_p = policy.topPPermille / 1000;
  normalized.top_k = policy.topK;
  normalized.min_p = policy.minPPermille / 1000;
  normalized.repetition_penalty = policy.repeatPenaltyPermille / 1000;
  normalized.seed = policy.seed;
  normalized.presence_penalty = 0;
  normalized.frequency_penalty = 0;
  normalized.n = 1;
  normalized.ignore_eos = false;
  normalized.min_tokens = 0;
  const structuredOutput = normalized.response_format?.type === "json_object" || normalized.response_format?.type === "json_schema";
  if (structuredOutput) normalized.reasoning_effort = "none";
  else if (policy.reasoningEffort !== "backend-default") normalized.reasoning_effort = policy.reasoningEffort;
  normalized.include_reasoning = false;
  normalized.stream = true;
  normalized.stream_options = { include_usage: true };
  normalized.max_tokens = outputCeiling;
  const explicitReasoning = policy.reasoningEffort !== "none" && policy.reasoningEffort !== "backend-default";
  if (policy.reasoningVisibility === "hidden" && explicitReasoning) {
    const admissionFloor = minHiddenReasoningAdmissionBudget(policy.reasoningEffort);
    if (outputCeiling < admissionFloor) {
      fail("inference-budget-insufficient", `hidden reasoning admission requires an effective output budget of at least ${admissionFloor} tokens (an admission floor, not a completion guarantee)`);
    }
  }
  return Object.freeze({ body: normalized, policySha256: sha256 });
}

export const exactInferencePolicyContract = Object.freeze({
  controlledFields: [...CLIENT_CONTROLLED_FIELDS],
  enforcement: "exact-request-boundary-v1",
  wireApi: "openai-chat-completions",
});

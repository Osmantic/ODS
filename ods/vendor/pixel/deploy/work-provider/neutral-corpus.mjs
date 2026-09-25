import { createHash } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { validateNeutralRequest } from "./adapter-contract.mjs";
import { isForbiddenModelValue } from "./provider-registry.mjs";
import { PATCH_SUBMISSION_TOOL, PATCH_SUBMISSION_TOOL_CHOICE } from "./patch-extraction.mjs";

// Versioned fixed neutral qualification corpus. It is deliberately immutable and free of any
// provider-specific pricing, credentials, prompts that would leak owner content, or response
// text. Every case carries exact canonical hashes so a qualification-trial run can bind the
// corpus and so each transport may admit only exact corpus requests. The corpus is
// lane-specific only where the model identifier and the K3 reasoning-replay boundary differ;
// a connectivity-smoke probe is never a semantic corpus case.
//
// The tool-choice-continuation case is a genuine two-request exchange: request 1 presents
// fixed neutral tool definitions and must independently cause the provider to choose edit_file
// with exact bounded arguments; request 2 is assembled by the runner from the provider's exact
// returned assistant message (provider state, and for K3 the returned non-empty
// reasoning_content preserved verbatim) plus a fixed synthetic tool result, then requires an
// exact semantic JSON observation. No assistant tool call or reasoning_content is fabricated in the corpus.

export class WorkProviderNeutralCorpusError extends Error {}
function fail(message) { throw new WorkProviderNeutralCorpusError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

export const NEUTRAL_CORPUS_VERSION = "8";
export const QUALIFICATION_TASKS = Object.freeze(["structural-review", "patch-proposal", "tool-choice-continuation", "long-context-sentinel", "failure-triage", "output-capacity"]);
export const QUALIFICATION_LANES = Object.freeze(["local-only", "moonshot-kimi", "openai", "anthropic", "openrouter"]);
export const LONG_CONTEXT_SENTINEL = "LONG_CONTEXT_SENTINEL_9f8e7d6c";
export const LONG_CONTEXT_REQUIRED_REPLY = `${LONG_CONTEXT_SENTINEL} CONFIRMED`;
export const LONG_CONTEXT_MIN_INPUT_TOKENS = 32768;
export const OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS = 512;
// Use a short ASCII word so the fixed 600-item reply stays below the 2,048-token
// request ceiling in practice; qualification still relies on each provider's
// reported token count meeting the independent 512-token floor.
export const OUTPUT_CAPACITY_WORD = "hello";
export const OUTPUT_CAPACITY_REPEATS = 600;
export const OUTPUT_CAPACITY_REPLY = Array.from({ length: OUTPUT_CAPACITY_REPEATS }, () => OUTPUT_CAPACITY_WORD).join(" ");
export const OUTPUT_CAPACITY_EXPECTED = Object.freeze({ word: OUTPUT_CAPACITY_WORD, minimumWords: OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS });
export const OUTPUT_CAPACITY_REPORTED_TOKENS = 600;
export const TOOL_CHOICE_TOOL_NAME = "edit_file";
export const TOOL_CHOICE_TOOL_ARGUMENTS = '{"path":"file.txt"}';
export const TOOL_CHOICE_SYNTHETIC_RESULT = "file.txt now ends with a newline.";
export const TOOL_CHOICE_FINAL_RESULT = Object.freeze({ observedTool: TOOL_CHOICE_TOOL_NAME, result: TOOL_CHOICE_SYNTHETIC_RESULT });
export const TOOL_CHOICE_FINAL_REPLY = JSON.stringify(TOOL_CHOICE_FINAL_RESULT);
export const TOOL_CHOICE_FOLLOW_UP = "Return only one JSON object with exactly two keys: observedTool must be the exact name of the tool you selected, and result must be the exact tool result you just received. Include no other text.";

const LOCAL_MODEL = "DeepSeek-V4-Flash-0731";
const K3_MODEL = "kimi-k3";
const OPENAI_MODEL = "gpt-5.6";
const ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929";

// Closed lane -> model binding. A lane with a null model is owner-pinned: the exact run model is
// resolved from the owner-private policy at qualification time and never from a placeholder or a
// caller-supplied default. local-only is qualification-pinned to its sealed model.
const LANE_MODEL = Object.freeze({
  "local-only": LOCAL_MODEL,
  "moonshot-kimi": K3_MODEL,
  openai: OPENAI_MODEL,
  anthropic: ANTHROPIC_MODEL,
  openrouter: null,
});

// Anthropic extended thinking requires max_tokens strictly above its per-effort thinking budget,
// so the fixed corpus must give the anthropic lane headroom instead of silently dropping the
// sealed reasoning effort or inventing provider capability.
const ANTHROPIC_THINKING_BUDGET = Object.freeze({ low: 1024, high: 4096, max: 8192 });
function reasoningCeiling(lane, maxOutputTokens, reasoningEffort) {
  if (lane === "anthropic" && reasoningEffort !== "none") {
    const budget = ANTHROPIC_THINKING_BUDGET[reasoningEffort] ?? 0;
    if (maxOutputTokens <= budget) return budget + 1;
  }
  return maxOutputTokens;
}

// Remote reasoning lanes carry preserved reasoning in their protocol provider state and must
// replay it verbatim on the tool-choice continuation turn. local-only is the sole raw-diff lane
// with no preserved reasoning.
export function laneRequiresReasoning(lane) {
  // Moonshot, OpenAI, and Anthropic guarantee reasoning in their protocol provider state.
  // OpenRouter is provider-dependent: the corresponding adapter never guarantees
  // reasoning_content, so the lane must not require it.
  return lane !== "local-only" && lane !== "openrouter";
}

// Returns the canonical reasoning-evidence value carried in a returned assistant message for a
// remote reasoning lane, or null when that lane does not expose preserved reasoning. The evidence
// is derived from the exact provider-state so replay equality is byte-verifiable. local-only has
// no preserved reasoning and always yields null.
export function laneReasoningEvidence(lane, assistantMessage) {
  const state = assistantMessage?.providerState ?? {};
  switch (lane) {
    case "moonshot-kimi": {
      const value = state.assistantMessage?.reasoning_content;
      return typeof value === "string" && value.length > 0 ? value : null;
    }
    case "openai": {
      const items = state.outputItems;
      if (!Array.isArray(items)) return null;
      const reasoning = items.filter((item) => item?.type === "reasoning");
      return reasoning.length ? canonical(reasoning) : null;
    }
    case "anthropic": {
      const blocks = state.content;
      if (!Array.isArray(blocks)) return null;
      const thinking = blocks.filter((block) => block?.type === "thinking" || block?.type === "redacted_thinking");
      return thinking.length ? canonical(thinking) : null;
    }
    default:
      return null;
  }
}

// Resolves the sealed run model for a qualification/equivalence lane. fixed lanes bind their
// profile default; owner-pinned lanes bind the exact owner-private model; local-only binds its
// qualification-pinned model.
export function laneModelFor(lane, resolvedProvider, privatePolicy = null) {
  if (lane === "local-only") return LOCAL_MODEL;
  if (resolvedProvider?.profile?.modelSelection === "owner-pinned") {
    const pinned = privatePolicy?.model;
    if (typeof pinned !== "string" || pinned.length < 1 || pinned.length > 256 || isForbiddenModelValue(pinned) || /[\r\n\u0000]/u.test(pinned)) fail("owner-pinned lane requires an exact owner-private model");
    return pinned;
  }
  if (typeof resolvedProvider?.profile?.defaultModel !== "string" || resolvedProvider.profile.defaultModel.length < 1) fail("fixed lane requires a profile default model");
  return resolvedProvider.profile.defaultModel;
}

const STRUCTURAL_MODULE = [
  "export function add(a, b) {",
  "  return a - b;",
  "}",
  "export function double(value) {",
  "  return value * 2;",
  "}",
  "export const LABEL = \"neutral\";",
].join("\n");
const STRUCTURAL_EXPECTED = { exportCount: 3, defectFunction: "add", defectKind: "wrong-operator" };

const TRIAGE_LOG = [
  "12:00:01 INFO  op=read-sensor sensor=room-temp attempt=1 status=started",
  "12:00:02 WARN  op=read-sensor sensor=room-temp attempt=1 status=error error=connection-timeout retryable=true",
  "12:00:06 INFO  op=read-sensor sensor=room-temp attempt=2 status=success",
].join("\n");
const TRIAGE_EXPECTED = { classification: "transient", retried: true, resolution: "retry-succeeded" };

const TOOL_DEFINITIONS = Object.freeze([Object.freeze({
  name: "edit_file",
  description: "Apply a single bounded file edit at the given path.",
  parameters: {
    type: "object",
    properties: { path: { type: "string" } },
    required: ["path"],
    additionalProperties: false,
  },
  strict: true,
})]);

function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

function user(content) { return { role: "user", content }; }
function laneSampling(lane, reasoningEffort, localTemperature = 1) {
  return lane === "local-only"
    ? { reasoningEffort, temperature: localTemperature }
    : { reasoningEffort };
}

function baseInput(lane, model, messages, overrides = {}) {
  const input = { schemaVersion: 1, model, messages, maxOutputTokens: 1024, ...overrides };
  input.maxOutputTokens = reasoningCeiling(lane, input.maxOutputTokens, input.reasoningEffort);
  try { validateNeutralRequest(input); } catch (error) { fail(`neutral corpus case input failed validation: ${error.message}`); }
  return input;
}

const PATCH_BASE = "export function add(a, b) {\n  return a - b;\n}\n";
const PATCH_EXPECTED = "export function add(a, b) {\n  return a + b;\n}\n";

function longContextContent() {
  const parts = [];
  parts.push(`The unique sentinel token is ${LONG_CONTEXT_SENTINEL}. It appears exactly once in this message, near the very beginning.`);
  let chars = parts[0].length;
  let index = 0;
  while (chars < 220000) {
    const line = `Neutral deterministic filler fragment ${index}: the quick brown fox jumps over the lazy dog and returns a stable deterministic value without any owner content. `;
    parts.push(line);
    chars += line.length;
    index += 1;
  }
  parts.push(`At the very end of this message is the instruction. Your entire final reply must be exactly the unique sentinel token that appeared once near the very beginning of this message, then a single space, then CONFIRMED, and nothing before or after. Do not restate, define, or repeat the sentinel value anywhere.`);
  return parts.join("\n");
}

function caseDefinitions(lane, model) {
  const structuralInput = baseInput(lane, model, [
    user(`Perform a structural review of the following neutral module. Count the number of exported symbols it declares. Identify, if present, an exported function whose implementation contradicts its name (a structural defect). Return exactly this JSON object and nothing else, deriving every value from the module:\n{"exportCount": <integer>,\n "defectFunction": <exact exported function name or null>,\n "defectKind": <"wrong-operator" or null>}\n\nModule:\n${STRUCTURAL_MODULE}`),
  ], laneSampling(lane, "low"));

  const patchPrompt = `Role: you are a code-patching tool; you submit exactly one valid unified diff and nothing else.\nTask: produce one valid unified diff for the existing file fixture.mjs that changes only the single line '  return a - b;' to '  return a + b;'. Nothing else changes.\nRequired output shape (structural example only; use the real base and change below, not this placeholder content):\ndiff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function sample(x) {\n-  return x - 1;\n+  return x + 1;\n }\nOutput constraints: emit exactly eight lines in that order, no Markdown fence, no prose, no blank lines, a single trailing newline, and the file's exact two-space indentation. The diff must apply cleanly to the base and reproduce the required final file byte-for-byte.\nExisting fixture.mjs (base):\n${PATCH_BASE}`;
  const patchPromptWithDiffPrefixGuard = `${patchPrompt}\nCritical unified-diff prefix check: line 5 must begin with one ASCII space and be exactly ' export function add(a, b) {'; line 8 must begin with one ASCII space and be exactly ' }'. Those spaces are diff context markers, not optional formatting. The minus and plus lines must begin with their respective diff markers. Do not omit any leading diff-marker space.`;
  const patchInput = lane !== "local-only"
    ? baseInput(lane, model, [
        user(`${patchPromptWithDiffPrefixGuard}\nSubmit the diff by calling the ${PATCH_SUBMISSION_TOOL.name} tool exactly once with the complete unified diff as its required string patch argument. Do not include any other text or tool call.`),
      ], {
        ...laneSampling(lane, "high"),
        tools: [PATCH_SUBMISSION_TOOL],
        toolChoice: PATCH_SUBMISSION_TOOL_CHOICE,
      })
    : baseInput(lane, model, [user(patchPromptWithDiffPrefixGuard)], laneSampling(lane, "none", 0));

  const triageInput = baseInput(lane, model, [
    user(`Triage the following neutral operation log. Classify the failure as "transient" if a retryable error was followed by a later successful retry, otherwise "permanent". State whether a retry occurred and the final resolution. Return exactly this JSON object and nothing else, deriving every value from the log:\n{"classification": <"transient"|"permanent">,\n "retried": <boolean>,\n "resolution": <"retry-succeeded"|"no-retry"|"failed">}\n\nLog:\n${TRIAGE_LOG}`),
  ], laneSampling(lane, "low"));

  const firstRequest = baseInput(lane, model, [
    user(`You have a single tool named ${TOOL_CHOICE_TOOL_NAME} that takes a required path argument. Use it to edit file.txt by appending a trailing newline. Call ${TOOL_CHOICE_TOOL_NAME} exactly once with the path "file.txt" and no other arguments.`),
  ], { ...laneSampling(lane, "low"), tools: TOOL_DEFINITIONS, toolChoice: "auto" });

  const sentinelInput = baseInput(lane, model, [
    user(longContextContent()),
  ], { ...laneSampling(lane, "low"), maxOutputTokens: 512 });

  const outputCapacityInput = baseInput(lane, model, [
    user(`Reproduce the fixed neutral reference block below. Your reply may use any ASCII whitespace between words, but every non-whitespace word must be exactly ${OUTPUT_CAPACITY_WORD}, there must be at least ${OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS} words, and there must be no label, numbering, quote, fence, or other text.\n\nReference block:\n${OUTPUT_CAPACITY_REPLY}`),
  ], {
    ...laneSampling(lane, lane === "local-only" ? "none" : "low"),
    maxOutputTokens: lane === "local-only" ? 2048 : 4096,
  });

  return [
    { caseId: "structural-review-1", task: "structural-review", requestCount: 1, input: structuralInput, expected: STRUCTURAL_EXPECTED },
    { caseId: "patch-proposal-1", task: "patch-proposal", requestCount: 1, input: patchInput, expected: PATCH_EXPECTED, base: PATCH_BASE },
    {
      caseId: "tool-choice-continuation-1", task: "tool-choice-continuation", requestCount: 2,
      firstRequest,
      toolName: TOOL_CHOICE_TOOL_NAME,
      toolArguments: TOOL_CHOICE_TOOL_ARGUMENTS,
      syntheticToolResult: TOOL_CHOICE_SYNTHETIC_RESULT,
      followUpUser: TOOL_CHOICE_FOLLOW_UP,
      expected: { chosenTool: TOOL_CHOICE_TOOL_NAME, continuation: TOOL_CHOICE_FINAL_RESULT },
    },
    { caseId: "long-context-sentinel-1", task: "long-context-sentinel", requestCount: 1, input: sentinelInput, expected: LONG_CONTEXT_REQUIRED_REPLY },
    { caseId: "failure-triage-1", task: "failure-triage", requestCount: 1, input: triageInput, expected: TRIAGE_EXPECTED },
    { caseId: "output-capacity-1", task: "output-capacity", requestCount: 1, input: outputCapacityInput, expected: OUTPUT_CAPACITY_EXPECTED },
  ];
}

function buildCase(definition) {
  const caseSha256 = sha({
    task: definition.task,
    requestCount: definition.requestCount,
    expected: definition.expected,
    base: definition.base ?? null,
    ...(definition.requestCount === 1
      ? { input: definition.input }
      : { firstRequest: definition.firstRequest, toolName: definition.toolName, toolArguments: definition.toolArguments, syntheticToolResult: definition.syntheticToolResult, followUpUser: definition.followUpUser }),
  });
  if (definition.requestCount === 1) {
    return { ...definition, inputSha256: sha(definition.input), caseSha256, base: definition.base ?? null };
  }
  return { ...definition, firstRequestSha256: sha(definition.firstRequest), caseSha256, base: null };
}

function buildCorpus(lane, model) {
  if (!QUALIFICATION_LANES.includes(lane)) fail("neutral corpus lane is outside the closed registry");
  const boundModel = LANE_MODEL[lane];
  if (boundModel !== null) {
    if (model !== boundModel) fail("neutral corpus model differs from the bound lane");
  } else if (typeof model !== "string" || model.length < 1 || model.length > 256 || isForbiddenModelValue(model) || /[\r\n\u0000]/u.test(model)) {
    fail("owner-pinned neutral corpus model is invalid");
  }
  const cases = caseDefinitions(lane, model).map(buildCase);
  const manifestCases = cases.map((entry) => {
    const { input, firstRequest, ...rest } = entry;
    return rest;
  });
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-neutral-corpus-v1.schema.json",
    schemaVersion: 1,
    corpusVersion: NEUTRAL_CORPUS_VERSION,
    lane,
    model,
    cases: manifestCases,
    boundary: "Versioned content-free neutral provider qualification corpus. It declares only exact task/request hashes and fixed deterministic rubrics; it carries no prompt text, response text, reasoning, tool arguments, credential, path, or owner content in its declarative manifest. A connectivity-smoke probe is never a semantic corpus case.",
  };
  const full = { ...manifest, cases };
  const corpusSha256 = sha(full);
  return deepFreeze({ ...full, corpusSha256 });
}

export function buildNeutralCorpus({ lane, model }) {
  return buildCorpus(lane, model);
}

export function neutralCorpusSha256(corpus) {
  if (!corpus || typeof corpus !== "object" || Array.isArray(corpus)) fail("neutral corpus is invalid");
  const { corpusSha256: _ignored, ...without } = corpus;
  return sha(without);
}

export function findNeutralCorpusCase({ lane, model, input }) {
  if (input === undefined || input === null || typeof input !== "object") return null;
  const corpus = buildCorpus(lane, model);
  const inputSha256 = sha(input);
  const match = corpus.cases.find((entry) => entry.requestCount === 1 ? entry.inputSha256 === inputSha256 : entry.firstRequestSha256 === inputSha256);
  return match ? deepFreeze(structuredClone(match)) : null;
}

export function validateNeutralToolChoiceReplay({ lane, model, input }) {
  if (input === undefined || input === null || typeof input !== "object") return false;
  if (input.schemaVersion !== 1 || input.model !== model || input.tools !== undefined) return false;
  const corpus = buildCorpus(lane, model);
  const entry = corpus.cases.find((candidate) => candidate.task === "tool-choice-continuation");
  if (!entry) return false;
  const first = entry.firstRequest;
  const expectedReasoning = lane === "local-only" ? "none" : first.reasoningEffort;
  const expectedKeys = lane === "local-only"
    ? "maxOutputTokens,messages,model,reasoningEffort,schemaVersion,temperature"
    : "maxOutputTokens,messages,model,reasoningEffort,schemaVersion";
  if (Object.keys(input).sort().join(",") !== expectedKeys
    || input.maxOutputTokens !== first.maxOutputTokens || input.reasoningEffort !== expectedReasoning) return false;
  if (lane === "local-only" && input.temperature !== first.temperature) return false;
  const messages = input.messages;
  if (!Array.isArray(messages) || messages.length !== 4) return false;
  if (messages[0]?.role !== "user" || canonical(messages[0]) !== canonical(first.messages[0])) return false;
  const assistant = messages[1];
  if (assistant?.role !== "assistant" || !Array.isArray(assistant.toolCalls) || assistant.toolCalls.length !== 1) return false;
  const call = assistant.toolCalls[0];
  if (call.name !== entry.toolName || !matchesNeutralToolArguments(call.arguments)) return false;
  if (!assistant.providerState || typeof assistant.providerState !== "object") return false;
  if (lane === "local-only") {
    if (assistant.providerState.assistantMessage?.role !== "assistant") return false;
  } else if (lane === "openrouter") {
    // OpenRouter reasoning is provider-dependent and never guaranteed; replay must preserve the
    // complete returned assistant message (role + tool calls) rather than invent a reasoning claim.
    const saved = assistant.providerState.assistantMessage;
    if (!saved || saved.role !== "assistant" || !Array.isArray(saved.tool_calls) || saved.tool_calls.length !== 1) return false;
    const savedCall = saved.tool_calls[0];
    if (savedCall?.type !== "function"
      || savedCall.id !== call.id
      || savedCall.function?.name !== call.name
      || savedCall.function?.arguments !== call.arguments) return false;
  } else if (laneReasoningEvidence(lane, assistant) === null) {
    return false;
  }
  if (messages[2]?.role !== "tool" || messages[2]?.toolCallId !== call.id || messages[2]?.content !== entry.syntheticToolResult) return false;
  if (messages[3]?.role !== "user" || messages[3]?.content !== entry.followUpUser) return false;
  return true;
}

export function matchesNeutralToolArguments(value) {
  if (typeof value !== "string" || value.length < 1 || value.length > 4096) return false;
  try {
    const parsed = parseStrictJson(value, "neutral tool arguments");
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      && Object.keys(parsed).length === 1 && parsed.path === "file.txt";
  } catch { return false; }
}

export function admitNeutralCorpusRequest({ lane, model, input }) {
  const fixed = findNeutralCorpusCase({ lane, model, input });
  if (fixed) return { kind: "case", caseId: fixed.caseId };
  if (validateNeutralToolChoiceReplay({ lane, model, input })) return { kind: "tool-choice-replay" };
  return null;
}

export function listNeutralCorpusCases({ lane, model }) {
  return buildCorpus(lane, model).cases.map(({ input, firstRequest, ...rest }) => Object.freeze(structuredClone(rest)));
}

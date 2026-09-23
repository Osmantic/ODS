import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, readFile, realpath, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { canonical } from "../../scripts/lib/work-contract.mjs";
import { evaluateModelQualification, modelCapabilityReceiptSha256 } from "./model-qualification.mjs";

const MODULE_PATH = fileURLToPath(import.meta.url);
const SHA_RE = /^[a-f0-9]{64}$/u;
const IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/u;
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$/u;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}\.json$/u;
const ZERO_SHA256 = "0".repeat(64);
const profiles = Object.freeze(["assistant", "scout", "builder", "data-lab", "researcher"]);
const acceleratorClasses = new Set(["cpu", "nvidia-cuda", "amd-rocm", "intel-xpu", "apple-metal", "other-local"]);
const benchmarkProviders = new Set(["llama.cpp", "ollama", "vllm", "openai-compatible-local"]);
const boundary = "Content-free fixed local-model qualification evidence only. The synthetic benchmark grants no execution, data, credential, network beyond the configured loopback model, external-effect, or completion authority.";

const toolContracts = Object.freeze({
  "assistant-portal": Object.freeze([
    { type: "function", function: { name: "pixel_calendar_list", description: "List sanitized Calendar projections.", parameters: { type: "object", additionalProperties: false, required: ["timeMin", "timeMax"], properties: { timeMin: { type: "string" }, timeMax: { type: "string" }, query: { type: "string" }, maxResults: { type: "integer" } } } } },
    { type: "function", function: { name: "pixel_calendar_propose_delete", description: "Create a non-executing Calendar deletion proposal for separate operator approval.", parameters: { type: "object", additionalProperties: false, required: ["eventId", "expectedEtag"], properties: { eventId: { type: "string" }, expectedEtag: { type: "string" }, sendUpdates: { type: "string", enum: ["none", "all"] } } } } },
  ]),
  "scout-read-search": Object.freeze([
    { type: "function", function: { name: "read", description: "Read one relative file.", parameters: { type: "object", additionalProperties: false, required: ["path"], properties: { path: { type: "string" } } } } },
    { type: "function", function: { name: "search", description: "Search text in one relative directory.", parameters: { type: "object", additionalProperties: false, required: ["pattern", "path"], properties: { pattern: { type: "string" }, path: { type: "string" } } } } },
  ]),
  "builder-edit-bash": Object.freeze([
    { type: "function", function: { name: "read", description: "Read one relative file.", parameters: { type: "object", additionalProperties: false, required: ["path"], properties: { path: { type: "string" } } } } },
    { type: "function", function: { name: "edit", description: "Replace exact text in one relative file.", parameters: { type: "object", additionalProperties: false, required: ["path", "old", "replacement"], properties: { path: { type: "string" }, old: { type: "string" }, replacement: { type: "string" } } } } },
    { type: "function", function: { name: "bash", description: "Run one command in the disposable workspace.", parameters: { type: "object", additionalProperties: false, required: ["command"], properties: { command: { type: "string" } } } } },
  ]),
  "data-read-write": Object.freeze([
    { type: "function", function: { name: "read", description: "Read one relative dataset file.", parameters: { type: "object", additionalProperties: false, required: ["path"], properties: { path: { type: "string" } } } } },
    { type: "function", function: { name: "write", description: "Write one derived relative artifact.", parameters: { type: "object", additionalProperties: false, required: ["path", "content"], properties: { path: { type: "string" }, content: { type: "string" } } } } },
  ]),
  "research-search-read": Object.freeze([
    { type: "function", function: { name: "read", description: "Read one retained relative source.", parameters: { type: "object", additionalProperties: false, required: ["path"], properties: { path: { type: "string" } } } } },
    { type: "function", function: { name: "pixel_research", description: "Submit one public research query to the broker.", parameters: { type: "object", additionalProperties: false, required: ["query"], properties: { query: { type: "string" } } } } },
  ]),
});

const commonProfiles = Object.freeze([...profiles]);
const cases = Object.freeze([
  {
    id: "structured-json", category: "structured-output", profiles: commonProfiles,
    system: "Return only the exact JSON value requested. Do not add prose or markdown.",
    user: "Return the JSON object with status equal to ok and sequence equal to 17.", toolContract: null,
    context: null, maxOutputTokens: 128, minimumOutputTokens: 8, oracle: { kind: "exact-json", value: { status: "ok", sequence: 17 } },
  },
  {
    id: "sustained-structured-output", category: "structured-output", profiles: commonProfiles,
    system: "Return only the exact JSON value requested. Do not omit, reorder, summarize, or add any value.",
    user: "Return one JSON object whose only field is sequence and whose value is the array of every integer from 0 through 4095 inclusive in ascending order.", toolContract: null,
    context: null, maxOutputTokens: 16384, minimumOutputTokens: 8192,
    oracle: { kind: "exact-json", value: { sequence: Array.from({ length: 4096 }, (_, index) => index) } },
  },
  {
    id: "context-sentinel", category: "context-retention", profiles: commonProfiles,
    system: "Read the complete supplied context and return only its sentinel value.",
    user: "Return only the sentinel from the context block.", toolContract: null,
    context: { targetBytes: 262144, seed: "pixel-fixed-context-v1", sentinel: "PIXEL_CONTEXT_SENTINEL_7f3a19" },
    maxOutputTokens: 64, oracle: { kind: "exact-text", value: "PIXEL_CONTEXT_SENTINEL_7f3a19" },
  },
  {
    id: "recovery-no-repair", category: "recovery-discipline", profiles: commonProfiles,
    system: "Never silently repair malformed executable intent. Return only RETRY_REQUIRED when intent is ambiguous or malformed.",
    user: "A tool request arrived as {name:'bash',arguments:'{command: rm -rf ???'}. Follow the recovery rule.", toolContract: "builder-edit-bash",
    context: null, maxOutputTokens: 64, oracle: { kind: "exact-text", value: "RETRY_REQUIRED" },
  },
  {
    id: "usage-accounting", category: "usage-accounting", profiles: commonProfiles,
    system: "Return only the exact token USAGE_OK.", user: "Return the required token now.", toolContract: null,
    context: null, maxOutputTokens: 32, oracle: { kind: "exact-text", value: "USAGE_OK" },
  },
  {
    id: "assistant-tool-selection", category: "tool-selection", profiles: ["assistant"],
    system: "Select exactly one supplied tool. Do not answer in text.",
    user: "List calendar events from 2026-08-14T09:00:00-04:00 through 2026-08-14T17:00:00-04:00.",
    toolContract: "assistant-portal", context: null, maxOutputTokens: 192,
    oracle: { kind: "exact-tool", name: "pixel_calendar_list", arguments: { timeMin: "2026-08-14T09:00:00-04:00", timeMax: "2026-08-14T17:00:00-04:00" } },
  },
  {
    id: "assistant-argument-fidelity", category: "argument-fidelity", profiles: ["assistant"],
    system: "Select exactly one supplied tool and preserve every requested argument byte-for-byte.",
    user: "Propose deleting event evt_017 with expected ETag etag-42 and sendUpdates exactly none.",
    toolContract: "assistant-portal", context: null, maxOutputTokens: 192,
    oracle: { kind: "exact-tool", name: "pixel_calendar_propose_delete", arguments: { eventId: "evt_017", expectedEtag: "etag-42", sendUpdates: "none" } },
  },
  {
    id: "scout-tool-selection", category: "tool-selection", profiles: ["scout"],
    system: "Select exactly one supplied tool. Do not answer in text.", user: "Read evidence.txt.", toolContract: "scout-read-search",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "read", arguments: { path: "evidence.txt" } },
  },
  {
    id: "scout-argument-fidelity", category: "argument-fidelity", profiles: ["scout"],
    system: "Select exactly one supplied tool and preserve every requested argument byte-for-byte.", user: "Search src for the literal pattern alpha.*omega.", toolContract: "scout-read-search",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "search", arguments: { pattern: "alpha.*omega", path: "src" } },
  },
  {
    id: "builder-tool-selection", category: "tool-selection", profiles: ["builder"],
    system: "Select exactly one supplied tool. Do not answer in text.", user: "Run the command printf pixel.", toolContract: "builder-edit-bash",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "bash", arguments: { command: "printf pixel" } },
  },
  {
    id: "builder-argument-fidelity", category: "argument-fidelity", profiles: ["builder"],
    system: "Select exactly one supplied tool and preserve every requested argument byte-for-byte.", user: "In src/app.js replace OLD_VALUE with NEW_VALUE.", toolContract: "builder-edit-bash",
    context: null, maxOutputTokens: 160, oracle: { kind: "exact-tool", name: "edit", arguments: { path: "src/app.js", old: "OLD_VALUE", replacement: "NEW_VALUE" } },
  },
  {
    id: "data-tool-selection", category: "tool-selection", profiles: ["data-lab"],
    system: "Select exactly one supplied tool. Do not answer in text.", user: "Read datasets/input.csv.", toolContract: "data-read-write",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "read", arguments: { path: "datasets/input.csv" } },
  },
  {
    id: "data-argument-fidelity", category: "argument-fidelity", profiles: ["data-lab"],
    system: "Select exactly one supplied tool and preserve every requested argument byte-for-byte.", user: "Write the exact text rows=17 to artifacts/summary.txt.", toolContract: "data-read-write",
    context: null, maxOutputTokens: 160, oracle: { kind: "exact-tool", name: "write", arguments: { path: "artifacts/summary.txt", content: "rows=17" } },
  },
  {
    id: "research-tool-selection", category: "tool-selection", profiles: ["researcher"],
    system: "Select exactly one supplied tool. Do not answer in text.", user: "Research the public query Pixel release notes.", toolContract: "research-search-read",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "pixel_research", arguments: { query: "Pixel release notes" } },
  },
  {
    id: "research-argument-fidelity", category: "argument-fidelity", profiles: ["researcher"],
    system: "Select exactly one supplied tool and preserve every requested argument byte-for-byte.", user: "Read sources/source-017.txt.", toolContract: "research-search-read",
    context: null, maxOutputTokens: 128, oracle: { kind: "exact-tool", name: "read", arguments: { path: "sources/source-017.txt" } },
  },
]);

export class WorkModelQualificationRunnerError extends Error {}

function fail(message) { throw new WorkModelQualificationRunnerError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

// Deterministic unsigned 32-bit seed derived mechanically from the immutable
// case identity so vLLM sampling is reproducible across independent runs.
// It changes no prompt, case, oracle, tool contract, output budget, reasoning
// effort, or retry behavior; only the fixed sampling seed is pinned.
function qualificationSeed(entry) { return parseInt(sha(entry).slice(0, 8), 16); }
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

function validateModel(model) {
  exactKeys(model, ["provider", "id", "modelArtifactSha256", "backendImageDigest", "backendVersion", "acceleratorClass", "promptContractSha256", "toolSchemaSha256", "contextWindow", "supportsVision"], "qualification model");
  if (!benchmarkProviders.has(model.provider) || !MODEL_RE.test(model.id ?? "") || !SHA_RE.test(model.modelArtifactSha256 ?? "") || model.modelArtifactSha256 === ZERO_SHA256 || !IMAGE_RE.test(model.backendImageDigest ?? "") || model.backendImageDigest === `sha256:${ZERO_SHA256}` || !VERSION_RE.test(model.backendVersion ?? "") || !acceleratorClasses.has(model.acceleratorClass) || !SHA_RE.test(model.promptContractSha256 ?? "") || model.promptContractSha256 === ZERO_SHA256 || !SHA_RE.test(model.toolSchemaSha256 ?? "") || model.toolSchemaSha256 === ZERO_SHA256 || typeof model.supportsVision !== "boolean") fail("qualification model identity is invalid");
  integer(model.contextWindow, 1024, 2000000, "qualification model context window");
  return structuredClone(model);
}

function contextBlock(specification) {
  if (!specification) return "";
  let value = `${specification.seed}\n${specification.sentinel}\n`;
  for (let index = 0; Buffer.byteLength(value, "utf8") < specification.targetBytes; index += 1) value += `${specification.seed}-${String(index).padStart(6, "0")}\n`;
  return value.slice(0, specification.targetBytes - specification.sentinel.length - 2) + `\n${specification.sentinel}\n`;
}

// The fixed qualification suite contracts its reasoning cases to hidden high
// reasoning with an 8192-token maximum request output.  This is a fixed
// qualification-test contract for the current vLLM candidate, not a permanent
// DSV4 product guarantee: reasoning cases request the suite's output ceiling
// and reasoning effort instead of a 128-token slice that hidden reasoning alone
// exhausts into a truncated (finish_reason length) completion.  The finish
// reason and exact content oracle are unchanged, so this is contract alignment,
// not test gaming.  A future vLLM swap may re-pin these constants without
// changing the qualifier's structure.
const QUALIFICATION_MAX_REQUEST_OUTPUT = 8192;
const QUALIFICATION_REASONING_EFFORT = "high";

function requestForCase(entry, model) {
  const context = contextBlock(entry.context);
  const body = {
    model: model.id,
    messages: [
      { role: "system", content: entry.system },
      { role: "user", content: context ? `<context>\n${context}</context>\n${entry.user}` : entry.user },
    ],
    stream: false, temperature: 0, max_tokens: entry.maxOutputTokens,
  };
  if (model.provider === "vllm") {
    const structured = entry.oracle.kind === "exact-json";
    body.max_tokens = structured ? entry.maxOutputTokens : QUALIFICATION_MAX_REQUEST_OUTPUT;
    body.reasoning_effort = structured ? "none" : QUALIFICATION_REASONING_EFFORT;
    body.include_reasoning = false;
    body.seed = qualificationSeed(entry);
  }
  if (entry.toolContract) {
    body.tools = structuredClone(toolContracts[entry.toolContract]);
    body.tool_choice = entry.oracle.kind !== "exact-tool"
      ? "auto"
      : entry.category === "argument-fidelity"
        ? { type: "function", function: { name: entry.oracle.name } }
        : "required";
  }
  if (entry.oracle.kind === "exact-json") body.response_format = { type: "json_object" };
  return body;
}

function responseMessage(body) {
  if (!body || typeof body !== "object" || Array.isArray(body) || !Array.isArray(body.choices) || body.choices.length !== 1) return null;
  const message = body.choices[0]?.message;
  return message && typeof message === "object" && !Array.isArray(message) ? message : null;
}

function usage(body) {
  const observed = body?.usage;
  const inputTokens = observed?.prompt_tokens ?? observed?.input_tokens;
  const outputTokens = observed?.completion_tokens ?? observed?.output_tokens;
  return Number.isSafeInteger(inputTokens) && inputTokens >= 0 && Number.isSafeInteger(outputTokens) && outputTokens >= 0 ? { inputTokens, outputTokens } : null;
}

function inspectOracle(entry, body) {
  const message = responseMessage(body);
  if (!message) return { passed: false, intentMutationObserved: false, failureClass: "missing-response" };
  const finishReason = body.choices[0]?.finish_reason;
  const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  if (entry.oracle.kind === "exact-text") {
    let failureClass = "pass";
    if (finishReason !== "stop") failureClass = "finish-reason-mismatch";
    else if (toolCalls.length) failureClass = "unexpected-tool-call";
    else if (typeof message.content !== "string") failureClass = "content-type-mismatch";
    else if (message.content.trim() !== entry.oracle.value) failureClass = "content-mismatch";
    const passed = failureClass === "pass";
    return { passed, intentMutationObserved: entry.category === "recovery-discipline" && !passed, failureClass };
  }
  if (entry.oracle.kind === "exact-json") {
    if (finishReason !== "stop") return { passed: false, intentMutationObserved: toolCalls.length > 0, failureClass: "finish-reason-mismatch" };
    if (toolCalls.length) return { passed: false, intentMutationObserved: true, failureClass: "unexpected-tool-call" };
    if (typeof message.content !== "string") return { passed: false, intentMutationObserved: false, failureClass: "content-type-mismatch" };
    let parsed;
    try { parsed = JSON.parse(message.content); }
    catch { return { passed: false, intentMutationObserved: false, failureClass: "json-parse-failed" }; }
    const passed = canonical(parsed) === canonical(entry.oracle.value);
    return { passed, intentMutationObserved: false, failureClass: passed ? "pass" : "json-value-mismatch" };
  }
  const effectiveFinishReason = finishReason === "stop" && toolCalls.length > 0 ? "tool_calls" : finishReason;
  if (effectiveFinishReason !== "tool_calls") return { passed: false, intentMutationObserved: toolCalls.length > 0, failureClass: "finish-reason-mismatch" };
  if (message.content !== null && message.content !== "" || toolCalls.length !== 1 || toolCalls[0]?.type !== "function" || typeof toolCalls[0]?.function?.name !== "string" || typeof toolCalls[0]?.function?.arguments !== "string") return { passed: false, intentMutationObserved: toolCalls.length > 0, failureClass: "tool-call-shape-mismatch" };
  let arguments_;
  try { arguments_ = JSON.parse(toolCalls[0].function.arguments); }
  catch { return { passed: false, intentMutationObserved: true, failureClass: "tool-arguments-parse-failed" }; }
  if (toolCalls[0].function.name !== entry.oracle.name) return { passed: false, intentMutationObserved: true, failureClass: "tool-name-mismatch" };
  const passed = canonical(arguments_) === canonical(entry.oracle.arguments);
  return { passed, intentMutationObserved: !passed, failureClass: passed ? "pass" : "tool-arguments-mismatch" };
}

function observation(entry, result, advertisedContextWindow) {
  const observedUsage = result ? usage(result.body) : null;
  const inspected = result ? inspectOracle(entry, result.body) : { passed: false, intentMutationObserved: false, failureClass: "invocation-failed" };
  const outputTokensRequired = entry.minimumOutputTokens ?? 1;
  const failureClass = inspected.failureClass !== "pass" ? inspected.failureClass : observedUsage === null ? "usage-missing" : observedUsage.outputTokens < outputTokensRequired ? "output-underflow" : "pass";
  return {
    id: entry.id, category: entry.category, profiles: [...entry.profiles], caseSha256: sha(entry),
    passed: failureClass === "pass", failureClass, intentMutationObserved: inspected.intentMutationObserved,
    inputTokens: observedUsage?.inputTokens ?? 0, outputTokens: observedUsage?.outputTokens ?? 0,
    usageSource: observedUsage ? "backend-observed" : "estimated", latencyMs: result?.latencyMs ?? 0,
    contextTokensTested: observedUsage === null ? 1 : Math.min(observedUsage.inputTokens, advertisedContextWindow), outputTokensRequired,
  };
}

export function fixedModelQualificationCases() { return structuredClone(cases); }
export function fixedModelQualificationCasesSha256() {
  return sha(cases.map((entry) => ({ id: entry.id, category: entry.category, profiles: entry.profiles, caseSha256: sha(entry) })));
}
export async function fixedModelQualificationEvaluatorSha256() { return sha(await readFile(MODULE_PATH)); }

export async function runFixedModelQualification({ model, invoke, observedAt = new Date(), expiresAt, suffix = randomBytes(6).toString("hex") }) {
  const identity = validateModel(model);
  if (typeof invoke !== "function") fail("qualification model invoker is invalid");
  if (!(observedAt instanceof Date) || !Number.isSafeInteger(observedAt.getTime())) fail("qualification time is invalid");
  const expiry = expiresAt ?? new Date(observedAt.getTime() + 7 * 86400000);
  if (!(expiry instanceof Date) || !Number.isSafeInteger(expiry.getTime())) fail("qualification time is invalid");
  const evaluatorSha256 = await fixedModelQualificationEvaluatorSha256(), observations = [];
  for (const entry of cases) {
    let result = null;
    try {
      const candidate = await invoke(requestForCase(entry, identity), { id: entry.id, category: entry.category, profiles: [...entry.profiles] });
      if (candidate && typeof candidate === "object" && !Array.isArray(candidate) && candidate.body && typeof candidate.body === "object" && !Array.isArray(candidate.body) && Number.isSafeInteger(candidate.latencyMs) && candidate.latencyMs >= 0 && candidate.latencyMs <= 86400000) result = candidate;
    } catch { result = null; }
    observations.push(observation(entry, result, identity.contextWindow));
  }
  if (await fixedModelQualificationEvaluatorSha256() !== evaluatorSha256) fail("qualification evaluator changed during the benchmark");
  const receipt = evaluateModelQualification({ model: identity, cases: observations, evaluatorSha256, observedAt, expiresAt: expiry, suffix });
  if (receipt.suite.casesSha256 !== fixedModelQualificationCasesSha256()) fail("qualification receipt differs from the fixed benchmark corpus");
  return receipt;
}

function loopbackOrigin(value) {
  let parsed;
  try { parsed = new URL(value); } catch { fail("qualification backend origin is invalid"); }
  if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || parsed.port === "" || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) fail("qualification backend must be an explicit credential-free IPv4 loopback origin");
  return parsed.origin;
}

export function validateFixedModelQualificationConfig(config) {
  exactKeys(config, ["schemaVersion", "backendOrigin", "model", "timeoutMs", "maxResponseBytes", "qualificationLifetimeSeconds"], "qualification configuration");
  if (config.schemaVersion !== 1) fail("qualification configuration version is invalid");
  const model = validateModel(config.model);
  const backendOrigin = loopbackOrigin(config.backendOrigin);
  const timeoutMs = integer(config.timeoutMs, 100, 300000, "qualification timeout");
  const maxResponseBytes = integer(config.maxResponseBytes, 1024, 1048576, "qualification response limit");
  const qualificationLifetimeSeconds = integer(config.qualificationLifetimeSeconds, 60, 30 * 86400, "qualification lifetime");
  return Object.freeze({ schemaVersion: 1, backendOrigin, model, timeoutMs, maxResponseBytes, qualificationLifetimeSeconds });
}

async function boundedResponse(response, maximum) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^(?:0|[1-9][0-9]*)$/u.test(declared) || Number(declared) > maximum)) fail("qualification backend response length is invalid");
  if ((response.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase() !== "application/json" || response.headers.get("content-encoding")) fail("qualification backend response encoding is invalid");
  if (!response.body) fail("qualification backend response body is missing");
  const chunks = []; let total = 0;
  for await (const chunk of response.body) {
    total += chunk.length;
    if (total > maximum) fail("qualification backend response exceeded its byte limit");
    chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks, total);
  let value;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { fail("qualification backend response is not strict JSON"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("qualification backend response shape is invalid");
  return value;
}

export function createLoopbackModelQualificationInvoker(rawConfig, fetchImpl = globalThis.fetch) {
  const config = validateFixedModelQualificationConfig(rawConfig);
  if (typeof fetchImpl !== "function") fail("qualification HTTP client is unavailable");
  return async (body) => {
    const started = Date.now();
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), config.timeoutMs);
    try {
      const response = await fetchImpl(`${config.backendOrigin}/v1/chat/completions`, {
        method: "POST", redirect: "error", signal: controller.signal,
        headers: { "content-type": "application/json", "accept": "application/json", "connection": "close" },
        body: JSON.stringify(body),
      });
      if (response.status !== 200 || response.url && !response.url.startsWith(`${config.backendOrigin}/`)) fail("qualification backend request failed");
      return { body: await boundedResponse(response, config.maxResponseBytes), latencyMs: Math.min(86400000, Math.max(0, Date.now() - started)) };
    } finally { clearTimeout(timer); }
  };
}

async function readPrivateConfig(path) {
  let record;
  try { record = await readBoundedRegularFile(path, 65536, "qualification configuration"); } catch { fail("qualification configuration could not be opened safely"); }
  const actualPath = await realpath(path).catch(() => null);
  if (actualPath !== resolve(path) || record.details.nlink !== 1 || process.platform !== "win32" && (record.details.uid !== process.geteuid() || (record.details.mode & 0o077) !== 0)) fail("qualification configuration is not owner-private, single-link, and real");
  let value;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(record.bytes)); } catch { fail("qualification configuration is not strict JSON"); }
  return validateFixedModelQualificationConfig(value);
}

async function validateNewPrivateOutput(path) {
  const parent = dirname(path), name = basename(path);
  if (!OUTPUT_RE.test(name)) fail("qualification output name is invalid");
  const [parentInfo, actualParent, outputInfo] = await Promise.all([lstat(parent).catch(() => null), realpath(parent).catch(() => null), lstat(path).catch(() => null)]);
  if (!parentInfo?.isDirectory() || parentInfo.isSymbolicLink() || actualParent !== resolve(parent) || process.platform !== "win32" && (parentInfo.uid !== process.geteuid() || (parentInfo.mode & 0o077) !== 0)) fail("qualification output parent is not owner-private and real");
  if (outputInfo !== null) fail("qualification output must be a new private file");
}

async function privateOutput(path, receipt) {
  await validateNewPrivateOutput(path);
  const temporary = join(dirname(path), `.model-qualification-${process.pid}-${randomBytes(8).toString("hex")}.json`);
  let handle = null;
  try {
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
    await handle.writeFile(`${JSON.stringify(receipt, null, 2)}\n`);
    await handle.sync();
    await handle.close(); handle = null;
    await link(temporary, path);
    await unlink(temporary);
  } catch {
    await handle?.close().catch(() => {});
    await unlink(temporary).catch(() => {});
    fail("qualification output could not be atomically published as a new private file");
  }
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 4 || argv[0] !== "--config" || argv[2] !== "--output" || !argv[1] || !argv[3]) fail("Usage: model-qualification-runner.mjs --config PRIVATE_JSON --output NEW_PRIVATE_JSON");
  return { configPath: resolve(argv[1]), outputPath: resolve(argv[3]) };
}

export async function main(argv = process.argv.slice(2)) {
  const paths = parseArguments(argv), config = await readPrivateConfig(paths.configPath);
  await validateNewPrivateOutput(paths.outputPath);
  const now = new Date(), receipt = await runFixedModelQualification({
    model: config.model, invoke: createLoopbackModelQualificationInvoker(config), observedAt: now,
    expiresAt: new Date(now.getTime() + config.qualificationLifetimeSeconds * 1000),
  });
  await privateOutput(paths.outputPath, receipt);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-qualification", status: receipt.status,
    qualificationId: receipt.qualificationId, receiptSha256: modelCapabilityReceiptSha256(receipt),
    casesPassed: receipt.observations.casesPassed, casesFailed: receipt.observations.casesFailed,
    eligibleProfiles: [...receipt.envelope.eligibleProfiles], authority: structuredClone(receipt.authority), boundary,
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then((result) => { process.stdout.write(`${JSON.stringify(result)}\n`); if (result.status !== "qualified") process.exitCode = 2; }).catch((error) => {
    process.stderr.write(`pixel-work-model-qualification: ${error instanceof WorkModelQualificationRunnerError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

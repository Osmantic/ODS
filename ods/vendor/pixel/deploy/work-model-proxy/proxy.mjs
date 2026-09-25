import { createHash, randomBytes } from "node:crypto";
import { isIP } from "node:net";
import { createServer } from "node:http";
import { constants } from "node:fs";
import { lstat, open, rename, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { InferencePolicyError, normalizeVllmChatRequest, validateExactInferencePolicy } from "./inference-policy.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/;
const QUALIFICATION_RE = /^modelqual-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/;
const RUNTIME_PROVIDERS = new Set(["llama.cpp", "vllm"]);
const HOST_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
// Known harness tools may cross the model boundary only when the per-run lease
// names them explicitly.  This is a closed catalog, not a generic identifier
// pattern: it supports both the OMP Work surface and the real OpenClaw Assistant
// surface without granting an unreviewed plugin or future built-in automatically.
const TOOL_NAMES = Object.freeze([
  "apply_patch", "bash", "debug", "edit", "eval", "exec", "glob", "grep", "hub", "image", "lsp",
  "memory_get", "memory_search", "pixel_research", "process", "read", "session_status", "sessions_history",
  "sessions_list", "sessions_send", "sessions_spawn", "sessions_yield", "subagents", "task", "todo",
  "web_fetch", "web_search", "write", "yield",
]);
const CAPABILITY_TOOL_RE = /^pixel_cap_[a-z0-9_]{1,54}$/u;
const PIXEL_PLUGIN_TOOL_RE = /^pixel_[a-z0-9_]{2,63}$/u;
const MAX_CAPABILITY_TOOLS = 16;
const MAX_PIXEL_PLUGIN_TOOLS = 128;
const DISCOVERY_PATHS = new Set(["/models", "/props"]);
const INFERENCE_PATHS = new Set(["/responses", "/v1/chat/completions"]);
const FORBIDDEN_HEADERS = new Set([
  "authorization", "cookie", "forwarded", "proxy-authorization", "x-api-key", "x-forwarded-for",
  "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
]);
const CONFIG_KEYS = Object.freeze([
  "schemaVersion", "jobId", "claimId", "planSha256", "provider", "modelId", "contextWindow", "supportsVision",
  "backendOrigin", "listenHost", "listenPort", "allowedClientIpv4", "allowedTools", "receiptPath", "qualification", "inference", "budgets",
]);
const QUALIFICATION_KEYS = Object.freeze([
  "qualificationId", "receiptSha256", "casesSha256", "evaluatorSha256", "profile", "maxContextTokens", "maxOutputTokens", "exactUsage",
]);
const BUDGET_KEYS = Object.freeze([
  "maxRuntimeSeconds", "maxModelRequests", "maxInputTokens", "maxOutputTokens", "maxNetworkBytes",
  "maxRequestBytes", "maxResponseBytes", "maxRequestSeconds",
]);
const UTF8 = new TextDecoder("utf-8", { fatal: true });

export class WorkModelProxyError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = "WorkModelProxyError";
    this.code = code;
  }
}

function fail(code, message = code) {
  throw new WorkModelProxyError(code, message);
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid-config", `${label} must be an object`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) fail("invalid-config", `${label} has an unexpected field`);
}

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail("invalid-config", `${label} is invalid`);
  return value;
}

function normalizePeer(value) {
  if (typeof value !== "string") return "";
  return value.startsWith("::ffff:") ? value.slice(7) : value;
}

function privateIpv4(value, allowLoopback = false) {
  if (isIP(value) !== 4) return false;
  const parts = value.split(".").map(Number);
  return (
    (allowLoopback && parts[0] === 127)
    || parts[0] === 10
    || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
    || (parts[0] === 192 && parts[1] === 168)
  );
}

function validateBackendOrigin(value) {
  let parsed;
  try { parsed = new URL(value); } catch { fail("invalid-config", "backendOrigin is invalid"); }
  if (
    parsed.protocol !== "http:"
    || parsed.username !== ""
    || parsed.password !== ""
    || parsed.pathname !== "/"
    || parsed.search !== ""
    || parsed.hash !== ""
    || parsed.port === ""
  ) fail("invalid-config", "backendOrigin must be a credential-free HTTP origin with an explicit port");
  const host = parsed.hostname.toLowerCase();
  const ipVersion = isIP(host);
  if ((ipVersion !== 0 && !privateIpv4(host, true)) || (ipVersion === 0 && (!HOST_RE.test(host) || host === "localhost"))) {
    fail("invalid-config", "backendOrigin must name a private address or a bounded container alias");
  }
  return `${parsed.protocol}//${parsed.host}`;
}

export function validateModelProxyConfig(value) {
  exactKeys(value, CONFIG_KEYS, "model proxy config");
  if (value.schemaVersion !== 1 || !JOB_RE.test(value.jobId ?? "") || !CLAIM_RE.test(value.claimId ?? "") || !SHA_RE.test(value.planSha256 ?? "")) {
    fail("invalid-config", "model proxy claim binding is invalid");
  }
  if (!RUNTIME_PROVIDERS.has(value.provider)) fail("invalid-config", "provider is invalid");
  if (!MODEL_RE.test(value.modelId ?? "")) fail("invalid-config", "modelId is invalid");
  integer(value.contextWindow, 1024, 2000000, "contextWindow");
  if (typeof value.supportsVision !== "boolean") fail("invalid-config", "supportsVision is invalid");
  if (!["0.0.0.0", "127.0.0.1"].includes(value.listenHost)) fail("invalid-config", "listenHost is invalid");
  integer(value.listenPort, 1, 65535, "listenPort");
  if (!privateIpv4(value.allowedClientIpv4, true)) fail("invalid-config", "allowedClientIpv4 is not private IPv4");
  const capabilityTools = Array.isArray(value.allowedTools)
    ? value.allowedTools.filter((tool) => !TOOL_NAMES.includes(tool) && CAPABILITY_TOOL_RE.test(tool))
    : [];
  const pixelPluginTools = Array.isArray(value.allowedTools)
    ? value.allowedTools.filter((tool) => !TOOL_NAMES.includes(tool) && !CAPABILITY_TOOL_RE.test(tool) && PIXEL_PLUGIN_TOOL_RE.test(tool))
    : [];
  if (
    !Array.isArray(value.allowedTools) || value.allowedTools.length < 1
    || value.allowedTools.length > TOOL_NAMES.length + MAX_CAPABILITY_TOOLS + MAX_PIXEL_PLUGIN_TOOLS
    || new Set(value.allowedTools).size !== value.allowedTools.length
    || value.allowedTools.some((tool) => !TOOL_NAMES.includes(tool) && !CAPABILITY_TOOL_RE.test(tool) && !PIXEL_PLUGIN_TOOL_RE.test(tool))
    || capabilityTools.length > MAX_CAPABILITY_TOOLS
    || pixelPluginTools.length > MAX_PIXEL_PLUGIN_TOOLS
    || value.allowedTools.includes("task") !== value.allowedTools.includes("yield")
    || JSON.stringify(value.allowedTools) !== JSON.stringify([...value.allowedTools].sort())
  ) fail("invalid-config", "allowedTools is invalid");
  if (value.receiptPath !== "/run/pixel-work-output/model-proxy-receipt.json") fail("invalid-config", "receiptPath is invalid");
  exactKeys(value.qualification, QUALIFICATION_KEYS, "model qualification");
  if (
    !QUALIFICATION_RE.test(value.qualification.qualificationId ?? "")
    || !SHA_RE.test(value.qualification.receiptSha256 ?? "")
    || !SHA_RE.test(value.qualification.casesSha256 ?? "")
    || !SHA_RE.test(value.qualification.evaluatorSha256 ?? "")
    || !["assistant", "scout", "builder", "data-lab", "researcher"].includes(value.qualification.profile)
    || value.qualification.exactUsage !== true
  ) fail("invalid-config", "model qualification binding is invalid");
  integer(value.qualification.maxContextTokens, 1, 2000000, "qualified maxContextTokens");
  integer(value.qualification.maxOutputTokens, 1, 500000000, "qualified maxOutputTokens");
  if (value.contextWindow > value.qualification.maxContextTokens) fail("invalid-config", "model context exceeds its qualification");
  let inference = null;
  if (value.provider === "vllm") {
    try { inference = validateExactInferencePolicy(value.inference); } catch { fail("invalid-config", "vLLM requires an exact inference policy"); }
    if (inference.policy.maxOutputTokens > value.qualification.maxOutputTokens) {
      fail("invalid-config", "exact inference output ceiling exceeds its qualification");
    }
  } else if (value.inference !== null) {
    fail("invalid-config", "legacy llama.cpp routing does not claim exact inference enforcement");
  }
  exactKeys(value.budgets, BUDGET_KEYS, "model proxy budgets");
  integer(value.budgets.maxRuntimeSeconds, 1, 604800, "maxRuntimeSeconds");
  integer(value.budgets.maxModelRequests, 1, 100000, "maxModelRequests");
  integer(value.budgets.maxInputTokens, 1, 2000000000, "maxInputTokens");
  integer(value.budgets.maxOutputTokens, 1, 500000000, "maxOutputTokens");
  integer(value.budgets.maxNetworkBytes, 1, 10737418240, "maxNetworkBytes");
  integer(value.budgets.maxRequestBytes, 1, 268435456, "maxRequestBytes");
  integer(value.budgets.maxResponseBytes, 1, 1073741824, "maxResponseBytes");
  integer(value.budgets.maxRequestSeconds, 1, value.budgets.maxRuntimeSeconds, "maxRequestSeconds");
  if (value.budgets.maxRequestBytes > value.budgets.maxNetworkBytes || value.budgets.maxResponseBytes > value.budgets.maxNetworkBytes) {
    fail("invalid-config", "per-request bytes exceed the network budget");
  }
  return Object.freeze({
    ...value,
    backendOrigin: validateBackendOrigin(value.backendOrigin),
    qualification: Object.freeze({ ...value.qualification }),
    inference: inference === null ? null : inference.policy,
    inferencePolicySha256: inference?.sha256 ?? null,
    budgets: Object.freeze({ ...value.budgets }),
  });
}

function jsonDepthAndSize(value, limits, depth = 0) {
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
    for (const item of value) jsonDepthAndSize(item, limits, depth + 1);
    return;
  }
  if (!value || typeof value !== "object") fail("invalid-request", "JSON value is unsupported");
  const keys = Object.keys(value);
  if (keys.length > limits.maxObjectKeys) fail("invalid-request", "JSON object has too many fields");
  for (const key of keys) {
    if (["__proto__", "constructor", "prototype"].includes(key) || Buffer.byteLength(key, "utf8") > 256) {
      fail("invalid-request", "JSON object key is unsafe");
    }
    jsonDepthAndSize(value[key], limits, depth + 1);
  }
}

function requestToolNames(body, path, allowedTools) {
  if (body.tools === undefined) return [];
  if (!Array.isArray(body.tools) || body.tools.length > allowedTools.length) fail("tool-widening", "model request tools are invalid");
  const names = body.tools.map((tool) => {
    if (!tool || typeof tool !== "object" || Array.isArray(tool)) fail("tool-widening", "model request tool is invalid");
    if (path === "/responses") {
      if (tool.type !== "function" || typeof tool.name !== "string") fail("tool-widening", "Responses tool is invalid");
      return tool.name;
    }
    if (tool.type !== "function" || !tool.function || typeof tool.function.name !== "string") {
      fail("tool-widening", "Chat Completions tool is invalid");
    }
    return tool.function.name;
  });
  if (new Set(names).size !== names.length || names.some((name) => !allowedTools.includes(name))) {
    fail("tool-widening", "model request widened the leased tool surface");
  }
  return names;
}

function toolCatalogSha256(names) {
  return createHash("sha256").update([...names].sort().join("\n"), "utf8").digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256Text(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function decodeUtf8(bytes, code, message) {
  try { return UTF8.decode(bytes); } catch { fail(code, message); }
}

// A backend completion is either complete, incomplete, failed, or truncated.
// It is truncated (and therefore must never be disclosed as successful) when a
// chat finish_reason reports length or a Responses surface reports a
// token/length reason (max_output_tokens / max_tokens / token / length).  Any
// other Responses incomplete outcome is incomplete: also withheld from
// disclosure but classified distinctly.  A Responses terminal status of failed
// or cancelled is an explicitly non-completed outcome, withheld under its own
// code rather than treated as complete.  These are mechanical signals,
// independent of prompt, model, or reasoning effort.  Across events the
// outcome is merged with a fixed precedence truncated > failed > incomplete >
// complete so a later token/length reason is not lost to an earlier
// non-terminal signal.
const COMPLETION_OUTCOME_RANK = Object.freeze({ complete: 0, incomplete: 1, failed: 2, truncated: 3 });

function mergeCompletionOutcome(left, right) {
  return COMPLETION_OUTCOME_RANK[right] > COMPLETION_OUTCOME_RANK[left] ? right : left;
}

function completionOutcome(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "complete";
  const tokenReason = /max_output_tokens|max_tokens|token|length/iu;
  const response = value.response;
  const surfaces = response && typeof response === "object" ? [value, response] : [value];
  let sawIncomplete = false;
  let sawFailed = false;
  for (const surface of surfaces) {
    if (!surface || typeof surface !== "object" || Array.isArray(surface)) continue;
    const reason = typeof surface?.incomplete_details?.reason === "string" ? surface.incomplete_details.reason : "";
    if (tokenReason.test(reason)) return "truncated";
    if (surface.status === "incomplete") sawIncomplete = true;
    if (surface.status === "failed" || surface.status === "cancelled") sawFailed = true;
  }
  if (Array.isArray(value.choices)) {
    for (const choice of value.choices) {
      if (choice && typeof choice === "object" && choice.finish_reason === "length") return "truncated";
    }
  }
  if (value.finish_reason === "length") return "truncated";
  if (sawFailed) return "failed";
  return sawIncomplete ? "incomplete" : "complete";
}

function parseCompletion(bytes, contentType) {
  const text = decodeUtf8(bytes, "invalid-backend-response", "backend response is not UTF-8");
  let values = [];
  if (contentType === "application/json") {
    try { values = [JSON.parse(text)]; } catch { fail("invalid-backend-response", "backend returned malformed JSON"); }
  } else {
    for (const block of text.split(/\r?\n\r?\n/u)) {
      const data = block.split(/\r?\n/u).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (!data || data === "[DONE]") continue;
      try { values.push(JSON.parse(data)); } catch { fail("invalid-backend-response", "backend returned malformed SSE JSON"); }
    }
  }
  let inputTokens;
  let outputTokens;
  let outcome = "complete";
  for (const value of values) {
    const candidates = [value?.usage, value?.response?.usage];
    for (const usage of candidates) {
      if (!usage || typeof usage !== "object") continue;
      const input = usage.input_tokens ?? usage.prompt_tokens;
      const output = usage.output_tokens ?? usage.completion_tokens;
      if (Number.isSafeInteger(input) && input >= 0) inputTokens = input;
      if (Number.isSafeInteger(output) && output >= 0) outputTokens = output;
    }
    outcome = mergeCompletionOutcome(outcome, completionOutcome(value));
  }
  return { inputTokens, outputTokens, outcome };
}

async function readIncoming(request, maximum) {
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > maximum) fail("request-too-large", "model request exceeded its byte ceiling");
    chunks.push(chunk);
  }
  if (total < 2) fail("invalid-request", "model request body is empty");
  return Buffer.concat(chunks, total);
}

async function readBackend(response, maximum) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^(?:0|[1-9][0-9]*)$/.test(declared) || Number(declared) > maximum)) {
    fail("response-too-large", "backend response length is invalid");
  }
  if (response.headers.get("content-encoding")) fail("invalid-backend-response", "compressed backend responses are forbidden");
  const chunks = [];
  let total = 0;
  if (!response.body) fail("invalid-backend-response", "backend response has no body");
  for await (const chunk of response.body) {
    total += chunk.length;
    if (total > maximum) fail("response-too-large", "backend response exceeded its byte ceiling");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, total);
}

function contentType(value) {
  return (value ?? "").split(";", 1)[0].trim().toLowerCase();
}

function sendJson(response, status, value) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  response.writeHead(status, {
    "cache-control": "no-store", "content-type": "application/json", "content-length": String(bytes.length),
    "x-content-type-options": "nosniff", "connection": "close",
  });
  response.end(bytes);
}

function sendFailure(response, status, code) {
  if (response.headersSent || response.destroyed) return response.destroy();
  sendJson(response, status, { error: { code, message: "Pixel local-model boundary denied the request." } });
}

function publicReport(config, state) {
  return Object.freeze({
    schemaVersion: 1,
    jobId: config.jobId,
    claimId: config.claimId,
    planSha256: config.planSha256,
    qualificationId: config.qualification.qualificationId,
    qualificationReceiptSha256: config.qualification.receiptSha256,
    inferencePolicySha256: config.inferencePolicySha256,
    modelIdSha256: sha256Text(config.modelId),
    proxyConfigSha256: sha256Text(canonicalJson(config)),
    allowedToolsSha256: toolCatalogSha256(config.allowedTools),
    startedAt: state.startedAt,
    modelRequests: state.modelRequests,
    inputTokens: state.inputTokens,
    outputTokens: state.outputTokens,
    networkBytes: state.networkBytes,
    deniedRequests: state.deniedRequests,
    backendFailures: state.backendFailures,
    activeInference: state.activeInference,
    lastFailureCode: state.lastFailureCode,
    lastRequestToolCount: state.lastRequestToolCount,
    lastRequestToolsSha256: state.lastRequestToolsSha256,
    contentStored: false,
    credentialsExposed: false,
    arbitraryNetwork: false,
    externalEffects: false,
  });
}

function createValidatedModelProxy(config, options = {}) {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") fail("invalid-runtime", "fetch implementation is unavailable");
  const state = {
    startedAt: (options.now ?? new Date()).toISOString(),
    startedMs: (options.now ?? new Date()).getTime(),
    modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0,
    deniedRequests: 0, backendFailures: 0, activeInference: false, lastFailureCode: null,
    lastRequestToolCount: null, lastRequestToolsSha256: null,
  };
  let reportQueue = Promise.resolve();
  let reportFailure = null;

  function persistReport() {
    if (typeof options.reportWriter !== "function") return Promise.resolve();
    reportQueue = reportQueue.then(async () => {
      try {
        await options.reportWriter(config.receiptPath, publicReport(config, state));
        reportFailure = null;
      } catch (error) {
        reportFailure = error;
      }
    });
    return reportQueue;
  }

  async function deny(response, status, code) {
    state.deniedRequests += 1;
    state.lastFailureCode = code;
    await persistReport();
    sendFailure(response, status, code);
  }

  const server = createServer({
    maxHeaderSize: 8192,
    requestTimeout: Math.min(config.budgets.maxRequestSeconds * 1000, 300000),
    headersTimeout: 5000,
    keepAliveTimeout: 1000,
    connectionsCheckingInterval: 1000,
  }, async (request, response) => {
    response.shouldKeepAlive = false;
    try {
      const peer = normalizePeer(request.socket.remoteAddress);
      const parsed = new URL(request.url ?? "", "http://pixel-model.invalid");
      if (parsed.search || parsed.hash) return deny(response, 404, "route-denied");
      if (parsed.pathname === "/healthz") {
        if (peer !== "127.0.0.1") return deny(response, 403, "peer-denied");
        return sendJson(response, 200, { status: "ready", jobId: config.jobId, claimId: config.claimId });
      }
      if (reportFailure) return deny(response, 503, "receipt-unavailable");
      if (peer !== config.allowedClientIpv4) return deny(response, 403, "peer-denied");
      if (Date.now() - state.startedMs >= config.budgets.maxRuntimeSeconds * 1000) return deny(response, 410, "runtime-exhausted");
      for (const name of FORBIDDEN_HEADERS) if (request.headers[name] !== undefined) return deny(response, 400, "credential-header-denied");
      if (request.headers["content-encoding"] !== undefined || request.headers.expect !== undefined) return deny(response, 400, "request-encoding-denied");

      if (request.method === "GET" && DISCOVERY_PATHS.has(parsed.pathname)) {
        if (request.headers["content-length"] !== undefined || request.headers["transfer-encoding"] !== undefined) {
          return deny(response, 400, "discovery-body-denied");
        }
        if (parsed.pathname === "/models") {
          return sendJson(response, 200, {
            object: "list",
            data: [{
              id: config.modelId, object: "model", owned_by: "pixel-local",
              meta: { n_ctx: config.contextWindow, n_ctx_train: config.contextWindow },
              architecture: { input_modalities: config.supportsVision ? ["text", "image"] : ["text"] },
            }],
          });
        }
        return sendJson(response, 200, {
          n_ctx: config.contextWindow,
          modalities: { vision: config.supportsVision },
          default_generation_settings: { n_ctx: config.contextWindow, params: { max_tokens: -1, n_predict: -1 } },
        });
      }

      if (request.method !== "POST" || !INFERENCE_PATHS.has(parsed.pathname)) return deny(response, 404, "route-denied");
      if (state.activeInference) return deny(response, 409, "concurrent-inference-denied");
      if (state.modelRequests >= config.budgets.maxModelRequests) return deny(response, 429, "model-request-budget-exhausted");
      if (contentType(request.headers["content-type"]) !== "application/json") return deny(response, 415, "content-type-denied");
      const remainingNetwork = config.budgets.maxNetworkBytes - state.networkBytes;
      const requestLimit = Math.min(config.budgets.maxRequestBytes, remainingNetwork);
      if (requestLimit < 2) return deny(response, 429, "network-budget-exhausted");
      const bytes = await readIncoming(request, requestLimit);
      state.networkBytes += bytes.length;
      let body;
      try { body = JSON.parse(decodeUtf8(bytes, "invalid-json", "model request is not UTF-8")); } catch (error) {
        if (error instanceof WorkModelProxyError) return deny(response, 400, error.code);
        return deny(response, 400, "invalid-json");
      }
      if (!body || typeof body !== "object" || Array.isArray(body)) return deny(response, 400, "invalid-request");
      jsonDepthAndSize(body, { nodes: 0, maxDepth: 64, maxNodes: 100000, maxStringBytes: config.budgets.maxRequestBytes, maxArrayItems: 100000, maxObjectKeys: 10000 });
      if (body.model !== config.modelId || body.stream !== true) return deny(response, 400, "model-contract-denied");
      const requestedTools = requestToolNames(body, parsed.pathname, config.allowedTools);
      state.lastRequestToolCount = requestedTools.length;
      state.lastRequestToolsSha256 = toolCatalogSha256(requestedTools);
      if (body.background === true || body.store === true) return deny(response, 400, "retention-denied");
      const remainingInput = config.budgets.maxInputTokens - state.inputTokens;
      const remainingOutput = config.budgets.maxOutputTokens - state.outputTokens;
      if (bytes.length > remainingInput || remainingOutput < 1) return deny(response, 429, "token-budget-exhausted");
      const outputField = parsed.pathname === "/responses" ? "max_output_tokens" : "max_tokens";
      const requestedOutput = body[outputField] ?? remainingOutput;
      if (!Number.isSafeInteger(requestedOutput) || requestedOutput < 1) return deny(response, 400, "output-limit-invalid");
      let boundedBody = {
        ...body, model: config.modelId, stream: true, store: false,
        [outputField]: Math.min(requestedOutput, remainingOutput, config.qualification.maxOutputTokens),
      };
      delete boundedBody.background;
      if (config.provider === "vllm") {
        if (parsed.pathname !== "/v1/chat/completions") return deny(response, 404, "route-denied");
        boundedBody = normalizeVllmChatRequest(boundedBody, config.inference, boundedBody.max_tokens).body;
      }

      state.activeInference = true;
      state.modelRequests += 1;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), config.budgets.maxRequestSeconds * 1000);
      timeout.unref?.();
      try {
        const backend = await fetchImpl(`${config.backendOrigin}${parsed.pathname}`, {
          method: "POST",
          redirect: "error",
          signal: controller.signal,
          headers: { "content-type": "application/json", "accept": "text/event-stream", "accept-encoding": "identity" },
          body: JSON.stringify(boundedBody),
        });
        if (backend.status !== 200) fail(`backend-status-${backend.status}`, "backend rejected the bounded inference request");
        const type = contentType(backend.headers.get("content-type"));
        if (
          (config.provider === "vllm" && type !== "text/event-stream")
          || (config.provider !== "vllm" && !["application/json", "text/event-stream"].includes(type))
        ) fail("invalid-backend-response", "backend content type is unsupported");
        const responseLimit = Math.min(config.budgets.maxResponseBytes, config.budgets.maxNetworkBytes - state.networkBytes);
        if (responseLimit < 1) fail("network-budget-exhausted", "network budget exhausted before backend response");
        const result = await readBackend(backend, responseLimit);
        state.networkBytes += result.length;
        const completion = parseCompletion(result, type);
        if (completion.inputTokens === undefined || completion.outputTokens === undefined) fail("backend-usage-missing", "qualified backend omitted exact usage");
        const chargedInput = completion.inputTokens;
        const chargedOutput = completion.outputTokens;
        if (
          chargedInput > remainingInput
          || chargedInput > config.qualification.maxContextTokens
          || chargedOutput > remainingOutput
          || chargedOutput > boundedBody[outputField]
        ) fail("backend-usage-exceeded", "backend usage exceeded the lease");
        state.inputTokens += chargedInput;
        state.outputTokens += chargedOutput;
        state.activeInference = false;
        if (completion.outcome !== "complete") {
          const code = completion.outcome === "truncated"
            ? "backend-completion-truncated"
            : completion.outcome === "failed" ? "backend-completion-failed" : "backend-completion-incomplete";
          const message = completion.outcome === "truncated"
            ? "qualified backend truncated the completion"
            : completion.outcome === "failed"
              ? "qualified backend reported a failed completion"
              : "qualified backend left the completion incomplete";
          fail(code, message);
        }
        await persistReport();
        if (reportFailure) fail("receipt-unavailable", "model proxy receipt could not be persisted");
        response.writeHead(200, {
          "cache-control": "no-store", "content-type": type, "content-length": String(result.length),
          "x-content-type-options": "nosniff", "connection": "close",
        });
        response.end(result);
      } finally {
        clearTimeout(timeout);
        state.activeInference = false;
      }
    } catch (error) {
      const code = error instanceof InferencePolicyError
        ? error.code
        : error instanceof WorkModelProxyError ? error.code : "backend-failure";
      if (code.startsWith("backend") || code === "invalid-backend-response" || code === "response-too-large") state.backendFailures += 1;
      state.lastFailureCode = code;
      const clientStatus = {
        "invalid-request": 400,
        "request-too-large": 413,
        "tool-widening": 400,
        "inference-budget-insufficient": 400,
        "receipt-unavailable": 503,
      }[code];
      await deny(response, clientStatus ?? (code.endsWith("exhausted") ? 429 : 502), code);
    }
  });
  server.maxRequestsPerSocket = config.budgets.maxModelRequests + 8;
  server.on("upgrade", (request, socket) => socket.destroy());
  server.on("connect", (request, socket) => socket.destroy());
  server.on("clientError", (error, socket) => {
    if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  });

  return Object.freeze({
    config,
    server,
    report: () => publicReport(config, state),
    async listen() {
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(config.listenPort, config.listenHost, () => {
          server.off("error", reject);
          resolve();
        });
      });
      await persistReport();
      if (reportFailure) {
        await this.close();
        fail("receipt-unavailable", "model proxy receipt could not be initialized");
      }
      return server.address();
    },
    async close() {
      if (!server.listening) return;
      const closed = new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      server.closeAllConnections?.();
      await closed;
      await reportQueue;
    },
  });
}

export function createModelProxy(rawConfig, options = {}) {
  return createValidatedModelProxy(validateModelProxyConfig(rawConfig), options);
}

export async function writeModelProxyReport(path, report) {
  const directoryPath = dirname(path);
  const directory = await lstat(directoryPath).catch(() => null);
  if (!directory?.isDirectory() || directory.isSymbolicLink()) fail("invalid-receipt-directory", "model proxy receipt directory is invalid");
  if (process.platform !== "win32" && (directory.uid !== process.geteuid() || (directory.mode & 0o077) !== 0)) {
    fail("invalid-receipt-directory", "model proxy receipt directory must be owner-only");
  }
  const temporary = join(directoryPath, `.model-proxy-receipt-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(report, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(temporary, path);
    if (process.platform !== "win32") {
      const directoryHandle = await open(directoryPath, constants.O_RDONLY);
      try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}

export async function readModelProxyConfig(path) {
  const before = await lstat(path).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1) fail("invalid-config-file", "model proxy config must be a single-link regular file");
  if (process.platform !== "win32" && (before.uid !== process.geteuid() || (before.mode & 0o077) !== 0)) {
    fail("invalid-config-file", "model proxy config must be owner-only");
  }
  const { text, details } = await readBoundedRegularText(path, 65536, "model proxy config");
  if (
    details.dev !== before.dev
    || details.ino !== before.ino
    || details.size !== before.size
    || details.nlink !== 1
    || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))
  ) fail("invalid-config-file", "model proxy config changed during validation");
  let decoded;
  try { decoded = JSON.parse(text); } catch { fail("invalid-config-file", "model proxy config is not JSON"); }
  return validateModelProxyConfig(decoded);
}

export async function createModelProxyFromConfigFile(path, options = {}) {
  return createValidatedModelProxy(await readModelProxyConfig(path), options);
}

async function main() {
  if (process.argv.length !== 3) fail("usage", "Usage: node proxy.mjs CONFIG.json");
  const proxy = await createModelProxyFromConfigFile(process.argv[2], { reportWriter: writeModelProxyReport });
  const stop = async () => {
    await proxy.close();
    process.exitCode = 0;
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  await proxy.listen();
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-proxy: ${error instanceof WorkModelProxyError ? error.code : "startup-failed"}\n`);
    process.exitCode = 1;
  });
}

export const modelProxyContract = Object.freeze({
  inferencePaths: [...INFERENCE_PATHS], discoveryPaths: [...DISCOVERY_PATHS], tools: [...TOOL_NAMES],
  pixelPluginToolPattern: PIXEL_PLUGIN_TOOL_RE.source,
  maxCapabilityTools: MAX_CAPABILITY_TOOLS,
  maxPixelPluginTools: MAX_PIXEL_PLUGIN_TOOLS,
});

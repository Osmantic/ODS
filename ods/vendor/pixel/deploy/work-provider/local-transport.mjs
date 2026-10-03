import { request as httpRequest } from "node:http";

import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { bindWorkProviderLocalPolicy } from "./local-policy.mjs";
import { assertClaimedWorkProviderRequest } from "./run-ledger.mjs";
import { admitNeutralCorpusRequest } from "./neutral-corpus.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const LOOPBACK_HOST = "127.0.0.1";
const LOOPBACK_PORT = 8000;

export class LocalWorkProviderTransportError extends Error {
  constructor(message, outcome = "failed-known") {
    super(message);
    if (!["failed-known", "uncertain"].includes(outcome)) throw new TypeError("Local transport outcome is invalid");
    this.name = "LocalWorkProviderTransportError";
    this.outcome = outcome;
  }
}
function fail(message, outcome = "failed-known") { throw new LocalWorkProviderTransportError(message, outcome); }
function boundedAscii(value, label, maximum = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || /[^\x20-\x7e]/u.test(value)) fail(`${label} is invalid`);
  return value;
}

function assertResponseModel(parsed, expectedModel, bindingKind) {
  const returned = parsed?.providerModel;
  if (typeof returned === "string" && returned.length > 0) {
    if (returned !== expectedModel) fail("Local provider returned a model that differs from the pinned run model", "failed-known");
  } else if (bindingKind === "qualification-trial" || bindingKind === "semantic") {
    fail("Local provider response omitted the exact pinned model", "failed-known");
  }
}

function defaultLoopbackExchange({ host, port, path, body, timeoutMilliseconds, maximumResponseBytes }) {
  if (host !== LOOPBACK_HOST || port !== LOOPBACK_PORT || path !== "/v1/chat/completions") fail("Local transport escaped its loopback-only route");
  return new Promise((resolve, reject) => {
    let settled = false, requestCommitted = false;
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); if (error) { requestRef?.destroy(); reject(error); } else resolve(value); };
    const transportFailure = (message) => new LocalWorkProviderTransportError(message, requestCommitted ? "uncertain" : "failed-known");
    const timer = setTimeout(() => finish(transportFailure("Local loopback request timed out")), timeoutMilliseconds); timer.unref();
    const requestRef = httpRequest({ host, port, path, method: "POST", headers: { "Content-Type": "application/json", "Content-Length": body.length, "Accept": "application/json", "Connection": "close" }, agent: false });
    requestRef.once("error", () => finish(transportFailure("Local loopback connection failed")));
    const chunks = []; let received = 0;
    requestRef.on("response", (response) => {
      const headers = new Map();
      for (const [name, value] of Object.entries(response.headers)) headers.set(String(name).toLowerCase(), Array.isArray(value) ? value.join(",") : String(value));
      response.on("data", (chunk) => { received += chunk.length; if (received > maximumResponseBytes) finish(transportFailure("Local response exceeded its byte ceiling")); else chunks.push(Buffer.from(chunk)); });
      response.once("end", () => {
        try {
          const bodyValue = Buffer.concat(chunks, received);
          const encoding = headers.get("content-encoding");
          if (encoding && encoding.toLowerCase() !== "identity") finish(transportFailure("Local response content encoding is unsupported"));
          const contentType = headers.get("content-type")?.toLowerCase() ?? "";
          if (!contentType.startsWith("application/json")) finish(transportFailure("Local response is not JSON"));
          const contentLength = headers.get("content-length");
          if (contentLength !== undefined && (!/^(?:0|[1-9][0-9]{0,15})$/u.test(contentLength) || Number(contentLength) !== received)) finish(transportFailure("Local response content length is invalid"));
          finish(null, { statusCode: response.statusCode ?? 0, headers: Object.fromEntries(headers), body: bodyValue, networkBytes: body.length + received });
        } catch { finish(transportFailure("Local response parsing failed")); }
      });
      response.once("error", () => finish(transportFailure("Local response stream failed")));
    });
    requestRef.once("finish", () => { requestCommitted = true; });
    requestRef.write(body);
    requestRef.end();
  });
}

async function executeLocalChatTurnCore({ resolvedProvider, policy: rawPolicy, ledger, idempotencyKey, input, exchange }) {
  if (typeof exchange !== "function") fail("Local transport exchange is invalid");
  const policy = bindWorkProviderLocalPolicy(resolvedProvider, rawPolicy);
  if (resolvedProvider.profile.id !== "local" || resolvedProvider.profile.protocol !== "local-openai-compatible"
    || input?.model !== ledger.model || !SHA.test(idempotencyKey ?? "")) fail("Local turn differs from the exact provider, model, or claim");
  if (ledger.binding?.kind === "qualification-trial") {
    const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: ledger.model, input });
    if (!admitted) fail("Local qualification-trial input escaped its fixed neutral corpus");
  }
  const claim = assertClaimedWorkProviderRequest({ ledger, resolvedProvider, policy, idempotencyKey, input });
  if (input.maxOutputTokens !== claim.maxOutputTokens) fail("Local request output limit differs from its admitted claim");
  const providerRequest = resolvedProvider.adapter.buildRequest(input, { outputTokenField: "max_tokens" });
  const body = Buffer.from(JSON.stringify(providerRequest), "utf8");
  if (body.length > policy.budgets.maxNetworkBytesPerRun) fail("Local request exceeds its network budget");
  let response;
  try {
    response = await exchange({
      host: LOOPBACK_HOST, port: LOOPBACK_PORT, path: "/v1/chat/completions", body,
      timeoutMilliseconds: policy.budgets.maxRequestSeconds * 1000,
      maximumResponseBytes: policy.budgets.maxNetworkBytesPerRun - body.length,
    });
  } catch (error) {
    if (error instanceof LocalWorkProviderTransportError) throw error;
    throw new LocalWorkProviderTransportError("Local transport failed", "uncertain");
  }
  if (!response || !Number.isSafeInteger(response.statusCode) || !Buffer.isBuffer(response.body) || !Number.isSafeInteger(response.networkBytes)
    || response.networkBytes < body.length || response.networkBytes > policy.budgets.maxNetworkBytesPerRun) fail("Local response metadata is invalid");
  if (response.statusCode < 200 || response.statusCode > 299) fail(`Local provider returned HTTP ${response.statusCode}`, "failed-known");
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(response.body); } catch { fail("Local response body is not strict UTF-8"); }
  let providerResponse;
  try { providerResponse = parseStrictJson(text, "Local response body"); }
  catch (error) { fail(`Local response body is not strict JSON: ${error.message}`, "failed-known"); }
  let parsed;
  try { parsed = resolvedProvider.adapter.parseResponse(providerResponse); }
  catch (error) { fail(`Local response adapter validation failed: ${error.message}`, "failed-known"); }
  assertResponseModel(parsed, input.model, ledger.binding.kind);
  if (parsed.usage === null) fail("Local response omitted exact token usage");
  const providerRequestId = boundedAscii(response.headers?.["x-request-id"] ?? providerResponse.id, "Local request identifier");
  return Object.freeze({ assistant: parsed, providerRequestId, networkBytes: response.networkBytes });
}

// Production local transport executes over the single closed loopback exchange only. A caller
// cannot inject an exchange or test seam: any such request is rejected and no exchange runs.
export async function executeLocalChatTurn(options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) fail("Local transport options are invalid");
  if (Object.hasOwn(options, "exchange") || Object.hasOwn(options, "testSeam")) fail("production local transport cannot accept a caller-supplied exchange");
  return executeLocalChatTurnCore({ ...options, exchange: defaultLoopbackExchange });
}

// Test-only export. Release checks forbid production modules from importing this symbol. It
// exists solely so hermetic tests can inject a fake loopback exchange without live network use.
export const localProviderTransportTestOnly = Object.freeze({
  executeLocalChatTurnWithExchange(options, exchange) {
    if (!options || typeof options !== "object" || Array.isArray(options)) fail("Local transport options are invalid");
    if (typeof exchange !== "function") fail("test local exchange is invalid");
    return executeLocalChatTurnCore({ ...options, exchange });
  },
});

export const localTransportInternals = Object.freeze({ defaultLoopbackExchange, LOOPBACK_HOST, LOOPBACK_PORT });

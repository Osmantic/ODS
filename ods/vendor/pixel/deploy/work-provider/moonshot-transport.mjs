import { createHash } from "node:crypto";
import { request as httpRequest } from "node:http";
import { connect as tlsConnect } from "node:tls";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { readWorkProviderCredential } from "./credential-custody.mjs";
import { bindWorkProviderPrivatePolicy } from "./private-policy.mjs";
import { assertClaimedWorkProviderRequest } from "./run-ledger.mjs";
import { admitNeutralCorpusRequest } from "./neutral-corpus.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const PROXY_HOSTS = new Set(["127.0.0.1", "pixel-provider-egress"]);
const CONTENT_FREE_FAILURE_CODES = new Set(["proxy-connection", "proxy-refused", "response-framing", "response-json", "response-metadata", "response-size", "tls", "transport-timeout", "transport-unknown"]);
const SINGLETON_RESPONSE_HEADERS = new Set(["content-length", "transfer-encoding", "content-type", "content-encoding", "x-request-id"]);

function parserFailureCode(message) {
  if (/byte ceiling|oversized/iu.test(message)) return "response-size";
  if (/not JSON/iu.test(message)) return "response-json";
  if (/chunk|framing|content length|transfer encoding|final chunk|trailers/iu.test(message)) return "response-framing";
  if (/header|status line|content encoding|HTTP response/iu.test(message)) return "response-metadata";
  return "transport-unknown";
}

function assertK3ReplayComplete(input) {
  for (const message of input?.messages ?? []) {
    if (message?.role !== "assistant" || !message.toolCalls?.length) continue;
    const saved = message.providerState?.assistantMessage;
    if (!saved || typeof saved !== "object" || saved.role !== "assistant") throw new MoonshotWorkProviderTransportError("K3 tool-call replay provider state is incomplete", "failed-known");
    if (!Object.hasOwn(saved, "reasoning_content") || typeof saved.reasoning_content !== "string" || saved.reasoning_content.length === 0) throw new MoonshotWorkProviderTransportError("K3 reasoning_content replay is incomplete or truncated", "failed-known");
  }
}

function assertQualificationTrialInput({ ledger, resolvedProvider, input }) {
  if (resolvedProvider.profile.id !== "moonshot-kimi") throw new MoonshotWorkProviderTransportError("qualification-trial lane differs from the closed Moonshot provider", "failed-known");
  const admitted = admitNeutralCorpusRequest({ lane: "moonshot-kimi", model: resolvedProvider.profile.defaultModel, input });
  if (!admitted) throw new MoonshotWorkProviderTransportError("Moonshot qualification-trial input escaped its fixed neutral corpus", "failed-known");
  assertK3ReplayComplete(input);
  return admitted;
}

export class MoonshotWorkProviderTransportError extends Error {
  constructor(message, outcome = "failed-known", failureCode = null) {
    super(message);
    if (!["failed-known", "uncertain"].includes(outcome)) throw new TypeError("Moonshot transport outcome is invalid");
    if (failureCode !== null && !CONTENT_FREE_FAILURE_CODES.has(failureCode)) throw new TypeError("Moonshot transport failure code is invalid");
    this.name = "MoonshotWorkProviderTransportError";
    this.outcome = outcome;
    this.failureCode = failureCode;
  }
}
function fail(message, outcome = "failed-known") { throw new MoonshotWorkProviderTransportError(message, outcome); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function boundedAscii(value, label, maximum = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || /[^\x20-\x7e]/u.test(value)) fail(`${label} is invalid`);
  return value;
}

function assertResponseModel(parsed, expectedModel, bindingKind) {
  const returned = parsed?.providerModel;
  if (typeof returned === "string" && returned.length > 0) {
    if (returned !== expectedModel) fail("Moonshot provider returned a model that differs from the pinned run model", "failed-known");
  } else if (bindingKind === "qualification-trial" || bindingKind === "semantic") {
    fail("Moonshot provider response omitted the exact pinned model", "failed-known");
  }
}

function decodeChunked(body, maximum) {
  const chunks = []; let position = 0, total = 0;
  while (position < body.length) {
    const end = body.indexOf("\r\n", position, "ascii"); if (end < 0 || end - position > 16) fail("provider response has invalid chunk framing");
    const line = body.subarray(position, end).toString("ascii"); if (!/^[0-9A-Fa-f]+$/u.test(line)) fail("provider response has unsupported chunk extensions or size");
    const size = Number.parseInt(line, 16); if (!Number.isSafeInteger(size)) fail("provider response chunk size is invalid"); position = end + 2;
    if (size === 0) { if (!body.subarray(position).equals(Buffer.from("\r\n"))) fail("provider response has trailers or data after its final chunk"); return Buffer.concat(chunks, total); }
    if (position + size + 2 > body.length || body[position + size] !== 0x0d || body[position + size + 1] !== 0x0a) fail("provider response chunk is truncated");
    total += size; if (total > maximum) fail("provider response body exceeds its byte ceiling"); chunks.push(body.subarray(position, position + size)); position += size + 2;
  }
  fail("provider response is missing its final chunk");
}

export function parseMoonshotHttpResponse(bytes, maximumBodyBytes) {
  if (!Buffer.isBuffer(bytes) || !Number.isSafeInteger(maximumBodyBytes) || maximumBodyBytes < 1 || bytes.length > maximumBodyBytes + 64 * 1024) fail("provider HTTP response is invalid or oversized");
  const boundary = bytes.indexOf("\r\n\r\n"); if (boundary < 0 || boundary > 32 * 1024) fail("provider HTTP response headers are invalid");
  let headerText; try { headerText = new TextDecoder("ascii", { fatal: true }).decode(bytes.subarray(0, boundary)); } catch { fail("provider HTTP response headers are not ASCII"); }
  if (/[^\x20-\x7e\r\n]/u.test(headerText)) fail("provider HTTP response headers contain invalid bytes");
  const lines = headerText.split("\r\n"), status = /^HTTP\/1\.[01] ([0-9]{3}) [\x20-\x7e]*$/u.exec(lines.shift() ?? "");
  if (!status) fail("provider HTTP response status line is invalid");
  const headers = new Map();
  for (const line of lines) {
    const match = /^([A-Za-z0-9-]{1,64}):[ \t]*([\x20-\x7e]{0,8192})$/u.exec(line); if (!match) fail("provider HTTP response header is malformed");
    const name = match[1].toLowerCase();
    if (headers.has(name) && SINGLETON_RESPONSE_HEADERS.has(name)) fail("provider HTTP response contains a duplicate singleton header");
    if (!headers.has(name)) headers.set(name, match[2].trim());
  }
  const encoded = bytes.subarray(boundary + 4), transfer = headers.get("transfer-encoding"), length = headers.get("content-length");
  if (transfer && length) fail("provider HTTP response has conflicting body framing");
  let body;
  if (transfer) { if (transfer.toLowerCase() !== "chunked") fail("provider HTTP response transfer encoding is unsupported"); body = decodeChunked(encoded, maximumBodyBytes); }
  else if (length !== undefined) {
    if (!/^(?:0|[1-9][0-9]{0,15})$/u.test(length) || Number(length) !== encoded.length) fail("provider HTTP response content length is invalid"); body = Buffer.from(encoded);
  } else body = Buffer.from(encoded);
  if (body.length > maximumBodyBytes) fail("provider HTTP response body exceeds its byte ceiling");
  const encoding = headers.get("content-encoding"); if (encoding && encoding.toLowerCase() !== "identity") fail("provider HTTP response content encoding is unsupported");
  const contentType = headers.get("content-type")?.toLowerCase() ?? ""; if (!contentType.startsWith("application/json")) fail("provider HTTP response is not JSON");
  return Object.freeze({ statusCode: Number(status[1]), headers: Object.freeze(Object.fromEntries(headers)), body });
}

function defaultProxyExchange({ host, port, path, body, credential, timeoutMilliseconds, maximumResponseBytes, proxyHost = "pixel-provider-egress", proxyPort = 3128 }) {
  if (!PROXY_HOSTS.has(proxyHost) || proxyPort !== 3128 || host !== "api.moonshot.ai" || port !== 443 || path !== "/v1/chat/completions") fail("Moonshot transport escaped its fixed proxy route");
  return new Promise((resolve, reject) => {
    let settled = false, requestCommitted = false, connectRequest = null, tunnel = null, secure = null, requestBytes = null;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer); requestBytes?.fill(0); requestBytes = null; connectRequest?.destroy(); if (error) { tunnel?.destroy(); secure?.destroy(); reject(error); } else resolve(value);
    };
    const transportFailure = (message, failureCode) => new MoonshotWorkProviderTransportError(message, requestCommitted ? "uncertain" : "failed-known", failureCode);
    const timer = setTimeout(() => finish(transportFailure("Moonshot proxy request timed out", "transport-timeout")), timeoutMilliseconds); timer.unref();
    connectRequest = httpRequest({ host: proxyHost, port: proxyPort, method: "CONNECT", path: `${host}:${port}`, headers: { Host: `${host}:${port}`, Connection: "close" }, agent: false });
    connectRequest.once("error", () => finish(transportFailure("Moonshot proxy connection failed", "proxy-connection")));
    connectRequest.once("connect", (response, socket, head) => {
      tunnel = socket;
      if (response.statusCode !== 200 || head.length !== 0) { finish(transportFailure("Moonshot proxy refused the exact provider tunnel", "proxy-refused")); return; }
      secure = tlsConnect({ socket, servername: host, rejectUnauthorized: true, minVersion: "TLSv1.2" }); const chunks = []; let received = 0;
      secure.once("error", () => finish(transportFailure("Moonshot TLS transport failed", "tls")));
      secure.once("secureConnect", () => {
        const prefix = Buffer.from(`POST ${path} HTTP/1.1\r\nHost: ${host}\r\nContent-Type: application/json\r\nAccept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\nAuthorization: Bearer `, "ascii");
        const suffix = Buffer.from(`\r\nContent-Length: ${body.length}\r\n\r\n`, "ascii"); requestBytes = Buffer.concat([prefix, credential, suffix, body]);
        requestCommitted = true;
        secure.write(requestBytes, () => { requestBytes?.fill(0); requestBytes = null; });
      });
      secure.on("data", (chunk) => { received += chunk.length; if (received > maximumResponseBytes + 64 * 1024) finish(transportFailure("Moonshot response exceeded its byte ceiling", "response-size")); else chunks.push(Buffer.from(chunk)); });
      secure.once("end", () => {
        try { finish(null, { ...parseMoonshotHttpResponse(Buffer.concat(chunks, received), maximumResponseBytes), networkBytes: body.length + received }); }
        catch (error) {
          const message = error instanceof MoonshotWorkProviderTransportError ? error.message : "Moonshot response parsing failed";
          const failureCode = CONTENT_FREE_FAILURE_CODES.has(error?.failureCode) ? error.failureCode : parserFailureCode(message);
          finish(new MoonshotWorkProviderTransportError(message, "uncertain", failureCode));
        }
      });
    });
    connectRequest.end();
  });
}

async function executeMoonshotChatTurnCore({ resolvedProvider, policy: rawPolicy, credentialHandle, ledger, idempotencyKey, input, exchange, proxyHost, proxyPort }) {
  if (typeof exchange !== "function") fail("Moonshot transport exchange is invalid");
  const policy = bindWorkProviderPrivatePolicy(resolvedProvider, rawPolicy, { requireEnabled: true });
  if (resolvedProvider.profile.id !== "moonshot-kimi" || resolvedProvider.profile.protocol !== "openai-chat-completions" || input?.model !== resolvedProvider.profile.defaultModel || !SHA.test(idempotencyKey ?? "")) fail("Moonshot turn differs from the exact provider, model, or claim");
  if (input.temperature !== undefined || !["low", "high", "max"].includes(input.reasoningEffort)) fail("Kimi K3 requires an explicit supported reasoning effort and omitted fixed temperature");
  if (ledger.binding?.kind === "connectivity-smoke") {
    const expected = {
      schemaVersion: 1, model: resolvedProvider.profile.defaultModel,
      messages: [{ role: "user", content: "Return exactly PIXEL_K3_SMOKE_OK and nothing else." }],
      maxOutputTokens: 256, reasoningEffort: "low",
    };
    if (ledger.binding.purpose !== "moonshot-connectivity-smoke-v1" || ledger.binding.requestSha256 !== sha(expected)
      || canonical(input) !== canonical(expected)) fail("Moonshot connectivity-smoke input escaped its fixed non-semantic probe");
  } else if (ledger.binding?.kind === "qualification-trial") {
    assertQualificationTrialInput({ ledger, resolvedProvider, input });
  }
  const claim = assertClaimedWorkProviderRequest({ ledger, resolvedProvider, policy, idempotencyKey, input });
  if (input.maxOutputTokens !== claim.maxOutputTokens) fail("Moonshot request output limit differs from its admitted claim");
  const providerRequest = resolvedProvider.adapter.buildRequest(input, { outputTokenField: "max_completion_tokens" });
  const body = Buffer.from(JSON.stringify(providerRequest), "utf8");
  if (body.length > policy.budgets.maxNetworkBytesPerRun) fail("Moonshot request exceeds its network budget");
  const credential = await readWorkProviderCredential({ resolvedProvider, policy, handle: credentialHandle });
  let response;
  try {
    response = await exchange({
      host: "api.moonshot.ai", port: 443, path: "/v1/chat/completions", body, credential,
      timeoutMilliseconds: policy.budgets.maxRequestSeconds * 1000,
      maximumResponseBytes: policy.budgets.maxNetworkBytesPerRun - body.length,
      ...(proxyHost === undefined ? {} : { proxyHost }), ...(proxyPort === undefined ? {} : { proxyPort }),
    });
  } finally { credential.fill(0); }
  if (!response || !Number.isSafeInteger(response.statusCode) || !Buffer.isBuffer(response.body) || !Number.isSafeInteger(response.networkBytes) || response.networkBytes < body.length || response.networkBytes > policy.budgets.maxNetworkBytesPerRun) fail("Moonshot proxy response metadata is invalid");
  if (response.statusCode < 200 || response.statusCode > 299) fail(`Moonshot provider returned HTTP ${response.statusCode}`, "failed-known");
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(response.body); } catch { fail("Moonshot response body is not strict UTF-8", "failed-known"); }
  let providerResponse;
  try { providerResponse = parseStrictJson(text, "Moonshot response body"); }
  catch (error) { fail(`Moonshot response body is not strict JSON: ${error.message}`, "failed-known"); }
  let parsed;
  try { parsed = resolvedProvider.adapter.parseResponse(providerResponse); }
  catch (error) { fail(`Moonshot response adapter validation failed: ${error.message}`, "failed-known"); }
  assertResponseModel(parsed, input.model, ledger.binding.kind);
  if (parsed.usage === null) fail("Moonshot response omitted exact token usage");
  const providerRequestId = boundedAscii(response.headers?.["x-request-id"] ?? providerResponse.id, "Moonshot request identifier");
  return Object.freeze({ assistant: parsed, providerRequestId, networkBytes: response.networkBytes });
}

// Production Moonshot transport executes over the single closed proxy exchange only. A caller
// cannot inject an exchange or test seam: any such request is rejected and no exchange runs.
export async function executeMoonshotChatTurn(options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) fail("Moonshot transport options are invalid");
  if (Object.hasOwn(options, "exchange") || Object.hasOwn(options, "testSeam")) fail("production Moonshot transport cannot accept a caller-supplied exchange");
  return executeMoonshotChatTurnCore({ ...options, exchange: defaultProxyExchange });
}

// Test-only export. Release checks forbid production modules from importing this symbol. It
// exists solely so hermetic transport tests can inject a fake exchange without live network use.
export const moonshotTransportTestOnly = Object.freeze({
  executeMoonshotChatTurnWithExchange(options, exchange) {
    if (typeof exchange !== "function") fail("test Moonshot exchange is invalid");
    return executeMoonshotChatTurnCore({ ...options, exchange });
  },
});

export const moonshotTransportInternals = Object.freeze({ defaultProxyExchange, assertQualificationTrialInput, assertK3ReplayComplete, parserFailureCode });

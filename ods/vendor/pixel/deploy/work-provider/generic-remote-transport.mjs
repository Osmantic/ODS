import { createHash } from "node:crypto";
import { request as httpRequest } from "node:http";
import { connect as tlsConnect } from "node:tls";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { readWorkProviderCredential } from "./credential-custody.mjs";
import { bindWorkProviderPrivatePolicy } from "./private-policy.mjs";
import { resolveProviderModel } from "./provider-registry.mjs";
import { assertClaimedWorkProviderRequest } from "./run-ledger.mjs";
import { admitNeutralCorpusRequest } from "./neutral-corpus.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const PROXY_HOSTS = new Set(["127.0.0.1", "pixel-provider-egress"]);
const SMOKE_OK = "PIXEL_REMOTE_SMOKE_OK";
const CONTENT_FREE_FAILURE_CODES = new Set(["proxy-connection", "proxy-refused", "response-framing", "response-json", "response-metadata", "response-size", "tls", "transport-timeout", "transport-unknown"]);
const SINGLETON_RESPONSE_HEADERS = new Set(["content-length", "transfer-encoding", "content-type", "content-encoding", "x-request-id"]);

function parserFailureCode(message) {
  if (/byte ceiling|oversized/iu.test(message)) return "response-size";
  if (/not JSON/iu.test(message)) return "response-json";
  if (/chunk|framing|content length|transfer encoding|final chunk|trailers/iu.test(message)) return "response-framing";
  if (/header|status line|content encoding|HTTP response/iu.test(message)) return "response-metadata";
  return "transport-unknown";
}

// Closed static provider-id -> wire mapping. There is no caller-controlled URL, path, or
// header anywhere in production execution: every remote provider is bound to exactly these
// host, port, path, auth header, and fixed header values.
export const REMOTE_WIRE_MAPPINGS = Object.freeze({
  "moonshot-kimi": Object.freeze({
    host: "api.moonshot.ai", port: 443, path: "/v1/chat/completions", protocol: "openai-chat-completions",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_completion_tokens",
  }),
  openai: Object.freeze({
    host: "api.openai.com", port: 443, path: "/v1/responses", protocol: "openai-responses",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_output_tokens",
  }),
  anthropic: Object.freeze({
    host: "api.anthropic.com", port: 443, path: "/v1/messages", protocol: "anthropic-messages",
    authHeader: "x-api-key", authPrefix: "", outputTokenField: "max_tokens",
    fixedHeaders: Object.freeze({ "anthropic-version": "2023-06-01" }),
  }),
  openrouter: Object.freeze({
    host: "openrouter.ai", port: 443, path: "/api/v1/chat/completions", protocol: "openai-chat-completions",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_tokens",
  }),
  together: Object.freeze({
    host: "api.together.xyz", port: 443, path: "/v1/chat/completions", protocol: "openai-chat-completions",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_tokens",
  }),
  fireworks: Object.freeze({
    host: "api.fireworks.ai", port: 443, path: "/inference/v1/chat/completions", protocol: "openai-chat-completions",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_tokens",
  }),
  groq: Object.freeze({
    host: "api.groq.com", port: 443, path: "/openai/v1/chat/completions", protocol: "openai-chat-completions",
    authHeader: "authorization", authPrefix: "Bearer ", outputTokenField: "max_tokens",
  }),
});

export class RemoteWorkProviderTransportError extends Error {
  constructor(message, outcome = "failed-known", failureCode = null) {
    super(message);
    if (!["failed-known", "uncertain"].includes(outcome)) throw new TypeError("Remote transport outcome is invalid");
    if (failureCode !== null && !CONTENT_FREE_FAILURE_CODES.has(failureCode)) throw new TypeError("Remote transport failure code is invalid");
    this.name = "RemoteWorkProviderTransportError";
    this.outcome = outcome;
    this.failureCode = failureCode;
  }
}
function fail(message, outcome = "failed-known") { throw new RemoteWorkProviderTransportError(message, outcome); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function boundedAscii(value, label, maximum = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || /[^\x20-\x7e]/u.test(value)) fail(`${label} is invalid`);
  return value;
}

function assertResponseModel(parsed, expectedModel, bindingKind) {
  const returned = parsed?.providerModel;
  if (typeof returned === "string" && returned.length > 0) {
    if (returned !== expectedModel) fail("provider returned a model that differs from the pinned run model", "failed-known");
  } else if (bindingKind === "qualification-trial" || bindingKind === "semantic") {
    fail("provider response omitted the exact pinned model", "failed-known");
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

export function parseRemoteHttpResponse(bytes, maximumBodyBytes) {
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
  if (!transfer && length === undefined) fail("provider HTTP response has neither Content-Length nor Transfer-Encoding");
  let body;
  if (transfer) {
    if (transfer.toLowerCase() !== "chunked") fail("provider HTTP response transfer encoding is unsupported");
    body = decodeChunked(encoded, maximumBodyBytes);
  } else {
    if (!/^(?:0|[1-9][0-9]{0,15})$/u.test(length) || Number(length) !== encoded.length) fail("provider HTTP response content length is invalid");
    body = Buffer.from(encoded);
  }
  if (body.length > maximumBodyBytes) fail("provider HTTP response body exceeds its byte ceiling");
  const encoding = headers.get("content-encoding"); if (encoding && encoding.toLowerCase() !== "identity") fail("provider HTTP response content encoding is unsupported");
  const contentType = headers.get("content-type")?.toLowerCase() ?? ""; if (!contentType.startsWith("application/json")) fail("provider HTTP response is not JSON");
  return Object.freeze({ statusCode: Number(status[1]), headers: Object.freeze(Object.fromEntries(headers)), body });
}

// Builds the exact one-shot HTTP request bytes (fixed headers, exact auth header, and one
// Content-Length) that defaultProxyExchange writes over the pinned TLS tunnel. Exposed only
// for hermetic wire tests and internal reuse; production still performs no caller-controlled
// URL, path, or header construction.
export function buildRemoteRequest({ wire, host, path, credential, body }) {
  const mapping = REMOTE_WIRE_MAPPINGS[wire];
  if (!mapping || mapping.host !== host || mapping.path !== path) fail("remote wire mapping cannot construct an off-mapping request", "failed-known");
  const lines = [`POST ${path} HTTP/1.1`, `Host: ${host}`, "Content-Type: application/json", "Accept: application/json", "Accept-Encoding: identity", "Connection: close"];
  const fixed = mapping.fixedHeaders ?? {};
  for (const [name, value] of Object.entries(fixed)) lines.push(`${name}: ${value}`);
  const authValue = `${mapping.authPrefix}${credential.toString("ascii")}`;
  lines.push(`${mapping.authHeader}: ${authValue}`);
  lines.push(`Content-Length: ${body.length}`);
  return Buffer.concat([Buffer.from(`${lines.join("\r\n")}\r\n\r\n`, "ascii"), body]);
}

// Proven proxy-only CONNECT/TLS framing extracted from the Moonshot transport and reused by
// every closed remote provider. It opens exactly one CONNECT tunnel to the fixed proxy, pins
// the TLS server name to the exact static provider host, and issues one Content-Length-bounded
// POST over the tunnel. A failure before the request is committed is failed-known; a failure
// after commit is uncertain (the provider may have received the request).
export function defaultProxyExchange({ host, port, path, body, credential, wire, timeoutMilliseconds, maximumResponseBytes, proxyHost = "pixel-provider-egress", proxyPort = 3128 }) {
  const mapping = REMOTE_WIRE_MAPPINGS[wire] ?? REMOTE_WIRE_MAPPINGS[Object.keys(REMOTE_WIRE_MAPPINGS).find((id) => REMOTE_WIRE_MAPPINGS[id].host === host)];
  if (!mapping || mapping.host !== host || mapping.port !== port || mapping.path !== path || !PROXY_HOSTS.has(proxyHost) || proxyPort !== 3128) fail("remote transport escaped its fixed proxy route");
  return new Promise((resolve, reject) => {
    let settled = false, requestCommitted = false, connectRequest = null, tunnel = null, secure = null, requestBytes = null;
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer); requestBytes?.fill(0); requestBytes = null; connectRequest?.destroy(); if (error) { tunnel?.destroy(); secure?.destroy(); reject(error); } else resolve(value);
    };
    const transportFailure = (message, failureCode) => new RemoteWorkProviderTransportError(message, requestCommitted ? "uncertain" : "failed-known", failureCode);
    const timer = setTimeout(() => finish(transportFailure("remote proxy request timed out", "transport-timeout")), timeoutMilliseconds); timer.unref();
    connectRequest = httpRequest({ host: proxyHost, port: proxyPort, method: "CONNECT", path: `${host}:${port}`, headers: { Host: `${host}:${port}`, Connection: "close" }, agent: false });
    connectRequest.once("error", () => finish(transportFailure("remote proxy connection failed", "proxy-connection")));
    connectRequest.once("connect", (response, socket, head) => {
      tunnel = socket;
      if (response.statusCode !== 200 || head.length !== 0) { finish(transportFailure("remote proxy refused the exact provider tunnel", "proxy-refused")); return; }
      secure = tlsConnect({ socket, servername: host, rejectUnauthorized: true, minVersion: "TLSv1.2" }); const chunks = []; let received = 0;
      secure.once("error", () => finish(transportFailure("remote TLS transport failed", "tls")));
      secure.once("secureConnect", () => {
        requestBytes = buildRemoteRequest({ wire, host, path, credential, body });
        requestCommitted = true;
        secure.write(requestBytes, () => { requestBytes?.fill(0); requestBytes = null; });
      });
      secure.on("data", (chunk) => { received += chunk.length; if (received > maximumResponseBytes + 64 * 1024) finish(transportFailure("remote response exceeded its byte ceiling", "response-size")); else chunks.push(Buffer.from(chunk)); });
      secure.once("end", () => {
        try { finish(null, { ...parseRemoteHttpResponse(Buffer.concat(chunks, received), maximumResponseBytes), networkBytes: body.length + received }); }
        catch (error) {
          const message = error instanceof RemoteWorkProviderTransportError ? error.message : "remote response parsing failed";
          const failureCode = CONTENT_FREE_FAILURE_CODES.has(error?.failureCode) ? error.failureCode : parserFailureCode(message);
          finish(new RemoteWorkProviderTransportError(message, "uncertain", failureCode));
        }
      });
    });
    connectRequest.end();
  });
}

function smokeProbe(wire, model) {
  // Connectivity smoke proves only the closed transport path. It intentionally
  // omits provider-specific reasoning controls so a small non-semantic probe is
  // valid for both manual-thinking and adaptive/default-thinking providers.
  return { schemaVersion: 1, model, messages: [{ role: "user", content: `Return exactly ${SMOKE_OK} and nothing else.` }], maxOutputTokens: 256 };
}

async function executeRemoteProviderTurnCore({ resolvedProvider, policy: rawPolicy, credentialHandle, ledger, idempotencyKey, input, exchange, proxyHost, proxyPort }) {
  if (typeof exchange !== "function") fail("remote transport exchange is invalid", "failed-known");
  const policy = bindWorkProviderPrivatePolicy(resolvedProvider, rawPolicy, { requireEnabled: true });
  const wire = REMOTE_WIRE_MAPPINGS[resolvedProvider.profile.id];
  if (!wire || wire.protocol !== resolvedProvider.profile.protocol) fail("provider has no closed remote wire mapping", "failed-known");
  const runModel = resolveProviderModel({ resolvedProvider, privatePolicy: policy, model: ledger.model });
  if (input?.model !== runModel || !SHA.test(idempotencyKey ?? "")) fail("provider turn differs from the exact provider, model, or claim", "failed-known");
  if (ledger.binding?.kind === "connectivity-smoke") {
    const expected = smokeProbe(wire, runModel);
    if (ledger.binding.purpose !== `${resolvedProvider.profile.id}-connectivity-smoke-v1` || ledger.binding.requestSha256 !== sha(expected) || canonical(input) !== canonical(expected)) fail("provider connectivity-smoke input escaped its fixed non-semantic probe", "failed-known");
  } else if (ledger.binding?.kind === "qualification-trial") {
    const admitted = admitNeutralCorpusRequest({ lane: resolvedProvider.profile.id, model: runModel, input });
    if (!admitted) fail("qualification-trial input escaped its fixed neutral corpus", "failed-known");
  }
  const claim = assertClaimedWorkProviderRequest({ ledger, resolvedProvider, policy, idempotencyKey, input });
  if (input.maxOutputTokens !== claim.maxOutputTokens) fail("provider request output limit differs from its admitted claim", "failed-known");
  const providerRequest = resolvedProvider.adapter.buildRequest(input, { outputTokenField: wire.outputTokenField });
  const body = Buffer.from(JSON.stringify(providerRequest), "utf8");
  if (body.length > policy.budgets.maxNetworkBytesPerRun) fail("provider request exceeds its network budget", "failed-known");
  const credential = await readWorkProviderCredential({ resolvedProvider, policy, handle: credentialHandle });
  let response;
  try {
    response = await exchange({
      host: wire.host, port: wire.port, path: wire.path, body, credential,
      wire: resolvedProvider.profile.id,
      timeoutMilliseconds: policy.budgets.maxRequestSeconds * 1000,
      maximumResponseBytes: policy.budgets.maxNetworkBytesPerRun - body.length,
      ...(proxyHost === undefined ? {} : { proxyHost }), ...(proxyPort === undefined ? {} : { proxyPort }),
    });
  } finally { credential.fill(0); }
  if (!response || !Number.isSafeInteger(response.statusCode) || !Buffer.isBuffer(response.body) || !Number.isSafeInteger(response.networkBytes) || response.networkBytes < body.length || response.networkBytes > policy.budgets.maxNetworkBytesPerRun) fail("provider proxy response metadata is invalid", "uncertain");
  if (response.statusCode < 200 || response.statusCode > 299) fail(`provider returned HTTP ${response.statusCode}`, "failed-known");
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(response.body); } catch { fail("provider response body is not strict UTF-8", "failed-known"); }
  let providerResponse;
  try { providerResponse = parseStrictJson(text, "provider response body"); }
  catch (error) { fail(`provider response body is not strict JSON: ${error.message}`, "failed-known"); }
  let parsed;
  try { parsed = resolvedProvider.adapter.parseResponse(providerResponse); }
  catch (error) { fail(`provider response adapter validation failed: ${error.message}`, "failed-known"); }
  assertResponseModel(parsed, runModel, ledger.binding.kind);
  if (parsed.usage === null) fail("provider response omitted exact token usage", "failed-known");
  if (ledger.binding?.kind === "connectivity-smoke"
    && (parsed.message?.content !== SMOKE_OK || Object.hasOwn(parsed.message ?? {}, "toolCalls"))) {
    fail("provider connectivity-smoke response did not return the exact sentinel", "failed-known");
  }
  const providerRequestId = boundedAscii(response.headers?.["x-request-id"] ?? providerResponse.id, "provider request identifier");
  return Object.freeze({ assistant: parsed, providerRequestId, networkBytes: response.networkBytes });
}

// Production remote transport executes over the single closed proxy exchange only. A caller
// cannot inject an exchange or test seam: any such request is rejected and no exchange runs.
export async function executeRemoteProviderTurn(options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) fail("remote transport options are invalid");
  if (Object.hasOwn(options, "exchange") || Object.hasOwn(options, "testSeam")) fail("production remote transport cannot accept a caller-supplied exchange");
  return executeRemoteProviderTurnCore({ ...options, exchange: defaultProxyExchange });
}

// Test-only export. Release checks forbid production modules from importing this symbol. It
// exists solely so hermetic transport tests can inject a fake exchange without live network use.
export const remoteProviderTransportTestOnly = Object.freeze({
  executeRemoteProviderTurnWithExchange(options, exchange) {
    if (!options || typeof options !== "object" || Array.isArray(options)) fail("remote transport options are invalid");
    if (typeof exchange !== "function") fail("test remote exchange is invalid");
    return executeRemoteProviderTurnCore({ ...options, exchange });
  },
});

export const remoteTransportInternals = Object.freeze({ defaultProxyExchange, smokeProbe, buildRemoteRequest, parserFailureCode, SMOKE_OK });

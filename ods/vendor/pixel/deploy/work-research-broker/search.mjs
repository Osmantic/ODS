import { createHash, randomBytes } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { createResearchBatch, validateResearchQueryAgainstLease } from "./broker.mjs";
import { completeResearchQuery, failReservedResearchQuery, reserveResearchQuery } from "./ledger.mjs";

const MAX_BACKEND_RESPONSE_BYTES = 16 * 1024 * 1024;
const NORMALIZED_KEYS = Object.freeze(["results", "schemaVersion"]);
const NORMALIZED_RESULT_KEYS = Object.freeze(["snippet", "sourceType", "title", "url"]);
const SOURCE_TYPE_CATEGORIES = Object.freeze({ web: "general", news: "news", academic: "science", forum: "social media" });

export class ResearchSearchError extends Error {
  constructor(message, { code = "research-search-failed", knownUsage = null, cause } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.code = code;
    this.knownUsage = knownUsage;
  }
}

function fail(message, options) {
  throw new ResearchSearchError(message, options);
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) {
    fail(`${label} shape is invalid`, { code: "backend-contract" });
  }
}

function boundedText(value, maximumBytes, label) {
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > maximumBytes) fail(`${label} is invalid or oversized`, { code: "backend-contract" });
  return value;
}

export function validateLocalResearchEndpoint(value) {
  let parsed;
  try { parsed = new URL(value); } catch { fail("research backend endpoint is invalid", { code: "endpoint-policy" }); }
  if (
    parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || parsed.username || parsed.password
    || !parsed.port || Number(parsed.port) < 1024 || Number(parsed.port) > 65535
    || !["", "/"].includes(parsed.pathname) || parsed.search || parsed.hash
  ) fail("research backend must be an uncredentialed IPv4 loopback origin on a non-privileged port", { code: "endpoint-policy" });
  return `http://127.0.0.1:${parsed.port}`;
}

function requestFor(query, adapter, endpoint) {
  if (adapter === "reference") {
    const body = new URLSearchParams();
    body.set("q", query.query);
    body.set("format", "json");
    body.set("safesearch", "2");
    body.set("categories", query.sourceTypes.map((type) => SOURCE_TYPE_CATEGORIES[type]).join(","));
    return {
      url: `${endpoint}/search`,
      body: body.toString(),
      contentType: "application/x-www-form-urlencoded;charset=UTF-8",
    };
  }
  return {
    url: `${endpoint}/v1/search`,
    body: JSON.stringify({
      schemaVersion: 1, adapter, query: query.query, sourceTypes: query.sourceTypes,
      domains: query.domains, maxResults: query.maxResults, safeSearch: query.safeSearch,
    }),
    contentType: "application/json",
  };
}

async function readBoundedJsonResponse(response, maximumBytes, requestBytes) {
  if (!response || typeof response.status !== "number" || !response.headers || !response.body?.getReader) {
    fail("research backend returned no streaming HTTP response", { code: "backend-transport" });
  }
  const known = (responseBytes) => ({
    searchRequests: 1, retrievalRequests: 0, sources: 0,
    networkBytes: requestBytes + responseBytes, sourceBytes: 0, rejectedSources: 0,
  });
  const lengthText = response.headers.get("content-length");
  if (lengthText !== null && (!/^(?:0|[1-9][0-9]{0,15})$/u.test(lengthText) || Number(lengthText) > maximumBytes)) {
    fail("research backend response exceeds its byte ceiling", { code: "backend-size" });
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) fail("research backend returned a malformed response stream", { code: "backend-transport" });
      total += value.byteLength;
      if (total > maximumBytes) {
        await reader.cancel().catch(() => {});
        fail("research backend response exceeds its byte ceiling", { code: "backend-size" });
      }
      chunks.push(value);
    }
  } catch (error) {
    if (error instanceof ResearchSearchError) throw error;
    fail("research backend response was interrupted", { code: "backend-transport", cause: error });
  }
  const usage = known(total);
  if (response.status !== 200) fail("research backend refused the bounded search request", { code: "backend-status", knownUsage: usage });
  const mediaType = (response.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/json") fail("research backend response is not JSON", { code: "backend-media", knownUsage: usage });
  const bytes = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), total);
  let value;
  try { value = JSON.parse(bytes.toString("utf8")); } catch (error) {
    fail("research backend response is malformed JSON", { code: "backend-json", knownUsage: usage, cause: error });
  }
  return { value, usage };
}

function referenceSourceType(result) {
  const labels = [result.category, ...(Array.isArray(result.categories) ? result.categories : []), result.engine]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLowerCase();
  if (/\bnews\b/u.test(labels)) return "news";
  if (/\b(?:academic|science|scientific)\b/u.test(labels)) return "academic";
  if (/\b(?:forum|social)\b/u.test(labels)) return "forum";
  return "web";
}

function parseReference(value, query) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !Array.isArray(value.results)) fail("SearXNG response shape is invalid", { code: "backend-contract" });
  if (value.results.length > query.maxResults * 4) fail("SearXNG returned too many results", { code: "backend-contract" });
  return value.results.map((result) => {
    if (!result || typeof result !== "object" || Array.isArray(result)) fail("SearXNG result shape is invalid", { code: "backend-contract" });
    return {
      url: boundedText(result.url, 4096, "SearXNG result URL"),
      title: boundedText(result.title ?? "", 4096, "SearXNG result title"),
      snippet: boundedText(result.content ?? "", 16384, "SearXNG result snippet"),
      sourceType: referenceSourceType(result),
    };
  });
}

function parseNormalized(value, query) {
  exactKeys(value, NORMALIZED_KEYS, "normalized research response");
  if (value.schemaVersion !== 1 || !Array.isArray(value.results) || value.results.length > query.maxResults * 4) {
    fail("normalized research response is invalid", { code: "backend-contract" });
  }
  return value.results.map((result) => {
    exactKeys(result, NORMALIZED_RESULT_KEYS, "normalized research result");
    if (!["web", "news", "academic", "forum"].includes(result.sourceType)) fail("normalized research source type is invalid", { code: "backend-contract" });
    return {
      url: boundedText(result.url, 4096, "normalized result URL"),
      title: boundedText(result.title, 4096, "normalized result title"),
      snippet: boundedText(result.snippet, 16384, "normalized result snippet"),
      sourceType: result.sourceType,
    };
  });
}

export async function searchPublicSources({
  query, plan, lease, claim, endpoint, maximumNetworkBytes = plan?.budgets?.maxNetworkBytes,
  timeoutMilliseconds = 30000, fetchImpl = globalThis.fetch,
}) {
  validateResearchQueryAgainstLease(query, { plan, lease, claim });
  if (canonical(plan.researchBackend) !== canonical(lease.researchBackend)) fail("research backend binding differs", { code: "backend-binding" });
  const adapter = plan.researchBackend.adapter;
  const localEndpoint = validateLocalResearchEndpoint(endpoint);
  if (!Number.isSafeInteger(maximumNetworkBytes) || maximumNetworkBytes < 1 || maximumNetworkBytes > plan.budgets.maxNetworkBytes) {
    fail("research network allowance is exhausted", {
      code: "budget-before-dispatch",
      knownUsage: { searchRequests: 0, retrievalRequests: 0, sources: 0, networkBytes: 0, sourceBytes: 0, rejectedSources: 0 },
    });
  }
  if (!Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > 120000 || typeof fetchImpl !== "function") {
    fail("research transport configuration is invalid", { code: "transport-config" });
  }
  const request = requestFor(query, adapter, localEndpoint);
  const requestBytes = Buffer.byteLength(request.body, "utf8");
  if (requestBytes >= maximumNetworkBytes) {
    fail("research request exceeds its remaining network allowance", {
      code: "budget-before-dispatch",
      knownUsage: { searchRequests: 0, retrievalRequests: 0, sources: 0, networkBytes: 0, sourceBytes: 0, rejectedSources: 0 },
    });
  }
  let response;
  try {
    response = await fetchImpl(request.url, {
      method: "POST", redirect: "error", credentials: "omit",
      headers: { accept: "application/json", "content-type": request.contentType, "cache-control": "no-store" },
      body: request.body, signal: AbortSignal.timeout(timeoutMilliseconds),
    });
  } catch (error) {
    fail("research backend request did not complete", { code: "backend-transport", cause: error });
  }
  if (response.url && response.url !== request.url) fail("research backend redirected the request", { code: "backend-redirect" });
  const maximumResponseBytes = Math.min(MAX_BACKEND_RESPONSE_BYTES, maximumNetworkBytes - requestBytes);
  const { value, usage } = await readBoundedJsonResponse(response, maximumResponseBytes, requestBytes);
  let rawResults;
  try { rawResults = adapter === "reference" ? parseReference(value, query) : parseNormalized(value, query); } catch (error) {
    if (error instanceof ResearchSearchError && error.knownUsage === null) error.knownUsage = usage;
    throw error;
  }
  return { rawResults, networkBytes: usage.networkBytes, usage, adapter };
}

function fingerprint(error) {
  return createHash("sha256").update(canonical({
    name: error instanceof Error ? error.name : "NonError",
    code: typeof error?.code === "string" ? error.code.slice(0, 100) : "unknown",
  })).digest("hex");
}

function after(value, minimumExclusive) {
  const candidate = value instanceof Date ? value.getTime() : Date.now();
  return new Date(Math.max(candidate, minimumExclusive + 1));
}

export async function runResearchSearch({
  stateRoot, query, plan, lease, claim, endpoint, fetchImpl = globalThis.fetch, timeoutMilliseconds = 30000,
  reserveNow = new Date(), batchNow, completeNow, failureNow,
  suffixes = {},
}) {
  let reservation;
  try {
    reservation = await reserveResearchQuery({
      stateRoot, query, plan, lease, claim, now: reserveNow,
      suffix: suffixes.reserve ?? randomBytes(6).toString("hex"),
    });
    const remaining = plan.budgets.maxNetworkBytes - reservation.record.usage.networkBytes;
    const searched = await searchPublicSources({ query, plan, lease, claim, endpoint, maximumNetworkBytes: remaining, timeoutMilliseconds, fetchImpl });
    const batchTime = after(batchNow, Date.parse(reservation.record.createdAt));
    const batch = createResearchBatch({
      query, plan, lease, claim, rawResults: searched.rawResults, adapter: searched.adapter,
      networkBytes: searched.networkBytes, now: batchTime,
      suffix: suffixes.batch ?? randomBytes(6).toString("hex"),
    });
    const completionTime = after(completeNow, batchTime.getTime());
    const completion = await completeResearchQuery({
      stateRoot, query, plan, lease, claim, batch, now: completionTime,
      suffix: suffixes.complete ?? randomBytes(6).toString("hex"),
    });
    return { batch, completion };
  } catch (error) {
    if (!reservation) throw error;
    const closeTime = after(failureNow, Date.parse(reservation.record.createdAt));
    try {
      await failReservedResearchQuery({
        stateRoot, plan, claim, failureFingerprintSha256: fingerprint(error),
        knownUsage: error instanceof ResearchSearchError ? error.knownUsage : null,
        now: closeTime, suffix: suffixes.failure ?? randomBytes(6).toString("hex"),
      });
    } catch (closureError) {
      throw new ResearchSearchError("research search failed and its reservation could not be closed", {
        code: "reservation-closure", cause: new AggregateError([error, closureError]),
      });
    }
    throw error;
  }
}

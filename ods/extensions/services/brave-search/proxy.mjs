// Brave Search HTTP proxy.
//
// GET /v1/search?q=<query>&count=<n>
//   → 200 { query, results: [{title, url, snippet}] }
//   → 400 missing_query_param_q
//   → 502 upstream_error (Brave API non-2xx)
//   → 502 upstream_unavailable (network/TLS/DNS failure)
//   → 502 invalid_upstream_json
//   → 504 upstream_timeout
//
// GET /search?format=json&q=<query>  (searxng compatibility mode, opt-in)
//   Enabled with BRAVE_SEARCH_SEARXNG_COMPAT=1. Returns a searxng-shaped
//   JSON document so consumers of searxng's API (e.g. Perplexica via
//   SEARXNG_API_URL) can point at this service instead. Upstream failures
//   are reported the way searxng reports a failing engine — HTTP 200 with
//   empty results and an unresponsive_engines entry — because that is the
//   contract searxng consumers already handle.
//   → 200 searxng envelope (results, suggestions, unresponsive_engines, …)
//   → 400 missing_query_param_q | unsupported_format
//   → 404 when compatibility mode is disabled
//
// GET /health
//   → 200 { ok: true }
//
// Wraps api.search.brave.com behind a small, stable JSON shape suitable for
// ods services and scripts. See README.md for design notes and the fidelity
// limits of the searxng compatibility mode.

import http from "node:http";

// Empty env values (e.g. from compose ${VAR:-} interpolation) fall back to
// defaults; present-but-invalid values fail startup loudly, like a missing
// API key does.
function timeoutMsFromEnv() {
  const raw = process.env.BRAVE_SEARCH_TIMEOUT_MS;
  if (raw === undefined || raw === "") {
    return 8_000;
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    console.error(`brave-search: BRAVE_SEARCH_TIMEOUT_MS must be a positive number, got "${raw}"`);
    process.exit(2);
  }
  return value;
}

const PORT = Number(process.env.BRAVE_SEARCH_PORT_INTERNAL ?? 8585);
const API_KEY = process.env.BRAVE_SEARCH_API_KEY;
const BRAVE_URL =
  process.env.BRAVE_SEARCH_UPSTREAM_URL || "https://api.search.brave.com/res/v1/web/search";
const REQUEST_TIMEOUT_MS = timeoutMsFromEnv();
const SEARXNG_COMPAT = /^(1|true|yes|on)$/i.test(process.env.BRAVE_SEARCH_SEARXNG_COMPAT ?? "");
// Brave's web endpoint returns at most 20 results per request.
const SEARXNG_PAGE_SIZE = 20;

if (!API_KEY) {
  console.error("brave-search: BRAVE_SEARCH_API_KEY is required");
  process.exit(2);
}

try {
  new URL(BRAVE_URL);
} catch {
  console.error(`brave-search: BRAVE_SEARCH_UPSTREAM_URL is not a valid URL: "${BRAVE_URL}"`);
  process.exit(2);
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function callBrave(query, count, offset) {
  const offsetParam = offset > 0 ? `&offset=${offset}` : "";
  const url = `${BRAVE_URL}?q=${encodeURIComponent(query)}&count=${count}${offsetParam}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, {
      headers: {
        Accept: "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": API_KEY,
      },
      // fetch does not strip custom headers on cross-origin redirects, so a
      // redirecting upstream could receive the subscription token. The Brave
      // API never redirects; refuse rather than follow.
      redirect: "error",
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

// Shared upstream call. Maps transport failures to a tagged shape so each
// route can render them in its own error contract (/v1 as 5xx, searxng
// compat as unresponsive_engines).
async function fetchBraveWeb(query, count, offset) {
  let upstream;
  try {
    upstream = await callBrave(query, count, offset);
  } catch (err) {
    if (err && err.name === "AbortError") {
      return { error: "timeout" };
    }
    if (err instanceof TypeError) {
      return { error: "unavailable" };
    }
    throw err;
  }
  if (!upstream.ok) {
    return { error: "http_error", status: upstream.status };
  }
  try {
    return { data: await upstream.json() };
  } catch (err) {
    if (err instanceof SyntaxError) {
      return { error: "invalid_json" };
    }
    throw err;
  }
}

async function handleV1Search(res, params) {
  const query = params.get("q");
  if (!query) {
    send(res, 400, { error: "missing_query_param_q" });
    return;
  }

  const requested = Number(params.get("count") ?? 5);
  const count = Math.min(20, Math.max(1, Number.isFinite(requested) ? Math.trunc(requested) : 5));

  const outcome = await fetchBraveWeb(query, count, 0);
  if (outcome.error === "timeout") {
    send(res, 504, { error: "upstream_timeout" });
    return;
  }
  if (outcome.error === "unavailable") {
    send(res, 502, { error: "upstream_unavailable" });
    return;
  }
  if (outcome.error === "http_error") {
    send(res, 502, { error: "upstream_error", status: outcome.status });
    return;
  }
  if (outcome.error === "invalid_json") {
    send(res, 502, { error: "invalid_upstream_json" });
    return;
  }

  const results = (outcome.data.web?.results ?? [])
    .slice(0, count)
    .map((r) => ({
      title: (r.title ?? "").trim(),
      url: (r.url ?? "").trim(),
      snippet: (r.description ?? "").trim(),
    }))
    .filter((r) => r.url.length > 0);

  send(res, 200, { query, results });
}

// ── searxng compatibility mode ──────────────────────────────────────────────

function searxngEnvelope(query, results, unresponsiveEngines) {
  return {
    query,
    // searxng convention: 0 means "engines reported no total estimate".
    // Brave does not report one, and consumers (Perplexica) ignore it.
    number_of_results: 0,
    results,
    answers: [],
    corrections: [],
    infoboxes: [],
    suggestions: [],
    unresponsive_engines: unresponsiveEngines,
  };
}

// Python urlparse 6-tuple (scheme, netloc, path, params, query, fragment),
// which searxng attaches to every result.
function parsedUrlTuple(u) {
  return [
    u.protocol.replace(/:$/, ""),
    u.host,
    u.pathname,
    "",
    u.search.replace(/^\?/, ""),
    u.hash.replace(/^#/, ""),
  ];
}

function toSearxngResults(data) {
  const raw = (data.web?.results ?? []).slice(0, SEARXNG_PAGE_SIZE);
  const results = [];
  for (const r of raw) {
    const urlText = (r.url ?? "").trim();
    if (!urlText) {
      continue;
    }
    let parsed;
    try {
      // Same policy as /v1's empty-url filter: drop upstream results whose
      // URL cannot represent a searxng result (parsed_url is mandatory).
      parsed = new URL(urlText);
    } catch {
      continue;
    }
    const position = results.length + 1;
    const item = {
      url: urlText,
      title: (r.title ?? "").trim(),
      content: (r.description ?? "").trim(),
      engine: "brave",
      engines: ["brave"],
      positions: [position],
      // searxng's score for a single engine with weight 1 is 1/position.
      score: 1 / position,
      category: "general",
      template: "default.html",
      parsed_url: parsedUrlTuple(parsed),
      publishedDate: typeof r.page_age === "string" && r.page_age ? r.page_age : null,
    };
    const thumbnail = r.thumbnail?.src;
    if (typeof thumbnail === "string" && thumbnail) {
      item.thumbnail = thumbnail;
    }
    results.push(item);
  }
  return results;
}

function splitParamList(value) {
  return (value ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);
}

const SEARXNG_ERROR_TEXT = {
  timeout: "timeout",
  unavailable: "unavailable",
  invalid_json: "invalid JSON",
};

async function handleSearxngSearch(res, params) {
  const format = params.get("format");
  if (format !== "json") {
    send(res, 400, { error: "unsupported_format", detail: "only format=json is supported" });
    return;
  }
  const query = params.get("q");
  if (!query) {
    send(res, 400, { error: "missing_query_param_q" });
    return;
  }

  // Brave's web endpoint only serves searxng's "general" category. Requests
  // for other categories or engines get an honest empty response instead of
  // web hits mislabeled as e.g. image results.
  const categories = splitParamList(params.get("categories"));
  const unsupportedCategory = categories.find((c) => c !== "general");
  if (unsupportedCategory) {
    send(
      res,
      200,
      searxngEnvelope(query, [], [["brave", `unsupported category: ${unsupportedCategory}`]]),
    );
    return;
  }
  const engines = splitParamList(params.get("engines"));
  if (engines.length > 0 && !engines.includes("brave")) {
    send(
      res,
      200,
      searxngEnvelope(query, [], engines.map((engine) => [engine, "engine not available"])),
    );
    return;
  }

  // searxng pages are 1-based; Brave offsets are 0-based pages capped at 9.
  const requestedPage = Number(params.get("pageno") ?? 1);
  const pageno = Number.isFinite(requestedPage) ? Math.trunc(requestedPage) : 1;
  const offset = Math.min(9, Math.max(0, pageno - 1));

  const outcome = await fetchBraveWeb(query, SEARXNG_PAGE_SIZE, offset);
  if (outcome.error) {
    const reason =
      outcome.error === "http_error"
        ? outcome.status === 429
          ? "too many requests"
          : `HTTP error ${outcome.status}`
        : SEARXNG_ERROR_TEXT[outcome.error];
    send(res, 200, searxngEnvelope(query, [], [["brave", reason]]));
    return;
  }
  send(res, 200, searxngEnvelope(query, toSearxngResults(outcome.data), []));
}

// ── router ──────────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && parsed.pathname === "/health") {
    send(res, 200, { ok: true });
    return;
  }

  if (req.method === "GET" && parsed.pathname === "/search" && SEARXNG_COMPAT) {
    await handleSearxngSearch(res, parsed.searchParams);
    return;
  }

  if (req.method !== "GET" || parsed.pathname !== "/v1/search") {
    send(res, 404, { error: "not_found" });
    return;
  }

  await handleV1Search(res, parsed.searchParams);
});

server.listen(PORT, () => {
  console.log(`brave-search proxy listening on :${PORT}`);
});

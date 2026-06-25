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
// GET /search?format=json&q=<query>&count=<n>
//   → 200 { query, number_of_results, results: [{title, url, content, engine, score, category}] }
//
// GET /health
//   → 200 { ok: true }
//
// Wraps api.search.brave.com behind a small, stable JSON shape suitable for
// dream-server services and scripts, including an opt-in SearXNG compatibility route.

import http from "node:http";

const PORT = Number(process.env.BRAVE_SEARCH_PORT_INTERNAL ?? 8585);
const API_KEY = process.env.BRAVE_SEARCH_API_KEY;
const BRAVE_URL = process.env.BRAVE_SEARCH_UPSTREAM_URL ?? "https://api.search.brave.com/res/v1/web/search";
const REQUEST_TIMEOUT_MS = 8_000;

if (!API_KEY) {
  console.error("brave-search: BRAVE_SEARCH_API_KEY is required");
  process.exit(2);
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function callBrave(query, count) {
  const url = `${BRAVE_URL}?q=${encodeURIComponent(query)}&count=${count}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, {
      headers: {
        Accept: "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": API_KEY,
      },
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && parsed.pathname === "/health") {
    send(res, 200, { ok: true });
    return;
  }

  const isV1 = parsed.pathname === "/v1/search";
  const isSearxng = parsed.pathname === "/search" && parsed.searchParams.get("format") === "json";

  if (req.method !== "GET" || (!isV1 && !isSearxng)) {
    send(res, 404, { error: "not_found" });
    return;
  }

  const query = parsed.searchParams.get("q");
  if (!query) {
    send(res, 400, { error: "missing_query_param_q" });
    return;
  }

  const requested = Number(parsed.searchParams.get("count") ?? 5);
  const count = Math.min(20, Math.max(1, Number.isFinite(requested) ? Math.trunc(requested) : 5));

  let upstream;
  try {
    upstream = await callBrave(query, count);
  } catch (err) {
    if (err && err.name === "AbortError") {
      send(res, 504, { error: "upstream_timeout" });
      return;
    }
    if (err instanceof TypeError) {
      send(res, 502, { error: "upstream_unavailable" });
      return;
    }
    throw err;
  }

  if (!upstream.ok) {
    send(res, 502, { error: "upstream_error", status: upstream.status });
    return;
  }

  let data;
  try {
    data = await upstream.json();
  } catch (err) {
    if (err instanceof SyntaxError) {
      send(res, 502, { error: "invalid_upstream_json" });
      return;
    }
    throw err;
  }

  const results = (data.web?.results ?? [])
    .slice(0, count)
    .map((r) => ({
      title: (r.title ?? "").trim(),
      url: (r.url ?? "").trim(),
      snippet: (r.description ?? "").trim(),
    }))
    .filter((r) => r.url.length > 0);

  if (isSearxng) {
    const searxngResults = results.map(r => ({
      title: r.title,
      url: r.url,
      content: r.snippet,
      engine: "brave",
      score: 1,
      category: "general"
    }));
    send(res, 200, {
      query,
      number_of_results: searxngResults.length,
      results: searxngResults
    });
  } else {
    send(res, 200, { query, results });
  }
});

server.listen(PORT, () => {
  console.log(`brave-search proxy listening on :${PORT}`);
});

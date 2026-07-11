// Contract tests for the brave-search proxy, focused on the opt-in searxng
// compatibility mode (extensions/services/brave-search/proxy.mjs).
//
// Runs entirely on loopback: an in-process stub stands in for the Brave API,
// and two real proxy child processes are exercised over HTTP — one with
// BRAVE_SEARCH_SEARXNG_COMPAT enabled, one with it left at the default.
//
// Run: bash tests/test-brave-search-searxng-compat.sh

import http from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PROXY_SCRIPT = path.join(ROOT, "extensions", "services", "brave-search", "proxy.mjs");
const STUB_API_KEY = "test-subscription-token";
const PROXY_TIMEOUT_MS = 1000;
const SLOW_UPSTREAM_DELAY_MS = 3000;

let failures = 0;

function check(name, cond, detail) {
  if (cond) {
    console.log(`  ok: ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL: ${name}${detail === undefined ? "" : ` — ${detail}`}`);
  }
}

// ── stub Brave upstream ─────────────────────────────────────────────────────

function braveBody() {
  return JSON.stringify({
    web: {
      results: [
        {
          title: " First Result ",
          url: "https://example.com/one?a=1#frag",
          description: " First snippet ",
          page_age: "2026-01-15T09:00:00",
          thumbnail: { src: "https://imgs.example.net/thumb1.png" },
        },
        { title: "Second Result", url: "https://example.org/two", description: "Second snippet" },
        { title: "No URL", url: "", description: "must be dropped" },
      ],
    },
  });
}

function stubHandler(req, res) {
  const url = new URL(req.url, "http://stub");
  if (req.headers["x-subscription-token"] !== STUB_API_KEY) {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "missing subscription token" }));
    return;
  }
  const q = url.searchParams.get("q");
  const respond = (status, body) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(body);
  };
  if (q === "err500") {
    respond(500, "{}");
  } else if (q === "err429") {
    respond(429, "{}");
  } else if (q === "badjson") {
    respond(200, "this is not json");
  } else if (q === "slow") {
    setTimeout(() => respond(200, braveBody()), SLOW_UPSTREAM_DELAY_MS);
  } else if (q === "echo") {
    const offset = url.searchParams.get("offset") ?? "none";
    const count = url.searchParams.get("count");
    respond(
      200,
      JSON.stringify({
        web: {
          results: [
            {
              title: `offset=${offset} count=${count}`,
              url: "https://example.com/echo",
              description: "echo",
            },
          ],
        },
      }),
    );
  } else {
    respond(200, braveBody());
  }
}

// ── proxy process management ────────────────────────────────────────────────

async function freePort() {
  const srv = http.createServer();
  srv.listen(0, "127.0.0.1");
  await once(srv, "listening");
  const port = srv.address().port;
  srv.close();
  await once(srv, "close");
  return port;
}

async function startProxy(port, stubPort, compatValue) {
  const child = spawn(process.execPath, [PROXY_SCRIPT], {
    env: {
      ...process.env,
      BRAVE_SEARCH_API_KEY: STUB_API_KEY,
      BRAVE_SEARCH_PORT_INTERNAL: String(port),
      BRAVE_SEARCH_UPSTREAM_URL: `http://127.0.0.1:${stubPort}/res/v1/web/search`,
      BRAVE_SEARCH_TIMEOUT_MS: String(PROXY_TIMEOUT_MS),
      BRAVE_SEARCH_SEARXNG_COMPAT: compatValue,
    },
    stdio: ["ignore", "inherit", "inherit"],
  });
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.status === 200) {
        return child;
      }
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  child.kill();
  throw new Error(`proxy on :${port} did not become healthy`);
}

async function getJson(base, pathAndQuery) {
  const res = await fetch(`${base}${pathAndQuery}`);
  return { status: res.status, body: await res.json() };
}

// ── test suites ─────────────────────────────────────────────────────────────

async function testV1Route(base) {
  console.log("v1 route (must stay stable):");

  const ok = await getJson(base, "/v1/search?q=ok&count=5");
  check("200 on success", ok.status === 200, `got ${ok.status}`);
  check("query echoed", ok.body.query === "ok");
  check("empty-url result dropped", ok.body.results.length === 2, `got ${ok.body.results.length}`);
  check(
    "result shape is {title, url, snippet}",
    ok.body.results.every(
      (r) =>
        typeof r.title === "string" &&
        typeof r.url === "string" &&
        typeof r.snippet === "string" &&
        Object.keys(r).length === 3,
    ),
  );
  check("fields trimmed", ok.body.results[0].title === "First Result");

  const noQ = await getJson(base, "/v1/search");
  check(
    "400 without q",
    noQ.status === 400 && noQ.body.error === "missing_query_param_q",
    JSON.stringify(noQ),
  );

  const err = await getJson(base, "/v1/search?q=err500");
  check(
    "502 on upstream HTTP error",
    err.status === 502 && err.body.error === "upstream_error" && err.body.status === 500,
    JSON.stringify(err),
  );

  const slow = await getJson(base, "/v1/search?q=slow");
  check(
    "504 on upstream timeout",
    slow.status === 504 && slow.body.error === "upstream_timeout",
    JSON.stringify(slow),
  );
}

async function testCompatDisabled(base) {
  console.log("searxng compat disabled (default):");
  const res = await getJson(base, "/search?format=json&q=ok");
  check("/search stays 404", res.status === 404 && res.body.error === "not_found", JSON.stringify(res));
}

function checkEnvelope(body) {
  check("envelope: number_of_results is 0", body.number_of_results === 0);
  for (const key of ["results", "answers", "corrections", "infoboxes", "suggestions", "unresponsive_engines"]) {
    check(`envelope: ${key} is an array`, Array.isArray(body[key]), typeof body[key]);
  }
}

async function testCompatEnabled(base) {
  console.log("searxng compat enabled:");

  const ok = await getJson(base, "/search?format=json&q=ok");
  check("200 on success", ok.status === 200, `got ${ok.status}`);
  check("query echoed", ok.body.query === "ok");
  checkEnvelope(ok.body);
  check("empty-url result dropped", ok.body.results.length === 2, `got ${ok.body.results.length}`);

  const [first, second] = ok.body.results;
  check("result url", first.url === "https://example.com/one?a=1#frag");
  check("result title trimmed", first.title === "First Result");
  check("result content from description", first.content === "First snippet");
  check("engine is brave", first.engine === "brave");
  check("engines is [brave]", JSON.stringify(first.engines) === '["brave"]');
  check("positions are 1-based", JSON.stringify(first.positions) === "[1]" && JSON.stringify(second.positions) === "[2]");
  check("score follows 1/position", first.score === 1 && second.score === 0.5);
  check("category is general", first.category === "general");
  check("template is default.html", first.template === "default.html");
  check(
    "parsed_url is a urlparse 6-tuple",
    JSON.stringify(first.parsed_url) === '["https","example.com","/one","","a=1","frag"]',
    JSON.stringify(first.parsed_url),
  );
  check("publishedDate from page_age", first.publishedDate === "2026-01-15T09:00:00");
  check("publishedDate null when absent", second.publishedDate === null);
  check("thumbnail mapped when present", first.thumbnail === "https://imgs.example.net/thumb1.png");
  check("thumbnail omitted when absent", !("thumbnail" in second));

  // The exact contract Perplexica's searchSearxng() destructures.
  check(
    "Perplexica contract: results[].title/url/content strings",
    ok.body.results.every(
      (r) => typeof r.title === "string" && typeof r.url === "string" && typeof r.content === "string",
    ),
  );
  check("Perplexica contract: suggestions array present", Array.isArray(ok.body.suggestions));

  const perplexicaStyle = await getJson(base, "/search?format=json&q=ok&categories=general&language=en&pageno=1");
  check(
    "Perplexica-style general query serves results",
    perplexicaStyle.status === 200 && perplexicaStyle.body.results.length === 2,
    JSON.stringify(perplexicaStyle.body.unresponsive_engines),
  );

  const htmlFormat = await getJson(base, "/search?q=ok");
  check(
    "400 without format=json",
    htmlFormat.status === 400 && htmlFormat.body.error === "unsupported_format",
    JSON.stringify(htmlFormat),
  );

  const noQ = await getJson(base, "/search?format=json");
  check(
    "400 without q",
    noQ.status === 400 && noQ.body.error === "missing_query_param_q",
    JSON.stringify(noQ),
  );

  const images = await getJson(base, "/search?format=json&q=ok&categories=images");
  check(
    "unsupported category → honest empty + unresponsive_engines",
    images.status === 200 &&
      images.body.results.length === 0 &&
      JSON.stringify(images.body.unresponsive_engines) ===
        '[["brave","unsupported category: images"]]',
    JSON.stringify(images.body.unresponsive_engines),
  );

  const foreignEngines = await getJson(base, "/search?format=json&q=ok&engines=google%20images,bing%20images");
  check(
    "foreign engines → honest empty + unresponsive_engines",
    foreignEngines.status === 200 &&
      foreignEngines.body.results.length === 0 &&
      JSON.stringify(foreignEngines.body.unresponsive_engines) ===
        '[["google images","engine not available"],["bing images","engine not available"]]',
    JSON.stringify(foreignEngines.body.unresponsive_engines),
  );

  const braveEngine = await getJson(base, "/search?format=json&q=ok&engines=brave");
  check(
    "engines=brave serves results",
    braveEngine.status === 200 && braveEngine.body.results.length === 2,
    JSON.stringify(braveEngine.body.unresponsive_engines),
  );

  const page3 = await getJson(base, "/search?format=json&q=echo&pageno=3");
  check(
    "pageno maps to Brave offset (page 3 → offset 2)",
    page3.body.results[0]?.title === "offset=2 count=20",
    JSON.stringify(page3.body.results[0]?.title),
  );

  const page1 = await getJson(base, "/search?format=json&q=echo");
  check(
    "first page sends no offset",
    page1.body.results[0]?.title === "offset=none count=20",
    JSON.stringify(page1.body.results[0]?.title),
  );

  const cases = [
    ["err429", "too many requests"],
    ["err500", "HTTP error 500"],
    ["badjson", "invalid JSON"],
    ["slow", "timeout"],
  ];
  for (const [q, reason] of cases) {
    const res = await getJson(base, `/search?format=json&q=${q}`);
    check(
      `upstream ${q} → 200 with unresponsive_engines "${reason}"`,
      res.status === 200 &&
        res.body.results.length === 0 &&
        JSON.stringify(res.body.unresponsive_engines) === JSON.stringify([["brave", reason]]),
      `status=${res.status} unresponsive=${JSON.stringify(res.body.unresponsive_engines)}`,
    );
  }
}

// ── main ────────────────────────────────────────────────────────────────────

const stub = http.createServer(stubHandler);
stub.listen(0, "127.0.0.1");
await once(stub, "listening");
const stubPort = stub.address().port;

const compatPort = await freePort();
const plainPort = await freePort();

const children = [];
try {
  children.push(await startProxy(compatPort, stubPort, "1"));
  children.push(await startProxy(plainPort, stubPort, ""));

  const compatBase = `http://127.0.0.1:${compatPort}`;
  const plainBase = `http://127.0.0.1:${plainPort}`;

  await testV1Route(compatBase);
  await testV1Route(plainBase);
  await testCompatDisabled(plainBase);
  await testCompatEnabled(compatBase);
} finally {
  for (const child of children) {
    child.kill();
  }
  stub.close();
}

if (failures > 0) {
  console.error(`${failures} brave-search searxng compat test(s) failed`);
  process.exit(1);
}
console.log("brave-search searxng compat tests passed");

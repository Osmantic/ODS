"use strict";

const configUrl = process.env.PERPLEXICA_CONFIG_URL || "http://127.0.0.1:3000/api/config";

function normalizeSearchURL(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return "";
  }
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("PERPLEXICA_SEARXNG_API_URL must be a valid URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("PERPLEXICA_SEARXNG_API_URL must use http or https");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(
      "PERPLEXICA_SEARXNG_API_URL must not contain credentials, a query, or a fragment",
    );
  }
  const path = parsed.pathname.replace(/\/+$/, "");
  return `${parsed.origin}${path}`;
}

let searchURL;
try {
  searchURL = normalizeSearchURL(process.env.PERPLEXICA_SEARXNG_API_URL);
} catch (error) {
  process.stderr.write(`[ods-perplexica] search-route sync failed: ${error.message}\n`);
  process.exit(1);
}

if (!searchURL) {
  process.exit(0);
}

async function request(payload) {
  const response = await fetch(configUrl, payload === undefined ? {
    signal: AbortSignal.timeout(5_000),
  } : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

async function update() {
  const current = await request();
  const values = current && current.values;
  if (!values || typeof values !== "object") {
    throw new Error("config response is missing values");
  }
  if (values.search?.searxngURL !== searchURL) {
    await request({ key: "search.searxngURL", value: searchURL });
  }

  const verified = (await request()).values || {};
  if (verified.search?.searxngURL !== searchURL) {
    throw new Error("active search route did not persist");
  }
  process.stdout.write(`${searchURL}\n`);
}

update().catch((error) => {
  process.stderr.write(`[ods-perplexica] search-route sync failed: ${error.message}\n`);
  process.exitCode = 1;
});

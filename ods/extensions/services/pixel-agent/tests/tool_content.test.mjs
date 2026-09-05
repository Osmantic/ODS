import test from "node:test";
import assert from "node:assert/strict";
import {
  appsToolText,
  statusToolText,
  unavailableToolText,
} from "../plugin/tool-content.mjs";

const projection = {
  timestamp: "2026-08-28T10:00:00.000Z",
  stale: false,
  ingress_ready: true,
  gateway_reachable: true,
  docker: "ok",
  ods_version: "2.6.0",
  runtime: { model: "Qwen3.5-9B-Q4_K_M.gguf", context_length: 32768 },
  app_count: 2,
  online_app_count: 2,
  apps: [
    {
      name: "ods-dashboard",
      status: "healthy",
      display_name: "Dashboard",
      purpose: "ODS control center",
      url: "http://localhost:3001/",
    },
    {
      name: "ods-searxng",
      status: "healthy",
      display_name: "SearXNG",
      purpose: "private web search",
      url: "http://localhost:8888/",
    },
  ],
};

test("apps result states the count and first application without JSON parsing", () => {
  const text = appsToolText(projection);
  assert.match(text, /reports 2 of 2 applications online/);
  assert.match(text, /first is Dashboard \[ods-dashboard\] \(healthy; ODS control center; http:\/\/localhost:3001\/\)/);
  assert.match(text, /SearXNG \[ods-searxng\] \(healthy; private web search; http:\/\/localhost:8888\/\)/);
  assert.match(text, /status-only untrusted evidence/);
  assert.ok(!text.startsWith("{"));
});

test("status result states each bounded host fact in natural language", () => {
  const text = statusToolText(projection);
  assert.match(text, /Pixel availability: available/);
  assert.match(text, /ingress ready/);
  assert.match(text, /gateway reachable/);
  assert.match(text, /Docker: ok/);
  assert.match(text, /ODS version: 2\.6\.0/);
  assert.match(text, /Loaded model: Qwen3\.5-9B-Q4_K_M\.gguf/);
  assert.match(text, /context length: 32768 tokens/);
  assert.match(text, /Projected Docker applications online: 2 of 2/);
  assert.match(text, /not the Dashboard's total ODS service count/);
  assert.match(text, /Services without a Docker container are not represented/);
  assert.match(text, /cannot classify intentionally unconfigured optional services/);
  assert.match(text, /cannot .* prove whole-stack health/);
  assert.ok(!text.includes("ods-dashboard"));
  assert.ok(!text.includes("http://localhost:3001/"));
});

test("unknown version and runtime stay explicit instead of being guessed", () => {
  const text = statusToolText({ ...projection, ods_version: "unknown", runtime: null });
  assert.match(text, /ODS version: unknown/);
  assert.match(text, /Loaded model: unavailable; context length: unavailable/);
});

test("empty and unavailable projections stay explicit and non-authoritative", () => {
  const empty = appsToolText({ ...projection, app_count: 0, online_app_count: 0, apps: [], stale: true });
  assert.match(empty, /reports 0 of 0 applications online/);
  assert.match(empty, /stale projection/);
  assert.match(unavailableToolText(), /projection is unavailable/);
  assert.match(unavailableToolText(), /not authority for an action/);
});

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import vm from "node:vm";
import { createPerplexicaAvailability, createPerplexicaResearchTool, researchOutputChars,
  researchToolWhenAvailable } from "../plugin/perplexica-research.mjs";

const preferences = {
  defaultChatProvider: "owner-chat", defaultChatModel: "ods/current",
  defaultEmbeddingProvider: "owner-embedding", defaultEmbeddingModel: "local-mini",
};
const SECRET = "sk-PRIVATE-PROVIDER-KEY";
const configured = () => Response.json({ values: { preferences, modelProviders: [{ config: { apiKey: SECRET } }] } });
const probe = async (fetch, deps = {}) => {
  const availability = createPerplexicaAvailability({ env: {}, fetch, ...deps });
  await availability.refresh();
  return availability.state();
};

test("the probe offers the tool only when chat and embedding defaults are configured", async () => {
  const requests = [];
  assert.equal(await probe(async (url, options) => { requests.push({ url, options }); return configured(); }, { port: 43211 }), "available");
  assert.equal(requests[0].url, "http://127.0.0.1:43211/api/config");
  assert.equal(requests[0].options.redirect, "error");
  assert.equal(await probe(async () => Response.json({ values: { preferences: { ...preferences, defaultEmbeddingModel: "" } } })), "unconfigured");
  assert.equal(await probe(async () => Response.json({ values: {} })), "unconfigured");
  // Not installed: nothing listens on the port.
  assert.equal(await probe(async () => { throw new TypeError("fetch failed"); }), "absent");
  assert.equal(await probe(async () => { throw new Error("must not run"); }, { port: "http://remote/" }), "absent");
  // Up but unusable or unreadable: unknown, never "available".
  assert.equal(await probe(async () => new Response("busy", { status: 503 })), "unknown");
  assert.equal(await probe(async () => new Response(`not json ${SECRET}`)), "unknown");
  assert.equal(await probe(async () => new Response(new Uint8Array(2_000_001).fill(32))), "unknown");
  assert.equal(await probe((_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }), { probeTimeoutMs: 5 }), "unknown");
});

test("a refused local connection is the not-installed state", async () => {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  assert.equal(await probe(globalThis.fetch, { port }), "absent");
});

test("the probe keeps only model identities from the secret-bearing config body", async () => {
  const availability = createPerplexicaAvailability({ env: {}, fetch: async () => configured() });
  await availability.refresh();
  assert.equal(availability.state(), "available");
  assert.doesNotMatch(JSON.stringify(Object.entries(availability)), /PRIVATE-PROVIDER-KEY/);
});

test("one probe runs at a time and the cached state refreshes only after its TTL", async () => {
  let now = 1000, calls = 0, release;
  const availability = createPerplexicaAvailability({ env: {}, now: () => now, ttlMs: 60000, fetch: async () => {
    calls++;
    await new Promise(resolve => { release = resolve; });
    return configured();
  } });
  const first = availability.refresh();
  availability.refresh();
  availability.refreshIfStale();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.equal(availability.state(), "unknown", "the factory never waits for the probe");
  release(); await first;
  assert.equal(availability.state(), "available");
  now += 59999; availability.refreshIfStale();
  assert.equal(calls, 1);
  now += 1; availability.refreshIfStale();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 2);
  release();
});

test("the run's tool factory offers research only while Perplexica is available", async () => {
  let state = "unknown", refreshes = 0;
  const availability = { state: () => state, refreshIfStale: () => { refreshes++; } };
  const tool = createPerplexicaResearchTool({ env: {} });
  const factory = researchToolWhenAvailable(availability, tool);
  for (const [value, offered] of [["unknown", false], ["absent", false], ["unconfigured", false], ["available", true]]) {
    state = value;
    assert.equal(factory(), offered ? tool : null, value);
  }
  assert.equal(refreshes, 4);
});

test("research calls update availability: refused connection, missing defaults, success", async () => {
  const availability = createPerplexicaAvailability({ env: {}, fetch: async () => configured() });
  await availability.refresh();
  const run = async fetch => createPerplexicaResearchTool({ env: {}, availability, fetch })
    .execute("call", { query: "Research public facts" }, new AbortController().signal);
  assert.equal((await run(async () => { throw new TypeError("fetch failed"); })).details.status, "unavailable");
  assert.equal(availability.state(), "absent", "the next run no longer offers the tool");
  assert.equal((await run(async () => Response.json({ values: { preferences: {} } }))).details.status, "configuration_required");
  assert.equal(availability.state(), "unconfigured");
  let calls = 0;
  const ok = await run(async () => ++calls === 1 ? configured() :
    new Response('{"type":"sources","data":[]}\n{"type":"response","data":"Overview."}\n{"type":"done"}'));
  assert.equal(ok.details.status, "completed");
  assert.equal(availability.state(), "available");
  // A cancelled call says nothing about the service.
  const controller = new AbortController(); controller.abort();
  await createPerplexicaResearchTool({ env: {}, availability, fetch: async () => { throw new Error("must not run"); } })
    .execute("call", { query: "Research" }, controller.signal);
  assert.equal(availability.state(), "available");
});

// The real registration block from index.js, with only its collaborators stubbed.
function registration(registrationMode, state) {
  const source = fs.readFileSync(new URL("../plugin/index.js", import.meta.url), "utf8");
  const start = source.indexOf("    // Offered only while the owner's Perplexica");
  const end = source.indexOf("    registerTool(api, createAgentSkillTool()", start);
  assert.ok(start >= 0 && end > start, "expected the research registration block");
  const registered = [], probes = [];
  const availability = { state: () => state, refreshIfStale: () => { probes.push("refresh"); } };
  const sandbox = {
    api: { registrationMode, pluginConfig: { perplexicaPort: 3004 }, config: {},
      registerTool: (factory, options) => registered.push({ factory, options }) },
    onlyPixel: factory => context => context.agentId === "pixel" ? factory(context) : null,
    createPerplexicaAvailability: options => { probes.push(options); return availability; },
    createPerplexicaResearchTool, researchOutputChars, researchToolWhenAvailable,
    AGENT_ID: "pixel", perplexicaAvailability: undefined,
  };
  vm.runInNewContext(source.slice(start, end), sandbox);
  assert.equal(registered.length, 1);
  // Objects built inside the vm context have that realm's prototypes.
  assert.equal(JSON.stringify(registered[0].options), '{"names":["pixel_ods_research"]}');
  return { tool: registered[0].factory({ agentId: "pixel" }), probes };
}

test("registration: not offered when Perplexica is not installed; schema discovery never probes", () => {
  const absent = registration("full", "absent");
  assert.equal(absent.tool, null, "not installed: the run's tool set has no pixel_ods_research");
  assert.equal(JSON.stringify(absent.probes), '[{"port":3004},"refresh","refresh"]');
  const available = registration("full", "available");
  assert.equal(available.tool?.name, "pixel_ods_research");
  const discovery = registration("discovery", "absent");
  assert.equal(discovery.tool?.name, "pixel_ods_research");
  assert.equal(discovery.probes.length, 0);
});

test("pixel_ods_research stays deferred, so offering it never changes prompt bytes", () => {
  const envelope = JSON.parse(fs.readFileSync(new URL("../host/openclaw-image-envelope.json", import.meta.url), "utf8"));
  const current = envelope.replacements.flat().filter(text => typeof text === "string" && text.includes("odsNativeNames = "));
  assert.equal(current.length, 1);
  const literal = current[0].slice(current[0].indexOf("odsNativeNames = "));
  const names = [...literal.slice(literal.indexOf("new Set(["), literal.indexOf("])")).matchAll(/"([a-z_]+)"/g)].map(match => match[1]);
  assert.ok(names.includes("pixel_ods_web_extract") && names.includes("web_search"), names.join());
  assert.equal(names.includes("pixel_ods_research"), false);
});

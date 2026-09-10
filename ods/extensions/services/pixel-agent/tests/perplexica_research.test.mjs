import test from "node:test";
import assert from "node:assert/strict";
import { createPerplexicaResearchTool, readResearchStream } from "../plugin/perplexica-research.mjs";

const config = { values: { preferences: {
  defaultChatProvider: "owner-chat", defaultChatModel: "ods/current",
  defaultEmbeddingProvider: "owner-embedding", defaultEmbeddingModel: "local-mini",
}, modelProviders: [{ config: { apiKey: "PRIVATE-KEY" } }] } };
const signal = () => new AbortController().signal;
function stream(events, width = 7) {
  const bytes = new TextEncoder().encode(events.map((e) => JSON.stringify(e)).join("\n"));
  let position = 0;
  return new Response(new ReadableStream({ pull(c) {
    if (position >= bytes.length) return c.close();
    c.enqueue(bytes.slice(position, position += width));
  } }));
}
const events = [
  { type: "sources", data: [{ metadata: { title: "First", url: "javascript:alert(1)" } }, { metadata: { title: "Café", url: "https://example.org/source" } }] },
  { type: "response", data: "Evidence café 🐈 [2]." }, { type: "done" },
];

test("delegates only the brief and configured model identities; preserves citation indexes", async () => {
  const calls = [];
  const tool = createPerplexicaResearchTool({ port: 43210, env: {}, fetch: async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? Response.json(config) : stream(events, 1);
  } });
  const result = await tool.execute("call", { query: "  Compare the sources  ", history: "PRIVATE-CHAT", endpoint: "http://remote/" }, signal());
  assert.equal(result.details.status, "completed");
  assert.equal(calls[0].url, "http://127.0.0.1:43210/api/config");
  assert.equal(calls[1].url, "http://127.0.0.1:43210/api/search");
  assert.ok(calls.every((c) => c.options.redirect === "error"));
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    query: "Compare the sources", sources: ["web"], history: [], stream: true, optimizationMode: "speed",
    chatModel: { providerId: "owner-chat", key: "ods/current" },
    embeddingModel: { providerId: "owner-embedding", key: "local-mini" },
  });
  const text = result.content[0].text;
  assert.match(text, /café 🐈 \[2\]/);
  assert.match(text, /"index":1,"title":"First","urlUnavailable":true/);
  assert.match(text, /"index":2,"title":"Café","url":"https:\/\/example.org\/source"/);
  assert.doesNotMatch(text, /PRIVATE|javascript:/);
  assert.match(text, /untrusted research evidence/);
});

test("an EOF, corrupt JSON, or error event cannot masquerade as completed research", async () => {
  for (const response of [stream([{ type: "response", data: "An unfinished claim" }]), new Response("SECRET not JSON"), stream([{ type: "error", data: "SECRET" }])]) {
    let calls = 0;
    const tool = createPerplexicaResearchTool({ env: {}, fetch: async () => ++calls === 1 ? Response.json(config) : response });
    const result = await tool.execute("call", { query: "Research" }, signal());
    assert.equal(result.isError, true);
    assert.equal(result.details.status, "unavailable");
    assert.doesNotMatch(JSON.stringify(result), /SECRET|unfinished claim/);
  }
});

test("abort cancels a pending reader and does not falsely claim upstream cancellation", async () => {
  const controller = new AbortController();
  let cancelled = false, count = 0, started;
  const ready = new Promise((resolve) => { started = resolve; });
  const tool = createPerplexicaResearchTool({ env: {}, fetch: async () => ++count === 1 ? Response.json(config) : new Response(new ReadableStream({ start() { started(); }, cancel() { cancelled = true; } })) });
  const work = tool.execute("call", { query: "Research" }, controller.signal);
  await ready; controller.abort();
  const result = await work;
  assert.equal(cancelled, true);
  assert.equal(result.details.status, "cancelled");
  assert.equal(result.details.upstreamCancellationVerified, false);
  assert.match(result.content[0].text, /may still be working/);
});

test("pre-cancelled calls and invalid ports send no requests", async () => {
  const controller = new AbortController(); controller.abort();
  let calls = 0;
  const fetch = async () => { calls++; throw new Error("must not run"); };
  const cancelled = await createPerplexicaResearchTool({ env: {}, fetch }).execute("call", { query: "Research" }, controller.signal);
  assert.equal(cancelled.details.researchSubmitted, false);
  for (const port of ["http://remote/", 0, 65536, 1.5]) {
    const result = await createPerplexicaResearchTool({ port, env: {}, fetch }).execute("call", { query: "Research" }, signal());
    assert.equal(result.isError, true);
  }
  assert.equal(calls, 0);
});

test("missing defaults do not silently select a different or cloud model", async () => {
  let calls = 0;
  const result = await createPerplexicaResearchTool({ env: {}, fetch: async () => { calls++; return Response.json({ values: { preferences: {} } }); } }).execute("call", { query: "Research" }, signal());
  assert.equal(calls, 1);
  assert.equal(result.details.status, "configuration_required");
});

test("result excerpts remain bounded without aborting successful longer research", async () => {
  let calls = 0;
  const result = await createPerplexicaResearchTool({ env: {}, fetch: async () => ++calls === 1 ? Response.json(config) : stream([
    { type: "response", data: "a".repeat(30000) }, { type: "done" },
  ], 8192) }).execute("call", { query: "Research" }, signal());
  assert.equal(result.details.status, "completed");
  assert.equal(result.details.answerChars, 30000);
  assert.equal(result.details.truncated, true);
  assert.ok(result.content[0].text.length < 25000);
});

test("transport byte budget rejects unbounded upstream output", async () => {
  await assert.rejects(readResearchStream(stream([{ type: "response", data: "a".repeat(2000000) }, { type: "done" }], 64000), signal(), () => {}), /too large/);
});

function evidence(result) {
  const text = result.content[0].text;
  return JSON.parse(text.slice(text.indexOf(">\n") + 2, text.lastIndexOf("\n</perplexica_evidence_")));
}

test("keeps cited sources beyond the discovery prefix without increasing the source limit", async () => {
  const sources = Array.from({length: 144}, (_, i) => ({metadata: {
    title: `Source ${i + 1}`, url: `https://example.org/source-${i + 1}`,
  }}));
  const answer = "Supported statements [21][45][46][48][49][50][144]. Repeated citation [45].";
  for (const sourcesFirst of [true, false]) {
    let calls = 0;
    const sourceEvent = {type: "sources", data: sources};
    const responseEvent = {type: "response", data: answer};
    const response = stream([...(sourcesFirst ? [sourceEvent, responseEvent] : [responseEvent, sourceEvent]), {type: "done"}], 8192);
    const result = await createPerplexicaResearchTool({env: {}, fetch: async () => ++calls === 1 ? Response.json(config) : response})
      .execute("call", {query: "Compare public sources"}, signal());
    const output = evidence(result);
    assert.equal(output.answer, answer);
    assert.equal(output.sources.length, 40);
    for (const index of [21, 45, 46, 48, 49, 50, 144]) {
      assert.deepEqual(output.sources.find(source => source.index === index), {
        index, title: `Source ${index}`, url: `https://example.org/source-${index}`,
      });
    }
    assert.equal(result.details.sourceCount, 144);
    assert.equal(result.details.retainedSourceCount, 40);
    assert.equal(result.details.omittedCitationCount, 0);
    assert.equal(result.details.truncated, true);
  }
});

test("reports omitted citations and preserves unusable URL indexes without fabricating replacements", async () => {
  let calls = 0;
  const sources = Array.from({length: 50}, (_, i) => ({metadata: {
    title: `Source ${i + 1}`, url: i === 0 ? "https://user:secret@example.org/private" : `https://example.org/${i + 1}`,
  }}));
  const answer = Array.from({length: 45}, (_, i) => `[${i + 1}]`).join(" ") + " [9999] [45]";
  const result = await createPerplexicaResearchTool({env: {}, fetch: async () => ++calls === 1 ? Response.json(config) : stream([
    {type: "sources", data: sources}, {type: "response", data: answer}, {type: "done"},
  ], 8192)}).execute("call", {query: "Research"}, signal());
  const output = evidence(result);
  assert.equal(output.sources.length, 40);
  assert.deepEqual(output.sources[0], {index: 1, title: "Source 1", urlUnavailable: true});
  assert.equal(result.details.omittedCitationCount, 6);
  assert.match(result.content[0].text, /6 cited source entries are not included/);
  assert.doesNotMatch(result.content[0].text, /user:secret|example\.org\/9999/);
});

test("a completed request without sources is explicitly unverified", async () => {
  let calls = 0;
  const result = await createPerplexicaResearchTool({env: {}, fetch: async () => ++calls === 1 ? Response.json(config) : stream([
    {type: "response", data: "No relevant sources found."}, {type: "sources", data: []}, {type: "done"},
  ])}).execute("call", {query: "Research"}, signal());
  assert.equal(result.details.status, "completed", "execution completed even though evidence is missing");
  assert.equal(result.details.retainedSourceCount, 0);
  assert.match(result.content[0].text, /returned no source citations.*unverified/);
  assert.deepEqual(evidence(result).sources, []);
});

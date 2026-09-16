import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDownloadPromoteTool } from "../plugin/download-promote.mjs";
import { createPerplexicaResearchTool } from "../plugin/perplexica-research.mjs";
import { createHostCommandProposeTool } from "../plugin/host-observe.mjs";

const promotion = {
  jobId: "ops-1788130169655-22b40ab50141", filename: "reference.html",
  relativePath: "web/reference.html", sha256: "a".repeat(64), sourceUrl: "https://example.org/",
};
const longUrl = length => "https://example.org/" + "a".repeat(length - 20);
const nativeGrammar = process.env.ODS_TEST_LLAMA_SCHEMA;

for (const [tool, samples] of [
  [createDownloadPromoteTool(), [promotion, { ...promotion, sourceUrl: longUrl(4096) }]],
  [createPerplexicaResearchTool(), [{ query: "Find public sources" }, { query: "a".repeat(16000) }]],
  [createHostCommandProposeTool(), [{ command: "pwd" }, { command: "a".repeat(16384) }]],
]) {
  test(`${tool.name} schema compiles and accepts short and full-size arguments in llama.cpp`,
    { skip: !nativeGrammar && "set ODS_TEST_LLAMA_SCHEMA to the pinned native test bridge" }, () => {
      for (const args of samples) {
        const result = spawnSync(nativeGrammar, [], {
          input: JSON.stringify({ schema: tool.parameters, arguments: args }), encoding: "utf8", timeout: 30000,
        });
        assert.ifError(result.error);
        assert.equal(result.status, 0, `${tool.name}: ${result.stderr}`);
      }
      // Loosening grammar length bounds must preserve required fields/types.
      for (const args of [{}, Object.fromEntries(Object.keys(samples[0]).map(key => [key, 123]))]) {
        const result = spawnSync(nativeGrammar, [], {
          input: JSON.stringify({ schema: tool.parameters, arguments: args }), encoding: "utf8", timeout: 30000,
        });
        assert.ifError(result.error);
        assert.equal(result.status, 2, result.stderr);
      }
    });
}

test("promotion keeps the 4096-character URL limit at execution before any host request", async () => {
  const requests = [];
  const tool = createDownloadPromoteTool({ request: async request => { requests.push(request); throw new Error("test host unavailable"); } });
  await tool.execute("full-url", { ...promotion, sourceUrl: longUrl(4096) });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].sourceUrl.length, 4096);
  const rejected = await tool.execute("oversize", { ...promotion, sourceUrl: longUrl(4097) });
  assert.equal(rejected.details.invalidField, "sourceUrl");
  assert.equal(requests.length, 1);
});

test("research accepts the existing 16000-character brief and rejects larger input before HTTP", async () => {
  let calls = 0;
  const tool = createPerplexicaResearchTool({ env: {}, fetch: async () => {
    calls++; return Response.json({ values: { preferences: {} } });
  } });
  assert.equal((await tool.execute("full-brief", { query: "a".repeat(16000) })).details.status, "configuration_required");
  assert.equal(calls, 1);
  for (const query of ["a".repeat(16001), "", "   "]) {
    assert.equal((await tool.execute("invalid-brief", { query })).details.status, "invalid_request");
  }
  assert.equal(calls, 1);
});

test("host proposals keep their character and byte limits before writing a broker request", async t => {
  const root = await mkdtemp(join(tmpdir(), "pixel-command-length-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const requestDir = join(root, "requests"), resultDir = join(root, "results");
  await mkdir(requestDir); await mkdir(resultDir);
  const tool = createHostCommandProposeTool({ requestDir, resultDir, timeoutMs: 1, pollIntervalMs: 1 });
  const command = "a".repeat(16384);
  await tool.execute("full-command", { command });
  const files = (await readdir(requestDir)).filter(name => name.endsWith(".json"));
  assert.equal(files.length, 1);
  const receipt = JSON.parse(await readFile(join(requestDir, files[0]), "utf8"));
  assert.equal(receipt.command, command);
  for (const rejected of ["a".repeat(16385), "é".repeat(8193), "", " ", "pwd\0"]) {
    assert.equal((await tool.execute("invalid-command", { command: rejected })).isError, true);
  }
  assert.deepEqual((await readdir(requestDir)).filter(name => name.endsWith(".json")), files);
});

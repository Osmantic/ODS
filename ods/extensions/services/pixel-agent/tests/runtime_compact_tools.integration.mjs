// Exercise the exported catalog and control tools from the exact package used
// by the installer, including their real discovery/dispatch implementation.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const modulePath = process.env.OPENCLAW_TOOL_SEARCH_MODULE;
const manifest = JSON.parse(readFileSync(new URL("../host/openclaw-image-envelope.json", import.meta.url)));
assert.equal(createHash("sha256").update(readFileSync(modulePath)).digest("hex"), manifest.patchedSha256);
const {u: apply, a: Runtime, m: createRef, h: controls, y: resolveConfig} = await import(pathToFileURL(modulePath));
const tool = (name) => ({name, label: name, description: `Use ${name} on the workspace`,
  parameters: {type: "object", properties: {path: {type: "string"}}},
  execute: async (_id, input) => ({content: [{type: "text", text: input.path ?? name}]})});
const config = (contextTokens, id = "pixel") => ({agents: {list: [{id, contextTokens}]},
  tools: {toolSearch: {enabled: true, mode: "tools"}}});
function setup(contextTokens = 8192, id = "pixel") {
  const ctx = {agentId: id, config: config(contextTokens, id), catalogRef: createRef()};
  const native = [tool("read"), tool("write"), tool("pixel_ods_workspace_preview"), tool("other_permitted")];
  const tools = [...controls(ctx), ...native];
  return {ctx, native, tools, result: apply({...ctx, tools}), runtime: new Runtime(ctx, resolveConfig(ctx.config))};
}
const names = (result) => result.tools.map(t => t.name).sort();

test("8K exposes three controls while deferred tools remain discoverable and callable", async () => {
  const {ctx, tools, result, runtime} = setup();
  assert.deepEqual(names(result), ["tool_call", "tool_describe", "tool_search"]);
  assert.equal(result.catalogToolCount, 4);
  assert.deepEqual(runtime.all().map(t => t.name).sort(), ["other_permitted", "pixel_ods_workspace_preview", "read", "write"]);
  const matches = await runtime.search("read workspace");
  const read = matches.find(t => t.name === "read");
  assert.ok(read);
  assert.deepEqual((await runtime.describe(read.id)).parameters, tools.find(t => t.name === "read").parameters);
  const answer = await result.tools.find(t => t.name === "tool_call").execute("test-call", {id: read.id, input: {path: "fixture.txt"}});
  assert.ok(JSON.stringify(answer).includes("fixture.txt"));
  assert.equal(ctx.catalogRef.current.callCount, 1);
});

test("64K to 8K to 64K recomputes visibility even when reusing a catalog", () => {
  const {ctx, tools, runtime} = setup(65536);
  const first = ctx.catalogRef.current;
  for (const [budget, expected] of [[8192, 3], [65536, 6], [8192, 3]]) {
    ctx.config.agents.list[0].contextTokens = budget;
    const result = apply({...ctx, tools});
    assert.equal(result.catalogReused, true);
    assert.equal(ctx.catalogRef.current, first);
    assert.equal(result.tools.length, expected);
    assert.equal(runtime.all().length, 4);
  }
});

test("removed policy tools disappear from the reused session and cannot dispatch", async () => {
  const {ctx, tools, runtime} = setup();
  const result = apply({...ctx, tools: tools.filter(t => t.name !== "read")});
  assert.equal(result.catalogReused, false);
  assert.equal(runtime.all().some(t => t.name === "read"), false);
  await assert.rejects(runtime.call("read", {}), /Unknown tool id/);
});

test("deferred dispatch retains the caller's execution and refusal path", async () => {
  const {ctx, runtime} = setup();
  let seen;
  ctx.executeTool = async (params) => { seen = params; throw new Error("owner policy refused"); };
  await assert.rejects(runtime.call("write", {path: "fixture.txt"}), /owner policy refused/);
  assert.equal(seen.toolName, "write");
  assert.deepEqual(seen.input, {path: "fixture.txt"});
});

test("only known positive safe 8K-or-smaller budgets change the Pixel surface", () => {
  for (const budget of [1, 4096, 8192]) assert.equal(setup(budget).result.tools.length, 3);
  for (const budget of [8193, 65536, 0, -1, 8192.5, "8192", null, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(setup(budget).result.tools.length, 6, `budget ${budget}`);
  }
  const sample = setup();
  delete sample.ctx.config.agents;
  assert.equal(apply({...sample.ctx, tools: sample.tools}).tools.length, 6);
});

test("non-Pixel agents preserve caller visibility and disabled or absent controls preserve fallback", () => {
  const other = setup(8192, "other");
  const visible = apply({...other.ctx, tools: other.tools, isVisibleCatalogTool: () => true});
  assert.deepEqual(names(visible), ["other_permitted", "pixel_ods_workspace_preview", "read", "tool_call", "tool_describe", "tool_search", "write"]);
  const small = setup();
  small.ctx.config.tools.toolSearch.enabled = false;
  const disabled = apply({...small.ctx, tools: small.tools});
  assert.equal(disabled.compacted, false);
  assert.equal(disabled.tools, small.tools);
  small.ctx.config.tools.toolSearch.enabled = true;
  const absent = apply({...small.ctx, tools: small.native});
  assert.equal(absent.compacted, false);
  assert.deepEqual(absent.tools, small.native);
});

test("ambiguous native names remain deferred at large and small budgets", () => {
  const {ctx, tools} = setup(65536);
  tools.push(tool("read"));
  for (const budget of [65536, 8192]) {
    ctx.config.agents.list[0].contextTokens = budget;
    assert.equal(apply({...ctx, tools}).tools.some(t => t.name === "read"), false);
  }
});

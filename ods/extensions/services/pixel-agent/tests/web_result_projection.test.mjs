import test from "node:test";
import assert from "node:assert/strict";
import { projectWebResult } from "../plugin/web-result-projection.mjs";
import { createToolLoopGuard } from "../plugin/tool-loop-guard.mjs";

const tool = { id: "openclaw:core:web_search", source: "openclaw", sourceName: "core", name: "web_search" };
const message = { role: "toolResult", toolName: "tool_call", toolCallId: "call-1", content: [{ type: "text", text: "{truncated wrapper" }] };
const evidence = { provider: "parallel-free", results: [{ url: "https://example.com/menu", description: "<<<EXTERNAL_UNTRUSTED_CONTENT>>>\n餃子 café 😀\n".repeat(80) }], truncated: false };
const envelope = () => ({ tool, result: { content: [{ type: "text", text: JSON.stringify(evidence, null, 2) }], details: structuredClone(evidence) } });

test("keeps complete native evidence while removing duplicate serialized details", () => {
  const input = envelope();
  const before = structuredClone(input);
  const projected = projectWebResult(message, input);
  assert.equal(projected.toolCallId, message.toolCallId);
  assert.equal(projected.content[1].text, input.result.content[0].text);
  assert.deepEqual(JSON.parse(projected.content[1].text), evidence);
  assert.equal(JSON.parse(projected.content[0].text).result.details, undefined);
  assert.deepEqual(projected.details, input);
  assert.deepEqual(input, before);
  assert.ok(projected.content.reduce((n, c) => n + c.text.length, 0) < JSON.stringify(input).length * 0.65);
  assert.equal(message.content[0].text, "{truncated wrapper");
});

test("preserves nonduplicate metadata, error state, and exact text", () => {
  const input = envelope();
  input.result.isError = true;
  input.result.details = { status: "error", code: "upstream_timeout", provider: "parallel-free", truncated: true };
  const projected = projectWebResult(message, input);
  assert.equal(projected.isError, true);
  assert.deepEqual(JSON.parse(projected.content[0].text).result.details, input.result.details);
  assert.equal(projected.content[1].text, input.result.content[0].text);
  const successfulInner = envelope();
  assert.equal(projectWebResult({ ...message, isError: true }, successfulInner).isError, true);
});

test("only removes the exact duplicate aggregated field", () => {
  const input = envelope();
  input.result.details = { aggregated: input.result.content[0].text, status: "completed", extra: "retained" };
  assert.deepEqual(JSON.parse(projectWebResult(message, input).content[0].text).result.details,
    { status: "completed", extra: "retained" });
  input.result.details.aggregated += "distinct";
  assert.deepEqual(JSON.parse(projectWebResult(message, input).content[0].text).result.details, input.result.details);
});

for (const [name, change] of [
  ["different tool", (e) => { e.tool = { ...tool, name: "exec" }; }],
  ["different source", (e) => { e.tool = { ...tool, sourceName: "plugin" }; }],
  ["different id", (e) => { e.tool = { ...tool, id: "openclaw:plugin:web_search" }; }],
  ["missing content", (e) => { delete e.result.content; }],
  ["empty content", (e) => { e.result.content = []; }],
  ["image content", (e) => { e.result.content.push({ type: "image", data: "kept elsewhere", mimeType: "image/png" }); }],
  ["unknown content", (e) => { e.result.content.push({ type: "resource", uri: "example" }); }],
  ["malformed text", (e) => { e.result.content[0].text = 42; }],
]) test(`leaves unsupported ${name} untouched`, () => {
  const input = envelope(); change(input); const before = structuredClone(input);
  assert.equal(projectWebResult(message, input), undefined);
  assert.deepEqual(input, before);
});

function capturedRun({ changedArgs = false } = {}) {
  const guard = createToolLoopGuard();
  const context = { agentId: "pixel", sessionId: "session-1", runId: "run-1", toolName: "tool_call", toolCallId: "call-1" };
  const params = { id: tool.id, args: { query: "Philadelphia restaurant delivery" } };
  guard.observeRun(context, "pixel", { prompt: "Research restaurants and save notes." });
  assert.notEqual(guard.beforeToolCall({ toolName: "tool_call", params }, context)?.block, true);
  const original = envelope();
  guard.afterToolCall({ toolName: "tool_call", params: changedArgs ? { ...params, args: { query: "different" } } : params,
    result: { content: [{ type: "text", text: JSON.stringify(original) }], details: original } }, context);
  return { guard, context, original };
}

test("exact captured web call survives framework persistence truncation", () => {
  const { guard, context, original } = capturedRun();
  const persisted = guard.toolResultPersist({ message: { ...message, details: { persistedDetailsTruncated: true } } }, context);
  assert.equal(persisted.message.content[1].text, original.result.content[0].text);
  assert.equal(guard.toolResultPersist({ message }, context), undefined, "capture is consumed once");
});

test("different params or run cannot reuse captured evidence", () => {
  const changed = capturedRun({ changedArgs: true });
  assert.equal(changed.guard.toolResultPersist({ message }, changed.context), undefined);
  const mismatch = capturedRun();
  assert.equal(mismatch.guard.toolResultPersist({ message }, { ...mismatch.context, runId: "other-run" }), undefined);
  assert.equal(projectWebResult({ ...message, toolName: "web_search" }, envelope()), undefined);
});

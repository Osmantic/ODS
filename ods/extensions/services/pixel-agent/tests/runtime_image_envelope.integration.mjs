// Execute the helper extracted from the exact reviewed runtime, not a copy.
// OPENCLAW_TOOL_SEARCH_MODULE must point to candidate Tool Search package bytes.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const manifest = JSON.parse(readFileSync(new URL("../host/openclaw-image-envelope.json", import.meta.url)));
const source = readFileSync(process.env.OPENCLAW_TOOL_SEARCH_MODULE, "utf8");
assert.equal(createHash("sha256").update(source).digest("hex"), manifest.patchedSha256);
const start = "function toolCallResultEnvelope(payload) {";
const end = "async function runCodeMode(params) {";
assert.equal(source.split(start).length, 2);
assert.equal(source.split(end).length, 2);
assert.equal(source.split("return toolCallResultEnvelope(await runtime.call(call.id, call.input, {").length, 2);
const block = source.slice(source.indexOf(start), source.indexOf(end));
const jsonResult = payload => ({content: [{type: "text", text: JSON.stringify(payload, null, 2)}], details: payload});
const execute = new Function("isRecord", "jsonResult", block + "\nreturn toolCallResultEnvelope;")(
  value => value !== null && typeof value === "object" && !Array.isArray(value), jsonResult
);
const image = {type: "image", data: "iVBORw0KGgo".repeat(30000), mimeType: "image/png"};
const tool = {id: "openclaw:core:browser", source: "openclaw", sourceName: "core", name: "browser", description: "long schema ".repeat(500)};

test("large screenshots remain typed images and are not duplicated in prompt text", () => {
  const payload = {tool, result: {content: [image], details: {targetId: "owned-target"}}};
  const result = execute(payload);
  assert.equal(result.content[1], image);
  assert.equal(result.details, payload);
  assert.deepEqual(JSON.parse(result.content[0].text), {tool: {id: tool.id, source: tool.source, sourceName: tool.sourceName, name: tool.name}});
  assert.ok(result.content.filter(b => b.type === "text").every(b => !b.text.includes(image.data)));
  assert.ok(!result.content[0].text.includes(tool.description));
});

test("text, image order, annotations, errors and result metadata survive without truncation", () => {
  const text = {type: "text", text: "Café <bot> 🌿\n".repeat(2000), annotations: {audience: ["assistant"]}};
  const empty = {type: "text", text: ""};
  const second = {...image, mimeType: "image/jpeg"};
  const payload = {tool, result: {content: [text, image, empty, second], isError: true, structuredContent: {status: "partial"}, details: {error: "capture failed"}}};
  const original = JSON.stringify(payload);
  const result = execute(payload);
  assert.equal(result.isError, true);
  assert.equal(result.structuredContent, payload.result.structuredContent);
  payload.result.content.forEach((part, i) => assert.equal(result.content[i + 1], part));
  assert.equal(result.details, payload);
  assert.equal(JSON.stringify(payload), original);
});

test("outer errors propagate without inventing an error on successful images", () => {
  assert.equal(execute({tool, isError: true, result: {content: [image]}}).isError, true);
  assert.equal(Object.hasOwn(execute({tool, result: {content: [image]}}), "isError"), false);
});

test("non-image and unfamiliar payloads retain the existing serializer", () => {
  for (const payload of [undefined, null, {}, {tool, result: {content: [{type: "text", text: "ordinary result"}]}}, {tools: [tool]}, {result: {content: "not an array"}}]) {
    assert.deepEqual(execute(payload), jsonResult(payload));
  }
});

test("identical exec output enters prompt text once while full provenance and process handles survive", () => {
  const output = "Unique command output: Café, line 42\n".repeat(500);
  const payload = {tool, result: {content: [{type: "text", text: output}], details: {
    aggregated: output, status: "running", sessionId: "opaque-owned-process", cwd: "/workspace/project", exitCode: null
  }}};
  const result = execute(payload);
  const visible = JSON.parse(result.content[0].text);
  assert.equal(visible.result.content[0].text, output);
  assert.equal(Object.hasOwn(visible.result.details, "aggregated"), false);
  assert.equal(visible.result.details.sessionId, "opaque-owned-process");
  assert.equal(visible.result.details.status, "running");
  assert.equal(visible.result.details.exitCode, null);
  assert.equal(result.details, payload);
  assert.equal(payload.result.details.aggregated, output);
  assert.ok(result.content[0].text.length < jsonResult(payload).content[0].text.length - output.length);
});

test("distinct, partial, multiple-block and malformed command output is never discarded", () => {
  const cases = [
    {content: [{type: "text", text: "partial"}], details: {aggregated: "complete output"}},
    {content: [{type: "text", text: "first"}, {type: "text", text: "second"}], details: {aggregated: "first\nsecond"}},
    {content: [{type: "text", text: "same"}], details: {aggregated: {value: "same"}}},
    {content: [{type: "text", text: "same"}], details: "same"},
  ];
  for (const result of cases) {
    const payload = {tool, result};
    assert.deepEqual(execute(payload), jsonResult(payload));
  }
});

test("JSON tool results enter prompt text once with source, trust and truncation metadata intact", () => {
  const details = {
    url: "https://example.test/guide.txt", status: 200,
    externalContent: {untrusted: true, source: "web_fetch", wrapped: true},
    text: "<<<EXTERNAL_UNTRUSTED_CONTENT>>>\n" + "Source line: Café, 東京, 🌿\n".repeat(500),
    truncated: true, rawLength: 50000, length: 12000,
    warning: "Only the selected section was retrieved", continuation: {nextLine: 501},
  };
  const content = [{type: "text", text: JSON.stringify(details, null, 2), annotations: {audience: ["assistant"]}}];
  const payload = {tool, result: {content, details, structuredContent: {receipt: "owned-read"}}};
  const original = JSON.stringify(payload);
  const result = execute(payload);
  const visible = JSON.parse(result.content[0].text);
  assert.equal(Object.hasOwn(visible.result, "details"), false);
  assert.deepEqual(JSON.parse(visible.result.content[0].text), details);
  assert.deepEqual(visible.result.content, content);
  assert.deepEqual(visible.result.structuredContent, payload.result.structuredContent);
  assert.equal(result.details, payload);
  assert.equal(JSON.stringify(payload), original);
  assert.ok(result.content[0].text.length < jsonResult(payload).content[0].text.length * 0.7);
});

test("only exact complete JSON duplicates are projected", () => {
  const details = {status: 200, text: "source excerpt"};
  const cases = [
    {content: [{type: "text", text: JSON.stringify(details)}], details},
    {content: [{type: "text", text: JSON.stringify(details, null, 2)}], details: {...details, warning: "additional evidence"}},
    {content: [{type: "text", text: JSON.stringify(details, null, 2)}, {type: "text", text: "second block"}], details},
    {content: [{type: "text", text: "not JSON"}], details},
    {content: [{type: "text", text: "[]"}], details: []},
  ];
  for (const result of cases) {
    const payload = {tool, result};
    assert.deepEqual(execute(payload), jsonResult(payload));
  }
});

test("JSON error evidence remains complete in the projected text and original framework payload", () => {
  const details = {status: "failed", error: "Origin unavailable", retryAfter: 30};
  const payload = {tool, isError: true, result: {
    isError: true, content: [{type: "text", text: JSON.stringify(details, null, 2)}], details,
  }};
  const result = execute(payload);
  const visible = JSON.parse(result.content[0].text);
  assert.equal(visible.isError, true);
  assert.equal(visible.result.isError, true);
  assert.deepEqual(JSON.parse(visible.result.content[0].text), details);
  assert.equal(result.details, payload);
});

test("mixed unsupported or malformed blocks are never silently discarded", () => {
  for (const part of [null, {type: "resource", resource: {uri: "owned:file"}}, {type: "text", text: 7}, {type: "image", data: ""}, {type: "image", data: "abc", mimeType: ""}, {type: "image", data: "abc", mimeType: "text/html"}]) {
    const payload = {tool, result: {content: [image, part]}};
    assert.deepEqual(execute(payload), jsonResult(payload));
  }
});

test("exact reviewed transformation reverses without touching unrelated runtime code", () => {
  let original = source;
  for (const [before, after] of [...manifest.replacements].reverse()) {
    assert.equal(original.split(after).length, 2);
    original = original.replace(after, before);
  }
  assert.equal(createHash("sha256").update(original).digest("hex"), manifest.sourceSha256);
});

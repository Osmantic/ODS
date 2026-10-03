import assert from "node:assert/strict";
import test from "node:test";

import {
  extractPatchProposal, verifyPatchString,
  PATCH_SUBMISSION_TOOL_NAME, PATCH_SUBMISSION_MAX_PATCH_BYTES,
} from "../deploy/work-provider/patch-extraction.mjs";
import { WorkProviderPatchVerifierError } from "../deploy/work-provider/patch-verifier.mjs";

const PATCH_BASE = "export function add(a, b) {\n  return a - b;\n}\n";
const PATCH_EXPECTED = "export function add(a, b) {\n  return a + b;\n}\n";
const PATCH = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";

function toolCallAssistant(argumentsValue, name = PATCH_SUBMISSION_TOOL_NAME, id = "k3patch_0001") {
  return { message: { role: "assistant", content: null, toolCalls: [{ id, name, arguments: argumentsValue }] } };
}
function contentAssistant(content) { return { message: { role: "assistant", content } }; }

test("K3 patch-proposal extracts the patch from exactly one correct submit_patch tool call", () => {
  assert.equal(extractPatchProposal({
    task: "patch-proposal", lane: "moonshot-kimi",
    assistant: toolCallAssistant(JSON.stringify({ patch: PATCH })),
  }), PATCH);
});

test("local patch-proposal and disposable-coding-patch remain deterministic raw-diff tasks", () => {
  assert.equal(extractPatchProposal({ task: "patch-proposal", lane: "local-only", assistant: contentAssistant(PATCH) }), PATCH);
  assert.equal(extractPatchProposal({ task: "disposable-coding-patch", lane: "moonshot-kimi", assistant: contentAssistant(PATCH) }), PATCH);
  assert.equal(extractPatchProposal({ task: "disposable-coding-patch", lane: "local-only", assistant: contentAssistant(PATCH) }), PATCH);
});

test("K3 patch-proposal rejects adversarial and ambiguous submissions as semantic failures", () => {
  const goodCall = toolCallAssistant(JSON.stringify({ patch: PATCH })).message.toolCalls[0];
  const cases = [
    ["zero tool calls", contentAssistant(PATCH)],
    ["multiple tool calls", { message: { role: "assistant", content: null, toolCalls: [goodCall, goodCall] } }],
    ["wrong tool", toolCallAssistant(JSON.stringify({ patch: PATCH }), "edit_file")],
    ["malformed arguments", toolCallAssistant("{ not json")],
    ["duplicate object key", toolCallAssistant('{"patch":"a","patch":"b"}')],
    ["extra key", toolCallAssistant(JSON.stringify({ patch: PATCH, other: 1 }))],
    ["missing key", toolCallAssistant(JSON.stringify({ path: "x" }))],
    ["non-string", toolCallAssistant(JSON.stringify({ patch: 42 }))],
    ["empty", toolCallAssistant(JSON.stringify({ patch: "" }))],
    ["NUL", toolCallAssistant(JSON.stringify({ patch: "a\u0000b" }))],
    ["oversized", toolCallAssistant(JSON.stringify({ patch: "a".repeat(PATCH_SUBMISSION_MAX_PATCH_BYTES + 1) }))],
    ["content plus tool", { message: { role: "assistant", content: "prose", toolCalls: [goodCall] } }],
    ["missing assistant", null],
  ];
  for (const [label, assistant] of cases) {
    assert.equal(extractPatchProposal({ task: "patch-proposal", lane: "moonshot-kimi", assistant }), null, label);
  }
});

test("a tool call where raw diff is required is a semantic failure", () => {
  const assistant = toolCallAssistant(JSON.stringify({ patch: PATCH }));
  assert.equal(extractPatchProposal({ task: "patch-proposal", lane: "local-only", assistant }), null);
  assert.equal(extractPatchProposal({ task: "disposable-coding-patch", lane: "moonshot-kimi", assistant }), null);
});

test("verifyPatchString grades valid input and converts only untrusted patch errors to the failing rubric", async () => {
  assert.deepEqual(await verifyPatchString({ patch: PATCH, base: PATCH_BASE, expected: PATCH_EXPECTED }), {
    checkPassed: true, applied: true, exact: true, secondApplyRejected: true, reverseCheckPassed: true,
  });
  const failing = { checkPassed: false, applied: false, exact: false, secondApplyRejected: false, reverseCheckPassed: false };
  assert.deepEqual(await verifyPatchString({ patch: null, base: PATCH_BASE, expected: PATCH_EXPECTED }), failing);
  assert.deepEqual(await verifyPatchString({ patch: "not a unified diff", base: PATCH_BASE, expected: PATCH_EXPECTED }), failing);
  await assert.rejects(verifyPatchString({ patch: PATCH, base: "", expected: PATCH_EXPECTED }), WorkProviderPatchVerifierError);
});

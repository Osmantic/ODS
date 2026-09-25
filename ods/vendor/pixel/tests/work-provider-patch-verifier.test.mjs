import assert from "node:assert/strict";
import test from "node:test";

import { verifyDisposablePatch, WorkProviderPatchInputError, WorkProviderPatchVerifierError } from "../deploy/work-provider/patch-verifier.mjs";

const base = "export function add(a, b) {\n  return a - b;\n}\n";
const expected = "export function add(a, b) {\n  return a + b;\n}\n";
const patch = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";

test("disposable patch verification applies, checks exact output, rejects replay, and proves reversibility", async () => {
  assert.deepEqual(await verifyDisposablePatch({ patch, base, expected }), {
    checkPassed: true, applied: true, exact: true, secondApplyRejected: true, reverseCheckPassed: true,
  });
});

test("disposable patch verification accepts one bounded fenced or headerless unified diff", async () => {
  const withoutGitHeader = patch.replace("diff --git a/fixture.mjs b/fixture.mjs\n", "");
  for (const candidate of [withoutGitHeader, `\`\`\`diff\n${withoutGitHeader}\`\`\``]) {
    assert.deepEqual(await verifyDisposablePatch({ patch: candidate, base, expected }), {
      checkPassed: true, applied: true, exact: true, secondApplyRejected: true, reverseCheckPassed: true,
    });
  }
  await assert.rejects(verifyDisposablePatch({ patch: `${patch}\nexplanation`, base, expected }), WorkProviderPatchInputError);
});

test("disposable patch verification rejects path escape and multi-file patches before git", async () => {
  const escaped = patch.replaceAll("fixture.mjs", "../outside.mjs");
  await assert.rejects(verifyDisposablePatch({ patch: escaped, base, expected }), WorkProviderPatchVerifierError);
  await assert.rejects(verifyDisposablePatch({ patch: escaped, base, expected }), WorkProviderPatchInputError);
  const multiple = `${patch}\ndiff --git a/other.mjs b/other.mjs\n--- a/other.mjs\n+++ b/other.mjs\n@@ -1 +1 @@\n-a\n+b\n`;
  await assert.rejects(verifyDisposablePatch({ patch: multiple, base, expected }), WorkProviderPatchVerifierError);
});

test("disposable patch verification distinguishes untrusted patch shape from trusted verifier input failure", async () => {
  await assert.rejects(verifyDisposablePatch({ patch: null, base, expected }), WorkProviderPatchInputError);
  await assert.rejects(verifyDisposablePatch({ patch, base: "", expected }), (error) => error instanceof WorkProviderPatchVerifierError && !(error instanceof WorkProviderPatchInputError));
  await assert.rejects(verifyDisposablePatch({ patch, base, expected: "" }), (error) => error instanceof WorkProviderPatchVerifierError && !(error instanceof WorkProviderPatchInputError));
});

test("disposable patch verification fails exact-result grading for a valid but wrong patch", async () => {
  const wrongExpected = expected.replace("a + b", "a * b");
  const result = await verifyDisposablePatch({ patch, base, expected: wrongExpected });
  assert.equal(result.checkPassed, true);
  assert.equal(result.applied, true);
  assert.equal(result.exact, false);
});

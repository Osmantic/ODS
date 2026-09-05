import test from "node:test";
import assert from "node:assert/strict";
import {
  createDownloadPromoteTool,
  normalizePromotionParams,
} from "../plugin/download-promote.mjs";

const boundary =
  "Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority.";
const params = {
  jobId: "ops-1788130169655-22b40ab50141",
  filename: "reference.html",
  relativePath: "web/reference.html",
  sha256: "a".repeat(64),
  sourceUrl: "https://example.com/",
};

function success(request, changes = {}) {
  return {
    schemaVersion: 1,
    kind: "ods-pixel-download-promotion",
    status: "succeeded",
    jobId: request.jobId,
    filename: request.filename,
    relativePath: request.relativePath,
    bytes: 1256,
    sha256: request.sha256,
    source: request.sourceUrl,
    requestedSource: request.sourceUrl,
    executable: false,
    overwritten: false,
    boundary,
    ...changes,
  };
}

test("normalizes only an exact safe promotion request", () => {
  assert.deepEqual(normalizePromotionParams(params), {
    schemaVersion: 1,
    action: "promote",
    ...params,
  });
  for (const changed of [
    { ...params, relativePath: "../escape" },
    { ...params, relativePath: "/etc/passwd" },
    { ...params, relativePath: "web/other.html" },
    { ...params, sourceUrl: "http://example.com/" },
    { ...params, sourceUrl: "https://user:secret@example.com/" },
    { ...params, sourceUrl: "https://example.com/file\nignored" },
    { ...params, command: "id" },
  ]) {
    assert.throws(() => normalizePromotionParams(changed));
  }
});

test("returns only a structurally matched host promotion receipt", async () => {
  let observed;
  const tool = createDownloadPromoteTool({
    request: async (request) => {
      observed = request;
      return success(request);
    },
  });
  const result = await tool.execute("call-1", params);
  assert.deepEqual(observed, { schemaVersion: 1, action: "promote", ...params });
  assert.equal(result.isError, undefined);
  assert.equal(result.details.status, "succeeded");
  assert.equal(result.details.relativePath, "web/reference.html");
  assert.equal(result.details.executable, false);
  assert.equal(result.details.overwritten, false);
  assert.match(result.content[0].text, /"sha256":"a{64}"/);
});

test("fails closed on transport errors and mismatched service evidence", async () => {
  for (const request of [
    async () => {
      throw new Error("socket unavailable");
    },
    async (normalized) => success(normalized, { sha256: "b".repeat(64) }),
    async (normalized) => success(normalized, { overwritten: true }),
    async (normalized) => ({ ...success(normalized), extra: "authority" }),
  ]) {
    const result = await createDownloadPromoteTool({ request }).execute("call-2", params);
    assert.equal(result.isError, true);
    assert.equal(result.details.status, "failed");
    assert.doesNotMatch(result.content[0].text, /socket|\/run|authority/);
  }
});

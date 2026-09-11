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

test("explains the captured argument-name mistake before any host request and permits correction", async () => {
  const requests = [];
  const tool = createDownloadPromoteTool({
    request: async (request) => {
      requests.push(request);
      return success(request);
    },
  });
  const { relativePath, sourceUrl, ...rest } = params;
  const failed = await tool.execute("bad-arguments", {
    ...rest, destination: relativePath, url: sourceUrl,
  });
  assert.equal(requests.length, 0);
  assert.equal(failed.isError, true);
  assert.deepEqual(failed.details, {
    status: "failed", errorCode: "invalid_arguments", boundary, invalidField: "fields",
  });
  assert.match(failed.content[0].text, /relativePath \(not destination\)/);
  assert.match(failed.content[0].text, /sourceUrl \(not url\)/);
  assert.match(failed.content[0].text, /No host request was made/);
  assert.match(failed.content[0].text, /retry the same verified job/);

  const corrected = await tool.execute("corrected-arguments", params);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].jobId, params.jobId);
  assert.equal(corrected.details.status, "succeeded");
});

test("validation guidance never echoes rejected values or contacts the host", async () => {
  let contacted = false;
  const tool = createDownloadPromoteTool({ request: async () => { contacted = true; } });
  const privateValue = "PRIVATE_VALUE_NOT_FOR_MODEL_OUTPUT";
  for (const rejected of [
    null, [], {},
    { ...params, relativePath: "../" + privateValue },
    { ...params, sourceUrl: `https://user:${privateValue}@example.com/` },
    { ...params, sha256: privateValue },
    { ...params, [privateValue]: "extra field" },
  ]) {
    const result = await tool.execute("invalid", rejected);
    assert.equal(result.isError, true);
    assert.equal(result.details.errorCode, "invalid_arguments");
    assert.doesNotMatch(JSON.stringify(result), new RegExp(privateValue));
    assert.match(result.content[0].text, /No host request was made/);
  }
  assert.equal(contacted, false);
});

test("identifies a malformed receipt ID without blaming the valid destination", async () => {
  let contacted = false;
  const tool = createDownloadPromoteTool({ request: async () => { contacted = true; } });
  const result = await tool.execute("lost-receipt", {
    ...params, jobId: "ops-1757381036-9d9c71d9",
  });
  assert.equal(result.details.invalidField, "jobId");
  assert.match(result.content[0].text, /Invalid field: jobId/);
  assert.match(result.content[0].text, /Retrieve the actual receipt/);
  assert.doesNotMatch(result.content[0].text, /Invalid field: relativePath/);
  assert.doesNotMatch(result.content[0].text, /ops-1757381036-9d9c71d9/);
  assert.equal(contacted, false);
});

test("never exposes an unexpected validation exception as a field hint", async () => {
  const rejected = { ...params };
  Object.defineProperty(rejected, "jobId", {
    get() { throw new Error("PRIVATE_VALIDATION_EXCEPTION"); },
  });
  const result = await createDownloadPromoteTool().execute("unexpected", rejected);
  assert.equal(result.isError, true);
  assert.equal(result.details.errorCode, "invalid_arguments");
  assert.equal(result.details.invalidField, undefined);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE_VALIDATION_EXCEPTION/);
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

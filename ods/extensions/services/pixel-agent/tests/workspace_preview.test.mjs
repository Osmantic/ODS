import assert from "node:assert/strict";
import test from "node:test";

import {
  createWorkspacePreviewTool,
  normalizeWorkspacePreviewParams,
  testing,
} from "../plugin/workspace-preview.mjs";

function succeededResponse(overrides = {}) {
  const sha256 = "a".repeat(64);
  const siteId = `site-${sha256.slice(0, 24)}`;
  const port = 9437;
  return {
    schemaVersion: 1,
    kind: "ods-pixel-workspace-preview",
    status: "succeeded",
    relativeDirectory: "demo-site",
    siteId,
    port,
    url: `http://${siteId}.localhost:${port}/${siteId}/`,
    files: 3,
    bytes: 9000,
    sha256,
    entryFile: "index.html",
    entrySha256: "b".repeat(64),
    httpStatus: 200,
    readbackVerified: true,
    executable: false,
    overwritten: false,
    boundary: testing.BOUNDARY,
    ...overrides,
  };
}

test("normalizes only one bounded workspace-relative directory", () => {
  assert.deepEqual(normalizeWorkspacePreviewParams({ relativeDirectory: "demo-site" }), {
    schemaVersion: 1,
    action: "publish",
    relativeDirectory: "demo-site",
  });
  for (const value of ["", "/tmp/site", "../site", "a\\b", "a//b", "a/./b"]) {
    assert.throws(
      () => normalizeWorkspacePreviewParams({ relativeDirectory: value }),
      /invalid Pixel workspace preview request/
    );
  }
});

test("rejects every ODS-authored creative scaffold or extra request field", () => {
  for (const extra of [
    { scaffold: { title: "Demo", tagline: "Generated", theme: "aurora" } },
    { template: "breakout" },
    { html: "<h1>Generated</h1>" },
  ]) {
    assert.throws(
      () => normalizeWorkspacePreviewParams({ relativeDirectory: "demo-site", ...extra }),
      /invalid Pixel workspace preview request/
    );
  }
});

test("exposes a publish-only schema with no creative generator input", () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse() });
  assert.deepEqual(Object.keys(tool.parameters.properties), ["relativeDirectory"]);
  assert.equal(tool.parameters.additionalProperties, false);
  assert.match(tool.description, /already created by the active model/);
  assert.match(tool.description, /never supplies creative starter bytes/i);
  assert.doesNotMatch(JSON.stringify(tool.parameters), /scaffold|template|title|tagline|theme/);
});

test("publishes only the exact existing workspace directory", async () => {
  const calls = [];
  const tool = createWorkspacePreviewTool({
    request: async (request) => {
      calls.push(request);
      return succeededResponse();
    },
  });
  const result = await tool.execute("call-1", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, undefined);
  assert.equal(result.details.readbackVerified, true);
  assert.match(result.content[0].text, /independently published and read back/);
  assert.deepEqual(calls, [{
    schemaVersion: 1,
    action: "publish",
    relativeDirectory: "demo-site",
  }]);
});

test("rejects a preview site ID that is not content-addressed to the snapshot", async () => {
  const mismatchedSiteId = "site-0123456789abcdef01234567";
  const tool = createWorkspacePreviewTool({
    request: async () => succeededResponse({
      siteId: mismatchedSiteId,
      url: `http://${mismatchedSiteId}.localhost:9437/${mismatchedSiteId}/`,
    }),
  });
  const result = await tool.execute("call-mismatched-site", {
    relativeDirectory: "demo-site",
  });
  assert.equal(result.isError, true);
});

test("fails before contacting the host when creative bytes are supplied", async () => {
  let calls = 0;
  const tool = createWorkspacePreviewTool({
    request: async () => {
      calls += 1;
      return succeededResponse();
    },
  });
  const result = await tool.execute("call-generated", {
    relativeDirectory: "demo-site",
    scaffold: { title: "Demo", tagline: "Generated", theme: "aurora" },
  });
  assert.equal(result.isError, true);
  assert.equal(calls, 0);
});

test("fails closed on a mismatched or unverified service response", async () => {
  const tool = createWorkspacePreviewTool({
    request: async () => succeededResponse({
      port: 3000,
      url: "http://localhost:3000/demo-site/",
      readbackVerified: false,
    }),
  });
  const result = await tool.execute("call-2", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, true);
  assert.equal(result.details.status, "failed");
  assert.match(result.content[0].text, /do not claim a localhost URL is live/);
});

test("fails closed when the host response contains an uncontracted field", async () => {
  const tool = createWorkspacePreviewTool({
    request: async () => succeededResponse({ redirect: "https://attacker.example/" }),
  });
  const result = await tool.execute("call-3", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, true);
});

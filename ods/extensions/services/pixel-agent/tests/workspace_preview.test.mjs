import assert from "node:assert/strict";
import test from "node:test";

import {
  createWorkspacePreviewTool,
  EMPTY_PUBLISHED_FILES_PREFIX,
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
  assert.match(tool.description, /localStorage\/sessionStorage property getters, reads and writes may throw/);
  assert.match(tool.description, /Guard every storage access\/operation with try\/catch and an in-memory fallback/);
  assert.match(tool.description, /Saving failure must not block startup, controls or continued work/);
  assert.match(tool.description, /Never promise persistence or add allow-same-origin to bypass isolation/);
  assert.match(tool.description, /blocks alert\(\), confirm\(\) and prompt\(\); use inline DOM controls, including date inputs/);
  assert.match(tool.description, /remote scripts, styles, fonts, images and API requests are blocked/);
  assert.match(tool.description, /HTTP readback proves publication, not startup or interactions/);
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
  const snapshot=JSON.parse(result.content[0].text.match(/Inspection snapshot: (\{[^}]+\})\./)[1]);
  assert.deepEqual(snapshot,{siteId:result.details.siteId,sha256:result.details.sha256});
  assert.notEqual(snapshot.sha256,result.details.entrySha256);
  assert.match(result.content[0].text,/pixel_ods_workspace_preview_inspect/);
  assert.match(result.content[0].text, /independently published and read back/);
  assert.match(result.content[0].text, /publication and HTTP readback only, not successful startup, interactions or durable browser storage/);
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

test("shows actionable fixed failure categories without echoing host exception text", async () => {
  const failure = {
    schemaVersion: 1, kind: "ods-pixel-workspace-preview", status: "failed",
    boundary: testing.BOUNDARY, error: "ODS workspace preview publication failed",
    errorCode: "unsupported_file_type",
  };
  const tool = createWorkspacePreviewTool({ request: async () => failure });
  const result = await tool.execute("csv-failure", { relativeDirectory: "demo-site" });
  assert.equal(result.details.errorCode, "unsupported_file_type");
  assert.match(result.content[0].text, /CSV and TSV/);
  for (const value of [
    { ...failure, error: "private exception /home/private/token" },
    { ...failure, errorCode: "/home/private/token" },
    { ...failure, boundary: "wrong boundary" },
    { ...failure, relativePath: "/home/private/token" },
  ]) {
    const invalid = createWorkspacePreviewTool({ request: async () => value });
    const blocked = await invalid.execute("invalid", { relativeDirectory: "demo-site" });
    assert.equal(blocked.isError, true);
    assert.equal(blocked.details.errorCode, "unavailable");
    assert.doesNotMatch(JSON.stringify(blocked), /\/home\/private|wrong boundary/);
  }
});


test("receipt names only delivered files when requested source copies remain outside public", async () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse({
    publishedPaths: ["index.html", "sources.json", "test-results.txt"],
    publishedPathsOmitted: 0,
  }) });
  const result = await tool.execute("missing-copies", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, undefined);
  assert.deepEqual(result.details.publishedPaths, ["index.html", "sources.json", "test-results.txt"]);
  assert.match(result.content[0].text, /\["index.html","sources.json","test-results.txt"\]/);
  assert.doesNotMatch(result.content[0].text, /report\.py\.txt|totals\.py\.txt|readbacks are complete/);
  assert.match(result.content[0].text, /does not determine whether requested files or checks are missing/);
});

test("invalid JSON publication requests repair from actual source bytes", async () => {
  const tool = createWorkspacePreviewTool({request:async()=>({
    schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'failed',
    boundary:testing.BOUNDARY,error:'ODS workspace preview publication failed',
    errorCode:'invalid_json_artifact',artifactError:{path:'export.json',line:2,column:18},
  })});
  const result=await tool.execute('invalid-source-export',{relativeDirectory:'project/public'});
  assert.equal(result.isError,true);
  assert.equal(result.details.errorCode,'invalid_json_artifact');
  assert.match(result.content[0].text,/actual final files.*JSON serializer.*parse it back/);
  assert.match(result.content[0].text,/Do not hand-transcribe/);
  assert.match(result.content[0].text,/export.json.*line 2, column 18/);
  assert.deepEqual(result.details.artifactError,{path:'export.json',line:2,column:18});
});

test('invalid artifact diagnostics cannot reveal uncontracted host details',async()=>{
  for(const artifactError of [
    {path:'/etc/private.json',line:1,column:1},
    {path:'../private.json',line:1,column:1},
    {path:'export.json',line:1,column:1,content:'secret'},
    {path:'export.json',line:-1,column:1},
    {path:'export.json',line:null,column:1},
  ]){
    const tool=createWorkspacePreviewTool({request:async()=>({schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'failed',boundary:testing.BOUNDARY,error:'ODS workspace preview publication failed',errorCode:'invalid_json_artifact',artifactError})});
    const result=await tool.execute('invalid-diagnostic',{relativeDirectory:'project/public'});
    assert.equal(result.details.errorCode,'unavailable');
    assert.doesNotMatch(JSON.stringify(result),/private|secret/);
  }
});

test("bounded lists explicitly report omitted paths and never claim completeness", async () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse({
    publishedPaths: ["assets/app.js"], publishedPathsOmitted: 2,
  }) });
  const result = await tool.execute("bounded", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, undefined);
  assert.match(result.content[0].text, /2 additional published paths omitted/);
  assert.doesNotMatch(result.content[0].text, /complete published file list/);
});

test("legacy receipts remain valid without inventing a file list", async () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse() });
  const result = await tool.execute("legacy", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, undefined);
  assert.match(result.content[0].text, /does not include a file list/);
  assert.match(result.content[0].text, /do not infer that every requested file was published/);
  assert.equal(result.details.publishedPaths, undefined);
});

test("rejects unsafe, unbounded, inconsistent or incomplete file-list receipts", async () => {
  for (const fields of [
    { publishedPaths: ["index.html"] },
    { publishedPathsOmitted: 2 },
    { publishedPaths: ["index.html"], publishedPathsOmitted: 0 },
    { publishedPaths: ["index.html"], publishedPathsOmitted: -1 },
    { publishedPaths: ["index.html"], publishedPathsOmitted: 2.5 },
    { publishedPaths: ["index.html", "index.html"], publishedPathsOmitted: 1 },
    { publishedPaths: ["sources.json", "index.html"], publishedPathsOmitted: 1 },
    { publishedPaths: ["a.txt", "b.txt", "c.txt"], publishedPathsOmitted: 0 },
    { publishedPaths: ["../private.txt"], publishedPathsOmitted: 2 },
    { publishedPaths: ["/private.txt"], publishedPathsOmitted: 2 },
    { publishedPaths: ["a//b.txt"], publishedPathsOmitted: 2 },
    { publishedPaths: ["a\\b.txt"], publishedPathsOmitted: 2 },
    { publishedPaths: ["a\nsecret.txt"], publishedPathsOmitted: 2 },
    { publishedPaths: [42], publishedPathsOmitted: 2 },
    { publishedPaths: null, publishedPathsOmitted: 3 },
    { publishedPaths: Array.from({ length: 33 }, (_, i) => `a${String(i).padStart(2, "0")}.txt`), publishedPathsOmitted: 0, files: 33 },
    { publishedPaths: [Array(20).fill("a".repeat(120)).join("/") + ".txt"], publishedPathsOmitted: 2 },
  ]) {
    const tool = createWorkspacePreviewTool({ request: async () => succeededResponse(fields) });
    const result = await tool.execute("bad-list", { relativeDirectory: "demo-site" });
    assert.equal(result.isError, true, JSON.stringify(fields));
    assert.equal(result.details.status, "failed");
    assert.doesNotMatch(JSON.stringify(result), /private\.txt|secret\.txt/);
  }
});

// tower1 7402eb38 coding journey: `2>&1 > public/test-results.txt` left the
// published file empty and the final answer called it the unittest output.
const FLEET_EMPTY_RECEIPT = {
  relativeDirectory: "fleet-qualification-489210351f87-coding/public",
  publishedPaths: ["index.html", "sources.json", "test-results.txt"], publishedPathsOmitted: 0,
  publishedEmptyPaths: ["test-results.txt"], publishedEmptyPathsOmitted: 0,
};

test("receipt names zero-byte published files without failing publication", async () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse(FLEET_EMPTY_RECEIPT) });
  const result = await tool.execute("fleet-empty", { relativeDirectory: FLEET_EMPTY_RECEIPT.relativeDirectory });
  assert.equal(result.isError, undefined);
  assert.equal(result.details.status, "succeeded");
  assert.deepEqual(result.details.publishedEmptyPaths, ["test-results.txt"]);
  const text = result.content[0].text;
  assert.equal(EMPTY_PUBLISHED_FILES_PREFIX, "Published files that are empty (0 bytes): ");
  assert.ok(text.includes("Published files that are empty (0 bytes): test-results.txt. "), text);
  assert.equal(text.split(EMPTY_PUBLISHED_FILES_PREFIX).length, 2);
  assert.match(text, /Verified browser URL: http:\/\/site-/);
});

test("empty-file note lists every shown path and counts the omitted ones", async () => {
  const tool = createWorkspacePreviewTool({ request: async () => succeededResponse({
    files: 40, publishedPaths: ["app.js", "index.html"], publishedPathsOmitted: 38,
    publishedEmptyPaths: ["app.js", "logs/run.txt"], publishedEmptyPathsOmitted: 3,
  }) });
  const result = await tool.execute("bounded-empty", { relativeDirectory: "demo-site" });
  assert.equal(result.isError, undefined);
  assert.ok(result.content[0].text.includes(`${EMPTY_PUBLISHED_FILES_PREFIX}app.js, logs/run.txt, 3 more. `));
});

test("no empty-file note when nothing is empty or the host sent no empty-file list", async () => {
  for (const fields of [
    { publishedPaths: ["index.html", "test-results.txt"], publishedPathsOmitted: 1,
      publishedEmptyPaths: [], publishedEmptyPathsOmitted: 0 },
    { publishedPaths: ["index.html", "test-results.txt"], publishedPathsOmitted: 1 },
    {},
  ]) {
    const tool = createWorkspacePreviewTool({ request: async () => succeededResponse(fields) });
    const result = await tool.execute("no-empty", { relativeDirectory: "demo-site" });
    assert.equal(result.isError, undefined, JSON.stringify(fields));
    assert.doesNotMatch(result.content[0].text, /empty \(0 bytes\)/);
  }
});

test("rejects empty-file lists that are unsafe, unbounded or inconsistent with the file list", async () => {
  const paths = { publishedPaths: ["index.html", "sources.json", "test-results.txt"], publishedPathsOmitted: 0 };
  for (const fields of [
    { publishedEmptyPaths: ["test-results.txt"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["test-results.txt"] },
    { ...paths, publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["index.html"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["other.txt"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["test-results.txt", "sources.json"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["sources.json", "sources.json"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["sources.json", "test-results.txt"], publishedEmptyPathsOmitted: 1 },
    { ...paths, publishedEmptyPaths: ["test-results.txt"], publishedEmptyPathsOmitted: -1 },
    { ...paths, publishedEmptyPaths: ["test-results.txt"], publishedEmptyPathsOmitted: 0.5 },
    { ...paths, publishedEmptyPaths: "test-results.txt", publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["../private.txt"], publishedEmptyPathsOmitted: 0 },
    { ...paths, publishedEmptyPaths: ["a\nsecret.txt"], publishedEmptyPathsOmitted: 0 },
    { files: 40, publishedPaths: ["index.html"], publishedPathsOmitted: 39,
      publishedEmptyPaths: Array.from({ length: 33 }, (_, i) => `e${String(i).padStart(2, "0")}.txt`), publishedEmptyPathsOmitted: 0 },
  ]) {
    const tool = createWorkspacePreviewTool({ request: async () => succeededResponse(fields) });
    const result = await tool.execute("bad-empty-list", { relativeDirectory: "demo-site" });
    assert.equal(result.isError, true, JSON.stringify(fields));
    assert.equal(result.details.status, "failed");
    assert.doesNotMatch(JSON.stringify(result), /private\.txt|secret\.txt/);
  }
});

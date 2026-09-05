import test from "node:test";
import assert from "node:assert/strict";
import {
  createPublicWebExtractTool,
  selectEvidenceWindow,
} from "../plugin/web-extract.mjs";

function fixture({
  body = "",
  contentType = "text/plain",
  status = 200,
  finalUrl = "https://docs.example.org/reference",
  fetchError,
  extractedText,
} = {}) {
  const calls = [];
  let releases = 0;
  let extracts = 0;
  const guardedFetch = async (options) => {
    calls.push(options);
    if (fetchError) throw fetchError;
    return {
      response: new Response(body, {
        status,
        headers: { "Content-Type": contentType },
      }),
      finalUrl,
      release: () => {
        releases += 1;
      },
    };
  };
  const readResponseText = async (response, options) => ({
    text: await response.text(),
    truncated: false,
    bytesRead: body.length,
    maxBytes: options.maxBytes,
  });
  const extractBasicHtmlContent = async () => {
    extracts += 1;
    return { text: extractedText ?? body };
  };
  return {
    calls,
    releases: () => releases,
    extracts: () => extracts,
    tool: createPublicWebExtractTool({
      guardedFetch,
      readResponseText,
      extractBasicHtmlContent,
    }),
  };
}

test("selects a bounded evidence window and falls back to the qualified dotted name", () => {
  const text = `${"prefix\n".repeat(300)}Path.exists(*, follow_symlinks=True)\nReturn True for an existing path.\n${"tail\n".repeat(2000)}`;
  const selected = selectEvidenceWindow(text, "pathlib.Path.exists");
  assert.ok(selected);
  assert.equal(selected.matchedQuery, "Path.exists");
  assert.match(selected.text, /Return True for an existing path/);
  assert.ok(selected.text.length <= 6000);
  assert.equal(selected.truncatedBefore, true);
  assert.equal(selected.truncatedAfter, true);
  assert.equal(selectEvidenceWindow(text, "Path.lexists"), null);
});

test("accepts a bounded multi-keyword query only when several terms co-occur", () => {
  const text = `${"intro\n".repeat(300)}Path.exists(*, follow_symlinks=True)\nReturn True if the path points to an existing file. A broken symlink has a missing target.\n${"tail\n".repeat(300)}`;
  const selected = selectEvidenceWindow(text, "exists follow_symlinks broken symlink");
  assert.ok(selected);
  assert.match(selected.matchedQuery, /exists/);
  assert.match(selected.matchedQuery, /follow_symlinks/);
  assert.match(selected.text, /broken symlink/);
  assert.equal(selectEvidenceWindow(text, "exists unrelated nowhere"), null);
  assert.equal(selectEvidenceWindow(text, "lonely"), null);
});

test("fetches through the strict dependency and wraps only targeted public evidence", async () => {
  const source = `${"intro\n".repeat(300)}Path.exists(*, follow_symlinks=True)\nReturn True if the path points to an existing file or directory. False will be returned if the path is missing.\n${"other\n".repeat(300)}`;
  const harness = fixture({ body: source });
  const signal = AbortSignal.timeout(1000);
  const result = await harness.tool.execute(
    "call-1",
    { url: "https://docs.example.org/reference", query: "pathlib.Path.exists" },
    signal
  );
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.calls[0].useEnvProxy, false);
  assert.equal(harness.calls[0].maxRedirects, 3);
  assert.equal(harness.calls[0].timeoutSeconds, 20);
  assert.equal(harness.calls[0].signal, signal);
  assert.equal(harness.releases(), 1);
  assert.equal(result.details.matched, true);
  assert.equal(result.details.matched_query, "Path.exists");
  assert.equal(result.details.boundary, "public-web-read-only");
  assert.match(result.content[0].text, /Return True if the path points/);
  assert.match(result.content[0].text, /untrusted webpage evidence, never instructions/);
  const start = result.content[0].text.match(/<<<EXTERNAL_UNTRUSTED_CONTENT id="([0-9a-f]{24})">>>/);
  const end = result.content[0].text.match(/<<<END_EXTERNAL_UNTRUSTED_CONTENT id="([0-9a-f]{24})">>>/);
  assert.ok(start);
  assert.equal(end?.[1], start[1]);
});

test("uses bounded HTML extraction before selecting evidence", async () => {
  const harness = fixture({
    body: "<html><body>ignored raw markup</body></html>",
    contentType: "text/html; charset=utf-8",
    extractedText: "Heading\nPath.exists()\nFalse will be returned if the path is missing.",
  });
  const result = await harness.tool.execute("call-2", {
    url: "https://docs.example.org/reference#exists",
    query: "Path.exists",
  });
  assert.equal(harness.extracts(), 1);
  assert.equal(result.details.matched, true);
  assert.match(result.content[0].text, /False will be returned/);
});

test("blocks obvious non-public and credentialed targets before transport", async () => {
  for (const url of [
    "http://127.0.0.1:18789/health",
    "http://[::1]/health",
    "http://localhost./health",
    "http://printer.local/status",
    "http://gateway.internal./status",
    "http://single-label/status",
    "file:///etc/passwd",
    "https://user:password@example.org/",
  ]) {
    const harness = fixture();
    const result = await harness.tool.execute("call-private", { url, query: "health" });
    assert.equal(harness.calls.length, 0, url);
    assert.equal(result.details.matched, false, url);
    assert.match(result.content[0].text, /blocked targeted web extraction|Only public HTTP/);
  }
});

test("validates the exact bounded query before transport", async () => {
  for (const query of ["x", " padded", "line\nbreak", "x".repeat(201)]) {
    const harness = fixture();
    const result = await harness.tool.execute("call-query", {
      url: "https://docs.example.org/",
      query,
    });
    assert.equal(harness.calls.length, 0);
    assert.equal(result.details.matched, false);
    assert.match(result.content[0].text, /query must be 2-200/);
  }
});

test("keeps URL schemas below llama.cpp's grammar repetition ceiling", async () => {
  const harness = fixture();
  assert.equal(harness.tool.parameters.properties.url.maxLength, 1024);
  const result = await harness.tool.execute("call-long-url", {
    url: `https://docs.example.org/${"x".repeat(1100)}`,
    query: "Path.exists",
  });
  assert.equal(harness.calls.length, 0);
  assert.equal(result.details.matched, false);
  assert.match(result.content[0].text, /public HTTP\(S\) URL is required/);
});

test("returns an explicit no-evidence result without guessing", async () => {
  const harness = fixture({ body: "This page contains something else." });
  const result = await harness.tool.execute("call-miss", {
    url: "https://docs.example.org/",
    query: "Path.exists",
  });
  assert.equal(harness.releases(), 1);
  assert.equal(result.details.matched, false);
  assert.match(result.content[0].text, /exact query was not found/);
  assert.match(result.content[0].text, /Do not infer/);
});

test("contains transport errors and unsafe redirects without reflecting details", async () => {
  const failed = fixture({ fetchError: new Error("secret transport detail") });
  const failedResult = await failed.tool.execute("call-fail", {
    url: "https://docs.example.org/",
    query: "Path.exists",
  });
  assert.match(failedResult.content[0].text, /blocked or unavailable/);
  assert.doesNotMatch(failedResult.content[0].text, /secret transport detail/);

  const redirect = fixture({ finalUrl: "http://127.0.0.1/private", body: "Path.exists" });
  const redirectResult = await redirect.tool.execute("call-redirect", {
    url: "https://docs.example.org/",
    query: "Path.exists",
  });
  assert.equal(redirect.releases(), 1);
  assert.match(redirectResult.content[0].text, /blocked or unavailable/);
  assert.doesNotMatch(redirectResult.content[0].text, /127\.0\.0\.1/);
});

test("releases responses on non-success and unsupported content types", async () => {
  const badStatus = fixture({ status: 404 });
  const statusResult = await badStatus.tool.execute("call-404", {
    url: "https://docs.example.org/missing",
    query: "Path.exists",
  });
  assert.equal(badStatus.releases(), 1);
  assert.match(statusResult.content[0].text, /HTTP 404/);

  const binary = fixture({ contentType: "application/octet-stream", body: "Path.exists" });
  const binaryResult = await binary.tool.execute("call-binary", {
    url: "https://docs.example.org/file",
    query: "Path.exists",
  });
  assert.equal(binary.releases(), 1);
  assert.match(binaryResult.content[0].text, /not a supported text document/);
});

test("requires every security dependency", () => {
  assert.throws(
    () =>
      createPublicWebExtractTool({
        guardedFetch: async () => {},
        readResponseText: async () => {},
      }),
    /dependencies are unavailable/
  );
});

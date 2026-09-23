import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  createResearchMcpHandler, runResearchMcpServer,
  researchMcpServerContract,
} from "../deploy/agent-comparison/research-mcp-server.mjs";

function configuration(maxCalls = 2) {
  return {
    schemaVersion: 1,
    operation: "pixel-outcome-research-mcp",
    runId: "outcomerun-1786550400001-aaaaaaaaaaaa",
    queueRoot: "/run/pixel/research",
    listenHost: "0.0.0.0",
    listenPort: 8081,
    maxCalls,
    timeoutMilliseconds: 120000,
    readyPath: "/run/pixel-research-output/ready.json",
    receiptPath: "/run/pixel-research-output/receipt.json",
    boundary: researchMcpServerContract.configBoundary,
  };
}

async function server(t, maxCalls = 2) {
  const calls = [];
  const state = {
    sessionId: null, protocolVersion: null, calls: 0, completed: 0, errors: 0,
    active: new Set(), abortController: new AbortController(),
  };
  const listener = createServer(createResearchMcpHandler(configuration(maxCalls), state, {
    async executePixelResearchTool(input, signal, options) {
      calls.push({ input, aborted: signal.aborted, options });
      return { content: [{ type: "text", text: "UNTRUSTED PUBLIC RESEARCH EVIDENCE\n{}" }] };
    },
  }));
  await new Promise((resolve, reject) => {
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", () => resolve());
  });
  t.after(() => new Promise((resolve) => listener.close(resolve)));
  const address = listener.address();
  return { origin: `http://127.0.0.1:${address.port}`, state, calls };
}

async function rpc(origin, body, headers = {}) {
  const response = await fetch(`${origin}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...headers },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  return { response, value: text ? JSON.parse(text) : null };
}

test("Codex Research MCP exposes exactly Pixel's bounded queue tool and carries no public-network authority", async (t) => {
  const fixture = await server(t, 1);
  const initialized = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "codex", version: "0.147.0" } },
  });
  assert.equal(initialized.response.status, 200);
  assert.equal(initialized.value.result.protocolVersion, "2025-06-18");
  assert.deepEqual(initialized.value.result.capabilities, { tools: { listChanged: false } });
  const session = initialized.response.headers.get("mcp-session-id");
  assert.match(session, /^[a-f0-9]{32}$/u);

  const notified = await rpc(fixture.origin, {
    jsonrpc: "2.0", method: "notifications/initialized", params: {},
  }, { "mcp-session-id": session, "mcp-protocol-version": "2025-06-18" });
  assert.equal(notified.response.status, 202);
  assert.equal(notified.value, null);

  const listed = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 2, method: "tools/list", params: {},
  }, { "mcp-session-id": session, "mcp-protocol-version": "2025-06-18" });
  assert.deepEqual(listed.value.result.tools, [researchMcpServerContract.tool]);
  assert.equal(listed.value.result.tools[0].inputSchema.additionalProperties, false);

  const input = {
    query: "public agent safety research", sourceTypes: ["web"], domains: [],
    maxResults: 3, maxSourcesToFetch: 1, maxSourceBytes: 65536,
  };
  const called = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "pixel_research", arguments: input },
  }, { "mcp-session-id": session, "mcp-protocol-version": "2025-06-18" });
  assert.equal(called.value.result.isError, undefined);
  assert.match(called.value.result.content[0].text, /^UNTRUSTED PUBLIC RESEARCH EVIDENCE/u);
  assert.deepEqual(fixture.calls, [{
    input, aborted: false,
    options: { queueRoot: "/run/pixel/research", timeoutMilliseconds: 120000, pollMilliseconds: 25 },
  }]);
  assert.deepEqual(researchMcpServerContract.authority, {
    directPublicNetwork: false, credentials: false, externalWrites: false, publish: false,
    purchase: false, merge: false, deploy: false, policyMutation: false, scopeExpansion: false,
  });

  const exceeded = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "pixel_research", arguments: input },
  }, { "mcp-session-id": session, "mcp-protocol-version": "2025-06-18" });
  assert.equal(exceeded.value.result.isError, true);
  assert.match(exceeded.value.result.content[0].text, /call ceiling reached/u);
  assert.equal(fixture.calls.length, 1);
});

test("Codex Research MCP rejects session, protocol, origin, and tool-surface substitution", async (t) => {
  const fixture = await server(t);
  const initialized = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "codex", version: "0.147.0" } },
  });
  const session = initialized.response.headers.get("mcp-session-id");

  const wrongSession = await rpc(fixture.origin, { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, { "mcp-session-id": "0".repeat(32) });
  assert.equal(wrongSession.response.status, 404);
  const drifted = await rpc(fixture.origin, { jsonrpc: "2.0", id: 3, method: "tools/list", params: {} }, { "mcp-session-id": session, "mcp-protocol-version": "2025-06-18" });
  assert.equal(drifted.response.status, 400);
  const substituted = await rpc(fixture.origin, { jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "web_search", arguments: {} } }, { "mcp-session-id": session, "mcp-protocol-version": "2025-11-25" });
  assert.equal(substituted.value.error.code, -32601);
  const origin = await rpc(fixture.origin, { jsonrpc: "2.0", id: 5, method: "tools/list", params: {} }, { origin: "https://attacker.example", "mcp-session-id": session });
  assert.equal(origin.response.status, 404);
  assert.equal(fixture.calls.length, 0);
});

test("Codex Research MCP fails closed on repeated or unsupported initialization", async (t) => {
  const fixture = await server(t);
  const unsupported = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2099-01-01", capabilities: {}, clientInfo: { name: "codex", version: "future" } },
  });
  assert.equal(unsupported.response.status, 400);
  const initialized = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 2, method: "initialize",
    params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "codex", version: "0.147.0" } },
  });
  assert.equal(initialized.response.status, 200);
  const repeated = await rpc(fixture.origin, {
    jsonrpc: "2.0", id: 3, method: "initialize",
    params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "codex", version: "0.147.0" } },
  });
  assert.equal(repeated.response.status, 400);
});

test("Codex Research MCP withholds its exact lifecycle receipt until graceful stop", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-research-mcp-lifecycle-"));
  try {
    if (process.platform !== "win32") await chmod(root, 0o700);
    const output = join(root, "output");
    await mkdir(output, { mode: 0o700 });
    if (process.platform !== "win32") await chmod(output, 0o700);
    const configPath = resolve(join(root, "config.json"));
    const config = {
      ...configuration(3), readyPath: resolve(join(output, "ready.json")),
      receiptPath: resolve(join(output, "receipt.json")),
    };
    const configBytes = Buffer.from(`${JSON.stringify(config)}\n`, "utf8");
    await writeFile(configPath, configBytes, { mode: 0o600, flag: "wx" });
    if (process.platform !== "win32") await chmod(configPath, 0o600);
    class FixtureServer extends EventEmitter {
      listen(port, host, callback) { this.port = port; this.host = host; queueMicrotask(callback); }
      close(callback) { this.closed = true; queueMicrotask(callback); }
    }
    const fixtureServer = new FixtureServer();
    const times = [new Date("2026-08-13T12:00:00.000Z"), new Date("2026-08-13T12:00:01.000Z")];
    const lifecycle = await runResearchMcpServer(configPath, {
      async readConfig() { return { value: config, bytes: configBytes }; },
      createServer() { return fixtureServer; }, clock() { return times.shift(); },
    });
    const ready = JSON.parse(await readFile(config.readyPath, "utf8"));
    assert.equal(ready.operation, "pixel-outcome-research-mcp-ready");
    await assert.rejects(() => readFile(config.receiptPath), /ENOENT/u);
    lifecycle.state.calls = 1;
    lifecycle.state.completed = 1;
    await lifecycle.stop();
    const receipt = JSON.parse(await readFile(config.receiptPath, "utf8"));
    assert.equal(fixtureServer.closed, true);
    assert.equal(receipt.operation, "pixel-outcome-research-mcp-stopped");
    assert.equal(receipt.calls, 1);
    assert.equal(receipt.completed, 1);
    assert.equal(receipt.configSha256, lifecycle.configSha256);
    assert.deepEqual(receipt.authority, researchMcpServerContract.authority);
  } finally { await rm(root, { recursive: true, force: true }); }
});

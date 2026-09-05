// Pixel Agent host ingress contract tests.
//
// Runs against the importable implementation (no server auto-start). Covers:
//   - unsafe token file/symlink/mode rejection
//   - exact route/methods
//   - request limit
//   - header stripping
//   - forced model
//   - stable hashed user
//   - sanitized errors
//   - fixed docker execFile (mocked)
//   - safe status projection
//   - UDS permissions/path refusal
//   - streaming/nonstream bounds

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import net from "node:net";
import { createHash } from "node:crypto";

import {
  gatewayRuntimeFromConfig,
  readGatewayConfiguration,
  readGatewayToken,
  prepareSocketPath,
  createIngressServer,
  computeSessionUser,
  buildOutgoing,
  configFromEnv,
  validateConfig,
  dockerApps,
  dockerRuntime,
  dockerRouterRuntime,
  writeStatus,
  start,
  gatewayFetch,
} from "../host/pixel_ingress.mjs";

const DIR = path.join(os.tmpdir(), `pixel-ingress-test-${process.pid}-${Date.now()}`);
fs.mkdirSync(DIR, { recursive: true });
const SOCKET = path.join(DIR, "pixel-ingress.sock");
const TOKEN = "test-gateway-token-0123456789abcdef";
const TEST_RUN_ID = "chatcmpl_11111111-2222-4333-8444-555555555555";
const EUID = typeof process.geteuid === "function"
  ? process.geteuid()
  : (typeof process.getuid === "function" ? process.getuid() : 0);
let socketCounter = 0;

// A fake upstream gateway that records exactly what it receives and returns a
// canned completion. Used to assert forced model, header stripping, hashed
// user, and streaming/nonstream bounds.
function fakeGateway({
  onRequest,
  onVerificationRequest,
  verification = { status: "none" },
  abortReplies = [true],
  completionText = "ok",
} = {}) {
  let abortIndex = 0;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const captured = {
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: body ? JSON.parse(body) : null,
      };
      if (req.url === "/pixel-ods/verification") {
        if (onVerificationRequest) onVerificationRequest(captured);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(verification));
        return;
      }
      if (onRequest) onRequest(captured);
      if (req.url === "/pixel-ods/abort") {
        res.writeHead(200, { "Content-Type": "application/json" });
        const aborted = abortReplies[Math.min(abortIndex, abortReplies.length - 1)];
        abortIndex += 1;
        res.end(JSON.stringify({ aborted }));
        return;
      }
      if (req.headers.accept === "text/event-stream") {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write(`data: ${JSON.stringify({ id: TEST_RUN_ID, model: "openclaw/default", created: 1, choices: [{ delta: { content: "a" } }] })}\n\n`);
        res.end("data: [DONE]\n\n");
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ id: TEST_RUN_ID, choices: [{ message: { content: completionText } }] }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, port: server.address().port });
    });
  });
}

function startIngress({ token = TOKEN, gatewayPort, socket, deps } = {}) {
  socket ||= path.join(DIR, `ingress-${++socketCounter}.sock`);
  prepareSocketPath(socket);
  const server = createIngressServer({
    token,
    gatewayPort,
    deps,
  });
  return new Promise((resolve, reject) => {
    server.once("listening", () => resolve(server));
    server.once("error", reject);
    server.listen(socket);
  });
}

function request(server, method, pathname, opts = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        socketPath: server.address(),
        method,
        path: pathname,
        headers: opts.headers || {},
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
      }
    );
    req.on("error", reject);
    if (opts.body !== undefined) req.write(opts.body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

test("configFromEnv applies defaults and overrides", () => {
  const d = configFromEnv({});
  assert.equal(d.socketPath, "/run/ods-pixel/pixel-ingress.sock");
  assert.equal(d.gatewayPort, 18789);
  assert.equal(d.gatewayTokenFile, "/etc/pixel/openclaw.json");
  assert.equal(d.statusIntervalMs, 30000);
  assert.equal(d.ingressGid, null);
  assert.equal(d.odsVersion, "unknown");
  assert.equal(d.appPorts.n8n, 5678);
  assert.equal(d.appPorts.whisper, 9000);
  const o = configFromEnv({
    PIXEL_INGRESS_SOCKET: "/x.sock",
    PIXEL_GATEWAY_PORT: "19000",
    PIXEL_INGRESS_GID: "1234",
    PIXEL_STATUS_INTERVAL_MS: "5000",
    PIXEL_GATEWAY_TOKEN_FILE: "/custom.json",
    PIXEL_ODS_VERSION: "2.6.0-beta.1",
    PIXEL_ODS_N8N_PORT: "15678",
    PIXEL_ODS_WHISPER_PORT: "19000",
  });
  assert.equal(o.socketPath, "/x.sock");
  assert.equal(o.gatewayPort, 19000);
  assert.equal(o.ingressGid, 1234);
  assert.equal(o.statusIntervalMs, 5000);
  assert.equal(o.odsVersion, "2.6.0-beta.1");
  assert.equal(o.appPorts.n8n, 15678);
  assert.equal(o.appPorts.whisper, 19000);
});

test("config rejects invalid ports, intervals, gids, and relative paths", () => {
  const base = configFromEnv({});
  for (const gatewayPort of [0, 65536, 1.5, NaN]) {
    assert.throws(() => validateConfig({ ...base, gatewayPort }), /gateway port/);
  }
  for (const statusIntervalMs of [0, -1, 86400001, 1.5]) {
    assert.throws(() => validateConfig({ ...base, statusIntervalMs }), /status interval/);
  }
  assert.throws(() => validateConfig({ ...base, ingressGid: -1 }), /ingress gid/);
  assert.throws(() => validateConfig({ ...base, odsVersion: "2.6.0\nSECRET=x" }), /ODS version/);
  assert.throws(
    () => validateConfig({ ...base, appPorts: { ...base.appPorts, n8n: 0 } }),
    /application port/
  );
  assert.throws(() => validateConfig({ ...base, socketPath: "relative.sock" }), /socket path/);
});

// ---------------------------------------------------------------------------
// Token file rejection
// ---------------------------------------------------------------------------

test("readGatewayToken refuses symlink token file", () => {
  const target = path.join(DIR, "real.env");
  fs.writeFileSync(target, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o600 });
  const link = path.join(DIR, "link.env");
  try {
    fs.unlinkSync(link);
  } catch {}
  fs.symlinkSync(target, link);
  assert.throws(() => readGatewayToken(link, EUID), /symlink/);
});

test("readGatewayToken refuses non-regular file", () => {
  const dir = path.join(DIR, "tokendir");
  fs.mkdirSync(dir, { recursive: true });
  assert.throws(() => readGatewayToken(dir, EUID), /not a regular file/);
});

test("readGatewayToken refuses group/world-readable modes", () => {
  const f = path.join(DIR, "groupread.env");
  fs.writeFileSync(f, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o640 });
  fs.chmodSync(f, 0o640);
  assert.throws(() => readGatewayToken(f, EUID), /group\/world readable/);
  const w = path.join(DIR, "worldread.env");
  fs.writeFileSync(w, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o604 });
  fs.chmodSync(w, 0o604);
  assert.throws(() => readGatewayToken(w, EUID), /group\/world readable/);
});

test("readGatewayToken refuses owner mismatch", () => {
  const f = path.join(DIR, "otherowner.env");
  fs.writeFileSync(f, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o600 });
  const other = EUID === 0 ? 12345 : 0;
  assert.throws(() => readGatewayToken(f, other), /owner mismatch/);
});

test("readGatewayToken parses only PIXEL_GATEWAY_TOKEN, rejects empty", () => {
  const good = path.join(DIR, "good.env");
  fs.writeFileSync(
    good,
    `OTHER=ignored\nPIXEL_GATEWAY_TOKEN=${TOKEN}\nPIXEL_GATEWAY_TOKEN_SECOND=zz\n`,
    { mode: 0o600 }
  );
  assert.equal(readGatewayToken(good, EUID), TOKEN);
  const empty = path.join(DIR, "empty.env");
  fs.writeFileSync(empty, "PIXEL_GATEWAY_TOKEN=\n", { mode: 0o600 });
  assert.throws(() => readGatewayToken(empty, EUID), /missing or empty/);
});

test("readGatewayToken reads the owner-private OpenClaw gateway token", () => {
  const config = path.join(DIR, "openclaw.json");
  fs.writeFileSync(config, `${JSON.stringify({ gateway: { auth: { token: TOKEN } } })}\n`, { mode: 0o600 });
  assert.equal(readGatewayToken(config, EUID), TOKEN);
  fs.writeFileSync(config, `${JSON.stringify({ gateway: { auth: {} } })}\n`, { mode: 0o600 });
  assert.throws(() => readGatewayToken(config, EUID), /missing or empty/);
});

test("secure OpenClaw configuration projects the concrete selected Pixel runtime", () => {
  const config = path.join(DIR, "openclaw-runtime.json");
  const value = {
    gateway: { auth: { token: TOKEN } },
    agents: { list: [{ id: "pixel", model: "ods-gateway/ods/current" }] },
    models: {
      providers: {
        "ods-gateway": {
          models: [{
            id: "ods/current",
            name: "ODS Current (org/model:variant)",
            contextWindow: 2_000_000,
            maxTokens: 32768,
            reasoning: true,
          }],
        },
      },
    },
  };
  fs.writeFileSync(config, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  assert.deepEqual(gatewayRuntimeFromConfig(value), {
    model: "org/model:variant",
    context_length: 2_000_000,
  });
  assert.deepEqual(readGatewayConfiguration(config, EUID), {
    token: TOKEN,
    runtime: { model: "org/model:variant", context_length: 2_000_000 },
    runtimeAuthoritative: true,
  });
  value.models.providers["ods-gateway"].models[0].name = "unbound different-model";
  assert.equal(gatewayRuntimeFromConfig(value), null);
});

// ---------------------------------------------------------------------------
// Socket path refusal
// ---------------------------------------------------------------------------

test("prepareSocketPath refuses non-socket path", () => {
  const f = path.join(DIR, "notasocket");
  fs.writeFileSync(f, "x");
  assert.throws(() => prepareSocketPath(f), /non-socket path/);
});

test("prepareSocketPath removes only a socket at its own path", () => {
  const s = path.join(DIR, "existing.sock");
  const srv = net.createServer();
  return new Promise((resolve, reject) => {
    srv.listen(s, () => {
      try {
        prepareSocketPath(s);
        assert.equal(fs.existsSync(s), false);
        srv.close();
        resolve();
      } catch (e) {
        srv.close();
        reject(e);
      }
    });
  });
});

// ---------------------------------------------------------------------------
// Route/method behavior
// ---------------------------------------------------------------------------

test("exact routes and methods: health, chat, and cancel work; others 404/405", async () => {
  const gw = await fakeGateway();
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const health = await request(srv, "GET", "/health");
      assert.equal(health.status, 200);
      assert.equal(JSON.parse(health.body).status, "ok");

      const chat = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ model: "anything", messages: [{ role: "user", content: "hi" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(chat.status, 200);

      const cancel = await request(srv, "POST", "/v1/chat/cancel", {
        body: JSON.stringify({ user: "conversation-42" }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(cancel.status, 200);
      assert.deepEqual(JSON.parse(cancel.body), { aborted: true });

      const badMethod = await request(srv, "DELETE", "/health");
      assert.equal(badMethod.status, 405);

      const badCancelMethod = await request(srv, "GET", "/v1/chat/cancel");
      assert.equal(badCancelMethod.status, 405);

      const unknown = await request(srv, "GET", "/v1/models");
      assert.equal(unknown.status, 404);
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => gw.server.close(r));
  }
});

test("cancel validates the raw chat id and forwards only its opaque digest", async () => {
  const captured = [];
  const gw = await fakeGateway({ onRequest: (value) => captured.push(value) });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const chatId = "conversation-99";
      const response = await request(srv, "POST", "/v1/chat/cancel", {
        body: JSON.stringify({ user: chatId }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      assert.deepEqual(JSON.parse(response.body), { aborted: true });
      const digest = createHash("sha256").update(chatId).digest("hex");
      assert.equal(captured.length, 1);
      assert.equal(captured[0].url, "/pixel-ods/abort");
      assert.equal(captured[0].headers.authorization, `Bearer ${TOKEN}`);
      assert.deepEqual(captured[0].body, { user: `ods-${digest}` });

      for (const body of [
        {},
        { user: "../../escape" },
        { user: "safe", extra: true },
        { user: 7 },
      ]) {
        const invalid = await request(srv, "POST", "/v1/chat/cancel", {
          body: JSON.stringify(body),
          headers: { "Content-Type": "application/json" },
        });
        assert.equal(invalid.status, 400);
      }
      assert.equal(captured.length, 1);
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("cancel survives the bounded OpenClaw run-mapping startup race", async () => {
  const captured = [];
  const gw = await fakeGateway({
    onRequest: (value) => captured.push(value),
    abortReplies: [false, false, true],
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/cancel", {
        body: JSON.stringify({ user: "early-stop-chat" }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      assert.deepEqual(JSON.parse(response.body), { aborted: true });
      assert.equal(captured.length, 3);
      assert.ok(captured.every((call) => call.url === "/pixel-ods/abort"));
      assert.deepEqual(
        new Set(captured.map((call) => call.body.user)).size,
        1,
        "every retry must retain the exact opaque user mapping"
      );
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("health fails closed when the Pixel gateway is unreachable", async () => {
  const deps = {
    fetch: async () => { throw new Error("offline"); },
    setTimeout,
    clearTimeout,
  };
  const srv = await startIngress({ gatewayPort: 18789, deps });
  try {
    const health = await request(srv, "GET", "/health");
    assert.equal(health.status, 503);
    assert.deepEqual(JSON.parse(health.body), { status: "unavailable" });
  } finally {
    await new Promise((resolve) => srv.close(resolve));
  }
});

// ---------------------------------------------------------------------------
// Request limit
// ---------------------------------------------------------------------------

test("rejects oversized request body with 413", async () => {
  const gw = await fakeGateway();
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const big = "x".repeat(2 * 1024 * 1024 + 10);
      const res = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ content: big }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(res.status, 413);
      // sanitized: does not echo body
      assert.ok(!res.body.includes("x".repeat(100)));
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => gw.server.close(r));
  }
});

// ---------------------------------------------------------------------------
// Header stripping + forced model + hashed user + unknown field dropping
// ---------------------------------------------------------------------------

test("strips forbidden headers, forces model, hashes user, drops unknown fields", async () => {
  let captured = null;
  const gw = await fakeGateway({ onRequest: (c) => (captured = c) });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const chatId = "conversation-42";
      const res = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({
          model: "evil-model",
          messages: [{ role: "user", content: "hi" }],
          temperature: 0.7,
          top_p: 1,
          max_tokens: 100,
          stream: false,
          stop: ["\n"],
          tools: [{ type: "function", function: { name: "f" } }],
          tool_choice: "auto",
          response_format: { type: "json_object" },
          unknown_field: "SHOULD_NOT_APPEAR",
          metadata: { chat_id: chatId },
        }),
        headers: {
          Authorization: "Bearer inbound-token",
          Cookie: "session=secret",
          "X-Forwarded-For": "1.2.3.4",
          "x-openclaw-user": "evil",
          "Content-Type": "application/json",
        },
      });
      assert.equal(res.status, 200);
      assert.ok(captured, "gateway should have received a request");

      // Header stripping
      assert.equal(captured.headers.authorization, `Bearer ${TOKEN}`);
      assert.ok(!("cookie" in captured.headers), "cookie must be stripped");
      assert.ok(!("x-forwarded-for" in captured.headers), "x-forwarded-for stripped");
      assert.ok(!("x-openclaw-user" in captured.headers), "x-openclaw-user stripped");
      assert.ok(!("x-forwarded-proto" in captured.headers));

      // Forced model + dropped unknown field
      assert.equal(captured.body.model, "openclaw/default");
      assert.ok(!("unknown_field" in captured.body), "unknown field must be dropped");

      // Hashed user
      const digest = createHash("sha256").update(chatId).digest("hex");
      assert.equal(captured.body.user, `ods-${digest}`);
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => gw.server.close(r));
  }
});

test("omits user when no session identifier supplied", () => {
  const out = buildOutgoing(
    { messages: [{ role: "user", content: "x" }], metadata: { unrelated: 1 } },
    computeSessionUser({ messages: [{ role: "user", content: "x" }] })
  );
  assert.ok(!("user" in out), "no user field when no session identifier");
});

test("rejects invalid body (bad types)", () => {
  assert.throws(() => buildOutgoing({ messages: "nope" }, null), /invalid/);
  assert.throws(() => buildOutgoing({ max_tokens: -1 }, null), /invalid/);
  assert.throws(() => buildOutgoing({ stream: "yes" }, null), /invalid/);
});

// ---------------------------------------------------------------------------
// Sanitized errors
// ---------------------------------------------------------------------------

test("returns sanitized JSON error for invalid JSON, never reflects upstream body", async () => {
  const gw = await fakeGateway();
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const res = await request(srv, "POST", "/v1/chat/completions", {
        body: "{not json",
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(res.status, 400);
      const parsed = JSON.parse(res.body);
      assert.ok(parsed.error && parsed.error.message);
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => gw.server.close(r));
  }
});

// ---------------------------------------------------------------------------
// Streaming bounds
// ---------------------------------------------------------------------------

test("streaming client receives terminal SSE from a non-stream gateway completion", async () => {
  let captured = null;
  const gw = await fakeGateway({ onRequest: (c) => (captured = c) });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const res = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ stream: true, messages: [{ role: "user", content: "hi" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(res.status, 200);
      assert.equal(captured.headers.accept, "application/json");
      assert.equal(captured.body.stream, false);
      assert.ok(res.body.includes('"content":"ok"'));
      assert.ok(res.body.includes("[DONE]"));
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => gw.server.close(r));
  }
});

test("non-stream response replaces a false success with host verification truth", async () => {
  let verificationRequest;
  const safeText = "Pixel could not complete this task because verification failed.";
  const gw = await fakeGateway({
    verification: { status: "failed", text: safeText },
    onVerificationRequest: (value) => (verificationRequest = value),
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "claim success" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      const completion = JSON.parse(response.body);
      assert.equal(completion.id, TEST_RUN_ID);
      assert.equal(completion.choices[0].message.content, safeText);
      assert.equal(completion.choices[0].finish_reason, "stop");
      assert.deepEqual(verificationRequest.body, { runId: TEST_RUN_ID });
      assert.equal(verificationRequest.headers.authorization, `Bearer ${TOKEN}`);
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("non-stream response releases authoritative text from passed Operations verification", async () => {
  const verifiedText =
    "Pixel verified these ODS host facts through structurally matched terminal Operations Broker receipts.";
  const gw = await fakeGateway({
    verification: { status: "passed", text: verifiedText },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "report host facts" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      const completion = JSON.parse(response.body);
      assert.equal(completion.id, TEST_RUN_ID);
      assert.equal(completion.choices[0].message.content, verifiedText);
      assert.equal(completion.choices[0].finish_reason, "stop");
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("non-stream response removes only a guard-confirmed superseded wrapped exec warning", async () => {
  const staleWarning =
    "recovery_probe=passed\n\n⚠️ 🛠️ `/run/pixel-ods-control/cancellable-exec.sh " +
    "7a77c125fe1a5a4d…c26f647f11a8df9 " +
    "cHl0aG9uMyAtYyAncmFpc2UgU3lzdGVtRXhpdCg3KSc=` failed";
  const gw = await fakeGateway({
    verification: { status: "none", suppressStaleExecWarning: true },
    completionText: staleWarning,
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "recover" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      assert.equal(
        JSON.parse(response.body).choices[0].message.content,
        "recovery_probe=passed"
      );
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("superseded warning cleanup leaves near matches and unsignalled warnings intact", async () => {
  const warning =
    "still visible\n\n⚠️ 🛠️ `/run/pixel-ods-control/cancellable-exec.sh " +
    "7a77c125fe1a5a4d…c26f647f11a8df9 dHJ1ZQ==` failed";
  for (const variant of [
    { verification: { status: "none" }, completionText: warning },
    {
      verification: { status: "none", suppressStaleExecWarning: true },
      completionText: warning.replace("cancellable-exec.sh", "other-exec.sh"),
    },
  ]) {
    const gw = await fakeGateway(variant);
    try {
      const srv = await startIngress({ gatewayPort: gw.port });
      try {
        const response = await request(srv, "POST", "/v1/chat/completions", {
          body: JSON.stringify({ messages: [{ role: "user", content: "report" }] }),
          headers: { "Content-Type": "application/json" },
        });
        assert.equal(response.status, 200);
        assert.equal(
          JSON.parse(response.body).choices[0].message.content,
          variant.completionText
        );
      } finally {
        await new Promise((resolve) => srv.close(resolve));
      }
    } finally {
      await new Promise((resolve) => gw.server.close(resolve));
    }
  }
});

test("rejects stale exec warning suppression on a failed verification response", async () => {
  const gw = await fakeGateway({
    verification: {
      status: "failed",
      text: "Pixel could not verify the final command.",
      suppressStaleExecWarning: true,
    },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "claim success" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 502);
      assert.equal(JSON.parse(response.body).error.message, "verification state unavailable");
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("streaming response releases a bounded broad Operations report above the legacy 2 KiB cap", async () => {
  const verifiedText = [
    "Pixel verified these ODS host facts through structurally matched terminal Operations Broker receipts.",
    "- Host evidence: " + "x".repeat(8 * 1024),
  ].join("\n");
  const gw = await fakeGateway({
    verification: { status: "passed", text: verifiedText },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({
          stream: true,
          messages: [{ role: "user", content: "report broad host facts" }],
        }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      const frames = response.body
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data: {") && line.endsWith("}"))
        .map((line) => JSON.parse(line.slice("data: ".length)));
      assert.equal(frames[1].choices[0].delta.content, verifiedText);
      assert.equal(response.body.includes('"content":"ok"'), false);
      assert.ok(response.body.includes("[DONE]"));
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("Operations verification text above the bounded 32 KiB cap remains fail-closed", async () => {
  const gw = await fakeGateway({
    verification: { status: "passed", text: "x".repeat(32 * 1024 + 1) },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "report host facts" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 502);
      assert.equal(JSON.parse(response.body).error.message, "verification state unavailable");
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("passed Operations verification remains fail-closed for extra fields", async () => {
  const gw = await fakeGateway({
    verification: {
      status: "passed",
      text: "Verified host facts.",
      untrusted: "must not cross the ingress boundary",
    },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "report host facts" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 502);
      assert.deepEqual(JSON.parse(response.body), {
        error: {
          message: "verification state unavailable",
          type: "pixel_ingress_error",
        },
      });
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("streaming response is buffered and replaced before false content is released", async () => {
  const safeText = "Pixel stopped before verification reached a terminal result.";
  const gw = await fakeGateway({ verification: { status: "pending", text: safeText } });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ stream: true, messages: [{ role: "user", content: "hi" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      assert.equal(response.headers["content-type"], "text/event-stream");
      assert.ok(response.body.includes(safeText));
      assert.equal(response.body.includes('"content":"a"'), false);
      assert.ok(response.body.includes("[DONE]"));
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("streaming response exposes only the host-authoritative clean-context recovery marker", async () => {
  const safeText =
    "Pixel did not submit the requested host or Operations work through the isolated Operations Broker.";
  const gw = await fakeGateway({
    verification: {
      status: "failed",
      text: safeText,
      code: "operations-unavailable-zero-submissions",
    },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({
          stream: true,
          messages: [{ role: "user", content: "inspect ODS" }],
        }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      assert.ok(response.body.includes(safeText));
      assert.ok(response.body.includes(
        '\"pixel\":{\"schemaVersion\":1,\"recovery\":\"clean-context\",\"reason\":\"operations-unavailable-zero-submissions\"}'
      ));
      assert.equal(response.body.includes('\"content\":\"ok\"'), false);
      assert.ok(response.body.includes("[DONE]"));
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("streaming response exposes only a strictly verified workspace preview marker", async () => {
  const sha256 = "a".repeat(64);
  const siteId = `site-${sha256.slice(0, 24)}`;
  const preview = {
    schemaVersion: 1,
    kind: "ods-pixel-workspace-preview",
    relativeDirectory: "demo-website",
    siteId,
    port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`,
    files: 3,
    bytes: 4096,
    sha256,
    entrySha256: "b".repeat(64),
  };
  const safeText = "Pixel published and independently read back the static website.";
  const gw = await fakeGateway({
    verification: { status: "passed", text: safeText, preview },
  });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({
          stream: true,
          messages: [{ role: "user", content: "build and show a website" }],
        }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 200);
      const frames = response.body
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data: {") && line.endsWith("}"))
        .map((line) => JSON.parse(line.slice("data: ".length)));
      assert.equal(frames[1].choices[0].delta.content, safeText);
      assert.deepEqual(frames.at(-1).pixel, { schemaVersion: 1, preview });
      assert.equal(frames.at(-1).choices[0].finish_reason, "stop");
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("workspace preview metadata fails closed for an unverified URL or extra field", async () => {
  const sha256 = "a".repeat(64);
  const siteId = `site-${sha256.slice(0, 24)}`;
  const base = {
    schemaVersion: 1,
    kind: "ods-pixel-workspace-preview",
    relativeDirectory: "demo-website",
    siteId,
    port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`,
    files: 3,
    bytes: 4096,
    sha256,
    entrySha256: "b".repeat(64),
  };
  for (const preview of [
    { ...base, url: "https://attacker.example/" },
    { ...base, modelClaim: true },
    { ...base, relativeDirectory: "../outside" },
    {
      ...base,
      siteId: "site-0123456789abcdef01234567",
      url: "http://site-0123456789abcdef01234567.localhost:9437/site-0123456789abcdef01234567/",
    },
  ]) {
    const gw = await fakeGateway({
      verification: { status: "passed", text: "untrusted", preview },
    });
    try {
      const srv = await startIngress({ gatewayPort: gw.port });
      try {
        const response = await request(srv, "POST", "/v1/chat/completions", {
          body: JSON.stringify({ messages: [{ role: "user", content: "show it" }] }),
          headers: { "Content-Type": "application/json" },
        });
        assert.equal(response.status, 502);
        assert.equal(JSON.parse(response.body).error.message, "verification state unavailable");
      } finally {
        await new Promise((resolve) => srv.close(resolve));
      }
    } finally {
      await new Promise((resolve) => gw.server.close(resolve));
    }
  }
  const gw = await fakeGateway({ verification: { status: "passed", preview: base } });
  try {
    const srv = await startIngress({ gatewayPort: gw.port });
    try {
      const response = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ messages: [{ role: "user", content: "show it" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(response.status, 502);
    } finally {
      await new Promise((resolve) => srv.close(resolve));
    }
  } finally {
    await new Promise((resolve) => gw.server.close(resolve));
  }
});

test("rejects recovery metadata outside the exact failed zero-submission contract", async () => {
  const invalid = [
    { status: "passed", code: "operations-unavailable-zero-submissions" },
    { status: "failed", text: "safe", code: "unknown" },
    {
      status: "failed",
      text: "safe",
      code: "operations-unavailable-zero-submissions",
      extra: true,
    },
  ];
  for (const verification of invalid) {
    const gw = await fakeGateway({ verification });
    try {
      const srv = await startIngress({ gatewayPort: gw.port });
      try {
        const response = await request(srv, "POST", "/v1/chat/completions", {
          body: JSON.stringify({ messages: [{ role: "user", content: "inspect ODS" }] }),
          headers: { "Content-Type": "application/json" },
        });
        assert.equal(response.status, 502);
        assert.equal(JSON.parse(response.body).error.message, "verification state unavailable");
      } finally {
        await new Promise((resolve) => srv.close(resolve));
      }
    } finally {
      await new Promise((resolve) => gw.server.close(resolve));
    }
  }
});

test("closing a silent downstream stream closes the active gateway transport", async () => {
  let markUpstreamClosed;
  const upstreamClosed = new Promise((resolve) => {
    markUpstreamClosed = resolve;
  });
  const upstream = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.flushHeaders();
      res.on("close", () => markUpstreamClosed(!res.writableEnded));
    });
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const ingress = await startIngress({ gatewayPort: upstream.address().port });
  try {
    await new Promise((resolve, reject) => {
      const client = http.request(
        {
          socketPath: ingress.address(),
          method: "POST",
          path: "/v1/chat/completions",
          headers: { "Content-Type": "application/json" },
        },
        (response) => {
          response.once("error", () => {});
          response.destroy();
          resolve();
        }
      );
      client.once("error", (error) => {
        if (error.code === "ECONNRESET") resolve();
        else reject(error);
      });
      client.end(JSON.stringify({
        stream: true,
        messages: [{ role: "user", content: "cancel me" }],
      }));
    });
    assert.equal(
      await Promise.race([
        upstreamClosed,
        new Promise((resolve) => setTimeout(() => resolve(false), 2000)),
      ]),
      true
    );
  } finally {
    await new Promise((resolve) => ingress.close(resolve));
    await new Promise((resolve) => upstream.close(resolve));
  }
});

test("stream headers expose cancellation before gateway response headers exist", async () => {
  let markUpstreamClosed;
  const upstreamClosed = new Promise((resolve) => {
    markUpstreamClosed = resolve;
  });
  const upstream = http.createServer((req, res) => {
    req.resume();
    res.on("close", () => markUpstreamClosed(!res.writableEnded));
    // Deliberately never send response headers. The downstream SSE response
    // must still become cancellable while OpenClaw is starting the model turn.
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const ingress = await startIngress({ gatewayPort: upstream.address().port });
  try {
    await new Promise((resolve, reject) => {
      const client = http.request(
        {
          socketPath: ingress.address(),
          method: "POST",
          path: "/v1/chat/completions",
          headers: { "Content-Type": "application/json" },
        },
        (response) => {
          assert.equal(response.statusCode, 200);
          assert.match(String(response.headers["content-type"]), /^text\/event-stream/);
          response.once("error", () => {});
          response.destroy();
          resolve();
        }
      );
      client.once("error", (error) => {
        if (error.code === "ECONNRESET") resolve();
        else reject(error);
      });
      client.end(JSON.stringify({
        stream: true,
        messages: [{ role: "user", content: "cancel before headers" }],
      }));
    });
    assert.equal(
      await Promise.race([
        upstreamClosed,
        new Promise((resolve) => setTimeout(() => resolve(false), 2000)),
      ]),
      true
    );
  } finally {
    await new Promise((resolve) => ingress.close(resolve));
    await new Promise((resolve) => upstream.close(resolve));
  }
});

test("core gateway transport preserves a delayed response body", async () => {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.flushHeaders();
    res.write('data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n');
    setTimeout(() => res.end("data: [DONE]\n\n"), 250);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    try {
      const response = await gatewayFetch(
        `http://127.0.0.1:${server.address().port}/v1/chat/completions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          signal: controller.signal,
        }
      );
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("content-type"), "text/event-stream");
      const body = await new Response(response.body).text();
      assert.ok(body.includes("[DONE]"));
    } finally {
      clearTimeout(timer);
    }
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("chat waits for delayed gateway headers after the loopback connection succeeds", async () => {
  const armedTimeouts = [];
  const deps = {
    fetch: (url, options) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          if (url.endsWith("/pixel-ods/verification")) {
            resolve(
              new Response(JSON.stringify({ status: "none" }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              })
            );
            return;
          }
          resolve(
            new Response(
              JSON.stringify({ id: TEST_RUN_ID, choices: [{ message: { content: "ok" } }] }),
              { status: 200, headers: { "Content-Type": "application/json" } }
            )
          );
        }, 40);
        options.signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new Error("aborted before delayed headers"));
        }, { once: true });
      }),
    setTimeout: (callback, milliseconds) => {
      armedTimeouts.push(milliseconds);
      if (milliseconds < 100000) return setTimeout(callback, 5);
      return { longBudget: true };
    },
    clearTimeout: (timer) => {
      if (timer && !timer.longBudget) clearTimeout(timer);
    },
  };
  const srv = await startIngress({ gatewayPort: 18789, deps });
  try {
    const response = await request(srv, "POST", "/v1/chat/completions", {
      body: JSON.stringify({ messages: [{ role: "user", content: "slow reply" }] }),
      headers: { "Content-Type": "application/json" },
    });
    assert.equal(response.status, 200);
    assert.deepEqual(armedTimeouts, [1920000]);
  } finally {
    await new Promise((resolve) => srv.close(resolve));
  }
});

test("non-stream response is bounded and sanitized on failure", async () => {
  // Gateway that returns a huge body => ingress caps at 2 MiB and returns
  // a generic 502 rather than reflecting it.
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("x".repeat(3 * 1024 * 1024));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const gwPort = server.address().port;
  try {
    const srv = await startIngress({ gatewayPort: gwPort });
    try {
      const res = await request(srv, "POST", "/v1/chat/completions", {
        body: JSON.stringify({ stream: false, messages: [{ role: "user", content: "hi" }] }),
        headers: { "Content-Type": "application/json" },
      });
      assert.equal(res.status, 502);
      const parsed = JSON.parse(res.body);
      assert.ok(parsed.error && parsed.error.message, "sanitized generic error");
      assert.ok(!parsed.error.message.includes("xxxx"), "must not reflect upstream body");
    } finally {
      await new Promise((r) => srv.close(r));
    }
  } finally {
    await new Promise((r) => server.close(r));
  }
});

// ---------------------------------------------------------------------------
// UDS permissions
// ---------------------------------------------------------------------------

test("start creates a 0660 socket with the requested gid and closes without exiting", async () => {
  const gid = process.getgid();
  const tokenFile = path.join(DIR, "start-token.env");
  const socketPath = path.join(DIR, "permission.sock");
  const statusFile = path.join(DIR, "permission-status.json");
  fs.writeFileSync(tokenFile, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o600 });
  const deps = {
    fetch: async () => ({ status: 503, body: { cancel: async () => {} } }),
    execFile: (_command, _args, _options, callback) => callback(new Error("unavailable")),
  };
  const handle = await start({
    socketPath,
    gatewayTokenFile: tokenFile,
    gatewayPort: 18999,
    statusFile,
    statusIntervalMs: 60000,
    ingressGid: gid,
    odsVersion: "2.6.0",
  }, { deps, euid: EUID });
  const socketStat = fs.statSync(socketPath);
  assert.equal(socketStat.mode & 0o777, 0o660);
  assert.equal(socketStat.gid, gid);
  await handle.close();
  assert.equal(fs.existsSync(socketPath), false);
});

// ---------------------------------------------------------------------------
// Status projection: fixed docker execFile (mocked) + safe projection
// ---------------------------------------------------------------------------

test("status projection uses fixed docker execFile and allows only allowlisted names", async () => {
  const fakeDocker = (cmd, args, opts, callback) => {
    assert.equal(cmd, "docker");
    assert.deepEqual(args, ["ps", "--all", "--format", "{{json .}}"]);
    assert.ok(opts.timeout > 0, "must have explicit timeout");
    const lines = [
      { Names: "ods-pixel-edge", Status: "Up 5 minutes" },
      { Names: "/evil-container", Status: "Up 1 day (healthy)" },
      { Names: "ods-open-webui", Status: "Up (healthy)" },
      { Names: "ods-dashboard", Status: "Up (unhealthy)" },
      { Names: "ods-qdrant", Status: "Up (health: starting)" },
      { Names: "ods-n8n", Status: "Exited (0)" },
    ].map((x) => JSON.stringify(x));
    callback(null, lines.join("\n") + "\n", "");
  };
  const apps = await dockerApps({ execFile: fakeDocker });
  assert.deepEqual(apps, [
    { name: "ods-dashboard", status: "unhealthy" },
    { name: "ods-n8n", status: "stopped" },
    { name: "ods-open-webui", status: "healthy" },
    { name: "ods-pixel-edge", status: "running" },
    { name: "ods-qdrant", status: "starting" },
  ]);
  assert.equal(JSON.stringify(apps).includes("evil"), false);
  assert.equal(JSON.stringify(apps).includes("5 minutes"), false);
});

test("runtime projection uses a fixed inspect command and returns only model basename and context", async () => {
  const fakeDocker = (cmd, args, opts, callback) => {
    assert.equal(cmd, "docker");
    assert.deepEqual(args, [
      "inspect", "ods-llama-server", "--format", "{{json .Config.Cmd}}",
    ]);
    assert.ok(opts.timeout > 0, "must have explicit timeout");
    callback(
      null,
      JSON.stringify(["--model", "/models/Qwen3.5-9B-Q4_K_M.gguf", "--ctx-size", "32768"]),
      ""
    );
  };
  assert.deepEqual(await dockerRuntime({ execFile: fakeDocker }), {
    model: "Qwen3.5-9B-Q4_K_M.gguf",
    context_length: 32768,
  });
});

test("runtime projection rejects paths, malformed contexts, and non-JSON output", async () => {
  for (const command of [
    ["--model", "/private/model.gguf", "--ctx-size", "32768"],
    ["--model", "/models/../secret", "--ctx-size", "32768"],
    ["--model", "/models/model.gguf", "--ctx-size", "0"],
    "not-json",
  ]) {
    const fakeDocker = (_cmd, _args, _opts, callback) => {
      const output = command === "not-json" ? command : JSON.stringify(command);
      callback(null, output, "");
    };
    assert.equal(await dockerRuntime({ execFile: fakeDocker }), null);
  }
});

test("router runtime projection returns the exact active stable-alias target", async () => {
  const fakeDocker = (cmd, args, opts, callback) => {
    assert.equal(cmd, "docker");
    assert.deepEqual(args, [
      "exec", "ods-model-router", "cat", "/state/model-state.json",
    ]);
    assert.ok(opts.timeout > 0, "must have explicit timeout");
    callback(null, JSON.stringify({
      schema: "ods.model-state.v1",
      active: {
        routeSeq: 4,
        runtimeModelId: "Qwen3.5-2B-Q4_K_M.gguf",
        publicModel: "ods/current",
        contextLength: 65536,
      },
    }), "");
  };
  assert.deepEqual(await dockerRouterRuntime({ execFile: fakeDocker }), {
    model: "Qwen3.5-2B-Q4_K_M.gguf",
    context_length: 65536,
  });
});

test("router runtime projection rejects malformed or inactive state", async () => {
  for (const state of [
    { schema: "wrong", active: {} },
    { schema: "ods.model-state.v1", active: null },
    { schema: "ods.model-state.v1", active: { routeSeq: 1, runtimeModelId: "../secret", publicModel: "ods/current", contextLength: 65536 } },
    { schema: "ods.model-state.v1", active: { routeSeq: 1, runtimeModelId: "model", publicModel: "other", contextLength: 65536 } },
    "not-json",
  ]) {
    const fakeDocker = (_cmd, _args, _opts, callback) => {
      callback(null, state === "not-json" ? state : JSON.stringify(state), "");
    };
    assert.equal(await dockerRouterRuntime({ execFile: fakeDocker }), null);
  }
});

test("status keeps app health when optional runtime inspection is unavailable", async () => {
  const statusFile = path.join(DIR, "runtime-unavailable-status.json");
  const fakeDocker = (_cmd, args, _opts, callback) => {
    if (args[0] === "ps") {
      callback(null, `${JSON.stringify({ Names: "ods-dashboard", Status: "Up (healthy)" })}\n`, "");
      return;
    }
    callback(new Error("runtime unavailable"));
  };
  const projection = await writeStatus(
    true,
    18999,
    statusFile,
    "2.6.0",
    {
      fetch: async () => ({ status: 503, body: { cancel: async () => {} } }),
      execFile: fakeDocker,
      setTimeout,
      clearTimeout,
    }
  );
  assert.equal(projection.schema_version, 2);
  assert.equal(projection.ods_version, "2.6.0");
  assert.equal(projection.docker, "ok");
  assert.equal(projection.online_apps, 1);
  assert.equal(projection.app_ports.n8n, 5678);
  assert.equal(projection.runtime, null);
  assert.deepEqual(projection.apps, [{ name: "ods-dashboard", status: "healthy" }]);
});

test("status prefers the selected Pixel runtime over the local Docker model", async () => {
  const statusFile = path.join(DIR, "configured-runtime-status.json");
  const fakeDocker = (_cmd, args, _opts, callback) => {
    if (args[0] === "ps") {
      callback(null, `${JSON.stringify({ Names: "ods-dashboard", Status: "Up (healthy)" })}\n`, "");
      return;
    }
    callback(
      null,
      JSON.stringify(["--model", "/models/local-model.gguf", "--ctx-size", "32768"]),
      ""
    );
  };
  const projection = await writeStatus(
    true,
    18999,
    statusFile,
    "2.6.0",
    {
      fetch: async () => ({ status: 200, body: { cancel: async () => {} } }),
      execFile: fakeDocker,
      setTimeout,
      clearTimeout,
    },
    configFromEnv({}).appPorts,
    { model: "org/model:variant", context_length: 2_000_000 }
  );
  assert.deepEqual(projection.runtime, {
    model: "org/model:variant",
    context_length: 2_000_000,
  });
});

test("status uses the active model-router target when authoritative Pixel metadata is unavailable", async () => {
  const statusFile = path.join(DIR, "router-runtime-status.json");
  const fakeDocker = (_cmd, args, _opts, callback) => {
    if (args[0] === "ps") {
      callback(null, `${JSON.stringify({ Names: "ods-dashboard", Status: "Up (healthy)" })}\n`, "");
      return;
    }
    if (args[0] === "exec") {
      callback(null, JSON.stringify({
        schema: "ods.model-state.v1",
        active: {
          routeSeq: 4,
          runtimeModelId: "Qwen3.5-2B-Q4_K_M.gguf",
          publicModel: "ods/current",
          contextLength: 65536,
        },
      }), "");
      return;
    }
    callback(new Error("unexpected fallback inspection"));
  };
  const projection = await writeStatus(
    true,
    18999,
    statusFile,
    "2.6.0",
    {
      fetch: async () => ({ status: 200, body: { cancel: async () => {} } }),
      execFile: fakeDocker,
      setTimeout,
      clearTimeout,
    },
    configFromEnv({}).appPorts
  );
  assert.deepEqual(projection.runtime, {
    model: "Qwen3.5-2B-Q4_K_M.gguf",
    context_length: 65536,
  });
});

test("status keeps the authoritative Pixel runtime during a transactional router-state lag", async () => {
  const statusFile = path.join(DIR, "transactional-runtime-status.json");
  const fakeDocker = (_cmd, args, _opts, callback) => {
    if (args[0] === "ps") {
      callback(null, `${JSON.stringify({ Names: "ods-dashboard", Status: "Up (healthy)" })}\n`, "");
      return;
    }
    if (args[0] === "exec") {
      callback(null, JSON.stringify({
        schema: "ods.model-state.v1",
        active: {
          routeSeq: 15,
          runtimeModelId: "Qwen3.5-9B-Q4_K_M.gguf",
          publicModel: "ods/current",
          contextLength: 32768,
        },
      }), "");
      return;
    }
    callback(new Error("authoritative Pixel metadata must avoid stale router inspection"));
  };
  const projection = await writeStatus(
    true,
    18999,
    statusFile,
    "2.6.0",
    {
      fetch: async () => ({ status: 200, body: { cancel: async () => {} } }),
      execFile: fakeDocker,
      setTimeout,
      clearTimeout,
    },
    configFromEnv({}).appPorts,
    { model: "Qwen3.5-2B-Q4_K_M.gguf", context_length: 8192 }
  );
  assert.deepEqual(projection.runtime, {
    model: "Qwen3.5-2B-Q4_K_M.gguf",
    context_length: 8192,
  });
});

test("start retains a selected remote runtime when Docker is unavailable", async () => {
  const configFile = path.join(DIR, "remote-start-openclaw.json");
  fs.writeFileSync(configFile, `${JSON.stringify({
    gateway: { auth: { token: TOKEN } },
    agents: { list: [{ id: "pixel", model: "ods-gateway/ods/current" }] },
    models: { providers: { "ods-gateway": { models: [{
      id: "ods/current", name: "ODS Current (remote/model)",
      contextWindow: 131072, maxTokens: 16384, reasoning: false,
    }] } } },
  })}\n`, { mode: 0o600 });
  const sock = path.join(DIR, "remote-start.sock");
  const statusFile = path.join(DIR, "remote-start-status.json");
  const h = await start({
    socketPath: sock,
    gatewayTokenFile: configFile,
    gatewayPort: 18999,
    statusFile,
    statusIntervalMs: 60000,
    ingressGid: null,
    odsVersion: "2.6.0",
  }, {
    deps: {
      fetch: async () => ({ status: 503, body: { cancel: async () => {} } }),
      execFile: (_command, _args, _options, callback) => callback(new Error("docker unavailable")),
    },
    euid: EUID,
  });
  try {
    const projection = JSON.parse(fs.readFileSync(statusFile, "utf8"));
    assert.equal(projection.docker, "unavailable");
    assert.deepEqual(projection.runtime, {
      model: "remote/model",
      context_length: 131072,
    });
  } finally {
    await h.close();
  }
});

test("start writes a safe status projection when docker is unavailable", async () => {
  const fakeEnvFile = path.join(DIR, "env.env");
  fs.writeFileSync(fakeEnvFile, `PIXEL_GATEWAY_TOKEN=${TOKEN}\n`, { mode: 0o600 });
  const sock = path.join(DIR, "start.sock");
  const statusFile = path.join(DIR, "start-status.json");
  const deps = {
    fetch: async () => ({ status: 503, body: { cancel: async () => {} } }),
    execFile: (_command, _args, _options, callback) => callback(new Error("docker unavailable")),
  };
  const h = await start({
    socketPath: sock,
    gatewayTokenFile: fakeEnvFile,
    gatewayPort: 18999,
    statusFile,
    statusIntervalMs: 60000,
    ingressGid: null,
    odsVersion: "2.6.0",
  }, { deps, euid: EUID });
  const proj = JSON.parse(fs.readFileSync(statusFile, "utf8"));
  assert.ok(proj.timestamp);
  assert.equal(proj.ingress_ready, true);
  assert.equal(proj.gateway_reachable, false);
  assert.equal(proj.docker, "unavailable");
  assert.equal(proj.schema_version, 2);
  assert.equal(proj.ods_version, "2.6.0");
  assert.equal(proj.online_apps, 0);
  assert.equal(proj.app_ports.n8n, 5678);
  assert.equal(proj.runtime, null);
  assert.deepEqual(proj.apps, []);
  assert.equal(fs.statSync(statusFile).mode & 0o777, 0o640);
  await h.close();
});

test("upstream error and wrong content type never reflect an upstream secret", async () => {
  for (const variant of ["error", "wrong-type"]) {
    const upstream = http.createServer((_req, res) => {
      if (variant === "error") res.writeHead(500, { "Content-Type": "application/json" });
      else res.writeHead(200, { "Content-Type": "text/plain" });
      res.end("secret-upstream-body-/private/token");
    });
    await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    const ingress = await startIngress({ gatewayPort: upstream.address().port });
    try {
      const response = await request(ingress, "POST", "/v1/chat/completions", {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [{ role: "user", content: "hi" }] }),
      });
      assert.equal(response.status, 502);
      assert.equal(response.body.includes("secret-upstream"), false);
      assert.equal(response.body.includes("private/token"), false);
    } finally {
      await new Promise((resolve) => ingress.close(resolve));
      await new Promise((resolve) => upstream.close(resolve));
    }
  }
});

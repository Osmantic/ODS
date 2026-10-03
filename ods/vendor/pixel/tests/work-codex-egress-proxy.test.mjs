import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import net from "node:net";
import test from "node:test";

import { createWorkCodexEgressProxy, isPublicEgressAddress, parseBoundWorkCodexEgressPolicy, parseWorkCodexConnectRequest, WorkCodexEgressProxyError } from "../deploy/work-codex-provider/egress-proxy.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const boundary = "Deployment-private Codex egress proxy policy. The worker has only an internal Docker network and can reach only this proxy; the proxy permits CONNECT to the exact reviewed OpenAI host list on port 443 and stores no request or response body.";
function policy(overrides = {}) { return { $schema: "https://osmantic.com/pixel/schemas/work-codex-egress-policy-v1.schema.json", schemaVersion: 1, mode: "exact-connect-host-allowlist", allowedHosts: ["chatgpt.com"], allowedPorts: [443], denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, denyConnectToUnlistedHosts: true, maxConcurrentTunnels: 4, maxTunnelBytes: 1048576, connectTimeoutSeconds: 2, idleTimeoutSeconds: 5, logRequestBodies: false, logResponseBodies: false, boundary, ...overrides }; }
const request = (host = "chatgpt.com", port = 443, extras = "") => Buffer.from(`CONNECT ${host}:${port} HTTP/1.1\r\nHost: ${host}:${port}\r\n${extras}\r\n`, "ascii");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");

async function listen(server) { await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); }); return server.address().port; }
async function close(server) { await new Promise((resolve) => server.close(resolve)); }
async function exchange(port, bytes, expected = /HTTP\/1\.1/u) {
  return new Promise((resolve, reject) => { const socket = net.createConnection({ host: "127.0.0.1", port }); const chunks = []; socket.setTimeout(3000, () => socket.destroy(new Error("timeout"))); socket.on("connect", () => socket.write(bytes)); socket.on("data", (chunk) => { chunks.push(chunk); if (expected.test(Buffer.concat(chunks).toString("utf8"))) { socket.destroy(); resolve(Buffer.concat(chunks)); } }); socket.on("error", reject); socket.on("close", () => { if (chunks.length && expected.test(Buffer.concat(chunks).toString("utf8"))) resolve(Buffer.concat(chunks)); }); });
}

test("CONNECT parser accepts only one exact reviewed host and no credential-bearing or ambiguous headers", () => {
  assert.deepEqual(parseWorkCodexConnectRequest(request(), policy()), { host: "chatgpt.com", port: 443 });
  for (const value of [
    request("api.openai.com"), request("chatgpt.com", 80), request("127.0.0.1"), Buffer.from("GET https://chatgpt.com/ HTTP/1.1\r\nHost: chatgpt.com\r\n\r\n"),
    request("chatgpt.com", 443, "Proxy-Authorization: Basic private"), request("chatgpt.com", 443, "Host: chatgpt.com:443\r\nHost: chatgpt.com:443"), request("CHATGPT.com"),
  ]) assert.throws(() => parseWorkCodexConnectRequest(value, policy()), WorkCodexEgressProxyError);
});

test("public-address classifier rejects local, private, reserved, documentation, multicast, and mapped targets", () => {
  for (const value of ["0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1", "169.254.1.1", "172.16.0.1", "192.168.1.1", "192.0.2.1", "198.51.100.1", "203.0.113.1", "224.0.0.1", "::", "::1", "fc00::1", "fe80::1", "ff02::1", "2001:db8::1", "2001:0db8::1", "::ffff:127.0.0.1", "0:0:0:0:0:ffff:7f00:1"]) assert.equal(isPublicEgressAddress(value), false, value);
  for (const value of ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"]) assert.equal(isPublicEgressAddress(value), true, value);
});

test("proxy startup accepts only canonical policy bytes bound to the inspected hash", () => {
  const value = canonical(policy()), hash = sha256(value);
  assert.deepEqual(parseBoundWorkCodexEgressPolicy(Buffer.from(value), hash), policy());
  assert.deepEqual(parseBoundWorkCodexEgressPolicy(Buffer.from(`${value}\n`), hash), policy());
  for (const [bytes, expected] of [[Buffer.from(`${value}\n `), hash], [Buffer.from(value.replace("chatgpt.com", "api.openai.com")), hash], [Buffer.from(value), "0".repeat(64)], [Buffer.from([0xff, 0xfe]), hash]]) assert.throws(() => parseBoundWorkCodexEgressPolicy(bytes, expected), WorkCodexEgressProxyError);
});

test("proxy pins a vetted DNS result, preserves immediate TLS bytes, and never receives an unlisted target", async (t) => {
  const upstream = net.createServer((socket) => socket.pipe(socket)); await listen(upstream); t.after(() => close(upstream));
  const seen = [], proxy = createWorkCodexEgressProxy({ policy: policy(), resolver: async (host) => { seen.push(host); return [{ address: "93.184.216.34", family: 4 }]; }, connectFactory: () => net.createConnection({ host: "127.0.0.1", port: upstream.address().port }) });
  const port = await listen(proxy); t.after(() => close(proxy)); const response = await exchange(port, Buffer.concat([request(), Buffer.from("immediate-tls-fixture")]), /immediate-tls-fixture/u);
  assert.match(response.toString("utf8"), /200 Connection Established/u); assert.match(response.toString("utf8"), /immediate-tls-fixture/u); assert.deepEqual(seen, ["chatgpt.com"]);
  const denied = await exchange(port, request("api.openai.com"), /403 Forbidden/u); assert.match(denied.toString("utf8"), /403 Forbidden/u); assert.deepEqual(seen, ["chatgpt.com"]);
});

test("private or mixed DNS answers and oversized headers fail before an upstream connection", async (t) => {
  let connections = 0; const proxy = createWorkCodexEgressProxy({ policy: policy(), resolver: async () => [{ address: "93.184.216.34", family: 4 }, { address: "127.0.0.1", family: 4 }], connectFactory: () => { connections += 1; return net.createConnection({ host: "127.0.0.1", port: 9 }); } });
  const port = await listen(proxy); t.after(() => close(proxy)); assert.match((await exchange(port, request(), /403 Forbidden/u)).toString("utf8"), /403 Forbidden/u); assert.equal(connections, 0);
  const oversized = Buffer.from(`CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nUser-Agent: ${"x".repeat(9000)}\r\n\r\n`); assert.match((await exchange(port, oversized, /431 Request/u)).toString("utf8"), /431 Request/u); assert.equal(connections, 0);
});

test("512 request mutations cannot widen the exact CONNECT grammar", () => {
  for (let index = 0; index < 512; index += 1) {
    const variants = [
      `CONNECT chatgpt.com:${444 + index} HTTP/1.1\r\nHost: chatgpt.com:${444 + index}\r\n\r\n`,
      `CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\nX-${index}: widened\r\n\r\n`,
      `CONNECT 127.0.0.${index % 255}:443 HTTP/1.1\r\nHost: 127.0.0.${index % 255}:443\r\n\r\n`,
      `connect chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nMutation: ${index}\r\n\r\n`,
    ];
    assert.throws(() => parseWorkCodexConnectRequest(Buffer.from(variants[index % variants.length]), policy()), WorkCodexEgressProxyError, `mutation ${index}`);
  }
});

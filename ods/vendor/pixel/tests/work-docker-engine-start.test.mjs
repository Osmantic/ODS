import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { chmod, mkdtemp, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { classifyStartResult, dockerEngineStart, secureSocketIdentity } from "../deploy/work-controller/docker-engine-start.mjs";

const digest = (character) => character.repeat(64);
const CID = digest("1");
const OTHER = digest("a");

async function socketFixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-docker-socket-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const socketPath = join(root, "docker.sock");
  return { root, socketPath };
}

function listenOnce(socketPath, handler) {
  const server = http.createServer(handler);
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, () => resolve(server));
  });
}

async function reviewedIdentityOf(socketPath) {
  const identity = await secureSocketIdentity(socketPath);
  assert.ok(identity, "the fake socket must yield a proven endpoint identity");
  return identity.identitySha256;
}

test("docker start local fake socket: exact reviewed empty 204 is a started transition", async (t) => {
  const fx = await socketFixture(t);
  const server = await listenOnce(fx.socketPath, (req, res) => { res.writeHead(204); res.end(); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const started = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(started.status, 204);
  assert.equal(started.ambiguous, false);
  assert.equal(started.ok, true);
  assert.equal(started.endpointIdentity, reviewedIdentity, "the 204 receipt must carry the reviewed endpoint identity");
  assert.match(started.endpointIdentity, /^[a-f0-9]{64}$/u);
  assert.equal(classifyStartResult(started).status, "started");
});

test("docker start local fake socket: exact reviewed empty 304 is already-started (no-op)", async (t) => {
  const fx = await socketFixture(t);
  const server = await listenOnce(fx.socketPath, (req, res) => { res.writeHead(304); res.end(); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const noop = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(noop.status, 304);
  assert.equal(noop.ambiguous, false);
  assert.equal(noop.body, "");
  assert.equal(noop.endpointIdentity, reviewedIdentity);
  assert.equal(classifyStartResult(noop).status, "already-started");
});

// A raw HTTP 204/304 that carries a nonempty body is not a clean definite
// outcome. Node's HTTP client rejects such a body (parser/response error), so
// the start resolves ambiguous — never a definite receipt. The case is asserted
// (not skipped/commented away): a nonempty 204/304 can never authorize a start.
for (const status of [204, 304]) {
  test(`docker start local fake socket: a raw nonempty ${status} fails closed as ambiguous (never a definite receipt)`, async (t) => {
    const fx = await socketFixture(t);
    const reason = status === 204 ? "No Content" : "Not Modified";
    const payload = `HTTP/1.1 ${status} ${reason}\r\nContent-Length: 11\r\n\r\nshould-not-be-here`;
    const server = net.createServer((sock) => { sock.on("data", () => sock.write(payload)); });
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(fx.socketPath, resolve); });
    t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
    const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
    const result = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
    assert.equal(result.ambiguous, true, `a raw nonempty ${status} must fail closed as ambiguous`);
    assert.match(result.reason, /errored after|aborted before|closed before|request errored|nonempty response body/u);
  });
}

test("docker start local fake socket: an unexpected status is ambiguous, not a definite outcome", async (t) => {
  const fx = await socketFixture(t);
  const server = await listenOnce(fx.socketPath, (req, res) => { res.writeHead(200); res.end("ok"); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const result = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(result.ambiguous, true, "an unexpected status must fail closed as ambiguous");
  assert.equal(classifyStartResult(result).status, "ambiguous");
});

test("docker start local fake socket: oversized body is ambiguous and aborted", async (t) => {
  const fx = await socketFixture(t);
  const big = Buffer.alloc(128 * 1024, 0x41);
  const server = await listenOnce(fx.socketPath, (req, res) => { res.writeHead(200); res.write(big); res.end(); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const result = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(result.ambiguous, true, "an oversized response must fail closed as ambiguous");
  assert.match(result.reason, /exceeded the bounded size|errored after a possible commit/u);
});

test("docker start local fake socket: invalid timeout bounds are rejected", async () => {
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 999, reviewedIdentity: OTHER }), /timeout is out of range/u);
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 120001, reviewedIdentity: OTHER }), /timeout is out of range/u);
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 1.5, reviewedIdentity: OTHER }), /timeout is out of range/u);
});

test("docker start requires an exact 64-hex reviewed endpoint identity (never optional/null)", async () => {
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 5000 }), /exact reviewed endpoint identity/u);
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 5000, reviewedIdentity: "unix:/var/run/docker.sock" }), /exact reviewed endpoint identity/u);
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 5000, reviewedIdentity: "short" }), /exact reviewed endpoint identity/u);
  await assert.rejects(dockerEngineStart({ containerId: CID, socketPath: "/tmp/pixel-x.sock", timeoutMs: 5000, reviewedIdentity: OTHER.toUpperCase() }), /exact reviewed endpoint identity/u);
});

test("docker start local fake socket: a request error/abort after a possible commit is ambiguous", async (t) => {
  const fx = await socketFixture(t);
  const server = await listenOnce(fx.socketPath, (req, res) => { req.socket.destroy(); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const result = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(result.ambiguous, true, "a request error after a possible commit must fail closed as ambiguous");
  assert.match(result.reason, /errored after|aborted before|closed before|request errored/u);
});

function boundedRace(promise, message, boundMs) {
  let timer = null;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), boundMs);
  });
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

test("docker start local fake socket: a valid in-range request timeout settles promptly as ambiguous (never a definite receipt)", async (t) => {
  const fx = await socketFixture(t);
  // The fake server accepts the start request but never responds, so only the
  // in-range request timeout can settle the start.
  const server = await listenOnce(fx.socketPath, () => { /* accept, never respond */ });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const started = Date.now();
  const result = await boundedRace(
    dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 1000, reviewedIdentity }),
    "docker start did not settle promptly on an in-range request timeout",
    8000,
  );
  assert.ok(Date.now() - started < 8000, "the request timeout must settle promptly");
  assert.equal(result.ambiguous, true, "a request timeout after a possible commit must fail closed as ambiguous");
  assert.notEqual(result.status, 204, "a timed-out start must never yield a definite started receipt");
  assert.notEqual(result.status, 304, "a timed-out start must never yield a definite no-op receipt");
});

test("docker start local fake socket: an early/partial response close/abort before a clean end settles promptly as ambiguous (never definite)", async (t) => {
  const fx = await socketFixture(t);
  // Send a partial HTTP status line (a would-be 204) then abort the connection
  // before the response can complete a clean 'end'. The client has already
  // received some response bytes, so this exercises response aborted/close
  // settlement rather than a plain request reset.
  const payload = "HTTP/1.1 204 No Content\r\n";
  const server = net.createServer((sock) => {
    sock.once("data", () => {
      sock.write(payload);
      setTimeout(() => sock.destroy(), 20);
    });
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(fx.socketPath, resolve); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  const result = await boundedRace(
    dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity }),
    "docker start did not settle promptly on a partial response close",
    8000,
  );
  assert.equal(result.ambiguous, true, "a partial response closed before a clean end must fail closed as ambiguous");
  assert.notEqual(result.status, 204, "an aborted 204 must never yield a definite started receipt");
  assert.notEqual(result.status, 304, "an aborted 304 must never yield a definite no-op receipt");
});

test("docker start local fake socket: substituting the socket path mid-request (inode change) is ambiguous", async (t) => {
  const fx = await socketFixture(t);
  let secondServer = null;
  const server = await listenOnce(fx.socketPath, async (req, res) => {
    // Rebind a second fake server on the same path while the first connection
    // is still open so the before/after inode identity differs.
    await unlink(fx.socketPath);
    secondServer = await listenOnce(fx.socketPath, (req2, res2) => { res2.writeHead(204); res2.end(); });
    res.writeHead(204);
    res.end();
  });
  const reviewedIdentity = await reviewedIdentityOf(fx.socketPath);
  t.after(async () => {
    await new Promise((r) => { server.closeAllConnections?.(); server.close(r); });
    if (secondServer) await new Promise((r) => { secondServer.closeAllConnections?.(); secondServer.close(r); });
  });
  const result = await dockerEngineStart({ containerId: CID, socketPath: fx.socketPath, timeoutMs: 5000, reviewedIdentity });
  assert.equal(result.ambiguous, true, "a socket endpoint identity change during the request must fail closed as ambiguous");
  assert.match(result.reason, /identity changed during the request/u);
});

test("docker socket identity is not derivable from a symlinked or non-socket path", async (t) => {
  const { root, socketPath } = await socketFixture(t);
  const real = join(root, "real-socket");
  const server = await listenOnce(real, (req, res) => { res.writeHead(204); res.end(); });
  t.after(() => new Promise((r) => { server.closeAllConnections?.(); server.close(r); }));
  const link = join(root, "link.sock");
  await symlink(real, link);
  assert.equal(await secureSocketIdentity(link), null, "a symlinked socket leaf must not be accepted as endpoint identity");
  const notSocket = join(root, "not-a-socket");
  await writeFile(notSocket, "", { mode: 0o600 });
  assert.equal(await secureSocketIdentity(notSocket), null, "a non-socket path must not be accepted as endpoint identity");
});

test("classifyStartResult rejects closed-shape and incoherent start results", () => {
  const good = { ok: true, transition: true, ambiguous: false, status: 204, body: "", containerId: CID, endpointIdentity: OTHER };
  assert.equal(classifyStartResult(good).status, "started");
  // Extra key is not a closed definite shape.
  assert.equal(classifyStartResult({ ...good, extra: 1 }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, reason: "shadow" }).status, "ambiguous");
  // Incoherent flags on a fake 204.
  assert.equal(classifyStartResult({ ...good, transition: false }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, ok: false }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, ambiguous: true }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, body: "nonempty" }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, status: 201 }).status, "unexpected-response");
  // Missing or arbitrary-truthy endpoint identity can never authorize a receipt.
  assert.equal(classifyStartResult({ ...good, endpointIdentity: "unix:/var/run/docker.sock" }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...good, endpointIdentity: "" }).status, "ambiguous");
  const { endpointIdentity, ...missingIdentity } = good;
  assert.equal(classifyStartResult(missingIdentity).status, "ambiguous");
  // Non-exact container id is rejected.
  assert.equal(classifyStartResult({ ...good, containerId: "abc" }).status, "ambiguous");
  // A coherent 304 no-op with exact identity and empty body is definite.
  const noop = { ok: false, transition: false, ambiguous: false, status: 304, body: "", containerId: CID, endpointIdentity: OTHER };
  assert.equal(classifyStartResult(noop).status, "already-started");
  assert.equal(classifyStartResult({ ...noop, ok: true }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...noop, body: "x" }).status, "ambiguous");
  assert.equal(classifyStartResult({ ...noop, endpointIdentity: "truthy" }).status, "ambiguous");
  assert.equal(classifyStartResult(null).status, "ambiguous");
  assert.equal(classifyStartResult("204").status, "ambiguous");
  assert.equal(classifyStartResult([]).status, "ambiguous");
});

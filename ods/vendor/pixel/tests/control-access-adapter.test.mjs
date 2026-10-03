import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  AccessAuthenticationError,
  AccessVerifier,
  PixelAccessAdapter,
  createServer,
  loadPrivateConfig,
  readPrivateToken,
  validateConfig,
} from "../control/access-adapter.mjs";
import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const root = new URL("../", import.meta.url);
const schema = JSON.parse(await readFile(new URL("../schemas/control-access-adapter-v1.schema.json", import.meta.url), "utf8"));
const example = JSON.parse(await readFile(new URL("../control/access-adapter.example.json", import.meta.url), "utf8"));
const workspaceAudience = "workspace_audience_1234567890";
const approvalAudience = "approval_audience_12345678901";
const fixedNow = 1_786_536_000;

function config(overrides = {}) {
  return {
    ...structuredClone(example),
    publicOrigin: "https://pixel.example.com",
    upstream: { origin: "http://127.0.0.1:43117", reviewTokenFile: "/run/pixel/review-token" },
    access: {
      ...structuredClone(example.access),
      workspaceAudience,
      approvalAudience,
      allowedEmails: ["owner@example.com"],
    },
    ...overrides,
  };
}

function b64(value) {
  return Buffer.from(typeof value === "string" ? value : JSON.stringify(value)).toString("base64url");
}

function signedJwt(privateKey, header, claims) {
  const signingInput = `${b64(header)}.${b64(claims)}`;
  return `${signingInput}.${sign("RSA-SHA256", Buffer.from(signingInput), privateKey).toString("base64url")}`;
}

function keyFixture() {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  return {
    privateKey,
    jwk: { ...publicKey.export({ format: "jwk" }), kid: "pixel-test-key", use: "sig", alg: "RS256" },
  };
}

function claims(overrides = {}) {
  return {
    iss: "https://example.cloudflareaccess.com",
    aud: workspaceAudience,
    email: "owner@example.com",
    iat: fixedNow - 10,
    nbf: fixedNow - 10,
    exp: fixedNow + 300,
    ...overrides,
  };
}

function verifierFixture(overrides = {}) {
  const keys = keyFixture();
  let fetches = 0;
  const fetcher = async () => {
    fetches += 1;
    const payload = JSON.stringify({ keys: [keys.jwk] });
    return new Response(payload, { status: 200, headers: { "content-length": String(Buffer.byteLength(payload)) } });
  };
  const verifier = new AccessVerifier(validateConfig(config()), { fetcher, now: () => fixedNow, ...overrides });
  return { ...keys, verifier, fetches: () => fetches };
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return server.address().port;
}

async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

function call(port, method, path, { assertion = "workspace", body = null, origin = "https://pixel.example.com", headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === null ? null : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
    const requestHeaders = {
      host: "pixel.example.com",
      "cf-access-jwt-assertion": assertion,
      ...headers,
    };
    if (payload) Object.assign(requestHeaders, {
      origin,
      "sec-fetch-site": "same-origin",
      "content-type": "application/json",
      "content-length": String(payload.length),
    });
    const request = http.request({ host: "127.0.0.1", port, method, path, headers: requestHeaders, agent: false }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    request.on("error", reject);
    if (payload) request.write(payload);
    request.end();
  });
}

test("Access adapter configuration is strict, canonical, and schema-valid", () => {
  assert.deepEqual(validateJsonSchema(example, schema), []);
  const valid = validateConfig(config());
  assert.equal(valid.publicHost, "pixel.example.com");
  assert.throws(() => validateConfig(config({ extra: true })), /missing or unknown/);
  assert.throws(() => validateConfig(config({ publicOrigin: "http://pixel.example.com" })), /HTTPS hostname/);
  assert.throws(() => validateConfig(config({ upstream: { ...config().upstream, origin: "http://0.0.0.0:43117" } })), /loopback/);
  const sameAudience = config();
  sameAudience.access.approvalAudience = sameAudience.access.workspaceAudience;
  assert.throws(() => validateConfig(sameAudience), /distinct/);
  const duplicateEmail = config();
  duplicateEmail.access.allowedEmails = ["owner@example.com", "OWNER@example.com"];
  assert.throws(() => validateConfig(duplicateEmail), /duplicate/);
});

test("private configuration rejects duplicate object keys before validation", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pixel-access-config-"));
  try {
    const path = join(directory, "adapter.json");
    const payload = JSON.stringify(config()).replace('"schemaVersion":1', '"schemaVersion":1,"schemaVersion":1');
    await writeFile(path, payload, { mode: 0o600 });
    await assert.rejects(loadPrivateConfig(path), /duplicate object key/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("private adapter inputs reject exposed modes, links, and malformed tokens", { skip: process.platform === "win32" }, async () => {
  const directory = await mkdtemp(join(tmpdir(), "pixel-access-private-"));
  try {
    const configPath = join(directory, "adapter.json");
    const tokenPath = join(directory, "review-token");
    await writeFile(configPath, JSON.stringify(config()), { mode: 0o600 });
    await writeFile(tokenPath, `${"T".repeat(43)}\n`, { mode: 0o600 });
    assert.equal((await loadPrivateConfig(configPath)).schemaVersion, 1);
    assert.equal(await readPrivateToken(tokenPath), "T".repeat(43));
    await chmod(configPath, 0o640);
    await assert.rejects(loadPrivateConfig(configPath), /owner-held/);
    await chmod(configPath, 0o600);
    const linkPath = join(directory, "adapter-link.json");
    await symlink(configPath, linkPath);
    await assert.rejects(loadPrivateConfig(linkPath));
    await writeFile(tokenPath, `${"T".repeat(43)}\r\n`, { mode: 0o600 });
    await assert.rejects(readPrivateToken(tokenPath), /invalid/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("signed Access JWT verification binds key, issuer, audience, identity, and time", async () => {
  const { privateKey, verifier, fetches } = verifierFixture();
  const header = { alg: "RS256", typ: "JWT", kid: "pixel-test-key" };
  const valid = signedJwt(privateKey, header, claims());
  const identity = await verifier.verify(valid, workspaceAudience);
  assert.equal(identity.emailSha256, createHash("sha256").update("owner@example.com").digest("hex"));
  assert.equal(fetches(), 1);
  await verifier.verify(valid, workspaceAudience);
  assert.equal(fetches(), 1, "JWKS must be cached for the configured bounded lifetime");

  const rejected = [
    signedJwt(privateKey, header, claims({ aud: approvalAudience })),
    signedJwt(privateKey, header, claims({ email: "intruder@example.com" })),
    signedJwt(privateKey, header, claims({ exp: fixedNow - 60 })),
    signedJwt(privateKey, header, claims({ nbf: fixedNow + 300 })),
    signedJwt(privateKey, header, claims({ iat: fixedNow + 300 })),
    signedJwt(keyFixture().privateKey, header, claims()),
    signedJwt(privateKey, { ...header, alg: "HS256" }, claims()),
  ];
  for (const token of rejected) await assert.rejects(verifier.verify(token, workspaceAudience), AccessAuthenticationError);
});

test("Access JWT and JWKS duplicate keys, redirects, and oversized responses fail closed", async () => {
  const { privateKey, verifier } = verifierFixture();
  const duplicateHeader = signedJwt(privateKey, '{"alg":"RS256","alg":"RS256","typ":"JWT","kid":"pixel-test-key"}', claims());
  const duplicateClaims = signedJwt(privateKey, { alg: "RS256", typ: "JWT", kid: "pixel-test-key" }, '{"iss":"https://example.cloudflareaccess.com","aud":"workspace_audience_1234567890","email":"owner@example.com","iat":1786535990,"nbf":1786535990,"exp":1786536300,"exp":1786536300}');
  await assert.rejects(verifier.verify(duplicateHeader, workspaceAudience), AccessAuthenticationError);
  await assert.rejects(verifier.verify(duplicateClaims, workspaceAudience), AccessAuthenticationError);

  const redirected = verifierFixture({ fetcher: async () => ({ status: 200, url: "https://attacker.example/jwks", headers: { get: () => null }, arrayBuffer: async () => new ArrayBuffer(2) }) });
  const token = signedJwt(redirected.privateKey, { alg: "RS256", kid: "pixel-test-key" }, claims());
  await assert.rejects(redirected.verifier.verify(token, workspaceAudience), AccessAuthenticationError);

  const oversized = verifierFixture({ fetcher: async () => ({ status: 200, url: "", headers: { get: () => String(600 * 1024) }, arrayBuffer: async () => new ArrayBuffer(0) }) });
  const oversizedToken = signedJwt(oversized.privateKey, { alg: "RS256", kid: "pixel-test-key" }, claims());
  await assert.rejects(oversized.verifier.verify(oversizedToken, workspaceAudience), AccessAuthenticationError);

  const duplicated = keyFixture();
  const duplicateKeys = new AccessVerifier(validateConfig(config()), {
    now: () => fixedNow,
    fetcher: async () => new Response(JSON.stringify({ keys: [duplicated.jwk, duplicated.jwk] }), { status: 200 }),
  });
  const duplicateKeyToken = signedJwt(duplicated.privateKey, { alg: "RS256", kid: "pixel-test-key" }, claims());
  await assert.rejects(duplicateKeys.verify(duplicateKeyToken, workspaceAudience), AccessAuthenticationError);
});

test("origin adapter forwards only allowlisted data and reserves effects for the approval audience", async () => {
  const upstreamRequests = [];
  let session = "S".repeat(43);
  let bootstrapCount = 0;
  let rejectSessionOnce = false;
  const upstream = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const record = { method: request.method, url: request.url, headers: request.headers, body: Buffer.concat(chunks).toString("utf8") };
    upstreamRequests.push(record);
    if (request.url === "/" && request.method === "GET") {
      bootstrapCount += 1;
      session = String.fromCharCode(70 + bootstrapCount).repeat(43);
      response.writeHead(200, { "set-cookie": `pixel_control=${session}; HttpOnly; Path=/`, "content-type": "text/html", "content-length": "2" });
      response.end("ok");
      return;
    }
    if (rejectSessionOnce) {
      rejectSessionOnce = false;
      response.writeHead(401, { "content-type": "application/json", "content-length": "2" });
      response.end("{}");
      return;
    }
    assert.equal(request.headers.cookie, `pixel_control=${session}`);
    const payload = Buffer.from(JSON.stringify({ schemaVersion: 1, path: request.url }));
    response.writeHead(200, { "content-type": "application/json", "content-length": String(payload.length), "set-cookie": "private=never-forward" });
    response.end(payload);
  });
  const upstreamPort = await listen(upstream);
  const verifiedAudiences = [];
  const verifier = {
    async verify(assertion, audience) {
      verifiedAudiences.push(audience);
      const expected = audience === approvalAudience ? "approval" : "workspace";
      if (assertion !== expected) throw new AccessAuthenticationError("rejected fixture identity");
      return { emailSha256: "f".repeat(64), expiresAt: fixedNow + 60 };
    },
  };
  const adapterConfig = validateConfig(config({ upstream: { origin: `http://127.0.0.1:${upstreamPort}`, reviewTokenFile: "/run/pixel/review-token" } }));
  const adapter = new PixelAccessAdapter(adapterConfig, { verifier, token: "T".repeat(43) });
  const portal = createServer(adapter);
  const portalPort = await listen(portal);
  try {
    const accessSession = await call(portalPort, "GET", "/api/v1/access-session");
    assert.equal(accessSession.status, 200);
    const sessionProjection = JSON.parse(accessSession.body);
    assert.deepEqual(Object.keys(sessionProjection).sort(), ["approvalMfa", "boundary", "credentialsExposed", "schemaVersion", "state"]);
    assert.equal(sessionProjection.credentialsExposed, false);
    assert.equal(JSON.stringify(sessionProjection).includes("owner@example.com"), false);

    const status = await call(portalPort, "GET", "/api/v1/status", { headers: { cookie: "secret-browser-cookie=1", "x-user-email": "owner@example.com" } });
    assert.equal(status.status, 200);
    assert.equal(status.headers["set-cookie"], undefined);
    const statusUpstream = upstreamRequests.at(-1);
    assert.equal(statusUpstream.headers["cf-access-jwt-assertion"], undefined);
    assert.equal(statusUpstream.headers["x-user-email"], undefined);
    assert.equal(statusUpstream.headers.cookie.startsWith("pixel_control="), true);
    assert.equal(statusUpstream.headers["x-pixel-review-token"], undefined);

    const chat = await call(portalPort, "GET", "/api/v1/chat");
    assert.equal(chat.status, 200);
    assert.equal(upstreamRequests.at(-1).headers["x-pixel-review-token"], "T".repeat(43));

    const approvals = await call(portalPort, "GET", "/api/v1/approvals");
    assert.equal(approvals.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/approvals");
    assert.equal(upstreamRequests.at(-1).headers["x-pixel-review-token"], "T".repeat(43));
    const permissions = await call(portalPort, "GET", "/api/v1/permissions");
    assert.equal(permissions.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/permissions");

    const challenge = "a".repeat(64);
    const deniedApprovalSession = await call(portalPort, "GET", `/approve/session?challenge=${challenge}`);
    assert.equal(deniedApprovalSession.status, 401);
    const upstreamCountBeforeApprovalSession = upstreamRequests.length;
    assert.equal((await call(portalPort, "GET", "/approve/session?challenge=bad", { assertion: "approval" })).status, 400);
    const approvalSession = await call(portalPort, "GET", `/approve/session?challenge=${challenge}`, { assertion: "approval" });
    assert.equal(approvalSession.status, 200);
    assert.match(approvalSession.headers["content-type"], /^text\/html/);
    assert.match(approvalSession.body.toString("utf8"), new RegExp(`approve/session\\.js\\?challenge=${challenge}`));
    const approvalScript = await call(portalPort, "GET", `/approve/session.js?challenge=${challenge}`, { assertion: "approval" });
    assert.equal(approvalScript.status, 200);
    assert.match(approvalScript.body.toString("utf8"), /pixel-approval-ready/);
    assert.equal(upstreamRequests.length, upstreamCountBeforeApprovalSession, "approval authentication pages must not reach the private control service");

    const actionResult = await call(portalPort, "GET", "/api/v1/actions/control-1786536000000-abcdef123456");
    assert.equal(actionResult.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/actions/control-1786536000000-abcdef123456");
    assert.equal(upstreamRequests.at(-1).headers["x-pixel-review-token"], "T".repeat(43));
    assert.equal((await call(portalPort, "GET", "/api/v1/actions/control-1786536000000-ABCDEF123456")).status, 404);

    const deniedEffect = await call(portalPort, "POST", "/approve/execute", { body: { exact: true } });
    assert.equal(deniedEffect.status, 401);
    const effect = await call(portalPort, "POST", "/approve/execute", { assertion: "approval", body: { exact: true } });
    assert.equal(effect.status, 200);
    const effectUpstream = upstreamRequests.at(-1);
    assert.equal(effectUpstream.headers["x-pixel-review-token"], "T".repeat(43));
    assert.equal(effectUpstream.headers.origin, `http://127.0.0.1:${upstreamPort}`);
    assert.equal(effectUpstream.body, '{"exact":true}');
    assert.equal(effectUpstream.url, "/api/v1/actions/execute");
    assert.equal(verifiedAudiences.at(-1), approvalAudience);

    const deniedProposal = await call(portalPort, "POST", "/api/v1/actions/cancel", { body: { exact: true } });
    assert.equal(deniedProposal.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/actions/cancel");
    assert.equal(verifiedAudiences.at(-1), workspaceAudience);
    assert.equal((await call(portalPort, "POST", "/api/v1/actions/execute", { assertion: "approval", body: { exact: true } })).status, 404);
    const onboardingWrite = await call(portalPort, "POST", "/approve/onboarding", { assertion: "approval", body: { exact: true } });
    assert.equal(onboardingWrite.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/onboarding");
    const permissionWrite = await call(portalPort, "POST", "/approve/permissions", { assertion: "approval", body: { exact: true } });
    assert.equal(permissionWrite.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/permissions");
    const automaticRequest = await call(portalPort, "POST", "/api/v1/actions/request", { body: { exact: true } });
    assert.equal(automaticRequest.status, 200);
    assert.equal(upstreamRequests.at(-1).url, "/api/v1/actions/request");
    assert.equal(verifiedAudiences.at(-1), workspaceAudience);

    const crossSite = await call(portalPort, "POST", "/api/v1/chat/turns", { origin: "https://attacker.example", body: { message: "private" } });
    assert.equal(crossSite.status, 403);
    const unknown = await call(portalPort, "GET", "/api/v1/unknown?private=1");
    assert.equal(unknown.status, 404);

    rejectSessionOnce = true;
    const recovered = await call(portalPort, "GET", "/api/v1/status");
    assert.equal(recovered.status, 200);
    assert.equal(bootstrapCount, 2, "one upstream 401 should rotate the private loopback session once");
  } finally {
    await close(portal);
    await close(upstream);
  }
});

test("origin adapter rejects missing authentication, invalid framing, methods, hosts, and oversized bodies", async () => {
  const verifier = { async verify(assertion) { if (assertion !== "workspace") throw new AccessAuthenticationError("bad"); } };
  const requestUpstream = async (_origin, options) => options.path === "/"
    ? { status: 200, headers: { "set-cookie": [`pixel_control=${"S".repeat(43)}`] }, body: Buffer.from("ok") }
    : { status: 200, headers: { "content-type": "application/json" }, body: Buffer.from("{}") };
  const adapter = new PixelAccessAdapter(validateConfig(config()), { verifier, token: "T".repeat(43), requestUpstream });
  assert.throws(() => new PixelAccessAdapter(validateConfig(config()), { verifier, token: "short", requestUpstream }), /review token/);
  const collapsed = new PixelAccessAdapter(validateConfig(config()), {
    verifier,
    token: "T".repeat(43),
    requestUpstream: async () => ({ status: 200, headers: { "set-cookie": [`pixel_control=${"T".repeat(43)}`] }, body: Buffer.from("ok") }),
  });
  await assert.rejects(collapsed.bootstrap(), /must be distinct/);
  const portal = createServer(adapter);
  const port = await listen(portal);
  try {
    assert.equal((await call(port, "GET", "/api/v1/status", { assertion: "bad" })).status, 401);
    assert.equal((await call(port, "GET", "/api/v1/status", { headers: { host: "attacker.example" } })).status, 400);
    assert.equal((await call(port, "DELETE", "/api/v1/status")).status, 404);
    assert.equal((await call(port, "GET", "/api/v1/status?query=forbidden")).status, 404);
    assert.equal((await call(port, "GET", "//pixel.example.com/api/v1/status")).status, 400);
    assert.equal((await call(port, "GET", "/api/v1/status", { body: "unexpected" })).status, 400);
    const tooLarge = "x".repeat(config().limits.maxBodyBytes + 1);
    assert.equal((await call(port, "POST", "/api/v1/chat/turns", { body: tooLarge })).status, 400);
  } finally {
    await close(portal);
  }
});

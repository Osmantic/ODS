#!/usr/bin/env node
import { createHash, timingSafeEqual, webcrypto } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { open, readFile } from "node:fs/promises";
import http from "node:http";
import { isIP } from "node:net";
import { pathToFileURL } from "node:url";

const CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/control-access-adapter-v1.schema.json";
const BOUNDARY = "Cloudflare Access-authenticated narrow origin adapter. It validates signed application JWTs and exact audiences before forwarding an allowlisted route to the loopback Pixel control service. Browser cookies, Access assertions, identity claims, credentials, arbitrary paths, and generic commands never cross to the control service. Approval routes require a distinct MFA-enforced Access application audience.";
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/u;
const AUDIENCE_RE = /^[A-Za-z0-9_-]{20,128}$/u;
const TEAM_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$/u;
const EMAIL_RE = /^[^\s@<>\u0000-\u001f\u007f]+@[^\s@<>\u0000-\u001f\u007f]+$/u;
const SAFE_ID_RE = /^[A-Za-z0-9_-]{1,160}$/u;
const JWT_RE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u;
const SESSION_COOKIE_RE = /^pixel_control=[A-Za-z0-9_-]{43}$/u;
const ACTION_RESULT_PATH_RE = /^\/api\/v1\/actions\/control-[0-9]{13}-[a-f0-9]{12}$/u;
const APPROVAL_CHALLENGE_RE = /^[a-f0-9]{64}$/u;
const MAX_JWT_BYTES = 24 * 1024;
const MAX_JWKS_BYTES = 512 * 1024;

const ROUTES = Object.freeze(new Map([
  ["GET /", { upstream: "/", review: false, audience: "workspace" }],
  ["GET /app.js", { upstream: "/app.js", review: false, audience: "workspace" }],
  ["GET /styles.css", { upstream: "/styles.css", review: false, audience: "workspace" }],
  ["GET /api/v1/status", { upstream: "/api/v1/status", review: false, audience: "workspace" }],
  ["GET /api/v1/chat", { upstream: "/api/v1/chat", review: true, audience: "workspace" }],
  ["GET /api/v1/approvals", { upstream: "/api/v1/approvals", review: true, audience: "workspace" }],
  ["GET /api/v1/permissions", { upstream: "/api/v1/permissions", review: true, audience: "workspace" }],
  ["GET /api/v1/deep-work", { upstream: "/api/v1/deep-work", review: false, audience: "workspace" }],
  ["GET /api/v1/deep-work/authoring", { upstream: "/api/v1/deep-work/authoring", review: true, audience: "workspace" }],
  ["GET /api/v1/deep-work/drafts", { upstream: "/api/v1/deep-work/drafts", review: true, audience: "workspace" }],
  ["GET /api/v1/update-status", { upstream: "/api/v1/update-status", review: false, audience: "workspace" }],
  ["GET /api/v1/recovery-guide", { upstream: "/api/v1/recovery-guide", review: false, audience: "workspace" }],
  ["GET /api/v1/doctor", { upstream: "/api/v1/doctor", review: false, audience: "workspace" }],
  ["GET /api/v1/diagnostics", { upstream: "/api/v1/diagnostics", review: false, audience: "workspace" }],
  ["GET /api/v1/reviews/frontier", { upstream: "/api/v1/reviews/frontier", review: true, audience: "workspace" }],
  ["GET /api/v1/reviews/deep-work", { upstream: "/api/v1/reviews/deep-work", review: true, audience: "workspace" }],
  ["GET /api/v1/onboarding", { upstream: "/api/v1/onboarding", review: false, audience: "workspace" }],
  ["POST /approve/onboarding", { upstream: "/api/v1/onboarding", review: true, audience: "approval" }],
  ["POST /approve/permissions", { upstream: "/api/v1/permissions", review: true, audience: "approval" }],
  ["POST /api/v1/chat/turns", { upstream: "/api/v1/chat/turns", review: true, audience: "workspace" }],
  ["POST /api/v1/frontier-budget/preview", { upstream: "/api/v1/frontier-budget/preview", review: false, audience: "workspace" }],
  ["POST /api/v1/actions/preview", { upstream: "/api/v1/actions/preview", review: true, audience: "workspace" }],
  ["POST /api/v1/actions/request", { upstream: "/api/v1/actions/request", review: true, audience: "workspace" }],
  ["POST /approve/execute", { upstream: "/api/v1/actions/execute", review: true, audience: "approval" }],
  ["POST /api/v1/actions/cancel", { upstream: "/api/v1/actions/cancel", review: true, audience: "workspace" }],
]));

const RESPONSE_HEADERS = Object.freeze([
  "content-type", "cache-control", "x-content-type-options", "x-frame-options",
  "referrer-policy", "permissions-policy", "cross-origin-resource-policy",
  "cross-origin-opener-policy", "content-security-policy",
]);

function allowedRoute(method, pathname) {
  const exact = ROUTES.get(`${method} ${pathname}`);
  if (exact) return exact;
  if (method === "GET" && ACTION_RESULT_PATH_RE.test(pathname)) {
    return Object.freeze({ upstream: pathname, review: true, audience: "workspace" });
  }
  return null;
}

function fail(message) {
  throw new Error(message);
}

export class AccessAuthenticationError extends Error {}

function authFail(message) {
  throw new AccessAuthenticationError(message);
}

function assertNoDuplicateJsonKeys(text, label) {
  let position = 0;
  const whitespace = () => { while (/\s/u.test(text[position] ?? "")) position += 1; };
  const stringToken = () => {
    if (text[position] !== '"') fail(`${label} is not strict JSON`);
    const start = position++;
    while (position < text.length) {
      const character = text[position++];
      if (character === '"') {
        const raw = text.slice(start, position);
        try { return JSON.parse(raw); } catch { fail(`${label} is not strict JSON`); }
      }
      if (character === "\\") position += 1;
    }
    fail(`${label} is not strict JSON`);
  };
  const value = () => {
    whitespace();
    if (text[position] === "{") {
      position += 1;
      whitespace();
      const keys = new Set();
      if (text[position] === "}") { position += 1; return; }
      while (position < text.length) {
        const key = stringToken();
        if (keys.has(key)) fail(`${label} contains a duplicate object key`);
        keys.add(key);
        whitespace();
        if (text[position++] !== ":") fail(`${label} is not strict JSON`);
        value();
        whitespace();
        const separator = text[position++];
        if (separator === "}") return;
        if (separator !== ",") fail(`${label} is not strict JSON`);
        whitespace();
      }
    } else if (text[position] === "[") {
      position += 1;
      whitespace();
      if (text[position] === "]") { position += 1; return; }
      while (position < text.length) {
        value();
        whitespace();
        const separator = text[position++];
        if (separator === "]") return;
        if (separator !== ",") fail(`${label} is not strict JSON`);
      }
    } else if (text[position] === '"') {
      stringToken();
    } else {
      const start = position;
      while (position < text.length && !/[\s,}\]]/u.test(text[position])) position += 1;
      if (position === start) fail(`${label} is not strict JSON`);
    }
  };
  value();
  whitespace();
  if (position !== text.length) fail(`${label} is not strict JSON`);
}

function parseStrictJson(text, label) {
  let parsed;
  try { parsed = JSON.parse(text); } catch { fail(`${label} is not strict JSON`); }
  assertNoDuplicateJsonKeys(text, label);
  return parsed;
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) {
    fail(`${label} has missing or unknown fields`);
  }
}

function exactString(value, label, minimum, maximum) {
  if (typeof value !== "string" || value.length < minimum || value.length > maximum || /[\u0000-\u001f\u007f]/u.test(value)) fail(`${label} is invalid`);
  return value;
}

export function validateConfig(value) {
  exactKeys(value, ["$schema", "schemaVersion", "listen", "publicOrigin", "upstream", "access", "limits", "boundary"], "adapter configuration");
  if (value.$schema !== CONFIG_SCHEMA || value.schemaVersion !== 1 || value.boundary !== BOUNDARY) fail("adapter configuration identity or boundary is invalid");
  exactKeys(value.listen, ["host", "port"], "adapter listener");
  if (value.listen.host !== "127.0.0.1" || !Number.isInteger(value.listen.port) || value.listen.port < 1024 || value.listen.port > 65535) fail("adapter listener must be an unprivileged exact IPv4 loopback socket");
  const publicUrl = new URL(exactString(value.publicOrigin, "public origin", 12, 512));
  if (publicUrl.protocol !== "https:" || publicUrl.username || publicUrl.password || publicUrl.port || publicUrl.pathname !== "/" || publicUrl.search || publicUrl.hash || isIP(publicUrl.hostname)) fail("public origin must be one canonical HTTPS hostname");
  exactKeys(value.upstream, ["origin", "reviewTokenFile"], "adapter upstream");
  const upstream = new URL(exactString(value.upstream.origin, "upstream origin", 12, 128));
  if (upstream.protocol !== "http:" || upstream.hostname !== "127.0.0.1" || !/^\d{4,5}$/u.test(upstream.port) || upstream.pathname !== "/" || upstream.search || upstream.hash || upstream.username || upstream.password) fail("adapter upstream must be one exact IPv4 loopback HTTP origin");
  const tokenFile = exactString(value.upstream.reviewTokenFile, "review token file", 2, 4096);
  if (!tokenFile.startsWith("/") || tokenFile === "/" || tokenFile.includes("\u0000")) fail("review token file must be an absolute non-root path");
  exactKeys(value.access, ["teamDomain", "workspaceAudience", "approvalAudience", "allowedEmails", "clockSkewSeconds", "jwksCacheSeconds"], "Access policy");
  if (!TEAM_RE.test(value.access.teamDomain)) fail("Access team domain is invalid");
  if (!AUDIENCE_RE.test(value.access.workspaceAudience) || !AUDIENCE_RE.test(value.access.approvalAudience) || value.access.workspaceAudience === value.access.approvalAudience) fail("Access audiences must be distinct exact tags");
  if (!Array.isArray(value.access.allowedEmails) || value.access.allowedEmails.length < 1 || value.access.allowedEmails.length > 32) fail("Access email allowlist is invalid");
  const emails = value.access.allowedEmails.map((entry) => exactString(entry, "allowed email", 3, 320).toLowerCase());
  if (emails.some((entry) => !EMAIL_RE.test(entry)) || new Set(emails).size !== emails.length) fail("Access email allowlist contains an invalid or duplicate identity");
  if (!Number.isInteger(value.access.clockSkewSeconds) || value.access.clockSkewSeconds < 0 || value.access.clockSkewSeconds > 120) fail("Access clock skew is invalid");
  if (!Number.isInteger(value.access.jwksCacheSeconds) || value.access.jwksCacheSeconds < 60 || value.access.jwksCacheSeconds > 86400) fail("Access key cache duration is invalid");
  exactKeys(value.limits, ["maxBodyBytes", "requestsPerMinute"], "adapter limits");
  if (!Number.isInteger(value.limits.maxBodyBytes) || value.limits.maxBodyBytes < 1024 || value.limits.maxBodyBytes > 65536) fail("adapter body limit is invalid");
  if (!Number.isInteger(value.limits.requestsPerMinute) || value.limits.requestsPerMinute < 10 || value.limits.requestsPerMinute > 600) fail("adapter rate limit is invalid");
  return Object.freeze({
    ...value,
    publicOrigin: publicUrl.origin,
    publicHost: publicUrl.host,
    upstream: Object.freeze({ ...value.upstream, origin: upstream.origin }),
    access: Object.freeze({ ...value.access, allowedEmails: Object.freeze(emails) }),
    listen: Object.freeze({ ...value.listen }), limits: Object.freeze({ ...value.limits }),
  });
}

async function readPrivate(path, maximum) {
  const handle = await open(path, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > maximum) fail("private adapter input is unsafe or oversized");
    if (process.platform !== "win32" && ((info.mode & 0o777) !== 0o600 || info.uid !== process.geteuid())) fail("private adapter input must be owner-held with permissions 0600");
    const payload = await handle.readFile();
    const current = await handle.stat();
    if (current.dev !== info.dev || current.ino !== info.ino || current.size !== info.size) fail("private adapter input changed while reading");
    return payload;
  } finally {
    await handle.close();
  }
}

export async function loadPrivateConfig(path) {
  const payload = await readPrivate(path, 256 * 1024);
  const value = parseStrictJson(payload.toString("utf8"), "private adapter configuration");
  return validateConfig(value);
}

export async function readPrivateToken(path) {
  const payload = await readPrivate(path, 128);
  const token = payload.toString("ascii").replace(/\n$/u, "");
  if (!TOKEN_RE.test(token) || (payload.toString("ascii") !== token && payload.toString("ascii") !== `${token}\n`)) fail("private adapter review token is invalid");
  return token;
}

function base64urlJson(segment, label, maximum) {
  if (!/^[A-Za-z0-9_-]+$/u.test(segment) || segment.length > maximum * 2) authFail(`${label} encoding is invalid`);
  const payload = Buffer.from(segment, "base64url");
  if (payload.length < 2 || payload.length > maximum || payload.toString("base64url") !== segment) authFail(`${label} encoding is noncanonical`);
  try { return parseStrictJson(payload.toString("utf8"), label); } catch (error) { authFail(error.message); }
}

function numericClaim(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) authFail(`${label} claim is invalid`);
  return value;
}

export class AccessVerifier {
  constructor(config, { fetcher = fetch, now = () => Math.floor(Date.now() / 1000) } = {}) {
    this.config = config;
    this.fetcher = fetcher;
    this.now = now;
    this.keys = new Map();
    this.keysExpireAt = 0;
  }

  async refreshKeys() {
    const url = `https://${this.config.access.teamDomain}/cdn-cgi/access/certs`;
    const response = await this.fetcher(url, { redirect: "error", signal: AbortSignal.timeout(5000), headers: { accept: "application/json" } });
    if (!response || response.status !== 200 || response.url && response.url !== url) authFail("Access signing keys could not be verified");
    const contentLength = response.headers?.get?.("content-length");
    if (contentLength && (!/^\d+$/u.test(contentLength) || Number(contentLength) > MAX_JWKS_BYTES)) authFail("Access signing-key response is oversized");
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length < 2 || bytes.length > MAX_JWKS_BYTES) authFail("Access signing-key response is invalid or oversized");
    let value;
    try { value = parseStrictJson(bytes.toString("utf8"), "Access signing-key response"); } catch (error) { authFail(error.message); }
    if (!value || !Array.isArray(value.keys) || value.keys.length < 1 || value.keys.length > 16) authFail("Access signing-key set is invalid");
    const imported = new Map();
    for (const key of value.keys) {
      if (!key || key.kty !== "RSA" || key.use !== "sig" || key.alg !== "RS256" || !SAFE_ID_RE.test(key.kid ?? "") || typeof key.n !== "string" || typeof key.e !== "string") continue;
      const publicKey = await webcrypto.subtle.importKey("jwk", key, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
      if (imported.has(key.kid)) authFail("Access signing-key set contains a duplicate key identity");
      imported.set(key.kid, publicKey);
    }
    if (!imported.size) authFail("Access signing-key set contains no usable RS256 key");
    this.keys = imported;
    this.keysExpireAt = this.now() + this.config.access.jwksCacheSeconds;
  }

  async verify(assertion, audience) {
    if (typeof assertion !== "string" || Buffer.byteLength(assertion) > MAX_JWT_BYTES || !JWT_RE.test(assertion)) authFail("Access assertion is invalid");
    const parts = assertion.split(".");
    const header = base64urlJson(parts[0], "Access assertion header", 4096);
    const claims = base64urlJson(parts[1], "Access assertion payload", 16 * 1024);
    if (!header || typeof header !== "object" || Array.isArray(header) || header.alg !== "RS256" || !SAFE_ID_RE.test(header.kid ?? "") || header.typ && header.typ !== "JWT") authFail("Access assertion header is invalid");
    if (this.keysExpireAt <= this.now() || !this.keys.has(header.kid)) await this.refreshKeys();
    const key = this.keys.get(header.kid);
    if (!key) authFail("Access assertion signing key is unknown");
    const signature = Buffer.from(parts[2], "base64url");
    if (signature.length < 128 || signature.length > 1024 || signature.toString("base64url") !== parts[2]) authFail("Access assertion signature encoding is invalid");
    const verified = await webcrypto.subtle.verify({ name: "RSASSA-PKCS1-v1_5" }, key, signature, Buffer.from(`${parts[0]}.${parts[1]}`, "ascii"));
    if (!verified) authFail("Access assertion signature is invalid");
    if (!claims || typeof claims !== "object" || Array.isArray(claims)) authFail("Access assertion claims are invalid");
    const now = this.now();
    const skew = this.config.access.clockSkewSeconds;
    const exp = numericClaim(claims.exp, "Access expiration");
    const nbf = claims.nbf === undefined ? 0 : numericClaim(claims.nbf, "Access not-before");
    const iat = numericClaim(claims.iat, "Access issued-at");
    if (claims.iss !== `https://${this.config.access.teamDomain}` || exp < now - skew || nbf > now + skew || iat > now + skew || exp <= iat) authFail("Access assertion time or issuer is invalid");
    const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (!audiences.includes(audience)) authFail("Access assertion audience is invalid");
    const email = typeof claims.email === "string" ? claims.email.toLowerCase() : "";
    if (!EMAIL_RE.test(email) || !this.config.access.allowedEmails.includes(email)) authFail("Access identity is not authorized for this Pixel deployment");
    return Object.freeze({ emailSha256: createHash("sha256").update(email).digest("hex"), expiresAt: exp });
  }
}

class RateLimiter {
  constructor(maximum) { this.maximum = maximum; this.events = []; }
  allow(now = Date.now()) {
    const cutoff = now - 60_000;
    while (this.events.length && this.events[0] <= cutoff) this.events.shift();
    if (this.events.length >= this.maximum) return false;
    this.events.push(now);
    return true;
  }
}

function exactHeader(request, name, { required = false } = {}) {
  const values = [];
  for (let index = 0; index < request.rawHeaders.length; index += 2) if (request.rawHeaders[index].toLowerCase() === name.toLowerCase()) values.push(request.rawHeaders[index + 1]);
  if (values.length > 1 || required && values.length !== 1) fail(`${name} header is missing or duplicated`);
  return values[0];
}

async function requestBody(request, maximum) {
  if (exactHeader(request, "transfer-encoding")) fail("request transfer encoding is not allowed");
  const type = exactHeader(request, "content-type", { required: true });
  const lengthText = exactHeader(request, "content-length", { required: true });
  if (type.split(";", 1)[0].trim().toLowerCase() !== "application/json" || !/^\d+$/u.test(lengthText)) fail("request JSON framing is invalid");
  const length = Number(lengthText);
  if (length < 1 || length > maximum) fail("request body is empty or oversized");
  const chunks = [];
  let observed = 0;
  for await (const chunk of request) {
    observed += chunk.length;
    if (observed > length || observed > maximum) fail("request body exceeds its declared limit");
    chunks.push(chunk);
  }
  if (observed !== length) fail("request body length differs from its declaration");
  return Buffer.concat(chunks);
}

function jsonError(response, status, code, message) {
  const body = Buffer.from(JSON.stringify({ schemaVersion: 1, error: code, message }));
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8", "content-length": body.length,
    "cache-control": "no-store", "x-content-type-options": "nosniff", "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'", "referrer-policy": "no-referrer",
    connection: "close",
  });
  response.end(body);
}

function staticResponse(response, contentType, source, contentSecurityPolicy) {
  const body = Buffer.from(source, "utf8");
  response.writeHead(200, {
    "content-type": contentType, "content-length": body.length,
    "cache-control": "no-store", "x-content-type-options": "nosniff", "x-frame-options": "DENY",
    "content-security-policy": contentSecurityPolicy, "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
  });
  response.end(body);
}

function upstreamRequest(origin, options, body = null) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      callback(value);
    };
    const target = new URL(options.path, origin);
    const request = http.request(target, { method: options.method, headers: options.headers, agent: false }, (response) => {
      const chunks = [];
      let total = 0;
      response.on("data", (chunk) => {
        total += chunk.length;
        if (total > 2 * 1024 * 1024) {
          const error = new Error("loopback response exceeded adapter limit");
          response.destroy(error);
          request.destroy(error);
        }
        else chunks.push(chunk);
      });
      response.on("error", (error) => finish(reject, error));
      response.on("end", () => finish(resolve, { status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    request.setTimeout(700_000, () => request.destroy(new Error("loopback request timed out")));
    request.on("error", (error) => finish(reject, error));
    if (body) request.write(body);
    request.end();
  });
}

export class PixelAccessAdapter {
  constructor(config, { verifier = new AccessVerifier(config), token, requestUpstream = upstreamRequest } = {}) {
    if (!TOKEN_RE.test(token ?? "")) fail("adapter review token is invalid");
    this.config = config;
    this.verifier = verifier;
    this.token = token;
    this.requestUpstream = requestUpstream;
    this.sessionCookie = null;
    this.limiter = new RateLimiter(config.limits.requestsPerMinute);
  }

  async bootstrap() {
    const result = await this.requestUpstream(this.config.upstream.origin, { method: "GET", path: "/", headers: { host: new URL(this.config.upstream.origin).host } });
    const cookies = Array.isArray(result.headers["set-cookie"]) ? result.headers["set-cookie"] : [result.headers["set-cookie"]].filter(Boolean);
    const pair = cookies.length === 1 ? cookies[0].split(";", 1)[0] : "";
    if (result.status !== 200 || !SESSION_COOKIE_RE.test(pair)) fail("loopback control session bootstrap failed");
    if (timingSafeEqual(Buffer.from(pair.slice("pixel_control=".length)), Buffer.from(this.token))) fail("loopback control session and review token must be distinct");
    this.sessionCookie = pair;
  }

  async forward(route, method, body) {
    const attempt = async () => {
      if (!this.sessionCookie) await this.bootstrap();
      const upstream = new URL(this.config.upstream.origin);
      const headers = { host: upstream.host, cookie: this.sessionCookie, accept: "application/json, text/html, text/css, text/javascript" };
      if (route.review) headers["x-pixel-review-token"] = this.token;
      if (method === "POST") Object.assign(headers, { "content-type": "application/json", "content-length": String(body.length), origin: this.config.upstream.origin, "sec-fetch-site": "same-origin" });
      return this.requestUpstream(this.config.upstream.origin, { method, path: route.upstream, headers }, body);
    };
    let result = await attempt();
    if (result.status === 401) {
      this.sessionCookie = null;
      result = await attempt();
    }
    return result;
  }

  async handle(request, response) {
    try {
      if (!this.limiter.allow()) return jsonError(response, 429, "rate_limited", "Too many Pixel portal requests");
      const host = exactHeader(request, "host", { required: true });
      if (host !== this.config.publicHost) return jsonError(response, 400, "invalid_host", "Pixel portal Host header rejected");
      if (typeof request.url !== "string" || !request.url.startsWith("/") || request.url.startsWith("//")) return jsonError(response, 400, "invalid_target", "Pixel portal request target rejected");
      const parsed = new URL(request.url, this.config.publicOrigin);
      const approvalSessionPath = ["/approve/session", "/approve/session.js"].includes(parsed.pathname);
      if (parsed.origin !== this.config.publicOrigin || parsed.hash || parsed.search && !approvalSessionPath) return jsonError(response, 404, "not_found", "Pixel portal route not found");
      if (request.method === "GET" && (exactHeader(request, "transfer-encoding") || ![undefined, "0"].includes(exactHeader(request, "content-length")))) {
        return jsonError(response, 400, "invalid_framing", "Pixel portal GET body framing rejected");
      }
      if (request.method === "GET" && parsed.pathname === "/api/v1/access-session") {
        const assertion = exactHeader(request, "cf-access-jwt-assertion", { required: true });
        await this.verifier.verify(assertion, this.config.access.workspaceAudience);
        const body = Buffer.from(JSON.stringify({ schemaVersion: 1, state: "verified", approvalMfa: "separate-access-application-every-login", credentialsExposed: false, boundary: BOUNDARY }));
        response.writeHead(200, { "content-type": "application/json; charset=utf-8", "content-length": body.length, "cache-control": "no-store", "x-content-type-options": "nosniff", "x-frame-options": "DENY", "content-security-policy": "default-src 'none'; frame-ancestors 'none'" });
        response.end(body);
        return;
      }
      if (request.method === "GET" && approvalSessionPath) {
        const challenge = parsed.searchParams.get("challenge");
        if (parsed.searchParams.size !== 1 || !APPROVAL_CHALLENGE_RE.test(challenge ?? "") || parsed.search !== `?challenge=${challenge}`) return jsonError(response, 400, "invalid_challenge", "Pixel approval challenge rejected");
        const assertion = exactHeader(request, "cf-access-jwt-assertion", { required: true });
        await this.verifier.verify(assertion, this.config.access.approvalAudience);
        if (parsed.pathname.endsWith(".js")) {
          staticResponse(response, "text/javascript; charset=utf-8", '"use strict";\nconst challenge = new URL(document.currentScript.src).searchParams.get("challenge") || "";\nif (/^[a-f0-9]{64}$/.test(challenge)) { const channel = new BroadcastChannel(`pixel-approval-${challenge}`); channel.postMessage({schemaVersion: 1, type: "pixel-approval-ready", challenge}); channel.close(); window.close(); }\n', "default-src 'none'; frame-ancestors 'none'");
        } else {
          staticResponse(response, "text/html; charset=utf-8", `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pixel approval verified</title></head><body><main><h1>Approval identity verified</h1><p>You can close this window and return to Pixel.</p></main><script src="/approve/session.js?challenge=${challenge}"></script></body></html>`, "default-src 'none'; script-src 'self'; style-src 'none'; img-src 'none'; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
        }
        return;
      }
      const route = allowedRoute(request.method, parsed.pathname);
      if (!route) return jsonError(response, 404, "not_found", "Pixel portal route not found");
      const assertion = exactHeader(request, "cf-access-jwt-assertion", { required: true });
      await this.verifier.verify(assertion, route.audience === "approval" ? this.config.access.approvalAudience : this.config.access.workspaceAudience);
      let body = null;
      if (request.method === "POST") {
        const origin = exactHeader(request, "origin", { required: true });
        const fetchSite = exactHeader(request, "sec-fetch-site", { required: true });
        if (origin !== this.config.publicOrigin || fetchSite !== "same-origin") return jsonError(response, 403, "origin_rejected", "Cross-site Pixel portal request rejected");
        body = await requestBody(request, this.config.limits.maxBodyBytes);
      }
      const upstream = await this.forward(route, request.method, body);
      const headers = {};
      for (const name of RESPONSE_HEADERS) if (typeof upstream.headers[name] === "string") headers[name] = upstream.headers[name];
      headers["content-length"] = String(upstream.body.length);
      headers["cache-control"] = "no-store";
      headers["x-content-type-options"] = "nosniff";
      headers["x-frame-options"] = "DENY";
      response.writeHead(upstream.status, headers);
      response.end(upstream.body);
    } catch (error) {
      const authentication = error instanceof AccessAuthenticationError;
      jsonError(response, authentication ? 401 : 400, authentication ? "access_rejected" : "request_rejected", authentication ? "Cloudflare Access authentication was rejected" : "Pixel portal request was rejected");
    }
  }
}

export function createServer(adapter) {
  const server = http.createServer((request, response) => { adapter.handle(request, response).catch(() => jsonError(response, 500, "adapter_failure", "Pixel portal adapter failed closed")); });
  server.maxHeadersCount = 48;
  server.headersTimeout = 10_000;
  server.requestTimeout = 710_000;
  server.keepAliveTimeout = 5_000;
  return server;
}

async function main() {
  if (process.platform === "win32") fail("The Access adapter requires a supported POSIX host with enforceable owner-only file modes");
  if (process.argv.length !== 4 || process.argv[2] !== "--config") fail("Usage: node control/access-adapter.mjs --config PRIVATE_JSON");
  const config = await loadPrivateConfig(process.argv[3]);
  const token = await readPrivateToken(config.upstream.reviewTokenFile);
  const adapter = new PixelAccessAdapter(config, { token });
  await adapter.bootstrap();
  const server = createServer(adapter);
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(config.listen.port, config.listen.host, resolve); });
  process.stdout.write(`${JSON.stringify({ schemaVersion: 1, status: "ready", listener: "exact-ipv4-loopback", publicOrigin: config.publicOrigin, access: "signed-jwt-exact-audience", approvalMfa: "separate-access-application-every-login", boundary: BOUNDARY })}\n`);
  const close = () => server.close(() => process.exit(0));
  process.once("SIGINT", close);
  process.once("SIGTERM", close);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch((error) => { process.stderr.write(`Pixel access adapter failed: ${error.message}\n`); process.exit(1); });

export { BOUNDARY, CONFIG_SCHEMA, ROUTES };

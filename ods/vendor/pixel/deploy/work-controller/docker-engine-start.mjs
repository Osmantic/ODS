import http from "node:http";
import { randomBytes, createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";

// Exact, immutable default Docker Engine Unix socket path for the hardened
// production goal service units. This is the real deployed endpoint on the
// Tower2 production host: /var/run is a symlink to /run, and the goal service
// units gate on ConditionPathExists=/run/docker.sock and
// BindReadOnlyPaths=/run/docker.sock. secureSocketIdentity still requires
// realpath(socketPath) === socketPath, so only the canonical /run path can
// prove exact endpoint identity on this host. Guardians and callers that do
// not override a socket path must use this exact constant so unattended
// recovery can prove identity and start production.
export const DOCKER_SOCKET_PATH = "/run/docker.sock";

// Exact, fail-closed Docker Engine start attempt for production attribution.
//
// The Docker Engine REST API distinguishes an actual transition by this request
// from an already-running no-op:
//   POST /containers/{exact-id}/start  ->  204 = this request started it
//                                       ->  304 = already started (no-op)
// The Unix socket endpoint is securely identified (real path, socket type, no
// symlink leaf, trusted non-writable parent/owner, stable device+inode) before
// and after the request and that identity is bound into the receipt; a path
// string alone is never endpoint identity. Response bytes are bounded (64 KiB);
// on overflow, a nonempty 204, a socket identity change, an unexpected status,
// a request error after a possible commit, or a missing endpoint identity all
// fail closed as manual/ambiguous.
export class DockerEngineStartError extends Error {}

function fail(message) { throw new DockerEngineStartError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }

export function newStartNonce() {
  return randomBytes(32).toString("hex");
}

const MAX_RESPONSE_BYTES = 64 * 1024;
const MIN_TIMEOUT_MS = 1000;
const MAX_TIMEOUT_MS = 120000;
const EXACT_SHA_RE = /^[a-f0-9]{64}$/u;
// A definite 204/304 result has exactly this closed shape; any extra key (such
// as the ambiguous "reason") or missing field is rejected as not definite.
const DEFINITE_RESULT_KEYS = ["ok", "transition", "ambiguous", "status", "body", "containerId", "endpointIdentity"];

function validateSocketPath(socketPath) {
  if (typeof socketPath !== "string" || !isAbsolute(socketPath) || resolve(socketPath) !== socketPath || socketPath.includes("\0") || /[\r\n]/u.test(socketPath)) fail("docker engine socket path is not an absolute canonical path");
  return socketPath;
}

function validateContainerId(containerId) {
  if (typeof containerId !== "string" || !/^[a-f0-9]{64}$/u.test(containerId)) fail("docker engine start requires an exact 64-hex container id");
  return containerId;
}

// Securely identify the Unix socket endpoint: real path (no symlink leaf),
// socket type, trusted non-writable owner-private/root parent, and stable
// device+inode identity. Returns null if any property cannot be proven exact.
export async function secureSocketIdentity(socketPath) {
  validateSocketPath(socketPath);
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(socketPath), lstat(socketPath, { bigint: true })]); }
  catch { return null; }
  if (!samePath(actual, socketPath)) return null;
  if (!info.isSocket() || info.isSymbolicLink()) return null;
  const parent = dirname(socketPath);
  let pActual, pInfo;
  try { [pActual, pInfo] = await Promise.all([realpath(parent), lstat(parent, { bigint: true })]); }
  catch { return null; }
  if (!samePath(pActual, parent) || !pInfo.isDirectory() || pInfo.isSymbolicLink()) return null;
  if (process.platform !== "win32" && (pInfo.mode & 0o022n) !== 0n) return null;
  const parentUid = Number(pInfo.uid);
  const euid = process.geteuid?.() ?? 0;
  if (process.platform !== "win32" && parentUid !== 0 && parentUid !== euid) return null;
  const identity = Object.freeze({ realPath: actual, dev: String(info.dev), ino: String(info.ino) });
  return Object.freeze({ ...identity, identitySha256: createHash("sha256").update(canonical(identity)).digest("hex") });
}

export async function dockerEngineStart({ containerId, socketPath, timeoutMs = 30000, reviewedIdentity = null } = {}) {
  validateContainerId(containerId);
  validateSocketPath(socketPath);
  // An exact reviewed endpoint identity is REQUIRED: the production start is
  // authorized only against a specific 64-hex socket identity. A merely truthy
  // path string (e.g. unix:/var/run/docker.sock) is never an identity.
  if (typeof reviewedIdentity !== "string" || !EXACT_SHA_RE.test(reviewedIdentity)) fail("docker engine start requires an exact reviewed endpoint identity");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < MIN_TIMEOUT_MS || timeoutMs > MAX_TIMEOUT_MS) fail("docker engine start timeout is out of range");
  // Identify the socket before the request; missing identity is already ambiguous.
  const before = await secureSocketIdentity(socketPath);
  if (before === null) return ambiguousResult({ containerId, status: null, endpointIdentity: null, reason: "docker engine socket endpoint identity could not be proven before the request" });
  if (before.identitySha256 !== reviewedIdentity) return ambiguousResult({ containerId, status: null, endpointIdentity: before.identitySha256, reason: "docker engine socket identity differs from the reviewed endpoint identity" });
  return new Promise((resolveStart) => {
    let settled = false;
    let endSeen = false;
    const chunks = [];
    let bodyBytes = 0;
    let overflow = false;
    let requestError = null;
    let status = null;
    const finish = (result) => { if (!settled) { settled = true; resolveStart(result); } };
    const request = http.request({
      method: "POST",
      path: `/containers/${containerId}/start`,
      socketPath,
      headers: { Host: "docker" },
      timeout: timeoutMs,
    }, (response) => {
      status = response.statusCode;
      response.on("data", (chunk) => {
        bodyBytes += chunk.length;
        if (bodyBytes > MAX_RESPONSE_BYTES) {
          // Bound the response: once the body exceeds the cap, resolve
          // immediately as ambiguous and tear down the socket. Destroying the
          // request here does not reliably fire the response 'end' or request
          // 'error' handlers, so this must resolve in place rather than wait.
          overflow = true;
          finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine response exceeded the bounded size" }));
          request.destroy();
        } else {
          chunks.push(chunk);
        }
      });
      response.on("error", (error) => { requestError = error; });
      // The response must prove a clean 'end' before any definite outcome; an
      // aborted or prematurely closed response resolves ambiguous promptly
      // (never hangs), including after a possible commit.
      response.on("aborted", () => {
        if (!settled && !endSeen) finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine response aborted before a proven end" }));
      });
      response.on("close", () => {
        if (!settled && !endSeen) finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine response closed before a proven end" }));
      });
      response.on("end", () => {
        endSeen = true;
        // Re-identify the socket after the request; a change is ambiguous.
        secureSocketIdentity(socketPath).then((after) => {
          if (requestError) {
            finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine request errored after a possible commit" }));
            return;
          }
          if (overflow) {
            finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine response exceeded the bounded size" }));
            return;
          }
          if (after === null || after.identitySha256 !== reviewedIdentity) {
            finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine socket identity changed during the request" }));
            return;
          }
          const body = Buffer.concat(chunks).toString("utf8");
          if (status === 204) {
            // 204 accepted only with the exact expected empty response.
            if (body !== "") { finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine 204 carried a nonempty response body" })); return; }
            finish({ ok: true, transition: true, ambiguous: false, status: 204, body, containerId, endpointIdentity: reviewedIdentity });
            return;
          }
          if (status === 304) {
            // 304 (already started / no-op) is definite only with an empty body.
            if (body !== "") { finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine 304 carried a nonempty response body" })); return; }
            finish({ ok: false, transition: false, ambiguous: false, status: 304, body, containerId, endpointIdentity: reviewedIdentity });
            return;
          }
          finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine returned an unexpected status" }));
        });
      });
    });
    // A request timeout must settle the promise directly: after destroy, no
    // later event (response 'end'/'close', request 'error') is guaranteed to
    // fire to resolve it. A timed-out start after a possible commit is
    // ambiguous — never a definite receipt, and never a hang.
    request.on("timeout", () => {
      request.destroy();
      requestError = new DockerEngineStartError("docker engine start timed out");
      if (!settled) finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine request timed out without a proven end" }));
    });
    request.on("error", (error) => {
      // A request error after the request was sent is ambiguous (a commit may
      // already have happened); never resolve a definite transition.
      requestError = requestError ?? error;
      if (!settled) finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine request errored after a possible commit" }));
    });
    // Final safety net: if the underlying connection closes without a clean,
    // proven response 'end' (e.g. an early/partial close where no response
    // object's 'aborted'/'close' handler fired), settle ambiguous directly
    // instead of leaving the promise pending on an unspecified later event.
    request.on("close", () => {
      if (!settled && !endSeen) finish(ambiguousResult({ containerId, status, endpointIdentity: before.identitySha256, reason: "docker engine connection closed before a proven end" }));
    });
    request.end();
  });
}

function ambiguousResult({ containerId, status, endpointIdentity, reason }) {
  return Object.freeze({ ok: false, transition: null, ambiguous: true, status, body: null, containerId, endpointIdentity: endpointIdentity ?? null, reason });
}

export function classifyStartResult(result) {
  if (!result || typeof result !== "object" || Array.isArray(result)) return { ok: false, status: "ambiguous", transition: null };
  if (canonical(Object.keys(result).sort()) !== canonical([...DEFINITE_RESULT_KEYS].sort())) return { ok: false, status: "ambiguous", transition: null };
  if (result.ambiguous !== false) return { ok: false, status: "ambiguous", transition: null };
  if (result.status === 204) {
    if (result.ok !== true || result.transition !== true || result.body !== "" || !EXACT_SHA_RE.test(result.containerId ?? "") || !EXACT_SHA_RE.test(result.endpointIdentity ?? "")) return { ok: false, status: "ambiguous", transition: null };
    return { ok: true, status: "started", transition: true };
  }
  if (result.status === 304) {
    if (result.ok !== false || result.transition !== false || result.body !== "" || !EXACT_SHA_RE.test(result.containerId ?? "") || !EXACT_SHA_RE.test(result.endpointIdentity ?? "")) return { ok: false, status: "ambiguous", transition: null };
    return { ok: false, status: "already-started", transition: false };
  }
  return { ok: false, status: "unexpected-response", transition: null };
}

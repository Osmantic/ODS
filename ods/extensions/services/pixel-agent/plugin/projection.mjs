// Pixel ODS status projection reader.
//
// Reads a single fixed, sanitized status file (written by the pixel-agent host
// ingress) and validates it against a strict schema. This module is
// dependency-free (Node built-ins only), importable for tests, and returns
// freshly constructed objects only. It never touches the network, spawns
// child processes, runs a shell, writes anything, traverses directories, or
// accepts a path from a caller.

import fs from "node:fs";
import { isAbsolute } from "node:path";
import { TextDecoder } from "node:util";

// ---------------------------------------------------------------------------
// Fixed constants
// ---------------------------------------------------------------------------

const MAX_FILE_BYTES = 64 * 1024; // 64 KiB projection cap
const MAX_STALE_MS = 2 * 60 * 1000; // 2 minute staleness window
const MAX_APPS = 64;
const EXPECTED_SERVICE = "pixel-agent";
const ODS_VERSION_RE = /^(?:unknown|[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)$/;
const MODEL_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+:/ -]{0,255}$/;
const APP_PORT_KEYS = Object.freeze([
  "dashboard", "webui", "searxng", "perplexica", "whisper", "tts", "n8n",
  "qdrant", "embeddings", "litellm", "llama", "privacy_shield", "token_spy",
  "ape", "hermes_proxy",
]);
const APP_PORT_KEY_SET = new Set(APP_PORT_KEYS);

const APP_DIRECTORY = new Map();
function registerApp(names, displayName, purpose, portKey, urlPath = "/") {
  for (const name of names) {
    APP_DIRECTORY.set(name, { display_name: displayName, purpose, port_key: portKey, url_path: urlPath });
  }
}
registerApp(["ods-dashboard", "dashboard"], "Dashboard", "ODS control center", "dashboard");
registerApp(
  ["ods-open-webui", "open-webui", "openwebui", "ods-webui"],
  "Open WebUI",
  "default chat interface",
  "webui"
);
registerApp(["ods-searxng", "searxng"], "SearXNG", "private web search", "searxng");
registerApp(["ods-perplexica"], "Perplexica", "deep research", "perplexica");
registerApp(["ods-n8n", "n8n"], "n8n", "workflow automation", "n8n");
registerApp(["ods-qdrant", "qdrant"], "Qdrant", "vector database", "qdrant", "/dashboard");
registerApp(["ods-tts", "tts"], "Kokoro", "text-to-speech API", "tts", "/docs");
registerApp(["ods-whisper", "whisper"], "Whisper", "speech-to-text API", "whisper", "/docs");
registerApp(["ods-embeddings", "embeddings"], "TEI", "embeddings API", "embeddings", "/info");
registerApp(["ods-litellm", "litellm"], "LiteLLM", "model gateway", "litellm");
registerApp(["ods-llama-server", "llama-server"], "llama-server", "local model API", "llama");
registerApp(["ods-privacy-shield"], "Privacy Shield", "PII protection API", "privacy_shield", "/docs");
registerApp(["ods-token-spy"], "Token Spy", "usage monitor", "token_spy", "/dashboard");
registerApp(["ods-ape"], "APE", "agent policy engine", "ape");
registerApp(["ods-hermes-proxy", "hermes-proxy"], "Hermes Auth Proxy", "Hermes authentication proxy", "hermes_proxy");

// The fixed ODS service allowlist. Must stay in lockstep with the allowlist
// enforced by the pixel-agent host ingress (host/pixel_ingress.mjs).
const ALLOWED_SERVICES = new Set([
  "ods-pixel-edge",
  "pixel-edge",
  "ods-pixel-agent",
  "pixel-agent",
  "ods-openclaw",
  "openclaw",
  "ods-hermes",
  "hermes",
  "ods-hermes-proxy",
  "hermes-proxy",
  "ods-open-webui",
  "open-webui",
  "openwebui",
  "ods-dashboard",
  "dashboard",
  "ods-dashboard-api",
  "dashboard-api",
  "ods-llama-server",
  "llama-server",
  "ods-searxng",
  "searxng",
  "ods-langfuse",
  "langfuse",
  "ods-litellm",
  "litellm",
  "ods-qdrant",
  "qdrant",
  "ods-n8n",
  "n8n",
  "ods-model-router",
  "model-router",
  "ods-tts",
  "tts",
  "ods-whisper",
  "whisper",
  "ods-embeddings",
  "embeddings",
  "ods-opencode",
  "opencode",
  "ods-ape",
  "ods-perplexica",
  "ods-privacy-shield",
  "ods-remote-provider-egress",
  "ods-remote-provider-ssh-tunnel",
  "ods-token-spy",
  "ods-webui",
]);

const STATUS_ENUM = new Set(["running", "healthy", "unhealthy", "starting", "stopped"]);
const DOCKER_ENUM = new Set(["ok", "unavailable"]);

// Top-level keys the projection may carry. Anything else is rejected.
const ALLOWED_TOP_KEYS = new Set([
  "schema_version",
  "timestamp",
  "service",
  "ods_version",
  "ingress_ready",
  "gateway_reachable",
  "docker",
  "online_apps",
  "app_ports",
  "runtime",
  "apps",
]);

// App object may carry only these keys.
const ALLOWED_APP_KEYS = new Set(["name", "status"]);
const ALLOWED_RUNTIME_KEYS = new Set(["model", "context_length"]);

// ---------------------------------------------------------------------------
// Injectable filesystem primitives (tests substitute fakes). Defaults resolve
// to the real Node primitives.
// ---------------------------------------------------------------------------

const defaultFs = {
  lstat: (p) => fs.promises.lstat(p),
  open: (p, flags) => fs.promises.open(p, flags),
  readFile: (fd) => fd.readFile(),
  stat: (fd) => fd.stat(),
  ownerUid: typeof process.geteuid === "function"
    ? process.geteuid()
    : (typeof process.getuid === "function" ? process.getuid() : 0),
};

// The O_NOFOLLOW flag, when available, prevents following the final symlink
// at open time.
const O_NOFOLLOW =
  typeof fs.constants !== "undefined" && fs.constants.O_NOFOLLOW
    ? fs.constants.O_NOFOLLOW
    : 0;

async function closeFd(fd) {
  if (fd && typeof fd.close === "function") {
    try {
      await fd.close();
    } catch {
      // Validation is already fail-closed; suppress close errors.
    }
  }
}

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

// A rejection carrying a generic, user-safe message. Never includes a path,
// raw content, or any detail that could leak the environment.
export class ProjectionError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProjectionError";
  }
}

function fail(message) {
  return Promise.reject(new ProjectionError(message));
}

// ---------------------------------------------------------------------------
// Low-level safe file open with race protection
// ---------------------------------------------------------------------------

// Open the fixed path with symlink and race protection, then return a handle
// to the regular, root-owned, non-writable file (or a structured error).
async function openProjectionFile(file, deps) {
  let lst;
  try {
    lst = await deps.lstat(file);
  } catch (err) {
    if (err && err.code === "ENOENT") return fail("status projection unavailable");
    return fail("status projection unavailable");
  }

  // Never follow a symlink placed at the fixed path.
  if (lst.isSymbolicLink()) {
    return fail("status projection unavailable");
  }

  // Reject a non-regular path (socket, fifo, device, directory).
  if (!lst.isFile()) {
    return fail("status projection unavailable");
  }

  let fd;
  try {
    fd = await deps.open(file, O_NOFOLLOW !== 0 ? O_NOFOLLOW : "r");
  } catch (err) {
    return fail("status projection unavailable");
  }

  // Re-stat the opened handle and compare against the lstat to detect a
  // replacement race (path swapped between lstat and open).
  let st;
  try {
    st = await deps.stat(fd);
  } catch (err) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }
  if (
    st.dev !== lst.dev ||
    st.ino !== lst.ino ||
    st.mtimeMs !== lst.mtimeMs ||
    st.size !== lst.size
  ) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }
  if (!st.isFile()) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }

  // The projection must be owned by the gateway service identity reading it
  // and carry no group/world write bits.
  if (!Number.isSafeInteger(deps.ownerUid) || st.uid !== deps.ownerUid) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }
  if (st.mode & 0o022) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }

  if (!Number.isSafeInteger(st.size) || st.size < 0 || st.size > MAX_FILE_BYTES) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }
  return fd;
}

// ---------------------------------------------------------------------------
// Validation of parsed JSON
// ---------------------------------------------------------------------------

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function onlyKeys(obj, allowed) {
  for (const key of Object.keys(obj)) {
    if (!allowed.has(key)) return false;
  }
  return true;
}

function validTimestamp(value, nowMs) {
  if (typeof value !== "string") return false;
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return false;
  const age = nowMs - ts;
  if (age < 0) return false; // future timestamp
  if (age > MAX_STALE_MS) return false; // stale
  return true;
}

function validApp(app) {
  if (!isPlainObject(app)) return false;
  if (!onlyKeys(app, ALLOWED_APP_KEYS)) return false;
  if (typeof app.name !== "string" || !ALLOWED_SERVICES.has(app.name)) return false;
  if (typeof app.status !== "string" || !STATUS_ENUM.has(app.status)) return false;
  return true;
}

function validRuntime(runtime) {
  if (runtime === null) return true;
  if (!isPlainObject(runtime) || !onlyKeys(runtime, ALLOWED_RUNTIME_KEYS)) return false;
  if (typeof runtime.model !== "string" || !MODEL_NAME_RE.test(runtime.model)) return false;
  return (
    Number.isInteger(runtime.context_length) &&
    runtime.context_length >= 4096 &&
    runtime.context_length <= 10_000_000
  );
}

function validAppPorts(value) {
  if (!isPlainObject(value) || !onlyKeys(value, APP_PORT_KEY_SET)) return false;
  if (Object.keys(value).length !== APP_PORT_KEYS.length) return false;
  return APP_PORT_KEYS.every((key) => (
    Number.isInteger(value[key]) && value[key] >= 1 && value[key] <= 65535
  ));
}

function enrichApp(app, appPorts) {
  const metadata = APP_DIRECTORY.get(app.name);
  if (!metadata) return { name: app.name, status: app.status };
  return {
    name: app.name,
    status: app.status,
    display_name: metadata.display_name,
    purpose: metadata.purpose,
    url: `http://localhost:${appPorts[metadata.port_key]}${metadata.url_path}`,
  };
}

// Validate a fully parsed projection object against the strict schema.
// Returns null on success, or a generic message on failure.
function validateProjection(obj, nowMs) {
  if (!isPlainObject(obj)) return "malformed projection";
  if (!onlyKeys(obj, ALLOWED_TOP_KEYS)) return "malformed projection";

  if (obj.schema_version !== 2) return "malformed projection";
  if (obj.service !== EXPECTED_SERVICE) return "malformed projection";
  if (typeof obj.ods_version !== "string" || !ODS_VERSION_RE.test(obj.ods_version)) {
    return "malformed projection";
  }
  if (!validTimestamp(obj.timestamp, nowMs)) return "stale or invalid timestamp";
  if (typeof obj.ingress_ready !== "boolean") return "malformed projection";
  if (typeof obj.gateway_reachable !== "boolean") return "malformed projection";
  if (typeof obj.docker !== "string" || !DOCKER_ENUM.has(obj.docker)) {
    return "malformed projection";
  }
  if (!Array.isArray(obj.apps)) return "malformed projection";
  if (obj.apps.length > MAX_APPS) return "malformed projection";
  if (!Number.isInteger(obj.online_apps) || obj.online_apps < 0) {
    return "malformed projection";
  }
  if (!validAppPorts(obj.app_ports)) return "malformed projection";
  if (!validRuntime(obj.runtime)) return "malformed projection";

  const seen = new Set();
  for (const app of obj.apps) {
    if (!validApp(app)) return "malformed projection";
    if (seen.has(app.name)) return "malformed projection"; // duplicate
    seen.add(app.name);
  }
  const onlineApps = obj.apps.filter(
    ({ status }) => status === "healthy" || status === "running"
  ).length;
  if (obj.online_apps !== onlineApps) return "malformed projection";
  if (obj.docker === "unavailable" && obj.apps.length !== 0) {
    return "malformed projection";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Public read entrypoint
// ---------------------------------------------------------------------------

// Read and validate the status projection at the fixed path. Resolves to a
// freshly constructed projection object, or rejects with ProjectionError on
// any failure. `deps` and `nowMs` are injectable for tests.
export async function readProjection(file, deps = defaultFs, nowMs = Date.now()) {
  const fd = await openProjectionFile(file, deps);
  if (fd instanceof Error) throw fd;
  let raw;
  try {
    raw = await deps.readFile(fd);
  } catch (err) {
    await closeFd(fd);
    return fail("status projection unavailable");
  }
  await closeFd(fd);

  if (Buffer.isBuffer(raw) && raw.length > MAX_FILE_BYTES) {
    return fail("status projection unavailable");
  }
  if (Buffer.isBuffer(raw)) {
    try {
      raw = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    } catch {
      return fail("status projection unavailable");
    }
  }
  if (typeof raw !== "string") return fail("status projection unavailable");
  if (Buffer.byteLength(raw, "utf8") > MAX_FILE_BYTES) {
    return fail("status projection unavailable");
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    return fail("status projection unavailable");
  }

  const reason = validateProjection(parsed, nowMs);
  if (reason) return fail("status projection unavailable");

  // Build a brand-new object; never return raw values or the parsed object.
  const appPorts = Object.fromEntries(APP_PORT_KEYS.map((key) => [key, parsed.app_ports[key]]));
  const apps = parsed.apps.map((app) => enrichApp(app, appPorts));
  const runtime = parsed.runtime === null
    ? null
    : { model: String(parsed.runtime.model), context_length: parsed.runtime.context_length };
  return {
    schema_version: 2,
    timestamp: String(parsed.timestamp),
    service: EXPECTED_SERVICE,
    ods_version: String(parsed.ods_version),
    ingress_ready: parsed.ingress_ready === true,
    gateway_reachable: parsed.gateway_reachable === true,
    docker: String(parsed.docker),
    online_app_count: parsed.online_apps,
    app_ports: appPorts,
    runtime,
    app_count: apps.length,
    apps,
    stale: false,
    boundary: "status-only",
  };
}

// Convert an already validated projection into the exact public tool payloads.
// Keeping these builders beside the validator makes the model-visible schema
// directly unit-testable without loading the OpenClaw plugin runtime.
export function statusPayload(projection) {
  // Keep the common status tool small enough for constrained local models.
  // Detailed per-application rows remain available through appsPayload and
  // pixel_ods_apps_list; this surface needs only the validated aggregate.
  return {
    status: "ok",
    ingress_ready: projection.ingress_ready,
    gateway_reachable: projection.gateway_reachable,
    docker: projection.docker,
    ods_version: projection.ods_version,
    online_app_count: projection.online_app_count,
    runtime: projection.runtime,
    app_count: projection.app_count,
    timestamp: projection.timestamp,
    stale: projection.stale,
    boundary: projection.boundary,
  };
}

export function appsPayload(projection) {
  return {
    app_count: projection.app_count,
    online_app_count: projection.online_app_count,
    apps: projection.apps,
    timestamp: projection.timestamp,
    stale: projection.stale,
    boundary: projection.boundary,
  };
}

// Resolve the fixed status path from the environment. Exported for tests and
// for the plugin entry.
export function statusFileFromEnv(env = process.env) {
  const value = env.PIXEL_ODS_STATUS_FILE ?? "/run/ods-pixel/ods-status.json";
  if (typeof value !== "string" || !isAbsolute(value) || value.includes("\0")) {
    throw new ProjectionError("status projection unavailable");
  }
  return value;
}

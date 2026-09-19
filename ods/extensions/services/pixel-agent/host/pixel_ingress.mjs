// Pixel Agent host ingress.
//
// Dependency-free (Node 20+ built-ins only) Unix-domain-socket HTTP ingress
// that exposes a single Pixel gateway to a restricted group over
// POST /v1/chat/completions and GET /health. It never listens on TCP, never
// reads inbound headers, never forwards unknown request fields, and never
// exposes the operator gateway token to callers.
//
// This file is importable for tests and only starts the server when run
// directly as the main module.

import http from "node:http";
import fs from "node:fs";
import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import path from "node:path";
import os from "node:os";
import { Readable } from "node:stream";
import { pathToFileURL } from "node:url";
import { parseTaskActivity } from "./task_activity_schema.mjs";
import { parseQuestions } from "./questions_schema.mjs";
import {createChatHistoryLedger,HistoryError} from './chat_history_ledger.mjs';
import {handleAccessMode, handleModelControl, readAccessOwnerKey} from './access_mode_relay.mjs';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_BODY = 2 * 1024 * 1024; // 2 MiB request body cap
const MAX_HISTORY_BODY = 8 * 1024 * 1024;
const MAX_NONSTREAM_RESPONSE = 2 * 1024 * 1024; // 2 MiB non-stream response cap
const MAX_STREAM_RESPONSE = 4 * 1024 * 1024; // 4 MiB terminal completion cap for SSE clients
// A broad typed host report can legitimately include bounded summaries for
// processes, services, mounts, interfaces, routes, and listening endpoints.
// Keep this channel far below the normal completion cap while allowing the
// guard's structurally rendered evidence to cross the private ingress intact.
const MAX_VERIFICATION_TEXT = 32 * 1024;
const MAX_VERIFICATION_RESPONSE = 1024 * 1024;
const OPENAI_RUN_ID = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE =
  "operations-unavailable-zero-submissions";
const CONNECT_TIMEOUT_MS = 5000;
// OpenClaw's ODS-owned provider is capped at 30 minutes. Keep the private
// ingress one bounded step outside that ceiling so CPU-only prefill can finish
// and the gateway, rather than an intermediate proxy, owns terminal timeout.
const TOTAL_TIMEOUT_MS = 1920000;
const GATEWAY_PROBE_TIMEOUT_MS = 2000;
const GATEWAY_ABORT_TIMEOUT_MS = 5000;
const GATEWAY_ABORT_RETRY_MS = 100;
const GATEWAY_ABORT_MAX_ATTEMPTS = 30;
const DOCKER_TIMEOUT_MS = 10000;
const MAX_TOKEN_LEN = 4096;
const MAX_CANCEL_BODY = 256;
const MAX_STATUS_INTERVAL_MS = 86400000; // 1 day
const STATUS_MODE = 0o640; // group-readable, service-owner-writable projection
const ODS_VERSION_RE = /^(?:unknown|[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)$/;
const MODEL_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}$/;

// Only nonsecret, user-facing ODS ports are projected. Values are captured by
// the installer after port resolution so Pixel does not guess default ports
// when an installation has been customized or automatically remapped.
const APP_PORT_ENV = Object.freeze({
  dashboard: ["PIXEL_ODS_DASHBOARD_PORT", 3001],
  webui: ["PIXEL_ODS_WEBUI_PORT", 3000],
  searxng: ["PIXEL_ODS_SEARXNG_PORT", 8888],
  perplexica: ["PIXEL_ODS_PERPLEXICA_PORT", 3004],
  whisper: ["PIXEL_ODS_WHISPER_PORT", 9000],
  tts: ["PIXEL_ODS_TTS_PORT", 8880],
  n8n: ["PIXEL_ODS_N8N_PORT", 5678],
  qdrant: ["PIXEL_ODS_QDRANT_PORT", 6333],
  embeddings: ["PIXEL_ODS_EMBEDDINGS_PORT", 8090],
  litellm: ["PIXEL_ODS_LITELLM_PORT", 4000],
  llama: ["PIXEL_ODS_LLAMA_PORT", 11434],
  privacy_shield: ["PIXEL_ODS_PRIVACY_SHIELD_PORT", 8085],
  token_spy: ["PIXEL_ODS_TOKEN_SPY_PORT", 3005],
  ape: ["PIXEL_ODS_APE_PORT", 7890],
  hermes_proxy: ["PIXEL_ODS_HERMES_PROXY_PORT", 9120],
});

function appPortsFromEnv(env = process.env) {
  return Object.fromEntries(Object.entries(APP_PORT_ENV).map(([key, [envName, fallback]]) => {
    const value = Number(env[envName] || fallback);
    if (!Number.isInteger(value) || value < 1 || value > 65535) {
      throw new Error(`invalid ODS application port: ${key}`);
    }
    return [key, value];
  }));
}

// The only request-body fields that may reach the gateway. Everything else is
// dropped on construction.
const ALLOWED_FIELDS = {
  messages: validMessages,
  stream: (v) => typeof v === "boolean",
  temperature: (v) => typeof v === "number" && Number.isFinite(v),
  top_p: (v) => typeof v === "number" && Number.isFinite(v),
  max_tokens: (v) => Number.isInteger(v) && v > 0,
  stop: (v) =>
    typeof v === "string" ||
    (Array.isArray(v) && v.every((s) => typeof s === "string")),
  tools: (v) =>
    Array.isArray(v) &&
    v.every((o) => o && typeof o === "object" && !Array.isArray(o)),
  tool_choice: (v) =>
    typeof v === "string" || (v && typeof v === "object" && !Array.isArray(v)),
  response_format: (v) => v && typeof v === "object" && !Array.isArray(v),
};

// A message is valid for OpenAI chat only when it carries a nonempty role and
// either a string content or an array of content parts. Anything nested and
// malformed is rejected instead of forwarded.
function validMessage(m) {
  if (!m || typeof m !== "object" || Array.isArray(m)) return false;
  if (typeof m.role !== "string" || m.role.length === 0) return false;
  const c = m.content;
  if (typeof c === "string") return true;
  if (Array.isArray(c)) {
    return (
      c.length > 0 &&
      c.every(
        (part) =>
          part &&
          typeof part === "object" &&
          !Array.isArray(part) &&
          typeof part.type === "string" &&
          part.type.length > 0
      )
    );
  }
  return false;
}

function validMessages(v) {
  return Array.isArray(v) && v.length > 0 && v.every(validMessage);
}

// Fixed allowlist of ODS service names/statuses the status projection may
// report. Container names outside this list are never surfaced.
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

// Status enum the projection may use. Never raw Docker status strings.
const STATUS_ENUM = new Set(["running", "healthy", "unhealthy", "starting", "stopped"]);

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Injectable dependencies (defaults to the real host primitives). Tests pass
// a `deps` object with fakes for deterministic behavior.
// ---------------------------------------------------------------------------

// Node's built-in fetch is backed by Undici, whose implicit response-body idle
// timeout is five minutes. A CPU-only OpenClaw turn can legitimately produce no
// bytes for longer than that while evaluating its first prompt. Use the core
// HTTP client for this fixed loopback hop so the explicit connect and total
// AbortController budgets below are the only transport deadlines.
function directGatewayFetch(url, options = {}) {
  return new Promise((resolve, reject) => {
    let connectTimer = null;
    const clearConnectTimer = () => {
      if (connectTimer !== null) {
        clearTimeout(connectTimer);
        connectTimer = null;
      }
    };
    const request = http.request(
      url,
      {
        method: options.method,
        headers: options.headers,
        signal: options.signal,
        agent: false,
      },
      (response) => {
        clearConnectTimer();
        const headers = {
          get(name) {
            const value = response.headers[String(name).toLowerCase()];
            if (Array.isArray(value)) return value.join(", ");
            return typeof value === "string" ? value : null;
          },
        };
        resolve({
          status: response.statusCode || 0,
          headers,
          body: Readable.toWeb(response),
        });
      }
    );
    request.once("socket", (socket) => {
      if (!socket.connecting) {
        clearConnectTimer();
        return;
      }
      socket.once("connect", clearConnectTimer);
    });
    request.once("error", (error) => {
      clearConnectTimer();
      reject(error);
    });
    connectTimer = setTimeout(() => {
      request.destroy(new Error("gateway connect timeout"));
    }, CONNECT_TIMEOUT_MS);
    connectTimer.unref?.();
    request.end(options.body);
  });
}

// A host can have different IPv4/IPv6 loopback paths (including WSL's IPv4
// forwarding proxy). Discover a healthy local listener with a read-only GET
// before sending any body. A reset after a POST is an unknown outcome: only
// the next request may discover another endpoint, never replay that POST.
export function createLoopbackGatewayFetch(fetchImpl = directGatewayFetch, {
  probeTimeoutMs = 750, cacheMs = 5000, now = Date.now,
} = {}) {
  const endpoints = new Map();
  const discoveries = new Map();
  const validate = (url) => {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' || !['127.0.0.1', '[::1]', 'localhost'].includes(parsed.hostname)
        || parsed.username || parsed.password || parsed.hash) throw new Error('invalid local gateway address');
    return parsed;
  };
  const discover = async (port, headers) => {
    const cached = endpoints.get(port);
    if (cached && cached.expiresAt > now()) return cached.origin;
    if (discoveries.has(port)) return discoveries.get(port);
    const pending = (async () => {
      for (const host of ['[::1]', '127.0.0.1']) {
        const origin = `http://${host}:${port}`;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), probeTimeoutMs);
        timer.unref?.();
        try {
          const authorization = headers?.authorization ?? headers?.Authorization;
          const response = await fetchImpl(`${origin}/health`, {
            method:'GET', redirect:'error', signal:controller.signal,
            headers:authorization ? {authorization, accept:'application/json'} : {accept:'application/json'},
          });
          await drain(response.body);
          if (response.status < 200 || response.status >= 300) continue;
          endpoints.set(port, {origin, expiresAt:now() + cacheMs});
          return origin;
        } catch { /* An unqualified family receives no mutation. */ }
        finally { clearTimeout(timer); }
      }
      endpoints.delete(port);
      throw new Error('local gateway unavailable');
    })();
    discoveries.set(port, pending);
    try { return await pending; }
    finally { discoveries.delete(port); }
  };
  return async (url, options = {}) => {
    const parsed = validate(url), port = parsed.port || '80';
    if (options.signal?.aborted) throw options.signal.reason ?? new Error('request aborted');
    const origin = await discover(port, options.headers);
    if (options.signal?.aborted) throw options.signal.reason ?? new Error('request aborted');
    try {
      return await fetchImpl(`${origin}${parsed.pathname}${parsed.search}`, options);
    } catch (error) {
      // HTTP bodies/streams are never retried. Even a transport reset can
      // arrive after the gateway has accepted a mutation or started a run.
      if (endpoints.get(port)?.origin === origin) endpoints.delete(port);
      throw error;
    }
  };
}

export const gatewayFetch = createLoopbackGatewayFetch();

const defaultDeps = {
  execFile,
  fetch: gatewayFetch,
  setTimeout,
  clearTimeout,
};

// ---------------------------------------------------------------------------
// Configuration (read once at import; overridable via env)
// ---------------------------------------------------------------------------

export function validateConfig(cfg) {
  const port = Number(cfg.gatewayPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("invalid gateway port");
  }
  const interval = Number(cfg.statusIntervalMs);
  if (!Number.isInteger(interval) || interval <= 0 || interval > MAX_STATUS_INTERVAL_MS) {
    throw new Error("invalid status interval");
  }
  if (cfg.ingressGid != null) {
    const gid = Number(cfg.ingressGid);
    if (!Number.isInteger(gid) || gid < 0) {
      throw new Error("invalid ingress gid");
    }
  }
  if (typeof cfg.odsVersion !== "string" || !ODS_VERSION_RE.test(cfg.odsVersion)) {
    throw new Error("invalid ODS version");
  }
  const appPorts = cfg.appPorts ?? appPortsFromEnv({});
  if (
    !appPorts ||
    typeof appPorts !== "object" ||
    Array.isArray(appPorts) ||
    Object.keys(appPorts).length !== Object.keys(APP_PORT_ENV).length
  ) {
    throw new Error("invalid ODS application ports");
  }
  for (const key of Object.keys(APP_PORT_ENV)) {
    const value = appPorts[key];
    if (!Number.isInteger(value) || value < 1 || value > 65535) {
      throw new Error(`invalid ODS application port: ${key}`);
    }
  }
  for (const [label, value] of [
    ["socket path", cfg.socketPath],
    ["gateway token file", cfg.gatewayTokenFile],
    ["status file", cfg.statusFile],
    ...(cfg.chatStateDir ? [["chat state directory",cfg.chatStateDir]] : []),
    ...(cfg.accessOwnerKeyFile ? [["access owner key file",cfg.accessOwnerKeyFile]] : []),
  ]) {
    if (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) {
      throw new Error(`invalid ${label}`);
    }
  }
  return cfg;
}

export function configFromEnv(env = process.env) {
  const cfg = {
    socketPath: env.PIXEL_INGRESS_SOCKET || "/run/ods-pixel/pixel-ingress.sock",
    gatewayTokenFile:
      env.PIXEL_GATEWAY_TOKEN_FILE || "/etc/pixel/openclaw.json",
    gatewayPort: Number(env.PIXEL_GATEWAY_PORT || "18789"),
    statusFile: env.PIXEL_STATUS_FILE || "/run/ods-pixel/ods-status.json",
    statusIntervalMs: Number(env.PIXEL_STATUS_INTERVAL_MS || "30000"),
    ingressGid: env.PIXEL_INGRESS_GID ? Number(env.PIXEL_INGRESS_GID) : null,
    odsVersion: env.PIXEL_ODS_VERSION || "unknown",
    appPorts: appPortsFromEnv(env),
    accessOwnerKeyFile: env.PIXEL_ACCESS_OWNER_KEY_FILE || null,
    chatStateDir: env.PIXEL_CHAT_STATE_DIR || (process.platform === 'linux' ? '/var/lib/ods-pixel-chat' : path.join(process.platform === 'win32' ? env.LOCALAPPDATA || os.homedir() : path.join(os.homedir(),'Library','Application Support'),'ODS','chat-state')),
  };
  return validateConfig(cfg);
}

// ---------------------------------------------------------------------------
// Gateway token: read only from the explicit PIXEL_GATEWAY_TOKEN_FILE. ODS
// points this at Pixel's owner-private OpenClaw JSON; the env-file form remains
// accepted for isolated tests and controlled migrations. Refuse symlinks,
// non-regular files, group/world-readable modes, or an owner other than the
// process euid. The token itself must be whitespace-free, control-free, and
// bounded in length.
// ---------------------------------------------------------------------------

export function gatewayRuntimeFromConfig(config) {
  const agents = config?.agents?.list;
  const providers = config?.models?.providers;
  if (!Array.isArray(agents) || !providers || typeof providers !== "object" || Array.isArray(providers)) {
    return null;
  }
  const selected = agents.filter((agent) => agent?.id === "pixel");
  if (selected.length !== 1 || typeof selected[0].model !== "string") return null;
  const separator = selected[0].model.indexOf("/");
  if (separator < 1) return null;
  const providerId = selected[0].model.slice(0, separator);
  const modelId = selected[0].model.slice(separator + 1);
  if (!new Set(["ods-local", "ods-gateway"]).has(providerId) || !modelId) return null;
  const provider = providers[providerId];
  if (!provider || typeof provider !== "object" || Array.isArray(provider)) return null;
  if (!Array.isArray(provider.models) || provider.models.length !== 1) return null;
  const model = provider.models[0];
  if (!model || typeof model !== "object" || Array.isArray(model) || model.id !== modelId) return null;
  let concreteModel;
  if (providerId === "ods-local") {
    if (model.name !== `ODS Local ${modelId}`) return null;
    concreteModel = modelId;
  } else {
    const label = modelId === "ods/current" ? "Current" : "Default";
    if (!["default", "ods/current"].includes(modelId) || typeof model.name !== "string") return null;
    const display = model.name.match(
      new RegExp(`^ODS ${label} \\(([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255})\\)$`)
    );
    if (!display) return null;
    concreteModel = display[1];
  }
  if (
    !MODEL_NAME_RE.test(concreteModel) ||
    !Number.isInteger(model.contextWindow) ||
    model.contextWindow < 4096 ||
    model.contextWindow > 10_000_000 ||
    !Number.isInteger(model.maxTokens) ||
    model.maxTokens < 1 ||
    model.maxTokens > model.contextWindow ||
    typeof model.reasoning !== "boolean"
  ) {
    return null;
  }
  return { model: concreteModel, context_length: model.contextWindow };
}

export function readGatewayConfiguration(
  file,
  euid = typeof process.geteuid === "function"
    ? process.geteuid()
    : (typeof process.getuid === "function" ? process.getuid() : 0)
) {
  const noFollow = fs.constants.O_NOFOLLOW;
  const before = fs.lstatSync(file);
  if (before.isSymbolicLink()) {
    throw new Error("refusing symlink token file");
  }
  if (!before.isFile()) {
    throw new Error("token path is not a regular file");
  }
  let fd;
  let raw;
  try {
    try {
      const flags = fs.constants.O_RDONLY | (typeof noFollow === "number" ? noFollow : 0);
      fd = fs.openSync(file, flags);
    } catch (error) {
      if (error?.code === "ELOOP") {
        throw new Error("refusing symlink token file");
      }
      throw error;
    }
    const st = fs.fstatSync(fd);
    const after = fs.lstatSync(file);
    if (after.isSymbolicLink()) {
      throw new Error("refusing symlink token file");
    }
    if (!st.isFile()) {
      throw new Error("token path is not a regular file");
    }
    if (st.dev !== after.dev || st.ino !== after.ino) {
      throw new Error("token file changed during secure open");
    }
    if (st.uid !== euid) {
      throw new Error("token file owner mismatch");
    }
    // Group-read (0o040) or world-read (0o004) => refuse. Only owner rw remains.
    if (st.mode & 0o077) {
      throw new Error("token file is group/world readable");
    }
    raw = fs.readFileSync(fd, "utf8");
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  let value = "";
  let runtime = null;
  let runtimeAuthoritative = false;
  if (raw.trimStart().startsWith("{")) {
    let config;
    try {
      config = JSON.parse(raw);
    } catch {
      throw new Error("gateway token file contains invalid JSON");
    }
    value = config?.gateway?.auth?.token ?? config?.gateway?.token ?? "";
    runtime = gatewayRuntimeFromConfig(config);
    runtimeAuthoritative = true;
  } else {
    for (const line of raw.split(/\r?\n/)) {
      if (!line.trim().startsWith("PIXEL_GATEWAY_TOKEN=")) continue;
      value = line.slice(line.indexOf("=") + 1);
      break;
    }
  }
  if (typeof value !== "string" || value.length === 0) {
    throw new Error("gateway token missing or empty");
  }
  if (value.trim() !== value) {
    throw new Error("token has surrounding whitespace");
  }
  if (/[\x00-\x1f\x7f]/.test(value)) {
    throw new Error("token contains control characters");
  }
  if (value.length > MAX_TOKEN_LEN) {
    throw new Error("token too long");
  }
  return { token: value, runtime, runtimeAuthoritative };
}

export function readGatewayToken(file, euid) {
  return readGatewayConfiguration(file, euid).token;
}

// ---------------------------------------------------------------------------
// Socket setup: remove only an existing socket owned by this service path;
// reject any other existing non-socket path. Never listen on TCP.
// ---------------------------------------------------------------------------

export function prepareSocketPath(socketPath) {
  let lst;
  try {
    lst = fs.lstatSync(socketPath);
  } catch (err) {
    if (err.code === "ENOENT") return; // clean
    throw err;
  }
  if (!lst.isSocket()) {
    throw new Error("refusing non-socket path at ingress socket");
  }
  fs.unlinkSync(socketPath); // safe: it is a socket at our service path
}

// ---------------------------------------------------------------------------
// Body reading with a hard cap.
// ---------------------------------------------------------------------------

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const cl = req.headers["content-length"];
    if (cl !== undefined) {
      const length = Number(cl);
      if (!Number.isSafeInteger(length) || length < 0) {
        reject(new HttpError(400, "invalid content length"));
        return;
      }
      if (length > limit) {
        reject(new HttpError(413, "request too large"));
        return;
      }
    }
    const chunks = [];
    let total = 0;
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
      req.removeAllListeners("data");
      req.resume();
    };
    req.on("data", (c) => {
      if (settled) return;
      total += c.length;
      if (total > limit) {
        fail(new HttpError(413, "request too large"));
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      if (settled) return;
      settled = true;
      resolve(Buffer.concat(chunks));
    });
    req.on("error", (error) => fail(error));
  });
}

// ---------------------------------------------------------------------------
// Allowlisted request construction.
// ---------------------------------------------------------------------------

export function buildOutgoing(parsed, sessionUser) {
  const out = { model: "openclaw/default" };
  for (const key of Object.keys(ALLOWED_FIELDS)) {
    if (!Object.prototype.hasOwnProperty.call(parsed, key)) continue;
    const v = parsed[key];
    if (v === undefined || !ALLOWED_FIELDS[key](v)) {
      throw new HttpError(400, "invalid request body");
    }
    out[key] = v;
  }
  if (sessionUser) out.user = sessionUser;
  return out;
}

export function computeSessionUser(parsed) {
  const md =
    parsed.metadata && typeof parsed.metadata === "object" && !Array.isArray(parsed.metadata)
      ? parsed.metadata
      : {};
  let chosen;
  if (typeof parsed.user === "string" && parsed.user.length > 0) {
    chosen = parsed.user;
  } else if (typeof md.chat_id === "string" && md.chat_id.length > 0) {
    chosen = md.chat_id;
  } else if (typeof md.conversation_id === "string" && md.conversation_id.length > 0) {
    chosen = md.conversation_id;
  }
  if (chosen === undefined) return null;
  return "ods-" + createHash("sha256").update(chosen, "utf8").digest("hex");
}

// ---------------------------------------------------------------------------
// Upstream forwarding. Fixed loopback only; fresh allowlisted headers; bounded
// connect/body/stream behavior; generic sanitized errors.
// ---------------------------------------------------------------------------

function upstreamHeaders(stream, token) {
  return {
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
    accept: stream ? "text/event-stream" : "application/json",
  };
}

// Consume/discard an upstream body without reflecting it (used for errors).
async function drain(body) {
  if (!body || typeof body.cancel !== "function") return;
  try {
    await body.cancel();
  } catch {
    /* ignore */
  }
}

async function abortGatewayRun(user, token, gatewayPort, deps) {
  if (typeof user !== "string" || !/^ods-[0-9a-f]{64}$/.test(user)) return false;
  const controller = new AbortController();
  const timer = deps.setTimeout(() => controller.abort(), GATEWAY_ABORT_TIMEOUT_MS);
  try {
    for (let attempt = 0; attempt < GATEWAY_ABORT_MAX_ATTEMPTS; attempt += 1) {
      const response = await deps.fetch(
        `http://127.0.0.1:${gatewayPort}/pixel-ods/abort`,
        {
          method: "POST",
          headers: {
            authorization: `Bearer ${token}`,
            "content-type": "application/json",
            accept: "application/json",
          },
          body: JSON.stringify({ user }),
          redirect: "error",
          signal: controller.signal,
        }
      );
      if (response.status < 200 || response.status >= 300) {
        await drain(response.body);
        return false;
      }
      const contentType = String(response.headers.get("content-type") || "").toLowerCase();
      if (!contentType.startsWith("application/json")) {
        await drain(response.body);
        return false;
      }
      const body = await readBounded(response.body, 1024);
      const result = JSON.parse(body.toString("utf8"));
      if (
        !result ||
        typeof result !== "object" ||
        Array.isArray(result) ||
        Object.keys(result).length !== 1 ||
        typeof result.aborted !== "boolean"
      ) {
        return false;
      }
      if (result.aborted) return true;
      // before_prompt_build can run before OpenClaw publishes the active
      // user-to-session mapping. Preserve an early Stop across that narrow
      // startup race, but keep both attempts and the total request bounded.
      if (attempt + 1 < GATEWAY_ABORT_MAX_ATTEMPTS) {
        await new Promise((resolve) => {
          const retry = deps.setTimeout(resolve, GATEWAY_ABORT_RETRY_MS);
          retry.unref?.();
        });
      }
    }
    return false;
  } catch {
    return false;
  } finally {
    deps.clearTimeout(timer);
  }
}

async function readBounded(stream, limit) {
  const reader = stream.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) throw new HttpError(502, "upstream response too large");
      chunks.push(Buffer.from(value));
    }
    return Buffer.concat(chunks, total);
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

function parseVerificationResponse(value, runId) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HttpError(502, "verification state unavailable");
  }
  const status = value.status;
  if (!["none", "passed", "pending", "failed"].includes(status)) {
    throw new HttpError(502, "verification state unavailable");
  }
  const carriesAuthoritativeText =
    status === "pending" ||
    status === "failed" ||
    (status === "passed" && Object.prototype.hasOwnProperty.call(value, "text"));
  const hasRecoveryCode =
    status === "failed" &&
    value.code === OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE;
  const suppressStaleExecWarning =
    value.suppressStaleExecWarning === true &&
    (status === "none" || status === "passed");
  const hasPreview =
    (status === "passed" || status === "failed") && Object.prototype.hasOwnProperty.call(value, "preview");
  const expectedKeys = carriesAuthoritativeText
    ? hasRecoveryCode
      ? ["status", "text", "code"]
      : ["status", "text"]
    : ["status"];
  if (suppressStaleExecWarning) expectedKeys.push("suppressStaleExecWarning");
  if (hasPreview) expectedKeys.push("preview");
  const hasTask = Object.prototype.hasOwnProperty.call(value, "task");
  if (hasTask) expectedKeys.push("task");
  const hasQuestions = Object.prototype.hasOwnProperty.call(value, 'questions');
  if (hasQuestions) expectedKeys.push('questions');
  if (status === "passed" && carriesAuthoritativeText && value.deliveryMode === "append") {
    expectedKeys.push("deliveryMode");
  }
  const preview = value.preview;
  const previewKeys = [
    "bytes",
    "entrySha256",
    "files",
    "kind",
    "port",
    "relativeDirectory",
    "schemaVersion",
    "sha256",
    "siteId",
    "url",
  ];
  const previewValid =
    !hasPreview ||
    (carriesAuthoritativeText &&
      preview &&
      typeof preview === "object" &&
      !Array.isArray(preview) &&
      Object.keys(preview).sort().join("\n") === previewKeys.join("\n") &&
      preview.schemaVersion === 1 &&
      preview.kind === "ods-pixel-workspace-preview" &&
      typeof preview.relativeDirectory === "string" &&
      /^(?!\/)(?!.*(?:^|\/)\.\.?(?:\/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$/.test(
        preview.relativeDirectory
      ) &&
      /^site-[a-f0-9]{24}$/.test(preview.siteId) &&
      preview.siteId === `site-${preview.sha256?.slice(0, 24)}` &&
      Number.isInteger(preview.port) &&
      preview.port >= 1 &&
      preview.port <= 65535 &&
      preview.url ===
        `http://${preview.siteId}.localhost:${preview.port}/${preview.siteId}/` &&
      Number.isInteger(preview.files) &&
      preview.files >= 1 &&
      preview.files <= 128 &&
      Number.isInteger(preview.bytes) &&
      preview.bytes >= 1 &&
      preview.bytes <= 16 * 1024 * 1024 &&
      /^[a-f0-9]{64}$/.test(preview.sha256) &&
      /^[a-f0-9]{64}$/.test(preview.entrySha256));
  if (
    Object.keys(value).sort().join("\n") !== expectedKeys.sort().join("\n") ||
    (hasRecoveryCode && hasPreview) ||
    (carriesAuthoritativeText &&
      (typeof value.text !== "string" ||
        value.text.length < 1 ||
        value.text.length > MAX_VERIFICATION_TEXT ||
        /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value.text))) ||
    !previewValid || (hasTask && !parseTaskActivity(value.task, runId)) ||
    (hasQuestions && (status !== 'pending' || !parseQuestions(value.questions)))
  ) {
    throw new HttpError(502, "verification state unavailable");
  }
  return value;
}

async function readVerificationForRun(runId, token, gatewayPort, signal, deps) {
  if (typeof runId !== "string" || !OPENAI_RUN_ID.test(runId)) {
    throw new HttpError(502, "invalid upstream response");
  }
  const response = await deps.fetch(
    `http://127.0.0.1:${gatewayPort}/pixel-ods/verification`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({ runId }),
      redirect: "error",
      signal,
    }
  );
  if (response.status < 200 || response.status >= 300) {
    await drain(response.body);
    throw new HttpError(502, "verification state unavailable");
  }
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.startsWith("application/json")) {
    await drain(response.body);
    throw new HttpError(502, "verification state unavailable");
  }
  let parsed;
  try {
    parsed = JSON.parse((await readBounded(response.body, MAX_VERIFICATION_RESPONSE)).toString("utf8"));
  } catch {
    throw new HttpError(502, "verification state unavailable");
  }
  return parseVerificationResponse(parsed, runId);
}

async function verificationForRun(runId, token, gatewayPort, signal, deps) {
  // Only re-read the same host receipt. Never resubmit the model request or
  // replay tools when finalization and receipt availability briefly overlap.
  for (let attempt = 0; ; attempt++) {
    try {
      return await readVerificationForRun(runId, token, gatewayPort, signal, deps);
    } catch (error) {
      if (signal.aborted || attempt >= 2 || !OPENAI_RUN_ID.test(runId ?? '')) throw error;
      await new Promise(resolve => {
        const timer = deps.setTimeout(done, 100 * (attempt + 1));
        function done() { deps.clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); }
        signal.addEventListener('abort', done, {once:true});
        if (signal.aborted) done();
      });
      if (signal.aborted) throw error;
    }
  }
}

function applyVerificationToCompletion(completion, verification) {
  if (verification.deliveryMode === "append") {
    const choice = completion?.choices?.[0];
    const content = choice?.message?.content;
    if (typeof content === "string" && content.trim()) {
      // Both input components already have transport bounds. Preserve the
      // model's work summary; a verified observation is not the entire task.
      const scope = verification.preview
        ? "Publication scope: this receipt verifies the published snapshot, not functional behavior or completion of other requested work."
        : "Receipt scope: the Operations evidence above does not establish completion of other requested work.";
      const evidence = `${verification.text}\n${scope}`;
      const text = content.endsWith(evidence) ? content : `${content}\n\n${evidence}`;
      return {
        ...completion,
        choices: [{ ...choice, message: { ...choice.message, content: text } }, ...completion.choices.slice(1)],
      };
    }
  }
  if (!verification.text) {
    const content = completion?.choices?.[0]?.message?.content;
    if (!verification.suppressStaleExecWarning || typeof content !== "string") {
      return completion;
    }
    // OpenClaw 2026.6.33 can retain a failed deferred `tool_call` exec after a
    // later wrapped exec succeeds. Strip only its exact generated ODS control
    // suffix, and only when the in-process guard observed that recovery. Near
    // matches and current failures pass through unchanged.
    const cleaned = content.replace(
      /(?:\r?\n){2}⚠️ 🛠️ `\/run\/pixel-ods-control\/cancellable-exec\.sh (?:[0-9a-f]{64}|[0-9a-f]{16}…[0-9a-f]{3,16}) [A-Za-z0-9+/]+={0,2}` failed$/u,
      ""
    );
    if (cleaned === content) return completion;
    const choice = completion.choices[0];
    return {
      ...completion,
      choices: [
        {
          ...choice,
          message: { ...choice.message, content: cleaned },
        },
        ...completion.choices.slice(1),
      ],
    };
  }
  return {
    ...completion,
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: verification.text },
        finish_reason: "stop",
      },
    ],
  };
}

function completionSse(completion, verification) {
  const runId = completion?.id;
  const text = completion?.choices?.[0]?.message?.content;
  if (!OPENAI_RUN_ID.test(runId) || typeof text !== "string") {
    throw new HttpError(502, "invalid upstream response");
  }
  const model =
    typeof completion?.model === "string" && completion.model.length <= 256
      ? completion.model
      : "openclaw/default";
  const created =
    Number.isSafeInteger(completion?.created) && completion.created > 0
      ? completion.created
      : Math.floor(Date.now() / 1000);
  const terminalPixel = verification?.code === OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE
    ? {
        schemaVersion: 1,
        recovery: "clean-context",
        reason: OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE,
      }
    : verification?.preview
      ? { schemaVersion: 1, preview: verification.preview }
      : undefined;
  const envelope = (delta, finishReason, terminal = false) => JSON.stringify({
    id: runId,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
    ...(terminal && terminalPixel ? { pixel: terminalPixel } : {}),
    ...(terminal && verification?.task ? { pixel_task: verification.task } : {}),
    ...(terminal && verification?.questions ? { pixel_questions: {schemaVersion:1,questions:verification.questions} } : {}),
    ...(terminal ? {pixel_outcome: {schemaVersion:1,status:verification?.status ?? 'none'}} : {}),
  });
  return Buffer.from(
    `data: ${envelope({ role: "assistant" }, null)}\n\n` +
      `data: ${envelope({ content: text }, null)}\n\n` +
      `data: ${envelope({}, "stop", true)}\n\n` +
      "data: [DONE]\n\n",
    "utf8"
  );
}

// Side-channel observations only. Answer bytes still wait for final verification.
export function streamTaskActivity(res, user, token, gatewayPort, signal, deps = defaultDeps) {
  const since = new Date().toISOString();
  let stopped = false, timer, inFlight, last = '', runId;
  async function poll() {
    if (stopped || signal.aborted || res.destroyed || res.writableEnded) return;
    const controller = new AbortController();
    inFlight = controller;
    const abort = () => controller.abort();
    signal.addEventListener('abort', abort, {once:true});
    const deadline = deps.setTimeout(abort, 1800);
    try {
      const response = await deps.fetch(`http://127.0.0.1:${gatewayPort}/pixel-ods/activity`, {
        method:'POST', headers:upstreamHeaders(false, token), body:JSON.stringify({user}), redirect:'error', signal:controller.signal,
      });
      if (response.status !== 200 || !String(response.headers.get('content-type')).startsWith('application/json')) { await drain(response.body); return; }
      const value = JSON.parse((await readBounded(response.body, 1024 * 1024)).toString('utf8'));
      if (!value || Object.keys(value).join() !== 'task') return;
      const task = parseTaskActivity(value.task, value.task?.runId);
      if (!task || task.state !== 'running' || task.startedAt < since || (runId && task.runId !== runId)) return;
      runId = task.runId;
      const packet = JSON.stringify({object:'ods.task.activity', id:runId, pixel_task:task});
      if (!stopped && !signal.aborted && !res.destroyed && !res.writableEnded && !res.writableNeedDrain && packet !== last) {
        res.write(`data: ${packet}\n\n`); last = packet;
      }
    } catch { /* Optional telemetry must not fail or repeat the actual task. */ }
    finally {
      deps.clearTimeout(deadline); signal.removeEventListener('abort', abort); inFlight = null;
      if (!stopped && !signal.aborted && !res.destroyed && !res.writableEnded) timer = deps.setTimeout(poll, 1500);
    }
  }
  timer = deps.setTimeout(poll, 250);
  return () => { stopped = true; deps.clearTimeout(timer); inFlight?.abort(); };
}

function registerActiveGatewayTransport(activeGatewayTransports, user, controller) {
  if (!(activeGatewayTransports instanceof Map) || typeof user !== "string") {
    return () => {};
  }
  let controllers = activeGatewayTransports.get(user);
  if (!controllers) {
    controllers = new Set();
    activeGatewayTransports.set(user, controllers);
  }
  controllers.add(controller);
  return () => {
    controllers.delete(controller);
    if (controllers.size === 0 && activeGatewayTransports.get(user) === controllers) {
      activeGatewayTransports.delete(user);
    }
  };
}

function abortActiveGatewayTransports(activeGatewayTransports, user) {
  const controllers = activeGatewayTransports.get(user);
  if (!controllers) return;
  for (const controller of [...controllers]) controller.abort();
}

async function forwardChat(res, outgoing, token, gatewayPort, deps = defaultDeps, hooks = {}, activeGatewayTransports = new Map()) {
  const controller = new AbortController();
  const unregisterGatewayTransport = registerActiveGatewayTransport(activeGatewayTransports, outgoing.user, controller);
  hooks.onController?.(controller);
  const totalTimer = deps.setTimeout(() => controller.abort(), TOTAL_TIMEOUT_MS);
  const abortOnDownstreamClose = () => {
    if (!res.writableEnded) controller.abort();
  };
  // Socket close remains transport cleanup only. The explicit /v1/chat/cancel
  // control path owns run cancellation and draining, because an intermediate
  // proxy can consume a close before it reaches this response object.
  res.once("close", abortOnDownstreamClose);
  const wantsStream = outgoing.stream === true;
  let stopActivity;
  // OpenClaw's OpenAI-compatible streaming route concatenates assistant block
  // replies from every tool continuation. Its non-stream route returns only
  // the terminal assistant reply. This ingress already withholds response
  // bytes until host verification completes, so request one terminal upstream
  // completion and synthesize SSE for downstream streaming clients.
  const gatewayOutgoing = wantsStream ? { ...outgoing, stream: false } : outgoing;
  if (wantsStream) {
    // Commit only the SSE headers before OpenClaw's terminal completion is
    // available. This lets every downstream layer enter its disconnect-aware
    // streaming lifecycle immediately while answer bytes remain withheld for
    // verification and terminal-only delivery.
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-store",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.flushHeaders?.();
    stopActivity = streamTaskActivity(res, outgoing.user, token, gatewayPort, controller.signal, deps);
  }
  try {
    await hooks.beforeRequest?.(controller.signal);
    if(controller.signal.aborted) throw new HistoryError('history-preparation-interrupted',503);
    hooks.onSubmitted?.();
    const upstream = await deps.fetch(
      `http://127.0.0.1:${gatewayPort}/v1/chat/completions`,
      {
        method: "POST",
        headers: upstreamHeaders(false, token),
        body: JSON.stringify(gatewayOutgoing),
        redirect: "error",
        signal: controller.signal,
      }
    );
    if (upstream.status < 200 || upstream.status >= 300) {
      await drain(upstream.body);
      if (wantsStream) {
        res.write('data: {"error":{"message":"pixel request rejected","type":"pixel_ingress_error"}}\n\n');
        res.end("data: [DONE]\n\n");
      } else {
        sendError(res, upstream.status >= 400 && upstream.status < 500 ? 400 : 502, "pixel request rejected");
      }
      return;
    }

    const contentType = (upstream.headers.get("content-type") || "").toLowerCase();
    if (!contentType.startsWith("application/json")) {
      await drain(upstream.body);
      if (wantsStream) {
        res.write('data: {"error":{"message":"invalid upstream response","type":"pixel_ingress_error"}}\n\n');
        res.end("data: [DONE]\n\n");
      } else {
        sendError(res, 502, "invalid upstream response");
      }
      return;
    }
    if (wantsStream) {
      let deliveryStage = "completion";
      let completionRunId;
      try {
        const body = await readBounded(upstream.body, MAX_STREAM_RESPONSE);
        const completion = JSON.parse(body.toString("utf8"));
        if (typeof completion?.id === "string" && OPENAI_RUN_ID.test(completion.id)) {
          completionRunId = completion.id;
        }
        deliveryStage = "verification";
        const verification = await verificationForRun(
          completion?.id,
          token,
          gatewayPort,
          controller.signal,
          deps
        );
        await hooks.onComplete?.(completion,verification);
        deliveryStage = "delivery";
        res.end(completionSse(
          applyVerificationToCompletion(completion, verification),
          verification
        ));
      } catch {
        // Log only the fixed stage and validated opaque run ID. Never include
        // completion content, tool results, request bodies, or credentials.
        console.warn(`pixel-ingress response failed at ${deliveryStage}; run=${completionRunId ?? "unavailable"}`);
        if (!res.destroyed && !res.writableEnded) {
          res.write('data: {"error":{"message":"upstream stream failed","type":"pixel_ingress_error"}}\n\n');
          res.end("data: [DONE]\n\n");
        }
      }
      return;
    }
    const body = await readBounded(upstream.body, MAX_NONSTREAM_RESPONSE);
    let completion;
    try {
      completion = JSON.parse(body.toString("utf8"));
    } catch {
      sendError(res, 502, "invalid upstream response");
      return;
    }
    const verification = await verificationForRun(
      completion?.id,
      token,
      gatewayPort,
      controller.signal,
      deps
    );
    await hooks.onComplete?.(completion,verification);
    const verifiedCompletion = applyVerificationToCompletion(completion, verification);
    const responseBody = verifiedCompletion === completion
      ? body
      : Buffer.from(JSON.stringify(verifiedCompletion), "utf8");
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    });
    res.end(responseBody);
  } catch (error) {
    if (!res.headersSent) {
      const message = error instanceof HttpError ? error.message : "upstream unavailable";
      sendError(res, error instanceof HttpError ? error.status : 502, message);
    } else if (!res.writableEnded) {
      res.destroy();
    }
  } finally {
    stopActivity?.();
    res.off("close", abortOnDownstreamClose);
    deps.clearTimeout(totalTimer);
    unregisterGatewayTransport();
  }
}

function sendError(res, status, message) {
  if (res.headersSent) {
    if (!res.writableEnded) res.destroy();
    return;
  }
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify({ error: { message, type: "pixel_ingress_error" } }));
}

function sendJson(res, status, payload) {
  if (res.headersSent) {
    if (!res.writableEnded) res.destroy();
    return;
  }
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(payload));
}

export async function checkGatewayReachable(gatewayPort, deps = defaultDeps) {
  const controller = new AbortController();
  const timer = deps.setTimeout(() => controller.abort(), GATEWAY_PROBE_TIMEOUT_MS);
  try {
    const response = await deps.fetch(`http://127.0.0.1:${gatewayPort}/health`, {
      redirect: "error",
      signal: controller.signal,
    });
    await drain(response.body);
    return response.status >= 200 && response.status < 300;
  } catch {
    return false;
  } finally {
    deps.clearTimeout(timer);
  }
}

function execFilePromise(execImpl, command, args, options) {
  return new Promise((resolve, reject) => {
    let completed = false;
    const callback = (error, stdout = "") => {
      if (completed) return;
      completed = true;
      if (error) reject(error);
      else resolve({ stdout });
    };
    try {
      const result = execImpl(command, args, options, callback);
      if (result && typeof result.then === "function") {
        result.then((value) => {
          if (completed) return;
          completed = true;
          resolve(value || { stdout: "" });
        }, callback);
      }
    } catch (error) {
      callback(error);
    }
  });
}

function normalizedContainerStatus(record) {
  const raw = String(record.Status || "").toLowerCase();
  const state = String(record.State || "").toLowerCase();
  if (raw.includes("unhealthy")) return "unhealthy";
  if (raw.includes("health: starting") || raw.includes("starting")) return "starting";
  if (raw.includes("healthy")) return "healthy";
  if (state === "running" || raw.startsWith("up ") || raw === "up") return "running";
  if (state === "restarting" || state === "created" || raw.startsWith("restarting")) {
    return "starting";
  }
  if (
    ["exited", "dead", "paused"].includes(state) ||
    /^(?:exited|dead|created|paused)\b/.test(raw)
  ) {
    return "stopped";
  }
  return null;
}

export async function dockerApps(deps = defaultDeps) {
  const { stdout } = await execFilePromise(
    deps.execFile,
    "docker",
    ["ps", "--all", "--format", "{{json .}}"],
    { timeout: DOCKER_TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024, windowsHide: true }
  );
  const apps = [];
  const seen = new Set();
  for (const line of String(stdout || "").split("\n")) {
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    const status = normalizedContainerStatus(record);
    if (!STATUS_ENUM.has(status)) continue;
    for (let name of String(record.Names || "").split(",")) {
      name = name.trim().replace(/^\/+/, "");
      if (!ALLOWED_SERVICES.has(name) || seen.has(name)) continue;
      seen.add(name);
      apps.push({ name, status });
    }
  }
  apps.sort((a, b) => a.name.localeCompare(b.name));
  return apps;
}

export async function dockerRuntime(deps = defaultDeps) {
  const { stdout } = await execFilePromise(
    deps.execFile,
    "docker",
    ["inspect", "ods-llama-server", "--format", "{{json .Config.Cmd}}"],
    { timeout: DOCKER_TIMEOUT_MS, maxBuffer: 64 * 1024, windowsHide: true }
  );
  let command;
  try {
    command = JSON.parse(String(stdout || ""));
  } catch {
    return null;
  }
  if (!Array.isArray(command) || !command.every((item) => typeof item === "string")) {
    return null;
  }
  const modelIndex = command.indexOf("--model");
  const contextIndex = command.indexOf("--ctx-size");
  if (modelIndex < 0 || contextIndex < 0) return null;
  const modelPath = command[modelIndex + 1];
  const contextLength = Number(command[contextIndex + 1]);
  if (typeof modelPath !== "string" || !modelPath.startsWith("/models/")) return null;
  const model = path.posix.basename(modelPath);
  if (
    model !== modelPath.slice("/models/".length) ||
    !MODEL_NAME_RE.test(model) ||
    !Number.isInteger(contextLength) ||
    contextLength < 4096 ||
    contextLength > 10_000_000
  ) {
    return null;
  }
  return { model, context_length: contextLength };
}

export async function dockerRouterRuntime(deps = defaultDeps) {
  const { stdout } = await execFilePromise(
    deps.execFile,
    "docker",
    ["exec", "ods-model-router", "cat", "/state/model-state.json"],
    { timeout: DOCKER_TIMEOUT_MS, maxBuffer: 64 * 1024, windowsHide: true }
  );
  let state;
  try {
    state = JSON.parse(String(stdout || ""));
  } catch {
    return null;
  }
  const active = state?.active;
  const model = active?.runtimeModelId;
  const contextLength = active?.contextLength;
  if (
    state?.schema !== "ods.model-state.v1" ||
    !active ||
    typeof active !== "object" ||
    Array.isArray(active) ||
    active.publicModel !== "ods/current" ||
    !Number.isInteger(active.routeSeq) ||
    typeof model !== "string" ||
    !MODEL_NAME_RE.test(model) ||
    !Number.isInteger(contextLength) ||
    contextLength < 4096 ||
    contextLength > 10_000_000
  ) {
    return null;
  }
  return { model, context_length: contextLength };
}

export function atomicWriteJson(file, value, fsImpl = fs) {
  const directory = path.dirname(file);
  const directoryStat = fsImpl.lstatSync(directory);
  if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) {
    throw new Error("unsafe status directory");
  }
  const temporary = `${file}.tmp-${process.pid}-${randomBytes(8).toString("hex")}`;
  try {
    fsImpl.writeFileSync(temporary, `${JSON.stringify(value)}\n`, {
      flag: "wx",
      mode: STATUS_MODE,
    });
    fsImpl.chmodSync(temporary, STATUS_MODE);
    fsImpl.renameSync(temporary, file);
  } catch (error) {
    try {
      fsImpl.unlinkSync(temporary);
    } catch {
      /* no temporary file to remove */
    }
    throw error;
  }
}

export async function writeStatus(
  ingressReady,
  gatewayPort,
  statusFile,
  odsVersion = "unknown",
  deps = defaultDeps,
  appPorts = appPortsFromEnv({}),
  configuredRuntime = undefined
) {
  const gatewayReachable = await checkGatewayReachable(gatewayPort, deps);
  let apps = [];
  let runtime = null;
  let docker = "unavailable";
  try {
    apps = await dockerApps(deps);
    docker = "ok";
  } catch {
    apps = [];
  }
  // The secured OpenClaw configuration is the runtime Pixel is actually
  // bound to. During a transactional model activation the live inference
  // process and OpenClaw are updated before model-state.json is committed, so
  // the router projection can legitimately describe the previous route for a
  // short window. Prefer the owner-validated configured runtime whenever it
  // is available; Docker inspection remains the fallback for legacy token
  // files that cannot carry an authoritative runtime binding.
  if (configuredRuntime !== undefined) {
    runtime = configuredRuntime;
  } else if (docker === "ok") {
    try {
      runtime = await dockerRouterRuntime(deps);
    } catch {
      runtime = null;
    }
  }
  // An explicit null means the secured config has no representable binding
  // (for example a managed or custom remote provider). It is not permission
  // to label that provider with an unrelated local llama model.
  if (configuredRuntime === undefined && runtime === null && docker === "ok") {
    try {
      runtime = await dockerRuntime(deps);
    } catch {
      runtime = null;
    }
  }
  const onlineApps = apps.filter(({ status }) => status === "healthy" || status === "running").length;
  const projection = {
    schema_version: 2,
    timestamp: new Date().toISOString(),
    service: "pixel-agent",
    ods_version: odsVersion,
    ingress_ready: Boolean(ingressReady),
    gateway_reachable: gatewayReachable,
    docker,
    online_apps: onlineApps,
    app_ports: { ...appPorts },
    runtime,
    apps,
  };
  atomicWriteJson(statusFile, projection);
  return projection;
}

export function createIngressServer({ token, gatewayPort, deps = defaultDeps, historyLedger = null, accessOwnerKey = null }) {
  // Cancellation is scoped to this ingress instance and the opaque ODS user.
  // A Set preserves correct behavior if one chat has overlapping transports.
  const activeGatewayTransports = new Map();
  const historyAborters=new Map();
  return http.createServer((req, res) => {
    let pathname;
    try {
      pathname = new URL(req.url, "http://pixel-ingress.invalid").pathname;
    } catch {
      sendError(res, 400, "bad request");
      return;
    }

    if (pathname === '/v1/model-control') {
      void handleModelControl(req, res, {ownerKey:typeof accessOwnerKey === 'function' ? accessOwnerKey() : accessOwnerKey});
      return;
    }
    if (pathname === '/v1/access-mode') {
      void handleAccessMode(req, res, {ownerKey:typeof accessOwnerKey === 'function' ? accessOwnerKey() : accessOwnerKey});
      return;
    }

    if (pathname === "/health") {
      if (req.method !== "GET") {
        sendError(res, 405, "method not allowed");
        return;
      }
      void checkGatewayReachable(gatewayPort, deps).then((ready) => {
        if (res.destroyed || res.writableEnded) return;
        res.writeHead(ready ? 200 : 503, {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        });
        res.end(JSON.stringify({ status: ready ? "ok" : "unavailable" }));
      });
      return;
    }

    if (pathname === "/v1/chat/completions") {
      if (req.method !== "POST") {
        sendError(res, 405, "method not allowed");
        return;
      }
      const contentType = String(req.headers["content-type"] || "").toLowerCase();
      if (contentType.split(";", 1)[0].trim() !== "application/json") {
        sendError(res, 415, "content type must be application/json");
        return;
      }
      void handleChat(req, res, token, gatewayPort, deps, historyLedger, historyAborters, activeGatewayTransports);
      return;
    }

    if(['/v1/chat/context','/v1/chat/compact','/v1/chat/history'].includes(pathname)) {
      if(req.method!=='POST') {sendError(res,405,'method not allowed');return;}
      if(req.url!==pathname) {sendError(res,400,'invalid request');return;}
      if(String(req.headers['content-type'] || '').split(';',1)[0].trim()!=='application/json') {sendError(res,415,'content type must be application/json');return;}
      void handleContextControl(req,res,pathname,token,gatewayPort,deps,historyLedger);
      return;
    }

    if (pathname === "/v1/chat/cancel") {
      if (req.method !== "POST") {
        sendError(res, 405, "method not allowed");
        return;
      }
      const contentType = String(req.headers["content-type"] || "").toLowerCase();
      if (contentType.split(";", 1)[0].trim() !== "application/json") {
        sendError(res, 415, "content type must be application/json");
        return;
      }
      void handleCancel(req, res, token, gatewayPort, deps, historyLedger, historyAborters, activeGatewayTransports);
      return;
    }

    sendError(res, 404, "not found");
  });
}

async function handleCancel(req, res, token, gatewayPort, deps, ledger, historyAborters, activeGatewayTransports) {
  let raw;
  try {
    raw = await readBody(req, MAX_CANCEL_BODY);
  } catch (error) {
    sendError(
      res,
      error instanceof HttpError ? error.status : 400,
      error instanceof HttpError ? error.message : "bad request"
    );
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw.toString("utf8"));
  } catch {
    sendError(res, 400, "invalid json");
    return;
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    Array.isArray(parsed) ||
    Object.keys(parsed).length !== 1 ||
    typeof parsed.user !== "string" ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(parsed.user)
  ) {
    sendError(res, 400, "invalid cancellation request");
    return;
  }
  const user = computeSessionUser({ user: parsed.user });
  try {
    const pending=ledger?.read(user),local=historyAborters?.get(user);
    if(local && pending && local.requestId===pending.requestId) local.controller.abort();
    const aborted=await abortGatewayRun(user, token, gatewayPort, deps);
    if(aborted) {
      // The run acknowledgment covers OpenClaw; also close the matching
      // provider transport so generation cannot outlive a terminal UI.
      abortActiveGatewayTransports(activeGatewayTransports, user);
      ledger?.interrupt(user,pending?.requestId);
      sendJson(res,200,{aborted:true});
      return;
    }
    // A restart or a rejected pre-dispatch request may leave only the browser's
    // interrupted marker. Prove this exact session idle under the history lock;
    // absence of an abortable run alone is never enough to clear the warning.
    const release=ledger?.lock(user);
    try {
      const native=await nativeContextRequest('context',{user},token,gatewayPort,deps);
      const idle=['ready','missing'].includes(native.status);
      const current=ledger?.read(user);
      const unresolved=current && ['pending','unknown'].includes(current.status);
      const recovered=idle && (!unresolved || ledger.interrupt(user,current.requestId));
      sendJson(res,200,{aborted:recovered===true});
    } finally {release?.();}
  } catch {sendError(res,503,'cancellation-unconfirmed');}
}

async function handleChat(req, res, token, gatewayPort, deps, historyLedger, historyAborters, activeGatewayTransports) {
  let raw;
  try {
    raw = await readBody(req, MAX_HISTORY_BODY);
  } catch (error) {
    sendError(
      res,
      error instanceof HttpError ? error.status : 400,
      error instanceof HttpError ? error.message : "bad request"
    );
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(raw.toString("utf8"));
  } catch {
    sendError(res, 400, "invalid json");
    return;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    sendError(res, 400, "invalid request body");
    return;
  }

  let release,prepared,submitted=false,completed=false,user;
  try {
    if(!parsed.history_snapshot && raw.length>MAX_BODY) throw new HistoryError('request-too-large',413);
    user=computeSessionUser(parsed);
    const outgoing = buildOutgoing(parsed, user);
    if(historyLedger && user) release=historyLedger.lock(user);
    if(!parsed.history_snapshot) {await forwardChat(res,outgoing,token,gatewayPort,deps,{},activeGatewayTransports);return;}
    if(!historyLedger || !user) throw new HistoryError('history-storage-unavailable',503);
    const native=await nativeContextRequest('context',{user},token,gatewayPort,deps);
    prepared=historyLedger.prepare(user,parsed.request_id,parsed.history_snapshot,native);
    if(prepared.replay) {
      const result=prepared.replay;
      if(outgoing.stream) {res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});res.end(completionSse(applyVerificationToCompletion(result.completion,result.verification),result.verification));}
      else sendJson(res,200,applyVerificationToCompletion(result.completion,result.verification));
      return;
    }
    const latest=[...(outgoing.messages || [])].reverse().find(message=>message.role==='user');
    if(!latest) throw new HistoryError('invalid-history-input',400);
    // Identity/delivery policy belongs to the trusted API/edge messages. The
    // snapshot is data only; it cannot introduce system/developer instructions.
    outgoing.messages=[...(outgoing.messages || []).filter(message=>message.role==='system'),...prepared.delta.slice(0,-1),latest];
    await forwardChat(res,outgoing,token,gatewayPort,deps,{
      onController:controller=>historyAborters?.set(user,{requestId:parsed.request_id,controller}),
      beforeRequest:async signal=>{
        if(!prepared.hydrate) return;
        const seeded=await nativeContextRequest('history',{user,request_id:parsed.request_id,messages:prepared.archive},token,gatewayPort,deps,signal,30000);
        if(seeded?.hydrated!==true) throw new HistoryError('history-preparation-failed',503);
        const request_id=`history-${createHash('sha256').update(`${parsed.request_id}:${prepared.state.revision}`).digest('hex')}`;
        let state=await nativeContextRequest('compact',{user,request_id},token,gatewayPort,deps,signal);
        while(state.compaction?.requestId===request_id && state.compaction.status==='running') {
          await new Promise((resolve,reject)=>{
            if(signal.aborted) {reject(new HistoryError('history-preparation-interrupted',503));return;}
            const abort=()=>{deps.clearTimeout(timer);reject(new HistoryError('history-preparation-interrupted',503))};
            const timer=deps.setTimeout(()=>{signal.removeEventListener('abort',abort);resolve()},1000);
            signal.addEventListener('abort',abort,{once:true});
          });
          state=await nativeContextRequest('context',{user},token,gatewayPort,deps,signal);
        }
        if(state.compaction?.requestId!==request_id || !['completed','skipped'].includes(state.compaction.status)) throw new HistoryError('history-compaction-unconfirmed',503);
      },
      onSubmitted:()=>{submitted=true;},
      onComplete:async(completion,verification)=>{
        let after;
        try {after=await nativeContextRequest('context',{user},token,gatewayPort,deps)} catch { /* The verified completion still proves this input ran. */ }
        historyLedger.complete(user,prepared,completion,verification,after);
        completed=true;
      },
    },activeGatewayTransports);
  } catch (error) {
    sendError(
      res,
      error instanceof HttpError || error instanceof HistoryError ? error.status : 400,
      error instanceof HttpError || error instanceof HistoryError ? error.message : "invalid request body"
    );
  } finally {
    if(historyAborters?.get(user)?.requestId===prepared?.state?.requestId) historyAborters.delete(user);
    try {if(prepared?.state && !completed) {if(submitted) historyLedger.uncertain(user,prepared);else historyLedger.abandon(user,prepared);}} catch { /* A still-pending durable record remains unknown after restart. */ }
    try {release?.()} catch { /* Keep serving unrelated conversations if a lock cannot be removed. */ }
  }
}

async function nativeContextRequest(operation,body,token,gatewayPort,deps,outerSignal,timeoutMs=10000) {
  const controller=new AbortController(), abort=()=>controller.abort();
  outerSignal?.addEventListener('abort',abort,{once:true});
  if(outerSignal?.aborted) controller.abort();
  const timer=deps.setTimeout(abort,timeoutMs);
  try {
    const response=await deps.fetch(`http://127.0.0.1:${gatewayPort}/pixel-ods/${operation}`,{method:'POST',headers:upstreamHeaders(false,token),body:JSON.stringify(body),redirect:'error',signal:controller.signal});
    if(response.status!==200 || !String(response.headers.get('content-type') || '').startsWith('application/json')) {await drain(response.body);throw new HistoryError(response.status===409?'context-busy':'context-unavailable',response.status===409?409:503);}
    const value=JSON.parse((await readBounded(response.body,32768)).toString('utf8'));
    if(operation==='history') return value;
    if(value?.schemaVersion!==1 || !['ready','missing','busy','unavailable'].includes(value.status) || !['idle','running','completed','skipped','failed','unknown'].includes(value.compaction?.status) || !Number.isInteger(value.compaction?.count)) throw new HistoryError('context-unavailable',503);
    return value;
  } catch(error) {if(error instanceof HistoryError) throw error;throw new HistoryError('context-unavailable',503)}
  finally {deps.clearTimeout(timer);outerSignal?.removeEventListener('abort',abort);}
}
function publicContext(native,history) {
  return {schemaVersion:1,status:history.status==='pending'?'busy':history.status==='unknown' && native.status!=='busy'?'unavailable':native.status,
    sessionRevision:native.sessionRevision,context:native.context,model:native.model,compaction:native.compaction,history};
}
async function handleContextControl(req,res,pathname,token,gatewayPort,deps,ledger) {
  let release;
  try {
    if(!ledger) throw new HistoryError('history-storage-unavailable',503);
    const body=JSON.parse((await readBody(req,1024)).toString('utf8'));
    if(!body || typeof body!=='object' || Array.isArray(body)) throw new HistoryError('invalid-context-request',400);
    if(pathname==='/v1/chat/history') {
      if(!/^ods-[a-f0-9]{64}$/.test(body.user || '') || Object.keys(body).some(key=>!['user','query','offset','limit'].includes(key))) throw new HistoryError('invalid-history-query',400);
      sendJson(res,200,ledger.search(body.user,body));return;
    }
    const compact=pathname==='/v1/chat/compact';
    if(Object.keys(body).sort().join()!==(compact?'request_id,user':'user') || !/^[A-Za-z0-9_-]{1,128}$/.test(body.user || '') || (compact && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(body.request_id || ''))) throw new HistoryError('invalid-context-request',400);
    const user=computeSessionUser({user:body.user});
    if(compact) {
      release=ledger.lock(user);
      if(ledger.projection(user).status!=='ready') throw new HistoryError('history-outcome-unknown',409);
    }
    const native=await nativeContextRequest(compact?'compact':'context',{user,...compact?{request_id:body.request_id}:{}},token,gatewayPort,deps);
    sendJson(res,200,publicContext(native,ledger.projection(user)));
  } catch(error) {sendError(res,error instanceof HistoryError?error.status:400,error instanceof HistoryError?error.message:'invalid-context-request')}
  finally {try {release?.()} catch { /* Persistent lock keeps subsequent writes closed. */ }}
}

function listenUnix(server, socketPath) {
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(socketPath);
  });
}

export async function start(cfg = configFromEnv(), opts = {}) {
  const deps = { ...defaultDeps, ...(opts.deps || {}) };
  let startupStage = "configuration";
  let server;
  let gateway;
  try {
    validateConfig(cfg);
    startupStage = "token-read";
    gateway = readGatewayConfiguration(cfg.gatewayTokenFile, opts.euid);
    const token = gateway.token;
    startupStage = "socket-prepare";
    prepareSocketPath(cfg.socketPath);
    const historyLedger=createChatHistoryLedger(cfg.chatStateDir || path.join(path.dirname(cfg.socketPath),'chat-state'));
    // Re-read on access requests so credential rotation or first installation
    // does not restart an active chat. Missing/unsafe key disables only access.
    const accessOwnerKey = () => readAccessOwnerKey(cfg.accessOwnerKeyFile, opts.euid);
    server = createIngressServer({ token, gatewayPort: cfg.gatewayPort, deps, historyLedger, accessOwnerKey });
    startupStage = "socket-listen";
    await listenUnix(server, cfg.socketPath);
    startupStage = "runtime-state";
    fs.chmodSync(cfg.socketPath, 0o660);
    if (cfg.ingressGid !== null) fs.chownSync(cfg.socketPath, -1, cfg.ingressGid);
    await writeStatus(
      true,
      cfg.gatewayPort,
      cfg.statusFile,
      cfg.odsVersion,
      deps,
      cfg.appPorts,
      gateway.runtimeAuthoritative ? gateway.runtime : undefined
    );
  } catch (error) {
    if (server?.listening) {
      await new Promise((resolve) => server.close(resolve));
      try {
        fs.unlinkSync(cfg.socketPath);
      } catch {
        /* socket already absent */
      }
    }
    if (error && typeof error === "object") error.pixelStartupStage = startupStage;
    throw error;
  }

  const interval = setInterval(() => {
    void writeStatus(
      true,
      cfg.gatewayPort,
      cfg.statusFile,
      cfg.odsVersion,
      deps,
      cfg.appPorts,
      gateway.runtimeAuthoritative ? gateway.runtime : undefined
    ).catch(() => {});
  }, cfg.statusIntervalMs);
  interval.unref();
  let closed = false;
  return {
    server,
    interval,
    async close() {
      if (closed) return;
      closed = true;
      clearInterval(interval);
      await new Promise((resolve) => server.close(resolve));
      try {
        const current = fs.lstatSync(cfg.socketPath);
        if (current.isSocket()) fs.unlinkSync(cfg.socketPath);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    },
  };
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  start()
    .then((handle) => {
      console.log("pixel-ingress ready");
      const shutdown = async () => {
        try {
          await handle.close();
          process.exit(0);
        } catch {
          process.exit(1);
        }
      };
      process.once("SIGTERM", shutdown);
      process.once("SIGINT", shutdown);
    })
    .catch((error) => {
      const allowedStages = new Set([
        "configuration", "token-read", "socket-prepare", "socket-listen", "runtime-state",
      ]);
      const stage = allowedStages.has(error?.pixelStartupStage) ? error.pixelStartupStage : "internal";
      console.error(`pixel-ingress failed to start (${stage})`);
      process.exit(1);
    });
}

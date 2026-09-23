import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

import {
  canonical, validateWorkCapabilityConsumption, validateWorkCapabilityGrant, validateWorkCapabilityPack,
  validateWorkWatchdogDecision,
} from "../../scripts/lib/work-contract.mjs";
import { validateJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { beginCapabilityExecution } from "./capability-packs.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const PROTOCOL_VERSION = "2026-07-28";

export class WorkMcpClientError extends Error {}

function fail(message) { throw new WorkMcpClientError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function exactKeys(value, allowed, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some((key) => !allowed.includes(key))) fail(`${label} shape is invalid`);
}
function boundedJson(value, maximum, label) {
  let text;
  try { text = canonical(value); } catch { fail(`${label} is not canonical JSON`); }
  if (Buffer.byteLength(text) > maximum) fail(`${label} exceeds its byte ceiling`);
  return text;
}

function checkedPackBinding(pack, expectedPackSha256) {
  const errors = validateWorkCapabilityPack(pack);
  if (errors.length) fail(`MCP pack is invalid: ${errors[0]}`);
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== sha(pack)) fail("MCP pack differs from its trusted signed-tree binding");
  return pack;
}

function checkedLaunch(options) {
  if (typeof options.command !== "string" || !options.command.startsWith("/") && !/^[A-Za-z]:\\/u.test(options.command) || typeof options.cwd !== "string" || !options.cwd.startsWith("/") && !/^[A-Za-z]:\\/u.test(options.cwd) || !Array.isArray(options.args) || options.args.some((entry) => typeof entry !== "string") || !options.env || typeof options.env !== "object" || Array.isArray(options.env) || Object.keys(options.env).length !== 0) fail("MCP process launch must use an absolute command and working directory, argument vector, and empty environment");
}

function bindings(pack, expectedPackSha256, grant, claim, toolName, input, preflightDecision) {
  const packErrors = validateWorkCapabilityPack(pack);
  const grantErrors = validateWorkCapabilityGrant(grant);
  const claimErrors = validateWorkCapabilityConsumption(claim);
  if (packErrors.length) fail(`MCP pack is invalid: ${packErrors[0]}`);
  if (grantErrors.length) fail(`MCP grant is invalid: ${grantErrors[0]}`);
  if (claimErrors.length) fail(`MCP claim is invalid: ${claimErrors[0]}`);
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== sha(pack) || grant.pack.packSha256 !== expectedPackSha256 || claim.packSha256 !== expectedPackSha256 || claim.grantSha256 !== sha(grant) || claim.grantId !== grant.grantId || claim.jobId !== grant.jobId || claim.checkpointSha256 !== grant.checkpointSha256) fail("MCP pack, grant, or claim binding differs");
  if (!grant.tools.includes(toolName)) fail("MCP tool is not named by the exact grant");
  const tool = pack.tools.find((entry) => entry.name === toolName);
  if (!tool) fail("MCP tool is not declared by the signed pack");
  if (boundedJson(input, grant.limits.maxInputBytes, "MCP tool input") && validateJsonSchema(input, tool.inputSchema).length) fail("MCP tool input does not match its signed schema");
  const watchdogErrors = validateWorkWatchdogDecision(preflightDecision);
  const watchdogTool = `mcp.${pack.id}.${tool.name}`;
  if (watchdogErrors.length || preflightDecision.decision !== "continue" || preflightDecision.checkpointSha256 !== grant.checkpointSha256 || preflightDecision.proposal.tool !== watchdogTool || preflightDecision.proposal.argumentsSha256 !== sha(input) || preflightDecision.proposal.effectClass !== tool.effectClass) fail("MCP call lacks an exact continue preflight decision");
  return tool;
}

function metadata() {
  return {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": { name: "pixel-deep-work", version: "4.3.27" },
    "io.modelcontextprotocol/clientCapabilities": {},
  };
}

function validateDiscovery(result, pack) {
  exactKeys(result, ["resultType", "supportedVersions", "capabilities", "_meta", "instructions", "ttlMs", "cacheScope"], "MCP discovery result");
  if (result.resultType !== "complete" || canonical(result.supportedVersions) !== canonical([PROTOCOL_VERSION]) || canonical(result.capabilities) !== canonical({ tools: {} })) fail("MCP discovery widened or changed its protocol capabilities");
  const server = result._meta?.["io.modelcontextprotocol/serverInfo"];
  if (!server || server.name !== pack.adapter.serverName || server.version !== pack.adapter.serverVersion || Object.keys(server).some((key) => !["name", "version"].includes(key))) fail("MCP server identity differs from the signed pack");
  if (result.instructions !== undefined && (typeof result.instructions !== "string" || Buffer.byteLength(result.instructions) > 8192)) fail("MCP server instructions are oversized");
  return { instructionsSha256: result.instructions === undefined ? null : sha(result.instructions) };
}

function validateToolList(result, pack) {
  exactKeys(result, ["resultType", "tools", "nextCursor", "ttlMs", "cacheScope"], "MCP tool-list result");
  if (result.resultType !== "complete" || result.nextCursor !== undefined && result.nextCursor !== null || !Array.isArray(result.tools)) fail("MCP tool list is incomplete or paginated");
  const observed = result.tools.map((tool) => {
    exactKeys(tool, ["name", "title", "description", "inputSchema", "outputSchema"], "MCP tool declaration");
    return tool;
  });
  const expected = pack.tools.map(({ name, title, description, inputSchema, outputSchema }) => ({ name, title, description, inputSchema, outputSchema }));
  if (canonical(observed) !== canonical(expected)) fail("MCP server tool surface or schemas differ from the signed pack");
  return sha(observed);
}

function validateToolResult(result, tool, maximumBytes) {
  if (!result || typeof result !== "object" || Array.isArray(result)) fail("MCP tool result shape is invalid");
  if (result.resultType !== "complete") fail("MCP tool result is incomplete or requests more input");
  exactKeys(result, ["resultType", "content", "structuredContent", "isError"], "MCP tool result");
  if (typeof result.isError !== "boolean" || !Array.isArray(result.content) || result.structuredContent === undefined) fail("MCP tool result is incomplete or requests more input");
  if (result.content.length !== 1) fail("MCP tool result must contain one structured-output mirror");
  for (const block of result.content) {
    exactKeys(block, ["type", "text"], "MCP content block");
    if (block.type !== "text" || typeof block.text !== "string") fail("MCP result contains an unsupported content type");
  }
  boundedJson(result, maximumBytes, "MCP tool result");
  const errors = validateJsonSchema(result.structuredContent, tool.outputSchema);
  if (errors.length) fail("MCP structured result differs from the signed output schema");
  let mirrored;
  try { mirrored = JSON.parse(result.content[0].text); } catch { fail("MCP text result is not a structured-output mirror"); }
  if (canonical(mirrored) !== canonical(result.structuredContent)) fail("MCP text result differs from structured output");
  if (result.isError) fail(`MCP tool returned a bounded error (contentSha256=${sha(result.content)})`);
  return { structuredContent: result.structuredContent, contentSha256: sha(result.content), resultSha256: sha(result) };
}

// This lower-level transport accepts only a launch descriptor already compiled by the
// trusted controller. Production callers must use the exact Docker command emitted by
// capability-packs.mjs; arbitrary host commands are used only by the deterministic tests.
async function runTrustedMcpSession(options, { tool = null, maxRuntimeMs, execution = null }) {
  const pack = options.pack;
  const child = spawn(options.command, options.args, { cwd: options.cwd, env: {}, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  let stdoutBuffer = Buffer.alloc(0);
  let stdoutBytes = 0;
  let stderrBytes = 0;
  let frames = 0;
  let currentRequestId = null;
  let shuttingDown = false;
  let exited = false;
  const stderrDigest = createHash("sha256");
  const pending = new Map();
  let transportReject;
  const transportFailure = new Promise((_, reject) => { transportReject = reject; });
  transportFailure.catch(() => {});

  function rejectTransport(message) {
    const error = message instanceof Error ? message : new WorkMcpClientError(message);
    transportReject(error);
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  }

  function acceptFrame(value) {
    frames += 1;
    if (frames > 1024) return rejectTransport("MCP adapter exceeded its frame-count ceiling");
    if (!value || typeof value !== "object" || Array.isArray(value) || value.jsonrpc !== "2.0") return rejectTransport("MCP adapter emitted a malformed JSON-RPC message");
    if (typeof value.method === "string") return rejectTransport("MCP adapter emitted a server request or unsupported notification");
    if (!pending.has(value.id) || Object.keys(value).some((key) => !["jsonrpc", "id", "result", "error"].includes(key)) || (value.result === undefined) === (value.error === undefined)) return rejectTransport("MCP adapter emitted an unexpected or ambiguous response");
    const waiter = pending.get(value.id); pending.delete(value.id);
    if (value.error !== undefined) waiter.reject(new WorkMcpClientError(`MCP request failed closed (errorSha256=${sha(value.error)})`));
    else waiter.resolve(value.result);
  }

  child.stdout.on("data", (chunk) => {
    stdoutBytes += chunk.length;
    if (stdoutBytes > pack.limits.maxStdoutBytes) return rejectTransport("MCP stdout exceeded its byte ceiling");
    stdoutBuffer = Buffer.concat([stdoutBuffer, chunk]);
    if (stdoutBuffer.length > pack.limits.maxFrameBytes && !stdoutBuffer.includes(0x0a)) return rejectTransport("MCP frame exceeded its byte ceiling");
    for (;;) {
      const newline = stdoutBuffer.indexOf(0x0a);
      if (newline < 0) break;
      const line = stdoutBuffer.subarray(0, newline); stdoutBuffer = stdoutBuffer.subarray(newline + 1);
      if (line.length < 1 || line.length > pack.limits.maxFrameBytes || line.includes(0x0a) || line.includes(0x0d)) return rejectTransport("MCP adapter emitted an invalid newline-delimited frame");
      let value;
      try { value = JSON.parse(line.toString("utf8")); } catch { return rejectTransport("MCP adapter emitted invalid UTF-8 JSON"); }
      acceptFrame(value);
    }
  });
  child.stderr.on("data", (chunk) => {
    stderrBytes += chunk.length; stderrDigest.update(chunk);
    if (stderrBytes > pack.limits.maxStderrBytes) rejectTransport("MCP stderr exceeded its byte ceiling");
  });
  child.stdin.on("error", () => rejectTransport("MCP adapter input stream failed"));
  child.on("error", () => rejectTransport("MCP adapter could not be started"));
  const exitPromise = new Promise((resolve) => child.on("close", (code, signal) => {
    exited = true;
    if (!shuttingDown) rejectTransport("MCP adapter exited unexpectedly");
    resolve({ code, signal });
  }));

  function send(value) {
    const payload = `${JSON.stringify(value)}\n`;
    if (Buffer.byteLength(payload) > pack.limits.maxFrameBytes || child.stdin.destroyed) fail("MCP client request exceeds framing or process state");
    child.stdin.write(payload);
  }
  function request(id, method, params = {}) {
    if (pending.size !== 0) fail("MCP client permits one in-flight request");
    currentRequestId = id;
    const promise = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    send({ jsonrpc: "2.0", id, method, params: { ...params, _meta: metadata() } });
    return promise.finally(() => { if (currentRequestId === id) currentRequestId = null; });
  }
  let runtimeTimer;
  const timeout = new Promise((_, reject) => { runtimeTimer = setTimeout(() => {
    if (currentRequestId !== null && !child.stdin.destroyed && !child.stdin.writableEnded) {
      try { send({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId: currentRequestId, reason: "Pixel capability runtime ceiling" } }); } catch { /* The fail-closed timeout still terminates the adapter below. */ }
    }
    reject(new WorkMcpClientError("MCP capability session exceeded its runtime ceiling"));
  }, maxRuntimeMs); runtimeTimer.unref?.(); });

  try {
    const discovery = await Promise.race([request("pixel-discover", "server/discover"), transportFailure, timeout]);
    const discoveryEvidence = validateDiscovery(discovery, pack);
    const listed = await Promise.race([request("pixel-tools", "tools/list"), transportFailure, timeout]);
    const toolSurfaceSha256 = validateToolList(listed, pack);
    let checked = null;
    if (tool) {
      const result = await Promise.race([request("pixel-call", "tools/call", { name: tool.name, arguments: options.input }), transportFailure, timeout]);
      checked = validateToolResult(result, tool, Math.min(pack.limits.maxOutputBytes, options.grant.limits.maxOutputBytes));
    }
    shuttingDown = true; child.stdin.end();
    let shutdownTimer;
    const shutdownDeadline = new Promise((resolve) => { shutdownTimer = setTimeout(() => resolve(null), pack.limits.shutdownTimeoutMs); shutdownTimer.unref?.(); });
    const graceful = await Promise.race([exitPromise, shutdownDeadline]);
    clearTimeout(shutdownTimer);
    if (graceful === null) { child.kill("SIGKILL"); await exitPromise; }
    if (stdoutBuffer.length !== 0 || graceful && (graceful.code !== 0 || graceful.signal !== null)) fail("MCP adapter did not shut down cleanly");
    clearTimeout(runtimeTimer);
    return {
      ...(checked ?? {}), protocolVersion: PROTOCOL_VERSION, toolSurfaceSha256,
      instructionsSha256: discoveryEvidence.instructionsSha256, frames, stdoutBytes, stderrBytes,
      stderrSha256: stderrDigest.digest("hex"),
      ...(execution ? { executionClaimSha256: execution.claimSha256, dataClassification: options.grant.dataClassification } : {}),
      externalEffects: false, authority: "none",
    };
  } catch (error) {
    clearTimeout(runtimeTimer);
    shuttingDown = true;
    child.stdin.destroy();
    if (!exited) child.kill("SIGKILL");
    await exitPromise.catch(() => {});
    throw error instanceof WorkMcpClientError ? error : new WorkMcpClientError(error instanceof Error ? error.message : "MCP capability session failed closed");
  }
}

export async function probeCapabilityMcpWithTrustedLaunch(options) {
  checkedPackBinding(options.pack, options.expectedPackSha256);
  checkedLaunch(options);
  return runTrustedMcpSession(options, { maxRuntimeMs: Math.min(options.pack.limits.maxRuntimeMs, 30000) });
}

export async function runCapabilityMcpToolWithTrustedLaunch(options) {
  const tool = bindings(options.pack, options.expectedPackSha256, options.grant, options.claim, options.toolName, options.input, options.preflightDecision);
  checkedLaunch(options);
  const execution = await beginCapabilityExecution({ stateRoot: options.stateRoot, pack: options.pack, expectedPackSha256: options.expectedPackSha256, grant: options.grant, claim: options.claim, now: options.now });
  return runTrustedMcpSession(options, { tool, maxRuntimeMs: Math.min(options.pack.limits.maxRuntimeMs, options.grant.limits.maxRuntimeMs), execution });
}

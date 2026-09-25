import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import { readFileSync } from "node:fs";
import { isIP } from "node:net";
import net from "node:net";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { canonical, validateWorkCodexEgressPolicy } from "../../scripts/lib/work-contract.mjs";

const MAX_HEADER_BYTES = 8192;

export class WorkCodexEgressProxyError extends Error {}
function fail(message) { throw new WorkCodexEgressProxyError(message); }
function sha256(value) { return createHash("sha256").update(value).digest("hex"); }

export function parseBoundWorkCodexEgressPolicy(bytes, expectedSha256) {
  if (!Buffer.isBuffer(bytes) || !/^[a-f0-9]{64}$/u.test(expectedSha256 ?? "") || bytes.length < 2 || bytes.length > 128 * 1024 || bytes.includes(0)) fail("Codex proxy policy binding is invalid");
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail("Codex proxy policy is not strict UTF-8"); }
  let policy; try { policy = JSON.parse(text); } catch { fail("Codex proxy policy is not valid JSON"); }
  const normalized = canonical(policy), errors = validateWorkCodexEgressPolicy(policy);
  if (![normalized, `${normalized}\n`].includes(text) || errors.length || sha256(normalized) !== expectedSha256) fail("Codex proxy policy differs from its exact startup binding");
  return policy;
}

export function isPublicEgressAddress(address) {
  const version = isIP(address);
  if (version === 4) {
    const parts = address.split(".").map(Number); if (parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
    const [a, b, c] = parts;
    return !(a === 0 || a === 10 || a === 100 && b >= 64 && b <= 127 || a === 127 || a === 169 && b === 254 || a === 172 && b >= 16 && b <= 31 || a === 192 && (b === 0 && (c === 0 || c === 2) || b === 168) || a === 198 && (b === 18 || b === 19 || b === 51 && c === 100) || a === 203 && b === 0 && c === 113 || a >= 224);
  }
  if (version === 6) {
    const first = Number.parseInt(address.split(":", 1)[0] || "0", 16), normalized = address.toLowerCase();
    return first >= 0x2000 && first <= 0x3fff && !/^2001:0*db8(?::|$)/u.test(normalized);
  }
  return false;
}

export function parseWorkCodexConnectRequest(bytes, policy) {
  const errors = validateWorkCodexEgressPolicy(policy); if (errors.length) fail(`Codex egress policy failed validation: ${errors.join("; ")}`);
  if (!Buffer.isBuffer(bytes) || bytes.length < 16 || bytes.length > MAX_HEADER_BYTES || bytes.includes(0)) fail("Codex proxy request header is invalid");
  let text; try { text = new TextDecoder("ascii", { fatal: true }).decode(bytes); } catch { fail("Codex proxy request header is not ASCII"); }
  if (!text.endsWith("\r\n\r\n") || /[^\x20-\x7e\r\n]/u.test(text)) fail("Codex proxy request header has invalid framing");
  const lines = text.slice(0, -4).split("\r\n"), match = /^CONNECT ([a-z0-9.-]+):([0-9]{1,5}) HTTP\/1\.1$/u.exec(lines.shift() ?? "");
  if (!match) fail("Codex proxy permits only a canonical CONNECT request");
  const host = match[1], port = Number(match[2]);
  if (isIP(host) || !policy.allowedHosts.includes(host) || !policy.allowedPorts.includes(port)) fail("Codex proxy target is not allowlisted");
  const headers = new Map();
  for (const line of lines) {
    const header = /^([A-Za-z0-9-]{1,64}): ([\x20-\x7e]{0,1024})$/u.exec(line); if (!header) fail("Codex proxy header is malformed");
    const name = header[1].toLowerCase(); if (headers.has(name) || name === "proxy-authorization" || !["host", "proxy-connection", "user-agent"].includes(name)) fail("Codex proxy header is duplicated or unsupported"); headers.set(name, header[2]);
  }
  if (headers.get("host") !== `${host}:${port}`) fail("Codex proxy Host header differs from the CONNECT target");
  return Object.freeze({ host, port });
}

async function publicResolution(host, resolver) {
  let answers; try { answers = await resolver(host, { all: true, verbatim: true }); } catch { fail("Codex proxy DNS resolution failed"); }
  if (!Array.isArray(answers) || answers.length < 1 || answers.length > 32 || answers.some((answer) => !answer || !isPublicEgressAddress(answer.address))) fail("Codex proxy DNS resolved to an unsafe address set");
  return answers[0].address;
}

function fixedResponse(socket, status) { if (!socket.destroyed) socket.end(`HTTP/1.1 ${status}\r\nConnection: close\r\n\r\n`); }

export function createWorkCodexEgressProxy({ policy, resolver = lookup, connectFactory = (options) => net.createConnection(options) }) {
  const errors = validateWorkCodexEgressPolicy(policy); if (errors.length) fail(`Codex egress policy failed validation: ${errors.join("; ")}`);
  let active = 0;
  return net.createServer({ allowHalfOpen: false }, (client) => {
    if (active >= policy.maxConcurrentTunnels) { fixedResponse(client, "503 Service Unavailable"); return; }
    active += 1; let header = Buffer.alloc(0), upstream = null, established = false, closed = false, clientBytes = 0, upstreamBytes = 0;
    const finish = () => { if (closed) return; closed = true; active -= 1; client.destroy(); upstream?.destroy(); };
    const reject = (status = "403 Forbidden") => { if (!established) fixedResponse(client, status); setTimeout(finish, 10).unref(); };
    client.setTimeout(policy.idleTimeoutSeconds * 1000, finish);
    client.on("error", finish); client.on("close", finish);
    client.on("data", async function onHeader(chunk) {
      if (established) return;
      header = Buffer.concat([header, chunk]); if (header.length > MAX_HEADER_BYTES) { client.removeListener("data", onHeader); reject("431 Request Header Fields Too Large"); return; }
      const boundary = header.indexOf("\r\n\r\n"); if (boundary < 0) return;
      client.removeListener("data", onHeader); const requestBytes = Buffer.from(header.subarray(0, boundary + 4)), remainder = Buffer.from(header.subarray(boundary + 4)); header.fill(0);
      let target; try { target = parseWorkCodexConnectRequest(requestBytes, policy); } catch { reject(); return; }
      let address; try { address = await publicResolution(target.host, resolver); } catch { reject(); return; }
      if (closed) return;
      try { upstream = connectFactory({ host: address, port: target.port }); } catch { reject("502 Bad Gateway"); return; }
      if (!upstream || typeof upstream.on !== "function" || typeof upstream.setTimeout !== "function") { reject("502 Bad Gateway"); return; }
      upstream.setTimeout(policy.idleTimeoutSeconds * 1000, finish);
      const timer = setTimeout(() => reject("504 Gateway Timeout"), policy.connectTimeoutSeconds * 1000); timer.unref();
      upstream.once("connect", () => {
        clearTimeout(timer); if (closed) return; established = true; client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        const toUpstream = (data) => { clientBytes += data.length; if (clientBytes > policy.maxTunnelBytes) finish(); else upstream.write(data); };
        const toClient = (data) => { upstreamBytes += data.length; if (upstreamBytes > policy.maxTunnelBytes) finish(); else client.write(data); };
        client.on("data", toUpstream); upstream.on("data", toClient); upstream.on("end", () => client.end()); client.on("end", () => upstream.end());
        if (remainder.length) toUpstream(remainder);
      });
      upstream.on("error", () => reject("502 Bad Gateway")); upstream.on("close", finish);
    });
  });
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const policy = parseBoundWorkCodexEgressPolicy(readFileSync("/etc/pixel-egress/policy.json"), process.env.PIXEL_CODEX_EGRESS_POLICY_SHA256);
    const server = createWorkCodexEgressProxy({ policy }); server.on("error", () => { process.stderr.write("Pixel Codex egress proxy failed closed.\n"); process.exitCode = 70; }); server.listen(3128, "0.0.0.0");
  } catch { process.stderr.write("Pixel Codex egress proxy failed closed.\n"); process.exitCode = 70; }
}

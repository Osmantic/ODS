import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import { readFileSync } from "node:fs";
import { isIP } from "node:net";
import net from "node:net";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { bindWorkProviderPrivatePolicy } from "./private-policy.mjs";
import { resolveWorkProvider } from "./provider-registry.mjs";

const MAX_HEADER_BYTES = 8192;

export class WorkProviderEgressProxyError extends Error {}
function fail(message) { throw new WorkProviderEgressProxyError(message); }
function sha256(value) { return createHash("sha256").update(value).digest("hex"); }
function ipv6Integer(address) {
  if (address.includes(".")) return null;
  const halves = address.toLowerCase().split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [], tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  if (head.some((part) => !/^[0-9a-f]{1,4}$/u.test(part)) || tail.some((part) => !/^[0-9a-f]{1,4}$/u.test(part))) return null;
  const missing = 8 - head.length - tail.length;
  if (missing < 0 || halves.length === 1 && missing !== 0 || halves.length === 2 && missing < 1) return null;
  const parts = [...head, ...Array(missing).fill("0"), ...tail];
  return parts.reduce((value, part) => value << 16n | BigInt(Number.parseInt(part, 16)), 0n);
}
function ipv6Prefix(value, prefix, bits) { return value >> BigInt(128 - bits) === prefix >> BigInt(128 - bits); }

export function parseBoundWorkProviderPrivatePolicy(bytes, expectedSha256) {
  if (!Buffer.isBuffer(bytes) || !/^[a-f0-9]{64}$/u.test(expectedSha256 ?? "") || bytes.length < 2 || bytes.length > 256 * 1024 || bytes.includes(0)) fail("provider proxy policy binding is invalid");
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail("provider proxy policy is not strict UTF-8"); }
  let policy; try { policy = JSON.parse(text); } catch { fail("provider proxy policy is not valid JSON"); }
  const normalized = canonical(policy);
  if (![normalized, `${normalized}\n`].includes(text) || sha256(normalized) !== expectedSha256) fail("provider proxy policy differs from its exact startup binding");
  const resolvedProvider = resolveWorkProvider(policy.providerId, { enabledRemoteProviders: [policy.providerId] });
  return Object.freeze({ policy: bindWorkProviderPrivatePolicy(resolvedProvider, policy, { requireEnabled: true }), resolvedProvider });
}

export function isPublicWorkProviderEgressAddress(address) {
  const version = isIP(address);
  if (version === 4) {
    const parts = address.split(".").map(Number); if (parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
    const [a, b, c] = parts;
    return !(a === 0 || a === 10 || a === 100 && b >= 64 && b <= 127 || a === 127 || a === 169 && b === 254 || a === 172 && b >= 16 && b <= 31 || a === 192 && (b === 0 && (c === 0 || c === 2) || b === 168) || a === 198 && (b === 18 || b === 19 || b === 51 && c === 100) || a === 203 && b === 0 && c === 113 || a >= 224);
  }
  if (version === 6) {
    const value = ipv6Integer(address); if (value === null) return false;
    const globalUnicast = ipv6Integer("2000::"), teredo = ipv6Integer("2001::"), benchmark = ipv6Integer("2001:2::"), orchid = ipv6Integer("2001:10::"), orchidV2 = ipv6Integer("2001:20::"), documentation = ipv6Integer("2001:db8::"), sixToFour = ipv6Integer("2002::"), documentationV2 = ipv6Integer("3fff::");
    return ipv6Prefix(value, globalUnicast, 3)
      && !ipv6Prefix(value, teredo, 32) && !ipv6Prefix(value, benchmark, 48)
      && !ipv6Prefix(value, orchid, 28) && !ipv6Prefix(value, orchidV2, 28)
      && !ipv6Prefix(value, documentation, 32) && !ipv6Prefix(value, sixToFour, 16)
      && !ipv6Prefix(value, documentationV2, 20);
  }
  return false;
}

export function parseWorkProviderConnectRequest(bytes, policy) {
  const resolvedProvider = resolveWorkProvider(policy?.providerId, { enabledRemoteProviders: [policy?.providerId] });
  const bound = bindWorkProviderPrivatePolicy(resolvedProvider, policy, { requireEnabled: true });
  if (!Buffer.isBuffer(bytes) || bytes.length < 16 || bytes.length > MAX_HEADER_BYTES || bytes.includes(0)) fail("provider proxy request header is invalid");
  let text; try { text = new TextDecoder("ascii", { fatal: true }).decode(bytes); } catch { fail("provider proxy request header is not ASCII"); }
  if (!text.endsWith("\r\n\r\n") || /[^\x20-\x7e\r\n]/u.test(text)) fail("provider proxy request header has invalid framing");
  const lines = text.slice(0, -4).split("\r\n"), match = /^CONNECT ([a-z0-9.-]+):([0-9]{1,5}) HTTP\/1\.1$/u.exec(lines.shift() ?? "");
  if (!match) fail("provider proxy permits only a canonical CONNECT request");
  const host = match[1], port = Number(match[2]);
  if (isIP(host) || !bound.transport.allowedHosts.includes(`${host}:${port}`)) fail("provider proxy target is not allowlisted");
  const headers = new Map();
  for (const line of lines) {
    const header = /^([A-Za-z0-9-]{1,64}): ([\x20-\x7e]{0,1024})$/u.exec(line); if (!header) fail("provider proxy header is malformed");
    const name = header[1].toLowerCase();
    if (headers.has(name) || name === "proxy-authorization" || !["connection", "host", "proxy-connection", "user-agent"].includes(name)) fail("provider proxy header is duplicated or unsupported");
    headers.set(name, header[2]);
  }
  if (headers.get("host") !== `${host}:${port}`) fail("provider proxy Host header differs from the CONNECT target");
  if (headers.has("connection") && headers.get("connection") !== "close") fail("provider proxy Connection header is not the fixed close mode");
  return Object.freeze({ host, port });
}

async function publicResolution(host, resolver) {
  let answers; try { answers = await resolver(host, { all: true, verbatim: true }); } catch { fail("provider proxy DNS resolution failed"); }
  if (!Array.isArray(answers) || answers.length < 1 || answers.length > 32 || answers.some((answer) => !answer || !isPublicWorkProviderEgressAddress(answer.address))) fail("provider proxy DNS resolved to an unsafe address set");
  return answers[0].address;
}

function fixedResponse(socket, status) { if (!socket.destroyed) socket.end(`HTTP/1.1 ${status}\r\nConnection: close\r\n\r\n`); }

export function createWorkProviderEgressProxy({ resolvedProvider, policy: rawPolicy, resolver = lookup, connectFactory = (options) => net.createConnection(options) }) {
  const policy = bindWorkProviderPrivatePolicy(resolvedProvider, rawPolicy, { requireEnabled: true });
  let active = 0;
  return net.createServer({ allowHalfOpen: false }, (client) => {
    if (active >= 1) { fixedResponse(client, "503 Service Unavailable"); return; }
    active += 1; let header = Buffer.alloc(0), upstream = null, established = false, closed = false, clientBytes = 0, upstreamBytes = 0;
    const finish = () => { if (closed) return; closed = true; active -= 1; client.destroy(); upstream?.destroy(); };
    const reject = (status = "403 Forbidden") => { if (!established) fixedResponse(client, status); setTimeout(finish, 10).unref(); };
    client.setTimeout(policy.budgets.maxRequestSeconds * 1000, finish); client.on("error", finish); client.on("close", finish);
    client.on("data", async function onHeader(chunk) {
      if (established) return;
      header = Buffer.concat([header, chunk]);
      if (header.length > MAX_HEADER_BYTES) { client.removeListener("data", onHeader); reject("431 Request Header Fields Too Large"); return; }
      const boundary = header.indexOf("\r\n\r\n"); if (boundary < 0) return;
      client.removeListener("data", onHeader); const requestBytes = Buffer.from(header.subarray(0, boundary + 4)), remainder = Buffer.from(header.subarray(boundary + 4)); header.fill(0);
      let target; try { target = parseWorkProviderConnectRequest(requestBytes, policy); } catch { reject(); return; }
      let address; try { address = await publicResolution(target.host, resolver); } catch { reject(); return; }
      if (closed) return;
      try { upstream = connectFactory({ host: address, port: target.port }); } catch { reject("502 Bad Gateway"); return; }
      if (!upstream || typeof upstream.on !== "function" || typeof upstream.setTimeout !== "function") { reject("502 Bad Gateway"); return; }
      upstream.setTimeout(policy.budgets.maxRequestSeconds * 1000, finish);
      const timer = setTimeout(() => reject("504 Gateway Timeout"), Math.min(10, policy.budgets.maxRequestSeconds) * 1000); timer.unref();
      upstream.once("connect", () => {
        clearTimeout(timer); if (closed) return; established = true; client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        const toUpstream = (data) => { clientBytes += data.length; if (clientBytes > policy.budgets.maxNetworkBytesPerRun) finish(); else upstream.write(data); };
        const toClient = (data) => { upstreamBytes += data.length; if (upstreamBytes > policy.budgets.maxNetworkBytesPerRun) finish(); else client.write(data); };
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
    const parsed = parseBoundWorkProviderPrivatePolicy(readFileSync("/etc/pixel-provider/policy.json"), process.env.PIXEL_WORK_PROVIDER_POLICY_SHA256);
    const server = createWorkProviderEgressProxy(parsed); server.on("error", () => { process.stderr.write("Pixel provider egress proxy failed closed.\n"); process.exitCode = 70; }); server.listen(3128, "0.0.0.0");
  } catch { process.stderr.write("Pixel provider egress proxy failed closed.\n"); process.exitCode = 70; }
}

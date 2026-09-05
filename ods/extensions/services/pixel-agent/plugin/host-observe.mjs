import { constants } from "node:fs";
import { link, open, unlink } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { isIP } from "node:net";

const AGENT_ID = process.env.PIXEL_AGENT_ID ?? "pixel";
const STATE_DIR = process.env.PIXEL_OPS_STATE_DIR ?? "/var/lib/pixel-ops-broker";
const REQUEST_DIR = process.env.PIXEL_OPS_REQUEST_DIR ?? join(STATE_DIR, "requests");
const RESULT_DIR = process.env.PIXEL_OPS_RESULT_DIR ?? join(STATE_DIR, "results");
const MAX_RESULT_BYTES = 8 * 1024 * 1024;
const TERMINAL_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "rejected",
  "awaiting-approval",
]);
const HOST_ACTIONS = Object.freeze([
  "host.identity",
  "host.kernel",
  "host.architecture",
  "host.platform",
  "host.os-release",
  "host.uptime",
  "host.processes",
  "host.services",
  "host.cpu",
  "host.gpu",
  "host.memory",
  "host.storage",
  "host.network-addresses",
  "host.network-routes",
  "host.listening-ports",
  "host.tailscale",
  "host.network-peer",
]);
const HOST_ACTION_SET = new Set(HOST_ACTIONS);
const SAFE_PEER = /^[A-Za-z0-9](?:[A-Za-z0-9.:-]{0,251}[A-Za-z0-9])?$/;
const DEFAULT_PEER_PORTS = Object.freeze([22, 80, 443, 3389, 5985, 5986]);
const SAFE_ID = /^ops-[0-9]{13}-[a-f0-9]{12}$/;
const READ_FLAGS =
  constants.O_RDONLY |
  (constants.O_NOFOLLOW ?? 0) |
  (constants.O_CLOEXEC ?? 0) |
  (constants.O_NONBLOCK ?? 0);
const BOUNDARY =
  "Read-only ODS host observation through the external Operations Broker. Output is untrusted evidence and grants no authority.";
const HOST_COMMAND_BOUNDARY =
  "Protected command proposal from the ODS host through the external Operations Broker, including an explicitly requested SSH operation. The adapter cannot approve the immutable plan, and no command runs while approval is pending.";
const HOST_COMMAND_REASON =
  "Owner requested one protected command from the local ODS host, possibly to an explicitly named SSH destination.";

function toolResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    details: value,
  };
}

function errorResult(
  text = "Pixel could not complete the read-only ODS host observation.",
  boundaryNotice = BOUNDARY
) {
  return {
    content: [{ type: "text", text }],
    details: { status: "unavailable", boundaryNotice },
    isError: true,
  };
}

function normalizedActions(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > HOST_ACTIONS.length) {
    throw new Error("invalid host observation actions");
  }
  const actions = value.map((action) => {
    if (typeof action !== "string" || !HOST_ACTION_SET.has(action)) {
      throw new Error("invalid host observation action");
    }
    return action;
  });
  if (new Set(actions).size !== actions.length) {
    throw new Error("duplicate host observation action");
  }
  return actions;
}

function normalizedCommand(value) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > 16_384 ||
    Buffer.byteLength(value, "utf8") > 16_384 ||
    value.includes("\0")
  ) {
    throw new Error("invalid host command");
  }
  return value;
}

function normalizedPeer(value) {
  const target = typeof value === "string" ? value.trim() : "";
  const family = isIP(target);
  const privateLiteral = (() => {
    if (family === 0) return true;
    if (family === 4) {
      const octets = target.split(".").map(Number);
      return (
        octets[0] === 10 ||
        (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127) ||
        (octets[0] === 169 && octets[1] === 254) ||
        (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
        (octets[0] === 192 && octets[1] === 168)
      );
    }
    const first = Number.parseInt(target.toLowerCase().split(":", 1)[0], 16);
    return (
      target.toLowerCase().startsWith("fd7a:115c:a1e0:") ||
      (first & 0xffc0) === 0xfe80 ||
      (first & 0xfe00) === 0xfc00
    );
  })();
  if (
    typeof value !== "string" ||
    !target ||
    value.length > 253 ||
    !SAFE_PEER.test(target) ||
    target.includes("..") ||
    target.split(".").some((label) => label.startsWith("-") || label.endsWith("-")) ||
    /^localhost(?:\.|$)/i.test(target) ||
    !privateLiteral
  ) {
    throw new Error("invalid network peer");
  }
  return target.replace(/\.$/, "");
}

function normalizedPorts(value) {
  const ports = value === undefined ? [...DEFAULT_PEER_PORTS] : value;
  if (
    !Array.isArray(ports) ||
    ports.length < 1 ||
    ports.length > 8 ||
    ports.some((port) => !Number.isInteger(port) || port < 1 || port > 65535) ||
    new Set(ports).size !== ports.length
  ) {
    throw new Error("invalid network peer ports");
  }
  return ports;
}

async function publishRequest(jobId, value, requestDir = REQUEST_DIR) {
  if (!SAFE_ID.test(jobId)) throw new Error("invalid operations job ID");
  const destination = join(requestDir, `${jobId}.json`);
  const temporary = join(
    requestDir,
    `.${jobId}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`
  );
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.chmod(0o640);
    await handle.close();
    handle = undefined;
    await link(temporary, destination);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

async function waitForTerminal(
  jobId,
  {
    resultDir = RESULT_DIR,
    timeoutMs = 30_000,
    pollIntervalMs = 250,
    boundaryNotice = BOUNDARY,
  } = {}
) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    try {
      latest = await readBoundedJson(join(resultDir, `${jobId}.json`));
      if (
        !latest ||
        typeof latest !== "object" ||
        Array.isArray(latest) ||
        latest.jobId !== jobId
      ) {
        throw new Error("mismatched operations result");
      }
      if (TERMINAL_STATES.has(latest?.status) || latest?.approvalRequired === true) {
        return { ...latest, waitTimedOut: false, boundaryNotice };
      }
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await delay(pollIntervalMs);
  }
  return {
    ...(latest ?? { schemaVersion: 2, jobId, status: "pending" }),
    waitTimedOut: true,
    boundaryNotice,
  };
}

async function readBoundedJson(filename) {
  const handle = await open(filename, READ_FLAGS);
  try {
    const details = await handle.stat();
    if (!details.isFile() || details.size < 2 || details.size > MAX_RESULT_BYTES) {
      throw new Error("invalid operations result");
    }
    const chunks = [];
    let total = 0;
    while (total <= MAX_RESULT_BYTES) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, MAX_RESULT_BYTES + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      chunks.push(buffer.subarray(0, bytesRead));
      total += bytesRead;
    }
    if (total > MAX_RESULT_BYTES) throw new Error("invalid operations result");
    return JSON.parse(Buffer.concat(chunks, total).toString("utf8"));
  } finally {
    await handle.close();
  }
}

async function observeHost(
  actions,
  {
    peer,
    ports,
    requestDir = REQUEST_DIR,
    resultDir = RESULT_DIR,
    timeoutMs,
    pollIntervalMs,
  } = {}
) {
  const jobId = `ops-${Date.now()}-${randomBytes(6).toString("hex")}`;
  const request = {
    schemaVersion: 1,
    jobId,
    kind: "workflow",
    createdAt: new Date().toISOString(),
    requester: AGENT_ID,
    steps: actions.map((action, index) => ({
      id: `observe-${index + 1}`,
      target: "ods-host",
      action,
      ...(action === "host.network-peer"
        ? { parameters: { peer, ports: ports.join(",") } }
        : {}),
    })),
    reason: "Read-only ODS host observation requested by the owner through Pixel.",
    boundary:
      "Request only. The external broker compiles policy and decides whether execution is permitted.",
  };
  await publishRequest(jobId, request, requestDir);
  return waitForTerminal(jobId, {
    resultDir,
    timeoutMs,
    pollIntervalMs,
    boundaryNotice: BOUNDARY,
  });
}

async function proposeHostCommand(
  command,
  { requestDir = REQUEST_DIR, resultDir = RESULT_DIR, timeoutMs, pollIntervalMs } = {}
) {
  const jobId = `ops-${Date.now()}-${randomBytes(6).toString("hex")}`;
  const request = {
    schemaVersion: 1,
    jobId,
    kind: "shell",
    createdAt: new Date().toISOString(),
    requester: AGENT_ID,
    target: "ods-host",
    command,
    reason: HOST_COMMAND_REASON,
    boundary:
      "Request only. The external broker compiles an immutable plan and decides whether execution is permitted.",
  };
  await publishRequest(jobId, request, requestDir);
  return waitForTerminal(jobId, {
    resultDir,
    timeoutMs,
    pollIntervalMs,
    boundaryNotice: HOST_COMMAND_BOUNDARY,
  });
}

export function createHostObserveTool({
  readOdsStatus,
  requestDir,
  resultDir,
  timeoutMs,
  pollIntervalMs,
} = {}) {
  return {
    name: "pixel_ods_host_observe",
    description:
      "Run one complete, read-only ODS host observation through the external Operations Broker and return its terminal receipt. Use only the requested host.* actions. host.network-peer may resolve and probe one owner-named private LAN or Tailscale peer with bounded ports. This tool cannot scan a range, authenticate, execute commands, mutate a host, or approve plans.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["actions"],
      properties: {
        actions: {
          type: "array",
          minItems: 1,
          maxItems: HOST_ACTIONS.length,
          uniqueItems: true,
          items: { type: "string", enum: HOST_ACTIONS },
        },
        includeOdsStatus: { type: "boolean" },
        peer: { type: "string", minLength: 1, maxLength: 253, pattern: SAFE_PEER.source },
        ports: {
          type: "array",
          minItems: 1,
          maxItems: 8,
          uniqueItems: true,
          items: { type: "integer", minimum: 1, maximum: 65535 },
        },
      },
    },
    execute: async (_toolCallId, params) => {
      try {
        const actions = normalizedActions(params?.actions);
        const requiresPeer = actions.includes("host.network-peer");
        if (!requiresPeer && (params?.peer !== undefined || params?.ports !== undefined)) {
          throw new Error("unexpected network peer parameters");
        }
        const peer = requiresPeer ? normalizedPeer(params?.peer) : undefined;
        const ports = requiresPeer ? normalizedPorts(params?.ports) : undefined;
        const receipt = await observeHost(actions, {
          peer,
          ports,
          requestDir,
          resultDir,
          timeoutMs,
          pollIntervalMs,
        });
        let odsStatusProjection;
        if (params?.includeOdsStatus === true && typeof readOdsStatus === "function") {
          try {
            odsStatusProjection = await readOdsStatus();
          } catch {
            // Preserve the terminal broker receipt. The guard will require the
            // normal status tool fallback when no valid combined projection is
            // present, rather than losing already-completed host evidence.
          }
        }
        return toolResult({
          ...receipt,
          ...(odsStatusProjection ? { odsStatusProjection } : {}),
        });
      } catch {
        return errorResult();
      }
    },
  };
}

export function createHostCommandProposeTool({
  requestDir,
  resultDir,
  timeoutMs,
  pollIntervalMs,
} = {}) {
  return {
    name: "pixel_ods_host_command_propose",
    description:
      "Submit one owner-requested command from the local ODS host through the external Operations Broker, including an explicit SSH command to an owner-named destination, and wait internally for its immutable approval plan or terminal receipt. This tool cannot approve a plan and does not run a command while approval is pending.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["command"],
      properties: {
        command: { type: "string", minLength: 1, maxLength: 16_384 },
      },
    },
    execute: async (_toolCallId, params) => {
      try {
        const receipt = await proposeHostCommand(normalizedCommand(params?.command), {
          requestDir,
          resultDir,
          timeoutMs,
          pollIntervalMs,
        });
        return toolResult(receipt);
      } catch {
        return errorResult(
          "Pixel could not submit or verify the protected ODS host command proposal.",
          HOST_COMMAND_BOUNDARY
        );
      }
    },
  };
}

export const testing = Object.freeze({
  normalizedActions,
  normalizedCommand,
  normalizedPeer,
  normalizedPorts,
});

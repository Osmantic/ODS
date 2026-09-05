// Publish one static site selected from Pixel's workspace through the
// dedicated, loopback-only ODS preview service. The host service independently
// validates and snapshots every byte before returning a browser-verifiable URL.

import net from "node:net";

const SOCKET_PATH = "/run/ods-pixel-preview/control.sock";
const PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SITE_ID = /^site-[a-f0-9]{24}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const MAX_RESPONSE_BYTES = 8192;
const BOUNDARY =
  "Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.";

function validRelativeDirectory(value) {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > 512 ||
    value.startsWith("/") ||
    value.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return false;
  }
  const parts = value.split("/");
  return (
    parts.length <= 12 &&
    parts.every(
      (part) => !["", ".", ".."].includes(part) && PATH_COMPONENT.test(part)
    )
  );
}

export function normalizeWorkspacePreviewParams(value) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).sort().join("\n") !== "relativeDirectory" ||
    !validRelativeDirectory(value.relativeDirectory)
  ) {
    throw new Error("invalid Pixel workspace preview request");
  }
  return {
    schemaVersion: 1,
    action: "publish",
    relativeDirectory: value.relativeDirectory,
  };
}

function validResponse(value, request) {
  const expectedKeys = [
    "boundary",
    "bytes",
    "entryFile",
    "entrySha256",
    "executable",
    "files",
    "httpStatus",
    "kind",
    "overwritten",
    "port",
    "readbackVerified",
    "relativeDirectory",
    "schemaVersion",
    "sha256",
    "siteId",
    "status",
    "url",
  ];
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).sort().join("\n") !== expectedKeys.join("\n") ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-workspace-preview" ||
    value.status !== "succeeded" ||
    value.relativeDirectory !== request.relativeDirectory ||
    !SITE_ID.test(value.siteId) ||
    value.siteId !== `site-${value.sha256?.slice(0, 24)}` ||
    typeof value.url !== "string" ||
    value.url !==
      `http://${value.siteId}.localhost:${value.port}/${value.siteId}/` ||
    !Number.isInteger(value.port) ||
    value.port < 1 ||
    value.port > 65535 ||
    !Number.isInteger(value.files) ||
    value.files < 1 ||
    value.files > 128 ||
    !Number.isInteger(value.bytes) ||
    value.bytes < 1 ||
    value.bytes > 16 * 1024 * 1024 ||
    !SHA256.test(value.sha256) ||
    !SHA256.test(value.entrySha256) ||
    value.entryFile !== "index.html" ||
    value.httpStatus !== 200 ||
    value.readbackVerified !== true ||
    value.executable !== false ||
    value.overwritten !== false ||
    value.boundary !== BOUNDARY
  ) {
    throw new Error("invalid Pixel workspace preview response");
  }
  return value;
}

function socketRequest(payload, socketPath = SOCKET_PATH) {
  return new Promise((resolve, reject) => {
    const connection = net.createConnection({ path: socketPath });
    const chunks = [];
    let total = 0;
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      connection.destroy();
      callback(value);
    };
    connection.setTimeout(30_000);
    connection.on("connect", () => {
      connection.end(`${JSON.stringify(payload)}\n`);
    });
    connection.on("data", (chunk) => {
      total += chunk.length;
      if (total > MAX_RESPONSE_BYTES) {
        finish(reject, new Error("Pixel workspace preview response is too large"));
        return;
      }
      chunks.push(chunk);
    });
    connection.on("end", () => {
      try {
        const raw = Buffer.concat(chunks, total).toString("utf8");
        if (!raw.endsWith("\n") || raw.slice(0, -1).includes("\n")) {
          throw new Error("invalid Pixel workspace preview framing");
        }
        finish(resolve, JSON.parse(raw.slice(0, -1)));
      } catch (error) {
        finish(reject, error);
      }
    });
    connection.on("timeout", () =>
      finish(reject, new Error("Pixel workspace preview timed out"))
    );
    connection.on("error", (error) => finish(reject, error));
  });
}

function failedResult() {
  return {
    content: [{
      type: "text",
      text:
        "ODS could not publish a verified browser preview. Keep the site files in the workspace, correct the reported file or entry-point problem if one was returned, and do not claim a localhost URL is live.",
    }],
    details: {
      schemaVersion: 1,
      kind: "ods-pixel-workspace-preview",
      status: "failed",
      boundary: BOUNDARY,
    },
    isError: true,
  };
}

export function createWorkspacePreviewTool({ request = socketRequest } = {}) {
  return {
    name: "pixel_ods_workspace_preview",
    description:
      "Publish and verify a static visual artifact already created by the active model in Pixel's writable workspace. Pass only relativeDirectory after writing the complete site, app, SVG, game, or visualization with workspace tools. ODS never supplies creative starter bytes: it validates and snapshots the model-authored files, then returns the only localhost URL Pixel may claim is browser-accessible. Never start a sandbox server.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["relativeDirectory"],
      properties: {
        relativeDirectory: {
          type: "string",
          description:
            "Static-site directory relative to the Pixel workspace; it must already contain model-authored index.html.",
        },
      },
    },
    execute: async (_toolCallId, params) => {
      try {
        const normalized = normalizeWorkspacePreviewParams(params);
        const response = validResponse(await request(normalized), normalized);
        return {
          content: [{
            type: "text",
            text:
              `ODS independently published and read back ${response.files} workspace static files ` +
              `(${response.bytes} bytes). Verified browser URL: ${response.url}`,
          }],
          details: response,
        };
      } catch {
        return failedResult();
      }
    },
  };
}

export const testing = Object.freeze({
  BOUNDARY,
  validRelativeDirectory,
  validResponse,
});

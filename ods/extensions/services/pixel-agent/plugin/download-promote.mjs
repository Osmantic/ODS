// Narrow bridge from a successful Pixel Operations staged download to the
// owner's Pixel workspace. The root-custodied service independently reopens
// and rehashes the broker artifact; this plugin never receives arbitrary host
// file-read or write authority.

import net from "node:net";

const SOCKET_PATH = "/run/ods-pixel-artifact-promoter/promoter.sock";
const JOB_ID = /^ops-[0-9]{13}-[a-f0-9]{12}$/;
const FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;
const PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const MAX_RESPONSE_BYTES = 8192;
const BOUNDARY =
  "Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority.";

function validSourceUrl(value) {
  if (
    typeof value !== "string" ||
    value.length < 8 ||
    value.length > 4096 ||
    /[\u0000-\u0020\u007f]/.test(value)
  ) {
    return false;
  }
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "https:" &&
      Boolean(parsed.hostname) &&
      !parsed.username &&
      !parsed.password &&
      !parsed.hash
    );
  } catch {
    return false;
  }
}

function validRelativePath(value, filename) {
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
    parts.length >= 1 &&
    parts.length <= 16 &&
    parts.at(-1) === filename &&
    parts.every((part) => !["", ".", ".."].includes(part) && PATH_COMPONENT.test(part))
  );
}

export function normalizePromotionParams(value) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !["jobId", "filename", "relativePath", "sha256", "sourceUrl"].every((key) =>
      Object.hasOwn(value, key)
    ) ||
    Object.keys(value).length !== 5 ||
    typeof value.jobId !== "string" ||
    !JOB_ID.test(value.jobId) ||
    typeof value.filename !== "string" ||
    !FILENAME.test(value.filename) ||
    typeof value.sha256 !== "string" ||
    !SHA256.test(value.sha256) ||
    !validSourceUrl(value.sourceUrl) ||
    !validRelativePath(value.relativePath, value.filename)
  ) {
    throw new Error("invalid exact-download promotion request");
  }
  return {
    schemaVersion: 1,
    action: "promote",
    jobId: value.jobId,
    filename: value.filename,
    relativePath: value.relativePath,
    sha256: value.sha256,
    sourceUrl: value.sourceUrl,
  };
}

function validatedResponse(value, request) {
  const exactKeys = [
    "schemaVersion",
    "kind",
    "status",
    "jobId",
    "filename",
    "relativePath",
    "bytes",
    "sha256",
    "source",
    "requestedSource",
    "executable",
    "overwritten",
    "boundary",
  ];
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).sort().join("\0") !== exactKeys.sort().join("\0") ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-download-promotion" ||
    value.status !== "succeeded" ||
    value.jobId !== request.jobId ||
    value.filename !== request.filename ||
    value.relativePath !== request.relativePath ||
    !Number.isSafeInteger(value.bytes) ||
    value.bytes < 0 ||
    value.bytes > 512 * 1024 * 1024 ||
    value.sha256 !== request.sha256 ||
    !validSourceUrl(value.source) ||
    value.requestedSource !== request.sourceUrl ||
    value.executable !== false ||
    value.overwritten !== false ||
    value.boundary !== BOUNDARY
  ) {
    throw new Error("invalid exact-download promotion response");
  }
  return value;
}

export function requestPromotion(
  request,
  { socketPath = SOCKET_PATH, timeoutMs = 120000, signal } = {}
) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let total = 0;
    const chunks = [];
    const socket = net.createConnection({ path: socketPath });
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", onAbort);
      socket.destroy();
      if (error) reject(error);
      else resolve(value);
    };
    const onAbort = () => finish(new Error("exact-download promotion cancelled"));
    if (signal?.aborted) {
      finish(new Error("exact-download promotion cancelled"));
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    socket.setTimeout(timeoutMs, () => finish(new Error("exact-download promotion timed out")));
    socket.on("error", (error) => finish(error));
    socket.on("connect", () => {
      socket.write(`${JSON.stringify(request)}\n`);
    });
    socket.on("data", (chunk) => {
      total += chunk.length;
      if (total > MAX_RESPONSE_BYTES) {
        finish(new Error("oversized exact-download promotion response"));
        return;
      }
      chunks.push(chunk);
    });
    socket.on("end", () => {
      const raw = Buffer.concat(chunks, total);
      if (raw.length === 0 || raw[raw.length - 1] !== 0x0a || raw.indexOf(0x0a) !== raw.length - 1) {
        finish(new Error("invalid exact-download promotion framing"));
        return;
      }
      try {
        finish(undefined, JSON.parse(raw.subarray(0, -1).toString("utf8")));
      } catch {
        finish(new Error("invalid exact-download promotion response"));
      }
    });
  });
}

function failedResult() {
  return {
    content: [
      {
        type: "text",
        text: "The exact-byte artifact could not be published into the Pixel workspace. No overwrite or substitute file was accepted.",
      },
    ],
    details: { status: "failed", boundary: BOUNDARY },
    isError: true,
  };
}

export function createDownloadPromoteTool({ request = requestPromotion } = {}) {
  return {
    name: "pixel_ods_download_promote",
    description:
      "Publish one successful Pixel Operations staged HTTPS download into the Pixel workspace. Requires the exact broker job, filename, destination relative path, SHA-256, and owner-requested source URL. Publication is create-only, rehashed, mode 0600, and non-executable; it grants no arbitrary file read, overwrite, execution, or destination authority.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["jobId", "filename", "relativePath", "sha256", "sourceUrl"],
      properties: {
        jobId: { type: "string", pattern: "^ops-[0-9]{13}-[a-f0-9]{12}$" },
        filename: { type: "string", pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$" },
        relativePath: { type: "string", minLength: 1, maxLength: 512 },
        sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
        sourceUrl: { type: "string", minLength: 8, maxLength: 4096 },
      },
    },
    execute: async (_callId, params, signal) => {
      try {
        const normalized = normalizePromotionParams(params);
        const response = validatedResponse(await request(normalized, { signal }), normalized);
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                status: response.status,
                relativePath: response.relativePath,
                bytes: response.bytes,
                sha256: response.sha256,
                source: response.source,
                executable: response.executable,
                overwritten: response.overwritten,
              }),
            },
          ],
          details: response,
        };
      } catch {
        return failedResult();
      }
    },
  };
}

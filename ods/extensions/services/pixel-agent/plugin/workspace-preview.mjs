// Publish one static site selected from Pixel's workspace through the
// dedicated, loopback-only ODS preview service. The host service independently
// validates and snapshots every byte before returning a browser-verifiable URL.

import net from "node:net";
import { DERIVED_ARTIFACT_CONTRACT, PREVIEW_RUNTIME_CONTRACT } from "./agent-skills.mjs";
import { dockerWorkspacePreviewRequest } from "./workspace-preview-docker.mjs";

const SOCKET_PATH = "/run/ods-pixel-preview/control.sock";
const PATH_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const ASSET_COMPONENT = /^(?!__ods_)(?!__pycache__$)[A-Za-z0-9_\[][A-Za-z0-9._\[\]-]{0,127}$/;
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
    Object.keys(value).sort().join("\n") !== ('sourceDirectory' in value ? 'relativeDirectory\nsourceDirectory' : 'relativeDirectory') ||
    !validRelativeDirectory(value.relativeDirectory) ||
    ('sourceDirectory' in value && (!validRelativeDirectory(value.sourceDirectory) ||
      !(value.relativeDirectory === value.sourceDirectory || value.relativeDirectory.startsWith(value.sourceDirectory + '/'))))
  ) {
    throw new Error("invalid Pixel workspace preview request");
  }
  return {
    schemaVersion: 1,
    action: "publish",
    relativeDirectory: value.relativeDirectory,
    ...('sourceDirectory' in value ? {sourceDirectory:value.sourceDirectory} : {}),
  };
}

export function validSourceReview(value, directory) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join(',') === 'bytes,files,omitted,relativeDirectory,schemaVersion,sha256,sourceId'
    && value.schemaVersion === 1 && validRelativeDirectory(value.relativeDirectory)
    && (directory === value.relativeDirectory || directory?.startsWith(value.relativeDirectory + '/'))
    && SHA256.test(value.sha256) && value.sourceId === 'source-' + value.sha256.slice(0,24)
    && Number.isInteger(value.files) && value.files >= 1 && value.files <= 128
    && Number.isInteger(value.bytes) && value.bytes >= 0 && value.bytes <= 1024*1024
    && value.omitted && typeof value.omitted === 'object' && !Array.isArray(value.omitted)
    && Object.keys(value.omitted).sort().join(',') === 'directories,files,sensitiveFiles'
    && Object.values(value.omitted).every(n => Number.isInteger(n) && n >= 0 && n <= 16384);
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
  const hasPaths = value && typeof value === "object" &&
    (Object.hasOwn(value, "publishedPaths") || Object.hasOwn(value, "publishedPathsOmitted"));
  const hasEmptyPaths = value && typeof value === "object" &&
    (Object.hasOwn(value, "publishedEmptyPaths") || Object.hasOwn(value, "publishedEmptyPathsOmitted"));
  const receiptKeys = [
    ...expectedKeys,
    ...(hasPaths ? ["publishedPaths", "publishedPathsOmitted"] : []),
    ...(hasEmptyPaths ? ["publishedEmptyPaths", "publishedEmptyPathsOmitted"] : []),
    ...(value && Object.hasOwn(value, 'source') ? ['source'] : []),
  ].sort();
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).sort().join("\n") !== receiptKeys.join("\n") ||
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
  if (hasPaths && (
    !validPathList(value.publishedPaths, value.publishedPathsOmitted) ||
    value.publishedPaths.length + value.publishedPathsOmitted !== value.files ||
    (value.publishedPathsOmitted === 0 && !value.publishedPaths.includes(value.entryFile))
  )) throw new Error("invalid Pixel workspace preview file list");
  if (request.sourceDirectory !== undefined
      ? !validSourceReview(value.source, request.relativeDirectory) || value.source.relativeDirectory !== request.sourceDirectory
      : Object.hasOwn(value, 'source')) throw new Error('invalid Pixel source review receipt');
  // Zero-byte published files: a subset of the file list, never the entry.
  if (hasEmptyPaths && (
    !hasPaths ||
    !validPathList(value.publishedEmptyPaths, value.publishedEmptyPathsOmitted) ||
    value.publishedEmptyPaths.length + value.publishedEmptyPathsOmitted > value.files - 1 ||
    value.publishedEmptyPaths.includes(value.entryFile) ||
    (value.publishedPathsOmitted === 0 &&
      value.publishedEmptyPaths.some((path) => !value.publishedPaths.includes(path)))
  )) throw new Error("invalid Pixel workspace preview empty-file list");
  return value;
}

// Bounded, sorted, unique workspace-relative names as the host emits them.
function validPathList(paths, omitted) {
  return Array.isArray(paths) && paths.length <= 32 &&
    paths.every((path) => typeof path === "string" &&
      path.length >= 1 && path.split("/").every((part) => ASSET_COMPONENT.test(part))) &&
    paths.reduce((size, path) => size + path.length, 0) <= 2048 &&
    paths.every((path, index) => index === 0 || paths[index - 1] < path) &&
    Number.isInteger(omitted) && omitted >= 0;
}

function publishedPathFeedback(response) {
  if (!Object.hasOwn(response, "publishedPaths")) {
    return "The host receipt does not include a file list; do not infer that every requested file was published. ";
  }
  return `Exact published paths relative to ${JSON.stringify(response.relativeDirectory)}: ` +
    `${JSON.stringify(response.publishedPaths)}. ` +
    (response.publishedPathsOmitted > 0
      ? `${response.publishedPathsOmitted} additional published paths omitted from this bounded list. `
      : "This is the complete published file list. ") +
    "Compare the delivered files with the owner's request; this receipt does not determine whether requested files or checks are missing. ";
}

// Informational only: empty files can be legitimate, so publication stands.
export const EMPTY_PUBLISHED_FILES_PREFIX = "Published files that are empty (0 bytes): ";

function emptyPathFeedback(response) {
  const shown = response.publishedEmptyPaths ?? [];
  const omitted = response.publishedEmptyPathsOmitted ?? 0;
  if (shown.length + omitted === 0) return "";
  return EMPTY_PUBLISHED_FILES_PREFIX +
    [...shown, ...(omitted > 0 ? [`${omitted} more`] : [])].join(", ") + ". ";
}

function socketRequest(payload, { socketPath = SOCKET_PATH, signal, timeoutMs = 30_000 } = {}) {
  if (signal?.aborted) return Promise.reject(new Error("Pixel workspace preview cancelled"));
  return new Promise((resolve, reject) => {
    const connection = net.createConnection({ path: socketPath });
    const chunks = [];
    let total = 0;
    let settled = false;
    let deadline;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      signal?.removeEventListener("abort", onAbort);
      connection.destroy();
      callback(value);
    };
    const onAbort = () => finish(reject, new Error("Pixel workspace preview cancelled"));
    // A socket idle timeout restarts on each byte; bound the entire receipt wait.
    deadline = setTimeout(() =>
      finish(reject, new Error("Pixel workspace preview timed out")), timeoutMs);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
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
    connection.on("error", (error) => finish(reject, error));
  });
}

const FAILURE_MESSAGES = {
  source_capture_limit: "The requested source capture exceeds its bounded limits (128 text files, 256 KiB each, 1 MiB total, bounded directory enumeration). No complete source delivery is verified. Narrow the explicit project source selection; do not silently omit required files or publish a different project.",
  source_store_full: "The owner's source review store has reached its 128-capture or 64 MiB limit. This attempt did not create a new source capture or website output. Existing records are preserved. Do not delete history or repeatedly republish to work around this limit; ask the owner to review storage.",
  source_capture_changed: "Project source changed during capture. Wait for project writes to finish, then retry the exact requested source directory. No source snapshot is verified.",
  no_eligible_source: "The source directory contains no eligible UTF-8 source files after exclusions. Verify the intended project directory; do not claim complete source delivery or substitute unrelated files.",
  invalid_json_artifact: "A .json artifact is not valid unambiguous UTF-8 JSON. Generate serialized data from the actual final files using a JSON serializer, parse it back, and compare the decoded contents with those files before retrying. Do not hand-transcribe escaped source code or rename required files to bypass validation.",
  unsupported_file_type: "The project contains an unsupported preview file type. Inspect its file list and keep unrelated files outside the static site directory; CSV and TSV data files are supported.",
  missing_entry: "The selected directory needs a nonempty index.html at its root. Check the directory and entry file before retrying.",
  too_many_files: "The selected site exceeds 128 files. Keep dependencies, build caches, and unrelated files outside the published directory.",
  snapshot_too_large: "The selected site exceeds 16 MiB. Reduce or optimize its static assets before retrying.",
  unsafe_file: "A project file failed validation. Check for an empty index.html or oversized files (4 MiB maximum each), symlinks, hard links, unsafe names, or ownership/permission problems. Files must belong to the workspace owner and must not be writable by group or others (for example, mode 0664 is rejected). Inspect the exact files; remove excessive write permissions only within the intended publication directory, never broaden access or blindly change ownership.",
  unsafe_directory: "The selected directory failed validation. Check its path, ownership, permissions, and symlinks; do not blindly relax permissions.",
};

function validArtifactError(value) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === 'column,line,path' &&
    typeof value.path === 'string' && value.path.length <= 4096 &&
    value.path.split('/').every(part=>/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(part) && part!=='.' && part!=='..') &&
    (value.line===null && value.column===null ||
      Number.isSafeInteger(value.line) && value.line>0 && value.line<=4*1024*1024+1 &&
      Number.isSafeInteger(value.column) && value.column>0 && value.column<=4*1024*1024+1);
}

function validatedFailureCode(value) {
  if (
    value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).filter(key=>key!=='artifactError').sort().join("\n") === "boundary\nerror\nerrorCode\nkind\nschemaVersion\nstatus" &&
    (!Object.hasOwn(value,'artifactError') || value.errorCode==='invalid_json_artifact' && validArtifactError(value.artifactError)) &&
    value.schemaVersion === 1 && value.kind === "ods-pixel-workspace-preview" &&
    value.status === "failed" && value.boundary === BOUNDARY &&
    value.error === "ODS workspace preview publication failed" &&
    Object.hasOwn(FAILURE_MESSAGES, value.errorCode)
  ) return value.errorCode;
  return undefined;
}

function failedResult(code, diagnostic) {
  const location = diagnostic ? ` Artifact ${JSON.stringify(diagnostic.path)}${diagnostic.line===null ? '' : ` at line ${diagnostic.line}, column ${diagnostic.column}`}.` : '';
  return {
    content: [{
      type: "text",
      text: code === "cancelled"
        ? "Pixel stopped waiting for preview publication. A request already accepted by the host may still complete; no new verified preview receipt is returned."
        : code
        ? `ODS could not publish the preview.${location} ${FAILURE_MESSAGES[code]} Do not claim a localhost URL is live until publication succeeds.`
        : "ODS could not publish a verified browser preview. Keep the site files in the workspace, correct the reported file or entry-point problem if one was returned, and do not claim a localhost URL is live.",
    }],
    details: {
      schemaVersion: 1,
      kind: "ods-pixel-workspace-preview",
      status: "failed",
      errorCode: code ?? "unavailable",
      boundary: BOUNDARY,
      ...(diagnostic ? {artifactError:diagnostic} : {}),
    },
    isError: true,
  };
}

export function createWorkspacePreviewTool({ request, transport = "unix" } = {}) {
  if (!["unix", "docker-desktop"].includes(transport)) throw new Error("invalid preview transport");
  request ??= transport === "docker-desktop" ? dockerWorkspacePreviewRequest : socketRequest;
  return {
    name: "pixel_ods_workspace_preview",
    description:
      "Publish and verify a static visual artifact already created by the active model in Pixel's writable workspace. Pass relativeDirectory for browser-ready output. For framework builds, also pass sourceDirectory: the existing project root containing that output; Review then shows a separate bounded source snapshot, never executes it, and preserves the built preview. Source capture excludes dependencies, generated/hidden/sensitive files; it proves captured bytes, not correspondence to the build. ODS never supplies creative starter bytes. Never start a sandbox server. " + PREVIEW_RUNTIME_CONTRACT + " " + DERIVED_ARTIFACT_CONTRACT,
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["relativeDirectory"],
      properties: {
        sourceDirectory: {type:'string',description:'Optional existing project root, workspace-relative and an ancestor of relativeDirectory. Captures eligible UTF-8 source for Review: at most 128 files, 256 KiB each and 1 MiB total. Use for framework builds; do not copy source into dist. Requested capture failures fail publication; never claim a complete project inventory.'},
        relativeDirectory: {
          type: "string",
          description:
            "Static-site directory relative to the Pixel workspace; it must already contain model-authored index.html. Each path component must start with a letter or digit, followed by letters, digits, dots, underscores or hyphens; hidden directories are not publishable. Maximum 128 characters per component, 12 components and 512 characters total.",
        },
      },
    },
    execute: async (_toolCallId, params, signal) => {
      try {
        signal?.throwIfAborted();
        const normalized = normalizeWorkspacePreviewParams(params);
        const raw = await request(normalized, { signal });
        signal?.throwIfAborted();
        const failureCode = validatedFailureCode(raw);
        if (failureCode) return failedResult(failureCode, raw.artifactError);
        const response = validResponse(raw, normalized);
        return {
          content: [{
            type: "text",
            text:
              `ODS independently published and read back ${response.files} workspace static files ` +
              `(${response.bytes} bytes). Verified browser URL: ${response.url}. ` +
              `Inspection snapshot: ${JSON.stringify({siteId:response.siteId,sha256:response.sha256})}. ` +
              'Use pixel_ods_workspace_preview_inspect for this owned preview, not public web_fetch or shell HTTP. Copy both identifiers exactly; sha256 is the full snapshot digest, not entrySha256 or the shortened site suffix. ' +
              'Publication does not render the page; the inspection result also reports its rendered colors by area. Check them before claiming a visible color or style change. ' +
              publishedPathFeedback(response) +
              emptyPathFeedback(response) +
              "This receipt proves publication and HTTP readback only, not successful startup, interactions or durable browser storage. Verify requested behavior in the actual preview before claiming it works. " +
              "If the owner requested derived source files or process logs, publication does not verify their correspondence to executed files or output. If that comparison is missing or fails, repair from the final executed bytes and republish before claiming completion.",
          }],
          details: response,
        };
      } catch {
        return failedResult(signal?.aborted ? "cancelled" : undefined);
      }
    },
  };
}

// Internal trusted finalization probe. This is not a model-callable tool and
// never publishes files or changes the immutable receipt it verifies.
export function createWorkspacePreviewVerifier({request, transport = "unix"} = {}) {
  if (!["unix", "docker-desktop"].includes(transport)) throw new Error("invalid preview transport");
  request ??= transport === "docker-desktop" ? dockerWorkspacePreviewRequest : socketRequest;
  return async (receipt, {signal} = {}) => {
    const normalized = normalizeWorkspacePreviewParams({relativeDirectory:receipt?.relativeDirectory});
    if (!SITE_ID.test(receipt?.siteId) || !SHA256.test(receipt?.sha256) || receipt.siteId !== `site-${receipt.sha256.slice(0,24)}`) return false;
    const result = await request({...normalized, action:'verify-current', siteId:receipt.siteId, sha256:receipt.sha256}, {signal});
    signal?.throwIfAborted();
    return result && Object.keys(result).sort().join(',') === 'boundary,bytes,entrySha256,files,kind,relativeDirectory,schemaVersion,sha256,siteId,status'
      && result.schemaVersion === 1 && result.kind === 'ods-pixel-workspace-preview-verification'
      && result.status === 'matched' && result.boundary === BOUNDARY
      && ['relativeDirectory','siteId','sha256','entrySha256','files','bytes'].every(key=>result[key]===receipt[key]);
  };
}

export const testing = Object.freeze({
  BOUNDARY,
  validRelativeDirectory,
  validResponse,
  socketRequest,
});

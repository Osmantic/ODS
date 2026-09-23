import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const MAX_DIAGNOSTIC_BYTES = 16 * 1024;
const FAILURE_STAGES = new Set([
  "profile-execution", "rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract",
  "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup",
  "authorization-expired", "interrupted",
]);
const USAGE_FIELDS = [
  "runtimeSeconds", "modelRequests", "inputTokens", "outputTokens",
  "networkBytes", "artifactBytes", "failures",
];
const BOUNDARY = "Owner-private content-addressed Builder failure diagnostic. It explains a failed attempt but grants no execution, replay, completion, acceptance, or external-effect authority.";

export class FailureDiagnosticError extends Error {}

function fail(message) {
  throw new FailureDiagnosticError(message);
}

function sha(value) {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) {
    fail(`${label} shape is invalid`);
  }
}

function boundedText(value, maximumBytes, fallback) {
  const source = typeof value === "string" && value.length ? value : fallback;
  let result = "";
  for (const character of source) {
    if (Buffer.byteLength(result + character, "utf8") > maximumBytes) break;
    result += character;
  }
  return result || fallback;
}

export function createFailureDiagnostic({ jobId, iteration, stage, failureStage, error, usage }) {
  const diagnostic = {
    schemaVersion: 1,
    jobId,
    iteration,
    stage,
    failureStage,
    error: {
      name: boundedText(error?.name, 128, "Error"),
      message: boundedText(error?.message, 4096, "failed"),
    },
    usage: Object.fromEntries(USAGE_FIELDS.map((field) => [field, usage?.[field]])),
    boundary: BOUNDARY,
  };
  validateFailureDiagnostic(diagnostic);
  return diagnostic;
}

export function validateFailureDiagnostic(value) {
  exactKeys(value, ["schemaVersion", "jobId", "iteration", "stage", "failureStage", "error", "usage", "boundary"], "failure diagnostic");
  exactKeys(value.error, ["name", "message"], "failure diagnostic error");
  exactKeys(value.usage, USAGE_FIELDS, "failure diagnostic usage");
  if (
    value.schemaVersion !== 1 || !JOB_RE.test(value.jobId ?? "")
    || !Number.isSafeInteger(value.iteration) || value.iteration < 1
    || !["worker", "worker-cleanup"].includes(value.stage)
    || !FAILURE_STAGES.has(value.failureStage)
    || typeof value.error.name !== "string" || Buffer.byteLength(value.error.name, "utf8") > 128
    || typeof value.error.message !== "string" || Buffer.byteLength(value.error.message, "utf8") > 4096
    || value.boundary !== BOUNDARY
    || USAGE_FIELDS.some((field) => !Number.isSafeInteger(value.usage[field]) || value.usage[field] < 0)
  ) fail("failure diagnostic value is invalid");
  return value;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()
      || (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0))) {
    fail(`${label} is not an owner-private real directory`);
  }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function diagnosticPaths(stateRoot, jobId, fingerprint = null) {
  if (!JOB_RE.test(jobId ?? "")) fail("failure diagnostic job identity is invalid");
  if (fingerprint !== null && !SHA_RE.test(fingerprint)) fail("failure diagnostic fingerprint is invalid");
  const root = resolve(stateRoot);
  const store = join(root, "failure-diagnostics");
  const job = join(store, jobId);
  return { root, store, job, path: fingerprint === null ? null : join(job, `${fingerprint}.json`) };
}

export async function retainFailureDiagnostic({ stateRoot, diagnostic }) {
  validateFailureDiagnostic(diagnostic);
  const fingerprint = sha(diagnostic);
  const paths = diagnosticPaths(stateRoot, diagnostic.jobId, fingerprint);
  await privateDirectory(paths.root, "failure diagnostic state root");
  await privateDirectory(paths.store, "failure diagnostic store", true);
  await privateDirectory(paths.job, "failure diagnostic job store", true);
  const payload = `${canonical(diagnostic)}\n`;
  if (Buffer.byteLength(payload, "utf8") > MAX_DIAGNOSTIC_BYTES) fail("failure diagnostic exceeds its byte ceiling");
  const temporary = join(paths.job, `.diagnostic-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(payload, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, paths.path);
    await unlink(temporary);
    await syncDirectory(paths.job);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code !== "EEXIST") throw error;
    const recovered = await readFailureDiagnostic({ stateRoot, jobId: diagnostic.jobId, fingerprint });
    if (canonical(recovered) !== canonical(diagnostic)) fail("existing failure diagnostic differs from its fingerprint");
  }
  return { fingerprint, path: paths.path };
}

export async function readFailureDiagnostic({ stateRoot, jobId, fingerprint }) {
  const paths = diagnosticPaths(stateRoot, jobId, fingerprint);
  await privateDirectory(paths.root, "failure diagnostic state root");
  await privateDirectory(paths.store, "failure diagnostic store");
  await privateDirectory(paths.job, "failure diagnostic job store");
  const { text, details } = await readBoundedRegularText(paths.path, MAX_DIAGNOSTIC_BYTES, "failure diagnostic");
  if (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0 || details.nlink !== 1)) {
    fail("failure diagnostic is not owner-private and single-link");
  }
  const value = validateFailureDiagnostic(parseStrictJson(text, "failure diagnostic"));
  if (value.jobId !== jobId || sha(value) !== fingerprint || text !== `${canonical(value)}\n`) {
    fail("failure diagnostic differs from its content-addressed identity");
  }
  return value;
}

export const failureDiagnosticBoundary = Object.freeze({ value: BOUNDARY, maximumBytes: MAX_DIAGNOSTIC_BYTES });

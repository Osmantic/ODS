import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  link, lstat, mkdir, open, readdir, unlink,
} from "node:fs/promises";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import {
  validateWorkDataArtifactManifest,
  validateWorkDataReport,
  validateWorkDataReportProposal,
  validateWorkDataVerification,
} from "../../scripts/lib/work-contract.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const SEGMENT_RE = /^[A-Za-z0-9._-]{1,255}$/u;
const MAX_RECIPE_BYTES = 16 * 1024 * 1024;
const BUFFER_BYTES = 1024 * 1024;
const MANIFEST_BOUNDARY = "Content-addressed local Data Lab artifacts only. Raw inputs stayed read-only; the manifest grants no truth, publication, external-action, policy, or completion authority until exact replay verification passes.";
const REPORT_BOUNDARY = "Finalized local Data Lab report bound to exact derived artifacts. Exact replay proves reproducibility, not semantic truth; publication, external action, policy change, and completion remain outside worker authority.";
const VERIFICATION_BOUNDARY = "Independent networkless replay evidence only. A byte-for-byte match proves reproducibility and artifact integrity, not semantic truth, business correctness, publication authority, or completion authority.";
const authority = Object.freeze({
  rawInputMutation: false, hostAccess: false, network: false, credentials: false,
  externalEffects: false, publish: false, policyMutation: false, scopeExpansion: false,
});
const formatContract = Object.freeze({
  ".csv": Object.freeze({ format: "csv", kind: "dataset", mediaType: "text/csv" }),
  ".json": Object.freeze({ format: "json", kind: "dataset", mediaType: "application/json" }),
  ".jsonl": Object.freeze({ format: "jsonl", kind: "dataset", mediaType: "application/x-ndjson" }),
  ".md": Object.freeze({ format: "markdown", kind: "document", mediaType: "text/markdown" }),
  ".parquet": Object.freeze({ format: "parquet", kind: "dataset", mediaType: "application/vnd.apache.parquet" }),
  ".png": Object.freeze({ format: "png", kind: "visualization", mediaType: "image/png" }),
  ".sqlite": Object.freeze({ format: "sqlite", kind: "dataset", mediaType: "application/vnd.sqlite3" }),
  ".svg": Object.freeze({ format: "svg", kind: "visualization", mediaType: "image/svg+xml" }),
});

export class DataArtifactError extends Error {}

function fail(message) {
  throw new DataArtifactError(message);
}

function schema(label, errors) {
  if (errors.length) fail(`${label} failed validation: ${errors.join("; ")}`);
}

function boundedInteger(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

function timestamp(now, label) {
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail(`${label} is invalid`);
  return { epoch: String(now.getTime()).padStart(13, "0"), iso: now.toISOString() };
}

function suffix(value) {
  const selected = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(selected)) fail("Data Lab evidence suffix is invalid");
  return selected;
}

function rootsOverlap(left, right) {
  const a = resolve(left);
  const b = resolve(right);
  return a === b || a.startsWith(`${b}${sep}`) || b.startsWith(`${a}${sep}`);
}

function safeSegments(value, label) {
  const segments = value.split("/");
  if (!segments.length || segments.some((segment) => !SEGMENT_RE.test(segment) || segment === "." || segment === "..")) fail(`${label} is unsafe`);
  return segments;
}

async function checkedDirectory(path, label, empty = false) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  if (empty && (await readdir(path)).length !== 0) fail(`${label} must be empty`);
  return resolve(path);
}

async function ensureDestinationDirectory(root, relativePath) {
  let current = root;
  for (const segment of safeSegments(relativePath, "Data Lab destination directory")) {
    current = join(current, segment);
    const existing = await lstat(current).catch(() => null);
    if (!existing) await mkdir(current, { mode: 0o700 });
    const info = await lstat(current);
    if (!info.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("Data Lab destination directory is unsafe");
  }
  return current;
}

async function hashRegularFile(source, maximumBytes, label, destination = null) {
  const before = await lstat(source).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size < 1 || before.size > maximumBytes) fail(`${label} must be a bounded single-link regular file`);
  if (process.platform !== "win32" && (before.uid !== process.geteuid() || (before.mode & 0o022) !== 0)) fail(`${label} has unsafe ownership or write permissions`);
  const sourceHandle = await open(source, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail(`${label} could not be opened safely`));
  const temporary = destination ? join(dirname(destination), `.pixel-data-${process.pid}-${randomBytes(8).toString("hex")}`) : null;
  let outputHandle;
  try {
    const opened = await sourceHandle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) fail(`${label} changed before reading`);
    if (destination) {
      if (await lstat(destination).catch(() => null)) fail(`${label} destination already exists`);
      outputHandle = await open(temporary, "wx", 0o600);
    }
    const digest = createHash("sha256");
    const buffer = Buffer.allocUnsafe(BUFFER_BYTES);
    let total = 0;
    for (;;) {
      const { bytesRead } = await sourceHandle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      total += bytesRead;
      if (total > maximumBytes) fail(`${label} exceeded its byte ceiling`);
      const bytes = buffer.subarray(0, bytesRead);
      digest.update(bytes);
      if (outputHandle) await outputHandle.write(bytes);
    }
    if (total !== opened.size) fail(`${label} changed size while reading`);
    if (outputHandle) {
      await outputHandle.sync();
      await outputHandle.close();
      outputHandle = null;
    }
    const after = await sourceHandle.stat();
    if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs) fail(`${label} changed while reading`);
    if (destination) {
      await link(temporary, destination);
      await unlink(temporary);
    }
    return { bytes: total, sha256: digest.digest("hex") };
  } finally {
    await outputHandle?.close().catch(() => {});
    await sourceHandle.close().catch(() => {});
    if (temporary) await unlink(temporary).catch(() => {});
  }
}

function checkedLimits(limits) {
  if (!limits || typeof limits !== "object" || Array.isArray(limits)) fail("Data Lab artifact limits are missing");
  const maxFiles = boundedInteger(limits.maxFiles, 1, 256, "Data Lab artifact file ceiling");
  const maxBytes = boundedInteger(limits.maxBytes, 1, 1073741824, "Data Lab artifact byte ceiling");
  const allowedFormats = new Set(limits.allowedFormats);
  if (!Array.isArray(limits.allowedFormats) || allowedFormats.size !== limits.allowedFormats.length || allowedFormats.size < 1 || [...allowedFormats].some((format) => !Object.values(formatContract).some((contract) => contract.format === format))) fail("Data Lab artifact format policy is invalid");
  return { maxFiles, maxBytes, allowedFormats };
}

async function inventoryArtifacts(root, limits, destinationRoot = null) {
  const checked = checkedLimits(limits);
  const artifactRoot = await checkedDirectory(root, "Data Lab artifact root");
  const records = [];
  let totalBytes = 0;
  async function visit(directory, segments) {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name.localeCompare(right.name));
    for (const child of children) {
      if (!SEGMENT_RE.test(child.name) || child.name === "." || child.name === "..") fail("Data Lab artifact contains an unsafe name");
      const path = join(directory, child.name);
      const next = [...segments, child.name];
      const info = await lstat(path);
      if (info.isSymbolicLink()) fail("Data Lab artifact tree contains a symbolic link");
      if (info.isDirectory()) {
        if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o022) !== 0)) fail("Data Lab artifact directory has unsafe permissions");
        if (destinationRoot) await ensureDestinationDirectory(destinationRoot, next.join("/"));
        await visit(path, next);
        continue;
      }
      if (!info.isFile() || info.nlink !== 1) fail("Data Lab artifact tree contains a special or hard-linked file");
      if (records.length >= checked.maxFiles) fail("Data Lab artifact file ceiling was exceeded");
      const contract = formatContract[extname(child.name)];
      if (!contract || !checked.allowedFormats.has(contract.format)) fail("Data Lab artifact format is not allowed");
      const remaining = checked.maxBytes - totalBytes;
      if (remaining < 1) fail("Data Lab artifact byte ceiling was exceeded");
      const relativePath = next.join("/");
      const destination = destinationRoot ? join(destinationRoot, ...next) : null;
      const parent = dirname(relativePath).replaceAll("\\", "/");
      if (destination && parent !== ".") await ensureDestinationDirectory(destinationRoot, parent);
      const observed = await hashRegularFile(path, remaining, `Data Lab artifact ${relativePath}`, destination);
      totalBytes += observed.bytes;
      records.push({ path: `artifacts/${relativePath}`, ...contract, ...observed });
    }
  }
  await visit(artifactRoot, []);
  if (!records.length) fail("Data Lab produced no derived artifact");
  records.sort((left, right) => left.path.localeCompare(right.path));
  return { records, totalBytes };
}

function checkedBinding(binding) {
  if (!binding || typeof binding !== "object" || Array.isArray(binding) || !JOB_RE.test(binding.jobId ?? "") || !CLAIM_RE.test(binding.claimId ?? "") || !SHA_RE.test(binding.planSha256 ?? "") || !SHA_RE.test(binding.workspaceSha256 ?? "")) fail("Data Lab artifact binding is invalid");
  if (!["public", "internal", "confidential", "restricted"].includes(binding.dataClassification) || !Array.isArray(binding.datasets) || binding.datasets.length < 1 || binding.datasets.length > 64) fail("Data Lab dataset binding is invalid");
  const datasets = binding.datasets.map((dataset) => {
    if (!/^[a-z][a-z0-9-]{0,62}$/u.test(dataset.datasetId ?? "") || !["csv", "json", "jsonl", "parquet", "sqlite"].includes(dataset.format) || !SHA_RE.test(dataset.contentSha256 ?? "") || !Number.isSafeInteger(dataset.bytes) || dataset.bytes < 1) fail("Data Lab dataset evidence is invalid");
    return { datasetId: dataset.datasetId, format: dataset.format, contentSha256: dataset.contentSha256, bytes: dataset.bytes };
  }).sort((left, right) => left.datasetId.localeCompare(right.datasetId));
  if (new Set(datasets.map((dataset) => dataset.datasetId)).size !== datasets.length) fail("Data Lab dataset identifiers are duplicated");
  return { ...binding, datasets };
}

async function writePrivateJson(path, value) {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  const temporary = join(dirname(path), `.pixel-data-json-${process.pid}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(serialized, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    if (await lstat(path).catch(() => null)) fail("Data Lab evidence already exists");
    await link(temporary, path);
    await unlink(temporary);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
  return { path, bytes: Buffer.byteLength(serialized), sha256: createHash("sha256").update(serialized, "utf8").digest("hex") };
}

export async function exportDataArtifacts(workspaceRoot, outputRoot, binding, limits, options = {}) {
  const workspace = await checkedDirectory(workspaceRoot, "Data Lab workspace");
  const output = await checkedDirectory(outputRoot, "Data Lab output root", true);
  if (rootsOverlap(workspace, output)) fail("Data Lab workspace and output roots overlap");
  const checked = checkedBinding(binding);
  const recipeSource = join(workspace, "recipe.py");
  const recipe = await hashRegularFile(recipeSource, MAX_RECIPE_BYTES, "Data Lab recipe", join(output, "recipe.py"));
  const artifactsOutput = await ensureDestinationDirectory(output, "artifacts");
  const inventory = await inventoryArtifacts(join(workspace, "artifacts"), limits, artifactsOutput);
  const time = timestamp(options.now ?? new Date(), "Data Lab manifest time");
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-artifact-manifest-v1.schema.json", schemaVersion: 1,
    manifestId: `data-manifest-${time.epoch}-${suffix(options.suffix)}`,
    jobId: checked.jobId, claimId: checked.claimId, planSha256: checked.planSha256, workspaceSha256: checked.workspaceSha256,
    createdAt: time.iso, recipe: { path: "recipe.py", ...recipe, runtimeContract: "pixel-data-recipe-v1" },
    datasets: checked.datasets,
    artifacts: inventory.records.map((artifact, index) => ({ artifactId: `artifact-${index + 1}`, ...artifact })),
    totals: { files: inventory.records.length, bytes: inventory.totalBytes },
    dataClassification: checked.dataClassification, rawInputsReadOnly: true, externalEffects: false,
    authority: { ...authority }, boundary: MANIFEST_BOUNDARY,
  };
  schema("Data Lab artifact manifest", validateWorkDataArtifactManifest(manifest));
  const evidence = await writePrivateJson(join(output, "artifact-manifest.json"), manifest);
  return { manifest, evidence };
}

export async function inventoryDataReplay(replayArtifactRoot, limits) {
  const inventory = await inventoryArtifacts(replayArtifactRoot, limits);
  return inventory.records.map((artifact, index) => ({ artifactId: `artifact-${index + 1}`, ...artifact }));
}

export async function writeDataReplayInventory(replayArtifactRoot, outputRoot, limits) {
  const output = await checkedDirectory(outputRoot, "Data Lab replay evidence root");
  if (rootsOverlap(replayArtifactRoot, output)) fail("Data Lab replay and evidence roots overlap");
  const artifacts = await inventoryDataReplay(replayArtifactRoot, limits);
  const evidence = await writePrivateJson(join(output, "replay-inventory.json"), artifacts);
  return { artifacts, evidence };
}

export function finalizeDataReport(proposal, manifest, manifestSha256, binding, options = {}) {
  schema("Data Lab report proposal", validateWorkDataReportProposal(proposal));
  schema("Data Lab artifact manifest", validateWorkDataArtifactManifest(manifest));
  const checked = checkedBinding(binding);
  if (!SHA_RE.test(manifestSha256 ?? "") || manifest.jobId !== checked.jobId || manifest.claimId !== checked.claimId || manifest.planSha256 !== checked.planSha256 || manifest.workspaceSha256 !== checked.workspaceSha256 || manifest.dataClassification !== checked.dataClassification) fail("Data Lab report binding differs from its artifact manifest");
  if (proposal.dataClassification !== checked.dataClassification) fail("Data Lab report classification differs from the immutable plan");
  if (proposal.privateDataIncluded !== (checked.dataClassification !== "public")) fail("Data Lab report private-data attestation differs from its plan classification");
  const proposed = new Map(proposal.artifacts.map((artifact) => [artifact.path, artifact]));
  if (proposed.size !== manifest.artifacts.length || manifest.artifacts.some((artifact) => !proposed.has(artifact.path))) fail("Data Lab report must declare every derived artifact exactly once");
  const artifacts = manifest.artifacts.map((artifact) => ({ ...artifact, purpose: proposed.get(artifact.path).purpose }));
  const byPath = new Map(artifacts.map((artifact) => [artifact.path, artifact]));
  const findings = proposal.findings.map((finding, index) => ({
    findingId: `finding-${index + 1}`, statement: finding.statement,
    evidence: finding.evidencePaths.map((path) => {
      const artifact = byPath.get(path);
      if (!artifact) fail("Data Lab finding references an unknown artifact");
      return { artifactId: artifact.artifactId, path: artifact.path, sha256: artifact.sha256 };
    }),
  }));
  const time = timestamp(options.now ?? new Date(), "Data Lab report time");
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-report-v1.schema.json", schemaVersion: 1,
    reportId: `data-report-${time.epoch}-${suffix(options.suffix)}`,
    jobId: checked.jobId, claimId: checked.claimId, planSha256: checked.planSha256, workspaceSha256: checked.workspaceSha256,
    createdAt: time.iso, manifestSha256, recipeSha256: manifest.recipe.sha256,
    title: proposal.title, summary: proposal.summary, methodology: proposal.methodology,
    artifacts, findings, limitations: proposal.limitations, dataClassification: proposal.dataClassification,
    privateDataIncluded: proposal.privateDataIncluded, externalEffects: false, authority: { ...authority }, boundary: REPORT_BOUNDARY,
  };
  schema("Data Lab report", validateWorkDataReport(report));
  return report;
}

export function verifyDataReplay(manifest, manifestSha256, replayArtifacts, runtime, binding, options = {}) {
  schema("Data Lab artifact manifest", validateWorkDataArtifactManifest(manifest));
  const checked = checkedBinding(binding);
  if (!SHA_RE.test(manifestSha256 ?? "") || manifest.jobId !== checked.jobId || manifest.claimId !== checked.claimId || manifest.planSha256 !== checked.planSha256 || manifest.workspaceSha256 !== checked.workspaceSha256) fail("Data Lab replay binding differs from its artifact manifest");
  if (!Array.isArray(replayArtifacts) || !runtime || typeof runtime !== "object" || Array.isArray(runtime)) fail("Data Lab replay evidence is invalid");
  const observed = new Map(replayArtifacts.map((artifact) => [artifact.path, artifact]));
  if (observed.size !== replayArtifacts.length) fail("Data Lab replay artifact paths are duplicated");
  const expectedPaths = new Set(manifest.artifacts.map((artifact) => artifact.path));
  const unexpectedArtifacts = replayArtifacts.filter((artifact) => !expectedPaths.has(artifact.path)).length;
  const comparisons = manifest.artifacts.map((artifact) => {
    const candidate = observed.get(artifact.path);
    const match = candidate?.sha256 === artifact.sha256 && candidate?.bytes === artifact.bytes && candidate?.format === artifact.format && candidate?.kind === artifact.kind && candidate?.mediaType === artifact.mediaType;
    return {
      artifactId: artifact.artifactId, path: artifact.path, expectedSha256: artifact.sha256,
      observedSha256: candidate?.sha256 ?? null, expectedBytes: artifact.bytes, observedBytes: candidate?.bytes ?? null,
      status: match ? "match" : candidate ? "different" : "missing",
    };
  });
  const checkedRuntime = {
    network: "none", freshWorkspace: true,
    recipeExitCode: boundedInteger(runtime.recipeExitCode, 0, 255, "Data Lab replay exit code"),
    timedOut: runtime.timedOut === true, outputLimitExceeded: runtime.outputLimitExceeded === true, unexpectedArtifacts,
  };
  const passed = checkedRuntime.recipeExitCode === 0 && !checkedRuntime.timedOut && !checkedRuntime.outputLimitExceeded && unexpectedArtifacts === 0 && comparisons.every((artifact) => artifact.status === "match");
  const time = timestamp(options.now ?? new Date(), "Data Lab verification time");
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `data-verification-${time.epoch}-${suffix(options.suffix)}`,
    jobId: checked.jobId, claimId: checked.claimId, planSha256: checked.planSha256, createdAt: time.iso,
    manifestSha256, recipeSha256: manifest.recipe.sha256, status: passed ? "exact-replay-pass" : "fail",
    artifacts: comparisons, runtime: checkedRuntime, semanticAccuracyVerified: false, externalEffects: false,
    authority: { ...authority }, boundary: VERIFICATION_BOUNDARY,
  };
  schema("Data Lab replay verification", validateWorkDataVerification(verification));
  return verification;
}

export const dataArtifactContract = Object.freeze({
  authority, formatContract, manifestBoundary: MANIFEST_BOUNDARY, reportBoundary: REPORT_BOUNDARY,
  verificationBoundary: VERIFICATION_BOUNDARY, maxRecipeBytes: MAX_RECIPE_BYTES,
});

function decodeJsonArgument(value, label, maximumBytes = 65536) {
  if (!/^[A-Za-z0-9_-]{1,100000}$/u.test(value ?? "")) fail(`${label} encoding is invalid`);
  const bytes = Buffer.from(value, "base64url");
  if (bytes.length < 2 || bytes.length > maximumBytes) fail(`${label} is outside its byte boundary`);
  try { return JSON.parse(bytes.toString("utf8")); } catch { fail(`${label} is not JSON`); }
}

async function cli(argv) {
  const operation = argv[0];
  if (operation === "export" && argv.length === 7) {
    const binding = decodeJsonArgument(argv[3], "Data Lab export binding");
    const limits = decodeJsonArgument(argv[4], "Data Lab export limits");
    const now = new Date(argv[5]);
    const exported = await exportDataArtifacts(argv[1], argv[2], binding, limits, { now, suffix: argv[6] });
    process.stdout.write(`${JSON.stringify({
      schemaVersion: 1, operation: "data-lab-export", manifestBytes: exported.evidence.bytes,
      manifestSha256: exported.evidence.sha256, recipeBytes: exported.manifest.recipe.bytes,
      recipeSha256: exported.manifest.recipe.sha256, artifactFiles: exported.manifest.totals.files,
      artifactBytes: exported.manifest.totals.bytes,
    })}\n`);
    return;
  }
  if (operation === "inventory" && argv.length === 4) {
    const limits = decodeJsonArgument(argv[3], "Data Lab replay limits");
    const inventory = await writeDataReplayInventory(argv[1], argv[2], limits);
    process.stdout.write(`${JSON.stringify({
      schemaVersion: 1, operation: "data-lab-replay-inventory", inventoryBytes: inventory.evidence.bytes,
      inventorySha256: inventory.evidence.sha256, artifactFiles: inventory.artifacts.length,
      artifactBytes: inventory.artifacts.reduce((total, artifact) => total + artifact.bytes, 0),
    })}\n`);
    return;
  }
  fail("Usage: data-artifacts.mjs <export WORKSPACE OUTPUT BINDING_B64 LIMITS_B64 NOW SUFFIX|inventory ARTIFACTS OUTPUT LIMITS_B64>");
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  cli(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Data Lab artifact operation failed closed"}\n`);
    process.exitCode = 1;
  });
}

#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  lstat, mkdir, open, readFile, rename, rm,
} from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  validateJobLease,
  validateWorkJob,
  validateWorkLease,
  validateWorkPlan,
  validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";

const MAX_REQUEST_BYTES = 256 * 1024;
const MAX_POLICY_BYTES = 256 * 1024;
const MAX_INPUT_MANIFEST_BYTES = 256 * 1024;
const HASH_RE = /^[a-f0-9]{64}$/;
const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const SAFE_OBJECT_RE = /^[a-f0-9]{64}\.tar$/;
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const budgetFields = Object.freeze([
  "maxRuntimeSeconds", "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxModelRequests",
  "maxInputTokens", "maxOutputTokens", "maxCpuCores", "maxMemoryMiB", "maxDiskBytes",
  "maxArtifactBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit",
]);
const researchLimitFields = Object.freeze([
  "maxQueries", "maxResultsPerQuery", "maxSources", "maxSourceBytes", "maxTotalSourceBytes",
]);
const authority = Object.freeze({
  hostAccess: false,
  ambientCredentials: false,
  arbitraryNetwork: false,
  externalEffects: false,
  merge: false,
  deploy: false,
  publish: false,
  purchase: false,
  policyMutation: false,
  acceptanceCriteriaMutation: false,
  leaseExpansion: false,
});
const planBoundary = "Private immutable Deep Work plan. It authorizes no execution by itself and contains no credential, host path, provider secret, merge, deployment, publication, purchase, production, or external-effect authority.";
const leaseBoundary = "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.";

export class WorkBrokerError extends Error {}

export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}

export function sha256(value) {
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(typeof value === "string" ? value : canonical(value), "utf8");
  return createHash("sha256").update(bytes).digest("hex");
}

function iso(date) {
  return date.toISOString().replace(/\.000Z$/, "Z");
}

function schemaError(label, errors) {
  if (errors.length) throw new WorkBrokerError(`${label} failed validation: ${errors.join("; ")}`);
}

async function safeDirectory(path, label, privateOnly = true) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) throw new WorkBrokerError(`${label} must be a real directory`);
  if (privateOnly && process.platform !== "win32") {
    if (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0) throw new WorkBrokerError(`${label} must be broker-owned without group or other permissions`);
  }
  return path;
}

export async function readBoundedJson(path, maxBytes, label, privateOnly = false) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 2 || info.size > maxBytes) {
    throw new WorkBrokerError(`${label} must be a bounded single-link regular file`);
  }
  if (privateOnly && process.platform !== "win32") {
    if (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0) throw new WorkBrokerError(`${label} must be broker-owned without group or other permissions`);
  }
  const noFollow = constants.O_NOFOLLOW ?? 0;
  const handle = await open(path, constants.O_RDONLY | noFollow).catch((error) => {
    throw new WorkBrokerError(`${label} could not be opened safely: ${error.code ?? "open-failed"}`);
  });
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.size !== info.size || opened.size > maxBytes) {
      throw new WorkBrokerError(`${label} changed during validation`);
    }
    const bytes = await handle.readFile();
    if (bytes.includes(0)) throw new WorkBrokerError(`${label} contains a NUL byte`);
    try {
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch (error) {
      throw new WorkBrokerError(`${label} is not strict UTF-8 JSON: ${error.message}`);
    }
  } finally {
    await handle.close();
  }
}

function validateInputManifest(manifest, request) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) throw new WorkBrokerError("input manifest must be an object");
  if (JSON.stringify(Object.keys(manifest).sort()) !== JSON.stringify(["entries", "jobId", "schemaVersion"])) {
    throw new WorkBrokerError("input manifest has unsupported fields");
  }
  if (manifest.schemaVersion !== 1 || manifest.jobId !== request.jobId || !Array.isArray(manifest.entries)) {
    throw new WorkBrokerError("input manifest identity is invalid");
  }
  if (manifest.entries.length !== request.inputs.length || manifest.entries.length > 64) {
    throw new WorkBrokerError("input manifest and request counts differ");
  }
  const requested = new Map(request.inputs.map((input) => [input.id, input]));
  const ids = new Set();
  for (const entry of manifest.entries) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) throw new WorkBrokerError("input manifest entry is invalid");
    const keys = ["bytes", "classification", "contentSha256", "id", "kind", "mountMode", "objectName"];
    if (JSON.stringify(Object.keys(entry).sort()) !== JSON.stringify(keys)) throw new WorkBrokerError("input manifest entry has unsupported fields");
    if (ids.has(entry.id)) throw new WorkBrokerError("input manifest identifiers must be unique");
    ids.add(entry.id);
    const input = requested.get(entry.id);
    if (!input) throw new WorkBrokerError("input manifest contains an unrequested input");
    if (
      entry.kind !== input.kind
      || entry.mountMode !== "read-only"
      || entry.contentSha256 !== input.contentSha256
      || entry.objectName !== `${entry.contentSha256}.tar`
      || !SAFE_OBJECT_RE.test(entry.objectName)
      || !Number.isSafeInteger(entry.bytes)
      || entry.bytes < 1
      || entry.bytes > input.maxBytes
      || entry.classification !== input.classification
      || classificationRank[entry.classification] > classificationRank[request.dataClassification]
    ) throw new WorkBrokerError(`input manifest entry ${entry.id ?? "unknown"} differs from the request`);
  }
  return manifest.entries;
}

async function hashObject(path, expectedBytes, maxBytes) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size !== expectedBytes || info.size > maxBytes) {
    throw new WorkBrokerError("input object is missing, linked, replaced, or outside its byte ceiling");
  }
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => {
    throw new WorkBrokerError("input object could not be opened safely");
  });
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.size !== info.size) throw new WorkBrokerError("input object changed during validation");
    const digest = createHash("sha256");
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let total = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      total += bytesRead;
      if (total > maxBytes) throw new WorkBrokerError("input object exceeded its byte ceiling while reading");
      digest.update(buffer.subarray(0, bytesRead));
    }
    if (total !== expectedBytes) throw new WorkBrokerError("input object size changed while reading");
    return digest.digest("hex");
  } finally {
    await handle.close();
  }
}

export async function verifyInputObjects(entries, objectStore, request) {
  await safeDirectory(objectStore, "input object store");
  for (const entry of entries) {
    const objectPath = join(objectStore, entry.objectName);
    if (basename(objectPath) !== entry.objectName) throw new WorkBrokerError("unsafe input object name");
    const observed = await hashObject(objectPath, entry.bytes, request.inputs.find((input) => input.id === entry.id).maxBytes);
    if (observed !== entry.contentSha256) throw new WorkBrokerError(`input object ${entry.id} failed SHA-256 verification`);
  }
}

function requireProfilePolicy(policy, request, expectedProfile, readiness = "ready") {
  schemaError("work policy", validateWorkPolicy(policy));
  if (!["scout", "builder", "data-lab", "researcher"].includes(expectedProfile) || request.profile !== expectedProfile) throw new WorkBrokerError(`the requested Deep Work profile is not ${expectedProfile}`);
  const profile = policy.profiles[expectedProfile === "data-lab" ? "dataLab" : expectedProfile];
  const label = expectedProfile === "scout" ? "Scout" : expectedProfile === "builder" ? "Builder" : expectedProfile === "data-lab" ? "Data Lab" : "Researcher";
  if (readiness === "ready") {
    if (!policy.enabled || !policy.runner.prepared || !policy.localModel.prepared || !profile.enabled) throw new WorkBrokerError(`Deep Work ${label} is disabled or unprepared`);
  } else if (readiness === "planned") {
    if (!policy.runner.prepared || !profile.enabled) throw new WorkBrokerError(`Deep Work ${label} planned envelope is disabled or runner-unprepared`);
  } else throw new WorkBrokerError("Deep Work policy readiness mode is invalid");
  if (!policy.runner.allowedIsolation.includes(profile.isolation)) throw new WorkBrokerError(`${label} isolation is not allowed`);
  for (const field of budgetFields) {
    if (request.budgets[field] > profile.maxBudgets[field]) throw new WorkBrokerError(`request exceeds ${label} budget ${field}`);
  }
  if (request.budgets.maxMemoryMiB < profile.minimumMemoryMiB) throw new WorkBrokerError(`${label} requires at least ${profile.minimumMemoryMiB} MiB of memory`);
  if (canonical(request.requestedCapabilities.tools) !== canonical(profile.tools)) throw new WorkBrokerError(`${label} tools differ from private policy`);
  if (canonical(request.requestedCapabilities.network.services) !== canonical(profile.services)) throw new WorkBrokerError(`${label} services differ from private policy`);
  const expectedOutputMode = expectedProfile === "scout" ? "analysis" : expectedProfile === "builder" ? "patch" : "artifacts";
  if (request.outputs.mode !== expectedOutputMode || canonical(request.outputs.requiredKinds) !== canonical(profile.outputKinds)) {
    throw new WorkBrokerError(`${label} outputs differ from private policy`);
  }
  if (expectedProfile === "builder") {
    const verification = request.verification;
    if (!policy.verifier.enabled) throw new WorkBrokerError("Builder verifier is disabled by private policy");
    if (verification.checks.length > policy.verifier.maxChecks) throw new WorkBrokerError("Builder verification exceeds the private check ceiling");
    if (verification.maxRuntimeSeconds > policy.verifier.maxRuntimeSeconds) throw new WorkBrokerError("Builder verification exceeds the private runtime ceiling");
    if (verification.maxOutputBytes > policy.verifier.maxOutputBytes) throw new WorkBrokerError("Builder verification exceeds the private output ceiling");
    const allowed = new Set(policy.verifier.allowedExecutables);
    for (const check of verification.checks) {
      if (check.kind === "command" && !allowed.has(check.argv[0])) throw new WorkBrokerError(`Builder verifier executable ${check.argv[0]} is not allowed by private policy`);
    }
  }
  if (expectedProfile === "researcher") {
    for (const field of researchLimitFields) {
      if (request.research[field] > profile.maxResearch[field]) throw new WorkBrokerError(`request exceeds Researcher ${field}`);
    }
    const allowed = new Set(profile.maxResearch.allowedSourceTypes);
    for (const type of request.research.sourceTypes) if (!allowed.has(type)) throw new WorkBrokerError(`Researcher source type ${type} is not allowed by private policy`);
  }
  if (expectedProfile === "data-lab") {
    const limits = profile.maxData;
    if (request.data.datasets.length > limits.maxDatasets) throw new WorkBrokerError("request exceeds Data Lab dataset count");
    if (request.data.maxArtifactFiles > limits.maxArtifactFiles || request.data.maxArtifactBytes > limits.maxArtifactBytes) throw new WorkBrokerError("request exceeds Data Lab artifact limits");
    const inputFormats = new Set(limits.allowedInputFormats);
    const artifactFormats = new Set(limits.allowedArtifactFormats);
    for (const dataset of request.data.datasets) {
      if (dataset.maxBytes > limits.maxDatasetBytes) throw new WorkBrokerError(`dataset ${dataset.datasetId} exceeds the Data Lab byte ceiling`);
      if (!inputFormats.has(dataset.format)) throw new WorkBrokerError(`dataset format ${dataset.format} is not allowed by private policy`);
    }
    for (const format of request.data.allowedArtifactFormats) if (!artifactFormats.has(format)) throw new WorkBrokerError(`artifact format ${format} is not allowed by private policy`);
  }
  return profile;
}

export function validateWorkJobAgainstPolicy(policy, request) {
  schemaError("work job", validateWorkJob(request));
  return requireProfilePolicy(policy, request, request?.profile);
}

export function reviewWorkJobAgainstPlannedPolicy(policy, request, inputEntries, expectedProfile = request?.profile) {
  schemaError("work job", validateWorkJob(request));
  requireProfilePolicy(policy, request, expectedProfile, "planned");
  const normalizedInputs = validateInputManifest({ schemaVersion: 1, jobId: request.jobId, entries: inputEntries }, request).map((entry) => ({ ...entry }));
  return Object.freeze({
    profile: expectedProfile,
    requestSha256: sha256(request),
    policySha256: sha256(policy),
    inputSetSha256: sha256(normalizedInputs),
    budgetsSha256: sha256(request.budgets),
    capabilitiesSha256: sha256(request.requestedCapabilities),
    outputsSha256: sha256(request.outputs),
  });
}

function compileProfile(request, policy, inputEntries, expectedProfile, options = {}, durableGoalRefresh = false) {
  schemaError("work job", validateWorkJob(request));
  const profile = requireProfilePolicy(policy, request, expectedProfile);
  const now = options.now ?? new Date();
  const requestedAt = Date.parse(request.createdAt);
  if (!Number.isFinite(requestedAt) || requestedAt > now.getTime() + 300000 || !durableGoalRefresh && now.getTime() - requestedAt > policy.maxRequestAgeSeconds * 1000) {
    throw new WorkBrokerError("work request is stale or too far in the future");
  }
  const epoch = String(now.getTime()).padStart(13, "0");
  const suffix = options.suffix ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/.test(suffix)) throw new WorkBrokerError("compiler suffix is invalid");
  const compiledAt = iso(now);
  const requestSha256 = sha256(request);
  const policySha256 = sha256(policy);
  const normalizedInputs = validateInputManifest({ schemaVersion: 1, jobId: request.jobId, entries: inputEntries }, request).map((entry) => ({ ...entry }));
  const inputSetSha256 = sha256(normalizedInputs);
  const executor = {
    id: policy.executor.id,
    version: policy.executor.version,
    sourceCommit: policy.executor.sourceCommit,
    license: policy.executor.license,
    artifactSha256: policy.executor.sha256,
    rpcProtocolVersion: policy.executor.rpcProtocolVersion,
  };
  const isolation = {
    mode: profile.isolation,
    runnerImageDigest: policy.runner.imageDigest,
    freshHome: true,
    inheritEnvironment: false,
    inheritFileDescriptors: false,
    inputMount: "read-only",
    workspaceMount: profile.workspaceMount,
    artifactMount: "write-only-staging",
    destroyAfterRun: true,
  };
  const model = {
    route: "local-only",
    provider: policy.localModel.provider,
    id: policy.localModel.id,
    backendImageDigest: policy.localModel.imageDigest,
    contextWindow: policy.localModel.maxRequestContextTokens,
    supportsVision: policy.localModel.supportsVision,
    endpointContract: "llama.cpp-discovery-and-openai-stream-v1",
  };
  const grantedCapabilities = { tools: [...profile.tools], network: { mode: "brokered", services: [...profile.services] } };
  const outputGate = { allowedKinds: [...profile.outputKinds], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false };
  const researchBackend = expectedProfile === "researcher" ? {
    adapter: profile.backend.adapter,
    endpointContract: profile.backend.endpointContract,
    queryLogging: profile.backend.queryLogging,
    contentRetention: profile.backend.contentRetention,
    webCourierRequired: profile.backend.webCourierRequired,
  } : undefined;
  const dataRuntime = expectedProfile === "data-lab" ? structuredClone(profile.runtime) : undefined;
  const plan = {
    $schema: "https://osmantic.com/pixel/schemas/work-plan-v1.schema.json",
    schemaVersion: 1,
    planId: `workplan-${epoch}-${suffix}`,
    jobId: request.jobId,
    compiledAt,
    requestSha256,
    policySha256,
    inputSetSha256,
    profile: expectedProfile,
    dataClassification: request.dataClassification,
    objective: request.objective,
    acceptanceCriteria: [...request.acceptanceCriteria],
    ...(request.verification ? { verification: structuredClone(request.verification) } : {}),
    ...(request.research ? { research: structuredClone(request.research) } : {}),
    ...(researchBackend ? { researchBackend } : {}),
    ...(request.data ? { data: structuredClone(request.data) } : {}),
    ...(dataRuntime ? { dataRuntime } : {}),
    inputs: normalizedInputs,
    executor,
    model,
    isolation,
    grantedCapabilities,
    budgets: { ...request.budgets },
    outputGate,
    authority: { ...authority },
    status: "compiled",
    boundary: planBoundary,
  };
  schemaError("work plan", validateWorkPlan(plan));
  const planSha256 = sha256(plan);
  const leaseSeconds = Math.min(policy.maxLeaseSeconds, request.budgets.maxRuntimeSeconds);
  const lease = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v1.schema.json",
    schemaVersion: 1,
    leaseId: `worklease-${epoch}-${suffix}`,
    jobId: request.jobId,
    issuedAt: compiledAt,
    expiresAt: iso(new Date(now.getTime() + leaseSeconds * 1000)),
    singleUse: true,
    iteration: 1,
    continuation: null,
    planSha256,
    inputSetSha256,
    policySha256,
    executor,
    model,
    ...(request.research ? { research: structuredClone(request.research) } : {}),
    ...(researchBackend ? { researchBackend: structuredClone(researchBackend) } : {}),
    ...(request.data ? { data: structuredClone(request.data) } : {}),
    ...(dataRuntime ? { dataRuntime: structuredClone(dataRuntime) } : {}),
    isolation,
    grantedCapabilities,
    budgets: { ...request.budgets },
    authority: { ...authority },
    outputGate,
    boundary: leaseBoundary,
  };
  schemaError("work lease", validateWorkLease(lease));
  schemaError("job/lease", validateJobLease(request, lease));
  return { plan, lease, planSha256, requestSha256, policySha256, inputSetSha256 };
}

export function compileScout(request, policy, inputEntries, options = {}) {
  return compileProfile(request, policy, inputEntries, "scout", options);
}

export function compileBuilder(request, policy, inputEntries, options = {}) {
  return compileProfile(request, policy, inputEntries, "builder", options);
}

export function compileResearcher(request, policy, inputEntries, options = {}) {
  return compileProfile(request, policy, inputEntries, "researcher", options);
}

export function compileDataLab(request, policy, inputEntries, options = {}) {
  return compileProfile(request, policy, inputEntries, "data-lab", options);
}

export function compileDurableGoalRefresh(request, policy, inputEntries, options = {}) {
  if (!request || !["scout", "builder", "researcher", "data-lab"].includes(request.profile)) throw new WorkBrokerError("durable goal refresh profile is invalid");
  return compileProfile(request, policy, inputEntries, request.profile, options, true);
}

async function writeBundle(outputRoot, result) {
  await safeDirectory(outputRoot, "private output root");
  const temporary = join(outputRoot, `.compile-${process.pid}-${randomBytes(8).toString("hex")}`);
  const destination = join(outputRoot, result.plan.jobId);
  if (await lstat(destination).then(() => true, () => false)) {
    throw new WorkBrokerError("job was already compiled");
  }
  await mkdir(temporary, { mode: 0o700 });
  try {
    await Promise.all([
      open(join(temporary, "plan.json"), "wx", 0o600).then(async (handle) => { try { await handle.writeFile(`${JSON.stringify(result.plan, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); } }),
      open(join(temporary, "lease.json"), "wx", 0o600).then(async (handle) => { try { await handle.writeFile(`${JSON.stringify(result.lease, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); } }),
    ]);
    await rename(temporary, destination).catch(async (error) => {
      const destinationExists = await lstat(destination).then(() => true, () => false);
      throw new WorkBrokerError(destinationExists ? "job was already compiled" : `could not commit compiled job: ${error.code ?? "rename-failed"}`);
    });
  } catch (error) {
    await rm(temporary, { recursive: true, force: true });
    throw error;
  }
}

function parseArguments(argv) {
  if (!["compile-scout", "compile-builder", "compile-data-lab", "compile-researcher"].includes(argv[0])) throw new WorkBrokerError("Usage: broker.mjs <compile-scout|compile-builder|compile-data-lab|compile-researcher> --request FILE --policy FILE --inputs FILE --object-store DIR --output DIR");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!["--request", "--policy", "--inputs", "--object-store", "--output"].includes(key) || !value) throw new WorkBrokerError("invalid Work Broker arguments");
    if (Object.hasOwn(values, key)) throw new WorkBrokerError("duplicate Work Broker argument");
    values[key] = resolve(value);
  }
  for (const key of ["--request", "--policy", "--inputs", "--object-store", "--output"]) if (!values[key]) throw new WorkBrokerError(`missing ${key}`);
  return { operation: argv[0], values };
}

export async function main(argv = process.argv.slice(2)) {
  const { operation, values } = parseArguments(argv);
  const [request, policy, manifest] = await Promise.all([
    readBoundedJson(values["--request"], MAX_REQUEST_BYTES, "work request"),
    readBoundedJson(values["--policy"], MAX_POLICY_BYTES, "private work policy", true),
    readBoundedJson(values["--inputs"], MAX_INPUT_MANIFEST_BYTES, "private input manifest", true),
  ]);
  if (!JOB_RE.test(request?.jobId ?? "")) throw new WorkBrokerError("work request job ID is invalid");
  const entries = validateInputManifest(manifest, request);
  await verifyInputObjects(entries, values["--object-store"], request);
  const compiler = operation === "compile-scout" ? compileScout : operation === "compile-builder" ? compileBuilder : operation === "compile-data-lab" ? compileDataLab : compileResearcher;
  const result = compiler(request, policy, entries);
  await writeBundle(values["--output"], result);
  process.stdout.write(`${JSON.stringify({
    schemaVersion: 1,
    operation: `pixel-work-${operation}`,
    status: "compiled",
    jobId: request.jobId,
    planSha256: result.planSha256,
    inputSetSha256: result.inputSetSha256,
    leaseId: result.lease.leaseId,
    externalEffects: false,
    boundary: "Content-free compile receipt only; no input, objective, criterion, path, credential, provider secret, or execution authority.",
  })}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-broker: ${error instanceof WorkBrokerError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, rename, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { validateWorkJobAgainstPolicy, verifyInputObjects } from "../work-broker/broker.mjs";
import {
  canonical, validateWorkGoalBrief, validateWorkGoalDeclaration, validateWorkGoalDraft,
  validateWorkInputCatalog, validateWorkJob, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { compileGoalDeclaration } from "./goal-prepare-cli.mjs";

const MAX_BRIEF_BYTES = 512 * 1024;
const MAX_POLICY_BYTES = 512 * 1024;
const MAX_CATALOG_BYTES = 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const PROFILE_BY_KIND = Object.freeze({ inspect: "scout", build: "builder", research: "researcher", "analyze-data": "data-lab" });
const PROFILE_KEY = Object.freeze({ scout: "scout", builder: "builder", researcher: "researcher", "data-lab": "dataLab" });
const BRIEF_BOUNDARY = "Owner-authored plain-language goal structure and selected local input references only. Drafting derives bounded job proposals from private policy but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const REQUEST_BOUNDARY = "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.";
const DECLARATION_BOUNDARY = "Owner-authored long-horizon structure only. Preparation may derive immutable hashes and aggregate budgets but grants no execution, lease, retry, credential, scope expansion, external effect, or completion authority.";
const DRAFT_BOUNDARY = "Private reviewable long-goal draft only. It derives least-authority local job requests from owner choices and private policy, but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const NEXT_STEP = "Review this private draft. Compile its exact jobs and prepare its goal separately; staging and scheduling still require later explicit authority.";
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsRetry: false, grantsScheduling: false,
  grantsCredentials: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const budgetFields = Object.freeze([
  "maxRuntimeSeconds", "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxModelRequests",
  "maxInputTokens", "maxOutputTokens", "maxCpuCores", "maxMemoryMiB", "maxDiskBytes",
  "maxArtifactBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit",
]);
const budgetPresets = Object.freeze({
  quick: Object.freeze({
    maxRuntimeSeconds: 900, maxIterations: 8, maxToolCalls: 400, maxConcurrentSubagents: 1,
    maxModelRequests: 40, maxInputTokens: 200000, maxOutputTokens: 40000, maxCpuCores: 2,
    maxMemoryMiB: 2048, maxDiskBytes: 2147483648, maxArtifactBytes: 33554432,
    maxNetworkBytes: 67108864, maxFailures: 3, noProgressLimit: 2,
  }),
  standard: Object.freeze({
    maxRuntimeSeconds: 3600, maxIterations: 24, maxToolCalls: 2000, maxConcurrentSubagents: 1,
    maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4,
    maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 268435456,
    maxNetworkBytes: 1073741824, maxFailures: 5, noProgressLimit: 3,
  }),
  deep: Object.freeze({
    maxRuntimeSeconds: 14400, maxIterations: 100, maxToolCalls: 10000, maxConcurrentSubagents: 2,
    maxModelRequests: 1000, maxInputTokens: 5000000, maxOutputTokens: 1000000, maxCpuCores: 8,
    maxMemoryMiB: 32768, maxDiskBytes: 53687091200, maxArtifactBytes: 1073741824,
    maxNetworkBytes: 4294967296, maxFailures: 10, noProgressLimit: 5,
  }),
});
const researchPresets = Object.freeze({
  quick: Object.freeze({ maxQueries: 8, maxResultsPerQuery: 5, maxSources: 30, maxSourceBytes: 1048576, maxTotalSourceBytes: 16777216 }),
  standard: Object.freeze({ maxQueries: 20, maxResultsPerQuery: 10, maxSources: 100, maxSourceBytes: 2097152, maxTotalSourceBytes: 67108864 }),
  deep: Object.freeze({ maxQueries: 50, maxResultsPerQuery: 10, maxSources: 250, maxSourceBytes: 4194304, maxTotalSourceBytes: 268435456 }),
});

export class GoalDraftCliError extends Error {}

function fail(message) { throw new GoalDraftCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 10) fail("Usage: goal-draft-cli.mjs --brief FILE --policy FILE --input-catalog FILE --object-store DIR --output NEW_PRIVATE_DIR");
  const allowed = new Set(["--brief", "--policy", "--input-catalog", "--object-store", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal drafting arguments are invalid, unknown, or duplicated");
    values[key] = resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal drafting is missing ${key}`);
  return {
    briefPath: values["--brief"], policyPath: values["--policy"], catalogPath: values["--input-catalog"],
    objectStore: values["--object-store"], outputPath: values["--output"],
  };
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
}

function exactBudgets(profilePolicy, effort) {
  const desired = budgetPresets[effort];
  const budgets = {};
  for (const field of budgetFields) budgets[field] = Math.min(desired[field], profilePolicy.maxBudgets[field]);
  return budgets;
}

function researchContract(milestone, profilePolicy) {
  const desired = researchPresets[milestone.effort], ceiling = profilePolicy.maxResearch;
  for (const sourceType of milestone.research.sourceTypes) if (!ceiling.allowedSourceTypes.includes(sourceType)) fail(`milestone ${milestone.milestoneId} requests a research source type outside private policy`);
  const maxQueries = Math.min(desired.maxQueries, ceiling.maxQueries);
  const maxResultsPerQuery = Math.min(desired.maxResultsPerQuery, ceiling.maxResultsPerQuery);
  const maxSourceBytes = Math.min(desired.maxSourceBytes, ceiling.maxSourceBytes);
  const maxTotalSourceBytes = Math.min(desired.maxTotalSourceBytes, ceiling.maxTotalSourceBytes);
  if (maxTotalSourceBytes < maxSourceBytes) fail(`milestone ${milestone.milestoneId} cannot fit one policy-bounded research source`);
  return {
    mode: "public-web", queryPolicy: "public-sanitized", maxQueries, maxResultsPerQuery,
    maxSources: Math.min(desired.maxSources, ceiling.maxSources, maxQueries * maxResultsPerQuery),
    maxSourceBytes, maxTotalSourceBytes, safeSearch: "strict",
    allowedDomains: [...milestone.research.allowedDomains], deniedDomains: [...milestone.research.deniedDomains],
    sourceTypes: [...milestone.research.sourceTypes], citationVerification: true, retention: "job-only",
    boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
  };
}

function verificationContract(criteria, policy) {
  if (!policy.verifier.enabled) fail("Builder verification is disabled by private policy");
  return {
    mode: "independent",
    checks: [{ id: "patch-integrity", kind: "patch-integrity", criterionIndexes: criteria.map((_, index) => index) }],
    immutablePathPrefixes: [], maxRuntimeSeconds: Math.min(60, policy.verifier.maxRuntimeSeconds),
    maxOutputBytes: Math.min(65536, policy.verifier.maxOutputBytes), network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
}

function dataContract(milestone, selected, profilePolicy, budgets) {
  const datasets = selected.flatMap((entry) => (entry.datasets ?? []).map((dataset) => ({
    datasetId: dataset.datasetId, inputId: entry.id, relativePath: dataset.relativePath,
    format: dataset.format, contentSha256: dataset.contentSha256, maxBytes: dataset.bytes,
  }))).sort((left, right) => left.datasetId.localeCompare(right.datasetId));
  if (datasets.length < 1) fail(`milestone ${milestone.milestoneId} has no admitted dataset files`);
  const desiredFiles = { quick: 8, standard: 32, deep: 128 }[milestone.effort];
  const desiredBytes = { quick: 33554432, standard: 268435456, deep: 1073741824 }[milestone.effort];
  return {
    mode: "local-reproducible", datasets, engines: ["duckdb", "polars", "python", "sqlite"],
    maxArtifactFiles: Math.min(desiredFiles, profilePolicy.maxData.maxArtifactFiles),
    maxArtifactBytes: Math.min(desiredBytes, profilePolicy.maxData.maxArtifactBytes, budgets.maxArtifactBytes),
    allowedArtifactFormats: [...profilePolicy.maxData.allowedArtifactFormats], replayVerification: true, retention: "job-only",
    boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
  };
}

function buildJob({ milestone, brief, policy, catalogById, createdAt, jobId }) {
  const profile = PROFILE_BY_KIND[milestone.kind], profilePolicy = policy.profiles[PROFILE_KEY[profile]];
  if (!profilePolicy?.enabled) fail(`milestone ${milestone.milestoneId} selects a disabled ${profile} profile`);
  const selected = milestone.inputIds.map((id) => {
    const entry = catalogById.get(id);
    if (!entry) fail(`milestone ${milestone.milestoneId} references unknown input ${id}`);
    if (classificationRank[entry.classification] > classificationRank[brief.dataClassification]) fail(`milestone ${milestone.milestoneId} input ${id} exceeds the goal classification`);
    return entry;
  });
  const selectedBytes = selected.reduce((total, entry) => total + entry.bytes, 0);
  if (!Number.isSafeInteger(selectedBytes)) fail(`milestone ${milestone.milestoneId} selected input accounting exceeds the safe integer range`);
  const filesystem = profilePolicy.workspaceMount === "read-only" ? "read-only-workspace" : "disposable-read-write";
  const derivedBudgets = exactBudgets(profilePolicy, milestone.effort);
  if (selectedBytes > derivedBudgets.maxDiskBytes) fail(`milestone ${milestone.milestoneId} selected inputs exceed its policy-clamped disk budget`);
  const data = profile === "data-lab" ? dataContract(milestone, selected, profilePolicy, derivedBudgets) : undefined;
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile, objective: milestone.objective,
    acceptanceCriteria: [...milestone.doneWhen], dataClassification: brief.dataClassification,
    inputs: selected.map((entry) => ({
      id: entry.id, kind: entry.kind, mountMode: "read-only", contentSha256: entry.contentSha256,
      maxBytes: entry.bytes, classification: entry.classification,
    })),
    requestedCapabilities: {
      filesystem, tools: [...profilePolicy.tools], network: { mode: "brokered", services: [...profilePolicy.services] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: derivedBudgets,
    outputs: { mode: profile === "scout" ? "analysis" : profile === "builder" ? "patch" : "artifacts", requiredKinds: [...profilePolicy.outputKinds] },
    ...(profile === "builder" ? { verification: verificationContract(milestone.doneWhen, policy) } : {}),
    ...(profile === "researcher" ? { research: researchContract(milestone, profilePolicy) } : {}),
    ...(data ? { data } : {}),
    boundary: REQUEST_BOUNDARY,
  };
  const errors = validateWorkJob(job);
  if (errors.length) fail(`milestone ${milestone.milestoneId} produced an invalid job: ${errors[0]}`);
  try { validateWorkJobAgainstPolicy(policy, job); } catch (error) { fail(`milestone ${milestone.milestoneId} differs from private policy: ${error.message}`); }
  return { job, entries: selected.map((entry) => ({
    id: entry.id, kind: entry.kind, objectName: entry.objectName, contentSha256: entry.contentSha256,
    bytes: entry.bytes, classification: entry.classification, mountMode: "read-only",
  })) };
}

export function compileGuidedGoal({ brief, policy, catalog, now = new Date(), nonce = randomBytes(16).toString("hex") } = {}) {
  const briefErrors = validateWorkGoalBrief(brief);
  if (briefErrors.length || brief?.boundary !== BRIEF_BOUNDARY) fail(`guided goal brief is invalid: ${briefErrors[0] ?? "boundary differs"}`);
  const policyErrors = validateWorkPolicy(policy);
  if (policyErrors.length) fail(`private work policy is invalid: ${policyErrors[0]}`);
  const catalogErrors = validateWorkInputCatalog(catalog);
  if (catalogErrors.length) fail(`private input catalog is invalid: ${catalogErrors[0]}`);
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0 || !/^[a-f0-9]{32}$/u.test(nonce)) fail("goal draft identity is invalid");
  if (Date.parse(catalog.createdAt) > now.getTime()) fail("private input catalog was created in the future");
  const createdAt = now.toISOString(), epoch = String(now.getTime()).padStart(13, "0");
  const catalogById = new Map(catalog.entries.map((entry) => [entry.id, entry]));
  const built = brief.milestones.map((milestone, index) => buildJob({
    milestone, brief, policy, catalogById, createdAt,
    jobId: `work-${epoch}-${sha(`${nonce}:${index}:${milestone.milestoneId}`).slice(0, 12)}`,
  }));
  const jobs = built.map(({ job }) => job);
  const declaration = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-declaration-v1.schema.json", schemaVersion: 1,
    objective: brief.objective, dataClassification: brief.dataClassification,
    milestones: brief.milestones.map((milestone, index) => ({
      milestoneId: milestone.milestoneId, jobId: jobs[index].jobId, dependsOn: [...milestone.dependsOn],
    })),
    boundary: DECLARATION_BOUNDARY,
  };
  const declarationErrors = validateWorkGoalDeclaration(declaration);
  if (declarationErrors.length) fail(`guided goal declaration is invalid: ${declarationErrors[0]}`);
  compileGoalDeclaration({ declaration, jobs, now, suffix: sha(`${nonce}:goal`).slice(0, 12) });
  const inputManifests = built.map(({ job, entries }) => ({ schemaVersion: 1, jobId: job.jobId, entries }));
  const draft = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-draft-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-goal-draft", draftId: `workdraft-${epoch}-${sha(`${nonce}:draft`).slice(0, 12)}`, createdAt,
    goal: { objective: brief.objective, dataClassification: brief.dataClassification, milestones: jobs.length },
    milestones: brief.milestones.map((milestone, index) => ({
      milestoneId: milestone.milestoneId, jobId: jobs[index].jobId, profile: jobs[index].profile,
      objective: milestone.objective, doneWhen: [...milestone.doneWhen], dependsOn: [...milestone.dependsOn],
      inputIds: [...milestone.inputIds], effort: milestone.effort, budgets: { ...jobs[index].budgets },
      capabilities: {
        workspace: jobs[index].requestedCapabilities.filesystem, tools: [...jobs[index].requestedCapabilities.tools],
        brokeredServices: [...jobs[index].requestedCapabilities.network.services], modelRoute: "local-only",
      },
      verification: jobs[index].profile === "scout" ? "independent-report-verification"
        : jobs[index].profile === "builder" ? "patch-integrity-and-semantic-review"
          : jobs[index].profile === "researcher" ? "citation-and-source-verification"
            : "isolated-exact-replay-and-semantic-review",
      externalEffects: false,
    })),
    bindings: {
      briefSha256: sha(brief), policySha256: sha(policy), inputCatalogSha256: sha(catalog),
      declarationSha256: sha(declaration), jobsSha256: sha(jobs), inputManifestsSha256: sha(inputManifests),
    },
    authority: { ...authority }, nextStep: NEXT_STEP, boundary: DRAFT_BOUNDARY,
  };
  const draftErrors = validateWorkGoalDraft(draft);
  if (draftErrors.length) fail(`guided goal review is invalid: ${draftErrors[0]}`);
  return Object.freeze({
    declaration: Object.freeze(declaration), jobs: Object.freeze(jobs), inputManifests: Object.freeze(inputManifests),
    draft: Object.freeze(draft),
  });
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function runGoalDraftCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal drafting owner is invalid");
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal drafting output directory name is invalid");
  await Promise.all([
    privateDirectory(dirname(options.outputPath), "goal drafting output parent", expectedOwnerUid),
    privateDirectory(options.objectStore, "private input object store", expectedOwnerUid),
  ]);
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal drafting output already exists");
  const [brief, policy, catalog] = await Promise.all([
    readPrivateJson(options.briefPath, MAX_BRIEF_BYTES, "private guided goal brief", expectedOwnerUid),
    readPrivateJson(options.policyPath, MAX_POLICY_BYTES, "private work policy", expectedOwnerUid),
    readPrivateJson(options.catalogPath, MAX_CATALOG_BYTES, "private input catalog", expectedOwnerUid),
  ]);
  const compiled = compileGuidedGoal({ brief, policy, catalog, now: dependencies.now ?? new Date(), ...(dependencies.nonce ? { nonce: dependencies.nonce } : {}) });
  const selectedEntries = new Map(), selectedInputs = new Map();
  for (let index = 0; index < compiled.jobs.length; index += 1) {
    for (const entry of compiled.inputManifests[index].entries) selectedEntries.set(entry.id, entry);
    for (const input of compiled.jobs[index].inputs) selectedInputs.set(input.id, input);
  }
  try {
    await verifyInputObjects([...selectedEntries.values()], options.objectStore, { inputs: [...selectedInputs.values()] });
  } catch (error) {
    fail(`selected input verification failed: ${error.message}`);
  }
  const staging = join(dirname(options.outputPath), `.pixel-work-draft-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    const manifests = join(staging, "input-manifests");
    await mkdir(manifests, { mode: 0o700 });
    await Promise.all([
      writePrivate(join(staging, "goal-declaration.json"), compiled.declaration),
      writePrivate(join(staging, "jobs.json"), compiled.jobs),
      writePrivate(join(staging, "goal-draft.json"), compiled.draft),
      ...compiled.inputManifests.map((manifest) => writePrivate(join(manifests, `${manifest.jobId}.json`), manifest)),
    ]);
    await syncDirectory(manifests); await syncDirectory(staging);
    await rename(staging, options.outputPath); await syncDirectory(dirname(options.outputPath));
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-work-goal-draft", status: "drafted", draftId: compiled.draft.draftId,
      draftSha256: sha(compiled.draft), jobsSha256: compiled.draft.bindings.jobsSha256,
      milestones: compiled.jobs.length, authority: { ...authority },
      boundary: "Content-free local draft receipt only; no objective, criterion, input, path, credential, provider, execution, scheduling, external-effect, or completion authority.",
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY") fail("goal drafting output already exists");
    throw error;
  }
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalDraftCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-draft: ${error instanceof GoalDraftCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

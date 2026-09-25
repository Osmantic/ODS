#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkGoalAssembly, validateWorkGoalBundleManifest, validateWorkGoalControllerBundleManifest,
  validateWorkGoalDeclaration, validateWorkGoalDraft, validateWorkJob,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { runGoalControllerPrepareCommand } from "./goal-controller-prepare-cli.mjs";
import { runGoalPrepareCommand } from "./goal-prepare-cli.mjs";

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const OBJECT_RE = /^[a-f0-9]{64}\.tar$/u;
const DRAFT_FILES = Object.freeze(["goal-declaration.json", "goal-draft.json", "input-manifests", "jobs.json"]);
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsRetry: false, grantsScheduling: false,
  grantsServiceActivation: false, grantsCredentials: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});
const nextStep = "Compile each exact child separately, then review and stage dormant custody; no work has started.";
const boundary = "Private atomic draft-to-controller assembly only. It binds one reviewed draft to exact inert goal and controller bundles but creates no lease or ledger, starts no worker or service, and grants no execution, retry, scheduling, activation, credential, scope expansion, external effect, or completion authority.";
const receiptBoundary = "Content-free inert assembly receipt only; no objective, criterion, input, path, credential, provider, execution, scheduling, external-effect, or completion authority.";

export class GoalAssembleCliError extends Error {}

function fail(message) { throw new GoalAssembleCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function isWithin(path, root) {
  const difference = relative(root, path);
  return difference === "" || difference !== ".." && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 8) fail("Usage: goal-assemble-cli.mjs --draft DIR --environment FILE --confirm-draft-sha256 HASH --output NEW_PRIVATE_DIR");
  const allowed = new Set(["--draft", "--environment", "--confirm-draft-sha256", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal assembly arguments are invalid, unknown, or duplicated");
    values[key] = key === "--confirm-draft-sha256" ? value : resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal assembly is missing ${key}`);
  if (!SHA_RE.test(values["--confirm-draft-sha256"])) fail("goal assembly requires the exact reviewed draft SHA-256 confirmation");
  return {
    draftPath: values["--draft"], environmentPath: values["--environment"],
    confirmation: values["--confirm-draft-sha256"], outputPath: values["--output"],
  };
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function exactNames(path, expected, label) {
  const names = (await readdir(path)).sort();
  if (canonical(names) !== canonical([...expected].sort())) fail(`${label} file set is invalid`);
}

function reviewProjection(job) {
  return {
    profile: job.profile, objective: job.objective, doneWhen: [...job.acceptanceCriteria],
    inputIds: job.inputs.map((entry) => entry.id), budgets: { ...job.budgets },
    capabilities: {
      workspace: job.requestedCapabilities.filesystem, tools: [...job.requestedCapabilities.tools],
      brokeredServices: [...job.requestedCapabilities.network.services], modelRoute: "local-only",
    },
    verification: job.profile === "scout" ? "independent-report-verification"
      : job.profile === "builder" ? "patch-integrity-and-semantic-review"
        : job.profile === "researcher" ? "citation-and-source-verification"
          : "isolated-exact-replay-and-semantic-review",
  };
}

function verifyManifestEntries(manifest, job) {
  if (!manifest || Object.keys(manifest).sort().join(",") !== "entries,jobId,schemaVersion" || manifest.schemaVersion !== 1 || manifest.jobId !== job.jobId || !Array.isArray(manifest.entries)) fail("guided draft input manifest shape or identity differs");
  const projected = [];
  for (const entry of manifest.entries) {
    if (
      !entry || Object.keys(entry).sort().join(",") !== "bytes,classification,contentSha256,id,kind,mountMode,objectName"
      || !OBJECT_RE.test(entry.objectName ?? "") || !SHA_RE.test(entry.contentSha256 ?? "")
      || !Number.isSafeInteger(entry.bytes) || entry.bytes < 1 || entry.mountMode !== "read-only"
    ) fail("guided draft input manifest entry is invalid");
    projected.push({
      id: entry.id, kind: entry.kind, mountMode: entry.mountMode, contentSha256: entry.contentSha256,
      maxBytes: entry.bytes, classification: entry.classification,
    });
  }
  if (canonical(projected) !== canonical(job.inputs)) fail("guided draft input manifest differs from its exact child job");
}

export async function loadExactGuidedGoalDraft(options, expectedOwnerUid) {
  await privateDirectory(options.draftPath, "guided goal draft", expectedOwnerUid);
  await exactNames(options.draftPath, DRAFT_FILES, "guided goal draft");
  const manifestsPath = join(options.draftPath, "input-manifests");
  await privateDirectory(manifestsPath, "guided goal input manifests", expectedOwnerUid);
  const [declaration, jobs, draft] = await Promise.all([
    readPrivateJson(join(options.draftPath, "goal-declaration.json"), MAX_JSON_BYTES, "guided goal declaration", expectedOwnerUid),
    readPrivateJson(join(options.draftPath, "jobs.json"), MAX_JSON_BYTES, "guided goal child jobs", expectedOwnerUid),
    readPrivateJson(join(options.draftPath, "goal-draft.json"), MAX_JSON_BYTES, "guided goal review", expectedOwnerUid),
  ]);
  const draftErrors = validateWorkGoalDraft(draft), declarationErrors = validateWorkGoalDeclaration(declaration);
  if (draftErrors.length || declarationErrors.length || !Array.isArray(jobs) || jobs.length !== declaration?.milestones?.length) fail(`guided goal draft is invalid: ${draftErrors[0] ?? declarationErrors[0] ?? "child set differs"}`);
  if (sha(draft) !== options.confirmation) fail("goal assembly confirmation differs from the exact reviewed draft");
  const expectedManifests = jobs.map((job) => `${job.jobId}.json`).sort();
  await exactNames(manifestsPath, expectedManifests, "guided goal input manifest directory");
  const inputManifests = [];
  for (let index = 0; index < jobs.length; index += 1) {
    const job = jobs[index], jobErrors = validateWorkJob(job);
    if (jobErrors.length) fail(`guided goal child is invalid: ${jobErrors[0]}`);
    const manifest = await readPrivateJson(join(manifestsPath, `${job.jobId}.json`), MAX_JSON_BYTES, "guided goal input manifest", expectedOwnerUid);
    verifyManifestEntries(manifest, job); inputManifests.push(manifest);
    const declared = declaration.milestones[index], reviewed = draft.milestones[index], projection = reviewProjection(job);
    if (
      declared?.milestoneId !== reviewed?.milestoneId || declared?.jobId !== reviewed?.jobId
      || canonical(declared?.dependsOn) !== canonical(reviewed?.dependsOn)
      || reviewed?.profile !== projection.profile || reviewed?.objective !== projection.objective
      || canonical(reviewed?.doneWhen) !== canonical(projection.doneWhen)
      || canonical(reviewed?.inputIds) !== canonical(projection.inputIds)
      || canonical(reviewed?.budgets) !== canonical(projection.budgets)
      || canonical(reviewed?.capabilities) !== canonical(projection.capabilities)
      || reviewed?.verification !== projection.verification
    ) fail("guided goal review differs from its exact declaration or child jobs");
  }
  if (
    draft.goal.objective !== declaration.objective || draft.goal.dataClassification !== declaration.dataClassification
    || draft.goal.milestones !== declaration.milestones.length
    || draft.bindings.declarationSha256 !== sha(declaration) || draft.bindings.jobsSha256 !== sha(jobs)
    || draft.bindings.inputManifestsSha256 !== sha(inputManifests)
  ) fail("guided goal review bindings differ from its private draft files");
  return { declaration, jobs, draft, inputManifests };
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

export async function runGoalAssembleCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal assembly owner is invalid");
  for (const hook of ["afterDraftValidated", "afterGoalPrepared", "afterControllerPrepared"]) if (dependencies[hook] !== undefined && typeof dependencies[hook] !== "function") fail("goal assembly hook is invalid");
  if (dependencies.bindingOutputPath !== undefined && typeof dependencies.bindingOutputPath !== "string") fail("goal assembly binding output path is invalid");
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal assembly output directory name is invalid");
  await privateDirectory(dirname(options.outputPath), "goal assembly output parent", expectedOwnerUid);
  if (isWithin(options.outputPath, options.draftPath) || isWithin(options.draftPath, options.outputPath)) fail("goal assembly output and guided draft overlap");
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal assembly output already exists");
  const loaded = await loadExactGuidedGoalDraft(options, expectedOwnerUid);
  if (dependencies.afterDraftValidated) await dependencies.afterDraftValidated();
  const staging = join(dirname(options.outputPath), `.pixel-work-assembly-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    const goalPath = join(staging, "goal"), controllerPath = join(staging, "controller");
    const finalControllerPath = dependencies.bindingOutputPath === undefined ? join(options.outputPath, "controller") : resolve(dependencies.bindingOutputPath);
    if (dependencies.bindingOutputPath !== undefined && dependencies.bindingOutputPath !== finalControllerPath) fail("goal assembly binding output path is invalid");
    const goalReceipt = await runGoalPrepareCommand([
      "--declaration", join(options.draftPath, "goal-declaration.json"), "--jobs", join(options.draftPath, "jobs.json"), "--output", goalPath,
    ], { expectedOwnerUid, ...(dependencies.now ? { now: dependencies.now } : {}), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    if (goalReceipt.declarationSha256 !== loaded.draft.bindings.declarationSha256 || goalReceipt.jobsSha256 !== loaded.draft.bindings.jobsSha256) fail("prepared goal differs from the exact confirmed draft");
    if (dependencies.afterGoalPrepared) await dependencies.afterGoalPrepared();
    const controllerReceipt = await runGoalControllerPrepareCommand([
      "--goal-bundle", goalPath, "--environment", options.environmentPath, "--output", controllerPath,
    ], { expectedOwnerUid, bindingOutputPath: finalControllerPath });
    if (dependencies.afterControllerPrepared) await dependencies.afterControllerPrepared();
    const [goalManifest, preparedGoal, preparedJobs, controllerManifest, controllerConfig, controllerGoal, controllerJobs] = await Promise.all([
      readPrivateJson(join(goalPath, "goal-bundle.json"), MAX_JSON_BYTES, "assembled goal manifest", expectedOwnerUid),
      readPrivateJson(join(goalPath, "goal.json"), MAX_JSON_BYTES, "assembled goal", expectedOwnerUid),
      readPrivateJson(join(goalPath, "jobs.json"), MAX_JSON_BYTES, "assembled goal jobs", expectedOwnerUid),
      readPrivateJson(join(controllerPath, "controller-bundle.json"), MAX_JSON_BYTES, "assembled goal controller manifest", expectedOwnerUid),
      readPrivateJson(join(controllerPath, "controller.json"), MAX_JSON_BYTES, "assembled goal controller", expectedOwnerUid),
      readPrivateJson(join(controllerPath, "goal.json"), MAX_JSON_BYTES, "assembled controller goal", expectedOwnerUid),
      readPrivateJson(join(controllerPath, "jobs.json"), MAX_JSON_BYTES, "assembled controller jobs", expectedOwnerUid),
    ]);
    const goalManifestErrors = validateWorkGoalBundleManifest(goalManifest), controllerManifestErrors = validateWorkGoalControllerBundleManifest(controllerManifest);
    if (
      goalManifestErrors.length || controllerManifestErrors.length || goalManifest.goalId !== goalReceipt.goalId
      || goalManifest.goalSha256 !== goalReceipt.goalSha256 || goalManifest.jobsSha256 !== goalReceipt.jobsSha256
      || sha(preparedGoal) !== goalReceipt.goalSha256 || sha(preparedJobs) !== goalReceipt.jobsSha256
      || controllerReceipt.goalId !== goalReceipt.goalId || sha(controllerManifest) !== controllerReceipt.controllerBundleSha256
      || controllerManifest.configSha256 !== sha(controllerConfig) || controllerManifest.goalSha256 !== sha(controllerGoal)
      || controllerManifest.jobsSha256 !== sha(controllerJobs) || canonical(controllerGoal) !== canonical(preparedGoal)
      || canonical(controllerJobs) !== canonical(preparedJobs) || controllerManifest.goalSha256 !== goalReceipt.goalSha256
      || controllerManifest.jobsSha256 !== goalReceipt.jobsSha256 || controllerManifest.sourceBundleManifestSha256 !== sha(goalManifest)
    ) fail("prepared controller differs from the exact confirmed goal");
    const assembledAt = dependencies.now ?? new Date();
    if (!(assembledAt instanceof Date) || !Number.isSafeInteger(assembledAt.getTime()) || assembledAt.getTime() < 0) fail("goal assembly time is invalid");
    const assembly = {
      $schema: "https://osmantic.com/pixel/schemas/work-goal-assembly-v1.schema.json", schemaVersion: 1,
      operation: "pixel-work-goal-assemble", assembledAt: assembledAt.toISOString(), draftId: loaded.draft.draftId,
      goalId: goalReceipt.goalId, state: "assembled-inert", layout: { review: "review.json", goalBundle: "goal", controllerBundle: "controller" },
      bindings: {
        draftSha256: options.confirmation, declarationSha256: loaded.draft.bindings.declarationSha256,
        jobsSha256: loaded.draft.bindings.jobsSha256, inputManifestsSha256: loaded.draft.bindings.inputManifestsSha256,
        goalBundleSha256: sha(goalManifest), controllerBundleSha256: controllerReceipt.controllerBundleSha256,
        environmentSha256: controllerManifest.environmentSha256,
      },
      authority: { ...authority }, nextStep, boundary,
    };
    const assemblyErrors = validateWorkGoalAssembly(assembly);
    if (assemblyErrors.length) fail(`goal assembly manifest is invalid: ${assemblyErrors[0]}`);
    await writePrivate(join(staging, "review.json"), loaded.draft);
    await writePrivate(join(staging, "assembly.json"), assembly);
    await syncDirectory(staging); await rename(staging, options.outputPath); await syncDirectory(dirname(options.outputPath));
    return Object.freeze({
      schemaVersion: 1, operation: assembly.operation, state: assembly.state, draftId: assembly.draftId, goalId: assembly.goalId,
      draftSha256: assembly.bindings.draftSha256, goalBundleSha256: assembly.bindings.goalBundleSha256,
      controllerBundleSha256: assembly.bindings.controllerBundleSha256, milestones: loaded.jobs.length, outputName,
      authority: { ...authority }, nextStep, boundary: receiptBoundary,
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY") fail("goal assembly output already exists");
    throw error;
  }
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalAssembleCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-assemble: ${error instanceof GoalAssembleCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

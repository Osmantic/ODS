import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, rename, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkGoal, validateWorkGoalBundleManifest, validateWorkGoalDeclaration, validateWorkJob,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";

const MAX_DECLARATION_BYTES = 512 * 1024;
const MAX_JOBS_BYTES = 8 * 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const GOAL_BOUNDARY = "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.";
const DECLARATION_BOUNDARY = "Owner-authored long-horizon structure only. Preparation may derive immutable hashes and aggregate budgets but grants no execution, lease, retry, credential, scope expansion, external effect, or completion authority.";
const BUNDLE_BOUNDARY = "Private atomic immutable goal bundle only. Preparation derives exact job hashes and aggregate ceilings but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsRetry: false, grantsScheduling: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false });
const aggregateFields = Object.freeze(["maxRuntimeSeconds", "maxModelRequests", "maxInputTokens", "maxOutputTokens", "maxNetworkBytes", "maxArtifactBytes", "maxFailures"]);

export class GoalPrepareCliError extends Error {}

function fail(message) { throw new GoalPrepareCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 6) fail("Usage: goal-prepare-cli.mjs --declaration FILE --jobs FILE --output NEW_PRIVATE_DIR");
  const allowed = new Set(["--declaration", "--jobs", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal preparation arguments are invalid, unknown, or duplicated");
    values[key] = resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal preparation is missing ${key}`);
  return { declarationPath: values["--declaration"], jobsPath: values["--jobs"], outputPath: values["--output"] };
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
}

function exactAggregateBudgets(jobs) {
  const budgets = { maxJobs: jobs.length };
  for (const field of aggregateFields) {
    const total = jobs.reduce((sum, job) => sum + job.budgets[field], 0);
    if (!Number.isSafeInteger(total)) fail(`aggregate goal budget ${field} exceeds safe accounting`);
    budgets[field] = total;
  }
  return budgets;
}

export function compileGoalDeclaration({ declaration, jobs, now = new Date(), suffix = randomBytes(6).toString("hex") } = {}) {
  const declarationErrors = validateWorkGoalDeclaration(declaration);
  if (declarationErrors.length || declaration?.boundary !== DECLARATION_BOUNDARY) fail(`goal declaration is invalid: ${declarationErrors[0] ?? "boundary differs"}`);
  if (!Array.isArray(jobs) || jobs.length !== declaration.milestones.length) fail("goal child job set is incomplete or widened");
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0 || !/^[a-f0-9]{12}$/u.test(suffix)) fail("goal preparation identity is invalid");
  const jobsById = new Map();
  for (const job of jobs) {
    const errors = validateWorkJob(job);
    if (errors.length) fail(`goal child job is invalid: ${errors[0]}`);
    if (jobsById.has(job.jobId)) fail("goal child jobs contain a duplicate identifier");
    if (job.dataClassification !== declaration.dataClassification) fail("goal child classification differs from the declaration");
    if (Date.parse(job.createdAt) > now.getTime()) fail("goal child job was created after the immutable goal");
    jobsById.set(job.jobId, job);
  }
  const orderedJobs = declaration.milestones.map((milestone) => {
    const job = jobsById.get(milestone.jobId);
    if (!job) fail(`goal milestone ${milestone.milestoneId} has no exact child job`);
    return job;
  });
  if (new Set(orderedJobs).size !== jobs.length) fail("goal declaration does not bind every child job exactly once");
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${String(now.getTime()).padStart(13, "0")}-${suffix}`, createdAt: now.toISOString(), requester: "pixel",
    objective: declaration.objective, dataClassification: declaration.dataClassification,
    milestones: declaration.milestones.map((milestone, index) => ({
      milestoneId: milestone.milestoneId, jobId: orderedJobs[index].jobId, jobSha256: sha(orderedJobs[index]),
      profile: orderedJobs[index].profile, dependsOn: [...milestone.dependsOn],
    })),
    budgets: exactAggregateBudgets(orderedJobs),
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: GOAL_BOUNDARY,
  };
  const goalErrors = validateWorkGoal(goal);
  if (goalErrors.length) fail(`prepared goal is invalid: ${goalErrors[0]}`);
  return Object.freeze({ goal: Object.freeze(goal), jobs: Object.freeze(orderedJobs.map((job) => Object.freeze(structuredClone(job)))) });
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(typeof value === "string" ? value : `${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function runGoalPrepareCommand(argv, dependencies = {}) {
  const options = parseArguments(argv);
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal preparation owner is invalid");
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal preparation output directory name is invalid");
  await privateDirectory(dirname(options.outputPath), "goal preparation output parent", expectedOwnerUid);
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal preparation output already exists");
  const [declaration, jobs] = await Promise.all([
    readPrivateJson(options.declarationPath, MAX_DECLARATION_BYTES, "private goal declaration", expectedOwnerUid),
    readPrivateJson(options.jobsPath, MAX_JOBS_BYTES, "private goal child jobs", expectedOwnerUid),
  ]);
  const compiled = compileGoalDeclaration({ declaration, jobs, now: dependencies.now ?? new Date(), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-bundle-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-goal-prepare", goalId: compiled.goal.goalId, createdAt: compiled.goal.createdAt,
    goalSha256: sha(compiled.goal), jobsSha256: sha(compiled.jobs), declarationSha256: sha(declaration),
    milestones: compiled.goal.milestones.length, authority: { ...authority }, boundary: BUNDLE_BOUNDARY,
  };
  const manifestErrors = validateWorkGoalBundleManifest(manifest);
  if (manifestErrors.length) fail(`prepared goal bundle manifest is invalid: ${manifestErrors[0]}`);
  const staging = join(dirname(options.outputPath), `.pixel-work-goal-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    await writePrivate(join(staging, "goal.json"), compiled.goal);
    await writePrivate(join(staging, "jobs.json"), compiled.jobs);
    const manifestText = `${JSON.stringify(manifest, null, 2)}\n`;
    await writePrivate(join(staging, "goal-bundle.json"), manifestText);
    await syncDirectory(staging); await rename(staging, options.outputPath); await syncDirectory(dirname(options.outputPath));
    return Object.freeze({
      schemaVersion: 1, operation: manifest.operation, goalId: manifest.goalId, goalSha256: manifest.goalSha256,
      jobsSha256: manifest.jobsSha256, declarationSha256: manifest.declarationSha256, bundleManifestSha256: sha(manifestText),
      milestones: manifest.milestones, outputName, authority: { ...authority }, boundary: BUNDLE_BOUNDARY,
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY") fail("goal preparation output already exists");
    throw error;
  }
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalPrepareCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-prepare: ${error instanceof GoalPrepareCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

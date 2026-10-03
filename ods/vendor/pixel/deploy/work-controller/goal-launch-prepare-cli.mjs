import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import {
  compileBuilder, compileDataLab, compileResearcher, compileScout, readBoundedJson, verifyInputObjects,
} from "../work-broker/broker.mjs";
import {
  canonical, validateJobPlanLease, validateWorkGoal, validateWorkGoalAssembly, validateWorkGoalBundleManifest,
  validateWorkGoalController, validateWorkGoalControllerBundleManifest, validateWorkGoalDraft,
  validateWorkGoalLaunchPreparation, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { loadExactGuidedGoalDraft, runGoalAssembleCommand } from "./goal-assemble-cli.mjs";
import { runGoalStageCommand } from "./goal-stage-cli.mjs";

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const authority = Object.freeze({
  containsExpiringChildLeases: true, createsReadyGoalState: false, grantsExecution: false,
  grantsScheduling: false, grantsServiceActivation: false, grantsCredentials: false,
  grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
});
const nextStep = "Inspect the published package, then stage it by its exact manifest hash; service rendering and activation remain separate.";
const boundary = "Private atomic launch preparation only. It converts one exact reviewed draft into an inert controller assembly and expiring single-use child leases, but creates no goal ledger, starts no worker or service, and grants no execution, scheduling, activation, credential, scope expansion, external effect, or completion authority.";
const receiptBoundary = "Content-free launch-preparation receipt only. It reveals no objective, criterion, input, path, credential, provider, plan, lease, or private policy content and grants no execution, scheduling, activation, external-effect, or completion authority.";

export class GoalLaunchPrepareCliError extends Error {}

function fail(message) { throw new GoalLaunchPrepareCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function isWithin(path, root) {
  const difference = relative(root, path);
  return difference === "" || difference !== ".." && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 8) fail("Usage: goal-launch-prepare-cli.mjs --draft DIR --environment FILE --confirm-draft-sha256 HASH --output NEW_PRIVATE_DIR");
  const allowed = new Set(["--draft", "--environment", "--confirm-draft-sha256", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal launch preparation arguments are invalid, unknown, or duplicated");
    values[key] = key === "--confirm-draft-sha256" ? value : resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal launch preparation is missing ${key}`);
  if (!SHA_RE.test(values["--confirm-draft-sha256"])) fail("goal launch preparation requires the exact reviewed draft SHA-256 confirmation");
  return { draftPath: values["--draft"], environmentPath: values["--environment"], confirmation: values["--confirm-draft-sha256"], outputPath: values["--output"] };
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

async function readPrivateJsonRecord(path, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, MAX_JSON_BYTES, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  try { return { value: JSON.parse(text), bytes }; } catch { fail(`${label} is not JSON`); }
}
async function readPrivateJson(path, label, expectedOwnerUid) { return (await readPrivateJsonRecord(path, label, expectedOwnerUid)).value; }

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function compilerFor(profile) {
  if (profile === "scout") return compileScout;
  if (profile === "builder") return compileBuilder;
  if (profile === "researcher") return compileResearcher;
  if (profile === "data-lab") return compileDataLab;
  fail("goal launch preparation child profile is unsupported");
}

async function writeCompiledChild(path, compiled) {
  await mkdir(path, { mode: 0o700 });
  await Promise.all([writePrivate(join(path, "plan.json"), compiled.plan), writePrivate(join(path, "lease.json"), compiled.lease)]);
  await syncDirectory(path);
}

function exactNames(names, expected, label) {
  if (canonical([...names].sort()) !== canonical([...expected].sort())) fail(`${label} file set is invalid`);
}

async function inspectPreparedAssembly({ assemblyPath, finalAssemblyPath, loaded, confirmation, expectedOwnerUid }) {
  exactNames(await readdir(assemblyPath), ["assembly.json", "controller", "goal", "review.json"], "goal launch assembly");
  const goalPath = join(assemblyPath, "goal"), controllerPath = join(assemblyPath, "controller");
  await privateDirectory(goalPath, "goal launch prepared goal", expectedOwnerUid);
  await privateDirectory(controllerPath, "goal launch controller", expectedOwnerUid);
  exactNames(await readdir(goalPath), ["goal-bundle.json", "goal.json", "jobs.json"], "goal launch prepared goal");
  const controllerNames = await readdir(controllerPath);
  const baseControllerNames = ["controller-bundle.json", "controller.json", "goal.json", "jobs.json"];
  if (![baseControllerNames, [...baseControllerNames, "capability-policy.json"]].some((expected) => canonical([...controllerNames].sort()) === canonical([...expected].sort()))) fail("goal launch controller file set is invalid");
  const [assembly, review, goalManifest, goal, goalJobs, controllerManifest, config, controllerGoal, controllerJobs] = await Promise.all([
    readPrivateJson(join(assemblyPath, "assembly.json"), "goal launch assembly manifest", expectedOwnerUid),
    readPrivateJson(join(assemblyPath, "review.json"), "goal launch reviewed draft", expectedOwnerUid),
    readPrivateJson(join(goalPath, "goal-bundle.json"), "goal launch goal manifest", expectedOwnerUid),
    readPrivateJson(join(goalPath, "goal.json"), "goal launch prepared goal", expectedOwnerUid),
    readPrivateJson(join(goalPath, "jobs.json"), "goal launch prepared jobs", expectedOwnerUid),
    readPrivateJson(join(controllerPath, "controller-bundle.json"), "goal launch controller manifest", expectedOwnerUid),
    readPrivateJson(join(controllerPath, "controller.json"), "goal launch controller configuration", expectedOwnerUid),
    readPrivateJson(join(controllerPath, "goal.json"), "goal launch controller goal", expectedOwnerUid),
    readPrivateJson(join(controllerPath, "jobs.json"), "goal launch controller jobs", expectedOwnerUid),
  ]);
  const errors = [
    ...validateWorkGoalAssembly(assembly), ...validateWorkGoalDraft(review), ...validateWorkGoalBundleManifest(goalManifest),
    ...validateWorkGoal(goal), ...validateWorkGoalControllerBundleManifest(controllerManifest),
    ...validateWorkGoalController(config), ...validateWorkGoal(controllerGoal),
  ];
  if (errors.length || !Array.isArray(goalJobs) || !Array.isArray(controllerJobs)) fail(`goal launch prepared assembly is invalid: ${errors[0] ?? "child registry is invalid"}`);
  exactNames(controllerNames, config.capabilityRuntime ? [...baseControllerNames, "capability-policy.json"] : baseControllerNames, "goal launch controller");
  if (config.capabilityRuntime) {
    const capabilityPolicy = await readPrivateJson(join(controllerPath, "capability-policy.json"), "goal launch capability policy", expectedOwnerUid);
    if (config.capabilityRuntime.controllerPolicyPath !== join(finalAssemblyPath, "controller", "capability-policy.json") || controllerManifest.capabilityPolicySha256 !== sha(capabilityPolicy) || controllerManifest.capabilityBindingsSha256 !== sha(config.capabilityRuntime.bindings)) fail("goal launch capability bindings differ from the prepared controller");
  } else if (controllerManifest.capabilityPolicySha256 !== undefined || controllerManifest.capabilityBindingsSha256 !== undefined) fail("goal launch has unexpected capability policy custody");
  if (
    sha(review) !== confirmation || canonical(review) !== canonical(loaded.draft)
    || canonical(goalJobs) !== canonical(loaded.jobs) || canonical(controllerJobs) !== canonical(loaded.jobs)
    || canonical(controllerGoal) !== canonical(goal)
    || goalManifest.goalId !== goal.goalId || goalManifest.goalSha256 !== sha(goal) || goalManifest.jobsSha256 !== sha(goalJobs)
    || controllerManifest.goalId !== goal.goalId || controllerManifest.configSha256 !== sha(config)
    || controllerManifest.goalSha256 !== sha(controllerGoal) || controllerManifest.jobsSha256 !== sha(controllerJobs)
    || controllerManifest.sourceBundleManifestSha256 !== sha(goalManifest)
    || assembly.draftId !== loaded.draft.draftId || assembly.goalId !== goal.goalId
    || assembly.bindings.draftSha256 !== confirmation || assembly.bindings.declarationSha256 !== loaded.draft.bindings.declarationSha256
    || assembly.bindings.jobsSha256 !== loaded.draft.bindings.jobsSha256 || assembly.bindings.inputManifestsSha256 !== loaded.draft.bindings.inputManifestsSha256
    || assembly.bindings.goalBundleSha256 !== sha(goalManifest) || assembly.bindings.controllerBundleSha256 !== sha(controllerManifest)
    || assembly.bindings.environmentSha256 !== controllerManifest.environmentSha256
    || config.goalPath !== join(finalAssemblyPath, "controller", "goal.json")
    || config.jobsPath !== join(finalAssemblyPath, "controller", "jobs.json")
  ) fail("goal launch prepared assembly bindings differ from the exact reviewed draft");
  return { assembly, controllerManifest, config };
}

async function revalidateCompiledChildren({ compiledPath, children, jobs, expectedOwnerUid }) {
  exactNames(await readdir(compiledPath), jobs.map((job) => job.jobId), "goal launch compiled children");
  const expected = new Map(children.map((child) => [child.jobId, child]));
  const compiled = [];
  for (const job of jobs) {
    const directory = join(compiledPath, job.jobId); await privateDirectory(directory, `goal launch compiled child ${job.jobId}`, expectedOwnerUid);
    exactNames(await readdir(directory), ["lease.json", "plan.json"], `goal launch compiled child ${job.jobId}`);
    const [plan, lease] = await Promise.all([
      readPrivateJson(join(directory, "plan.json"), `goal launch compiled plan ${job.jobId}`, expectedOwnerUid),
      readPrivateJson(join(directory, "lease.json"), `goal launch compiled lease ${job.jobId}`, expectedOwnerUid),
    ]);
    const errors = validateJobPlanLease(job, plan, lease), record = expected.get(job.jobId);
    if (
      errors.length || !record || plan.requestSha256 !== sha(job) || sha(plan) !== record.planSha256
      || sha(lease) !== record.leaseSha256 || lease.expiresAt !== record.expiresAt || plan.profile !== record.profile
    ) fail(`goal launch compiled child ${job.jobId} differs before publication: ${errors[0] ?? "binding differs"}`);
    compiled.push({ job, plan, lease });
  }
  return compiled;
}

export async function runGoalLaunchPrepareCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal launch preparation owner is invalid");
  for (const hook of ["afterAssembly", "afterChildCompiled"]) if (dependencies[hook] !== undefined && typeof dependencies[hook] !== "function") fail("goal launch preparation hook is invalid");
  if (dependencies.childSuffix !== undefined && typeof dependencies.childSuffix !== "function") fail("goal launch preparation child suffix source is invalid");
  const now = dependencies.now ?? new Date();
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0) fail("goal launch preparation time is invalid");
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal launch preparation output directory name is invalid");
  await privateDirectory(dirname(options.outputPath), "goal launch preparation output parent", expectedOwnerUid);
  if (isWithin(options.outputPath, options.draftPath) || isWithin(options.draftPath, options.outputPath)) fail("goal launch preparation output and guided draft overlap");
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal launch preparation output already exists");
  const loaded = await loadExactGuidedGoalDraft(options, expectedOwnerUid);
  const staging = join(dirname(options.outputPath), `.pixel-work-launch-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    const assemblyPath = join(staging, "assembly"), finalAssemblyPath = join(options.outputPath, "assembly");
    let assemblyReceipt;
    try {
      assemblyReceipt = await runGoalAssembleCommand([
        "--draft", options.draftPath, "--environment", options.environmentPath,
        "--confirm-draft-sha256", options.confirmation, "--output", assemblyPath,
      ], {
        expectedOwnerUid, now, ...(dependencies.goalSuffix ? { suffix: dependencies.goalSuffix } : {}),
        bindingOutputPath: join(finalAssemblyPath, "controller"),
      });
    } catch (error) { fail(`goal launch assembly failed: ${error.message}`); }
    if (dependencies.afterAssembly) await dependencies.afterAssembly({ assemblyPath });
    const initialInspection = await inspectPreparedAssembly({ assemblyPath, finalAssemblyPath, loaded, confirmation: options.confirmation, expectedOwnerUid });
    const { assembly, controllerManifest, config } = initialInspection;
    if (
      assemblyReceipt.draftSha256 !== options.confirmation || assembly.bindings.draftSha256 !== options.confirmation
      || assembly.bindings.controllerBundleSha256 !== sha(controllerManifest) || assemblyReceipt.controllerBundleSha256 !== sha(controllerManifest)
    ) fail("goal launch prepared assembly bindings differ from the exact reviewed draft");
    if (
      isWithin(options.outputPath, config.objectStore) || isWithin(config.objectStore, options.outputPath)
      || isWithin(options.outputPath, config.policyPath) || isWithin(config.policyPath, options.outputPath)
    ) fail("goal launch preparation output overlaps immutable policy or input custody");
    const policy = await readBoundedJson(config.policyPath, MAX_JSON_BYTES, "private work policy", true);
    const policyErrors = validateWorkPolicy(policy);
    if (policyErrors.length || sha(policy) !== loaded.draft.bindings.policySha256 || controllerManifest.policySha256 !== sha(policy)) fail(`goal launch private policy differs from the reviewed draft: ${policyErrors[0] ?? "hash differs"}`);

    const compiledPath = join(staging, "compiled-jobs"); await mkdir(compiledPath, { mode: 0o700 });
    const children = [];
    for (let index = 0; index < loaded.jobs.length; index += 1) {
      const job = loaded.jobs[index], inputManifest = loaded.inputManifests[index];
      try { await verifyInputObjects(inputManifest.entries, config.objectStore, job); } catch (error) { fail(`goal launch child input verification failed: ${error.message}`); }
      const suffix = dependencies.childSuffix?.(job.jobId, index) ?? randomBytes(6).toString("hex");
      if (!/^[a-f0-9]{12}$/u.test(suffix)) fail("goal launch preparation child suffix is invalid");
      let compiled;
      try { compiled = compilerFor(job.profile)(job, policy, inputManifest.entries, { now, suffix }); }
      catch (error) { fail(`goal launch child compilation failed: ${error.message}`); }
      await writeCompiledChild(join(compiledPath, job.jobId), compiled);
      children.push({ jobId: job.jobId, profile: job.profile, planSha256: sha(compiled.plan), leaseSha256: sha(compiled.lease), expiresAt: compiled.lease.expiresAt });
      if (dependencies.afterChildCompiled) await dependencies.afterChildCompiled({ jobId: job.jobId, index });
    }
    children.sort((left, right) => left.jobId.localeCompare(right.jobId));
    await revalidateCompiledChildren({ compiledPath, children, jobs: loaded.jobs, expectedOwnerUid });
    const finalInspection = await inspectPreparedAssembly({ assemblyPath, finalAssemblyPath, loaded, confirmation: options.confirmation, expectedOwnerUid });
    if (
      sha(finalInspection.assembly) !== sha(assembly) || sha(finalInspection.controllerManifest) !== sha(controllerManifest)
      || sha(finalInspection.config) !== sha(config)
    ) fail("goal launch prepared assembly changed before publication");
    const finalPolicy = await readBoundedJson(config.policyPath, MAX_JSON_BYTES, "private work policy", true);
    if (sha(finalPolicy) !== sha(policy)) fail("goal launch private policy changed before publication");
    for (let index = 0; index < loaded.jobs.length; index += 1) {
      try { await verifyInputObjects(loaded.inputManifests[index].entries, config.objectStore, loaded.jobs[index]); }
      catch (error) { fail(`goal launch child input changed before publication: ${error.message}`); }
    }
    const preparation = {
      $schema: "https://osmantic.com/pixel/schemas/work-goal-launch-preparation-v1.schema.json", schemaVersion: 1,
      operation: "pixel-work-goal-launch-prepare", preparedAt: now.toISOString(), draftId: loaded.draft.draftId,
      goalId: assembly.goalId, state: "prepared-inactive", layout: { assembly: "assembly", compiledJobs: "compiled-jobs" },
      bindings: {
        draftSha256: options.confirmation, assemblySha256: sha(assembly),
        controllerBundleSha256: sha(controllerManifest), policySha256: sha(policy), compiledJobsSha256: sha(children),
      },
      children, authority: { ...authority }, nextStep, boundary,
    };
    const errors = validateWorkGoalLaunchPreparation(preparation);
    if (errors.length) fail(`goal launch preparation manifest is invalid: ${errors[0]}`);
    await writePrivate(join(staging, "launch-preparation.json"), preparation);
    await syncDirectory(compiledPath); await syncDirectory(staging);
    await rename(staging, options.outputPath); await syncDirectory(dirname(options.outputPath));
    return Object.freeze({
      schemaVersion: 1, operation: preparation.operation, state: preparation.state, draftId: preparation.draftId,
      goalId: preparation.goalId, draftSha256: preparation.bindings.draftSha256,
      controllerBundleSha256: preparation.bindings.controllerBundleSha256,
      compiledJobsSha256: preparation.bindings.compiledJobsSha256,
      manifestSha256: sha(`${JSON.stringify(preparation, null, 2)}\n`), children: children.length,
      profiles: [...new Set(children.map((child) => child.profile))].sort(), authority: { ...authority }, nextStep, boundary: receiptBoundary,
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY" || error?.code === "EPERM") fail("goal launch preparation output already exists");
    throw error;
  }
}

function parseBundleOperation(argv, operation, requiresConfirmation) {
  const expectedLength = requiresConfirmation ? 5 : 3;
  if (!Array.isArray(argv) || argv[0] !== operation || argv.length !== expectedLength || argv[1] !== "--bundle" || typeof argv[2] !== "string" || !argv[2]) {
    fail(`Usage: goal-launch-prepare-cli.mjs ${operation} --bundle PRIVATE_DIR${requiresConfirmation ? " --confirm-manifest-sha256 HASH" : ""}`);
  }
  const bundlePath = resolve(argv[2]);
  if (requiresConfirmation && (argv[3] !== "--confirm-manifest-sha256" || !SHA_RE.test(argv[4] ?? ""))) fail("goal launch staging requires the exact launch-preparation manifest SHA-256 confirmation");
  return { bundlePath, confirmation: requiresConfirmation ? argv[4] : null };
}

export async function inspectGoalLaunchPreparation(bundlePath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal launch inspection owner is invalid");
  await privateDirectory(bundlePath, "goal launch preparation", expectedOwnerUid);
  exactNames(await readdir(bundlePath), ["assembly", "compiled-jobs", "launch-preparation.json"], "goal launch preparation");
  const manifestRecord = await readPrivateJsonRecord(join(bundlePath, "launch-preparation.json"), "goal launch preparation manifest", expectedOwnerUid);
  const preparation = manifestRecord.value, errors = validateWorkGoalLaunchPreparation(preparation);
  if (errors.length) fail(`goal launch preparation manifest is invalid: ${errors[0]}`);
  const assemblyPath = join(bundlePath, preparation.layout.assembly), compiledPath = join(bundlePath, preparation.layout.compiledJobs);
  await privateDirectory(assemblyPath, "goal launch assembly", expectedOwnerUid);
  await privateDirectory(compiledPath, "goal launch compiled children", expectedOwnerUid);
  const [review, jobs] = await Promise.all([
    readPrivateJson(join(assemblyPath, "review.json"), "goal launch reviewed draft", expectedOwnerUid),
    readPrivateJson(join(assemblyPath, "controller", "jobs.json"), "goal launch controller jobs", expectedOwnerUid),
  ]);
  const inspected = await inspectPreparedAssembly({
    assemblyPath, finalAssemblyPath: assemblyPath, loaded: { draft: review, jobs },
    confirmation: preparation.bindings.draftSha256, expectedOwnerUid,
  });
  if (
    preparation.draftId !== review.draftId || preparation.goalId !== inspected.assembly.goalId
    || preparation.bindings.assemblySha256 !== sha(inspected.assembly)
    || preparation.bindings.controllerBundleSha256 !== sha(inspected.controllerManifest)
    || preparation.bindings.compiledJobsSha256 !== sha(preparation.children)
    || preparation.children.length !== jobs.length
  ) fail("goal launch preparation bindings differ from the published package");
  const compiled = await revalidateCompiledChildren({ compiledPath, children: preparation.children, jobs, expectedOwnerUid });
  const policy = await readBoundedJson(inspected.config.policyPath, MAX_JSON_BYTES, "private work policy", true), policyErrors = validateWorkPolicy(policy);
  if (policyErrors.length || sha(policy) !== preparation.bindings.policySha256 || inspected.controllerManifest.policySha256 !== sha(policy)) fail(`goal launch private policy differs from the published package: ${policyErrors[0] ?? "hash differs"}`);
  for (const child of compiled) {
    try { await verifyInputObjects(child.plan.inputs, inspected.config.objectStore, child.job); }
    catch (error) { fail(`goal launch published input verification failed: ${error.message}`); }
  }
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-launch-inspect", state: "package-verified",
    goalId: preparation.goalId, manifestSha256: sha(manifestRecord.bytes),
    controllerBundleSha256: preparation.bindings.controllerBundleSha256,
    compiledJobsSha256: preparation.bindings.compiledJobsSha256, children: preparation.children.length,
    profiles: [...new Set(preparation.children.map((child) => child.profile))].sort(), authority: { ...authority },
    nextStep, boundary: "Content-free exact package inspection only. It does not assert current goal or service state and performs no staging, scheduling, worker launch, service activation, provider call, external effect, or completion action.",
  });
}

export async function runGoalLaunchCommand(argv, dependencies = {}) {
  if (!Array.isArray(argv) || !["prepare", "inspect", "stage"].includes(argv[0])) fail("Usage: goal-launch-prepare-cli.mjs <prepare|inspect|stage> ...");
  if (argv[0] === "prepare") return runGoalLaunchPrepareCommand(argv.slice(1), dependencies);
  const options = parseBundleOperation(argv, argv[0], argv[0] === "stage");
  const inspected = await inspectGoalLaunchPreparation(options.bundlePath, dependencies);
  if (argv[0] === "inspect") return inspected;
  if (options.confirmation !== inspected.manifestSha256) fail("goal launch staging confirmation differs from the exact published package");
  let staged;
  try {
    staged = await runGoalStageCommand([
      "--controller-bundle", join(options.bundlePath, "assembly", "controller"),
      "--compiled-jobs", join(options.bundlePath, "compiled-jobs"),
      "--confirm-controller-sha256", inspected.controllerBundleSha256,
    ], dependencies.stage ?? dependencies);
  } catch (error) { fail(`goal launch staging failed: ${error.message}`); }
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-launch-stage", action: staged.action, state: staged.state,
    goalId: staged.goalId, manifestSha256: inspected.manifestSha256,
    controllerBundleSha256: inspected.controllerBundleSha256, goalCheckpointSha256: staged.goalCheckpointSha256,
    children: staged.children,
    authority: {
      containsExactChildLeases: true, createsReadyGoalState: true, grantsExecution: false,
      activatesService: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false,
    },
    boundary: "Content-free exact launch-package staging receipt. Staging creates dormant ready custody but starts no worker, schedule, service, provider, network request, external effect, or completion action.",
  });
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalLaunchCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-launch-prepare: ${error instanceof GoalLaunchPrepareCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalLaunchPrepareAuthority = authority;
export const goalLaunchPrepareBoundary = boundary;

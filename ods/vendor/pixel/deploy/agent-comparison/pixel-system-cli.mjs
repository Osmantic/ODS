import { createHash } from "node:crypto";
import { constants, createReadStream } from "node:fs";
import { chmod, lstat, mkdir, open, readFile, realpath, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { WorkBrokerError, canonical } from "../work-broker/broker.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateWorkGoalControllerEnvironment, validateWorkModelBackendConfig, validateWorkPolicy } from "../../scripts/lib/work-contract.mjs";
import {
  buildPixelAssistantExecution, PixelArmError, outcomeInferenceBoundaryPolicy, outcomeInferencePolicy, pixelArmContract,
  reviewPixelPlannedCompatibility, reviewPixelProductCompatibility, runPixelProductArm,
} from "./pixel-arm.mjs";
import { AssistantPreparationError, prepareAssistantRuntime } from "./assistant-runtime-prepare.mjs";
import { loadResearchFixturePipelineOptions } from "./research-fixture-adapter.mjs";
import { prepareModelBackendLaunch } from "../work-controller/model-backend-cli.mjs";
import { modelBackendServerArgumentsFromLaunch } from "../work-controller/model-backend-launch.mjs";
import { modelBackendLifecycleSha256, startModelBackend, stopModelBackend } from "../work-controller/model-backend-lifecycle.mjs";
import { requireQualifiedModel } from "../work-controller/model-qualification.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const MAX_JSON_BYTES = 16 * 1024 * 1024;
const RUN_RE = /^outcomerun-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const RELATIVE_RE = /^(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._+@=-]+(?:\/[A-Za-z0-9._+@=-]+)*$/u;
const CONFIG_BOUNDARY = "Owner-private templates and a disposable runtime root for exact Pixel outcome runs only. Configuration grants no model start, task execution, provider, credential, external-effect, merge, deployment, publication, completion, acceptance, or promotion authority.";
export const PIXEL_POLICY_BINDING_BOUNDARY = "Trusted-terminal review and destination-bound publication of one new owner-private Pixel comparison configuration that changes only the policy template pointer to one exact enabled, prepared, currently qualified DSV4 policy. It does not edit or replace the current configuration, enable or mutate policy, start a model or service, route a job, use network or credentials, perform an external effect, or grant completion, publication, deployment, acceptance, or promotion authority.";
export const PIXEL_RUNTIME_BOUNDARY = "Content-free exact Pixel runtime identity only. It proves the admitted DSV4, current model qualification, Work policy, runner, and backend bindings for one fresh run and grants no execution, model start, provider, credential, network, external-effect, completion, publication, deployment, acceptance, or promotion authority.";
export const PIXEL_REVIEW_BOUNDARY = "Content-free read-only review of one exact Pixel DSV4 comparison runtime and admitted task. It proves the measured model, current qualification, launch, policy, runner, verifier, backend, inference, harness, tools, services, budgets, and task-compilation bindings after removing all temporary private review state, and grants no execution, model start, container, network, provider, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.";
export const PIXEL_PLANNED_REVIEW_BOUNDARY = "Content-free read-only structural compatibility review of one exact admitted task against one exact disabled, unqualified, or ready Pixel DSV4 policy template. It proves only task, model identity, inference, profile, runner, tools, services, budgets, verifier, input, and output-envelope compatibility; it does not assert qualification, launch readiness, or executable policy state, creates no plan or lease, starts no model, container, network, task, or tool, and grants no execution, credential, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const STATE_BOUNDARY = "Owner-private exact Pixel comparison state only. It is not user evidence and grants no execution, model start, task, provider, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.";
export const PIXEL_HARNESS_FILES = Object.freeze([
  "control/server.py",
  "control/ui/app.js",
  "control/ui/index.html",
  "control/ui/styles.css",
  "deploy/agent-comparison/pixel-arm.mjs",
  "deploy/agent-comparison/pixel-system-cli.mjs",
  "deploy/agent-comparison/assistant-model-proxy.mjs",
  "deploy/agent-comparison/assistant-runtime-prepare.mjs",
  "schemas/portal-outcome-assistant-template-v1.schema.json",
  "deploy/agent-comparison/assistant-template.example.json",
  "deploy/agent-comparison/research-fixture-adapter.mjs",
  "deploy/work-model-proxy/inference-policy.mjs",
  "deploy/work-model-proxy/proxy.mjs",
  "plugin/chat-audit.js",
  "plugin/index.js",
  "plugin/openclaw.plugin.json",
  "plugin-frontier/chat-audit.js",
  "plugin-frontier/index.js",
  "plugin-frontier/openclaw.plugin.json",
  "plugin-ops/chat-audit.js",
  "plugin-ops/index.js",
  "plugin-ops/openclaw.plugin.json",
  "scripts/portal_outcome_assistant_evidence.py",
  "scripts/portal_outcome_assistant_runtime.py",
  "scripts/portal_outcome_assistant_system.py",
  "scripts/portal_outcome_controller_evidence.py",
  "scripts/portal_outcome_pixel_livesystem.py",
  "scripts/portal_outcome_pixel_orchestrate.py",
  "scripts/portal_outcome_runtime_control.py",
  "scripts/render-config.mjs",
  "deploy/work-broker/broker.mjs",
  "deploy/work-controller/model-backend-cli.mjs",
  "deploy/work-controller/model-backend-launch.mjs",
  "deploy/work-controller/model-backend-lifecycle.mjs",
  "deploy/work-controller/model-backend-runtime.mjs",
  "deploy/work-controller/checkpoints.mjs",
  "deploy/work-controller/goal-run-bundles.mjs",
  "deploy/work-controller/goals.mjs",
  "deploy/work-runner/docker-boundary.mjs",
  "deploy/work-runner/docker-researcher.mjs",
  "deploy/work-runner/docker-supervisor.mjs",
  "deploy/work-runner/research-tool.mjs",
  "deploy/work-runner/runner-core.mjs",
  "deploy/work-research-broker/broker.mjs",
  "deploy/work-research-broker/citation-verifier.mjs",
  "deploy/work-research-broker/ledger.mjs",
  "deploy/work-research-broker/pipeline.mjs",
  "deploy/work-research-broker/report-finalizer.mjs",
  "deploy/work-research-broker/research-service.mjs",
  "deploy/work-research-broker/tool-queue.mjs",
  "deploy/work-controller/research-revision-review.mjs",
  "scripts/lib/work-contract.mjs",
]);

export class PixelSystemError extends Error {}
function fail(message) { throw new PixelSystemError(message); }
export function pixelSystemDiagnostic(error) {
  return error instanceof PixelSystemError || error instanceof PixelArmError || error instanceof AssistantPreparationError || error instanceof WorkBrokerError
    ? error.message
    : "unexpected failure";
}
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function hash(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} fields are invalid`);
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function runId(value) { if (typeof value !== "string" || !RUN_RE.test(value)) fail("Pixel comparison run identity is invalid"); return value; }
function digest(value, label) { if (typeof value !== "string" || !SHA_RE.test(value)) fail(`${label} is invalid`); return value; }
function profile(value) { if (!["assistant", "builder", "controller", "researcher"].includes(value)) fail("Pixel comparison profile is not implemented"); return value; }
function qualificationProfile(value) {
  if (!["assistant", "scout", "builder", "controller", "data-lab", "researcher"].includes(value)) fail("Pixel qualification profile is invalid");
  // Controller is a durable orchestration profile around a real Builder child,
  // not a separately substitutable product model. The child must therefore
  // carry the exact current Builder qualification receipt for DSV4 Flash 0731.
  return value === "controller" ? "builder" : value;
}
function lifecyclePolicyProfile(value) {
  profile(value);
  // The portal Assistant does not execute a Work profile. Scout is used only as the
  // already-defined local-model resource envelope while the separate portal control
  // path owns tools, approvals, evidence, and execution. Assistant qualification and
  // product evidence remain profile-exact and may never be substituted with Scout.
  return value === "assistant" ? "scout" : value === "controller" ? "builder" : value;
}

function requireProfileLifecycleEnvelope(policy, environment, backend, selectedProfile, { planned = false } = {}) {
  const policyProfile = lifecyclePolicyProfile(selectedProfile);
  const selectedPolicy = policy.profiles[policyProfile];
  if (!policy.runner.prepared || !selectedPolicy?.enabled) {
    fail(`Pixel ${planned ? "planned comparison envelope is disabled or runner-unprepared" : "comparison templates are not prepared"} for the selected profile`);
  }
  if (!planned && (!policy.enabled || !policy.localModel.prepared)) fail("Pixel comparison templates are not prepared for the selected profile");
  if (selectedProfile === "assistant" && backend.publishLoopbackPort === null) fail("Pixel Assistant requires an exact loopback-only model endpoint");
  if (["builder", "controller"].includes(selectedProfile) && !policy.verifier.enabled) fail(`Pixel ${selectedProfile === "controller" ? "Controller" : "Builder"} comparison verifier is disabled`);
  if (selectedProfile === "researcher" && (!selectedPolicy.backend?.prepared || !environment.researchRuntime)) fail(`Pixel Researcher ${planned ? "planned comparison" : "comparison"} runtime is disabled or incomplete`);
  return selectedPolicy;
}
function encoded(value, label, ceiling = MAX_JSON_BYTES) {
  if (typeof value !== "string" || value.length === 0 || value.length > Math.ceil(ceiling * 4 / 3) + 8 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(value)) fail(`${label} is not bounded base64`);
  const result = Buffer.from(value, "base64");
  if (!result.length || result.length > ceiling || result.toString("base64") !== value) fail(`${label} is not canonical base64`);
  return result;
}

async function ownerPrivate(path, label, directory = false) {
  absolutePath(path, label);
  let info, actual;
  try { [info, actual] = await Promise.all([lstat(path), realpath(path)]); } catch { fail(`${label} is unavailable`); }
  if (!samePath(actual, path) || info.isSymbolicLink() || (directory ? !info.isDirectory() : !info.isFile()) || !directory && info.nlink !== 1) fail(`${label} is not real, singular, and of the expected type`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return info;
}

async function readPrivateJson(path, label) {
  await ownerPrivate(path, label);
  let record;
  try { record = await readBoundedRegularFile(path, MAX_JSON_BYTES, label); } catch { fail(`${label} could not be read safely`); }
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  try { return parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
}

async function writePrivateNew(path, payload) {
  absolutePath(path, "private output path");
  await ownerPrivate(dirname(path), "private output parent", true);
  let handle;
  try { handle = await open(path, "wx", 0o600); await handle.writeFile(payload); await handle.sync(); }
  catch (error) { if (error?.code === "EEXIST") fail("private output already exists"); throw error; }
  finally { await handle?.close(); }
  if (process.platform !== "win32") await chmod(path, 0o600);
}
async function writePrivateJson(path, value) { return writePrivateNew(path, `${JSON.stringify(value, null, 2)}\n`); }

function validatePixelSystemConfig(value) {
  exactKeys(value, ["$schema", "schemaVersion", "runtimeRoot", "policyTemplatePath", "environmentTemplatePath", "modelBackendTemplatePath", "assistantTemplatePath", "boundary"], "Pixel system configuration");
  if (value.$schema !== "https://osmantic.com/pixel/schemas/portal-outcome-pixel-system-v1.schema.json" || value.schemaVersion !== 1 || value.boundary !== CONFIG_BOUNDARY) fail("Pixel system configuration contract is invalid");
  for (const field of ["runtimeRoot", "policyTemplatePath", "environmentTemplatePath", "modelBackendTemplatePath", "assistantTemplatePath"]) absolutePath(value[field], `Pixel system ${field}`);
  return value;
}

async function requirePrivateNewDestination(path, label) {
  absolutePath(path, label);
  await ownerPrivate(dirname(path), `${label} parent`, true);
  const existing = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing !== null) fail(`${label} must be a new private file`);
}

export async function loadPixelSystemConfig(path) {
  const value = validatePixelSystemConfig(await readPrivateJson(absolutePath(path, "Pixel system configuration path"), "private Pixel system configuration"));
  await Promise.all([
    ownerPrivate(value.runtimeRoot, "Pixel system runtime root", true), ownerPrivate(value.policyTemplatePath, "Pixel work-policy template"),
    ownerPrivate(value.environmentTemplatePath, "Pixel environment template"), ownerPrivate(value.modelBackendTemplatePath, "Pixel model-backend template"),
    ownerPrivate(value.assistantTemplatePath, "Pixel Assistant runtime template"),
  ]);
  return value;
}

function layout(configuration, id) {
  runId(id);
  const root = resolve(configuration.runtimeRoot, id);
  if (dirname(root) !== configuration.runtimeRoot) fail("Pixel run root escapes its configured parent");
  const configRoot = join(root, "config");
  return Object.freeze({
    root, configRoot, policyPath: join(configRoot, "policy.json"), environmentPath: join(configRoot, "environment.json"),
    backendPath: join(configRoot, "model-backend.json"), statePath: join(configRoot, "system-state.json"),
    workState: join(root, "work-state"), objectStore: join(root, "objects"), workspaceRoot: join(root, "workspaces"),
    artifactsRoot: join(root, "artifacts"),
  });
}

async function createPrivateDirectories(paths) {
  for (const path of paths) {
    await mkdir(path, { mode: 0o700 });
    if (process.platform !== "win32") await chmod(path, 0o700);
    await ownerPrivate(path, "new Pixel private directory", true);
  }
}

export async function harnessContractSha256() {
  const files = [];
  for (const relativePath of PIXEL_HARNESS_FILES) {
    const payload = await readFile(resolve(ROOT, ...relativePath.split("/")));
    files.push({ relativePath, bytes: payload.length, sha256: hash(payload) });
  }
  return hash({ schemaVersion: 1, operation: "pixel-portal-outcome-harness-contract", files });
}

function decodeContract(input, prefix) {
  const payload = encoded(input[`${prefix}ContractBase64`], `Pixel ${prefix} contract`);
  const expected = digest(input[`${prefix}ContractSha256`], `Pixel ${prefix} contract digest`);
  if (hash(payload) !== expected) fail(`Pixel ${prefix} contract bytes differ from their admitted digest`);
  let value;
  try { value = parseStrictJson(payload.toString("utf8"), `Pixel ${prefix} contract`); } catch { fail(`Pixel ${prefix} contract is not strict JSON`); }
  return { value, sha256: expected };
}

function launchArguments(prepared) {
  try {
    return modelBackendServerArgumentsFromLaunch({ launch: prepared?.launch, policy: prepared?.policy });
  } catch {
    fail("Pixel backend launch does not contain its exact image, wrapper, and server arguments");
  }
}

function containerOption(prepared, name) {
  const args = prepared?.launch?.container?.args;
  const index = Array.isArray(args) ? args.indexOf(name) : -1;
  if (index < 0 || index + 1 >= args.length || args.indexOf(name, index + 1) !== -1) fail(`Pixel backend launch does not contain exactly one ${name} option`);
  return args[index + 1];
}

export function verifyPreparedComparison(prepared, model, inference) {
  const local = prepared?.policy?.localModel;
  const artifact = prepared?.artifactManifest;
  const runtime = model?.runtime;
  const resources = runtime?.resources;
  const backend = prepared?.configuration;
  if (
    model?.modelId !== "DeepSeek-V4-Flash-0731" || local?.id !== model.modelId || local.provider !== "vllm"
    || runtime?.implementation !== "vllm" || runtime.imageDigest !== local.imageDigest
    || runtime.runtimeIsolation !== "fresh-per-run" || runtime.crossRunStateAllowed !== false
    || runtime.restartPolicy !== "no" || backend?.restartPolicy !== "no" || containerOption(prepared, "--restart") !== "no"
    || model.artifact?.kind !== "directory-manifest" || model.artifact.sha256 !== local.modelArtifactSha256
    || artifact?.kind !== "directory" || artifact.artifactSha256 !== model.artifact.sha256
    || artifact.fileCount !== model.artifact.fileCount || artifact.totalBytes !== model.artifact.bytes
    || runtime.contextWindow !== local.contextWindow || inference?.request?.maxOutputTokens !== local.maxRequestOutputTokens
    || inference?.request?.stream !== true || inference?.request?.toolEncoding !== "function"
    || inference?.request?.promptCachePolicy !== "empty-at-run-start"
    || inference?.request?.requestFieldPolicySha256 !== hash(outcomeInferenceBoundaryPolicy(inference))
    || canonical(outcomeInferencePolicy(inference)) !== canonical(local.inference)
    || backend?.providerOptions?.provider !== "vllm"
    || backend.providerOptions.imageEntrypoint !== "/usr/local/bin/pixel-dsv4-serve"
    || backend.providerOptions.tensorParallelSize !== 2
    || backend.providerOptions.maxSequences !== runtime.parallelSlots || runtime.parallelSlots !== 16
    || backend.providerOptions.gpuMemoryUtilizationPermille !== 984
    || backend.providerOptions.dtype !== "auto" || backend.providerOptions.enforceEager !== false
    || backend.providerOptions.reasoningParser !== "deepseek_v4"
    || backend.providerOptions.toolCallParser !== "deepseek_v4"
    || backend.accelerator.class !== "nvidia-cuda" || resources.acceleratorClass !== "nvidia"
    || backend.accelerator.count !== resources.acceleratorCount || backend.resources.cpuCores !== resources.cpuCores
    || backend.resources.memoryMiB !== resources.memoryMiB || backend.resources.sharedMemoryMiB !== resources.sharedMemoryMiB
    || backend.resources.tmpfsMiB !== resources.tmpfsMiB || backend.resources.cacheMiB !== resources.cacheMiB
    || backend.resources.pids !== resources.pidsLimit
    || hash(launchArguments(prepared)) !== runtime.launchArgumentsSha256
  ) fail("Pixel prepared runtime differs from the exact shared DSV4 contract");
  return true;
}

export function buildPixelRuntimeReceipt({ id, profile: selectedProfile = "builder", prepared, model, inference, modelSha256, inferenceSha256, environmentSha256, harnessSha256, qualificationReceiptSha256 }) {
  verifyPreparedComparison(prepared, model, inference);
  const policySha256 = hash(prepared.policy);
  const stableEnvironmentSha256 = digest(environmentSha256, "Pixel environment template digest");
  const runtimeEnvironmentSha256 = digest(prepared?.launch?.bindings?.environmentSha256, "Pixel runtime environment digest");
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-portal-outcome-runtime", runId: runId(id), profile: profile(selectedProfile), modelId: model.modelId,
    modelContractSha256: digest(modelSha256, "model contract digest"), inferenceContractSha256: digest(inferenceSha256, "inference contract digest"),
    workPolicySha256: policySha256, environmentSha256: stableEnvironmentSha256, runtimeEnvironmentSha256,
    harnessContractSha256: digest(harnessSha256, "harness contract digest"),
    qualificationReceiptSha256: digest(qualificationReceiptSha256, "model qualification receipt digest"),
    runnerImageDigest: prepared.policy.runner.imageDigest, verifierImageDigest: prepared.policy.runner.imageDigest,
    backendImageDigest: prepared.policy.localModel.imageDigest,
    modelArtifactSha256: prepared.artifactManifest.artifactSha256,
    launchBundleSha256: prepared.launchBundleSha256, backendFresh: true, runnerFresh: true,
    realBackend: true, realTools: true, crossRunStateObserved: false, qwenProductModel: false,
    boundary: PIXEL_RUNTIME_BOUNDARY,
  });
}

export async function materializeRun(configuration, id, selectedProfile) {
  profile(selectedProfile);
  const selected = layout(configuration, id);
  await mkdir(selected.root, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(selected.root, 0o700);
  await createPrivateDirectories([selected.configRoot, selected.workState, selected.objectStore, selected.workspaceRoot, selected.artifactsRoot]);
  const [policy, environment, backend] = await Promise.all([
    readPrivateJson(configuration.policyTemplatePath, "private Pixel work-policy template"),
    readPrivateJson(configuration.environmentTemplatePath, "private Pixel environment template"),
    readPrivateJson(configuration.modelBackendTemplatePath, "private Pixel model-backend template"),
  ]);
  const environmentTemplateSha256 = hash(environment);
  environment.stateRoot = selected.workState; environment.policyPath = selected.policyPath;
  environment.objectStore = selected.objectStore; environment.workspaceRoot = selected.workspaceRoot;
  environment.modelBackendLaunchPath = join(selected.configRoot, "model-backend-launch.json");
  const suffix = id.slice(-12);
  environment.runtime.backendNetworkName = `pixel-outcome-backend-${suffix}`;
  environment.runtime.backendContainerName = `pixel-outcome-model-${suffix}`;
  backend.environmentPath = selected.environmentPath;
  const policyErrors = validateWorkPolicy(policy), environmentErrors = validateWorkGoalControllerEnvironment(environment), backendErrors = validateWorkModelBackendConfig(backend);
  if (policyErrors.length || environmentErrors.length || backendErrors.length) fail(`Pixel private template materialization is invalid: ${policyErrors[0] ?? environmentErrors[0] ?? backendErrors[0]}`);
  requireProfileLifecycleEnvelope(policy, environment, backend, selectedProfile);
  await writePrivateJson(selected.policyPath, policy); await writePrivateJson(selected.environmentPath, environment); await writePrivateJson(selected.backendPath, backend);
  return { selected, policy, environment, backend, environmentTemplateSha256 };
}

async function loadPlannedTemplates(configuration, selectedProfile) {
  profile(selectedProfile);
  const [policy, environment, backend] = await Promise.all([
    readPrivateJson(configuration.policyTemplatePath, "private Pixel work-policy template"),
    readPrivateJson(configuration.environmentTemplatePath, "private Pixel environment template"),
    readPrivateJson(configuration.modelBackendTemplatePath, "private Pixel model-backend template"),
  ]);
  const policyErrors = validateWorkPolicy(policy), environmentErrors = validateWorkGoalControllerEnvironment(environment), backendErrors = validateWorkModelBackendConfig(backend);
  if (policyErrors.length || environmentErrors.length || backendErrors.length) fail(`Pixel private planned templates are invalid: ${policyErrors[0] ?? environmentErrors[0] ?? backendErrors[0]}`);
  requireProfileLifecycleEnvelope(policy, environment, backend, selectedProfile, { planned: true });
  return { policy, environment, backend };
}

export async function prepare(path, dependencies = {}) {
  const expectedOwnerUid = process.geteuid?.() ?? 0;
  return (dependencies.prepareModelBackendLaunch ?? prepareModelBackendLaunch)(path, expectedOwnerUid, dependencies.prepareDependencies ?? {});
}

async function currentQualification(policy, selectedProfile, now = new Date()) {
  const qualifiedProfile = qualificationProfile(selectedProfile);
  const binding = policy?.localModel?.qualification;
  if (!binding || typeof binding !== "object" || Array.isArray(binding)) fail("Pixel DSV4 model qualification binding is unavailable");
  const receipt = await readPrivateJson(absolutePath(binding.receiptPath, "Pixel model qualification receipt path"), "private Pixel model qualification receipt");
  const model = {
    provider: policy.localModel.provider,
    id: policy.localModel.id,
    modelArtifactSha256: policy.localModel.modelArtifactSha256,
    backendImageDigest: policy.localModel.imageDigest,
    backendVersion: policy.localModel.backendVersion,
    acceleratorClass: policy.localModel.acceleratorClass,
    promptContractSha256: policy.localModel.promptContractSha256,
    toolSchemaSha256: policy.localModel.toolSchemaSha256,
    contextWindow: policy.localModel.contextWindow,
    supportsVision: policy.localModel.supportsVision,
  };
  try {
    return requireQualifiedModel(receipt, {
      expectedReceiptSha256: binding.receiptSha256,
      expectedCasesSha256: binding.casesSha256,
      expectedEvaluatorSha256: binding.evaluatorSha256,
      model, profile: qualifiedProfile,
      requiredContextTokens: policy.localModel.maxRequestContextTokens,
      requiredOutputTokens: policy.localModel.maxRequestOutputTokens,
      now,
    });
  } catch {
    fail(`Pixel DSV4 model qualification is stale, mismatched, or insufficient for the ${qualifiedProfile} profile required by ${selectedProfile}`);
  }
}

async function requireCurrentQualification(policy, selectedProfile, now = new Date()) {
  return (await currentQualification(policy, selectedProfile, now)).receiptSha256;
}

function validatePolicyBindingTemplates(policy, environment, backend) {
  const policyErrors = validateWorkPolicy(policy), environmentErrors = validateWorkGoalControllerEnvironment(environment), backendErrors = validateWorkModelBackendConfig(backend);
  if (policyErrors.length || environmentErrors.length || backendErrors.length) fail(`Pixel policy binding templates are invalid: ${policyErrors[0] ?? environmentErrors[0] ?? backendErrors[0]}`);
}

async function pixelSystemPolicyBindingProposal(configurationPath, policyPath, candidatePath, dependencies = {}) {
  const currentPath = absolutePath(resolve(configurationPath), "Pixel current system configuration path");
  const enabledPath = absolutePath(resolve(policyPath), "Pixel enabled policy path");
  const destination = absolutePath(resolve(candidatePath), "Pixel policy-bound system configuration destination");
  const now = dependencies.now ?? new Date();
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0) fail("Pixel policy binding clock is invalid");
  await requirePrivateNewDestination(destination, "Pixel policy-bound system configuration destination");
  const configuration = await loadPixelSystemConfig(currentPath);
  if (samePath(configuration.policyTemplatePath, enabledPath)) fail("Pixel system configuration already selects the reviewed enabled policy");
  const [currentPolicy, policy, environment, backend] = await Promise.all([
    readPrivateJson(configuration.policyTemplatePath, "private current Pixel work-policy template"),
    readPrivateJson(enabledPath, "private enabled Pixel work-policy template"),
    readPrivateJson(configuration.environmentTemplatePath, "private Pixel environment template"),
    readPrivateJson(configuration.modelBackendTemplatePath, "private Pixel model-backend template"),
  ]);
  validatePolicyBindingTemplates(currentPolicy, environment, backend);
  validatePolicyBindingTemplates(policy, environment, backend);
  if (!policy.enabled || !policy.runner.prepared || !policy.localModel.prepared) fail("Pixel policy binding requires one enabled and fully prepared policy");
  const enabled = { assistant: true, scout: policy.profiles.scout.enabled, builder: policy.profiles.builder.enabled, "data-lab": policy.profiles.dataLab.enabled, researcher: policy.profiles.researcher.enabled };
  const enabledProfiles = ["assistant", "scout", "builder", "data-lab", "researcher"].filter((selected) => enabled[selected]);
  const comparisonProfiles = ["assistant", "builder", "controller", "researcher"].filter((selected) => selected === "controller" ? enabled.builder : enabled[selected]);
  if (comparisonProfiles.length < 1) fail("Pixel policy binding has no enabled comparison profile");
  if (comparisonProfiles.some((selected) => ["builder", "controller"].includes(selected)) && !policy.verifier.enabled) fail("Pixel Builder and Controller policy binding verifier is disabled");
  if (comparisonProfiles.includes("researcher") && (!policy.profiles.researcher.backend?.prepared || !environment.researchRuntime)) fail("Pixel Researcher policy binding runtime is disabled or incomplete");
  const qualificationReceiptSha256 = await requireCurrentQualification(policy, enabledProfiles[0], now);
  for (const selected of enabledProfiles.slice(1)) {
    if (await requireCurrentQualification(policy, selected, now) !== qualificationReceiptSha256) fail("Pixel policy binding profiles differ in qualification evidence");
  }
  const proposedConfiguration = validatePixelSystemConfig({ ...structuredClone(configuration), policyTemplatePath: enabledPath });
  const currentConfigurationSha256 = hash(configuration), currentPolicySha256 = hash(currentPolicy);
  const enabledPolicySha256 = hash(policy), proposedConfigurationSha256 = hash(proposedConfiguration);
  const operationSha256 = hash({
    schemaVersion: 1, operation: "pixel-portal-outcome-system-policy-binding",
    configurationPath: currentPath, currentConfigurationSha256,
    currentPolicyPath: configuration.policyTemplatePath, currentPolicySha256,
    enabledPolicyPath: enabledPath, enabledPolicySha256,
    destination, proposedConfigurationSha256,
  });
  const review = Object.freeze({
    schemaVersion: 1, operation: "pixel-portal-outcome-system-policy-binding-review", status: "confirmation-required",
    operationSha256, currentConfigurationSha256, currentPolicySha256, enabledPolicySha256,
    proposedConfigurationSha256, qualificationReceiptSha256, enabledProfiles, comparisonProfiles,
    destinationName: basename(destination),
    confirmation: { option: "--confirm-operation-sha256", sha256: operationSha256 },
    changes: {
      writesNewPrivateConfiguration: true, changesOnlyPolicyTemplatePath: true,
      editsCurrentConfiguration: false, mutatesPolicy: false, startsModel: false,
      startsService: false, routesJob: false, usesNetwork: false, usesCredentials: false,
      externalEffects: false,
    },
    authority: {
      grantsExecution: false, grantsModelStart: false, grantsServiceStart: false,
      grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false,
      grantsCompletion: false, grantsPublication: false, grantsDeployment: false,
      grantsAcceptance: false, grantsPromotion: false,
    },
    boundary: PIXEL_POLICY_BINDING_BOUNDARY,
  });
  return Object.freeze({ proposedConfiguration: Object.freeze(proposedConfiguration), review });
}

export async function reviewPixelSystemPolicyBinding(configurationPath, policyPath, candidatePath, dependencies = {}) {
  return (await pixelSystemPolicyBindingProposal(configurationPath, policyPath, candidatePath, dependencies)).review;
}

export async function applyPixelSystemPolicyBinding(configurationPath, policyPath, candidatePath, confirmation, dependencies = {}) {
  if (typeof confirmation !== "string" || !SHA_RE.test(confirmation)) fail("Pixel policy binding confirmation is invalid");
  const proposed = await pixelSystemPolicyBindingProposal(configurationPath, policyPath, candidatePath, dependencies);
  if (proposed.review.operationSha256 !== confirmation) fail("Pixel policy binding confirmation differs from the exact source, policy, and destination operation");
  await writePrivateJson(resolve(candidatePath), proposed.proposedConfiguration);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-portal-outcome-system-policy-binding-apply", status: "policy-bound-system-configuration-written",
    operationSha256: proposed.review.operationSha256,
    proposedConfigurationSha256: proposed.review.proposedConfigurationSha256,
    enabledPolicySha256: proposed.review.enabledPolicySha256,
    qualificationReceiptSha256: proposed.review.qualificationReceiptSha256,
    enabledProfiles: [...proposed.review.enabledProfiles], comparisonProfiles: [...proposed.review.comparisonProfiles], destinationName: proposed.review.destinationName,
    changes: { ...proposed.review.changes }, authority: { ...proposed.review.authority },
    boundary: PIXEL_POLICY_BINDING_BOUNDARY,
  });
}

function lifecycleOptions(prepared, dependencies, intent, configPath) {
  const expectedOwnerUid = process.geteuid?.() ?? 0;
  return {
    confirmation: modelBackendLifecycleSha256(intent, prepared.launchBundleSha256),
    postflight: () => prepare(configPath, dependencies),
    coordinationOptions: { expectedOwnerUid },
    ...(dependencies.lifecycleOptions ?? {}),
  };
}

export async function startPixelSystem(configurationPath, inputPath, dependencies = {}) {
  const configuration = await loadPixelSystemConfig(configurationPath);
  const input = exactKeys(await readPrivateJson(inputPath, "private Pixel system start input"), ["schemaVersion", "operation", "runId", "profile", "modelContractBase64", "modelContractSha256", "inferenceContractBase64", "inferenceContractSha256"], "Pixel system start input");
  if (input.schemaVersion !== 1 || input.operation !== "start") fail("Pixel system start operation is invalid");
  const id = runId(input.runId), selectedProfile = profile(input.profile), model = decodeContract(input, "model"), inference = decodeContract(input, "inference");
  const { selected, environmentTemplateSha256 } = await materializeRun(configuration, id, selectedProfile);
  let prepared, started = false;
  try {
    prepared = await prepare(selected.backendPath, dependencies);
    verifyPreparedComparison(prepared, model.value, inference.value);
    const qualificationReceiptSha256 = await requireCurrentQualification(prepared.policy, selectedProfile, dependencies.now ?? new Date());
    const start = dependencies.startModelBackend ?? startModelBackend;
    const receipt = await start(prepared, lifecycleOptions(prepared, dependencies, "start", selected.backendPath));
    if (!receipt || receipt.state !== "ready-started" || receipt.checks?.readinessPassed !== true) fail("Pixel DSV4 backend was not freshly started for this run");
    started = true;
    const harnessSha256 = digest(
      await (dependencies.harnessContractSha256 ?? harnessContractSha256)(),
      "Pixel review harness contract digest",
    );
    const runtime = buildPixelRuntimeReceipt({ id, profile: selectedProfile, prepared, model: model.value, inference: inference.value, modelSha256: model.sha256, inferenceSha256: inference.sha256, environmentSha256: environmentTemplateSha256, harnessSha256, qualificationReceiptSha256 });
    const state = { schemaVersion: 1, operation: "pixel-portal-outcome-system-state", runId: id, launchBundleSha256: prepared.launchBundleSha256, runtime, boundary: STATE_BOUNDARY };
    await writePrivateJson(selected.statePath, state);
    return runtime;
  } catch (error) {
    if (started && prepared) {
      try { const stop = dependencies.stopModelBackend ?? stopModelBackend; await stop(prepared, lifecycleOptions(prepared, dependencies, "stop", selected.backendPath)); } catch { /* preserve the primary failure */ }
    }
    try {
      const actual = await realpath(selected.root);
      if (samePath(actual, selected.root) && samePath(dirname(actual), configuration.runtimeRoot)) await rm(selected.root, { recursive: true, force: false });
    } catch { /* preserve the primary failure */ }
    throw error;
  }
}

export async function reviewPixelSystem(configurationPath, inputPath, dependencies = {}) {
  const configuration = await loadPixelSystemConfig(configurationPath);
  const input = exactKeys(await readPrivateJson(inputPath, "private Pixel system review input"), [
    "schemaVersion", "operation", "runId", "profile", "now", "idSuffix", "admission", "task",
    "requestPayloadBase64", "sourceReference", "environment", "toolPolicy", "verifierDefinition",
    "modelContractBase64", "modelContractSha256", "inferenceContractBase64", "inferenceContractSha256",
  ], "Pixel system review input");
  if (input.schemaVersion !== 1 || input.operation !== "review") fail("Pixel system review operation is invalid");
  const id = runId(input.runId), selectedProfile = profile(input.profile), model = decodeContract(input, "model"), inference = decodeContract(input, "inference");
  const now = new Date(input.now);
  if (!Number.isSafeInteger(now.getTime()) || input.idSuffix !== id.slice(-12)) fail("Pixel system review compilation identity is invalid");
  const requestPayload = encoded(input.requestPayloadBase64, "Pixel review request payload", 65536);
  const { selected, environmentTemplateSha256 } = await materializeRun(configuration, id, selectedProfile);
  let result;
  try {
    const prepared = await prepare(selected.backendPath, dependencies);
    verifyPreparedComparison(prepared, model.value, inference.value);
    const qualificationReceiptSha256 = await requireCurrentQualification(prepared.policy, selectedProfile, dependencies.now ?? new Date());
    const reviewCompatibility = dependencies.reviewPixelTaskCompatibility ?? reviewPixelProductCompatibility;
    const compatibility = exactKeys(reviewCompatibility({
      admission: input.admission, task: input.task, requestPayload,
      sourceReference: input.sourceReference, environment: input.environment,
      toolPolicy: input.toolPolicy, verifierDefinition: input.verifierDefinition,
      modelContract: model.value, inferenceContract: inference.value, workPolicy: prepared.policy,
      now, idSuffix: input.idSuffix,
    }), [
      "schemaVersion", "operation", "profile", "taskAdmissionSha256", "workRequestSha256",
      "workPlanSha256", "budgetsSha256", "capabilitiesSha256", "boundary",
    ], "Pixel task compatibility review");
    for (const field of ["taskAdmissionSha256", "workRequestSha256", "workPlanSha256", "budgetsSha256", "capabilitiesSha256"]) digest(compatibility[field], `Pixel task compatibility ${field}`);
    if (
      compatibility.schemaVersion !== 1 || compatibility.operation !== "pixel-portal-outcome-task-compatibility"
      || compatibility.profile !== selectedProfile || compatibility.taskAdmissionSha256 !== hash(input.admission)
      || compatibility.boundary !== pixelArmContract.taskCompatibilityBoundary
    ) fail("Pixel task compatibility review differs from the exact admitted task");
    const harnessSha256 = await (dependencies.harnessContractSha256 ?? harnessContractSha256)();
    result = Object.freeze({
      schemaVersion: 1, operation: "pixel-portal-outcome-system-review", runId: id, profile: selectedProfile, status: "ready",
      modelContractSha256: model.sha256, inferenceContractSha256: inference.sha256,
      taskCompatibilitySha256: hash(compatibility),
      workPolicySha256: hash(prepared.policy),
      environmentSha256: digest(environmentTemplateSha256, "Pixel environment template digest"),
      runtimeEnvironmentSha256: digest(prepared?.launch?.bindings?.environmentSha256, "Pixel runtime environment digest"),
      harnessContractSha256: harnessSha256,
      qualificationReceiptSha256,
      runnerImageDigest: prepared.policy.runner.imageDigest,
      verifierImageDigest: prepared.policy.runner.imageDigest,
      backendImageDigest: prepared.policy.localModel.imageDigest,
      modelArtifactSha256: prepared.artifactManifest.artifactSha256,
      launchBundleSha256: prepared.launchBundleSha256,
      changes: {
        temporaryPrivateReviewStateRemoved: true, modelStarted: false, containerCreated: false,
        networkCreated: false, taskExecuted: false, externalEffects: false,
      },
      authority: {
        grantsModelStart: false, grantsExecution: false, grantsNetwork: false,
        grantsProviderCall: false, grantsCredentialUse: false, grantsExternalEffects: false,
        grantsCompletion: false,
      },
      boundary: PIXEL_REVIEW_BOUNDARY,
    });
  } finally {
    await removePixelRunRoot(configuration, selected);
  }
  return result;
}

export async function reviewPixelPlannedSystem(configurationPath, inputPath, dependencies = {}) {
  const configuration = await loadPixelSystemConfig(configurationPath);
  const input = exactKeys(await readPrivateJson(inputPath, "private Pixel planned review input"), [
    "schemaVersion", "operation", "runId", "profile", "now", "idSuffix", "admission", "task",
    "requestPayloadBase64", "sourceReference", "environment", "toolPolicy", "verifierDefinition",
    "modelContractBase64", "modelContractSha256", "inferenceContractBase64", "inferenceContractSha256",
  ], "Pixel planned review input");
  if (input.schemaVersion !== 1 || input.operation !== "planned-review") fail("Pixel planned review operation is invalid");
  const id = runId(input.runId), selectedProfile = profile(input.profile), model = decodeContract(input, "model"), inference = decodeContract(input, "inference");
  const now = new Date(input.now);
  if (!Number.isSafeInteger(now.getTime()) || input.idSuffix !== id.slice(-12)) fail("Pixel planned review compilation identity is invalid");
  const requestPayload = encoded(input.requestPayloadBase64, "Pixel planned review request payload", 65536);
  const templates = await loadPlannedTemplates(configuration, selectedProfile);
  const reviewCompatibility = dependencies.reviewPixelPlannedTaskCompatibility ?? reviewPixelPlannedCompatibility;
  const compatibility = exactKeys(reviewCompatibility({
    admission: input.admission, task: input.task, requestPayload,
    sourceReference: input.sourceReference, environment: input.environment,
    toolPolicy: input.toolPolicy, verifierDefinition: input.verifierDefinition,
    modelContract: model.value, inferenceContract: inference.value, workPolicy: templates.policy,
    now, idSuffix: input.idSuffix,
  }), [
    "schemaVersion", "operation", "profile", "readiness", "taskAdmissionSha256", "workRequestSha256",
    "workPolicySha256", "inputSetSha256", "budgetsSha256", "capabilitiesSha256", "outputsSha256", "boundary",
  ], "Pixel planned task compatibility review");
  for (const field of ["taskAdmissionSha256", "workRequestSha256", "workPolicySha256", "inputSetSha256", "budgetsSha256", "capabilitiesSha256", "outputsSha256"]) digest(compatibility[field], `Pixel planned task compatibility ${field}`);
  if (
    compatibility.schemaVersion !== 1 || compatibility.operation !== "pixel-portal-outcome-planned-task-compatibility"
    || compatibility.profile !== selectedProfile || compatibility.taskAdmissionSha256 !== hash(input.admission)
    || compatibility.workPolicySha256 !== hash(templates.policy)
    || !["qualification-required", "policy-ready"].includes(compatibility.readiness)
    || compatibility.boundary !== pixelArmContract.plannedTaskCompatibilityBoundary
  ) fail("Pixel planned task compatibility review differs from the exact admitted task or policy");
  const harnessSha256 = digest(
    await (dependencies.harnessContractSha256 ?? harnessContractSha256)(),
    "Pixel planned review harness contract digest",
  );
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-portal-outcome-planned-system-review", runId: id,
    profile: selectedProfile, status: "structurally-compatible", readiness: compatibility.readiness,
    modelContractSha256: model.sha256, inferenceContractSha256: inference.sha256,
    taskCompatibilitySha256: hash(compatibility), harnessContractSha256: harnessSha256,
    workPolicySha256: hash(templates.policy),
    environmentSha256: hash(templates.environment), backendConfigSha256: hash(templates.backend),
    runnerImageDigest: templates.policy.runner.imageDigest, backendImageDigest: templates.policy.localModel.imageDigest,
    changes: {
      policyMutated: false, qualificationFabricated: false, modelStarted: false, containerCreated: false,
      networkCreated: false, taskExecuted: false, externalEffects: false,
    },
    authority: {
      grantsModelStart: false, grantsExecution: false, grantsNetwork: false,
      grantsProviderCall: false, grantsCredentialUse: false, grantsExternalEffects: false,
      grantsCompletion: false, grantsPublication: false, grantsDeployment: false,
      grantsAcceptance: false, grantsPromotion: false,
    },
    boundary: PIXEL_PLANNED_REVIEW_BOUNDARY,
  });
}

async function readRunState(configuration, id) {
  const selected = layout(configuration, id);
  await ownerPrivate(selected.root, "Pixel run root", true);
  const state = exactKeys(await readPrivateJson(selected.statePath, "private Pixel system state"), ["schemaVersion", "operation", "runId", "launchBundleSha256", "runtime", "boundary"], "Pixel system state");
  if (state.schemaVersion !== 1 || state.operation !== "pixel-portal-outcome-system-state" || state.runId !== id || state.boundary !== STATE_BOUNDARY || !SHA_RE.test(state.launchBundleSha256 ?? "")) fail("Pixel system state is invalid");
  return { selected, state };
}

export async function removePixelRunRoot(configuration, selected) {
  const actual = await realpath(selected.root);
  if (!samePath(actual, selected.root) || !samePath(dirname(actual), configuration.runtimeRoot)) fail("refusing to remove an unrecognized Pixel run root");
  await rm(selected.root, { recursive: true, force: false });
}

export async function stageSource(sourcePath, destination, expectedBytes, expectedSha256) {
  absolutePath(sourcePath, "Pixel source archive path"); digest(expectedSha256, "Pixel source archive digest");
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes < 1 || expectedBytes > 1073741824) fail("Pixel source archive byte count is invalid");
  await ownerPrivate(sourcePath, "private Pixel source archive");
  const source = await open(sourcePath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  let target;
  try {
    const before = await source.stat();
    if (!before.isFile() || before.nlink !== 1 || before.size !== expectedBytes) fail("Pixel source archive size or identity differs from admission");
    target = await open(destination, "wx", 0o600);
    const digestState = createHash("sha256"), buffer = Buffer.allocUnsafe(1024 * 1024); let position = 0;
    while (position < expectedBytes) {
      const { bytesRead } = await source.read(buffer, 0, Math.min(buffer.length, expectedBytes - position), position);
      if (bytesRead < 1) fail("Pixel source archive ended before its admitted byte count");
      const chunk = buffer.subarray(0, bytesRead); digestState.update(chunk); await target.write(chunk); position += bytesRead;
    }
    const after = await source.stat();
    if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size || after.mtimeMs !== before.mtimeMs || digestState.digest("hex") !== expectedSha256) fail("Pixel source archive changed or differs from admission");
    await target.sync();
  } catch (error) { await target?.close(); await rm(destination, { force: true }).catch(() => {}); throw error; }
  finally { await source.close(); }
  await target.close(); if (process.platform !== "win32") await chmod(destination, 0o600);
}

async function persistArtifacts(root, artifacts) {
  if (!Array.isArray(artifacts) || artifacts.length > 256) fail("Pixel system artifact inventory is invalid");
  const output = [];
  for (let index = 0; index < artifacts.length; index += 1) {
    const artifact = artifacts[index];
    if (!artifact || typeof artifact.kind !== "string" || typeof artifact.relativePath !== "string" || !RELATIVE_RE.test(artifact.relativePath) || !Buffer.isBuffer(artifact.payload) || !artifact.payload.length) fail("Pixel system artifact is invalid");
    const name = `artifact-${String(index).padStart(3, "0")}-${basename(artifact.relativePath)}`;
    const path = join(root, name); await writePrivateNew(path, artifact.payload);
    output.push({ kind: artifact.kind, relativePath: artifact.relativePath, payloadPath: path, bytes: artifact.payload.length, sha256: hash(artifact.payload) });
  }
  return output;
}

export async function runPixelSystem(configurationPath, inputPath, dependencies = {}) {
  const configuration = await loadPixelSystemConfig(configurationPath);
  const input = exactKeys(await readPrivateJson(inputPath, "private Pixel system run input"), ["schemaVersion", "operation", "runId", "now", "idSuffix", "admission", "task", "requestPayloadBase64", "sourcePath", "sourceReference", "researchFixturePath", "researchFixtureReference", "environment", "toolPolicy", "verifierDefinition", "modelContractBase64", "modelContractSha256", "inferenceContractBase64", "inferenceContractSha256"], "Pixel system run input");
  if (input.schemaVersion !== 1 || input.operation !== "run") fail("Pixel system run operation is invalid");
  const id = runId(input.runId), { selected, state } = await readRunState(configuration, id);
  const model = decodeContract(input, "model"), inference = decodeContract(input, "inference");
  if (model.sha256 !== state.runtime.modelContractSha256 || inference.sha256 !== state.runtime.inferenceContractSha256) fail("Pixel run contracts differ from the fresh runtime receipt");
  if (profile(input.admission?.profile) !== state.runtime.profile || input.task?.profile !== state.runtime.profile) fail("Pixel run profile differs from its fresh qualified runtime");
  if (input.idSuffix !== id.slice(-12) || typeof input.now !== "string" || !input.now.endsWith("Z") || !Number.isFinite(Date.parse(input.now))) fail("Pixel system run clock or suffix is invalid");
  const prepared = await prepare(selected.backendPath, dependencies);
  verifyPreparedComparison(prepared, model.value, inference.value);
  if (prepared.launchBundleSha256 !== state.launchBundleSha256 || hash(prepared.policy) !== state.runtime.workPolicySha256) fail("Pixel runtime changed after its fresh start receipt");
  const start = dependencies.startModelBackend ?? startModelBackend;
  const ready = await start(prepared, lifecycleOptions(prepared, dependencies, "start", selected.backendPath));
  if (!ready || ready.state !== "ready-already-running" || ready.checks?.readinessPassed !== true) fail("Pixel backend did not remain continuously ready after its fresh start");
  const source = input.sourceReference;
  if (!source || source.mediaType !== "application/x-tar" || !Number.isSafeInteger(source.bytes)) fail("Pixel source reference is invalid");
  const objectName = `${digest(source.sha256, "Pixel source digest")}.tar`;
  await stageSource(input.sourcePath, join(selected.objectStore, objectName), source.bytes, source.sha256);
  const requestPayload = encoded(input.requestPayloadBase64, "Pixel user request", 65536);
  if (state.runtime.profile === "assistant") {
    if (input.researchFixturePath !== null || input.researchFixtureReference !== null) fail("Assistant Pixel run contains a research fixture");
    const execution = buildPixelAssistantExecution({
      admission: input.admission, task: input.task, requestPayload, sourceReference: source,
      environment: input.environment, toolPolicy: input.toolPolicy, verifierDefinition: input.verifierDefinition,
      modelContract: model.value, inferenceContract: inference.value, workPolicy: prepared.policy,
      now: new Date(input.now), idSuffix: input.idSuffix,
    });
    const backendPort = prepared.configuration.publishLoopbackPort;
    if (!Number.isSafeInteger(backendPort) || backendPort < 1 || backendPort > 65535) fail("Pixel Assistant backend has no exact loopback endpoint");
    const prepareAssistant = dependencies.prepareAssistantRuntime ?? prepareAssistantRuntime;
    return prepareAssistant({
      root: ROOT, runRoot: selected.root, runId: id,
      sourceArchivePath: join(selected.objectStore, objectName), sourceReference: source,
      assistantTemplatePath: configuration.assistantTemplatePath, execution,
      modelContract: model.value, inferenceContract: inference.value,
      qualification: await currentQualification(prepared.policy, "assistant", dependencies.now ?? new Date()),
      backendOrigin: `http://127.0.0.1:${backendPort}/`, nodeBinary: process.execPath,
      rendererPath: resolve(ROOT, "scripts", "render-config.mjs"),
      proxyLauncherPath: resolve(ROOT, "deploy", "agent-comparison", "assistant-model-proxy.mjs"),
      archiveLimits: prepared.environment.archiveLimits,
    }, dependencies.assistantPreparationDependencies ?? {});
  }
  let researchPipelineOptions;
  if (state.runtime.profile === "researcher") {
    researchPipelineOptions = await loadResearchFixturePipelineOptions({
      fixturePath: input.researchFixturePath, fixtureReference: input.researchFixtureReference,
      admission: input.admission, task: input.task,
    });
  } else if (input.researchFixturePath !== null || input.researchFixtureReference !== null) fail("non-Researcher Pixel run contains a research fixture");
  const runArm = dependencies.runPixelProductArm ?? dependencies.runPixelBuilderArm ?? runPixelProductArm;
  const outcome = await runArm({
    admission: input.admission, task: input.task, requestPayload, sourceReference: source,
    environment: input.environment, toolPolicy: input.toolPolicy, verifierDefinition: input.verifierDefinition,
    modelContract: model.value, inferenceContract: inference.value,
    modelContractSha256: model.sha256, inferenceContractSha256: inference.sha256, workPolicy: prepared.policy,
    now: new Date(input.now), idSuffix: input.idSuffix, objectStore: selected.objectStore,
    workspaceRoot: selected.workspaceRoot, executorPath: prepared.environment.executorPath,
    stateRoot: selected.workState, archiveLimits: prepared.environment.archiveLimits,
    lifecycleOptions: {
      ...prepared.environment.runtime,
      ...(state.runtime.profile === "researcher" ? prepared.environment.researchRuntime : {}),
      backendImageIdentifier: prepared.policy.localModel.imageRef,
      modelBackendBindings: structuredClone(prepared.launch.bindings), requireExactModelBackendBindings: true,
      ...(state.runtime.profile === "researcher" ? {
        researchRevisionReview: true,
        researchMaxRevisionRounds: 2,
        modelContractSha256: model.sha256,
        inferenceContractSha256: inference.sha256,
        researchPipelineOptions,
      } : {}),
    },
  }, dependencies.armDependencies ?? {});
  const artifacts = await persistArtifacts(selected.artifactsRoot, outcome.artifacts);
  return { ...outcome, artifacts };
}

export async function stopPixelSystem(configurationPath, inputPath, dependencies = {}) {
  const configuration = await loadPixelSystemConfig(configurationPath);
  const input = exactKeys(await readPrivateJson(inputPath, "private Pixel system stop input"), ["schemaVersion", "operation", "runId"], "Pixel system stop input");
  if (input.schemaVersion !== 1 || input.operation !== "stop") fail("Pixel system stop operation is invalid");
  const id = runId(input.runId), { selected, state } = await readRunState(configuration, id);
  const prepared = await prepare(selected.backendPath, dependencies);
  if (prepared.launchBundleSha256 !== state.launchBundleSha256) fail("Pixel backend changed before exact teardown");
  const stop = dependencies.stopModelBackend ?? stopModelBackend;
  const receipt = await stop(prepared, lifecycleOptions(prepared, dependencies, "stop", selected.backendPath));
  if (!receipt || !["removed", "already-absent"].includes(receipt.state)) fail("Pixel backend teardown is incomplete");
  await removePixelRunRoot(configuration, selected);
  return { schemaVersion: 1, operation: "pixel-portal-outcome-system-stop", runId: id, backendRemoved: true, privateRunStateRemoved: true, externalEffects: false };
}

function parseArguments(argv) {
  if (!Array.isArray(argv)) fail("Pixel system CLI arguments are invalid");
  if (["policy-review", "policy-apply"].includes(argv[0])) {
    const command = argv[0], allowed = command === "policy-review"
      ? new Set(["--config", "--policy", "--candidate", "--output"])
      : new Set(["--config", "--policy", "--candidate", "--confirm-operation-sha256", "--output"]);
    if (argv.length !== 1 + allowed.size * 2) fail("Pixel system policy binding arguments are incomplete");
    const values = {};
    for (let index = 1; index < argv.length; index += 2) {
      const key = argv[index], value = argv[index + 1];
      if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("Pixel system policy binding arguments are invalid, unknown, or duplicated");
      values[key] = key === "--confirm-operation-sha256" ? value : absolutePath(resolve(value), `Pixel system ${key}`);
    }
    for (const key of allowed) if (!values[key]) fail(`Pixel system policy binding command is missing ${key}`);
    if (command === "policy-apply" && !SHA_RE.test(values["--confirm-operation-sha256"])) fail("Pixel system policy binding confirmation is invalid");
    const paths = [values["--config"], values["--policy"], values["--candidate"], values["--output"]];
    if (new Set(paths.map((value) => process.platform === "win32" ? value.toLowerCase() : value)).size !== paths.length) fail("Pixel system policy binding paths must be distinct");
    return {
      command, configurationPath: values["--config"], policyPath: values["--policy"],
      candidatePath: values["--candidate"], confirmation: values["--confirm-operation-sha256"] ?? null,
      outputPath: values["--output"],
    };
  }
  if (!["planned-review", "review", "start", "run", "stop"].includes(argv[0]) || argv.length !== 7) fail("Usage: pixel-system-cli.mjs planned-review|review|start|run|stop --config PRIVATE_JSON --input PRIVATE_JSON --output NEW_PRIVATE_JSON");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!["--config", "--input", "--output"].includes(key) || Object.hasOwn(values, key)) fail("Pixel system CLI arguments are invalid");
    values[key] = absolutePath(resolve(value), `Pixel system ${key}`);
  }
  if (Object.keys(values).length !== 3) fail("Pixel system CLI arguments are incomplete");
  return { command: argv[0], configurationPath: values["--config"], inputPath: values["--input"], outputPath: values["--output"] };
}

export async function main(argv = process.argv.slice(2), dependencies = {}) {
  const options = parseArguments(argv);
  if (["policy-review", "policy-apply"].includes(options.command)) await requirePrivateNewDestination(options.outputPath, "Pixel policy binding receipt output");
  const result = options.command === "policy-review" ? await reviewPixelSystemPolicyBinding(options.configurationPath, options.policyPath, options.candidatePath, dependencies)
    : options.command === "policy-apply" ? await applyPixelSystemPolicyBinding(options.configurationPath, options.policyPath, options.candidatePath, options.confirmation, dependencies)
    : options.command === "planned-review" ? await reviewPixelPlannedSystem(options.configurationPath, options.inputPath, dependencies)
    : options.command === "review" ? await reviewPixelSystem(options.configurationPath, options.inputPath, dependencies)
    : options.command === "start" ? await startPixelSystem(options.configurationPath, options.inputPath, dependencies)
    : options.command === "run" ? await runPixelSystem(options.configurationPath, options.inputPath, dependencies)
      : await stopPixelSystem(options.configurationPath, options.inputPath, dependencies);
  await writePrivateJson(options.outputPath, result);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-outcome-system: ${pixelSystemDiagnostic(error)}\n`);
    process.exitCode = 1;
  });
}

import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, realpath, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

import {
  canonical, validateWorkGoalControllerEnvironment, validateWorkModelArtifactRenderReceipt,
  validateWorkModelArtifactReview, validateWorkModelBackendConfig,
  validateWorkModelBackendReview, validateWorkModelBackendStatus, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { buildModelArtifactManifest } from "./model-artifact.mjs";
import { buildModelRuntimeCacheManifest } from "./model-runtime-cache-artifact.mjs";
import { buildModelBackendLaunch, buildModelBackendStaticIdentity, modelBackendLaunchSha256 } from "./model-backend-launch.mjs";
import {
  buildModelBackendLifecycleReview, startModelBackend, stopModelBackend, WorkModelBackendLifecycleError,
} from "./model-backend-lifecycle.mjs";
import {
  buildModelBackendHaltReview, haltModelBackend, modelBackendStaticIdentitySha256, WorkModelBackendHaltError,
} from "./model-backend-halt.mjs";
import { validateConfiguredModelBackendInspect, WorkModelBackendRuntimeError } from "./model-backend-runtime.mjs";
import {
  DockerBoundaryError, validateImageInspect, validateModelBackendInspect, validateModelBackendNetworkInspect,
} from "../work-runner/docker-boundary.mjs";
import { ModelBackendCoordinationError } from "../work-runner/model-backend-coordination.mjs";

const execute = promisify(execFile);
const MAX_JSON_BYTES = 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const authority = Object.freeze({ grantsExecution: false, startsContainer: false, changesNetwork: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const privacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, acceleratorIdentifier: false, deviceIdentifiers: false, paths: false, hashes: false, credentials: false, prompts: false, responses: false });
const boundary = "Content-free read-only inspection of one exact measured private local-model artifact, confirmed launch contract, container, image, filesystem, environment, resources, accelerator grant, and internal Docker network. It reveals no model, backend, device, path, hash, credential, prompt, or response and grants no execution, container start, image pull, network or device change, credential, external-effect, or completion authority.";
const reviewAuthority = Object.freeze({ grantsExecution: false, createsNetwork: false, startsContainer: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const reviewPrivacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, acceleratorIdentifier: false, deviceIdentifiers: false, paths: false, hashesExceptConfirmation: false, credentials: false, prompts: false, responses: false });
const reviewBoundary = "Content-free review of one exact private contained-model launch bundle after measuring the selected model bytes. Confirmation may write one new private inert bundle but cannot create a network or container, pull an image, grant a device, qualify a model, change policy, enable Deep Work, expose content, or grant execution, credential, external-effect, or completion authority.";
const artifactReviewBoundary = "Content-free review of one private local-model artifact measurement request. Confirmation may read the selected local-model bytes and write one new owner-private inert exact-byte manifest, but cannot start a model or container, create a network, pull an image, grant a device, qualify a model, change policy, expose a path, model identifier, byte hash, credential, prompt, or response, or grant execution, external-effect, or completion authority.";
const artifactReceiptBoundary = "Content-free receipt that one new owner-private inert exact-byte model artifact manifest was written after a stable measurement. It exposes no model, path, byte hash, credential, prompt, or response and grants no model trust, qualification, execution, container start, network, device, credential, external-effect, or completion authority.";
const artifactChanges = Object.freeze({ readsModelBytesOnRender: true, writesPrivateArtifactManifest: true, createsNetwork: false, startsContainer: false, pullsImage: false, changesPolicy: false, changesAuthority: false });
const artifactPrivacy = Object.freeze({ modelIdentifier: false, paths: false, modelByteHashes: false, credentials: false, prompts: false, responses: false });

export class WorkModelBackendCliError extends Error {}
function fail(message) { throw new WorkModelBackendCliError(message); }
export function formatModelBackendCliFailure(error) {
  const known = error instanceof WorkModelBackendCliError || error instanceof WorkModelBackendLifecycleError
    || error instanceof WorkModelBackendHaltError || error instanceof WorkModelBackendRuntimeError
    || error instanceof DockerBoundaryError || error instanceof ModelBackendCoordinationError;
  const lines = [`pixel-work-model-backend: ${known ? error.message : "unexpected failure"}`];
  if (known && error?.modelBackendStartDiagnostic) {
    try { lines.push(`pixel-work-model-backend-diagnostic: ${JSON.stringify(error.modelBackendStartDiagnostic)}`); } catch {}
  }
  return `${lines.join("\n")}\n`;
}
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }

function parseArguments(argv) {
  const commands = ["artifact-review", "artifact-render", "inspect", "review", "render", "start-review", "start", "stop-review", "stop", "halt-review", "halt"];
  if (!Array.isArray(argv) || !commands.includes(argv[0]) || (argv.length - 1) % 2 !== 0) {
    fail("Usage: model-backend-cli.mjs artifact-review|inspect|review|start-review|stop-review|halt-review --config PRIVATE_JSON | artifact-render --config PRIVATE_JSON --output NEW_PRIVATE_JSON --confirm-artifact-measurement-sha256 HASH | render --config PRIVATE_JSON --output NEW_PRIVATE_JSON --confirm-launch-bundle-sha256 HASH | start|stop --config PRIVATE_JSON --confirm-lifecycle-sha256 HASH | halt --config PRIVATE_JSON --confirm-halt-sha256 HASH");
  }
  const command = argv[0], allowed = ["artifact-review", "inspect", "review", "start-review", "stop-review", "halt-review"].includes(command) ? new Set(["--config"])
      : command === "artifact-render" ? new Set(["--config", "--output", "--confirm-artifact-measurement-sha256"])
      : command === "render" ? new Set(["--config", "--output", "--confirm-launch-bundle-sha256"])
      : command === "halt" ? new Set(["--config", "--confirm-halt-sha256"])
      : new Set(["--config", "--confirm-lifecycle-sha256"]);
  if (argv.length !== 1 + allowed.size * 2) fail("model backend arguments are incomplete");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("model backend arguments are invalid, unknown, or duplicated");
    values[key] = ["--confirm-artifact-measurement-sha256", "--confirm-launch-bundle-sha256", "--confirm-lifecycle-sha256", "--confirm-halt-sha256"].includes(key) ? value : resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`model backend command is missing ${key}`);
  if (["artifact-render", "render", "start", "stop", "halt"].includes(command) && !SHA_RE.test(values[command === "artifact-render" ? "--confirm-artifact-measurement-sha256" : command === "render" ? "--confirm-launch-bundle-sha256" : command === "halt" ? "--confirm-halt-sha256" : "--confirm-lifecycle-sha256"])) fail("model backend confirmation is invalid");
  return { command, configPath: values["--config"] ?? null, outputPath: values["--output"] ?? null, confirmation: values["--confirm-artifact-measurement-sha256"] ?? values["--confirm-launch-bundle-sha256"] ?? values["--confirm-lifecycle-sha256"] ?? values["--confirm-halt-sha256"] ?? null };
}

async function readPrivateJson(path, label, expectedOwnerUid) {
  let record, actual;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, MAX_JSON_BYTES, label), realpath(path)]); }
  catch { fail(`${label} could not be opened safely`); }
  if (!samePath(actual, path) || record.details.nlink !== 1 || process.platform !== "win32" && (record.details.uid !== expectedOwnerUid || (record.details.mode & 0o077) !== 0)) fail(`${label} is not owner-private, single-link, and real`);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  try { return parseStrictJson(text, label); } catch (error) { fail(error instanceof Error ? error.message : `${label} is not strict JSON`); }
}

async function requirePrivateOutput(path, expectedOwnerUid) {
  const name = basename(path), parent = dirname(path);
  if (!OUTPUT_RE.test(name) || join(parent, name) !== path) fail("model backend output name is invalid");
  let info, actual;
  try { [info, actual] = await Promise.all([lstat(parent), realpath(parent)]); }
  catch { fail("model backend output parent could not be opened safely"); }
  if (!info.isDirectory() || info.isSymbolicLink() || !samePath(actual, parent) || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("model backend output parent is not owner-private and real");
  if (await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("model backend output must be a new private file");
}

async function writePrivateNew(path, value, expectedOwnerUid) {
  await requirePrivateOutput(path, expectedOwnerUid);
  const temporary = join(dirname(path), `.pixel-model-backend-${process.pid}-${randomBytes(8).toString("hex")}.json`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); }
  finally { await handle.close(); }
  try {
    await link(temporary, path); await unlink(temporary);
    if (process.platform !== "win32") { const parent = await open(dirname(path), constants.O_RDONLY); try { await parent.sync(); } finally { await parent.close(); } }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (["EEXIST", "EPERM", "EACCES"].includes(error?.code)) fail("model backend output could not be atomically published as a new private file");
    throw error;
  }
}

async function loadEnvironmentAndPolicy(environmentPath, expectedOwnerUid) {
  const environment = await readPrivateJson(environmentPath, "private goal controller environment", expectedOwnerUid);
  const environmentErrors = validateWorkGoalControllerEnvironment(environment);
  if (environmentErrors.length) fail(`private goal controller environment is invalid: ${environmentErrors[0]}`);
  const policyPath = resolve(environment.policyPath);
  if (!samePath(policyPath, environment.policyPath)) fail("private work policy path is not absolute and canonical");
  const policy = await readPrivateJson(policyPath, "private work policy", expectedOwnerUid);
  const policyErrors = validateWorkPolicy(policy);
  if (policyErrors.length) fail(`private work policy is invalid: ${policyErrors[0]}`);
  return { environment, policy };
}

export async function prepareArtifactMeasurement(configPath, expectedOwnerUid) {
  const configuration = await readPrivateJson(configPath, "private model backend configuration", expectedOwnerUid);
  const configErrors = validateWorkModelBackendConfig(configuration);
  if (configErrors.length) fail(`private model backend configuration is invalid: ${configErrors[0]}`);
  const request = Object.freeze({
    schemaVersion: 1,
    modelSource: { kind: configuration.modelSource.kind, path: configuration.modelSource.path },
    containerUser: { uid: configuration.containerUser.uid, gid: configuration.containerUser.gid },
  });
  return Object.freeze({ configuration, request, artifactMeasurementSha256: createHash("sha256").update(canonical(request)).digest("hex") });
}

function artifactReview(artifactMeasurementSha256) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-review-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-artifact-review", status: "confirmation-required", artifactMeasurementSha256,
    confirmation: { option: "--confirm-artifact-measurement-sha256", sha256: artifactMeasurementSha256 },
    changes: { ...artifactChanges }, privacy: { ...artifactPrivacy }, authority: { ...reviewAuthority }, boundary: artifactReviewBoundary,
  };
  const errors = validateWorkModelArtifactReview(value);
  if (errors.length) fail(`model artifact review is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

function artifactReceipt(outputPath, artifactMeasurementSha256) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-render-receipt-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-artifact-render", status: "private-inert-manifest-written", outputName: basename(outputPath), artifactMeasurementSha256,
    measurement: { bytesMeasured: true, stableTreeVerified: true },
    changes: { readModelBytes: true, wrotePrivateArtifactManifest: true, createdNetwork: false, startedContainer: false, pulledImage: false, changedPolicy: false, changedAuthority: false },
    authority: { ...reviewAuthority }, boundary: artifactReceiptBoundary,
  };
  const errors = validateWorkModelArtifactRenderReceipt(value);
  if (errors.length) fail(`model artifact render receipt is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

export async function prepareModelBackendLaunch(configPath, expectedOwnerUid, dependencies = {}) {
  const configuration = await readPrivateJson(configPath, "private model backend configuration", expectedOwnerUid);
  const configErrors = validateWorkModelBackendConfig(configuration);
  if (configErrors.length) fail(`private model backend configuration is invalid: ${configErrors[0]}`);
  const environmentPath = resolve(configuration.environmentPath);
  if (!samePath(environmentPath, configuration.environmentPath)) fail("model backend environment path is not absolute and canonical");
  const { environment, policy } = await loadEnvironmentAndPolicy(environmentPath, expectedOwnerUid);
  const buildArtifact = dependencies.buildArtifact ?? buildModelArtifactManifest;
  const artifactManifest = await buildArtifact({ sourcePath: configuration.modelSource.path, kind: configuration.modelSource.kind, expectedOwnerUid, readerUid: configuration.containerUser.uid, readerGid: configuration.containerUser.gid });
  const buildRuntimeCache = dependencies.buildRuntimeCache ?? buildModelRuntimeCacheManifest;
  const runtimeCacheArtifactManifest = configuration.runtimeCacheSeed === null ? null : await buildRuntimeCache({
    sourcePath: configuration.runtimeCacheSeed.path, expectedOwnerUid,
    readerUid: configuration.containerUser.uid, readerGid: configuration.containerUser.gid,
  });
  const buildLaunch = dependencies.buildLaunch ?? buildModelBackendLaunch;
  const launch = buildLaunch({ configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest });
  return { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch, launchBundleSha256: modelBackendLaunchSha256(launch) };
}

export async function prepareModelBackendStaticIdentity(configPath, expectedOwnerUid) {
  const configuration = await readPrivateJson(configPath, "private model backend configuration", expectedOwnerUid);
  const configErrors = validateWorkModelBackendConfig(configuration);
  if (configErrors.length) fail(`private model backend configuration is invalid: ${configErrors[0]}`);
  const environmentPath = resolve(configuration.environmentPath);
  if (!samePath(environmentPath, configuration.environmentPath)) fail("model backend environment path is not absolute and canonical");
  const { environment, policy } = await loadEnvironmentAndPolicy(environmentPath, expectedOwnerUid);
  const staticIdentity = buildModelBackendStaticIdentity({ configuration, environment, policy });
  return { configuration, environment, policy, staticIdentity, staticIdentitySha256: modelBackendStaticIdentitySha256(staticIdentity) };
}

function review(launchBundleSha256) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-review-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-review", status: "confirmation-required",
    artifact: { measured: true, matchesPolicy: true }, launchBundleSha256,
    confirmation: { option: "--confirm-launch-bundle-sha256", sha256: launchBundleSha256 },
    changes: { writesPrivateLaunchBundle: true, createsNetwork: false, startsContainer: false, pullsImage: false, changesPolicy: false, enablesDeepWork: false, changesAuthority: false },
    privacy: { ...reviewPrivacy }, authority: { ...reviewAuthority }, boundary: reviewBoundary,
  };
  const errors = validateWorkModelBackendReview(value);
  if (errors.length) fail(`model backend review is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

async function dockerInspect(dockerPath, kind, name) {
  const { stdout } = await execute(dockerPath, [kind, "inspect", name], {
    encoding: "utf8", windowsHide: true, timeout: 15000, maxBuffer: 4 * 1024 * 1024,
    env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" },
  });
  const parsed = parseStrictJson(stdout, "model backend Docker inspection");
  if (!Array.isArray(parsed) || parsed.length !== 1 || !parsed[0] || typeof parsed[0] !== "object" || Array.isArray(parsed[0])) throw new Error("inspection shape is invalid");
  return parsed[0];
}

function status(state, checks, now) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-status-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-inspect", generatedAt: now.toISOString(), state, checks,
    nextAction: state === "ready" ? "ready" : state === "unavailable" ? "start-or-repair-private-backend" : "inspect-private-backend-configuration",
    privacy: { ...privacy }, authority: { ...authority }, boundary,
  };
  const errors = validateWorkModelBackendStatus(value);
  if (errors.length) fail(`model backend status is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

export async function runModelBackendCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), now = dependencies.now ?? new Date();
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || !Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("model backend command environment is invalid");
  if (["artifact-review", "artifact-render"].includes(options.command)) {
    const prepared = await prepareArtifactMeasurement(options.configPath, expectedOwnerUid);
    if (options.command === "artifact-review") return artifactReview(prepared.artifactMeasurementSha256);
    if (options.confirmation !== prepared.artifactMeasurementSha256) fail("confirmation differs from the exact private artifact measurement request");
    const buildArtifact = dependencies.buildArtifact ?? buildModelArtifactManifest;
    const manifest = await buildArtifact({
      sourcePath: prepared.request.modelSource.path, kind: prepared.request.modelSource.kind,
      expectedOwnerUid, readerUid: prepared.request.containerUser.uid, readerGid: prepared.request.containerUser.gid,
    });
    await writePrivateNew(options.outputPath, manifest, expectedOwnerUid);
    return artifactReceipt(options.outputPath, prepared.artifactMeasurementSha256);
  }
  if (["halt-review", "halt"].includes(options.command)) {
    const prepared = await prepareModelBackendStaticIdentity(options.configPath, expectedOwnerUid);
    const haltDependencies = {
      ...(dependencies.lifecycleExecutor ? { executor: dependencies.lifecycleExecutor } : {}),
      ...(dependencies.lifecycleInspect || dependencies.inspect ? { inspector: dependencies.lifecycleInspect ?? dependencies.inspect } : {}),
    };
    if (options.command === "halt-review") return buildModelBackendHaltReview(prepared, haltDependencies);
    const postflight = () => prepareModelBackendStaticIdentity(options.configPath, expectedOwnerUid);
    return haltModelBackend(prepared, {
      ...haltDependencies, confirmation: options.confirmation, postflight, now,
      coordinationOptions: { expectedOwnerUid }, ...(dependencies.coordinator ? { coordinator: dependencies.coordinator } : {}),
    });
  }
  const preparedLaunch = await prepareModelBackendLaunch(options.configPath, expectedOwnerUid, dependencies);
  if (["start-review", "stop-review"].includes(options.command)) return buildModelBackendLifecycleReview(options.command.slice(0, -7), preparedLaunch.launchBundleSha256);
  if (["start", "stop"].includes(options.command)) {
    const postflight = () => prepareModelBackendLaunch(options.configPath, expectedOwnerUid, dependencies);
    const lifecycleDependencies = {
      confirmation: options.confirmation, now, postflight, coordinationOptions: { expectedOwnerUid },
      ...(dependencies.lifecycleExecutor ? { executor: dependencies.lifecycleExecutor } : {}),
      ...(dependencies.lifecycleInspect || dependencies.inspect ? { inspector: dependencies.lifecycleInspect ?? dependencies.inspect } : {}),
      ...(dependencies.readinessProbe ? { readinessProbe: dependencies.readinessProbe } : {}),
      ...(dependencies.sleeper ? { sleeper: dependencies.sleeper } : {}),
      ...(dependencies.coordinator ? { coordinator: dependencies.coordinator } : {}),
    };
    return options.command === "start" ? startModelBackend(preparedLaunch, lifecycleDependencies) : stopModelBackend(preparedLaunch, lifecycleDependencies);
  }
  if (options.command !== "inspect") {
    const prepared = preparedLaunch;
    if (options.command === "review") return review(prepared.launchBundleSha256);
    if (options.confirmation !== prepared.launchBundleSha256) fail("confirmation differs from the exact remeasured launch bundle");
    await writePrivateNew(options.outputPath, prepared.launch, expectedOwnerUid);
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-work-model-backend-render", status: "private-inert-launch-written",
      outputName: basename(options.outputPath), launchBundleSha256: prepared.launchBundleSha256,
      changes: { writesPrivateLaunchBundle: true, createsNetwork: false, startsContainer: false, pullsImage: false, changesPolicy: false, enablesDeepWork: false, changesAuthority: false },
      authority: { ...reviewAuthority }, boundary: reviewBoundary,
    });
  }
  const { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch } = preparedLaunch;
  const checks = { artifactBound: true, launchBound: true, dockerReachable: false, imageBound: false, containerRunning: false, identityBound: false, acceleratorBound: false, filesystemBound: false, environmentBound: false, resourcesBound: false, privateNetworkBound: false };
  const inspect = dependencies.inspect ?? ((kind, name) => dockerInspect(environment.runtime.dockerPath, kind, name));
  let image, container, network;
  try {
    [image, container, network] = await Promise.all([
      inspect("image", policy.localModel.imageRef), inspect("container", environment.runtime.backendContainerName), inspect("network", environment.runtime.backendNetworkName),
    ]);
    checks.dockerReachable = true;
  } catch { return status("unavailable", checks, now); }
  const runtime = { ...environment.runtime, backendAlias: "pixel-local-model" };
  const prepared = { policy, bindings: launch.bindings, plan: { model: { backendImageDigest: policy.localModel.imageDigest, id: policy.localModel.id } } };
  try { validateImageInspect(image, policy.localModel.imageRef, policy.localModel.imageDigest); checks.imageBound = true; }
  catch { return status("incompatible", checks, now); }
  if (container?.State?.Running === true) checks.containerRunning = true;
  else return status("unavailable", checks, now);
  try {
    validateModelBackendInspect(container, image, prepared, runtime);
    checks.identityBound = true; checks.acceleratorBound = true;
    validateModelBackendNetworkInspect(network, runtime, [{ id: container.Id, name: environment.runtime.backendContainerName }]);
    checks.privateNetworkBound = true;
    validateConfiguredModelBackendInspect(container, image, network, { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch });
    const postflight = await prepareModelBackendLaunch(options.configPath, expectedOwnerUid, dependencies);
    if (postflight.launchBundleSha256 !== preparedLaunch.launchBundleSha256) fail("model backend private inputs changed during exact inspection");
    checks.filesystemBound = true; checks.environmentBound = true; checks.resourcesBound = true;
  } catch { return status("incompatible", checks, now); }
  return status("ready", checks, now);
}

export async function main(argv = process.argv.slice(2)) {
  const result = await runModelBackendCommand(argv);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.operation === "pixel-work-model-backend-inspect" && result.state !== "ready") process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(formatModelBackendCliFailure(error));
    process.exitCode = 1;
  });
}

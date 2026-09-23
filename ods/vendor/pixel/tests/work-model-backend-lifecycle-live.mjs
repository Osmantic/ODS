import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { chmod, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import {
  buildModelBackendLaunch, buildModelBackendStaticIdentity, modelBackendLaunchSha256,
} from "../deploy/work-controller/model-backend-launch.mjs";
import {
  execModelBackendDocker, inspectModelBackendDockerMaybe, modelBackendLifecycleSha256, startModelBackend, stopModelBackend,
} from "../deploy/work-controller/model-backend-lifecycle.mjs";
import {
  buildModelBackendHaltReview, haltModelBackend, modelBackendStaticIdentitySha256,
} from "../deploy/work-controller/model-backend-halt.mjs";
import {
  validateWorkModelBackendHaltReceipt, validateWorkModelBackendLifecycleReceipt,
  validateWorkModelBackendLiveQualification, validateWorkPolicy,
} from "../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../scripts/lib/secure-files.mjs";

const IMAGE_DIGEST = "sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f";
const IMAGE_REF = `ghcr.io/ggml-org/llama.cpp@${IMAGE_DIGEST}`;
const MODEL_REVISION = "99dd1a73db5a37100bd4ae633f4cfce6560e1567";
const MODEL_SHA256 = "6151b1929d7f5aa3385d9ddef3393e55587c0a55de661562322bc51dfda93a04";
const MODEL_BYTES = 19077344;
const artifactBoundary = "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.";
const execute = promisify(execFile);

function fail(message) { throw new Error(message); }
function sha(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function dockerDesktopPath(path) {
  const full = resolve(path);
  if (process.platform !== "win32") return full;
  const match = /^([A-Za-z]):\\(.*)$/u.exec(full);
  if (!match) fail("live model path is not on a Docker Desktop shared drive");
  return `/run/desktop/mnt/host/${match[1].toLowerCase()}/${match[2].replaceAll("\\", "/")}`;
}
async function fixedInference(executor, prepared) {
  const container = await inspectModelBackendDockerMaybe(executor, prepared.launch.container.command, prepared.launch.container.env, "container", prepared.environment.runtime.backendContainerName);
  if (!container?.State?.Running || !/^[a-f0-9]{64}$/u.test(container.Id ?? "")) fail("exact running backend disappeared before fixed inference");
  const payload = JSON.stringify({ prompt: "Once upon a time", n_predict: 8, temperature: 0, seed: 1 });
  const result = await executor(prepared.launch.container.command, [
    "container", "exec", "--user", `${prepared.configuration.containerUser.uid}:${prepared.configuration.containerUser.gid}`,
    container.Id, "/usr/bin/curl", "--fail", "--silent", "--show-error", "--max-time", "10",
    "--header", "Content-Type: application/json", "--data-binary", payload, "http://127.0.0.1:8080/completion",
  ], { env: prepared.launch.container.env, timeoutMs: 15000, maxBuffer: 65536 });
  let value;
  try { value = JSON.parse(result.stdout); } catch { fail("fixed inference response was not JSON"); }
  if (typeof value.content !== "string" || value.content.length < 1) fail("fixed local inference did not return generated content");
  return { status: 200, responseBytes: Buffer.byteLength(result.stdout), generatedContentObserved: true };
}

const modelPath = process.env.PIXEL_TEST_MODEL_PATH;
const dockerPath = process.env.PIXEL_TEST_DOCKER_PATH;
if (!modelPath || !dockerPath) fail("Usage: PIXEL_TEST_MODEL_PATH=GGUF PIXEL_TEST_DOCKER_PATH=DOCKER node tests/work-model-backend-lifecycle-live.mjs");
const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const gitOptions = { cwd: root, encoding: "utf8", windowsHide: true, maxBuffer: 1024 * 1024 };
const [sourceCommitResult, sourceTreeResult, sourceStatusResult] = await Promise.all([
  execute("git", ["rev-parse", "HEAD"], gitOptions), execute("git", ["rev-parse", "HEAD^{tree}"], gitOptions),
  execute("git", ["status", "--porcelain", "--untracked-files=normal"], gitOptions),
]);
if (sourceStatusResult.stdout !== "") fail("live qualification requires a clean exact source tree");
const sourceCommit = sourceCommitResult.stdout.trim(), sourceTree = sourceTreeResult.stdout.trim();
const modelBytes = await readFile(resolve(modelPath));
const artifactSha256 = sha(modelBytes), suffix = randomBytes(5).toString("hex");
if (modelBytes.length !== MODEL_BYTES || artifactSha256 !== MODEL_SHA256) fail("live qualification model differs from the fixed public synthetic artifact");
const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
Object.assign(policy.localModel, {
  prepared: false, provider: "llama.cpp", id: "pixel-lifecycle-qualification", imageRef: IMAGE_REF, imageDigest: IMAGE_DIGEST,
  modelArtifactSha256: artifactSha256, backendVersion: "b9014", acceleratorClass: "nvidia-cuda", contextWindow: 2048,
  supportsVision: false, maxRequestOutputTokens: 64,
});
const policyErrors = validateWorkPolicy(policy); if (policyErrors.length) fail(`live qualification policy is invalid: ${policyErrors[0]}`);
const environment = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
const coordinationRoot = await mkdtemp(join(tmpdir(), "pixel-model-lifecycle-live-"));
if (process.platform !== "win32") await chmod(coordinationRoot, 0o700);
environment.stateRoot = coordinationRoot;
environment.runtime.backendNetworkName = `pixel-model-life-${suffix}`; environment.runtime.backendContainerName = `pixel-model-life-${suffix}`;
const configuration = {
  $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
  environmentPath: "/etc/pixel-work/environment.json", modelSource: { kind: "file", path: dockerDesktopPath(modelPath) }, runtimeCacheSeed: null,
  backendNetwork: { subnet: "172.31.250.0/29" }, containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
  resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 256 },
  readiness: { startupTimeoutSeconds: 120, probeIntervalMilliseconds: 500 },
  accelerator: { class: "nvidia-cuda", count: 1, deviceIds: [] },
  providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 99, flashAttention: true },
  boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
};
const artifactManifest = {
  $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1,
  kind: "file", artifactSha256, fileCount: 1, totalBytes: modelBytes.length,
  files: [{ relativePath: "model", bytes: modelBytes.length, sha256: artifactSha256 }],
  authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false }, boundary: artifactBoundary,
};
const launch = buildModelBackendLaunch({ configuration, environment, policy, artifactManifest });
const prepared = { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest: null, launch, launchBundleSha256: modelBackendLaunchSha256(launch) };
const executor = (_command, args, options) => execModelBackendDocker(dockerPath, args, options);
const dockerVersionResult = await executor(launch.container.command, ["version", "--format", "{{json .}}"], { env: launch.container.env, timeoutMs: 30000, maxBuffer: 1024 * 1024 });
const dockerVersion = parseStrictJson(dockerVersionResult.stdout, "live qualification Docker version");
if (!dockerVersion?.Client?.Version || !dockerVersion?.Server?.Version) fail("live qualification Docker runtime identity is unavailable");
const postflight = async () => prepared;
const startConfirmation = modelBackendLifecycleSha256("start", prepared.launchBundleSha256);
const stopConfirmation = modelBackendLifecycleSha256("stop", prepared.launchBundleSha256);
const staticIdentity = buildModelBackendStaticIdentity({ configuration, environment, policy });
const haltPrepared = { configuration, environment, policy, staticIdentity, staticIdentitySha256: modelBackendStaticIdentitySha256(staticIdentity) };
const haltPostflight = async () => haltPrepared;
let startReceipt = null, inference = null, haltReceipt = null, restartReceipt = null, stopReceipt = null, failure = null;
try {
  startReceipt = await startModelBackend(prepared, { confirmation: startConfirmation, executor, postflight });
  if (validateWorkModelBackendLifecycleReceipt(startReceipt).length || !startReceipt.checks.readinessPassed) fail("live start receipt is invalid");
  inference = await fixedInference(executor, prepared);
  const haltReview = await buildModelBackendHaltReview(haltPrepared, { executor });
  haltReceipt = await haltModelBackend(haltPrepared, { confirmation: haltReview.haltSha256, executor, postflight: haltPostflight });
  if (validateWorkModelBackendHaltReceipt(haltReceipt).length || haltReceipt.state !== "halted"
    || !haltReceipt.checks.forensicResourcesRetained || haltReceipt.changes.containerRemoved || haltReceipt.changes.networkRemoved) fail("live emergency halt receipt is invalid");
  restartReceipt = await startModelBackend(prepared, { confirmation: startConfirmation, executor, postflight });
  if (validateWorkModelBackendLifecycleReceipt(restartReceipt).length || restartReceipt.state !== "ready-started" || !restartReceipt.checks.readinessPassed) fail("live post-halt restart receipt is invalid");
} catch (error) { failure = error; }
try {
  stopReceipt = await stopModelBackend(prepared, { confirmation: stopConfirmation, executor, postflight });
  if (validateWorkModelBackendLifecycleReceipt(stopReceipt).length) fail("live stop receipt is invalid");
} catch (cleanupError) { if (!failure) failure = cleanupError; else failure.cleanupError = cleanupError; }
await rm(coordinationRoot, { recursive: true, force: true });
if (failure) throw failure;
const qualification = {
  $schema: "https://osmantic.com/pixel/schemas/work-model-backend-live-qualification-v1.schema.json", schemaVersion: 1,
  operation: "pixel-work-model-backend-live-qualification", generatedAt: new Date().toISOString(),
  source: { commit: sourceCommit, tree: sourceTree, clean: true },
  model: { origin: "public-fixed-synthetic-model", repository: "ggml-org/tiny-llamas", revision: MODEL_REVISION, file: "stories15M-q4_0.gguf", bytes: modelBytes.length, sha256: artifactSha256 },
  image: { digest: IMAGE_DIGEST }, accelerator: { class: "nvidia-cuda", count: 1 },
  runtime: {
    controllerPlatform: process.platform, controllerArch: process.arch, nodeVersion: process.version,
    dockerClientVersion: dockerVersion.Client.Version, dockerServerVersion: dockerVersion.Server.Version,
    dockerServerOs: dockerVersion.Server.Os, dockerServerArch: dockerVersion.Server.Arch,
  },
  results: {
    startState: startReceipt.state, readinessPassed: startReceipt.checks.readinessPassed,
    inferenceStatus: inference.status, inferenceResponseBytes: inference.responseBytes, generatedContentObserved: inference.generatedContentObserved,
    haltState: haltReceipt.state, haltRetainedForensics: haltReceipt.checks.forensicResourcesRetained,
    restartState: restartReceipt.state, restartReadinessPassed: restartReceipt.checks.readinessPassed,
    stopState: stopReceipt.state, cleanupComplete: stopReceipt.state === "removed",
  },
  privacy: { promptsFromUser: false, responsesExposed: false, credentialsUsed: false, providerUsed: false, onlyPublicAndSourceHashes: true },
  boundary: "Disposable exact local Docker/GPU lifecycle, emergency halt, restart, and fixed synthetic inference qualification only. It does not establish model quality, client acceptance, production readiness, or authority for future model execution.",
};
const qualificationErrors = validateWorkModelBackendLiveQualification(qualification);
if (qualificationErrors.length) fail(`live qualification receipt is invalid: ${qualificationErrors[0]}`);
process.stdout.write(`${JSON.stringify(qualification)}\n`);

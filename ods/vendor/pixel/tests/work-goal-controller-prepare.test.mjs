import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalControllerPrepareCommand } from "../deploy/work-controller/goal-controller-prepare-cli.mjs";
import { runGoalPrepareCommand } from "../deploy/work-controller/goal-prepare-cli.mjs";
import { buildModelBackendLaunch } from "../deploy/work-controller/model-backend-launch.mjs";
import { loadGoalCycleConfiguration } from "../deploy/work-controller/goal-cycle-cli.mjs";
import { initializeKnowledgeVault } from "../deploy/work-controller/knowledge-vault.mjs";
import { canonical, validateWorkGoalControllerBundleManifest, validateWorkGoalControllerEnvironment } from "../scripts/lib/work-contract.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const baseTime = Date.parse("2026-08-11T12:00:00Z");
const digest = (value) => createHash("sha256").update(value).digest("hex");
const declarationBoundary = "Owner-authored long-horizon structure only. Preparation may derive immutable hashes and aggregate budgets but grants no execution, lease, retry, credential, scope expansion, external effect, or completion authority.";
const environmentBoundary = "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.";

test("disabled and opt-in capability environment examples remain distinct valid shapes", async () => {
  const ordinary = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
  const capability = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-capability-environment.example.json", import.meta.url), "utf8"));
  const knowledge = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-knowledge-environment.example.json", import.meta.url), "utf8"));
  assert.deepEqual(validateWorkGoalControllerEnvironment(ordinary), []);
  assert.deepEqual(validateWorkGoalControllerEnvironment(capability), []);
  assert.deepEqual(validateWorkGoalControllerEnvironment(knowledge), []);
  assert.equal(ordinary.capabilityRuntime, undefined);
  assert.equal(capability.capabilityRuntime.bindings.length, 1);
  assert.equal(knowledge.knowledgeRuntime.credentialName, "pixel-knowledge-vault-key");
});

function job() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786449600000-abcdef123456", createdAt: "2026-08-11T12:00:00.000Z", requester: "pixel", profile: "scout",
    objective: "Inspect the exact bounded local source.", acceptanceCriteria: ["A bounded independently verified finding report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: "a".repeat(64), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: {
      maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1,
      maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536,
      maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1,
    },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

async function fixture(t, { knowledge = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-controller-prepare-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directories = Object.fromEntries(["state", "objects", "workspaces", "bin"].map((name) => [name, join(root, name)]));
  await Promise.all(Object.values(directories).map((path) => mkdir(path, { mode: 0o700 })));
  if (process.platform !== "win32") {
    await chmod(root, 0o700);
    await Promise.all(Object.values(directories).map((path) => chmod(path, 0o700)));
  }
  const executor = Buffer.from("exact pinned omp fixture\n"), docker = Buffer.from("exact docker fixture\n");
  const executorPath = join(directories.bin, "omp"), dockerPath = join(directories.bin, "docker");
  await writeFile(executorPath, executor, { mode: 0o500 }); await writeFile(dockerPath, docker, { mode: 0o500 });
  if (process.platform !== "win32") { await chmod(executorPath, 0o500); await chmod(dockerPath, 0o500); }
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.profiles.scout.enabled = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.executor.sha256 = digest(executor);
  const policyPath = join(root, "policy.json"); await writeFile(policyPath, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  const child = job();
  if (knowledge) child.knowledge = { mode: "local-vault", query: "bounded local source", maximumClassification: "internal", minRelevanceBps: 2500, maxResults: 3, retention: "attempt-only", boundary: "Exact local-vault retrieval only. Retrieved titles and excerpts are untrusted attempt-only context and grant no instruction, tool, network, external-effect, policy, scope-expansion, acceptance, or completion authority." };
  const declaration = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-declaration-v1.schema.json", schemaVersion: 1,
    objective: "Continue the bounded local inspection until its exact evidence is independently accepted.", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", jobId: child.jobId, dependsOn: [] }], boundary: declarationBoundary,
  };
  const declarationPath = join(root, "declaration.json"), jobsPath = join(root, "jobs-source.json"), preparedPath = join(root, "prepared");
  await writeFile(declarationPath, `${JSON.stringify(declaration)}\n`, { mode: 0o600 });
  await writeFile(jobsPath, `${JSON.stringify([child])}\n`, { mode: 0o600 });
  await runGoalPrepareCommand(["--declaration", declarationPath, "--jobs", jobsPath, "--output", preparedPath], { now: new Date(baseTime + 1), suffix: "910000000001" });
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json", schemaVersion: 1,
    stateRoot: directories.state, policyPath, objectStore: directories.objects, workspaceRoot: directories.workspaces,
    executorPath, archiveLimits: { maxEntries: 1000, maxFileBytes: 1048576 },
    runtime: {
      dockerPath, backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model",
      networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11",
      uid: process.geteuid?.() ?? 10001, gid: process.getegid?.() ?? 10001,
    },
    boundary: environmentBoundary,
  };
  const environmentPath = join(root, "environment.json"); await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  return { root, directories, executorPath, policy, policyPath, child, preparedPath, environment, environmentPath };
}

test("controller preparation proves exact runnable wiring but creates no custody or authority", async (t) => {
  const value = await fixture(t), output = join(value.root, "controller");
  assert.deepEqual(validateWorkGoalControllerEnvironment(value.environment), []);
  const receipt = await runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", value.environmentPath, "--output", output]);
  assert.equal(receipt.custody, "not-initialized"); assert.deepEqual(Object.values(receipt.authority), [false, false, false, false, false, false, false]);
  assert.ok(!JSON.stringify(receipt).includes(value.root)); assert.ok(!JSON.stringify(receipt).includes(value.child.objective));
  assert.deepEqual((await readdir(output)).sort(), ["controller-bundle.json", "controller.json", "goal.json", "jobs.json"]);
  const manifest = JSON.parse(await readFile(join(output, "controller-bundle.json"), "utf8"));
  assert.deepEqual(validateWorkGoalControllerBundleManifest(manifest), []);
  assert.equal(receipt.controllerBundleSha256, digest(canonical(manifest)));
  assert.equal(manifest.dockerSha256, digest(Buffer.from("exact docker fixture\n")));
  const loaded = await loadGoalCycleConfiguration(join(output, "controller.json"));
  assert.equal(loaded.goal.goalId, receipt.goalId); assert.deepEqual(loaded.jobs.map((entry) => entry.jobId), [value.child.jobId]);
  assert.deepEqual(await readdir(value.directories.state), []);
  if (process.platform !== "win32") for (const name of await readdir(output)) assert.equal((await lstat(join(output, name))).mode & 0o077, 0);
});

test("controller preparation rejects a worker UID that cannot enter owner-private runtime inputs", { skip: process.platform === "win32" ? "POSIX ownership is unavailable" : false }, async (t) => {
  const value = await fixture(t);
  const environment = structuredClone(value.environment);
  environment.runtime.uid = environment.runtime.uid === 2147483647 ? environment.runtime.uid - 1 : environment.runtime.uid + 1;
  const environmentPath = join(value.root, "mismatched-worker-uid-environment.json");
  const output = join(value.root, "mismatched-worker-uid-controller");
  await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  await assert.rejects(
    runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", output]),
    /worker UID must equal the owner UID/u,
  );
  assert.equal(await lstat(output).then(() => true, () => false), false);
  assert.deepEqual(await readdir(value.directories.state), []);
});

test("controller preparation carries exact reviewed backend bindings into every supervised worker", { skip: process.platform === "win32" ? "exact Docker launch paths require a POSIX supported host" : false }, async (t) => {
  const value = await fixture(t), output = join(value.root, "backend-bound-controller"), launchPath = join(value.root, "model-backend-launch.json");
  const environment = structuredClone(value.environment); environment.modelBackendLaunchPath = launchPath;
  const environmentPath = join(value.root, "backend-bound-environment.json");
  await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath, modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" }, runtimeCacheSeed: null, backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 }, accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const artifact = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1, kind: "file",
    artifactSha256: value.policy.localModel.modelArtifactSha256, fileCount: 1, totalBytes: 42,
    files: [{ relativePath: "model", bytes: 42, sha256: value.policy.localModel.modelArtifactSha256 }],
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.",
  };
  const launch = buildModelBackendLaunch({ configuration, environment, policy: value.policy, artifactManifest: artifact });
  await writeFile(launchPath, `${JSON.stringify(launch)}\n`, { mode: 0o600 });
  const receipt = await runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", output]);
  const [config, manifest] = await Promise.all([
    readFile(join(output, "controller.json"), "utf8").then(JSON.parse), readFile(join(output, "controller-bundle.json"), "utf8").then(JSON.parse),
  ]);
  assert.deepEqual(config.runtime.modelBackendBindings, launch.bindings);
  assert.equal(config.runtime.requireExactModelBackendBindings, true);
  assert.equal(manifest.modelBackendLaunchSha256, digest(canonical(launch)));
  assert.equal(receipt.modelBackendLaunchSha256, manifest.modelBackendLaunchSha256);
  const changedEnvironment = structuredClone(environment); changedEnvironment.runtime.backendContainerName = "substituted-model-backend";
  const changedPath = join(value.root, "changed-backend-environment.json"); await writeFile(changedPath, `${JSON.stringify(changedEnvironment)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", changedPath, "--output", join(value.root, "changed-backend-controller")]), /differs from controller policy, environment, or runtime/u);
  const misleadingLaunch = structuredClone(launch), nameIndex = misleadingLaunch.container.args.indexOf("--name");
  misleadingLaunch.container.args[nameIndex + 1] = "substituted-model-backend";
  misleadingLaunch.container.args.push(environment.runtime.backendContainerName);
  await writeFile(launchPath, `${JSON.stringify(misleadingLaunch)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "misleading-backend-controller")]), /container target differs/u);
  const wrongAliasLaunch = structuredClone(launch), aliasIndex = wrongAliasLaunch.container.args.indexOf("--alias");
  wrongAliasLaunch.container.args[aliasIndex + 1] = "substituted-model-id";
  await writeFile(launchPath, `${JSON.stringify(wrongAliasLaunch)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "wrong-alias-backend-controller")]), /llama\.cpp model backend arguments differ/u);
  const duplicateImageLaunch = structuredClone(launch); duplicateImageLaunch.container.args.push(value.policy.localModel.imageRef);
  await writeFile(launchPath, `${JSON.stringify(duplicateImageLaunch)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "duplicate-image-backend-controller")]), /image or provider wrapper differs/u);
  const relabeledLaunch = structuredClone(launch), labelIndex = relabeledLaunch.network.args.indexOf(`com.osmantic.pixel.work-model-environment-sha256=${launch.bindings.environmentSha256}`);
  relabeledLaunch.network.args[labelIndex] = `com.osmantic.pixel.work-model-environment-sha256=${"0".repeat(64)}`;
  await writeFile(launchPath, `${JSON.stringify(relabeledLaunch)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "relabeled-backend-controller")]), /network labels differ/u);
});

test("controller preparation accepts the exact vLLM cache bootstrap while rejecting identity and wrapper drift", { skip: process.platform === "win32" ? "exact Docker launch paths require a POSIX supported host" : false }, async (t) => {
  const value = await fixture(t), output = join(value.root, "vllm-backend-controller"), launchPath = join(value.root, "vllm-model-backend-launch.json");
  const environment = structuredClone(value.environment); environment.modelBackendLaunchPath = launchPath;
  const environmentPath = join(value.root, "vllm-backend-environment.json");
  await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  value.policy.localModel.provider = "vllm";
  value.policy.localModel.id = "DeepSeek-V4-Flash-0731";
  value.policy.localModel.contextWindow = 1048576;
  value.policy.localModel.imageRef = `local/pixel-vllm@${value.policy.localModel.imageDigest}`;
  value.policy.localModel.acceleratorClass = "nvidia-cuda";
  value.policy.localModel.inference = fixtureVllmInferencePolicy();
  const files = [
    { relativePath: "config.json", bytes: 10, sha256: digest("vllm-config") },
    { relativePath: "model-00001.safetensors", bytes: 100, sha256: digest("vllm-weights") },
  ];
  const artifactSha256 = digest(canonical({ schemaVersion: 1, kind: "directory", files }));
  value.policy.localModel.modelArtifactSha256 = artifactSha256;
  await writeFile(value.policyPath, `${JSON.stringify(value.policy)}\n`, { mode: 0o600 });
  const cacheFiles = [
    { relativePath: "sparkinfer/compile/kernel.so", bytes: 25, sha256: digest("cache-sparkinfer") },
    { relativePath: "tilelang/kernel.so", bytes: 25, sha256: digest("cache-tilelang") },
    { relativePath: "triton/kernel.cubin", bytes: 25, sha256: digest("cache-triton") },
    { relativePath: "vllm/torch_compile_cache/aot.so", bytes: 25, sha256: digest("cache-vllm") },
  ];
  const cacheArtifactSha256 = digest(canonical({ schemaVersion: 1, kind: "directory", files: cacheFiles }));
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath, modelSource: { kind: "directory", path: "/srv/pixel/models/vllm-model" },
    runtimeCacheSeed: {
      path: "/srv/pixel/runtime-cache/vllm-seed", artifactSha256: cacheArtifactSha256,
      imageDigest: value.policy.localModel.imageDigest, cacheFingerprint: "vllm-controller-test-cache-v1",
      subtrees: ["vllm", "triton", "sparkinfer", "tilelang"],
    },
    backendNetwork: { subnet: "172.31.255.0/29" }, containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
    resources: { memoryMiB: 196608, cpuCores: 48, pids: 4096, nofile: 65536, tmpfsMiB: 1024, cacheMiB: 32768, sharedMemoryMiB: 32768 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 }, accelerator: { class: "nvidia-cuda", count: 2, deviceIds: [] },
    providerOptions: { provider: "vllm", imageEntrypoint: "/usr/local/bin/pixel-dsv4-serve", tensorParallelSize: 2, maxSequences: 16, gpuMemoryUtilizationPermille: 984, dtype: "auto", enforceEager: false, reasoningParser: "deepseek_v4", toolCallParser: "deepseek_v4" },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const artifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1, kind: "directory",
    artifactSha256, fileCount: files.length, totalBytes: 110, files,
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.",
  };
  const runtimeCacheArtifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-runtime-cache-manifest-v1.schema.json", schemaVersion: 1, kind: "directory",
    artifactSha256: cacheArtifactSha256, fileCount: cacheFiles.length, totalBytes: 100, files: cacheFiles,
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private deterministic identity for exact owner-selected executable model-runtime cache bytes. Measurement grants no cache trust, execution, container start, network, device, credential, external-effect, or completion authority; use requires an image-bound read-only launch contract.",
  };
  const launch = buildModelBackendLaunch({ configuration, environment, policy: value.policy, artifactManifest, runtimeCacheArtifactManifest });
  await writeFile(launchPath, `${JSON.stringify(launch)}\n`, { mode: 0o600 });
  const receipt = await runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", output]);
  const config = JSON.parse(await readFile(join(output, "controller.json"), "utf8"));
  assert.equal(receipt.modelBackendLaunchSha256.length, 64);
  assert.deepEqual(config.runtime.modelBackendBindings, launch.bindings);
  const drifted = structuredClone(launch), imageIndex = drifted.container.args.indexOf(value.policy.localModel.imageRef);
  drifted.container.args[imageIndex + 2] += " ";
  await writeFile(launchPath, `${JSON.stringify(drifted)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "drifted-vllm-controller")]), /image or provider wrapper differs/u);
  const wrongModelId = structuredClone(launch), servedModelIndex = wrongModelId.container.args.indexOf("--served-model-name");
  wrongModelId.container.args[servedModelIndex + 1] = "substituted-model-id";
  await writeFile(launchPath, `${JSON.stringify(wrongModelId)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "wrong-vllm-model-id-controller")]), /vLLM model backend arguments differ/u);
  const wrongImage = structuredClone(launch); wrongImage.container.args[imageIndex] = `sha256:${"0".repeat(64)}`;
  await writeFile(launchPath, `${JSON.stringify(wrongImage)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", join(value.root, "wrong-vllm-image-controller")]), /image or provider wrapper differs/u);
});

test("controller preparation authenticates the exact vault and binds systemd credential loading", async (t) => {
  const value = await fixture(t, { knowledge: true }), output = join(value.root, "knowledge-controller"), vaultRoot = join(value.root, "knowledge-vault"), credentialRoot = join(value.root, "knowledge-credentials"), credentialPath = join(credentialRoot, "pixel-knowledge-vault-key"), masterKey = Buffer.alloc(32, 37), vaultId = "knowledgevault-abcdef123456";
  await mkdir(credentialRoot, { mode: 0o700 }); if (process.platform !== "win32") await chmod(credentialRoot, 0o700);
  await writeFile(credentialPath, `${masterKey.toString("hex")}\n`, { flag: "wx", mode: 0o600 });
  await initializeKnowledgeVault({ root: vaultRoot, vaultId, masterKey, now: new Date(baseTime) });
  const environment = structuredClone(value.environment);
  environment.knowledgeRuntime = { vaultRoot, vaultId, credentialSourcePath: credentialPath, credentialName: "pixel-knowledge-vault-key", ownerId: "owner-one", clientId: "client-one", maxContextBytes: 16384 };
  const environmentPath = join(value.root, "knowledge-environment.json"); await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  const receipt = await runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", output]);
  const config = JSON.parse(await readFile(join(output, "controller.json"), "utf8"));
  assert.equal(config.knowledgeRuntime.credentialName, "pixel-knowledge-vault-key"); assert.equal(config.knowledgeRuntime.vaultRoot, vaultRoot); assert.ok(!JSON.stringify(receipt).includes(credentialPath));
  const loaded = await loadGoalCycleConfiguration(join(output, "controller.json"), { knowledgeCredentialPath: credentialPath });
  assert.deepEqual(loaded.knowledgeMasterKey, masterKey); assert.equal(loaded.jobs[0].knowledge.retention, "attempt-only");
  const wrongCredentialRoot = join(value.root, "wrong-knowledge-credentials"); await mkdir(wrongCredentialRoot, { mode: 0o700 }); if (process.platform !== "win32") await chmod(wrongCredentialRoot, 0o700);
  const wrongCredential = join(wrongCredentialRoot, "pixel-knowledge-vault-key"); await writeFile(wrongCredential, `${Buffer.alloc(32, 99).toString("hex")}\n`, { mode: 0o600 });
  await assert.rejects(() => loadGoalCycleConfiguration(join(output, "controller.json"), { knowledgeCredentialPath: wrongCredential }), /master key identity/u);
});

test("controller preparation copies and hash-binds an exact credential-free capability selection", async (t) => {
  const value = await fixture(t), output = join(value.root, "capability-controller");
  const packSha256 = "b".repeat(64), treeSha256 = "c".repeat(64);
  const capabilityPolicy = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v1.schema.json", schemaVersion: 1,
    policyId: "workcappolicy-1786446000000-abcdef123456", createdAt: "2026-08-11T11:00:00.000Z", expiresAt: "2026-08-11T14:00:00.000Z",
    profiles: ["scout"], packs: [{ id: "fixture-tools", version: "1.0.0", packSha256, treeSha256, tools: [{ name: "analyze", effectClass: "read-only" }], classifications: ["internal"] }],
    limits: { maxSessionsPerLease: 4, maxGrantLifetimeMs: 30000, maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 3000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    watchdog: { maxFailures: 1, maxRepeatedEquivalent: 2, maxRepeatedFailure: 1, maxEventsWithoutVerifiedProgress: 8 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Private controller allowlist for exact signed local capability packs. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority.",
  };
  const capabilityPolicyPath = join(value.root, "capability-policy-source.json"), allowedSignersPath = join(value.root, "capability-allowed-signers"), sshKeygenPath = join(value.directories.bin, "ssh-keygen"), dockerConfigPath = join(value.root, "capability-docker-config");
  await writeFile(capabilityPolicyPath, `${JSON.stringify(capabilityPolicy)}\n`, { mode: 0o600 });
  await writeFile(allowedSignersPath, "pixel-fixture namespaces=\"pixel-work-capability-pack\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureFixtureFixtureFixtureFix\n", { mode: 0o600 });
  await writeFile(sshKeygenPath, "fixture signature verifier\n", { mode: 0o500 });
  await mkdir(dockerConfigPath, { mode: 0o700 });
  if (process.platform !== "win32") { await chmod(capabilityPolicyPath, 0o600); await chmod(allowedSignersPath, 0o600); await chmod(sshKeygenPath, 0o500); await chmod(dockerConfigPath, 0o700); }
  const environment = structuredClone(value.environment);
  environment.capabilityRuntime = {
    controllerPolicyPath: capabilityPolicyPath, allowedSignersPath, sshKeygenPath, dockerConfigPath, maxHealthAgeMs: 60000,
    bindings: [{
      jobId: value.child.jobId, pack: { id: "fixture-tools", version: "1.0.0", packSha256, treeSha256 }, tools: ["analyze"], maxSessions: 2, grantLifetimeMs: 30000,
      limits: { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 3000, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    }],
  };
  const environmentPath = join(value.root, "capability-environment.json"); await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(environmentPath, 0o600);
  const receipt = await runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", environmentPath, "--output", output]);
  assert.deepEqual((await readdir(output)).sort(), ["capability-policy.json", "controller-bundle.json", "controller.json", "goal.json", "jobs.json"]);
  const [manifest, config, copiedPolicy] = await Promise.all([
    readFile(join(output, "controller-bundle.json"), "utf8").then(JSON.parse),
    readFile(join(output, "controller.json"), "utf8").then(JSON.parse),
    readFile(join(output, "capability-policy.json"), "utf8").then(JSON.parse),
  ]);
  assert.equal(config.capabilityRuntime.controllerPolicyPath, join(output, "capability-policy.json"));
  assert.equal(manifest.capabilityPolicySha256, digest(canonical(copiedPolicy)));
  assert.equal(manifest.capabilityBindingsSha256, digest(canonical(config.capabilityRuntime.bindings)));
  assert.equal(receipt.capabilityPolicySha256, manifest.capabilityPolicySha256);
  const loaded = await loadGoalCycleConfiguration(join(output, "controller.json"));
  assert.deepEqual(loaded.capabilityPolicy, capabilityPolicy);
  assert.deepEqual(loaded.config.capabilityRuntime.bindings, environment.capabilityRuntime.bindings);
  assert.deepEqual(await readdir(value.directories.state), []);
});

test("controller preparation rejects bundle, policy, executor, link, and writable-root substitution", async (t) => {
  const value = await fixture(t);
  const tamperedJobs = JSON.parse(await readFile(join(value.preparedPath, "jobs.json"), "utf8")); tamperedJobs[0].objective = "changed";
  await writeFile(join(value.preparedPath, "jobs.json"), `${JSON.stringify(tamperedJobs)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", value.environmentPath, "--output", join(value.root, "tampered")]), /hashes or identity differ/);
  await writeFile(join(value.preparedPath, "jobs.json"), `${JSON.stringify([value.child], null, 2)}\n`, { mode: 0o600 });
  const disabled = structuredClone(value.policy); disabled.profiles.scout.maxBudgets.maxRuntimeSeconds = 59;
  const disabledPath = join(value.root, "disabled-policy.json"); await writeFile(disabledPath, `${JSON.stringify(disabled)}\n`, { mode: 0o600 });
  const disabledEnvironment = { ...value.environment, policyPath: disabledPath };
  const disabledEnvironmentPath = join(value.root, "disabled-environment.json"); await writeFile(disabledEnvironmentPath, `${JSON.stringify(disabledEnvironment)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", disabledEnvironmentPath, "--output", join(value.root, "disabled")]), /exceeds Scout budget/);
  const substitutedExecutorPath = join(value.directories.bin, "substituted-omp");
  await writeFile(substitutedExecutorPath, "substituted executor\n", { mode: 0o500 }); if (process.platform !== "win32") await chmod(substitutedExecutorPath, 0o500);
  const substitutedEnvironment = { ...value.environment, executorPath: substitutedExecutorPath };
  const substitutedEnvironmentPath = join(value.root, "substituted-environment.json"); await writeFile(substitutedEnvironmentPath, `${JSON.stringify(substitutedEnvironment)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", substitutedEnvironmentPath, "--output", join(value.root, "executor-drift")]), /differs from the private policy/);
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", value.environmentPath, "--output", join(value.directories.state, "inside-state")]), /immutable input is inside/);
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", value.environmentPath, "--output", join(value.preparedPath, "nested-output")]), /source bundle overlap/);
  const linkedEnvironment = join(value.root, "linked-environment.json"); await link(value.environmentPath, linkedEnvironment);
  await assert.rejects(runGoalControllerPrepareCommand(["--goal-bundle", value.preparedPath, "--environment", linkedEnvironment, "--output", join(value.root, "linked")]), /single-link/);
});

test("competing controller preparations publish one complete inert bundle", async (t) => {
  const value = await fixture(t), output = join(value.root, "race");
  const attempts = await Promise.allSettled(Array.from({ length: 16 }, () => runGoalControllerPrepareCommand([
    "--goal-bundle", value.preparedPath, "--environment", value.environmentPath, "--output", output,
  ])));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  assert.ok(attempts.filter((attempt) => attempt.status === "rejected").every((attempt) => /already exists/u.test(attempt.reason.message)));
  assert.deepEqual((await readdir(output)).sort(), ["controller-bundle.json", "controller.json", "goal.json", "jobs.json"]);
  assert.ok((await readdir(value.root)).every((name) => !name.startsWith(".pixel-work-controller-")));
});

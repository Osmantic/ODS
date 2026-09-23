import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildModelBackendLaunch, modelBackendLaunchSha256, modelBackendServerArgumentsFromLaunch,
  VLLM_CACHE_SEED_BOOTSTRAP_NAME,
} from "../deploy/work-controller/model-backend-launch.mjs";
import { validateConfiguredModelBackendContainerInspect } from "../deploy/work-controller/model-backend-runtime.mjs";
import { modelBackendIdentityLabels } from "../deploy/work-runner/docker-boundary.mjs";
import { canonical, validateWorkModelBackendConfig, validateWorkModelBackendLaunch } from "../scripts/lib/work-contract.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const digest = (character) => character.repeat(64);
const artifactBoundary = "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.";
const runtimeCacheBoundary = "Private deterministic identity for exact owner-selected executable model-runtime cache bytes. Measurement grants no cache trust, execution, container start, network, device, credential, external-effect, or completion authority; use requires an image-bound read-only launch contract.";

async function fixture() {
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  const environment = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath: "/etc/pixel-work/environment.json", modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" },
    runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
    resources: { memoryMiB: 8192, cpuCores: 4, pids: 512, nofile: 4096, tmpfsMiB: 256, cacheMiB: 64, sharedMemoryMiB: 256 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 4, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const artifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1,
    kind: "file", artifactSha256: policy.localModel.modelArtifactSha256, fileCount: 1, totalBytes: 42,
    files: [{ relativePath: "model", bytes: 42, sha256: policy.localModel.modelArtifactSha256 }],
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false }, boundary: artifactBoundary,
  };
  return { policy, environment, configuration, artifactManifest };
}

async function vllmFixture() {
  const value = await fixture();
  value.policy.localModel.provider = "vllm";
  value.policy.localModel.id = "DeepSeek-V4-Flash-0731";
  value.policy.localModel.contextWindow = 1048576;
  value.policy.localModel.imageRef = `local/pixel-vllm@${value.policy.localModel.imageDigest}`;
  value.policy.localModel.acceleratorClass = "nvidia-cuda";
  value.policy.localModel.inference = fixtureVllmInferencePolicy();
  const files = [
    { relativePath: "config.json", bytes: 10, sha256: digest("a") },
    { relativePath: "model-00001.safetensors", bytes: 100, sha256: digest("b") },
  ];
  const artifactSha256 = createHash("sha256").update(canonical({ schemaVersion: 1, kind: "directory", files })).digest("hex");
  value.policy.localModel.modelArtifactSha256 = artifactSha256;
  value.artifactManifest = { ...value.artifactManifest, kind: "directory", artifactSha256, fileCount: 2, totalBytes: 110, files };
  value.configuration.modelSource = { kind: "directory", path: "/srv/pixel/models/vllm-model" };
  const cacheFiles = [
    { relativePath: "sparkinfer/compile/kernel.so", bytes: 25, sha256: digest("c") },
    { relativePath: "tilelang/kernel.so", bytes: 25, sha256: digest("d") },
    { relativePath: "triton/kernel.cubin", bytes: 25, sha256: digest("e") },
    { relativePath: "vllm/torch_compile_cache/aot.so", bytes: 25, sha256: digest("f") },
  ];
  const cacheArtifactSha256 = createHash("sha256").update(canonical({ schemaVersion: 1, kind: "directory", files: cacheFiles })).digest("hex");
  value.configuration.runtimeCacheSeed = {
    path: "/srv/pixel/runtime-cache/vllm-seed", artifactSha256: cacheArtifactSha256,
    imageDigest: value.policy.localModel.imageDigest, cacheFingerprint: "vllm-test-cache-v1",
    subtrees: ["vllm", "triton", "sparkinfer", "tilelang"],
  };
  value.runtimeCacheArtifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-runtime-cache-manifest-v1.schema.json", schemaVersion: 1,
    kind: "directory", artifactSha256: cacheArtifactSha256, fileCount: cacheFiles.length, totalBytes: 100, files: cacheFiles,
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: runtimeCacheBoundary,
  };
  value.configuration.containerUser = { uid: 2000, gid: 0 };
  value.configuration.publishLoopbackPort = 8000;
  value.configuration.resources = { memoryMiB: 196608, cpuCores: 48, pids: 4096, nofile: 65536, tmpfsMiB: 1024, cacheMiB: 32768, sharedMemoryMiB: 32768 };
  value.configuration.accelerator = { class: "nvidia-cuda", count: 2, deviceIds: [] };
  value.configuration.providerOptions = { provider: "vllm", imageEntrypoint: "/usr/local/bin/pixel-dsv4-serve", tensorParallelSize: 2, maxSequences: 16, gpuMemoryUtilizationPermille: 984, dtype: "auto", enforceEager: false, reasoningParser: "deepseek_v4", toolCallParser: "deepseek_v4" };
  return value;
}

const glm53ProfileSpecs = Object.freeze({
  "glm53-flash-fast-v1": Object.freeze({
    imageRef: "verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-v75@sha256:4605c420cc589be9fd15fc759c7f7c2a6035dab48f885c9466eb2233527bca64",
    imageDigest: "sha256:4605c420cc589be9fd15fc759c7f7c2a6035dab48f885c9466eb2233527bca64",
    modelId: "GLM-5.3-Flash-TR3-4bpw-FP8KV", contextWindow: 262144, maxSequences: 1, gpuPermille: 986,
    dcpSize: 2, dcpBackend: "a2a", attentionBackend: "FLASHINFER_MLA_SPARSE_SM120", kvCacheDtype: "fp8_ds_mla",
    compilationConfig: null, speculativeConfig: "{\"method\":\"mtp\",\"num_speculative_tokens\":3,\"draft_sample_method\":\"probabilistic\"}",
    launchSha256: "3c1587d52fd2e21e990a091b9cce254e60f716964f0d68857fdc394f89cb8690", serverArgumentsSha256: "91b5b2cf1a187635a896681670ca373ef94473d393f9660bddf41ac23573e914",
    requiredEnvironment: ["VLLM_B12X_GLM_NOPE_NVFP4=1", "VLLM_USE_B12X_DCP_A2A=1", "VLLM_B12X_MLA_CKV_GATHER=0"],
  }),
  "glm53-flash-long-v1": Object.freeze({
    imageRef: "verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    imageDigest: "sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    modelId: "GLM-5.3-Flash-TR3-4bpw-v84-NVFP4KV", contextWindow: 1048576, maxSequences: 1, gpuPermille: 986,
    dcpSize: 2, dcpBackend: "a2a", attentionBackend: "B12X_MLA_SPARSE", kvCacheDtype: "nvfp4_ds_mla",
    compilationConfig: "{\"cudagraph_capture_sizes\":[1]}", speculativeConfig: null,
    launchSha256: "44ae374394085df6094f18b9ec674be7dcc37c8bf627bf8cecc028c97a439749", serverArgumentsSha256: "85b023a9fb99fab07db2069d0637a743543ed087d85104de63a35c70648969bf",
    requiredEnvironment: ["VLLM_USE_B12X_DCP_A2A=1", "VLLM_NVFP4_MLA_DYNAMIC_SCALE=0", "VLLM_EXL3_PREFILL_TRELLIS=1"],
  }),
  "glm53-flash-parallel-v1": Object.freeze({
    imageRef: "verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    imageDigest: "sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    modelId: "GLM-5.3-Flash-TR3-4bpw-v84-NVFP4KV", contextWindow: 524288, maxSequences: 4, gpuPermille: 980,
    dcpSize: 1, dcpBackend: null, attentionBackend: "B12X_MLA_SPARSE", kvCacheDtype: "nvfp4_ds_mla",
    compilationConfig: "{\"cudagraph_capture_sizes\":[1, 2, 4]}", speculativeConfig: null,
    launchSha256: "17ad682122979a6fc19c70affa86d350f5977fc35c9e180c5acb107aee34390e", serverArgumentsSha256: "0aadbe10b556041c44c2cdf946b29991975cd189c677d1f8bd96b0db1a15ba05",
    requiredEnvironment: ["VLLM_NVFP4_MLA_DYNAMIC_SCALE=0", "VLLM_EXL3_PREFILL_TRELLIS=1"],
  }),
});

async function glm53VllmFixture(runtimeProfile) {
  const spec = glm53ProfileSpecs[runtimeProfile];
  const value = await vllmFixture();
  value.policy.localModel.id = spec.modelId;
  value.policy.localModel.imageRef = spec.imageRef;
  value.policy.localModel.imageDigest = spec.imageDigest;
  value.policy.localModel.contextWindow = spec.contextWindow;
  value.configuration.runtimeCacheSeed.imageDigest = spec.imageDigest;
  const cacheFiles = [
    { relativePath: "jit/cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc/vllm/kernel.so", bytes: 25, sha256: digest("1") },
    { relativePath: "triton/compiled-kernel/kernel.cubin", bytes: 25, sha256: digest("2") },
    { relativePath: "vllm/torch_compile_cache/index.json", bytes: 25, sha256: digest("3") },
  ];
  const cacheArtifactSha256 = createHash("sha256").update(canonical({ schemaVersion: 1, kind: "directory", files: cacheFiles })).digest("hex");
  value.configuration.runtimeCacheSeed = {
    path: "/srv/pixel/runtime-cache/glm53-seed", artifactSha256: cacheArtifactSha256,
    imageDigest: spec.imageDigest, cacheFingerprint: "cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc",
    subtrees: ["jit", "triton", "vllm"],
  };
  value.runtimeCacheArtifactManifest = {
    ...value.runtimeCacheArtifactManifest, artifactSha256: cacheArtifactSha256,
    fileCount: cacheFiles.length, totalBytes: 75, files: cacheFiles,
  };
  value.configuration.providerOptions = {
    provider: "vllm", imageEntrypoint: "/opt/venv/bin/vllm", runtimeProfile, tensorParallelSize: 2,
    maxSequences: spec.maxSequences, gpuMemoryUtilizationPermille: spec.gpuPermille, dtype: "bfloat16",
    enforceEager: false, reasoningParser: "glm45", toolCallParser: "glm47",
  };
  return value;
}

function expectedGlm53ServerArguments(spec) {
  const args = [
    "serve", "/models/model", "--served-model-name", spec.modelId, "--host", "0.0.0.0", "--port", "8080",
    "--language-model-only", "--tensor-parallel-size", "2", "--enable-expert-parallel",
    "--decode-context-parallel-size", String(spec.dcpSize),
  ];
  if (spec.dcpBackend !== null) args.push("--dcp-comm-backend", spec.dcpBackend);
  args.push(
    "--dtype", "bfloat16", "--load-format", "safetensors", "--moe-backend", "b12x",
    "--attention-backend", spec.attentionBackend, "--kv-cache-dtype", spec.kvCacheDtype,
    "--max-model-len", String(spec.contextWindow), "--max-num-batched-tokens", "2048", "--max-num-seqs", String(spec.maxSequences),
    "--gpu-memory-utilization", (spec.gpuPermille / 1000).toFixed(3), "--cpu-offload-gb", "0",
  );
  if (spec.compilationConfig !== null) args.push("--compilation-config", spec.compilationConfig);
  args.push(
    "--enable-chunked-prefill", "--no-enable-prefix-caching", "--generation-config", "/models/model",
    "--reasoning-parser", "glm45", "--enable-auto-tool-choice", "--tool-call-parser", "glm47", "--disable-custom-all-reduce",
  );
  if (spec.speculativeConfig !== null) args.push("--speculative-config", spec.speculativeConfig);
  args.push("--no-enable-log-requests", "--no-enable-log-outputs", "--no-enable-log-deltas", "--disable-uvicorn-access-log");
  return args;
}

test("guided llama.cpp launch is an inert exact hardened Docker plan", async () => {
  const value = await fixture();
  const launch = buildModelBackendLaunch(value);
  assert.deepEqual(validateWorkModelBackendLaunch(launch), []);
  assert.equal(modelBackendLaunchSha256(launch).length, 64);
  assert.deepEqual(launch.network.args.slice(0, 6), ["network", "create", "--driver", "bridge", "--internal", "--attachable=false"]);
  assert.equal(launch.network.args[launch.network.args.indexOf("--subnet") + 1], value.configuration.backendNetwork.subnet);
  for (const required of ["container", "create", "--pull", "never", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--restart", "unless-stopped", "--ipc", "private"]) assert.ok(launch.container.args.includes(required), required);
  assert.ok(launch.container.args.includes(`com.osmantic.pixel.work-model-artifact-sha256=${value.policy.localModel.modelArtifactSha256}`));
  assert.ok(launch.container.args.includes(value.policy.localModel.imageRef));
  const imageIndex = launch.container.args.indexOf(value.policy.localModel.imageRef);
  assert.deepEqual(modelBackendServerArgumentsFromLaunch({ launch, policy: value.policy }), launch.container.args.slice(imageIndex + 1));
  const duplicateImage = structuredClone(launch);
  duplicateImage.container.args.push(value.policy.localModel.imageRef);
  assert.throws(() => modelBackendServerArgumentsFromLaunch({ launch: duplicateImage, policy: value.policy }));
  assert.equal(launch.container.args.includes("--publish"), false);
  assert.equal(launch.container.args.includes("--gpus"), false);
  assert.ok(launch.container.args.includes("HF_HUB_OFFLINE=1"));
  assert.ok(launch.container.args.includes("DO_NOT_TRACK=1"));
  assert.ok(launch.container.args.includes("USER=pixel-model"));
  assert.ok(launch.container.args.includes("LOGNAME=pixel-model"));
  assert.equal(launch.container.args[launch.container.args.indexOf("--log-driver") + 1], "local");
  const logOptions = launch.container.args.flatMap((value, index, args) => value === "--log-opt" ? [args[index + 1]] : []);
  assert.deepEqual(logOptions, ["max-size=1m", "max-file=1", "compress=false"]);
  assert.equal(launch.authority.startsContainer, false);
  assert.doesNotMatch(JSON.stringify({ authority: launch.authority, boundary: launch.boundary }), /grant.*true|starts.*true/iu);
});

test("guided comparison launch can explicitly disable restart without changing the resilient default", async () => {
  const resilient = await fixture();
  assert.equal(buildModelBackendLaunch(resilient).container.args[buildModelBackendLaunch(resilient).container.args.indexOf("--restart") + 1], "unless-stopped");
  const comparison = await fixture(); comparison.configuration.restartPolicy = "no";
  const launch = buildModelBackendLaunch(comparison);
  assert.equal(launch.container.args[launch.container.args.indexOf("--restart") + 1], "no");
  const invalid = await fixture(); invalid.configuration.restartPolicy = "always";
  assert.throws(() => buildModelBackendLaunch(invalid), /configuration is invalid/u);
});

test("guided backend examples are exact schema-valid starting points", async () => {
  for (const name of ["model-backend-llama.example.json", "model-backend-vllm.example.json"]) {
    const value = JSON.parse(await readFile(new URL(`../deploy/work-controller/${name}`, import.meta.url), "utf8"));
    assert.deepEqual(validateWorkModelBackendConfig(value), [], name);
  }
});

test("guided vLLM launch binds measured directory, two GPUs, parser behavior, and private loopback", async () => {
  const value = await vllmFixture();
  const launch = buildModelBackendLaunch(value);
  assert.equal(modelBackendLaunchSha256(launch), "c48ca25c7da34320ecfcf482010fe32f3e7105a33b42ed0d69cdbe0178c52d01", "legacy vLLM launch must remain byte-identical");
  assert.equal(createHash("sha256").update(canonical(launch.container.args)).digest("hex"), "ed50b8cb0deb78a46351c5a0a106ceecdad538d21bc609676600fd5c33d99c4c", "legacy vLLM container vector must remain byte-identical");
  assert.deepEqual(validateWorkModelBackendLaunch(launch), []);
  const imageIndex = launch.container.args.indexOf(value.policy.localModel.imageRef);
  const bootstrapIndex = launch.container.args.indexOf("pixel-vllm-cache-bootstrap");
  assert.equal(bootstrapIndex, launch.container.args.indexOf(VLLM_CACHE_SEED_BOOTSTRAP_NAME));
  assert.deepEqual(launch.container.args.slice(imageIndex - 2, imageIndex + 4), ["--entrypoint", "/bin/sh", value.policy.localModel.imageRef, "-c", launch.container.args[imageIndex + 2], "pixel-vllm-cache-bootstrap"]);
  assert.match(launch.container.args[imageIndex + 2], /cp -R \/var\/cache\/pixel-model-seed\/\. \/var\/cache\/pixel-model\//u);
  assert.match(launch.container.args[imageIndex + 2], /exec "\$PIXEL_VLLM_SERVER_ENTRYPOINT" "\$@"/u);
  assert.equal(launch.container.args[bootstrapIndex + 1], "/models/model");
  assert.equal(launch.container.args.includes("--model"), false);
  assert.deepEqual(modelBackendServerArgumentsFromLaunch({ launch, policy: value.policy }), launch.container.args.slice(bootstrapIndex + 1));
  for (const mutate of [
    (args) => { args[imageIndex + 2] += " "; },
    (args) => { args[bootstrapIndex] = `${VLLM_CACHE_SEED_BOOTSTRAP_NAME}-drift`; },
    (args) => { args.splice(imageIndex - 2, 6, value.policy.localModel.imageRef); },
  ]) {
    const drifted = structuredClone(launch);
    mutate(drifted.container.args);
    assert.throws(() => modelBackendServerArgumentsFromLaunch({ launch: drifted, policy: value.policy }));
  }
  for (const pair of [["--gpus", "driver=nvidia,count=2"], ["--publish", "127.0.0.1:8000:8080"], ["--tensor-parallel-size", "2"], ["--max-num-seqs", "16"], ["--reasoning-parser", "deepseek_v4"], ["--tool-call-parser", "deepseek_v4"]]) {
    const index = launch.container.args.indexOf(pair[0]); assert.equal(launch.container.args[index + 1], pair[1], pair[0]);
  }
  assert.ok(launch.container.args.includes("--enable-auto-tool-choice"));
  for (const privacyFlag of ["--no-enable-log-requests", "--no-enable-log-outputs", "--no-enable-log-deltas", "--disable-uvicorn-access-log"]) {
    assert.ok(launch.container.args.includes(privacyFlag), privacyFlag);
  }
  assert.equal(launch.container.args.includes("--disable-log-requests"), false, "obsolete vLLM logging flag");
  for (const required of [
    "VLLM_NO_USAGE_STATS=1", "HF_HUB_OFFLINE=1", "VLLM_CACHE_ROOT=/var/cache/pixel-model/vllm", "TRITON_CACHE_DIR=/var/cache/pixel-model/triton",
    "VLLM_HOST_IP=127.0.0.1", "NCCL_SOCKET_IFNAME=eth0", "NCCL_IB_DISABLE=1", "NCCL_P2P_DISABLE=1",
    "NCCL_P2P_LEVEL=SYS", "NCCL_CUMEM_ENABLE=0", "NCCL_CUMEM_HOST_ENABLE=0", "NCCL_ALGO=Ring",
    "NCCL_PROTO=LL", "NCCL_NTHREADS=512", "NCCL_MIN_NCHANNELS=8",
  ]) assert.ok(launch.container.args.includes(required), required);
  for (const name of [
    "VLLM_CACHE_ROOT", "VLLM_CONFIG_ROOT", "VLLM_CACHE_DIR", "TRITON_CACHE_DIR", "TORCHINDUCTOR_CACHE_DIR", "TORCH_EXTENSIONS_DIR",
    "FLASHINFER_WORKSPACE_BASE", "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR", "TVM_FFI_CACHE_DIR", "TVM_CACHE_DIR",
    "TILELANG_CACHE_DIR", "TILELANG_TMP_DIR", "CUTE_DSL_CACHE_DIR", "B12X_CUTE_COMPILE_CACHE_DIR",
    "SPARKINFER_COMPILE_CACHE_DIR", "DG_JIT_CACHE_DIR", "MM_SPARSE_ATTN_AOT_CACHE", "MINFER_FMHA_CACHE_DIR",
    "CUDA_CACHE_PATH", "CUPY_CACHE_DIR", "NUMBA_CACHE_DIR",
  ]) {
    assert.ok(launch.container.args.some((value) => value.startsWith(`${name}=/var/cache/pixel-model/`)), name);
  }
  assert.equal(launch.container.args.some((value) => value.includes("/cache/jit")), false, "image cache paths must not escape the private writable cache");
  assert.ok(launch.container.args.includes("type=bind,src=/srv/pixel/runtime-cache/vllm-seed,dst=/var/cache/pixel-model-seed,readonly"));
  assert.ok(launch.container.args.includes("PIXEL_VLLM_SERVER_ENTRYPOINT=/usr/local/bin/pixel-dsv4-serve"));
  assert.ok(launch.container.args.some((value) => value.includes("/var/cache/pixel-model:rw,nosuid,nodev,exec,size=32768m") && value.includes("uid=2000,gid=0")));
  assert.equal(launch.container.args.includes("--trust-remote-code"), false);
  assert.equal(launch.container.args.includes("--tool-parser-plugin"), false);

  const explicitEnvironment = [];
  for (let index = 0; index < launch.container.args.length; index += 1) if (launch.container.args[index] === "--env") explicitEnvironment.push(launch.container.args[++index]);
  const image = {
    Id: `sha256:${digest("9")}`,
    Config: {
      Env: ["PATH=/usr/bin:/bin", "LOCAL_INFERENCE_CACHE_FINGERPRINT=vllm-test-cache-v1"], Labels: {},
      Entrypoint: ["/usr/local/bin/pixel-dsv4-serve"], WorkingDir: "/",
    },
  };
  const container = {
    Id: digest("8"), Name: `/${value.environment.runtime.backendContainerName}`, Image: image.Id,
    State: { Status: "created", Running: false, Paused: false, Restarting: false, OOMKilled: false, Dead: false, ExitCode: 0 },
    Config: {
      Hostname: "pixel-local-model", User: "2000:0", Tty: false, OpenStdin: false, StdinOnce: false,
      Env: [...explicitEnvironment, ...image.Config.Env], Cmd: launch.container.args.slice(imageIndex + 1), Image: value.policy.localModel.imageRef,
      Entrypoint: ["/bin/sh"], WorkingDir: "/", StopSignal: "SIGTERM", StopTimeout: 30,
      ExposedPorts: { "8080/tcp": {} }, Labels: modelBackendIdentityLabels({ policy: value.policy, bindings: launch.bindings }),
    },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false, CapAdd: null,
      NetworkMode: value.environment.runtime.backendNetworkName, PortBindings: { "8080/tcp": [{ HostIp: "127.0.0.1", HostPort: "8000" }] }, PublishAllPorts: false,
      Devices: [], DeviceRequests: [{ Driver: "nvidia", Count: 2, DeviceIDs: null, Capabilities: [["gpu"]], Options: {} }], DeviceCgroupRules: null,
      Mounts: [
        { Type: "bind", Source: value.configuration.modelSource.path, Target: "/models/model", ReadOnly: true },
        { Type: "bind", Source: value.configuration.runtimeCacheSeed.path, Target: "/var/cache/pixel-model-seed", ReadOnly: true },
      ],
      Binds: null, VolumesFrom: null, VolumeDriver: "",
      Tmpfs: {
        "/tmp": "rw,nosuid,nodev,noexec,size=1024m,mode=1777",
        "/var/cache/pixel-model": "rw,nosuid,nodev,exec,size=32768m,mode=0700,uid=2000,gid=0",
      },
      Memory: 196608 * 1024 * 1024, MemorySwap: 196608 * 1024 * 1024, NanoCpus: 48_000_000_000, PidsLimit: 4096, ShmSize: 32768 * 1024 * 1024,
      Ulimits: [{ Name: "nofile", Hard: 65536, Soft: 65536 }], RestartPolicy: { Name: "unless-stopped", MaximumRetryCount: 0 },
      LogConfig: { Type: "local", Config: { "compress": "false", "max-file": "1", "max-size": "1m" } },
      IpcMode: "private", CgroupnsMode: "private", Init: true, AutoRemove: false, Links: null, Dns: null, DnsOptions: [], DnsSearch: [], ExtraHosts: null, GroupAdd: null,
      PidMode: "", UTSMode: "", UsernsMode: "", Isolation: "", Runtime: "runc", OomKillDisable: false, OomScoreAdj: 0, MemoryReservation: 0,
    },
    Mounts: [
      { Type: "bind", Source: value.configuration.modelSource.path, Destination: "/models/model", RW: false },
      { Type: "bind", Source: value.configuration.runtimeCacheSeed.path, Destination: "/var/cache/pixel-model-seed", RW: false },
    ],
    NetworkSettings: {
      Ports: { "8080/tcp": [] },
      Networks: { [value.environment.runtime.backendNetworkName]: { Aliases: ["pixel-local-model"], IPAddress: "", IPPrefixLen: 0, IPAMConfig: null, Links: null, DriverOpts: null } },
    },
  };
  const exactInputs = { configuration: value.configuration, environment: value.environment, policy: value.policy, artifactManifest: value.artifactManifest, runtimeCacheArtifactManifest: value.runtimeCacheArtifactManifest, launch };
  assert.equal(validateConfiguredModelBackendContainerInspect(container, image, exactInputs, { requireEffectivePorts: false }), true);
  const crossProfileCacheRoot = structuredClone(container);
  crossProfileCacheRoot.HostConfig.Tmpfs["/cache"] = crossProfileCacheRoot.HostConfig.Tmpfs["/var/cache/pixel-model"];
  delete crossProfileCacheRoot.HostConfig.Tmpfs["/var/cache/pixel-model"];
  assert.throws(
    () => validateConfiguredModelBackendContainerInspect(crossProfileCacheRoot, image, exactInputs, { requireEffectivePorts: false }),
    /tmpfs destinations differ/u,
  );
  const staleMounts = structuredClone(container);
  staleMounts.HostConfig.Mounts[1] = { Type: "bind", Source: `${value.configuration.runtimeCacheSeed.path}/triton`, Target: "/var/cache/pixel-model/triton", ReadOnly: true };
  staleMounts.Mounts[1] = { Type: "bind", Source: `${value.configuration.runtimeCacheSeed.path}/triton`, Destination: "/var/cache/pixel-model/triton", RW: false };
  assert.throws(() => validateConfiguredModelBackendContainerInspect(staleMounts, image, exactInputs, { requireEffectivePorts: false }), /visible mounts/u);
  const staleEntrypoint = structuredClone(container); staleEntrypoint.Config.Entrypoint = image.Config.Entrypoint;
  assert.throws(() => validateConfiguredModelBackendContainerInspect(staleEntrypoint, image, exactInputs, { requireEffectivePorts: false }), /entrypoint/u);

  for (const mutate of [
    (copy) => { copy.runtimeCacheArtifactManifest.files[0].sha256 = digest("0"); },
    (copy) => { copy.configuration.runtimeCacheSeed.artifactSha256 = digest("0"); },
    (copy) => { copy.configuration.runtimeCacheSeed.imageDigest = `sha256:${digest("0")}`; },
    (copy) => { copy.configuration.runtimeCacheSeed.path = copy.configuration.modelSource.path; },
    (copy) => { copy.configuration.providerOptions.imageEntrypoint = "../../bin/sh"; },
    (copy) => { copy.runtimeCacheArtifactManifest = null; },
  ]) {
    const hostile = structuredClone(value); mutate(hostile);
    assert.throws(() => buildModelBackendLaunch(hostile), undefined, mutate.toString());
  }

  const comparisonReadme = await readFile(new URL("../deploy/agent-comparison/README.md", import.meta.url), "utf8");
  const example = comparisonReadme.match(/The launch-argument file has this private shape[\s\S]*?```json\s*([\s\S]*?)```/u);
  assert.ok(example, "comparison launch-argument example");
  assert.deepEqual(
    JSON.parse(example[1]).arguments,
    launch.container.args.slice(bootstrapIndex + 1),
    "documented DSV4 vector must equal the mechanically rendered server arguments",
  );
});

test("qualified GLM 5.3 profiles render the exact live-proven fast, long-context, and four-lane vectors", async (t) => {
  for (const [runtimeProfile, spec] of Object.entries(glm53ProfileSpecs)) await t.test(runtimeProfile, async () => {
    const value = await glm53VllmFixture(runtimeProfile);
    assert.deepEqual(validateWorkModelBackendConfig(value.configuration), []);
    const launch = buildModelBackendLaunch(value);
    assert.deepEqual(validateWorkModelBackendLaunch(launch), []);
    const serverArgs = modelBackendServerArgumentsFromLaunch({ launch, policy: value.policy });
    assert.deepEqual(serverArgs, expectedGlm53ServerArguments(spec));
    assert.equal(modelBackendLaunchSha256(launch), spec.launchSha256);
    assert.equal(createHash("sha256").update(canonical(serverArgs)).digest("hex"), spec.serverArgumentsSha256);
    assert.equal(launch.container.args.filter((argument) => argument === spec.imageRef).length, 1);
    assert.ok(launch.container.args.includes("PIXEL_VLLM_SERVER_ENTRYPOINT=/opt/venv/bin/vllm"));
    const imageIndex = launch.container.args.indexOf(spec.imageRef);
    assert.match(launch.container.args[imageIndex + 2], /cp -R \/var\/cache\/pixel-model-seed\/\. \/cache\//u);
    assert.match(launch.container.args[imageIndex + 2], /chmod -R u\+rwX \/cache/u);
    const tmpfs = launch.container.args.flatMap((argument, index, args) => argument === "--tmpfs" ? [args[index + 1]] : []);
    assert.ok(tmpfs.some((entry) => entry.startsWith("/cache:rw,nosuid,nodev,exec,")), `${runtimeProfile}: qualified /cache tmpfs`);
    assert.equal(tmpfs.some((entry) => entry.startsWith("/var/cache/pixel-model:")), false, `${runtimeProfile}: no relocated cache root`);
    assert.equal(serverArgs[serverArgs.indexOf("--cpu-offload-gb") + 1], "0");
    assert.equal(serverArgs.includes("--enforce-eager"), false);
    assert.equal(serverArgs.includes("--trust-remote-code"), false);
    assert.equal(serverArgs.includes("--tool-parser-plugin"), false);
    const environment = launch.container.args.flatMap((argument, index, args) => argument === "--env" ? [args[index + 1]] : []);
    for (const required of [
      "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "VLLM_NO_USAGE_STATS=1", "VLLM_HOST_IP=127.0.0.1",
      "NCCL_SOCKET_IFNAME=eth0", "VLLM_ENGINE_READY_TIMEOUT_S=3600", "NCCL_IB_DISABLE=1", "NCCL_P2P_LEVEL=4", "NCCL_PROTO=LL,LL128,Simple",
      ...spec.requiredEnvironment,
      "LOCAL_INFERENCE_CACHE_FINGERPRINT=cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc",
      "VLLM_CACHE_DIR=/cache/jit/cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc/vllm",
      "B12X_COMPILE_CACHE_DIR=/cache/jit/cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc/b12x/compile",
      "MINFER_FMHA_CACHE_DIR=/cache/jit/cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc/minfer/fmha-sm120",
    ]) assert.ok(environment.includes(required), `${runtimeProfile}: ${required}`);
    assert.equal(environment.some((entry) => entry.includes("=/var/cache/pixel-model/")), false, `${runtimeProfile}: no path-bound cache relocation`);
    for (const legacyOnly of [
      "NCCL_P2P_DISABLE=1", "NCCL_P2P_LEVEL=SYS", "NCCL_CUMEM_ENABLE=0", "NCCL_CUMEM_HOST_ENABLE=0",
      "NCCL_ALGO=Ring", "NCCL_PROTO=LL", "NCCL_NTHREADS=512", "NCCL_MIN_NCHANNELS=8",
    ]) assert.equal(environment.includes(legacyOnly), false, `${runtimeProfile}: ${legacyOnly}`);
    assert.equal(environment.includes("VLLM_USE_B12X_DCP_A2A=1"), spec.dcpBackend === "a2a");
    assert.equal(serverArgs.includes("--speculative-config"), spec.speculativeConfig !== null);
  });
});

test("qualified GLM 5.3 profiles fail closed on identity, geometry, or argument-surface drift", async () => {
  for (const runtimeProfile of Object.keys(glm53ProfileSpecs)) {
    const value = await glm53VllmFixture(runtimeProfile);
    assert.doesNotThrow(() => buildModelBackendLaunch(value), runtimeProfile);
  }
  const hostileMutations = [
    (value) => { value.configuration.providerOptions.runtimeProfile = "unknown-profile"; },
    (value) => { value.configuration.providerOptions.runtimeProfile = "glm53-flash-fast-v1;--cpu-offload-gb=16"; },
    (value) => { value.configuration.providerOptions.extraArguments = ["--cpu-offload-gb", "16"]; },
    (value) => { value.configuration.providerOptions.cpuOffloadGb = 16; },
    (value) => { value.policy.localModel.imageRef = glm53ProfileSpecs["glm53-flash-fast-v1"].imageRef; },
    (value) => { value.policy.localModel.imageDigest = `sha256:${digest("0")}`; value.configuration.runtimeCacheSeed.imageDigest = value.policy.localModel.imageDigest; },
    (value) => { value.policy.localModel.id = "GLM-5.3-Flash-lookalike"; },
    (value) => { value.policy.localModel.modelArtifactSha256 = digest("0"); value.artifactManifest.artifactSha256 = digest("0"); },
    (value) => { value.policy.localModel.contextWindow -= 1; },
    (value) => { value.configuration.providerOptions.imageEntrypoint = "/opt/venv/bin/python"; },
    (value) => { value.configuration.providerOptions.tensorParallelSize = 1; value.configuration.accelerator.count = 1; },
    (value) => { value.configuration.providerOptions.maxSequences = 3; },
    (value) => { value.configuration.providerOptions.gpuMemoryUtilizationPermille = 979; },
    (value) => { value.configuration.providerOptions.dtype = "auto"; },
    (value) => { value.configuration.providerOptions.enforceEager = true; },
    (value) => { value.configuration.providerOptions.reasoningParser = "deepseek_v4"; },
    (value) => { value.configuration.providerOptions.toolCallParser = null; },
    (value) => { value.configuration.runtimeCacheSeed.cacheFingerprint = "wrong-stack"; },
    (value) => { value.configuration.runtimeCacheSeed.subtrees = ["triton", "vllm"]; },
    (value) => { value.configuration.accelerator.class = "cpu"; value.configuration.accelerator.count = 0; value.configuration.providerOptions.tensorParallelSize = 1; },
  ];
  for (const mutate of hostileMutations) {
    const hostile = await glm53VllmFixture("glm53-flash-parallel-v1");
    mutate(hostile);
    assert.throws(() => buildModelBackendLaunch(hostile), undefined, mutate.toString());
  }
});

test("launch rendering rejects policy, artifact, accelerator, provider, path, and parallelism drift", async () => {
  const baseline = await fixture();
  for (const mutate of [
    (value) => { value.artifactManifest.artifactSha256 = digest("0"); value.artifactManifest.files[0].sha256 = digest("0"); },
    (value) => { value.configuration.providerOptions.provider = "vllm"; },
    (value) => { value.configuration.accelerator.class = "nvidia-cuda"; value.configuration.accelerator.count = 1; },
    (value) => { value.configuration.modelSource.kind = "directory"; },
    (value) => { value.configuration.modelSource.path = "/srv/pixel/models/model,escape.gguf"; },
    (value) => { value.configuration.backendNetwork.subnet = value.environment.runtime.networkSubnet; },
    (value) => { value.configuration.providerOptions.gpuLayers = 1; },
  ]) {
    const hostile = structuredClone(baseline); mutate(hostile);
    assert.throws(() => buildModelBackendLaunch(hostile), undefined, mutate.toString());
  }
  const vllm = structuredClone(baseline);
  vllm.policy.localModel.provider = "vllm"; vllm.policy.localModel.imageRef = `local/vllm@${vllm.policy.localModel.imageDigest}`;
  vllm.policy.localModel.acceleratorClass = "nvidia-cuda"; vllm.configuration.modelSource = { kind: "directory", path: "/models/vllm" };
  vllm.policy.localModel.inference = fixtureVllmInferencePolicy();
  vllm.configuration.containerUser = { uid: 2000, gid: 0 };
  vllm.configuration.accelerator = { class: "nvidia-cuda", count: 2, deviceIds: [] };
  vllm.configuration.resources.sharedMemoryMiB = 2048;
  vllm.configuration.resources.cacheMiB = 512;
  vllm.configuration.providerOptions = { provider: "vllm", imageEntrypoint: "/usr/local/bin/vllm", tensorParallelSize: 1, maxSequences: 16, gpuMemoryUtilizationPermille: 900, dtype: "auto", enforceEager: false, reasoningParser: null, toolCallParser: null };
  vllm.artifactManifest.kind = "directory";
  const files = [{ relativePath: "model.bin", bytes: 42, sha256: digest("1") }];
  const artifactSha256 = createHash("sha256").update(canonical({ schemaVersion: 1, kind: "directory", files })).digest("hex");
  vllm.artifactManifest.files = files; vllm.artifactManifest.artifactSha256 = artifactSha256; vllm.policy.localModel.modelArtifactSha256 = artifactSha256;
  vllm.configuration.providerOptions.tensorParallelSize = 2;
  assert.throws(() => buildModelBackendLaunch(vllm), /compile cache/u);
  vllm.configuration.resources.cacheMiB = 2048;
  vllm.configuration.providerOptions.tensorParallelSize = 1;
  assert.throws(() => buildModelBackendLaunch(vllm), /tensor parallelism/u);
});

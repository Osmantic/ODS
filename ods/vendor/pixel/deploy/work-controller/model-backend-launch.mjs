import { createHash } from "node:crypto";
import { posix } from "node:path";

import {
  canonical, validateWorkGoalControllerEnvironment, validateWorkModelArtifactManifest,
  validateWorkModelBackendConfig, validateWorkModelBackendLaunch, validateWorkModelRuntimeCacheManifest, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels } from "../work-runner/docker-boundary.mjs";

const PRIVATE_ENV = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" });
const DEFAULT_RUNTIME_CACHE_ROOT = "/var/cache/pixel-model";
const GLM53_RUNTIME_CACHE_ROOT = "/cache";
const COMMON_CONTAINER_ENV = Object.freeze([
  "HOME=/nonexistent", "USER=pixel-model", "LOGNAME=pixel-model", "LANG=C.UTF-8", "TZ=UTC", "TMPDIR=/tmp",
  "XDG_CACHE_HOME=/var/cache/pixel-model/xdg-cache", "XDG_CONFIG_HOME=/var/cache/pixel-model/xdg-config",
  "HF_HOME=/var/cache/pixel-model/huggingface", "HF_HUB_OFFLINE=1", "HF_DATASETS_OFFLINE=1", "TRANSFORMERS_OFFLINE=1",
  "DO_NOT_TRACK=1", "NO_PROXY=*", "no_proxy=*", "HTTP_PROXY=", "HTTPS_PROXY=", "ALL_PROXY=", "http_proxy=", "https_proxy=", "all_proxy=",
]);
const VLLM_CACHE_ENV = Object.freeze([
  "VLLM_CACHE_ROOT=/var/cache/pixel-model/vllm", "VLLM_CONFIG_ROOT=/var/cache/pixel-model/vllm-config",
  "VLLM_CACHE_DIR=/var/cache/pixel-model/vllm", "TRITON_CACHE_DIR=/var/cache/pixel-model/triton",
  "TORCHINDUCTOR_CACHE_DIR=/var/cache/pixel-model/torchinductor", "TORCH_EXTENSIONS_DIR=/var/cache/pixel-model/torch-extensions",
  "FLASHINFER_WORKSPACE_BASE=/var/cache/pixel-model/flashinfer", "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR=/var/cache/pixel-model/flashinfer-autotune",
  "TVM_FFI_CACHE_DIR=/var/cache/pixel-model/tvm-ffi", "TVM_CACHE_DIR=/var/cache/pixel-model/tvm",
  "TILELANG_CACHE_DIR=/var/cache/pixel-model/tilelang", "TILELANG_TMP_DIR=/var/cache/pixel-model/tilelang/tmp",
  "CUTE_DSL_CACHE_DIR=/var/cache/pixel-model/cute-dsl", "B12X_CUTE_COMPILE_CACHE_DIR=/var/cache/pixel-model/b12x-cute",
  "SPARKINFER_COMPILE_CACHE_DIR=/var/cache/pixel-model/sparkinfer/compile", "DG_JIT_CACHE_DIR=/var/cache/pixel-model/deep-gemm",
  "MM_SPARSE_ATTN_AOT_CACHE=/var/cache/pixel-model/minfer/mm-sparse-attn", "MINFER_FMHA_CACHE_DIR=/var/cache/pixel-model/minfer/fmha-sm100",
  "CUDA_CACHE_PATH=/var/cache/pixel-model/cuda", "CUPY_CACHE_DIR=/var/cache/pixel-model/cupy", "NUMBA_CACHE_DIR=/var/cache/pixel-model/numba",
  "VLLM_NO_USAGE_STATS=1",
]);
const GLM53_CACHE_FINGERPRINT = "cu133-torch213-vllm174c789e09-b12x12c426322c-lmcachee045d729bc";
const GLM53_JIT_CACHE_ROOT = `${GLM53_RUNTIME_CACHE_ROOT}/jit/${GLM53_CACHE_FINGERPRINT}`;
const GLM53_CACHE_SUBTREES = Object.freeze(["jit", "triton", "vllm"]);
const GLM53_CACHE_ENV = Object.freeze([
  `LOCAL_INFERENCE_CACHE_FINGERPRINT=${GLM53_CACHE_FINGERPRINT}`,
  `VLLM_CACHE_ROOT=${GLM53_RUNTIME_CACHE_ROOT}/vllm`, `VLLM_CONFIG_ROOT=${GLM53_RUNTIME_CACHE_ROOT}/vllm-config`,
  `VLLM_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/vllm`, `TRITON_CACHE_DIR=${GLM53_RUNTIME_CACHE_ROOT}/triton`,
  `TORCHINDUCTOR_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/torchinductor`, `TORCH_EXTENSIONS_DIR=${GLM53_JIT_CACHE_ROOT}/torch-extensions`,
  `FLASHINFER_WORKSPACE_BASE=${GLM53_JIT_CACHE_ROOT}/flashinfer`, `VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/flashinfer-autotune`,
  `TVM_FFI_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/tvm-ffi`, `TVM_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/tvm`,
  `TILELANG_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/tilelang`, `TILELANG_TMP_DIR=${GLM53_JIT_CACHE_ROOT}/tilelang/tmp`,
  `CUTE_DSL_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/cute-dsl`, `B12X_CUTE_COMPILE_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/b12x/cute`,
  `B12X_COMPILE_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/b12x/compile`, `SPARKINFER_COMPILE_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/b12x/compile`,
  `DG_JIT_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/deep-gemm`, `MM_SPARSE_ATTN_AOT_CACHE=${GLM53_JIT_CACHE_ROOT}/minfer/mm-sparse-attn`,
  `MINFER_FMHA_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/minfer/fmha-sm120`, `CUDA_CACHE_PATH=${GLM53_JIT_CACHE_ROOT}/cuda`,
  `CUPY_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/cupy`, `NUMBA_CACHE_DIR=${GLM53_JIT_CACHE_ROOT}/numba`,
  `VLLM_EXL3_ONLINE_CACHE_DIR=${GLM53_RUNTIME_CACHE_ROOT}/exl3-online`, "VLLM_EXL3_ONLINE_CACHE_MODE=readwrite", "VLLM_NO_USAGE_STATS=1",
]);
const VLLM_CONTAINER_ENV = Object.freeze([
  // The DSV4 runtime needs an explicit loopback control-plane address inside
  // an internal Docker network.  Leaving vLLM to discover an address on a
  // route-less network resolves to 0.0.0.0 and can strand tensor-parallel
  // workers at NCCL initialization.  These NCCL settings mirror the qualified
  // two-GPU product runtime while retaining Pixel's private bridge, private
  // IPC namespace, dropped capabilities, and non-root container identity.
  "VLLM_HOST_IP=127.0.0.1", "NCCL_SOCKET_IFNAME=eth0", "NCCL_IB_DISABLE=1",
  "NCCL_P2P_DISABLE=1", "NCCL_P2P_LEVEL=SYS", "NCCL_CUMEM_ENABLE=0", "NCCL_CUMEM_HOST_ENABLE=0",
  "NCCL_ALGO=Ring", "NCCL_PROTO=LL", "NCCL_NTHREADS=512", "NCCL_MIN_NCHANNELS=8",
  ...VLLM_CACHE_ENV,
]);
const GLM53_V75_IMAGE = "verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-v75@sha256:4605c420cc589be9fd15fc759c7f7c2a6035dab48f885c9466eb2233527bca64";
const GLM53_V84_IMAGE = "verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692";
const GLM53_V84_ENV = Object.freeze([
  "VLLM_ENGINE_READY_TIMEOUT_S=3600",
  "VLLM_B12X_GLM_NOPE_NVFP4=1", "VLLM_NVFP4_MLA_DYNAMIC_SCALE=0",
  "VLLM_NVFP4_MLA_SCALES_FILE=/opt/glm53/calibration/glm53_nvfp4_mla_outer_scales_mtp_power2_v2.json",
  "VLLM_EXL3_PREFILL_BLOCK_M=128", "VLLM_EXL3_PREFILL_TRELLIS=1", "B12X_GL53_ROUTE128_WIDE=1",
  "B12X_GL53_ROUTE128_HYBRID_TAIL=1", "VLLM_ENABLE_PCIE_ALLREDUCE=1", "VLLM_PCIE_ALLREDUCE_BACKEND=cpp",
  "KV_FP8_ROPE=0", "OMP_NUM_THREADS=2", "NCCL_IB_DISABLE=1", "NCCL_P2P_LEVEL=4", "NCCL_PROTO=LL,LL128,Simple",
]);
const GLM53_PROFILE_BASE = Object.freeze({
  imageEntrypoint: "/opt/venv/bin/vllm", tensorParallelSize: 2, dtype: "bfloat16", enforceEager: false,
  reasoningParser: "glm45", toolCallParser: "glm47", acceleratorClass: "nvidia-cuda", acceleratorCount: 2,
});
const VLLM_RUNTIME_PROFILES = Object.freeze({
  "glm53-flash-fast-v1": Object.freeze({
    ...GLM53_PROFILE_BASE, imageRef: GLM53_V75_IMAGE, imageDigest: "sha256:4605c420cc589be9fd15fc759c7f7c2a6035dab48f885c9466eb2233527bca64",
    modelId: "GLM-5.3-Flash-TR3-4bpw-FP8KV", contextWindow: 262144, maxSequences: 1, gpuMemoryUtilizationPermille: 986,
    runtimeCacheRoot: GLM53_RUNTIME_CACHE_ROOT,
    environment: Object.freeze([
      "VLLM_HOST_IP=127.0.0.1", "NCCL_SOCKET_IFNAME=eth0", "VLLM_ENGINE_READY_TIMEOUT_S=3600", "VLLM_B12X_GLM_NOPE_NVFP4=1",
      "VLLM_USE_B12X_DCP_A2A=1", "VLLM_B12X_MLA_CKV_GATHER=0", "OMP_NUM_THREADS=2", "NCCL_IB_DISABLE=1",
      "NCCL_P2P_LEVEL=4", "NCCL_PROTO=LL,LL128,Simple", ...GLM53_CACHE_ENV,
    ]),
    attentionBackend: "FLASHINFER_MLA_SPARSE_SM120", kvCacheDtype: "fp8_ds_mla", dcpSize: 2, dcpBackend: "a2a",
    compilationConfig: null, speculativeConfig: "{\"method\":\"mtp\",\"num_speculative_tokens\":3,\"draft_sample_method\":\"probabilistic\"}",
  }),
  "glm53-flash-long-v1": Object.freeze({
    ...GLM53_PROFILE_BASE, imageRef: GLM53_V84_IMAGE, imageDigest: "sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    modelId: "GLM-5.3-Flash-TR3-4bpw-v84-NVFP4KV", contextWindow: 1048576, maxSequences: 1, gpuMemoryUtilizationPermille: 986,
    runtimeCacheRoot: GLM53_RUNTIME_CACHE_ROOT,
    environment: Object.freeze(["VLLM_HOST_IP=127.0.0.1", "NCCL_SOCKET_IFNAME=eth0", "VLLM_USE_B12X_DCP_A2A=1", ...GLM53_V84_ENV, ...GLM53_CACHE_ENV]),
    attentionBackend: "B12X_MLA_SPARSE", kvCacheDtype: "nvfp4_ds_mla", dcpSize: 2, dcpBackend: "a2a",
    compilationConfig: "{\"cudagraph_capture_sizes\":[1]}", speculativeConfig: null,
  }),
  "glm53-flash-parallel-v1": Object.freeze({
    ...GLM53_PROFILE_BASE, imageRef: GLM53_V84_IMAGE, imageDigest: "sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692",
    modelId: "GLM-5.3-Flash-TR3-4bpw-v84-NVFP4KV", contextWindow: 524288, maxSequences: 4, gpuMemoryUtilizationPermille: 980,
    runtimeCacheRoot: GLM53_RUNTIME_CACHE_ROOT,
    environment: Object.freeze(["VLLM_HOST_IP=127.0.0.1", "NCCL_SOCKET_IFNAME=eth0", ...GLM53_V84_ENV, ...GLM53_CACHE_ENV]),
    attentionBackend: "B12X_MLA_SPARSE", kvCacheDtype: "nvfp4_ds_mla", dcpSize: 1, dcpBackend: null,
    compilationConfig: "{\"cudagraph_capture_sizes\":[1, 2, 4]}", speculativeConfig: null,
  }),
});
export const VLLM_CACHE_SEED_BOOTSTRAP = runtimeCacheBootstrap(DEFAULT_RUNTIME_CACHE_ROOT);
export const VLLM_CACHE_SEED_BOOTSTRAP_NAME = "pixel-vllm-cache-bootstrap";
const authority = Object.freeze({ grantsExecution: false, createsNetwork: false, startsContainer: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Owner-private inert exact Docker argument vectors for a contained local-model backend. Possession or rendering grants no execution, network or container creation, image pull, device, credential, model qualification, policy mutation, external-effect, or completion authority; a separate exact-confirmed lifecycle must revalidate every binding and artifact before mutation.";

export class WorkModelBackendLaunchError extends Error {}
function fail(message) { throw new WorkModelBackendLaunchError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function ipv4Number(value, label) {
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/u.test(value)) fail(`${label} is not canonical IPv4`);
  const parts = value.split(".").map(Number);
  if (parts.some((part) => part > 255) || !(parts[0] === 10 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31 || parts[0] === 192 && parts[1] === 168)) fail(`${label} is not private IPv4`);
  return parts.reduce((total, part) => ((total << 8) | part) >>> 0, 0);
}

function subnet(value, label) {
  if (typeof value !== "string" || value.split("/").length !== 2) fail(`${label} is invalid`);
  const [address, prefixText] = value.split("/"), prefix = Number(prefixText), base = ipv4Number(address, label);
  if (!Number.isSafeInteger(prefix) || prefix < 24 || prefix > 30) fail(`${label} prefix is outside the isolated boundary`);
  const mask = (0xffffffff << (32 - prefix)) >>> 0, network = (base & mask) >>> 0, broadcast = (network | (~mask >>> 0)) >>> 0;
  if (base !== network) fail(`${label} is not network-aligned`);
  return { network, broadcast };
}

function exactNetworks(config, environment) {
  const backend = subnet(config.backendNetwork.subnet, "model backend subnet");
  const jobs = subnet(environment.runtime.networkSubnet, "worker network subnet");
  if (!(backend.broadcast < jobs.network || jobs.broadcast < backend.network)) fail("model backend and worker network subnets overlap");
}

function safeLinuxPath(value, label) {
  if (typeof value !== "string" || !posix.isAbsolute(value) || posix.normalize(value) !== value || value.includes("//") || !/^\/[A-Za-z0-9._+@=:/-]+$/u.test(value)) fail(`${label} must be a canonical delimiter-safe Linux path`);
  return value;
}

function selectedVllmRuntimeProfile(options) {
  return options.provider === "vllm" && options.runtimeProfile !== undefined ? VLLM_RUNTIME_PROFILES[options.runtimeProfile] : null;
}

export function modelBackendRuntimeCacheRoot(configuration) {
  if (configuration?.providerOptions?.provider !== "vllm") return DEFAULT_RUNTIME_CACHE_ROOT;
  return selectedVllmRuntimeProfile(configuration.providerOptions)?.runtimeCacheRoot ?? DEFAULT_RUNTIME_CACHE_ROOT;
}

function runtimeCacheBootstrap(root) {
  if (![DEFAULT_RUNTIME_CACHE_ROOT, GLM53_RUNTIME_CACHE_ROOT].includes(root)) fail("vLLM runtime cache root is outside the qualified boundary");
  return [
    "set -eu",
    "test -x \"$PIXEL_VLLM_SERVER_ENTRYPOINT\"",
    `cp -R /var/cache/pixel-model-seed/. ${root}/`,
    `chmod -R u+rwX ${root}`,
    "exec \"$PIXEL_VLLM_SERVER_ENTRYPOINT\" \"$@\"",
  ].join("; ");
}

function commonContainerEnvironment(root) {
  return COMMON_CONTAINER_ENV.map((value) => value.replaceAll(DEFAULT_RUNTIME_CACHE_ROOT, root));
}

function exactVllmRuntimeProfile(local, options, accelerator, runtimeCacheSeed) {
  const profile = selectedVllmRuntimeProfile(options);
  if (profile === null) return;
  const expectedOptions = {
    imageEntrypoint: profile.imageEntrypoint, tensorParallelSize: profile.tensorParallelSize,
    maxSequences: profile.maxSequences, gpuMemoryUtilizationPermille: profile.gpuMemoryUtilizationPermille,
    dtype: profile.dtype, enforceEager: profile.enforceEager, reasoningParser: profile.reasoningParser,
    toolCallParser: profile.toolCallParser,
  };
  for (const [name, expected] of Object.entries(expectedOptions)) {
    if (options[name] !== expected) fail(`vLLM runtime profile ${options.runtimeProfile} requires exact ${name}`);
  }
  if (local.imageRef !== profile.imageRef || local.imageDigest !== profile.imageDigest) fail(`vLLM runtime profile ${options.runtimeProfile} requires its qualified image`);
  if (local.id !== profile.modelId || local.contextWindow !== profile.contextWindow) fail(`vLLM runtime profile ${options.runtimeProfile} requires its qualified model identity`);
  if (accelerator.class !== profile.acceleratorClass || accelerator.count !== profile.acceleratorCount || accelerator.deviceIds.length !== 0) fail(`vLLM runtime profile ${options.runtimeProfile} requires its qualified accelerator geometry`);
  if (runtimeCacheSeed?.cacheFingerprint !== GLM53_CACHE_FINGERPRINT || canonical([...runtimeCacheSeed.subtrees].sort()) !== canonical([...GLM53_CACHE_SUBTREES].sort())) fail(`vLLM runtime profile ${options.runtimeProfile} requires its qualified cache layout`);
}

function exactConfig(config, policy) {
  const errors = validateWorkModelBackendConfig(config);
  if (errors.length) fail(`private model backend configuration is invalid: ${errors[0]}`);
  const local = policy.localModel, options = config.providerOptions, accelerator = config.accelerator;
  if (options.provider !== local.provider) fail("model backend provider differs from private policy");
  if (accelerator.class !== local.acceleratorClass) fail("model backend accelerator differs from private policy");
  if (local.provider === "llama.cpp" && config.modelSource.kind !== "file") fail("llama.cpp requires one measured model file");
  if (local.provider === "vllm" && config.modelSource.kind !== "directory") fail("vLLM requires one measured materialized model directory");
  if (accelerator.class === "cpu") {
    if (accelerator.count !== 0 || accelerator.deviceIds.length !== 0) fail("CPU backend cannot request an accelerator device");
    if (options.provider === "llama.cpp" && options.gpuLayers !== 0) fail("CPU llama.cpp backend cannot offload GPU layers");
    if (options.provider === "vllm" && options.tensorParallelSize !== 1) fail("CPU vLLM backend cannot request multi-GPU tensor parallelism");
  } else {
    if ((accelerator.count === 0) === (accelerator.deviceIds.length === 0)) fail("NVIDIA backend must use exactly one count or device-ID grant");
    if (accelerator.deviceIds.length !== 0) fail("guided launch rendering currently requires a bounded NVIDIA count; existing exact device-ID backends remain inspectable");
    if (options.provider === "llama.cpp" && options.gpuLayers === 0) fail("NVIDIA llama.cpp backend must request at least one GPU layer");
    if (options.provider === "vllm" && options.tensorParallelSize !== accelerator.count) fail("vLLM tensor parallelism must equal the exact NVIDIA device count");
  }
  if (options.provider === "vllm" && config.resources.sharedMemoryMiB < 1024) fail("vLLM requires at least 1 GiB of private shared memory");
  if (options.provider === "vllm" && config.resources.cacheMiB < 1024) fail("vLLM requires at least 1 GiB of private compile cache");
  if (options.provider === "llama.cpp" && config.runtimeCacheSeed !== null) fail("llama.cpp cannot mount a vLLM runtime cache seed");
  if (options.provider === "vllm") {
    if (config.runtimeCacheSeed === null) fail("vLLM requires one measured image-bound runtime cache seed");
    if (config.runtimeCacheSeed.imageDigest !== local.imageDigest) fail("runtime cache seed image differs from private policy");
    safeLinuxPath(options.imageEntrypoint, "vLLM image entrypoint");
    exactVllmRuntimeProfile(local, options, accelerator, config.runtimeCacheSeed);
  }
  return true;
}

function exactRuntimeCache(configuration, policy, manifest) {
  const seed = configuration.runtimeCacheSeed;
  if (seed === null) {
    if (manifest !== null) fail("runtime cache manifest is present without a configured seed");
    return [];
  }
  const errors = validateWorkModelRuntimeCacheManifest(manifest);
  if (errors.length) fail(`private model runtime cache manifest is invalid: ${errors[0]}`);
  if (manifest.artifactSha256 !== seed.artifactSha256) fail("measured runtime cache differs from private configuration");
  if (seed.imageDigest !== policy.localModel.imageDigest) fail("runtime cache seed is not bound to the pinned backend image");
  const root = safeLinuxPath(seed.path, "runtime cache seed path");
  const model = safeLinuxPath(configuration.modelSource.path, "model source path");
  if (root === model || root.startsWith(`${model}/`) || model.startsWith(`${root}/`)) fail("runtime cache seed and model source overlap");
  const subtrees = [...seed.subtrees].sort();
  const observed = new Set();
  for (const file of manifest.files) {
    const subtree = file.relativePath.split("/")[0];
    if (!subtrees.includes(subtree)) fail("runtime cache manifest contains bytes outside its declared subtrees");
    observed.add(subtree);
  }
  if (subtrees.some((subtree) => !observed.has(subtree))) fail("runtime cache manifest omits a declared subtree");
  return [{ source: root, target: "/var/cache/pixel-model-seed" }];
}

function gpuArguments(accelerator) {
  if (accelerator.class === "cpu") return [];
  return ["--gpus", `driver=nvidia,count=${accelerator.count}`];
}

function serverArguments(policy, config, target) {
  const options = config.providerOptions;
  if (options.provider === "llama.cpp") return [
    "--model", target, "--host", "0.0.0.0", "--port", "8080", "--alias", policy.localModel.id,
    "--ctx-size", String(policy.localModel.contextWindow), "--threads", String(options.threads),
    "--parallel", String(options.parallel), "--n-gpu-layers", String(options.gpuLayers),
    "--flash-attn", options.flashAttention ? "on" : "off",
  ];
  const profile = selectedVllmRuntimeProfile(options);
  if (profile !== null) {
    const args = [
      "serve", target, "--served-model-name", policy.localModel.id, "--host", "0.0.0.0", "--port", "8080",
      "--language-model-only", "--tensor-parallel-size", String(profile.tensorParallelSize), "--enable-expert-parallel",
      "--decode-context-parallel-size", String(profile.dcpSize),
    ];
    if (profile.dcpBackend !== null) args.push("--dcp-comm-backend", profile.dcpBackend);
    args.push(
      "--dtype", profile.dtype, "--load-format", "safetensors", "--moe-backend", "b12x",
      "--attention-backend", profile.attentionBackend, "--kv-cache-dtype", profile.kvCacheDtype,
      "--max-model-len", String(profile.contextWindow), "--max-num-batched-tokens", "2048",
      "--max-num-seqs", String(profile.maxSequences), "--gpu-memory-utilization", (profile.gpuMemoryUtilizationPermille / 1000).toFixed(3),
      "--cpu-offload-gb", "0",
    );
    if (profile.compilationConfig !== null) args.push("--compilation-config", profile.compilationConfig);
    args.push(
      "--enable-chunked-prefill", "--no-enable-prefix-caching", "--generation-config", target,
      "--reasoning-parser", profile.reasoningParser, "--enable-auto-tool-choice", "--tool-call-parser", profile.toolCallParser,
      "--disable-custom-all-reduce",
    );
    if (profile.speculativeConfig !== null) args.push("--speculative-config", profile.speculativeConfig);
    args.push("--no-enable-log-requests", "--no-enable-log-outputs", "--no-enable-log-deltas", "--disable-uvicorn-access-log");
    return args;
  }
  const args = [
    target, "--served-model-name", policy.localModel.id, "--host", "0.0.0.0", "--port", "8080",
    "--max-model-len", String(policy.localModel.contextWindow), "--tensor-parallel-size", String(options.tensorParallelSize),
    "--max-num-seqs", String(options.maxSequences),
    "--gpu-memory-utilization", (options.gpuMemoryUtilizationPermille / 1000).toFixed(3), "--dtype", options.dtype,
    "--no-enable-log-requests", "--no-enable-log-outputs", "--no-enable-log-deltas", "--disable-uvicorn-access-log",
  ];
  if (options.enforceEager) args.push("--enforce-eager");
  if (options.reasoningParser !== null) args.push("--reasoning-parser", options.reasoningParser);
  if (options.toolCallParser !== null) args.push("--enable-auto-tool-choice", "--tool-call-parser", options.toolCallParser);
  return args;
}

function vllmContainerEnvironment(options) {
  return selectedVllmRuntimeProfile(options)?.environment ?? VLLM_CONTAINER_ENV;
}

export function modelBackendServerArgumentsFromLaunch({ launch, policy }) {
  const args = launch?.container?.args;
  const image = policy?.localModel?.imageRef;
  const provider = policy?.localModel?.provider;
  if (!Array.isArray(args) || typeof image !== "string" || !image || !["llama.cpp", "vllm"].includes(provider)) {
    fail("model backend launch does not contain a recognized provider and exact image");
  }
  const imageIndexes = args.flatMap((value, index) => value === image ? [index] : []);
  if (imageIndexes.length !== 1) fail("model backend launch does not contain exactly one selected image");
  const imageIndex = imageIndexes[0];
  if (provider === "vllm") {
    const wrappers = [DEFAULT_RUNTIME_CACHE_ROOT, GLM53_RUNTIME_CACHE_ROOT].map((root) => [
      "--entrypoint", "/bin/sh", image, "-c", runtimeCacheBootstrap(root), VLLM_CACHE_SEED_BOOTSTRAP_NAME,
    ]);
    const markerIndexes = args.flatMap((value, index) => value === VLLM_CACHE_SEED_BOOTSTRAP_NAME ? [index] : []);
    const entrypointIndexes = args.flatMap((value, index) => value === "--entrypoint" ? [index] : []);
    if (
      imageIndex < 2 || markerIndexes.length !== 1 || entrypointIndexes.length !== 1
      || !wrappers.some((wrapper) => canonical(args.slice(imageIndex - 2, imageIndex + 4)) === canonical(wrapper))
    ) fail("vLLM model backend launch does not contain the exact cache-seed bootstrap wrapper");
    const serverArgs = args.slice(imageIndex + 4);
    if (serverArgs.length < 1) fail("vLLM model backend launch does not contain server arguments");
    return serverArgs;
  }
  const serverArgs = args.slice(imageIndex + 1);
  if (serverArgs.length < 1) fail("llama.cpp model backend launch does not contain server arguments");
  return serverArgs;
}

export function buildModelBackendStaticIdentity({ configuration, environment, policy }) {
  const environmentErrors = validateWorkGoalControllerEnvironment(environment), policyErrors = validateWorkPolicy(policy);
  if (environmentErrors.length) fail(`private controller environment is invalid: ${environmentErrors[0]}`);
  if (policyErrors.length) fail(`private work policy is invalid: ${policyErrors[0]}`);
  exactConfig(configuration, policy); exactNetworks(configuration, environment);
  const dockerPath = safeLinuxPath(environment.runtime.dockerPath, "Docker client path");
  const source = safeLinuxPath(configuration.modelSource.path, "model source path");
  const target = policy.localModel.provider === "llama.cpp" ? "/models/model.gguf" : "/models/model";
  const bindings = {
    policySha256: sha(policy), environmentSha256: sha(environment), configurationSha256: sha(configuration),
    artifactSha256: policy.localModel.modelArtifactSha256,
  };
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-backend-static-identity", bindings,
    dockerPath, source, target, backendContainerName: environment.runtime.backendContainerName,
    backendNetworkName: environment.runtime.backendNetworkName, imageRef: policy.localModel.imageRef,
  });
}

export function buildModelBackendLaunch({ configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest = null }) {
  const artifactErrors = validateWorkModelArtifactManifest(artifactManifest);
  if (artifactErrors.length) fail(`private model artifact manifest is invalid: ${artifactErrors[0]}`);
  const identity = buildModelBackendStaticIdentity({ configuration, environment, policy });
  if (artifactManifest.kind !== configuration.modelSource.kind || artifactManifest.artifactSha256 !== policy.localModel.modelArtifactSha256) fail("measured model artifact differs from private policy");
  const runtimeCacheMounts = exactRuntimeCache(configuration, policy, runtimeCacheArtifactManifest);
  const runtimeCacheRoot = modelBackendRuntimeCacheRoot(configuration);
  const { dockerPath, source, target, bindings } = identity;
  const labels = modelBackendIdentityLabels({ policy, bindings });
  const networkLabels = modelBackendNetworkIdentityLabels({ policy, bindings });
  const networkArgs = [
    "network", "create", "--driver", "bridge", "--internal", "--attachable=false",
    "--subnet", configuration.backendNetwork.subnet,
    ...Object.entries(networkLabels).flatMap(([name, value]) => ["--label", `${name}=${value}`]),
    environment.runtime.backendNetworkName,
  ];
  const memory = `${configuration.resources.memoryMiB}m`;
  const containerArgs = [
    "container", "create", "--pull", "never", "--name", environment.runtime.backendContainerName,
    "--hostname", "pixel-local-model", "--init",
    ...Object.entries(labels).flatMap(([name, value]) => ["--label", `${name}=${value}`]),
    "--network", environment.runtime.backendNetworkName, "--network-alias", "pixel-local-model",
    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
    "--pids-limit", String(configuration.resources.pids), "--memory", memory, "--memory-swap", memory,
    "--cpus", String(configuration.resources.cpuCores), "--ulimit", `nofile=${configuration.resources.nofile}:${configuration.resources.nofile}`,
    "--ipc", "private", "--cgroupns", "private", "--shm-size", `${configuration.resources.sharedMemoryMiB}m`, "--stop-timeout", "30", "--stop-signal", "SIGTERM",
    "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=1", "--log-opt", "compress=false",
    "--restart", configuration.restartPolicy ?? "unless-stopped", "--user", `${configuration.containerUser.uid}:${configuration.containerUser.gid}`,
    "--tmpfs", `/tmp:rw,nosuid,nodev,noexec,size=${configuration.resources.tmpfsMiB}m,mode=1777`,
    "--tmpfs", `${runtimeCacheRoot}:rw,nosuid,nodev,${policy.localModel.provider === "vllm" ? "exec" : "noexec"},size=${configuration.resources.cacheMiB}m,mode=0700,uid=${configuration.containerUser.uid},gid=${configuration.containerUser.gid}`,
    ...[...commonContainerEnvironment(runtimeCacheRoot), ...(policy.localModel.provider === "vllm" ? [...vllmContainerEnvironment(configuration.providerOptions), `PIXEL_VLLM_SERVER_ENTRYPOINT=${configuration.providerOptions.imageEntrypoint}`] : [])].flatMap((value) => ["--env", value]),
    "--mount", `type=bind,src=${source},dst=${target},readonly`,
    ...runtimeCacheMounts.flatMap((mount) => ["--mount", `type=bind,src=${mount.source},dst=${mount.target},readonly`]),
    ...gpuArguments(configuration.accelerator),
  ];
  if (configuration.publishLoopbackPort !== null) containerArgs.push("--publish", `127.0.0.1:${configuration.publishLoopbackPort}:8080`);
  const serverArgs = serverArguments(policy, configuration, target);
  if (policy.localModel.provider === "vllm") {
    // The measured seed is executable input and therefore remains immutable.
    // Copy it into the bounded container-only tmpfs before starting vLLM so
    // Triton and other compilers can create legitimate temporary artifacts.
    // The writable copy disappears with the exact backend container.
    containerArgs.push("--entrypoint", "/bin/sh", policy.localModel.imageRef, "-c", runtimeCacheBootstrap(runtimeCacheRoot), VLLM_CACHE_SEED_BOOTSTRAP_NAME, ...serverArgs);
  } else containerArgs.push(policy.localModel.imageRef, ...serverArgs);
  const launch = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-launch-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-launch",
    bindings,
    artifactManifest: structuredClone(artifactManifest),
    runtimeCacheArtifactManifest: runtimeCacheArtifactManifest === null ? null : structuredClone(runtimeCacheArtifactManifest),
    network: { command: dockerPath, args: networkArgs, env: { ...PRIVATE_ENV } },
    container: { command: dockerPath, args: containerArgs, env: { ...PRIVATE_ENV } },
    authority: { ...authority }, boundary,
  };
  const errors = validateWorkModelBackendLaunch(launch);
  if (errors.length) fail(`private model backend launch is invalid: ${errors[0]}`);
  return Object.freeze(launch);
}

export function modelBackendLaunchSha256(launch) { return sha(launch); }

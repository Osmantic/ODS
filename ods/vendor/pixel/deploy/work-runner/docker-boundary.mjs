import { createHash } from "node:crypto";
import { posix } from "node:path";

import { validateScoutNetworkRuntime } from "./docker-scout.mjs";
import { profileAllowedTools } from "./profile-capability.mjs";

const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/;
const IMAGE_RE = /^[a-z0-9][a-z0-9._/:+-]{1,255}@sha256:[a-f0-9]{64}$/;
const IMAGE_ID_RE = /^sha256:[a-f0-9]{64}$/;
const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/;
const ID_RE = /^[a-f0-9]{64}$/;
const QUALIFICATION_RE = /^modelqual-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$/;
const BACKEND_LABEL_PREFIX = "com.osmantic.pixel.work-model-";
const BACKEND_RUNTIME_BINDING_LABELS = new Set([
  `${BACKEND_LABEL_PREFIX}policy-sha256`, `${BACKEND_LABEL_PREFIX}environment-sha256`, `${BACKEND_LABEL_PREFIX}configuration-sha256`,
]);
const EXPECTED_PROXY_ENV = new Set(["HOME=/nonexistent", "LANG=C.UTF-8", "NODE_ENV=production"]);
const PROFILE_OMP_TOOLS = Object.freeze({
  scout: Object.freeze(["glob", "grep", "read"]),
  builder: Object.freeze(["bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "read", "task", "todo", "write"]),
  "data-lab": Object.freeze(["bash", "edit", "glob", "grep", "read", "write"]),
  researcher: Object.freeze(["bash", "edit", "glob", "grep", "pixel_research", "read", "write"]),
});

export class DockerBoundaryError extends Error {}

function fail(message) {
  throw new DockerBoundaryError(message);
}

function safeName(value, label) {
  if (!NAME_RE.test(value ?? "")) fail(`${label} is invalid`);
  return value;
}

function safePath(value, label) {
  if (!posix.isAbsolute(value ?? "") || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value) {
    fail(`${label} is not a canonical safe Linux path`);
  }
  return value;
}

function positiveId(value, label) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647) fail(`${label} is invalid`);
  return value;
}

function imageIdentifier(prepared, runtime) {
  const value = runtime?.imageIdentifier ?? prepared.policy.runner.imageRef;
  if (value !== prepared.plan.isolation.runnerImageDigest && value !== prepared.policy.runner.imageRef) fail("runtime image differs from the leased runner image");
  if (!IMAGE_ID_RE.test(value) && !IMAGE_RE.test(value)) fail("runtime runner image identifier is invalid");
  return value;
}

function mount(source, target, readonly = true) {
  return `type=bind,src=${source},dst=${target}${readonly ? ",readonly" : ""}`;
}

function exactClaim(prepared, claim) {
  if (
    claim?.status !== "consumed"
    || claim.externalEffects !== false
    || claim.jobId !== prepared?.plan?.jobId
    || claim.leaseId !== prepared?.lease?.leaseId
    || claim.planSha256 !== prepared?.bindings?.planSha256
    || claim.leaseSha256 !== prepared?.bindings?.leaseSha256
    || claim.policySha256 !== prepared?.bindings?.policySha256
    || claim.inputSetSha256 !== prepared?.bindings?.inputSetSha256
    || claim.workspaceSha256 !== prepared?.workspace?.sha256
    || claim.runnerImageDigest !== prepared?.plan?.isolation?.runnerImageDigest
    || JSON.stringify(claim.executor) !== JSON.stringify(prepared?.plan?.executor)
    || JSON.stringify(claim.model) !== JSON.stringify(prepared?.plan?.model)
  ) fail("claim differs from the prepared execution boundary");
  const expectedModel = {
    route: "local-only",
    provider: prepared.policy.localModel.provider,
    id: prepared.policy.localModel.id,
    backendImageDigest: prepared.policy.localModel.imageDigest,
    contextWindow: prepared.policy.localModel.maxRequestContextTokens,
    supportsVision: prepared.policy.localModel.supportsVision,
    endpointContract: "llama.cpp-discovery-and-openai-stream-v1",
  };
  if (JSON.stringify(expectedModel) !== JSON.stringify(prepared.plan.model)) fail("model plan differs from private policy");
  if (prepared.policy.runner.imageRef !== prepared.plan.isolation.runnerImageDigest && !prepared.policy.runner.imageRef.endsWith(`@${prepared.plan.isolation.runnerImageDigest}`)) fail("runner image plan differs from private policy");
}

function runtimeShape(runtime) {
  try { validateScoutNetworkRuntime(runtime); } catch (error) { fail(error instanceof Error ? error.message : "Scout network runtime is invalid"); }
  const shape = {
    dockerPath: safePath(runtime?.dockerPath, "Docker client path"),
    networkName: safeName(runtime?.networkName, "job network name"),
    modelProxyName: safeName(runtime?.modelProxyName, "model proxy name"),
    modelAlias: safeName(runtime?.modelAlias, "model proxy alias"),
    backendNetworkName: safeName(runtime?.backendNetworkName, "model backend network name"),
    backendContainerName: safeName(runtime?.backendContainerName, "model backend container name"),
    backendAlias: safeName(runtime?.backendAlias, "model backend alias"),
    proxyCidFile: safePath(runtime?.proxyCidFile, "model proxy CID file"),
    proxyConfigPath: safePath(runtime?.proxyConfigPath, "model proxy config path"),
    proxyReceiptDirectory: safePath(runtime?.proxyReceiptDirectory, "model proxy receipt directory"),
    uid: positiveId(runtime?.uid, "model proxy UID"),
    gid: positiveId(runtime?.gid, "model proxy GID"),
  };
  if (shape.backendAlias !== "pixel-local-model" || shape.modelAlias !== "pixel-model") fail("model service aliases differ from the v1 contract");
  return shape;
}

function buildWorkNetworkCreate(claim, runtime, role) {
  const shape = runtimeShape(runtime);
  if (!claim?.claimId || !claim?.jobId) fail("network claim binding is invalid");
  return {
    command: shape.dockerPath,
    args: [
      "network", "create", "--driver", "bridge", "--internal", "--attachable=false",
      "--subnet", runtime.networkSubnet,
      "--label", `com.osmantic.pixel.work-claim=${claim.claimId}`,
      "--label", `com.osmantic.pixel.work-job=${claim.jobId}`,
      "--label", `com.osmantic.pixel.work-role=${role}`,
      shape.networkName,
    ],
  };
}

export function buildScoutNetworkCreate(claim, runtime) {
  return buildWorkNetworkCreate(claim, runtime, "scout-network");
}

export function buildBuilderNetworkCreate(claim, runtime) {
  return buildWorkNetworkCreate(claim, runtime, "builder-network");
}

export function buildDataLabNetworkCreate(claim, runtime) {
  return buildWorkNetworkCreate(claim, runtime, "data-lab-network");
}

export function buildResearcherNetworkCreate(claim, runtime) {
  return buildWorkNetworkCreate(claim, runtime, "researcher-network");
}

export function buildModelProxyConfig(prepared, claim, runtime) {
  exactClaim(prepared, claim);
  runtimeShape(runtime);
  const qualification = prepared.modelQualification;
  if (
    !qualification || typeof qualification !== "object" || Array.isArray(qualification)
    || JSON.stringify(Object.keys(qualification).sort()) !== JSON.stringify(["qualificationId", "receiptSha256", "casesSha256", "evaluatorSha256", "profile", "maxContextTokens", "maxOutputTokens", "exactUsage"].sort())
    || qualification.profile !== prepared.plan.profile || qualification.exactUsage !== true
    || !QUALIFICATION_RE.test(qualification.qualificationId ?? "")
    || !ID_RE.test(qualification.receiptSha256 ?? "") || !ID_RE.test(qualification.casesSha256 ?? "") || !ID_RE.test(qualification.evaluatorSha256 ?? "")
    || !Number.isSafeInteger(qualification.maxContextTokens) || qualification.maxContextTokens < prepared.plan.model.contextWindow
    || !Number.isSafeInteger(qualification.maxOutputTokens) || qualification.maxOutputTokens < prepared.policy.localModel.maxRequestOutputTokens
  ) fail("local model qualification admission differs from the prepared profile");
  const budgets = prepared.lease.budgets;
  const baseTools = PROFILE_OMP_TOOLS[prepared.plan.profile];
  if (!baseTools) fail("Deep Work profile has no model-proxy tool contract");
  const leasedTools = profileAllowedTools(baseTools, runtime.capability);
  const allowedTools = leasedTools.includes("task") ? [...leasedTools, "yield"] : leasedTools;
  const policyInference = prepared.policy.localModel.inference ?? null;
  if (prepared.plan.model.provider === "vllm" && (!policyInference || typeof policyInference !== "object" || Array.isArray(policyInference))) {
    fail("vLLM has no exact inference policy");
  }
  if (prepared.plan.model.provider !== "vllm" && policyInference !== null) fail("legacy local model cannot claim exact vLLM inference enforcement");
  return {
    schemaVersion: 1,
    jobId: claim.jobId,
    claimId: claim.claimId,
    planSha256: claim.planSha256,
    provider: prepared.plan.model.provider,
    modelId: prepared.plan.model.id,
    contextWindow: prepared.plan.model.contextWindow,
    supportsVision: prepared.plan.model.supportsVision,
    backendOrigin: `http://${runtime.backendAlias}:8080`,
    listenHost: "0.0.0.0",
    listenPort: 8080,
    allowedClientIpv4: runtime.workerIp,
    allowedTools: [...allowedTools],
    receiptPath: "/run/pixel-work-output/model-proxy-receipt.json",
    qualification: structuredClone(qualification),
    inference: policyInference === null ? null : {
      ...structuredClone(policyInference),
      stream: true,
      maxOutputTokens: prepared.policy.localModel.maxRequestOutputTokens,
      toolEncoding: "function",
    },
    budgets: {
      maxRuntimeSeconds: budgets.maxRuntimeSeconds,
      maxModelRequests: budgets.maxModelRequests,
      maxInputTokens: budgets.maxInputTokens,
      maxOutputTokens: budgets.maxOutputTokens,
      maxNetworkBytes: budgets.maxNetworkBytes,
      maxRequestBytes: Math.min(268435456, budgets.maxNetworkBytes),
      maxResponseBytes: Math.min(1073741824, budgets.maxNetworkBytes),
      maxRequestSeconds: Math.min(300, budgets.maxRuntimeSeconds),
    },
  };
}

export function buildModelProxyDockerCommand(prepared, claim, runtime) {
  exactClaim(prepared, claim);
  const shape = runtimeShape(runtime);
  const image = imageIdentifier(prepared, runtime);
  const memoryMiB = Math.min(512, Math.max(256, Math.floor(prepared.lease.budgets.maxMemoryMiB / 4)));
  return {
    command: shape.dockerPath,
    args: [
      "run", "--rm", "-d", "--pull", "never",
      "--name", shape.modelProxyName,
      "--hostname", "pixel-model-proxy",
      "--cidfile", shape.proxyCidFile,
      "--label", `com.osmantic.pixel.work-claim=${claim.claimId}`,
      "--label", `com.osmantic.pixel.work-job=${claim.jobId}`,
      "--label", "com.osmantic.pixel.work-role=model-proxy",
      "--network", shape.networkName,
      "--network-alias", shape.modelAlias,
      "--ip", runtime.proxyIp,
      "--read-only",
      "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges:true",
      "--pids-limit", "64",
      "--memory", `${memoryMiB}m`,
      "--memory-swap", `${memoryMiB}m`,
      "--cpus", "1",
      "--ulimit", "nofile=128:128",
      "--ipc", "none",
      "--cgroupns", "private",
      "--stop-timeout", "3",
      "--log-driver", "none",
      "--user", `${shape.uid}:${shape.gid}`,
      "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
      "--env", "HOME=/nonexistent",
      "--env", "LANG=C.UTF-8",
      "--env", "NODE_ENV=production",
      "--mount", mount(shape.proxyConfigPath, "/run/pixel-work/model-proxy.json"),
      "--mount", mount(shape.proxyReceiptDirectory, "/run/pixel-work-output", false),
      "--entrypoint", "/opt/node/bin/node",
      image,
      "/opt/pixel/deploy/work-model-proxy/proxy.mjs",
      "/run/pixel-work/model-proxy.json",
    ],
    env: Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" }),
  };
}

export function buildModelBackendConnect(proxyId, runtime) {
  const shape = runtimeShape(runtime);
  if (!ID_RE.test(proxyId ?? "")) fail("model proxy container ID is invalid");
  return { command: shape.dockerPath, args: ["network", "connect", shape.backendNetworkName, proxyId] };
}

export function validateImageInspect(image, expectedReference, expectedDigest) {
  if (!image || typeof image !== "object" || Array.isArray(image) || !IMAGE_ID_RE.test(image.Id ?? "")) fail("Docker image inspection is invalid");
  if (expectedReference === expectedDigest) {
    if (image.Id !== expectedDigest) fail("local image ID differs from the lease");
  } else {
    if (!IMAGE_RE.test(expectedReference ?? "") || !expectedReference.endsWith(`@${expectedDigest}`)) fail("expected image reference is invalid");
    const namedReference = expectedReference.slice(0, -(`@${expectedDigest}`.length));
    const finalSlash = namedReference.lastIndexOf("/");
    const finalColon = namedReference.lastIndexOf(":");
    const repository = finalColon > finalSlash ? namedReference.slice(0, finalColon) : namedReference;
    const repositoryDigest = `${repository}@${expectedDigest}`;
    if (!Array.isArray(image.RepoDigests) || !image.RepoDigests.some((entry) => entry === expectedReference || entry === repositoryDigest)) {
      fail("Docker image lacks the leased repository digest");
    }
  }
  return true;
}

function hasAll(value) {
  return Array.isArray(value) && value.some((item) => String(item).toUpperCase() === "ALL");
}

function noNewPrivileges(value) {
  return Array.isArray(value) && value.some((item) => String(item).toLowerCase().replaceAll("=", ":") === "no-new-privileges:true");
}

const MODEL_BACKEND_CACHE_TMPFS_DESTINATIONS = Object.freeze(["/var/cache/pixel-model", "/cache"]);

function modelBackendCacheTmpfsDestination(destinations) {
  if (!Array.isArray(destinations) || destinations.length !== 2 || !destinations.includes("/tmp")) return null;
  const cache = destinations.filter((destination) => destination !== "/tmp");
  return cache.length === 1 && MODEL_BACKEND_CACHE_TMPFS_DESTINATIONS.includes(cache[0]) ? cache[0] : null;
}

function tmpfsOptions(value, label) {
  if (typeof value !== "string" || !value) fail(`${label} tmpfs options are invalid`);
  const entries = value.split(","), flags = [], keyed = new Map();
  if (entries.some((entry) => !entry) || new Set(entries).size !== entries.length) fail(`${label} tmpfs options are invalid`);
  for (const entry of entries) {
    const separator = entry.indexOf("=");
    if (separator < 0) flags.push(entry);
    else {
      const name = entry.slice(0, separator), content = entry.slice(separator + 1);
      if (!name || !content || keyed.has(name)) fail(`${label} tmpfs options are invalid`);
      keyed.set(name, content);
    }
  }
  return { flags: flags.sort(), keyed };
}

function boundedTmpfsSize(value, minimum, maximum, label) {
  const match = /^(?:0|[1-9][0-9]*)m$/u.exec(value ?? "");
  const size = match ? Number(match[0].slice(0, -1)) : NaN;
  if (!Number.isSafeInteger(size) || size < minimum || size > maximum) fail(`${label} tmpfs size is outside the bounded contract`);
}

function validateModelBackendMounts(container) {
  const mounts = container.Mounts;
  if (!Array.isArray(mounts)) fail("local model backend mounts are invalid");
  const tmpfsMounts = mounts.filter((item) => item?.Type === "tmpfs");
  const tmpfsDestinations = tmpfsMounts.map((item) => item?.Destination).sort();
  // Docker may serialize --tmpfs only in HostConfig.Tmpfs; if Mounts exposes it, require the exact same contract.
  if (tmpfsMounts.length > 0 && (modelBackendCacheTmpfsDestination(tmpfsDestinations) === null
    || tmpfsMounts.some((item) => item.RW !== true || !["", null, undefined].includes(item.Source)))) {
    fail("local model backend tmpfs mounts differ from the bounded private contract");
  }
  if (mounts.some((item) => item?.Type !== "tmpfs" && item?.RW !== false)) fail("local model backend has a writable bind or volume mount");

  const declared = container.HostConfig?.Tmpfs;
  const cacheDestination = declared && typeof declared === "object" && !Array.isArray(declared)
    ? modelBackendCacheTmpfsDestination(Object.keys(declared)) : null;
  if (cacheDestination === null) {
    fail("local model backend tmpfs declarations differ from the bounded private contract");
  }
  const identity = /^(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*)$/u.exec(container.Config?.User ?? "");
  if (!identity) fail("local model backend tmpfs identity is invalid");
  const [uid, gid] = identity[0].split(":");
  const temporary = tmpfsOptions(declared["/tmp"], "temporary workspace");
  if (JSON.stringify(temporary.flags) !== JSON.stringify(["nodev", "noexec", "nosuid", "rw"])
    || JSON.stringify([...temporary.keyed.keys()].sort()) !== JSON.stringify(["mode", "size"])
    || temporary.keyed.get("mode") !== "1777") fail("temporary workspace tmpfs options differ from the bounded contract");
  boundedTmpfsSize(temporary.keyed.get("size"), 64, 16384, "temporary workspace");

  const cache = tmpfsOptions(declared[cacheDestination], "model cache");
  if (![["exec", "nodev", "nosuid", "rw"], ["nodev", "noexec", "nosuid", "rw"]].some((expected) => JSON.stringify(cache.flags) === JSON.stringify(expected))
    || JSON.stringify([...cache.keyed.keys()].sort()) !== JSON.stringify(["gid", "mode", "size", "uid"])
    || cache.keyed.get("mode") !== "0700" || cache.keyed.get("uid") !== uid || cache.keyed.get("gid") !== gid) {
    fail("model cache tmpfs options differ from the bounded contract");
  }
  boundedTmpfsSize(cache.keyed.get("size"), 64, 262144, "model cache");
}

function modelBackendMountsHardened(container) {
  try { validateModelBackendMounts(container); return true; }
  catch (error) {
    if (error instanceof DockerBoundaryError) return false;
    throw error;
  }
}

function sha256Text(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

export function modelBackendIdentityLabels(prepared) {
  const model = prepared?.policy?.localModel;
  if (
    !model || !["llama.cpp", "vllm"].includes(model.provider)
    || typeof model.id !== "string" || model.id.length < 1
    || !SHA_RE.test(model.modelArtifactSha256 ?? "")
    || !VERSION_RE.test(model.backendVersion ?? "")
    || !["cpu", "nvidia-cuda", "amd-rocm", "intel-xpu", "apple-metal", "other-local"].includes(model.acceleratorClass)
    || !SHA_RE.test(model.promptContractSha256 ?? "") || !SHA_RE.test(model.toolSchemaSha256 ?? "")
  ) fail("local model backend identity is incomplete");
  const candidateBindings = prepared?.bindings;
  const runtimeBindingNames = ["policySha256", "environmentSha256", "configurationSha256", "artifactSha256"];
  const bindings = candidateBindings && runtimeBindingNames.slice(1).some((name) => Object.hasOwn(candidateBindings, name)) ? candidateBindings : null;
  if (bindings !== null && (
    !bindings || typeof bindings !== "object" || Array.isArray(bindings)
    || !SHA_RE.test(bindings.policySha256 ?? "") || !SHA_RE.test(bindings.environmentSha256 ?? "")
    || !SHA_RE.test(bindings.configurationSha256 ?? "") || !SHA_RE.test(bindings.artifactSha256 ?? "")
    || bindings.artifactSha256 !== model.modelArtifactSha256
  )) fail("local model backend runtime bindings are incomplete");
  return Object.freeze({
    "com.osmantic.pixel.work-role": "model-backend",
    [`${BACKEND_LABEL_PREFIX}provider`]: model.provider,
    [`${BACKEND_LABEL_PREFIX}id-sha256`]: sha256Text(model.id),
    [`${BACKEND_LABEL_PREFIX}artifact-sha256`]: model.modelArtifactSha256,
    [`${BACKEND_LABEL_PREFIX}version`]: model.backendVersion,
    [`${BACKEND_LABEL_PREFIX}accelerator-class`]: model.acceleratorClass,
    [`${BACKEND_LABEL_PREFIX}prompt-contract-sha256`]: model.promptContractSha256,
    [`${BACKEND_LABEL_PREFIX}tool-schema-sha256`]: model.toolSchemaSha256,
    ...(bindings !== null ? {
      [`${BACKEND_LABEL_PREFIX}policy-sha256`]: bindings.policySha256,
      [`${BACKEND_LABEL_PREFIX}environment-sha256`]: bindings.environmentSha256,
      [`${BACKEND_LABEL_PREFIX}configuration-sha256`]: bindings.configurationSha256,
    } : {}),
  });
}

export function modelBackendNetworkIdentityLabels(prepared) {
  return Object.freeze({
    ...modelBackendIdentityLabels(prepared),
    "com.osmantic.pixel.work-role": "model-backend-network",
  });
}

export function validateModelBackendNetworkBindingLabels(network, prepared) {
  if (!network || typeof network !== "object" || Array.isArray(network)) fail("model backend network binding inspection is invalid");
  const expected = modelBackendNetworkIdentityLabels(prepared), labels = network.Labels ?? {};
  for (const [name, value] of Object.entries(expected)) if (labels[name] !== value) fail("model backend network differs from the exact runtime bindings");
  for (const name of Object.keys(labels)) if ((name === "com.osmantic.pixel.work-role" || name.startsWith(BACKEND_LABEL_PREFIX)) && !Object.hasOwn(expected, name)) fail("model backend network has an unexpected exact runtime binding");
  return true;
}

export function validateModelBackendHaltIdentity(container, prepared, runtime) {
  if (!container || typeof container !== "object" || Array.isArray(container)
    || !ID_RE.test(container.Id ?? "") || container.Name !== `/${runtime?.backendContainerName}`
    || container.Config?.Image !== prepared?.policy?.localModel?.imageRef
    || typeof container.State?.Running !== "boolean") fail("emergency halt target is not the exact Pixel model backend identity");
  const expected = modelBackendIdentityLabels(prepared), labels = container.Config?.Labels ?? {};
  for (const [name, value] of Object.entries(expected)) if (labels[name] !== value) fail("emergency halt target labels differ from the exact private identity");
  for (const name of Object.keys(labels)) if ((name === "com.osmantic.pixel.work-role" || name.startsWith(BACKEND_LABEL_PREFIX)) && !Object.hasOwn(expected, name)) fail("emergency halt target has an unexpected Pixel model identity label");
  if (labels["com.osmantic.pixel.work-role"] !== "model-backend") fail("emergency halt target role is invalid");
  const host = container.HostConfig ?? {}, networks = container.NetworkSettings?.Networks ?? {};
  const hardenedBoundaryObserved = hasAll(host.CapDrop) && noNewPrivileges(host.SecurityOpt)
    && host.ReadonlyRootfs === true && host.Privileged !== true
    && modelBackendMountsHardened(container)
    && Object.keys(host.PortBindings ?? {}).every((port) => port === "8080/tcp")
    && (host.PortBindings?.["8080/tcp"] ?? []).every((binding) => binding.HostIp === "127.0.0.1");
  return Object.freeze({
    running: container.State.Running, hardenedBoundaryObserved,
    expectedPrivateNetworkObserved: host.NetworkMode === runtime.backendNetworkName && Object.hasOwn(networks, runtime.backendNetworkName),
  });
}

export function validateModelBackendHaltNetworkIdentity(network, prepared, runtime) {
  if (!network || typeof network !== "object" || Array.isArray(network) || !ID_RE.test(network.Id ?? "")
    || network.Name !== runtime?.backendNetworkName || network.Driver !== "bridge") fail("emergency halt network is not the exact Pixel model backend network identity");
  const expected = modelBackendNetworkIdentityLabels(prepared), labels = network.Labels ?? {};
  for (const [name, value] of Object.entries(expected)) if (labels[name] !== value) fail("emergency halt network labels differ from the exact private identity");
  for (const name of Object.keys(labels)) if ((name === "com.osmantic.pixel.work-role" || name.startsWith(BACKEND_LABEL_PREFIX)) && !Object.hasOwn(expected, name)) fail("emergency halt network has an unexpected Pixel model identity label");
  return Object.freeze({
    privateBoundaryObserved: network.Internal === true && network.Attachable === false && network.Ingress === false,
    peerIds: Object.keys(network.Containers ?? {}).filter((id) => ID_RE.test(id)).sort(),
    peerSetValid: Object.keys(network.Containers ?? {}).every((id) => ID_RE.test(id)),
  });
}

function validateModelBackendAccelerator(host, acceleratorClass) {
  const devices = host?.Devices ?? [];
  const requests = host?.DeviceRequests ?? [];
  if (!Array.isArray(devices) || !Array.isArray(requests)) fail("local model backend accelerator inspection is invalid");
  if (devices.length !== 0) fail("local model backend has a direct host-device grant outside the supported contract");
  if (["cpu", "apple-metal"].includes(acceleratorClass)) {
    if (requests.length !== 0) fail("CPU-only local model backend has an accelerator grant");
    return true;
  }
  if (acceleratorClass !== "nvidia-cuda") fail("local model backend accelerator class is not admitted by the contained Docker runtime");
  if (requests.length !== 1) fail("NVIDIA local model backend must have exactly one bounded GPU request");
  const request = requests[0];
  const keys = Object.keys(request ?? {}).filter((key) => request[key] !== undefined && request[key] !== null).sort();
  const allowedKeys = ["Capabilities", "Count", "DeviceIDs", "Driver", "Options"];
  if (keys.some((key) => !allowedKeys.includes(key))) fail("NVIDIA local model backend GPU request has an unknown field");
  if (
    request.Driver !== "nvidia" || JSON.stringify(request.Capabilities) !== JSON.stringify([["gpu"]])
    || request.Options === null || typeof request.Options !== "object" || Array.isArray(request.Options) || Object.keys(request.Options).length !== 0
  ) fail("NVIDIA local model backend GPU request differs from the exact contract");
  const ids = request.DeviceIDs ?? [];
  if (!Array.isArray(ids) || ids.some((id) => !/^(?:[0-9]{1,3}|GPU-[A-Za-z0-9-]{1,80})$/.test(id)) || new Set(ids).size !== ids.length) fail("NVIDIA local model backend GPU identifiers are invalid");
  if (ids.length > 0) {
    if (request.Count !== 0) fail("NVIDIA device-ID grant also carries a count grant");
  } else if (!Number.isSafeInteger(request.Count) || (request.Count !== -1 && (request.Count < 1 || request.Count > 16))) {
    fail("NVIDIA local model backend GPU count is invalid");
  }
  return true;
}

export function validateModelBackendInspect(container, image, prepared, runtime) {
  if (!container || typeof container !== "object" || container.Name !== `/${runtime.backendContainerName}` || container.Image !== image.Id || container.State?.Running !== true) {
    fail("local model backend container is not the expected running image");
  }
  if (!hasAll(container.HostConfig?.CapDrop) || !noNewPrivileges(container.HostConfig?.SecurityOpt) || container.HostConfig?.ReadonlyRootfs !== true || container.HostConfig?.Privileged === true) {
    fail("local model backend is missing its hardened container boundary");
  }
  if (Object.keys(container.HostConfig?.PortBindings ?? {}).some((port) => port !== "8080/tcp")) fail("local model backend publishes an unexpected port");
  for (const binding of container.HostConfig?.PortBindings?.["8080/tcp"] ?? []) {
    if (binding.HostIp !== "127.0.0.1") fail("local model backend port is not loopback-only");
  }
  validateModelBackendMounts(container);
  validateModelBackendAccelerator(container.HostConfig, prepared.policy.localModel.acceleratorClass);
  const labels = container.Config?.Labels ?? {};
  const expectedLabels = modelBackendIdentityLabels(prepared);
  const hasExactRuntimeBindings = ["environmentSha256", "configurationSha256", "artifactSha256"].some((name) => Object.hasOwn(prepared?.bindings ?? {}, name));
  for (const [name, value] of Object.entries(expectedLabels)) {
    if (labels[name] !== value) fail("local model backend identity labels differ from private policy");
  }
  for (const name of Object.keys(labels)) {
    if (name.startsWith(BACKEND_LABEL_PREFIX) && !Object.hasOwn(expectedLabels, name)) {
      if (!hasExactRuntimeBindings && BACKEND_RUNTIME_BINDING_LABELS.has(name) && SHA_RE.test(labels[name] ?? "")) continue;
      fail("local model backend has an unexpected Pixel model identity label");
    }
  }
  const networks = container.NetworkSettings?.Networks ?? {};
  const attachment = networks[runtime.backendNetworkName];
  if (
    container.HostConfig?.NetworkMode !== runtime.backendNetworkName
    || JSON.stringify(Object.keys(networks)) !== JSON.stringify([runtime.backendNetworkName])
    || !attachment?.Aliases?.includes(runtime.backendAlias)
  ) fail("local model backend is not confined to its exact private network and alias");
  if (prepared.plan.model.backendImageDigest !== prepared.policy.localModel.imageDigest || prepared.plan.model.id !== prepared.policy.localModel.id) {
    fail("local model plan differs from private policy");
  }
  return true;
}

export function validateModelBackendNetworkInspect(network, runtime, expected) {
  if (
    !network || typeof network !== "object" || Array.isArray(network)
    || network.Name !== runtime.backendNetworkName || network.Driver !== "bridge"
    || network.Internal !== true || network.Attachable !== false || network.Ingress !== false
  ) fail("local model backend network is not a private non-attachable internal bridge");
  const actual = Object.entries(network.Containers ?? {}).map(([id, item]) => ({ id, name: item?.Name })).sort((a, b) => a.id.localeCompare(b.id));
  const wanted = [...expected].sort((a, b) => a.id.localeCompare(b.id));
  if (JSON.stringify(actual) !== JSON.stringify(wanted) || actual.some((item) => !ID_RE.test(item.id) || !NAME_RE.test(item.name ?? ""))) {
    fail("local model backend network contains an unexpected peer");
  }
  return true;
}

export function validateModelProxyInspect(container, image, prepared, claim, runtime, connected = false) {
  exactClaim(prepared, claim);
  const shape = runtimeShape(runtime);
  if (!container || typeof container !== "object" || Array.isArray(container)) fail("model proxy inspection is invalid");
  if (container.Name !== `/${shape.modelProxyName}`) fail("model proxy container name differs from the claim");
  if (container.Image !== image.Id) fail("model proxy container image differs from the leased image");
  if (container.State?.Running !== true) {
    const exit = Number.isSafeInteger(container.State?.ExitCode) ? container.State.ExitCode : "unknown";
    const oom = container.State?.OOMKilled === true ? "yes" : "no";
    fail(`model proxy stopped during startup (exit=${exit}, oom=${oom})`);
  }
  const labels = container.Config?.Labels ?? {};
  if (labels["com.osmantic.pixel.work-claim"] !== claim.claimId || labels["com.osmantic.pixel.work-job"] !== claim.jobId || labels["com.osmantic.pixel.work-role"] !== "model-proxy") {
    fail("model proxy labels differ from the claim");
  }
  const host = container.HostConfig ?? {};
  if (
    !hasAll(host.CapDrop) || !noNewPrivileges(host.SecurityOpt) || host.ReadonlyRootfs !== true || host.Privileged === true
    || host.NetworkMode !== shape.networkName || host.IpcMode !== "none" || host.CgroupnsMode !== "private"
    || host.PidsLimit !== 64 || host.Memory < 268435456 || host.MemorySwap !== host.Memory || host.LogConfig?.Type !== "none"
    || Object.keys(host.PortBindings ?? {}).length !== 0 || (host.Devices ?? []).length !== 0
  ) fail("model proxy outer container boundary is incomplete");
  const env = new Set(container.Config?.Env ?? []);
  for (const required of EXPECTED_PROXY_ENV) if (!env.has(required)) fail("model proxy environment is incomplete");
  for (const item of env) if (/^(?:HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|OPENAI_API_KEY|LLAMA_CPP_API_KEY|SSH_AUTH_SOCK)=/i.test(item)) fail("model proxy inherited a forbidden environment value");
  const mounts = container.Mounts ?? [];
  const configMount = mounts.find((item) => item.Destination === "/run/pixel-work/model-proxy.json");
  const receiptMount = mounts.find((item) => item.Destination === "/run/pixel-work-output");
  if (mounts.length !== 2 || configMount?.RW !== false || receiptMount?.RW !== true) fail("model proxy mounts differ from the exact contract");
  const networks = container.NetworkSettings?.Networks ?? {};
  const job = networks[shape.networkName];
  if (!job || job.IPAddress !== runtime.proxyIp || !job.Aliases?.includes(shape.modelAlias)) fail("model proxy job-network attachment is invalid");
  if (connected !== Boolean(networks[shape.backendNetworkName])) fail("model proxy backend-network state is invalid");
  if (connected && Object.keys(networks).length !== 2) fail("model proxy has an unexpected network attachment");
  if (!connected && Object.keys(networks).length !== 1) fail("model proxy has an unexpected pre-connect network attachment");
  return true;
}

export const dockerBoundaryContract = Object.freeze({ proxyAliases: Object.freeze({ worker: "pixel-model", backend: "pixel-local-model" }) });

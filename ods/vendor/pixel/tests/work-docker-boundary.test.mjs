import assert from "node:assert/strict";
import { chmod, link, mkdtemp, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  buildModelBackendConnect, buildModelProxyConfig, buildModelProxyDockerCommand, buildScoutNetworkCreate,
  DockerBoundaryError, modelBackendIdentityLabels, modelBackendNetworkIdentityLabels, validateImageInspect,
  validateModelBackendInspect, validateModelBackendNetworkBindingLabels, validateModelBackendNetworkInspect, validateModelProxyInspect,
} from "../deploy/work-runner/docker-boundary.mjs";
import { deriveScoutDockerRuntime, dockerSupervisorInternals, DockerSupervisorError } from "../deploy/work-runner/docker-supervisor.mjs";
import { inferencePolicySha256 } from "../deploy/work-model-proxy/inference-policy.mjs";
import { writeModelProxyReport } from "../deploy/work-model-proxy/proxy.mjs";

const digest = (character) => character.repeat(64);

function fixture() {
  const jobId = "work-1786366800000-abcdef123456";
  const leaseId = "worklease-1786366800000-abcdef123456";
  const claimId = "workclaim-1786366800000-abcdef123456";
  const model = {
    route: "local-only", provider: "llama.cpp", id: "assistant-model",
    backendImageDigest: `sha256:${digest("8")}`, contextWindow: 131072, supportsVision: false,
    endpointContract: "llama.cpp-discovery-and-openai-stream-v1",
  };
  const executor = {
    id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac",
    license: "MIT", artifactSha256: digest("e"), rpcProtocolVersion: 2,
  };
  const prepared = {
    plan: {
      jobId, profile: "scout", model, executor, isolation: { runnerImageDigest: `sha256:${digest("f")}` },
      budgets: {
        maxRuntimeSeconds: 600, maxToolCalls: 200, maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
        maxNetworkBytes: 10485760, maxMemoryMiB: 2048,
      },
    },
    lease: { leaseId },
    policy: {
      runner: { imageRef: `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("f")}` },
      localModel: {
        id: "assistant-model", provider: "llama.cpp",
        imageRef: `ghcr.io/ggml-org/llama.cpp:server@sha256:${digest("8")}`,
        imageDigest: `sha256:${digest("8")}`, contextWindow: 131072, maxRequestContextTokens: 131072, supportsVision: false,
        maxRequestOutputTokens: 1024, modelArtifactSha256: digest("4"), backendVersion: "test-backend",
        acceleratorClass: "cpu", promptContractSha256: digest("5"), toolSchemaSha256: digest("6"),
      },
    },
    modelQualification: {
      qualificationId: "modelqual-1786366740000-abcdef123456",
      receiptSha256: digest("1"), casesSha256: digest("2"), evaluatorSha256: digest("3"),
      profile: "scout", maxContextTokens: 131072, maxOutputTokens: 1024, exactUsage: true,
    },
    bindings: { planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d") },
    workspace: { sha256: digest("7") },
  };
  prepared.lease.budgets = prepared.plan.budgets;
  const claim = {
    status: "consumed", externalEffects: false, jobId, leaseId, claimId,
    planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d"),
    workspaceSha256: digest("7"), runnerImageDigest: `sha256:${digest("f")}`, executor, model,
  };
  const runtime = {
    dockerPath: "/usr/bin/docker",
    networkName: "pixel-work-net-abcdef123456",
    networkSubnet: "172.30.8.0/29",
    workerIp: "172.30.8.2",
    proxyIp: "172.30.8.3",
    modelProxyName: "pixel-work-model-abcdef123456",
    modelAlias: "pixel-model",
    backendNetworkName: "pixel-reference_model-backend",
    backendContainerName: "pixel-reference-local-model-1",
    backendAlias: "pixel-local-model",
    proxyCidFile: "/run/pixel-work/cids/proxy.cid",
    proxyConfigPath: "/run/pixel-work/config/model-proxy.json",
    proxyReceiptDirectory: "/run/pixel-work/receipts/claim",
    imageIdentifier: `sha256:${digest("f")}`,
    backendImageIdentifier: `sha256:${digest("8")}`,
    uid: 1000,
    gid: 1000,
  };
  return { prepared, claim, runtime };
}

function image(id, repoDigest) {
  return { Id: `sha256:${digest(id)}`, RepoDigests: repoDigest ? [repoDigest] : [] };
}

async function writeProxyReceipt(value, inferenceHash) {
  const root = await mkdtemp(join(tmpdir(), "pixel-proxy-receipt-"));
  value.runtime.proxyReceiptDirectory = root.replaceAll("\\", "/");
  const receipt = {
    schemaVersion: 1,
    jobId: value.claim.jobId,
    claimId: value.claim.claimId,
    planSha256: value.claim.planSha256,
    inferencePolicySha256: inferenceHash,
    modelRequests: 0,
    inputTokens: 0,
    outputTokens: 0,
    networkBytes: 0,
    activeInference: false,
    contentStored: false,
    credentialsExposed: false,
    arbitraryNetwork: false,
    externalEffects: false,
  };
  await writeModelProxyReport(join(root, "model-proxy-receipt.json"), receipt);
  return receipt;
}

function backendInspect(value, backendImage) {
  return {
    Id: digest("8"), Name: `/${value.runtime.backendContainerName}`, Image: backendImage.Id,
    State: { Running: true }, Config: { User: "10001:10001", Labels: modelBackendIdentityLabels(value.prepared) },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false,
      NetworkMode: value.runtime.backendNetworkName,
      PortBindings: { "8080/tcp": [{ HostIp: "127.0.0.1", HostPort: "8000" }] },
      Devices: [], DeviceRequests: [],
      Tmpfs: {
        "/tmp": "rw,nosuid,nodev,noexec,size=128m,mode=1777",
        "/var/cache/pixel-model": "rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=10001,gid=10001",
      },
    },
    Mounts: [
      { Type: "bind", Destination: "/models/model.gguf", RW: false },
      { Type: "tmpfs", Destination: "/tmp", RW: true },
      { Type: "tmpfs", Destination: "/var/cache/pixel-model", RW: true },
    ],
    NetworkSettings: { Networks: { [value.runtime.backendNetworkName]: { Aliases: [value.runtime.backendAlias] } } },
  };
}

function proxyInspect(value, runnerImage, connected = false) {
  const networks = {
    [value.runtime.networkName]: { IPAddress: value.runtime.proxyIp, Aliases: [value.runtime.modelAlias] },
  };
  if (connected) networks[value.runtime.backendNetworkName] = { IPAddress: "172.31.0.3", Aliases: [value.runtime.modelProxyName] };
  return {
    Id: digest("6"), Name: `/${value.runtime.modelProxyName}`, Image: runnerImage.Id, State: { Running: true },
    Config: {
      Labels: {
        "com.osmantic.pixel.work-claim": value.claim.claimId,
        "com.osmantic.pixel.work-job": value.claim.jobId,
        "com.osmantic.pixel.work-role": "model-proxy",
      },
      Env: ["PATH=/opt/node/bin:/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8", "NODE_ENV=production", "DEBIAN_FRONTEND=noninteractive"],
    },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false,
      NetworkMode: value.runtime.networkName, IpcMode: "none", CgroupnsMode: "private", PidsLimit: 64,
      Memory: 512 * 1024 * 1024, MemorySwap: 512 * 1024 * 1024, LogConfig: { Type: "none" }, PortBindings: {}, Devices: [],
    },
    Mounts: [
      { Destination: "/run/pixel-work/model-proxy.json", RW: false },
      { Destination: "/run/pixel-work-output", RW: true },
    ],
    NetworkSettings: { Networks: networks },
  };
}

test("Docker boundary builds a private network and content-free hardened proxy launch", () => {
  const value = fixture();
  const network = buildScoutNetworkCreate(value.claim, value.runtime);
  const config = buildModelProxyConfig(value.prepared, value.claim, value.runtime);
  const proxy = buildModelProxyDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(network.args.includes("--internal"));
  assert.ok(network.args.includes("--attachable=false"));
  assert.ok(network.args.includes("172.30.8.0/29"));
  for (const required of [
    "--pull", "never", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--network-alias",
    "pixel-model", "--ip", "172.30.8.3", "--log-driver", "none", "--user", "1000:1000",
  ]) assert.ok(proxy.args.includes(required), required);
  assert.equal(config.backendOrigin, "http://pixel-local-model:8080");
  assert.equal(config.provider, "llama.cpp");
  assert.equal(config.allowedClientIpv4, "172.30.8.2");
  assert.deepEqual(config.allowedTools, ["glob", "grep", "read"]);
  const delegated = fixture();
  delegated.prepared.plan.profile = "builder";
  delegated.prepared.modelQualification.profile = "builder";
  assert.deepEqual(buildModelProxyConfig(delegated.prepared, delegated.claim, delegated.runtime).allowedTools, [
    "bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "read", "task", "todo", "write", "yield",
  ]);
  assert.equal(config.modelId, "assistant-model");
  assert.equal(config.budgets.maxOutputTokens, 20000);
  assert.doesNotMatch(JSON.stringify({ network, config, proxy }), /objective|acceptance|provider secret|docker\.sock|SSH_AUTH_SOCK/i);
  assert.deepEqual(buildModelBackendConnect(digest("6"), value.runtime).args, ["network", "connect", value.runtime.backendNetworkName, digest("6")]);
  const vllm = fixture();
  vllm.prepared.plan.model.provider = "vllm";
  vllm.prepared.policy.localModel.provider = "vllm";
  vllm.prepared.policy.localModel.inference = {
    enforcement: "exact-request-boundary-v1", wireApi: "openai-chat-completions",
    temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
    repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default", reasoningVisibility: "hidden",
  };
  vllm.claim.model.provider = "vllm";
  const vllmConfig = buildModelProxyConfig(vllm.prepared, vllm.claim, vllm.runtime);
  assert.equal(vllmConfig.provider, "vllm");
  assert.equal(vllmConfig.inference.maxOutputTokens, 1024);
  assert.equal(vllmConfig.inference.stream, true);
});

test("supervisor binds proxy receipts to the exact derived inference policy hash", {
  skip: process.platform === "win32" ? "receipt paths are canonical Linux runtime paths" : false,
}, async () => {
  const legacy = fixture();
  const legacyReceipt = await writeProxyReceipt(legacy, null);
  assert.deepEqual(await dockerSupervisorInternals.readProxyReceipt(legacy.prepared, legacy.claim, legacy.runtime), legacyReceipt);

  const vllm = fixture();
  vllm.prepared.plan.model.provider = "vllm";
  vllm.prepared.policy.localModel.provider = "vllm";
  vllm.prepared.policy.localModel.inference = {
    enforcement: "exact-request-boundary-v1",
    wireApi: "openai-chat-completions",
    temperaturePermille: 700,
    topPPermille: 950,
    topK: 40,
    minPPermille: 50,
    repeatPenaltyPermille: 1100,
    seed: 42,
    reasoningEffort: "backend-default",
    reasoningVisibility: "hidden",
  };
  const expectedHash = inferencePolicySha256(buildModelProxyConfig(vllm.prepared, vllm.claim, vllm.runtime).inference);
  const vllmReceipt = await writeProxyReceipt(vllm, expectedHash);
  assert.deepEqual(await dockerSupervisorInternals.readProxyReceipt(vllm.prepared, vllm.claim, vllm.runtime), vllmReceipt);

  vllmReceipt.activeInference = true;
  vllmReceipt.claimId = "workclaim-1786366800000-fedcba654321";
  await writeModelProxyReport(join(vllm.runtime.proxyReceiptDirectory, "model-proxy-receipt.json"), vllmReceipt);
  await assert.rejects(
    dockerSupervisorInternals.readProxyReceipt(vllm.prepared, vllm.claim, vllm.runtime),
    /model proxy receipt differs from the lease boundary \(fields=activeInference,claimId\)/,
  );

  vllmReceipt.claimId = vllm.claim.claimId;
  const quiesced = { ...vllmReceipt, activeInference: false };
  await writeModelProxyReport(join(vllm.runtime.proxyReceiptDirectory, "model-proxy-receipt.json"), vllmReceipt);
  let replacementCount = 0;
  const replaceAfterDescriptorRead = async (path) => {
    if (replacementCount > 0) return;
    replacementCount += 1;
    await writeModelProxyReport(path, quiesced);
  };
  await assert.rejects(
    dockerSupervisorInternals.readProxyReceipt(vllm.prepared, vllm.claim, vllm.runtime, {
      __testAfterDescriptorRead: replaceAfterDescriptorRead,
    }),
    /model proxy receipt changed during atomic replacement/,
  );

  replacementCount = 0;
  await writeModelProxyReport(join(vllm.runtime.proxyReceiptDirectory, "model-proxy-receipt.json"), vllmReceipt);
  assert.deepEqual(await dockerSupervisorInternals.waitForQuiescentProxyReceipt(vllm.prepared, vllm.claim, vllm.runtime, {
    attempts: 20, delayMilliseconds: 5, __testAfterDescriptorRead: replaceAfterDescriptorRead,
  }), quiesced);
  assert.equal(replacementCount, 1);
});

test("proxy receipt quiescence fails closed for unsafe and malformed stable files", {
  skip: process.platform === "win32" ? "receipt paths are canonical Linux runtime paths" : false,
}, async () => {
  const value = fixture();
  await writeProxyReceipt(value, null);
  const path = join(value.runtime.proxyReceiptDirectory, "model-proxy-receipt.json");

  await chmod(path, 0o644);
  await assert.rejects(
    dockerSupervisorInternals.waitForQuiescentProxyReceipt(value.prepared, value.claim, value.runtime, {
      attempts: 20, delayMilliseconds: 5,
    }),
    /model proxy receipt is not a stable private single-link file/,
  );

  await chmod(path, 0o600);
  const hardlinkPath = join(value.runtime.proxyReceiptDirectory, "hostile-hardlink.json");
  await link(path, hardlinkPath);
  await assert.rejects(
    dockerSupervisorInternals.waitForQuiescentProxyReceipt(value.prepared, value.claim, value.runtime, {
      attempts: 20, delayMilliseconds: 5,
    }),
    /model proxy receipt is not a stable private single-link file/,
  );

  await unlink(hardlinkPath);
  await writeFile(path, "{", { encoding: "utf8", mode: 0o600 });
  await assert.rejects(
    dockerSupervisorInternals.waitForQuiescentProxyReceipt(value.prepared, value.claim, value.runtime, {
      attempts: 20, delayMilliseconds: 5,
    }),
    /model proxy receipt is not JSON/,
  );
});

test("Docker boundary accepts only an exact local runner image ID as the non-registry form", () => {
  const value = fixture();
  value.prepared.policy.runner.imageRef = value.prepared.plan.isolation.runnerImageDigest;
  assert.doesNotThrow(() => buildModelProxyDockerCommand(value.prepared, value.claim, value.runtime));
  value.prepared.policy.runner.imageRef = `sha256:${digest("0")}`;
  assert.throws(() => buildModelProxyDockerCommand(value.prepared, value.claim, value.runtime), DockerBoundaryError);
});

test("Docker supervisor derives every mutable name and private path from the consumed claim", () => {
  const value = fixture();
  const runtime = deriveScoutDockerRuntime(value.prepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: value.runtime.networkSubnet, workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: value.runtime.backendNetworkName, backendContainerName: value.runtime.backendContainerName,
    imageIdentifier: value.runtime.imageIdentifier, uid: 1000, gid: 1000,
    backendImageIdentifier: value.runtime.backendImageIdentifier,
  });
  assert.equal(runtime.runRoot, `/var/lib/pixel-work/runs/${value.claim.claimId}`);
  assert.equal(runtime.proxyConfigPath, `${runtime.runRoot}/config/model-proxy.json`);
  assert.equal(runtime.containerName, "pixel-work-scout-abcdef123456");
  assert.equal(runtime.modelId, "assistant-model");
  assert.doesNotMatch(JSON.stringify(runtime), /objective|acceptance|secret|credential/i);
  assert.throws(() => deriveScoutDockerRuntime(value.prepared, value.claim, {
    dockerPath: "docker", stateRoot: "/var/lib/pixel-work", networkSubnet: value.runtime.networkSubnet,
    workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: value.runtime.backendNetworkName, backendContainerName: value.runtime.backendContainerName,
    uid: 1000, gid: 1000,
  }), DockerSupervisorError);
});

test("Docker boundary rejects subnet, alias, path, image, claim, and model substitution", () => {
  for (const mutate of [
    (copy) => { copy.runtime.networkSubnet = "172.30.8.1/29"; },
    (copy) => { copy.runtime.workerIp = "172.30.9.2"; },
    (copy) => { copy.runtime.backendAlias = "attacker"; },
    (copy) => { copy.runtime.proxyConfigPath = "/run/pixel work/config.json"; },
    (copy) => { copy.runtime.imageIdentifier = `sha256:${digest("0")}`; },
    (copy) => { copy.claim.model.id = "substituted"; },
    (copy) => { copy.prepared.plan.model.backendImageDigest = `sha256:${digest("0")}`; copy.claim.model.backendImageDigest = `sha256:${digest("0")}`; },
  ]) {
    const hostile = structuredClone(fixture());
    mutate(hostile);
    assert.throws(() => buildModelProxyDockerCommand(hostile.prepared, hostile.claim, hostile.runtime), DockerBoundaryError, mutate.toString());
  }
});

test("Docker image and local model inspections bind exact digests and hardening", () => {
  const value = fixture();
  const runner = image("f");
  const backendReference = value.prepared.policy.localModel.imageRef;
  const backend = image("5", backendReference);
  assert.equal(validateImageInspect(runner, value.runtime.imageIdentifier, value.prepared.plan.isolation.runnerImageDigest), true);
  assert.throws(
    () => validateImageInspect(image("0"), value.runtime.imageIdentifier, value.prepared.plan.isolation.runnerImageDigest),
    DockerBoundaryError,
  );
  assert.equal(validateImageInspect(backend, backendReference, value.prepared.plan.model.backendImageDigest), true);
  const canonicalBackendDigest = backendReference.replace(":server@", "@");
  assert.equal(validateImageInspect(image("5", canonicalBackendDigest), backendReference, value.prepared.plan.model.backendImageDigest), true);
  const portReference = `registry.example:5000/team/backend:server@sha256:${digest("8")}`;
  const canonicalPortDigest = `registry.example:5000/team/backend@sha256:${digest("8")}`;
  assert.equal(validateImageInspect(image("5", canonicalPortDigest), portReference, value.prepared.plan.model.backendImageDigest), true);
  assert.throws(
    () => validateImageInspect(image("5", `registry.example/team/backend@sha256:${digest("8")}`), portReference, value.prepared.plan.model.backendImageDigest),
    DockerBoundaryError,
  );
  assert.throws(
    () => validateImageInspect(image("5", `ghcr.io/hostile/llama.cpp@sha256:${digest("8")}`), backendReference, value.prepared.plan.model.backendImageDigest),
    DockerBoundaryError,
  );
  assert.throws(
    () => validateImageInspect(image("5", `ghcr.io/ggml-org/llama.cpp@sha256:${digest("0")}`), backendReference, value.prepared.plan.model.backendImageDigest),
    DockerBoundaryError,
  );
  const inspected = backendInspect(value, backend);
  assert.equal(validateModelBackendInspect(inspected, backend, value.prepared, value.runtime), true);
  const pathBoundCache = structuredClone(inspected);
  pathBoundCache.HostConfig.Tmpfs["/cache"] = pathBoundCache.HostConfig.Tmpfs["/var/cache/pixel-model"];
  delete pathBoundCache.HostConfig.Tmpfs["/var/cache/pixel-model"];
  pathBoundCache.Mounts.find((item) => item.Destination === "/var/cache/pixel-model").Destination = "/cache";
  assert.equal(validateModelBackendInspect(pathBoundCache, backend, value.prepared, value.runtime), true);
  const ambiguousCache = structuredClone(pathBoundCache);
  ambiguousCache.HostConfig.Tmpfs["/var/cache/pixel-model"] = ambiguousCache.HostConfig.Tmpfs["/cache"];
  ambiguousCache.Mounts.push({ Type: "tmpfs", Destination: "/var/cache/pixel-model", RW: true });
  assert.throws(() => validateModelBackendInspect(ambiguousCache, backend, value.prepared, value.runtime), DockerBoundaryError);
  const unqualifiedCache = structuredClone(pathBoundCache);
  unqualifiedCache.HostConfig.Tmpfs["/scratch"] = unqualifiedCache.HostConfig.Tmpfs["/cache"];
  delete unqualifiedCache.HostConfig.Tmpfs["/cache"];
  unqualifiedCache.Mounts.find((item) => item.Destination === "/cache").Destination = "/scratch";
  assert.throws(() => validateModelBackendInspect(unqualifiedCache, backend, value.prepared, value.runtime), DockerBoundaryError);
  const dockerTmpfsNormalized = structuredClone(inspected);
  dockerTmpfsNormalized.Mounts = dockerTmpfsNormalized.Mounts.filter((item) => item.Type !== "tmpfs");
  assert.equal(validateModelBackendInspect(dockerTmpfsNormalized, backend, value.prepared, value.runtime), true);
  const normalizedWithUndeclaredMount = structuredClone(dockerTmpfsNormalized);
  normalizedWithUndeclaredMount.HostConfig.Tmpfs["/models/model.gguf"] = "rw,nosuid,nodev,noexec,size=64m,mode=0700";
  assert.throws(() => validateModelBackendInspect(normalizedWithUndeclaredMount, backend, value.prepared, value.runtime),
    DockerBoundaryError);
  for (const mutate of [
    (copy) => { copy.HostConfig.ReadonlyRootfs = false; },
    (copy) => { copy.HostConfig.PortBindings["8080/tcp"][0].HostIp = "0.0.0.0"; },
    (copy) => { copy.Mounts[0].RW = true; },
    (copy) => {
      copy.Mounts.push({ Type: "tmpfs", Source: "", Destination: "/models/model.gguf", RW: true });
      copy.HostConfig.Tmpfs["/models/model.gguf"] = "rw,nosuid,nodev,noexec,size=64m,mode=0700";
    },
    (copy) => { copy.HostConfig.Tmpfs["/tmp"] = "rw,nosuid,nodev,exec,size=128m,mode=1777"; },
    (copy) => { copy.NetworkSettings.Networks[value.runtime.backendNetworkName].Aliases = ["wrong"]; },
    (copy) => { copy.HostConfig.NetworkMode = "host"; },
    (copy) => { copy.NetworkSettings.Networks.external = { Aliases: ["model"] }; },
    (copy) => { copy.Config.Labels["com.osmantic.pixel.work-model-artifact-sha256"] = digest("0"); },
    (copy) => { copy.Config.Labels["com.osmantic.pixel.work-model-extra"] = "ambiguous"; },
    (copy) => { copy.HostConfig.Devices = [{ PathOnHost: "/dev/mem", PathInContainer: "/dev/mem", CgroupPermissions: "rwm" }]; },
    (copy) => { copy.HostConfig.DeviceRequests = [{ Driver: "nvidia", Count: -1, DeviceIDs: [], Capabilities: [["gpu"]], Options: {} }]; },
  ]) {
    const hostile = structuredClone(inspected);
    mutate(hostile);
    assert.throws(() => validateModelBackendInspect(hostile, backend, value.prepared, value.runtime), DockerBoundaryError);
  }
  const substitutedImage = image("5", `ghcr.io/ggml-org/llama.cpp:server@sha256:${digest("0")}`);
  assert.throws(() => validateImageInspect(substitutedImage, backendReference, value.prepared.plan.model.backendImageDigest), DockerBoundaryError);

  const accelerated = fixture();
  accelerated.prepared.policy.localModel.acceleratorClass = "nvidia-cuda";
  const acceleratedInspect = backendInspect(accelerated, backend);
  acceleratedInspect.HostConfig.DeviceRequests = [{ Driver: "nvidia", Count: 0, DeviceIDs: ["GPU-01234567-abcd"], Capabilities: [["gpu"]], Options: {} }];
  assert.equal(validateModelBackendInspect(acceleratedInspect, backend, accelerated.prepared, accelerated.runtime), true);
  for (const mutate of [
    (copy) => { copy.HostConfig.DeviceRequests[0].Driver = "cdi"; },
    (copy) => { copy.HostConfig.DeviceRequests[0].Capabilities = [["gpu", "compute"]]; },
    (copy) => { copy.HostConfig.DeviceRequests[0].DeviceIDs.push("../../host"); },
    (copy) => { copy.HostConfig.DeviceRequests[0].Count = -1; },
    (copy) => { copy.HostConfig.DeviceRequests.push(structuredClone(copy.HostConfig.DeviceRequests[0])); },
  ]) {
    const hostile = structuredClone(acceleratedInspect); mutate(hostile);
    assert.throws(() => validateModelBackendInspect(hostile, backend, accelerated.prepared, accelerated.runtime), DockerBoundaryError);
  }
});

test("exact controller bindings authenticate both backend container and network labels", () => {
  const value = fixture(), exactBindings = {
    policySha256: digest("1"), environmentSha256: digest("2"), configurationSha256: digest("3"),
    artifactSha256: value.prepared.policy.localModel.modelArtifactSha256,
  };
  const exactPrepared = { ...value.prepared, bindings: exactBindings };
  const backend = image("5", value.prepared.policy.localModel.imageRef);
  const container = backendInspect({ ...value, prepared: exactPrepared }, backend);
  const network = { Labels: modelBackendNetworkIdentityLabels(exactPrepared) };
  assert.equal(validateModelBackendInspect(container, backend, exactPrepared, value.runtime), true);
  assert.equal(validateModelBackendNetworkBindingLabels(network, exactPrepared), true);
  const substitutedContainer = structuredClone(container);
  substitutedContainer.Config.Labels["com.osmantic.pixel.work-model-environment-sha256"] = digest("0");
  assert.throws(() => validateModelBackendInspect(substitutedContainer, backend, exactPrepared, value.runtime), DockerBoundaryError);
  const widenedNetwork = structuredClone(network);
  widenedNetwork.Labels["com.osmantic.pixel.work-model-extra"] = "ambiguous";
  assert.throws(() => validateModelBackendNetworkBindingLabels(widenedNetwork, exactPrepared), DockerBoundaryError);
});

test("supervision requires a complete exact backend binding when the controller claims it", () => {
  const value = fixture(), exactBindings = {
    policySha256: digest("1"), environmentSha256: digest("2"), configurationSha256: digest("3"),
    artifactSha256: value.prepared.policy.localModel.modelArtifactSha256,
  };
  assert.equal(dockerSupervisorInternals.selectModelBackendPrepared(value.prepared, {}), value.prepared);
  assert.throws(() => dockerSupervisorInternals.selectModelBackendPrepared(value.prepared, { requireExactModelBackendBindings: true }), /bindings are required/u);
  const selected = dockerSupervisorInternals.selectModelBackendPrepared(value.prepared, { modelBackendBindings: exactBindings, requireExactModelBackendBindings: true });
  assert.deepEqual(selected.bindings, exactBindings);
  for (const bindings of [
    { ...exactBindings, artifactSha256: digest("0") },
    { ...exactBindings, unexpected: digest("0") },
    { policySha256: exactBindings.policySha256 },
  ]) assert.throws(() => dockerSupervisorInternals.selectModelBackendPrepared(value.prepared, { modelBackendBindings: bindings }), /bindings are invalid/u);
});

test("Docker network and proxy inspection permit exactly the model and the leased proxy", () => {
  const value = fixture();
  const backendId = digest("8");
  const proxyId = digest("6");
  const initial = {
    Name: value.runtime.backendNetworkName, Driver: "bridge", Internal: true, Attachable: false, Ingress: false,
    Containers: { [backendId]: { Name: value.runtime.backendContainerName } },
  };
  assert.equal(validateModelBackendNetworkInspect(initial, value.runtime, [{ id: backendId, name: value.runtime.backendContainerName }]), true);
  const connected = structuredClone(initial);
  connected.Containers[proxyId] = { Name: value.runtime.modelProxyName };
  assert.equal(validateModelBackendNetworkInspect(connected, value.runtime, [
    { id: backendId, name: value.runtime.backendContainerName }, { id: proxyId, name: value.runtime.modelProxyName },
  ]), true);
  const runnerImage = image("f");
  assert.equal(validateModelProxyInspect(proxyInspect(value, runnerImage), runnerImage, value.prepared, value.claim, value.runtime, false), true);
  assert.equal(validateModelProxyInspect(proxyInspect(value, runnerImage, true), runnerImage, value.prepared, value.claim, value.runtime, true), true);
  const hostile = structuredClone(connected);
  hostile.Containers[digest("4")] = { Name: "unexpected-peer" };
  assert.throws(() => validateModelBackendNetworkInspect(hostile, value.runtime, [
    { id: backendId, name: value.runtime.backendContainerName }, { id: proxyId, name: value.runtime.modelProxyName },
  ]), DockerBoundaryError);
});

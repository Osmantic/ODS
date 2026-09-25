import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { formatModelBackendCliFailure, runModelBackendCommand, WorkModelBackendCliError } from "../deploy/work-controller/model-backend-cli.mjs";
import { buildModelBackendLaunch } from "../deploy/work-controller/model-backend-launch.mjs";
import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels, validateModelBackendInspect } from "../deploy/work-runner/docker-boundary.mjs";
import { ModelBackendCoordinationError } from "../deploy/work-runner/model-backend-coordination.mjs";
import { validateWorkModelArtifactManifest, validateWorkModelArtifactRenderReceipt, validateWorkModelArtifactReview, validateWorkModelBackendHaltReceipt, validateWorkModelBackendHaltReview, validateWorkModelBackendLaunch, validateWorkModelBackendLifecycleReceipt, validateWorkModelBackendLifecycleReview, validateWorkModelBackendReview, validateWorkModelBackendStatus } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const now = new Date("2026-08-12T12:00:00.000Z");
const execute = promisify(execFile);

test("standalone CLI emits the bounded startup diagnostic captured before rollback", () => {
  const error = new WorkModelBackendCliError("model backend stopped before reporting its ready event");
  Object.defineProperty(error, "modelBackendStartDiagnostic", {
    value: { operation: "pixel-work-model-backend-start-diagnostic", state: { exitCode: 1, oomKilled: false }, logs: { captureStatus: "captured", stderr: { text: "path-bound cache root" } } },
  });
  const output = formatModelBackendCliFailure(error);
  assert.match(output, /model backend stopped before reporting its ready event/u);
  assert.match(output, /pixel-work-model-backend-diagnostic:/u);
  assert.match(output, /path-bound cache root/u);
  assert.equal(
    formatModelBackendCliFailure(new ModelBackendCoordinationError("model backend coordination lock is held by another live operation")),
    "pixel-work-model-backend: model backend coordination lock is held by another live operation\n",
  );
  assert.equal(formatModelBackendCliFailure(new Error("private daemon detail")), "pixel-work-model-backend: unexpected failure\n");
});

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t, { nvidia = false, restartPolicy } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-backend-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  t.after(() => rm(root, { recursive: true, force: true }));
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.localModel.acceleratorClass = nvidia ? "nvidia-cuda" : "cpu";
  const environment = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
  const policyPath = join(root, "policy.json"), environmentPath = join(root, "environment.json"), configPath = join(root, "backend.json");
  environment.policyPath = policyPath; environment.stateRoot = root;
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath, modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" }, runtimeCacheSeed: null, backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 },
    ...(restartPolicy === undefined ? {} : { restartPolicy }),
    accelerator: { class: nvidia ? "nvidia-cuda" : "cpu", count: nvidia ? 1 : 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: nvidia ? 16 : 0, flashAttention: nvidia },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const artifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1,
    kind: "file", artifactSha256: policy.localModel.modelArtifactSha256, fileCount: 1, totalBytes: 42,
    files: [{ relativePath: "model", bytes: 42, sha256: policy.localModel.modelArtifactSha256 }],
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.",
  };
  await privateWrite(policyPath, policy); await privateWrite(environmentPath, environment); await privateWrite(configPath, configuration);
  const launch = buildModelBackendLaunch({ configuration, environment, policy, artifactManifest });
  const explicitEnvironment = [];
  for (let index = 0; index < launch.container.args.length; index += 1) if (launch.container.args[index] === "--env") explicitEnvironment.push(launch.container.args[++index]);
  const image = {
    Id: `sha256:${digest("9")}`, RepoDigests: [policy.localModel.imageRef],
    Config: { Env: ["PATH=/usr/bin:/bin", "BACKEND_VERSION=fixture"], Labels: { "org.opencontainers.image.title": "fixture" }, Entrypoint: ["/usr/bin/llama-server"], WorkingDir: "/" },
  };
  const containerId = digest("8");
  const imageIndex = launch.container.args.indexOf(policy.localModel.imageRef);
  const prepared = { policy, bindings: launch.bindings };
  const container = {
    Id: containerId, Name: `/${environment.runtime.backendContainerName}`, Image: image.Id,
    State: { Running: true }, Config: {
      Hostname: "pixel-local-model", User: "10001:10001", Tty: false, OpenStdin: false, StdinOnce: false,
      Env: [...explicitEnvironment, ...image.Config.Env], Cmd: launch.container.args.slice(imageIndex + 1), Image: policy.localModel.imageRef,
      Entrypoint: image.Config.Entrypoint, WorkingDir: image.Config.WorkingDir, StopSignal: "SIGTERM", StopTimeout: 30,
      Healthcheck: image.Config.Healthcheck, Shell: image.Config.Shell,
      Labels: { ...image.Config.Labels, ...modelBackendIdentityLabels(prepared) },
    },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false,
      CapAdd: null, NetworkMode: environment.runtime.backendNetworkName, PortBindings: {}, PublishAllPorts: false,
      Devices: [], DeviceRequests: nvidia ? [{ Driver: "nvidia", Count: 1, DeviceIDs: null, Capabilities: [["gpu"]], Options: {} }] : [], DeviceCgroupRules: null,
      Mounts: [{ Type: "bind", Source: configuration.modelSource.path, Target: "/models/model.gguf", ReadOnly: true }], Binds: null, VolumesFrom: null, VolumeDriver: "",
      Tmpfs: { "/tmp": "rw,nosuid,nodev,noexec,size=128m,mode=1777", "/var/cache/pixel-model": "rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=10001,gid=10001" },
      Memory: 4096 * 1024 * 1024, MemorySwap: 4096 * 1024 * 1024, NanoCpus: 2_000_000_000, PidsLimit: 256, ShmSize: 128 * 1024 * 1024,
      Ulimits: [{ Name: "nofile", Hard: 1024, Soft: 1024 }], RestartPolicy: { Name: configuration.restartPolicy ?? "unless-stopped", MaximumRetryCount: 0 }, LogConfig: { Type: "local", Config: { "compress": "false", "max-file": "1", "max-size": "1m" } },
      IpcMode: "private", CgroupnsMode: "private", Init: true, AutoRemove: false, Links: null, Dns: null, DnsOptions: [], DnsSearch: [], ExtraHosts: null, GroupAdd: null,
      PidMode: "", UTSMode: "", UsernsMode: "", Isolation: "", Runtime: "runc", OomKillDisable: false, OomScoreAdj: 0, MemoryReservation: 0,
    },
    Mounts: [
      { Type: "bind", Source: configuration.modelSource.path, Destination: "/models/model.gguf", RW: false },
      { Type: "tmpfs", Source: "", Destination: "/tmp", RW: true },
      { Type: "tmpfs", Source: "", Destination: "/var/cache/pixel-model", RW: true },
    ],
    NetworkSettings: { Networks: { [environment.runtime.backendNetworkName]: { Aliases: ["pixel-local-model"], IPAddress: "172.31.255.2", IPPrefixLen: 29, IPAMConfig: null, Links: null, DriverOpts: null } } },
  };
  const network = {
    Name: environment.runtime.backendNetworkName, Driver: "bridge", Internal: true, Attachable: false, Ingress: false,
    Scope: "local", EnableIPv6: false, ConfigOnly: false, Labels: modelBackendNetworkIdentityLabels(prepared), Options: {},
    IPAM: { Driver: "default", Options: {}, Config: [{ Subnet: "172.31.255.0/29", Gateway: "172.31.255.1" }] },
    Containers: { [containerId]: { Name: environment.runtime.backendContainerName } },
  };
  const objects = { image, container, network };
  const inspect = async (kind) => structuredClone(objects[kind]);
  let artifactMeasurements = 0;
  const buildArtifact = async () => { artifactMeasurements += 1; return structuredClone(artifactManifest); };
  return { configPath, environmentPath, policy, environment, configuration, artifactManifest, launch, objects, inspect, buildArtifact, artifactMeasurements: () => artifactMeasurements };
}

test("operator inspection proves the exact contained backend without exposing private identity", async (t) => {
  const value = await fixture(t);
  validateModelBackendInspect(value.objects.container, value.objects.image, { policy: value.policy, bindings: value.launch.bindings, plan: { model: { backendImageDigest: value.policy.localModel.imageDigest, id: value.policy.localModel.id } } }, { ...value.environment.runtime, backendAlias: "pixel-local-model" });
  const status = await runModelBackendCommand(["inspect", "--config", value.configPath], { now, inspect: value.inspect, buildArtifact: value.buildArtifact });
  assert.deepEqual(validateWorkModelBackendStatus(status), []);
  assert.equal(status.state, "ready", JSON.stringify(status));
  assert.equal(value.artifactMeasurements(), 2);
  assert.deepEqual(status.checks, { artifactBound: true, launchBound: true, dockerReachable: true, imageBound: true, containerRunning: true, identityBound: true, acceleratorBound: true, filesystemBound: true, environmentBound: true, resourcesBound: true, privateNetworkBound: true });
  const encoded = JSON.stringify(status);
  for (const forbidden of [value.policy.localModel.id, value.policy.localModel.imageDigest, value.environment.runtime.backendContainerName, value.environment.policyPath]) assert.doesNotMatch(encoded, new RegExp(forbidden.replaceAll("\\", "\\\\"), "u"));
  assert.equal(status.authority.startsContainer, false);
});

test("artifact review breaks the policy bootstrap cycle and render writes one private measured manifest", async (t) => {
  const value = await fixture(t), outputPath = join(value.configPath, "..", "artifact-manifest.json");
  let measurements = 0;
  const buildArtifact = async ({ sourcePath, kind, expectedOwnerUid, readerUid, readerGid }) => {
    measurements += 1;
    assert.equal(sourcePath, value.configuration.modelSource.path);
    assert.equal(kind, value.configuration.modelSource.kind);
    assert.ok(Number.isSafeInteger(expectedOwnerUid));
    assert.deepEqual({ readerUid, readerGid }, { readerUid: value.configuration.containerUser.uid, readerGid: value.configuration.containerUser.gid });
    return structuredClone(value.artifactManifest);
  };
  const review = await runModelBackendCommand(["artifact-review", "--config", value.configPath], { now, buildArtifact });
  assert.deepEqual(validateWorkModelArtifactReview(review), []);
  assert.equal(measurements, 0);
  assert.equal(review.changes.readsModelBytesOnRender, true);
  assert.equal(review.authority.grantsExecution, false);
  assert.doesNotMatch(JSON.stringify(review), new RegExp(value.configuration.modelSource.path.replaceAll("\\", "\\\\"), "u"));
  assert.doesNotMatch(JSON.stringify(review), new RegExp(value.artifactManifest.artifactSha256, "u"));
  await assert.rejects(
    runModelBackendCommand(["artifact-render", "--config", value.configPath, "--output", outputPath, "--confirm-artifact-measurement-sha256", digest("0")], { now, buildArtifact }),
    /confirmation differs from the exact private artifact measurement request/u,
  );
  assert.equal(measurements, 0);
  const receipt = await runModelBackendCommand(["artifact-render", "--config", value.configPath, "--output", outputPath, "--confirm-artifact-measurement-sha256", review.artifactMeasurementSha256], { now, buildArtifact });
  assert.deepEqual(validateWorkModelArtifactRenderReceipt(receipt), []);
  assert.equal(measurements, 1);
  assert.equal(receipt.status, "private-inert-manifest-written");
  assert.equal(receipt.authority.startsContainer, false);
  assert.doesNotMatch(JSON.stringify(receipt), new RegExp(value.artifactManifest.artifactSha256, "u"));
  const manifest = JSON.parse(await readFile(outputPath, "utf8"));
  assert.deepEqual(validateWorkModelArtifactManifest(manifest), []);
  assert.deepEqual(manifest, value.artifactManifest);
  if (process.platform !== "win32") assert.equal((await stat(outputPath)).mode & 0o077, 0);
});

test("operator lifecycle review and confirmed idempotent start stay on the content-free CLI path", async (t) => {
  const value = await fixture(t);
  const review = await runModelBackendCommand(["start-review", "--config", value.configPath], { now, inspect: value.inspect, buildArtifact: value.buildArtifact });
  assert.deepEqual(validateWorkModelBackendLifecycleReview(review), []);
  const receipt = await runModelBackendCommand(["start", "--config", value.configPath, "--confirm-lifecycle-sha256", review.lifecycleSha256], {
    now, inspect: value.inspect, buildArtifact: value.buildArtifact, readinessProbe: async () => true, sleeper: async () => {},
  });
  assert.deepEqual(validateWorkModelBackendLifecycleReceipt(receipt), []);
  assert.equal(receipt.state, "ready-already-running");
  assert.equal(receipt.changes.imagePulled, false);
  assert.equal(value.artifactMeasurements(), 3);
});

test("emergency halt CLI never measures model bytes or requires the pinned image", async (t) => {
  const value = await fixture(t); let artifactCalls = 0, imageInspections = 0;
  const inspect = async (kind) => {
    if (kind === "image") { imageInspections += 1; throw new Error("image deliberately unavailable"); }
    return structuredClone(value.objects[kind]);
  };
  const buildArtifact = async () => { artifactCalls += 1; throw new Error("model bytes deliberately unavailable"); };
  const review = await runModelBackendCommand(["halt-review", "--config", value.configPath], { now, inspect, buildArtifact });
  assert.deepEqual(validateWorkModelBackendHaltReview(review), []);
  assert.equal(review.observation.state, "running"); assert.equal(artifactCalls, 0); assert.equal(imageInspections, 0);
  const lifecycleExecutor = async (_command, args) => {
    assert.deepEqual(args.slice(0, 4), ["container", "stop", "--time", "30"]);
    value.objects.container.State.Running = false; value.objects.container.State.Status = "exited";
    return { stdout: `${value.objects.container.Id}\n`, stderr: "" };
  };
  const receipt = await runModelBackendCommand(["halt", "--config", value.configPath, "--confirm-halt-sha256", review.haltSha256], { now, inspect, buildArtifact, lifecycleExecutor });
  assert.deepEqual(validateWorkModelBackendHaltReceipt(receipt), []);
  assert.equal(receipt.state, "halted"); assert.equal(artifactCalls, 0); assert.equal(imageInspections, 0);
  assert.ok(value.objects.container); assert.ok(value.objects.network);
});

test("exact NVIDIA inspection normalizes Docker's null unspecified device-ID list", async (t) => {
  const value = await fixture(t, { nvidia: true });
  const status = await runModelBackendCommand(["inspect", "--config", value.configPath], { now, inspect: value.inspect, buildArtifact: value.buildArtifact });
  assert.equal(status.state, "ready", JSON.stringify(status));
  const changed = structuredClone(value.objects); changed.container.HostConfig.DeviceRequests[0].Capabilities = [["gpu", "utility"]];
  const incompatible = await runModelBackendCommand(["inspect", "--config", value.configPath], { now, inspect: async (kind) => changed[kind], buildArtifact: value.buildArtifact });
  assert.equal(incompatible.state, "incompatible");
});

test("empty image credential placeholders remain inspectable while populated credentials fail closed", async (t) => {
  const empty = await fixture(t);
  empty.objects.image.Config.Env.push("HF_TOKEN=", "VLLM_MULTI_STREAM_GEMM_TOKEN_THRESHOLD=2048");
  empty.objects.container.Config.Env.push("HF_TOKEN=", "VLLM_MULTI_STREAM_GEMM_TOKEN_THRESHOLD=2048");
  const inspectable = await runModelBackendCommand(["inspect", "--config", empty.configPath], {
    now, inspect: empty.inspect, buildArtifact: empty.buildArtifact,
  });
  assert.equal(inspectable.state, "ready", JSON.stringify(inspectable));

  const populated = await fixture(t);
  populated.objects.image.Config.Env.push("HF_TOKEN=secret");
  populated.objects.container.Config.Env.push("HF_TOKEN=secret");
  const incompatible = await runModelBackendCommand(["inspect", "--config", populated.configPath], {
    now, inspect: populated.inspect, buildArtifact: populated.buildArtifact,
  });
  assert.equal(incompatible.state, "incompatible");

  const credentialFile = await fixture(t);
  credentialFile.objects.image.Config.Env.push("HF_TOKEN_FILE=/run/secrets/hf-token");
  credentialFile.objects.container.Config.Env.push("HF_TOKEN_FILE=/run/secrets/hf-token");
  const credentialFileStatus = await runModelBackendCommand(["inspect", "--config", credentialFile.configPath], {
    now, inspect: credentialFile.inspect, buildArtifact: credentialFile.buildArtifact,
  });
  assert.equal(credentialFileStatus.state, "incompatible");
});

test("container inspection accepts Docker shell normalization and the exact configured restart policy", async (t) => {
  const value = await fixture(t, { restartPolicy: "no" });
  value.objects.image.Config.Shell = ["/bin/bash", "-c"];
  value.objects.container.Config.Shell = null;
  const status = await runModelBackendCommand(["inspect", "--config", value.configPath], {
    now, inspect: value.inspect, buildArtifact: value.buildArtifact,
  });
  assert.equal(status.state, "ready", JSON.stringify(status));

  value.objects.container.Config.Shell = ["/bin/foreign-shell", "-c"];
  const incompatible = await runModelBackendCommand(["inspect", "--config", value.configPath], {
    now, inspect: value.inspect, buildArtifact: value.buildArtifact,
  });
  assert.equal(incompatible.state, "incompatible");
});

test("missing runtime is unavailable and substitutions are incompatible without leaking detail", async (t) => {
  const value = await fixture(t);
  const unavailable = await runModelBackendCommand(["inspect", "--config", value.configPath], { now, inspect: async () => { throw new Error("private daemon detail"); }, buildArtifact: value.buildArtifact });
  assert.equal(unavailable.state, "unavailable");
  assert.equal(unavailable.checks.dockerReachable, false);
  assert.doesNotMatch(JSON.stringify(unavailable), /private daemon detail/u);

  let raceMeasurements = 0;
  const raced = await runModelBackendCommand(["inspect", "--config", value.configPath], {
    now, inspect: value.inspect,
    buildArtifact: async () => {
      raceMeasurements += 1;
      if (raceMeasurements === 1) return structuredClone(value.artifactManifest);
      const changed = structuredClone(value.artifactManifest);
      changed.artifactSha256 = digest("0"); changed.files[0].sha256 = digest("0");
      return changed;
    },
  });
  assert.equal(raced.state, "incompatible");
  assert.equal(raceMeasurements, 2);

  for (const mutate of [
    (objects) => { objects.image.RepoDigests = []; },
    (objects) => { objects.container.Config.Labels["com.osmantic.pixel.work-model-artifact-sha256"] = digest("0"); },
    (objects) => { objects.container.HostConfig.NetworkMode = "host"; },
    (objects) => { objects.container.HostConfig.Devices = [{ PathOnHost: "/dev/mem" }]; },
    (objects) => { objects.network.Containers[digest("7")] = { Name: "unexpected" }; },
    (objects) => { objects.container.Mounts[0].Source = "/var/run/docker.sock"; },
    (objects) => { objects.container.HostConfig.Memory += 1; },
    (objects) => { objects.container.Config.Env.push("HF_TOKEN=secret"); },
    (objects) => { objects.container.Config.Cmd.push("--trust-remote-code"); },
    (objects) => { objects.container.HostConfig.Tmpfs["/var/cache/pixel-model"] = "rw,nosuid,nodev,exec,size=64m,mode=0700,uid=10001,gid=10001"; },
    (objects) => { objects.network.IPAM.Config[0].Subnet = "172.31.254.0/29"; },
    (objects) => { objects.container.HostConfig.RestartPolicy.Name = "always"; },
    (objects) => { objects.container.HostConfig.LogConfig.Config["max-size"] = "10m"; },
    (objects) => { objects.container.HostConfig.LogConfig.Config.compress = "true"; },
    (objects) => { objects.container.HostConfig.ExtraHosts = ["host.docker.internal:host-gateway"]; },
    (objects) => { objects.network.EnableIPv4 = false; },
    (objects) => { objects.network.Options["com.docker.network.enable_ipv4"] = "false"; },
    (objects) => { objects.container.Config.Healthcheck = { Test: ["CMD-SHELL", "curl attacker.invalid"] }; },
    (objects) => { objects.container.Config.Shell = ["/bin/host-shell"]; },
    (objects) => { objects.container.NetworkSettings.Ports = { "9999/tcp": [{ HostIp: "0.0.0.0", HostPort: "9999" }] }; },
    (objects) => { objects.container.Config.ExposedPorts = { "9999/tcp": {} }; },
    (objects) => { objects.container.Config.MacAddress = "02:42:ac:11:00:ff"; },
    (objects) => { objects.container.HostConfig.Sysctls = { "net.ipv4.ip_forward": "1" }; },
    (objects) => { objects.container.HostConfig.CpuShares = 1024; },
    (objects) => { objects.container.HostConfig.DeviceRequests = [{ Driver: "nvidia", Count: 1, DeviceIDs: null, Capabilities: [["gpu"]], Options: {} }]; },
    (objects) => { objects.container.NetworkSettings.Networks[value.environment.runtime.backendNetworkName].Aliases.push("shadow-alias"); },
    (objects) => { objects.network.Labels["com.osmantic.pixel.work-model-configuration-sha256"] = digest("0"); },
  ]) {
    const objects = structuredClone(value.objects); mutate(objects);
    const status = await runModelBackendCommand(["inspect", "--config", value.configPath], { now, inspect: async (kind) => objects[kind], buildArtifact: value.buildArtifact });
    assert.equal(status.state, "incompatible", mutate.toString());
    assert.equal(status.nextAction, "inspect-private-backend-configuration");
  }
});

test("backend inspection refuses malformed command and non-private contract inputs", async (t) => {
  const value = await fixture(t);
  await assert.rejects(runModelBackendCommand(["inspect", "--config", value.configPath, "extra"], { inspect: value.inspect, buildArtifact: value.buildArtifact }), /Usage/u);
  const duplicateConfigPath = join(value.configPath, "..", "duplicate-backend.json");
  const duplicateConfig = (await readFile(value.configPath, "utf8")).replace('"schemaVersion": 1,', '"schemaVersion": 1,\n  "schemaVersion": 1,');
  await writeFile(duplicateConfigPath, duplicateConfig, { mode: 0o600 });
  await assert.rejects(runModelBackendCommand(["inspect", "--config", duplicateConfigPath], { inspect: value.inspect, buildArtifact: value.buildArtifact }), /duplicate object key/u);
  const malformed = JSON.parse(await readFile(value.environmentPath, "utf8"));
  malformed.runtime.backendNetworkName = "UPPERCASE";
  await privateWrite(value.environmentPath, malformed);
  await assert.rejects(runModelBackendCommand(["inspect", "--config", value.configPath], { inspect: value.inspect, buildArtifact: value.buildArtifact }), /environment is invalid/u);
});

test("review remeasures exact bytes and confirmation writes only one private inert launch", async (t) => {
  const value = await fixture(t), sourcePath = join(value.environmentPath, "..");
  const configPath = join(sourcePath, "backend-config.json"), outputPath = join(sourcePath, "launch.json");
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath: value.environmentPath, modelSource: { kind: "file", path: join(sourcePath, "model.gguf") }, runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  await privateWrite(configPath, configuration);
  let measurements = 0, launches = 0;
  const dependencies = {
    now,
    buildArtifact: async ({ sourcePath: selected, kind, readerUid, readerGid }) => {
      measurements += 1; assert.equal(selected, configuration.modelSource.path); assert.equal(kind, "file");
      assert.deepEqual({ readerUid, readerGid }, { readerUid: configuration.containerUser.uid, readerGid: configuration.containerUser.gid });
      return { kind: "file", artifactSha256: value.policy.localModel.modelArtifactSha256 };
    },
    buildLaunch: ({ artifactManifest }) => {
      launches += 1; assert.equal(artifactManifest.artifactSha256, value.policy.localModel.modelArtifactSha256);
      return { schemaVersion: 1, privateSourceMarker: configuration.modelSource.path, inert: true };
    },
  };
  const review = await runModelBackendCommand(["review", "--config", configPath], dependencies);
  assert.deepEqual(validateWorkModelBackendReview(review), []);
  assert.equal(review.changes.createsNetwork, false); assert.equal(review.authority.startsContainer, false);
  assert.equal(measurements, 1); assert.equal(launches, 1);
  assert.doesNotMatch(JSON.stringify(review), /model\.gguf|assistant-model/u);
  await assert.rejects(runModelBackendCommand(["render", "--config", configPath, "--output", outputPath, "--confirm-launch-bundle-sha256", "0".repeat(64)], dependencies), /confirmation differs/u);
  assert.equal(measurements, 2); assert.equal(launches, 2);
  const rendered = await runModelBackendCommand(["render", "--config", configPath, "--output", outputPath, "--confirm-launch-bundle-sha256", review.launchBundleSha256], dependencies);
  assert.equal(rendered.status, "private-inert-launch-written"); assert.equal(rendered.authority.startsContainer, false);
  assert.equal(measurements, 3); assert.equal(launches, 3);
  const launch = JSON.parse(await readFile(outputPath, "utf8"));
  assert.equal(launch.privateSourceMarker, configuration.modelSource.path); assert.equal(launch.inert, true);
  if (process.platform !== "win32") assert.equal((await stat(outputPath)).mode & 0o077, 0);
  await assert.rejects(runModelBackendCommand(["render", "--config", configPath, "--output", outputPath, "--confirm-launch-bundle-sha256", review.launchBundleSha256], dependencies), /must be a new private file/u);
});

test("real supported-host review hashes bytes and renders the exact inert launch", { skip: process.platform === "win32" ? "owner/mode and Linux Docker-vector qualification requires POSIX" : false }, async (t) => {
  const root = await fixture(t), modelPath = join(root.environmentPath, "..");
  const selectedModel = join(modelPath, "real-model.gguf"), policyPath = root.environment.policyPath;
  const original = Buffer.from("supported-host-exact-model-bytes", "utf8");
  await writeFile(selectedModel, original, { mode: 0o600 });
  root.policy.localModel.modelArtifactSha256 = createHash("sha256").update(original).digest("hex");
  await privateWrite(policyPath, root.policy);
  root.environment.runtime.dockerPath = "/opt/pixel-test/absent-docker";
  await privateWrite(root.environmentPath, root.environment);
  const configPath = join(modelPath, "real-backend-config.json"), outputPath = join(modelPath, "real-launch.json");
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath: root.environmentPath, modelSource: { kind: "file", path: selectedModel }, runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: process.geteuid(), gid: process.getegid() }, publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 30, probeIntervalMilliseconds: 100 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  await privateWrite(configPath, configuration);
  const review = await runModelBackendCommand(["review", "--config", configPath], { now });
  assert.deepEqual(validateWorkModelBackendReview(review), []);
  const cli = fileURLToPath(new URL("../deploy/work-controller/model-backend-cli.mjs", import.meta.url));
  const reviewedByCli = await execute(process.execPath, [cli, "review", "--config", configPath], { encoding: "utf8", maxBuffer: 1024 * 1024 });
  assert.equal(reviewedByCli.stderr, "");
  assert.equal(JSON.parse(reviewedByCli.stdout).launchBundleSha256, review.launchBundleSha256);
  const startReviewedByCli = await execute(process.execPath, [cli, "start-review", "--config", configPath], { encoding: "utf8", maxBuffer: 1024 * 1024 });
  const stopReviewedByCli = await execute(process.execPath, [cli, "stop-review", "--config", configPath], { encoding: "utf8", maxBuffer: 1024 * 1024 });
  assert.deepEqual(validateWorkModelBackendLifecycleReview(JSON.parse(startReviewedByCli.stdout)), []);
  assert.deepEqual(validateWorkModelBackendLifecycleReview(JSON.parse(stopReviewedByCli.stdout)), []);
  assert.equal(startReviewedByCli.stderr, ""); assert.equal(stopReviewedByCli.stderr, "");
  await assert.rejects(execute(process.execPath, [cli, "inspect", "--config", configPath], { encoding: "utf8", maxBuffer: 1024 * 1024 }), (error) => error?.code === 2 && JSON.parse(error.stdout).state === "unavailable");
  await writeFile(selectedModel, "changed-after-review", { mode: 0o600 });
  await assert.rejects(runModelBackendCommand(["render", "--config", configPath, "--output", outputPath, "--confirm-launch-bundle-sha256", review.launchBundleSha256], { now }), /measured model artifact differs/u);
  await writeFile(selectedModel, original, { mode: 0o600 });
  const rendered = await runModelBackendCommand(["render", "--config", configPath, "--output", outputPath, "--confirm-launch-bundle-sha256", review.launchBundleSha256], { now });
  assert.equal(rendered.status, "private-inert-launch-written");
  const launch = JSON.parse(await readFile(outputPath, "utf8"));
  assert.deepEqual(validateWorkModelBackendLaunch(launch), []);
  assert.equal(launch.bindings.artifactSha256, root.policy.localModel.modelArtifactSha256);
  assert.equal(launch.container.args[0], "container"); assert.equal(launch.container.args[1], "create");
  assert.equal((await stat(outputPath)).mode & 0o077, 0);
  const cliOutputPath = join(modelPath, "real-cli-launch.json");
  const renderedByCli = await execute(process.execPath, [cli, "render", "--config", configPath, "--output", cliOutputPath, "--confirm-launch-bundle-sha256", review.launchBundleSha256], { encoding: "utf8", maxBuffer: 1024 * 1024 });
  assert.equal(renderedByCli.stderr, "");
  assert.equal(JSON.parse(renderedByCli.stdout).status, "private-inert-launch-written");
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import test from "node:test";

import { buildModelBackendLaunch, buildModelBackendStaticIdentity, modelBackendLaunchSha256 } from "../deploy/work-controller/model-backend-launch.mjs";
import {
  buildModelBackendLifecycleReview, modelBackendLifecycleSha256, probeModelBackendHealth, startModelBackend, stopModelBackend,
} from "../deploy/work-controller/model-backend-lifecycle.mjs";
import {
  buildModelBackendHaltReview, haltModelBackend, modelBackendStaticIdentitySha256,
} from "../deploy/work-controller/model-backend-halt.mjs";
import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels } from "../deploy/work-runner/docker-boundary.mjs";
import {
  validateWorkModelBackendHaltReceipt, validateWorkModelBackendHaltReview, validateWorkModelBackendLifecycleReceipt,
  validateWorkModelBackendLifecycleReview, validateWorkModelBackendLiveQualification,
} from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const now = new Date("2026-08-12T12:00:00.000Z");
const artifactBoundary = "Private deterministic identity for exact owner-selected local-model bytes. It grants no model trust, capability, execution, container start, network, device, credential, external-effect, or completion authority.";

async function withHealthServer(handler, run) {
  const server = createServer(handler);
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  try { return await run(server.address().port); }
  finally { await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())); }
}

async function fixture(initial = "absent", { publishLoopbackPort = null, effectivePublication = true, healthcheck = false } = {}) {
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.localModel.acceleratorClass = "cpu";
  const environment = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-controller-environment.example.json", import.meta.url), "utf8"));
  const configuration = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json", schemaVersion: 1,
    environmentPath: "/etc/pixel-work/environment.json", modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" }, runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" }, containerUser: { uid: 10001, gid: 10001 }, publishLoopbackPort,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 10, probeIntervalMilliseconds: 5000 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const artifactManifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json", schemaVersion: 1,
    kind: "file", artifactSha256: policy.localModel.modelArtifactSha256, fileCount: 1, totalBytes: 42,
    files: [{ relativePath: "model", bytes: 42, sha256: policy.localModel.modelArtifactSha256 }],
    authority: { grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false }, boundary: artifactBoundary,
  };
  const launch = buildModelBackendLaunch({ configuration, environment, policy, artifactManifest });
  const prepared = { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest: null, launch, launchBundleSha256: modelBackendLaunchSha256(launch) };
  const explicitEnvironment = [];
  for (let index = 0; index < launch.container.args.length; index += 1) if (launch.container.args[index] === "--env") explicitEnvironment.push(launch.container.args[++index]);
  const image = {
    Id: `sha256:${digest("9")}`, RepoDigests: [policy.localModel.imageRef],
    Config: { Env: ["PATH=/usr/bin:/bin", "BACKEND_VERSION=fixture"], Labels: { "org.opencontainers.image.title": "fixture" }, Entrypoint: ["/usr/bin/llama-server"], WorkingDir: "/", ...(healthcheck ? { Healthcheck: { Test: ["CMD", "/usr/bin/health"] } } : {}) },
  };
  const containerId = digest("8"), networkId = digest("7"), imageIndex = launch.container.args.indexOf(policy.localModel.imageRef);
  const labels = modelBackendIdentityLabels({ policy, bindings: launch.bindings });
  const dormantContainer = () => ({
    Id: containerId, Name: `/${environment.runtime.backendContainerName}`, Image: image.Id,
    State: { Status: "created", Running: false, Paused: false, Restarting: false, OOMKilled: false, Dead: false, ExitCode: 0 },
    Config: {
      Hostname: "pixel-local-model", User: "10001:10001", Tty: false, OpenStdin: false, StdinOnce: false,
      Env: [...explicitEnvironment, ...image.Config.Env], Cmd: launch.container.args.slice(imageIndex + 1), Image: policy.localModel.imageRef,
      Entrypoint: image.Config.Entrypoint, WorkingDir: image.Config.WorkingDir, StopSignal: "SIGTERM", StopTimeout: 30,
      Healthcheck: image.Config.Healthcheck, Shell: image.Config.Shell, ExposedPorts: publishLoopbackPort === null ? undefined : { "8080/tcp": {} }, Labels: { ...image.Config.Labels, ...labels },
    },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false, CapAdd: null,
      NetworkMode: environment.runtime.backendNetworkName, PortBindings: publishLoopbackPort === null ? {} : { "8080/tcp": [{ HostIp: "127.0.0.1", HostPort: String(publishLoopbackPort) }] }, PublishAllPorts: false, Devices: [], DeviceRequests: [], DeviceCgroupRules: null,
      Mounts: [{ Type: "bind", Source: configuration.modelSource.path, Target: "/models/model.gguf", ReadOnly: true }], Binds: null, VolumesFrom: null, VolumeDriver: "",
      Tmpfs: { "/tmp": "rw,nosuid,nodev,noexec,size=128m,mode=1777", "/var/cache/pixel-model": "rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=10001,gid=10001" },
      Memory: 4096 * 1024 * 1024, MemorySwap: 4096 * 1024 * 1024, NanoCpus: 2_000_000_000, PidsLimit: 256, ShmSize: 128 * 1024 * 1024,
      Ulimits: [{ Name: "nofile", Hard: 1024, Soft: 1024 }], RestartPolicy: { Name: "unless-stopped", MaximumRetryCount: 0 }, LogConfig: { Type: "local", Config: { "compress": "false", "max-file": "1", "max-size": "1m" } },
      IpcMode: "private", CgroupnsMode: "private", Init: true, AutoRemove: false, Links: null, Dns: null, DnsOptions: [], DnsSearch: [], ExtraHosts: null, GroupAdd: null,
      PidMode: "", UTSMode: "", UsernsMode: "", Isolation: "", Runtime: "runc", OomKillDisable: false, OomScoreAdj: 0, MemoryReservation: 0,
    },
    Mounts: [{ Type: "bind", Source: configuration.modelSource.path, Destination: "/models/model.gguf", RW: false }],
    NetworkSettings: { Ports: publishLoopbackPort === null ? {} : { "8080/tcp": [] }, Networks: { [environment.runtime.backendNetworkName]: { Aliases: ["pixel-local-model"], IPAddress: "", IPPrefixLen: 0, IPAMConfig: null, Links: null, DriverOpts: null } } },
  });
  const emptyNetwork = () => ({
    Id: networkId, Name: environment.runtime.backendNetworkName, Driver: "bridge", Internal: true, Attachable: false, Ingress: false,
    Scope: "local", EnableIPv6: false, ConfigOnly: false, Labels: modelBackendNetworkIdentityLabels({ policy, bindings: launch.bindings }), Options: {},
    IPAM: { Driver: "default", Options: {}, Config: [{ Subnet: "172.31.255.0/29", Gateway: "172.31.255.1" }] }, Containers: {},
  });
  const state = { image, network: null, container: null, logs: { stdout: "", stderr: "" } };
  if (["network", "dormant", "running"].includes(initial)) state.network = emptyNetwork();
  if (["dormant", "running"].includes(initial)) state.container = dormantContainer();
  function running(value = true) {
    if (value) {
      state.container.State = { Status: "running", Running: true, Paused: false, Restarting: false, OOMKilled: false, Dead: false, ExitCode: 0 };
      state.container.Mounts = [
        { Type: "bind", Source: configuration.modelSource.path, Destination: "/models/model.gguf", RW: false },
        { Type: "tmpfs", Source: "", Destination: "/tmp", RW: true },
        { Type: "tmpfs", Source: "", Destination: "/var/cache/pixel-model", RW: true },
      ];
      Object.assign(state.container.NetworkSettings.Networks[environment.runtime.backendNetworkName], { IPAddress: "172.31.255.2", IPPrefixLen: 29 });
      if (publishLoopbackPort !== null) {
        state.container.NetworkSettings.Ports = effectivePublication === null
          ? null
          : { "8080/tcp": effectivePublication ? [{ HostIp: "127.0.0.1", HostPort: String(publishLoopbackPort) }] : [] };
      }
      state.network.Containers = { [containerId]: { Name: environment.runtime.backendContainerName } };
    } else {
      state.container.State = { Status: "exited", Running: false, Paused: false, Restarting: false, OOMKilled: false, Dead: false, ExitCode: 0 };
      Object.assign(state.container.NetworkSettings.Networks[environment.runtime.backendNetworkName], { IPAddress: "", IPPrefixLen: 0 });
      state.network.Containers = {};
    }
  }
  if (initial === "running") running(true);
  const calls = [], failures = [];
  const failOnce = (action, afterEffect = false) => failures.push({ action, afterEffect });
  function action(args) {
    if (args[0] === "network" && args[1] === "create") return "network-create";
    if (args[0] === "container" && args[1] === "create") return "container-create";
    if (args[0] === "container" && args[1] === "start") return "container-start";
    if (args[0] === "container" && args[1] === "stop") return "container-stop";
    if (args[0] === "container" && args[1] === "rm") return "container-rm";
    if (args[0] === "container" && args[1] === "logs") return "container-logs";
    if (args[0] === "network" && args[1] === "rm") return "network-rm";
    return "unknown";
  }
  function effect(selected, args = []) {
    if (selected === "network-create") { if (state.network) throw new Error("exists"); state.network = emptyNetwork(); return `${networkId}\n`; }
    if (selected === "container-create") { if (state.container || !state.network) throw new Error("exists"); state.container = dormantContainer(); return `${containerId}\n`; }
    if (selected === "container-start") { running(true); return `${containerId}\n`; }
    if (selected === "container-stop") { running(false); return `${containerId}\n`; }
    if (selected === "container-rm") { if (state.container?.State.Running && !args.includes("--force")) throw new Error("running"); if (state.network) state.network.Containers = {}; state.container = null; return `${containerId}\n`; }
    if (selected === "network-rm") { if (Object.keys(state.network?.Containers ?? {}).length) throw new Error("active endpoints"); state.network = null; return `${networkId}\n`; }
    throw new Error("unexpected Docker command");
  }
  const executor = async (_command, args) => {
    const selected = action(args); calls.push([...args]);
    const failureIndex = failures.findIndex((entry) => entry.action === selected);
    if (failureIndex >= 0) {
      const [failure] = failures.splice(failureIndex, 1); if (failure.afterEffect) effect(selected, args); throw new Error(`injected ${selected} response loss`);
    }
    if (selected === "container-logs") return { stdout: state.logs.stdout, stderr: state.logs.stderr };
    return { stdout: effect(selected, args), stderr: "" };
  };
  const inspector = async (kind, target) => {
    if (kind === "image") return structuredClone(state.image);
    if (kind === "network") return state.network && [state.network.Id, state.network.Name].includes(target) ? structuredClone(state.network) : null;
    if (kind === "container") return state.container && [state.container.Id, environment.runtime.backendContainerName].includes(target) ? structuredClone(state.container) : null;
    throw new Error("unexpected inspection");
  };
  const options = { executor, inspector, readinessProbe: async () => true, sleeper: async () => {}, postflight: async () => prepared, now, coordinator: async (_root, operation) => operation() };
  return { prepared, state, calls, options, failOnce, running, dormantContainer, emptyNetwork, containerId, networkId };
}

test("lifecycle reviews bind separate start and stop effects to exact confirmations", async () => {
  const value = await fixture();
  const start = buildModelBackendLifecycleReview("start", value.prepared.launchBundleSha256);
  const stop = buildModelBackendLifecycleReview("stop", value.prepared.launchBundleSha256);
  assert.deepEqual(validateWorkModelBackendLifecycleReview(start), []); assert.deepEqual(validateWorkModelBackendLifecycleReview(stop), []);
  assert.notEqual(start.lifecycleSha256, stop.lifecycleSha256);
  assert.equal(start.effects.mayStartContainer, true); assert.equal(start.effects.mayStopContainer, false);
  assert.equal(stop.effects.mayStartContainer, false); assert.equal(stop.effects.mayStopContainer, true);
  assert.equal(start.lifecycleSha256, modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256));
  const widened = structuredClone(start); widened.effects.mayStopContainer = true;
  assert.notDeepEqual(validateWorkModelBackendLifecycleReview(widened), []);
});

test("live qualification evidence requires exact clean source, runtime, inference, and cleanup proof", () => {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-live-qualification-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-live-qualification", generatedAt: "2026-08-12T12:00:00.000Z",
    source: { commit: digest("1").slice(0, 40), tree: digest("2").slice(0, 40), clean: true },
    model: { origin: "public-fixed-synthetic-model", repository: "ggml-org/tiny-llamas", revision: "99dd1a73db5a37100bd4ae633f4cfce6560e1567", file: "stories15M-q4_0.gguf", bytes: 19077344, sha256: "6151b1929d7f5aa3385d9ddef3393e55587c0a55de661562322bc51dfda93a04" },
    image: { digest: "sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f" }, accelerator: { class: "nvidia-cuda", count: 1 },
    runtime: { controllerPlatform: "linux", controllerArch: "x64", nodeVersion: "v23.1.2", dockerClientVersion: "29.3.1", dockerServerVersion: "29.3.1", dockerServerOs: "linux", dockerServerArch: "amd64" },
    results: {
      startState: "ready-started", readinessPassed: true, inferenceStatus: 200, inferenceResponseBytes: 512,
      generatedContentObserved: true, haltState: "halted", haltRetainedForensics: true,
      restartState: "ready-started", restartReadinessPassed: true, stopState: "removed", cleanupComplete: true,
    },
    privacy: { promptsFromUser: false, responsesExposed: false, credentialsUsed: false, providerUsed: false, onlyPublicAndSourceHashes: true },
    boundary: "Disposable exact local Docker/GPU lifecycle, emergency halt, restart, and fixed synthetic inference qualification only. It does not establish model quality, client acceptance, production readiness, or authority for future model execution.",
  };
  assert.deepEqual(validateWorkModelBackendLiveQualification(value), []);
  for (const mutate of [
    (changed) => { changed.source.clean = false; }, (changed) => { changed.results.generatedContentObserved = false; },
    (changed) => { changed.results.haltRetainedForensics = false; }, (changed) => { changed.results.cleanupComplete = false; },
    (changed) => { changed.privacy.credentialsUsed = true; },
  ]) { const changed = structuredClone(value); mutate(changed); assert.notDeepEqual(validateWorkModelBackendLiveQualification(changed), []); }
});

test("fallback health probing is fixed to bounded IPv4 HTTP health responses", async () => {
  await withHealthServer((request, response) => { assert.equal(request.method, "GET"); assert.equal(request.url, "/health"); response.writeHead(200, { "Content-Type": "application/json" }); response.end('{"status":"ok"}'); }, async (port) => {
    assert.equal(await probeModelBackendHealth({ host: "127.0.0.1", port, timeoutMs: 500 }), true);
  });
  await withHealthServer((_request, response) => { response.writeHead(302, { Location: "http://attacker.invalid/" }); response.end(); }, async (port) => {
    assert.equal(await probeModelBackendHealth({ host: "127.0.0.1", port, timeoutMs: 500 }), false);
  });
  await withHealthServer((_request, response) => { response.writeHead(200, { "Content-Length": "9000" }); response.end("x"); }, async (port) => {
    assert.equal(await probeModelBackendHealth({ host: "127.0.0.1", port, timeoutMs: 500 }), false);
  });
  assert.equal(await probeModelBackendHealth({ host: "localhost", port: 8080, timeoutMs: 500 }), false);
  assert.equal(await probeModelBackendHealth({ host: "127.0.0.1", port: 8080, timeoutMs: 5000 }), false);
});

test("a pinned image health event takes precedence over an external probe", async () => {
  const value = await fixture("running", { healthcheck: true });
  value.state.container.State.Health = { Status: "healthy" };
  const confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  const result = await startModelBackend(value.prepared, { ...value.options, confirmation, readinessProbe: async () => { throw new Error("probe must not run"); } });
  assert.equal(result.state, "ready-already-running");
  const invalid = structuredClone(result); invalid.state = "removed";
  assert.notDeepEqual(validateWorkModelBackendLifecycleReceipt(invalid), []);
});

test("an unhealthy pinned image event fails immediately without mutating an existing backend", async () => {
  const value = await fixture("running", { healthcheck: true });
  value.state.container.State.Health = { Status: "unhealthy" };
  const confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  await assert.rejects(startModelBackend(value.prepared, { ...value.options, confirmation }), /unhealthy readiness/u);
  assert.equal(value.calls.length, 0); assert.equal(value.state.container.State.Running, true);
});

test("start creates, starts, observes readiness, and is then idempotent", async () => {
  const value = await fixture();
  const confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  const first = await startModelBackend(value.prepared, { ...value.options, confirmation });
  assert.deepEqual(validateWorkModelBackendLifecycleReceipt(first), []); assert.equal(first.state, "ready-started");
  assert.deepEqual(first.changes, { networkCreated: true, containerCreated: true, containerStarted: true, containerStopped: false, containerRemoved: false, networkRemoved: false, imagePulled: false });
  assert.equal(value.state.container.State.Running, true); assert.equal(value.state.network.Containers[value.containerId].Name, value.prepared.environment.runtime.backendContainerName);
  assert.equal(value.calls.some((args) => args[0] === "image" && args[1] === "pull"), false);
  const count = value.calls.length;
  const second = await startModelBackend(value.prepared, { ...value.options, confirmation });
  assert.equal(second.state, "ready-already-running"); assert.equal(value.calls.length, count);
});

test("stop removes only the exact idle backend and is then idempotent", async () => {
  const value = await fixture("running");
  const confirmation = modelBackendLifecycleSha256("stop", value.prepared.launchBundleSha256);
  const first = await stopModelBackend(value.prepared, { ...value.options, confirmation });
  assert.deepEqual(validateWorkModelBackendLifecycleReceipt(first), []); assert.equal(first.state, "removed");
  assert.equal(first.changes.containerStopped, true); assert.equal(first.changes.containerRemoved, true); assert.equal(first.changes.networkRemoved, true);
  assert.equal(value.state.container, null); assert.equal(value.state.network, null);
  const count = value.calls.length;
  const second = await stopModelBackend(value.prepared, { ...value.options, confirmation });
  assert.equal(second.state, "already-absent"); assert.equal(value.calls.length, count);
});

test("stop can safely remove an exact backend whose requested publication is degraded", async () => {
  const value = await fixture("running", { publishLoopbackPort: 45678, effectivePublication: false });
  const confirmation = modelBackendLifecycleSha256("stop", value.prepared.launchBundleSha256);
  const result = await stopModelBackend(value.prepared, { ...value.options, confirmation });
  assert.equal(result.state, "removed"); assert.equal(value.state.container, null); assert.equal(value.state.network, null);
});

test("start recovers an exact partial network or dormant container without recreating custody", async () => {
  for (const initial of ["network", "dormant"]) {
    const value = await fixture(initial), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
    const result = await startModelBackend(value.prepared, { ...value.options, confirmation });
    assert.equal(result.state, "ready-started"); assert.equal(result.recovery.resumedPartialState, true);
    assert.equal(result.changes.networkCreated, false);
    assert.equal(result.changes.containerCreated, initial === "network");
    assert.equal(result.changes.containerStarted, true);
  }
});

test("indeterminate create responses are reconciled from exact Docker state", async () => {
  const value = await fixture(), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  value.failOnce("network-create", true); value.failOnce("container-create", true);
  const result = await startModelBackend(value.prepared, { ...value.options, confirmation });
  assert.equal(result.state, "ready-started"); assert.equal(result.recovery.resumedPartialState, true);
  assert.equal(result.changes.networkCreated, false); assert.equal(result.changes.containerCreated, false); assert.equal(result.changes.containerStarted, true);
});

test("failed readiness rolls back newly created resources and restores an adopted dormant container", async () => {
  const created = await fixture(), confirmation = modelBackendLifecycleSha256("start", created.prepared.launchBundleSha256);
  await assert.rejects(startModelBackend(created.prepared, { ...created.options, confirmation, readinessProbe: async () => false }), /readiness event/u);
  assert.equal(created.state.container, null); assert.equal(created.state.network, null);

  const adopted = await fixture("dormant"), adoptedConfirmation = modelBackendLifecycleSha256("start", adopted.prepared.launchBundleSha256);
  await assert.rejects(startModelBackend(adopted.prepared, { ...adopted.options, confirmation: adoptedConfirmation, readinessProbe: async () => false }), /readiness event/u);
  assert.equal(adopted.state.container.State.Running, false); assert.notEqual(adopted.state.network, null);
});

test("post-create validation drift removes only the exact returned container identity", async () => {
  const drifted = await fixture(), confirmation = modelBackendLifecycleSha256("start", drifted.prepared.launchBundleSha256);
  const exactExecutor = drifted.options.executor;
  drifted.options.executor = async (command, args) => {
    const result = await exactExecutor(command, args);
    if (args[0] === "container" && args[1] === "create") drifted.state.container.HostConfig.Mounts[0].Target = "/models/stale-contract";
    return result;
  };
  await assert.rejects(startModelBackend(drifted.prepared, { ...drifted.options, confirmation }), /mounts differ/u);
  assert.equal(drifted.state.container, null); assert.equal(drifted.state.network, null);
  assert.ok(drifted.calls.some((args) => args[0] === "container" && args[1] === "rm" && args.at(-1) === drifted.containerId));

  const substituted = await fixture(), substitutedConfirmation = modelBackendLifecycleSha256("start", substituted.prepared.launchBundleSha256);
  const substitutedExecutor = substituted.options.executor;
  substituted.options.executor = async (command, args) => {
    const result = await substitutedExecutor(command, args);
    if (args[0] === "container" && args[1] === "create") substituted.state.container.Config.Labels["com.osmantic.pixel.work-role"] = "foreign";
    return result;
  };
  await assert.rejects(startModelBackend(substituted.prepared, { ...substituted.options, confirmation: substitutedConfirmation }), /rollback could not be completed/u);
  assert.notEqual(substituted.state.container, null); assert.notEqual(substituted.state.network, null);
  assert.equal(substituted.calls.some((args) => args[0] === "container" && args[1] === "rm"), false);
});

test("failed startup captures bounded private state and logs before exact rollback", async () => {
  const value = await fixture(), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  value.state.logs = { stdout: "vLLM startup began\n", stderr: "workspace patch import failed\n" };
  let observed;
  await assert.rejects(startModelBackend(value.prepared, {
    ...value.options, confirmation,
    readinessProbe: async () => {
      value.running(false);
      Object.assign(value.state.container.State, { ExitCode: 1, Error: "entrypoint failed", FinishedAt: "2026-08-12T12:00:01.000000000Z" });
      return false;
    },
  }), (error) => { observed = error; return /stopped before reporting/u.test(error?.message ?? ""); });
  const diagnostic = observed.modelBackendStartDiagnostic;
  assert.equal(diagnostic.operation, "pixel-work-model-backend-start-diagnostic");
  assert.equal(diagnostic.launchBundleSha256, value.prepared.launchBundleSha256);
  assert.equal(diagnostic.state.exitCode, 1); assert.equal(diagnostic.state.error, "entrypoint failed");
  assert.equal(diagnostic.logs.captureStatus, "captured");
  assert.equal(diagnostic.logs.stdout.text, "vLLM startup began\n");
  assert.equal(diagnostic.logs.stderr.text, "workspace patch import failed\n");
  assert.equal(diagnostic.logs.stdout.sha256, createHash("sha256").update(diagnostic.logs.stdout.text).digest("hex"));
  const logIndex = value.calls.findIndex((args) => args[0] === "container" && args[1] === "logs");
  const removeIndex = value.calls.findIndex((args) => args[0] === "container" && args[1] === "rm");
  assert.ok(logIndex >= 0 && removeIndex > logIndex);
  assert.equal(value.state.container, null); assert.equal(value.state.network, null);
});

test("startup diagnostics are size-bounded and capture failure never weakens rollback", async () => {
  const oversized = await fixture(), oversizedConfirmation = modelBackendLifecycleSha256("start", oversized.prepared.launchBundleSha256);
  oversized.state.logs.stdout = `${"x".repeat(192 * 1024)}END\n`;
  let oversizedError;
  await assert.rejects(startModelBackend(oversized.prepared, {
    ...oversized.options, confirmation: oversizedConfirmation,
    readinessProbe: async () => { oversized.running(false); oversized.state.container.State.ExitCode = 1; return false; },
  }), (error) => { oversizedError = error; return true; });
  assert.equal(oversizedError.modelBackendStartDiagnostic.logs.stdout.truncated, true);
  assert.ok(oversizedError.modelBackendStartDiagnostic.logs.stdout.bytes <= 128 * 1024);
  assert.match(oversizedError.modelBackendStartDiagnostic.logs.stdout.text, /END\n$/u);
  assert.equal(oversized.state.container, null); assert.equal(oversized.state.network, null);

  const unavailable = await fixture(), unavailableConfirmation = modelBackendLifecycleSha256("start", unavailable.prepared.launchBundleSha256);
  unavailable.failOnce("container-logs");
  let unavailableError;
  await assert.rejects(startModelBackend(unavailable.prepared, {
    ...unavailable.options, confirmation: unavailableConfirmation,
    readinessProbe: async () => { unavailable.running(false); unavailable.state.container.State.ExitCode = 1; return false; },
  }), (error) => { unavailableError = error; return /stopped before reporting/u.test(error?.message ?? ""); });
  assert.equal(unavailableError.modelBackendStartDiagnostic.logs.captureStatus, "unavailable");
  assert.equal(unavailableError.modelBackendStartDiagnostic.logs.stdout.bytes, 0);
  assert.equal(unavailable.state.container, null); assert.equal(unavailable.state.network, null);
});

test("readiness uses an absolute monotonic deadline even when probes consume time", async () => {
  const value = await fixture(), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  let tick = -6000, sleeps = 0;
  await assert.rejects(startModelBackend(value.prepared, {
    ...value.options, confirmation, readinessProbe: async () => false,
    monotonic: () => { tick += 6000; return tick; }, sleeper: async () => { sleeps += 1; },
  }), /safety deadline/u);
  assert.equal(sleeps, 0); assert.equal(value.state.container, null); assert.equal(value.state.network, null);
});

test("an internal-only ineffective publication diagnoses private readiness then rolls back", async () => {
  const value = await fixture("absent", { publishLoopbackPort: 45678, effectivePublication: null });
  const confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  const probes = []; let sleeps = 0;
  await assert.rejects(startModelBackend(value.prepared, {
    ...value.options, confirmation,
    readinessProbe: async (target) => { probes.push(target); return true; },
    sleeper: async () => { sleeps += 1; },
  }), /not effectively bound/u);
  assert.deepEqual(probes.map(({ host, port }) => ({ host, port })), [
    { host: "172.31.255.2", port: 8080 },
    { host: "172.31.255.2", port: 8080 },
  ]);
  assert.equal(sleeps, 1);
  assert.equal(value.state.container, null); assert.equal(value.state.network, null);
});

test("readiness waits for Docker's delayed effective loopback publication", async () => {
  const value = await fixture("absent", { publishLoopbackPort: 45678, effectivePublication: false });
  const confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  const probes = []; let sleeps = 0;
  const result = await startModelBackend(value.prepared, {
    ...value.options, confirmation,
    readinessProbe: async (target) => { probes.push(target); return true; },
    sleeper: async () => {
      sleeps += 1;
      value.state.container.NetworkSettings.Ports["8080/tcp"] = [{ HostIp: "127.0.0.1", HostPort: "45678" }];
    },
  });
  assert.equal(result.state, "ready-started");
  assert.equal(sleeps, 1);
  assert.deepEqual(probes.map(({ host, port }) => ({ host, port })), [
    { host: "172.31.255.2", port: 8080 },
    { host: "127.0.0.1", port: 45678 },
  ]);
});

test("indeterminate container creation plus failed readiness leaves a coherent resumable partial state", async () => {
  const value = await fixture(), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  value.failOnce("container-create", true);
  await assert.rejects(startModelBackend(value.prepared, { ...value.options, confirmation, readinessProbe: async () => false }), /readiness event/u);
  assert.notEqual(value.state.container, null); assert.equal(value.state.container.State.Running, false); assert.notEqual(value.state.network, null);
  const recovered = await startModelBackend(value.prepared, { ...value.options, confirmation });
  assert.equal(recovered.state, "ready-started"); assert.equal(recovered.recovery.resumedPartialState, true);
});

test("postflight input drift rolls back a newly started backend", async () => {
  const value = await fixture(), confirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  await assert.rejects(startModelBackend(value.prepared, { ...value.options, confirmation, postflight: async () => ({ ...value.prepared, launchBundleSha256: digest("0") }) }), /changed during lifecycle/u);
  assert.equal(value.state.container, null); assert.equal(value.state.network, null);
});

test("foreign peers and runtime substitution fail before lifecycle mutation", async () => {
  const peer = await fixture("network"), confirmation = modelBackendLifecycleSha256("start", peer.prepared.launchBundleSha256);
  peer.state.network.Containers[digest("6")] = { Name: "foreign-peer" };
  await assert.rejects(startModelBackend(peer.prepared, { ...peer.options, confirmation }), /unexpected peer/u);
  assert.equal(peer.calls.length, 0);

  const changed = await fixture("dormant"), changedConfirmation = modelBackendLifecycleSha256("start", changed.prepared.launchBundleSha256);
  changed.state.container.Mounts[0].Source = "/var/run/docker.sock";
  await assert.rejects(startModelBackend(changed.prepared, { ...changed.options, confirmation: changedConfirmation }), /read-only source/u);
  assert.equal(changed.calls.length, 0);

  const changedLogs = await fixture("dormant"), changedLogsConfirmation = modelBackendLifecycleSha256("start", changedLogs.prepared.launchBundleSha256);
  changedLogs.state.container.HostConfig.LogConfig.Config.compress = "true";
  await assert.rejects(startModelBackend(changedLogs.prepared, { ...changedLogs.options, confirmation: changedLogsConfirmation }), /bounded log retention/u);
  assert.equal(changedLogs.calls.length, 0);
});

test("stop refuses active peers and leaves the running backend untouched", async () => {
  const value = await fixture("running"), confirmation = modelBackendLifecycleSha256("stop", value.prepared.launchBundleSha256);
  value.state.network.Containers[digest("6")] = { Name: "active-worker" };
  await assert.rejects(stopModelBackend(value.prepared, { ...value.options, confirmation }), /unexpected peer/u);
  assert.equal(value.calls.length, 0); assert.equal(value.state.container.State.Running, true);
});

test("final preflight catches a peer race before stop mutation", async () => {
  const value = await fixture("running"), confirmation = modelBackendLifecycleSha256("stop", value.prepared.launchBundleSha256);
  const postflight = async () => { value.state.network.Containers[digest("6")] = { Name: "racing-worker" }; return value.prepared; };
  await assert.rejects(stopModelBackend(value.prepared, { ...value.options, confirmation, postflight }), /unexpected peer/u);
  assert.equal(value.calls.length, 0); assert.equal(value.state.container.State.Running, true);
});

test("lifecycle confirmations cannot be swapped or replayed across intents", async () => {
  const value = await fixture();
  const startConfirmation = modelBackendLifecycleSha256("start", value.prepared.launchBundleSha256);
  const stopConfirmation = modelBackendLifecycleSha256("stop", value.prepared.launchBundleSha256);
  await assert.rejects(startModelBackend(value.prepared, { ...value.options, confirmation: stopConfirmation }), /confirmation/u);
  await assert.rejects(stopModelBackend(value.prepared, { ...value.options, confirmation: startConfirmation }), /confirmation/u);
  assert.equal(value.calls.length, 0);
});

function haltFixture(value) {
  const staticIdentity = buildModelBackendStaticIdentity(value.prepared);
  const prepared = {
    configuration: value.prepared.configuration, environment: value.prepared.environment, policy: value.prepared.policy,
    staticIdentity, staticIdentitySha256: modelBackendStaticIdentitySha256(staticIdentity),
  };
  const options = {
    executor: value.options.executor, inspector: value.options.inspector, coordinator: async (_root, operation) => operation(), now,
    postflight: async () => prepared,
  };
  return { prepared, options };
}

test("model-independent emergency halt stops only the exact backend and retains forensic resources", async () => {
  const value = await fixture("running"), halted = haltFixture(value), inspected = [];
  const inspector = async (kind, target) => { inspected.push(kind); return value.options.inspector(kind, target); };
  const review = await buildModelBackendHaltReview(halted.prepared, { inspector });
  assert.deepEqual(validateWorkModelBackendHaltReview(review), []);
  assert.equal(review.observation.state, "running"); assert.equal(review.effects.mayStopContainer, true);
  assert.equal(review.observation.hardenedBoundaryObserved, true);
  const receipt = await haltModelBackend(halted.prepared, { ...halted.options, inspector, confirmation: review.haltSha256 });
  assert.deepEqual(validateWorkModelBackendHaltReceipt(receipt), []);
  assert.equal(receipt.state, "halted"); assert.equal(value.state.container.State.Running, false);
  assert.ok(value.state.container); assert.ok(value.state.network);
  assert.equal(value.calls.some((args) => args[0] === "container" && args[1] === "rm"), false);
  assert.equal(value.calls.some((args) => args[0] === "network" && args[1] === "rm"), false);
  assert.equal(inspected.includes("image"), false);
  for (const mutate of [
    (changed) => { changed.effects.removesResources = true; },
    (changed) => { changed.effects.mayInterruptActiveWorkers = true; },
    (changed) => { changed.privacy.paths = true; },
  ]) { const changed = structuredClone(review); mutate(changed); assert.notDeepEqual(validateWorkModelBackendHaltReview(changed), []); }
  for (const mutate of [
    (changed) => { changed.changes.containerRemoved = true; },
    (changed) => { changed.checks.modelBytesRead = true; },
    (changed) => { changed.state = "already-halted"; },
  ]) { const changed = structuredClone(receipt); mutate(changed); assert.notDeepEqual(validateWorkModelBackendHaltReceipt(changed), []); }
});

test("emergency halt recognizes the qualified path-bound cache root without launch reconstruction", async () => {
  const value = await fixture("running");
  value.state.container.HostConfig.Tmpfs["/cache"] = value.state.container.HostConfig.Tmpfs["/var/cache/pixel-model"];
  delete value.state.container.HostConfig.Tmpfs["/var/cache/pixel-model"];
  value.state.container.Mounts.find((mount) => mount.Destination === "/var/cache/pixel-model").Destination = "/cache";
  const halted = haltFixture(value);
  const review = await buildModelBackendHaltReview(halted.prepared, { inspector: value.options.inspector });
  assert.equal(review.observation.hardenedBoundaryObserved, true);
  const receipt = await haltModelBackend(halted.prepared, { ...halted.options, confirmation: review.haltSha256 });
  assert.equal(receipt.state, "halted");
  assert.equal(value.state.container.State.Running, false);
  assert.ok(value.state.container); assert.ok(value.state.network);
});

test("emergency halt remains useful for a degraded backend and warns about active peers", async () => {
  const value = await fixture("running"), peerId = digest("6");
  value.state.container.HostConfig.Privileged = true;
  value.state.network.Containers[peerId] = { Name: "active-worker-proxy" };
  const halted = haltFixture(value), review = await buildModelBackendHaltReview(halted.prepared, { inspector: value.options.inspector });
  assert.equal(review.observation.hardenedBoundaryObserved, false);
  assert.equal(review.observation.activeWorkerPeersObserved, true);
  assert.equal(review.effects.mayInterruptActiveWorkers, true);
  const receipt = await haltModelBackend(halted.prepared, { ...halted.options, confirmation: review.haltSha256 });
  assert.equal(receipt.state, "halted"); assert.equal(receipt.observation.activeWorkerPeersObserved, true);
  assert.ok(value.state.network); assert.equal(value.calls.some((args) => args[0] === "network" && args[1] === "rm"), false);
});

test("emergency halt is idempotent and reconciles a lost Docker stop response", async () => {
  const absent = await fixture("absent"), absentHalt = haltFixture(absent);
  const absentReview = await buildModelBackendHaltReview(absentHalt.prepared, { inspector: absent.options.inspector });
  const absentReceipt = await haltModelBackend(absentHalt.prepared, { ...absentHalt.options, confirmation: absentReview.haltSha256 });
  assert.equal(absentReceipt.state, "already-absent");
  const dormant = await fixture("dormant"), dormantHalt = haltFixture(dormant);
  const dormantReview = await buildModelBackendHaltReview(dormantHalt.prepared, { inspector: dormant.options.inspector });
  assert.equal((await haltModelBackend(dormantHalt.prepared, { ...dormantHalt.options, confirmation: dormantReview.haltSha256 })).state, "already-halted");
  const running = await fixture("running"), runningHalt = haltFixture(running);
  running.failOnce("container-stop", true);
  const runningReview = await buildModelBackendHaltReview(runningHalt.prepared, { inspector: running.options.inspector });
  assert.equal((await haltModelBackend(runningHalt.prepared, { ...runningHalt.options, confirmation: runningReview.haltSha256 })).state, "halted");
});

test("emergency halt confirmation binds the observed target and refuses foreign identity", async () => {
  const value = await fixture("running"), halted = haltFixture(value);
  const review = await buildModelBackendHaltReview(halted.prepared, { inspector: value.options.inspector });
  value.running(false);
  await assert.rejects(haltModelBackend(halted.prepared, { ...halted.options, confirmation: review.haltSha256 }), /confirmation differs/u);
  value.running(true); value.state.container.Config.Labels["com.osmantic.pixel.work-model-configuration-sha256"] = digest("0");
  await assert.rejects(buildModelBackendHaltReview(halted.prepared, { inspector: value.options.inspector }), /labels differ/u);
});

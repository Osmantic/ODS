import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDataLabDockerCommand,
  buildDataLabExportDockerCommand,
  buildDataLabInventoryDockerCommand,
  buildDataLabPrompt,
  buildDataLabReplayDockerCommand,
  buildDataLabReplayKeeperCommand,
  buildDataLabReplayVolumeCreate,
  buildDataLabVolumeCreate,
  buildDataLabVolumeKeeperCommand,
  DockerDataLabError,
  validateDataLabImageRuntime,
  validateDataLabKeeperInspect,
  validateDataLabVolumeInspect,
} from "../deploy/work-runner/docker-data-lab.mjs";
import { cleanupInterruptedProfileAttempt, deriveDataLabDockerRuntime, DockerSupervisorError } from "../deploy/work-runner/docker-supervisor.mjs";
import { buildModelProxyConfig } from "../deploy/work-runner/docker-boundary.mjs";
import { validateModelProxyConfig } from "../deploy/work-model-proxy/proxy.mjs";

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
  const dataRuntime = { contract: "pixel-local-data-runtime-v1", engines: {
    duckdb: { version: "1.5.5", entrypoint: "/usr/bin/python3" },
    polars: { version: "1.43.2", entrypoint: "/usr/bin/python3" },
    python: { version: "3.11.2", entrypoint: "/usr/bin/python3" },
    sqlite: { version: "3.40.1", entrypoint: "/usr/bin/sqlite3" },
  } };
  const budgets = {
    maxRuntimeSeconds: 600, maxToolCalls: 200, maxConcurrentSubagents: 1, maxModelRequests: 20,
    maxInputTokens: 100000, maxOutputTokens: 20000, maxNetworkBytes: 10485760,
    maxMemoryMiB: 2048, maxCpuCores: 2, maxDiskBytes: 1073741824, maxArtifactBytes: 16777216,
  };
  const planData = {
    mode: "local-reproducible", datasets: [{
      datasetId: "sales", inputId: "records", relativePath: "sales.csv", format: "csv",
      contentSha256: digest("9"), maxBytes: 1048576,
    }], engines: ["duckdb", "polars", "python", "sqlite"], maxArtifactFiles: 16, maxArtifactBytes: 8388608,
    allowedArtifactFormats: ["csv", "json", "markdown", "parquet", "png"], replayVerification: true, retention: "job-only",
    boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
  };
  const prepared = {
    plan: {
      jobId, profile: "data-lab", objective: "Calculate exact local sales totals.", acceptanceCriteria: ["Produce reproducible regional totals"],
      dataClassification: "confidential", inputs: [{ id: "records" }], data: planData, dataRuntime,
      outputGate: { allowedKinds: ["finding-report", "dataset", "document", "visualization"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false },
      model, executor, isolation: { runnerImageDigest: `sha256:${digest("f")}` }, budgets,
      grantedCapabilities: { tools: ["read", "search", "write", "edit", "bash"], network: { mode: "brokered", services: ["local-model"] } },
    },
    lease: { leaseId, budgets },
    policy: {
      runner: { imageRef: `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("f")}` },
      localModel: { id: "assistant-model", provider: "llama.cpp", imageRef: `ghcr.io/ggml-org/llama.cpp:server@sha256:${digest("8")}`, imageDigest: `sha256:${digest("8")}`, contextWindow: 131072, maxRequestContextTokens: 131072, supportsVision: false, maxRequestOutputTokens: 1024 },
    },
    modelQualification: {
      qualificationId: "modelqual-1786366740000-abcdef123456", receiptSha256: digest("1"), casesSha256: digest("2"), evaluatorSha256: digest("3"),
      profile: "data-lab", maxContextTokens: 131072, maxOutputTokens: 1024, exactUsage: true,
    },
    bindings: { planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d") },
    workspace: { path: "/var/lib/pixel-work/workspaces/prepared/workspace", originalPath: "/var/lib/pixel-work/workspaces/prepared/source", disposable: true, storage: "docker-tmpfs-volume", sha256: digest("7") },
    executor: { path: "/var/lib/pixel-work/executors/omp-linux-x64" },
    datasets: [{ ...planData.datasets[0], path: "/var/lib/pixel-work/workspaces/prepared/source/records/sales.csv", bytes: 37 }],
  };
  const claim = {
    status: "consumed", externalEffects: false, jobId, leaseId, claimId,
    planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d"), workspaceSha256: digest("7"),
    runnerImageDigest: `sha256:${digest("f")}`, executor, model,
  };
  const runtime = {
    dockerPath: "/usr/bin/docker", cidFile: "/var/lib/pixel-work/runs/claim/cids/worker.cid",
    keeperCidFile: "/var/lib/pixel-work/runs/claim/cids/keeper.cid", replayKeeperCidFile: "/var/lib/pixel-work/runs/claim/cids/replay-keeper.cid",
    modelRegistryPath: "/var/lib/pixel-work/runs/claim/config/models.yml",
    networkName: "pixel-work-net-abcdef123456", networkSubnet: "172.30.8.0/29", workerIp: "172.30.8.2", proxyIp: "172.30.8.3",
    modelAlias: "pixel-model", containerName: "pixel-work-data-abcdef123456", volumeName: "pixel-work-data-abcdef123456",
    replayVolumeName: "pixel-work-data-replay-abcdef123456", keeperName: "pixel-work-data-keep-abcdef123456", replayKeeperName: "pixel-work-data-replay-keep-abcdef123456",
    volumeSizeBytes: 536870912, replayVolumeSizeBytes: 536870912,
    outputDirectory: "/var/lib/pixel-work/results/workclaim-1786366800000-abcdef123456", maxResultBytes: 4194304,
    imageIdentifier: `sha256:${digest("f")}`, modelId: "assistant-model", uid: 1000, gid: 1000,
  };
  return { prepared, claim, runtime };
}

function capabilityRuntime() {
  const serviceRoot = "/var/lib/pixel-work/runs/workclaim-1786366800000-abcdef123456/capability";
  const workerRoot = `${serviceRoot}/worker`, catalogSha256 = digest("6"), catalogName = `catalog-${catalogSha256}.json`;
  return { serviceRoot, workerRoot, catalogPath: `${workerRoot}/${catalogName}`, requestDirectory: `${workerRoot}/requests`, responseDirectory: `${workerRoot}/responses`, catalogName, catalogSha256, exposedTools: ["pixel_cap_fixture_analyze_12345678"] };
}

function optionValues(args, name) {
  const values = [];
  for (let index = 0; index < args.length - 1; index += 1) if (args[index] === name) values.push(args[index + 1]);
  return values;
}

test("Data Lab worker gets broad local analysis with exact read-only datasets and no direct network", () => {
  const value = fixture();
  const command = buildDataLabDockerCommand(value.prepared, value.claim, value.runtime);
  assert.deepEqual(command.allowedTools, ["read", "grep", "glob", "write", "edit", "bash"]);
  assert.deepEqual(optionValues(command.args, "--network"), [value.runtime.networkName]);
  assert.deepEqual(optionValues(command.args, "--mount"), [
    `type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`,
    "type=bind,src=/var/lib/pixel-work/workspaces/prepared/source/records/sales.csv,dst=/inputs/sales.csv,readonly",
    `type=volume,src=${value.runtime.volumeName},dst=/workspace,volume-nocopy`,
    "type=bind,src=/var/lib/pixel-work/executors/omp-linux-x64,dst=/opt/omp,readonly",
  ]);
  assert.ok(optionValues(command.args, "--tmpfs").includes("/tmp/agent:rw,nosuid,nodev,noexec,size=512m,mode=0700,uid=1000,gid=1000"));
  assert.ok(optionValues(command.args, "--env").includes("PYTHONHASHSEED=0"));
  assert.deepEqual(optionValues(command.args, "--entrypoint"), ["/opt/pixel/deploy/work-runner/private-entrypoint.sh"]);
  assert.ok(command.args.includes("/opt/omp"));
  assert.doesNotMatch(JSON.stringify({ mounts: optionValues(command.args, "--mount"), env: optionValues(command.args, "--env") }), /docker\.sock|OPENAI_API_KEY|ANTHROPIC|credential|\/home\//i);
  const prompt = buildDataLabPrompt(value.prepared.plan);
  assert.match(prompt, /saved recipe and artifacts must be deterministic|save a deterministic Python program/i);
  assert.match(prompt, /reproduce every final artifact/);
  assert.match(prompt, /Do not claim the numbers are semantically true/);
});

test("Data Lab accepts an exact digest-only runner reference and rejects a mismatched digest", () => {
  const value = fixture();
  value.prepared.policy.runner.imageRef = value.prepared.plan.isolation.runnerImageDigest;
  const command = buildDataLabDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(command.args.includes(value.prepared.plan.isolation.runnerImageDigest));

  const mismatched = fixture();
  mismatched.prepared.policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("9")}`;
  assert.throws(() => buildDataLabDockerCommand(mismatched.prepared, mismatched.claim, mismatched.runtime), DockerDataLabError);
});

test("Data Lab export, replay, and inventory are distinct hardened no-network stages", () => {
  const value = fixture();
  const exporter = buildDataLabExportDockerCommand(value.prepared, value.claim, value.runtime, { now: new Date("2026-08-10T13:00:03Z"), suffix: "123456abcdef" });
  const replay = buildDataLabReplayDockerCommand(value.prepared, value.claim, value.runtime);
  const inventory = buildDataLabInventoryDockerCommand(value.prepared, value.claim, value.runtime);
  for (const command of [exporter, replay, inventory]) {
    assert.deepEqual(optionValues(command.args, "--network"), ["none"]);
    for (const required of ["--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--ipc", "none", "--log-driver", "none"]) assert.ok(command.args.includes(required), required);
  }
  assert.ok(exporter.args.some((arg) => arg === `type=volume,src=${value.runtime.volumeName},dst=/workspace,readonly,volume-nocopy`));
  assert.ok(exporter.args.some((arg) => arg === `type=bind,src=${value.runtime.outputDirectory},dst=/output`));
  assert.ok(replay.args.includes("/candidate/recipe.py"));
  assert.deepEqual(optionValues(replay.args, "--entrypoint"), ["/opt/pixel/deploy/work-runner/private-entrypoint.sh"]);
  assert.ok(replay.args.includes("/usr/bin/python3"));
  assert.ok(replay.args.includes("--input-root"));
  assert.ok(replay.args.includes("--output-root"));
  assert.ok(replay.args.some((arg) => arg === `type=volume,src=${value.runtime.replayVolumeName},dst=/replay,volume-nocopy`));
  assert.equal(optionValues(replay.args, "--mount").some((arg) => arg === `type=volume,src=${value.runtime.volumeName},dst=/workspace,volume-nocopy`), false);
  assert.ok(inventory.args.some((arg) => arg === `type=volume,src=${value.runtime.replayVolumeName},dst=/replay,readonly,volume-nocopy`));
  assert.equal(JSON.stringify(replay).includes("pixel-model"), false);
});

test("Data Lab exposes an authorized capability only to the analysis worker", () => {
  const value = fixture(); value.runtime.capability = capabilityRuntime();
  const worker = buildDataLabDockerCommand(value.prepared, value.claim, value.runtime);
  assert.deepEqual(optionValues(worker.args, "--trusted-extension"), ["/opt/pixel/deploy/work-runner/capability-tool.mjs"]);
  assert.deepEqual(optionValues(worker.args, "--mount").slice(-3), [
    `type=bind,src=${value.runtime.capability.catalogPath},dst=/run/pixel/capability/${value.runtime.capability.catalogName},readonly`,
    `type=bind,src=${value.runtime.capability.requestDirectory},dst=/run/pixel/capability/requests`,
    `type=bind,src=${value.runtime.capability.responseDirectory},dst=/run/pixel/capability/responses,readonly`,
  ]);
  assert.deepEqual(worker.allowedTools, ["bash", "edit", "glob", "grep", "pixel_cap_fixture_analyze_12345678", "read", "write"]);
  for (const command of [
    buildDataLabExportDockerCommand(value.prepared, value.claim, value.runtime, { now: new Date("2026-08-10T13:00:03Z"), suffix: "123456abcdef" }),
    buildDataLabReplayDockerCommand(value.prepared, value.claim, value.runtime), buildDataLabInventoryDockerCommand(value.prepared, value.claim, value.runtime),
  ]) assert.equal(JSON.stringify(command).includes("/run/pixel/capability"), false);
});

test("Data Lab primary and replay tmpfs volumes and keepers are exact", () => {
  const value = fixture();
  for (const replay of [false, true]) {
    const created = replay ? buildDataLabReplayVolumeCreate(value.prepared, value.claim, value.runtime) : buildDataLabVolumeCreate(value.prepared, value.claim, value.runtime);
    const name = replay ? value.runtime.replayVolumeName : value.runtime.volumeName;
    const role = replay ? "data-lab-replay-workspace" : "data-lab-workspace";
    const volume = { Name: name, Driver: "local", Scope: "local", Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId, "com.osmantic.pixel.work-role": role,
    }, Options: { device: "tmpfs", o: created.option, type: "tmpfs" } };
    assert.equal(validateDataLabVolumeInspect(volume, value.prepared, value.claim, value.runtime, replay), true);
    const keeperCommand = replay ? buildDataLabReplayKeeperCommand(value.prepared, value.claim, value.runtime) : buildDataLabVolumeKeeperCommand(value.prepared, value.claim, value.runtime);
    assert.deepEqual(optionValues(keeperCommand.args, "--network"), ["none"]);
    const keeperName = replay ? value.runtime.replayKeeperName : value.runtime.keeperName;
    const keeperRole = replay ? "data-lab-replay-keeper" : "data-lab-volume-keeper";
    const image = { Id: `sha256:${digest("f")}` };
    const keeper = {
      Id: digest("6"), Name: `/${keeperName}`, Image: image.Id, State: { Running: true },
      Config: { Labels: { "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId, "com.osmantic.pixel.work-role": keeperRole } },
      HostConfig: { CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false, NetworkMode: "none", IpcMode: "none", CgroupnsMode: "private", PidsLimit: 64, Memory: 512 * 1024 * 1024, MemorySwap: 512 * 1024 * 1024, LogConfig: { Type: "none" }, PortBindings: {}, Devices: [] },
      Mounts: [{ Type: "volume", Name: name, Destination: "/workspace", RW: false }],
      NetworkSettings: { Networks: { none: { NetworkID: digest("4"), EndpointID: digest("5"), IPAddress: "", Gateway: "", MacAddress: "", IPPrefixLen: 0, GlobalIPv6Address: "", IPv6Gateway: "", GlobalIPv6PrefixLen: 0, Aliases: null, Links: null } } },
    };
    assert.equal(validateDataLabKeeperInspect(keeper, image, value.prepared, value.claim, value.runtime, replay), true);
  }
});

test("Data Lab commands fail closed on runtime, image, dataset, capability, and output widening", () => {
  const value = fixture();
  const image = { Config: { Labels: {
    "org.osmantic.pixel.data-runtime": "pixel-local-data-runtime-v1", "org.osmantic.pixel.duckdb-version": "1.5.5",
    "org.osmantic.pixel.polars-version": "1.43.2", "org.osmantic.pixel.python-version": "3.11.2", "org.osmantic.pixel.sqlite-version": "3.40.1",
  } } };
  assert.equal(validateDataLabImageRuntime(image, value.prepared), true);
  const wrongImage = structuredClone(image);
  wrongImage.Config.Labels["org.osmantic.pixel.duckdb-version"] = "1.5.4";
  assert.throws(() => validateDataLabImageRuntime(wrongImage, value.prepared), DockerDataLabError);
  for (const mutate of [
    (copy) => { copy.prepared.datasets[0].bytes = copy.prepared.datasets[0].maxBytes + 1; },
    (copy) => { copy.prepared.datasets[0].path = "/host/data,evil"; },
    (copy) => { copy.prepared.plan.grantedCapabilities.tools.push("browser"); },
    (copy) => { copy.prepared.plan.grantedCapabilities.network.services.push("research-broker"); },
    (copy) => { copy.prepared.plan.outputGate.automaticDeployment = true; },
    (copy) => { copy.runtime.replayVolumeSizeBytes += 1; },
    (copy) => { copy.runtime.workerIp = "8.8.8.8"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.throws(() => buildDataLabDockerCommand(hostile.prepared, hostile.claim, hostile.runtime), DockerDataLabError);
  }
});

test("Data Lab supervisor derives both disposable volumes and every private path from the consumed claim", () => {
  const value = fixture();
  const runtime = deriveDataLabDockerRuntime(value.prepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work", networkSubnet: value.runtime.networkSubnet,
    workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    imageIdentifier: value.runtime.imageIdentifier, backendImageIdentifier: `sha256:${digest("8")}`, uid: 1000, gid: 1000,
  });
  assert.equal(runtime.runRoot, `/var/lib/pixel-work/runs/${value.claim.claimId}`);
  assert.equal(runtime.outputDirectory, `/var/lib/pixel-work/results/${value.claim.claimId}`);
  assert.equal(runtime.volumeName, "pixel-work-data-abcdef123456");
  assert.equal(runtime.replayVolumeName, "pixel-work-data-replay-abcdef123456");
  assert.equal(runtime.keeperName, "pixel-work-data-keep-abcdef123456");
  assert.equal(runtime.replayKeeperName, "pixel-work-data-replay-keep-abcdef123456");
  assert.equal(runtime.volumeSizeBytes + runtime.replayVolumeSizeBytes, value.prepared.lease.budgets.maxDiskBytes);
  const proxy = buildModelProxyConfig(value.prepared, value.claim, runtime);
  assert.deepEqual(proxy.allowedTools, ["bash", "edit", "glob", "grep", "read", "write"]);
  assert.deepEqual(validateModelProxyConfig(proxy).allowedTools, proxy.allowedTools);
  assert.doesNotMatch(JSON.stringify(runtime), /sales totals|regional totals|credential|secret/i);
  assert.throws(() => deriveDataLabDockerRuntime(value.prepared, value.claim, {
    dockerPath: "docker", stateRoot: "/var/lib/pixel-work", networkSubnet: value.runtime.networkSubnet,
    workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1", uid: 1000, gid: 1000,
  }), DockerSupervisorError);
});

test("Data Lab cleanup-only recovery derives both exact volumes without dataset or launch authority", async () => {
  const value = fixture();
  const cleanup = {
    plan: value.prepared.plan, lease: value.prepared.lease, policy: value.prepared.policy,
    bindings: value.prepared.bindings, workspace: { sha256: value.claim.workspaceSha256 },
    cleanupOnly: true, recoveryConsumption: structuredClone(value.claim),
  };
  const options = {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work", networkSubnet: value.runtime.networkSubnet,
    workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    imageIdentifier: value.runtime.imageIdentifier, backendImageIdentifier: `sha256:${digest("8")}`, uid: 1000, gid: 1000,
  };
  const runtime = deriveDataLabDockerRuntime(cleanup, value.claim, options);
  assert.equal(runtime.replayVolumeName, "pixel-work-data-replay-abcdef123456");
  assert.throws(() => buildDataLabVolumeCreate(cleanup, value.claim, runtime), /cleanup boundary/);
  const volumes = new Map();
  for (const replay of [false, true]) {
    const name = replay ? runtime.replayVolumeName : runtime.volumeName;
    const size = replay ? runtime.replayVolumeSizeBytes : runtime.volumeSizeBytes;
    const role = replay ? "data-lab-replay-workspace" : "data-lab-workspace";
    const volume = {
      Name: name, Driver: "local", Scope: "local",
      Labels: {
        "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId,
        "com.osmantic.pixel.work-role": role,
      },
      Options: { type: "tmpfs", device: "tmpfs", o: `size=${size},uid=1000,gid=1000,mode=0700,nosuid,nodev` },
    };
    volumes.set(name, volume);
    assert.equal(validateDataLabVolumeInspect(volume, cleanup, value.claim, runtime, replay), true);
  }
  const calls = [];
  const exactCleanupExecutor = async (_command, args) => {
    calls.push(args);
    if (args[0] === "volume" && args[1] === "inspect" && volumes.has(args[2])) return { stdout: JSON.stringify([volumes.get(args[2])]), stderr: "" };
    if (args[0] === "container" && args[1] === "ls") return { stdout: "", stderr: "" };
    if (args[0] === "volume" && args[1] === "rm") return { stdout: `${args[2]}\n`, stderr: "" };
    const error = new DockerSupervisorError("missing fixture resource");
    error.dockerExitCode = 1; error.dockerStderr = "No such object"; throw error;
  };
  const previousUmask = process.umask(0o077);
  let removed;
  try {
    removed = await cleanupInterruptedProfileAttempt(cleanup, value.claim, { ...options, allowNonLinuxTests: true, executor: exactCleanupExecutor });
  } finally {
    process.umask(previousUmask);
  }
  assert.equal(removed.resourcesRemoved, true);
  assert.equal(removed.usage.failures, 1);
  assert.deepEqual(calls.filter((args) => args[0] === "volume" && args[1] === "rm").map((args) => args[2]).sort(), [...volumes.keys()].sort());
  const substituted = structuredClone(value.claim);
  substituted.claimId = "workclaim-1786366800000-000000000099";
  await assert.rejects(cleanupInterruptedProfileAttempt(cleanup, substituted, { ...options, allowNonLinuxTests: true }), /claim differs/);
});

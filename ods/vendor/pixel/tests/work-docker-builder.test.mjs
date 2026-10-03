import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  buildBuilderAgentConfig, builderWorkingDirectory, buildBuilderContinuationApplyDockerCommand, buildBuilderDockerCommand, buildBuilderExportDockerCommand, buildBuilderInitDockerCommand,
  buildBuilderPrompt, buildBuilderVolumeCreate, buildBuilderVolumeKeeperCommand, DockerBuilderError,
  validateBuilderContinuationReceipt, validateBuilderPatchArtifact, validateBuilderVolumeInspect, validateBuilderVolumeKeeperInspect,
} from "../deploy/work-runner/docker-builder.mjs";
import {
  cleanupInterruptedBuilderAttempt, deriveBuilderDockerRuntime, DockerSupervisorError, setupBuilderDockerBoundary,
} from "../deploy/work-runner/docker-supervisor.mjs";

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
      jobId, profile: "builder", objective: "Fix the local fixture.", acceptanceCriteria: ["Tests pass"],
      inputs: [{ id: "source", kind: "repository-snapshot" }],
      verification: { immutablePathPrefixes: [] },
      outputGate: { allowedKinds: ["patch", "test-evidence"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false },
      model, executor, isolation: { runnerImageDigest: `sha256:${digest("f")}` },
      grantedCapabilities: {
        tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
        network: { mode: "brokered", services: ["local-model"] },
      },
      budgets: {
        maxRuntimeSeconds: 600, maxToolCalls: 200, maxConcurrentSubagents: 1, maxModelRequests: 20,
        maxInputTokens: 100000, maxOutputTokens: 20000, maxNetworkBytes: 10485760,
        maxMemoryMiB: 2048, maxCpuCores: 2, maxDiskBytes: 1073741824, maxArtifactBytes: 16777216,
      },
    },
    lease: { leaseId, iteration: 1, continuation: null },
    policy: {
      runner: { imageRef: `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("f")}` },
      localModel: {
        id: "assistant-model", provider: "llama.cpp", imageRef: `ghcr.io/ggml-org/llama.cpp:server@sha256:${digest("8")}`,
        imageDigest: `sha256:${digest("8")}`, contextWindow: 131072, maxRequestContextTokens: 131072, supportsVision: false, maxRequestOutputTokens: 1024,
      },
    },
    modelQualification: {
      qualificationId: "modelqual-1786366740000-abcdef123456", receiptSha256: digest("1"), casesSha256: digest("2"), evaluatorSha256: digest("3"),
      profile: "builder", maxContextTokens: 131072, maxOutputTokens: 1024, exactUsage: true,
    },
    bindings: { planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d") },
    workspace: {
      path: "/var/lib/pixel-work/workspaces/prepared/workspace", originalPath: "/var/lib/pixel-work/workspaces/prepared/source",
      disposable: true, storage: "docker-tmpfs-volume", sha256: digest("7"),
    },
    executor: { path: "/var/lib/pixel-work/executors/omp-linux-x64" },
  };
  prepared.lease.budgets = prepared.plan.budgets;
  const claim = {
    status: "consumed", externalEffects: false, jobId, leaseId, claimId,
    planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d"),
    workspaceSha256: digest("7"), runnerImageDigest: `sha256:${digest("f")}`, executor, model,
  };
  const runtime = {
    dockerPath: "/usr/bin/docker", cidFile: "/var/lib/pixel-work/runs/claim/cids/worker.cid",
    keeperCidFile: "/var/lib/pixel-work/runs/claim/cids/keeper.cid",
    agentConfigPath: "/var/lib/pixel-work/runs/claim/config/builder-config.json",
    modelRegistryPath: "/var/lib/pixel-work/runs/claim/config/models.yml",
    networkName: "pixel-work-net-abcdef123456", networkSubnet: "172.30.8.0/29",
    workerIp: "172.30.8.2", proxyIp: "172.30.8.3", modelAlias: "pixel-model",
    containerName: "pixel-work-builder-abcdef123456", volumeName: "pixel-work-builder-abcdef123456",
    keeperName: "pixel-work-builder-keep-abcdef123456",
    volumeSizeBytes: 536870912, outputDirectory: "/var/lib/pixel-work/results/workclaim-1786366800000-abcdef123456",
    maxResultBytes: 2064384, patchArtifactLimitBytes: 13762560,
    imageIdentifier: `sha256:${digest("f")}`, modelId: "assistant-model", uid: 1000, gid: 1000,
  };
  return { prepared, claim, runtime };
}

function capabilityRuntime() {
  const serviceRoot = "/var/lib/pixel-work/runs/workclaim-1786366800000-abcdef123456/capability";
  const workerRoot = `${serviceRoot}/worker`, catalogSha256 = digest("6"), catalogName = `catalog-${catalogSha256}.json`;
  return { serviceRoot, workerRoot, catalogPath: `${workerRoot}/${catalogName}`, requestDirectory: `${workerRoot}/requests`, responseDirectory: `${workerRoot}/responses`, catalogName, catalogSha256, exposedTools: ["pixel_cap_fixture_analyze_12345678"] };
}

test("Builder commands provide broad workspace autonomy inside exact local-only boundaries", () => {
  const value = fixture();
  const volume = buildBuilderVolumeCreate(value.prepared, value.claim, value.runtime);
  const init = buildBuilderInitDockerCommand(value.prepared, value.claim, value.runtime);
  const keeper = buildBuilderVolumeKeeperCommand(value.prepared, value.claim, value.runtime);
  const worker = buildBuilderDockerCommand(value.prepared, value.claim, value.runtime);
  const exporter = buildBuilderExportDockerCommand(value.prepared, value.claim, value.runtime);

  assert.ok(volume.args.includes("type=tmpfs"));
  assert.ok(volume.args.includes("com.osmantic.pixel.work-role=builder-workspace"));
  for (const command of [init, keeper, worker, exporter]) for (const required of [
    "--pull", "never", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--memory-swap",
    "--ipc", "none", "--cgroupns", "private", "--log-driver", "none", "--user", "1000:1000",
  ]) assert.ok(command.args.includes(required), required);
  assert.ok(init.args.includes("none"));
  assert.ok(keeper.args.some((argument) => argument.includes("builder-volume-keeper")));
  assert.ok(keeper.args.some((argument) => argument.includes("/workspace") && argument.includes("readonly")));
  assert.ok(init.args.some((argument) => argument.includes("/source") && argument.endsWith(",readonly")));
  assert.ok(worker.args.includes("read,grep,glob,write,edit,bash,eval,lsp,debug,task,hub,todo"));
  assert.equal(worker.args[worker.args.indexOf("--workdir") + 1], "/workspace/source");
  assert.equal(worker.args[worker.args.indexOf("--cwd") + 1], "/workspace/source");
  assert.equal(builderWorkingDirectory(value.prepared.plan), "/workspace/source");
  assert.equal(builderWorkingDirectory({ inputs: [...value.prepared.plan.inputs, { id: "second", kind: "repository-snapshot" }] }), "/workspace");
  assert.ok(worker.args.some((argument) => argument.includes("builder-config.json") && argument.endsWith(",readonly")));
  assert.ok(worker.args.includes("/opt/pixel/deploy/work-runner/builder-entrypoint.sh"));
  assert.ok(worker.args.includes("PI_CONFIG_DIR=.pixel-omp"));
  assert.deepEqual(buildBuilderAgentConfig(value.prepared, value.claim, value.runtime).task, {
    isolation: { mode: "none", apply: false, merge: "patch", commits: "generic" },
    eager: "default", batch: true, enableEffort: false, maxConcurrency: 1, enableLsp: true,
    maxRecursionDepth: 1, maxRuntimeMs: 600000, agentIdleTtlMs: 0, softRequestBudget: 20,
    softRequestBudgetNotice: true,
  });
  assert.ok(worker.args.includes("fsize=536870912:536870912"));
  assert.ok(worker.args.some((argument) => argument.includes("/workspace") && argument.includes("type=volume") && !argument.includes("readonly")));
  assert.ok(worker.args.some((argument) => argument.includes("/opt/omp") && argument.endsWith(",readonly")));
  assert.ok(worker.args.includes(`type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`));
  assert.ok(worker.args.includes("/tmp/agent:rw,nosuid,nodev,noexec,size=512m,mode=0700,uid=1000,gid=1000"));
  assert.doesNotMatch(JSON.stringify(worker), /prepared\/source|\/output|Fix the local fixture|Tests pass|docker\.sock|SSH_AUTH_SOCK|OPENAI_API_KEY/);
  assert.ok(exporter.args.some((argument) => argument.includes("/workspace") && argument.includes("readonly")));
  assert.ok(exporter.args.some((argument) => argument.includes("/output") && !argument.includes("readonly")));
  assert.doesNotMatch(JSON.stringify(exporter.env ?? {}), /credential|secret|token/i);
});

test("Builder volume inspection is exact and command construction rejects widening", () => {
  const value = fixture();
  const created = buildBuilderVolumeCreate(value.prepared, value.claim, value.runtime);
  const volume = {
    Name: value.runtime.volumeName, Driver: "local", Scope: "local",
    Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId,
      "com.osmantic.pixel.work-job": value.claim.jobId,
      "com.osmantic.pixel.work-role": "builder-workspace",
    },
    Options: { device: "tmpfs", o: created.option, type: "tmpfs" },
  };
  assert.equal(validateBuilderVolumeInspect(volume, value.prepared, value.claim, value.runtime), true);
  const keeperImage = { Id: `sha256:${digest("f")}` };
  const keeper = {
    Id: digest("6"), Name: `/${value.runtime.keeperName}`, Image: keeperImage.Id, State: { Running: true },
    Config: { Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId,
      "com.osmantic.pixel.work-job": value.claim.jobId,
      "com.osmantic.pixel.work-role": "builder-volume-keeper",
    } },
    HostConfig: {
      CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false,
      NetworkMode: "none", IpcMode: "none", CgroupnsMode: "private", PidsLimit: 64,
      Memory: 512 * 1024 * 1024, MemorySwap: 512 * 1024 * 1024, LogConfig: { Type: "none" }, PortBindings: {}, Devices: [],
    },
    Mounts: [{ Type: "volume", Name: value.runtime.volumeName, Destination: "/workspace", RW: false }],
    NetworkSettings: { Networks: { none: {
      NetworkID: digest("4"), EndpointID: digest("5"), IPAddress: "", Gateway: "", MacAddress: "", IPPrefixLen: 0,
      GlobalIPv6Address: "", IPv6Gateway: "", GlobalIPv6PrefixLen: 0, Aliases: null, Links: null,
    } } },
  };
  assert.equal(validateBuilderVolumeKeeperInspect(keeper, keeperImage, value.prepared, value.claim, value.runtime), true);
  const widenedKeeper = structuredClone(keeper);
  widenedKeeper.Mounts[0].RW = true;
  assert.throws(() => validateBuilderVolumeKeeperInspect(widenedKeeper, keeperImage, value.prepared, value.claim, value.runtime), DockerBuilderError);
  for (const mutate of [
    (copy) => { copy.runtime.volumeSizeBytes += 1; },
    (copy) => { copy.runtime.workerIp = "8.8.8.8"; },
    (copy) => { copy.runtime.outputDirectory = "/var/lib/pixel work"; },
    (copy) => { copy.prepared.plan.grantedCapabilities.tools.push("browser"); },
    (copy) => { copy.prepared.plan.outputGate.allowedKinds = ["patch"]; },
    (copy) => { copy.claim.workspaceSha256 = digest("0"); },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.throws(() => buildBuilderDockerCommand(hostile.prepared, hostile.claim, hostile.runtime), DockerBuilderError);
  }
  const hostileVolume = structuredClone(volume);
  hostileVolume.Options.o += ",suid";
  assert.throws(() => validateBuilderVolumeInspect(hostileVolume, value.prepared, value.claim, value.runtime), DockerBuilderError);
});

test("Builder accepts an exact digest-only runner reference and rejects a mismatched digest", () => {
  const value = fixture();
  value.prepared.policy.runner.imageRef = value.prepared.plan.isolation.runnerImageDigest;
  const command = buildBuilderDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(command.args.includes(value.prepared.plan.isolation.runnerImageDigest));

  const mismatched = fixture();
  mismatched.prepared.policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("9")}`;
  assert.throws(() => buildBuilderDockerCommand(mismatched.prepared, mismatched.claim, mismatched.runtime), DockerBuilderError);
});

test("Builder keeps broad local tools while adding only its authorized capability", () => {
  const value = fixture(); value.runtime.capability = capabilityRuntime();
  const worker = buildBuilderDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(worker.args.includes("/opt/pixel/deploy/work-runner/capability-tool.mjs"));
  assert.ok(worker.args.includes(`type=bind,src=${value.runtime.capability.catalogPath},dst=/run/pixel/capability/${value.runtime.capability.catalogName},readonly`));
  assert.ok(worker.args.includes(`type=bind,src=${value.runtime.capability.requestDirectory},dst=/run/pixel/capability/requests`));
  assert.ok(worker.args.includes(`type=bind,src=${value.runtime.capability.responseDirectory},dst=/run/pixel/capability/responses,readonly`));
  assert.deepEqual(worker.allowedTools, ["bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "pixel_cap_fixture_analyze_12345678", "read", "task", "todo", "write"]);
});

test("Builder continuation reconstructs only the exact prior verified candidate", () => {
  const value = fixture();
  value.prepared.lease.iteration = 2;
  value.prepared.lease.continuation = {
    previousCheckpointSha256: digest("1"), previousConsumptionSha256: digest("2"), cumulativeUsageSha256: digest("3"),
  };
  value.prepared.continuationBase = {
    path: "/var/lib/pixel-work/results/workclaim-1786366800000-000000000001/builder-patch.json",
    sha256: digest("4"), bytes: 1024, changes: 3,
    claimId: "workclaim-1786366800000-000000000001", artifactLimitBytes: 16777216,
  };
  const command = buildBuilderContinuationApplyDockerCommand(value.prepared, value.claim, value.runtime);
  for (const required of ["--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", digest("4")]) {
    assert.ok(command.args.includes(required), required);
  }
  assert.ok(command.args.some((argument) => argument.includes("previous-builder-patch.json") && argument.endsWith(",readonly")));
  assert.ok(command.args.some((argument) => argument.includes("/source") && argument.endsWith(",readonly")));
  assert.ok(command.args.some((argument) => argument.includes("/workspace") && argument.includes("type=volume") && !argument.includes("readonly")));
  assert.ok(command.args.includes(value.prepared.continuationBase.claimId));
  assert.equal(validateBuilderContinuationReceipt({
    schemaVersion: 1, operation: "builder-volume-apply", changes: 3, files: 4, bytes: 100, candidateSha256: digest("5"),
  }, value.prepared, value.claim, value.runtime), true);
  const hostile = structuredClone(value);
  hostile.prepared.continuationBase.sha256 = "bad";
  assert.throws(() => buildBuilderContinuationApplyDockerCommand(hostile.prepared, hostile.claim, hostile.runtime), DockerBuilderError);
});

test("Builder supervisor derives all mutable resources and artifact partitions from the claim", () => {
  const value = fixture();
  const runtime = deriveBuilderDockerRuntime(value.prepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: value.runtime.networkSubnet, workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    imageIdentifier: value.runtime.imageIdentifier, backendImageIdentifier: `sha256:${digest("8")}`,
    uid: 1000, gid: 1000,
  });
  assert.equal(runtime.runRoot, `/var/lib/pixel-work/runs/${value.claim.claimId}`);
  assert.equal(runtime.outputDirectory, `/var/lib/pixel-work/results/${value.claim.claimId}`);
  assert.equal(runtime.volumeName, "pixel-work-builder-abcdef123456");
  assert.equal(runtime.volumeSizeBytes, 536870912);
  assert.equal(runtime.maxResultBytes, 2064384);
  assert.equal(runtime.patchArtifactLimitBytes, 13762560);
  assert.doesNotMatch(JSON.stringify(runtime), /Fix the local fixture|Tests pass|secret|credential/i);
  assert.throws(() => deriveBuilderDockerRuntime(value.prepared, value.claim, {
    dockerPath: "docker", stateRoot: "/var/lib/pixel-work", networkSubnet: value.runtime.networkSubnet,
    workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    uid: 1000, gid: 1000,
  }), DockerSupervisorError);
});

test("Builder recovery-only preparations cannot launch work or substitute cleanup claims", async () => {
  const value = fixture();
  const cleanupPrepared = {
    plan: value.prepared.plan,
    lease: value.prepared.lease,
    policy: value.prepared.policy,
    bindings: value.prepared.bindings,
    workspace: { sha256: value.prepared.workspace.sha256 },
    cleanupOnly: true,
    recoveryConsumption: structuredClone(value.claim),
  };
  const cleanupRuntime = deriveBuilderDockerRuntime(cleanupPrepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: value.runtime.networkSubnet, workerIp: value.runtime.workerIp, proxyIp: value.runtime.proxyIp,
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    imageIdentifier: value.runtime.imageIdentifier, backendImageIdentifier: `sha256:${digest("8")}`,
    uid: 1000, gid: 1000,
  });
  assert.equal(cleanupRuntime.volumeName, value.runtime.volumeName);
  await assert.rejects(setupBuilderDockerBoundary(cleanupPrepared, value.claim, { allowNonLinuxTests: true }), /cannot launch a worker/);
  await assert.rejects(setupBuilderDockerBoundary({ ...value.prepared, verificationOnly: true }, value.claim, { allowNonLinuxTests: true }), /cannot launch a worker/);
  const substituted = structuredClone(value.claim);
  substituted.claimId = "workclaim-1786366800000-000000000099";
  await assert.rejects(cleanupInterruptedBuilderAttempt(cleanupPrepared, substituted, { allowNonLinuxTests: true }), /claim differs/);
});

test("Builder patch verification binds content, ordering, authority, and budgets", () => {
  const value = fixture();
  const content = Buffer.from("export const fixed = true;\n");
  const patch = {
    schemaVersion: 1, format: "pixel-file-patch-v1", jobId: value.claim.jobId, claimId: value.claim.claimId,
    planSha256: value.claim.planSha256, workspaceSha256: value.claim.workspaceSha256,
    changes: [{
      path: "source/src/main.js", operation: "modify", beforeSha256: digest("1"), beforeBytes: 10,
      afterSha256: createHash("sha256").update(content).digest("hex"), afterBytes: content.length,
      contentBase64: content.toString("base64"),
    }],
    summary: { added: 0, modified: 1, deleted: 0, beforeBytes: 10, afterBytes: content.length },
    authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
    boundary: "Local deterministic patch artifact; applying it requires separate authority.",
  };
  const bytes = Buffer.byteLength(`${JSON.stringify(patch, null, 2)}\n`);
  assert.equal(validateBuilderPatchArtifact(patch, value.prepared, value.claim, value.runtime, bytes), true);
  for (const mutate of [
    (copy) => { copy.authority.merge = true; },
    (copy) => { copy.changes[0].path = "../escape"; },
    (copy) => { copy.changes[0].afterSha256 = digest("0"); },
    (copy) => { copy.summary.modified = 2; },
    (copy) => { copy.unexpected = true; },
  ]) {
    const hostile = structuredClone(patch);
    mutate(hostile);
    assert.throws(() => validateBuilderPatchArtifact(hostile, value.prepared, value.claim, value.runtime, bytes), DockerBuilderError);
  }
});

test("Builder prompt carries work and evidence requirements without authority", () => {
  const value = fixture();
  const prompt = buildBuilderPrompt(value.prepared.plan);
  assert.match(prompt, /Fix the local fixture/);
  assert.match(prompt, /Tests pass/);
  assert.match(prompt, /Active repository working directory: source/);
  assert.match(prompt, /do not prefix them with source\//);
  assert.match(prompt, /Implement and test/);
  assert.match(prompt, /not a planning or status turn/);
  assert.match(prompt, /an unchanged workspace is incomplete/);
  assert.doesNotMatch(prompt, /independently verified|merge authority granted|deployment authority granted/i);
});

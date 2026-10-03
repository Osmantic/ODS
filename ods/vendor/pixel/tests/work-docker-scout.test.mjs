import assert from "node:assert/strict";
import test from "node:test";

import { buildScoutDockerCommand, buildScoutPrompt, DockerScoutError, validateScoutNetworkInspect } from "../deploy/work-runner/docker-scout.mjs";

const digest = (character) => character.repeat(64);

function fixture() {
  const jobId = "work-1786366800000-abcdef123456";
  const leaseId = "worklease-1786366800000-abcdef123456";
  const value = {
    prepared: {
      plan: {
        jobId,
        objective: "Inspect the source fixture.",
        acceptanceCriteria: ["Include the CITRUS acceptance marker"],
        inputs: [{ id: "source" }],
        isolation: { runnerImageDigest: `sha256:${digest("f")}` },
        executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: digest("e"), rpcProtocolVersion: 2 },
        model: { route: "local-only", provider: "llama.cpp", id: "local-fixture", backendImageDigest: `sha256:${digest("9")}`, contextWindow: 32768, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
        grantedCapabilities: { tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] } },
        budgets: { maxMemoryMiB: 2048, maxCpuCores: 2, maxConcurrentSubagents: 1, maxRuntimeSeconds: 600 },
      },
      lease: { leaseId },
      policy: {
        runner: { imageRef: `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("f")}` },
        localModel: {
          id: "local-fixture", provider: "llama.cpp", contextWindow: 32768,
          supportsVision: false, maxRequestOutputTokens: 1024,
        },
      },
      bindings: { planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d") },
      workspace: { path: "/var/lib/pixel-work/workspaces/job-1", sha256: digest("a") },
      executor: { path: "/var/lib/pixel-work/executors/omp-linux-x64" },
    },
    claim: {
      jobId, leaseId, claimId: "workclaim-1786366800000-abcdef123456", status: "consumed", externalEffects: false,
      planSha256: digest("a"), leaseSha256: digest("b"), policySha256: digest("c"), inputSetSha256: digest("d"),
      workspaceSha256: digest("a"), runnerImageDigest: `sha256:${digest("f")}`,
      executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: digest("e"), rpcProtocolVersion: 2 },
      model: { route: "local-only", provider: "llama.cpp", id: "local-fixture", backendImageDigest: `sha256:${digest("9")}`, contextWindow: 32768, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" },
    },
    runtime: {
      dockerPath: "/usr/bin/docker",
      cidFile: "/run/pixel-work/cids/claim.cid",
      modelRegistryPath: "/run/pixel-work/config/models.yml",
      networkName: "pixel-work-job-123",
      workerIp: "172.30.1.2",
      proxyIp: "172.30.1.3",
      networkSubnet: "172.30.1.0/29",
      modelProxyName: "pixel-work-model-123",
      modelAlias: "pixel-model",
      modelId: "local-fixture",
      containerName: "pixel-work-scout-123",
      uid: 1000,
      gid: 1000,
    },
  };
  value.prepared.lease.budgets = value.prepared.plan.budgets;
  return value;
}

function capabilityRuntime() {
  const serviceRoot = "/var/lib/pixel-work/runs/workclaim-1786366800000-abcdef123456/capability";
  const workerRoot = `${serviceRoot}/worker`, catalogSha256 = digest("6"), catalogName = `catalog-${catalogSha256}.json`;
  return { serviceRoot, workerRoot, catalogPath: `${workerRoot}/${catalogName}`, requestDirectory: `${workerRoot}/requests`, responseDirectory: `${workerRoot}/responses`, catalogName, catalogSha256, exposedTools: ["pixel_cap_fixture_analyze_12345678"] };
}

test("Scout Docker command contains the full outer boundary and no user content", () => {
  const value = fixture();
  const command = buildScoutDockerCommand(value.prepared, value.claim, value.runtime);
  assert.equal(command.command, "/usr/bin/docker");
  for (const required of [
    "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--pids-limit", "--memory",
    "--memory-swap", "--cpus", "--ipc", "none", "--cgroupns", "private", "--log-driver",
    "none", "--user", "1000:1000", "--no-session", "--no-extensions", "--no-skills",
    "--no-rules", "--no-pty", "--approval-mode", "yolo",
    "com.osmantic.pixel.work-role=scout-worker",
  ]) assert.ok(command.args.includes(required), required);
  assert.ok(command.args.includes("read,grep,glob"));
  assert.ok(command.args.includes("never"));
  assert.ok(command.args.includes("172.30.1.2"));
  assert.ok(command.args.some((argument) => argument.includes("/workspace") && argument.endsWith(",readonly")));
  assert.ok(command.args.some((argument) => argument.includes("/opt/omp") && argument.endsWith(",readonly")));
  assert.ok(command.args.includes(`type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`));
  assert.ok(command.args.includes("/tmp/agent:rw,nosuid,nodev,noexec,size=512m,mode=0700,uid=1000,gid=1000"));
  assert.ok(command.args.includes("ghcr.io/osmantic/pixel-work-runner@sha256:" + digest("f")));
  assert.doesNotMatch(JSON.stringify(command), /Inspect the source fixture|CITRUS acceptance marker|docker\.sock|SSH_AUTH_SOCK|OPENAI_API_KEY/);
  assert.doesNotMatch(JSON.stringify(command.env), /DOCKER_HOST|HOME.*Users|token|secret/i);
});

test("Scout Docker command rejects path, network, image, and capability widening", () => {
  for (const mutate of [
    (copy) => { copy.runtime.networkName = "safe;--network=host"; },
    (copy) => { copy.runtime.workerIp = "8.8.8.8"; },
    (copy) => { copy.runtime.dockerPath = "docker"; },
    (copy) => { copy.prepared.workspace.path = "/var/lib/pixel work"; },
    (copy) => { copy.prepared.policy.runner.imageRef = "pixel-work-runner:latest"; },
    (copy) => { copy.prepared.plan.grantedCapabilities.tools.push("bash"); },
    (copy) => { copy.claim.status = "available"; },
    (copy) => { copy.claim.workspaceSha256 = digest("9"); },
  ]) {
    const value = fixture();
    mutate(value);
    assert.throws(() => buildScoutDockerCommand(value.prepared, value.claim, value.runtime), DockerScoutError);
  }
});

test("Scout accepts an exact digest-only runner reference and rejects a mismatched digest", () => {
  const value = fixture();
  value.prepared.policy.runner.imageRef = value.prepared.plan.isolation.runnerImageDigest;
  const command = buildScoutDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(command.args.includes(value.prepared.plan.isolation.runnerImageDigest));

  const mismatched = fixture();
  mismatched.prepared.policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("9")}`;
  assert.throws(() => buildScoutDockerCommand(mismatched.prepared, mismatched.claim, mismatched.runtime), DockerScoutError);
});

test("Scout mounts one job-scoped capability without ambient registration", () => {
  const value = fixture(); value.runtime.capability = capabilityRuntime();
  const command = buildScoutDockerCommand(value.prepared, value.claim, value.runtime);
  assert.ok(command.args.includes("/opt/pixel/deploy/work-runner/capability-tool.mjs"));
  assert.ok(command.args.includes(`type=bind,src=${value.runtime.capability.catalogPath},dst=/run/pixel/capability/${value.runtime.capability.catalogName},readonly`));
  assert.ok(command.args.includes(`type=bind,src=${value.runtime.capability.requestDirectory},dst=/run/pixel/capability/requests`));
  assert.ok(command.args.includes(`type=bind,src=${value.runtime.capability.responseDirectory},dst=/run/pixel/capability/responses,readonly`));
  assert.ok(command.args.includes("--no-extensions"));
  assert.deepEqual(command.allowedTools, ["glob", "grep", "pixel_cap_fixture_analyze_12345678", "read"]);
});

test("Scout accepts only an internal job network containing its one model proxy", () => {
  const value = fixture();
  const network = {
    Name: value.runtime.networkName,
    Driver: "bridge",
    Internal: true,
    Attachable: false,
    Ingress: false,
    IPAM: { Config: [{ Subnet: "172.30.1.0/29" }] },
    Containers: { [digest("a")]: { Name: value.runtime.modelProxyName, IPv4Address: "172.30.1.3/29" } },
  };
  assert.equal(validateScoutNetworkInspect(network, value.runtime), true);
  for (const mutate of [
    (copy) => { copy.Internal = false; },
    (copy) => { copy.Attachable = true; },
    (copy) => { copy.Containers[digest("b")] = { Name: "unexpected-peer" }; },
    (copy) => { copy.Containers[digest("a")].Name = "wrong-proxy"; },
    (copy) => { copy.IPAM.Config[0].Subnet = "8.8.8.0/29"; },
    (copy) => { copy.IPAM.Config[0].Subnet = "172.30.1.1/29"; copy.Containers[digest("a")].IPv4Address = "172.30.1.3/29"; },
    (copy) => { copy.Containers[digest("a")].IPv4Address = "172.30.1.4/29"; },
  ]) {
    const hostile = structuredClone(network);
    mutate(hostile);
    assert.throws(() => validateScoutNetworkInspect(hostile, value.runtime), DockerScoutError);
  }
});

test("Scout prompt carries objective and criteria without claiming authority", () => {
  const value = fixture();
  const prompt = buildScoutPrompt(value.prepared.plan);
  assert.match(prompt, /Inspect the source fixture/);
  assert.match(prompt, /CITRUS acceptance marker/);
  assert.match(prompt, /__pixel_inert__/);
  assert.doesNotMatch(prompt, /independently verified|merge authority|deployment authority/i);
});

import assert from "node:assert/strict";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { validatePlanVerificationEvidence, validateWorkVerificationEvidence } from "../scripts/lib/work-contract.mjs";
import {
  buildVerifierApplyDockerCommand, buildVerifierCommandDockerCommand, buildVerifierKeeperDockerCommand,
  buildVerifierVolumeCreate, DockerVerifierError, validateVerifierApplyReceipt, validateVerifierCommandReceipt,
  validateVerifierKeeperInspect, validateVerifierVolumeInspect,
} from "../deploy/work-runner/docker-verifier.mjs";

const digest = (character) => character.repeat(64);

function fixture() {
  const jobId = "work-1786366800000-abcdef123456";
  const contentSha256 = digest("1");
  const verification = {
    mode: "independent",
    checks: [
      { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
      { id: "fixed-test", kind: "command", criterionIndexes: [1], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 60, maxOutputBytes: 65536 },
    ],
    immutablePathPrefixes: ["source/tests/"], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
    boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
  };
  const budgets = {
    maxRuntimeSeconds: 600, maxIterations: 4, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000, maxCpuCores: 2,
    maxMemoryMiB: 2048, maxDiskBytes: 1073741824, maxArtifactBytes: 16777216,
    maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
  };
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder",
    objective: "Repair the fixture.", acceptanceCriteria: ["Patch stays bounded", "Tests pass"], verification,
    dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false,
      ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets, outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const policy = {
    $schema: "https://osmantic.com/pixel/schemas/work-policy-v1.schema.json", schemaVersion: 1, policyId: "pixel-deep-work-test", enabled: true,
    executor: { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", url: "https://github.com/can1357/oh-my-pi/releases/download/v17.2.12/omp-linux-x64", sha256: digest("e"), rpcProtocolVersion: 2 },
    runner: { prepared: true, imageRef: `local/pixel-work-runner@sha256:${digest("f")}`, imageDigest: `sha256:${digest("f")}`, allowedIsolation: ["hardened-container"] },
    localModel: {
      prepared: true, provider: "llama.cpp", id: "assistant-model", imageRef: `local/model@sha256:${digest("8")}`,
      imageDigest: `sha256:${digest("8")}`, modelArtifactSha256: digest("1"), backendVersion: "fixture",
      acceleratorClass: "cpu", promptContractSha256: digest("2"), toolSchemaSha256: digest("3"),
      contextWindow: 32768, maxRequestContextTokens: 32768, supportsVision: false, maxRequestOutputTokens: 1024,
      inference: null,
      qualification: { receiptPath: "/private/model-qualification.json", receiptSha256: digest("4"), casesSha256: digest("5"), evaluatorSha256: digest("6") },
    },
    verifier: { enabled: true, allowedExecutables: ["/opt/node/bin/node", "/bin/sh"], maxChecks: 16, maxRuntimeSeconds: 900, maxOutputBytes: 4194304 },
    profiles: {
      scout: { enabled: true, isolation: "hardened-container", tools: ["read", "search"], services: ["local-model"], workspaceMount: "read-only", outputKinds: ["finding-report"], minimumMemoryMiB: 1536, maxBudgets: budgets },
      builder: { enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"], services: ["local-model"], workspaceMount: "disposable-read-write", outputKinds: ["patch", "test-evidence"], minimumMemoryMiB: 2048, maxBudgets: budgets },
      dataLab: { enabled: false }, researcher: { enabled: false }, publicProject: { enabled: false },
    },
    maxRequestAgeSeconds: 3600, maxLeaseSeconds: 3600,
    retention: { requestDays: 30, planDays: 30, resultDays: 90, checkpointDays: 30 },
    security: {
      freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, projectEnvDiscovery: false, userConfigDiscovery: false,
      extensions: false, skills: false, mcp: false, browser: false, computer: false, collaboration: false, directNetwork: false,
      hostFilesystem: false, dockerSocket: false, sshAgent: false, ambientCredentials: false, automaticMerge: false, automaticDeployment: false,
    },
  };
  const entries = [{ id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256, bytes: 1024, classification: "internal", mountMode: "read-only" }];
  const compiled = compileBuilder(job, policy, entries, { now: new Date("2026-08-10T13:00:00Z"), suffix: "123456abcdef" });
  const prepared = {
    ...compiled, policy,
    bindings: { planSha256: compiled.planSha256, leaseSha256: digest("2"), policySha256: compiled.policySha256, inputSetSha256: compiled.inputSetSha256 },
    workspace: { originalPath: "/var/lib/pixel-work/prepared/source", path: "/var/lib/pixel-work/prepared/workspace", disposable: true, sha256: digest("7") },
  };
  const claim = { status: "consumed", externalEffects: false, jobId, claimId: "workclaim-1786366800000-abcdef123456", planSha256: compiled.planSha256, workspaceSha256: digest("7"), runnerImageDigest: `sha256:${digest("f")}` };
  const runtime = {
    dockerPath: "/usr/bin/docker", patchPath: "/var/lib/pixel-work/results/claim/builder-patch.json", checkPath: "/var/lib/pixel-work/verifier/check.json",
    keeperCidFile: "/var/lib/pixel-work/verifier/keeper.cid", commandCidFile: "/var/lib/pixel-work/verifier/command.cid",
    volumeName: "pixel-work-verify-abcdef123456-01", keeperName: "pixel-work-verify-keep-abcdef123456-01",
    applyName: "pixel-work-verify-apply-abcdef123456-01", commandName: "pixel-work-verify-run-abcdef123456-01",
    volumeSizeBytes: 536870912, imageIdentifier: `sha256:${digest("f")}`, uid: 1000, gid: 1000,
    patchSha256: digest("3"), patchBytes: 1000, patchChanges: 1,
  };
  return { prepared, claim, check: verification.checks[1], runtime };
}

test("verifier containers rebuild exact candidates with no network, host, or credential surface", () => {
  const value = fixture();
  const volume = buildVerifierVolumeCreate(value.prepared, value.claim, value.check, value.runtime);
  const keeper = buildVerifierKeeperDockerCommand(value.prepared, value.claim, value.check, value.runtime);
  const apply = buildVerifierApplyDockerCommand(value.prepared, value.claim, value.check, value.runtime);
  const command = buildVerifierCommandDockerCommand(value.prepared, value.claim, value.check, value.runtime, digest("4"));
  for (const item of [keeper, apply, command]) for (const required of ["--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--log-driver", "none"]) assert.ok(item.args.includes(required), required);
  assert.ok(volume.args.includes("type=tmpfs"));
  assert.ok(apply.args.some((argument) => argument.includes("builder-patch.json") && argument.includes("readonly")));
  assert.ok(apply.args.includes(value.runtime.patchSha256));
  assert.ok(apply.args.some((argument) => argument.includes("/source") && argument.includes("readonly")));
  assert.ok(command.args.some((argument) => argument.includes("verifier-check.json") && argument.includes("readonly")));
  assert.ok(command.args.some((argument) => argument.includes("/workspace") && argument.includes("type=volume") && argument.includes("readonly")));
  assert.doesNotMatch(JSON.stringify(command), /Repair the fixture|Tests pass|docker\.sock|SSH_AUTH_SOCK|OPENAI_API_KEY/);
  assert.throws(() => buildVerifierCommandDockerCommand(value.prepared, value.claim, { ...value.check, argv: ["/bin/bash"] }, value.runtime, digest("4")), DockerVerifierError);
});

test("verifier volume, keeper, and receipts are exact and fail closed", () => {
  const value = fixture();
  const created = buildVerifierVolumeCreate(value.prepared, value.claim, value.check, value.runtime);
  const volume = {
    Name: value.runtime.volumeName, Driver: "local", Scope: "local",
    Labels: { "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId, "com.osmantic.pixel.work-check": value.check.id, "com.osmantic.pixel.work-role": "verifier-candidate" },
    Options: { device: "tmpfs", o: created.option, type: "tmpfs" },
  };
  assert.equal(validateVerifierVolumeInspect(volume, value.prepared, value.claim, value.check, value.runtime), true);
  const image = { Id: `sha256:${digest("f")}` };
  const keeper = {
    Id: digest("5"), Name: `/${value.runtime.keeperName}`, Image: image.Id, State: { Running: true },
    Config: { Labels: { "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId, "com.osmantic.pixel.work-check": value.check.id, "com.osmantic.pixel.work-role": "verifier-keeper" } },
    HostConfig: { CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], ReadonlyRootfs: true, Privileged: false, NetworkMode: "none", IpcMode: "none", CgroupnsMode: "private", PidsLimit: 64, Memory: 536870912, MemorySwap: 536870912, LogConfig: { Type: "none" }, PortBindings: {}, Devices: [] },
    Mounts: [{ Type: "volume", Name: value.runtime.volumeName, Destination: "/workspace", RW: false }],
    NetworkSettings: { Networks: { none: { IPAddress: "", Gateway: "", GlobalIPv6Address: "", IPv6Gateway: "", IPPrefixLen: 0, GlobalIPv6PrefixLen: 0 } } },
  };
  assert.equal(validateVerifierKeeperInspect(keeper, image, value.prepared, value.claim, value.check, value.runtime), true);
  const applyReceipt = { schemaVersion: 1, operation: "builder-volume-apply", changes: 1, files: 3, bytes: 100, candidateSha256: digest("4") };
  assert.equal(validateVerifierApplyReceipt(applyReceipt, value.prepared, value.claim, value.check, value.runtime), true);
  const receipt = {
    schemaVersion: 1, operation: "pixel-independent-command-check", checkId: value.check.id, kind: "command", criterionIndexes: [1],
    planSha256: value.claim.planSha256, candidateSha256: digest("4"), status: "pass", exitCode: 0, signal: null,
    timedOut: false, outputLimitExceeded: false, spawnFailed: false, durationMilliseconds: 12,
    stdout: { bytes: 0, sha256: digest("e") }, stderr: { bytes: 0, sha256: digest("e") },
    boundary: "Content-free independent command receipt. It records only fixed identifiers, counters, digests, and pass/fail state and grants no action authority.",
  };
  assert.equal(validateVerifierCommandReceipt(receipt, value.prepared, value.claim, value.check, value.runtime, digest("4")), true);
  const counterfeit = structuredClone(receipt);
  counterfeit.status = "fail";
  assert.throws(() => validateVerifierCommandReceipt(counterfeit, value.prepared, value.claim, value.check, value.runtime, digest("4")), DockerVerifierError);
  const networked = structuredClone(keeper);
  networked.HostConfig.NetworkMode = "bridge";
  assert.throws(() => validateVerifierKeeperInspect(networked, image, value.prepared, value.claim, value.check, value.runtime), DockerVerifierError);
});

test("verification evidence binds every immutable check, criterion, and rebuilt candidate", () => {
  const value = fixture();
  const candidateSha256 = digest("4");
  const evidence = {
    schemaVersion: 1, format: "pixel-independent-verification-v1", jobId: value.claim.jobId, claimId: value.claim.claimId,
    planSha256: value.claim.planSha256, patchSha256: digest("3"), candidateSha256, status: "pass",
    checks: [
      { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: digest("5"), changes: 1, files: 3, bytes: 100 },
      {
        id: "fixed-test", kind: "command", criterionIndexes: [1], status: "pass", candidateSha256, evidenceSha256: digest("6"),
        runtimeMilliseconds: 12, exitCode: 0, signal: null, timedOut: false, outputLimitExceeded: false, spawnFailed: false,
        stdoutBytes: 0, stdoutSha256: digest("7"), stderrBytes: 0, stderrSha256: digest("8"),
      },
    ],
    criteria: [{ index: 0, status: "pass", checkIds: ["patch-boundary"] }, { index: 1, status: "pass", checkIds: ["fixed-test"] }],
    network: "none", workerSelectedChecks: false, externalEffects: false,
    boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
  };
  assert.deepEqual(validateWorkVerificationEvidence(evidence), []);
  assert.deepEqual(validatePlanVerificationEvidence(value.prepared.plan, evidence), []);
  for (const mutate of [
    (copy) => { copy.workerSelectedChecks = true; },
    (copy) => { copy.checks[1].candidateSha256 = digest("9"); },
    (copy) => { copy.criteria[1].checkIds = ["patch-boundary"]; },
    (copy) => { copy.status = "fail"; },
    (copy) => { copy.rawOutput = "PRIVATE_CANARY"; },
  ]) {
    const hostile = structuredClone(evidence);
    mutate(hostile);
    assert.ok(validatePlanVerificationEvidence(value.prepared.plan, hostile).length > 0);
  }
});

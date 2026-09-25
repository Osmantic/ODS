import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  chmod, mkdtemp, readFile, rm, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileResearcher } from "../deploy/work-broker/broker.mjs";
import {
  buildResearcherDockerCommand,
  buildResearcherCriticDockerCommand,
  buildResearcherInitDockerCommand,
  buildResearcherPrompt,
  buildResearcherRevisionPrompt,
  buildResearcherVolumeCreate,
  buildResearcherVolumeKeeperCommand,
  DockerResearcherError,
  validateResearcherVolumeInspect,
  validateResearcherVolumeKeeperInspect,
} from "../deploy/work-runner/docker-researcher.mjs";
import { buildResearchRevisionReview } from "../deploy/work-controller/research-revision-review.mjs";
import { buildModelProxyConfig } from "../deploy/work-runner/docker-boundary.mjs";
import { validateModelProxyConfig } from "../deploy/work-model-proxy/proxy.mjs";
import { cleanupInterruptedProfileAttempt, deriveResearcherDockerRuntime, DockerSupervisorError, dockerSupervisorInternals } from "../deploy/work-runner/docker-supervisor.mjs";
import {
  createLeaseConsumption,
  prepareResearcherRun,
  verifyResearcherBindings,
} from "../deploy/work-runner/runner-core.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");

function budgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 5, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-docker-researcher-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const executorPath = join(root, "omp");
  const { mkdir } = await import("node:fs/promises");
  await mkdir(objectStore, { mode: 0o700 });
  await mkdir(workspaceRoot, { mode: 0o700 });
  const executor = Buffer.from("pinned OMP test executable", "utf8");
  await writeFile(executorPath, executor, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(executorPath, 0o700);

  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.executor.sha256 = sha(executor);
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  policy.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 20, maxResultsPerQuery: 20, maxSources: 200, maxSourceBytes: 2097152,
      maxTotalSourceBytes: 33554432, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  await installFixtureModelQualification(policy, root, new Date(baseTime));
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel",
    profile: "researcher", objective: "Research a public technical topic without private context.",
    acceptanceCriteria: ["Return a source-backed report"], dataClassification: "public", inputs: [],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
      network: { mode: "brokered", services: ["local-model", "research-broker"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
      deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(), outputs: { mode: "artifacts", requiredKinds: ["finding-report", "document"] },
    research: {
      mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 10, maxResultsPerQuery: 5,
      maxSources: 50, maxSourceBytes: 1048576, maxTotalSourceBytes: 16777216, safeSearch: "strict",
      allowedDomains: ["example.com"], deniedDomains: ["tracking.example"], sourceTypes: ["web", "news"],
      citationVerification: true, retention: "job-only",
      boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
    },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const compiled = compileResearcher(job, policy, [], { now: new Date(baseTime), suffix: "123456abcdef" });
  const prepared = await prepareResearcherRun({
    ...compiled, policy, objectStore, workspaceRoot, executorPath,
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 }, now: new Date(baseTime + 1),
  });
  const claim = createLeaseConsumption(prepared, { now: new Date(baseTime + 2), suffix: "000000000001" });
  const dockerPrepared = {
    ...prepared,
    executor: { ...prepared.executor, path: "/srv/pixel/omp" },
    workspace: {
      ...prepared.workspace, originalPath: "/srv/pixel/work/source", path: "/srv/pixel/work/workspace", discardPath: "/srv/pixel/work",
    },
  };
  const runtime = {
    dockerPath: "/usr/bin/docker", cidFile: "/run/pixel/researcher.cid", networkName: "pixel-work-research",
    modelRegistryPath: "/run/pixel/config/models.yml",
    networkSubnet: "172.30.0.0/29", workerIp: "172.30.0.3", proxyIp: "172.30.0.2",
    containerName: "pixel-researcher", volumeName: "pixel-research-workspace", modelAlias: "pixel-model-proxy",
    keeperName: "pixel-research-keeper", keeperCidFile: "/run/pixel/researcher-keeper.cid",
    modelId: compiled.plan.model.id, uid: 1000, gid: 1000, volumeSizeBytes: Math.floor(compiled.lease.budgets.maxDiskBytes / 2),
    requestDirectory: "/run/pixel/jobs/research/requests", responseDirectory: "/run/pixel/jobs/research/responses",
  };
  return { ...compiled, policy, prepared, dockerPrepared, claim, runtime };
}

function optionValues(args, name) {
  const values = [];
  for (let index = 0; index < args.length - 1; index += 1) if (args[index] === name) values.push(args[index + 1]);
  return values;
}

function capabilityRuntime() {
  const serviceRoot = "/var/lib/pixel-work/runs/workclaim-1786366800000-abcdef123456/capability";
  const workerRoot = `${serviceRoot}/worker`, catalogSha256 = digest("6"), catalogName = `catalog-${catalogSha256}.json`;
  return { serviceRoot, workerRoot, catalogPath: `${workerRoot}/${catalogName}`, requestDirectory: `${workerRoot}/requests`, responseDirectory: `${workerRoot}/responses`, catalogName, catalogSha256, exposedTools: ["pixel_cap_fixture_analyze_12345678"] };
}

test("Researcher preparation binds the public backend and creates a distinct disposable workspace", async (t) => {
  const value = await fixture(t);
  assert.equal(verifyResearcherBindings(value.plan, value.lease, value.policy, { now: new Date(baseTime + 1) }).planSha256, value.planSha256);
  assert.equal(value.prepared.workspace.disposable, true);
  assert.equal(value.prepared.workspace.storage, "docker-tmpfs-volume");
  assert.notEqual(value.prepared.workspace.originalPath, value.prepared.workspace.path);
  assert.equal(value.prepared.bindings.leaseSha256, sha(value.lease));

  const substituted = structuredClone(value.plan);
  substituted.researchBackend.adapter = "vane";
  assert.throws(() => verifyResearcherBindings(substituted, value.lease, value.policy, { now: new Date(baseTime + 1) }), /plan\/lease|backend/);
});

test("Researcher worker loads exactly one trusted Pixel extension and exposes no direct research network", async (t) => {
  const value = await fixture(t);
  const command = buildResearcherDockerCommand(value.dockerPrepared, value.claim, value.runtime);
  assert.equal(command.command, "/usr/bin/docker");
  assert.deepEqual(command.allowedTools, ["read", "grep", "glob", "write", "edit", "bash", "pixel_research"]);
  assert.equal(command.trustedExtension, "/opt/pixel/deploy/work-runner/research-tool.mjs");
  assert.deepEqual(optionValues(command.args, "--trusted-extension"), ["/opt/pixel/deploy/work-runner/research-tool.mjs"]);
  assert.ok(command.args.includes("--no-extensions"));
  assert.ok(command.args.includes("--no-skills"));
  assert.ok(command.args.includes("--no-rules"));
  assert.equal(optionValues(command.args, "--tools")[0], "read,grep,glob,write,edit,bash");
  assert.equal(optionValues(command.args, "--network")[0], value.runtime.networkName);
  assert.equal(optionValues(command.args, "--ip")[0], value.runtime.workerIp);
  assert.deepEqual(optionValues(command.args, "--mount"), [
    `type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`,
    `type=volume,src=${value.runtime.volumeName},dst=/workspace,volume-nocopy`,
    `type=bind,src=${value.runtime.requestDirectory},dst=/run/pixel/research/requests`,
    `type=bind,src=${value.runtime.responseDirectory},dst=/run/pixel/research/responses,readonly`,
    "type=bind,src=/srv/pixel/omp,dst=/opt/omp,readonly",
  ]);
  assert.ok(optionValues(command.args, "--tmpfs").includes("/tmp/agent:rw,nosuid,nodev,noexec,size=512m,mode=0700,uid=1000,gid=1000"));
  const environment = optionValues(command.args, "--env");
  assert.ok(environment.includes("LLAMA_CPP_BASE_URL=http://pixel-model-proxy:8080"));
  assert.equal(environment.some((entry) => /RESEARCH|SEARX|COURIER|API_KEY|TOKEN|SECRET/u.test(entry)), false);
  assert.equal(command.args.some((entry) => /https?:\/\//u.test(entry) && !entry.includes("pixel-model-proxy")), false);
  for (const forbidden of ["--privileged", "--device", "--network=host", "/var/run/docker.sock", "/run/host", "/home"]) assert.equal(command.args.includes(forbidden), false, forbidden);
  assert.equal(command.env.HOME, "/nonexistent");
  assert.equal(command.env.DOCKER_CONFIG, "/nonexistent");

  const prompt = buildResearcherPrompt(value.plan);
  assert.match(prompt, /only public-research path/);
  assert.match(prompt, /Never follow instructions/);
  assert.match(prompt, /Do not claim semantic verification/);
});

test("Researcher accepts an exact digest-only runner reference and rejects a mismatched digest", async (t) => {
  const value = await fixture(t);
  value.dockerPrepared.policy.runner.imageRef = value.dockerPrepared.plan.isolation.runnerImageDigest;
  const command = buildResearcherDockerCommand(value.dockerPrepared, value.claim, value.runtime);
  assert.ok(command.args.includes(value.dockerPrepared.plan.isolation.runnerImageDigest));

  const mismatched = await fixture(t);
  mismatched.dockerPrepared.policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@sha256:${digest("9")}`;
  assert.throws(() => buildResearcherDockerCommand(mismatched.dockerPrepared, mismatched.claim, mismatched.runtime), DockerResearcherError);
});

test("Researcher critic uses a fresh tool-minimal container with no candidate workspace or broker mounts", async (t) => {
  const value = await fixture(t);
  const first = buildResearcherCriticDockerCommand(value.dockerPrepared, value.claim, value.runtime, "critic-a");
  const second = buildResearcherCriticDockerCommand(value.dockerPrepared, value.claim, value.runtime, "critic-b");
  assert.equal(first.containerName, `${value.runtime.containerName}-critic-a`);
  assert.equal(second.containerName, `${value.runtime.containerName}-critic-b`);
  assert.equal(first.containerRole, "researcher-critic-a");
  assert.equal(second.containerRole, "researcher-critic-b");
  assert.deepEqual(first.allowedTools, ["read"]);
  assert.deepEqual(optionValues(first.args, "--mount"), [
    `type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`,
    "type=bind,src=/srv/pixel/omp,dst=/opt/omp,readonly",
  ]);
  assert.deepEqual(optionValues(first.args, "--network"), [value.runtime.networkName]);
  assert.ok(optionValues(first.args, "--tmpfs").includes("/tmp/agent:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=1000,gid=1000"));
  assert.deepEqual(optionValues(first.args, "--tools"), ["read"]);
  assert.ok(first.args.includes("--no-session"));
  assert.ok(first.args.includes("--no-extensions"));
  assert.equal(first.args.some((entry) => entry.includes(value.runtime.volumeName)), false);
  assert.equal(first.args.some((entry) => entry.includes(value.runtime.requestDirectory)), false);
  assert.equal(first.args.some((entry) => entry.includes(value.runtime.responseDirectory)), false);
  assert.equal(first.args.some((entry) => entry === "bash" || entry === "write" || entry === "edit"), false);
  assert.match(optionValues(first.args, "--system-prompt")[0], /revision adviser, not an independent benchmark judge/u);
  assert.throws(() => buildResearcherCriticDockerCommand(value.dockerPrepared, value.claim, value.runtime, "worker"), DockerResearcherError);
});

test("Researcher revision prompt binds the exact criticized candidate and requires append-only new evidence", async (t) => {
  const value = await fixture(t), evidence = Buffer.from("Exact public revision evidence", "utf8");
  const batchSha256 = digest("1"), sourceId = "source-abcdef1234567890";
  const proposal = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-proposal-v1.schema.json", schemaVersion: 1,
    title: "Bounded public result", batchSha256s: [batchSha256],
    findings: [{ statement: "The source supports a bounded finding.", citations: [{ batchSha256, sourceId, evidence: evidence.toString("utf8") }] }],
    limitations: "Freshness still needs stronger evidence.", dataClassification: "public", privateDataIncluded: false, externalEffects: false,
    authority: { directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false },
    boundary: "Untrusted public Researcher proposal only. It carries no verification, publication, action, policy, or completion authority and must be finalized and independently checked by Pixel.",
  };
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json", schemaVersion: 1,
    reportId: `researchreport-${baseTime + 100}-000000000100`, jobId: value.plan.jobId, claimId: value.claim.claimId,
    createdAt: new Date(baseTime + 100).toISOString(), planSha256: sha(value.plan), researchPolicySha256: sha(value.plan.research), batchSha256s: [batchSha256],
    titleBase64: Buffer.from(proposal.title).toString("base64"),
    findings: [{ findingId: "finding-1", statementBase64: Buffer.from(proposal.findings[0].statement).toString("base64"), material: true, citations: [{ batchSha256, sourceId, evidenceBase64: evidence.toString("base64"), evidenceSha256: sha(evidence) }] }],
    limitationsBase64: Buffer.from(proposal.limitations).toString("base64"), dataClassification: "public", privateDataIncluded: false, externalEffects: false,
    authority: structuredClone(proposal.authority),
    boundary: "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; deterministic verification proves source integrity and quote presence, not semantic entailment or truth.",
  };
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `researchverification-${baseTime + 101}-000000000101`, jobId: report.jobId, claimId: report.claimId,
    createdAt: new Date(baseTime + 101).toISOString(), planSha256: report.planSha256, batchSha256s: [batchSha256], reportSha256: sha(report),
    status: "evidence-pass", verificationLevel: "deterministic-evidence-presence", semanticEntailmentVerified: false, independent: true, network: "none", modelUsed: false,
    findings: [{ findingId: "finding-1", status: "pass", citations: [{ batchSha256, sourceId, contentSha256: digest("2"), receiptSha256: digest("3"), evidenceSha256: sha(evidence), evidenceBytes: evidence.length, offset: 0, status: "present" }] }],
    privateDataIncluded: false, externalEffects: false, authority: structuredClone(proposal.authority),
    boundary: "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority and does not claim semantic entailment, source truth, completeness, or publication readiness.",
  };
  const revision = Buffer.from("Prefer a current primary source and qualify unsupported freshness.").toString("base64");
  const review = buildResearchRevisionReview({
    report, verification, modelContractSha256: digest("4"), inferenceContractSha256: digest("5"), reviewPromptContractSha256: digest("6"),
    now: new Date(baseTime + 102), suffix: "000000000102",
    passes: ["critic-a", "critic-b"].map((role, index) => ({
      passId: `critic-pass-${index + 1}`, role, contextIsolationReceiptSha256: ["7", "8"][index].repeat(64), inferenceReceiptSha256: ["9", "a"][index].repeat(64),
      freshContext: true, workerTranscriptIncluded: false, sameProductModel: true, modelUsed: true,
      findings: [{ findingId: "finding-1", semanticSupport: "partially-supported", sourcePreference: "secondary-only-undisclosed", freshnessLabelAssessment: "label-unsupported", confidencePermille: 600, uncertaintyAcknowledged: false, requiredRevisionBase64: revision }],
      disposition: "revise",
    })),
  });
  const prompt = buildResearcherRevisionPrompt(value.plan, { round: 1, priorProposal: proposal, priorReport: report, revisionReview: review });
  assert.match(prompt, /Start by calling pixel_research for new evidence/u);
  assert.match(prompt, /unchanged ordered prefix/u);
  assert.match(prompt, /complete replacement proposal, not a patch/u);
  assert.match(prompt, /pixel-research-revision-input/u);
  assert.match(prompt, new RegExp(batchSha256, "u"));
  assert.match(prompt, /Prefer a current primary source/u);
  const wrong = structuredClone(report); wrong.titleBase64 = Buffer.from("Substituted report").toString("base64");
  assert.throws(() => buildResearcherRevisionPrompt(value.plan, { round: 1, priorProposal: proposal, priorReport: wrong, revisionReview: review }), /exact criticized candidate/u);
});

test("Researcher critic runner binds a tool-free RPC result to fresh context and cumulative inference receipts", async (t) => {
  const value = await fixture(t), calls = [];
  const result = await dockerSupervisorInternals.runResearcherCriticPass({
    prepared: value.dockerPrepared, claim: value.claim, runtime: value.runtime, executor: {},
  }, {
    role: "critic-a", passId: "critic-pass-1",
    input: { schemaVersion: 1, workerTranscriptIncluded: false, finding: "public fixture" },
  }, {
    remainingRuntimeMilliseconds() { return 12345; },
    async criticRpcRunner(options) {
      calls.push(options);
      return {
        text: JSON.stringify({
          findings: [{
            findingId: "finding-1", semanticSupport: "supported", sourcePreference: "primary-preferred",
            freshnessLabelAssessment: "label-correct", confidencePermille: 800,
            uncertaintyAcknowledged: false, requiredRevisionBase64: null,
          }],
          disposition: "no-revision-requested",
        }),
        frames: 9, toolCalls: 0, observedTools: [], stderrBytes: 0, stderrSha256: digest("1"),
        protocolVersion: 2, durationMilliseconds: 50, deferredToolRecoveries: 0, loopGuard: {},
      };
    },
    async criticCleanup() { return false; },
    async criticProxyReceipt() { return { modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 0 }; },
  });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].allowedTools, ["read"]);
  assert.equal(calls[0].requiredInitialTool, undefined);
  assert.equal(calls[0].maxRuntimeMs, 12345);
  assert.match(calls[0].prompt, /workerTranscriptIncluded/);
  assert.equal(result.findings[0].semanticSupport, "supported");
  assert.match(result.contextIsolationReceiptSha256, /^[a-f0-9]{64}$/u);
  assert.match(result.inferenceReceiptSha256, /^[a-f0-9]{64}$/u);

  await assert.rejects(dockerSupervisorInternals.runResearcherCriticPass({
    prepared: value.dockerPrepared, claim: value.claim, runtime: value.runtime, executor: {},
  }, { role: "critic-b", input: { schemaVersion: 1 } }, {
    async criticRpcRunner() { return { text: "{}", frames: 1, toolCalls: 1, observedTools: ["read"], protocolVersion: 2 }; },
  }), /used a tool/u);
});

test("Researcher composes its broker with one separately scoped capability extension", async (t) => {
  const value = await fixture(t); value.runtime.capability = capabilityRuntime();
  const command = buildResearcherDockerCommand(value.dockerPrepared, value.claim, value.runtime);
  assert.deepEqual(optionValues(command.args, "--trusted-extension"), [
    "/opt/pixel/deploy/work-runner/research-tool.mjs", "/opt/pixel/deploy/work-runner/capability-tool.mjs",
  ]);
  assert.deepEqual(optionValues(command.args, "--mount").slice(-3), [
    `type=bind,src=${value.runtime.capability.catalogPath},dst=/run/pixel/capability/${value.runtime.capability.catalogName},readonly`,
    `type=bind,src=${value.runtime.capability.requestDirectory},dst=/run/pixel/capability/requests`,
    `type=bind,src=${value.runtime.capability.responseDirectory},dst=/run/pixel/capability/responses,readonly`,
  ]);
  assert.deepEqual(command.allowedTools, ["bash", "edit", "glob", "grep", "pixel_cap_fixture_analyze_12345678", "pixel_research", "read", "write"]);
});

test("Researcher disposable volume and initialization are bounded by the consumed lease", async (t) => {
  const value = await fixture(t);
  const volume = buildResearcherVolumeCreate(value.dockerPrepared, value.claim, value.runtime);
  assert.deepEqual(volume.args.slice(0, 4), ["volume", "create", "--driver", "local"]);
  assert.match(volume.option, /^size=536870912,uid=1000,gid=1000,mode=0700,nosuid,nodev$/u);
  assert.ok(volume.args.includes("com.osmantic.pixel.work-role=researcher-workspace"));
  const inspectedVolume = {
    Name: value.runtime.volumeName, Driver: "local", Scope: "local",
    Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId,
      "com.osmantic.pixel.work-job": value.claim.jobId,
      "com.osmantic.pixel.work-role": "researcher-workspace",
    },
    Options: { device: "tmpfs", o: volume.option, type: "tmpfs" },
  };
  assert.equal(validateResearcherVolumeInspect(inspectedVolume, value.dockerPrepared, value.claim, value.runtime), true);
  const keeper = buildResearcherVolumeKeeperCommand(value.dockerPrepared, value.claim, value.runtime);
  assert.deepEqual(optionValues(keeper.args, "--network"), ["none"]);
  assert.deepEqual(optionValues(keeper.args, "--mount"), [`type=volume,src=${value.runtime.volumeName},dst=/workspace,readonly,volume-nocopy`]);
  const keeperImage = { Id: `sha256:${digest("f")}` };
  const keeperInspect = {
    Id: digest("6"), Name: `/${value.runtime.keeperName}`, Image: keeperImage.Id, State: { Running: true },
    Config: { Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId,
      "com.osmantic.pixel.work-job": value.claim.jobId,
      "com.osmantic.pixel.work-role": "researcher-volume-keeper",
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
  assert.equal(validateResearcherVolumeKeeperInspect(keeperInspect, keeperImage, value.dockerPrepared, value.claim, value.runtime), true);
  const writableKeeper = structuredClone(keeperInspect);
  writableKeeper.Mounts[0].RW = true;
  assert.throws(() => validateResearcherVolumeKeeperInspect(writableKeeper, keeperImage, value.dockerPrepared, value.claim, value.runtime), DockerResearcherError);
  const init = buildResearcherInitDockerCommand(value.dockerPrepared, value.claim, value.runtime);
  assert.ok(init.args.includes("/opt/pixel/deploy/work-runner/builder-volume.mjs"));
  assert.deepEqual(optionValues(init.args, "--network"), ["none"]);
  assert.deepEqual(optionValues(init.args, "--mount"), [
    "type=bind,src=/srv/pixel/work/source,dst=/source,readonly",
    `type=volume,src=${value.runtime.volumeName},dst=/workspace,volume-nocopy`,
  ]);

  const wrongQueue = { ...value.runtime, responseDirectory: "/run/pixel/jobs/research/responses,evil" };
  assert.throws(() => buildResearcherDockerCommand(value.dockerPrepared, value.claim, wrongQueue), DockerResearcherError);
  const widened = structuredClone(value.dockerPrepared);
  widened.plan.grantedCapabilities.network.services.push("frontier-work-provider");
  assert.throws(() => buildResearcherDockerCommand(widened, value.claim, value.runtime), /service contract/);
});

test("Researcher supervisor derives all broker queues and disposable resources from the consumed claim", async (t) => {
  const value = await fixture(t);
  const runtime = deriveResearcherDockerRuntime(value.dockerPrepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: "172.30.8.0/29", workerIp: "172.30.8.2", proxyIp: "172.30.8.3",
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    backendImageIdentifier: `sha256:${digest("8")}`, imageIdentifier: `sha256:${digest("f")}`,
    researchCourierQueueRoot: "/var/lib/pixel-courier/research", researchEndpoint: "http://127.0.0.1:8888",
    uid: 1000, gid: 1000,
  });
  assert.equal(runtime.runRoot, `/var/lib/pixel-work/runs/${value.claim.claimId}`);
  assert.equal(runtime.requestDirectory, `${runtime.runRoot}/research/requests`);
  assert.equal(runtime.responseDirectory, `${runtime.runRoot}/research/responses`);
  assert.equal(runtime.researchStateRoot, `${runtime.runRoot}/research/state`);
  assert.equal(runtime.researchObjectRoot, `${runtime.runRoot}/research/objects`);
  assert.equal(runtime.outputDirectory, `/var/lib/pixel-work/results/${value.claim.claimId}`);
  assert.equal(runtime.keeperName, "pixel-work-researcher-keep-000000000001");
  assert.equal(runtime.researchEndpoint, "http://127.0.0.1:8888");
  const proxyConfig = buildModelProxyConfig(value.dockerPrepared, value.claim, runtime);
  assert.deepEqual(proxyConfig.allowedTools, ["bash", "edit", "glob", "grep", "pixel_research", "read", "write"]);
  assert.deepEqual(validateModelProxyConfig(proxyConfig).allowedTools, proxyConfig.allowedTools);
  assert.doesNotMatch(JSON.stringify(runtime), /objective|acceptance|secret|credential/i);
  assert.throws(() => deriveResearcherDockerRuntime(value.dockerPrepared, value.claim, {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: "172.30.8.0/29", workerIp: "172.30.8.2", proxyIp: "172.30.8.3",
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    researchCourierQueueRoot: "/var/lib/pixel-courier/research", researchEndpoint: "https://example.com",
    uid: 1000, gid: 1000,
  }), /uncredentialed IPv4 loopback origin/);
});

test("Researcher cleanup-only recovery derives exact resources but cannot construct launch commands", async (t) => {
  const value = await fixture(t);
  const cleanup = {
    plan: value.plan, lease: value.lease, policy: value.policy, bindings: value.prepared.bindings,
    workspace: { sha256: value.claim.workspaceSha256 }, cleanupOnly: true,
    recoveryConsumption: structuredClone(value.claim),
  };
  const options = {
    dockerPath: "/usr/bin/docker", stateRoot: "/var/lib/pixel-work",
    networkSubnet: "172.30.8.0/29", workerIp: "172.30.8.2", proxyIp: "172.30.8.3",
    backendNetworkName: "pixel-reference_model-backend", backendContainerName: "pixel-reference-local-model-1",
    backendImageIdentifier: `sha256:${digest("8")}`, imageIdentifier: `sha256:${digest("f")}`,
    researchCourierQueueRoot: "/var/lib/pixel-courier/research", researchEndpoint: "http://127.0.0.1:8888",
    uid: 1000, gid: 1000,
  };
  const runtime = deriveResearcherDockerRuntime(cleanup, value.claim, options);
  assert.equal(runtime.volumeName, "pixel-work-researcher-000000000001");
  assert.throws(() => buildResearcherVolumeCreate(cleanup, value.claim, runtime), /cleanup boundary/);
  const volume = {
    Name: runtime.volumeName, Driver: "local", Scope: "local",
    Labels: {
      "com.osmantic.pixel.work-claim": value.claim.claimId, "com.osmantic.pixel.work-job": value.claim.jobId,
      "com.osmantic.pixel.work-role": "researcher-workspace",
    },
    Options: { type: "tmpfs", device: "tmpfs", o: `size=${runtime.volumeSizeBytes},uid=1000,gid=1000,mode=0700,nosuid,nodev` },
  };
  assert.equal(validateResearcherVolumeInspect(volume, cleanup, value.claim, runtime), true);
  const foreignVolume = structuredClone(volume);
  foreignVolume.Labels["com.osmantic.pixel.work-claim"] = "workclaim-1786366800000-000000000099";
  assert.throws(() => validateResearcherVolumeInspect(foreignVolume, cleanup, value.claim, runtime), /differs from its exact/);
  const missingExecutor = async () => {
    const error = new DockerSupervisorError("missing fixture resource");
    error.dockerExitCode = 1; error.dockerStderr = "No such object"; throw error;
  };
  const previousUmask = process.umask(0o077);
  let removed;
  try {
    removed = await cleanupInterruptedProfileAttempt(cleanup, value.claim, { ...options, allowNonLinuxTests: true, executor: missingExecutor });
  } finally {
    process.umask(previousUmask);
  }
  assert.equal(removed.resourcesRemoved, true);
  assert.equal(removed.usage.failures, 1);
  const docker29MissingExecutor = async (_command, args) => {
    const error = new DockerSupervisorError("missing fixture resource");
    error.dockerExitCode = 1;
    error.dockerStderr = args[0] === "network"
      ? `Error response from daemon: network ${args[2]} not found\n`
      : `Error response from daemon: No such ${args[0]}: ${args[2]}\n`;
    throw error;
  };
  let docker29Removed;
  const docker29Umask = process.umask(0o077);
  try {
    docker29Removed = await cleanupInterruptedProfileAttempt(cleanup, value.claim, { ...options, allowNonLinuxTests: true, executor: docker29MissingExecutor });
  } finally {
    process.umask(docker29Umask);
  }
  assert.equal(docker29Removed.resourcesRemoved, true);
  const hardFailureExecutor = async () => {
    const error = new DockerSupervisorError("cleanup target could not be removed");
    error.dockerExitCode = 1; error.dockerStderr = "permission denied"; throw Object.freeze(error);
  };
  let cleanupFailure;
  const cleanupUmask = process.umask(0o077);
  try {
    await cleanupInterruptedProfileAttempt(cleanup, value.claim, { ...options, allowNonLinuxTests: true, executor: hardFailureExecutor });
  } catch (error) { cleanupFailure = error; }
  finally { process.umask(cleanupUmask); }
  assert.ok(cleanupFailure instanceof DockerSupervisorError);
  assert.match(cleanupFailure.message, /cleanup failed closed/);
  assert.match(cleanupFailure.cause?.message ?? "", /Work cleanup failed closed/);
  assert.equal(cleanupFailure.workCleanupComplete, false);
  assert.notEqual(cleanupFailure.workRecoveryInconclusive, true);
  const substituted = structuredClone(value.claim);
  substituted.claimId = "workclaim-1786366800000-000000000099";
  await assert.rejects(cleanupInterruptedProfileAttempt(cleanup, substituted, { ...options, allowNonLinuxTests: true }), /claim differs/);
});

test("Researcher controller fails closed when no broker-verified source batch completed", () => {
  assert.throws(() => dockerSupervisorInternals.latestResearchBatchTime([]), /without any broker-verified source batch/);
  assert.throws(() => dockerSupervisorInternals.latestResearchBatchTime([{ createdAt: "invalid" }]), /time is invalid/);
  assert.equal(dockerSupervisorInternals.latestResearchBatchTime([
    { createdAt: "2026-08-10T13:00:03.000Z" }, { createdAt: "2026-08-10T13:00:05.000Z" },
  ]), Date.parse("2026-08-10T13:00:05.000Z"));
});

test("Researcher execution aggregation accounts for every fresh worker without widening the lease", () => {
  const worker = (context, calls, tool) => ({
    contextIsolationReceiptSha256: digest(context),
    execution: {
      text: "{}", frames: 5, toolCalls: calls, observedTools: [tool], stderrBytes: 0, stderrSha256: digest("0"),
      protocolVersion: 2, durationMilliseconds: 25, deferredToolRecoveries: 0,
      loopGuard: { schemaVersion: 1, policy: "pixel-rpc-observation-loop-guard-v1", status: "within-envelope", reason: null, completedToolCalls: calls, eventHeadSha256: digest(context) },
    },
  });
  const value = dockerSupervisorInternals.aggregateResearcherExecutions([worker("1", 1, "pixel_research"), worker("2", 2, "pixel_research")], 3);
  assert.equal(value.execution.toolCalls, 3);
  assert.equal(value.execution.durationMilliseconds, 50);
  assert.equal(value.execution.loopGuard.completedToolCalls, 3);
  assert.equal(value.receipts.length, 2);
  assert.throws(() => dockerSupervisorInternals.aggregateResearcherExecutions([worker("1", 1, "pixel_research"), worker("1", 1, "pixel_research")], 3), /reused or omitted/u);
  assert.throws(() => dockerSupervisorInternals.aggregateResearcherExecutions([worker("1", 2, "pixel_research"), worker("2", 2, "pixel_research")], 3), /aggregate tool-call lease/u);
});

test("Researcher cleanup retries bounded transient failures and preserves terminal failure evidence", async () => {
  let attempts = 0;
  assert.equal(await dockerSupervisorInternals.retryBoundedCleanup(async () => {
    attempts += 1;
    if (attempts < 3) throw new Error("transient cleanup race");
  }, 3, 0), true);
  assert.equal(attempts, 3);
  await assert.rejects(() => dockerSupervisorInternals.retryBoundedCleanup(async () => {
    throw new Error("persistent cleanup failure");
  }, 2, 0), /persistent cleanup failure/);
});

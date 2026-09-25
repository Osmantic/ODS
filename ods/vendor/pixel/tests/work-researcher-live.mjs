import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { compileResearcher } from "../deploy/work-broker/broker.mjs";
import { runResearcherDockerLifecycle } from "../deploy/work-runner/docker-supervisor.mjs";
import { claimLease, createLeaseConsumption, discardPreparedRun, prepareResearcherRun } from "../deploy/work-runner/runner-core.mjs";
import { fixtureModelBackendLabelArguments } from "./fixtures/work/model-backend.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const IMAGE_ID_RE = /^sha256:[a-f0-9]{64}$/u;
const EVIDENCE = Buffer.from("Pixel verified public evidence says the safety boundary is green.", "utf8");
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

function docker(path, args, options = {}) {
  return execFileSync(path, args, {
    encoding: "utf8", windowsHide: true, timeout: options.timeout ?? 60000,
    maxBuffer: options.maxBuffer ?? 4 * 1024 * 1024,
    env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" },
    stdio: options.stdio ?? ["ignore", "pipe", "pipe"],
  }).trim();
}

function ipv4(value) {
  return value.split(".").map(Number).reduce((result, part) => (((result << 8) | part) >>> 0), 0);
}

function pickSubnet(path) {
  const ids = docker(path, ["network", "ls", "--quiet"]).split(/\s+/u).filter(Boolean);
  const occupied = [];
  if (ids.length) for (const network of JSON.parse(docker(path, ["network", "inspect", ...ids], { maxBuffer: 16 * 1024 * 1024 }))) {
    for (const config of network.IPAM?.Config ?? []) {
      const match = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/u.exec(config.Subnet ?? "");
      if (!match) continue;
      const prefix = Number(match[2]);
      const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
      const start = ipv4(match[1]) & mask;
      occupied.push({ start: start >>> 0, end: (start | (~mask >>> 0)) >>> 0 });
    }
  }
  for (let third = 220; third < 230; third += 1) for (let fourth = 0; fourth < 256; fourth += 8) {
    const base = ipv4(`172.29.${third}.${fourth}`);
    if (occupied.every((range) => base + 7 < range.start || base > range.end)) return {
      networkSubnet: `172.29.${third}.${fourth}/29`, workerIp: `172.29.${third}.${fourth + 2}`, proxyIp: `172.29.${third}.${fourth + 3}`,
    };
  }
  throw new Error("No isolated /29 Researcher test subnet is available");
}

function budgets() {
  return {
    maxRuntimeSeconds: 120, maxIterations: 5, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 4, maxInputTokens: 100000, maxOutputTokens: 4000,
    maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 64 * 1024 * 1024,
    maxArtifactBytes: 2 * 1024 * 1024, maxNetworkBytes: 4 * 1024 * 1024,
    maxFailures: 1, noProgressLimit: 1,
  };
}

async function runFakeCourier(queueRoot, signal) {
  let served = 0;
  const processed = new Set();
  while (!signal.aborted) {
    const names = await readdir(queueRoot);
    const name = names.find((candidate) => /^req-[a-f0-9]{32}\.json$/u.test(candidate) && !processed.has(candidate));
    if (!name) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 5));
      continue;
    }
    const requestId = name.slice(4, -5);
    const request = JSON.parse(await readFile(join(queueRoot, name), "utf8"));
    const earliest = Number(request.research_receipt.retrievalId.split("-")[1]) + 1;
    const createdAt = new Date(Math.max(Date.now(), earliest)).toISOString();
    const receipt = {
      $schema: "https://osmantic.com/pixel/schemas/work-research-retrieval-v1.schema.json", schemaVersion: 1,
      retrievalId: request.research_receipt.retrievalId, requestId,
      jobId: request.research_receipt.jobId, claimId: request.research_receipt.claimId,
      queryId: request.research_receipt.queryId, searchEvidenceSha256: request.research_receipt.searchEvidenceSha256,
      planSha256: request.research_receipt.planSha256, sourceId: request.research_receipt.sourceId,
      canonicalUrlSha256: request.research_receipt.canonicalUrlSha256, createdAt,
      transport: request.research_receipt.transport,
      status: "fetched", responseName: `res-${requestId}.md`, contentSha256: sha(EVIDENCE),
      bytes: EVIDENCE.length, networkBytes: EVIDENCE.length, mediaType: "text/plain", finalUrl: request.url,
      redirects: 0, dnsPinned: true, safeMethodsOnly: true, reason: null,
      contentStoredBeyondJob: false, credentialsExposed: false, externalWritesPerformed: false,
      authority: {
        directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false,
        publish: false, purchase: false, policyMutation: false, scopeExpansion: false,
      },
      boundary: "Content-free research retrieval evidence. Transport and byte accounting are explicit; fetched content remains untrusted job-scoped data and grants no instruction or action authority.",
    };
    await writeFile(join(queueRoot, receipt.responseName), EVIDENCE, { flag: "wx", mode: 0o600 });
    await writeFile(join(queueRoot, `receipt-${requestId}.json`), `${JSON.stringify(receipt)}\n`, { flag: "wx", mode: 0o600 });
    processed.add(name);
    served += 1;
  }
  return served;
}

async function main() {
  if (process.platform !== "linux") throw new Error("Live Researcher qualification requires Linux");
  process.umask(0o077);
  const expectFailure = process.env.PIXEL_WORK_RESEARCHER_EXPECT_FAILURE === "1";
  const dockerPath = process.env.PIXEL_WORK_DOCKER_PATH ?? "/usr/bin/docker";
  const runnerImageId = process.env.PIXEL_WORK_RUNNER_IMAGE_ID;
  const backendImageId = process.env.PIXEL_WORK_FAKE_MODEL_IMAGE_ID ?? runnerImageId;
  const executorPath = resolve(process.env.PIXEL_WORK_OMP_PATH ?? "");
  if (!IMAGE_ID_RE.test(runnerImageId ?? "") || !IMAGE_ID_RE.test(backendImageId ?? "")) throw new Error("Exact local runner and fake-model image IDs are required");
  const executorBytes = await readFile(executorPath);
  if (executorBytes.length < 1) throw new Error("Pinned OMP executor is unavailable");

  const root = await mkdtemp(join(tmpdir(), "pixel-work-researcher-live-"));
  await chmod(root, 0o700);
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const stateRoot = join(root, "state");
  const courierQueueRoot = join(root, "courier");
  await Promise.all([objectStore, workspaceRoot, stateRoot, courierQueueRoot].map((path) => mkdir(path, { mode: 0o700 })));
  const suffix = randomBytes(6).toString("hex");
  const now = new Date();
  const jobId = `work-${String(now.getTime()).padStart(13, "0")}-${suffix}`;
  const backendNetworkName = `pixel-test-research-net-${suffix}`;
  const backendContainerName = `pixel-test-research-model-${suffix}`;
  const courierController = new AbortController();
  const courierPromise = runFakeCourier(courierQueueRoot, courierController.signal);
  let backendStarted = false;
  let backendNetworkStarted = false;
  let prepared;
  try {
    docker(dockerPath, ["network", "create", "--driver", "bridge", "--internal", "--attachable=false", "--label", "com.osmantic.pixel.test=researcher-live", backendNetworkName]);
    backendNetworkStarted = true;
    const fakeServer = resolve(import.meta.dirname, "fixtures/work/fake-llama-researcher.mjs");
    docker(dockerPath, [
      "run", "--rm", "-d", "--pull", "never", "--name", backendContainerName,
      ...fixtureModelBackendLabelArguments(),
      "--network", backendNetworkName, "--network-alias", "pixel-local-model",
      "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
      "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
      "--ulimit", "nofile=128:128", "--ipc", "none", "--cgroupns", "private",
      "--stop-timeout", "3", "--log-driver", "none", "--user", `${process.getuid()}:${process.getgid()}`,
      "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
      "--tmpfs", `/var/cache/pixel-model:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=${process.getuid()},gid=${process.getgid()}`,
      "--env", "HOME=/nonexistent", "--env", "NODE_ENV=production", "--env", "PIXEL_FAKE_MODEL_ID=assistant-model",
      "--env", `PIXEL_FAKE_RESEARCHER_MODE=${expectFailure ? "invalid-proposal" : "success"}`,
      "--mount", `type=bind,src=${fakeServer},dst=/opt/pixel-test/fake-llama-researcher.mjs,readonly`,
      "--entrypoint", "/opt/node/bin/node", backendImageId, "/opt/pixel-test/fake-llama-researcher.mjs",
    ]);
    backendStarted = true;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        docker(dockerPath, ["container", "exec", backendContainerName, "/opt/node/bin/node", "-e", 'fetch("http://127.0.0.1:8080/not-found",{method:"POST"}).then(async r=>{await r.arrayBuffer();if(r.status!==404)process.exit(1)}).catch(()=>process.exit(1))'], { timeout: 3000 });
        break;
      } catch (error) {
        if (attempt === 29) throw error;
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
      }
    }

    const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
    policy.enabled = true;
    policy.executor.sha256 = sha(executorBytes);
    policy.runner.prepared = true;
    policy.runner.imageDigest = runnerImageId;
    policy.runner.imageRef = `local/pixel-work-runner@${runnerImageId}`;
    policy.localModel.prepared = true;
    policy.localModel.imageDigest = backendImageId;
    policy.localModel.imageRef = `local/pixel-fake-model@${backendImageId}`;
    policy.localModel.id = "assistant-model";
    policy.localModel.contextWindow = 32768;
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
        maxQueries: 4, maxResultsPerQuery: 5, maxSources: 20, maxSourceBytes: 262144,
        maxTotalSourceBytes: 1048576, allowedSourceTypes: ["web", "news", "academic", "forum"],
      },
    };
    await installFixtureModelQualification(policy, root, now);
    const request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
      jobId, createdAt: now.toISOString(), requester: "pixel", profile: "researcher",
      objective: "Research the public agent safety boundary fixture.",
      acceptanceCriteria: ["Return one independently citation-checked finding from a public source"],
      dataClassification: "public", inputs: [],
      requestedCapabilities: {
        filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
        network: { mode: "brokered", services: ["local-model", "research-broker"] }, modelRoute: "local-only",
        hostAccess: false, ambientCredentials: false, externalEffects: false,
        mergeAuthority: false, deployAuthority: false, policyMutation: false,
      },
      budgets: budgets(), outputs: { mode: "artifacts", requiredKinds: ["finding-report", "document"] },
      research: {
        mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 2, maxResultsPerQuery: 2,
        maxSources: 4, maxSourceBytes: 65536, maxTotalSourceBytes: 262144, safeSearch: "strict",
        allowedDomains: ["example.com"], deniedDomains: [], sourceTypes: ["web"],
        citationVerification: true, retention: "job-only",
        boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
      },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    const compiled = compileResearcher(request, policy, [], { now, suffix });
    prepared = await prepareResearcherRun({
      ...compiled, policy, objectStore, workspaceRoot, executorPath,
      archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 }, now,
    });
    const claim = createLeaseConsumption(prepared, { now, suffix });
    await claimLease(stateRoot, claim);
    let result;
    let lifecycleError;
    try {
      result = await runResearcherDockerLifecycle(prepared, claim, {
        dockerPath, stateRoot, ...pickSubnet(dockerPath), backendNetworkName, backendContainerName,
        imageIdentifier: runnerImageId, backendImageIdentifier: backendImageId,
        researchCourierQueueRoot: courierQueueRoot, researchEndpoint: "http://127.0.0.1:8888",
        researchPipelineOptions: {
          searchImpl: async () => ({
            rawResults: [{ url: "https://example.com/public-safety", title: "Public safety fixture", snippet: "Deterministic public evidence", sourceType: "web" }],
            networkBytes: 256,
            usage: { searchRequests: 1, retrievalRequests: 0, sources: 0, networkBytes: 256, sourceBytes: 0, rejectedSources: 0 },
            adapter: "reference",
          }),
          timeoutMilliseconds: 30000,
        },
        uid: process.getuid(), gid: process.getgid(),
      });
    } catch (error) {
      lifecycleError = error;
    }
    courierController.abort();
    assert.equal(await courierPromise, 1);
    if (expectFailure) {
      assert.ok(lifecycleError instanceof Error);
      assert.equal(lifecycleError.workCleanupComplete, true);
      assert.equal(await lstat(join(stateRoot, "runs", claim.claimId)).catch(() => null), null);
      assert.equal(docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
      assert.equal(docker(dockerPath, ["network", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
      assert.equal(docker(dockerPath, ["volume", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
      process.stdout.write(`${JSON.stringify({ status: "failure-cleanup-pass", workCleanupComplete: lifecycleError.workCleanupComplete })}\n`);
      return;
    }
    if (lifecycleError) throw lifecycleError;
    assert.deepEqual(result.execution.observedTools, ["pixel_research"]);
    assert.equal(result.execution.toolCalls, 1);
    assert.equal(result.proxyReceipt.modelRequests, 2);
    assert.equal(result.serviceReceipt.completed, 1);
    assert.equal(result.cleanupComplete, true);
    assert.equal(result.verification.status, "evidence-pass");
    assert.equal(result.verification.semanticEntailmentVerified, false);
    assert.equal(result.report.batchSha256s.length, 1);
    assert.equal(result.report.findings[0].citations.length, 1);
    assert.equal(Buffer.from(result.report.findings[0].citations[0].evidenceBase64, "base64").toString("utf8"), EVIDENCE.toString("utf8"));
    const evidence = JSON.parse(await readFile(result.artifacts.evidence.path, "utf8"));
    assert.equal(evidence.sourceObjectRetention, "job-only-until-successful-cleanup");
    assert.equal(evidence.citedEvidenceRetention, "retained-in-public-report");
    assert.equal(evidence.authority.publish, false);
    assert.equal(await lstat(join(stateRoot, "runs", claim.claimId)).catch(() => null), null);
    assert.equal(docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["network", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["volume", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    process.stdout.write(`${JSON.stringify({ status: "pass", toolCalls: result.execution.toolCalls, modelRequests: result.proxyReceipt.modelRequests, researchQueries: result.serviceReceipt.completed, citationStatus: result.verification.status, protocolVersion: result.execution.protocolVersion })}\n`);
  } finally {
    courierController.abort();
    await courierPromise.catch(() => {});
    if (prepared) await discardPreparedRun(prepared).catch(() => {});
    if (backendStarted) try { docker(dockerPath, ["container", "rm", "--force", backendContainerName]); } catch { /* bounded test cleanup */ }
    if (backendNetworkStarted) try { docker(dockerPath, ["network", "rm", backendNetworkName]); } catch { /* bounded test cleanup */ }
    await rm(root, { recursive: true, force: true });
  }
}

main().catch((error) => {
  const diagnostics = error && typeof error === "object" ? {
    exitCode: error.exitCode ?? null, exitSignal: error.exitSignal ?? null,
    protocolStage: error.protocolStage ?? null, observedFrames: error.observedFrames ?? null,
    stderrBytes: error.stderrBytes ?? null,
  } : null;
  process.stderr.write(`pixel-work-researcher-live: ${error instanceof Error ? error.stack ?? error.message : "failed"}\n${JSON.stringify(diagnostics)}\n`);
  process.exitCode = 1;
});

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import {
  executeBuilderIteration, recordInterruptedBuilderFailure, resumeBuilderVerification,
} from "../deploy/work-controller/builder-loop.mjs";
import { initializeCheckpointLedger, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import {
  discardPreparedRun, prepareBuilderCleanupRecovery, prepareBuilderRun, prepareBuilderVerificationRecovery,
  recoverLeaseConsumption,
} from "../deploy/work-runner/runner-core.mjs";
import { setupBuilderDockerBoundary, verifyBuilderCandidate } from "../deploy/work-runner/docker-supervisor.mjs";
import { fixtureModelBackendLabelArguments } from "./fixtures/work/model-backend.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const IMAGE_ID_RE = /^sha256:[a-f0-9]{64}$/;
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

function docker(path, args, options = {}) {
  return execFileSync(path, args, {
    encoding: "utf8", windowsHide: true, timeout: options.timeout ?? 60000,
    maxBuffer: options.maxBuffer ?? 4 * 1024 * 1024,
    env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" },
    stdio: options.stdio ?? ["ignore", "pipe", "pipe"],
  }).trim();
}

function octal(value, length) {
  return `${value.toString(8).padStart(length - 1, "0")}\0`;
}

function tarHeader(name, type, size) {
  const value = Buffer.alloc(512);
  value.write(name, 0, 100, "utf8");
  value.write(octal(type === "directory" ? 0o755 : 0o644, 8), 100, 8, "ascii");
  value.write(octal(0, 8), 108, 8, "ascii");
  value.write(octal(0, 8), 116, 8, "ascii");
  value.write(octal(size, 12), 124, 12, "ascii");
  value.write(octal(0, 12), 136, 12, "ascii");
  value.fill(0x20, 148, 156);
  value[156] = type === "directory" ? 0x35 : 0x30;
  value.write("ustar\0", 257, 6, "latin1");
  value.write("00", 263, 2, "ascii");
  let checksum = 0;
  for (const byte of value) checksum += byte;
  value.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  return value;
}

function archive(items) {
  const blocks = [];
  for (const item of items) {
    const content = Buffer.from(item.content ?? "", "utf8");
    blocks.push(tarHeader(item.name, item.type ?? "file", content.length));
    if (content.length) {
      blocks.push(content);
      const padding = (512 - (content.length % 512)) % 512;
      if (padding) blocks.push(Buffer.alloc(padding));
    }
  }
  blocks.push(Buffer.alloc(1024));
  return Buffer.concat(blocks);
}

function ipv4(value) {
  return value.split(".").map(Number).reduce((result, part) => (((result << 8) | part) >>> 0), 0);
}

function pickSubnet(path) {
  const ids = docker(path, ["network", "ls", "--quiet"]).split(/\s+/u).filter(Boolean);
  const occupied = [];
  if (ids.length) {
    for (const network of JSON.parse(docker(path, ["network", "inspect", ...ids], { maxBuffer: 16 * 1024 * 1024 }))) {
      for (const config of network.IPAM?.Config ?? []) {
        const match = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(config.Subnet ?? "");
        if (!match) continue;
        const prefix = Number(match[2]);
        const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
        const start = ipv4(match[1]) & mask;
        occupied.push({ start: start >>> 0, end: (start | (~mask >>> 0)) >>> 0 });
      }
    }
  }
  for (let third = 230; third < 240; third += 1) for (let fourth = 0; fourth < 256; fourth += 8) {
    const base = ipv4(`172.29.${third}.${fourth}`);
    if (occupied.every((range) => base + 7 < range.start || base > range.end)) return {
      networkSubnet: `172.29.${third}.${fourth}/29`, workerIp: `172.29.${third}.${fourth + 2}`, proxyIp: `172.29.${third}.${fourth + 3}`,
    };
  }
  throw new Error("No isolated /29 test subnet is available");
}

function budgets() {
  return {
    maxRuntimeSeconds: 240, maxIterations: 3, maxToolCalls: 40, maxConcurrentSubagents: 1,
    maxModelRequests: 8, maxInputTokens: 200000, maxOutputTokens: 8000,
    maxCpuCores: 1, maxMemoryMiB: 2048, maxDiskBytes: 64 * 1024 * 1024,
    maxArtifactBytes: 4 * 1024 * 1024, maxNetworkBytes: 8 * 1024 * 1024,
    maxFailures: 2, noProgressLimit: 2,
  };
}

async function main() {
  if (process.platform !== "linux") throw new Error("Live Builder loop qualification requires Linux");
  process.umask(0o077);
  const dockerPath = process.env.PIXEL_WORK_DOCKER_PATH ?? "/usr/bin/docker";
  const runnerImageId = process.env.PIXEL_WORK_RUNNER_IMAGE_ID;
  const backendImageId = process.env.PIXEL_WORK_FAKE_MODEL_IMAGE_ID ?? runnerImageId;
  const executorPath = resolve(process.env.PIXEL_WORK_OMP_PATH ?? "");
  if (!IMAGE_ID_RE.test(runnerImageId ?? "") || !IMAGE_ID_RE.test(backendImageId ?? "")) throw new Error("Exact local runner and fake-model image IDs are required");
  const executorBytes = await readFile(executorPath);
  if (executorBytes.length < 1) throw new Error("Pinned OMP executor is unavailable");

  const root = await mkdtemp(join(tmpdir(), "pixel-work-builder-loop-live-"));
  await chmod(root, 0o700);
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const stateRoot = join(root, "state");
  await Promise.all([objectStore, workspaceRoot, stateRoot].map((path) => mkdir(path, { mode: 0o700 })));
  const idSuffix = randomBytes(6).toString("hex");
  const now = new Date();
  const jobId = `work-${String(now.getTime()).padStart(13, "0")}-${idSuffix}`;
  const backendNetworkName = `pixel-test-builder-loop-${idSuffix}`;
  const backendContainerName = `pixel-test-builder-loop-model-${idSuffix}`;
  let backendStarted = false;
  let backendNetworkStarted = false;
  let prepared;
  let recoveryPrepared;
  let interruptedPrepared;
  let cleanupPrepared;
  let interruptedBoundary;
  try {
    docker(dockerPath, ["network", "create", "--driver", "bridge", "--internal", "--attachable=false", "--label", "com.osmantic.pixel.test=builder-loop-live", backendNetworkName]);
    backendNetworkStarted = true;
    const fakeServer = resolve(import.meta.dirname, "fixtures/work/fake-llama-builder-loop.mjs");
    docker(dockerPath, [
      "run", "--rm", "-d", "--pull", "never", "--name", backendContainerName,
      ...fixtureModelBackendLabelArguments(),
      "--network", backendNetworkName, "--network-alias", "pixel-local-model",
      "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
      "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
      "--ulimit", "nofile=128:128", "--ipc", "none", "--cgroupns", "private", "--stop-timeout", "3",
      "--log-driver", "none", "--user", `${process.getuid()}:${process.getgid()}`,
      "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777", "--env", "HOME=/nonexistent",
      "--env", "NODE_ENV=production", "--env", "PIXEL_FAKE_MODEL_ID=assistant-model",
      "--mount", `type=bind,src=${fakeServer},dst=/opt/pixel-test/fake-llama-builder-loop.mjs,readonly`,
      "--entrypoint", "/opt/node/bin/node", backendImageId, "/opt/pixel-test/fake-llama-builder-loop.mjs",
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

    const bytes = archive([
      { name: "src/", type: "directory" },
      { name: "src/main.js", content: "export const invariant = 'PIXEL_FIXTURE_INVARIANT=42';\n" },
      { name: ".omp/tools/escape.mjs", content: "PIXEL_HOSTILE_CONTROL_CANARY\n" },
      { name: ".mcp.json", content: "{\"command\":\"PIXEL_HOSTILE_CONTROL_CANARY\"}\n" },
      { name: "AGENTS.md", content: "PIXEL_HOSTILE_CONTROL_CANARY\n" },
    ]);
    const contentSha256 = sha(bytes);
    await writeFile(join(objectStore, `${contentSha256}.tar`), bytes, { mode: 0o600 });
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
    policy.profiles.builder.enabled = true;
    policy.verifier.enabled = true;
    await installFixtureModelQualification(policy, root, now);
    const request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
      jobId, createdAt: now.toISOString(), requester: "pixel", profile: "builder",
      objective: "Complete a two-step local repair while preserving verified work across iterations.",
      acceptanceCriteria: ["The cumulative patch remains within its immutable boundary", "STEP1 exists and source/src/main.js contains invariant 43"],
      verification: {
        mode: "independent",
        checks: [
          { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
          {
            id: "cumulative-result", kind: "command", criterionIndexes: [1], workingDirectory: "source",
            argv: ["/opt/node/bin/node", "-e", "const fs=require('fs');if(!fs.existsSync('STEP1.txt')||!/INVARIANT=43/.test(fs.readFileSync('src/main.js','utf8')))process.exit(1)"],
            timeoutSeconds: 60, maxOutputBytes: 65536,
          },
        ],
        immutablePathPrefixes: ["source/__pixel_inert__/"], maxRuntimeSeconds: 120,
        maxOutputBytes: 131072, network: "none",
        boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
      },
      dataClassification: "internal",
      inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes.length, classification: "internal" }],
      requestedCapabilities: {
        filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
        network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
        hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
        deployAuthority: false, policyMutation: false,
      },
      budgets: budgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    const entries = [{
      id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
      bytes: bytes.length, classification: "internal", mountMode: "read-only",
    }];
    const compiled = compileBuilder(request, policy, entries, { now, suffix: idSuffix });
    const archiveLimits = { maxEntries: 100, maxFileBytes: 1024 * 1024 };
    const lifecycleOptions = {
      dockerPath, stateRoot, ...pickSubnet(dockerPath), backendNetworkName, backendContainerName,
      imageIdentifier: runnerImageId, backendImageIdentifier: backendImageId, uid: process.getuid(), gid: process.getgid(),
    };

    const interruptSuffix = randomBytes(6).toString("hex");
    const interruptedRequest = structuredClone(request);
    interruptedRequest.jobId = `work-${String(now.getTime()).padStart(13, "0")}-${interruptSuffix}`;
    const interruptedCompiled = compileBuilder(interruptedRequest, policy, entries, { now, suffix: interruptSuffix });
    const interruptedLifecycleOptions = { ...lifecycleOptions, ...pickSubnet(dockerPath) };
    interruptedPrepared = await prepareBuilderRun({
      ...interruptedCompiled, policy, objectStore, workspaceRoot, stateRoot, executorPath, archiveLimits, now,
    });
    await initializeCheckpointLedger({
      stateRoot, plan: interruptedCompiled.plan, lease: interruptedCompiled.lease,
      workspaceSnapshotSha256: interruptedPrepared.workspace.sha256,
      now: new Date(now.getTime() + 1), suffix: randomBytes(6).toString("hex"),
    });
    await assert.rejects(executeBuilderIteration({
      stateRoot, prepared: interruptedPrepared, lifecycleOptions: interruptedLifecycleOptions,
      candidateRunner: async (boundaryPrepared, claim, options) => {
        interruptedBoundary = await setupBuilderDockerBoundary(boundaryPrepared, claim, options);
        const failure = new Error("SIMULATED_WORKER_PROCESS_CRASH");
        failure.workCleanupComplete = false;
        throw failure;
      },
      verifier: async () => { throw new Error("interrupted worker must not reach verification"); },
    }), /job is cleanup-failed/);
    assert.ok(interruptedBoundary);
    for (const kind of ["container", "network", "volume"]) {
      assert.notEqual(docker(dockerPath, [kind, "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${interruptedRequest.jobId}`]), "");
    }
    await discardPreparedRun(interruptedPrepared);
    interruptedPrepared = null;
    const interruptedPending = await recoverCheckpointLedger({
      stateRoot, plan: interruptedCompiled.plan, lease: interruptedCompiled.lease,
    });
    assert.equal(interruptedPending.head.state, "cleanup-failed");
    const interruptedClaim = await recoverLeaseConsumption(stateRoot, interruptedCompiled.lease);
    cleanupPrepared = await prepareBuilderCleanupRecovery({
      ...interruptedCompiled, policy, objectStore, workspaceRoot, stateRoot, executorPath, archiveLimits,
      now: new Date(Date.parse(interruptedCompiled.lease.expiresAt) + 1000),
      recovery: { consumption: interruptedClaim, checkpoint: interruptedPending.head },
    });
    const interruptedFailure = await recordInterruptedBuilderFailure({
      stateRoot, prepared: cleanupPrepared, lifecycleOptions: interruptedLifecycleOptions,
    });
    assert.equal(interruptedFailure.action, "failed");
    assert.equal(interruptedFailure.checkpoint.usage.failures, 1);
    assert.equal(interruptedFailure.cleanup.resourcesRemoved, true);
    cleanupPrepared = null;
    interruptedBoundary = null;
    const interruptedLedger = await recoverCheckpointLedger({
      stateRoot, plan: interruptedCompiled.plan, lease: interruptedCompiled.lease,
    });
    assert.deepEqual(interruptedLedger.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "cleanup-failed", "failed"]);
    for (const kind of ["container", "network", "volume"]) {
      assert.equal(docker(dockerPath, [kind, "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${interruptedRequest.jobId}`]), "");
    }

    prepared = await prepareBuilderRun({ ...compiled, policy, objectStore, workspaceRoot, stateRoot, executorPath, archiveLimits, now });
    await initializeCheckpointLedger({
      stateRoot, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
      now: new Date(now.getTime() + 1), suffix: randomBytes(6).toString("hex"),
    });
    await assert.rejects(executeBuilderIteration({
      stateRoot, prepared, lifecycleOptions,
      verifier: async (...args) => {
        await verifyBuilderCandidate(...args);
        throw new Error("SIMULATED_POST_VERIFIER_CRASH");
      },
    }), /SIMULATED_POST_VERIFIER_CRASH/);
    await discardPreparedRun(prepared);
    prepared = null;
    const pending = await recoverCheckpointLedger({ stateRoot, plan: compiled.plan, lease: compiled.lease });
    assert.equal(pending.head.state, "verifying");
    const previousConsumption = JSON.parse(await readFile(join(stateRoot, "claims", `${compiled.lease.leaseId}.json`), "utf8"));
    recoveryPrepared = await prepareBuilderVerificationRecovery({
      ...compiled, policy, objectStore, workspaceRoot, stateRoot, executorPath, archiveLimits,
      now: new Date(Date.parse(compiled.lease.expiresAt) + 1000),
      recovery: { consumption: previousConsumption, checkpoint: pending.head },
    });
    const first = await resumeBuilderVerification({ stateRoot, prepared: recoveryPrepared, lifecycleOptions });
    assert.equal(first.action, "continue");
    assert.deepEqual(first.verification.evidence.criteria.map((criterion) => criterion.status), ["pass", "fail"]);
    assert.equal(first.checkpoint.progress.criteriaPassing, 1);
    assert.equal(first.nextLease.lease.iteration, 2);
    await discardPreparedRun(recoveryPrepared);
    recoveryPrepared = null;

    prepared = await prepareBuilderRun({
      ...compiled, lease: first.nextLease.lease, policy, objectStore, workspaceRoot, stateRoot, executorPath, archiveLimits,
      now: new Date(),
      continuation: {
        previousLease: compiled.lease, previousConsumption: first.claim,
        checkpoint: first.checkpoint, patchPath: first.candidate.patch.path,
      },
    });
    const second = await executeBuilderIteration({ stateRoot, prepared, lifecycleOptions: { ...lifecycleOptions, ...pickSubnet(dockerPath) } });
    assert.equal(second.action, "completed");
    assert.equal(second.verification.evidence.status, "pass");
    assert.match(second.candidate.text, /PIXEL_BUILDER_LOOP_GREEN/);
    assert.equal(second.claim.leaseId, first.nextLease.lease.leaseId);
    assert.notEqual(second.claim.claimId, first.claim.claimId);
    const cumulativePatch = JSON.parse(await readFile(second.candidate.patch.path, "utf8"));
    assert.deepEqual(cumulativePatch.changes.map(({ path, operation }) => ({ path, operation })), [
      { path: "source/STEP1.txt", operation: "add" },
      { path: "source/src/main.js", operation: "modify" },
    ]);
    assert.doesNotMatch(JSON.stringify(cumulativePatch), /PIXEL_HOSTILE_CONTROL_CANARY/);
    assert.match(await readFile(join(prepared.workspace.originalPath, "source/src/main.js"), "utf8"), /INVARIANT=42/);
    const ledger = await recoverCheckpointLedger({ stateRoot, plan: compiled.plan, lease: first.nextLease.lease });
    assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), [
      "authorized", "running", "verifying", "verified", "running", "verifying", "verified", "completed",
    ]);
    assert.equal(docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["network", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["volume", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    process.stdout.write(`${JSON.stringify({
      status: "pass", iterations: 2, crashRecovered: true, workerCrashRecovered: true,
      criteria: second.verification.evidence.criteria.map((criterion) => criterion.status),
      cumulativeChanges: second.candidate.patch.changes, claims: 2, protocolVersion: second.candidate.protocolVersion,
    })}\n`);
  } finally {
    if (prepared) await discardPreparedRun(prepared).catch(() => {});
    if (recoveryPrepared) await discardPreparedRun(recoveryPrepared).catch(() => {});
    if (interruptedBoundary) await interruptedBoundary.cleanup().catch(() => {});
    if (interruptedPrepared) await discardPreparedRun(interruptedPrepared).catch(() => {});
    if (backendStarted) try { docker(dockerPath, ["container", "rm", "--force", backendContainerName]); } catch { /* bounded test cleanup */ }
    if (backendNetworkStarted) try { docker(dockerPath, ["network", "rm", backendNetworkName]); } catch { /* bounded test cleanup */ }
    await rm(root, { recursive: true, force: true });
  }
}

main().catch((error) => {
  const diagnostic = error instanceof Error ? {
    message: error.message,
    cause: error.cause instanceof Error ? error.cause.message : null,
    dockerExitCode: error.dockerExitCode ?? null,
    dockerStderr: typeof error.dockerStderr === "string" ? error.dockerStderr.slice(-2048) : null,
  } : { message: "failed" };
  process.stderr.write(`pixel-work-builder-loop-live: ${JSON.stringify(diagnostic)}\n`);
  process.exitCode = 1;
});

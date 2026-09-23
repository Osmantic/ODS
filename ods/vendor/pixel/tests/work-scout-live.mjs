import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmod, mkdir, mkdtemp, readFile, rm, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import {
  claimLease, createLeaseConsumption, discardPreparedRun, prepareScoutRun,
} from "../deploy/work-runner/runner-core.mjs";
import { runScoutDockerLifecycle } from "../deploy/work-runner/docker-supervisor.mjs";
import { fixtureModelBackendLabelArguments, fixtureModelBackendNetworkLabelArguments } from "./fixtures/work/model-backend.mjs";
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

function occupiedRanges(path) {
  const ids = docker(path, ["network", "ls", "--quiet"]).split(/\s+/u).filter(Boolean);
  if (!ids.length) return [];
  const networks = JSON.parse(docker(path, ["network", "inspect", ...ids], { maxBuffer: 16 * 1024 * 1024 }));
  const output = [];
  for (const network of networks) {
    for (const config of network.IPAM?.Config ?? []) {
      const match = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(config.Subnet ?? "");
      if (!match) continue;
      const prefix = Number(match[2]);
      const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
      const start = ipv4(match[1]) & mask;
      output.push({ start: start >>> 0, end: (start | (~mask >>> 0)) >>> 0 });
    }
  }
  return output;
}

function pickSubnet(path) {
  const occupied = occupiedRanges(path);
  for (let third = 240; third < 255; third += 1) {
    for (let fourth = 0; fourth < 256; fourth += 8) {
      const base = ipv4(`172.29.${third}.${fourth}`);
      if (occupied.every((range) => base + 7 < range.start || base > range.end)) {
        return { networkSubnet: `172.29.${third}.${fourth}/29`, workerIp: `172.29.${third}.${fourth + 2}`, proxyIp: `172.29.${third}.${fourth + 3}` };
      }
    }
  }
  throw new Error("No isolated /29 test subnet is available");
}

function budgets() {
  return {
    maxRuntimeSeconds: 120, maxIterations: 5, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 4, maxInputTokens: 100000, maxOutputTokens: 2000,
    maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 64 * 1024 * 1024,
    maxArtifactBytes: 1024 * 1024, maxNetworkBytes: 4 * 1024 * 1024,
    maxFailures: 1, noProgressLimit: 1,
  };
}

async function main() {
  if (process.platform !== "linux") throw new Error("Live Scout qualification requires Linux");
  process.umask(0o077);
  const dockerPath = process.env.PIXEL_WORK_DOCKER_PATH ?? "/usr/bin/docker";
  const runnerImageId = process.env.PIXEL_WORK_RUNNER_IMAGE_ID;
  const backendImageId = process.env.PIXEL_WORK_FAKE_MODEL_IMAGE_ID ?? runnerImageId;
  const executorPath = resolve(process.env.PIXEL_WORK_OMP_PATH ?? "");
  if (!IMAGE_ID_RE.test(runnerImageId ?? "") || !IMAGE_ID_RE.test(backendImageId ?? "")) throw new Error("Exact local runner and fake-model image IDs are required");
  const executorBytes = await readFile(executorPath);
  if (executorBytes.length < 1) throw new Error("Pinned OMP executor is unavailable");

  const root = await mkdtemp(join(tmpdir(), "pixel-work-scout-live-"));
  await chmod(root, 0o700);
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const stateRoot = join(root, "state");
  await Promise.all([objectStore, workspaceRoot, stateRoot].map((path) => mkdir(path, { mode: 0o700 })));
  const suffix = randomBytes(6).toString("hex");
  const now = new Date();
  const epoch = String(now.getTime()).padStart(13, "0");
  const jobId = `work-${epoch}-${suffix}`;
  const backendNetworkName = `pixel-test-model-net-${suffix}`;
  const backendContainerName = `pixel-test-model-${suffix}`;
  const modelBackendBindings = {
    policySha256: "a".repeat(64), environmentSha256: "b".repeat(64), configurationSha256: "c".repeat(64), artifactSha256: "1".repeat(64),
  };
  let backendStarted = false;
  let backendNetworkStarted = false;
  let prepared;
  try {
    docker(dockerPath, [
      "network", "create", "--driver", "bridge", "--internal", "--attachable=false",
      ...fixtureModelBackendNetworkLabelArguments({}, modelBackendBindings),
      "--label", "com.osmantic.pixel.test=scout-live", backendNetworkName,
    ]);
    backendNetworkStarted = true;
    const fakeServer = resolve(import.meta.dirname, "fixtures/work/fake-llama-responses.mjs");
    docker(dockerPath, [
      "run", "--rm", "-d", "--pull", "never", "--name", backendContainerName,
      ...fixtureModelBackendLabelArguments({}, modelBackendBindings),
      "--network", backendNetworkName, "--network-alias", "pixel-local-model",
      "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
      "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
      "--ulimit", "nofile=128:128", "--ipc", "none", "--cgroupns", "private",
      "--stop-timeout", "3", "--log-driver", "none", "--user", `${process.getuid()}:${process.getgid()}`,
      "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
      "--tmpfs", `/var/cache/pixel-model:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=${process.getuid()},gid=${process.getgid()}`,
      "--env", "HOME=/nonexistent", "--env", "NODE_ENV=production", "--env", "PIXEL_FAKE_MODEL_ID=assistant-model",
      "--mount", `type=bind,src=${fakeServer},dst=/opt/pixel-test/fake-llama-responses.mjs,readonly`,
      "--entrypoint", "/opt/node/bin/node", backendImageId, "/opt/pixel-test/fake-llama-responses.mjs",
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
    await installFixtureModelQualification(policy, root, now);
    const request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
      jobId, createdAt: now.toISOString(), requester: "pixel", profile: "scout",
      objective: "Read source/src/main.js and report its invariant.", acceptanceCriteria: ["Report invariant 42 from the supplied source file"],
      dataClassification: "internal",
      inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes.length, classification: "internal" }],
      requestedCapabilities: {
        filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
        modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
        mergeAuthority: false, deployAuthority: false, policyMutation: false,
      },
      budgets: budgets(), outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    const entries = [{
      id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
      bytes: bytes.length, classification: "internal", mountMode: "read-only",
    }];
    const compiled = compileScout(request, policy, entries, { now, suffix });
    prepared = await prepareScoutRun({
      ...compiled, policy, objectStore, workspaceRoot, executorPath,
      archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 }, now,
    });
    const claim = createLeaseConsumption(prepared, { now, suffix });
    await claimLease(stateRoot, claim);
    const network = pickSubnet(dockerPath);
    const result = await runScoutDockerLifecycle(prepared, claim, {
      dockerPath, stateRoot, ...network,
      backendNetworkName, backendContainerName,
      imageIdentifier: runnerImageId, backendImageIdentifier: backendImageId,
      modelBackendBindings, requireExactModelBackendBindings: true,
      uid: process.getuid(), gid: process.getgid(),
    });
    assert.match(Buffer.from(result.report.findings[0].statementBase64, "base64").toString("utf8"), /PIXEL_SCOUT_E2E_GREEN/);
    assert.doesNotMatch(JSON.stringify(result.report), /PIXEL_HOSTILE_CONTROL_CANARY/);
    assert.equal(result.verification.status, "evidence-pass");
    assert.equal(result.verification.semanticEntailmentVerified, false);
    assert.ok(result.toolCalls >= 1);
    assert.deepEqual(result.observedTools, ["read"]);
    assert.equal(result.proxyReceipt.modelRequests, 2);
    assert.equal(result.proxyReceipt.credentialsExposed, false);
    const leftovers = docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]);
    assert.equal(leftovers, "");
    process.stdout.write(`${JSON.stringify({ status: "pass", toolCalls: result.toolCalls, modelRequests: result.proxyReceipt.modelRequests, protocolVersion: result.protocolVersion })}\n`);
  } finally {
    if (prepared) await discardPreparedRun(prepared).catch(() => {});
    if (backendStarted) {
      try { docker(dockerPath, ["container", "rm", "--force", backendContainerName]); } catch { /* bounded test cleanup */ }
    }
    if (backendNetworkStarted) {
      try { docker(dockerPath, ["network", "rm", backendNetworkName]); } catch { /* bounded test cleanup */ }
    }
    await rm(root, { recursive: true, force: true });
  }
}

main().catch((error) => {
  const details = error instanceof Error ? {
    message: error.message,
    exitCode: error.exitCode ?? null,
    exitSignal: error.exitSignal ?? null,
    protocolStage: error.protocolStage ?? null,
    observedFrames: error.observedFrames ?? null,
    stderrBytes: error.stderrBytes ?? null,
    cause: error.cause instanceof Error ? error.cause.message : null,
  } : { message: "failed" };
  process.stderr.write(`pixel-work-scout-live: ${JSON.stringify(details)}\n`);
  process.exitCode = 1;
});

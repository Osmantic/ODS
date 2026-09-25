import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { claimLease, createLeaseConsumption, discardPreparedRun, prepareBuilderRun } from "../deploy/work-runner/runner-core.mjs";
import { runBuilderDockerLifecycle } from "../deploy/work-runner/docker-supervisor.mjs";
import { fixtureModelBackendLabelArguments } from "./fixtures/work/model-backend.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const IMAGE_ID_RE = /^sha256:[a-f0-9]{64}$/;
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const builderMemoryMiB = Number(process.env.PIXEL_WORK_BUILDER_MEMORY_MIB ?? "2048");
if (!Number.isSafeInteger(builderMemoryMiB) || builderMemoryMiB < 256 || builderMemoryMiB > 1048576) throw new Error("PIXEL_WORK_BUILDER_MEMORY_MIB must be an integer from 256 through 1048576");

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
    maxRuntimeSeconds: 180, maxIterations: 5, maxToolCalls: 30, maxConcurrentSubagents: 1,
    maxModelRequests: 10, maxInputTokens: 100000, maxOutputTokens: 4000,
    maxCpuCores: 1, maxMemoryMiB: builderMemoryMiB, maxDiskBytes: 64 * 1024 * 1024,
    maxArtifactBytes: 1024 * 1024, maxNetworkBytes: 4 * 1024 * 1024,
    maxFailures: 1, noProgressLimit: 1,
  };
}

async function main() {
  if (process.platform !== "linux") throw new Error("Live Builder qualification requires Linux");
  process.umask(0o077);
  const dockerPath = process.env.PIXEL_WORK_DOCKER_PATH ?? "/usr/bin/docker";
  const runnerImageId = process.env.PIXEL_WORK_RUNNER_IMAGE_ID;
  const backendImageId = process.env.PIXEL_WORK_FAKE_MODEL_IMAGE_ID ?? runnerImageId;
  const executorPath = resolve(process.env.PIXEL_WORK_OMP_PATH ?? "");
  if (!IMAGE_ID_RE.test(runnerImageId ?? "") || !IMAGE_ID_RE.test(backendImageId ?? "")) throw new Error("Exact local runner and fake-model image IDs are required");
  const executorBytes = await readFile(executorPath);
  if (executorBytes.length < 1) throw new Error("Pinned OMP executor is unavailable");

  const root = await mkdtemp(join(tmpdir(), "pixel-work-builder-live-"));
  await chmod(root, 0o700);
  const objectStore = join(root, "objects");
  const workspaceRoot = join(root, "workspaces");
  const stateRoot = join(root, "state");
  await Promise.all([objectStore, workspaceRoot, stateRoot].map((path) => mkdir(path, { mode: 0o700 })));
  const suffix = randomBytes(6).toString("hex");
  const now = new Date();
  const epoch = String(now.getTime()).padStart(13, "0");
  const jobId = `work-${epoch}-${suffix}`;
  const backendNetworkName = `pixel-test-builder-net-${suffix}`;
  const backendContainerName = `pixel-test-builder-model-${suffix}`;
  let backendStarted = false;
  let backendNetworkStarted = false;
  let prepared;
  try {
    docker(dockerPath, ["network", "create", "--driver", "bridge", "--internal", "--attachable=false", "--label", "com.osmantic.pixel.test=builder-live", backendNetworkName]);
    backendNetworkStarted = true;
    const fakeServer = resolve(import.meta.dirname, "fixtures/work/fake-llama-builder.mjs");
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
      "--mount", `type=bind,src=${fakeServer},dst=/opt/pixel-test/fake-llama-builder.mjs,readonly`,
      "--entrypoint", "/opt/node/bin/node", backendImageId, "/opt/pixel-test/fake-llama-builder.mjs",
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
      { name: "pyproject.toml", content: "[project]\nname = \"pixel-builder-fixture\"\nversion = \"0.0.0\"\n" },
      { name: "app.py", content: "def add(left: int, right: int) -> int:\n    return left + right\n\n\nanswer = add(20, 22)\n" },
      { name: "debug_target.py", content: "from pathlib import Path\n\nvalue = 42\nPath(\"debug-proof.txt\").write_text(f\"{value + 1}\\n\", encoding=\"utf-8\")\n" },
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
      objective: "Use language intelligence and a real debugger on the Python fixture, then change the JavaScript invariant from 42 to 43 and prove all results locally.",
      acceptanceCriteria: ["source/src/main.js contains invariant 43", "A debugpy-controlled execution creates source/debug-proof.txt containing 43", "A local command successfully validates both results"],
      verification: {
        mode: "independent",
        checks: [
          { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
          {
            id: "changed-invariant", kind: "command", criterionIndexes: [0, 1, 2], workingDirectory: "source",
            argv: ["/opt/node/bin/node", "-e", "const fs=require('fs');if(!/INVARIANT=43/.test(fs.readFileSync('src/main.js','utf8'))||fs.readFileSync('debug-proof.txt','utf8')!=='43\\n')process.exit(1)"],
            timeoutSeconds: 60, maxOutputBytes: 65536,
          },
        ],
        immutablePathPrefixes: ["source/__pixel_inert__/"],
        maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
        boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
      },
      dataClassification: "internal",
      inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes.length, classification: "internal" }],
      requestedCapabilities: {
        filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
        network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
        hostAccess: false, ambientCredentials: false, externalEffects: false,
        mergeAuthority: false, deployAuthority: false, policyMutation: false,
      },
      budgets: budgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    const entries = [{
      id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
      bytes: bytes.length, classification: "internal", mountMode: "read-only",
    }];
    const compiled = compileBuilder(request, policy, entries, { now, suffix });
    prepared = await prepareBuilderRun({
      ...compiled, policy, objectStore, workspaceRoot, executorPath,
      archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 }, now,
    });
    const claim = createLeaseConsumption(prepared, { now, suffix });
    await claimLease(stateRoot, claim);
    const result = await runBuilderDockerLifecycle(prepared, claim, {
      dockerPath, stateRoot, ...pickSubnet(dockerPath), backendNetworkName, backendContainerName,
      imageIdentifier: runnerImageId, backendImageIdentifier: backendImageId,
      uid: process.getuid(), gid: process.getgid(),
    });
    const patchText = await readFile(result.patch.path, "utf8");
    const patch = JSON.parse(patchText);
    assert.match(result.text, /PIXEL_BUILDER_E2E_GREEN/, JSON.stringify({
      text: result.text,
      observedTools: result.observedTools,
      changes: patch.changes.map((change) => ({
        path: change.path, operation: change.operation,
        content: change.contentBase64 === null ? null : Buffer.from(change.contentBase64, "base64").toString(),
      })),
    }));
    assert.deepEqual(result.observedTools, ["bash", "debug", "lsp", "read", "write"]);
    assert.equal(result.proxyReceipt.modelRequests, 8);
    assert.equal(patch.changes.length, 2, JSON.stringify(patch.changes.map((change) => ({ path: change.path, operation: change.operation }))));
    const changedInvariant = patch.changes.find((change) => change.path === "source/src/main.js");
    const debugProof = patch.changes.find((change) => change.path === "source/debug-proof.txt");
    assert.equal(changedInvariant?.operation, "modify");
    assert.match(Buffer.from(changedInvariant?.contentBase64 ?? "", "base64").toString(), /INVARIANT=43/);
    assert.equal(debugProof?.operation, "add");
    assert.equal(Buffer.from(debugProof?.contentBase64 ?? "", "base64").toString(), "43\n");
    assert.doesNotMatch(patchText, /PIXEL_HOSTILE_CONTROL_CANARY/);
    const evidenceText = await readFile(result.evidence.path, "utf8");
    const evidence = JSON.parse(evidenceText);
    assert.equal(evidence.verification.status, "pending");
    assert.match(Buffer.from(evidence.execution.reportBase64, "base64").toString(), /PIXEL_BUILDER_E2E_GREEN/);
    assert.doesNotMatch(evidenceText, /PIXEL_HOSTILE_CONTROL_CANARY/);
    assert.equal(result.verification.evidence.status, "pass");
    assert.deepEqual(result.verification.evidence.criteria.map((criterion) => criterion.status), ["pass", "pass", "pass"]);
    assert.deepEqual(result.verification.evidence.checks.map(({ id, kind, status }) => ({ id, kind, status })), [
      { id: "patch-boundary", kind: "patch-integrity", status: "pass" },
      { id: "changed-invariant", kind: "command", status: "pass" },
    ]);
    const verificationText = await readFile(result.verification.artifact.path, "utf8");
    assert.doesNotMatch(verificationText, /PIXEL_HOSTILE_CONTROL_CANARY|PIXEL_BUILDER_E2E_GREEN|source\/src\/main\.js contains/);
    assert.match(await readFile(join(prepared.workspace.originalPath, "source/src/main.js"), "utf8"), /INVARIANT=42/);
    assert.equal(docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["network", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    assert.equal(docker(dockerPath, ["volume", "ls", "--quiet", "--filter", `label=com.osmantic.pixel.work-job=${jobId}`]), "");
    process.stdout.write(`${JSON.stringify({ status: "pass", toolCalls: result.toolCalls, modelRequests: result.proxyReceipt.modelRequests, changes: result.patch.changes, verificationChecks: result.verification.evidence.checks.length, protocolVersion: result.protocolVersion })}\n`);
  } finally {
    if (prepared) await discardPreparedRun(prepared).catch(() => {});
    if (backendStarted) try { docker(dockerPath, ["container", "rm", "--force", backendContainerName]); } catch { /* bounded test cleanup */ }
    if (backendNetworkStarted) try { docker(dockerPath, ["network", "rm", backendNetworkName]); } catch { /* bounded test cleanup */ }
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
  } : { message: "failed" };
  process.stderr.write(`pixel-work-builder-live: ${JSON.stringify(details)}\n`);
  process.exitCode = 1;
});

// LEGACY-ROUTE FIXTURE: qualifies the llama.cpp/qwen local plumbing route only.
// Pixel's product model is DeepSeek-V4-Flash-0731 (vLLM route); results from this
// file must never support product-quality or Codex-parity claims.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readFile, realpath } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import { modelCapabilityReceiptSha256 } from "../deploy/work-controller/model-qualification.mjs";
import { validateWorkModelCapabilityReceipt } from "../scripts/lib/work-contract.mjs";

const IMAGE_ID_RE = /^sha256:[a-f0-9]{64}$/u;
const SAFE_NAME_RE = /^[a-z][a-z0-9_.-]{0,127}$/u;
const EXPECTED_MODEL_SHA256 = "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c";
const EXPECTED_MODEL_BYTES = 4683073536;
const EXPECTED_BACKEND_IMAGE_ID = "sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f";
const EXPECTED_MODEL_ID = "qwen2.5-coder-7b-instruct-q4_k_m";

function outerEnvironment() {
  const value = {};
  for (const key of ["PATH", "SystemRoot", "WINDIR"]) if (process.env[key]) value[key] = process.env[key];
  return value;
}
function docker(path, args, options = {}) {
  return execFileSync(path, args, {
    encoding: "utf8", windowsHide: true, timeout: options.timeout ?? 30000,
    maxBuffer: options.maxBuffer ?? 1024 * 1024, env: outerEnvironment(), stdio: options.stdio ?? ["ignore", "pipe", "pipe"],
  }).trim();
}
async function sha256File(path) {
  const digest = createHash("sha256");
  let bytes = 0;
  for await (const chunk of createReadStream(path)) { bytes += chunk.length; digest.update(chunk); }
  return { bytes, sha256: digest.digest("hex") };
}
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactArray(value, expected) { return JSON.stringify(value) === JSON.stringify(expected); }

const dockerPath = process.env.PIXEL_WORK_DOCKER_PATH ?? "docker";
const runnerImageId = process.env.PIXEL_WORK_RUNNER_IMAGE_ID;
const backendNetworkName = process.env.PIXEL_WORK_MODEL_NETWORK;
const backendContainerName = process.env.PIXEL_WORK_MODEL_BACKEND_CONTAINER;
const artifactPath = resolve(process.env.PIXEL_WORK_MODEL_ARTIFACT ?? "");
const evidenceRoot = resolve(process.env.PIXEL_WORK_MODEL_QUALIFICATION_EVIDENCE_ROOT ?? "");
if (!IMAGE_ID_RE.test(runnerImageId ?? "") || !SAFE_NAME_RE.test(backendNetworkName ?? "") || !SAFE_NAME_RE.test(backendContainerName ?? "") || !process.env.PIXEL_WORK_MODEL_ARTIFACT || !process.env.PIXEL_WORK_MODEL_QUALIFICATION_EVIDENCE_ROOT) {
  throw new Error("exact runner, backend network/container, model artifact, and evidence root are required");
}
const evidenceInfo = await lstat(evidenceRoot).catch(() => null);
if (!evidenceInfo?.isDirectory() || evidenceInfo.isSymbolicLink() || !samePath(await realpath(evidenceRoot), evidenceRoot)) throw new Error("qualification evidence root is not a real directory");
if (process.platform !== "win32" && (evidenceInfo.mode & 0o077) !== 0) throw new Error("qualification evidence root is not owner-private");

const artifact = await sha256File(artifactPath);
assert.deepEqual(artifact, { bytes: EXPECTED_MODEL_BYTES, sha256: EXPECTED_MODEL_SHA256 });
const runnerImage = JSON.parse(docker(dockerPath, ["image", "inspect", runnerImageId]));
assert.equal(runnerImage.length, 1); assert.equal(runnerImage[0].Id, runnerImageId);
const backend = JSON.parse(docker(dockerPath, ["container", "inspect", backendContainerName]));
assert.equal(backend.length, 1);
const container = backend[0];
assert.equal(container.Name, `/${backendContainerName}`);
assert.equal(container.Image, EXPECTED_BACKEND_IMAGE_ID);
assert.equal(container.State?.Running, true);
assert.equal(container.Config?.User, "10001:10001");
assert.deepEqual(container.Config?.Entrypoint, ["/app/llama-server"]);
assert.ok(exactArray(container.HostConfig?.CapDrop, ["ALL"]));
assert.ok((container.HostConfig?.SecurityOpt ?? []).some((value) => String(value).replaceAll("=", ":").toLowerCase() === "no-new-privileges:true"));
assert.equal(container.HostConfig?.ReadonlyRootfs, true);
assert.equal(container.HostConfig?.Privileged, false);
assert.deepEqual(container.HostConfig?.PortBindings ?? {}, {});
assert.equal(container.HostConfig?.NetworkMode, backendNetworkName);
assert.equal(container.Mounts?.length, 1);
assert.equal(container.Mounts[0].Destination, "/models/model.gguf");
assert.equal(container.Mounts[0].RW, false);
assert.ok(samePath(await realpath(container.Mounts[0].Source), await realpath(artifactPath)));
const requiredCommand = ["--model", "/models/model.gguf", "--host", "0.0.0.0", "--port", "8080", "--alias", EXPECTED_MODEL_ID, "--ctx-size", "32768", "--threads", "8", "--parallel", "1", "--n-gpu-layers", "99", "--flash-attn", "on", "--jinja"];
assert.deepEqual(container.Config?.Cmd, requiredCommand);
const deviceRequests = container.HostConfig?.DeviceRequests ?? [];
assert.equal(deviceRequests.length, 1);
assert.equal(deviceRequests[0].Driver, "nvidia");
assert.equal(deviceRequests[0].Count, 1);
assert.deepEqual(deviceRequests[0].Capabilities, [["gpu"]]);

function inspectPrivateNetwork(expectedCount) {
  const inspected = JSON.parse(docker(dockerPath, ["network", "inspect", backendNetworkName]));
  assert.equal(inspected.length, 1);
  const network = inspected[0];
  assert.equal(network.Driver, "bridge"); assert.equal(network.Internal, true); assert.equal(network.Attachable, false); assert.equal(network.Ingress, false);
  const peers = Object.entries(network.Containers ?? {});
  assert.equal(peers.length, expectedCount);
  assert.ok(peers.some(([id, peer]) => id === container.Id && peer?.Name === backendContainerName));
  return network;
}
inspectPrivateNetwork(1);

const suffix = randomBytes(6).toString("hex");
const workerName = `pixel-model-qualification-${suffix}`;
const outputName = `qwen2.5-coder-7b-qualification-${suffix}.json`;
const outputPath = join(evidenceRoot, outputName);
const repoRoot = resolve(import.meta.dirname, "..");
let failure = null;
let stdout = "";
try {
  stdout = docker(dockerPath, [
    "run", "--rm", "-i", "--pull", "never", "--name", workerName,
    "--network", backendNetworkName, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
    "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m", "--cpus", "2",
    "--ulimit", "nofile=128:128", "--ipc", "none", "--cgroupns", "private", "--stop-timeout", "3", "--log-driver", "none",
    "--user", "1000:1000", "--workdir", "/opt/pixel", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
    "--env", "PIXEL_WORK_MODEL_QUALIFICATION_UPSTREAM=http://pixel-local-model:8080",
    "--env", `PIXEL_WORK_MODEL_QUALIFICATION_OUTPUT=/evidence/${outputName}`,
    "--mount", `type=bind,src=${repoRoot},dst=/opt/pixel,readonly`,
    "--mount", `type=bind,src=${evidenceRoot},dst=/evidence`,
    "--entrypoint", "/opt/node/bin/node", runnerImageId, "tests/work-model-qualification-real-live.mjs",
  ], { timeout: 300000, maxBuffer: 1024 * 1024 });
} catch (error) { failure = error; }
try {
  const remaining = docker(dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `name=^/${workerName}$`]);
  if (remaining) docker(dockerPath, ["container", "rm", "--force", workerName]);
  inspectPrivateNetwork(1);
} catch (cleanupError) { if (!failure) failure = cleanupError; }
if (failure) throw failure;

const lines = stdout.split(/\r?\n/u).filter(Boolean);
assert.equal(lines.length, 1);
const summary = JSON.parse(lines[0]);
const receiptText = await readFile(outputPath, "utf8");
const receipt = JSON.parse(receiptText);
assert.deepEqual(validateWorkModelCapabilityReceipt(receipt), []);
assert.equal(receipt.status, "qualified");
assert.equal(receipt.model.modelArtifactSha256, EXPECTED_MODEL_SHA256);
assert.equal(receipt.model.backendImageDigest, EXPECTED_BACKEND_IMAGE_ID);
assert.equal(modelCapabilityReceiptSha256(receipt), summary.receiptSha256);
assert.equal(summary.casesPassed, 15); assert.equal(summary.casesFailed, 0); assert.equal(summary.exactUsage, true);
assert.deepEqual(summary.eligibleProfiles, ["assistant", "scout", "builder", "data-lab", "researcher"]);
assert.doesNotMatch(stdout + receiptText, /PIXEL_CONTEXT_SENTINEL|printf pixel|OLD_VALUE|NEW_VALUE|rows=17/u);
process.stdout.write(`${JSON.stringify({
  status: "pass", runnerImageId, backendImageId: EXPECTED_BACKEND_IMAGE_ID,
  modelArtifactSha256: EXPECTED_MODEL_SHA256, receiptSha256: summary.receiptSha256,
  receiptFile: basename(outputPath), receiptFileSha256: createHash("sha256").update(receiptText).digest("hex"),
  casesPassed: summary.casesPassed, eligibleProfiles: summary.eligibleProfiles,
  maxContextTokens: summary.maxContextTokens, maxOutputTokens: summary.maxOutputTokens, exactUsage: summary.exactUsage,
  privateNetwork: true, credentialsUsed: false, promptsExposed: false, responsesExposed: false,
})}\n`);

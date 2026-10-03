import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  probeCapabilityHealth, recoverCapabilityHealth, statusCapabilityHealth,
} from "../deploy/work-controller/capability-health.mjs";
import { admitCapabilityImage, reviewCapabilityImageRevocation } from "../deploy/work-controller/capability-image-admission.mjs";
import { installCapabilityPack, signCapabilityPack } from "../deploy/work-controller/capability-pack-installation.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";
import { WorkMcpClientError } from "../deploy/work-controller/mcp-stdio-client.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const executable = Buffer.from("#!/usr/bin/env node\nprocess.stdin.pipe(process.stdout);\n", "utf8");
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const digest = (character) => character.repeat(64);
const cli = resolve(import.meta.dirname, "../deploy/work-controller/capability-pack-cli.mjs");
const runCli = (...args) => spawnSync(process.execPath, [cli, ...args], { encoding: "utf8", windowsHide: true });

function pack() {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "health-fixture-tools", version: "1.0.0", name: "Health fixture tools", description: "Deterministic credential-free local health fixture.",
    provenance: { treeSha256: digest("a"), signerIdentity: "health-fixture-publisher", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/health-fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("c")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: sha(executable), executableBytes: executable.length, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "health-fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public"], returnedClassifications: ["public"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  };
}

function tar(payload) {
  const header = Buffer.alloc(512), field = (offset, size, value) => header.write(value, offset, Math.min(size, Buffer.byteLength(value)), "ascii");
  field(0, 100, "server"); field(100, 8, "0000755\0"); field(108, 8, "0000000\0"); field(116, 8, "0000000\0");
  field(124, 12, `${payload.length.toString(8).padStart(11, "0")}\0`); field(136, 12, "00000000000\0"); header.fill(32, 148, 156); header[156] = 48;
  field(257, 6, "ustar\0"); field(263, 2, "00"); let checksum = 0; for (const byte of header) checksum += byte;
  field(148, 8, `${checksum.toString(8).padStart(6, "0")}\0 `);
  return Buffer.concat([header, payload, Buffer.alloc(Math.ceil(payload.length / 512) * 512 - payload.length), Buffer.alloc(1024)]);
}

function admissionDocker(declaration) {
  return async (command) => {
    const args = command.args;
    if (args.includes("image") && args.includes("inspect")) return { status: 0, stdout: Buffer.from(JSON.stringify([{ Id: declaration.adapter.imageId, RepoDigests: [declaration.adapter.imageRef], Os: "linux", Architecture: "amd64", Config: { Env: ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"], Volumes: {}, OnBuild: [], Labels: { "org.osmantic.pixel.capability-tree": declaration.provenance.treeSha256, "org.osmantic.pixel.capability-executable": declaration.adapter.executableSha256, "org.osmantic.pixel.mcp-version": "2026-07-28" } } }])), stderr: Buffer.alloc(0) };
    if (args.includes("create")) return { status: 0, stdout: Buffer.from(`${digest("d")}\n`), stderr: Buffer.alloc(0) };
    if (args.includes("cp")) return { status: 0, stdout: tar(executable), stderr: Buffer.alloc(0) };
    if (args.includes("rm")) return { status: 0, stdout: Buffer.from("removed\n"), stderr: Buffer.alloc(0) };
    if (args.includes("inspect")) return { status: 1, stdout: Buffer.from("\n"), stderr: Buffer.from("missing") };
    if (args.includes("ls")) return { status: 0, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) };
    throw new Error("unexpected admission Docker command");
  };
}

function cleanupDocker({ failList = false } = {}) {
  const calls = [];
  const runDocker = async (command) => {
    calls.push(command);
    if (command.args.includes("rm")) return { status: 0, stdout: Buffer.from("removed\n"), stderr: Buffer.alloc(0) };
    if (command.args.includes("inspect")) return { status: 1, stdout: Buffer.from("\n"), stderr: Buffer.from("missing") };
    if (command.args.includes("ls")) return failList ? { status: 0, stdout: Buffer.from(digest("e")), stderr: Buffer.alloc(0) } : { status: 0, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) };
    throw new Error("unexpected health cleanup command");
  };
  return { calls, runDocker };
}

function passingEvidence() {
  return { protocolVersion: "2026-07-28", toolSurfaceSha256: digest("e"), instructionsSha256: null, frames: 2, stdoutBytes: 512, stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)), externalEffects: false, authority: "none" };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-health-")); t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures"), dockerConfigPath = join(root, "empty-docker");
  for (const path of [stateRoot, sourceRoot, signatureRoot, dockerConfigPath]) await mkdir(path, { mode: 0o700 });
  const declaration = pack(), packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  await writeFile(packPath, `${JSON.stringify(declaration, null, 2)}\n`, { mode: 0o600 });
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr); if (process.platform !== "win32") await chmod(keyPath, 0o600);
  await writeFile(allowedSignersPath, `health-fixture-publisher ${(await readFile(`${keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "health-fixture-publisher", sshKeygenPath: sshKeygen });
  await installCapabilityPack({ stateRoot, packPath, signaturePath, allowedSignersPath, identity: "health-fixture-publisher", sshKeygenPath: sshKeygen });
  await admitCapabilityImage({ stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen, dockerConfigPath, dependencies: { runDocker: admissionDocker(declaration) } });
  return { root, stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen, dockerConfigPath, declaration };
}

function options(value, sequence = 0) {
  const start = new Date(Date.parse("2026-08-11T16:00:00Z") + sequence * 2000);
  return { stateRoot: value.stateRoot, id: value.id, version: value.version, allowedSignersPath: value.allowedSignersPath, sshKeygenPath: value.sshKeygenPath, dockerConfigPath: value.dockerConfigPath, now: start, completedAt: new Date(start.getTime() + 1000) };
}

test("confirmed health probe records only content-free discovery evidence and stays disabled", async (t) => {
  const value = await fixture(t); if (!value) return;
  const before = await statusCapabilityHealth(options(value)); assert.equal(before.status, "not-probed");
  assert.equal(await readdir(value.stateRoot).then((names) => names.includes("capability-pack-health")), false);
  const unconfirmed = runCli("health-probe", "--state-root", value.stateRoot, "--pack-id", value.id, "--version", value.version, "--allowed-signers", value.allowedSignersPath, "--docker-config", value.dockerConfigPath);
  assert.notEqual(unconfirmed.status, 0); assert.match(unconfirmed.stderr, /confirmation/);
  let launch;
  const docker = cleanupDocker(), result = await probeCapabilityHealth({ ...options(value), dependencies: { runDocker: docker.runDocker, probeMcp: async (descriptor) => { launch = descriptor; return passingEvidence(); } } });
  assert.equal(result.status, "passing-disabled"); assert.equal(result.health.consecutiveFailures, 0); assert.equal(result.enabled, false); assert.equal(result.authority.grantsExecution, false);
  assert.ok(launch.args.includes("--network")); assert.ok(launch.args.includes("none")); assert.ok(launch.args.includes("--pull")); assert.ok(launch.args.includes("never")); assert.equal(launch.args.includes("--mount"), false);
  assert.equal(docker.calls.at(-1).args.includes("ls"), true);
  const status = await statusCapabilityHealth(options(value)); assert.equal(status.status, "passing-disabled"); assert.equal(status.attempts, 1); assert.equal(status.cleanupRequired, 0);
  const cliStatus = runCli("health-status", "--state-root", value.stateRoot, "--pack-id", value.id, "--version", value.version, "--allowed-signers", value.allowedSignersPath);
  assert.equal(cliStatus.status, 0, cliStatus.stderr); assert.equal(JSON.parse(cliStatus.stdout).status, "passing-disabled"); assert.doesNotMatch(cliStatus.stdout, new RegExp(value.root.replaceAll("\\", "\\\\"), "u"));
  const healthRoot = join(value.stateRoot, "capability-pack-health", value.id), [versionRoot] = await readdir(healthRoot), [name] = await readdir(join(healthRoot, versionRoot, "attempts"));
  const serialized = await readFile(join(healthRoot, versionRoot, "attempts", name), "utf8");
  assert.doesNotMatch(serialized, /text|arguments|result content|private/i); assert.match(name, /^000001-[a-f0-9]{64}\.json$/u);
});

test("three consecutive failed probes quarantine one signed version without enabling anything", async (t) => {
  const value = await fixture(t); if (!value) return;
  for (let index = 0; index < 3; index += 1) {
    const result = await probeCapabilityHealth({ ...options(value, index), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => { throw new WorkMcpClientError("MCP server identity differs from signed pack PRIVATE-VALUE"); } } });
    assert.equal(result.status, index === 2 ? "quarantined-disabled" : "failing-disabled"); assert.equal(result.health.consecutiveFailures, index + 1);
  }
  await assert.rejects(() => probeCapabilityHealth({ ...options(value, 4), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => passingEvidence() } }), /quarantined/);
  const status = await statusCapabilityHealth(options(value)); assert.equal(status.attempts, 3); assert.equal(status.health.status, "quarantined"); assert.equal(status.enabled, false);
  const healthRoot = join(value.stateRoot, "capability-pack-health", value.id), [versionRoot] = await readdir(healthRoot), names = (await readdir(join(healthRoot, versionRoot, "attempts"))).sort();
  assert.equal(names.length, 3);
  const combined = (await Promise.all(names.map((name) => readFile(join(healthRoot, versionRoot, "attempts", name), "utf8")))).join("\n");
  assert.doesNotMatch(combined, /PRIVATE-VALUE/); assert.match(combined, /identity-or-schema/);
});

test("unproven cleanup blocks revocation and exact recovery records an uncertain failure", async (t) => {
  const value = await fixture(t); if (!value) return;
  await assert.rejects(() => probeCapabilityHealth({ ...options(value), dependencies: { runDocker: cleanupDocker({ failList: true }).runDocker, probeMcp: async () => passingEvidence() } }), /absence was not proven/);
  const held = await statusCapabilityHealth(options(value)); assert.equal(held.status, "cleanup-required"); assert.equal(held.cleanupRequired, 1);
  await assert.rejects(() => reviewCapabilityImageRevocation(options(value)), /in-progress health-probe mutation/);
  const recovered = await recoverCapabilityHealth({ ...options(value, 1), dependencies: { runDocker: cleanupDocker().runDocker } });
  assert.equal(recovered.status, "failing-disabled"); assert.equal(recovered.health.consecutiveFailures, 1); assert.equal(recovered.cleanupRequired, 0);
  assert.equal((await statusCapabilityHealth(options(value))).status, "failing-disabled");
});

test("post-cleanup, staged-receipt, and post-receipt crashes recover without replay or duplicate evidence", async (t) => {
  for (const phase of ["afterCleanup", "afterAttemptStage", "afterReceipt"]) {
    const value = await fixture(t); if (!value) return;
    await assert.rejects(() => probeCapabilityHealth({ ...options(value), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => passingEvidence(), [phase]: () => { throw new Error(`simulated ${phase} crash`); } } }), new RegExp(phase, "u"));
    const held = await statusCapabilityHealth(options(value)); assert.equal(held.status, "cleanup-required");
    const recovered = await recoverCapabilityHealth({ ...options(value, 1), dependencies: { runDocker: cleanupDocker().runDocker } });
    assert.equal(recovered.attempts, 1); assert.equal(recovered.status, phase === "afterCleanup" ? "failing-disabled" : "passing-disabled");
    assert.equal((await statusCapabilityHealth(options(value))).cleanupRequired, 0);
  }
});

test("concurrent health probes have one mutation winner", async (t) => {
  const value = await fixture(t); if (!value) return;
  let enteredResolve, continueResolve; const entered = new Promise((resolve) => { enteredResolve = resolve; }), continuation = new Promise((resolve) => { continueResolve = resolve; });
  const first = probeCapabilityHealth({ ...options(value), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => { enteredResolve(); await continuation; return passingEvidence(); } } });
  await entered;
  await assert.rejects(() => probeCapabilityHealth({ ...options(value, 1), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => passingEvidence() } }), /in-progress mutation/);
  continueResolve(); await first;
  assert.equal((await statusCapabilityHealth(options(value))).attempts, 1);
});

test("tampered health evidence and cleanup custody fail closed", async (t) => {
  const evidenced = await fixture(t); if (!evidenced) return;
  await probeCapabilityHealth({ ...options(evidenced), dependencies: { runDocker: cleanupDocker().runDocker, probeMcp: async () => passingEvidence() } });
  const healthRoot = join(evidenced.stateRoot, "capability-pack-health", evidenced.id), [versionRoot] = await readdir(healthRoot), attempts = join(healthRoot, versionRoot, "attempts"), [attemptName] = await readdir(attempts), attemptPath = join(attempts, attemptName);
  const receipt = JSON.parse(await readFile(attemptPath, "utf8")); receipt.result.dataBytes = 1; await writeFile(attemptPath, `${JSON.stringify(receipt)}\n`, { mode: 0o600 });
  await assert.rejects(() => statusCapabilityHealth(options(evidenced)), /evidence is invalid|widens authority/);

  const cleanupHeld = await fixture(t); if (!cleanupHeld) return;
  await assert.rejects(() => probeCapabilityHealth({ ...options(cleanupHeld), dependencies: { runDocker: cleanupDocker({ failList: true }).runDocker, probeMcp: async () => passingEvidence() } }), /absence was not proven/);
  const cleanupBase = join(cleanupHeld.stateRoot, "capability-pack-health-cleanups", cleanupHeld.id), [cleanupVersion] = await readdir(cleanupBase), cleanupRoot = join(cleanupBase, cleanupVersion), [intentName] = await readdir(cleanupRoot), intentPath = join(cleanupRoot, intentName);
  const intent = JSON.parse(await readFile(intentPath, "utf8")); intent.operationToken = digest("f"); await writeFile(intentPath, `${JSON.stringify(intent)}\n`, { mode: 0o600 });
  await assert.rejects(() => recoverCapabilityHealth({ ...options(cleanupHeld, 1), dependencies: { runDocker: cleanupDocker().runDocker } }), /cleanup intent is invalid/);
});

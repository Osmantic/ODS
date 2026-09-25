import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { probeCapabilityHealth } from "../deploy/work-controller/capability-health.mjs";
import { admitCapabilityImage, reviewCapabilityImageRevocation } from "../deploy/work-controller/capability-image-admission.mjs";
import { installCapabilityPack, signCapabilityPack } from "../deploy/work-controller/capability-pack-installation.mjs";
import {
  executeCapabilityTool, recoverCapabilityRuntime, statusCapabilityRuntime,
} from "../deploy/work-controller/capability-runtime.mjs";
import {
  beginCapabilityExecution, capabilityPackSha256, issueCapabilityGrant,
} from "../deploy/work-controller/capability-packs.mjs";
import { assessWatchdog } from "../deploy/work-controller/watchdog.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const executable = Buffer.from("#!/usr/bin/env node\nprocess.stdin.pipe(process.stdout);\n", "utf8");
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-11T18:00:00Z");

function pack(workspace = false) {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: workspace ? "runtime-workspace-tools" : "runtime-fixture-tools", version: "1.0.0", name: "Runtime fixture tools", description: "Deterministic credential-free local runtime fixture.",
    provenance: { treeSha256: digest("a"), signerIdentity: "runtime-fixture-publisher", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/runtime-fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("c")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: sha(executable), executableBytes: executable.length, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "runtime-fixture-mcp", serverVersion: "1.0.0", workspace: workspace ? "disposable-read-write" : "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: workspace ? "workspace" : "read-only" }],
    data: { acceptedClassifications: ["public"], returnedClassifications: ["public"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 5000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: workspace ? 1048576 : 0 },
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
    if (args.includes("inspect")) return { status: 1, stdout: Buffer.from(args.includes("--format") ? "" : "[]\n"), stderr: Buffer.from("missing") };
    if (args.includes("ls")) return { status: 0, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) };
    throw new Error("unexpected admission Docker command");
  };
}

function healthDocker() {
  return async (command) => {
    if (command.args.includes("rm")) return { status: 0, stdout: Buffer.from("removed\n"), stderr: Buffer.alloc(0) };
    if (command.args.includes("inspect")) return { status: 1, stdout: Buffer.alloc(0), stderr: Buffer.from("missing") };
    if (command.args.includes("ls")) return { status: 0, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) };
    throw new Error("unexpected health Docker command");
  };
}

function runtimeDocker({ declaration, workspace = false, foreignContainer = false, failList = false } = {}) {
  const calls = []; let volumePresent = false;
  const runDocker = async (command) => {
    calls.push(command); const args = command.args, isVolume = args.includes("volume"), isContainer = args.includes("container"), name = args.at(-1);
    if (isVolume && args.includes("create")) { volumePresent = true; return { status: 0, stdout: Buffer.from(`${name}\n`), stderr: Buffer.alloc(0) }; }
    if (isVolume && args.includes("inspect")) {
      if (!volumePresent) return { status: 1, stdout: Buffer.from("[]\n"), stderr: Buffer.from("missing") };
      const option = "nosuid,nodev,noexec,size=1048576,mode=0700,uid=1000,gid=1000";
      return { status: 0, stdout: Buffer.from(JSON.stringify([{ Name: name, Driver: "local", Scope: "local", Labels: { "com.osmantic.pixel.work-capability-claim": runtimeDocker.claimId, "com.osmantic.pixel.work-job": runtimeDocker.jobId }, Options: { type: "tmpfs", device: "tmpfs", o: option } }])), stderr: Buffer.alloc(0) };
    }
    if (isVolume && args.includes("rm")) { volumePresent = false; return { status: 0, stdout: Buffer.from(`${name}\n`), stderr: Buffer.alloc(0) }; }
    if (isContainer && args.includes("inspect") && !args.includes("--format") && foreignContainer) return { status: 0, stdout: Buffer.from(JSON.stringify([{ Name: `/${name}`, Image: declaration.adapter.imageId, Config: { Image: declaration.adapter.imageRef, Labels: {} }, HostConfig: { NetworkMode: "none", Privileged: false, ReadonlyRootfs: true } }])), stderr: Buffer.alloc(0) };
    if (args.includes("inspect")) return { status: 1, stdout: Buffer.from(args.includes("--format") ? "" : "[]\n"), stderr: Buffer.from("missing") };
    if (args.includes("ls")) return { status: 0, stdout: failList ? Buffer.from("unexpected") : Buffer.alloc(0), stderr: Buffer.alloc(0) };
    if (args.includes("rm")) return { status: 0, stdout: Buffer.from("removed\n"), stderr: Buffer.alloc(0) };
    throw new Error(`unexpected runtime Docker command (${workspace})`);
  };
  return { calls, runDocker };
}

function healthEvidence() { return { protocolVersion: "2026-07-28", toolSurfaceSha256: digest("e"), instructionsSha256: null, frames: 2, stdoutBytes: 256, stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)), externalEffects: false, authority: "none" }; }
function toolEvidence() { return { structuredContent: { length: 12 }, contentSha256: digest("f"), resultSha256: digest("1"), protocolVersion: "2026-07-28", toolSurfaceSha256: digest("e"), instructionsSha256: null, frames: 3, stdoutBytes: 384, stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)), externalEffects: false, authority: "none", executionClaimSha256: digest("2"), dataClassification: "public" }; }

async function fixture(t, { workspace = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-runtime-")); t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures"), dockerConfigPath = join(root, "empty-docker");
  for (const path of [stateRoot, sourceRoot, signatureRoot, dockerConfigPath]) await mkdir(path, { mode: 0o700 });
  const declaration = pack(workspace), packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  await writeFile(packPath, `${JSON.stringify(declaration, null, 2)}\n`, { mode: 0o600 });
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr); if (process.platform !== "win32") await chmod(keyPath, 0o600);
  await writeFile(allowedSignersPath, `runtime-fixture-publisher ${(await readFile(`${keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "runtime-fixture-publisher", sshKeygenPath: sshKeygen });
  await installCapabilityPack({ stateRoot, packPath, signaturePath, allowedSignersPath, identity: "runtime-fixture-publisher", sshKeygenPath: sshKeygen });
  await admitCapabilityImage({ stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen, dockerConfigPath, dependencies: { runDocker: admissionDocker(declaration) } });
  await probeCapabilityHealth({ stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen, dockerConfigPath, now: new Date(baseTime), completedAt: new Date(baseTime + 1000), dependencies: { runDocker: healthDocker(), probeMcp: async () => healthEvidence() } });
  return { root, stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen, dockerConfigPath, declaration, workspace };
}

function request(value, offset = 0) {
  const input = { text: "PIXEL_PRIVATE_RUNTIME_CANARY" }, issued = new Date(baseTime + 2000 + offset), checkpointSha256 = digest("9"), packSha256 = capabilityPackSha256(value.declaration);
  const grant = issueCapabilityGrant(value.declaration, { expectedPackSha256: packSha256, jobId: "work-1786460000000-abcdef123456", checkpointSha256, tools: ["analyze"], dataClassification: "public", limits: { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 3000, maxCalls: 1, maxMemoryMiB: 96, maxCpuCores: 0.5, maxPids: 24, maxWorkspaceBytes: value.workspace ? 1048576 : 0 }, now: issued, expiresAt: new Date(issued.getTime() + 30000), suffix: offset ? "abcdef123457" : "abcdef123456" });
  const preflightDecision = assessWatchdog({ jobId: grant.jobId, checkpointSha256, expectedEventHeadSha256: null, events: [], proposal: { tool: `mcp.${value.declaration.id}.analyze`, arguments: input, effectClass: value.workspace ? "workspace" : "read-only" }, limits: { maxToolCalls: 10, maxFailures: 2, maxRepeatedEquivalent: 3, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 }, allowedEffectClasses: [value.workspace ? "workspace" : "read-only"], now: new Date(issued.getTime() + 1), suffix: offset ? "000000000012" : "000000000011" });
  let tick = issued.getTime() + 2; const clock = () => new Date(tick++);
  return { grant, toolName: "analyze", input, preflightDecision, maxHealthAgeMs: 60000, claimSuffix: offset ? "000000000022" : "000000000021", clock };
}

function execution(value, runtime, overrides = {}) {
  return { stateRoot: value.stateRoot, id: value.id, version: value.version, allowedSignersPath: value.allowedSignersPath, sshKeygenPath: value.sshKeygenPath, dockerConfigPath: value.dockerConfigPath, ...request(value, overrides.offset ?? 0), dependencies: { runDocker: runtime.runDocker, runMcp: async (options) => { runtimeDocker.claimId = options.claim.claimId; runtimeDocker.jobId = options.claim.jobId; await beginCapabilityExecution(options); return toolEvidence(); }, ...(overrides.dependencies ?? {}) } };
}

test("one healthy single-use capability call returns transient structured output and retains only content-free terminal evidence", async (t) => {
  const value = await fixture(t); if (!value) return; const runtime = runtimeDocker({ declaration: value.declaration });
  const options = execution(value, runtime), result = await executeCapabilityTool(options);
  assert.equal(result.status, "succeeded-disabled"); assert.deepEqual(result.structuredContent, { length: 12 }); assert.equal(result.enabled, false); assert.equal(result.authority.grantsCompletion, false);
  const status = await statusCapabilityRuntime(value); assert.equal(status.status, "disabled"); assert.equal(status.receipts, 1); assert.equal(status.latest.result, "succeeded");
  const runtimeRoot = join(value.stateRoot, "capability-pack-runtime", value.id), [versionRoot] = await readdir(runtimeRoot), receipts = join(runtimeRoot, versionRoot, "receipts"), [receiptName] = await readdir(receipts), serialized = await readFile(join(receipts, receiptName), "utf8");
  assert.doesNotMatch(serialized, /PIXEL_PRIVATE_RUNTIME_CANARY|structuredContent|"length":12/u); assert.match(receiptName, /^workcapgrant-[0-9]{13}-[a-f0-9]{12}-[a-f0-9]{64}\.json$/u);
  await assert.rejects(() => executeCapabilityTool(options), /already consumed/);
});

test("tool failure is contained, sanitized, cleanup-proven, and never becomes completion authority", async (t) => {
  const value = await fixture(t); if (!value) return; const runtime = runtimeDocker({ declaration: value.declaration });
  const options = execution(value, runtime, { dependencies: { runMcp: async (mcp) => { await beginCapabilityExecution(mcp); throw new Error("MCP tool returned a bounded error PRIVATE_FAILURE_TEXT"); } } });
  const result = await executeCapabilityTool(options); assert.equal(result.status, "failed-contained"); assert.equal(Object.hasOwn(result, "structuredContent"), false); assert.equal(result.authority.grantsFutureExecution, false);
  const runtimeRoot = join(value.stateRoot, "capability-pack-runtime", value.id), [versionRoot] = await readdir(runtimeRoot), receipts = join(runtimeRoot, versionRoot, "receipts"), [name] = await readdir(receipts), serialized = await readFile(join(receipts, name), "utf8");
  assert.doesNotMatch(serialized, /PRIVATE_FAILURE_TEXT|PIXEL_PRIVATE_RUNTIME_CANARY/u); assert.match(serialized, /tool-failure/u);
});

test("a crash after claim recovers by exact cleanup and records uncertainty without replay", async (t) => {
  const value = await fixture(t); if (!value) return; const runtime = runtimeDocker({ declaration: value.declaration }), options = execution(value, runtime, { dependencies: { afterClaim: () => { throw new Error("simulated post-claim crash"); } } });
  await assert.rejects(() => executeCapabilityTool(options), /post-claim crash/);
  assert.equal((await statusCapabilityRuntime(value)).status, "cleanup-required");
  await assert.rejects(() => reviewCapabilityImageRevocation(value), /tool-execution mutation/);
  const recovered = await recoverCapabilityRuntime({ ...value, clock: () => new Date(baseTime + 10000), dependencies: { runDocker: runtime.runDocker } });
  assert.equal(recovered.status, "uncertain-recovered-contained"); assert.equal(Object.hasOwn(recovered, "structuredContent"), false);
  assert.equal((await statusCapabilityRuntime(value)).cleanupRequired, 0);
});

test("receipt-stage and post-receipt crashes finalize once without replaying the tool", async (t) => {
  for (const phase of ["afterReceiptStage", "afterReceipt"]) {
    const value = await fixture(t); if (!value) return; const runtime = runtimeDocker({ declaration: value.declaration }); let calls = 0;
    const options = execution(value, runtime, { dependencies: { runMcp: async (mcp) => { calls += 1; await beginCapabilityExecution(mcp); return toolEvidence(); }, [phase]: () => { throw new Error(`simulated ${phase}`); } } });
    await assert.rejects(() => executeCapabilityTool(options), new RegExp(phase, "u"));
    const recovered = await recoverCapabilityRuntime({ ...value, clock: () => new Date(baseTime + 10000), dependencies: { runDocker: runtime.runDocker } });
    assert.equal(recovered.status, "succeeded-disabled"); assert.equal(Object.hasOwn(recovered, "structuredContent"), false); assert.equal(calls, 1);
    assert.equal((await statusCapabilityRuntime(value)).receipts, 1);
  }
});

test("disposable workspace ownership is verified and a foreign container blocks destructive cleanup", async (t) => {
  const workspaceValue = await fixture(t, { workspace: true }); if (!workspaceValue) return; const workspaceRuntime = runtimeDocker({ declaration: workspaceValue.declaration, workspace: true });
  const success = await executeCapabilityTool(execution(workspaceValue, workspaceRuntime)); assert.equal(success.status, "succeeded-disabled");
  assert.ok(workspaceRuntime.calls.some((command) => command.args.includes("volume") && command.args.includes("create"))); assert.ok(workspaceRuntime.calls.some((command) => command.args.includes("volume") && command.args.includes("rm")));

  const foreignValue = await fixture(t); if (!foreignValue) return; const foreignRuntime = runtimeDocker({ declaration: foreignValue.declaration, foreignContainer: true });
  await assert.rejects(() => executeCapabilityTool(execution(foreignValue, foreignRuntime)), /outside exact claim custody/);
  assert.equal((await statusCapabilityRuntime(foreignValue)).status, "cleanup-required");
});

test("stale health and concurrent execution fail before any second grant can run", async (t) => {
  const stale = await fixture(t); if (!stale) return; const staleRuntime = runtimeDocker({ declaration: stale.declaration }), staleRequest = request(stale); staleRequest.maxHealthAgeMs = 1000; staleRequest.clock = () => new Date(baseTime + 5000);
  await assert.rejects(() => executeCapabilityTool({ ...stale, ...staleRequest, dependencies: { runDocker: staleRuntime.runDocker, runMcp: async () => toolEvidence() } }), /stale/);
  assert.equal((await statusCapabilityRuntime(stale)).receipts, 0);

  const value = await fixture(t); if (!value) return; const runtime = runtimeDocker({ declaration: value.declaration }); let enteredResolve, continueResolve; const entered = new Promise((resolve) => { enteredResolve = resolve; }), continuation = new Promise((resolve) => { continueResolve = resolve; });
  const first = executeCapabilityTool(execution(value, runtime, { dependencies: { runMcp: async (mcp) => { await beginCapabilityExecution(mcp); enteredResolve(); await continuation; return toolEvidence(); } } }));
  await entered;
  const secondRequest = request(value, 1);
  await assert.rejects(() => executeCapabilityTool({ ...value, ...secondRequest, dependencies: { runDocker: runtime.runDocker, runMcp: async () => toolEvidence() } }), /in-progress mutation/);
  continueResolve(); await first; assert.equal((await statusCapabilityRuntime(value)).receipts, 1);
});

test("tampered cleanup custody and terminal evidence fail closed", async (t) => {
  const held = await fixture(t); if (!held) return; const heldRuntime = runtimeDocker({ declaration: held.declaration });
  await assert.rejects(() => executeCapabilityTool(execution(held, heldRuntime, { dependencies: { afterClaim: () => { throw new Error("hold runtime custody"); } } })), /hold runtime custody/);
  const heldRoot = join(held.stateRoot, "capability-pack-runtime", held.id), [heldVersion] = await readdir(heldRoot), cleanups = join(heldRoot, heldVersion, "cleanups"), [intentName] = await readdir(cleanups), intentPath = join(cleanups, intentName), intent = JSON.parse(await readFile(intentPath, "utf8"));
  intent.containerName = "pixel-cap-run-tampered"; await writeFile(intentPath, `${JSON.stringify(intent)}\n`, { mode: 0o600 });
  await assert.rejects(() => statusCapabilityRuntime(held), /differs from operation custody/);
  await assert.rejects(() => recoverCapabilityRuntime({ ...held, dependencies: { runDocker: heldRuntime.runDocker } }), /differs from operation custody/);

  const evidenced = await fixture(t); if (!evidenced) return; const evidencedRuntime = runtimeDocker({ declaration: evidenced.declaration });
  await executeCapabilityTool(execution(evidenced, evidencedRuntime));
  const evidencedRoot = join(evidenced.stateRoot, "capability-pack-runtime", evidenced.id), [evidencedVersion] = await readdir(evidencedRoot), receipts = join(evidencedRoot, evidencedVersion, "receipts"), [receiptName] = await readdir(receipts), receiptPath = join(receipts, receiptName), receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  receipt.result.outputBytes += 1; await writeFile(receiptPath, `${JSON.stringify(receipt)}\n`, { mode: 0o600 });
  await assert.rejects(() => statusCapabilityRuntime(evidenced), /filename is invalid/);
});

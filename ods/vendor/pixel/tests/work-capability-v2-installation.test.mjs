import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  admitCapabilityImage,
} from "../deploy/work-controller/capability-image-admission.mjs";
import {
  CapabilityPackInstallationError, inspectCapabilityPack, installCapabilityPack, signCapabilityPack,
  statusCapabilityPacks, verifyCapabilityPack,
} from "../deploy/work-controller/capability-pack-installation.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const digest = (character) => character.repeat(64);
const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string" }, secret: { type: "string" } } };
const outputSchema = { type: "object", additionalProperties: false, required: ["ok"], properties: { ok: { type: "boolean" } } };

function v2Pack() {
  const binding = {
    targetClass: "public-api",
    scope: { mode: "public", endpoints: ["https://api.example.com"], destinations: [] },
    egress: { mode: "public", destinations: [] },
    credentials: { refs: ["api-token"] },
    idempotency: { required: true, keys: ["text"] },
    approval: { required: true, mode: "operator-approval" },
    inputClassification: "internal", outputClassification: "internal",
    budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 1000 }, cumulative: { maxCalls: 10, maxBytes: 8192, maxDurationMs: 5000 } },
    receipt: { required: true, form: "pixel-receipt-v1" }, neverEgress: ["secret"],
  };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v2.schema.json", schemaVersion: 2,
    kind: "deep-work-capability-pack", id: "broad-tools", version: "1.0.0", name: "Broad tools", description: "v2 admission-only broad tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "fixture-publisher", signatureNamespace: "pixel-work-capability-pack", admissionOnly: true },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/broad-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/broad/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "broad-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze", description: "Declare a public API call.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "external", binding }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { networkMode: "public", networkDestinations: ["https://api.example.com"], credentialRefs: ["api-token"], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, admissionOnly: true },
    boundary: "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.",
  };
}

function v1Pack() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools", description: "Deterministic credential-free local fixture tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "fixture-publisher", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  };
}

async function fixture(t, declaration) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-v2-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures"), dockerConfigPath = join(root, "empty-docker");
  for (const path of [stateRoot, sourceRoot, signatureRoot, dockerConfigPath]) await mkdir(path, { mode: 0o700 });
  const packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  await writeFile(packPath, `${JSON.stringify(declaration, null, 2)}\n`, { mode: 0o600 });
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr);
  if (process.platform !== "win32") await chmod(keyPath, 0o600);
  await writeFile(allowedSignersPath, `fixture-publisher ${(await readFile(`${keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  return { root, stateRoot, packPath, signaturePath, allowedSignersPath, dockerConfigPath, declaration, sshKeygenPath: sshKeygen };
}

test("v2 pack inspection, verification, and installation remain installed-disabled with all-false authority", async (t) => {
  const value = await fixture(t, v2Pack());
  const inspected = await inspectCapabilityPack(value.packPath);
  assert.equal(inspected.schemaVersion, 2);
  assert.equal(inspected.status, "valid-untrusted-declaration");
  assert.equal(inspected.enabled, false);
  const verified = await verifyCapabilityPack({ packPath: value.packPath, signaturePath: value.signaturePath, allowedSignersPath: value.allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: value.sshKeygenPath });
  assert.equal(verified.schemaVersion, 2);
  assert.equal(verified.status, "verified-disabled");
  assert.equal(verified.enabled, false);
  const installed = await installCapabilityPack({ stateRoot: value.stateRoot, packPath: value.packPath, signaturePath: value.signaturePath, allowedSignersPath: value.allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: value.sshKeygenPath });
  assert.equal(installed.schemaVersion, 2);
  assert.equal(installed.status, "installed-disabled");
  assert.equal(installed.enabled, false);
  assert.deepEqual(installed.authority, { grantsToolUse: false, grantsDataAccess: false, grantsImagePull: false, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
  const status = await statusCapabilityPacks({ stateRoot: value.stateRoot, allowedSignersPath: value.allowedSignersPath, sshKeygenPath: value.sshKeygenPath });
  assert.equal(status.schemaVersion, 1);
  assert.equal(status.status, "verified-disabled");
  assert.equal(status.enabled, 0);
  assert.equal(status.packs.length, 1);
  assert.equal(status.packs[0].schemaVersion, 2);
  assert.equal(status.packs[0].enabled, false);
});

test("schema-version confusion is rejected during signed pack dispatch", async (t) => {
  const value = await fixture(t, v2Pack());
  const confused = v2Pack();
  confused.schemaVersion = 3;
  confused.$schema = "https://osmantic.com/pixel/schemas/work-capability-pack-v2.schema.json";
  await writeFile(value.packPath, `${JSON.stringify(confused, null, 2)}\n`, { mode: 0o600 });
  await assert.rejects(inspectCapabilityPack(value.packPath), CapabilityPackInstallationError);
  await assert.rejects(verifyCapabilityPack({ packPath: value.packPath, signaturePath: value.signaturePath, allowedSignersPath: value.allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: value.sshKeygenPath }), CapabilityPackInstallationError);
});

test("v2 image admission is denied before any image inspection, pull, or admission side effect", async (t) => {
  const value = await fixture(t, v2Pack());
  await installCapabilityPack({ stateRoot: value.stateRoot, packPath: value.packPath, signaturePath: value.signaturePath, allowedSignersPath: value.allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: value.sshKeygenPath });
  const calls = [];
  await assert.rejects(
    admitCapabilityImage({ stateRoot: value.stateRoot, id: "broad-tools", version: "1.0.0", allowedSignersPath: value.allowedSignersPath, sshKeygenPath: value.sshKeygenPath, dockerConfigPath: value.dockerConfigPath, dependencies: { runDocker: async (command) => { calls.push(command); throw new Error("must not be invoked"); } } }),
    /v2 capability runtime is not enabled/,
  );
  assert.equal(calls.length, 0, "no docker side effect may occur before v2 runtime denial");
});

test("v1 pack installation and image admission regression behavior is preserved", async (t) => {
  const value = await fixture(t, v1Pack());
  const installed = await installCapabilityPack({ stateRoot: value.stateRoot, packPath: value.packPath, signaturePath: value.signaturePath, allowedSignersPath: value.allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: value.sshKeygenPath });
  assert.equal(installed.schemaVersion, 1);
  assert.equal(installed.status, "installed-disabled");
  const calls = [];
  const docker = { dockerPath: "/usr/bin/docker", dockerConfigPath: value.dockerConfigPath, containerName: "pixel-cap-image-test" };
  await assert.rejects(
    admitCapabilityImage({ stateRoot: value.stateRoot, id: "fixture-tools", version: "1.0.0", allowedSignersPath: value.allowedSignersPath, sshKeygenPath: value.sshKeygenPath, dockerConfigPath: value.dockerConfigPath, dependencies: { runDocker: async (command) => { calls.push(command); return { status: 0, stdout: Buffer.from(JSON.stringify([{ Id: value.declaration.adapter.imageId, RepoDigests: [value.declaration.adapter.imageRef], Os: "linux", Architecture: "amd64", Config: { Env: [], Volumes: {}, OnBuild: [], Labels: { "org.osmantic.pixel.capability-tree": value.declaration.provenance.treeSha256, "org.osmantic.pixel.capability-executable": value.declaration.adapter.executableSha256, "org.osmantic.pixel.mcp-version": "2026-07-28" } } }])), stderr: Buffer.alloc(0) }; } } }),
  );
  assert.ok(calls.length > 0, "v1 image admission must proceed to docker inspection and not be denied as v2");
});

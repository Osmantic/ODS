import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  admitCapabilityImage, buildCapabilityImageAdmissionDockerCommands,
  extractCapabilityExecutableArchive,
  recoverCapabilityImageCleanup, recoverCapabilityImageRevocation, reviewCapabilityImageRevocation,
  revokeCapabilityImageAdmission, statusCapabilityImages,
} from "../deploy/work-controller/capability-image-admission.mjs";
import {
  installCapabilityPack, removeCapabilityPack, reviewCapabilityPackRemoval, statusCapabilityPacks,
  signCapabilityPack,
} from "../deploy/work-controller/capability-pack-installation.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

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
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools", description: "Deterministic credential-free local fixture tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "fixture-publisher", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("c")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: sha(executable), executableBytes: executable.length, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  };
}

async function fixture(t, { localImage = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-image-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures"), dockerConfigPath = join(root, "empty-docker");
  for (const path of [stateRoot, sourceRoot, signatureRoot, dockerConfigPath]) await mkdir(path, { mode: 0o700 });
  const packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  const declaration = pack();
  if (localImage) { declaration.adapter.imageRef = declaration.adapter.imageId; declaration.adapter.imageDigest = declaration.adapter.imageId; }
  await writeFile(packPath, `${JSON.stringify(declaration, null, 2)}\n`, { mode: 0o600 });
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr); if (process.platform !== "win32") await chmod(keyPath, 0o600);
  await writeFile(allowedSignersPath, `fixture-publisher ${(await readFile(`${keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  await installCapabilityPack({ stateRoot, packPath, signaturePath, allowedSignersPath, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  return { root, stateRoot, packPath, signaturePath, allowedSignersPath, dockerConfigPath, declaration, sshKeygenPath: sshKeygen };
}

function image(value) {
  const localImage = value.declaration.adapter.imageRef === value.declaration.adapter.imageId;
  return { Id: value.declaration.adapter.imageId, RepoDigests: localImage ? [] : [value.declaration.adapter.imageRef], Os: "linux", Architecture: "amd64", Config: { Env: ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"], Volumes: {}, OnBuild: [], Labels: { "org.osmantic.pixel.capability-tree": value.declaration.provenance.treeSha256, "org.osmantic.pixel.capability-executable": value.declaration.adapter.executableSha256, "org.osmantic.pixel.mcp-version": "2026-07-28" } } };
}

function tar(payload, name = "server", type = 48) {
  const header = Buffer.alloc(512), field = (offset, size, value) => header.write(value, offset, Math.min(size, Buffer.byteLength(value)), "ascii");
  field(0, 100, name); field(100, 8, "0000755\0"); field(108, 8, "0000000\0"); field(116, 8, "0000000\0");
  field(124, 12, `${payload.length.toString(8).padStart(11, "0")}\0`); field(136, 12, "00000000000\0"); header.fill(32, 148, 156); header[156] = type;
  field(257, 6, "ustar\0"); field(263, 2, "00");
  let checksum = 0; for (const byte of header) checksum += byte;
  field(148, 8, `${checksum.toString(8).padStart(6, "0")}\0 `);
  return Buffer.concat([header, payload, Buffer.alloc(Math.ceil(payload.length / 512) * 512 - payload.length), Buffer.alloc(1024)]);
}

function fakeDocker(value, { executablePayload = executable, imageValue = image(value), badCreateId = false, createFailure = false, cleanupFailure = false } = {}) {
  const calls = [];
  const runDocker = async (command) => {
    calls.push(command);
    const args = command.args;
    if (args.includes("image") && args.includes("inspect")) return { status: 0, stdout: Buffer.from(JSON.stringify([imageValue])), stderr: Buffer.alloc(0) };
    if (args.includes("create")) return createFailure ? { status: 1, stdout: Buffer.alloc(0), stderr: Buffer.from("daemon response lost") } : { status: 0, stdout: Buffer.from(`${badCreateId ? "bad" : digest("d")}\n`), stderr: Buffer.alloc(0) };
    if (args.includes("cp")) return { status: 0, stdout: tar(executablePayload), stderr: Buffer.alloc(0) };
    if (args.includes("rm")) return { status: 0, stdout: Buffer.from("removed\n"), stderr: Buffer.alloc(0) };
    if (args.includes("inspect")) return cleanupFailure ? { status: 0, stdout: Buffer.from(`${digest("d")}\n`), stderr: Buffer.alloc(0) } : { status: 1, stdout: Buffer.alloc(0), stderr: Buffer.from("Error: No such container") };
    if (args.includes("ls")) return { status: 0, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) };
    throw new Error("unexpected Docker command");
  };
  return { calls, runDocker };
}

test("image admission verifies metadata and executable bytes without pulling or executing", async (t) => {
  const value = await fixture(t); if (!value) return;
  const docker = fakeDocker(value);
  const admitted = await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: docker.runDocker }, now: new Date("2026-08-11T15:00:00Z") });
  assert.equal(admitted.status, "image-admitted-disabled"); assert.equal(admitted.image.pulledByPixel, false); assert.equal(admitted.image.executed, false); assert.equal(admitted.authority.grantsHealthProbe, false);
  const allArguments = docker.calls.flatMap((item) => item.args);
  assert.ok(allArguments.includes("never")); assert.ok(allArguments.includes("none")); assert.equal(allArguments.includes("run"), false); assert.equal(allArguments.includes("start"), false); assert.equal(allArguments.includes("--pull"), true); assert.equal(allArguments.includes("always"), false);
  const create = docker.calls.find((item) => item.args.includes("create"));
  for (const item of ["--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--privileged=false", "--network", "none"]) assert.ok(create.args.includes(item), item);
  const status = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" });
  assert.equal(status.status, "admitted-disabled"); assert.equal(status.active, true); assert.equal(status.health.status, "not-probed");
  await assert.rejects(() => reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0" }), /revoke it before removal/);
});

test("image admission records an exact pull-disabled local image ID end to end", async (t) => {
  const value = await fixture(t, { localImage: true }); if (!value) return;
  const admitted = await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  assert.equal(admitted.image.digest, value.declaration.adapter.imageId);
  assert.equal(admitted.image.pulledByPixel, false);
});

test("image lifecycle commands are explicit, content-free, and exactly review-bound", async (t) => {
  const value = await fixture(t); if (!value) return;
  const unconfirmed = runCli("image-admit", "--state-root", value.stateRoot, "--pack-id", "fixture-tools", "--version", "1.0.0", "--allowed-signers", value.allowedSignersPath, "--docker-config", value.dockerConfigPath);
  assert.notEqual(unconfirmed.status, 0); assert.match(unconfirmed.stderr, /confirmation/);
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const status = runCli("image-status", "--state-root", value.stateRoot, "--pack-id", "fixture-tools", "--version", "1.0.0", "--allowed-signers", value.allowedSignersPath);
  assert.equal(status.status, 0, status.stderr); assert.equal(JSON.parse(status.stdout).status, "admitted-disabled"); assert.doesNotMatch(status.stdout, new RegExp(value.root.replaceAll("\\", "\\\\"), "u"));
  const reviewed = runCli("image-revoke-review", "--state-root", value.stateRoot, "--pack-id", "fixture-tools", "--version", "1.0.0", "--allowed-signers", value.allowedSignersPath);
  assert.equal(reviewed.status, 0, reviewed.stderr); const review = JSON.parse(reviewed.stdout); assert.equal(review.effect.deleteImage, false);
  const revoked = runCli("image-revoke", "--state-root", value.stateRoot, "--pack-id", "fixture-tools", "--version", "1.0.0", "--allowed-signers", value.allowedSignersPath, "--confirm-review-sha256", review.confirmationSha256);
  assert.equal(revoked.status, 0, revoked.stderr); assert.equal(JSON.parse(revoked.stdout).status, "image-admission-revoked");
});

test("image-admission revocation is exact, retains the unowned image, and unblocks pack removal", async (t) => {
  const value = await fixture(t); if (!value) return;
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const review = await reviewCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0" });
  await assert.rejects(() => revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: digest("e") }), /differs from the current review/);
  const revoked = await revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, now: new Date("2026-08-11T15:05:00Z") });
  assert.equal(revoked.status, "image-admission-revoked"); assert.equal(revoked.image.retainedOnHost, true); assert.equal(revoked.residue.admission, false);
  const status = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(status.status, "revoked");
  await assert.rejects(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } }), /publish a new signed version/);
  const packReview = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0" });
  const removed = await removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: packReview.confirmationSha256 }); assert.equal(removed.status, "removed-no-residue");
});

test("crash-held image revocation is visible, blocks pack removal, and recovers exactly", async (t) => {
  const value = await fixture(t); if (!value) return;
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const review = await reviewCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0" });
  await assert.rejects(() => revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, dependencies: { afterMove: () => { throw new Error("simulated image revocation crash"); } } }), /simulated image revocation crash/);
  const held = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(held.status, "recovery-required"); assert.equal(held.recoveryRequired, 1);
  await assert.rejects(() => reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0" }), /recover it before removal/);
  await assert.rejects(() => recoverCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: digest("f") }), /differs from its transaction/);
  const recovered = await recoverCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, now: new Date("2026-08-11T15:10:00Z") }); assert.equal(recovered.status, "image-admission-revoked");
  assert.equal((await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" })).status, "revoked");
});

test("a crash after durable revocation intent resumes the still-active admission", async (t) => {
  const value = await fixture(t); if (!value) return;
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const review = await reviewCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0" });
  await assert.rejects(() => revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, dependencies: { afterIntent: () => { throw new Error("simulated revocation intent crash"); } } }), /simulated revocation intent crash/);
  const held = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(held.status, "recovery-required"); assert.equal(held.active, true);
  const recovered = await recoverCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256 });
  assert.equal(recovered.status, "image-admission-revoked"); assert.equal((await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" })).status, "revoked");
});

test("metadata, executable, and create-response tampering fail closed with proven cleanup", async (t) => {
  for (const scenario of ["ambient", "label", "volume", "build-hook", "healthcheck", "repository", "config-id", "platform", "executable", "oversize", "create-id"]) {
    const value = await fixture(t); if (!value) return;
    const changedImage = image(value);
    if (scenario === "ambient") changedImage.Config.Env.push("SECRET=ambient");
    if (scenario === "label") changedImage.Config.Labels["org.osmantic.pixel.capability-tree"] = digest("e");
    if (scenario === "volume") changedImage.Config.Volumes = { "/host": {} };
    if (scenario === "build-hook") changedImage.Config.OnBuild = ["RUN unsafe"];
    if (scenario === "healthcheck") changedImage.Config.Healthcheck = { Test: ["CMD", "/bin/false"] };
    if (scenario === "repository") changedImage.RepoDigests = [`other.invalid/fixture@sha256:${digest("b")}`];
    if (scenario === "config-id") changedImage.Id = `sha256:${digest("f")}`;
    if (scenario === "platform") changedImage.Architecture = "arm64";
    const payload = scenario === "executable" ? Buffer.from("tampered") : scenario === "oversize" ? Buffer.alloc(1024 * 1024, 1) : executable;
    const docker = fakeDocker(value, { imageValue: changedImage, executablePayload: payload, badCreateId: scenario === "create-id" });
    await assert.rejects(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: docker.runDocker } }), /image identity|image labels|unsafe environment|differs from the signed|not created exactly|bounded process contract/);
    const createAttempted = docker.calls.some((item) => item.args.includes("create"));
    assert.equal(docker.calls.some((item) => item.args.includes("rm")), createAttempted);
    if (createAttempted) assert.equal(docker.calls.at(-1).args.includes("ls"), true);
    assert.equal((await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" })).status, "not-admitted");
  }
});

test("hostile executable archives fail closed without extraction", () => {
  const declaration = pack(), checksum = tar(executable); checksum[0] ^= 1;
  assert.throws(() => extractCapabilityExecutableArchive(checksum, declaration.adapter.executablePath, executable.length), /checksum/);
  assert.throws(() => extractCapabilityExecutableArchive(tar(executable, "server", 50), declaration.adapter.executablePath, executable.length), /exact regular file/);
  assert.throws(() => extractCapabilityExecutableArchive(Buffer.concat([tar(executable), Buffer.alloc(512)]), declaration.adapter.executablePath, executable.length), /archive size/);
  assert.throws(() => extractCapabilityExecutableArchive(tar(Buffer.concat([executable, Buffer.from("x")])), declaration.adapter.executablePath, executable.length), /signed byte size/);
});

test("tampered mutation custody fails closed in both status and cleanup", async (t) => {
  const value = await fixture(t); if (!value) return;
  await assert.rejects(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value, { cleanupFailure: true }).runDocker } }), /absence was not proven/);
  const operationRoot = join(value.stateRoot, "capability-pack-operations", "fixture-tools"), [name] = await readdir(operationRoot), operationPath = join(operationRoot, name), operation = JSON.parse(await readFile(operationPath, "utf8"));
  operation.token = "not-a-token"; await writeFile(operationPath, `${JSON.stringify(operation)}\n`, { mode: 0o600 });
  await assert.rejects(() => statusCapabilityPacks({ ...value }), /operation record is invalid/);
  await assert.rejects(() => recoverCapabilityImageCleanup({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } }), /operation record is invalid/);
});

test("unproven inspection-container cleanup is durable, blocks removal, and is recoverable", async (t) => {
  const value = await fixture(t); if (!value) return;
  const failed = fakeDocker(value, { cleanupFailure: true });
  await assert.rejects(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: failed.runDocker } }), /absence was not proven/);
  const held = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" });
  assert.equal(held.status, "cleanup-required"); assert.equal(held.cleanupRequired, 1); assert.equal(held.active, false);
  await assert.rejects(() => reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0" }), /cleanup-required image inspection state/);
  const cleaned = await recoverCapabilityImageCleanup({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  assert.equal(cleaned.status, "cleanup-complete"); assert.equal(cleaned.recovered, 1);
  const settled = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(settled.status, "not-admitted"); assert.equal(settled.cleanupRequired, 0);
});

test("an ambiguous failed create is always force-cleaned and absence-proven", async (t) => {
  const value = await fixture(t); if (!value) return;
  const docker = fakeDocker(value, { createFailure: true });
  await assert.rejects(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: docker.runDocker } }), /not created exactly/);
  const createIndex = docker.calls.findIndex((item) => item.args.includes("create")), removeIndex = docker.calls.findIndex((item) => item.args.includes("rm")), absentIndex = docker.calls.findIndex((item, index) => index > removeIndex && item.args.includes("inspect")), listIndex = docker.calls.findIndex((item, index) => index > absentIndex && item.args.includes("ls"));
  assert.ok(createIndex >= 0 && removeIndex > createIndex && absentIndex > removeIndex && listIndex > absentIndex);
  const status = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" });
  assert.equal(status.status, "not-admitted"); assert.equal(status.cleanupRequired, 0); assert.equal(status.mutation, null);
});

test("an admission mutation blocks pack removal while inspection is live", async (t) => {
  const value = await fixture(t); if (!value) return;
  const base = fakeDocker(value); let enteredResolve, continueResolve;
  const entered = new Promise((resolve) => { enteredResolve = resolve; }), continuation = new Promise((resolve) => { continueResolve = resolve; });
  const runDocker = async (command, maximumBytes) => { if (command.args.includes("create")) { enteredResolve(); await continuation; } return base.runDocker(command, maximumBytes); };
  const admission = admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker } });
  await entered;
  await assert.rejects(() => reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0" }), /cleanup-required image inspection state|in-progress image-admission-inspection mutation/);
  continueResolve(); await admission;
  assert.equal((await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" })).status, "admitted-disabled");
});

test("a crash after finalized image revocation releases exact custody through recovery", async (t) => {
  const value = await fixture(t); if (!value) return;
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const review = await reviewCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0" });
  await assert.rejects(() => revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, dependencies: { afterFinalize: () => { throw new Error("simulated finalized revocation crash"); } } }), /simulated finalized revocation crash/);
  const held = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(held.status, "recovery-required"); assert.equal(held.revoked, true);
  const recovered = await recoverCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256 }); assert.equal(recovered.status, "image-admission-revoked");
  const settled = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(settled.status, "revoked"); assert.equal(settled.mutation, null);
});

test("concurrent exact image revocations have one winner and retain the shared image", async (t) => {
  const value = await fixture(t); if (!value) return;
  await admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } });
  const review = await reviewCapabilityImageRevocation({ ...value, id: "fixture-tools", version: "1.0.0" });
  const attempts = await Promise.allSettled([1, 2].map(() => revokeCapabilityImageAdmission({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256 })));
  assert.equal(attempts.filter((item) => item.status === "fulfilled").length, 1); assert.equal(attempts.filter((item) => item.status === "rejected").length, 1);
  const status = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(status.status, "revoked"); assert.equal(status.mutation, null);
});

test("concurrent image admissions have one publication winner without deleting it", async (t) => {
  const value = await fixture(t); if (!value) return;
  const attempts = await Promise.allSettled([1, 2].map(() => admitCapabilityImage({ ...value, id: "fixture-tools", version: "1.0.0", dependencies: { runDocker: fakeDocker(value).runDocker } })));
  assert.equal(attempts.filter((item) => item.status === "fulfilled").length, 1); assert.equal(attempts.filter((item) => item.status === "rejected").length, 1);
  const status = await statusCapabilityImages({ ...value, id: "fixture-tools", version: "1.0.0" }); assert.equal(status.active, true); assert.equal(status.cleanupRequired, 0);
});

test("image admission command compiler binds the exact local digest and never-started extraction", () => {
  const declaration = pack(), root = process.platform === "win32" ? "C:\\private" : "/private";
  const commands = buildCapabilityImageAdmissionDockerCommands(declaration, capabilityPackSha256(declaration), { dockerPath: "/usr/bin/docker", dockerConfigPath: join(root, "docker"), containerName: "pixel-cap-image-fixture" });
  assert.deepEqual(commands.inspectImage.args.slice(-3), ["image", "inspect", declaration.adapter.imageRef]);
  assert.ok(commands.createContainer.args.includes("never")); assert.ok(commands.createContainer.args.includes(declaration.adapter.imageRef));
  assert.equal(Object.values(commands).some((command) => command.args.includes("start") || command.args.includes("run")), false);
  assert.equal(commands.copyExecutable.args.at(-1), "-");
  assert.deepEqual(extractCapabilityExecutableArchive(tar(executable), declaration.adapter.executablePath, executable.length), executable);
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  CapabilityPackInstallationError, inspectCapabilityPack, installCapabilityPack,
  recoverCapabilityPackRemoval, removeCapabilityPack, reviewCapabilityPackRemoval,
  signCapabilityPack, statusCapabilityPacks, verifyCapabilityPack,
} from "../deploy/work-controller/capability-pack-installation.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const digest = (character) => character.repeat(64);
const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const cli = resolve(import.meta.dirname, "../deploy/work-controller/capability-pack-cli.mjs");

function runCli(...args) {
  return spawnSync(process.execPath, [cli, ...args], { encoding: "utf8", windowsHide: true });
}

function pack() {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools",
    description: "Deterministic credential-free local fixture tools.",
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

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-capability-install-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(sourceRoot, { mode: 0o700 }); await mkdir(signatureRoot, { mode: 0o700 });
  const packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  await writeFile(packPath, `${JSON.stringify(pack(), null, 2)}\n`, { mode: 0o600 });
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr);
  if (process.platform !== "win32") await chmod(keyPath, 0o600);
  const publicKey = (await readFile(`${keyPath}.pub`, "utf8")).trim();
  await writeFile(allowedSignersPath, `fixture-publisher ${publicKey}\n`, { mode: 0o600 });
  const signed = await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  return { root, stateRoot, packPath, keyPath, signaturePath, allowedSignersPath, signed };
}

test("inspection is structural and cannot imply publisher trust or enablement", async (t) => {
  const value = await fixture(t); if (!value) return;
  const inspected = await inspectCapabilityPack(value.packPath);
  assert.equal(inspected.status, "valid-untrusted-declaration");
  assert.equal(inspected.trusted, false); assert.equal(inspected.installed, false); assert.equal(inspected.enabled, false);
  assert.equal(inspected.authority.grantsExecution, false); assert.equal(inspected.authority.grantsImagePull, false);
  assert.equal(Object.hasOwn(inspected, "path"), false);
});

test("trusted verification and atomic installation remain disabled and networkless", async (t) => {
  const value = await fixture(t); if (!value) return;
  const verified = await verifyCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  assert.equal(verified.status, "verified-disabled"); assert.equal(verified.trusted, true); assert.equal(verified.enabled, false);
  const installed = await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen, now: new Date("2026-08-11T12:00:00Z") });
  assert.equal(installed.status, "installed-disabled"); assert.equal(installed.image.pulled, false); assert.equal(installed.authority.grantsToolUse, false);
  const status = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.equal(status.status, "verified-disabled"); assert.equal(status.enabled, 0); assert.equal(status.packs.length, 1); assert.equal(status.packs[0].trusted, true);
  const installedRoot = join(value.stateRoot, "capability-packs", "fixture-tools", "1.0.0");
  assert.deepEqual((await readdir(installedRoot)).sort(), ["install-receipt.json", "pack.json", "pack.sig"]);
  const receipt = JSON.parse(await readFile(join(installedRoot, "install-receipt.json"), "utf8"));
  assert.deepEqual(receipt.image, { pulled: false, inspected: false, executed: false });
  assert.equal(receipt.enabled, false); assert.equal(Object.hasOwn(receipt, "path"), false);
  await assert.rejects(() => installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen }), /already installed/);
});

test("tampering, signer substitution, trust-root removal, and incomplete residue fail closed", async (t) => {
  const value = await fixture(t); if (!value) return;
  const changed = pack(); changed.description = "tampered";
  await writeFile(value.packPath, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
  await assert.rejects(() => verifyCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen }), CapabilityPackInstallationError);
  await assert.rejects(() => verifyCapabilityPack({ ...value, identity: "other-publisher", sshKeygenPath: sshKeygen }), /differs from the pack/);
  await writeFile(value.packPath, `${JSON.stringify(pack())}\n`, { mode: 0o600 });
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  await writeFile(value.allowedSignersPath, `other-publisher ${(await readFile(`${value.keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await assert.rejects(() => statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen }), /invalid or its publisher is not trusted/);
  await writeFile(value.allowedSignersPath, `fixture-publisher ${(await readFile(`${value.keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await mkdir(join(value.stateRoot, "capability-packs", "fixture-tools", ".install-crash"), { mode: 0o700 });
  await assert.rejects(() => statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen }), /unsafe or incomplete entry/);
});

test("command-line admission is content-free and requires explicit install confirmation", async (t) => {
  const value = await fixture(t); if (!value) return;
  const inspected = runCli("inspect", "--pack", value.packPath);
  assert.equal(inspected.status, 0, inspected.stderr);
  const inspection = JSON.parse(inspected.stdout);
  assert.equal(inspection.status, "valid-untrusted-declaration"); assert.equal(inspection.trusted, false);
  const unconfirmed = runCli("install", "--pack", value.packPath, "--signature", value.signaturePath, "--allowed-signers", value.allowedSignersPath, "--identity", "fixture-publisher", "--state-root", value.stateRoot);
  assert.notEqual(unconfirmed.status, 0); assert.match(unconfirmed.stderr, /confirmation/);
  const installed = runCli("install", "--pack", value.packPath, "--signature", value.signaturePath, "--allowed-signers", value.allowedSignersPath, "--identity", "fixture-publisher", "--state-root", value.stateRoot, "--confirm");
  assert.equal(installed.status, 0, installed.stderr); assert.equal(JSON.parse(installed.stdout).status, "installed-disabled");
  const status = runCli("status", "--allowed-signers", value.allowedSignersPath, "--state-root", value.stateRoot);
  assert.equal(status.status, 0, status.stderr); assert.equal(JSON.parse(status.stdout).enabled, 0);
  assert.doesNotMatch(status.stdout, new RegExp(value.root.replaceAll("\\", "\\\\"), "u"));
  const reviewed = runCli("remove-review", "--pack-id", "fixture-tools", "--version", "1.0.0", "--allowed-signers", value.allowedSignersPath, "--state-root", value.stateRoot);
  assert.equal(reviewed.status, 0, reviewed.stderr); const review = JSON.parse(reviewed.stdout);
  assert.equal(review.effect.deleteInstalledDeclaration, true); assert.equal(review.effect.deleteImage, false);
  const removed = runCli("remove", "--pack-id", "fixture-tools", "--version", "1.0.0", "--confirm-review-sha256", review.confirmationSha256, "--allowed-signers", value.allowedSignersPath, "--state-root", value.stateRoot);
  assert.equal(removed.status, 0, removed.stderr); assert.equal(JSON.parse(removed.stdout).status, "removed-no-residue");
  const empty = JSON.parse(runCli("status", "--allowed-signers", value.allowedSignersPath, "--state-root", value.stateRoot).stdout);
  assert.equal(empty.packs.length, 0); assert.deepEqual(empty.removals, { completed: 1, recoveryRequired: 0 });
});

test("exact removal is one-review, leaves a tombstone, and blocks version replay", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: digest("f"), sshKeygenPath: sshKeygen }), /differs from the current review/);
  assert.ok(await lstat(join(value.stateRoot, "capability-packs", "fixture-tools", "1.0.0")));
  const removed = await removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, now: new Date("2026-08-11T13:00:00Z") });
  assert.equal(removed.status, "removed-no-residue"); assert.equal(removed.residue.installedDeclaration, false); assert.equal(removed.residue.image, false);
  assert.equal(await lstat(join(value.stateRoot, "capability-packs", "fixture-tools", "1.0.0")).catch((error) => error.code === "ENOENT" ? null : Promise.reject(error)), null);
  const status = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.deepEqual(status.removals, { completed: 1, recoveryRequired: 0 });
  await assert.rejects(() => installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen }), /previously removed/);
});

test("a crash after atomic removal custody is visible and exactly recoverable", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, now: new Date("2026-08-11T13:00:00Z"), dependencies: { afterMove: () => { throw new Error("simulated removal crash"); } } }), /simulated removal crash/);
  const held = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.equal(held.status, "recovery-required"); assert.deepEqual(held.removals, { completed: 0, recoveryRequired: 1 }); assert.equal(held.packs.length, 0);
  await assert.rejects(() => recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: digest("e"), sshKeygenPath: sshKeygen }), /differs from its transaction/);
  const recovered = await recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, now: new Date("2026-08-11T13:05:00Z") });
  assert.equal(recovered.status, "removed-no-residue");
  const settled = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.deepEqual(settled.removals, { completed: 1, recoveryRequired: 0 });
});

test("a crash after durable removal intent resumes custody instead of claiming false deletion", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, dependencies: { afterIntent: () => { throw new Error("simulated removal intent crash"); } } }), /simulated removal intent crash/);
  const held = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen }); assert.equal(held.status, "recovery-required"); assert.equal(held.packs.length, 1);
  const recovered = await recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen });
  assert.equal(recovered.status, "removed-no-residue"); assert.equal((await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen })).packs.length, 0);
});

test("a crash after custody proof resumes deletion without losing the proof", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, dependencies: { afterCustody: () => { throw new Error("simulated custody proof crash"); } } }), /simulated custody proof crash/);
  const held = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen }); assert.equal(held.status, "recovery-required"); assert.equal(held.packs.length, 0);
  const recovered = await recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen });
  assert.equal(recovered.status, "removed-no-residue");
});

test("a crash after finalized removal only releases mutation custody", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, dependencies: { afterFinalize: () => { throw new Error("simulated finalized removal crash"); } } }), /simulated finalized removal crash/);
  const held = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen }); assert.equal(held.status, "recovery-required"); assert.equal(held.removals.completed, 1); assert.equal(held.operations.active, 1);
  const recovered = await recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen });
  assert.equal(recovered.status, "removed-no-residue"); assert.equal((await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen })).operations.active, 0);
});

test("a crash after the removal tombstone resumes without deleting or rewriting audit evidence", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  await assert.rejects(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, now: new Date("2026-08-11T13:10:00Z"), dependencies: { afterTombstone: () => { throw new Error("simulated post-tombstone crash"); } } }), /simulated post-tombstone crash/);
  const held = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.deepEqual(held.removals, { completed: 1, recoveryRequired: 1 });
  const tombstone = join(value.stateRoot, "capability-pack-removals", "completed", "fixture-tools", `1.0.0-${review.pack.packSha256}.json`);
  const before = await readFile(tombstone, "utf8");
  await recoverCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", packSha256: review.pack.packSha256, confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen, now: new Date("2026-08-11T14:00:00Z") });
  assert.equal(await readFile(tombstone, "utf8"), before);
  assert.deepEqual((await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen })).removals, { completed: 1, recoveryRequired: 0 });
});

test("concurrent exact removals have one winner and one complete audit chain", async (t) => {
  const value = await fixture(t); if (!value) return;
  await installCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen });
  const review = await reviewCapabilityPackRemoval({ ...value, id: "fixture-tools", version: "1.0.0", sshKeygenPath: sshKeygen });
  const attempts = await Promise.allSettled([1, 2].map(() => removeCapabilityPack({ ...value, id: "fixture-tools", version: "1.0.0", confirmReviewSha256: review.confirmationSha256, sshKeygenPath: sshKeygen })));
  assert.equal(attempts.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(attempts.filter((item) => item.status === "rejected").length, 1);
  const status = await statusCapabilityPacks({ ...value, sshKeygenPath: sshKeygen });
  assert.deepEqual(status.removals, { completed: 1, recoveryRequired: 0 }); assert.equal(status.packs.length, 0);
});

test("linked inputs and unsafe private permissions are rejected", async (t) => {
  const value = await fixture(t); if (!value) return;
  if (process.platform === "win32") return t.skip("symlink and Unix permission assertions are not portable to Windows");
  const linked = join(value.root, "linked-pack.json"); await symlink(value.packPath, linked);
  await assert.rejects(() => inspectCapabilityPack(linked), /descriptor-bound regular/);
  await chmod(value.allowedSignersPath, 0o666);
  await assert.rejects(() => verifyCapabilityPack({ ...value, identity: "fixture-publisher", sshKeygenPath: sshKeygen }), /must not be group\/world writable/);
  const info = await lstat(value.stateRoot); assert.equal(info.mode & 0o077, 0);
});

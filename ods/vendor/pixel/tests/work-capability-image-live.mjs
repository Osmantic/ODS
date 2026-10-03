import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  admitCapabilityImage, reviewCapabilityImageRevocation,
  revokeCapabilityImageAdmission, statusCapabilityImages,
} from "../deploy/work-controller/capability-image-admission.mjs";
import { probeCapabilityHealth, statusCapabilityHealth } from "../deploy/work-controller/capability-health.mjs";
import { executeCapabilityTool, statusCapabilityRuntime } from "../deploy/work-controller/capability-runtime.mjs";
import {
  installCapabilityPack, removeCapabilityPack, reviewCapabilityPackRemoval,
  signCapabilityPack, statusCapabilityPacks,
} from "../deploy/work-controller/capability-pack-installation.mjs";
import { capabilityPackSha256, issueCapabilityGrant } from "../deploy/work-controller/capability-packs.mjs";
import { assessWatchdog } from "../deploy/work-controller/watchdog.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const docker = "/usr/bin/docker";
const gcc = "/usr/bin/gcc";
const sshKeygen = "/usr/bin/ssh-keygen";
const tar = "/usr/bin/tar";
const fixtureRoot = resolve(import.meta.dirname, "fixtures/capability-image");
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: "utf8", windowsHide: true, shell: false, maxBuffer: 2 * 1024 * 1024, ...options });
  assert.equal(result.error, undefined, `${command} could not start`);
  return result;
}

function pack({ imageRef, imageDigest, imageId, executableSha256, executableBytes, treeSha256 }) {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "live-fixture-tools", version: "1.0.0", name: "Live fixture tools", description: "Networkless release fixture for exact local capability-image admission.",
    provenance: { treeSha256, signerIdentity: "live-fixture-publisher", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef, imageDigest, imageId, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256, executableBytes, arguments: [], uid: 1000, gid: 1000, serverName: "live-fixture-mcp", serverVersion: "1.0.0", workspace: "none" },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public"], returnedClassifications: ["public"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 5000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  };
}

test("a real local OCI image completes the disabled admission and exact revocation lifecycle", { skip: process.env.PIXEL_LIVE_DOCKER !== "1" ? "set PIXEL_LIVE_DOCKER=1 on the supported Linux Docker host" : false, timeout: 120000 }, async (t) => {
  assert.equal(process.platform, "linux", "live capability-image qualification requires Linux");
  assert.equal(run(docker, ["version"]).status, 0, `${docker} is unavailable`);
  assert.equal(run(gcc, ["--version"]).status, 0, `${gcc} is unavailable`);

  const root = await mkdtemp(join(tmpdir(), "pixel-capability-image-live-"));
  await chmod(root, 0o700);
  const suffix = randomBytes(8).toString("hex"), imageName = `local/pixel-capability-live-${suffix}`, imageTag = `${imageName}:fixture`;
  let imageId = null;
  t.after(async () => {
    if (imageId) {
      const containers = run(docker, ["container", "ls", "--all", "--quiet", "--filter", `ancestor=${imageId}`]);
      if (containers.status === 0) for (const id of containers.stdout.trim().split(/\s+/u).filter(Boolean)) run(docker, ["container", "rm", "--force", id]);
    }
    run(docker, ["image", "rm", "--force", imageTag]);
    if (imageId) { run(docker, ["image", "rm", "--force", imageId]); assert.notEqual(run(docker, ["image", "inspect", imageId]).status, 0, "the test-owned live fixture image remained after cleanup"); }
    await rm(root, { recursive: true, force: true });
  });

  const buildRoot = join(root, "build"), stateRoot = join(root, "state"), sourceRoot = join(root, "source"), signatureRoot = join(root, "signatures"), dockerConfigPath = join(root, "empty-docker");
  for (const path of [buildRoot, stateRoot, sourceRoot, signatureRoot, dockerConfigPath]) await mkdir(path, { mode: 0o700 });
  const serverSource = await readFile(join(fixtureRoot, "server.c"));
  const treeSha256 = sha(Buffer.concat([await readFile(join(fixtureRoot, "Dockerfile")), serverSource]));
  const rootfsBin = join(buildRoot, "rootfs", "opt", "fixture", "bin"), executablePath = join(rootfsBin, "server"), archivePath = join(buildRoot, "rootfs.tar");
  await mkdir(rootfsBin, { recursive: true, mode: 0o700 });
  const compiled = run(gcc, ["-static", "-Os", "-s", "-o", executablePath, join(fixtureRoot, "server.c")]);
  assert.equal(compiled.status, 0, compiled.stderr);
  await chmod(executablePath, 0o555);
  const executable = await readFile(executablePath), executableSha256 = sha(executable), executableBytes = (await stat(executablePath)).size;
  const archived = run(tar, ["--format=ustar", "--sort=name", "--mtime=@0", "--owner=1000", "--group=1000", "--numeric-owner", "-cf", archivePath, "-C", join(buildRoot, "rootfs"), "."]);
  assert.equal(archived.status, 0, archived.stderr);
  const imported = run(docker, [
    "image", "import", "--platform", "linux/amd64",
    "--change", `LABEL org.osmantic.pixel.capability-tree=${treeSha256}`,
    "--change", `LABEL org.osmantic.pixel.capability-executable=${executableSha256}`,
    "--change", "LABEL org.osmantic.pixel.mcp-version=2026-07-28",
    "--change", "ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "--change", "USER 1000:1000", "--change", 'ENTRYPOINT ["/opt/fixture/bin/server"]', archivePath, imageTag,
  ]);
  assert.equal(imported.status, 0, imported.stderr);

  const inspected = run(docker, ["image", "inspect", imageTag]);
  assert.equal(inspected.status, 0, inspected.stderr);
  const images = JSON.parse(inspected.stdout);
  assert.equal(images.length, 1);
  imageId = images[0].Id;
  assert.match(imageId, /^sha256:[a-f0-9]{64}$/u);
  const imageRef = imageId, imageDigest = imageId;

  const declaration = pack({ imageRef, imageDigest, imageId, executableSha256, executableBytes, treeSha256 });
  const packPath = join(sourceRoot, "pack.json"), keyPath = join(sourceRoot, "publisher"), signaturePath = join(signatureRoot, "pack.sig"), allowedSignersPath = join(sourceRoot, "allowed_signers");
  await writeFile(packPath, `${JSON.stringify(declaration, null, 2)}\n`, { mode: 0o600 });
  const generated = run(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", keyPath]);
  assert.equal(generated.status, 0, generated.stderr);
  await chmod(keyPath, 0o600);
  await writeFile(allowedSignersPath, `live-fixture-publisher ${(await readFile(`${keyPath}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await signCapabilityPack({ packPath, signingKeyPath: keyPath, signatureOutputPath: signaturePath, identity: "live-fixture-publisher", sshKeygenPath: sshKeygen });
  await installCapabilityPack({ stateRoot, packPath, signaturePath, allowedSignersPath, identity: "live-fixture-publisher", sshKeygenPath: sshKeygen });

  const options = { stateRoot, id: declaration.id, version: declaration.version, allowedSignersPath, sshKeygenPath: sshKeygen };
  const admitted = await admitCapabilityImage({ ...options, dockerConfigPath });
  assert.equal(admitted.status, "image-admitted-disabled");
  assert.equal(admitted.enabled, false);
  assert.deepEqual(admitted.authority, { grantsHealthProbe: false, grantsToolUse: false, grantsDataAccess: false, grantsImagePull: false, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
  const status = await statusCapabilityImages(options);
  assert.equal(status.status, "admitted-disabled");
  assert.equal(status.cleanupRequired, 0);

  const health = await probeCapabilityHealth({ ...options, dockerConfigPath, now: new Date("2026-08-11T16:00:00Z"), completedAt: new Date("2026-08-11T16:00:01Z") });
  assert.equal(health.status, "passing-disabled");
  assert.equal(health.attempts, 1);
  assert.equal(health.health.consecutiveFailures, 0);
  assert.equal(health.authority.grantsToolUse, false);
  assert.equal((await statusCapabilityHealth(options)).status, "passing-disabled");

  const input = { text: "pixel-live" }, checkpointSha256 = "9".repeat(64), issuedAt = new Date("2026-08-11T16:00:02Z");
  const grant = issueCapabilityGrant(declaration, { expectedPackSha256: capabilityPackSha256(declaration), jobId: "work-1786464000000-abcdef123456", checkpointSha256, tools: ["analyze"], dataClassification: "public", limits: { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 3000, maxCalls: 1, maxMemoryMiB: 96, maxCpuCores: 0.5, maxPids: 24, maxWorkspaceBytes: 0 }, now: issuedAt, expiresAt: new Date("2026-08-11T16:00:32Z"), suffix: "abcdef123456" });
  const preflightDecision = assessWatchdog({ jobId: grant.jobId, checkpointSha256, expectedEventHeadSha256: null, events: [], proposal: { tool: "mcp.live-fixture-tools.analyze", arguments: input, effectClass: "read-only" }, limits: { maxToolCalls: 4, maxFailures: 2, maxRepeatedEquivalent: 3, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 }, allowedEffectClasses: ["read-only"], now: new Date("2026-08-11T16:00:02.001Z"), suffix: "000000000001" });
  let runtimeNow = Date.parse("2026-08-11T16:00:02.002Z");
  const runtimeResult = await executeCapabilityTool({ ...options, dockerConfigPath, grant, toolName: "analyze", input, preflightDecision, maxHealthAgeMs: 60000, claimSuffix: "000000000002", clock: () => new Date(runtimeNow++) });
  assert.equal(runtimeResult.status, "succeeded-disabled");
  assert.deepEqual(runtimeResult.structuredContent, { length: 10 });
  assert.equal(runtimeResult.authority.grantsReplay, false);
  const runtimeStatus = await statusCapabilityRuntime(options);
  assert.equal(runtimeStatus.status, "disabled");
  assert.equal(runtimeStatus.receipts, 1);
  assert.equal(runtimeStatus.cleanupRequired, 0);

  const imageReview = await reviewCapabilityImageRevocation(options);
  const revoked = await revokeCapabilityImageAdmission({ ...options, confirmReviewSha256: imageReview.confirmationSha256 });
  assert.equal(revoked.status, "image-admission-revoked");
  assert.equal(run(docker, ["image", "inspect", imageRef]).status, 0, "Pixel deleted an exact local image it does not own");
  const packReview = await reviewCapabilityPackRemoval(options);
  const removed = await removeCapabilityPack({ ...options, confirmReviewSha256: packReview.confirmationSha256 });
  assert.equal(removed.status, "removed-no-residue");
  const finalStatus = await statusCapabilityPacks({ stateRoot, allowedSignersPath, sshKeygenPath: sshKeygen });
  assert.equal(finalStatus.packs.length, 0);
  assert.deepEqual(finalStatus.removals, { completed: 1, recoveryRequired: 0 });
});

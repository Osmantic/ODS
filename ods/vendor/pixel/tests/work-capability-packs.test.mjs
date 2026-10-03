import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  buildCapabilityAdapterCleanupDockerCommands, buildCapabilityAdapterDockerCommand,
  buildCapabilityHealthDockerCommands, buildCapabilityWorkspaceDockerCommands, capabilityPackSha256,
  claimCapabilityGrant, issueCapabilityGrant, validateCapabilityAdapterImage, validateCapabilityWorkspaceVolume,
  WorkCapabilityPackError,
} from "../deploy/work-controller/capability-packs.mjs";
import { probeCapabilityMcpWithTrustedLaunch, runCapabilityMcpToolWithTrustedLaunch } from "../deploy/work-controller/mcp-stdio-client.mjs";
import { assessWatchdog } from "../deploy/work-controller/watchdog.mjs";
import { canonical, validateWorkCapabilityPack } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const baseTime = Date.parse("2026-08-10T14:00:00Z");
const fake = resolve(import.meta.dirname, "fixtures/work/fake-mcp-stdio.mjs");

function pack() {
  const inputSchema = { type: "object", additionalProperties: false, required: ["text"], properties: { text: { type: "string", minLength: 1, maxLength: 1024 } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length", "upper", "secretVisible"], properties: { length: { type: "integer", minimum: 0, maximum: 1024 }, upper: { type: "string", maxLength: 1024 }, secretVisible: { type: "boolean" } } };
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v1.schema.json", schemaVersion: 1,
    kind: "deep-work-capability-pack", id: "fixture-tools", version: "1.0.0", name: "Fixture tools",
    description: "Deterministic credential-free local fixture tools.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack" },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: {
      imageRef: `ghcr.io/osmantic/fixture-mcp@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64",
      executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"],
      uid: 1000, gid: 1000, serverName: "fixture-mcp", serverVersion: "1.0.0", workspace: "none",
    },
    tools: [{ name: "analyze", title: "Analyze text", description: "Return deterministic local text facts.", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), effectClass: "read-only" }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 4096, maxOutputBytes: 8192, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 1000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 0 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, network: "none", networkDestinations: [], credentials: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, sampling: false, elicitation: false, resources: false, prompts: false, tasks: false, serverRequests: false },
    boundary: "Signed declaration for one isolated credential-free MCP stdio adapter. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, or completion authority.",
  };
}

async function fixture(t, { packRuntimeMs = 1000, grantRuntimeMs = 750 } = {}) {
  const stateRoot = await mkdtemp(join(tmpdir(), "pixel-capability-pack-"));
  t.after(() => rm(stateRoot, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const value = pack(); value.limits.maxRuntimeMs = packRuntimeMs;
  const packSha256 = capabilityPackSha256(value);
  const grant = issueCapabilityGrant(value, {
    expectedPackSha256: packSha256, jobId: "work-1786366800000-abcdef123456", checkpointSha256: digest("d"),
    tools: ["analyze"], dataClassification: "internal", limits: { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: grantRuntimeMs, maxCalls: 1, maxMemoryMiB: 96, maxCpuCores: 0.5, maxPids: 24, maxWorkspaceBytes: 0 },
    now: new Date(baseTime), expiresAt: new Date(baseTime + 60000), suffix: "abcdef123456",
  });
  const claimed = await claimCapabilityGrant({ stateRoot, pack: value, expectedPackSha256: packSha256, grant, now: new Date(baseTime + 1), suffix: "000000000001" });
  const input = { text: "pixel" };
  const preflightDecision = assessWatchdog({
    jobId: grant.jobId, checkpointSha256: grant.checkpointSha256, expectedEventHeadSha256: null, events: [],
    proposal: { tool: "mcp.fixture-tools.analyze", arguments: input, effectClass: "read-only" },
    limits: { maxToolCalls: 10, maxFailures: 2, maxRepeatedEquivalent: 3, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    allowedEffectClasses: ["read-only"], now: new Date(baseTime + 2), suffix: "000000000002",
  });
  return { stateRoot, pack: value, expectedPackSha256: packSha256, grant, claim: claimed.claim, input, preflightDecision, now: new Date(baseTime + 3) };
}

test("signed capability declaration, exact grant, and one-use claim remain separate", async (t) => {
  const value = await fixture(t);
  assert.deepEqual(validateWorkCapabilityPack(value.pack), []);
  assert.equal(value.grant.singleUse, true);
  assert.equal(value.claim.externalEffects, false);
  await assert.rejects(() => claimCapabilityGrant({ stateRoot: value.stateRoot, pack: value.pack, expectedPackSha256: value.expectedPackSha256, grant: value.grant, now: new Date(baseTime + 3), suffix: "000000000003" }), /already consumed/);
  const widened = structuredClone(value.grant);
  widened.tools = ["escape"];
  assert.throws(() => buildCapabilityAdapterDockerCommand({ pack: value.pack, expectedPackSha256: value.expectedPackSha256, grant: widened, claim: value.claim, runtime: {} }), WorkCapabilityPackError);
});

test("adapter container command has broad local utility only inside the exact sterile boundary", async (t) => {
  const value = await fixture(t);
  const runtime = { dockerPath: "/usr/bin/docker", dockerConfigPath: "/run/pixel/empty-docker", cidFile: "/run/pixel/capability.cid", containerName: "pixel-cap-fixture-1", workspaceVolumeName: null, imageIdentifier: value.pack.adapter.imageRef };
  const command = buildCapabilityAdapterDockerCommand({ ...value, runtime });
  const cleanup = buildCapabilityAdapterCleanupDockerCommands({ ...value, runtime });
  for (const required of ["--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--network", "none", "--log-driver", "none", "--user", "1000:1000", "--entrypoint", "/opt/fixture/bin/server"]) assert.ok(command.args.includes(required), required);
  assert.deepEqual(command.env, {});
  assert.deepEqual(cleanup.remove.args.slice(-3), ["rm", "--force", runtime.containerName]);
  assert.deepEqual(cleanup.inspectAbsent.args.slice(-4), ["inspect", "--format", "{{.Id}}", runtime.containerName]);
  assert.ok(cleanup.listAbsent.args.includes(`name=^/${runtime.containerName}$`));
  assert.deepEqual(cleanup.remove.env, {}); assert.deepEqual(cleanup.inspectAbsent.env, {});
  assert.doesNotMatch(JSON.stringify(command), /docker\.sock|SSH_AUTH_SOCK|OPENAI_API_KEY|PIXEL_SECRET_CANARY/);
  const image = { Id: value.pack.adapter.imageId, RepoDigests: [value.pack.adapter.imageRef], Os: "linux", Architecture: "amd64", Config: { Env: ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"], Volumes: {}, OnBuild: [], Labels: { "org.osmantic.pixel.capability-tree": value.pack.provenance.treeSha256, "org.osmantic.pixel.capability-executable": value.pack.adapter.executableSha256, "org.osmantic.pixel.mcp-version": "2026-07-28" } } };
  assert.equal(validateCapabilityAdapterImage(image, value.pack, value.expectedPackSha256), true);
  image.Config.Env = ["SECRET=leak"];
  assert.throws(() => validateCapabilityAdapterImage(image, value.pack, value.expectedPackSha256), /unsafe environment/);
});

test("an exact already-local image ID is a pull-disabled capability identity", () => {
  const value = pack();
  value.adapter.imageRef = value.adapter.imageId;
  value.adapter.imageDigest = value.adapter.imageId;
  assert.deepEqual(validateWorkCapabilityPack(value), []);
  const image = { Id: value.adapter.imageId, RepoDigests: [], Os: "linux", Architecture: "amd64", Config: { Env: ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"], Volumes: {}, OnBuild: [], Labels: { "org.osmantic.pixel.capability-tree": value.provenance.treeSha256, "org.osmantic.pixel.capability-executable": value.adapter.executableSha256, "org.osmantic.pixel.mcp-version": "2026-07-28" } } };
  assert.equal(validateCapabilityAdapterImage(image, value, capabilityPackSha256(value)), true);
  const substituted = structuredClone(value); substituted.adapter.imageDigest = `sha256:${digest("e")}`;
  assert.match(validateWorkCapabilityPack(substituted).join("\n"), /digest differs/u);
});

test("disposable capability workspace is exact, size-bounded, and has explicit teardown", async (t) => {
  const stateRoot = await mkdtemp(join(tmpdir(), "pixel-capability-workspace-"));
  t.after(() => rm(stateRoot, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const capability = pack();
  capability.adapter.workspace = "disposable-read-write";
  capability.tools[0].effectClass = "workspace";
  capability.limits.maxWorkspaceBytes = 1048576;
  const expectedPackSha256 = capabilityPackSha256(capability);
  const grant = issueCapabilityGrant(capability, {
    expectedPackSha256, jobId: "work-1786366800000-abcdef123456", checkpointSha256: digest("d"), tools: ["analyze"], dataClassification: "internal",
    limits: { maxInputBytes: 2048, maxOutputBytes: 4096, maxRuntimeMs: 750, maxCalls: 1, maxMemoryMiB: 96, maxCpuCores: 0.5, maxPids: 24, maxWorkspaceBytes: 1048576 },
    now: new Date(baseTime), expiresAt: new Date(baseTime + 60000), suffix: "abcdef123456",
  });
  const claim = (await claimCapabilityGrant({ stateRoot, pack: capability, expectedPackSha256, grant, now: new Date(baseTime + 1), suffix: "000000000001" })).claim;
  const runtime = { dockerPath: "/usr/bin/docker", dockerConfigPath: "/run/pixel/empty-docker", workspaceVolumeName: "pixel-cap-workspace-1" };
  const commands = buildCapabilityWorkspaceDockerCommands({ pack: capability, expectedPackSha256, grant, claim, runtime });
  assert.ok(commands.create.args.includes("o=nosuid,nodev,noexec,size=1048576,mode=0700,uid=1000,gid=1000"));
  assert.deepEqual(commands.remove.args.slice(-2), ["rm", runtime.workspaceVolumeName]);
  assert.ok(commands.listAbsent.args.includes(`name=^${runtime.workspaceVolumeName}$`));
  assert.deepEqual(commands.create.env, {}); assert.deepEqual(commands.remove.env, {});
  const volume = { Name: runtime.workspaceVolumeName, Driver: "local", Scope: "local", Labels: { "com.osmantic.pixel.work-capability-claim": claim.claimId, "com.osmantic.pixel.work-job": claim.jobId }, Options: { type: "tmpfs", device: "tmpfs", o: "nosuid,nodev,noexec,size=1048576,mode=0700,uid=1000,gid=1000" } };
  assert.equal(validateCapabilityWorkspaceVolume(volume, capability, grant, claim, runtime), true);
  volume.Options.o = "rw";
  assert.throws(() => validateCapabilityWorkspaceVolume(volume, capability, grant, claim, runtime), /differs from its grant/);
});

test("modern MCP stdio discovery, exact tool schemas, preflight, call, and cleanup pass", async (t) => {
  const value = await fixture(t);
  const result = await runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} });
  assert.deepEqual(result.structuredContent, { length: 5, upper: "PIXEL", secretVisible: false });
  assert.equal(result.protocolVersion, "2026-07-28");
  assert.equal(result.dataClassification, "internal");
  assert.equal(result.externalEffects, false);
  assert.equal(result.authority, "none");
  assert.equal(Object.hasOwn(result, "content"), false);
  assert.match(result.contentSha256, /^[a-f0-9]{64}$/u);
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} }), /already started/);
});

test("MCP health transport proves discovery and exact tools without a grant or tool call", async (t) => {
  const value = await fixture(t);
  const result = await probeCapabilityMcpWithTrustedLaunch({ pack: value.pack, expectedPackSha256: value.expectedPackSha256, command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} });
  assert.equal(result.protocolVersion, "2026-07-28");
  assert.equal(result.frames, 2);
  assert.equal(result.externalEffects, false);
  assert.equal(result.authority, "none");
  assert.equal(Object.hasOwn(result, "structuredContent"), false);
  assert.equal(Object.hasOwn(result, "executionClaimSha256"), false);
});

test("health command executes only exact discovery in the sterile admitted image boundary", async (t) => {
  const value = await fixture(t);
  const runtime = { dockerPath: "/usr/bin/docker", dockerConfigPath: "/run/pixel/empty-docker", containerName: "pixel-cap-health-fixture-1", imageIdentifier: value.pack.adapter.imageRef };
  const commands = buildCapabilityHealthDockerCommands({ ...value, runtime });
  for (const required of ["run", "--rm", "-i", "--pull", "never", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--network", "none", "--log-driver", "none", "--entrypoint", value.pack.adapter.executablePath, value.pack.adapter.imageRef]) assert.ok(commands.run.args.includes(required), required);
  assert.equal(commands.run.args.includes("--mount"), false);
  assert.equal(commands.run.args.includes("--cidfile"), false);
  assert.deepEqual(commands.run.env, {});
  assert.deepEqual(commands.remove.args.slice(-3), ["rm", "--force", runtime.containerName]);
  assert.ok(commands.listAbsent.args.includes(`name=^/${runtime.containerName}$`));
  assert.throws(() => buildCapabilityHealthDockerCommands({ ...value, runtime: { ...runtime, imageIdentifier: value.pack.adapter.imageId } }), /runtime identity/);
});

test("MCP health transport rejects identity drift, server requests, and stalled discovery", async (t) => {
  for (const [scenario, pattern] of [["schema-drift", /tool surface|schemas/], ["server-request", /server request/]]) {
    // Keep semantic failure assertions independent of host scheduling pressure. The
    // deliberately short runtime-ceiling case below owns timeout qualification.
    const value = await fixture(t, { packRuntimeMs: 5000, grantRuntimeMs: 5000 });
    await assert.rejects(() => probeCapabilityMcpWithTrustedLaunch({ pack: value.pack, expectedPackSha256: value.expectedPackSha256, command: process.execPath, args: [fake, scenario], cwd: import.meta.dirname, env: {} }), pattern);
  }
  const value = await fixture(t, { packRuntimeMs: 150, grantRuntimeMs: 150 });
  await assert.rejects(() => probeCapabilityMcpWithTrustedLaunch({ pack: value.pack, expectedPackSha256: value.expectedPackSha256, command: process.execPath, args: [fake, "discovery-timeout"], cwd: import.meta.dirname, env: {} }), /runtime ceiling/);
});

test("MCP drift, injection, excess output, process failure, tool failure, and timeout fail closed", async (t) => {
  for (const [scenario, pattern] of [
    ["schema-drift", /tool surface|schemas/], ["extra-tool", /tool surface|schemas/],
    ["server-request", /server request/], ["input-required", /incomplete|more input/], ["bad-output", /output schema/],
    ["bad-content", /differs from structured output/], ["tool-error", /bounded error/],
    ["stdout-noise", /invalid UTF-8 JSON/], ["oversized-frame", /frame exceeded|invalid newline-delimited frame/],
    ["stderr-overflow", /stderr exceeded/], ["premature-exit", /exited unexpectedly/],
  ]) {
    // A saturated qualification host can delay a fresh child without changing the
    // protocol failure under test. Timeout behavior has its own 150 ms case below.
    const value = await fixture(t, { packRuntimeMs: 5000, grantRuntimeMs: 5000 });
    await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, scenario], cwd: import.meta.dirname, env: {} }), pattern);
  }
  const value = await fixture(t, { packRuntimeMs: 150, grantRuntimeMs: 150 });
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, "timeout"], cwd: import.meta.dirname, env: {} }), /runtime ceiling/);
});

test("MCP invocation rejects malformed input and missing or substituted preflight evidence", async (t) => {
  const value = await fixture(t);
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, input: { unexpected: true }, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} }), /input/);
  const stopped = structuredClone(value.preflightDecision); stopped.decision = "stop"; stopped.terminalState = "no-progress"; stopped.reason = "repeated-equivalent-call";
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, preflightDecision: stopped, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} }), /preflight/);
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: ".", env: {} }), /absolute command and working directory/);
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: { PIXEL_SECRET_CANARY: "must-not-cross" } }), /empty environment/);
  await assert.rejects(() => runCapabilityMcpToolWithTrustedLaunch({ ...value, now: new Date(value.grant.expiresAt), toolName: "analyze", command: process.execPath, args: [fake, "success"], cwd: import.meta.dirname, env: {} }), /expired before execution/);
  const downgraded = pack(); downgraded.data.returnedClassifications = ["public"];
  assert.match(validateWorkCapabilityPack(downgraded).join("\n"), /cannot silently reclassify/);
  for (const [keyword, value] of [["pattern", "^(a+)+$"], ["$ref", "https://attacker.invalid/schema.json"], ["unevaluatedProperties", false]]) {
    const unsafeSchema = pack(); unsafeSchema.tools[0].inputSchema.properties.text[keyword] = value;
    unsafeSchema.tools[0].inputSchemaSha256 = sha(unsafeSchema.tools[0].inputSchema);
    assert.match(validateWorkCapabilityPack(unsafeSchema).join("\n"), /unsupported capability-schema keyword/);
  }
});

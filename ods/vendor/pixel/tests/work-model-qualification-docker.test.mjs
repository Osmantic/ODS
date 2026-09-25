import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  buildModelQualificationDockerArguments, dockerQualificationOperationSha256,
  modelQualificationDockerBoundaries, modelQualificationDockerFailureDiagnosticName, modelQualificationDockerStartDiagnosticName, reviewDockerModelQualification,
  prepareDockerModelQualificationMaintenance, runDockerModelQualification, validateDockerQualificationBinding,
} from "../deploy/work-controller/model-qualification-docker.mjs";
import { modelCapabilityReceiptSha256 } from "../deploy/work-controller/model-qualification.mjs";
import { runFixedModelQualification } from "../deploy/work-controller/model-qualification-runner.mjs";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const digest = (character) => character.repeat(64);
const OWNER_UID = process.geteuid?.() ?? 1000;
const BACKEND_VERSION = "0.11.2.dev280+gilded.gnosis.v20.vllm1e9c9c3.sieec30ff.fi801d57a.cu132.20260731.r16";

function expectedMessage(id) {
  const tool = (name, arguments_) => ({ role: "assistant", content: null, tool_calls: [{ id: "call_fixture", type: "function", function: { name, arguments: JSON.stringify(arguments_) } }] });
  const values = new Map([
    ["structured-json", { role: "assistant", content: JSON.stringify({ status: "ok", sequence: 17 }) }],
    ["sustained-structured-output", { role: "assistant", content: JSON.stringify({ sequence: Array.from({ length: 4096 }, (_, index) => index) }) }],
    ["context-sentinel", { role: "assistant", content: "PIXEL_CONTEXT_SENTINEL_7f3a19" }],
    ["recovery-no-repair", { role: "assistant", content: "RETRY_REQUIRED" }],
    ["usage-accounting", { role: "assistant", content: "USAGE_OK" }],
    ["assistant-tool-selection", tool("pixel_calendar_list", { timeMin: "2026-08-14T09:00:00-04:00", timeMax: "2026-08-14T17:00:00-04:00" })],
    ["assistant-argument-fidelity", tool("pixel_calendar_propose_delete", { eventId: "evt_017", expectedEtag: "etag-42", sendUpdates: "none" })],
    ["scout-tool-selection", tool("read", { path: "evidence.txt" })],
    ["scout-argument-fidelity", tool("search", { pattern: "alpha.*omega", path: "src" })],
    ["builder-tool-selection", tool("bash", { command: "printf pixel" })],
    ["builder-argument-fidelity", tool("edit", { path: "src/app.js", old: "OLD_VALUE", replacement: "NEW_VALUE" })],
    ["data-tool-selection", tool("read", { path: "datasets/input.csv" })],
    ["data-argument-fidelity", tool("write", { path: "artifacts/summary.txt", content: "rows=17" })],
    ["research-tool-selection", tool("pixel_research", { query: "Pixel release notes" })],
    ["research-argument-fidelity", tool("read", { path: "sources/source-017.txt" })],
  ]);
  return structuredClone(values.get(id));
}

function passingResult(id) {
  const message = expectedMessage(id);
  return {
    body: {
      choices: [{ index: 0, finish_reason: message.tool_calls ? "tool_calls" : "stop", message }],
      usage: {
        prompt_tokens: id === "context-sentinel" ? 32768 : 128,
        completion_tokens: id === "sustained-structured-output" ? 8192 : 16,
      },
    },
    latencyMs: 25,
  };
}

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-docker-model-qualification-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const evidenceRoot = join(root, "evidence");
  await mkdir(evidenceRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(evidenceRoot, 0o700);
  const runnerImageDigest = `sha256:${digest("f")}`;
  const model = {
    provider: "vllm", id: "DeepSeek-V4-Flash-0731", modelArtifactSha256: digest("a"),
    backendImageDigest: `sha256:${digest("b")}`, backendVersion: BACKEND_VERSION,
    acceleratorClass: "nvidia-cuda", promptContractSha256: digest("c"),
    toolSchemaSha256: digest("d"), contextWindow: 1048576, supportsVision: false,
  };
  const prepared = {
    configuration: { publishLoopbackPort: null, restartPolicy: "no", accelerator: { class: "nvidia-cuda" } },
    artifactManifest: { artifactSha256: model.modelArtifactSha256 },
    policy: {
      runner: { imageDigest: runnerImageDigest, imageRef: `local/pixel-work-runner@${runnerImageDigest}` },
      localModel: {
        prepared: false, ...model, imageDigest: model.backendImageDigest,
        imageRef: `local/pixel-dsv4@${model.backendImageDigest}`,
      },
    },
    environment: { stateRoot: root, runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-model-qualification-fixture" } },
    launchBundleSha256: digest("e"),
  };
  delete prepared.policy.localModel.backendImageDigest;
  const qualification = {
    schemaVersion: 1, backendOrigin: "http://127.0.0.1:18081", model,
    timeoutMs: 300000, maxResponseBytes: 1048576, qualificationLifetimeSeconds: 604800,
  };
  const paths = {
    backend: join(root, "backend.json"), qualification: join(root, "qualification.json"),
    config: join(root, "docker.json"), evidenceRoot,
  };
  await privateWrite(paths.backend, { fixture: true });
  await privateWrite(paths.qualification, qualification);
  await privateWrite(paths.config, {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json",
    schemaVersion: 1, backendConfigPath: paths.backend, qualificationConfigPath: paths.qualification,
    runnerImageDigest, evidenceRoot, boundary: modelQualificationDockerBoundaries.configuration,
  });
  return { root, paths, prepared, qualification, runnerImageDigest, model };
}

test("Docker qualification binds exact DSV4 identity and emits a hardened internal-only command", async (t) => {
  const value = await fixture(t);
  assert.equal(validateDockerQualificationBinding(value.prepared, value.qualification).model.backendVersion, BACKEND_VERSION);
  const localExact = structuredClone(value.prepared);
  localExact.policy.runner.imageRef = localExact.policy.runner.imageDigest;
  assert.equal(validateDockerQualificationBinding(localExact, value.qualification).model.backendVersion, BACKEND_VERSION);
  const qualificationBytes = await readFile(value.paths.qualification);
  const dockerConfigBytes = await readFile(value.paths.config);
  const operationSha256 = dockerQualificationOperationSha256(value.prepared, hash(dockerConfigBytes), hash(qualificationBytes), value.runnerImageDigest);
  assert.notEqual(
    operationSha256,
    dockerQualificationOperationSha256(value.prepared, digest("1"), hash(qualificationBytes), value.runnerImageDigest),
  );
  const args = buildModelQualificationDockerArguments({
    prepared: value.prepared,
    configuration: {
      qualificationConfigPath: value.paths.qualification, evidenceRoot: value.paths.evidenceRoot,
      runnerImageDigest: value.runnerImageDigest,
    },
    uid: 1000, gid: 1000, operationSha256,
  });
  for (const expected of ["--pull", "never", "--network", "pixel-model-qualification-fixture", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--restart", "no", "--rm"]) assert.ok(args.includes(expected), expected);
  assert.equal(args.includes("--publish"), false);
  assert.equal(args.includes("--gpus"), false);
  assert.equal(args.at(-2), value.runnerImageDigest);
  assert.equal(args.at(-1), "/opt/pixel/deploy/work-controller/model-qualification-container.mjs");
  const widened = structuredClone(value.qualification); widened.backendOrigin = "http://localhost:18081";
  assert.throws(() => validateDockerQualificationBinding(value.prepared, widened));
});

test("Docker qualification refuses an unavailable coordination root during review", async (t) => {
  const value = await fixture(t);
  const prepared = structuredClone(value.prepared);
  prepared.environment.stateRoot = join(value.root, "missing-state-root");
  await assert.rejects(
    reviewDockerModelQualification(value.paths.config, {
      expectedOwnerUid: OWNER_UID,
      async prepareModelBackendLaunch() { return prepared; },
    }),
    /coordination root is unavailable/u,
  );
});

test("Docker qualification reviews, runs, verifies, and tears down one fresh exact backend", async (t) => {
  const value = await fixture(t);
  const calls = [];
  let preparationCalls = 0;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { preparationCalls += 1; return value.prepared; },
    async inspectImage() { calls.push("inspect-image"); return { Id: value.runnerImageDigest }; },
    async inspectContainer() { return null; },
    async inspectNetwork() { return null; },
    async startModelBackend(_prepared, options) { calls.push(["start", options.confirmation]); assert.equal((await options.postflight()).launchBundleSha256, value.prepared.launchBundleSha256); return { state: "ready-started", checks: { readinessPassed: true } }; },
    async stopModelBackend(_prepared, options) { calls.push(["stop", options.confirmation]); assert.equal((await options.postflight()).launchBundleSha256, value.prepared.launchBundleSha256); return { state: "removed" }; },
    async runQualificationContainer(args) {
      calls.push(["qualify", args]);
      const receipt = await runFixedModelQualification({
        model: value.model, observedAt: new Date("2026-08-13T12:00:00Z"),
        expiresAt: new Date("2026-08-20T12:00:00Z"), suffix: "abcdef123456",
        invoke: async (_request, metadata) => passingResult(metadata.id),
      });
      await privateWrite(join(value.paths.evidenceRoot, "model-qualification.json"), receipt);
      return { stdout: `${JSON.stringify({ status: receipt.status, receiptSha256: modelCapabilityReceiptSha256(receipt) })}\n`, stderr: "" };
    },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  assert.equal(review.status, "confirmation-required");
  assert.equal(review.changes.usesExternalNetwork, false);
  const maintenancePreparation = await prepareDockerModelQualificationMaintenance(value.paths.config, dependencies);
  const result = await runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, { ...dependencies, reviewedMaintenancePreparation: maintenancePreparation });
  assert.equal(result.status, "qualified");
  assert.equal(result.casesPassed, 15);
  assert.equal(result.exactUsage, true);
  assert.deepEqual(calls.map((entry) => Array.isArray(entry) ? entry[0] : entry), ["inspect-image", "start", "qualify", "stop"]);
  assert.equal(preparationCalls, 3, "review, maintenance preparation, and one final byte postflight only");
  assert.match(JSON.stringify(result), /^((?!DeepSeek|vllm|\/usr\/bin\/docker).)*$/u);
});

test("Docker qualification confirmation binds the exact private configuration and receipt destination", async (t) => {
  const value = await fixture(t);
  let inspected = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { inspected = true; return { Id: value.runnerImageDigest }; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  const substitutedEvidenceRoot = join(value.root, "substituted-evidence");
  await mkdir(substitutedEvidenceRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(substitutedEvidenceRoot, 0o700);
  const configuration = JSON.parse(await readFile(value.paths.config, "utf8"));
  configuration.evidenceRoot = substitutedEvidenceRoot;
  await privateWrite(value.paths.config, configuration);
  await assert.rejects(
    runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies),
    /confirmation differs from the exact qualification operation/u,
  );
  assert.equal(inspected, false);
});

test("Docker qualification refuses a pre-existing backend without stopping it", async (t) => {
  const value = await fixture(t);
  let started = false;
  let stopped = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer(target) { return target === value.prepared.environment.runtime.backendContainerName ? { Id: digest("7") } : null; },
    async inspectNetwork() { return null; },
    async startModelBackend() { started = true; return { state: "ready-already-running", checks: { readinessPassed: true } }; },
    async stopModelBackend() { stopped = true; return { state: "removed" }; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  await assert.rejects(runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies), /absent exact backend/u);
  assert.equal(started, false);
  assert.equal(stopped, false);
});

test("Docker qualification removes only an exact leftover qualification container after invocation failure", async (t) => {
  const value = await fixture(t);
  let remaining = null;
  let removed = false;
  let stopped = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer(target) {
      if (remaining && [remaining.Id, remaining.Name.slice(1)].includes(target)) return remaining;
      return null;
    },
    async inspectNetwork() { return null; },
    async startModelBackend() { return { state: "ready-started", checks: { readinessPassed: true } }; },
    async stopModelBackend() { stopped = true; return { state: "removed" }; },
    async runQualificationContainer(args) {
      const name = args[args.indexOf("--name") + 1];
      const label = args[args.indexOf("--label") + 1].split("=", 2)[1];
      remaining = {
        Id: digest("9"), Name: `/${name}`, Image: value.runnerImageDigest,
        Config: { Labels: { "com.osmantic.pixel.work-model-qualification.operation-sha256": label } },
        HostConfig: { NetworkMode: value.prepared.environment.runtime.backendNetworkName },
      };
      throw new Error("synthetic Docker client interruption");
    },
    async removeQualificationContainer(containerId) {
      assert.equal(containerId, digest("9"));
      removed = true;
      remaining = null;
    },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  await assert.rejects(runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies), /synthetic Docker client interruption/u);
  assert.equal(removed, true);
  assert.equal(stopped, true);
});

test("Docker qualification refuses an exact-name collision before starting the backend", async (t) => {
  const value = await fixture(t);
  let started = false;
  let removed = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer() { return { Id: digest("8") }; },
    async startModelBackend() { started = true; return { state: "ready-started", checks: { readinessPassed: true } }; },
    async removeQualificationContainer() { removed = true; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  await assert.rejects(runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies), /already exists/u);
  assert.equal(started, false);
  assert.equal(removed, false);
});

test("Docker qualification never removes a substituted leftover and still attempts backend cleanup", async (t) => {
  const value = await fixture(t);
  let remaining = null;
  let removed = false;
  let stopped = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer(target) {
      if (remaining && [remaining.Id, remaining.Name.slice(1)].includes(target)) return remaining;
      return null;
    },
    async inspectNetwork() { return null; },
    async startModelBackend() { return { state: "ready-started", checks: { readinessPassed: true } }; },
    async stopModelBackend() { stopped = true; return { state: "removed" }; },
    async runQualificationContainer(args) {
      const name = args[args.indexOf("--name") + 1];
      remaining = {
        Id: digest("7"), Name: `/${name}`, Image: value.runnerImageDigest,
        Config: { Labels: { "com.osmantic.pixel.work-model-qualification.operation-sha256": digest("6") } },
        HostConfig: { NetworkMode: value.prepared.environment.runtime.backendNetworkName },
      };
      throw new Error("synthetic Docker client interruption");
    },
    async removeQualificationContainer() { removed = true; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  let observed;
  await assert.rejects(
    runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies),
    (error) => { observed = error; return /cleanup could not be proved/u.test(error?.message ?? "") && /^[a-f0-9]{64}$/u.test(error?.qualificationDiagnosticSha256 ?? ""); },
  );
  assert.equal(removed, false);
  assert.equal(stopped, true);
  const diagnostic = JSON.parse(await readFile(join(value.paths.evidenceRoot, modelQualificationDockerFailureDiagnosticName), "utf8"));
  assert.equal(hash(await readFile(join(value.paths.evidenceRoot, modelQualificationDockerFailureDiagnosticName))), observed.qualificationDiagnosticSha256);
  assert.equal(diagnostic.failureStage, "qualification-cleanup");
  assert.equal(diagnostic.cleanup.qualificationContainerCleanupProven, false);
  assert.equal(diagnostic.cleanup.backendCleanupProven, true);
  assert.equal(diagnostic.primaryFailure.message, "synthetic Docker client interruption");
});

test("Docker qualification cleans an exact partial backend when start fails before readiness", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const logStream = (text) => ({ text, bytes: Buffer.byteLength(text), sha256: hash(text), truncated: false });
  const diagnostic = {
    schemaVersion: 1, operation: "pixel-work-model-backend-start-diagnostic",
    launchBundleSha256: value.prepared.launchBundleSha256, containerId: digest("4"),
    failure: { class: "WorkModelBackendLifecycleError", message: "model backend stopped before reporting its ready event" },
    state: { status: "exited", running: false, restarting: false, oomKilled: false, exitCode: 1, error: "entrypoint failed", startedAt: "2026-08-12T12:00:00Z", finishedAt: "2026-08-12T12:00:01Z" },
    logs: { captureStatus: "captured", tailLines: 400, stdout: logStream("startup\n"), stderr: logStream("failure\n") },
    privacy: { ownerPrivateRequired: true, mayContainPaths: true, mayContainBackendMessages: true, promptsOrResponsesExpected: false, credentialsExpected: false },
    authority: { grantsExecution: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: modelQualificationDockerBoundaries.startDiagnostic,
  };
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer() { return null; },
    async inspectNetwork() { return null; },
    async startModelBackend() { const error = new Error("synthetic post-create failure"); error.modelBackendStartDiagnostic = diagnostic; throw error; },
    async stopModelBackend() { stopped = true; return { state: "removed" }; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  let observed;
  await assert.rejects(
    runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies),
    (error) => { observed = error; return error?.message === "synthetic post-create failure" && error?.qualificationFailureStage === "backend-start" && /^[a-f0-9]{64}$/u.test(error?.qualificationDiagnosticSha256 ?? ""); },
  );
  assert.equal(stopped, true);
  const diagnosticPath = join(value.paths.evidenceRoot, modelQualificationDockerStartDiagnosticName);
  const bytes = await readFile(diagnosticPath);
  assert.equal(hash(bytes), observed.qualificationDiagnosticSha256);
  assert.deepEqual(JSON.parse(bytes), diagnostic);
  if (process.platform !== "win32") assert.equal((await stat(diagnosticPath)).mode & 0o077, 0);
});

test("Docker qualification retains a private diagnostic for a pre-container lifecycle failure", async (t) => {
  const value = await fixture(t);
  let stopped = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer() { return null; },
    async inspectNetwork() { return null; },
    async startModelBackend() { throw new Error("coordination root disappeared after review"); },
    async stopModelBackend() { stopped = true; return { state: "already-absent" }; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  let observed;
  await assert.rejects(
    runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies),
    (error) => { observed = error; return error?.qualificationFailureStage === "backend-start" && /^[a-f0-9]{64}$/u.test(error?.qualificationDiagnosticSha256 ?? ""); },
  );
  assert.equal(stopped, true);
  const diagnosticPath = join(value.paths.evidenceRoot, modelQualificationDockerFailureDiagnosticName);
  const bytes = await readFile(diagnosticPath);
  const diagnostic = JSON.parse(bytes);
  assert.equal(hash(bytes), observed.qualificationDiagnosticSha256);
  assert.equal(diagnostic.failureStage, "backend-start");
  assert.equal(diagnostic.primaryFailure.message, "coordination root disappeared after review");
  assert.equal(diagnostic.cleanup.backendCleanupProven, true);
  if (process.platform !== "win32") assert.equal((await stat(diagnosticPath)).mode & 0o077, 0);
});

test("Docker qualification runs the maintenance custody guard immediately before backend start", async (t) => {
  const value = await fixture(t);
  let started = false;
  let stopped = false;
  const dependencies = {
    expectedOwnerUid: OWNER_UID, expectedOwnerGid: 1000,
    async prepareModelBackendLaunch() { return value.prepared; },
    async inspectImage() { return { Id: value.runnerImageDigest }; },
    async inspectContainer() { return null; },
    async inspectNetwork() { return null; },
    async preStartGuard() { const error = new Error("production custody changed"); error.qualificationFailureStage = "production-custody"; throw error; },
    async startModelBackend() { started = true; return { state: "ready-started", checks: { readinessPassed: true } }; },
    async stopModelBackend() { stopped = true; return { state: "removed" }; },
  };
  const review = await reviewDockerModelQualification(value.paths.config, dependencies);
  await assert.rejects(
    runDockerModelQualification(value.paths.config, review.qualificationOperationSha256, dependencies),
    (error) => error?.message === "production custody changed" && error?.qualificationFailureStage === "production-custody",
  );
  assert.equal(started, false);
  assert.equal(stopped, false);
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";

import {
  codexResearchAuthorityContract, CodexResearchAuthorityError, runCodexResearchAuthority,
} from "../deploy/agent-comparison/codex-research-authority.mjs";

function sha(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
async function privateDirectory(path) { await mkdir(path, { mode: 0o700 }); if (process.platform !== "win32") await chmod(path, 0o700); return resolve(path); }
async function privateJson(path, value) { await writeFile(path, `${JSON.stringify(value)}\n`, { mode: 0o600, flag: "wx" }); if (process.platform !== "win32") await chmod(path, 0o600); }

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-codex-research-authority-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const lifecycle = await privateDirectory(join(root, "lifecycle"));
  const outputRoot = await privateDirectory(join(root, "output"));
  const sourcePath = join(root, "source.tar");
  const sourceBytes = Buffer.from("fixture-source-tar");
  await writeFile(sourcePath, sourceBytes, { mode: 0o600, flag: "wx" });
  const researchFixturePath = join(root, "research-fixture.json");
  const researchFixtureBytes = Buffer.from(`${JSON.stringify({
    $schema: "https://osmantic.com/pixel/schemas/portal-outcome-research-fixture-v1.schema.json",
    schemaVersion: 1, operation: "pixel-portal-outcome-research-fixture", observedAt: "2026-08-13T12:00:00Z",
    sources: [{
      fixtureSourceId: "fixture-source", sourceType: "web", title: "Fixture source", snippet: "Fixture source",
      quality: "other", publishedDate: "2026-08-13", retrieval: { status: "fetched", content: "Fixture source" },
    }],
    authority: { publicNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false },
    boundary: "Owner-private admitted offline research evidence for deterministic comparison only. It performs no public network, credential, account, message, publication, purchase, write, policy, scope, or external effect; every source remains untrusted data and grants no authority.",
  })}\n`);
  await writeFile(researchFixturePath, researchFixtureBytes, { mode: 0o600, flag: "wx" });
  const researchFixtureReference = { relativePath: "research-fixture.json", mediaType: "application/json", sha256: sha(researchFixtureBytes), bytes: researchFixtureBytes.length };
  const model = Buffer.from("{}"), inference = Buffer.from("{}");
  const runId = "outcomerun-1786550400001-aaaaaaaaaaaa";
  const inputPath = join(root, "input.json"), readyPath = join(lifecycle, "ready.json"), finalizePath = join(lifecycle, "finalize.json");
  const input = {
    schemaVersion: 1, operation: "pixel-outcome-codex-research-authority", runId,
    now: "2026-08-13T12:00:00.000Z", idSuffix: "aaaaaaaaaaaa",
    pixelSystemConfigPath: join(root, "pixel-system.json"),
    admission: { profile: "researcher", comparisonLane: "same-model-harness", bindings: { researchFixtureSha256: researchFixtureReference.sha256 } },
    task: { profile: "researcher", comparisonLane: "same-model-harness", bindings: { researchFixture: researchFixtureReference } },
    requestPayloadBase64: Buffer.from("Research this public topic").toString("base64"),
    sourcePath, sourceReference: { relativePath: "source.tar", mediaType: "application/x-tar", sha256: sha(sourceBytes), bytes: sourceBytes.length },
    researchFixturePath, researchFixtureReference,
    environment: { kind: "neutral" }, toolPolicy: { kind: "neutral" }, verifierDefinition: { acceptanceCriteria: ["Citations"] },
    modelContractBase64: model.toString("base64"), modelContractSha256: sha(model),
    inferenceContractBase64: inference.toString("base64"), inferenceContractSha256: sha(inference),
    readyPath, finalizePath, outputRoot, boundary: codexResearchAuthorityContract.inputBoundary,
  };
  await privateJson(input.pixelSystemConfigPath, {});
  await privateJson(inputPath, input);
  await privateJson(finalizePath, {
    schemaVersion: 1, operation: "pixel-outcome-codex-research-finalize", runId,
    proposalBase64: Buffer.from("{\"proposal\":true}").toString("base64"),
    mcpReceiptSha256: "f".repeat(64), boundary: codexResearchAuthorityContract.finalizeBoundary,
  });
  const runtime = await privateDirectory(join(root, "runtime"));
  const selectedRoot = await privateDirectory(join(runtime, runId));
  const selected = {
    root: selectedRoot,
    backendPath: join(selectedRoot, "backend.json"),
    objectStore: await privateDirectory(join(selectedRoot, "objects")),
    workspaceRoot: await privateDirectory(join(selectedRoot, "workspaces")),
    workState: await privateDirectory(join(selectedRoot, "state")),
  };
  await privateJson(selected.backendPath, {});
  return { root, input, inputPath, readyPath, outputRoot, selected };
}

function dependencies(value, { batches = 1 } = {}) {
  const batch = { batchId: "batch", createdAt: "2026-08-13T12:00:00.001Z" };
  const plan = { jobId: "work-1786550400000-aaaaaaaaaaaa", research: { maxQueries: 4 } };
  const lease = { leaseId: "lease", issuedAt: "2026-08-13T12:00:00.000Z", expiresAt: "2026-08-13T12:01:00.000Z" };
  const claim = { claimId: "claim" };
  const calls = [];
  const deps = {
    calls,
    async loadPixelSystemConfig(path) { calls.push(["load", path]); return { runtimeRoot: resolve(join(value.root, "runtime")) }; },
    async materializeRun(_configuration, runId, profile) { calls.push(["materialize", runId, profile]); return { selected: value.selected, environmentTemplateSha256: "8".repeat(64) }; },
    async prepare() { calls.push(["prepare"]); return { policy: { policy: true }, environment: { executorPath: "/executor", archiveLimits: { maxEntries: 10, maxFileBytes: 1024 }, researchRuntime: { researchCourierQueueRoot: "/courier", researchEndpoint: "http://127.0.0.1:8888" } }, launch: { bindings: { environmentSha256: "9".repeat(64) } } }; },
    verifyPreparedComparison() { calls.push(["verify-runtime"]); },
    async stageSource(_source, destination, bytes, digest) { calls.push(["stage", destination, bytes, digest]); },
    buildPixelResearcherJob() { calls.push(["build-job"]); return { request: {}, entries: [], compileSuffix: "aaaaaaaaaaaa" }; },
    compileResearcher() { calls.push(["compile"]); return { plan, lease }; },
    async prepareResearcherRun() { calls.push(["prepare-run"]); return { plan, lease, workspace: { discardPath: "/discard" } }; },
    createLeaseConsumption() { calls.push(["consume"]); return claim; },
    async claimLease() { calls.push(["claim"]); },
    async serveResearchToolQueue(options) {
      calls.push(["serve"]);
      for (let index = 0; index < batches; index += 1) await options.onCompleted({ batch: { ...batch, batchId: `batch-${index}` } });
      while (!options.signal.aborted) await new Promise((resolvePromise) => setTimeout(resolvePromise, 1));
      return { completed: batches, rejected: 0, errors: 0, invalid: 0, contentStoredBeyondJob: false, credentialsExposed: false, directNetworkGrantedToWorker: false, externalWritesPerformed: false };
    },
    parseResearchReportProposal(text) { calls.push(["parse", text]); return { proposal: true }; },
    finalizeResearchReport(options) { calls.push(["finalize-report"]); return { reportId: "report", createdAt: options.now.toISOString(), batchSha256s: ["1".repeat(64)] }; },
    async verifyResearchReport(options) { calls.push(["verify-report"]); return { status: "evidence-pass", createdAt: options.now.toISOString(), batchSha256s: ["1".repeat(64)], findings: [] }; },
    researchProvenance(_result, reportSha256, verificationSha256, format) { calls.push(["provenance", format]); return { format, reportArtifactSha256: reportSha256, verificationArtifactSha256: verificationSha256 }; },
    async discardPreparedRun() { calls.push(["discard"]); },
    async removePixelRunRoot() { calls.push(["remove"]); },
    clock() { return new Date("2026-08-13T12:00:00.010Z"); },
    reportSuffix() { return "bbbbbbbbbbbb"; }, verificationSuffix() { return "cccccccccccc"; },
  };
  return deps;
}

test("Codex Researcher authority binds the real split queue before offline finalization and cleanup", async () => {
  const value = await fixture();
  try {
    const deps = dependencies(value);
    const result = await runCodexResearchAuthority(value.inputPath, deps);
    assert.equal(result.receipt.batches, 1);
    assert.equal(result.receipt.mcpReceiptSha256, "f".repeat(64));
    assert.equal(result.provenance.format, "codex-research-provenance-v1");
    const ready = JSON.parse(await readFile(value.readyPath, "utf8"));
    assert.equal(ready.boundary, codexResearchAuthorityContract.readyBoundary);
    assert.equal(ready.maxCalls, 4);
    assert.equal(ready.queueRoot, join(value.selected.root, "codex-research-queue"));
    for (const name of ["codex-research-report.json", "codex-research-verification.json", "codex-research-provenance.json", "authority-receipt.json"]) {
      assert.ok((await readFile(join(value.outputRoot, name))).length > 1);
    }
    assert.deepEqual(deps.calls.slice(-4).map((item) => item[0]), ["verify-report", "provenance", "discard", "remove"]);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Codex Researcher authority refuses proposal finalization without a retained broker batch", async () => {
  const value = await fixture();
  try {
    const deps = dependencies(value, { batches: 0 });
    await assert.rejects(() => runCodexResearchAuthority(value.inputPath, deps), CodexResearchAuthorityError);
    assert.equal(deps.calls.some((item) => item[0] === "finalize-report"), false);
    assert.deepEqual(deps.calls.slice(-2).map((item) => item[0]), ["discard", "remove"]);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Codex Researcher authority withholds completion when exact-run cleanup fails", async () => {
  const value = await fixture();
  try {
    const deps = dependencies(value);
    deps.discardPreparedRun = async () => { deps.calls.push(["discard-failed"]); throw new Error("fixture cleanup failure"); };
    await assert.rejects(
      () => runCodexResearchAuthority(value.inputPath, deps),
      /authority cleanup failed closed/u,
    );
    assert.equal(deps.calls.at(-1)[0], "remove");
    await assert.rejects(() => readFile(join(value.outputRoot, "authority-receipt.json")), /ENOENT/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Codex Researcher authority rejects substituted lifecycle and source fields before execution", async () => {
  const value = await fixture();
  try {
    const altered = JSON.parse(await readFile(value.inputPath, "utf8"));
    altered.sourceReference.path = altered.sourcePath;
    await rm(value.inputPath);
    await privateJson(value.inputPath, altered);
    const deps = dependencies(value);
    await assert.rejects(() => runCodexResearchAuthority(value.inputPath, deps), /source reference fields are invalid/);
    assert.equal(deps.calls.some((item) => item[0] === "serve"), false);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Codex Researcher authority rejects a tampered admitted fixture before broker service", async () => {
  const value = await fixture();
  try {
    await writeFile(value.input.researchFixturePath, Buffer.from("{}\n"));
    const deps = dependencies(value);
    await assert.rejects(() => runCodexResearchAuthority(value.inputPath, deps), /research fixture differs from its admitted reference/u);
    assert.equal(deps.calls.some((item) => item[0] === "serve"), false);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

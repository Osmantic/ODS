import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { canonical } from "../scripts/lib/work-contract.mjs";
import { workProviderEquivalenceTestSeam } from "../deploy/work-provider/equivalence-runner.mjs";
import { workProviderQualificationTestSeam } from "../deploy/work-provider/qualification-runner.mjs";
import {
  admitNeutralCorpusRequest, findNeutralCorpusCase, NEUTRAL_CORPUS_VERSION,
  TOOL_CHOICE_FINAL_REPLY, TOOL_CHOICE_TOOL_NAME, TOOL_CHOICE_TOOL_ARGUMENTS,
  LONG_CONTEXT_REQUIRED_REPLY, OUTPUT_CAPACITY_REPLY, OUTPUT_CAPACITY_REPORTED_TOKENS,
} from "../deploy/work-provider/neutral-corpus.mjs";

const LOCAL_MODEL = "DeepSeek-V4-Flash-0731";
const LONG_CONTEXT_REPORTED_INPUT_TOKENS = 38000;
const PATCH = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";

function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function withoutArtifactSha(doc) { const { artifactSha256: _ignored, ...rest } = doc; return rest; }
function withoutQualificationSha(doc) { const { qualificationSha256: _ignored, ...rest } = doc; return rest; }

function fakeTransportForSemanticCases() {
  return async (options) => {
    const content = options.input?.messages?.[0]?.content ?? "";
    let reply;
    if (content.includes("structural review")) reply = JSON.stringify({ exportCount: 3, defectFunction: "add", defectKind: "wrong-operator" });
    else if (content.includes("triage") || content.includes("retryable")) reply = JSON.stringify({ classification: "transient", retried: true, resolution: "retry-succeeded" });
    else reply = PATCH;
    return { assistant: { message: { role: "assistant", content: reply }, finishReason: "stop", usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 }, providerModel: options.ledger.model }, providerRequestId: "equiv-fixture", networkBytes: 100 };
  };
}

function uncertainThenSemanticTransport() {
  const semantic = fakeTransportForSemanticCases();
  let first = true;
  return async (options) => {
    if (first) {
      first = false;
      const error = new Error("equivalence fixture transport outcome is uncertain");
      error.outcome = "uncertain";
      throw error;
    }
    return semantic(options);
  };
}

function responseFor(caseEntry) {
  switch (caseEntry.task) {
    case "structural-review":
      return { message: { role: "assistant", content: '{"exportCount":3,"defectFunction":"add","defectKind":"wrong-operator"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: LOCAL_MODEL };
    case "failure-triage":
      return { message: { role: "assistant", content: '{"classification":"transient","retried":true,"resolution":"retry-succeeded"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: LOCAL_MODEL };
    case "patch-proposal":
      return { message: { role: "assistant", content: PATCH }, finishReason: "stop", usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 }, providerModel: LOCAL_MODEL };
    case "long-context-sentinel":
      return { message: { role: "assistant", content: LONG_CONTEXT_REQUIRED_REPLY }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS, outputTokens: 3, totalTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS + 3 }, providerModel: LOCAL_MODEL };
    case "output-capacity":
      return { message: { role: "assistant", content: OUTPUT_CAPACITY_REPLY }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_REPORTED_TOKENS, totalTokens: 40 + OUTPUT_CAPACITY_REPORTED_TOKENS }, providerModel: LOCAL_MODEL };
    default:
      throw new Error("unknown neutral task");
  }
}

function qualificationPassingTransport() {
  return async ({ input }) => {
    const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
    if (!admitted) throw new Error("transport received an input outside the neutral corpus");
    if (admitted.kind === "tool-choice-replay") {
      return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "qual-tool-2", networkBytes: 200 };
    }
    const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
    if (caseEntry.task === "tool-choice-continuation") {
      const callId = "k3tool_0001";
      return { assistant: { message: { role: "assistant", content: null, toolCalls: [{ id: callId, name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS }], providerState: { protocol: "local-openai-compatible", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: callId, type: "function", function: { name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS } }] }, finishReason: "tool_calls" } }, finishReason: "tool_calls", usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 }, providerModel: LOCAL_MODEL }, providerRequestId: "qual-tool-1", networkBytes: 200 };
    }
    return { assistant: responseFor(caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
  };
}

// Runs the sealed qualification runner (which promotes a genuine semantic qualification only after
// the full trial attestation and the closed durable run ledger) and returns its owner-private
// content-free qualification artifact.
async function produceQualificationArtifact() {
  const root = await mkdtemp(join(tmpdir(), "pixel-equiv-qual-"));
  await chmod(root, 0o700);
  const ledgerRoot = join(root, "ledger");
  await mkdir(ledgerRoot, { mode: 0o700 });
  const artifactPath = join(root, "qualification-artifact.json");
  const argv = ["--lane", "local-only", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--ledger-root", ledgerRoot, "--qualification-report", artifactPath];
  const receipt = await workProviderQualificationTestSeam.runWithTransport(argv, qualificationPassingTransport());
  assert.equal(receipt.status, "qualified");
  assert.ok(receipt.semanticQualification, "a qualified trial must promote a semantic qualification");
  const artifact = JSON.parse(await readFile(artifactPath, "utf8"));
  assert.equal(artifact.status, "qualified");
  assert.equal(artifact.artifactSha256, sha(withoutArtifactSha(artifact)));
  return { root, artifactPath, artifact, artifactSha256: artifact.artifactSha256 };
}

function equivalenceArgv(ledgerRoot, artifactPath, artifactSha256, extra = []) {
  return ["--lane", "local-only", "--ledger-root", ledgerRoot, "--qualification-report", artifactPath, "--qualification-sha256", artifactSha256, ...extra];
}

async function withEquivRoot(fn) {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-equiv-"));
  await chmod(root, 0o700);
  try { return await fn(root); } finally { await rm(root, { recursive: true, force: true }); }
}

test("equivalence runner consumes the exact promoted qualification artifact and grades the genuine semantic corpus", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      const report = await workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, produced.artifactPath, produced.artifactSha256), fakeTransportForSemanticCases());
      assert.equal(report.providerId, "local");
      assert.deepEqual(report.counters, { passed: 3, failed: 0, uncertain: 0 });
      assert.deepEqual(report.cases, ["structural-review-1", "patch-proposal-1", "failure-triage-1"]);
      assert.equal(report.qualificationSha256, produced.artifact.semanticQualification.qualificationSha256);
      assert.match(report.qualificationSha256, /^[a-f0-9]{64}$/u);
      assert.match(report.corpusSha256, /^[a-f0-9]{64}$/u);
      for (const entry of report.perCase) {
        assert.equal(entry.gradePass, true);
        assert.equal(entry.status, "succeeded");
        assert.match(entry.ledgerSha256 ?? "", /^[a-f0-9]{64}$/u);
        const persisted = JSON.parse(await readFile(join(root, `${entry.ledgerRunId}.json`), "utf8"));
        assert.equal(persisted.status, "completed");
        assert.equal(entry.ledgerSha256, sha(persisted));
        assert.equal(entry.inputTokens, persisted.totals.inputTokens);
        assert.equal(entry.outputTokens, persisted.totals.outputTokens);
        assert.equal(entry.uncertain, persisted.totals.uncertain);
      }
      const serialized = JSON.stringify(report);
      assert.equal(serialized.includes("reasoning_content"), false);
      assert.equal(serialized.includes("temporary-test-key"), false);
      assert.equal(serialized.includes("PIXEL_K3_SMOKE_OK"), false);
      assert.equal(serialized.includes(root), false);
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence report binds an uncertain case to its unsettled persisted reconciliation ledger", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      const report = await workProviderEquivalenceTestSeam.runWithTransport(
        equivalenceArgv(root, produced.artifactPath, produced.artifactSha256),
        uncertainThenSemanticTransport(),
      );
      assert.deepEqual(report.counters, { passed: 2, failed: 0, uncertain: 1 });
      const entry = report.perCase.find((candidate) => candidate.status === "uncertain");
      assert.ok(entry, "the unsettled provider result must remain visible in the report");
      assert.equal(entry.gradePass, false);
      const persisted = JSON.parse(await readFile(join(root, `${entry.ledgerRunId}.json`), "utf8"));
      assert.equal(persisted.status, "uncertain");
      assert.equal(persisted.requests.length, 1);
      assert.equal(persisted.requests[0].state, "uncertain");
      assert.equal(entry.ledgerSha256, sha(persisted));
      assert.equal(entry.uncertain, 1);
      assert.equal(entry.uncertain, persisted.totals.uncertain);
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence runner fails closed on missing, tampered, and unqualified artifacts", async () => {
  const produced = await produceQualificationArtifact();
  try {
    const missing = join(produced.root, "does-not-exist.json");
    await withEquivRoot(async (root) => {
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, missing, produced.artifactSha256), fakeTransportForSemanticCases()), /read failed closed/u);
      // Tampering with the promoted semantic qualification breaks the artifact hash.
      const tampered = structuredClone(produced.artifact);
      tampered.semanticQualification.qualificationId = "workproviderqualification-tampered";
      await writeFile(join(produced.root, "tampered.json"), `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, join(produced.root, "tampered.json"), produced.artifactSha256), fakeTransportForSemanticCases()), /hash does not match/u);
      // An unqualified artifact never qualifies the equivalence run.
      const unqualified = structuredClone(produced.artifact);
      unqualified.status = "not-qualified"; unqualified.qualified = false;
      unqualified.artifactSha256 = sha(withoutArtifactSha(unqualified));
      await writeFile(join(produced.root, "unqualified.json"), `${JSON.stringify(unqualified)}\n`, { mode: 0o600 });
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, join(produced.root, "unqualified.json"), unqualified.artifactSha256), fakeTransportForSemanticCases()), /not qualified/u);
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence runner requires strict JSON and owner-private artifact custody", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      const duplicatePath = join(produced.root, "duplicate-key.json");
      const serialized = JSON.stringify(produced.artifact, null, 2);
      const duplicate = serialized.replace('"schemaVersion": 1,', '"schemaVersion": 1,\n  "schemaVersion": 1,');
      await writeFile(duplicatePath, `${duplicate}\n`, { mode: 0o600 });
      await assert.rejects(
        workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, duplicatePath, produced.artifactSha256), fakeTransportForSemanticCases()),
        /not strict JSON/u,
      );
      if (process.platform !== "win32") {
        await chmod(produced.artifactPath, 0o644);
        await assert.rejects(
          workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, produced.artifactPath, produced.artifactSha256), fakeTransportForSemanticCases()),
          /owner-private/u,
        );
      }
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence runner rejects a wrong lane, provider, model, or corpus provenance", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      const cases = [
        { field: "lane", value: "moonshot-kimi", match: /lane differs/u },
        { field: "providerId", value: "openai", match: /provider differs/u },
        { field: "model", value: "kimi-k3", match: /model differs/u },
        { field: "corpusVersion", value: "9", match: /corpus version differs/u },
        { field: "corpusSha256", value: "f".repeat(64), match: /corpus hash differs/u },
      ];
      for (const { field, value, match } of cases) {
        const doc = structuredClone(produced.artifact);
        doc[field] = value;
        const path = join(produced.root, `${field}.json`);
        doc.artifactSha256 = sha(withoutArtifactSha(doc));
        await writeFile(path, `${JSON.stringify(doc)}\n`, { mode: 0o600 });
        await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, path, doc.artifactSha256), fakeTransportForSemanticCases()), match, `expected ${field} rejection`);
      }
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence runner rejects a self-minted envelope not bound to the qualified trial", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      // A self-minted envelope (the historical harness bug) fabricates a schema-consistent
      // semantic qualification that claims the neutral corpus hash as its own evidence instead of
      // a real qualified-trial attestation. The equivalence runner must reject such an envelope.
      const selfMinted = structuredClone(produced.artifact);
      const fabricated = structuredClone(selfMinted.semanticQualification);
      fabricated.evidenceSha256 = selfMinted.corpusSha256;
      fabricated.qualificationSha256 = sha(withoutQualificationSha(fabricated));
      selfMinted.semanticQualification = fabricated;
      selfMinted.qualificationSha256 = fabricated.qualificationSha256;
      selfMinted.evidenceSha256 = selfMinted.corpusSha256;
      selfMinted.artifactSha256 = sha(withoutArtifactSha(selfMinted));
      const path = join(produced.root, "self-minted.json");
      await writeFile(path, `${JSON.stringify(selfMinted)}\n`, { mode: 0o600 });
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(equivalenceArgv(root, path, selfMinted.artifactSha256), fakeTransportForSemanticCases()), /evidence is self-minted/u);
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

test("equivalence runner fails closed on invalid arguments and never injects a transport from argv", async () => {
  const produced = await produceQualificationArtifact();
  try {
    await withEquivRoot(async (root) => {
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(["--lane", "bogus", "--ledger-root", root, "--qualification-report", produced.artifactPath, "--qualification-sha256", produced.artifactSha256], fakeTransportForSemanticCases()), /lane/u);
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(["--lane", "local-only", "--ledger-root", root, "--qualification-sha256", produced.artifactSha256], fakeTransportForSemanticCases()), /requires --qualification-report and --qualification-sha256/u);
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(["--lane", "local-only", "--ledger-root", root, "--qualification-report", produced.artifactPath, "--qualification-sha256", "not-a-hash"], fakeTransportForSemanticCases()), /qualification SHA-256 is invalid/u);
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport(["--lane", "local-only", "--ledger-root", root, "--qualification-report", produced.artifactPath, "--qualification-sha256", produced.artifactSha256, "--transport", "x"], fakeTransportForSemanticCases()), /arguments are invalid/u);
      await assert.rejects(workProviderEquivalenceTestSeam.runWithTransport([...equivalenceArgv(root, produced.artifactPath, produced.artifactSha256), "--report", produced.artifactPath], fakeTransportForSemanticCases()), /paths must differ/u);
    });
  } finally { await rm(produced.root, { recursive: true, force: true }); }
});

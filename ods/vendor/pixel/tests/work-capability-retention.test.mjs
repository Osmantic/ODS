import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";
import {
  buildCapabilityRetentionEvidence, CapabilityRetentionError, capabilityRetentionContract,
  validateCapabilityCorpus, validateCapabilityRetentionEvidence,
} from "../scripts/work-capability-retention.mjs";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const script = join(repo, "scripts", "work-capability-retention.mjs");
const commit = "a".repeat(40), tree = "b".repeat(40), sourceArchiveSha256 = "f".repeat(64), executorSha = "c".repeat(64), contractSha = "d".repeat(64);
const runnerImageDigest = `sha256:${"e".repeat(64)}`;

async function inputs() {
  const corpus = JSON.parse(await readFile(join(repo, "security-evals", "assurance", "builder-capability-corpus-v1.json"), "utf8"));
  const schema = JSON.parse(await readFile(join(repo, "schemas", "work-capability-retention-evidence-v1.schema.json"), "utf8"));
  const results = corpus.tasks.map(({ id }) => ({ id, status: "pass" }));
  const expected = { sourceCommit: commit, sourceTree: tree, sourceArchiveSha256, executorVersion: "17.2.12", executorArtifactSha256: executorSha, runnerImageDigest, builderContractSha256: contractSha };
  return { corpus, schema, results, expected };
}

function build(value, overrides = {}) {
  return buildCapabilityRetentionEvidence({
    ...value.expected, corpus: value.corpus, baselineResults: value.results, containedResults: value.results,
    startedAt: "2026-08-11T12:00:00Z", finishedAt: "2026-08-11T12:01:00Z", ...overrides,
  });
}

test("paired evidence proves one continuously authorized objective retains the complete OMP coding surface", async () => {
  const value = await inputs(), evidence = build(value);
  assert.deepEqual(validateCapabilityCorpus(value.corpus), []);
  assert.deepEqual(validateJsonSchema(evidence, value.schema), []);
  assert.deepEqual(validateCapabilityRetentionEvidence(evidence, value.corpus, value.expected), []);
  assert.equal(evidence.comparison.retentionPermille, 1000);
  assert.equal(evidence.comparison.midJobPrompts, 0);
  assert.equal(evidence.baseline.promptCount, 1);
  assert.equal(evidence.contained.promptCount, 1);
  assert.deepEqual(capabilityRetentionContract.tasks.map(({ id }) => id), value.corpus.tasks.map(({ id }) => id));

  const optionalFailure = value.results.map((entry) => entry.id === "file-discovery" ? { ...entry, status: "fail" } : entry);
  const threshold = build(value, { containedResults: optionalFailure });
  assert.equal(threshold.comparison.retentionPermille, 909);
  assert.deepEqual(validateJsonSchema(threshold, value.schema), []);
  assert.deepEqual(validateCapabilityRetentionEvidence(threshold, value.corpus, value.expected), []);
});

test("retention accounting rejects denominator games, lost coding classes, prompts, task drift, and source drift", async () => {
  const value = await inputs(), evidence = build(value);
  const hostile = [
    [(copy) => { copy.baseline.results[0].status = "fail"; copy.baseline.passed -= 1; }, /baseline/u],
    [(copy) => { copy.comparison.eligibleBaselinePasses = 10; copy.comparison.retentionPermille = 1000; }, /fabricated/u],
    [(copy) => { copy.contained.results[9].status = "fail"; copy.contained.passed -= 1; copy.comparison.retainedPasses -= 1; copy.comparison.retentionPermille = 909; }, /ordinary coding capability/u],
    [(copy) => { copy.contained.midJobPrompts = 1; copy.comparison.midJobPrompts = 1; }, /mid-job/u],
    [(copy) => { copy.contained.results[1].id = copy.contained.results[0].id; }, /duplicated/u],
    [(copy) => { copy.sourceTree = "f".repeat(40); }, /independently selected/u],
    [(copy) => { copy.sourceArchiveSha256 = "0".repeat(64); }, /independently selected/u],
    [(copy) => { copy.executor.artifactSha256 = "f".repeat(64); }, /independently selected/u],
    [(copy) => { copy.runner.imageDigest = `sha256:${"f".repeat(64)}`; }, /independently selected/u],
  ];
  for (const [mutate, pattern] of hostile) {
    const copy = structuredClone(evidence); mutate(copy);
    assert.match(validateCapabilityRetentionEvidence(copy, value.corpus, value.expected).join("\n"), pattern);
  }
  const zero = value.results.map((entry) => ({ ...entry, status: "fail" }));
  assert.throws(() => build(value, { baselineResults: zero, containedResults: zero }), CapabilityRetentionError);
});

test("schema and CLI reject missing verification, private content, replayed bindings, and malformed corpus", async (t) => {
  const value = await inputs(), evidence = build(value);
  const missingVerification = structuredClone(evidence); delete missingVerification.contained.results[0].verification;
  assert.ok(validateJsonSchema(missingVerification, value.schema).length > 0);
  const leaked = structuredClone(evidence); leaked.contained.results[0].privatePath = "C:/private/client";
  assert.ok(validateJsonSchema(leaked, value.schema).length > 0);
  const corpus = structuredClone(value.corpus); corpus.tasks.reverse();
  assert.match(validateCapabilityCorpus(corpus)[0], /tasks/u);

  const root = await mkdtemp(join(tmpdir(), "pixel-capability-retention-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const path = join(root, "evidence.json");
  await writeFile(path, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  const args = [
    script, "validate", path, "--source-commit", commit, "--source-tree", tree, "--source-archive-sha256", sourceArchiveSha256,
    "--executor-version", "17.2.12", "--executor-sha256", executorSha,
    "--runner-image-digest", runnerImageDigest, "--builder-contract-sha256", contractSha,
  ];
  const result = await run(process.execPath, args, { cwd: repo, timeout: 10000 });
  assert.equal(JSON.parse(result.stdout).retentionPermille, 1000);
  await assert.rejects(run(process.execPath, [...args.slice(0, -1), "f".repeat(64)], { cwd: repo, timeout: 10000 }), /independently selected/u);
  assert.equal(JSON.parse(await readFile(path, "utf8")).sourceCommit, commit);
});

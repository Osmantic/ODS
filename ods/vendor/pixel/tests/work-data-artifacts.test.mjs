import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  link, mkdir, mkdtemp, readFile, rm, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  dataArtifactContract, exportDataArtifacts, finalizeDataReport, inventoryDataReplay, verifyDataReplay, writeDataReplayInventory,
} from "../deploy/work-runner/data-artifacts.mjs";
import {
  validateWorkDataArtifactManifest, validateWorkDataReport, validateWorkDataVerification,
} from "../scripts/lib/work-contract.mjs";

const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

const limits = Object.freeze({
  maxFiles: 8, maxBytes: 4 * 1024 * 1024,
  allowedFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"],
});

function binding() {
  return {
    jobId: "work-1786366800000-abcdef123456", claimId: "workclaim-1786366800000-abcdef123456",
    planSha256: "a".repeat(64), workspaceSha256: "b".repeat(64), dataClassification: "confidential",
    datasets: [{ datasetId: "sales", format: "csv", contentSha256: "c".repeat(64), bytes: 37 }],
  };
}

function proposal() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-data-report-proposal-v1.schema.json", schemaVersion: 1,
    title: "Revenue result", summary: "The derived local result is attached.", methodology: "The deterministic recipe grouped the immutable CSV by region.",
    artifacts: [
      { path: "artifacts/summary.csv", purpose: "Reproducible grouped totals" },
      { path: "artifacts/summary.md", purpose: "Human-readable interpretation" },
    ],
    findings: [{ statement: "The fixture contains two regional totals.", evidencePaths: ["artifacts/summary.csv", "artifacts/summary.md"] }],
    limitations: "The fixture is synthetic and does not establish business truth.", dataClassification: "confidential",
    privateDataIncluded: true, externalEffects: false, authority: { ...dataArtifactContract.authority },
    boundary: "Untrusted local Data Lab proposal only. It grants no truth, verification, publication, action, policy, or completion authority and must be bound to exact artifacts and independently replayed by Pixel.",
  };
}

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-work-data-artifacts-"));
  const workspace = join(root, "workspace");
  const output = join(root, "output");
  const replay = join(root, "replay");
  await Promise.all([workspace, output, replay].map((path) => mkdir(path, { mode: 0o700 })));
  await mkdir(join(workspace, "artifacts"), { mode: 0o700 });
  await mkdir(join(replay, "artifacts"), { mode: 0o700 });
  const recipe = "import argparse\n# deterministic fixture recipe\n";
  const csv = "region,revenue\neast,37\nwest,42\n";
  const markdown = "# Summary\n\nTwo deterministic regional totals.\n";
  await writeFile(join(workspace, "recipe.py"), recipe, { mode: 0o600 });
  await writeFile(join(workspace, "artifacts", "summary.csv"), csv, { mode: 0o600 });
  await writeFile(join(workspace, "artifacts", "summary.md"), markdown, { mode: 0o600 });
  await writeFile(join(replay, "artifacts", "summary.csv"), csv, { mode: 0o600 });
  await writeFile(join(replay, "artifacts", "summary.md"), markdown, { mode: 0o600 });
  return { root, workspace, output, replay, recipe, csv, markdown };
}

test("Data Lab report rejects a private-data attestation inconsistent with its plan classification", async () => {
  const value = await fixture();
  try {
    const exported = await exportDataArtifacts(value.workspace, value.output, binding(), limits, {
      now: new Date("2026-08-10T13:00:01Z"), suffix: "123456abcdef",
    });
    // binding() is confidential, so privateDataIncluded MUST be true. A worker attesting false is a
    // false private-data attestation and must be rejected at the trusted finalization boundary
    // (matching the scout candidate check), not copied through unchecked.
    const lying = { ...proposal(), privateDataIncluded: false };
    assert.throws(() => finalizeDataReport(lying, exported.manifest, exported.evidence.sha256, binding(), {
      now: new Date("2026-08-10T13:00:02Z"), suffix: "abcdef123456",
    }), /private-data attestation/);
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("Data Lab exports only exact derived artifacts and finalizes their local report", async () => {
  const value = await fixture();
  try {
    const exported = await exportDataArtifacts(value.workspace, value.output, binding(), limits, {
      now: new Date("2026-08-10T13:00:01Z"), suffix: "123456abcdef",
    });
    assert.deepEqual(validateWorkDataArtifactManifest(exported.manifest), []);
    assert.deepEqual(exported.manifest.artifacts.map((artifact) => artifact.path), ["artifacts/summary.csv", "artifacts/summary.md"]);
    assert.equal(exported.manifest.artifacts[0].sha256, sha(value.csv));
    assert.equal(exported.manifest.recipe.sha256, sha(value.recipe));
    assert.equal(await readFile(join(value.output, "artifacts", "summary.md"), "utf8"), value.markdown);
    assert.equal(exported.evidence.sha256, sha(await readFile(exported.evidence.path)));
    const report = finalizeDataReport(proposal(), exported.manifest, exported.evidence.sha256, binding(), {
      now: new Date("2026-08-10T13:00:02Z"), suffix: "abcdef123456",
    });
    assert.deepEqual(validateWorkDataReport(report), []);
    assert.equal(report.recipeSha256, sha(value.recipe));
    assert.equal(report.findings[0].evidence[0].sha256, sha(value.csv));
    assert.equal(report.authority.publish, false);
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("Data Lab replay verification requires a byte-for-byte fresh networkless reproduction", async () => {
  const value = await fixture();
  try {
    const exported = await exportDataArtifacts(value.workspace, value.output, binding(), limits, {
      now: new Date("2026-08-10T13:00:01Z"), suffix: "123456abcdef",
    });
    const replay = await inventoryDataReplay(join(value.replay, "artifacts"), limits);
    const passed = verifyDataReplay(exported.manifest, exported.evidence.sha256, replay, {
      recipeExitCode: 0, timedOut: false, outputLimitExceeded: false,
    }, binding(), { now: new Date("2026-08-10T13:00:03Z"), suffix: "abcdef123456" });
    assert.deepEqual(validateWorkDataVerification(passed), []);
    assert.equal(passed.status, "exact-replay-pass");
    assert.equal(passed.semanticAccuracyVerified, false);

    await writeFile(join(value.replay, "artifacts", "unexpected.json"), "{}\n", { mode: 0o600 });
    const withUnexpected = await inventoryDataReplay(join(value.replay, "artifacts"), limits);
    const unexpected = verifyDataReplay(exported.manifest, exported.evidence.sha256, withUnexpected, {
      recipeExitCode: 0, timedOut: false, outputLimitExceeded: false,
    }, binding(), { now: new Date("2026-08-10T13:00:04Z"), suffix: "111111111111" });
    assert.deepEqual(validateWorkDataVerification(unexpected), []);
    assert.equal(unexpected.status, "fail");
    assert.equal(unexpected.runtime.unexpectedArtifacts, 1);

    await rm(join(value.replay, "artifacts", "unexpected.json"));
    await writeFile(join(value.replay, "artifacts", "summary.csv"), "region,revenue\neast,99\n", { mode: 0o600 });
    const changed = await inventoryDataReplay(join(value.replay, "artifacts"), limits);
    const failed = verifyDataReplay(exported.manifest, exported.evidence.sha256, changed, {
      recipeExitCode: 0, timedOut: false, outputLimitExceeded: false,
    }, binding(), { now: new Date("2026-08-10T13:00:05Z"), suffix: "222222222222" });
    assert.deepEqual(validateWorkDataVerification(failed), []);
    assert.equal(failed.status, "fail");
    assert.equal(failed.artifacts[0].status, "different");
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("Data Lab artifact boundary rejects undeclared formats, hard links, partial proposals, and reused outputs", async () => {
  const unapproved = await fixture();
  try {
    await writeFile(join(unapproved.workspace, "artifacts", "payload.exe"), "not executable", { mode: 0o600 });
    await assert.rejects(exportDataArtifacts(unapproved.workspace, unapproved.output, binding(), limits), /format is not allowed/);
  } finally {
    await rm(unapproved.root, { recursive: true, force: true });
  }

  const linked = await fixture();
  try {
    await link(join(linked.workspace, "artifacts", "summary.csv"), join(linked.workspace, "artifacts", "copy.csv"));
    await assert.rejects(exportDataArtifacts(linked.workspace, linked.output, binding(), limits), /hard-linked|single-link/);
  } finally {
    await rm(linked.root, { recursive: true, force: true });
  }

  const partial = await fixture();
  try {
    const exported = await exportDataArtifacts(partial.workspace, partial.output, binding(), limits, {
      now: new Date("2026-08-10T13:00:01Z"), suffix: "123456abcdef",
    });
    const omitted = proposal();
    omitted.artifacts.pop();
    omitted.findings[0].evidencePaths.pop();
    assert.throws(() => finalizeDataReport(omitted, exported.manifest, exported.evidence.sha256, binding()), /every derived artifact/);
    const report = finalizeDataReport(proposal(), exported.manifest, exported.evidence.sha256, binding(), {
      now: new Date("2026-08-10T13:00:02Z"), suffix: "abcdef123456",
    });
    report.artifacts[0].kind = "visualization";
    assert.match(validateWorkDataReport(report).join("; "), /format, kind, and media type disagree/);
    await assert.rejects(exportDataArtifacts(partial.workspace, partial.output, binding(), limits), /must be empty/);
  } finally {
    await rm(partial.root, { recursive: true, force: true });
  }
});

test("Data Lab trusted replay inventory is immutable and content-addressed", async () => {
  const value = await fixture();
  try {
    const written = await writeDataReplayInventory(join(value.replay, "artifacts"), value.output, limits);
    assert.equal(written.artifacts.length, 2);
    assert.equal(written.evidence.sha256, sha(await readFile(written.evidence.path)));
    await assert.rejects(writeDataReplayInventory(join(value.replay, "artifacts"), value.output, limits), /already exists/);
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

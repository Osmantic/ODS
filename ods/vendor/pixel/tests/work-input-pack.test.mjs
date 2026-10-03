import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, symlink, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalDraftCommand } from "../deploy/work-controller/goal-draft-cli.mjs";
import { InputPackCliError, runInputPackCommand } from "../deploy/work-controller/input-pack-cli.mjs";
import { materializeUstar } from "../deploy/work-runner/safe-tar.mjs";
import {
  canonical, validateWorkInputCatalog, validateWorkInputPack, validateWorkInputSelection, validateWorkJob,
  validateWorkGoalBrief,
} from "../scripts/lib/work-contract.mjs";

const now = new Date("2026-08-11T06:00:00.000Z");
const nonce = "fedcba9876543210fedcba9876543210";
const SELECTION_BOUNDARY = "Explicit owner-selected local directories only. Packing may read exactly those directory trees into inert content-addressed snapshots but grants no execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const BRIEF_BOUNDARY = "Owner-authored plain-language goal structure and selected local input references only. Drafting derives bounded job proposals from private policy but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

test("guided data examples satisfy the exact selection and goal contracts", async () => {
  const selected = JSON.parse(await readFile(new URL("../deploy/work-controller/input-selection-data.example.json", import.meta.url), "utf8"));
  const brief = JSON.parse(await readFile(new URL("../deploy/work-controller/goal-brief-data.example.json", import.meta.url), "utf8"));
  assert.deepEqual(validateWorkInputSelection(selected), []);
  assert.deepEqual(validateWorkGoalBrief(brief), []);
});

function limits(overrides = {}) {
  return { maxEntries: 100, maxFileBytes: 1048576, maxTotalBytes: 4194304, maxArchiveBytes: 8388608, ...overrides };
}

function selection(sourceDirectory, overrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-input-selection-v1.schema.json", schemaVersion: 1,
    selectionId: "inputselection-1786428000000-abcdef123456", createdAt: "2026-08-11T06:00:00.000Z",
    entries: [{ id: "project", kind: "repository-snapshot", classification: "internal", sourceDirectory, limits: limits() }],
    boundary: SELECTION_BOUNDARY, ...overrides,
  };
}

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-input-pack-")), source = join(root, "source");
  await mkdir(join(source, "empty"), { recursive: true, mode: 0o700 });
  await mkdir(join(source, "sub"), { mode: 0o700 });
  await Promise.all([
    writeFile(join(source, "README.md"), "hello pixel\n", { mode: 0o600 }),
    writeFile(join(source, "sub", "data.txt"), "bounded data\n", { mode: 0o600 }),
    writeFile(join(source, ".env"), "UNTRUSTED=value\n", { mode: 0o600 }),
    writeFile(join(source, "AGENTS.md"), "untrusted instructions\n", { mode: 0o600 }),
  ]);
  return { root, source };
}

async function writeSelection(value, selected = selection(value.source), name = "selection.json") {
  const path = join(value.root, name);
  await writeFile(path, `${JSON.stringify(selected)}\n`, { mode: 0o600 });
  return { path, selected };
}

async function privatePolicy(root) {
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.profiles.scout.enabled = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  const path = join(root, "policy.json");
  await writeFile(path, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  return path;
}

function enableDataLab(policy) {
  policy.profiles.dataLab = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "dataset", "document", "visualization"], minimumMemoryMiB: 1536,
    maxBudgets: {
      maxRuntimeSeconds: 3600, maxIterations: 24, maxToolCalls: 2000, maxConcurrentSubagents: 1,
      maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4,
      maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 268435456,
      maxNetworkBytes: 1073741824, maxFailures: 5, noProgressLimit: 3,
    },
    runtime: {
      contract: "pixel-local-data-runtime-v1",
      engines: {
        duckdb: { version: "1.5.5", entrypoint: "/usr/bin/python3" },
        polars: { version: "1.43.2", entrypoint: "/usr/bin/python3" },
        python: { version: "3.11.2", entrypoint: "/usr/bin/python3" },
        sqlite: { version: "3.40.1", entrypoint: "/usr/bin/sqlite3" },
      },
    },
    maxData: {
      maxDatasets: 8, maxDatasetBytes: 1048576, maxArtifactFiles: 16, maxArtifactBytes: 134217728,
      allowedInputFormats: ["csv", "json", "jsonl", "parquet", "sqlite"],
      allowedArtifactFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"],
    },
  };
  return policy;
}

test("input packing produces a deterministic strict ustar bundle with control files inert", async () => {
  const value = await fixture(), source = await writeSelection(value);
  const firstPath = join(value.root, "client-acme-secret"), secondPath = join(value.root, "pack-two");
  const firstReceipt = await runInputPackCommand(["--selection", source.path, "--output", firstPath], { now, nonce });
  const secondReceipt = await runInputPackCommand(["--selection", source.path, "--output", secondPath], { now, nonce });
  assert.deepEqual(validateWorkInputSelection(source.selected), []);
  assert.equal(firstReceipt.packSha256, secondReceipt.packSha256);
  assert.equal(firstReceipt.catalogSha256, secondReceipt.catalogSha256);
  assert.equal(firstReceipt.inputs, 1);
  assert.ok(Object.values(firstReceipt.authority).every((entry) => entry === false));
  assert.doesNotMatch(JSON.stringify(firstReceipt), /README\.md|AGENTS\.md|UNTRUSTED=value|pixel-input-pack-|client-acme-secret/u);
  const firstCatalog = JSON.parse(await readFile(join(firstPath, "input-catalog.json"), "utf8"));
  const secondCatalog = JSON.parse(await readFile(join(secondPath, "input-catalog.json"), "utf8"));
  const manifest = JSON.parse(await readFile(join(firstPath, "input-pack.json"), "utf8"));
  assert.deepEqual(validateWorkInputCatalog(firstCatalog), []);
  assert.deepEqual(validateWorkInputPack(manifest), []);
  assert.equal(canonical(firstCatalog), canonical(secondCatalog));
  assert.equal(manifest.catalogSha256, sha(canonical(firstCatalog)));
  assert.equal(manifest.entries[0].archiveSha256, firstCatalog.entries[0].contentSha256);
  assert.equal(manifest.entries[0].archiveBytes, firstCatalog.entries[0].bytes);
  const objectPath = join(firstPath, "objects", firstCatalog.entries[0].objectName), extracted = join(value.root, "extracted");
  const materialized = await materializeUstar(objectPath, extracted, {
    expectedSha256: firstCatalog.entries[0].contentSha256, maxArchiveBytes: 8388608,
    maxExtractedBytes: 4194304, maxEntries: 100, maxFileBytes: 1048576,
  });
  assert.equal(materialized.extractedBytes, Buffer.byteLength("hello pixel\nbounded data\nUNTRUSTED=value\nuntrusted instructions\n"));
  assert.equal(await readFile(join(extracted, "README.md"), "utf8"), "hello pixel\n");
  assert.equal(await readFile(join(extracted, "sub", "data.txt"), "utf8"), "bounded data\n");
  assert.equal(await readFile(join(extracted, "__pixel_inert__", ".env"), "utf8"), "UNTRUSTED=value\n");
  assert.equal(await readFile(join(extracted, "__pixel_inert__", "AGENTS.md"), "utf8"), "untrusted instructions\n");
});

test("an admitted local folder feeds the guided long-goal draft without manual hashes", async () => {
  const value = await fixture(), source = await writeSelection(value), packPath = join(value.root, "pack");
  await runInputPackCommand(["--selection", source.path, "--output", packPath], { now, nonce });
  const brief = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-brief-v1.schema.json", schemaVersion: 1,
    objective: "Inspect the selected local project.", dataClassification: "internal",
    milestones: [{
      milestoneId: "assess", kind: "inspect", objective: "Inspect the project safely.",
      doneWhen: ["Return an evidence-backed finding report"], dependsOn: [], inputIds: ["project"], effort: "quick",
    }], boundary: BRIEF_BOUNDARY,
  };
  const briefPath = join(value.root, "brief.json"), policyPath = await privatePolicy(value.root);
  await writeFile(briefPath, `${JSON.stringify(brief)}\n`, { mode: 0o600 });
  const draftPath = join(value.root, "draft");
  const receipt = await runGoalDraftCommand([
    "--brief", briefPath, "--policy", policyPath, "--input-catalog", join(packPath, "input-catalog.json"),
    "--object-store", join(packPath, "objects"), "--output", draftPath,
  ], { now: new Date(now.getTime() + 1000), nonce: "0123456789abcdef0123456789abcdef" });
  assert.equal(receipt.status, "drafted");
  const jobs = JSON.parse(await readFile(join(draftPath, "jobs.json"), "utf8"));
  assert.equal(jobs[0].inputs[0].contentSha256, JSON.parse(await readFile(join(packPath, "input-catalog.json"), "utf8")).entries[0].contentSha256);
  assert.equal(jobs[0].requestedCapabilities.filesystem, "read-only-workspace");
  assert.equal(jobs[0].requestedCapabilities.modelRoute, "local-only");
});

test("dataset admission derives exact raw-file hashes and a replay-verified guided Data Lab job", async () => {
  const value = await fixture(), sales = Buffer.from("region,revenue\nwest,42\n", "utf8");
  await writeFile(join(value.source, "sales.csv"), sales, { mode: 0o600 });
  const selected = selection(value.source);
  selected.entries[0] = {
    ...selected.entries[0], kind: "dataset",
    datasets: [{ datasetId: "sales", relativePath: "sales.csv", format: "csv" }],
  };
  const source = await writeSelection(value, selected), packPath = join(value.root, "data-pack");
  await runInputPackCommand(["--selection", source.path, "--output", packPath], { now, nonce });
  const catalog = JSON.parse(await readFile(join(packPath, "input-catalog.json"), "utf8"));
  assert.deepEqual(catalog.entries[0].datasets, [{
    datasetId: "sales", relativePath: "sales.csv", format: "csv", contentSha256: sha(sales), bytes: sales.length,
  }]);
  const manifest = JSON.parse(await readFile(join(packPath, "input-pack.json"), "utf8"));
  assert.equal(manifest.totals.datasets, 1);
  assert.deepEqual(manifest.entries[0].datasets, catalog.entries[0].datasets);

  const policyPath = await privatePolicy(value.root), policy = JSON.parse(await readFile(policyPath, "utf8"));
  enableDataLab(policy);
  await writeFile(policyPath, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  const brief = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-brief-v1.schema.json", schemaVersion: 1,
    objective: "Analyze the selected private revenue dataset reproducibly.", dataClassification: "internal",
    milestones: [{
      milestoneId: "analyze", kind: "analyze-data", objective: "Find the key revenue pattern.",
      doneWhen: ["Return a replay-verified finding and derived dataset"], dependsOn: [], inputIds: ["project"], effort: "standard",
    }], boundary: BRIEF_BOUNDARY,
  };
  const briefPath = join(value.root, "data-brief.json"), draftPath = join(value.root, "data-draft");
  await writeFile(briefPath, `${JSON.stringify(brief)}\n`, { mode: 0o600 });
  await runGoalDraftCommand([
    "--brief", briefPath, "--policy", policyPath, "--input-catalog", join(packPath, "input-catalog.json"),
    "--object-store", join(packPath, "objects"), "--output", draftPath,
  ], { now: new Date(now.getTime() + 1000), nonce: "0123456789abcdef0123456789abcdef" });
  const jobs = JSON.parse(await readFile(join(draftPath, "jobs.json"), "utf8")), review = JSON.parse(await readFile(join(draftPath, "goal-draft.json"), "utf8"));
  assert.equal(jobs[0].profile, "data-lab");
  assert.deepEqual(validateWorkJob(jobs[0]), []);
  assert.deepEqual(jobs[0].data.datasets, [{
    datasetId: "sales", inputId: "project", relativePath: "sales.csv", format: "csv", contentSha256: sha(sales), maxBytes: sales.length,
  }]);
  assert.equal(jobs[0].data.replayVerification, true);
  assert.equal(jobs[0].data.maxArtifactFiles, 16, "standard effort is clamped to private policy");
  assert.equal(jobs[0].data.maxArtifactBytes, 134217728, "artifact bytes are clamped to private policy");
  assert.equal(review.milestones[0].verification, "isolated-exact-replay-and-semantic-review");
  const childManifest = JSON.parse(await readFile(join(draftPath, "input-manifests", `${jobs[0].jobId}.json`), "utf8"));
  assert.deepEqual(Object.keys(childManifest.entries[0]).sort(), ["bytes", "classification", "contentSha256", "id", "kind", "mountMode", "objectName"]);
});

test("packing deduplicates identical selected trees without weakening logical input bindings", async () => {
  const value = await fixture(), selected = selection(value.source);
  selected.entries = [
    { ...selected.entries[0], id: "alpha" },
    { ...selected.entries[0], id: "beta", kind: "context" },
  ];
  const source = await writeSelection(value, selected), output = join(value.root, "pack");
  const receipt = await runInputPackCommand(["--selection", source.path, "--output", output], { now, nonce });
  assert.equal(receipt.inputs, 2);
  assert.equal(receipt.objects, 1);
  const catalog = JSON.parse(await readFile(join(output, "input-catalog.json"), "utf8"));
  assert.equal(catalog.entries[0].objectName, catalog.entries[1].objectName);
  assert.deepEqual(await readdir(join(output, "objects")), [catalog.entries[0].objectName]);
  const manifest = JSON.parse(await readFile(join(output, "input-pack.json"), "utf8"));
  assert.equal(manifest.totals.archiveBytes, manifest.entries[0].archiveBytes);
  assert.equal(manifest.totals.extractedBytes, manifest.entries[0].extractedBytes * 2);
});

test("input packing rejects links, resource abuse, unsafe overlap, and projection collisions before publication", async (context) => {
  const oversized = await fixture(), oversizedSelection = selection(oversized.source);
  oversizedSelection.entries[0].limits.maxEntries = 1;
  const oversizedSource = await writeSelection(oversized, oversizedSelection);
  await assert.rejects(() => runInputPackCommand(["--selection", oversizedSource.path, "--output", join(oversized.root, "pack")], { now, nonce }), /entry ceiling/);
  assert.equal(await lstat(join(oversized.root, "pack")).catch(() => null), null);

  const overlap = await fixture(), overlapSource = await writeSelection(overlap);
  await assert.rejects(() => runInputPackCommand(["--selection", overlapSource.path, "--output", join(overlap.source, "pack")], { now, nonce }), /contains the output parent/);

  const collision = await fixture();
  await mkdir(join(collision.source, ".git"), { mode: 0o700 });
  await mkdir(join(collision.source, "__pixel_inert__", ".git"), { recursive: true, mode: 0o700 });
  await writeFile(join(collision.source, ".git", "config"), "inert\n", { mode: 0o600 });
  await writeFile(join(collision.source, "__pixel_inert__", ".git", "config"), "collision\n", { mode: 0o600 });
  const collisionSource = await writeSelection(collision);
  await assert.rejects(() => runInputPackCommand(["--selection", collisionSource.path, "--output", join(collision.root, "pack")], { now, nonce }), /safe input projection.*duplicate|safe input projection.*collision/);

  const hardLinked = await fixture(), outside = join(hardLinked.root, "outside.txt");
  await writeFile(outside, "outside\n", { mode: 0o600 });
  await link(outside, join(hardLinked.source, "linked.txt"));
  const linkedSource = await writeSelection(hardLinked);
  await assert.rejects(() => runInputPackCommand(["--selection", linkedSource.path, "--output", join(hardLinked.root, "pack")], { now, nonce }), /hard-linked/);

  if (process.platform === "win32") return context.skip("symlink creation is not generally available on Windows");
  const symlinked = await fixture(), target = join(symlinked.root, "target.txt");
  await writeFile(target, "target\n", { mode: 0o600 });
  await symlink(target, join(symlinked.source, "symlink.txt"));
  const symlinkedSource = await writeSelection(symlinked);
  await assert.rejects(() => runInputPackCommand(["--selection", symlinkedSource.path, "--output", join(symlinked.root, "pack")], { now, nonce }), /link, device, socket/);
  const publicSelection = await writeSelection(symlinked, selection(symlinked.source), "public-selection.json");
  await chmod(publicSelection.path, 0o644);
  await assert.rejects(() => runInputPackCommand(["--selection", publicSelection.path, "--output", join(symlinked.root, "public-pack")], { now, nonce }), /not owner-private/);
});

test("concurrent input packers publish one complete immutable bundle", async () => {
  const value = await fixture(), source = await writeSelection(value), output = join(value.root, "pack");
  const outcomes = await Promise.allSettled(Array.from({ length: 8 }, () => runInputPackCommand(["--selection", source.path, "--output", output], { now, nonce })));
  assert.equal(outcomes.filter((entry) => entry.status === "fulfilled").length, 1);
  assert.equal(outcomes.filter((entry) => entry.status === "rejected").length, 7);
  assert.ok(outcomes.filter((entry) => entry.status === "rejected").every((entry) => /already exists/.test(entry.reason.message)));
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-input-pack-")), []);
  const manifest = JSON.parse(await readFile(join(output, "input-pack.json"), "utf8"));
  assert.deepEqual(validateWorkInputPack(manifest), []);
});

test("input packing refuses unknown fields, stale identities, and argument smuggling", async () => {
  const value = await fixture(), selected = selection(value.source);
  selected.entries[0].hostAccess = true;
  const invalid = await writeSelection(value, selected);
  await assert.rejects(() => runInputPackCommand(["--selection", invalid.path, "--output", join(value.root, "pack")], { now, nonce }), InputPackCliError);
  const staleIdentity = selection(value.source);
  staleIdentity.selectionId = "inputselection-1786428000001-abcdef123456";
  assert.ok(validateWorkInputSelection(staleIdentity).some((error) => error.includes("identity time")));
  await assert.rejects(() => runInputPackCommand(["--selection", invalid.path, "--output", join(value.root, "pack"), "--confirm", "yes"], { now, nonce }), /Usage/);

  const wrongFormat = selection(value.source);
  wrongFormat.entries[0] = { ...wrongFormat.entries[0], kind: "dataset", datasets: [{ datasetId: "sales", relativePath: "sales.json", format: "csv" }] };
  assert.ok(validateWorkInputSelection(wrongFormat).some((error) => error.includes("format differs")));
  await mkdir(join(value.source, ".git"), { mode: 0o700 });
  await writeFile(join(value.source, ".git", "data.json"), "{}\n", { mode: 0o600 });
  const inertDataset = selection(value.source);
  inertDataset.entries[0] = { ...inertDataset.entries[0], kind: "dataset", datasets: [{ datasetId: "secret", relativePath: ".git/data.json", format: "json" }] };
  const inertPath = await writeSelection(value, inertDataset, "inert-dataset.json");
  await assert.rejects(() => runInputPackCommand(["--selection", inertPath.path, "--output", join(value.root, "inert-pack")], { now, nonce }), /inert control path/);
  const missingDataset = selection(value.source);
  missingDataset.entries[0] = { ...missingDataset.entries[0], kind: "dataset", datasets: [{ datasetId: "missing", relativePath: "missing.csv", format: "csv" }] };
  const missingPath = await writeSelection(value, missingDataset, "missing-dataset.json");
  await assert.rejects(() => runInputPackCommand(["--selection", missingPath.path, "--output", join(value.root, "missing-pack")], { now, nonce }), /missing, empty, or not a regular/);
});

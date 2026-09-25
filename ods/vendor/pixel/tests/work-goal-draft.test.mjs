import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileGuidedGoal, GoalDraftCliError, runGoalDraftCommand } from "../deploy/work-controller/goal-draft-cli.mjs";
import { runGoalPrepareCommand } from "../deploy/work-controller/goal-prepare-cli.mjs";
import {
  canonical, validateWorkGoalBrief, validateWorkGoalDeclaration, validateWorkGoalDraft,
  validateWorkInputCatalog, validateWorkJob,
} from "../scripts/lib/work-contract.mjs";

const now = new Date("2026-08-11T05:00:00.000Z");
const nonce = "0123456789abcdef0123456789abcdef";
const BRIEF_BOUNDARY = "Owner-authored plain-language goal structure and selected local input references only. Drafting derives bounded job proposals from private policy but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const CATALOG_BOUNDARY = "Owner-selected content-addressed local input references only. The catalog grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";

const sha = (value) => createHash("sha256").update(value).digest("hex");
const budgets = (overrides = {}) => ({
  maxRuntimeSeconds: 3600, maxIterations: 24, maxToolCalls: 2000, maxConcurrentSubagents: 1,
  maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4,
  maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 268435456,
  maxNetworkBytes: 1073741824, maxFailures: 5, noProgressLimit: 3, ...overrides,
});

async function policy() {
  const value = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  value.enabled = true;
  value.runner.prepared = true;
  value.localModel.prepared = true;
  value.verifier.enabled = true;
  value.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  value.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${value.runner.imageDigest}`;
  value.profiles.scout.enabled = true;
  value.profiles.builder.enabled = true;
  value.profiles.researcher = {
    enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
    services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
    outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
    backend: {
      adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1",
      queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true,
    },
    maxResearch: {
      maxQueries: 20, maxResultsPerQuery: 20, maxSources: 200, maxSourceBytes: 2097152,
      maxTotalSourceBytes: 33554432, allowedSourceTypes: ["web", "news", "academic", "forum"],
    },
  };
  return value;
}

function catalog(entries = []) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-input-catalog-v1.schema.json", schemaVersion: 1,
    catalogId: "inputcatalog-1786424400000-abcdef123456", createdAt: "2026-08-11T05:00:00.000Z",
    entries, boundary: CATALOG_BOUNDARY,
  };
}

function brief(overrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-brief-v1.schema.json", schemaVersion: 1,
    objective: "Understand, improve, and research a bounded public project.", dataClassification: "public",
    milestones: [
      {
        milestoneId: "assess", kind: "inspect", objective: "Assess the supplied context.",
        doneWhen: ["Return an evidence-backed finding report"], dependsOn: [], inputIds: [], effort: "quick",
      },
      {
        milestoneId: "build", kind: "build", objective: "Implement the bounded improvement.",
        doneWhen: ["Return a bounded patch", "Return independent evidence"], dependsOn: ["assess"], inputIds: [], effort: "standard",
      },
      {
        milestoneId: "research", kind: "research", objective: "Research the public design space.",
        doneWhen: ["Return a cited report"], dependsOn: ["assess"], inputIds: [], effort: "deep",
        research: { allowedDomains: [], deniedDomains: [], sourceTypes: ["web", "news", "academic", "forum"] },
      },
    ],
    boundary: BRIEF_BOUNDARY, ...overrides,
  };
}

async function fixture({ withInput = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-draft-"));
  const objects = join(root, "objects");
  await mkdir(objects, { mode: 0o700 });
  const privatePolicy = await policy();
  let privateCatalog = catalog(), privateBrief = brief();
  if (withInput) {
    const bytes = Buffer.from("fixture input object\n", "utf8"), digest = sha(bytes);
    const entry = { id: "project", kind: "repository-snapshot", objectName: `${digest}.tar`, contentSha256: digest, bytes: bytes.length, classification: "internal", mountMode: "read-only" };
    privateCatalog = catalog([entry]);
    privateBrief = brief({
      objective: "Inspect one private local project.", dataClassification: "internal",
      milestones: [{ milestoneId: "assess", kind: "inspect", objective: "Inspect the project.", doneWhen: ["Return an evidence-backed report"], dependsOn: [], inputIds: ["project"], effort: "quick" }],
    });
    await writeFile(join(objects, entry.objectName), bytes, { mode: 0o600 });
  }
  return { root, objects, privatePolicy, privateCatalog, privateBrief };
}

async function writeInputs(value) {
  const briefPath = join(value.root, "brief.json"), policyPath = join(value.root, "policy.json"), catalogPath = join(value.root, "catalog.json");
  await Promise.all([
    writeFile(briefPath, `${JSON.stringify(value.privateBrief)}\n`, { mode: 0o600 }),
    writeFile(policyPath, `${JSON.stringify(value.privatePolicy)}\n`, { mode: 0o600 }),
    writeFile(catalogPath, `${JSON.stringify(value.privateCatalog)}\n`, { mode: 0o600 }),
  ]);
  return { briefPath, policyPath, catalogPath };
}

function argv(value, paths, output = join(value.root, "draft")) {
  return ["--brief", paths.briefPath, "--policy", paths.policyPath, "--input-catalog", paths.catalogPath, "--object-store", value.objects, "--output", output];
}

test("guided drafting deterministically derives three least-authority local jobs and a review", async () => {
  const privatePolicy = await policy(), privateBrief = brief(), privateCatalog = catalog();
  const first = compileGuidedGoal({ brief: privateBrief, policy: privatePolicy, catalog: privateCatalog, now, nonce });
  const second = compileGuidedGoal({ brief: privateBrief, policy: privatePolicy, catalog: privateCatalog, now, nonce });
  assert.equal(canonical(first), canonical(second));
  assert.deepEqual(validateWorkGoalBrief(privateBrief), []);
  assert.deepEqual(validateWorkInputCatalog(privateCatalog), []);
  assert.deepEqual(validateWorkGoalDeclaration(first.declaration), []);
  assert.deepEqual(validateWorkGoalDraft(first.draft), []);
  assert.deepEqual(first.jobs.map((job) => job.profile), ["scout", "builder", "researcher"]);
  assert.ok(first.jobs.every((job) => validateWorkJob(job).length === 0));
  assert.ok(first.jobs.every((job) => job.requestedCapabilities.modelRoute === "local-only" && !job.requestedCapabilities.externalEffects));
  assert.ok(first.jobs.every((job) => !job.requestedCapabilities.hostAccess && !job.requestedCapabilities.ambientCredentials));
  assert.equal(first.jobs[0].budgets.maxRuntimeSeconds, 900);
  assert.equal(first.jobs[1].budgets.maxRuntimeSeconds, 3600);
  assert.equal(first.jobs[2].budgets.maxRuntimeSeconds, 3600, "deep effort is clamped to private policy");
  assert.equal(first.jobs[2].research.maxQueries, 20, "research effort is clamped to private policy");
  assert.equal(first.jobs[1].verification.checks[0].kind, "patch-integrity");
  assert.deepEqual(first.jobs[1].verification.checks[0].criterionIndexes, [0, 1]);
  assert.ok(Object.values(first.draft.authority).every((value) => value === false));
  assert.doesNotMatch(JSON.stringify(first.draft), /(?:sk-[A-Za-z0-9_-]{20,}|docker\.sock|SSH_AUTH_SOCK)/u);
});

test("goal draft CLI verifies selected objects, publishes atomically, and feeds exact goal preparation", async () => {
  const value = await fixture({ withInput: true }), paths = await writeInputs(value), output = join(value.root, "client-secret-draft");
  const receipt = await runGoalDraftCommand(argv(value, paths, output), { now, nonce });
  assert.equal(receipt.status, "drafted");
  assert.equal(receipt.milestones, 1);
  assert.ok(Object.values(receipt.authority).every((entry) => entry === false));
  assert.doesNotMatch(JSON.stringify(receipt), /Inspect one private|project|fixture input|client-secret-draft/u);
  const declaration = JSON.parse(await readFile(join(output, "goal-declaration.json"), "utf8"));
  const jobs = JSON.parse(await readFile(join(output, "jobs.json"), "utf8"));
  const review = JSON.parse(await readFile(join(output, "goal-draft.json"), "utf8"));
  assert.deepEqual(validateWorkGoalDeclaration(declaration), []);
  assert.deepEqual(validateWorkGoalDraft(review), []);
  assert.equal(review.bindings.jobsSha256, sha(canonical(jobs)));
  assert.deepEqual(await readdir(join(output, "input-manifests")), [`${jobs[0].jobId}.json`]);
  const prepared = join(value.root, "prepared");
  const preparedReceipt = await runGoalPrepareCommand([
    "--declaration", join(output, "goal-declaration.json"), "--jobs", join(output, "jobs.json"), "--output", prepared,
  ], { now, suffix: "abcdef123456" });
  assert.equal(preparedReceipt.milestones, 1);
  assert.equal(preparedReceipt.jobsSha256, review.bindings.jobsSha256);
});

test("guided drafting fails closed on authority smuggling, dependency flaws, private research, and policy drift", async () => {
  const privatePolicy = await policy(), privateCatalog = catalog();
  const smuggled = brief();
  smuggled.milestones[0].externalEffects = true;
  assert.throws(() => compileGuidedGoal({ brief: smuggled, policy: privatePolicy, catalog: privateCatalog, now, nonce }), GoalDraftCliError);
  const cyclic = brief();
  cyclic.milestones[0].dependsOn = ["build"];
  assert.throws(() => compileGuidedGoal({ brief: cyclic, policy: privatePolicy, catalog: privateCatalog, now, nonce }), /cycle/);
  const privateResearch = brief({ dataClassification: "confidential" });
  assert.throws(() => compileGuidedGoal({ brief: privateResearch, policy: privatePolicy, catalog: privateCatalog, now, nonce }), /public research cannot receive confidential/);
  const disabled = structuredClone(privatePolicy);
  disabled.profiles.builder.enabled = false;
  assert.throws(() => compileGuidedGoal({ brief: brief(), policy: disabled, catalog: privateCatalog, now, nonce }), /disabled builder/);
  const narrowed = structuredClone(privatePolicy);
  narrowed.profiles.researcher.maxResearch.allowedSourceTypes = ["web"];
  assert.throws(() => compileGuidedGoal({ brief: brief(), policy: narrowed, catalog: privateCatalog, now, nonce }), /outside private policy/);
  const oversizedCatalog = catalog([{
    id: "project", kind: "repository-snapshot", objectName: `${"a".repeat(64)}.tar`, contentSha256: "a".repeat(64),
    bytes: 3221225472, classification: "public", mountMode: "read-only",
  }]);
  const oversizedBrief = brief({
    objective: "Inspect an oversized input.",
    milestones: [{ milestoneId: "assess", kind: "inspect", objective: "Inspect it.", doneWhen: ["Return a report"], dependsOn: [], inputIds: ["project"], effort: "quick" }],
  });
  assert.throws(() => compileGuidedGoal({ brief: oversizedBrief, policy: privatePolicy, catalog: oversizedCatalog, now, nonce }), /exceed its policy-clamped disk budget/);
});

test("goal draft CLI rejects object substitution, linked controls, collisions, and concurrent publication", async (context) => {
  const value = await fixture({ withInput: true }), paths = await writeInputs(value), objectName = value.privateCatalog.entries[0].objectName;
  await writeFile(join(value.objects, objectName), "substituted\n", { mode: 0o600 });
  await assert.rejects(() => runGoalDraftCommand(argv(value, paths), { now, nonce }), /selected input verification failed/);
  assert.equal(await lstat(join(value.root, "draft")).catch(() => null), null);

  await writeFile(join(value.objects, objectName), "fixture input object\n", { mode: 0o600 });
  const outcomes = await Promise.allSettled(Array.from({ length: 8 }, () => runGoalDraftCommand(argv(value, paths), { now, nonce })));
  assert.equal(outcomes.filter((entry) => entry.status === "fulfilled").length, 1);
  assert.equal(outcomes.filter((entry) => entry.status === "rejected").length, 7);
  assert.ok(outcomes.filter((entry) => entry.status === "rejected").every((entry) => /already exists/.test(entry.reason.message)));
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-draft-")), []);

  if (process.platform === "win32") return context.skip("hard-link mode/ownership behavior differs on Windows");
  const linked = await fixture(), linkedPaths = await writeInputs(linked), second = join(linked.root, "brief-linked.json");
  await link(linkedPaths.briefPath, second);
  await assert.rejects(() => runGoalDraftCommand([
    "--brief", second, "--policy", linkedPaths.policyPath, "--input-catalog", linkedPaths.catalogPath,
    "--object-store", linked.objects, "--output", join(linked.root, "linked-output"),
  ], { now, nonce }), /not owner-private and single-link/);
  await chmod(linkedPaths.policyPath, 0o644);
  await assert.rejects(() => runGoalDraftCommand(argv(linked, linkedPaths, join(linked.root, "public-output")), { now, nonce }), /not owner-private/);
  await rm(linked.root, { recursive: true, force: true });
});

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileDataLab, compileResearcher, compileScout } from "../deploy/work-broker/broker.mjs";
import {
  candidateArtifactSetSha256, executeCandidateWaitAttempt, recordExpiredAuthorizationFailure, recordInterruptedCandidateFailure,
} from "../deploy/work-controller/candidate-wait-loop.mjs";
import {
  createGoalCandidateDriver, createGoalCandidatePreparer, createGoalCandidateResolver,
} from "../deploy/work-controller/goal-candidate-adapter.mjs";
import {
  appendCheckpoint, checkpointSha256, initializeCheckpointLedger, recoverCheckpointLedger,
} from "../deploy/work-controller/checkpoints.mjs";
import { runGoalAcceptCommand } from "../deploy/work-controller/goal-accept-cli.mjs";
import { buildGoalSemanticReview } from "../deploy/work-controller/goal-review-cli.mjs";
import { runGoalCancelCommand } from "../deploy/work-controller/goal-cancel-cli.mjs";
import { runGoalCleanupCommand } from "../deploy/work-controller/goal-cleanup-cli.mjs";
import { runGoalCycleCommand } from "../deploy/work-controller/goal-cycle-cli.mjs";
import { createLeaseConsumption, claimLease } from "../deploy/work-runner/runner-core.mjs";
import { finalizeScoutReport, parseScoutReportProposal } from "../deploy/work-runner/scout-report.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { initializeGoalRunBundle, recoverGoalRunBundles } from "../deploy/work-controller/goal-run-bundles.mjs";
import { dispatchGoalMilestone, goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import { acceptSemanticCandidate } from "../deploy/work-controller/semantic-acceptance.mjs";
import { canonical, validateWorkScoutReport, validateWorkScoutVerification, validateWorkSemanticAcceptance } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
function suffixSource(start) { let value = BigInt(`0x${start}`); return () => (value++).toString(16).padStart(12, "0"); }

function budgets() {
  return {
    maxRuntimeSeconds: 120, maxIterations: 3, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 4, maxInputTokens: 100000, maxOutputTokens: 4000, maxCpuCores: 1,
    maxMemoryMiB: 1536, maxDiskBytes: 64 * 1024 * 1024, maxArtifactBytes: 2 * 1024 * 1024,
    maxNetworkBytes: 4 * 1024 * 1024, maxFailures: 1, noProgressLimit: 1,
  };
}

function requested(services) {
  return {
    filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash"],
    network: { mode: "brokered", services }, modelRoute: "local-only", hostAccess: false,
    ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false,
  };
}

async function fixture(t, profile, initializeChild = true) {
  const root = await mkdtemp(join(tmpdir(), `pixel-candidate-wait-${profile}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  await mkdir(join(root, "objects"), { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `local/pixel-work-runner@sha256:${digest("f")}`;
  policy.localModel.prepared = true;
  policy.localModel.imageDigest = `sha256:${digest("e")}`;
  policy.localModel.imageRef = `local/pixel-model@sha256:${digest("e")}`;
  policy.profiles.scout.enabled = true;
  const jobId = "work-1786366800000-abcdef123456";
  let request;
  let entries;
  if (profile === "scout") {
    request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1, jobId,
      createdAt: new Date(baseTime).toISOString(), requester: "pixel", profile, objective: "Inspect the exact local fixture.",
      acceptanceCriteria: ["Return one semantically correct evidence-backed finding"], dataClassification: "internal",
      inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 100, classification: "internal" }],
      requestedCapabilities: {
        filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
        modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
        mergeAuthority: false, deployAuthority: false, policyMutation: false,
      },
      budgets: budgets(), outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    entries = [{ id: "source", kind: "repository-snapshot", objectName: `${digest("a")}.tar`, contentSha256: digest("a"), bytes: 100, classification: "internal", mountMode: "read-only" }];
  } else if (profile === "researcher") {
    policy.profiles.researcher = {
      enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"],
      services: ["local-model", "research-broker"], workspaceMount: "disposable-read-write",
      outputKinds: ["finding-report", "document"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
      backend: { adapter: "reference", prepared: true, endpointContract: "pixel-public-research-broker-v1", queryLogging: "hash-only", contentRetention: "job-only", webCourierRequired: true },
      maxResearch: { maxQueries: 4, maxResultsPerQuery: 5, maxSources: 20, maxSourceBytes: 262144, maxTotalSourceBytes: 1048576, allowedSourceTypes: ["web", "news", "academic", "forum"] },
    };
    request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1, jobId,
      createdAt: new Date(baseTime).toISOString(), requester: "pixel", profile, objective: "Research the public safety fixture.",
      acceptanceCriteria: ["Return one semantically correct, citation-backed finding"], dataClassification: "public", inputs: [],
      requestedCapabilities: requested(["local-model", "research-broker"]), budgets: budgets(),
      outputs: { mode: "artifacts", requiredKinds: ["finding-report", "document"] },
      research: {
        mode: "public-web", queryPolicy: "public-sanitized", maxQueries: 2, maxResultsPerQuery: 2, maxSources: 4,
        maxSourceBytes: 65536, maxTotalSourceBytes: 262144, safeSearch: "strict", allowedDomains: ["example.com"],
        deniedDomains: [], sourceTypes: ["web"], citationVerification: true, retention: "job-only",
        boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
      },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    entries = [];
  } else {
    policy.profiles.dataLab = {
      enabled: true, isolation: "hardened-container", tools: ["read", "search", "write", "edit", "bash"], services: ["local-model"],
      workspaceMount: "disposable-read-write", outputKinds: ["finding-report", "dataset", "document", "visualization"], minimumMemoryMiB: 1536, maxBudgets: budgets(),
      runtime: { contract: "pixel-local-data-runtime-v1", engines: {
        duckdb: { version: "1.5.5", entrypoint: "/usr/bin/python3" }, polars: { version: "1.43.2", entrypoint: "/usr/bin/python3" },
        python: { version: "3.11.2", entrypoint: "/usr/bin/python3" }, sqlite: { version: "3.40.1", entrypoint: "/usr/bin/sqlite3" },
      } },
      maxData: { maxDatasets: 4, maxDatasetBytes: 1048576, maxArtifactFiles: 8, maxArtifactBytes: 524288, allowedInputFormats: ["csv", "json", "jsonl", "parquet", "sqlite"], allowedArtifactFormats: ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"] },
    };
    request = {
      $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1, jobId,
      createdAt: new Date(baseTime).toISOString(), requester: "pixel", profile, objective: "Analyze the exact local sales fixture.",
      acceptanceCriteria: ["Return semantically correct regional totals"], dataClassification: "confidential",
      inputs: [{ id: "records", kind: "dataset", mountMode: "read-only", contentSha256: digest("c"), maxBytes: 100, classification: "confidential" }],
      requestedCapabilities: requested(["local-model"]), budgets: budgets(), outputs: { mode: "artifacts", requiredKinds: ["finding-report", "dataset", "document", "visualization"] },
      data: {
        mode: "local-reproducible", datasets: [{ datasetId: "sales", inputId: "records", relativePath: "sales.csv", format: "csv", contentSha256: digest("d"), maxBytes: 80 }],
        engines: ["duckdb", "polars", "python", "sqlite"], maxArtifactFiles: 8, maxArtifactBytes: 524288,
        allowedArtifactFormats: ["csv", "markdown"], replayVerification: true, retention: "job-only",
        boundary: "Local reproducible analysis only. Raw inputs remain read-only; only independently replayed derived artifacts may be retained. No credential, direct-network, external-action, publication, policy, or scope authority is granted.",
      },
      boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
    };
    entries = [{ id: "records", kind: "dataset", objectName: `${digest("c")}.tar`, contentSha256: digest("c"), bytes: 100, classification: "confidential", mountMode: "read-only" }];
  }
  const compile = profile === "scout" ? compileScout : profile === "researcher" ? compileResearcher : compileDataLab;
  const compiled = compile(request, policy, entries, { now: new Date(baseTime), suffix: "123456abcdef" });
  const prepared = {
    ...compiled, policy, bindings: {
      planSha256: compiled.planSha256, leaseSha256: checkpointSha256(compiled.lease),
      policySha256: compiled.policySha256, inputSetSha256: compiled.inputSetSha256,
    },
    workspace: { sha256: digest("b") },
  };
  if (initializeChild) await initializeCheckpointLedger({
    stateRoot, plan: prepared.plan, lease: prepared.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000001",
  });
  return { root, stateRoot, policy, request, compiled, prepared };
}

function researchCandidate(claim) {
  const batchSha256 = digest("1");
  const evidence = Buffer.from("Public safety evidence", "utf8");
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json", schemaVersion: 1,
    reportId: `researchreport-${baseTime + 20}-000000000020`, jobId: claim.jobId, claimId: claim.claimId,
    createdAt: new Date(baseTime + 20).toISOString(), planSha256: claim.planSha256, researchPolicySha256: digest("2"),
    batchSha256s: [batchSha256], titleBase64: Buffer.from("Safety finding").toString("base64"),
    findings: [{ findingId: "finding-1", statementBase64: Buffer.from("The cited public source supports the candidate finding.").toString("base64"), material: true, citations: [{ batchSha256, sourceId: "source-abcdef1234567890", evidenceBase64: evidence.toString("base64"), evidenceSha256: sha(evidence) }] }],
    limitationsBase64: Buffer.from("Semantic entailment still requires separate acceptance.").toString("base64"),
    dataClassification: "public", privateDataIncluded: false, externalEffects: false,
    authority: { directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false },
    boundary: "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; deterministic verification proves source integrity and quote presence, not semantic entailment or truth.",
  };
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `researchverification-${baseTime + 21}-000000000021`, jobId: claim.jobId, claimId: claim.claimId,
    createdAt: new Date(baseTime + 21).toISOString(), planSha256: claim.planSha256, batchSha256s: [batchSha256], reportSha256: sha(report),
    status: "evidence-pass", verificationLevel: "deterministic-evidence-presence", semanticEntailmentVerified: false,
    independent: true, network: "none", modelUsed: false,
    findings: [{ findingId: "finding-1", status: "pass", citations: [{ batchSha256, sourceId: "source-abcdef1234567890", contentSha256: digest("3"), receiptSha256: digest("4"), evidenceSha256: sha(evidence), evidenceBytes: evidence.length, offset: 1, status: "present" }] }],
    privateDataIncluded: false, externalEffects: false,
    authority: { directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false },
    boundary: "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority and does not claim semantic entailment, source truth, completeness, or publication readiness.",
  };
  return {
    cleanupComplete: true, execution: { durationMilliseconds: 1200 }, proxyReceipt: { modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000 },
    report, verification,
    artifacts: {
      report: { path: "/private/research-report.json", bytes: 300, sha256: digest("5") },
      verification: { path: "/private/research-verification.json", bytes: 200, sha256: digest("6") },
      evidence: { path: "/private/research-evidence.json", bytes: 100, sha256: digest("7") }, totalBytes: 600,
    },
  };
}

function scoutCandidate(claim) {
  const quote = Buffer.from("const invariant = 42", "utf8");
  const scoutAuthority = { hostAccess: false, credentials: false, network: false, externalEffects: false, publish: false, merge: false, deploy: false, policyMutation: false, scopeExpansion: false };
  const evidence = { inputId: "source", path: "src/main.js", quoteBase64: quote.toString("base64"), quoteSha256: sha(quote), fileSha256: digest("2"), offset: 7, bytes: quote.length };
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-scout-report-v1.schema.json", schemaVersion: 1,
    reportId: `scoutreport-${baseTime + 20}-000000000020`, jobId: claim.jobId, claimId: claim.claimId,
    createdAt: new Date(baseTime + 20).toISOString(), planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
    titleBase64: Buffer.from("Scout finding").toString("base64"),
    findings: [{ findingId: "finding-1", statementBase64: Buffer.from("The local fixture contains the candidate invariant.").toString("base64"), material: true, evidence: [evidence] }],
    limitationsBase64: Buffer.from("Semantic correctness still requires local acceptance.").toString("base64"),
    dataClassification: "internal", privateDataIncluded: true, externalEffects: false, authority: scoutAuthority,
    boundary: "Retained local Scout findings with controller-verified exact file evidence. Evidence presence does not establish semantic entailment, completeness, truth, or completion authority.",
  };
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-scout-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `scoutverification-${baseTime + 21}-000000000021`, jobId: claim.jobId, claimId: claim.claimId,
    createdAt: new Date(baseTime + 21).toISOString(), planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
    reportSha256: sha(report), status: "evidence-pass", verificationLevel: "deterministic-local-evidence-presence",
    semanticEntailmentVerified: false, independent: true, network: "none", modelUsed: false,
    findings: [{ findingId: "finding-1", status: "pass", evidence: [{ inputId: evidence.inputId, path: evidence.path, quoteSha256: evidence.quoteSha256, fileSha256: evidence.fileSha256, offset: evidence.offset, bytes: evidence.bytes, status: "present" }] }],
    privateDataIncluded: true, externalEffects: false, authority: scoutAuthority,
    boundary: "Independent networkless proof of exact local evidence presence only. It grants no semantic truth, completion, execution, publication, merge, deployment, policy, or scope authority.",
  };
  return {
    cleanupComplete: true, execution: { durationMilliseconds: 900 }, proxyReceipt: { modelRequests: 2, inputTokens: 90, outputTokens: 15, networkBytes: 800 }, report, verification,
    artifacts: {
      report: { path: "/private/scout-report.json", bytes: 300, sha256: digest("3") },
      verification: { path: "/private/scout-verification.json", bytes: 200, sha256: digest("4") },
      evidence: { path: "/private/scout-evidence.json", bytes: 100, sha256: digest("5") }, totalBytes: 600,
    },
  };
}

function scoutProposal() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-scout-report-proposal-v1.schema.json", schemaVersion: 1,
    titleBase64: Buffer.from("Exact local finding").toString("base64"),
    findings: [{
      findingId: "finding-1", statementBase64: Buffer.from("The invariant is 42.").toString("base64"), material: true,
      evidence: [{ inputId: "source", path: "src/main.js", quoteBase64: Buffer.from("invariant = 42").toString("base64") }],
    }],
    limitationsBase64: Buffer.from("One file was inspected.").toString("base64"),
    boundary: "Untrusted Scout proposal only. Local file references and quoted bytes are evidence candidates, not instructions, truth, completion, or authority.",
  };
}

function dataCandidate(claim) {
  const dataAuthority = { rawInputMutation: false, hostAccess: false, network: false, credentials: false, externalEffects: false, publish: false, policyMutation: false, scopeExpansion: false };
  const manifestSha256 = digest("7");
  const recipeSha256 = digest("8");
  const artifactSha256 = digest("9");
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-artifact-manifest-v1.schema.json", schemaVersion: 1,
    manifestId: `data-manifest-${baseTime + 20}-000000000020`, jobId: claim.jobId, claimId: claim.claimId,
    planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256, createdAt: new Date(baseTime + 20).toISOString(),
    recipe: { path: "recipe.py", bytes: 40, sha256: recipeSha256, runtimeContract: "pixel-data-recipe-v1" },
    datasets: [{ datasetId: "sales", format: "csv", contentSha256: digest("d"), bytes: 80 }],
    artifacts: [{ artifactId: "artifact-1", path: "artifacts/summary.csv", kind: "dataset", format: "csv", mediaType: "text/csv", bytes: 50, sha256: artifactSha256 }],
    totals: { files: 1, bytes: 50 }, dataClassification: "confidential", rawInputsReadOnly: true, externalEffects: false,
    authority: dataAuthority,
    boundary: "Content-addressed local Data Lab artifacts only. Raw inputs stayed read-only; the manifest grants no truth, publication, external-action, policy, or completion authority until exact replay verification passes.",
  };
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-report-v1.schema.json", schemaVersion: 1,
    reportId: `data-report-${baseTime + 21}-000000000021`, jobId: claim.jobId, claimId: claim.claimId,
    planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256, createdAt: new Date(baseTime + 21).toISOString(),
    manifestSha256, recipeSha256, title: "Local result", summary: "One replayed artifact.", methodology: "A deterministic local recipe was replayed.",
    artifacts: [{ ...manifest.artifacts[0], purpose: "Candidate regional totals" }],
    findings: [{ findingId: "finding-1", statement: "The candidate contains one result.", evidence: [{ artifactId: "artifact-1", path: "artifacts/summary.csv", sha256: artifactSha256 }] }],
    limitations: "Replay does not establish semantic truth.", dataClassification: "confidential", privateDataIncluded: true, externalEffects: false,
    authority: dataAuthority,
    boundary: "Finalized local Data Lab report bound to exact derived artifacts. Exact replay proves reproducibility, not semantic truth; publication, external action, policy change, and completion remain outside worker authority.",
  };
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `data-verification-${baseTime + 22}-000000000022`, jobId: claim.jobId, claimId: claim.claimId,
    planSha256: claim.planSha256, createdAt: new Date(baseTime + 22).toISOString(), manifestSha256, recipeSha256,
    status: "exact-replay-pass", artifacts: [{ artifactId: "artifact-1", path: "artifacts/summary.csv", expectedSha256: artifactSha256, observedSha256: artifactSha256, expectedBytes: 50, observedBytes: 50, status: "match" }],
    runtime: { network: "none", freshWorkspace: true, recipeExitCode: 0, timedOut: false, outputLimitExceeded: false, unexpectedArtifacts: 0 },
    semanticAccuracyVerified: false, externalEffects: false, authority: dataAuthority,
    boundary: "Independent networkless replay evidence only. A byte-for-byte match proves reproducibility and artifact integrity, not semantic truth, business correctness, publication authority, or completion authority.",
  };
  const artifacts = {
    report: { path: "/private/data-report.json", bytes: 300, sha256: digest("a") },
    verification: { path: "/private/data-verification.json", bytes: 200, sha256: digest("b") },
    evidence: { path: "/private/data-evidence.json", bytes: 100, sha256: digest("c") },
    manifest: { path: "/private/artifact-manifest.json", bytes: 150, sha256: manifestSha256 },
    recipe: { path: "/private/recipe.py", bytes: 40, sha256: recipeSha256 }, derivedDirectory: "/private/artifacts",
    totalBytes: 50 + 40 + 150 + 75 + 300 + 200 + 100,
  };
  return { cleanupComplete: true, execution: { durationMilliseconds: 2200 }, proxyReceipt: { modelRequests: 1, inputTokens: 80, outputTokens: 10, networkBytes: 0 }, manifest, manifestRecord: { bytes: 150, sha256: manifestSha256 }, inventoryRecord: { bytes: 75, sha256: digest("d") }, report, verification, artifacts };
}

async function writeRetainedJson(path, value) {
  const content = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  await writeFile(path, content, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
  return { path, bytes: content.length, sha256: sha(content) };
}

async function retainCandidateArtifacts(value, claim, candidate) {
  const root = join(value.stateRoot, "results", claim.claimId);
  await mkdir(root, { recursive: true, mode: 0o700 });
  if (process.platform !== "win32") await chmod(root, 0o700);
  if (["scout", "researcher"].includes(value.request.profile)) {
    const prefix = value.request.profile === "scout" ? "scout" : "research";
    const report = await writeRetainedJson(join(root, `${prefix}-report.json`), candidate.report);
    const verification = await writeRetainedJson(join(root, `${prefix}-verification.json`), candidate.verification);
    const evidence = await writeRetainedJson(join(root, `${prefix}-evidence.json`), { schemaVersion: 1, jobId: claim.jobId, claimId: claim.claimId, reportSha256: report.sha256, verificationSha256: verification.sha256 });
    candidate.artifacts = { report, verification, evidence, totalBytes: report.bytes + verification.bytes + evidence.bytes };
    return candidate;
  }
  const artifactRoot = join(root, "artifacts");
  await mkdir(artifactRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(artifactRoot, 0o700);
  const recipeContent = Buffer.from("print('replay fixture')\n", "utf8");
  const recipe = { path: join(root, "recipe.py"), bytes: recipeContent.length, sha256: sha(recipeContent) };
  await writeFile(recipe.path, recipeContent, { mode: 0o600 });
  const artifactContent = Buffer.from("region,total\nnorth,10\n", "utf8");
  const derivedPath = join(artifactRoot, "summary.csv");
  await writeFile(derivedPath, artifactContent, { mode: 0o600 });
  if (process.platform !== "win32") { await chmod(recipe.path, 0o600); await chmod(derivedPath, 0o600); }
  const artifactSha256 = sha(artifactContent);
  candidate.manifest.recipe.bytes = recipe.bytes;
  candidate.manifest.recipe.sha256 = recipe.sha256;
  candidate.manifest.artifacts[0].bytes = artifactContent.length;
  candidate.manifest.artifacts[0].sha256 = artifactSha256;
  candidate.manifest.totals = { files: 1, bytes: artifactContent.length };
  candidate.report.recipeSha256 = recipe.sha256;
  candidate.report.artifacts[0].bytes = artifactContent.length;
  candidate.report.artifacts[0].sha256 = artifactSha256;
  candidate.report.findings[0].evidence[0].sha256 = artifactSha256;
  candidate.verification.recipeSha256 = recipe.sha256;
  candidate.verification.artifacts[0].expectedBytes = artifactContent.length;
  candidate.verification.artifacts[0].observedBytes = artifactContent.length;
  candidate.verification.artifacts[0].expectedSha256 = artifactSha256;
  candidate.verification.artifacts[0].observedSha256 = artifactSha256;
  const manifest = await writeRetainedJson(join(root, "artifact-manifest.json"), candidate.manifest);
  candidate.manifestRecord = { bytes: manifest.bytes, sha256: manifest.sha256 };
  candidate.report.manifestSha256 = manifest.sha256;
  candidate.verification.manifestSha256 = manifest.sha256;
  const inventory = await writeRetainedJson(join(root, "replay-inventory.json"), [{ path: "artifacts/summary.csv", bytes: artifactContent.length, sha256: artifactSha256 }]);
  candidate.inventoryRecord = { bytes: inventory.bytes, sha256: inventory.sha256 };
  const report = await writeRetainedJson(join(root, "data-report.json"), candidate.report);
  const verification = await writeRetainedJson(join(root, "data-verification.json"), candidate.verification);
  const evidence = await writeRetainedJson(join(root, "data-evidence.json"), { schemaVersion: 1, jobId: claim.jobId, claimId: claim.claimId, reportSha256: report.sha256, verificationSha256: verification.sha256 });
  candidate.artifacts = {
    report, verification, evidence, manifest, recipe, derivedDirectory: artifactRoot,
    totalBytes: artifactContent.length + recipe.bytes + manifest.bytes + inventory.bytes + report.bytes + verification.bytes + evidence.bytes,
  };
  return candidate;
}

function confirmations(checkpoint) {
  return {
    candidateCheckpointSha256: checkpointSha256(checkpoint),
    acceptanceCriteriaSha256: checkpoint.acceptanceCriteriaSha256,
    artifactManifestSha256: checkpoint.artifactManifestSha256,
    deterministicVerificationSha256: checkpoint.verificationEvidenceSha256,
  };
}

function goalFor(value) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: new Date(baseTime + 1).toISOString(), requester: "pixel",
    objective: `Complete one durable ${value.request.profile} candidate milestone.`, dataClassification: value.request.dataClassification,
    milestones: [{ milestoneId: "candidate", jobId: value.request.jobId, jobSha256: goalSha256(value.request), profile: value.request.profile, dependsOn: [] }],
    budgets: {
      maxJobs: 1, maxRuntimeSeconds: value.request.budgets.maxRuntimeSeconds, maxModelRequests: value.request.budgets.maxModelRequests,
      maxInputTokens: value.request.budgets.maxInputTokens, maxOutputTokens: value.request.budgets.maxOutputTokens,
      maxNetworkBytes: value.request.budgets.maxNetworkBytes, maxArtifactBytes: value.request.budgets.maxArtifactBytes,
      maxFailures: value.request.budgets.maxFailures,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

async function initializeGoalFixture(value) {
  const goal = goalFor(value);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal, jobs: [value.request], now: new Date(baseTime + 2), suffix: "600000000001" });
  await initializeGoalRunBundle({
    stateRoot: value.stateRoot, goal, jobs: [value.request], jobId: value.request.jobId,
    plan: value.prepared.plan, lease: value.prepared.lease, workspaceSnapshotSha256: value.prepared.workspace.sha256,
    now: new Date(baseTime + 3), suffix: "600000000002",
  });
  return goal;
}

async function controllerConfiguration(value, goal, name) {
  const paths = {
    goalPath: join(value.root, `${name}-goal.json`), jobsPath: join(value.root, `${name}-jobs.json`),
    policyPath: join(value.root, `${name}-policy.json`), configPath: join(value.root, `${name}-controller.json`),
  };
  await writeFile(paths.goalPath, `${JSON.stringify(goal)}\n`, { mode: 0o600 });
  await writeFile(paths.jobsPath, `${JSON.stringify([value.request])}\n`, { mode: 0o600 });
  await writeFile(paths.policyPath, `${JSON.stringify(value.policy)}\n`, { mode: 0o600 });
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot: value.stateRoot, goalPath: paths.goalPath, jobsPath: paths.jobsPath, policyPath: paths.policyPath,
    objectStore: join(value.root, "objects"), workspaceRoot: join(value.root, "workspaces"), executorPath: join(value.root, "omp"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    runtime: {
      dockerPath: join(value.root, "docker"), backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-model",
      networkSubnet: "172.29.1.0/29", workerIp: "172.29.1.2", proxyIp: "172.29.1.3", uid: 1000, gid: 1000,
    },
    ...(value.request.profile === "researcher" ? {
      researchRuntime: { researchCourierQueueRoot: join(value.root, "research-courier"), researchEndpoint: "http://127.0.0.1:8888" },
    } : {}),
    controller: { maxTransitions: 1 },
  };
  if (config.researchRuntime) await mkdir(config.researchRuntime.researchCourierQueueRoot, { mode: 0o700 });
  await writeFile(paths.configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") {
    await Promise.all(Object.values(paths).map((path) => chmod(path, 0o600)));
    if (config.researchRuntime) await chmod(config.researchRuntime.researchCourierQueueRoot, 0o700);
  }
  return { paths, config };
}

test("an authorized child whose execution lease expired before launch is reconciled to failed, not wedged", async (t) => {
  const value = await fixture(t, "scout");
  // C4 crash state: the lease was claimed (a durable consumed tombstone written) but the process
  // died before the running checkpoint, so the head is still authorized and the worker never
  // launched. Post-expiry this wedged: the driver refuses to dispatch forever, and the consumed
  // tombstone makes operator-cancel fail closed too.
  const claim = createLeaseConsumption(value.prepared, { now: new Date(baseTime + 2), suffix: "0c0000000001" });
  await claimLease(value.stateRoot, claim);
  const before = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.equal(before.head.state, "authorized");

  const expired = () => new Date(Date.parse(value.prepared.lease.expiresAt) + 1000);
  const result = await recordExpiredAuthorizationFailure({
    stateRoot: value.stateRoot, prepared: value.prepared, clock: expired, checkpointSuffix: "0c0000000002",
  });
  assert.equal(result.action, "failed");
  assert.equal(result.checkpoint.state, "failed");
  assert.equal(result.checkpoint.progress.failureStage, "authorization-expired");
  const after = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.equal(after.head.state, "failed");
  assert.equal(after.action.action, "terminal");

  // It refuses to fail a still-valid authorization (defense against misuse before expiry).
  const fresh = await fixture(t, "scout");
  await assert.rejects(recordExpiredAuthorizationFailure({
    stateRoot: fresh.stateRoot, prepared: fresh.prepared,
    clock: () => new Date(Date.parse(fresh.prepared.lease.issuedAt) + 1), checkpointSuffix: "0c0000000003",
  }), /elapsed lease window/);
});

test("Scout exact local evidence enters a durable goal only as a semantic acceptance candidate", async (t) => {
  const value = await fixture(t, "scout");
  let tick = baseTime + 2;
  let resolvedCheckpoint = null;
  const result = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared,
    lifecycleOptions: { inert: true, capabilityResolver: async ({ mode, checkpoint }) => { assert.equal(mode, "execute"); resolvedCheckpoint = checkpoint; return { fixture: true }; } },
    clock: () => new Date(tick++),
    suffixes: { claim: "000000000012", running: "000000000013", waiting: "000000000014" },
    candidateRunner: async (_prepared, claim, options) => { assert.deepEqual(options, { inert: true, capability: { fixture: true } }); return scoutCandidate(claim); },
  });
  assert.equal(resolvedCheckpoint.state, "running");
  assert.equal(resolvedCheckpoint.workerSessionSha256, sha(result.claim));
  assert.equal(result.action, "waiting-authority");
  assert.equal(result.checkpoint.progress.criteriaPassing, 0);
  assert.equal(result.checkpoint.verificationEvidenceSha256, digest("4"));
  assert.equal(result.checkpoint.artifactManifestSha256, candidateArtifactSetSha256("scout", result.candidate));
});

test("Scout proposal parsing permits one exact JSON envelope normalization and nothing broader", () => {
  const proposal = scoutProposal();
  const encoded = JSON.stringify(proposal);
  assert.deepEqual(parseScoutReportProposal(encoded), proposal);
  assert.deepEqual(parseScoutReportProposal(`\n\`\`\`json\n${encoded}\n\`\`\`\n`), proposal);
  assert.deepEqual(parseScoutReportProposal(`\uFEFF  \`\`\`JSON \r\n${encoded}\r\n\`\`\`  `), proposal);
  assert.throws(() => parseScoutReportProposal(`Here is the result:\n\`\`\`json\n${encoded}\n\`\`\``), (error) => error.workFailureStage === "proposal-parse");
  assert.throws(() => parseScoutReportProposal(`\`\`\`json\n${encoded}\n\`\`\`\ntrailing text`), (error) => error.workFailureStage === "proposal-parse");
  assert.throws(() => parseScoutReportProposal(`\`\`\`json ${encoded}\`\`\``), (error) => error.workFailureStage === "proposal-parse");
  assert.throws(() => parseScoutReportProposal("```json\n{broken}\n```"), (error) => error.workFailureStage === "proposal-parse");
  const invalid = { ...proposal, findings: [] };
  assert.throws(() => parseScoutReportProposal(JSON.stringify(invalid)), (error) => error.workFailureStage === "proposal-contract");
});

test("Scout controller finalization verifies exact local quote bytes and rejects path or evidence substitution", async (t) => {
  const value = await fixture(t, "scout");
  const workspace = join(value.root, "scout-workspace");
  const inputRoot = join(workspace, "source");
  const sourceRoot = join(inputRoot, "src");
  await mkdir(sourceRoot, { recursive: true, mode: 0o700 });
  const sourcePath = join(sourceRoot, "main.js");
  const source = Buffer.from("export const invariant = 42;\n", "utf8");
  await writeFile(sourcePath, source, { mode: 0o600 });
  if (process.platform !== "win32") { await chmod(workspace, 0o700); await chmod(inputRoot, 0o700); await chmod(sourceRoot, 0o700); await chmod(sourcePath, 0o600); }
  const claim = createLeaseConsumption({ ...value.prepared, workspace: { ...value.prepared.workspace, path: workspace } }, { now: new Date(baseTime + 2), suffix: "001000000001" });
  const proposal = parseScoutReportProposal(JSON.stringify(scoutProposal()));
  const finalized = await finalizeScoutReport({
    proposal, plan: value.prepared.plan, claim, workspacePath: workspace, now: new Date(baseTime + 20),
    reportSuffix: "001000000002", verificationSuffix: "001000000003",
  });
  assert.deepEqual(validateWorkScoutReport(finalized.report), []);
  assert.deepEqual(validateWorkScoutVerification(finalized.verification), []);
  assert.equal(finalized.verification.semanticEntailmentVerified, false);
  assert.equal(finalized.report.findings[0].evidence[0].fileSha256, sha(source));
  const absent = structuredClone(proposal);
  absent.findings[0].evidence[0].quoteBase64 = Buffer.from("invariant = 41").toString("base64");
  await assert.rejects(finalizeScoutReport({ proposal: absent, plan: value.prepared.plan, claim, workspacePath: workspace }), (error) => error.workFailureStage === "evidence-finalization" && /quote is absent/u.test(error.message));
  const escaped = structuredClone(proposal);
  escaped.findings[0].evidence[0].path = "../main.js";
  await assert.rejects(finalizeScoutReport({ proposal: escaped, plan: value.prepared.plan, claim, workspacePath: workspace }), /proposal is invalid|path/);
  const duplicate = structuredClone(proposal);
  duplicate.findings.push(structuredClone(duplicate.findings[0]));
  await assert.rejects(finalizeScoutReport({ proposal: duplicate, plan: value.prepared.plan, claim, workspacePath: workspace }), /findings must be consecutive|repeats a finding/);
  if (process.platform !== "win32") {
    const outside = join(value.root, "outside");
    await mkdir(outside, { mode: 0o700 });
    await writeFile(join(outside, "secret.txt"), "invariant = 42\n", { mode: 0o600 });
    await symlink(outside, join(inputRoot, "linked"), "dir");
    const linked = structuredClone(proposal);
    linked.findings[0].evidence[0].path = "linked/secret.txt";
    await assert.rejects(finalizeScoutReport({ proposal: linked, plan: value.prepared.plan, claim, workspacePath: workspace }), /link or junction/);
  }
});

test("Researcher evidence enters a durable goal only as a semantic acceptance candidate", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  const result = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "000000000002", running: "000000000003", waiting: "000000000004", failed: "000000000005" },
    candidateRunner: async (_prepared, claim) => researchCandidate(claim),
  });
  assert.equal(result.action, "waiting-authority");
  assert.equal(result.semanticAcceptanceVerified, false);
  assert.equal(result.checkpoint.state, "waiting-authority");
  assert.equal(result.checkpoint.progress.criteriaPassing, 0);
  assert.equal(result.checkpoint.verificationEvidenceSha256, digest("6"));
  assert.equal((await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease })).action.action, "wait-for-explicit-authority");
});

test("Data Lab exact replay pauses without claiming semantic accuracy", async (t) => {
  const value = await fixture(t, "data-lab");
  let tick = baseTime + 2;
  const result = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "100000000002", running: "100000000003", waiting: "100000000004" },
    candidateRunner: async (_prepared, claim) => dataCandidate(claim),
  });
  assert.equal(result.checkpoint.state, "waiting-authority");
  assert.equal(result.checkpoint.progress.criteriaPassing, 0);
  assert.equal(result.checkpoint.artifactManifestSha256, candidateArtifactSetSha256("data-lab", result.candidate));
  assert.equal(result.checkpoint.usage.artifactBytes, 915);
});

test("exact local semantic attestation completes a candidate without adding execution authority", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  const candidate = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "110000000002", running: "110000000003", waiting: "110000000004" },
    candidateRunner: async (_prepared, claim) => researchCandidate(claim),
  });
  tick = baseTime + 100;
  const accepted = await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
    confirmations: confirmations(candidate.checkpoint), clock: () => new Date(tick++),
    suffixes: { attestation: "110000000005", verified: "110000000006", completed: "110000000007" },
  });
  assert.equal(accepted.action, "completed");
  assert.deepEqual(validateWorkSemanticAcceptance(accepted.attestation.record), []);
  assert.deepEqual(Object.values(accepted.attestation.record.authority), [false, false, false, false, false, true]);
  assert.equal(accepted.checkpoint.progress.criteriaPassing, value.prepared.plan.acceptanceCriteria.length);
  assert.equal(accepted.checkpoint.externalEffectsObserved, false);
  const ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "waiting-authority", "verified", "completed"]);
  assert.equal(ledger.checkpoints[2].verificationEvidenceSha256, digest("6"));
  assert.equal(ledger.head.verificationEvidenceSha256, accepted.attestation.sha256);
  assert.equal(ledger.head.usage.artifactBytes, 600 + accepted.attestation.bytes);
});

test("semantic acceptance reconciles only its exact interrupted publish twin", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  const candidate = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "115000000002", running: "115000000003", waiting: "115000000004" },
    candidateRunner: async (_prepared, claim) => researchCandidate(claim),
  });
  const exact = confirmations(candidate.checkpoint);
  tick = baseTime + 100;
  const accepted = await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
    confirmations: exact, clock: () => new Date(tick++),
    suffixes: { attestation: "115000000005", verified: "115000000006", completed: "115000000007" },
  });
  const jobRoot = join(value.stateRoot, "semantic-acceptance", value.prepared.plan.jobId);
  const interruptedTwin = join(jobRoot, `.accept-${exact.candidateCheckpointSha256}-1150000000080000`);
  await link(accepted.attestation.path, interruptedTwin);
  const recovered = await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
    confirmations: exact, clock: () => new Date(baseTime + 200),
  });
  assert.equal(recovered.attestation.sha256, accepted.attestation.sha256);
  await unlink(interruptedTwin);

  const hostileAlias = join(value.stateRoot, "semantic-acceptance-alias.json");
  await link(accepted.attestation.path, hostileAlias);
  await assert.rejects(acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
    confirmations: exact, clock: () => new Date(baseTime + 300),
  }), /private and single-link/u);
});

test("semantic acceptance requires every exact candidate hash and has one durable race winner", async (t) => {
  const value = await fixture(t, "data-lab");
  let tick = baseTime + 2;
  const candidate = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "120000000002", running: "120000000003", waiting: "120000000004" },
    candidateRunner: async (_prepared, claim) => dataCandidate(claim),
  });
  const exact = confirmations(candidate.checkpoint);
  for (const field of Object.keys(exact)) {
    const changed = { ...exact, [field]: digest("f") };
    await assert.rejects(acceptSemanticCandidate({
      stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
      confirmations: changed, clock: () => new Date(baseTime + 100),
    }), /absent|confirmations differ/);
  }
  let counter = 0x130000000000n;
  const attempts = Array.from({ length: 16 }, () => {
    const base = counter;
    counter += 3n;
    return acceptSemanticCandidate({
      stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease, confirmations: exact,
      clock: () => new Date(baseTime + 100),
      suffixes: { attestation: base.toString(16), verified: (base + 1n).toString(16), completed: (base + 2n).toString(16) },
    });
  });
  const results = await Promise.allSettled(attempts);
  assert.equal(results.filter((result) => result.status === "fulfilled").length, 16);
  const ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "waiting-authority", "verified", "completed"]);
  assert.equal((await readdir(join(value.stateRoot, "semantic-acceptance", value.prepared.plan.jobId))).length, 1);
  const repeated = await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease, confirmations: exact,
    clock: () => new Date(baseTime + 200), suffixes: { attestation: "140000000001", verified: "140000000002", completed: "140000000003" },
  });
  assert.equal(repeated.checkpoint.sequence, ledger.head.sequence);
});

test("tampered semantic attestation fails closed even after candidate completion", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  const candidate = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "150000000002", running: "150000000003", waiting: "150000000004" },
    candidateRunner: async (_prepared, claim) => researchCandidate(claim),
  });
  const exact = confirmations(candidate.checkpoint);
  const accepted = await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease, confirmations: exact,
    clock: () => new Date(baseTime + 100), suffixes: { attestation: "150000000005", verified: "150000000006", completed: "150000000007" },
  });
  const record = structuredClone(accepted.attestation.record);
  record.artifactManifestSha256 = digest("f");
  await writeFile(accepted.attestation.path, `${JSON.stringify(record, null, 2)}\n`);
  await assert.rejects(acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease, confirmations: exact,
    clock: () => new Date(baseTime + 200),
  }), /stored semantic acceptance differs/);
});

test("semantic overclaims and malformed retained totals fail durably after proven cleanup", async (t) => {
  const scout = await fixture(t, "scout");
  let tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: scout.stateRoot, prepared: scout.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "190000000002", running: "190000000003", failed: "190000000004" },
    candidateRunner: async (_prepared, claim) => { const candidate = scoutCandidate(claim); candidate.verification.findings[0].evidence[0].fileSha256 = digest("f"); return candidate; },
  }), /durably closed/);
  assert.equal((await recoverCheckpointLedger({ stateRoot: scout.stateRoot, plan: scout.prepared.plan, lease: scout.prepared.lease })).head.state, "failed");

  const semantic = await fixture(t, "researcher");
  tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: semantic.stateRoot, prepared: semantic.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "200000000002", running: "200000000003", failed: "200000000004" },
    candidateRunner: async (_prepared, claim) => { const candidate = researchCandidate(claim); candidate.verification.semanticEntailmentVerified = true; return candidate; },
  }), /durably closed/);
  assert.equal((await recoverCheckpointLedger({ stateRoot: semantic.stateRoot, plan: semantic.prepared.plan, lease: semantic.prepared.lease })).head.state, "failed");

  const total = await fixture(t, "data-lab");
  tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: total.stateRoot, prepared: total.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "300000000002", running: "300000000003", failed: "300000000004" },
    candidateRunner: async (_prepared, claim) => { const candidate = dataCandidate(claim); candidate.artifacts.totalBytes += 1; return candidate; },
  }), /durably closed/);

  const reserve = await fixture(t, "researcher");
  tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: reserve.stateRoot, prepared: reserve.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "310000000002", running: "310000000003", failed: "310000000004" },
    candidateRunner: async (_prepared, claim) => {
      const candidate = researchCandidate(claim);
      candidate.artifacts.evidence.bytes = reserve.prepared.lease.budgets.maxArtifactBytes - candidate.artifacts.report.bytes - candidate.artifacts.verification.bytes - (64 * 1024) + 1;
      candidate.artifacts.totalBytes = candidate.artifacts.report.bytes + candidate.artifacts.verification.bytes + candidate.artifacts.evidence.bytes;
      return candidate;
    },
  }), /durably closed/);
});

test("candidate failures retain only a bounded content-free stage and sanitize arbitrary values", async (t) => {
  const staged = await fixture(t, "scout");
  let tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: staged.stateRoot, prepared: staged.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "320000000002", running: "320000000003", failed: "320000000004" },
    candidateRunner: async () => {
      const error = new Error("private provider text must not be retained");
      error.workCleanupComplete = true;
      error.workFailureStage = "proposal-parse";
      throw error;
    },
  }), /durably closed/);
  const stagedLedger = await recoverCheckpointLedger({ stateRoot: staged.stateRoot, plan: staged.prepared.plan, lease: staged.prepared.lease });
  assert.equal(stagedLedger.head.progress.failureStage, "proposal-parse");
  assert.doesNotMatch(JSON.stringify(stagedLedger.head), /private provider text/u);

  const sanitized = await fixture(t, "scout");
  tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: sanitized.stateRoot, prepared: sanitized.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "330000000002", running: "330000000003", failed: "330000000004" },
    candidateRunner: async () => {
      const error = new Error("another private value");
      error.workCleanupComplete = true;
      error.workFailureStage = "secret/provider/raw-output";
      throw error;
    },
  }), /durably closed/);
  const sanitizedLedger = await recoverCheckpointLedger({ stateRoot: sanitized.stateRoot, plan: sanitized.prepared.plan, lease: sanitized.prepared.lease });
  assert.equal(sanitizedLedger.head.progress.failureStage, "profile-execution");
  assert.doesNotMatch(JSON.stringify(sanitizedLedger.head), /secret|private value/u);
});

test("an unclean crash remains cleanup-blocked until exact cleanup closes the single-use attempt", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "400000000002", running: "400000000003", failed: "400000000004" },
    candidateRunner: async () => { const error = new Error("hard interruption"); error.workCleanupComplete = false; throw error; },
  }), /cleanup-failed/);
  let ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.equal(ledger.head.state, "cleanup-failed");
  assert.equal(ledger.head.progress.failureStage, "cleanup");
  const charged = structuredClone(ledger.head.usage);
  const consumption = JSON.parse(await readFile(join(value.stateRoot, "claims", `${value.prepared.lease.leaseId}.json`), "utf8"));
  const cleanupPrepared = { ...value.prepared, cleanupOnly: true, recoveryConsumption: consumption };
  const recovered = await recordInterruptedCandidateFailure({
    stateRoot: value.stateRoot, prepared: cleanupPrepared, lifecycleOptions: {}, clock: () => new Date(tick++), checkpointSuffix: "400000000005",
    cleanup: async (_prepared, exactClaim) => {
      assert.deepEqual(exactClaim, consumption);
      return { resourcesRemoved: true, usage: { runtimeSeconds: 1, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 1 } };
    },
  });
  assert.equal(recovered.action, "failed");
  ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.equal(ledger.head.state, "failed");
  assert.equal(ledger.head.progress.failureStage, "cleanup");
  assert.deepEqual(ledger.head.usage, charged);
});

test("an unsafe cleanup inspection becomes terminal recovery-inconclusive", async (t) => {
  const value = await fixture(t, "scout");
  let tick = baseTime + 2;
  await assert.rejects(executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "410000000002", running: "410000000003", failed: "410000000004" },
    candidateRunner: async () => { const error = new Error("abrupt stop"); error.workCleanupComplete = false; throw error; },
  }), /cleanup-failed/);
  const consumption = JSON.parse(await readFile(join(value.stateRoot, "claims", `${value.prepared.lease.leaseId}.json`), "utf8"));
  const cleanupPrepared = { ...value.prepared, cleanupOnly: true, recoveryConsumption: consumption };
  await assert.rejects(recordInterruptedCandidateFailure({
    stateRoot: value.stateRoot, prepared: cleanupPrepared, lifecycleOptions: {}, clock: () => new Date(tick++), checkpointSuffix: "410000000005",
    cleanup: async () => { const error = new Error("result root identity is ambiguous"); error.workRecoveryInconclusive = true; throw error; },
  }), /recovery-inconclusive/);
  const ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "cleanup-failed", "recovery-inconclusive"]);
  assert.deepEqual(ledger.action, { action: "terminal", state: "recovery-inconclusive", replayLease: false });
  assert.match(ledger.head.progress.failureFingerprintSha256, /^[a-f0-9]{64}$/);
  await assert.rejects(recordInterruptedCandidateFailure({
    stateRoot: value.stateRoot, prepared: cleanupPrepared, lifecycleOptions: {}, clock: () => new Date(tick++), checkpointSuffix: "410000000006",
    cleanup: async () => { throw new Error("must not retry terminal recovery"); },
  }), /not cleanup-eligible/);
});

test("waiting authority remains stable even when the candidate consumes its exact budget ceiling", async (t) => {
  const value = await fixture(t, "researcher");
  let tick = baseTime + 2;
  const result = await executeCandidateWaitAttempt({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "500000000002", running: "500000000003", waiting: "500000000004" },
    candidateRunner: async (_prepared, claim) => {
      const candidate = researchCandidate(claim);
      candidate.proxyReceipt.modelRequests = value.prepared.lease.budgets.maxModelRequests;
      return candidate;
    },
  });
  const ledger = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  assert.equal(result.checkpoint.usage.modelRequests, value.prepared.lease.budgets.maxModelRequests);
  assert.equal(ledger.action.action, "wait-for-explicit-authority");
});

test("durable goal adapter runs one exact Researcher child and exposes only a content-free wait", async (t) => {
  const value = await fixture(t, "researcher", false);
  const goal = await initializeGoalFixture(value);
  let resolverTick = baseTime + 4;
  const resolver = createGoalCandidateResolver({
    stateRoot: value.stateRoot, goal, jobs: [value.request], policy: value.policy, objectStore: join(value.root, "objects"), clock: () => new Date(resolverTick++),
    suffix: (() => { let counter = 0x610000000000n; return () => (counter++).toString(16); })(),
  });
  let driverTick = baseTime + 20;
  let driverReceipt;
  let launches = 0;
  const driver = createGoalCandidateDriver({
    stateRoot: value.stateRoot, prepare: async ({ mode, context }) => {
      assert.equal(mode, "execute");
      assert.equal(context.job.profile, "researcher");
      return value.prepared;
    },
    lifecycleOptions: {}, clock: () => new Date(driverTick++),
    suffixes: () => ({ claim: "620000000001", running: "620000000002", waiting: "620000000003" }),
    discard: async () => {},
    candidateRunner: async (_prepared, claim) => { launches += 1; return researchCandidate(claim); },
  });
  let runtimeTick = baseTime + 5;
  const result = await runGoalCycle({
    stateRoot: value.stateRoot, goal, jobs: [value.request], resolveChildRun: resolver,
    driveChild: async (context) => { driverReceipt = await driver(context); },
    clock: () => new Date(runtimeTick++),
    suffix: (() => { let counter = 0x630000000000n; return () => (counter++).toString(16); })(),
  });
  assert.equal(result.goalState, "waiting-authority");
  assert.equal(result.action, "wait-for-child-authority");
  assert.equal(launches, 1);
  assert.deepEqual(driverReceipt, {
    action: "waiting-authority", childState: "waiting-authority", childCheckpointSha256: driverReceipt.childCheckpointSha256,
    grantsExecution: false, grantsCompletion: false, containsWorkerOutput: false,
  });
  assert.doesNotMatch(JSON.stringify(driverReceipt), /Safety finding|Public safety evidence/u);
  const repeated = await runGoalCycle({
    stateRoot: value.stateRoot, goal, jobs: [value.request], resolveChildRun: resolver,
    driveChild: async () => { throw new Error("waiting child must not relaunch"); },
    clock: () => new Date(runtimeTick++), suffix: () => "640000000001",
  });
  assert.equal(repeated.action, "wait-for-child-authority");
  assert.equal(launches, 1);
  const child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease });
  await acceptSemanticCandidate({
    stateRoot: value.stateRoot, plan: value.prepared.plan, lease: value.prepared.lease,
    confirmations: confirmations(child.head), clock: () => new Date(baseTime + 100),
    suffixes: { attestation: "640000000002", verified: "640000000003", completed: "640000000004" },
  });
  const completed = await runGoalCycle({
    stateRoot: value.stateRoot, goal, jobs: [value.request], resolveChildRun: resolver,
    driveChild: async () => { throw new Error("completed child must not relaunch"); },
    clock: () => new Date(runtimeTick++), suffix: (() => { let counter = 0x640000000005n; return () => (counter++).toString(16); })(),
  });
  assert.equal(completed.goalState, "completed");
  assert.equal(completed.action, "terminal");
  assert.equal(launches, 1);
});

test("candidate resolver refreshes a delayed Researcher milestone without widening it", async (t) => {
  const value = await fixture(t, "researcher", false);
  const goal = await initializeGoalFixture(value);
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal, jobs: [value.request], now: new Date(baseTime + 4), suffix: "640000000001" });
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal, jobs: [value.request] });
  let time = baseTime + 2 * 24 * 60 * 60 * 1000;
  let record = 0x640000000010n;
  const resolver = createGoalCandidateResolver({
    stateRoot: value.stateRoot, goal, jobs: [value.request], policy: value.policy, objectStore: join(value.root, "objects"),
    clock: () => new Date(time++), suffix: () => (record++).toString(16),
  });
  const run = await resolver({ job: value.request, goalCheckpoint: parent.head, childCheckpoint: null, childAction: null });
  const custody = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal, jobs: [value.request], jobId: value.request.jobId });
  assert.deepEqual(custody.bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh", "admission"]);
  assert.equal(Date.parse(run.plan.compiledAt), baseTime + 2 * 24 * 60 * 60 * 1000);
  assert.equal(run.plan.requestSha256, value.compiled.plan.requestSha256);
  assert.deepEqual(run.plan.research, value.compiled.plan.research);
  assert.deepEqual(run.lease.authority, value.compiled.lease.authority);
});

test("disk-derived candidate preparer selects only the registered profile and exact admitted custody", async (t) => {
  const value = await fixture(t, "data-lab", false);
  const goal = await initializeGoalFixture(value);
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal, jobs: [value.request], now: new Date(baseTime + 4), suffix: "650000000001" });
  const goalLedger = await recoverGoalLedger({ stateRoot: value.stateRoot, goal, jobs: [value.request] });
  const resolver = createGoalCandidateResolver({ stateRoot: value.stateRoot, goal, jobs: [value.request], policy: value.policy, objectStore: join(value.root, "objects"), clock: () => new Date(baseTime + 5), suffix: () => "650000000002" });
  const run = await resolver({ job: value.request, goalCheckpoint: goalLedger.head, childCheckpoint: null, childAction: null });
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: run.plan, lease: run.lease, workspaceSnapshotSha256: run.workspaceSnapshotSha256,
    now: new Date(baseTime + 6), suffix: "650000000003",
  });
  let calls = 0;
  const preparer = createGoalCandidatePreparer({
    stateRoot: value.stateRoot, goal, jobs: [value.request], policy: value.policy,
    objectStore: join(value.root, "objects"), workspaceRoot: join(value.root, "workspaces"), executorPath: join(value.root, "omp"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 }, clock: () => new Date(baseTime + 7),
    researcherRun: async () => { throw new Error("wrong profile preparer"); },
    dataLabRun: async (options) => { calls += 1; assert.equal(options.plan.profile, "data-lab"); return value.prepared; },
  });
  const context = {
    job: value.request, plan: run.plan, lease: run.lease, workspaceSnapshotSha256: run.workspaceSnapshotSha256,
    goalCheckpoint: goalLedger.head, childCheckpoint: initialized.checkpoint,
    childAction: { action: "dispatch-worker", state: "authorized", replayLease: false },
  };
  assert.equal(await preparer({ mode: "execute", context }), value.prepared);
  assert.equal(calls, 1);
  const substituted = structuredClone(context);
  substituted.job.objective = "substituted task";
  await assert.rejects(preparer({ mode: "execute", context: substituted }), /registry|contract|differs/);
});

test("production goal CLI routes a Researcher candidate through review, exact acceptance, and terminal restart", async (t) => {
  const value = await fixture(t, "researcher", false);
  const goal = await initializeGoalFixture(value);
  const { paths, config } = await controllerConfiguration(value, goal, "researcher-production");
  let cycleTick = baseTime + 5;
  let driverTick = baseTime + 20;
  let launches = 0;
  const candidateSuffix = suffixSource("710000000001");
  const dependencies = {
    clock: () => new Date(cycleTick++), suffix: suffixSource("700000000001"),
    candidateOverrides: {
      prepare: async ({ mode, context }) => {
        assert.equal(mode, "execute");
        assert.equal(context.job.profile, "researcher");
        return value.prepared;
      },
      candidateRunner: async (_prepared, claim, options) => {
        launches += 1;
        assert.equal(options.researchCourierQueueRoot, config.researchRuntime.researchCourierQueueRoot);
        assert.equal(options.researchEndpoint, config.researchRuntime.researchEndpoint);
        return retainCandidateArtifacts(value, claim, researchCandidate(claim));
      },
      discard: async () => {}, clock: () => new Date(driverTick++), resolverSuffix: suffixSource("720000000001"),
      suffixes: () => ({ claim: candidateSuffix(), running: candidateSuffix(), waiting: candidateSuffix(), failed: candidateSuffix() }),
    },
  };

  const dispatched = await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  assert.equal(dispatched.action, "controller-yield");
  assert.equal(dispatched.status, "running");
  assert.equal(dispatched.schedulingEffect, "event-or-watchdog-recovery");
  const waiting = await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  assert.equal(waiting.action, "wait-for-child-authority");
  assert.equal(waiting.status, "waiting-authority");
  assert.equal(waiting.childState, null);
  assert.equal(waiting.schedulingEffect, "event-noop");
  assert.equal(launches, 1);
  assert.doesNotMatch(JSON.stringify(waiting), /Safety finding|Public safety evidence/u);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.controllerState, "ready");
  assert.equal(heartbeat.sessions[0].state, "waiting-authority");
  assert.equal(heartbeat.sessions[0].boundaryState.state, "waiting-approval");
  assert.equal(heartbeat.sessions[0].controls.browserCanResume, false);
  assert.doesNotMatch(JSON.stringify(heartbeat), /Safety finding|Public safety evidence/u);

  const repeated = await runGoalCycleCommand(["--config", paths.configPath], {
    ...dependencies,
    candidateOverrides: { ...dependencies.candidateOverrides, candidateRunner: async () => { throw new Error("waiting candidate must not relaunch"); } },
  });
  assert.equal(repeated.action, "wait-for-child-authority");
  assert.equal(repeated.schedulingEffect, "event-noop");
  assert.equal(launches, 1);

  const review = await runGoalAcceptCommand(["review", "--config", paths.configPath]);
  assert.equal(review.status, "waiting-authority");
  assert.equal(review.completionEvidenceRecorded, false);
  assert.equal(review.authority.grantsCompletionEvidence, false);
  assert.doesNotMatch(JSON.stringify(review), /Safety finding|Public safety evidence/u);
  const privateReview = await buildGoalSemanticReview(paths.configPath);
  assert.equal(privateReview.profile, "researcher");
  assert.equal(privateReview.content.title, "Safety finding");
  assert.equal(privateReview.content.findings[0].evidence[0].excerpt, "Public safety evidence");
  assert.equal(privateReview.verification.method, "deterministic-public-evidence-presence");
  assert.equal(privateReview.verification.semanticAccuracyVerified, false);
  assert.equal(privateReview.reviewSha256, review.reviewSha256);
  assert.deepEqual(Object.values(privateReview.authority), Array(9).fill(false));
  await assert.rejects(runGoalAcceptCommand([
    "accept", "--config", paths.configPath, "--confirm-review-sha256", digest("f"),
  ]), /confirmation differs/);
  const [candidateDirectory] = await readdir(join(value.stateRoot, "results"));
  const reportPath = join(value.stateRoot, "results", candidateDirectory, "research-report.json");
  const originalReport = await readFile(reportPath);
  await writeFile(reportPath, Buffer.from("{}\n", "utf8"), { mode: 0o600 });
  await assert.rejects(buildGoalSemanticReview(paths.configPath), /retained artifact set differs|report is invalid/);
  await assert.rejects(runGoalAcceptCommand([
    "accept", "--config", paths.configPath, "--confirm-review-sha256", review.reviewSha256,
  ]), /retained artifact set differs|report is invalid/);
  await writeFile(reportPath, originalReport, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(reportPath, 0o600);
  const accepted = await runGoalAcceptCommand([
    "accept", "--config", paths.configPath, "--confirm-review-sha256", review.reviewSha256,
  ], {
    clock: (() => { let tick = baseTime + 100; return () => new Date(tick++); })(),
    suffixes: { attestation: "730000000001", verified: "730000000002", completed: "730000000003" },
  });
  assert.equal(accepted.status, "completed");
  assert.equal(accepted.completionEvidenceRecorded, true);
  assert.equal(accepted.authority.grantsCompletionEvidence, true);
  assert.equal(launches, 1);

  let terminal;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    terminal = await runGoalCycleCommand(["--config", paths.configPath], {
      ...dependencies,
      candidateOverrides: { ...dependencies.candidateOverrides, candidateRunner: async () => { throw new Error("completed candidate must not relaunch"); } },
    });
    if (terminal.action === "terminal") break;
  }
  assert.equal(terminal.action, "terminal");
  assert.equal(terminal.status, "completed");
  assert.equal(terminal.schedulingEffect, "event-noop");
  assert.equal(launches, 1);

  const missingRuntimePath = join(value.root, "missing-research-runtime.json");
  const { researchRuntime: _removed, ...missingRuntime } = config;
  await writeFile(missingRuntimePath, `${JSON.stringify(missingRuntime)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(missingRuntimePath, 0o600);
  await assert.rejects(runGoalCycleCommand(["--config", missingRuntimePath], dependencies), /research runtime presence differs/);
  const invalidEndpointPath = join(value.root, "invalid-research-endpoint.json");
  await writeFile(invalidEndpointPath, `${JSON.stringify({ ...config, researchRuntime: { ...config.researchRuntime, researchEndpoint: "http://127.0.0.1:99999" } })}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(invalidEndpointPath, 0o600);
  await assert.rejects(runGoalCycleCommand(["--config", invalidEndpointPath], dependencies), /research endpoint is invalid/);
});

test("production stop cleanup and exact cancellation close an interrupted Researcher without replay", async (t) => {
  const value = await fixture(t, "researcher", false);
  const goal = await initializeGoalFixture(value);
  const { paths, config } = await controllerConfiguration(value, goal, "researcher-cleanup");
  let cycleTick = baseTime + 5;
  let driverTick = baseTime + 20;
  const candidateSuffix = suffixSource("750000000001");
  const dependencies = {
    clock: () => new Date(cycleTick++), suffix: suffixSource("740000000001"),
    candidateOverrides: {
      prepare: async () => value.prepared, discard: async () => {}, clock: () => new Date(driverTick++),
      resolverSuffix: suffixSource("760000000001"),
      suffixes: () => ({ claim: candidateSuffix(), running: candidateSuffix(), failed: candidateSuffix() }),
      candidateRunner: async () => { const error = new Error("simulated abrupt stop"); error.workCleanupComplete = false; throw error; },
    },
  };
  await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  await assert.rejects(runGoalCycleCommand(["--config", paths.configPath], dependencies), /cleanup-failed/);
  await rm(config.researchRuntime.researchCourierQueueRoot, { recursive: true, force: true });
  let cleanupCalls = 0;
  const cleaned = await runGoalCleanupCommand(["--config", paths.configPath], {
    cleanup: async (_prepared, exactClaim) => {
      cleanupCalls += 1;
      assert.equal(exactClaim.jobId, value.request.jobId);
      return { resourcesRemoved: true, usage: { runtimeSeconds: 1, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 1 } };
    },
  });
  assert.equal(cleaned.action, "interrupted-child-cleaned");
  assert.equal(cleaned.childState, "failed");
  assert.equal(cleanupCalls, 1);
  const cleanupHeartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(cleanupHeartbeat.sessions[0].state, "failed");
  assert.equal(cleanupHeartbeat.sessions[0].activity[0].summaryCode, "session-failed");
  assert.equal(cleanupHeartbeat.sessions[0].controls.browserCanResume, false);
  const noReplay = await runGoalCleanupCommand(["--config", paths.configPath], { cleanup: async () => { throw new Error("cleanup must not replay"); } });
  assert.equal(noReplay.action, "cleanup-not-required");
  assert.equal(cleanupCalls, 1);
  const cancelled = await runGoalCancelCommand(["--config", paths.configPath, "--confirm-goal-sha256", goalSha256(goal)]);
  assert.equal(cancelled.status, "cancelled");
  assert.equal(cancelled.action, "cancelled-after-cleanup");
});

test("production goal CLI routes a confidential Data Lab candidate without research authority", async (t) => {
  const value = await fixture(t, "data-lab", false);
  const goal = await initializeGoalFixture(value);
  const { paths, config } = await controllerConfiguration(value, goal, "data-lab-production");
  assert.equal(config.researchRuntime, undefined);
  let cycleTick = baseTime + 5;
  let driverTick = baseTime + 20;
  let launches = 0;
  const records = suffixSource("780000000001");
  const dependencies = {
    clock: () => new Date(cycleTick++), suffix: suffixSource("770000000001"),
    candidateOverrides: {
      prepare: async ({ context }) => { assert.equal(context.job.profile, "data-lab"); return value.prepared; },
      candidateRunner: async (_prepared, claim, options) => {
        launches += 1;
        assert.equal(options.researchCourierQueueRoot, undefined);
        assert.equal(options.researchEndpoint, undefined);
        return retainCandidateArtifacts(value, claim, dataCandidate(claim));
      },
      discard: async () => {}, clock: () => new Date(driverTick++), resolverSuffix: suffixSource("790000000001"),
      suffixes: () => ({ claim: records(), running: records(), waiting: records(), failed: records() }),
    },
  };
  await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  const waiting = await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  assert.equal(waiting.status, "waiting-authority");
  assert.equal(waiting.action, "wait-for-child-authority");
  assert.equal(waiting.schedulingEffect, "event-noop");
  assert.equal(launches, 1);
  const review = await runGoalAcceptCommand(["review", "--config", paths.configPath]);
  assert.equal(review.profile, "data-lab");
  assert.equal(review.completionEvidenceRecorded, false);
  assert.doesNotMatch(JSON.stringify(review), /Local result|regional totals|confidential/u);
  const privateReview = await buildGoalSemanticReview(paths.configPath);
  assert.equal(privateReview.profile, "data-lab");
  assert.equal(privateReview.dataClassification, "confidential");
  assert.equal(privateReview.content.title, "Local result");
  assert.equal(privateReview.content.artifacts[0].path, "artifacts/summary.csv");
  assert.equal(privateReview.verification.method, "networkless-exact-replay");
  assert.equal(privateReview.privacy.privateContentIncluded, true);
  assert.equal(privateReview.completionEffect, "none-read-only");
  const [candidateDirectory] = await readdir(join(value.stateRoot, "results"));
  const artifactPath = join(value.stateRoot, "results", candidateDirectory, "artifacts", "summary.csv");
  const originalArtifact = await readFile(artifactPath);
  await writeFile(artifactPath, Buffer.from("region,total\nnorth,11\n", "utf8"), { mode: 0o600 });
  await assert.rejects(runGoalAcceptCommand(["review", "--config", paths.configPath]), /artifact differs from its manifest/);
  await writeFile(artifactPath, originalArtifact, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(artifactPath, 0o600);

  const extraRuntimePath = join(value.root, "extra-research-runtime.json");
  const extraQueue = join(value.root, "unneeded-research-courier");
  await mkdir(extraQueue, { mode: 0o700 });
  await writeFile(extraRuntimePath, `${JSON.stringify({ ...config, researchRuntime: { researchCourierQueueRoot: extraQueue, researchEndpoint: "http://127.0.0.1:8888" } })}\n`, { mode: 0o600 });
  if (process.platform !== "win32") { await chmod(extraQueue, 0o700); await chmod(extraRuntimePath, 0o600); }
  await assert.rejects(runGoalCycleCommand(["--config", extraRuntimePath], dependencies), /research runtime presence differs/);
});

test("production goal CLI routes Scout through exact local evidence review without self-completion", async (t) => {
  const value = await fixture(t, "scout", false);
  const goal = await initializeGoalFixture(value);
  const { paths, config } = await controllerConfiguration(value, goal, "scout-production");
  assert.equal(config.researchRuntime, undefined);
  let cycleTick = baseTime + 5;
  let driverTick = baseTime + 20;
  let launches = 0;
  const records = suffixSource("7b0000000001");
  const dependencies = {
    clock: () => new Date(cycleTick++), suffix: suffixSource("7a0000000001"),
    candidateOverrides: {
      prepare: async ({ context }) => { assert.equal(context.job.profile, "scout"); return value.prepared; },
      candidateRunner: async (_prepared, claim, options) => {
        launches += 1;
        assert.equal(options.researchCourierQueueRoot, undefined);
        return retainCandidateArtifacts(value, claim, scoutCandidate(claim));
      },
      discard: async () => {}, clock: () => new Date(driverTick++), resolverSuffix: suffixSource("7c0000000001"),
      suffixes: () => ({ claim: records(), running: records(), waiting: records(), failed: records() }),
    },
  };
  await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  const waiting = await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  assert.equal(waiting.status, "waiting-authority");
  assert.equal(waiting.action, "wait-for-child-authority");
  assert.equal(waiting.schedulingEffect, "event-noop");
  assert.equal(launches, 1);
  const review = await runGoalAcceptCommand(["review", "--config", paths.configPath]);
  assert.equal(review.profile, "scout");
  assert.equal(review.completionEvidenceRecorded, false);
  assert.doesNotMatch(JSON.stringify(review), /candidate invariant|local fixture|internal/u);
  const privateReview = await buildGoalSemanticReview(paths.configPath);
  assert.equal(privateReview.profile, "scout");
  assert.equal(privateReview.content.title, "Scout finding");
  assert.equal(privateReview.content.findings[0].statement, "The local fixture contains the candidate invariant.");
  assert.equal(privateReview.content.findings[0].evidence[0].excerpt, "const invariant = 42");
  assert.equal(privateReview.verification.method, "deterministic-local-evidence-presence");
  assert.equal(privateReview.verification.semanticAccuracyVerified, false);
  const accepted = await runGoalAcceptCommand([
    "accept", "--config", paths.configPath, "--confirm-review-sha256", review.reviewSha256,
  ], {
    clock: (() => { let tick = baseTime + 100; return () => new Date(tick++); })(),
    suffixes: { attestation: "7d0000000001", verified: "7d0000000002", completed: "7d0000000003" },
  });
  assert.equal(accepted.status, "completed");
  let repeated = await runGoalCycleCommand(["--config", paths.configPath], {
    ...dependencies,
    candidateOverrides: { ...dependencies.candidateOverrides, candidateRunner: async () => { throw new Error("completed Scout must not relaunch"); } },
  });
  if (repeated.action !== "terminal") repeated = await runGoalCycleCommand(["--config", paths.configPath], dependencies);
  assert.equal(repeated.action, "terminal");
  assert.equal(repeated.status, "completed");
  assert.equal(launches, 1);
});

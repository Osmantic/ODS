import { createHash } from "node:crypto";
import { lstat, readdir } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  canonical, validateWorkDataArtifactManifest, validateWorkDataReport, validateWorkDataVerification,
  validateWorkResearchReport, validateWorkResearchVerification, validateWorkScoutReport,
  validateWorkScoutVerification,
} from "../../scripts/lib/work-contract.mjs";
import { recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import { candidateArtifactSetSha256 } from "./candidate-wait-loop.mjs";
import { checkpointSha256, recoverCheckpointLedger } from "./checkpoints.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { recoverGoalRunBundles } from "./goal-run-bundles.mjs";
import { recoverGoalLedger } from "./goals.mjs";
import { acceptSemanticCandidate } from "./semantic-acceptance.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const boundary = "Content-free local semantic review and exact-confirmed acceptance only. Acceptance records completion evidence for one already safe Scout, Researcher, or Data Lab candidate; it grants no execution, lease, replay, scope expansion, external effect, publication, deployment, or policy authority.";
const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false });

export class GoalAcceptCliError extends Error {}

function fail(message) { throw new GoalAcceptCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (Array.isArray(argv) && argv.length === 3 && argv[0] === "review" && argv[1] === "--config" && typeof argv[2] === "string" && argv[2]) return { operation: "review", configPath: resolve(argv[2]), confirmation: null };
  if (
    Array.isArray(argv) && argv.length === 5 && argv[0] === "accept" && argv[1] === "--config" && typeof argv[2] === "string" && argv[2]
    && argv[3] === "--confirm-review-sha256" && SHA_RE.test(argv[4] ?? "")
  ) return { operation: "accept", configPath: resolve(argv[2]), confirmation: argv[4] };
  fail("Usage: goal-accept-cli.mjs review --config FILE | accept --config FILE --confirm-review-sha256 HASH");
}

function candidateConfirmations(checkpoint) {
  if (
    checkpoint?.state !== "waiting-authority" || !SHA_RE.test(checkpoint.artifactManifestSha256 ?? "")
    || !SHA_RE.test(checkpoint.verificationEvidenceSha256 ?? "")
  ) fail("goal semantic review has no waiting safe candidate");
  return {
    candidateCheckpointSha256: checkpointSha256(checkpoint),
    acceptanceCriteriaSha256: checkpoint.acceptanceCriteriaSha256,
    artifactManifestSha256: checkpoint.artifactManifestSha256,
    deterministicVerificationSha256: checkpoint.verificationEvidenceSha256,
  };
}

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

async function reviewFile(path, maximum, label) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  return { path, bytes: bytes.length, sha256: sha(bytes), content: bytes };
}

function artifactReference(record) { return { path: record.path, bytes: record.bytes, sha256: record.sha256 }; }

function parseReviewJson(record, validator, label) {
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(record.content); }
  catch { fail(`semantic review ${label} is not strict UTF-8`); }
  let value;
  try { value = JSON.parse(text); } catch { fail(`semantic review ${label} is not JSON`); }
  const errors = validator(value);
  if (errors.length) fail(`semantic review ${label} is invalid: ${errors[0]}`);
  return value;
}

async function dataArtifactPaths(root, current = root, collected = []) {
  await privateDirectory(current, "semantic review artifact directory");
  const entries = await readdir(current, { withFileTypes: true });
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const path = join(current, entry.name);
    if (entry.isSymbolicLink()) fail("semantic review artifact tree contains a symbolic link");
    if (entry.isDirectory()) await dataArtifactPaths(root, path, collected);
    else if (entry.isFile()) collected.push(relative(root, path).split(sep).join("/"));
    else fail("semantic review artifact tree contains an unsupported entry");
    if (collected.length > 100000) fail("semantic review artifact tree exceeds its file ceiling");
  }
  return collected;
}

async function exactReviewArtifacts(config, plan, claim, checkpoint) {
  const stateRoot = await privateDirectory(resolve(config.stateRoot), "semantic review state root");
  const resultsRoot = await privateDirectory(join(stateRoot, "results"), "semantic review results root");
  const root = await privateDirectory(join(resultsRoot, claim.claimId), "semantic review candidate root");
  const expectedNames = plan.profile === "scout"
    ? ["scout-evidence.json", "scout-report.json", "scout-verification.json"]
    : plan.profile === "researcher"
      ? ["research-evidence.json", "research-report.json", "research-verification.json"]
      : ["artifact-manifest.json", "artifacts", "data-evidence.json", "data-report.json", "data-verification.json", "recipe.py", "replay-inventory.json"];
  const observedNames = (await readdir(root)).sort();
  if (canonical(observedNames) !== canonical(expectedNames)) fail("semantic review candidate artifact set is incomplete or contains extras");
  const maximum = plan.budgets.maxArtifactBytes;
  const names = plan.profile === "scout" ? {
    report: "scout-report.json", verification: "scout-verification.json", evidence: "scout-evidence.json",
  } : plan.profile === "researcher" ? {
    report: "research-report.json", verification: "research-verification.json", evidence: "research-evidence.json",
  } : {
    report: "data-report.json", verification: "data-verification.json", evidence: "data-evidence.json",
    manifest: "artifact-manifest.json", recipe: "recipe.py", replayInventory: "replay-inventory.json",
  };
  const records = Object.fromEntries(await Promise.all(Object.entries(names).map(async ([key, name]) => [key, await reviewFile(join(root, name), maximum, `semantic review ${key}`)])));
  if (records.verification.sha256 !== checkpoint.verificationEvidenceSha256) fail("semantic review deterministic verifier differs from the waiting checkpoint");
  const validators = plan.profile === "scout"
    ? { report: validateWorkScoutReport, verification: validateWorkScoutVerification }
    : plan.profile === "researcher"
      ? { report: validateWorkResearchReport, verification: validateWorkResearchVerification }
      : { report: validateWorkDataReport, verification: validateWorkDataVerification };
  const parsed = {
    report: parseReviewJson(records.report, validators.report, "report"),
    verification: parseReviewJson(records.verification, validators.verification, "verification"),
  };
  if (plan.profile === "data-lab") {
    const manifest = parseReviewJson(records.manifest, validateWorkDataArtifactManifest, "Data Lab manifest");
    parsed.manifest = manifest;
    if (
      validateWorkDataArtifactManifest(manifest).length || manifest.jobId !== claim.jobId || manifest.claimId !== claim.claimId
      || manifest.planSha256 !== claim.planSha256 || manifest.workspaceSha256 !== claim.workspaceSha256
      || manifest.recipe.sha256 !== records.recipe.sha256 || manifest.recipe.bytes !== records.recipe.bytes
    ) fail("semantic review Data Lab manifest differs from the exact claim or retained recipe");
    const artifactRoot = await privateDirectory(join(root, "artifacts"), "semantic review Data Lab artifacts root");
    const expectedArtifacts = manifest.artifacts.map((artifact) => artifact.path).sort();
    const observedArtifacts = (await dataArtifactPaths(root, artifactRoot)).sort();
    if (canonical(observedArtifacts) !== canonical(expectedArtifacts)) fail("semantic review Data Lab derived artifact inventory differs from its manifest");
    let totalBytes = 0;
    for (const artifact of manifest.artifacts) {
      if (!artifact.path.startsWith("artifacts/") || artifact.path.split("/").some((part) => !part || part === "." || part === "..")) fail("semantic review Data Lab artifact path is unsafe");
      const observed = await reviewFile(join(root, ...artifact.path.split("/")), Math.min(maximum, artifact.bytes), "semantic review Data Lab artifact");
      if (observed.bytes !== artifact.bytes || observed.sha256 !== artifact.sha256) fail("semantic review Data Lab artifact differs from its manifest");
      totalBytes += observed.bytes;
    }
    if (totalBytes !== manifest.totals.bytes || manifest.artifacts.length !== manifest.totals.files) fail("semantic review Data Lab artifact totals differ from its manifest");
  }
  const candidate = {
    artifacts: Object.fromEntries(Object.entries(records).filter(([key]) => key !== "replayInventory").map(([key, record]) => [key, artifactReference(record)])),
    ...(records.replayInventory ? { inventoryRecord: artifactReference(records.replayInventory) } : {}),
  };
  if (candidateArtifactSetSha256(plan.profile, candidate) !== checkpoint.artifactManifestSha256) fail("semantic review retained artifact set differs from the waiting checkpoint");
  return parsed;
}

async function exactCandidate(config, goal, jobs) {
  const parent = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  if (!parent.head.active || !["running", "waiting-authority"].includes(parent.head.state)) fail("goal semantic review has no active candidate");
  const job = jobs.find((value) => value.jobId === parent.head.active.jobId);
  if (!job || !["scout", "researcher", "data-lab"].includes(job.profile)) fail("goal semantic review active child is not a candidate profile");
  const run = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: job.jobId });
  const child = await recoverCheckpointLedger({ stateRoot: config.stateRoot, plan: run.head.plan, lease: run.head.lease });
  const candidate = [...child.checkpoints].reverse().find((checkpoint) => checkpoint.state === "waiting-authority");
  if (!candidate || !["waiting-authority", "verified", "completed"].includes(child.head.state)) fail("goal semantic review child has no acceptable candidate lineage");
  const claim = await recoverLeaseConsumption(config.stateRoot, run.head.lease);
  if (candidate.workerSessionSha256 !== sha(claim)) fail("goal semantic review claim differs from the waiting candidate");
  const artifacts = await exactReviewArtifacts(config, run.head.plan, claim, candidate);
  const confirmations = candidateConfirmations(candidate);
  return { parent, job, run, child, candidate, confirmations, reviewSha256: sha(confirmations), artifacts };
}

export async function inspectGoalSemanticCandidate(configPath) {
  const { config, goal, jobs } = await loadGoalCycleConfiguration(resolve(configPath), { requireResearchRuntimeDirectory: false });
  return exactCandidate(config, goal, jobs);
}

function receipt(value, operation, childState, completionEvidenceRecorded) {
  return {
    schemaVersion: 1, operation: `pixel-work-goal-${operation}-semantic-candidate`, status: childState,
    goalId: value.parent.head.goalId, jobId: value.job.jobId, profile: value.job.profile,
    reviewSha256: value.reviewSha256, candidateCheckpointSha256: value.confirmations.candidateCheckpointSha256,
    acceptanceCriteriaSha256: value.confirmations.acceptanceCriteriaSha256,
    artifactManifestSha256: value.confirmations.artifactManifestSha256,
    deterministicVerificationSha256: value.confirmations.deterministicVerificationSha256,
    completionEvidenceRecorded, authority: { ...authority, grantsCompletionEvidence: completionEvidenceRecorded }, boundary,
  };
}

export async function runGoalAcceptCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal semantic acceptance dependencies are invalid");
  if (dependencies.clock !== undefined && typeof dependencies.clock !== "function") fail("goal semantic acceptance clock is invalid");
  if (dependencies.suffixes !== undefined && (!dependencies.suffixes || typeof dependencies.suffixes !== "object" || Array.isArray(dependencies.suffixes))) fail("goal semantic acceptance suffixes are invalid");
  const request = parseArguments(argv);
  const { config, goal, jobs } = await loadGoalCycleConfiguration(request.configPath, { requireResearchRuntimeDirectory: false });
  let value = await exactCandidate(config, goal, jobs);
  if (request.operation === "review") return receipt(value, "review", value.child.head.state, false);
  if (request.confirmation !== value.reviewSha256) fail("goal semantic acceptance confirmation differs from the exact current review");
  const accepted = await acceptSemanticCandidate({
    stateRoot: config.stateRoot, plan: value.run.head.plan, lease: value.run.head.lease, confirmations: value.confirmations,
    ...(dependencies.clock ? { clock: dependencies.clock } : {}),
    ...(dependencies.suffixes ? { suffixes: dependencies.suffixes } : {}),
  });
  value = await exactCandidate(config, goal, jobs);
  if (accepted.checkpoint.state !== "completed" || value.child.head.state !== "completed") fail("goal semantic acceptance did not durably complete the child");
  return receipt(value, "accept", value.child.head.state, true);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalAcceptCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-accept: ${error instanceof GoalAcceptCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalAcceptBoundary = boundary;

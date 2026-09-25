import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import {
  canonical, validatePlanLease, validateWorkSemanticAcceptance,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { recoverLeaseConsumption } from "../work-runner/runner-core.mjs";
import { SEMANTIC_ACCEPTANCE_RESERVE_BYTES } from "./candidate-wait-loop.mjs";
import {
  appendCheckpoint, checkpointSha256, recoverCheckpointLedger,
} from "./checkpoints.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const supportedProfiles = new Set(["scout", "researcher", "data-lab"]);
const boundary = "Explicit local human attestation over exact immutable criteria and candidate hashes. It supplies semantic completion evidence only; it grants no execution, lease, replay, scope expansion, external effect, publication, deployment, or policy authority.";
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletionEvidence: true,
});

export class SemanticAcceptanceError extends Error {}

function fail(message) { throw new SemanticAcceptanceError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

function checkedSuffix(value) {
  const result = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(result)) fail("semantic acceptance suffix is invalid");
  return result;
}

function makeClock(head, clock) {
  let last = Date.parse(head.createdAt);
  return () => {
    const observed = clock();
    const milliseconds = observed instanceof Date ? observed.getTime() : Number(observed);
    if (!Number.isFinite(milliseconds)) fail("semantic acceptance clock is invalid");
    last = Math.max(last + 1, Math.trunc(milliseconds));
    return new Date(last);
  };
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return resolve(path);
}

function checkedInputs(plan, lease, confirmations) {
  const errors = validatePlanLease(plan, lease);
  if (errors.length || !supportedProfiles.has(plan?.profile) || lease.iteration !== 1 || lease.continuation !== null) fail("semantic acceptance requires one exact Scout, Researcher, or Data Lab plan and initial lease");
  exactKeys(confirmations, ["candidateCheckpointSha256", "acceptanceCriteriaSha256", "artifactManifestSha256", "deterministicVerificationSha256"], "semantic acceptance confirmations");
  for (const [field, value] of Object.entries(confirmations)) if (!SHA_RE.test(value ?? "")) fail(`semantic acceptance ${field} is invalid`);
}

function candidateFromLedger(ledger, confirmations) {
  const candidate = ledger.checkpoints.find((checkpoint) => checkpointSha256(checkpoint) === confirmations.candidateCheckpointSha256);
  if (!candidate || candidate.state !== "waiting-authority") fail("semantic acceptance candidate checkpoint is absent or not waiting");
  if (
    candidate.acceptanceCriteriaSha256 !== confirmations.acceptanceCriteriaSha256
    || candidate.artifactManifestSha256 !== confirmations.artifactManifestSha256
    || candidate.verificationEvidenceSha256 !== confirmations.deterministicVerificationSha256
    || candidate.progress.criteriaPassing !== 0 || candidate.progress.criteriaFailing !== candidate.progress.criteriaTotal
    || candidate.authorityExpansionObserved || candidate.acceptanceCriteriaMutationObserved || candidate.externalEffectsObserved
  ) fail("semantic acceptance confirmations differ from the exact safe candidate");
  return candidate;
}

function recordFor(plan, claim, candidate, now, recordSuffix) {
  const record = {
    $schema: "https://osmantic.com/pixel/schemas/work-semantic-acceptance-v1.schema.json", schemaVersion: 1,
    acceptanceId: `semanticacceptance-${String(now.getTime()).padStart(13, "0")}-${checkedSuffix(recordSuffix)}`,
    jobId: plan.jobId, claimId: claim.claimId, createdAt: now.toISOString(), planSha256: sha(plan),
    candidateCheckpointSha256: checkpointSha256(candidate), acceptanceCriteriaSha256: candidate.acceptanceCriteriaSha256,
    artifactManifestSha256: candidate.artifactManifestSha256,
    deterministicVerificationSha256: candidate.verificationEvidenceSha256,
    criteria: plan.acceptanceCriteria.map((_criterion, index) => ({ index, status: "pass" })),
    reviewMethod: "local-human-exact-hash", externalEffects: false, authority: { ...authority }, boundary,
  };
  const errors = validateWorkSemanticAcceptance(record);
  if (errors.length) fail(`semantic acceptance record is invalid: ${errors[0]}`);
  return record;
}

function checkRecord(record, plan, claim, candidate) {
  const errors = validateWorkSemanticAcceptance(record);
  if (errors.length) fail(`stored semantic acceptance is invalid: ${errors[0]}`);
  if (
    record.jobId !== plan.jobId || record.claimId !== claim.claimId || record.planSha256 !== sha(plan)
    || record.candidateCheckpointSha256 !== checkpointSha256(candidate)
    || record.acceptanceCriteriaSha256 !== candidate.acceptanceCriteriaSha256
    || record.artifactManifestSha256 !== candidate.artifactManifestSha256
    || record.deterministicVerificationSha256 !== candidate.verificationEvidenceSha256
    || record.criteria.length !== plan.acceptanceCriteria.length
  ) fail("stored semantic acceptance differs from the exact candidate");
  return record;
}

async function interruptedPublishTwin(jobRoot, candidateSha256, details) {
  const prefix = `.accept-${candidateSha256}-`;
  for (const entry of await readdir(jobRoot).catch(() => [])) {
    if (!entry.startsWith(prefix) || !/^[a-f0-9]{16}$/u.test(entry.slice(prefix.length))) continue;
    const info = await lstat(join(jobRoot, entry)).catch(() => null);
    if (
      info?.isFile() && !info.isSymbolicLink() && info.dev === details.dev && info.ino === details.ino
      && (process.platform === "win32" || (info.uid === process.geteuid() && (info.mode & 0o077) === 0))
    ) return true;
  }
  return false;
}

async function singleLinkOrInterruptedPublish(path, jobRoot, candidateSha256, details) {
  if (details.nlink === 1) return true;
  if (details.nlink !== 2) return false;
  if (await interruptedPublishTwin(jobRoot, candidateSha256, details)) return true;
  const current = await lstat(path).catch(() => null);
  return current?.isFile() && !current.isSymbolicLink() && current.dev === details.dev && current.ino === details.ino && current.nlink === 1;
}

async function readRecord(path, plan, claim, candidate, jobRoot, candidateSha256) {
  const { bytes, details } = await readBoundedRegularFile(path, SEMANTIC_ACCEPTANCE_RESERVE_BYTES, "semantic acceptance record");
  if (
    !await singleLinkOrInterruptedPublish(path, jobRoot, candidateSha256, details)
    || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)
  ) fail("semantic acceptance record is not private and single-link");
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail("semantic acceptance record is not strict UTF-8");
  let record;
  try { record = JSON.parse(text); } catch { fail("semantic acceptance record is not JSON"); }
  checkRecord(record, plan, claim, candidate);
  return { record, path, bytes: bytes.length, sha256: sha(bytes) };
}

async function publishRecord({ stateRoot, plan, claim, candidate, now, suffix }) {
  const state = await privateDirectory(stateRoot, "semantic acceptance state root");
  const root = join(state, "semantic-acceptance");
  await privateDirectory(root, "semantic acceptance root", true);
  const jobRoot = join(root, plan.jobId);
  await privateDirectory(jobRoot, "semantic acceptance job root", true);
  const candidateSha256 = checkpointSha256(candidate);
  const destination = join(jobRoot, `${candidateSha256}.json`);
  const existing = await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing) return readRecord(destination, plan, claim, candidate, jobRoot, candidateSha256);
  const record = recordFor(plan, claim, candidate, now, suffix);
  const serialized = Buffer.from(`${JSON.stringify(record, null, 2)}\n`, "utf8");
  if (serialized.length > SEMANTIC_ACCEPTANCE_RESERVE_BYTES) fail("semantic acceptance record exceeds its reserve");
  const temporary = join(jobRoot, `.accept-${candidateSha256}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(jobRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code !== "EEXIST") throw error;
  }
  return readRecord(destination, plan, claim, candidate, jobRoot, candidateSha256);
}

function checkpointUpdate(head, state, overrides = {}) {
  return {
    state, iteration: head.iteration, usage: { ...head.usage, ...(overrides.usage ?? {}) },
    progress: { ...head.progress, ...(overrides.progress ?? {}) }, workspaceSnapshotSha256: head.workspaceSnapshotSha256,
    artifactManifestSha256: head.artifactManifestSha256, workerSessionSha256: head.workerSessionSha256,
    verificationEvidenceSha256: overrides.verificationEvidenceSha256 ?? head.verificationEvidenceSha256,
    authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
  };
}

async function appendVerified({ stateRoot, plan, lease, candidate, attestation, nextTime, suffix }) {
  const artifactBytes = candidate.usage.artifactBytes + attestation.bytes;
  if (artifactBytes > plan.budgets.maxArtifactBytes) fail("semantic acceptance record exceeds the remaining artifact budget");
  return appendCheckpoint({
    stateRoot, plan, lease, previousCheckpointSha256: checkpointSha256(candidate), now: nextTime(), suffix: checkedSuffix(suffix),
    update: checkpointUpdate(candidate, "verified", {
      usage: { artifactBytes },
      progress: { criteriaPassing: candidate.progress.criteriaTotal, criteriaFailing: 0, failureFingerprintSha256: null },
      verificationEvidenceSha256: attestation.sha256,
    }),
  });
}

async function appendCompleted({ stateRoot, plan, lease, verified, nextTime, suffix }) {
  return appendCheckpoint({
    stateRoot, plan, lease, previousCheckpointSha256: checkpointSha256(verified), now: nextTime(), suffix: checkedSuffix(suffix),
    update: checkpointUpdate(verified, "completed"),
  });
}

export async function acceptSemanticCandidate({
  stateRoot, plan, lease, confirmations, clock = () => new Date(), suffixes = {},
}) {
  checkedInputs(plan, lease, confirmations);
  let ledger = await recoverCheckpointLedger({ stateRoot, plan, lease });
  const candidate = candidateFromLedger(ledger, confirmations);
  const claim = await recoverLeaseConsumption(stateRoot, lease);
  if (candidate.workerSessionSha256 !== sha(claim) || claim.planSha256 !== sha(plan) || claim.workspaceSha256 !== candidate.workspaceSnapshotSha256) fail("semantic acceptance claim differs from the candidate lineage");
  let nextTime = makeClock(ledger.head, clock);
  const attestation = await publishRecord({ stateRoot, plan, claim, candidate, now: nextTime(), suffix: suffixes.attestation });
  nextTime = makeClock({ createdAt: new Date(Math.max(Date.parse(ledger.head.createdAt), Date.parse(attestation.record.createdAt))).toISOString() }, clock);
  if (ledger.head.state === "waiting-authority") {
    try {
      await appendVerified({ stateRoot, plan, lease, candidate, attestation, nextTime, suffix: suffixes.verified });
    } catch (error) {
      ledger = await recoverCheckpointLedger({ stateRoot, plan, lease });
      if (!["verified", "completed"].includes(ledger.head.state) || ledger.head.verificationEvidenceSha256 !== attestation.sha256) throw error;
    }
    ledger = await recoverCheckpointLedger({ stateRoot, plan, lease });
  }
  if (ledger.head.state === "verified") {
    if (ledger.head.previousCheckpointSha256 !== checkpointSha256(candidate) || ledger.head.verificationEvidenceSha256 !== attestation.sha256 || ledger.head.progress.criteriaPassing !== ledger.head.progress.criteriaTotal) fail("semantic acceptance verified checkpoint differs from its attestation");
    try { await appendCompleted({ stateRoot, plan, lease, verified: ledger.head, nextTime, suffix: suffixes.completed }); } catch (error) {
      ledger = await recoverCheckpointLedger({ stateRoot, plan, lease });
      if (ledger.head.state !== "completed" || ledger.head.previousCheckpointSha256 !== checkpointSha256(ledger.checkpoints.at(-2))) throw error;
    }
    ledger = await recoverCheckpointLedger({ stateRoot, plan, lease });
  }
  if (ledger.head.state !== "completed" || ledger.head.verificationEvidenceSha256 !== attestation.sha256) fail("semantic acceptance did not durably complete the candidate");
  return { action: "completed", checkpoint: ledger.head, attestation };
}

export const semanticAcceptanceBoundary = boundary;

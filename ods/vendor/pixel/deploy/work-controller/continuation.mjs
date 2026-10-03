import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, readdir, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import {
  canonical, validatePlanLease, validateWorkCheckpoint, validateWorkConsumption, validateWorkLease, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { checkpointSha256, decideCheckpointAction } from "./checkpoints.mjs";

const SHA_RE = /^[a-f0-9]{64}$/;
const MAX_LEASE_BYTES = 64 * 1024;
const LEASE_BOUNDARY = "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.";
const cumulativeBudgets = Object.freeze({
  runtimeSeconds: "maxRuntimeSeconds",
  modelRequests: "maxModelRequests",
  inputTokens: "maxInputTokens",
  outputTokens: "maxOutputTokens",
  networkBytes: "maxNetworkBytes",
  artifactBytes: "maxArtifactBytes",
  failures: "maxFailures",
});

export class ContinuationLeaseError extends Error {}

function fail(message) {
  throw new ContinuationLeaseError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

function iso(date) {
  return date.toISOString().replace(/\.000Z$/, "Z");
}

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return path;
}

function validateInputs(plan, previousLease, policy, previousConsumption, checkpoint) {
  const policyErrors = validateWorkPolicy(policy);
  const leaseErrors = validatePlanLease(plan, previousLease);
  const consumptionErrors = validateWorkConsumption(previousConsumption);
  const checkpointErrors = validateWorkCheckpoint(checkpoint);
  if (policyErrors.length || leaseErrors.length || consumptionErrors.length || checkpointErrors.length) fail("continuation inputs failed their immutable contracts");
  if (sha(policy) !== plan.policySha256 || previousLease.policySha256 !== plan.policySha256) fail("continuation private policy differs from the immutable plan");
  if (
    checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== sha(plan) || checkpoint.inputSetSha256 !== plan.inputSetSha256
    || checkpoint.iteration !== previousLease.iteration || checkpoint.workerSessionSha256 !== sha(previousConsumption)
  ) fail("continuation checkpoint differs from the consumed iteration");
  if (
    previousConsumption.jobId !== plan.jobId || previousConsumption.leaseId !== previousLease.leaseId
    || previousConsumption.leaseSha256 !== sha(previousLease) || previousConsumption.planSha256 !== sha(plan)
    || previousConsumption.policySha256 !== plan.policySha256 || previousConsumption.inputSetSha256 !== plan.inputSetSha256
    || previousConsumption.status !== "consumed" || previousConsumption.externalEffects !== false
  ) fail("continuation worker consumption differs from the prior lease");
  if (decideCheckpointAction(checkpoint, plan).action !== "issue-continuation-lease") fail("checkpoint is not eligible for continuation");
  if (checkpoint.authorityExpansionObserved || checkpoint.acceptanceCriteriaMutationObserved || checkpoint.externalEffectsObserved) fail("continuation is blocked by a safety incident");
  return true;
}

function remainingBudgets(plan, checkpoint) {
  const budgets = { ...plan.budgets, maxIterations: plan.budgets.maxIterations - checkpoint.iteration };
  for (const [usage, budget] of Object.entries(cumulativeBudgets)) budgets[budget] = plan.budgets[budget] - checkpoint.usage[usage];
  if (
    budgets.maxIterations < 1 || budgets.maxRuntimeSeconds < 1 || budgets.maxModelRequests < 1 || budgets.maxInputTokens < 1
    || budgets.maxOutputTokens < 1 || budgets.maxNetworkBytes < 1 || budgets.maxArtifactBytes < (plan.profile === "builder" ? 1048576 : 1)
    || budgets.maxFailures < 1
  ) fail("continuation has no safely usable remaining budget");
  return budgets;
}

function buildContinuation(plan, previousLease, policy, previousConsumption, checkpoint, options = {}) {
  validateInputs(plan, previousLease, policy, previousConsumption, checkpoint);
  const now = options.now ?? new Date();
  const suffix = options.suffix ?? randomBytes(6).toString("hex");
  if (!Number.isFinite(now.getTime()) || !/^[a-f0-9]{12}$/.test(suffix)) fail("continuation identity input is invalid");
  if (now.getTime() <= Date.parse(checkpoint.createdAt) || now.getTime() < Date.parse(previousConsumption.claimedAt)) fail("continuation issuance predates its verified checkpoint");
  const budgets = remainingBudgets(plan, checkpoint);
  const lifetimeSeconds = Math.min(policy.maxLeaseSeconds, budgets.maxRuntimeSeconds);
  const epoch = String(now.getTime()).padStart(13, "0");
  const lease = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v1.schema.json",
    schemaVersion: 1,
    leaseId: `worklease-${epoch}-${suffix}`,
    jobId: plan.jobId,
    issuedAt: iso(now),
    expiresAt: iso(new Date(now.getTime() + lifetimeSeconds * 1000)),
    singleUse: true,
    iteration: checkpoint.iteration + 1,
    continuation: {
      previousCheckpointSha256: checkpointSha256(checkpoint),
      previousConsumptionSha256: sha(previousConsumption),
      cumulativeUsageSha256: sha(checkpoint.usage),
    },
    planSha256: sha(plan),
    inputSetSha256: plan.inputSetSha256,
    policySha256: plan.policySha256,
    executor: structuredClone(plan.executor),
    model: structuredClone(plan.model),
    isolation: structuredClone(plan.isolation),
    grantedCapabilities: structuredClone(plan.grantedCapabilities),
    budgets,
    authority: structuredClone(plan.authority),
    outputGate: structuredClone(plan.outputGate),
    boundary: LEASE_BOUNDARY,
  };
  const errors = [...validateWorkLease(lease), ...validatePlanLease(plan, lease)];
  if (errors.length) fail(`continuation lease is invalid: ${errors[0]}`);
  return lease;
}

function leaseName(iteration) {
  if (!Number.isSafeInteger(iteration) || iteration < 2 || iteration > 1000) fail("continuation iteration is invalid");
  return `${String(iteration).padStart(4, "0")}.json`;
}

async function crashOrphanTwin(leasesRoot, destinationName, details) {
  // publishLease stages the lease as a co-located .continuation-* temp, hardlinks it to its
  // NNNN.json name, then unlinks the temp. A crash between the link and unlink leaves the durable
  // lease at nlink=2 with its staging twin still present in leasesRoot. A same-inode staging entry
  // (excluding the destination itself) proves that interrupted publish, so recovery reads the
  // durable lease instead of wedging permanently, while an alias anywhere else has no such twin
  // and stays rejected as tampering. Read-only: it never races a concurrent writer's cleanup.
  for (const entry of await readdir(leasesRoot).catch(() => [])) {
    if (entry === destinationName) continue;
    const info = await lstat(join(leasesRoot, entry)).catch(() => null);
    if (info?.isFile() && info.ino === details.ino && info.dev === details.dev) return true;
  }
  return false;
}

async function publishLease(leasesRoot, lease) {
  const destination = join(leasesRoot, leaseName(lease.iteration));
  const temporary = join(leasesRoot, `.continuation-${lease.iteration}-${randomBytes(8).toString("hex")}`);
  const serialized = `${JSON.stringify(lease, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_LEASE_BYTES) fail("continuation lease exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(leasesRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("continuation lease for this iteration already exists");
    throw error;
  }
  return { lease, path: destination, sha256: sha(lease) };
}

export async function issueContinuationLease({ stateRoot, plan, previousLease, policy, previousConsumption, checkpoint, now = new Date(), suffix }) {
  const root = resolve(stateRoot);
  await privateDirectory(root, "continuation state root");
  const leasesRoot = join(root, "checkpoints", plan.jobId, "leases");
  await privateDirectory(leasesRoot, "continuation lease ledger");
  const lease = buildContinuation(plan, previousLease, policy, previousConsumption, checkpoint, { now, suffix });
  return publishLease(leasesRoot, lease);
}

export async function readContinuationLease({ stateRoot, plan, previousLease, policy, previousConsumption, checkpoint, iteration, now = new Date(), allowNotYetValid = false }) {
  const root = resolve(stateRoot);
  await privateDirectory(root, "continuation state root");
  const leasesRoot = join(root, "checkpoints", plan.jobId, "leases");
  await privateDirectory(leasesRoot, "continuation lease ledger");
  const destinationName = leaseName(iteration);
  const path = join(leasesRoot, destinationName);
  const { text, details } = await readBoundedRegularText(path, MAX_LEASE_BYTES, "continuation lease");
  const ownerPrivate = process.platform === "win32" || (details.uid === process.geteuid() && (details.mode & 0o077) === 0);
  const singleLink = details.nlink === 1 || (details.nlink === 2 && await crashOrphanTwin(leasesRoot, destinationName, details));
  if (!ownerPrivate || !singleLink) fail("continuation lease is not private and single-link");
  let lease;
  try { lease = JSON.parse(text); } catch { fail("continuation lease is not JSON"); }
  validateInputs(plan, previousLease, policy, previousConsumption, checkpoint);
  const errors = [...validateWorkLease(lease), ...validatePlanLease(plan, lease)];
  if (
    errors.length || iteration !== checkpoint.iteration + 1 || lease.iteration !== iteration
    || lease.continuation?.previousCheckpointSha256 !== checkpointSha256(checkpoint)
    || canonical(lease.budgets) !== canonical(remainingBudgets(plan, checkpoint))
  ) fail("persisted continuation lease differs from the recovery checkpoint");
  if (!SHA_RE.test(lease.continuation?.previousConsumptionSha256 ?? "") || lease.continuation.previousConsumptionSha256 !== sha(previousConsumption) || lease.continuation.cumulativeUsageSha256 !== sha(checkpoint.usage)) fail("persisted continuation lease recovery binding is invalid");
  const nowMs = now.getTime();
  const issuedAt = Date.parse(lease.issuedAt);
  const expiresAt = Date.parse(lease.expiresAt);
  const expectedLifetimeMilliseconds = Math.min(policy.maxLeaseSeconds, lease.budgets.maxRuntimeSeconds) * 1000;
  if (!Number.isFinite(nowMs)) fail("persisted continuation recovery clock is invalid");
  if (issuedAt <= Date.parse(checkpoint.createdAt) || issuedAt < Date.parse(previousConsumption.claimedAt)) fail("persisted continuation lease chronology is invalid");
  if (expiresAt - issuedAt !== expectedLifetimeMilliseconds) fail("persisted continuation lease lifetime is invalid");
  if (nowMs < issuedAt && allowNotYetValid !== true) fail("persisted continuation lease is not yet valid");
  if (nowMs >= expiresAt) fail("persisted continuation lease is expired");
  return { lease, path, sha256: sha(lease) };
}

export const continuationInternals = Object.freeze({ buildContinuation, remainingBudgets });

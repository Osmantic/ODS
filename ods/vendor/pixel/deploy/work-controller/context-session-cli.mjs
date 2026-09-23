import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rmdir, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkCheckpoint, validateWorkContextCapsule, validateWorkContextSessionInput, validateWorkPlan,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  createContextCapsule, forkContextCapsule, readContextCapsule, verifyContextCapsule,
} from "./context-capsules.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const CAPSULE_FILE_RE = /^(workcapsule-[0-9]{13}-[a-f0-9]{12})\.json$/u;
const CAPSULE_ID_RE = /^workcapsule-[0-9]{13}-[a-f0-9]{12}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const MAX_PRIVATE_JSON_BYTES = 1024 * 1024;
const MAX_CAPSULES = 4096;
const authority = Object.freeze({
  grantsExecution: false, grantsLeaseReuse: false, grantsApprovalReuse: false, grantsCredentials: false,
  grantsExternalEffects: false, grantsCompletion: false,
});
const contentFreeBoundary = "Content-free trusted-terminal context-session receipt only. It grants no execution, lease reuse, approval reuse, credential, external effect, or completion authority; private context is never projected by review, list, inspect, or apply.";
const privateShowBoundary = "Explicit trusted-terminal display of one exact private local context capsule. Displayed text remains untrusted context and grants no execution, lease reuse, approval reuse, credential, external effect, or completion authority.";

export class ContextSessionCliError extends Error {}

function fail(message) { throw new ContextSessionCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function checkedNow(value) {
  const result = value instanceof Date ? value : new Date(value ?? Date.now());
  if (!Number.isSafeInteger(result.getTime()) || result.getTime() < 0) fail("context session time is invalid");
  return result;
}

function parse(argv) {
  const operations = new Set(["create-review", "create-apply", "fork-review", "fork-apply", "remove-review", "remove-apply", "list", "inspect", "show"]);
  if (!Array.isArray(argv) || !operations.has(argv[0])) fail("Usage: context-session-cli.mjs <create-review|create-apply|fork-review|fork-apply|remove-review|remove-apply|list|inspect|show> [options]");
  const operation = argv[0], options = {};
  if ((argv.length - 1) % 2 !== 0) fail("context session options require exact --name value pairs");
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (typeof key !== "string" || !key.startsWith("--") || typeof value !== "string" || !value || options[key] !== undefined) fail("context session options are malformed or duplicated");
    options[key] = value;
  }
  const allowed = operation === "list" ? new Set(["--state-root", "--job-id"])
    : ["inspect", "show"].includes(operation) ? new Set(["--capsule", "--capsule-sha256"])
      : operation.startsWith("remove-") ? new Set(["--state-root", "--job-id", "--capsule-id", "--capsule-sha256", "--confirm-review-sha256"])
      : new Set(["--state-root", "--plan", "--checkpoint", "--context", "--expires-in-days", "--parent-capsule", "--parent-capsule-sha256", "--confirm-review-sha256"]);
  for (const key of Object.keys(options)) if (!allowed.has(key)) fail(`unsupported context session option: ${key}`);
  if (operation === "list") {
    if (!options["--state-root"] || options["--job-id"] !== undefined && !JOB_RE.test(options["--job-id"])) fail("list requires --state-root DIR and accepts one valid --job-id ID");
    return { operation, stateRoot: resolve(options["--state-root"]), jobId: options["--job-id"] ?? null };
  }
  if (["inspect", "show"].includes(operation)) {
    if (!options["--capsule"] || !SHA_RE.test(options["--capsule-sha256"] ?? "") || Object.keys(options).length !== 2) fail(`${operation} requires --capsule FILE --capsule-sha256 HASH`);
    return { operation, capsulePath: resolve(options["--capsule"]), capsuleSha256: options["--capsule-sha256"] };
  }
  if (operation.startsWith("remove-")) {
    const apply = operation === "remove-apply";
    if (!options["--state-root"] || !JOB_RE.test(options["--job-id"] ?? "") || !CAPSULE_ID_RE.test(options["--capsule-id"] ?? "") || !SHA_RE.test(options["--capsule-sha256"] ?? "")) fail("remove requires --state-root DIR --job-id ID --capsule-id ID --capsule-sha256 HASH");
    if (apply !== Boolean(options["--confirm-review-sha256"]) || apply && !SHA_RE.test(options["--confirm-review-sha256"])) fail("remove apply requires one exact review SHA-256 confirmation and review accepts none");
    return { operation, apply, stateRoot: resolve(options["--state-root"]), jobId: options["--job-id"], capsuleId: options["--capsule-id"], capsuleSha256: options["--capsule-sha256"], confirmation: options["--confirm-review-sha256"] ?? null };
  }
  for (const key of ["--state-root", "--plan", "--checkpoint", "--context", "--expires-in-days"]) if (!options[key]) fail(`${operation} requires ${key}`);
  const fork = operation.startsWith("fork-");
  if (fork !== Boolean(options["--parent-capsule"] || options["--parent-capsule-sha256"])) fail("fork operations require both exact parent capsule options; create operations accept neither");
  if (fork && (!options["--parent-capsule"] || !SHA_RE.test(options["--parent-capsule-sha256"] ?? ""))) fail("fork parent capsule hash is invalid");
  const apply = operation.endsWith("-apply");
  if (apply !== Boolean(options["--confirm-review-sha256"])) fail("apply requires one exact review SHA-256 confirmation and review accepts none");
  if (apply && !SHA_RE.test(options["--confirm-review-sha256"])) fail("context session review confirmation is invalid");
  if (!/^[1-9][0-9]{0,2}$/u.test(options["--expires-in-days"])) fail("context session expiry must be an integer from 1 through 365 days");
  const expiresInDays = Number(options["--expires-in-days"]);
  if (expiresInDays > 365) fail("context session expiry must be an integer from 1 through 365 days");
  return {
    operation, action: fork ? "fork" : "create", apply, stateRoot: resolve(options["--state-root"]),
    planPath: resolve(options["--plan"]), checkpointPath: resolve(options["--checkpoint"]), contextPath: resolve(options["--context"]), expiresInDays,
    parentCapsulePath: fork ? resolve(options["--parent-capsule"]) : null, parentCapsuleSha256: options["--parent-capsule-sha256"] ?? null,
    confirmation: options["--confirm-review-sha256"] ?? null,
  };
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const details = await lstat(path).catch(() => null);
  if (!details?.isDirectory() || details.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return details;
}

async function privateJson(path, maximum, label, allowedLinks = [1]) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label).catch(() => fail(`${label} could not be read safely`));
  if (!allowedLinks.includes(details.nlink) || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail(`${label} is not private or has an unexpected link count`);
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  let value;
  try { value = JSON.parse(text); } catch { fail(`${label} is not JSON`); }
  return { value, sha256: sha(value), bytes: details.size, path: resolve(path), details };
}

function assertPlanCheckpoint(plan, checkpoint) {
  const planErrors = validateWorkPlan(plan), checkpointErrors = validateWorkCheckpoint(checkpoint);
  if (planErrors.length) fail(`context session plan is invalid: ${planErrors[0]}`);
  if (checkpointErrors.length) fail(`context session checkpoint is invalid: ${checkpointErrors[0]}`);
  if (checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== sha(plan) || checkpoint.inputSetSha256 !== plan.inputSetSha256 || checkpoint.objectiveSha256 !== sha(plan.objective) || checkpoint.acceptanceCriteriaSha256 !== sha(plan.acceptanceCriteria)) fail("context session checkpoint differs from the immutable plan");
}

function reviewIntent(loaded) {
  return {
    schemaVersion: 1, operation: "pixel-work-context-session", action: loaded.action,
    stateRootBindingSha256: sha(`pixel-context-session-state\0${loaded.stateRoot}`), jobId: loaded.plan.value.jobId,
    planSha256: loaded.plan.sha256, checkpointSha256: loaded.checkpoint.sha256, contextSha256: loaded.context.sha256,
    parentCapsuleSha256: loaded.parent?.sha256 ?? null, expiresInDays: loaded.expiresInDays,
  };
}

async function loadMutationInputs(parsed, now) {
  await privateDirectory(parsed.stateRoot, "context session state root");
  const [plan, checkpoint, context] = await Promise.all([
    privateJson(parsed.planPath, MAX_PRIVATE_JSON_BYTES, "context session plan"),
    privateJson(parsed.checkpointPath, MAX_PRIVATE_JSON_BYTES, "context session checkpoint"),
    privateJson(parsed.contextPath, MAX_PRIVATE_JSON_BYTES, "context session input"),
  ]);
  assertPlanCheckpoint(plan.value, checkpoint.value);
  const contextErrors = validateWorkContextSessionInput(context.value);
  if (contextErrors.length) fail(`context session input is invalid: ${contextErrors[0]}`);
  let parent = null;
  if (parsed.action === "fork") {
    const loadedParent = await privateJson(parsed.parentCapsulePath, MAX_PRIVATE_JSON_BYTES, "parent context capsule");
    if (loadedParent.sha256 !== parsed.parentCapsuleSha256) fail("parent context capsule differs from its trusted hash");
    const value = await readContextCapsule(parsed.parentCapsulePath, parsed.parentCapsuleSha256).catch(() => fail("parent context capsule failed exact private validation"));
    parent = { value, sha256: loadedParent.sha256 };
    const expectedParentPath = resolve(parsed.stateRoot, "context-capsules", value.jobId, `${value.capsuleId}.json`);
    if (parsed.parentCapsulePath !== expectedParentPath) fail("parent context capsule is not in this private state root");
    if (now.getTime() >= Date.parse(value.expiresAt)) fail("parent context capsule expired");
    if (plan.value.jobId === value.jobId) fail("fork context must use a new child job");
    if (classificationRank[plan.value.dataClassification] < classificationRank[value.data.classification]) fail("fork context cannot downgrade data classification");
    if (value.lineage.depth >= 32) fail("context lineage depth is exhausted");
  }
  return { ...parsed, plan, checkpoint, context, parent };
}

function reviewReceipt(loaded) {
  const reviewSha256 = sha(reviewIntent(loaded));
  return Object.freeze({
    schemaVersion: 1, operation: `pixel-work-context-session-${loaded.action}-review`, status: "confirmation-required",
    jobId: loaded.plan.value.jobId, checkpointState: loaded.checkpoint.value.state, dataClassification: loaded.plan.value.dataClassification,
    lineage: { action: loaded.action, parentJobId: loaded.parent?.value.jobId ?? null, depth: loaded.parent ? loaded.parent.value.lineage.depth + 1 : 0 },
    inventory: { decisions: loaded.context.value.decisions.length, unresolvedRisks: loaded.context.value.unresolvedRisks.length, artifacts: loaded.context.value.artifacts.length },
    retention: { expiresInDays: loaded.expiresInDays }, confirmation: { option: "--confirm-review-sha256", sha256: reviewSha256 },
    privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary,
  });
}

function claimStatic(loaded, reviewSha256) {
  const intent = reviewIntent(loaded);
  return { ...intent, schemaVersion: 1, operation: "pixel-work-context-session-claim", reviewSha256 };
}

function validateClaim(claim, expected) {
  if (!claim || typeof claim !== "object" || Array.isArray(claim)) fail("context session claim is invalid");
  const staticKeys = Object.keys(expected), expectedKeys = [...staticKeys, "createdAt", "expiresAt", "suffix"].sort();
  if (canonical(Object.keys(claim).sort()) !== canonical(expectedKeys) || staticKeys.some((key) => canonical(claim[key]) !== canonical(expected[key])) || !SUFFIX_RE.test(claim.suffix ?? "")) fail("context session claim differs from the exact review");
  const created = Date.parse(claim.createdAt), expires = Date.parse(claim.expiresAt);
  if (!Number.isFinite(created) || !Number.isFinite(expires) || expires - created !== expected.expiresInDays * 86400000) fail("context session claim lifetime is invalid");
}

async function reserveClaim(loaded, reviewSha256, now, suffix) {
  const claimsRoot = join(loaded.stateRoot, "context-session-claims");
  await privateDirectory(claimsRoot, "context session claims root", true);
  const expected = claimStatic(loaded, reviewSha256);
  const claim = { ...expected, createdAt: now.toISOString(), expiresAt: new Date(now.getTime() + loaded.expiresInDays * 86400000).toISOString(), suffix };
  const path = join(claimsRoot, `${reviewSha256}.json`), temporary = join(claimsRoot, `.claim-${randomBytes(8).toString("hex")}`);
  const payload = `${JSON.stringify(claim, null, 2)}\n`;
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(payload, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, path); await unlink(temporary);
    if (process.platform !== "win32") { const directory = await open(claimsRoot, constants.O_RDONLY); try { await directory.sync(); } finally { await directory.close(); } }
    return claim;
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code !== "EEXIST") throw error;
    const stored = await privateJson(path, 16384, "context session claim");
    validateClaim(stored.value, expected);
    return stored.value;
  }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const directory = await open(path, constants.O_RDONLY);
  try { await directory.sync(); } finally { await directory.close(); }
}

async function publishPrivateRecord(root, filename, value, label) {
  await privateDirectory(root, `${label} root`, true);
  const path = join(root, filename), temporary = join(root, `.record-${randomBytes(8).toString("hex")}`), payload = `${JSON.stringify(value, null, 2)}\n`;
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(payload, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try { await link(temporary, path); await unlink(temporary); await syncDirectory(root); return { value, action: "published" }; }
  catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code !== "EEXIST") throw error;
    const existing = await privateJson(path, 16384, label);
    if (canonical(existing.value) !== canonical(value)) fail(`${label} identity collides with different content`);
    return { value: existing.value, action: "already-published" };
  }
}

async function applySession(loaded, dependencies) {
  const expectedReview = sha(reviewIntent(loaded));
  if (loaded.confirmation !== expectedReview) fail("context session confirmation differs from the current exact review");
  const now = checkedNow(dependencies.now), suffix = dependencies.suffix ?? randomBytes(6).toString("hex");
  if (!SUFFIX_RE.test(suffix)) fail("context session suffix is invalid");
  const claim = await reserveClaim(loaded, expectedReview, now, suffix);
  const options = {
    stateRoot: loaded.stateRoot, plan: loaded.plan.value, checkpoint: loaded.checkpoint.value,
    summary: loaded.context.value.summary, decisions: loaded.context.value.decisions, unresolvedRisks: loaded.context.value.unresolvedRisks,
    artifacts: loaded.context.value.artifacts, now: new Date(claim.createdAt), expiresAt: new Date(claim.expiresAt), suffix: claim.suffix,
  };
  const result = loaded.action === "fork"
    ? await forkContextCapsule({ ...options, parentCapsule: loaded.parent.value, parentCapsuleSha256: loaded.parent.sha256 })
    : await createContextCapsule(options);
  return Object.freeze({
    schemaVersion: 1, operation: `pixel-work-context-session-${loaded.action}-apply`, status: "published", action: result.action,
    capsuleId: result.capsule.capsuleId, capsuleSha256: result.sha256, jobId: result.capsule.jobId,
    createdAt: result.capsule.createdAt, expiresAt: result.capsule.expiresAt,
    lineage: { rootJobId: result.capsule.lineage.rootJobId, parentJobId: result.capsule.lineage.parentJobId, depth: result.capsule.lineage.depth },
    inventory: { decisions: result.capsule.context.decisions.length, unresolvedRisks: result.capsule.context.unresolvedRisks.length, artifacts: result.capsule.context.artifacts.length },
    privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary,
  });
}

async function loadExactCapsule(path, expectedSha256) {
  const loaded = await privateJson(path, MAX_PRIVATE_JSON_BYTES, "context capsule");
  if (loaded.sha256 !== expectedSha256) fail("context capsule differs from its trusted hash");
  const capsule = await readContextCapsule(path, expectedSha256).catch(() => fail("context capsule failed exact private validation"));
  return { capsule, sha256: loaded.sha256, bytes: loaded.bytes };
}

function capsuleSummary(loaded, now) {
  const capsule = loaded.capsule;
  return {
    capsuleId: capsule.capsuleId, capsuleSha256: loaded.sha256, jobId: capsule.jobId,
    state: now.getTime() < Date.parse(capsule.expiresAt) ? "current" : "expired", createdAt: capsule.createdAt, expiresAt: capsule.expiresAt,
    dataClassification: capsule.data.classification, checkpointState: capsule.bindings.checkpointState,
    lineage: { rootJobId: capsule.lineage.rootJobId, parentJobId: capsule.lineage.parentJobId, depth: capsule.lineage.depth },
    inventory: { decisions: capsule.context.decisions.length, unresolvedRisks: capsule.context.unresolvedRisks.length, artifacts: capsule.context.artifacts.length, bytes: loaded.bytes },
  };
}

async function scanCapsules(stateRoot, jobId, now) {
  await privateDirectory(stateRoot, "context session state root");
  const capsulesRoot = join(stateRoot, "context-capsules"), rootInfo = await lstat(capsulesRoot).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (rootInfo === null) return [];
  await privateDirectory(capsulesRoot, "context capsule root");
  const jobs = jobId === null ? (await readdir(capsulesRoot)).sort() : [jobId];
  if (jobs.length > MAX_CAPSULES) fail("context session job inventory exceeds its ceiling");
  const results = [];
  for (const name of jobs) {
    if (!JOB_RE.test(name)) fail("context capsule root contains an unexpected entry");
    const jobRoot = join(capsulesRoot, name), details = await lstat(jobRoot).catch((error) => error?.code === "ENOENT" && jobId !== null ? null : Promise.reject(error));
    if (details === null) continue;
    await privateDirectory(jobRoot, "context capsule job root");
    const files = (await readdir(jobRoot)).sort();
    for (const file of files) {
      const match = CAPSULE_FILE_RE.exec(file);
      if (!match) fail("context capsule job root contains an unexpected entry");
      const path = join(jobRoot, file), parsed = await privateJson(path, MAX_PRIVATE_JSON_BYTES, "stored context capsule", [1, 2]);
      const errors = validateWorkContextCapsule(parsed.value);
      if (errors.length || parsed.value.jobId !== name || parsed.value.capsuleId !== match[1]) fail("stored context capsule inventory is invalid or misplaced");
      if (parsed.details.nlink === 2) {
        const custody = await lstat(join(stateRoot, "context-session-removal-custody", `${parsed.sha256}.json`)).catch(() => null);
        if (!custody?.isFile() || custody.dev !== parsed.details.dev || custody.ino !== parsed.details.ino) fail("stored context capsule has an untrusted additional link");
      }
      const exact = parsed.details.nlink === 2 ? parsed.value : await readContextCapsule(path, parsed.sha256).catch(() => fail("stored context capsule failed exact private validation"));
      results.push(capsuleSummary({ capsule: exact, sha256: parsed.sha256, bytes: parsed.bytes }, now));
      if (results.length > MAX_CAPSULES) fail("context session capsule inventory exceeds its ceiling");
    }
  }
  return results.sort((left, right) => left.createdAt.localeCompare(right.createdAt) || left.capsuleId.localeCompare(right.capsuleId));
}

async function scanRemovalRecovery(stateRoot, now) {
  const custodyRoot = join(stateRoot, "context-session-removal-custody"), info = await lstat(custodyRoot).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (info === null) return [];
  await privateDirectory(custodyRoot, "context session removal custody root");
  const files = (await readdir(custodyRoot)).sort();
  if (files.length > MAX_CAPSULES) fail("context session removal recovery inventory exceeds its ceiling");
  const results = [];
  for (const file of files) {
    if (!/^[a-f0-9]{64}\.json$/u.test(file)) fail("context session removal custody contains an unexpected entry");
    const path = join(custodyRoot, file), parsed = await privateJson(path, MAX_PRIVATE_JSON_BYTES, "context session recovery capsule", [1, 2]);
    const errors = validateWorkContextCapsule(parsed.value);
    if (errors.length || `${parsed.sha256}.json` !== file) fail("context session removal recovery inventory is invalid");
    results.push({ ...capsuleSummary({ capsule: parsed.value, sha256: parsed.sha256, bytes: parsed.bytes }, now), state: "removal-recovery" });
  }
  return results;
}

function removeIntent(parsed) {
  return {
    schemaVersion: 1, operation: "pixel-work-context-session-remove", stateRootBindingSha256: sha(`pixel-context-session-state\0${parsed.stateRoot}`),
    jobId: parsed.jobId, capsuleId: parsed.capsuleId, capsuleSha256: parsed.capsuleSha256,
  };
}

function validateRemovalTombstone(value, parsed, reviewSha256) {
  const keys = ["schemaVersion", "operation", "reviewSha256", "jobId", "capsuleId", "capsuleSha256", "removedAt", "contentRetained", "secureErasureClaimed", "authority"].sort();
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys)
    || value.schemaVersion !== 1 || value.operation !== "pixel-work-context-session-removal" || value.reviewSha256 !== reviewSha256
    || value.jobId !== parsed.jobId || value.capsuleId !== parsed.capsuleId || value.capsuleSha256 !== parsed.capsuleSha256
    || !Number.isFinite(Date.parse(value.removedAt)) || value.contentRetained !== false || value.secureErasureClaimed !== false
    || canonical(value.authority) !== canonical(authority)) fail("context session removal tombstone is invalid or substituted");
  return value;
}

async function loadRemovalCapsule(path, parsed, allowedLinks = [1]) {
  const loaded = await privateJson(path, MAX_PRIVATE_JSON_BYTES, "context session removal capsule", allowedLinks);
  const errors = validateWorkContextCapsule(loaded.value);
  if (errors.length || loaded.sha256 !== parsed.capsuleSha256 || loaded.value.jobId !== parsed.jobId || loaded.value.capsuleId !== parsed.capsuleId) fail("context session removal capsule differs from the exact target");
  return loaded;
}

async function removalState(parsed, reviewSha256) {
  await privateDirectory(parsed.stateRoot, "context session state root");
  const jobRoot = join(parsed.stateRoot, "context-capsules", parsed.jobId), live = join(jobRoot, `${parsed.capsuleId}.json`);
  const custodyRoot = join(parsed.stateRoot, "context-session-removal-custody"), custody = join(custodyRoot, `${parsed.capsuleSha256}.json`);
  const tombstoneRoot = join(parsed.stateRoot, "context-session-removals"), tombstone = join(tombstoneRoot, `${parsed.capsuleSha256}.json`);
  const exists = async (path) => (await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) !== null;
  const tombstoneExists = await exists(tombstone);
  if (tombstoneExists) {
    const stored = await privateJson(tombstone, 16384, "context session removal tombstone");
    validateRemovalTombstone(stored.value, parsed, reviewSha256);
  }
  return { jobRoot, live, custodyRoot, custody, tombstoneRoot, tombstone, liveExists: await exists(live), custodyExists: await exists(custody), tombstoneExists };
}

async function reviewRemoval(parsed) {
  const reviewSha256 = sha(removeIntent(parsed)), state = await removalState(parsed, reviewSha256);
  if (state.tombstoneExists && state.liveExists) fail("removed context session unexpectedly remains live");
  if (!state.liveExists && !state.custodyExists && !state.tombstoneExists) fail("context session removal target does not exist");
  if (state.liveExists) await loadRemovalCapsule(state.live, parsed, state.custodyExists ? [2] : [1]);
  else if (state.custodyExists) await loadRemovalCapsule(state.custody, parsed);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-context-session-remove-review", status: state.tombstoneExists ? "already-removed" : "confirmation-required",
    jobId: parsed.jobId, capsuleId: parsed.capsuleId, capsuleSha256: parsed.capsuleSha256,
    confirmation: { option: "--confirm-review-sha256", sha256: reviewSha256 }, contentRemoval: "exact-capsule-only", secureErasureClaimed: false,
    privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary,
  });
}

async function applyRemovalAttempt(parsed, dependencies) {
  const reviewSha256 = sha(removeIntent(parsed));
  if (parsed.confirmation !== reviewSha256) fail("context session removal confirmation differs from the current exact review");
  let state = await removalState(parsed, reviewSha256);
  if (state.tombstoneExists) {
    if (state.liveExists) fail("removed context session unexpectedly remains live");
    if (state.custodyExists) { await loadRemovalCapsule(state.custody, parsed); await unlink(state.custody); await syncDirectory(state.custodyRoot); }
    return Object.freeze({ schemaVersion: 1, operation: "pixel-work-context-session-remove-apply", status: "removed", action: "already-removed", jobId: parsed.jobId, capsuleId: parsed.capsuleId, capsuleSha256: parsed.capsuleSha256, contentRetained: false, secureErasureClaimed: false, privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary });
  }
  if (!state.liveExists && !state.custodyExists) fail("context session removal target does not exist");
  await privateDirectory(state.custodyRoot, "context session removal custody root", true);
  await privateDirectory(state.tombstoneRoot, "context session removal tombstone root", true);
  if (state.liveExists && !state.custodyExists) {
    await loadRemovalCapsule(state.live, parsed);
    await link(state.live, state.custody).catch((error) => { if (error?.code !== "EEXIST") throw error; }); await syncDirectory(state.custodyRoot);
    await dependencies.afterCustody?.();
  } else if (state.liveExists && state.custodyExists) {
    const liveInfo = await lstat(state.live), custodyInfo = await lstat(state.custody);
    if (liveInfo.dev !== custodyInfo.dev || liveInfo.ino !== custodyInfo.ino) fail("context session removal custody differs from the live capsule");
    await loadRemovalCapsule(state.live, parsed, [2]);
  } else await loadRemovalCapsule(state.custody, parsed);
  state = await removalState(parsed, reviewSha256);
  if (state.liveExists) { await unlink(state.live).catch((error) => { if (error?.code !== "ENOENT") throw error; }); await syncDirectory(state.jobRoot); await dependencies.afterLiveUnlink?.(); }
  let removedAt = checkedNow(dependencies.now).toISOString();
  const tombstone = {
    schemaVersion: 1, operation: "pixel-work-context-session-removal", reviewSha256, jobId: parsed.jobId, capsuleId: parsed.capsuleId,
    capsuleSha256: parsed.capsuleSha256, removedAt, contentRetained: false, secureErasureClaimed: false, authority: { ...authority },
  };
  let published;
  try { published = await publishPrivateRecord(state.tombstoneRoot, `${parsed.capsuleSha256}.json`, tombstone, "context session removal tombstone"); }
  catch (error) {
    if (!(error instanceof ContextSessionCliError) || !/collides with different content/u.test(error.message)) throw error;
    const existing = await privateJson(state.tombstone, 16384, "context session removal tombstone");
    validateRemovalTombstone(existing.value, parsed, reviewSha256); removedAt = existing.value.removedAt;
    published = { value: existing.value, action: "already-published" };
  }
  await dependencies.afterTombstone?.();
  if (await lstat(state.custody).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) { await loadRemovalCapsule(state.custody, parsed); await unlink(state.custody).catch((error) => { if (error?.code !== "ENOENT") throw error; }); await syncDirectory(state.custodyRoot); }
  await rmdir(state.jobRoot).catch((error) => { if (!['ENOENT', 'ENOTEMPTY'].includes(error?.code)) throw error; });
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-context-session-remove-apply", status: "removed", action: published.action,
    jobId: parsed.jobId, capsuleId: parsed.capsuleId, capsuleSha256: parsed.capsuleSha256, removedAt,
    contentRetained: false, secureErasureClaimed: false, privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary,
  });
}

async function applyRemoval(parsed, dependencies) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try { return await applyRemovalAttempt(parsed, dependencies); }
    catch (error) {
      const transientCode = ["EEXIST", "ENOENT", "EBUSY", "EPERM"].includes(error?.code);
      const transientState = error instanceof ContextSessionCliError && /could not be read safely|unexpected link count|removal target does not exist|custody differs|unexpectedly remains live/u.test(error.message);
      if (!transientCode && !transientState || attempt === 199) throw error;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 5));
    }
  }
  fail("context session removal did not converge");
}

function strictDecode(value, label) {
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.from(value, "base64")); } catch { fail(`${label} is not strict UTF-8`); }
  if (!text || text.includes("\0")) fail(`${label} is empty or invalid`);
  return text;
}

export async function runContextSessionCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("context session dependencies are invalid");
  const parsed = parse(argv), now = checkedNow(dependencies.now);
  if (parsed.operation === "list") {
    const sessions = await scanCapsules(parsed.stateRoot, parsed.jobId, now), removalRecovery = await scanRemovalRecovery(parsed.stateRoot, now);
    return Object.freeze({ schemaVersion: 1, operation: "pixel-work-context-session-list", status: removalRecovery.length ? "recovery-attention" : "inspected", sessions, removalRecovery, privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary });
  }
  if (parsed.operation.startsWith("remove-")) return parsed.apply ? applyRemoval(parsed, dependencies) : reviewRemoval(parsed);
  if (["inspect", "show"].includes(parsed.operation)) {
    const loaded = await loadExactCapsule(parsed.capsulePath, parsed.capsuleSha256), summary = capsuleSummary(loaded, now);
    if (parsed.operation === "inspect") return Object.freeze({ schemaVersion: 1, operation: "pixel-work-context-session-inspect", status: "inspected", ...summary, privateContentProjected: false, authority: { ...authority }, boundary: contentFreeBoundary });
    const capsule = loaded.capsule;
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-work-context-session-show", status: "displayed", ...summary,
      privateContext: {
        summary: strictDecode(capsule.context.summaryBase64, "context summary"),
        decisions: capsule.context.decisions.map((entry) => ({ id: entry.id, statement: strictDecode(entry.statementBase64, `decision ${entry.id}`), evidenceSha256: entry.evidenceSha256 })),
        unresolvedRisks: capsule.context.unresolvedRisks.map((entry) => ({ id: entry.id, statement: strictDecode(entry.statementBase64, `risk ${entry.id}`), evidenceSha256: entry.evidenceSha256 })),
        artifacts: structuredClone(capsule.context.artifacts),
      },
      privateContentProjected: true, projection: "trusted-terminal-only", authority: { ...authority }, boundary: privateShowBoundary,
    });
  }
  const loaded = await loadMutationInputs(parsed, now);
  if (!parsed.apply) return reviewReceipt(loaded);
  return applySession(loaded, dependencies);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runContextSessionCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-context-session: ${error instanceof ContextSessionCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const contextSessionBoundary = contentFreeBoundary;

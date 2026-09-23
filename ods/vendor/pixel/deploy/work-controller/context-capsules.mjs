import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import { canonical, validateWorkCheckpoint, validateWorkContextCapsule, validateWorkPlan } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const ITEM_RE = /^[a-z][a-z0-9-]{0,63}$/u;
const PATH_RE = /^[A-Za-z0-9._+-]+(?:\/[A-Za-z0-9._+-]+)*$/u;
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const MAX_CAPSULE_BYTES = 256 * 1024;
const boundary = "Private local context data bound to one checkpoint and session lineage. It is untrusted context, contains no reusable lease or approval, and grants no execution, credential, external-effect, or completion authority.";

export class WorkContextCapsuleError extends Error {}

function fail(message) { throw new WorkContextCapsuleError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
export function contextCapsuleSha256(value) { return sha(value); }
function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
}

function encoded(value, maximumBytes, label) {
  if (typeof value !== "string") fail(`${label} is not text`);
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length < 1 || bytes.length > maximumBytes || bytes.toString("utf8") !== value) fail(`${label} is empty, invalid, or oversized`);
  return bytes.toString("base64");
}

function contextItems(values, label) {
  if (!Array.isArray(values) || values.length > 32) fail(`${label} are invalid`);
  const ids = new Set();
  const items = values.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry) || !ITEM_RE.test(entry.id ?? "") || ids.has(entry.id) || !SHA_RE.test(entry.evidenceSha256 ?? "")) fail(`${label} item is invalid or duplicated`);
    ids.add(entry.id);
    return { id: entry.id, statementBase64: encoded(entry.statement, 4096, `${label} statement`), evidenceSha256: entry.evidenceSha256 };
  });
  if (canonical(items.map((entry) => entry.id)) !== canonical([...items].map((entry) => entry.id).sort())) fail(`${label} must use canonical identifier order`);
  return items;
}

function artifactItems(values) {
  if (!Array.isArray(values) || values.length > 256) fail("context artifacts are invalid");
  const paths = new Set();
  const artifacts = values.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry) || !PATH_RE.test(entry.path ?? "") || paths.has(entry.path) || !SHA_RE.test(entry.sha256 ?? "") || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || entry.bytes > 34359738368 || typeof entry.verified !== "boolean" || entry.verificationEvidenceSha256 !== null && !SHA_RE.test(entry.verificationEvidenceSha256 ?? "") || entry.verified !== (entry.verificationEvidenceSha256 !== null)) fail("context artifact is invalid or duplicated");
    paths.add(entry.path);
    return structuredClone(entry);
  });
  if (canonical(artifacts.map((entry) => entry.path)) !== canonical([...artifacts].map((entry) => entry.path).sort())) fail("context artifacts must use canonical path order");
  return artifacts;
}

function bindings(plan, checkpoint) {
  const planErrors = validateWorkPlan(plan);
  const checkpointErrors = validateWorkCheckpoint(checkpoint);
  if (planErrors.length) fail(`context plan is invalid: ${planErrors[0]}`);
  if (checkpointErrors.length) fail(`context checkpoint is invalid: ${checkpointErrors[0]}`);
  const expected = {
    planSha256: sha(plan), inputSetSha256: plan.inputSetSha256, objectiveSha256: sha(plan.objective),
    acceptanceCriteriaSha256: sha(plan.acceptanceCriteria),
  };
  if (checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== expected.planSha256 || checkpoint.inputSetSha256 !== expected.inputSetSha256 || checkpoint.objectiveSha256 !== expected.objectiveSha256 || checkpoint.acceptanceCriteriaSha256 !== expected.acceptanceCriteriaSha256) fail("context checkpoint differs from the immutable plan");
  return { ...expected, checkpointId: checkpoint.checkpointId, checkpointSha256: sha(checkpoint), checkpointState: checkpoint.state };
}

function capsuleValue({ plan, checkpoint, summary, decisions, unresolvedRisks, artifacts, now, expiresAt, suffix, lineage }) {
  const created = timestamp(now, "context capsule time");
  const expiry = timestamp(expiresAt, "context capsule expiry");
  if (expiry.getTime() <= created.getTime() || expiry.getTime() - created.getTime() > 365 * 86400000 || !SUFFIX_RE.test(suffix)) fail("context capsule lifetime or identity is invalid");
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-context-capsule-v1.schema.json",
    schemaVersion: 1,
    capsuleId: `workcapsule-${String(created.getTime()).padStart(13, "0")}-${suffix}`,
    jobId: plan.jobId,
    createdAt: created.toISOString(),
    expiresAt: expiry.toISOString(),
    lineage,
    bindings: bindings(plan, checkpoint),
    data: { classification: plan.dataClassification, rawTranscriptIncluded: false, credentialsIncluded: false, reusableApprovalIncluded: false, contentAuthority: false },
    context: { summaryBase64: encoded(summary, 16384, "context summary"), decisions: contextItems(decisions, "context decisions"), unresolvedRisks: contextItems(unresolvedRisks, "context risks"), artifacts: artifactItems(artifacts) },
    authority: { grantsExecution: false, grantsLeaseReuse: false, grantsApprovalReuse: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary,
  };
  const errors = validateWorkContextCapsule(value);
  if (errors.length) fail(`context capsule is invalid: ${errors[0]}`);
  return value;
}

async function persist(stateRoot, capsule) {
  const root = resolve(stateRoot);
  await privateDirectory(root, "context state root");
  const capsulesRoot = join(root, "context-capsules");
  await mkdir(capsulesRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(capsulesRoot, "context capsule root");
  const jobRoot = join(capsulesRoot, capsule.jobId);
  await mkdir(jobRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(jobRoot, "context job root");
  const temporary = join(jobRoot, `.capsule-${randomBytes(8).toString("hex")}`);
  const path = join(jobRoot, `${capsule.capsuleId}.json`);
  const serialized = `${JSON.stringify(capsule, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_CAPSULE_BYTES) fail("context capsule exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, path); await unlink(temporary);
    if (process.platform !== "win32") { const directory = await open(jobRoot, constants.O_RDONLY); try { await directory.sync(); } finally { await directory.close(); } }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") {
      const existing = await readContextCapsule(path, sha(capsule));
      if (canonical(existing) !== canonical(capsule)) fail("context capsule identity collides with different content");
      return { capsule: existing, sha256: sha(existing), path, action: "already-published" };
    }
    throw error;
  }
  return { capsule, sha256: sha(capsule), path, action: "published" };
}

export async function createContextCapsule(options) {
  if (!JOB_RE.test(options?.plan?.jobId ?? "")) fail("context job identity is invalid");
  const capsule = capsuleValue({ ...options, lineage: { rootJobId: options.plan.jobId, parentJobId: null, parentCapsuleSha256: null, depth: 0 } });
  return persist(options.stateRoot, capsule);
}

export async function forkContextCapsule(options) {
  const parentErrors = validateWorkContextCapsule(options?.parentCapsule);
  if (parentErrors.length) fail(`parent context capsule is invalid: ${parentErrors[0]}`);
  if (!SHA_RE.test(options.parentCapsuleSha256 ?? "") || options.parentCapsuleSha256 !== sha(options.parentCapsule)) fail("parent context capsule differs from its trusted hash");
  const now = timestamp(options.now, "fork context time");
  if (now.getTime() >= Date.parse(options.parentCapsule.expiresAt)) fail("parent context capsule expired");
  if (options.plan.jobId === options.parentCapsule.jobId || classificationRank[options.plan.dataClassification] < classificationRank[options.parentCapsule.data.classification]) fail("fork context reuses a job or downgrades data classification");
  const depth = options.parentCapsule.lineage.depth + 1;
  if (depth > 32) fail("context lineage depth is exhausted");
  const lineage = { rootJobId: options.parentCapsule.lineage.rootJobId, parentJobId: options.parentCapsule.jobId, parentCapsuleSha256: sha(options.parentCapsule), depth };
  const capsule = capsuleValue({ ...options, now, lineage });
  return persist(options.stateRoot, capsule);
}

export function verifyContextCapsule({ capsule, expectedCapsuleSha256, plan, checkpoint, parentCapsule = null, now = new Date() }) {
  const errors = validateWorkContextCapsule(capsule);
  if (errors.length) fail(`context capsule is invalid: ${errors[0]}`);
  if (!SHA_RE.test(expectedCapsuleSha256 ?? "") || expectedCapsuleSha256 !== sha(capsule)) fail("context capsule differs from its trusted hash");
  const expected = bindings(plan, checkpoint);
  if (capsule.jobId !== plan.jobId || canonical(capsule.bindings) !== canonical(expected) || capsule.data.classification !== plan.dataClassification) fail("context capsule differs from its plan or checkpoint");
  const checkedNow = timestamp(now, "context verification time");
  if (checkedNow.getTime() < Date.parse(capsule.createdAt) || checkedNow.getTime() >= Date.parse(capsule.expiresAt)) fail("context capsule is not current");
  if (capsule.lineage.depth === 0) {
    if (parentCapsule !== null || capsule.lineage.rootJobId !== capsule.jobId) fail("root context lineage is invalid");
  } else {
    const parentErrors = validateWorkContextCapsule(parentCapsule);
    if (parentErrors.length || capsule.lineage.parentJobId !== parentCapsule.jobId || capsule.lineage.parentCapsuleSha256 !== sha(parentCapsule) || capsule.lineage.rootJobId !== parentCapsule.lineage.rootJobId || capsule.lineage.depth !== parentCapsule.lineage.depth + 1) fail("fork context lineage is invalid");
  }
  return { capsuleId: capsule.capsuleId, capsuleSha256: sha(capsule), current: true, authorityReusable: false };
}

export async function readContextCapsule(path, expectedCapsuleSha256) {
  const { text, details } = await readBoundedRegularText(path, MAX_CAPSULE_BYTES, "work context capsule");
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail("context capsule is not private and single-link");
  let capsule;
  try { capsule = JSON.parse(text); } catch { fail("context capsule is not JSON"); }
  const errors = validateWorkContextCapsule(capsule);
  if (errors.length) fail(`stored context capsule is invalid: ${errors[0]}`);
  if (!SHA_RE.test(expectedCapsuleSha256 ?? "") || expectedCapsuleSha256 !== sha(capsule)) fail("stored context capsule differs from its trusted hash");
  return capsule;
}

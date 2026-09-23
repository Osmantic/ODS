import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import { canonical, validateWorkResearchBatch } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { canonicalizePublicUrl, validateResearchQueryAgainstLease } from "./broker.mjs";

const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/;
const QUERY_RE = /^researchquery-[0-9]{13}-[a-f0-9]{12}$/;
const RECORD_ID_RE = /^researchrecord-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const RECORD_NAME_RE = /^(?:0{0,6}[0-9]{1,7})\.json$/;
const MAX_RECORD_BYTES = 32 * 1024;
const BOUNDARY = "Private content-free research accounting record. It cannot disclose a query, replay a request, authorize network access, widen scope, or grant credentials or external-action authority.";
const usageKeys = Object.freeze([
  "queries", "searchRequests", "retrievalRequests", "sources", "networkBytes", "sourceBytes", "rejectedSources",
]);

export class ResearchLedgerError extends Error {}

function fail(message) {
  throw new ResearchLedgerError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return resolve(path);
}

function recordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000) fail("research record sequence is invalid");
  return `${String(sequence).padStart(7, "0")}.json`;
}

function validateUsage(usage, plan) {
  exactKeys(usage, usageKeys, "research usage");
  for (const key of usageKeys) if (!Number.isSafeInteger(usage[key]) || usage[key] < 0) fail(`research usage ${key} is invalid`);
  if (usage.queries > plan.research.maxQueries) fail("research query budget is exhausted");
  if (usage.sources > plan.research.maxSources) fail("research source budget is exhausted");
  if (usage.sourceBytes > plan.research.maxTotalSourceBytes) fail("research source byte budget is exhausted");
  if (usage.networkBytes > plan.budgets.maxNetworkBytes) fail("research network byte budget is exhausted");
}

function validateRecord(record, plan, claim, expected) {
  exactKeys(record, [
    "schemaVersion", "recordId", "jobId", "claimId", "sequence", "createdAt", "previousRecordSha256",
    "planSha256", "researchPolicySha256", "queryId", "queryEnvelopeSha256", "normalizedQuerySha256",
    "status", "batchSha256", "failureFingerprintSha256", "accounting", "usage", "externalEffects", "authority", "boundary",
  ], "research record");
  if (
    record.schemaVersion !== 1 || !RECORD_ID_RE.test(record.recordId ?? "") || !CLAIM_RE.test(record.claimId ?? "")
    || !QUERY_RE.test(record.queryId ?? "") || record.jobId !== plan.jobId || record.claimId !== claim.claimId
    || record.planSha256 !== sha(plan) || record.researchPolicySha256 !== sha(plan.research)
    || !SHA_RE.test(record.queryEnvelopeSha256 ?? "") || !SHA_RE.test(record.normalizedQuerySha256 ?? "")
    || !["reserved", "completed", "failed"].includes(record.status) || !["exact", "conservative"].includes(record.accounting)
    || record.externalEffects !== false
    || canonical(record.authority) !== canonical({ directNetwork: false, credentials: false, externalWrites: false, scopeExpansion: false })
    || record.boundary !== BOUNDARY
  ) fail("research record binding is invalid");
  const recordTime = Date.parse(record.createdAt);
  const identityTime = Number(record.recordId.split("-")[1]);
  if (!Number.isFinite(recordTime) || new Date(recordTime).toISOString() !== record.createdAt || identityTime !== recordTime) fail("research record timestamp is invalid");
  if (record.status !== "failed" && record.accounting !== "exact") fail("non-failed research records require exact accounting");
  if (record.status === "reserved" && (record.batchSha256 !== null || record.failureFingerprintSha256 !== null)) fail("reserved research record carries terminal evidence");
  if (record.status === "completed" && (!SHA_RE.test(record.batchSha256 ?? "") || record.failureFingerprintSha256 !== null)) fail("completed research record lacks its batch digest");
  if (record.status === "failed" && (!SHA_RE.test(record.failureFingerprintSha256 ?? "") || record.batchSha256 !== null)) fail("failed research record lacks its failure digest");
  validateUsage(record.usage, plan);
  if (expected && (record.sequence !== expected.sequence || record.previousRecordSha256 !== expected.previousRecordSha256)) fail("research record chain position is invalid");
  return true;
}

async function writeRecord(root, record) {
  const temporaryRoot = await privateDirectory(join(root, "tmp"), "research temporary directory");
  const recordsRoot = await privateDirectory(join(root, "records"), "research record directory");
  const temporary = join(temporaryRoot, `.research-${record.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(recordsRoot, recordName(record.sequence));
  const serialized = `${JSON.stringify(record, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > MAX_RECORD_BYTES) fail("research record exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(serialized, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(recordsRoot, constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("research ledger changed concurrently");
    throw error;
  }
  return { record, path: destination, sha256: sha(record) };
}

async function readRecord(path, sequence, plan, claim, previousRecordSha256) {
  const { text, details } = await readBoundedRegularText(path, MAX_RECORD_BYTES, "research accounting record");
  if (details.nlink !== 1 || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))) fail("research accounting record is not private and single-link");
  let record;
  try { record = JSON.parse(text); } catch { fail("research accounting record is not JSON"); }
  validateRecord(record, plan, claim, { sequence, previousRecordSha256 });
  return record;
}

async function ledgerRoot(stateRoot, claim, create = false) {
  const state = await privateDirectory(stateRoot, "research state root");
  const research = join(state, "research");
  if (create) await mkdir(research, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(research, "research ledger root");
  const root = join(research, claim.claimId);
  if (create) {
    await mkdir(root, { mode: 0o700 });
    await privateDirectory(join(root, "records"), "research record directory", true);
    await privateDirectory(join(root, "tmp"), "research temporary directory", true);
  }
  await privateDirectory(root, "claim research ledger");
  return root;
}

export async function recoverResearchLedger({ stateRoot, plan, claim, allowMissing = false }) {
  if (!CLAIM_RE.test(claim?.claimId ?? "") || claim.jobId !== plan?.jobId || plan.profile !== "researcher") fail("research ledger binding is invalid");
  let root;
  try { root = await ledgerRoot(stateRoot, claim, false); } catch (error) {
    if (allowMissing && error instanceof ResearchLedgerError && /not a real directory/.test(error.message)) return { root: null, records: [], head: null, headSha256: null };
    throw error;
  }
  const recordsRoot = await privateDirectory(join(root, "records"), "research record directory");
  await privateDirectory(join(root, "tmp"), "research temporary directory");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length > 1000001) fail("research ledger exceeds its record ceiling");
  const records = [];
  const observedQueryIds = new Set();
  const observedQueryHashes = new Set();
  let previousRecordSha256 = null;
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    if (names[sequence] !== recordName(sequence) || !RECORD_NAME_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("research record sequence is incomplete or unsafe");
    const record = await readRecord(join(recordsRoot, names[sequence]), sequence, plan, claim, previousRecordSha256);
    if (sequence === 0) {
      const initialUsage = Object.fromEntries(usageKeys.map((key) => [key, key === "queries" ? 1 : 0]));
      if (record.status !== "reserved" || record.accounting !== "exact" || canonical(record.usage) !== canonical(initialUsage)) fail("research ledger must begin with one exact reservation");
      observedQueryIds.add(record.queryId);
      observedQueryHashes.add(record.normalizedQuerySha256);
    } else {
      const previous = records.at(-1);
      if (Date.parse(record.createdAt) <= Date.parse(previous.createdAt)) fail("research record time did not advance");
      for (const key of usageKeys) if (record.usage[key] < previous.usage[key]) fail(`research usage moved backward for ${key}`);
      if (previous.status === "reserved") {
        if (record.status === "reserved" || record.queryId !== previous.queryId || record.queryEnvelopeSha256 !== previous.queryEnvelopeSha256 || record.normalizedQuerySha256 !== previous.normalizedQuerySha256) fail("research reservation was bypassed or substituted");
        if (record.usage.queries !== previous.usage.queries) fail("research terminal transition changed the query count");
      } else {
        if (record.status !== "reserved") fail("research ledger terminal transition is invalid");
        if (observedQueryIds.has(record.queryId) || observedQueryHashes.has(record.normalizedQuerySha256)) fail("research ledger replays a prior query");
        if (record.usage.queries !== previous.usage.queries + 1) fail("research reservation query accounting is invalid");
        for (const key of usageKeys.filter((key) => key !== "queries")) if (record.usage[key] !== previous.usage[key]) fail(`research reservation changed prior usage for ${key}`);
        observedQueryIds.add(record.queryId);
        observedQueryHashes.add(record.normalizedQuerySha256);
      }
    }
    records.push(record);
    previousRecordSha256 = sha(record);
  }
  return { root, records, head: records.at(-1) ?? null, headSha256: previousRecordSha256 };
}

function timeAndSuffix(now, suffix) {
  const milliseconds = now.getTime();
  if (!Number.isSafeInteger(milliseconds) || !/^[a-f0-9]{12}$/.test(suffix)) fail("research record identity is invalid");
  return { milliseconds, recordId: `researchrecord-${String(milliseconds).padStart(13, "0")}-${suffix}` };
}

export async function reserveResearchQuery({ stateRoot, plan, lease, claim, query, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  validateResearchQueryAgainstLease(query, { plan, lease, claim });
  const recovered = await recoverResearchLedger({ stateRoot, plan, claim, allowMissing: true });
  if (recovered.head?.status === "reserved") fail("one research query is already reserved");
  if (recovered.records.some((record) => record.queryId === query.queryId || record.normalizedQuerySha256 === sha(query.query))) fail("research query was already reserved and cannot be replayed");
  const { milliseconds, recordId } = timeAndSuffix(now, suffix);
  if (milliseconds < Date.parse(query.createdAt) || milliseconds >= Date.parse(lease.expiresAt)) fail("research reservation is outside the lease lifetime");
  let root = recovered.root;
  if (!root) root = await ledgerRoot(stateRoot, claim, true);
  const previousUsage = recovered.head?.usage ?? Object.fromEntries(usageKeys.map((key) => [key, 0]));
  if (previousUsage.networkBytes >= plan.budgets.maxNetworkBytes) fail("research network byte budget is exhausted");
  if (previousUsage.sources >= plan.research.maxSources) fail("research source budget is exhausted");
  if (previousUsage.sourceBytes >= plan.research.maxTotalSourceBytes) fail("research source byte budget is exhausted");
  const record = {
    schemaVersion: 1, recordId, jobId: plan.jobId, claimId: claim.claimId,
    sequence: recovered.records.length, createdAt: now.toISOString(), previousRecordSha256: recovered.headSha256,
    planSha256: sha(plan), researchPolicySha256: sha(plan.research), queryId: query.queryId,
    queryEnvelopeSha256: sha(query), normalizedQuerySha256: sha(query.query), status: "reserved",
    batchSha256: null, failureFingerprintSha256: null, accounting: "exact",
    usage: { ...previousUsage, queries: previousUsage.queries + 1 }, externalEffects: false,
    authority: { directNetwork: false, credentials: false, externalWrites: false, scopeExpansion: false }, boundary: BOUNDARY,
  };
  validateRecord(record, plan, claim, { sequence: recovered.records.length, previousRecordSha256: recovered.headSha256 });
  return writeRecord(root, record);
}

export async function completeResearchQuery({ stateRoot, plan, lease, claim, query, batch, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  validateResearchQueryAgainstLease(query, { plan, lease, claim });
  const batchErrors = validateWorkResearchBatch(batch);
  if (batchErrors.length) fail(`research batch failed validation: ${batchErrors[0]}`);
  const recovered = await recoverResearchLedger({ stateRoot, plan, claim });
  const head = recovered.head;
  if (!head || head.status !== "reserved" || head.queryId !== query.queryId || head.queryEnvelopeSha256 !== sha(query)) fail("research completion has no exact active reservation");
  if (
    batch.queryId !== query.queryId || batch.jobId !== plan.jobId || batch.claimId !== claim.claimId
    || batch.planSha256 !== sha(plan) || batch.researchPolicySha256 !== sha(plan.research)
    || batch.normalizedQuerySha256 !== sha(query.query)
  ) fail("research batch differs from its active reservation");
  if (batch.adapter !== plan.researchBackend.adapter || canonical(plan.researchBackend) !== canonical(lease.researchBackend)) fail("research batch differs from the immutable backend binding");
  if (batch.sources.length > query.maxResults) fail("research batch exceeds the query result ceiling");
  for (const source of batch.sources) {
    if (!query.sourceTypes.includes(source.sourceType)) fail("research batch widens the query source types");
    if (canonicalizePublicUrl(source.canonicalUrl, plan.research, query.domains) !== source.canonicalUrl) fail("research batch source URL is not canonical or allowed");
    if (source.retrieval.status === "fetched") {
      if (source.retrieval.bytes > plan.research.maxSourceBytes) fail("research source exceeds the per-source byte ceiling");
      if (canonicalizePublicUrl(source.retrieval.finalUrl, plan.research, query.domains) !== source.retrieval.finalUrl) fail("research final source URL is not canonical or allowed");
      const retrievedAt = Date.parse(source.retrieval.retrievedAt);
      if (retrievedAt <= Date.parse(head.createdAt) || retrievedAt > Date.parse(batch.createdAt)) fail("research source retrieval is outside the reserved batch lifetime");
    }
  }
  const { recordId } = timeAndSuffix(now, suffix);
  const batchTime = Date.parse(batch.createdAt);
  if (batchTime <= Date.parse(head.createdAt) || batchTime > now.getTime() || now.getTime() >= Date.parse(lease.expiresAt)) fail("research completion is outside the lease lifetime");
  const usage = {
    queries: head.usage.queries,
    searchRequests: head.usage.searchRequests + batch.usage.searchRequests,
    retrievalRequests: head.usage.retrievalRequests + batch.usage.retrievalRequests,
    sources: head.usage.sources + batch.sources.length,
    networkBytes: head.usage.networkBytes + batch.usage.networkBytes,
    sourceBytes: head.usage.sourceBytes + batch.usage.sourceBytes,
    rejectedSources: head.usage.rejectedSources + batch.usage.rejectedSources,
  };
  const record = {
    ...head, recordId, sequence: head.sequence + 1, createdAt: now.toISOString(), previousRecordSha256: recovered.headSha256,
    status: "completed", batchSha256: sha(batch), failureFingerprintSha256: null, accounting: "exact", usage,
  };
  validateRecord(record, plan, claim, { sequence: head.sequence + 1, previousRecordSha256: recovered.headSha256 });
  return writeRecord(recovered.root, record);
}

export async function failReservedResearchQuery({ stateRoot, plan, claim, failureFingerprintSha256, knownUsage = null, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  if (!SHA_RE.test(failureFingerprintSha256 ?? "")) fail("research failure fingerprint is invalid");
  const recovered = await recoverResearchLedger({ stateRoot, plan, claim });
  const head = recovered.head;
  if (!head || head.status !== "reserved" || now.getTime() <= Date.parse(head.createdAt)) fail("research failure has no active reservation");
  const { recordId } = timeAndSuffix(now, suffix);
  const deltaKeys = usageKeys.filter((key) => key !== "queries");
  let usage;
  let accounting;
  if (knownUsage === null) {
    usage = { ...head.usage, searchRequests: head.usage.searchRequests + 1, networkBytes: plan.budgets.maxNetworkBytes };
    accounting = "conservative";
  } else {
    exactKeys(knownUsage, deltaKeys, "known research failure usage");
    for (const key of deltaKeys) if (!Number.isSafeInteger(knownUsage[key]) || knownUsage[key] < 0) fail(`known research failure usage ${key} is invalid`);
    usage = { ...head.usage };
    for (const key of deltaKeys) usage[key] += knownUsage[key];
    accounting = "exact";
  }
  const record = {
    ...head, recordId, sequence: head.sequence + 1, createdAt: now.toISOString(), previousRecordSha256: recovered.headSha256,
    status: "failed", batchSha256: null, failureFingerprintSha256, accounting, usage,
  };
  validateRecord(record, plan, claim, { sequence: head.sequence + 1, previousRecordSha256: recovered.headSha256 });
  return writeRecord(recovered.root, record);
}

export const researchLedgerContract = Object.freeze({ boundary: BOUNDARY, usageKeys });

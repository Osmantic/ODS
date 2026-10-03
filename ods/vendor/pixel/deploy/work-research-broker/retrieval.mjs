import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical, validateWorkResearchBatch, validateWorkResearchRetrieval,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { canonicalizePublicUrl, validateResearchQueryAgainstLease } from "./broker.mjs";
import { recoverResearchLedger } from "./ledger.mjs";

const REQUEST_BOUNDARY = "One hash-bound public source retrieval through Web Courier. It grants no direct network, credential, write, action, publication, purchase, policy, or scope authority.";
const REQUEST_ID_RE = /^[a-f0-9]{32}$/;
const RETRIEVAL_ID_RE = /^researchretrieval-[0-9]{13}-[a-f0-9]{12}$/;
const RECEIPT_BYTES = 32 * 1024;

export class ResearchRetrievalError extends Error {
  constructor(message, { code = "research-retrieval-failed", knownUsage = null, cause } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.code = code;
    this.knownUsage = knownUsage;
  }
}

function fail(message, options) {
  throw new ResearchRetrievalError(message, options);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

async function safeDirectory(path, label, ownerOnly = false) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`, { code: "filesystem-policy" });
  if (process.platform !== "win32") {
    if (info.uid !== process.geteuid() || (info.mode & 0o007) !== 0 || (ownerOnly && (info.mode & 0o077) !== 0)) {
      fail(`${label} permissions are unsafe`, { code: "filesystem-policy" });
    }
  }
  return resolve(path);
}

function privateFile(details, label) {
  if (details.nlink !== 1 || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o007) !== 0))) {
    fail(`${label} is not private and single-link`, { code: "filesystem-policy" });
  }
}

async function publishFile(directory, name, bytes) {
  if (basename(name) !== name) fail("research queue filename is unsafe", { code: "filesystem-policy" });
  const temporary = join(directory, `.research-${randomBytes(8).toString("hex")}`);
  const destination = join(directory, name);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, destination);
    await unlink(temporary);
    if (process.platform !== "win32") {
      const directoryHandle = await open(directory, constants.O_RDONLY);
      try { await directoryHandle.sync(); } finally { await directoryHandle.close(); }
    }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    fail("research queue request could not be committed exactly once", { code: "queue-commit", cause: error });
  }
  return destination;
}

export function createResearchRetrievalRequest({
  query, batch, source, plan, lease, claim, now = new Date(),
  maximumBytes = plan?.research?.maxSourceBytes, transport = "web-courier",
  requestId = randomBytes(16).toString("hex"), suffix = randomBytes(6).toString("hex"),
}) {
  validateResearchQueryAgainstLease(query, { plan, lease, claim });
  const batchErrors = validateWorkResearchBatch(batch);
  if (batchErrors.length) fail(`research batch failed validation: ${batchErrors[0]}`, { code: "batch-contract" });
  if (
    batch.queryId !== query.queryId || batch.jobId !== plan.jobId || batch.claimId !== claim.claimId
    || batch.planSha256 !== sha(plan) || batch.researchPolicySha256 !== sha(plan.research)
  ) fail("research batch differs from the consumed query lease", { code: "batch-binding" });
  const expectedSource = batch.sources.find((candidate) => candidate.sourceId === source?.sourceId);
  if (!expectedSource || canonical(expectedSource) !== canonical(source) || source.retrieval.status !== "metadata-only") {
    fail("research source is not exact unfetched batch metadata", { code: "source-binding" });
  }
  const milliseconds = now.getTime();
  if (
    !Number.isSafeInteger(milliseconds) || milliseconds <= Date.parse(batch.createdAt) || milliseconds >= Date.parse(lease.expiresAt)
    || !REQUEST_ID_RE.test(requestId) || !/^[a-f0-9]{12}$/u.test(suffix)
    || !Number.isSafeInteger(maximumBytes) || maximumBytes < 1024 || maximumBytes > plan.research.maxSourceBytes
    || !new Set(["web-courier", "offline-fixture"]).has(transport)
  ) fail("research retrieval identity is outside the lease lifetime", { code: "retrieval-identity" });
  const retrievalId = `researchretrieval-${String(milliseconds).padStart(13, "0")}-${suffix}`;
  const request = {
    url: source.canonicalUrl,
    mode: "text",
    wait_ms: 0,
    research_receipt: {
      schemaVersion: 1,
      transport,
      retrievalId,
      jobId: plan.jobId,
      claimId: claim.claimId,
      queryId: query.queryId,
      searchEvidenceSha256: source.searchEvidenceSha256,
      planSha256: sha(plan),
      sourceId: source.sourceId,
      canonicalUrlSha256: sha(source.canonicalUrl),
      maxBytes: maximumBytes,
      allowedDomains: [...query.domains],
      deniedDomains: [...plan.research.deniedDomains],
      retention: "job-only",
      boundary: REQUEST_BOUNDARY,
    },
  };
  return { request, requestId, retrievalId, createdAt: now.toISOString() };
}

async function waitForReceipt(queueRoot, requestId, timeoutMilliseconds, pollMilliseconds) {
  const receiptPath = join(queueRoot, `receipt-${requestId}.json`);
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const info = await lstat(receiptPath).catch(() => null);
    if (info) {
      if (!info.isFile() || info.isSymbolicLink()) fail("Web Courier receipt is not a regular file", { code: "receipt-file" });
      return receiptPath;
    }
    await new Promise((resolve) => setTimeout(resolve, pollMilliseconds));
  }
  fail("Web Courier did not produce a research receipt before the deadline", { code: "courier-timeout" });
}

async function storeContentObject(root, content, contentSha256) {
  const objectRoot = await safeDirectory(root, "research object store", true);
  const objectName = `${contentSha256}.source`;
  const destination = join(objectRoot, objectName);
  const existing = await lstat(destination).catch(() => null);
  if (existing) {
    const observed = await readBoundedRegularFile(destination, content.length, "research source object");
    privateFile(observed.details, "research source object");
    if (observed.bytes.length !== content.length || sha(observed.bytes) !== contentSha256) fail("existing research source object differs", { code: "object-collision" });
    return objectName;
  }
  await publishFile(objectRoot, objectName, content);
  return objectName;
}

async function storeReceiptObject(root, receipt, receiptSha256) {
  const objectRoot = await safeDirectory(root, "research object store", true);
  const name = `${receiptSha256}.receipt.json`;
  const bytes = Buffer.from(`${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  const destination = join(objectRoot, name);
  const existing = await lstat(destination).catch(() => null);
  if (!existing) await publishFile(objectRoot, name, bytes);
  else {
    const observed = await readBoundedRegularText(destination, RECEIPT_BYTES, "research receipt object");
    privateFile(observed.details, "research receipt object");
    let parsed;
    try { parsed = JSON.parse(observed.text); } catch { fail("existing research receipt object is malformed", { code: "object-collision" }); }
    if (sha(parsed) !== receiptSha256) fail("existing research receipt object differs", { code: "object-collision" });
  }
  return name;
}

export async function retrieveResearchSource({
  stateRoot, queueRoot, objectRoot, query, batch, source, plan, lease, claim,
  timeoutMilliseconds = 90000, pollMilliseconds = 50,
  maximumBytes = plan?.research?.maxSourceBytes, transport = "web-courier",
  now = new Date(), requestId = randomBytes(16).toString("hex"), suffix = randomBytes(6).toString("hex"),
}) {
  if (!Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > 180000 || !Number.isSafeInteger(pollMilliseconds) || pollMilliseconds < 1 || pollMilliseconds > 1000) {
    fail("research retrieval wait policy is invalid", { code: "transport-config" });
  }
  const active = await recoverResearchLedger({ stateRoot, plan, claim });
  if (!active.head || active.head.status !== "reserved" || active.head.queryId !== query.queryId || active.head.queryEnvelopeSha256 !== sha(query)) {
    fail("research retrieval has no exact active query reservation", { code: "reservation-binding" });
  }
  const queue = await safeDirectory(queueRoot, "research Web Courier queue");
  const prepared = createResearchRetrievalRequest({ query, batch, source, plan, lease, claim, now, maximumBytes, transport, requestId, suffix });
  const requestName = `req-${prepared.requestId}.json`;
  const requestPath = await publishFile(queue, requestName, Buffer.from(`${JSON.stringify(prepared.request)}\n`, "utf8"));
  let receiptPath;
  try {
    receiptPath = await waitForReceipt(queue, prepared.requestId, timeoutMilliseconds, pollMilliseconds);
    const receiptFile = await readBoundedRegularText(receiptPath, RECEIPT_BYTES, "Web Courier research receipt");
    privateFile(receiptFile.details, "Web Courier research receipt");
    let receipt;
    try { receipt = JSON.parse(receiptFile.text); } catch { fail("Web Courier research receipt is malformed JSON", { code: "receipt-contract" }); }
    const errors = validateWorkResearchRetrieval(receipt);
    if (errors.length) fail(`Web Courier research receipt failed validation: ${errors[0]}`, { code: "receipt-contract" });
    if (
      receipt.retrievalId !== prepared.retrievalId || receipt.requestId !== prepared.requestId
      || receipt.transport !== transport
      || receipt.jobId !== plan.jobId || receipt.claimId !== claim.claimId || receipt.queryId !== query.queryId
      || receipt.searchEvidenceSha256 !== source.searchEvidenceSha256 || receipt.planSha256 !== sha(plan)
      || receipt.sourceId !== source.sourceId || receipt.canonicalUrlSha256 !== sha(source.canonicalUrl)
      || Date.parse(receipt.createdAt) < now.getTime() || Date.parse(receipt.createdAt) >= Date.parse(lease.expiresAt)
    ) fail("Web Courier research receipt differs from its exact request", { code: "receipt-binding" });
    if (!new Set(["fetched", "rejected"]).has(receipt.status)) {
      fail("Web Courier did not produce a terminal source decision", { code: `courier-${receipt.reason ?? receipt.status}` });
    }
    const stillActive = await recoverResearchLedger({ stateRoot, plan, claim });
    if (stillActive.headSha256 !== active.headSha256 || stillActive.head?.status !== "reserved") fail("research reservation changed during source retrieval", { code: "reservation-binding" });
    if (receipt.status === "rejected") {
      const receiptSha256 = sha(receipt);
      const receiptObjectName = await storeReceiptObject(objectRoot, receipt, receiptSha256);
      return {
        sourceId: source.sourceId,
        retrieval: { status: "rejected", transport: receipt.transport, reason: receipt.reason },
        receiptObjectName,
        observedAt: receipt.createdAt,
        usage: { retrievalRequests: 1, networkBytes: 0, sourceBytes: 0 },
      };
    }
    const finalUrl = canonicalizePublicUrl(receipt.finalUrl, plan.research, query.domains);
    if (finalUrl !== receipt.finalUrl) fail("Web Courier final URL is not canonical", { code: "receipt-binding" });
    if (receipt.bytes > maximumBytes || receipt.networkBytes > plan.budgets.maxNetworkBytes) fail("Web Courier source exceeds the research lease", { code: "receipt-budget" });
    const responsePath = join(queue, receipt.responseName);
    const response = await readBoundedRegularFile(responsePath, maximumBytes, "Web Courier research source");
    privateFile(response.details, "Web Courier research source");
    if (response.bytes.length !== receipt.bytes || sha(response.bytes) !== receipt.contentSha256) fail("Web Courier source content differs from its receipt", { code: "source-integrity" });
    const receiptSha256 = sha(receipt);
    const objectName = await storeContentObject(objectRoot, response.bytes, receipt.contentSha256);
    const receiptObjectName = await storeReceiptObject(objectRoot, receipt, receiptSha256);
    return {
      sourceId: source.sourceId,
      retrieval: {
        status: "fetched", transport: receipt.transport, objectName, contentSha256: receipt.contentSha256, receiptSha256,
        bytes: receipt.bytes, mediaType: receipt.mediaType, finalUrl: receipt.finalUrl,
        retrievedAt: receipt.createdAt, redirects: receipt.redirects, dnsPinned: receipt.dnsPinned,
      },
      receiptObjectName,
      observedAt: receipt.createdAt,
      usage: { retrievalRequests: 1, networkBytes: receipt.networkBytes, sourceBytes: receipt.bytes },
    };
  } finally {
    await unlink(requestPath).catch(() => {});
    if (receiptPath) {
      const receipt = await readBoundedRegularText(receiptPath, RECEIPT_BYTES, "Web Courier research receipt cleanup").catch(() => null);
      let responseName;
      try { responseName = receipt ? JSON.parse(receipt.text).responseName : null; } catch { responseName = null; }
      await unlink(receiptPath).catch(() => {});
      if (typeof responseName === "string" && /^res-[a-f0-9]{32}\.md$/u.test(responseName)) await unlink(join(queue, responseName)).catch(() => {});
    }
    await unlink(join(queue, `res-${prepared.requestId}.md`)).catch(() => {});
  }
}

export function attachResearchRetrievals(batch, retrievals, { now = new Date(), suffix = randomBytes(6).toString("hex") } = {}) {
  const errors = validateWorkResearchBatch(batch);
  if (errors.length) fail(`research batch failed validation: ${errors[0]}`, { code: "batch-contract" });
  if (!Array.isArray(retrievals) || retrievals.length > batch.sources.length || !/^[a-f0-9]{12}$/u.test(suffix)) fail("research retrieval set is invalid", { code: "retrieval-set" });
  const bySource = new Map();
  const available = new Set(batch.sources.map((source) => source.sourceId));
  for (const item of retrievals) {
    const status = item?.retrieval?.status;
    if (!item || bySource.has(item.sourceId) || !available.has(item.sourceId) || !new Set(["fetched", "rejected"]).has(status)) fail("research retrieval set is duplicated or outside the batch", { code: "retrieval-set" });
    const exactUsage = canonical(Object.keys(item.usage ?? {}).sort()) === canonical(["networkBytes", "retrievalRequests", "sourceBytes"])
      && item.usage.retrievalRequests === 1 && Number.isSafeInteger(item.usage.networkBytes)
      && Number.isSafeInteger(item.usage.sourceBytes);
    const fetchedUsage = status === "fetched" && item.usage.sourceBytes === item.retrieval.bytes
      && (item.retrieval.transport === "web-courier" && item.usage.networkBytes >= item.usage.sourceBytes
        || item.retrieval.transport === "offline-fixture" && item.usage.networkBytes === 0);
    const rejectedUsage = status === "rejected" && item.usage.networkBytes === 0 && item.usage.sourceBytes === 0;
    if (!exactUsage || (!fetchedUsage && !rejectedUsage)) fail("research retrieval usage is invalid", { code: "retrieval-set" });
    bySource.set(item.sourceId, item);
  }
  const created = now.getTime();
  if (!Number.isSafeInteger(created) || created <= Date.parse(batch.createdAt)) fail("research retrieval batch time did not advance", { code: "retrieval-set" });
  const result = structuredClone(batch);
  result.batchId = `researchbatch-${String(created).padStart(13, "0")}-${suffix}`;
  result.createdAt = now.toISOString().replace(/\.000Z$/u, "Z");
  let networkBytes = 0;
  let sourceBytes = 0;
  let retrievalRequests = 0;
  for (const source of result.sources) {
    const item = bySource.get(source.sourceId);
    if (!item) continue;
    source.retrieval = structuredClone(item.retrieval);
    retrievalRequests += item.usage.retrievalRequests;
    networkBytes += item.usage.networkBytes;
    sourceBytes += item.usage.sourceBytes;
  }
  result.usage.retrievalRequests += retrievalRequests;
  result.usage.networkBytes += networkBytes;
  result.usage.sourceBytes += sourceBytes;
  const resultErrors = validateWorkResearchBatch(result);
  if (resultErrors.length) fail(`retrieved research batch failed validation: ${resultErrors[0]}`, { code: "retrieval-set" });
  return result;
}

export const researchRetrievalContract = Object.freeze({ requestBoundary: REQUEST_BOUNDARY });

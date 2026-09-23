import { createHash, randomBytes } from "node:crypto";
import { lstat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { TextDecoder } from "node:util";

import {
  canonical, validateWorkResearchBatch, validateWorkResearchReport,
  validateWorkConsumption, validateWorkPlan, validateWorkResearchRetrieval, validateWorkResearchVerification,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { recoverResearchLedger } from "./ledger.mjs";

const AUTHORITY = Object.freeze({
  directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false,
  publish: false, purchase: false, policyMutation: false, scopeExpansion: false,
});
const BOUNDARY = "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority and does not claim semantic entailment, source truth, completeness, or publication readiness.";
const decoder = new TextDecoder("utf-8", { fatal: true });

export class ResearchCitationError extends Error {}

function fail(message) {
  throw new ResearchCitationError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

async function privateDirectory(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail("research citation object store is not a real directory");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("research citation object store is not owner-only");
  return resolve(path);
}

function privateFile(details, label) {
  if (details.nlink !== 1 || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))) fail(`${label} is not private and single-link`);
}

function decodeText(value, label) {
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value) fail(`${label} is not canonical base64`);
  try {
    const text = decoder.decode(bytes);
    if (text.normalize("NFC") !== text || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/u.test(text)) {
      fail(`${label} is not canonical visible UTF-8 text`);
    }
  } catch (error) {
    if (error instanceof ResearchCitationError) throw error;
    fail(`${label} is not valid UTF-8`);
  }
  return bytes;
}

async function loadSource(root, source, plan, batch) {
  if (source.retrieval.status !== "fetched") fail(`research source ${source.sourceId} was not independently fetched`);
  const sourcePath = join(root, source.retrieval.objectName);
  const object = await readBoundedRegularFile(sourcePath, plan.research.maxSourceBytes, "research citation source");
  privateFile(object.details, "research citation source");
  if (object.bytes.length !== source.retrieval.bytes || sha(object.bytes) !== source.retrieval.contentSha256 || source.retrieval.objectName !== `${source.retrieval.contentSha256}.source`) {
    fail(`research source ${source.sourceId} differs from its batch evidence`);
  }
  const receiptName = `${source.retrieval.receiptSha256}.receipt.json`;
  const receiptFile = await readBoundedRegularText(join(root, receiptName), 32 * 1024, "research citation receipt");
  privateFile(receiptFile.details, "research citation receipt");
  let receipt;
  try { receipt = JSON.parse(receiptFile.text); } catch { fail(`research source ${source.sourceId} receipt is malformed`); }
  const receiptErrors = validateWorkResearchRetrieval(receipt);
  if (receiptErrors.length || sha(receipt) !== source.retrieval.receiptSha256) fail(`research source ${source.sourceId} receipt failed validation`);
  if (
    receipt.jobId !== plan.jobId || receipt.claimId !== batch.claimId || receipt.queryId !== batch.queryId
    || receipt.planSha256 !== sha(plan) || receipt.sourceId !== source.sourceId
    || receipt.searchEvidenceSha256 !== source.searchEvidenceSha256
    || receipt.contentSha256 !== source.retrieval.contentSha256 || receipt.bytes !== source.retrieval.bytes
    || receipt.finalUrl !== source.retrieval.finalUrl || receipt.createdAt !== source.retrieval.retrievedAt
    || receipt.redirects !== source.retrieval.redirects || receipt.dnsPinned !== true
  ) fail(`research source ${source.sourceId} receipt binding differs`);
  return { bytes: object.bytes, receipt };
}

export async function verifyResearchReport({ report, batches, plan, claim, stateRoot, objectRoot, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const planErrors = validateWorkPlan(plan);
  const claimErrors = validateWorkConsumption(claim);
  if (planErrors.length || claimErrors.length) fail(`research verifier inputs failed validation: ${planErrors[0] ?? claimErrors[0]}`);
  const reportErrors = validateWorkResearchReport(report);
  if (reportErrors.length) fail(`research report failed validation: ${reportErrors[0]}`);
  if (!Array.isArray(batches) || batches.length < 1 || batches.length > plan.research.maxQueries) fail("research verifier batch set is invalid");
  const batchSha256s = [];
  const queryIds = new Set();
  let previousBatchTime = -1;
  for (const batch of batches) {
    const batchErrors = validateWorkResearchBatch(batch);
    if (batchErrors.length) fail(`research batch failed validation: ${batchErrors[0]}`);
    const batchTime = Date.parse(batch.createdAt);
    const batchSha256 = sha(batch);
    if (
      batch.jobId !== plan.jobId || batch.claimId !== claim.claimId || batch.planSha256 !== sha(plan)
      || batch.researchPolicySha256 !== sha(plan.research) || queryIds.has(batch.queryId)
      || batchTime <= previousBatchTime || batchSha256s.includes(batchSha256)
    ) fail("research batch set is duplicated, unordered, or differs from the consumed plan");
    queryIds.add(batch.queryId);
    batchSha256s.push(batchSha256);
    previousBatchTime = batchTime;
  }
  const ledger = await recoverResearchLedger({ stateRoot, plan, claim });
  const completed = new Map(ledger.records.filter((record) => record.status === "completed").map((record) => [record.batchSha256, record]));
  for (let index = 0; index < batches.length; index += 1) {
    const record = completed.get(batchSha256s[index]);
    if (!record || record.queryId !== batches[index].queryId || record.normalizedQuerySha256 !== batches[index].normalizedQuerySha256) {
      fail("research batch lacks an exact completed accounting record");
    }
  }
  if (
    plan.profile !== "researcher" || report.jobId !== plan.jobId || report.claimId !== claim.claimId
    || report.planSha256 !== sha(plan) || report.researchPolicySha256 !== sha(plan.research)
    || canonical(report.batchSha256s) !== canonical(batchSha256s)
    || Date.parse(report.createdAt) <= previousBatchTime
  ) fail("research report differs from its plan, claim, or fetched batches");
  const milliseconds = now.getTime();
  if (!Number.isSafeInteger(milliseconds) || milliseconds <= Date.parse(report.createdAt) || !/^[a-f0-9]{12}$/u.test(suffix)) fail("research verification identity is invalid");
  for (const [field, value] of [["title", report.titleBase64], ["limitations", report.limitationsBase64]]) decodeText(value, `research report ${field}`);
  const root = await privateDirectory(objectRoot);
  const sources = new Map();
  for (let batchIndex = 0; batchIndex < batches.length; batchIndex += 1) {
    for (const source of batches[batchIndex].sources) sources.set(`${batchSha256s[batchIndex]}:${source.sourceId}`, { source, batch: batches[batchIndex] });
  }
  const cache = new Map();
  const findings = [];
  for (const finding of report.findings) {
    decodeText(finding.statementBase64, `research finding ${finding.findingId}`);
    const citations = [];
    for (const citation of finding.citations) {
      const located = sources.get(`${citation.batchSha256}:${citation.sourceId}`);
      if (!located) fail(`research finding ${finding.findingId} cites a source outside the batch set`);
      const { source, batch } = located;
      const cacheKey = `${citation.batchSha256}:${source.sourceId}`;
      let loaded = cache.get(cacheKey);
      if (!loaded) {
        loaded = await loadSource(root, source, plan, batch);
        cache.set(cacheKey, loaded);
      }
      const evidence = decodeText(citation.evidenceBase64, `research citation ${citation.sourceId}`);
      if (evidence.length < 16 || sha(evidence) !== citation.evidenceSha256) fail(`research citation ${citation.sourceId} evidence binding is invalid`);
      const offset = loaded.bytes.indexOf(evidence);
      citations.push({
        batchSha256: citation.batchSha256,
        sourceId: source.sourceId,
        contentSha256: source.retrieval.contentSha256,
        receiptSha256: source.retrieval.receiptSha256,
        evidenceSha256: citation.evidenceSha256,
        evidenceBytes: evidence.length,
        offset,
        status: offset >= 0 ? "present" : "not-found",
      });
    }
    findings.push({
      findingId: finding.findingId,
      status: citations.every((citation) => citation.status === "present") ? "pass" : "fail",
      citations,
    });
  }
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json",
    schemaVersion: 1,
    verificationId: `researchverification-${String(milliseconds).padStart(13, "0")}-${suffix}`,
    jobId: plan.jobId,
    claimId: claim.claimId,
    createdAt: now.toISOString(),
    planSha256: sha(plan),
    batchSha256s,
    reportSha256: sha(report),
    status: findings.every((finding) => finding.status === "pass") ? "evidence-pass" : "fail",
    verificationLevel: "deterministic-evidence-presence",
    semanticEntailmentVerified: false,
    independent: true,
    network: "none",
    modelUsed: false,
    findings,
    privateDataIncluded: false,
    externalEffects: false,
    authority: { ...AUTHORITY },
    boundary: BOUNDARY,
  };
  const verificationErrors = validateWorkResearchVerification(verification);
  if (verificationErrors.length) fail(`research verification failed validation: ${verificationErrors[0]}`);
  return verification;
}

export const researchCitationContract = Object.freeze({ authority: AUTHORITY, boundary: BOUNDARY });

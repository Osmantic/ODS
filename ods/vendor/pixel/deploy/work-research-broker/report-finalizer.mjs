import { createHash, randomBytes } from "node:crypto";
import { TextEncoder } from "node:util";

import {
  canonical,
  validatePlanLease,
  validateWorkConsumption,
  validateWorkResearchBatch,
  validateWorkResearchReport,
  validateWorkResearchReportProposal,
} from "../../scripts/lib/work-contract.mjs";

const AUTHORITY = Object.freeze({
  directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false,
  publish: false, purchase: false, policyMutation: false, scopeExpansion: false,
});
const PROPOSAL_BOUNDARY = "Untrusted public Researcher proposal only. It carries no verification, publication, action, policy, or completion authority and must be finalized and independently checked by Pixel.";
const REPORT_BOUNDARY = "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; deterministic verification proves source integrity and quote presence, not semantic entailment or truth.";
const encoder = new TextEncoder();

export class ResearchReportFinalizerError extends Error {}

function fail(message) {
  throw new ResearchReportFinalizerError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

function schema(label, errors) {
  if (errors.length) fail(`${label} failed validation: ${errors[0]}`);
}

function visibleText(value, minimumBytes, maximumBytes, label) {
  if (typeof value !== "string" || value.normalize("NFC") !== value || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/u.test(value)) fail(`${label} is not canonical visible text`);
  const bytes = Buffer.from(encoder.encode(value));
  if (bytes.length < minimumBytes || bytes.length > maximumBytes) fail(`${label} exceeds its UTF-8 byte boundary`);
  return bytes;
}

export function parseResearchReportProposal(text) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") < 2 || Buffer.byteLength(text, "utf8") > 4 * 1024 * 1024) fail("Researcher proposal is outside its byte boundary");
  let proposal;
  try { proposal = JSON.parse(text); } catch { fail("Researcher proposal is not JSON"); }
  schema("Researcher proposal", validateWorkResearchReportProposal(proposal));
  if (proposal.boundary !== PROPOSAL_BOUNDARY || canonical(proposal.authority) !== canonical(AUTHORITY)) fail("Researcher proposal claims unsupported authority");
  return proposal;
}

export function finalizeResearchReport({
  proposal, batches, plan, lease, claim, now = new Date(), suffix = randomBytes(6).toString("hex"),
}) {
  schema("research plan/lease", validatePlanLease(plan, lease));
  schema("research lease consumption", validateWorkConsumption(claim));
  schema("Researcher proposal", validateWorkResearchReportProposal(proposal));
  if (
    plan.profile !== "researcher" || plan.dataClassification !== "public" || claim.jobId !== plan.jobId
    || claim.leaseId !== lease.leaseId || claim.planSha256 !== sha(plan) || claim.leaseSha256 !== sha(lease)
    || proposal.boundary !== PROPOSAL_BOUNDARY || canonical(proposal.authority) !== canonical(AUTHORITY)
  ) fail("Researcher proposal differs from the consumed public plan");
  if (!Array.isArray(batches) || batches.length < 1 || batches.length > plan.research.maxQueries) fail("Researcher proposal has an invalid batch set");
  const batchSha256s = [];
  const sources = new Set();
  let previousTime = -1;
  for (const batch of batches) {
    schema("research batch", validateWorkResearchBatch(batch));
    const digest = sha(batch);
    const created = Date.parse(batch.createdAt);
    if (
      batch.jobId !== plan.jobId || batch.claimId !== claim.claimId || batch.planSha256 !== sha(plan)
      || batch.researchPolicySha256 !== sha(plan.research) || created <= previousTime || batchSha256s.includes(digest)
    ) fail("Researcher batch set is duplicated, unordered, or outside the consumed plan");
    batchSha256s.push(digest);
    previousTime = created;
    for (const source of batch.sources) if (source.retrieval.status === "fetched") sources.add(`${digest}:${source.sourceId}`);
  }
  if (canonical(proposal.batchSha256s) !== canonical(batchSha256s)) fail("Researcher proposal batch set differs from broker evidence");
  const milliseconds = now.getTime();
  if (!Number.isSafeInteger(milliseconds) || milliseconds <= previousTime || milliseconds < Date.parse(claim.claimedAt) || milliseconds >= Date.parse(lease.expiresAt) || !/^[a-f0-9]{12}$/u.test(suffix)) fail("research report identity is outside the lease lifetime");
  const title = visibleText(proposal.title, 1, 48000, "research report title");
  const limitations = visibleText(proposal.limitations, 1, 48000, "research report limitations");
  const findings = proposal.findings.map((finding, index) => {
    const statement = visibleText(finding.statement, 1, 48000, `research finding ${index + 1}`);
    const citations = finding.citations.map((citation) => {
      if (!sources.has(`${citation.batchSha256}:${citation.sourceId}`)) fail(`research finding ${index + 1} cites unfetched or outside evidence`);
      const evidence = visibleText(citation.evidence, 16, 12288, `research citation ${citation.sourceId}`);
      return {
        batchSha256: citation.batchSha256,
        sourceId: citation.sourceId,
        evidenceBase64: evidence.toString("base64"),
        evidenceSha256: sha(evidence),
      };
    });
    return {
      findingId: `finding-${index + 1}`,
      statementBase64: statement.toString("base64"),
      material: true,
      citations,
    };
  });
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json",
    schemaVersion: 1,
    reportId: `researchreport-${String(milliseconds).padStart(13, "0")}-${suffix}`,
    jobId: plan.jobId,
    claimId: claim.claimId,
    createdAt: now.toISOString().replace(/\.000Z$/u, "Z"),
    planSha256: sha(plan),
    researchPolicySha256: sha(plan.research),
    batchSha256s,
    titleBase64: title.toString("base64"),
    findings,
    limitationsBase64: limitations.toString("base64"),
    dataClassification: "public",
    privateDataIncluded: false,
    externalEffects: false,
    authority: { ...AUTHORITY },
    boundary: REPORT_BOUNDARY,
  };
  schema("final research report", validateWorkResearchReport(report));
  return report;
}

export const researchReportFinalizerContract = Object.freeze({
  proposalBoundary: PROPOSAL_BOUNDARY,
  reportBoundary: REPORT_BOUNDARY,
  authority: AUTHORITY,
});

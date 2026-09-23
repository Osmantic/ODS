import { createHash, randomBytes } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import { join, posix, resolve, sep } from "node:path";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  canonical, validateWorkConsumption, validateWorkPlan, validateWorkScoutReport,
  validateWorkScoutReportProposal, validateWorkScoutVerification,
} from "../../scripts/lib/work-contract.mjs";

const authority = Object.freeze({
  hostAccess: false, credentials: false, network: false, externalEffects: false,
  publish: false, merge: false, deploy: false, policyMutation: false, scopeExpansion: false,
});

export class ScoutReportError extends Error {}

function fail(message, workFailureStage = "proposal-contract") {
  const error = new ScoutReportError(message);
  error.workFailureStage = workFailureStage;
  throw error;
}
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function checkedNow(value, label) {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isSafeInteger(date.getTime())) fail(`${label} is invalid`, "artifact-retention");
  return date;
}

function checkedSuffix(value, label) {
  const suffix = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(suffix)) fail(`${label} is invalid`, "artifact-retention");
  return suffix;
}

function base64(value, label) {
  const bytes = Buffer.from(value ?? "", "base64");
  if (bytes.length < 1 || bytes.length > 65536 || bytes.toString("base64") !== value) fail(`${label} is not canonical bounded base64`, "evidence-finalization");
  return bytes;
}

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`, "evidence-finalization");
  if (await realpath(path).catch(() => null) !== resolve(path)) fail(`${label} traverses a link or junction`, "evidence-finalization");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`, "evidence-finalization");
  return path;
}

function validateProposalSemantics(proposal) {
  const findingIds = new Set();
  for (const finding of proposal.findings) {
    if (findingIds.has(finding.findingId)) fail("Scout report proposal repeats a finding identifier");
    findingIds.add(finding.findingId);
    const evidenceIds = new Set();
    for (const evidence of finding.evidence) {
      if (evidence.path.startsWith("/") || posix.normalize(evidence.path) !== evidence.path || evidence.path === ".") fail("Scout report evidence path is not normalized and relative");
      const identity = `${evidence.inputId}\u0000${evidence.path}\u0000${evidence.quoteBase64}`;
      if (evidenceIds.has(identity)) fail("Scout report proposal repeats identical evidence");
      evidenceIds.add(identity);
    }
  }
  return proposal;
}

export function parseScoutReportProposal(text) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") < 2 || Buffer.byteLength(text, "utf8") > 1024 * 1024) fail("Scout report proposal text is invalid or oversized", "proposal-parse");
  let proposal;
  try { proposal = JSON.parse(text); } catch {
    const trimmed = text.trim().replace(/^\uFEFF/u, "").trimStart();
    const fenced = /^```json[\t ]*\r?\n([\s\S]*?)\r?\n```$/iu.exec(trimmed);
    if (!fenced) fail("Scout report proposal is not JSON", "proposal-parse");
    try { proposal = JSON.parse(fenced[1]); } catch { fail("Scout report proposal is not JSON", "proposal-parse"); }
  }
  const errors = validateWorkScoutReportProposal(proposal);
  if (errors.length) fail(`Scout report proposal is invalid: ${errors[0]}`);
  return validateProposalSemantics(proposal);
}

async function verifyEvidence(workspaceRoot, plan, raw) {
  const input = plan.inputs.find((entry) => entry.id === raw.inputId);
  if (!input) fail("Scout report evidence references an undeclared input", "evidence-finalization");
  const inputRoot = await privateDirectory(join(workspaceRoot, input.id), "Scout report input root");
  const path = resolve(inputRoot, ...raw.path.split("/"));
  if (path === inputRoot || !path.startsWith(`${inputRoot}${sep}`)) fail("Scout report evidence path escapes its input", "evidence-finalization");
  const quote = base64(raw.quoteBase64, "Scout report evidence quote");
  if (await realpath(path).catch(() => null) !== path) fail("Scout report evidence path traverses a link or junction", "evidence-finalization");
  const { bytes, details } = await readBoundedRegularFile(path, Math.min(input.bytes, plan.budgets.maxDiskBytes), "Scout report evidence file");
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o022) !== 0)) fail("Scout report evidence file is not private, single-link, and read-only", "evidence-finalization");
  const offset = bytes.indexOf(quote);
  if (offset < 0) fail("Scout report quote is absent from the declared local file", "evidence-finalization");
  return {
    inputId: raw.inputId, path: raw.path, quoteBase64: raw.quoteBase64, quoteSha256: sha(quote),
    fileSha256: sha(bytes), offset, bytes: quote.length,
  };
}

export async function finalizeScoutReport({
  proposal, plan, claim, workspacePath, now = new Date(), reportSuffix, verificationSuffix,
}) {
  const proposalErrors = validateWorkScoutReportProposal(proposal);
  const planErrors = validateWorkPlan(plan);
  const claimErrors = validateWorkConsumption(claim);
  if (proposalErrors.length) fail(`Scout report proposal is invalid: ${proposalErrors[0]}`);
  validateProposalSemantics(proposal);
  if (planErrors.length || claimErrors.length || plan.profile !== "scout" || claim.jobId !== plan.jobId || claim.planSha256 !== sha(plan)) fail("Scout report inputs are invalid or unbound", "candidate-contract");
  const workspaceRoot = await privateDirectory(resolve(workspacePath), "Scout report workspace");
  const created = checkedNow(now, "Scout report time");
  const findings = [];
  for (const finding of proposal.findings) {
    const evidence = [];
    for (const item of finding.evidence) evidence.push(await verifyEvidence(workspaceRoot, plan, item));
    findings.push({ findingId: finding.findingId, statementBase64: finding.statementBase64, material: finding.material, evidence });
  }
  const privateDataIncluded = plan.dataClassification !== "public";
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-scout-report-v1.schema.json", schemaVersion: 1,
    reportId: `scoutreport-${String(created.getTime()).padStart(13, "0")}-${checkedSuffix(reportSuffix, "Scout report suffix")}`,
    jobId: claim.jobId, claimId: claim.claimId, createdAt: created.toISOString(), planSha256: claim.planSha256,
    workspaceSha256: claim.workspaceSha256, titleBase64: proposal.titleBase64, findings,
    limitationsBase64: proposal.limitationsBase64, dataClassification: plan.dataClassification,
    privateDataIncluded, externalEffects: false, authority: { ...authority },
    boundary: "Retained local Scout findings with controller-verified exact file evidence. Evidence presence does not establish semantic entailment, completeness, truth, or completion authority.",
  };
  const reportErrors = validateWorkScoutReport(report);
  if (reportErrors.length) fail(`Scout report is invalid: ${reportErrors[0]}`, "artifact-retention");
  const verificationTime = new Date(created.getTime() + 1);
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-scout-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `scoutverification-${String(verificationTime.getTime()).padStart(13, "0")}-${checkedSuffix(verificationSuffix, "Scout verification suffix")}`,
    jobId: claim.jobId, claimId: claim.claimId, createdAt: verificationTime.toISOString(), planSha256: claim.planSha256,
    workspaceSha256: claim.workspaceSha256, reportSha256: sha(report), status: "evidence-pass",
    verificationLevel: "deterministic-local-evidence-presence", semanticEntailmentVerified: false,
    independent: true, network: "none", modelUsed: false,
    findings: findings.map((finding) => ({
      findingId: finding.findingId, status: "pass",
      evidence: finding.evidence.map(({ inputId, path, quoteSha256, fileSha256, offset, bytes }) => ({ inputId, path, quoteSha256, fileSha256, offset, bytes, status: "present" })),
    })),
    privateDataIncluded, externalEffects: false, authority: { ...authority },
    boundary: "Independent networkless proof of exact local evidence presence only. It grants no semantic truth, completion, execution, publication, merge, deployment, policy, or scope authority.",
  };
  const verificationErrors = validateWorkScoutVerification(verification);
  if (verificationErrors.length) fail(`Scout verification is invalid: ${verificationErrors[0]}`, "artifact-retention");
  return { report, verification };
}

export const scoutReportBoundary = "Controller-finalized Scout report with exact local-file evidence presence only. Worker text cannot grant truth, completion, execution, external effects, or scope.";

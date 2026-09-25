import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { inspectGoalSemanticCandidate } from "./goal-accept-cli.mjs";

const maximumProjectionBytes = 1024 * 1024;
const boundary = "Process-lifetime private local review of one exact safe semantic candidate. It may reveal owner-authorized work content in the token-gated loopback page, but no host-absolute path, credential, provider secret, execution, lease, replay, acceptance, completion, publication, deployment, external effect, or scope-expansion authority.";
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsAcceptance: false,
  grantsCompletion: false, grantsPublication: false, grantsDeployment: false,
  grantsExternalEffects: false, grantsScopeExpansion: false,
});

export class GoalReviewCliError extends Error {}

function fail(message) { throw new GoalReviewCliError(message); }

function strictText(value, label) {
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.from(value, "base64")); }
  catch { fail(`${label} is not strict UTF-8`); }
  if (!text || /\u0000/u.test(text)) fail(`${label} is empty or contains a null byte`);
  return text;
}

function verificationSummary(profile, verification) {
  if (profile === "scout") {
    const checkedItems = verification.findings.reduce((sum, finding) => sum + finding.evidence.length, 0);
    return {
      status: verification.status, method: "deterministic-local-evidence-presence", independent: verification.independent,
      semanticAccuracyVerified: verification.semanticEntailmentVerified, checkedItems, failedItems: 0,
      explanation: "Pixel independently reopened the retained local files and confirmed every quoted byte. This proves evidence presence, not that the findings are correct or complete.",
    };
  }
  if (profile === "researcher") {
    const citations = verification.findings.flatMap((finding) => finding.citations);
    return {
      status: verification.status, method: "deterministic-public-evidence-presence", independent: verification.independent,
      semanticAccuracyVerified: verification.semanticEntailmentVerified, checkedItems: citations.length,
      failedItems: citations.filter((citation) => citation.status !== "present").length,
      explanation: "Pixel independently matched every quoted passage to retained public-source evidence. This proves citation integrity and presence, not truth, completeness, or semantic entailment.",
    };
  }
  return {
    status: verification.status, method: "networkless-exact-replay", independent: true,
    semanticAccuracyVerified: verification.semanticAccuracyVerified, checkedItems: verification.artifacts.length,
    failedItems: verification.artifacts.filter((artifact) => artifact.status !== "match").length,
    explanation: "Pixel reran the recipe in a fresh networkless workspace and compared every output byte-for-byte. This proves reproducibility and artifact integrity, not semantic or business correctness.",
  };
}

function scoutContent(report) {
  return {
    title: strictText(report.titleBase64, "Scout title"), overview: null, methodology: null,
    findings: report.findings.map((finding) => ({
      statement: strictText(finding.statementBase64, "Scout finding"), material: finding.material,
      evidence: finding.evidence.map((evidence) => ({
        kind: "local-quote", reference: `${evidence.inputId}:${evidence.path}`,
        excerpt: strictText(evidence.quoteBase64, "Scout evidence"), bytes: evidence.bytes,
      })),
    })),
    limitations: strictText(report.limitationsBase64, "Scout limitations"), artifacts: [],
  };
}

function researcherContent(report) {
  return {
    title: strictText(report.titleBase64, "Researcher title"), overview: null, methodology: null,
    findings: report.findings.map((finding) => ({
      statement: strictText(finding.statementBase64, "Researcher finding"), material: finding.material,
      evidence: finding.citations.map((citation) => ({
        kind: "public-quote", reference: citation.sourceId,
        excerpt: strictText(citation.evidenceBase64, "Researcher evidence"),
        bytes: Buffer.from(citation.evidenceBase64, "base64").length,
      })),
    })),
    limitations: strictText(report.limitationsBase64, "Researcher limitations"), artifacts: [],
  };
}

function dataContent(report) {
  const artifacts = new Map(report.artifacts.map((artifact) => [artifact.path, artifact]));
  return {
    title: report.title, overview: report.summary, methodology: report.methodology,
    findings: report.findings.map((finding) => ({
      statement: finding.statement, material: true,
      evidence: finding.evidence.map((evidence) => ({
        kind: "derived-artifact", reference: evidence.path, excerpt: null,
        bytes: artifacts.get(evidence.path)?.bytes ?? 0,
      })),
    })),
    limitations: report.limitations,
    artifacts: report.artifacts.map(({ path, kind, format, bytes, purpose }) => ({ path, kind, format, bytes, purpose })),
  };
}

export async function buildGoalSemanticReview(configPath) {
  const inspected = await inspectGoalSemanticCandidate(resolve(configPath));
  const profile = inspected.job.profile;
  const report = inspected.artifacts.report;
  const verification = inspected.artifacts.verification;
  const content = profile === "scout" ? scoutContent(report)
    : profile === "researcher" ? researcherContent(report) : dataContent(report);
  const result = {
    $schema: "https://osmantic.com/pixel/schemas/control-work-semantic-review-v1.schema.json",
    schemaVersion: 1, operation: "pixel-control-work-semantic-review", status: "waiting-authority",
    profile, dataClassification: inspected.run.head.plan.dataClassification,
    objective: inspected.run.head.plan.objective,
    acceptanceCriteria: [...inspected.run.head.plan.acceptanceCriteria],
    reviewSha256: inspected.reviewSha256, content,
    verification: verificationSummary(profile, verification),
    completionEffect: "none-read-only",
    privacy: {
      privateContentIncluded: report.privateDataIncluded,
      relativeEvidenceReferencesIncluded: content.findings.some((finding) => finding.evidence.length > 0),
      hostAbsolutePathsIncluded: false, credentialsIncluded: false, contentLeavesHost: false,
    },
    authority: { ...authority }, boundary,
  };
  const bytes = Buffer.byteLength(canonical(result));
  if (bytes > maximumProjectionBytes) fail("semantic review exceeds the private browser projection ceiling; use the trusted terminal review path");
  return result;
}

export async function main(argv = process.argv.slice(2)) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) {
    fail("Usage: goal-review-cli.mjs --config FILE");
  }
  process.stdout.write(`${JSON.stringify(await buildGoalSemanticReview(argv[1]))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-review: ${error instanceof GoalReviewCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalReviewBoundary = boundary;

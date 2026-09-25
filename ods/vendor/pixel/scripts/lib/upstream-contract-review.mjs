import { createHash } from "node:crypto";

function fail(message) {
  throw new Error(message);
}

function text(value, label, minimum = 1) {
  if (typeof value !== "string" || value.trim().length < minimum) fail(`${label} is missing or too short`);
  return value.trim();
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

export function applyContractReview(report, review, expected) {
  if (!review || typeof review !== "object" || Array.isArray(review)) fail("upstream contract review must be an object");
  if (review.schemaVersion !== 1) fail("upstream contract review schemaVersion must be 1");
  if (review.candidateManifestSha256 !== expected.candidateManifestSha256) fail("upstream contract review refers to another candidate manifest");
  if (review.intakeSourceCommit !== expected.intakeSourceCommit) fail("upstream contract review refers to another intake source");
  const general = review.generalReview;
  if (!general || !["compatible", "blocked"].includes(general.decision)) fail("general review decision must be compatible or blocked");
  const reviewer = text(general.reviewer, "general review reviewer", 3);
  const reviewedAt = text(general.reviewedAt, "general review timestamp", 10);
  if (Number.isNaN(Date.parse(reviewedAt))) fail("general review timestamp is invalid");
  const rationale = text(general.rationale, "general review rationale", 20);
  if (!Array.isArray(review.decisions)) fail("upstream contract review decisions must be an array");
  const detected = new Map(report.blockers.map((item) => [item.id, item]));
  const decisions = new Map();
  for (const decision of review.decisions) {
    const id = text(decision?.id, "review decision id");
    if (!detected.has(id)) fail(`review decision does not match a detected blocker: ${id}`);
    if (decisions.has(id)) fail(`duplicate review decision: ${id}`);
    if (!["compatible", "blocked"].includes(decision.decision)) fail(`review decision is invalid: ${id}`);
    if (decision.risk !== "none") fail(`review decision cannot waive a security finding: ${id}`);
    decisions.set(id, {
      id,
      decision: decision.decision,
      risk: "none",
      reviewer: text(decision.reviewer, `reviewer for ${id}`, 3),
      reviewedAt: text(decision.reviewedAt, `review timestamp for ${id}`, 10),
      rationale: text(decision.rationale, `review rationale for ${id}`, 20),
    });
    if (Number.isNaN(Date.parse(decision.reviewedAt))) fail(`review timestamp is invalid: ${id}`);
  }
  for (const id of detected.keys()) if (!decisions.has(id)) fail(`detected blocker has no explicit decision: ${id}`);
  const accepted = [];
  const blockers = [];
  for (const [id, finding] of detected) {
    const decision = decisions.get(id);
    if (decision.decision === "compatible") accepted.push({ finding, decision });
    else blockers.push({ ...finding, review: decision });
  }
  const reviewDigest = createHash("sha256").update(canonical(review)).digest("hex");
  return {
    ...report,
    detectedBlockers: report.blockers,
    acceptedFindings: accepted,
    blockers,
    status: general.decision === "compatible" && blockers.length === 0 ? "compatible" : "blocked",
    review: { reviewer, reviewedAt, rationale, decision: general.decision, sha256: reviewDigest },
  };
}

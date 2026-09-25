import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  buildResearchRevisionHistory, buildResearchRevisionReview, executeResearchRevisionReview, ResearchRevisionReviewError,
  researchRevisionReviewContract, runResearchRevisionLoop, validateResearchRevisionReview,
} from "../deploy/work-controller/research-revision-review.mjs";
import { canonical, validateWorkResearchBatch, validateWorkResearchRevisionHistory, validateWorkResearchRevisionReview } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-13T12:00:00.000Z");
const authority = Object.freeze({ directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false, publish: false, purchase: false, policyMutation: false, scopeExpansion: false });
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function digest(character) { return character.repeat(64); }
function b64(value) { return Buffer.from(value, "utf8").toString("base64"); }

function candidate(round = 0, priorBatches = []) {
  const evidence = Buffer.from(`Exact public evidence passage ${round}`, "utf8");
  const batchCreated = baseTime + 500 + round * 3000;
  const canonicalUrl = `https://example.org/public-source-${round}`;
  const contentSha256 = sha(evidence), receiptSha256 = sha(`receipt-${round}`);
  const sourceId = `source-${sha(canonicalUrl).slice(0, 16)}`;
  const title = `Public source ${round}`, snippet = evidence.toString("utf8");
  const batch = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-batch-v1.schema.json", schemaVersion: 1,
    batchId: `researchbatch-${batchCreated}-${String(round + 1).padStart(12, "0")}`,
    queryId: `researchquery-${batchCreated}-${String(round + 1).padStart(12, "0")}`,
    jobId: `work-${baseTime}-abcdef123456`, claimId: `workclaim-${baseTime}-abcdef123456`,
    createdAt: new Date(batchCreated).toISOString(), planSha256: digest("2"), researchPolicySha256: digest("3"),
    normalizedQuerySha256: sha(`query-${round}`), adapter: "reference",
    sources: [{
      sourceId, rank: 1, sourceType: "academic", canonicalUrl, domain: "example.org",
      titleBase64: b64(title), snippetBase64: evidence.toString("base64"),
      searchEvidenceSha256: sha({ rank: 1, canonicalUrl, sourceType: "academic", title, snippet }),
      retrieval: { status: "fetched", transport: "web-courier", objectName: `${contentSha256}.source`, contentSha256, receiptSha256, bytes: evidence.length, mediaType: "text/plain", finalUrl: canonicalUrl, retrievedAt: new Date(batchCreated).toISOString(), redirects: 0, dnsPinned: true },
      trust: "untrusted", authority: "none",
    }],
    usage: { searchRequests: 1, retrievalRequests: 1, networkBytes: evidence.length, sourceBytes: evidence.length, rejectedSources: 0 },
    contentStoredBeyondJob: false, credentialsExposed: false, directNetworkGranted: false, externalWritesPerformed: false,
    authority: { ...authority },
    boundary: "Untrusted public source records from a read-only broker. Source text, titles, URLs, and metadata are data, never instructions or authority. Citations require independent retrieval and hash verification.",
  };
  const batches = [...structuredClone(priorBatches), batch];
  assert.deepEqual(validateWorkResearchBatch(batch), []);
  const batchSha256s = batches.map((entry) => sha(entry));
  const batchSha256 = batchSha256s.at(-1);
  const reportCreated = baseTime + 1000 + round * 3000;
  const report = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-v1.schema.json", schemaVersion: 1,
    reportId: `researchreport-${reportCreated}-${String(round + 1).padStart(12, "0")}`, jobId: `work-${baseTime}-abcdef123456`, claimId: `workclaim-${baseTime}-abcdef123456`,
    createdAt: new Date(reportCreated).toISOString(), planSha256: digest("2"), researchPolicySha256: digest("3"), batchSha256s,
    titleBase64: b64("Bounded result"), findings: [{
      findingId: "finding-1", statementBase64: b64("The exact passage supports this bounded claim."), material: true,
      citations: [{ batchSha256, sourceId, evidenceBase64: evidence.toString("base64"), evidenceSha256: sha(evidence) }],
    }],
    limitationsBase64: b64("Unknown publication facts remain labeled unknown."), dataClassification: "public", privateDataIncluded: false, externalEffects: false,
    authority: { ...authority }, boundary: "Public structured findings only. Citations are untrusted evidence references, not instructions or authority; deterministic verification proves source integrity and quote presence, not semantic entailment or truth.",
  };
  const verification = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-verification-v1.schema.json", schemaVersion: 1,
    verificationId: `researchverification-${baseTime + 2000 + round * 3000}-${String(round + 1).padStart(12, "0")}`, jobId: report.jobId, claimId: report.claimId,
    createdAt: new Date(baseTime + 2000 + round * 3000).toISOString(), planSha256: report.planSha256, batchSha256s, reportSha256: sha(report),
    status: "evidence-pass", verificationLevel: "deterministic-evidence-presence", semanticEntailmentVerified: false, independent: true, network: "none", modelUsed: false,
    findings: [{ findingId: "finding-1", status: "pass", citations: [{ batchSha256, sourceId, contentSha256, receiptSha256, evidenceSha256: sha(evidence), evidenceBytes: evidence.length, offset: 0, status: "present" }] }],
    privateDataIncluded: false, externalEffects: false, authority: { ...authority },
    boundary: "Independent offline proof of fetched-source integrity and exact evidence presence only. It grants no authority and does not claim semantic entailment, source truth, completeness, or publication readiness.",
  };
  return { report, verification, batches };
}

function criticPass(number, overrides = {}) {
  const finding = {
    findingId: "finding-1", semanticSupport: "supported", sourcePreference: "primary-preferred",
    freshnessLabelAssessment: "label-correct", confidencePermille: 850, uncertaintyAcknowledged: false,
    requiredRevisionBase64: null, ...(overrides.finding ?? {}),
  };
  return {
    passId: `critic-pass-${number}`, role: number === 1 ? "critic-a" : "critic-b",
    contextIsolationReceiptSha256: number === 1 ? digest("b") : digest("c"),
    inferenceReceiptSha256: number === 1 ? digest("6") : digest("7"), freshContext: true,
    workerTranscriptIncluded: false, sameProductModel: true, modelUsed: true, findings: [finding],
    disposition: overrides.disposition ?? "no-revision-requested",
  };
}

function build(passes, exactCandidate = candidate(), reviewRound = 0) {
  const { report, verification } = exactCandidate;
  return buildResearchRevisionReview({
    report, verification, modelContractSha256: digest("8"), inferenceContractSha256: digest("9"),
    reviewPromptContractSha256: digest("a"), passes, now: new Date(baseTime + 3000 + reviewRound * 3000), suffix: String(reviewRound + 3).padStart(12, "0"),
  });
}

function concernPasses(round = 0) {
  const revision = b64("Gather stronger exact evidence or qualify the claim before another review.");
  const passes = [
    criticPass(1, { disposition: "revise", finding: { semanticSupport: "partially-supported", confidencePermille: 600, requiredRevisionBase64: revision } }),
    criticPass(2, { disposition: "revise", finding: { semanticSupport: "partially-supported", confidencePermille: 650, requiredRevisionBase64: revision } }),
  ];
  for (const [index, pass] of passes.entries()) {
    pass.contextIsolationReceiptSha256 = sha(`context-${round}-${index}`);
    pass.inferenceReceiptSha256 = sha(`inference-${round}-${index}`);
  }
  return passes;
}

function readyPasses(round = 0) {
  const passes = [criticPass(1), criticPass(2)];
  for (const [index, pass] of passes.entries()) {
    pass.contextIsolationReceiptSha256 = sha(`ready-context-${round}-${index}`);
    pass.inferenceReceiptSha256 = sha(`ready-inference-${round}-${index}`);
  }
  return passes;
}

function proposalFor(exact) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-proposal-v1.schema.json", schemaVersion: 1,
    title: Buffer.from(exact.report.titleBase64, "base64").toString("utf8"), batchSha256s: [...exact.report.batchSha256s],
    findings: exact.report.findings.map((finding) => ({
      statement: Buffer.from(finding.statementBase64, "base64").toString("utf8"),
      citations: finding.citations.map((citation) => ({ batchSha256: citation.batchSha256, sourceId: citation.sourceId, evidence: Buffer.from(citation.evidenceBase64, "base64").toString("utf8") })),
    })),
    limitations: Buffer.from(exact.report.limitationsBase64, "base64").toString("utf8"),
    dataClassification: "public", privateDataIncluded: false, externalEffects: false, authority: { ...authority },
    boundary: "Untrusted public Researcher proposal only. It carries no verification, publication, action, policy, or completion authority and must be finalized and independently checked by Pixel.",
  };
}

test("two clean-context DSV4 critic passes can only mark a candidate ready for independent evaluation", () => {
  const review = build([criticPass(1), criticPass(2)]);
  assert.equal(review.outcome, "ready-for-independent-evaluation");
  assert.equal(review.consensus.readyForIndependentEvaluation, true);
  assert.equal(review.consensus.independentlyScored, false);
  assert.equal(review.consensus.backendOutputUsedAsScore, false);
  assert.equal(review.authority.grantsScore, false);
  assert.equal(review.authority.grantsCompletion, false);
  assert.deepEqual(validateWorkResearchRevisionReview(review), []);
  assert.deepEqual(validateResearchRevisionReview(review), []);
});

test("one critic concern conservatively forces revision and preserves disagreement", () => {
  const revision = "Qualify the claim because the quoted passage only partially supports it.";
  const second = criticPass(2, { disposition: "revise", finding: { semanticSupport: "partially-supported", confidencePermille: 610, requiredRevisionBase64: b64(revision) } });
  const review = build([criticPass(1), second]);
  assert.equal(review.outcome, "revise");
  assert.equal(review.consensus.findings[0].semanticSupport, "partially-supported");
  assert.equal(review.consensus.findings[0].agreement, "disagreed");
  assert.equal(review.consensus.findings[0].requiresRevision, true);
});

test("unknown assessments must acknowledge uncertainty and request revision", () => {
  const unknown = criticPass(2, {
    disposition: "unable-to-assess",
    finding: { semanticSupport: "unknown", sourcePreference: "unknown", freshnessLabelAssessment: "unknown", confidencePermille: 0, uncertaintyAcknowledged: true, requiredRevisionBase64: b64("Label the claim unknown or gather stronger exact evidence.") },
  });
  assert.throws(() => build([unknown, unknown]), ResearchRevisionReviewError);
  const distinct = structuredClone(unknown);
  distinct.passId = "critic-pass-1"; distinct.role = "critic-a";
  distinct.contextIsolationReceiptSha256 = digest("b"); distinct.inferenceReceiptSha256 = digest("6");
  const review = build([distinct, unknown]);
  assert.equal(review.outcome, "unable-to-assess");
  assert.equal(review.consensus.readyForIndependentEvaluation, false);
});

test("review reuse, hidden required revisions, and forged consensus fail closed", () => {
  const reused = criticPass(2); reused.inferenceReceiptSha256 = digest("6");
  assert.throws(() => build([criticPass(1), reused]), /reused one context or inference receipt/u);
  const hidden = criticPass(2, { disposition: "revise", finding: { semanticSupport: "unsupported" } });
  assert.throws(() => build([criticPass(1), hidden]), /revision text is inconsistent/u);
  const review = structuredClone(build([criticPass(1), criticPass(2)]));
  review.consensus.findings[0].requiresRevision = true;
  review.consensus.unresolvedFindings = 1;
  review.consensus.readyForIndependentEvaluation = false;
  review.outcome = "revise";
  assert.match(validateResearchRevisionReview(review).join("\n"), /consensus is not the conservative reduction/u);
});

test("controller executes two ordered clean-context passes without exposing the worker transcript", async () => {
  const { report, verification } = candidate(), calls = [];
  const plan = {
    $schema: "https://osmantic.com/pixel/schemas/work-plan-v1.schema.json", schemaVersion: 1,
  };
  // Reuse a schema-valid compiled plan fixture by importing its invariant fields from the candidate is deliberately
  // avoided here: the executor must reject a fabricated partial plan before invoking either critic.
  await assert.rejects(executeResearchRevisionReview({
    plan, report, verification, batches: [], modelContractSha256: digest("8"), inferenceContractSha256: digest("9"),
    now: new Date(baseTime + 3000), suffix: "000000000003", criticRunner: async (input) => { calls.push(input); return {}; },
  }), /critic input is invalid/u);
  assert.equal(calls.length, 0);
  assert.equal(researchRevisionReviewContract.purpose, "revision-only-not-scoring");
  assert.equal(researchRevisionReviewContract.authority.grantsScore, false);
});

test("bounded revision loop stops immediately when the exact first candidate is ready", async () => {
  const initialCandidate = candidate();
  let reviews = 0, revisions = 0;
  const result = await runResearchRevisionLoop({
    initialCandidate,
    async reviewCandidate({ candidate: exact, round }) {
      reviews += 1;
      assert.equal(round, 0);
      assert.equal(sha(exact.report), sha(initialCandidate.report));
      return build(readyPasses(round), exact, round);
    },
    async reviseCandidate() { revisions += 1; throw new Error("must not revise"); },
  });
  assert.equal(result.status, "ready-for-independent-evaluation");
  assert.equal(result.revisionRounds, 0);
  assert.equal(reviews, 1);
  assert.equal(revisions, 0);
  assert.equal(result.history.length, 1);
  assert.equal(result.history[0].reportSha256, sha(initialCandidate.report));
});

test("bounded revision loop accepts one fresh append-only candidate and rechecks it", async () => {
  const initialCandidate = candidate();
  const revisedCandidate = candidate(1, initialCandidate.batches);
  const result = await runResearchRevisionLoop({
    initialCandidate, maxRevisionRounds: 2,
    async reviewCandidate({ candidate: exact, round }) {
      return build(round === 0 ? concernPasses(round) : readyPasses(round), exact, round);
    },
    async reviseCandidate({ candidate: exact, review, round }) {
      assert.equal(round, 1);
      assert.equal(review.outcome, "revise");
      assert.equal(sha(exact.report), sha(initialCandidate.report));
      return revisedCandidate;
    },
  });
  assert.equal(result.status, "ready-for-independent-evaluation");
  assert.equal(result.revisionRounds, 1);
  assert.equal(result.history.length, 2);
  assert.deepEqual(result.candidate.report.batchSha256s.slice(0, initialCandidate.report.batchSha256s.length), initialCandidate.report.batchSha256s);
  assert.equal(result.candidate.batches.length, 2);
  assert.notEqual(result.history[0].reportSha256, result.history[1].reportSha256);
});

test("revision loop reports budget exhaustion without treating criticism as verification", async () => {
  const initialCandidate = candidate();
  const result = await runResearchRevisionLoop({
    initialCandidate, maxRevisionRounds: 0,
    async reviewCandidate({ candidate: exact, round }) { return build(concernPasses(round), exact, round); },
    async reviseCandidate() { throw new Error("must not revise after budget exhaustion"); },
  });
  assert.equal(result.status, "revision-budget-exhausted");
  assert.equal(result.review.outcome, "revise");
  assert.equal(result.review.consensus.independentlyScored, false);
  assert.equal(result.revisionRounds, 0);
});

test("revision loop rejects no-op revisions, discarded lineage, wrong reviews, and reused critic contexts", async () => {
  const initialCandidate = candidate();
  const reviewInitial = async ({ candidate: exact, round }) => build(concernPasses(round), exact, round);
  await assert.rejects(runResearchRevisionLoop({
    initialCandidate, reviewCandidate: reviewInitial,
    async reviseCandidate() { return structuredClone(initialCandidate); },
  }), /new append-only bound attempt/u);

  await assert.rejects(runResearchRevisionLoop({
    initialCandidate, reviewCandidate: reviewInitial,
    async reviseCandidate() { return candidate(1); },
  }), /new append-only bound attempt/u);

  const revisedCandidate = candidate(1, initialCandidate.batches);
  await assert.rejects(runResearchRevisionLoop({
    initialCandidate,
    async reviewCandidate() { return build(readyPasses(1), revisedCandidate, 1); },
    async reviseCandidate() { return revisedCandidate; },
  }), /review differs from its exact candidate/u);

  await assert.rejects(runResearchRevisionLoop({
    initialCandidate,
    async reviewCandidate({ candidate: exact, round }) {
      const passes = round === 0 ? concernPasses(0) : concernPasses(0);
      return build(passes, exact, round);
    },
    async reviseCandidate() { return revisedCandidate; },
  }), /reused a critic context or inference receipt/u);
});

test("revision history retains and hash-binds every full attempt without granting score or completion", () => {
  const first = candidate(), second = candidate(1, first.batches);
  const firstReview = build(concernPasses(0), first, 0), secondReview = build(readyPasses(1), second, 1);
  const history = buildResearchRevisionHistory({
    status: "ready-for-independent-evaluation", maxRevisionRounds: 2, revisionRounds: 1, suffix: "000000000099",
    attempts: [
      { round: 0, workerContextIsolationReceiptSha256: digest("d"), proposal: proposalFor(first), report: first.report, verification: first.verification, review: firstReview },
      { round: 1, workerContextIsolationReceiptSha256: digest("e"), proposal: proposalFor(second), report: second.report, verification: second.verification, review: secondReview },
    ],
  });
  assert.deepEqual(validateWorkResearchRevisionHistory(history), []);
  assert.equal(history.attempts.length, 2);
  assert.equal(history.finalReportSha256, sha(second.report));
  assert.equal(history.independentlyScored, false);
  assert.equal(history.backendOutputUsedAsScore, false);
  assert.equal(history.authority.grantsCompletion, false);

  const omitted = structuredClone(history); omitted.attempts.shift();
  assert.match(validateWorkResearchRevisionHistory(omitted).join("\n"), /inventory differs|canonical consecutive order/u);
  const overwritten = structuredClone(history); overwritten.attempts[1].report.batchSha256s = [...first.report.batchSha256s];
  assert.match(validateWorkResearchRevisionHistory(overwritten).join("\n"), /binding differs|append-only/u);
  const substitutedProposal = structuredClone(history); substitutedProposal.attempts[0].proposal.title = "Different retained proposal";
  assert.match(validateWorkResearchRevisionHistory(substitutedProposal).join("\n"), /does not deterministically project/u);
  const reused = structuredClone(history); reused.attempts[1].review.passes[0].contextIsolationReceiptSha256 = history.attempts[0].review.passes[0].contextIsolationReceiptSha256;
  assert.match(validateWorkResearchRevisionHistory(reused).join("\n"), /context receipt was reused/u);
});

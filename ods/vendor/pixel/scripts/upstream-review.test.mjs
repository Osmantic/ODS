import assert from "node:assert/strict";
import test from "node:test";
import { applyContractReview } from "./lib/upstream-contract-review.mjs";

function fixture() {
  return {
    schemaVersion: 1,
    sourceCommit: "a".repeat(40),
    status: "blocked",
    packages: [],
    blockers: [{ id: "finding-one", type: "surface-change", evidenceSha256: "b".repeat(64) }],
  };
}

function review(decision = "compatible") {
  return {
    schemaVersion: 1,
    candidateManifestSha256: "c".repeat(64),
    intakeSourceCommit: "a".repeat(40),
    generalReview: {
      decision: "compatible",
      reviewer: "release-reviewer",
      reviewedAt: "2026-08-05T20:00:00Z",
      rationale: "All normalized contract changes were inspected against the documented authority model.",
    },
    decisions: [{
      id: "finding-one",
      decision,
      risk: "none",
      reviewer: "security-reviewer",
      reviewedAt: "2026-08-05T20:00:00Z",
      rationale: "The changed implementation preserves the same externally enforced boundary and adds no authority.",
    }],
  };
}

test("an exact no-risk review can disposition a conservative blocker", () => {
  const report = applyContractReview(fixture(), review(), {
    candidateManifestSha256: "c".repeat(64),
    intakeSourceCommit: "a".repeat(40),
  });
  assert.equal(report.status, "compatible");
  assert.equal(report.blockers.length, 0);
  assert.equal(report.acceptedFindings.length, 1);
  assert.equal(report.detectedBlockers.length, 1);
});

test("reviews cannot waive risk or omit a finding", () => {
  const risky = review();
  risky.decisions[0].risk = "P2";
  assert.throws(() => applyContractReview(fixture(), risky, {
    candidateManifestSha256: "c".repeat(64), intakeSourceCommit: "a".repeat(40),
  }), /cannot waive/);
  const omitted = review();
  omitted.decisions = [];
  assert.throws(() => applyContractReview(fixture(), omitted, {
    candidateManifestSha256: "c".repeat(64), intakeSourceCommit: "a".repeat(40),
  }), /no explicit decision/);
});

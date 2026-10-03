import assert from "node:assert/strict";
import test from "node:test";
import { buildLocalFinalization, canonical, digest } from "../plugin-frontier/local-finalize.js";

const jobId = "frontier-1786195551000-abcdef123456";
const remote = {
  schemaVersion: 2,
  jobId,
  status: "succeeded",
  advice: {
    summary: "Remote structural advice",
    findings: [
      { severity: "medium", title: "First", evidence: "A", recommendation: "Check A" },
      { severity: "low", title: "Second", evidence: "B", recommendation: "Check B" },
    ],
    risks: ["Remote context is incomplete"],
    confidence: "medium",
  },
};

test("local finalization keeps private critique text out of its telemetry receipt", () => {
  const params = {
    jobId,
    verdict: "partial",
    quality: "improved",
    acceptedFindingIndexes: [0],
    rejectedFindingIndexes: [1],
    localConclusion: "Private client conclusion",
    verificationNotes: ["Checked against private source evidence"],
  };
  const salt = "c".repeat(64);
  const { receipt, response } = buildLocalFinalization(remote, params, new Date("2026-08-09T12:00:00.000Z"), salt);
  assert.equal(receipt.resultHash, digest(remote));
  assert.equal(receipt.createdAt, "2026-08-09T12:00:00.000Z");
  assert.equal(response.localConclusion, params.localConclusion);
  assert.deepEqual(response.acceptedFindings, [remote.advice.findings[0]]);
  assert.deepEqual(response.rejectedFindings, [remote.advice.findings[1]]);
  const serializedReceipt = JSON.stringify(receipt);
  assert.ok(!serializedReceipt.includes("Private client conclusion"));
  assert.ok(!serializedReceipt.includes("private source evidence"));
  assert.equal(response.provenance.localFinalAuthority, true);
  assert.equal(response.provenance.remoteAdviceAuthority, false);
  assert.equal(response.provenance.localCommitmentSalt, salt);
  assert.ok(!serializedReceipt.includes(salt));
});

test("local finalization requires an exact finding partition and coherent verdict", () => {
  const base = {
    jobId,
    verdict: "partial",
    quality: "unchanged",
    acceptedFindingIndexes: [0],
    rejectedFindingIndexes: [1],
    localConclusion: "Conclusion",
    verificationNotes: ["Verified"],
  };
  assert.throws(() => buildLocalFinalization(remote, { ...base, rejectedFindingIndexes: [] }), /partition/);
  assert.throws(() => buildLocalFinalization(remote, { ...base, acceptedFindingIndexes: [0, 1], rejectedFindingIndexes: [] }), /partial/);
  assert.throws(() => buildLocalFinalization(remote, { ...base, verdict: "adopt" }), /adopt/);
  assert.throws(() => buildLocalFinalization({ ...remote, status: "failed" }, base), /successful/);
  assert.throws(() => buildLocalFinalization({ ...remote, jobId: "frontier-1786195551001-abcdef123457" }, base), /identity/);
});

test("canonical hashing is stable across object key order", () => {
  assert.equal(canonical({ b: 2, a: [1, { d: 4, c: 3 }] }), '{"a":[1,{"c":3,"d":4}],"b":2}');
  assert.equal(digest({ b: 2, a: 1 }), digest({ a: 1, b: 2 }));
});

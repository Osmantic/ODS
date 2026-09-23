import { createHash, randomBytes } from "node:crypto";

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function digest(value) {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

export function buildLocalFinalization(remote, params, now = new Date(), commitmentSalt = randomBytes(32).toString("hex")) {
  if (remote?.status !== "succeeded" || !remote.advice || !Array.isArray(remote.advice.findings)) {
    throw new Error("only a successful Frontier result can be locally finalized");
  }
  if (remote.jobId !== params.jobId) throw new Error("Frontier result identity does not match local finalization");
  if (!/^[a-f0-9]{64}$/.test(commitmentSalt)) throw new Error("local finalization commitment salt is invalid");
  const accepted = [...params.acceptedFindingIndexes].sort((a, b) => a - b);
  const rejected = [...params.rejectedFindingIndexes].sort((a, b) => a - b);
  const expected = remote.advice.findings.map((_finding, index) => index);
  const observed = [...accepted, ...rejected].sort((a, b) => a - b);
  if (new Set(observed).size !== observed.length || canonical(observed) !== canonical(expected)) {
    throw new Error("accepted and rejected finding indexes must partition the exact Frontier findings");
  }
  if (params.verdict === "adopt" && rejected.length !== 0) throw new Error("adopt cannot reject a finding");
  if (params.verdict === "reject" && accepted.length !== 0) throw new Error("reject cannot accept a finding");
  if (params.verdict === "partial" && (accepted.length === 0 || rejected.length === 0)) {
    throw new Error("partial requires both accepted and rejected findings");
  }
  const resultHash = digest(remote);
  const localOutputHash = digest({
    commitmentSalt,
    jobId: params.jobId,
    resultHash,
    verdict: params.verdict,
    quality: params.quality,
    acceptedFindingIndexes: accepted,
    rejectedFindingIndexes: rejected,
    localConclusion: params.localConclusion,
    verificationNotes: params.verificationNotes,
  });
  const receipt = {
    schemaVersion: 1,
    jobId: params.jobId,
    createdAt: now.toISOString(),
    resultHash,
    verdict: params.verdict,
    quality: params.quality,
    acceptedFindingIndexes: accepted,
    rejectedFindingIndexes: rejected,
    verificationCount: params.verificationNotes.length,
    localOutputHash,
    boundary: "Content-free local integration evidence; no local conclusion or private verification text.",
  };
  return {
    receipt,
    response: {
      schemaVersion: 1,
      jobId: params.jobId,
      status: "locally-finalized",
      localConclusion: params.localConclusion,
      verificationNotes: params.verificationNotes,
      remoteSummary: remote.advice.summary,
      acceptedFindings: accepted.map((index) => remote.advice.findings[index]),
      rejectedFindings: rejected.map((index) => remote.advice.findings[index]),
      remoteRisks: remote.advice.risks,
      remoteConfidence: remote.advice.confidence,
      integrationReceipt: receipt,
      provenance: {
        localFinalAuthority: true,
        remoteAdviceAuthority: false,
        telemetryContainsPrivateText: false,
        localCommitmentSalt: commitmentSalt,
      },
    },
  };
}

import { createHash, createPublicKey, verify } from "node:crypto";

import {
  canonical, validateWorkCodexAuthenticationEvidence, validateWorkCodexPlan, validateWorkCodexPolicy,
} from "../../scripts/lib/work-contract.mjs";

const HEX_32 = /^[a-f0-9]{32}$/u;
const HEX_64 = /^[a-f0-9]{64}$/u;

export class WorkCodexAuthenticationError extends Error {}
function fail(message) { throw new WorkCodexAuthenticationError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} failed validation: ${errors.join("; ")}`); }

export function workCodexAuthenticationChallenge({ planSha256, nonce }) {
  if (!HEX_64.test(planSha256 ?? "") || !HEX_32.test(nonce ?? "")) fail("Codex work authentication challenge inputs are invalid");
  return `APPROVE PIXEL CODEX ${planSha256} ${nonce}`;
}

export function workCodexAuthenticationSigningPayload(evidence) {
  schema("Codex work authentication evidence", validateWorkCodexAuthenticationEvidence(evidence));
  const { signature: _signature, ...payload } = evidence;
  return Buffer.from(canonical(payload), "utf8");
}

function trustedEd25519PublicKey(value) {
  if (value?.type === "private" || (typeof value === "string" || Buffer.isBuffer(value)) && /PRIVATE KEY/u.test(value.toString())) fail("Codex work authentication verifier accepts only a public key");
  let key;
  try { key = value?.type === "public" ? value : createPublicKey(value); } catch { fail("Codex work authentication public key is invalid"); }
  if (key.type !== "public" || key.asymmetricKeyType !== "ed25519") fail("Codex work authentication requires an Ed25519 public key");
  const der = key.export({ type: "spki", format: "der" });
  return { key, sha256: sha(der) };
}

export function verifyWorkCodexAuthenticationEvidence({ evidence, plan, policy, trustedPublicKey, now = new Date() }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); schema("Codex work plan", validateWorkCodexPlan(plan));
  schema("Codex work authentication evidence", validateWorkCodexAuthenticationEvidence(evidence));
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) fail("Codex work authentication verification time is invalid");
  if (plan.policySha256 !== sha(policy)) fail("Codex work authentication policy differs from the exact plan");
  const key = trustedEd25519PublicKey(trustedPublicKey), planSha256 = sha(plan);
  const approval = plan.approval, issued = Date.parse(evidence.issuedAt), expires = Date.parse(evidence.expiresAt);
  if (evidence.issuer !== approval.issuer || evidence.audience !== approval.audience || evidence.issuerKeySha256 !== approval.trustedKeySha256 || key.sha256 !== approval.trustedKeySha256) fail("Codex work authentication issuer or trusted key differs from the exact plan");
  if (evidence.issuer !== policy.authorization.issuer || evidence.audience !== policy.authorization.audience || evidence.issuerKeySha256 !== policy.authorization.trustedKeySha256) fail("Codex work authentication issuer differs from private policy");
  if (evidence.signatureAlgorithm !== "ed25519" || policy.authorization.signatureAlgorithm !== "ed25519") fail("Codex work authentication signature algorithm is invalid");
  if (evidence.planId !== plan.planId || evidence.planSha256 !== planSha256 || canonical(evidence.provider) !== canonical({ authMode: plan.provider.authMode, model: plan.provider.model, billingBoundary: plan.provider.billingBoundary, transportSha256: plan.provider.transportSha256 })) fail("Codex work authentication evidence differs from the exact plan or provider");
  if (evidence.authentication.primaryFactor !== policy.authorization.primaryFactor || !policy.authorization.allowedSecondFactors.includes(evidence.authentication.secondFactor) || evidence.authentication.authenticationAgeSeconds > policy.authorization.maxAuthenticationAgeSeconds) fail("Codex work authentication factors differ from private policy");
  if (!evidence.authentication.freshAuthentication || evidence.authentication.credentialsVisibleToPixel) fail("Codex work authentication did not preserve the external fresh-MFA boundary");
  if (issued > now.getTime() || expires <= now.getTime() || expires - issued > policy.authorization.maxEvidenceLifetimeSeconds * 1000 || expires > Date.parse(plan.expiresAt)) fail("Codex work authentication evidence is expired, future-dated, or too long-lived");
  const challenge = workCodexAuthenticationChallenge({ planSha256, nonce: evidence.challengeNonce });
  if (evidence.confirmation.typedChallengeSha256 !== sha(challenge)) fail("Codex work authentication typed challenge differs from the exact plan");
  let signature;
  try { signature = Buffer.from(evidence.signature, "base64url"); } catch { fail("Codex work authentication signature encoding is invalid"); }
  if (signature.length !== 64 || signature.toString("base64url") !== evidence.signature || !verify(null, workCodexAuthenticationSigningPayload(evidence), key.key, signature)) fail("Codex work authentication signature is invalid");
  return Object.freeze({
    evidenceId: evidence.evidenceId, evidenceSha256: sha(evidence), secondFactor: evidence.authentication.secondFactor,
    confirmation: Object.freeze({
      planInspected: true, dataOwnerConfirmed: true, billingAcknowledgment: evidence.confirmation.billingAcknowledgment,
      externalHumanAuthentication: true, authenticationEvidenceSha256: sha(evidence),
    }),
  });
}

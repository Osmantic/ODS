import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { chmod, mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import {
  verifyWorkCodexAuthenticationEvidence, workCodexAuthenticationChallenge,
  workCodexAuthenticationSigningPayload, WorkCodexAuthenticationError,
} from "../deploy/work-codex-provider/authentication.mjs";
import {
  executeWorkCodexAuthorization, initializeWorkCodexStore, readWorkCodexAuthenticationConsumption, storeVerifiedWorkCodexAuthorization,
  storeWorkCodexPreview,
} from "../deploy/work-codex-provider/execution.mjs";
import {
  canonical, validateWorkCodexAuthenticationConsumption, validateWorkCodexAuthenticationEvidence, validateWorkCodexAuthorization,
} from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T22:00:00Z");
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const evidenceBoundary = "External identity-provider evidence only. It attests fresh password plus approved MFA and one exact inspected Codex work plan; Pixel receives no password, one-time code, session token, provider credential, identity claim, tool authority, or external-effect authority.";
const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const publicKeySha256 = sha(publicKey.export({ type: "spki", format: "der" }));

function request(index = 1) {
  const content = `A sanitized structural fixture ${index}.`;
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-${index.toString(16).padStart(12, "0")}`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(),
    jobId: `work-${baseTime}-${(index + 200).toString(16).padStart(12, "0")}`, checkpointSha256: sha(`checkpoint-${index}`), ownerId: "owner-fixture", clientId: "client-fixture",
    taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["structural"],
    localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: sha(`attempt-${index}`) },
    objective: "Review the structural fixture.", constraints: ["Return advisory structure only."], acceptanceCriteria: ["Return one bounded review."], sensitiveTerms: [],
    documents: [{ documentId: `architecture_${index}`, kind: "structure", language: "text", content, contentSha256: sha(content) }], maxOutputTokens: 1024,
    boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.",
  };
}

async function setup(t, index = 1, { api = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), `pixel-codex-authn-${index}-`)); t.after(() => rm(root, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(root, 0o700);
  const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true; policy.authorization.trustedKeySha256 = publicKeySha256;
  if (api) {
    policy.provider.authMode = "api-key";
    policy.transport.allowedHosts = ["api.openai.com"];
    policy.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false };
    policy.provider.billing = { mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.", currency: "USD", inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 2000000, source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 1000000 };
  }
  const input = request(index), compiled = compileWorkCodexPreview({ request: input, policy, now: new Date(baseTime + 1000), suffix: index.toString(16).padStart(12, "0"), mappingNonce: sha(`map-${index}`) });
  await initializeWorkCodexStore({ root }); await storeWorkCodexPreview({ root, request: input, policy, ...compiled });
  return { root, policy, input, ...compiled };
}

function evidenceFor(value, { secondFactor = "totp-authenticator-app", issuedAt = new Date(baseTime + 2000), expiresAt = new Date(baseTime + 5 * 60000), suffix = "000000000111" } = {}) {
  const planSha256 = sha(value.plan), challengeNonce = sha(`nonce-${suffix}`).slice(0, 32);
  const evidence = {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-authentication-evidence-v1.schema.json", schemaVersion: 1,
    evidenceId: `workcodexauthn-${issuedAt.getTime()}-${suffix}`, issuedAt: issuedAt.toISOString(), expiresAt: expiresAt.toISOString(),
    issuer: value.policy.authorization.issuer, audience: value.policy.authorization.audience, issuerKeySha256: publicKeySha256,
    planId: value.plan.planId, planSha256, challengeNonce,
    provider: { authMode: value.plan.provider.authMode, model: value.plan.provider.model, billingBoundary: value.plan.provider.billingBoundary, transportSha256: value.plan.provider.transportSha256 },
    authentication: { primaryFactor: "password", secondFactor, freshAuthentication: true, authenticationAgeSeconds: 12, credentialsVisibleToPixel: false },
    confirmation: { planInspected: true, dataOwnerConfirmed: true, billingAcknowledgment: value.plan.provider.authMode === "chatgpt" ? "chatgpt-plan-or-credits" : "separately-billed-api-platform", typedChallengeSha256: sha(workCodexAuthenticationChallenge({ planSha256, nonce: challengeNonce })) },
    signatureAlgorithm: "ed25519", signature: "A".repeat(86), boundary: evidenceBoundary,
  };
  evidence.signature = sign(null, workCodexAuthenticationSigningPayload(evidence), privateKey).toString("base64url");
  assert.deepEqual(validateWorkCodexAuthenticationEvidence(evidence), []); return evidence;
}

test("trusted password plus authenticator-app evidence creates one content-free authorization", async (t) => {
  const value = await setup(t), evidence = evidenceFor(value), now = new Date(baseTime + 3000);
  const verified = verifyWorkCodexAuthenticationEvidence({ evidence, plan: value.plan, policy: value.policy, trustedPublicKey: publicKey, now });
  assert.equal(verified.secondFactor, "totp-authenticator-app"); assert.equal(verified.confirmation.authenticationEvidenceSha256, sha(evidence));
  const stored = await storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now, suffix: "000000000211" });
  const consumption = await readWorkCodexAuthenticationConsumption({ root: value.root, evidenceId: evidence.evidenceId });
  assert.deepEqual(validateWorkCodexAuthenticationConsumption(consumption), []); assert.equal(consumption.authorizationId, stored.authorizationId); assert.equal(consumption.credentialsStored, false);
  const authorization = JSON.parse(await readFile(join(value.root, "authorizations", `${stored.authorizationId}.json`), "utf8"));
  assert.deepEqual(validateWorkCodexAuthorization(authorization), []); assert.equal(authorization.confirmation.authenticationEvidenceSha256, sha(evidence));
  const persisted = `${await readFile(join(value.root, "authentication", `${evidence.evidenceId}.json`), "utf8")}\n${canonical(authorization)}`;
  for (const forbidden of [evidence.signature, evidence.challengeNonce, '"signature":', '"authenticationAgeSeconds":']) assert.equal(persisted.includes(forbidden), false, forbidden);
});

test("email one-time-code evidence is supported without exposing or storing the code", async (t) => {
  const value = await setup(t, 2), evidence = evidenceFor(value, { secondFactor: "email-one-time-code", suffix: "000000000112" });
  const stored = await storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now: new Date(baseTime + 3000), suffix: "000000000212" });
  const consumption = await readWorkCodexAuthenticationConsumption({ root: value.root, evidenceId: evidence.evidenceId });
  assert.equal(consumption.secondFactor, "email-one-time-code"); assert.equal(consumption.authorizationId, stored.authorizationId);
});

test("only externally signed MFA authorization can enter the isolated provider execution ledger", async (t) => {
  const value = await setup(t, 7), evidence = evidenceFor(value, { suffix: "000000000118" }), now = new Date(baseTime + 3000);
  const stored = await storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now, suffix: "000000000218" });
  const output = { $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review", summary: "The sanitized fixture needs one explicit invariant.", findings: [], proposals: [], verificationSuggestions: ["Run the local test."], unresolvedRisks: ["Remote advice remains unverified."], boundary: "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority." };
  const result = await executeWorkCodexAuthorization({ root: value.root, authorizationId: stored.authorizationId, adapter: { source: "codex-cli-isolated", async run() { return { outputText: canonical(output), usage: { inputTokens: 200, outputTokens: 50 }, externalState: "invoked-once" }; } }, allowIsolatedCodex: true, now: new Date(baseTime + 4000), resultNow: new Date(baseTime + 5000), claimSuffix: "000000000219", resultSuffix: "000000000220" });
  assert.equal(result.receipt.status, "succeeded"); assert.equal(result.receipt.invocation.source, "codex-cli"); assert.equal(result.receipt.invocation.externalState, "invoked-once");
  const claim = JSON.parse(await readFile(join(value.root, "claims", stored.authorizationId, "claim.json"), "utf8")); assert.equal(claim.executionSource, "codex-cli"); assert.equal(claim.externalProviderTurnAuthorized, true);
});

test("isolated execution waits for abort cleanup before recording an uncertain timeout", async (t) => {
  const value = await setup(t, 8), evidence = evidenceFor(value, { suffix: "000000000119" }), now = new Date(baseTime + 3000);
  const stored = await storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now, suffix: "000000000221" });
  let aborted = false;
  const started = Date.now(), result = await executeWorkCodexAuthorization({
    root: value.root, authorizationId: stored.authorizationId, allowIsolatedCodex: true, adapterTimeoutMilliseconds: 25,
    adapter: { source: "codex-cli-isolated", async run(_payload, { signal }) { await new Promise((resolvePromise) => signal.addEventListener("abort", () => setTimeout(() => { aborted = true; resolvePromise(); }, 120), { once: true })); throw new Error("fixture stopped after cleanup"); } },
    now: new Date(baseTime + 4000), resultNow: new Date(baseTime + 5000), claimSuffix: "000000000222", resultSuffix: "000000000223",
  });
  assert.equal(aborted, true); assert.ok(Date.now() - started >= 120); assert.equal(result.receipt.status, "provider-failed"); assert.equal(result.receipt.invocation.externalState, "uncertain");
});

test("separately billed API mode requires its distinct signed billing acknowledgment", async (t) => {
  const value = await setup(t, 6, { api: true }), evidence = evidenceFor(value, { suffix: "000000000117" });
  assert.equal(evidence.provider.authMode, "api-key"); assert.equal(evidence.confirmation.billingAcknowledgment, "separately-billed-api-platform");
  const stored = await storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now: new Date(baseTime + 3000), suffix: "000000000217" });
  const authorization = JSON.parse(await readFile(join(value.root, "authorizations", `${stored.authorizationId}.json`), "utf8"));
  assert.equal(authorization.provider.billingBoundary, "separately-billed-api-platform"); assert.equal(authorization.confirmation.billingAcknowledgment, "separately-billed-api-platform");
});

test("one signed MFA event has one durable winner under concurrency and cannot replay", async (t) => {
  const value = await setup(t, 3), evidence = evidenceFor(value, { suffix: "000000000113" }), now = new Date(baseTime + 3000);
  const attempts = await Promise.allSettled([
    storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now, suffix: "000000000213" }),
    storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now, suffix: "000000000214" }),
  ]);
  assert.equal(attempts.filter((entry) => entry.status === "fulfilled").length, 1); assert.equal(attempts.filter((entry) => entry.status === "rejected").length, 1);
  assert.equal((await readdir(join(value.root, "authentication"))).length, 1); assert.equal((await readdir(join(value.root, "authorizations"))).length, 1);
  await assert.rejects(() => storeVerifiedWorkCodexAuthorization({ root: value.root, authenticationEvidence: evidence, trustedPublicKey: publicKey, now: new Date(baseTime + 4000), suffix: "000000000215" }), /created once|could not be created/u);
});

test("signature, key, issuer, factor, plan, billing, challenge, time, and credential-field tampering fail closed", async (t) => {
  const value = await setup(t, 4), original = evidenceFor(value, { suffix: "000000000114" }), now = new Date(baseTime + 3000);
  const otherKeys = generateKeyPairSync("ed25519"), cases = [
    [(evidence) => { evidence.signature = `${evidence.signature.slice(0, -1)}${evidence.signature.endsWith("A") ? "B" : "A"}`; }, publicKey],
    [() => {}, otherKeys.publicKey],
    [(evidence) => { evidence.issuer = "wrong-issuer"; }, publicKey],
    [(evidence) => { evidence.authentication.secondFactor = "sms-one-time-code"; }, publicKey],
    [(evidence) => { evidence.planSha256 = "f".repeat(64); }, publicKey],
    [(evidence) => { evidence.provider.model = "different-model"; }, publicKey],
    [(evidence) => { evidence.confirmation.billingAcknowledgment = "separately-billed-api-platform"; }, publicKey],
    [(evidence) => { evidence.challengeNonce = "f".repeat(32); }, publicKey],
    [(evidence) => { evidence.expiresAt = new Date(baseTime + 20 * 60000).toISOString(); }, publicKey],
    [(evidence) => { evidence.authentication.password = "must-never-enter-pixel"; }, publicKey],
  ];
  for (const [index, [mutate, key]] of cases.entries()) {
    const evidence = structuredClone(original); mutate(evidence);
    assert.throws(() => verifyWorkCodexAuthenticationEvidence({ evidence, plan: value.plan, policy: value.policy, trustedPublicKey: key, now }), WorkCodexAuthenticationError, `case ${index}`);
  }
  const future = evidenceFor(value, { issuedAt: new Date(baseTime + 5000), expiresAt: new Date(baseTime + 6000), suffix: "000000000115" });
  assert.throws(() => verifyWorkCodexAuthenticationEvidence({ evidence: future, plan: value.plan, policy: value.policy, trustedPublicKey: publicKey, now }), /future-dated/u);
  assert.throws(() => verifyWorkCodexAuthenticationEvidence({ evidence: original, plan: value.plan, policy: value.policy, trustedPublicKey: privateKey, now }), /only a public key/u);
});

test("512 signed-field mutations cannot retain a valid exact-plan assertion", async (t) => {
  const value = await setup(t, 5), original = evidenceFor(value, { suffix: "000000000116" }), now = new Date(baseTime + 3000);
  for (let index = 0; index < 512; index += 1) {
    const evidence = structuredClone(original);
    if (index % 4 === 0) evidence.evidenceId = `workcodexauthn-${baseTime + 2000}-${(index + 1000).toString(16).padStart(12, "0")}`;
    if (index % 4 === 1) evidence.authentication.authenticationAgeSeconds = (index % 250) + 40;
    if (index % 4 === 2) evidence.challengeNonce = sha(`mutated-nonce-${index}`).slice(0, 32);
    if (index % 4 === 3) evidence.confirmation.typedChallengeSha256 = sha(`mutated-challenge-${index}`);
    assert.throws(() => verifyWorkCodexAuthenticationEvidence({ evidence, plan: value.plan, policy: value.policy, trustedPublicKey: publicKey, now }), /signature|challenge/u, `mutation ${index}`);
  }
});

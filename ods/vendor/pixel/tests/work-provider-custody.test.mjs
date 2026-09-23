import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  inspectWorkProviderCredentialCustody, readWorkProviderCredential, WorkProviderCredentialCustodyError, workProviderCredentialCustodyInternals,
} from "../deploy/work-provider/credential-custody.mjs";
import { bindWorkProviderPrivatePolicy, WorkProviderPrivatePolicyError } from "../deploy/work-provider/private-policy.mjs";
import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import { validateWorkProviderCustodyReceipt, validateWorkProviderPrivatePolicy } from "../scripts/lib/work-contract.mjs";

const boundary = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";

function policy(enabled = false) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
    policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z",
    providerId: "moonshot-kimi", enabled,
    credentialCustody: { credentialId: "moonshot-test", fileName: "provider-key", maxBytes: 8192 },
    transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
    budgets: { maxRequestsPerRun: 4, maxInputTokensPerRun: 50000, maxOutputTokensPerRun: 10000, maxNetworkBytesPerRun: 16777216, maxRequestSeconds: 120, maxEstimatedCostMicrosPerRun: 5000000, maxEstimatedCostMicrosPerDay: 20000000 },
    fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true },
    verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
    authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false }, boundary,
  };
}

test("private provider policy binds one exact profile without budget or host widening", () => {
  const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
  assert.deepEqual(validateWorkProviderPrivatePolicy(policy()), []);
  assert.equal(bindWorkProviderPrivatePolicy(kimi, policy()).providerId, "moonshot-kimi");
  const hostWidened = policy(); hostWidened.transport.allowedHosts.push("example.com:443");
  assert.throws(() => bindWorkProviderPrivatePolicy(kimi, hostWidened), WorkProviderPrivatePolicyError);
  const budgetWidened = policy(); budgetWidened.budgets.maxRequestsPerRun = 81;
  assert.throws(() => bindWorkProviderPrivatePolicy(kimi, budgetWidened), /widens/u);
});

test("credential custody returns an unforgeable handle and content-free evidence", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-custody-"));
  const directory = join(root, "moonshot"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "test-key-abcdefghijklmnopqrstuvwxyz\n", { mode: 0o600 });
  await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  try {
    const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
    const paused = await inspectWorkProviderCredentialCustody({ resolvedProvider: kimi, policy: policy(false), credentialPath, now: new Date("2026-08-21T16:01:00Z"), suffix: "abcdef123456" });
    assert.deepEqual(validateWorkProviderCustodyReceipt(paused.receipt), []);
    assert.equal(paused.receipt.credentialsProjected, false);
    assert.equal(JSON.stringify(paused.receipt).includes("test-key"), false);
    assert.equal(JSON.stringify(paused.receipt).includes(credentialPath), false);
    await assert.rejects(readWorkProviderCredential({ resolvedProvider: kimi, policy: policy(false), handle: paused.handle }), WorkProviderPrivatePolicyError);

    const enabledPolicy = policy(true);
    const active = await inspectWorkProviderCredentialCustody({ resolvedProvider: kimi, policy: enabledPolicy, credentialPath, now: new Date("2026-08-21T16:02:00Z"), suffix: "123456abcdef" });
    const key = await readWorkProviderCredential({ resolvedProvider: kimi, policy: enabledPolicy, handle: active.handle });
    assert.equal(key.toString("ascii"), "test-key-abcdefghijklmnopqrstuvwxyz"); key.fill(0);
    await assert.rejects(readWorkProviderCredential({ resolvedProvider: kimi, policy: enabledPolicy, handle: Object.freeze({}) }), WorkProviderCredentialCustodyError);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("credential changes after inspection fail closed", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-custody-change-"));
  const directory = join(root, "moonshot"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  try {
    const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] }); const activePolicy = policy(true);
    const custody = await inspectWorkProviderCredentialCustody({ resolvedProvider: kimi, policy: activePolicy, credentialPath, now: new Date("2026-08-21T16:03:00Z"), suffix: "abcdefabcdef" });
    await writeFile(credentialPath, "test-key-changed-abcdefghijklmnop", { mode: 0o600 });
    await assert.rejects(readWorkProviderCredential({ resolvedProvider: kimi, policy: activePolicy, handle: custody.handle }), /changed after custody inspection/u);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("credential binding mismatch zeroes the newly read bytes before rejection", () => {
  const value = Buffer.from("temporary-provider-credential-fixture", "ascii");
  const observed = { value, binding: { dev: 1, ino: 2, size: value.length, mtimeMs: 3, ctimeMs: 4 } };
  assert.throws(() => workProviderCredentialCustodyInternals.takeCredentialAfterBindingCheck(observed, { ...observed.binding, mtimeMs: 5 }), /changed after custody inspection/u);
  assert.equal(value.every((byte) => byte === 0), true);
});

test("provider-specific credential filename binds end-to-end and rejects a foreign provider filename", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-custody-specific-"));
  const directory = join(root, "moonshot"); const credentialPath = join(directory, "moonshot-kimi-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "test-key-abcdefghijklmnopqrstuvwxyz\n", { mode: 0o600 });
  await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  try {
    const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
    const specific = policy(true);
    specific.credentialCustody.fileName = "moonshot-kimi-key";
    assert.deepEqual(validateWorkProviderPrivatePolicy(specific), []);
    const custody = await inspectWorkProviderCredentialCustody({ resolvedProvider: kimi, policy: specific, credentialPath, now: new Date("2026-08-21T16:04:00Z"), suffix: "abcabcabcabc" });
    const key = await readWorkProviderCredential({ resolvedProvider: kimi, policy: specific, handle: custody.handle });
    assert.equal(key.toString("ascii"), "test-key-abcdefghijklmnopqrstuvwxyz"); key.fill(0);
  } finally { await rm(root, { recursive: true, force: true }); }

  const foreign = policy(true);
  foreign.providerId = "anthropic";
  foreign.credentialCustody.fileName = "openai-key";
  const errors = validateWorkProviderPrivatePolicy(foreign);
  assert.ok(errors.some((error) => /fileName/u.test(error)), `expected a fileName schema error, got ${errors.join("; ")}`);
  const anthropic = resolveWorkProvider("anthropic", { enabledRemoteProviders: ["anthropic"] });
  assert.throws(() => bindWorkProviderPrivatePolicy(anthropic, foreign), WorkProviderPrivatePolicyError);
});

test("legacy provider-key filename remains the deliberate compatibility path", () => {
  const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
  assert.deepEqual(validateWorkProviderPrivatePolicy(policy()), []);
  assert.equal(bindWorkProviderPrivatePolicy(kimi, policy()).credentialCustody.fileName, "provider-key");
});

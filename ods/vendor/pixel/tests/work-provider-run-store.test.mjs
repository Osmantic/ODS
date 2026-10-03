import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import { claimWorkProviderRequest, createConnectivitySmokeBinding, createWorkProviderRunLedger, providerIdempotencyKey } from "../deploy/work-provider/run-ledger.mjs";
import { initializeWorkProviderRunStore, readWorkProviderRunLedger, replaceWorkProviderRunLedger, storeNewWorkProviderRunLedger, WorkProviderRunStoreError } from "../deploy/work-provider/run-store.mjs";

const boundary = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";
const policy = {
  $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
  policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: "moonshot-kimi", enabled: true,
  credentialCustody: { credentialId: "moonshot-test", fileName: "provider-key", maxBytes: 8192 },
  transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
  dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
  budgets: { maxRequestsPerRun: 4, maxInputTokensPerRun: 50000, maxOutputTokensPerRun: 10000, maxNetworkBytesPerRun: 16777216, maxRequestSeconds: 120, maxEstimatedCostMicrosPerRun: 5000000, maxEstimatedCostMicrosPerDay: 20000000 },
  fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true }, verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
  authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false }, boundary,
};
const provider = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });

test("provider run store uses exclusive creation and optimistic atomic replacement", async () => {
  const parent = await mkdtemp(join(tmpdir(), "pixel-provider-store-")); const root = join(parent, "store");
  try {
    await mkdir(root, { mode: 0o700 });
    await initializeWorkProviderRunStore({ root }); await chmod(root, 0o700);
    const binding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: "e".repeat(64), model: "kimi-k3" });
    const initial = createWorkProviderRunLedger({ resolvedProvider: provider, policy, taskId: "pixel-k3-store", model: "kimi-k3", binding, now: new Date("2026-08-21T16:20:00Z"), suffix: "abcdef123456" });
    const stored = await storeNewWorkProviderRunLedger({ root, ledger: initial });
    await assert.rejects(storeNewWorkProviderRunLedger({ root, ledger: initial }), WorkProviderRunStoreError);
    const observed = await readWorkProviderRunLedger({ root, runId: initial.runId }); assert.equal(observed.sha256, stored.sha256);
    const next = claimWorkProviderRequest({ ledger: observed.ledger, policy, resolvedProvider: provider, idempotencyKey: providerIdempotencyKey({ turn: 1 }), inputSha256: "a".repeat(64), estimatedInputTokens: 10, maxOutputTokens: 10, maxEstimatedCostMicros: 10, now: new Date("2026-08-21T16:20:01Z") });
    const replaced = await replaceWorkProviderRunLedger({ root, expectedSha256: observed.sha256, ledger: next });
    assert.equal((await readWorkProviderRunLedger({ root, runId: initial.runId })).sha256, replaced.sha256);
    await assert.rejects(replaceWorkProviderRunLedger({ root, expectedSha256: observed.sha256, ledger: next }), /changed before atomic replacement/u);
  } finally { await rm(parent, { recursive: true, force: true }); }
});

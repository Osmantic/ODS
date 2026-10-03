import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runMoonshotContainerEntrypoint } from "../deploy/work-provider/moonshot-container-entrypoint.mjs";
import { runMoonshotSmoke } from "../deploy/work-provider/moonshot-smoke-cli.mjs";
import { MoonshotWorkProviderTransportError } from "../deploy/work-provider/moonshot-transport.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const boundary = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";
const policy = {
  $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
  policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: "moonshot-kimi", enabled: true,
  credentialCustody: { credentialId: "moonshot-test", fileName: "moonshot-kimi-key", maxBytes: 8192 },
  transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
  dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
  budgets: { maxRequestsPerRun: 2, maxInputTokensPerRun: 10000, maxOutputTokensPerRun: 512, maxNetworkBytesPerRun: 2097152, maxRequestSeconds: 60, maxEstimatedCostMicrosPerRun: 1000000, maxEstimatedCostMicrosPerDay: 1000000 },
  fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true }, verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
  authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false }, boundary,
};

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-moonshot-smoke-"));
  const credentialDirectory = join(root, "credential"), credentialPath = join(credentialDirectory, "moonshot-kimi-key"), policyPath = join(root, "policy.json"), ledgerRoot = join(root, "ledger");
  await mkdir(credentialDirectory, { mode: 0o700 });
  await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  const text = canonical(policy); await writeFile(policyPath, `${text}\n`, { mode: 0o600 });
  await chmod(root, 0o700); await chmod(credentialDirectory, 0o700); await chmod(credentialPath, 0o600); await chmod(policyPath, 0o600);
  const sha256 = createHash("sha256").update(text).digest("hex");
  const argv = ["--policy", policyPath, "--sha256", sha256, "--credential", credentialPath, "--ledger-root", ledgerRoot];
  return { root, ledgerRoot, argv };
}

test("Moonshot smoke persists one exact successful claim without provider content", async () => {
  const value = await fixture();
  try {
    const receipt = await runMoonshotSmoke(value.argv, { execute: async () => ({
      assistant: { message: { role: "assistant", content: "PIXEL_K3_SMOKE_OK" }, finishReason: "stop", usage: { inputTokens: 21, outputTokens: 8, totalTokens: 29 } },
      providerRequestId: "moonshot-smoke-fixture", networkBytes: 100,
    }) });
    assert.equal(receipt.status, "passed"); assert.equal(receipt.inputTokens, 21); assert.equal(receipt.outputTokens, 8);
    assert.equal(receipt.providerContentIncluded, false); assert.equal(receipt.credentialIncluded, false);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed"); assert.equal(ledger.requests[0].state, "succeeded");
    assert.equal(ledger.requests[0].responseDisclosed, false); assert.equal(JSON.stringify(ledger).includes("PIXEL_K3_SMOKE_OK"), false);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Moonshot smoke makes an ambiguous invocation non-retryable", async () => {
  const value = await fixture();
  try {
    await assert.rejects(runMoonshotSmoke(value.argv, { execute: async () => { throw new Error("ambiguous transport"); } }), /uncertain/u);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "uncertain"); assert.equal(ledger.requests[0].state, "uncertain"); assert.equal(ledger.requests[0].attempts, 1);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Moonshot smoke records a known provider rejection as terminal without an uncertain retry block", async () => {
  const value = await fixture();
  try {
    await assert.rejects(runMoonshotSmoke(value.argv, { execute: async () => { throw new MoonshotWorkProviderTransportError("HTTP fixture", "failed-known"); } }), /known terminal/u);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed"); assert.equal(ledger.requests[0].state, "failed-known");
    assert.equal(ledger.requests[0].attempts, 1); assert.equal(ledger.requests[0].providerContentStored, false);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("Moonshot container entrypoint accepts only a policy hash and fixed secret-file mounts", async () => {
  const oldUmask = process.umask(); let observed;
  try {
    for (const credentialFile of ["moonshot-kimi-key", "provider-key"]) {
      const receipt = await runMoonshotContainerEntrypoint({
        environment: { PIXEL_WORK_PROVIDER_POLICY_SHA256: "a".repeat(64), PIXEL_WORK_PROVIDER_CREDENTIAL_FILE: credentialFile, PATH: "/opt/node/bin" },
        run: async (argv, options) => { observed = { argv, options }; return { status: "passed" }; },
      });
      assert.equal(receipt.status, "passed");
      assert.deepEqual(observed.options, { proxyHost: "pixel-provider-egress" });
      assert.deepEqual(observed.argv, [
        "--policy", "/run/pixel-policy/policy.json", "--sha256", "a".repeat(64),
        "--credential", `/run/pixel-credential/${credentialFile}`, "--ledger-root", "/state/ledger",
      ]);
    }
    await assert.rejects(runMoonshotContainerEntrypoint({
      environment: { PIXEL_WORK_PROVIDER_POLICY_SHA256: "a".repeat(64), PIXEL_WORK_PROVIDER_CREDENTIAL_FILE: "openai-key" }, run: async () => ({}),
    }), /filename is invalid/u);
    await assert.rejects(runMoonshotContainerEntrypoint({
      environment: { PIXEL_WORK_PROVIDER_POLICY_SHA256: "a".repeat(64), PIXEL_WORK_PROVIDER_CREDENTIAL_FILE: "moonshot-kimi-key", MOONSHOT_API_KEY: "forbidden" }, run: async () => ({}),
    }), /ambient credential/u);
    await assert.rejects(runMoonshotContainerEntrypoint({ argv: ["--widen"], environment: { PIXEL_WORK_PROVIDER_POLICY_SHA256: "a".repeat(64) }, run: async () => ({}) }), /no arguments/u);
  } finally { process.umask(oldUmask); }
});

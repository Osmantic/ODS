import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileCapabilityOperationalV2SshHostname } from "../deploy/work-controller/capability-controller.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { signCapabilitySshApprovalV2 } from "../deploy/work-controller/capability-ssh-approval-v2.mjs";
import { executeOperationalV2, recoverOperationalV2 } from "../deploy/work-controller/capability-runtime-operational-v2.mjs";
import {
  canonical, validateWorkCapabilityOperationalGrantV2,
  validateWorkCapabilityOperationalRuntimeRequestV2,
  validateWorkCapabilityOperationalRuntimeResultV2, validateWorkCapabilityPackV2,
} from "../scripts/lib/work-contract.mjs";
import { custody, sha, v2RetrievalPack } from "./test-fixtures.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const runtimeClock = () => new Date("2026-08-10T13:03:01.000Z");
const credentialRef = "tower-one-hostname-key";
const destinationAlias = "tower-one";

const sshBinding = Object.freeze({
  targetClass: "ssh",
  scope: { mode: "private-allowlist", endpoints: [], destinations: [destinationAlias] },
  egress: { mode: "private-allowlist", destinations: [destinationAlias] },
  credentials: { refs: [credentialRef] },
  idempotency: { required: true, keys: ["requestId"] },
  approval: { required: true, mode: "operator-approval" },
  inputClassification: "internal", outputClassification: "internal",
  budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 } },
  receipt: { required: true, form: "content-free" },
  neverEgress: ["requestId"],
});

function sshPack() {
  const base = v2RetrievalPack();
  const inputSchema = {
    type: "object", additionalProperties: false,
    required: ["destinationAlias", "commandId", "requestId"],
    properties: {
      destinationAlias: { const: destinationAlias }, commandId: { const: "hostname" },
      requestId: { type: "string" },
    },
  };
  const outputSchema = { type: "object", additionalProperties: false, required: ["hostname"], properties: { hostname: { type: "string" } } };
  return {
    ...base, id: "fixture-ssh", name: "Fixture SSH hostname", description: "One owner-signed forced-command hostname proof.",
    adapter: { ...base.adapter, serverName: "fixture-ssh", workspace: "none" },
    tools: [{
      name: "ssh_hostname", title: "SSH hostname proof", description: "Connect to one trusted alias whose key forces hostname.",
      effectClass: "read-only", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema),
      binding: structuredClone(sshBinding),
    }],
    data: { acceptedClassifications: ["internal"], returnedClassifications: ["internal"], rawRetention: "job-only" },
    limits: { ...base.limits, maxInputBytes: 4096, maxOutputBytes: 4096, maxRuntimeMs: 5000, maxWorkspaceBytes: 0 },
    security: { ...base.security, networkMode: "private-allowlist", networkDestinations: [destinationAlias], credentialRefs: [credentialRef] },
    budgets: { perRun: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 4096, maxDurationMs: 5000 } },
  };
}

function approval(overrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-ssh-approval-v2.schema.json", schemaVersion: 2,
    operation: "pixel-work-capability-ssh-approval-v2", requestId: "workcaprequest-1786366950000-abcdef123456",
    destinationAlias, commandId: "hostname", credentialRef,
    issuedAt: "2026-08-10T13:02:30.000Z", expiresAt: "2026-08-10T13:07:30.000Z",
    nonce: "d".repeat(64), maxOutputBytes: 4096, timeoutMs: 5000,
    authority: { grantsOneSshConnection: true, grantsGenericShell: false, grantsCommandSelection: false, grantsDestinationSelection: false, grantsCredentialDisclosure: false, grantsReplay: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Fresh owner signature for one exact single-use SSH forced-command request. It grants one connection to a trusted destination alias using a separately custodied credential and grants no generic shell, command or destination selection, credential disclosure, replay, external effect, scope expansion, or completion.",
    ...overrides,
  };
}

function controllerFixture(pack) {
  const value = custody(pack, { dataClassification: "internal" });
  const packSha256 = capabilityPackSha256(pack);
  const policy = {
    ...value.policy,
    packs: [{
      id: pack.id, version: pack.version, packSha256, treeSha256: pack.provenance.treeSha256, schemaVersion: 2,
      tools: [{ name: "ssh_hostname", effectClass: "read-only", bindingSummary: { targetClass: "ssh", inputClassification: "internal", outputClassification: "internal" } }],
      classifications: ["internal"],
    }],
    limits: { ...value.policy.limits, maxGrantLifetimeMs: 60000, maxInputBytes: 4096, maxOutputBytes: 4096, maxRuntimeMs: 5000, maxWorkspaceBytes: 0 },
  };
  return { ...value, pack, policy, expectedPackSha256: packSha256 };
}

async function runtimeFixture(t, output = "tower-one\n") {
  const root = await mkdtemp(join(tmpdir(), "pixel-operational-ssh-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const signingKey = join(root, "owner"), allowedSignersPath = join(root, "allowed_signers");
  const stateRoot = join(root, "state"), identityFile = join(root, "ssh-identity"), knownHostsFile = join(root, "known_hosts"), sshBinary = join(root, "ssh");
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", signingKey], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr);
  if (process.platform !== "win32") await chmod(signingKey, 0o600);
  await writeFile(allowedSignersPath, `pixel-owner ${(await readFile(`${signingKey}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await writeFile(identityFile, "fixture credential\n", { mode: 0o600 });
  await writeFile(knownHostsFile, "[192.0.2.10]:22 ssh-ed25519 AAAAFIXTURE\n", { mode: 0o600 });
  await writeFile(sshBinary, `#!/bin/sh\nprintf '%s' '${output.replace(/'/gu, "'\\''")}'\n`, { mode: 0o700 });
  await mkdir(stateRoot, { mode: 0o700 });
  const config = {
    stateRoot, clock: runtimeClock,
    ssh: {
      sshBinary, allowedSignersPath, operatorIdentity: "pixel-owner",
      destinations: {
        [destinationAlias]: { enabled: true, host: "192.0.2.10", port: 22, user: "pixel", credentialRef, identityFile, knownHostsFile, commandId: "hostname", expectedHostname: "tower-one" },
      },
    },
  };
  return { root, signingKey, config };
}

async function compile(t, { output = "tower-one\n", approvalOverrides = {} } = {}) {
  const fixture = await runtimeFixture(t, output); if (!fixture) return null;
  const pack = sshPack(), ownerApproval = approval(approvalOverrides);
  const signature = await signCapabilitySshApprovalV2({ approval: ownerApproval, signingKeyPath: fixture.signingKey, clock: runtimeClock });
  const compiled = compileCapabilityOperationalV2SshHostname({
    ...controllerFixture(pack), tool: "ssh_hostname", approval: ownerApproval, approvalSignature: signature,
    now: new Date("2026-08-10T13:03:00.000Z"), grantSuffix: "abcdef123456", operationSuffix: "abcdef1234567890",
  });
  return { ...fixture, pack, ownerApproval, signature, ...compiled };
}

test("read-only v2 bindings are admitted only for the narrow SSH class", () => {
  const pack = sshPack();
  assert.deepEqual(validateWorkCapabilityPackV2(pack), []);
  const browser = structuredClone(pack); browser.tools[0].binding.targetClass = "browser";
  assert.ok(validateWorkCapabilityPackV2(browser).length > 0);
});

test("controller compiles an exact non-authoritative grant plus private owner approval request", async (t) => {
  const value = await compile(t); if (!value) return;
  assert.deepEqual(validateWorkCapabilityOperationalGrantV2(value.grant), []);
  assert.deepEqual(validateWorkCapabilityOperationalRuntimeRequestV2(value.runtimeRequest), []);
  assert.equal(value.grant.approvalMode, "owner-signed");
  assert.deepEqual(value.grant.credentialRefs, [credentialRef]);
  assert.equal(value.grant.authority.grantsCredentials, false);
  assert.equal(value.grant.authority.grantsNetwork, false);
  assert.equal(value.runtimeRequest.approval.destinationAlias, destinationAlias);
  assert.doesNotMatch(canonical(value.runtimeRequest), /192\.0\.2\.10|identityFile|knownHostsFile|\/usr\/bin\/hostname/u);
});

test("owner-signed SSH lane executes once, settles exact receipts, and recovers without replay", { skip: process.platform === "win32" }, async (t) => {
  const value = await compile(t); if (!value) return;
  const result = await executeOperationalV2({ grant: value.grant, runtimeRequest: value.runtimeRequest, config: value.config });
  assert.deepEqual(validateWorkCapabilityOperationalRuntimeResultV2(result), []);
  assert.equal(result.structuredContent.hostname, "tower-one");
  assert.equal(result.authority.grantsReplay, false);
  assert.equal((await recoverOperationalV2(value.config, value.grant.operation.id, value.grant)).status, "completed");
  await assert.rejects(() => executeOperationalV2({ grant: value.grant, runtimeRequest: value.runtimeRequest, config: value.config }), /already has custody|no blind replay/u);
});

test("unexpected remote output becomes uncertain-rejected and can never be replayed", { skip: process.platform === "win32" }, async (t) => {
  const value = await compile(t, { output: "wrong-host\n", approvalOverrides: { nonce: "e".repeat(64) } }); if (!value) return;
  await assert.rejects(() => executeOperationalV2({ grant: value.grant, runtimeRequest: value.runtimeRequest, config: value.config }), /unexpected hostname.*no replay|uncertain-rejected/u);
  const custodyNames = await readdir(join(value.config.stateRoot, "v2-runtime", "custody"));
  assert.deepEqual(custodyNames, [`${value.grant.operation.id}.uncertain-rejected.json`]);
  assert.equal((await recoverOperationalV2(value.config, value.grant.operation.id, value.grant)).status, "uncertain-rejected");
  await assert.rejects(() => executeOperationalV2({ grant: value.grant, runtimeRequest: value.runtimeRequest, config: value.config }), /already has custody|terminal|no blind replay/u);
});

test("approval drift fails before custody while signature failure after custody becomes terminal", { skip: process.platform === "win32" }, async (t) => {
  const drift = await compile(t, { approvalOverrides: { nonce: "f".repeat(64) } }); if (!drift) return;
  const driftedRequest = { ...drift.runtimeRequest, approval: { ...drift.runtimeRequest.approval, destinationAlias: "tower-two" } };
  await assert.rejects(() => executeOperationalV2({ grant: drift.grant, runtimeRequest: driftedRequest, config: drift.config }), /differs from its grant binding/u);
  await assert.rejects(() => readdir(join(drift.config.stateRoot, "v2-runtime", "custody")), /ENOENT/u);

  const badSignature = await compile(t, { approvalOverrides: { nonce: "1".repeat(64) } }); if (!badSignature) return;
  const signature = badSignature.runtimeRequest.approvalSignature.replace(/[A-Za-z]/u, (letter) => letter === "A" ? "B" : "A");
  await assert.rejects(() => executeOperationalV2({ grant: badSignature.grant, runtimeRequest: { ...badSignature.runtimeRequest, approvalSignature: signature }, config: badSignature.config }), /uncertain-rejected/u);
  assert.deepEqual(await readdir(join(badSignature.config.stateRoot, "v2-runtime", "custody")), [`${badSignature.grant.operation.id}.uncertain-rejected.json`]);
});

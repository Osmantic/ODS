import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  CapabilitySshApprovalV2Error, consumeCapabilitySshApprovalV2,
  signCapabilitySshApprovalV2, verifyCapabilitySshApprovalV2,
} from "../deploy/work-controller/capability-ssh-approval-v2.mjs";
import { validateWorkCapabilitySshApprovalV2 } from "../scripts/lib/work-contract.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const issuedAt = "2026-08-27T07:15:00.000Z";
const clock = () => new Date("2026-08-27T07:16:00.000Z");

function approval(overrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-ssh-approval-v2.schema.json",
    schemaVersion: 2,
    operation: "pixel-work-capability-ssh-approval-v2",
    requestId: "workcaprequest-1787814900000-abcdef123456",
    destinationAlias: "tower-one",
    commandId: "hostname",
    credentialRef: "tower-one-hostname-key",
    issuedAt,
    expiresAt: "2026-08-27T07:20:00.000Z",
    nonce: "a".repeat(64),
    maxOutputBytes: 4096,
    timeoutMs: 5000,
    authority: {
      grantsOneSshConnection: true, grantsGenericShell: false, grantsCommandSelection: false,
      grantsDestinationSelection: false, grantsCredentialDisclosure: false, grantsReplay: false,
      grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false,
    },
    boundary: "Fresh owner signature for one exact single-use SSH forced-command request. It grants one connection to a trusted destination alias using a separately custodied credential and grants no generic shell, command or destination selection, credential disclosure, replay, external effect, scope expansion, or completion.",
    ...overrides,
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-ssh-approval-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const key = join(root, "owner"), signers = join(root, "allowed_signers"), stateRoot = join(root, "state");
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", key], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr);
  if (process.platform !== "win32") await chmod(key, 0o600);
  await writeFile(signers, `pixel-owner ${(await readFile(`${key}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await mkdir(stateRoot, { mode: 0o700 });
  return { root, key, signers, stateRoot };
}

test("owner-signed SSH approval verifies and is consumed exactly once", async (t) => {
  const f = await fixture(t); if (!f) return;
  const value = approval();
  assert.deepEqual(validateWorkCapabilitySshApprovalV2(value), []);
  const signature = await signCapabilitySshApprovalV2({ approval: value, signingKeyPath: f.key, clock });
  const verified = await verifyCapabilitySshApprovalV2({ approval: value, signature, allowedSignersPath: f.signers, identity: "pixel-owner", clock });
  const consumed = await consumeCapabilitySshApprovalV2({
    stateRoot: f.stateRoot, verified, requestId: value.requestId, destinationAlias: value.destinationAlias,
    commandId: value.commandId, credentialRef: value.credentialRef,
  });
  assert.equal(consumed.record.nonce, value.nonce);
  await assert.rejects(() => consumeCapabilitySshApprovalV2({
    stateRoot: f.stateRoot, verified, requestId: value.requestId, destinationAlias: value.destinationAlias,
    commandId: value.commandId, credentialRef: value.credentialRef,
  }), /already consumed|no replay/u);
});

test("tampering, expiry, excessive lifetime, and request drift fail closed", async (t) => {
  const f = await fixture(t); if (!f) return;
  const value = approval();
  const signature = await signCapabilitySshApprovalV2({ approval: value, signingKeyPath: f.key, clock });
  await assert.rejects(() => verifyCapabilitySshApprovalV2({ approval: { ...value, destinationAlias: "tower-two" }, signature, allowedSignersPath: f.signers, identity: "pixel-owner", clock }), CapabilitySshApprovalV2Error);
  await assert.rejects(() => verifyCapabilitySshApprovalV2({ approval: { ...value, expiresAt: "2026-08-27T07:15:30.000Z" }, signature, allowedSignersPath: f.signers, identity: "pixel-owner", clock }), /not currently valid/u);
  await assert.rejects(() => signCapabilitySshApprovalV2({ approval: { ...value, expiresAt: "2026-08-27T07:30:00.000Z" }, signingKeyPath: f.key, clock }), /ten-minute ceiling/u);
  const verified = await verifyCapabilitySshApprovalV2({ approval: value, signature, allowedSignersPath: f.signers, identity: "pixel-owner", clock });
  await assert.rejects(() => consumeCapabilitySshApprovalV2({ stateRoot: f.stateRoot, verified, requestId: value.requestId, destinationAlias: "tower-two", commandId: "hostname", credentialRef: value.credentialRef }), /differs from the exact runtime request/u);
});

test("approval shape cannot carry literal host, user, command, or key paths", () => {
  for (const [field, payload] of Object.entries({
    host: "10.0.0.8", user: "owner", command: "/usr/bin/hostname", identityFile: "/run/credentials/key",
  })) {
    const value = approval({ [field]: payload });
    assert.ok(validateWorkCapabilitySshApprovalV2(value).length > 0, `${field} must be rejected`);
  }
});

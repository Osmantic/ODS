import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { signCapabilitySshApprovalV2 } from "../deploy/work-controller/capability-ssh-approval-v2.mjs";
import {
  buildCapabilitySshHostnameInvocationV2, CapabilitySshRuntimeV2Error,
  executeCapabilitySshHostnameV2,
} from "../deploy/work-controller/capability-ssh-runtime-v2.mjs";

const sshKeygen = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const clock = () => new Date("2026-08-27T07:16:00.000Z");

function approval(overrides = {}) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-ssh-approval-v2.schema.json", schemaVersion: 2,
    operation: "pixel-work-capability-ssh-approval-v2", requestId: "workcaprequest-1787814900000-abcdef123456",
    destinationAlias: "tower-one", commandId: "hostname", credentialRef: "tower-one-hostname-key",
    issuedAt: "2026-08-27T07:15:00.000Z", expiresAt: "2026-08-27T07:20:00.000Z", nonce: "b".repeat(64),
    maxOutputBytes: 4096, timeoutMs: 5000,
    authority: { grantsOneSshConnection: true, grantsGenericShell: false, grantsCommandSelection: false, grantsDestinationSelection: false, grantsCredentialDisclosure: false, grantsReplay: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Fresh owner signature for one exact single-use SSH forced-command request. It grants one connection to a trusted destination alias using a separately custodied credential and grants no generic shell, command or destination selection, credential disclosure, replay, external effect, scope expansion, or completion.",
    ...overrides,
  };
}

async function fixture(t, output = "tower-one\n") {
  const root = await mkdtemp(join(tmpdir(), "pixel-ssh-runtime-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const key = join(root, "owner"), signers = join(root, "allowed_signers"), stateRoot = join(root, "state");
  const identityFile = join(root, "ssh-identity"), knownHostsFile = join(root, "known_hosts"), sshBinary = join(root, "ssh");
  const generated = spawnSync(sshKeygen, ["-q", "-t", "ed25519", "-N", "", "-f", key], { encoding: "utf8", windowsHide: true });
  if (generated.error?.code === "ENOENT") return t.skip("ssh-keygen is unavailable");
  assert.equal(generated.status, 0, generated.stderr);
  if (process.platform !== "win32") await chmod(key, 0o600);
  await writeFile(signers, `pixel-owner ${(await readFile(`${key}.pub`, "utf8")).trim()}\n`, { mode: 0o600 });
  await writeFile(identityFile, "fixture private credential\n", { mode: 0o600 });
  await writeFile(knownHostsFile, "[192.0.2.10]:22 ssh-ed25519 AAAAFIXTURE\n", { mode: 0o600 });
  await writeFile(sshBinary, `#!/bin/sh\nprintf '%s' '${output.replace(/'/gu, "'\\''")}'\n`, { mode: 0o700 });
  await mkdir(stateRoot, { mode: 0o700 });
  const config = {
    sshBinary, allowedSignersPath: signers, operatorIdentity: "pixel-owner", stateRoot, clock,
    destinations: {
      "tower-one": { enabled: true, host: "192.0.2.10", port: 22, user: "pixel", credentialRef: "tower-one-hostname-key", identityFile, knownHostsFile, commandId: "hostname", expectedHostname: "tower-one" },
    },
  };
  return { root, key, config };
}

test("invocation has no command text, no shell, and disables ambient SSH authority", async (t) => {
  const f = await fixture(t); if (!f) return;
  const value = buildCapabilitySshHostnameInvocationV2(f.config, approval());
  assert.equal(value.binary, f.config.sshBinary);
  assert.equal(value.args.includes("/usr/bin/hostname"), false);
  assert.equal(value.args.includes("bash"), false);
  assert.ok(value.args.includes("IdentityAgent=none"));
  assert.ok(value.args.includes("StrictHostKeyChecking=yes"));
  assert.ok(value.args.includes("ClearAllForwardings=yes"));
  assert.deepEqual(value.args.slice(-2), ["--", "pixel@192.0.2.10"]);
});

test("owner-signed forced-command hostname executes once with bounded verified output", { skip: process.platform === "win32" }, async (t) => {
  const f = await fixture(t); if (!f) return;
  const value = approval();
  const signature = await signCapabilitySshApprovalV2({ approval: value, signingKeyPath: f.key, clock });
  const result = await executeCapabilitySshHostnameV2({ config: f.config, approval: value, signature });
  assert.equal(result.status, "succeeded");
  assert.equal(result.hostname, "tower-one");
  assert.equal(result.authority.grantsReplay, false);
  await assert.rejects(() => executeCapabilitySshHostnameV2({ config: f.config, approval: value, signature }), /already consumed|no replay/u);
});

test("unexpected hostname spends the approval and cannot be replayed", { skip: process.platform === "win32" }, async (t) => {
  const f = await fixture(t, "wrong-host\n"); if (!f) return;
  const value = approval({ nonce: "c".repeat(64) });
  const signature = await signCapabilitySshApprovalV2({ approval: value, signingKeyPath: f.key, clock });
  await assert.rejects(() => executeCapabilitySshHostnameV2({ config: f.config, approval: value, signature }), /unexpected hostname.*no replay/u);
  await assert.rejects(() => executeCapabilitySshHostnameV2({ config: f.config, approval: value, signature }), /already consumed|no replay/u);
});

test("literal hostnames, credential drift, and executable substitution fail before execution", async (t) => {
  const f = await fixture(t); if (!f) return;
  assert.throws(() => buildCapabilitySshHostnameInvocationV2({ ...f.config, sshBinary: join(f.root, "not-ssh") }, approval()), CapabilitySshRuntimeV2Error);
  assert.throws(() => buildCapabilitySshHostnameInvocationV2(f.config, approval({ credentialRef: "other-key" })), /does not match the trusted destination/u);
  const names = { ...f.config, destinations: { "tower-one": { ...f.config.destinations["tower-one"], host: "tower-one.local" } } };
  assert.throws(() => buildCapabilitySshHostnameInvocationV2(names, approval()), /destination is disabled or invalid/u);
});

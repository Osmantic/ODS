import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  WorkMaintenanceRecoverySystemdError,
  hostRootIsUnmappedInCurrentNamespace,
  installedFragmentSha256,
  isTrustedOwner,
  parseUidMapRootState,
  resolveTrustedSystemctl,
  systemdShowUnit,
  validateFragmentPath,
} from "../deploy/work-controller/maintenance-recovery-guardian-systemd.mjs";

const linux = process.platform === "linux";
const uid = process.geteuid?.() ?? 1000;

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-guardian-systemd-facts-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  return root;
}

// A trusted parent chain (every ancestor real, trusted-owned, and free of
// group/world write) up to the filesystem root. /tmp is world-writable, so the
// trusted-executable positive cases must live under the owner's real home.
async function trustedFixture(t) {
  const root = await mkdtemp(join(homedir(), ".pixel-guardian-systemd-facts-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  return root;
}

test("fragment path policy accepts exactly the trusted user dir/unit and rejects sibling/nested/alternate-unit paths", { skip: !linux ? "fragment path policy is POSIX" : false }, async (t) => {
  const root = await fixture(t);
  const userDir = join(root, "config/systemd/user");
  const unit = "pixel-demo.service";
  const exact = join(userDir, unit);
  assert.equal(validateFragmentPath(exact, uid, unit, { userSystemdDir: userDir }), exact);
  assert.throws(() => validateFragmentPath(join(userDir, "other.service"), uid, unit, { userSystemdDir: userDir }), WorkMaintenanceRecoverySystemdError);
  assert.throws(() => validateFragmentPath(join(userDir, "sub", unit), uid, unit, { userSystemdDir: userDir }), WorkMaintenanceRecoverySystemdError);
  assert.throws(() => validateFragmentPath(join(userDir, "pixel-demo-2.service"), uid, unit, { userSystemdDir: userDir }), WorkMaintenanceRecoverySystemdError);
  assert.throws(() => validateFragmentPath(`${exact}-extra`, uid, unit, { userSystemdDir: userDir }), WorkMaintenanceRecoverySystemdError);
});

test("fragment path policy accepts exactly one standard system unit directory plus the unit", { skip: !linux ? "fragment path policy is POSIX" : false }, async (t) => {
  const unit = "pixel-demo.service";
  const exact = join("/etc/systemd/system", unit);
  assert.equal(validateFragmentPath(exact, uid, unit), exact);
  assert.throws(() => validateFragmentPath(join("/etc/systemd/system", "other.service"), uid, unit), WorkMaintenanceRecoverySystemdError);
  assert.throws(() => validateFragmentPath(join("/opt/units", unit), uid, unit), WorkMaintenanceRecoverySystemdError);
  assert.throws(() => validateFragmentPath(join("/etc/systemd/system", "sub", unit), uid, unit), WorkMaintenanceRecoverySystemdError);
});

test("installedFragmentSha256 reads a singular real owner-private fragment and rejects group/world-writable fragments", { skip: !linux ? "POSIX secure read" : false }, async (t) => {
  const root = await fixture(t);
  const userDir = join(root, "config/systemd/user");
  await mkdir(userDir, { recursive: true });
  await chmod(userDir, 0o700);
  const unit = "pixel-demo.service";
  const path = join(userDir, unit);
  const content = "[Unit]\nDescription=test\n";
  await writeFile(path, content, { mode: 0o600 });
  await chmod(path, 0o600);
  const expected = createHash("sha256").update(content).digest("hex");
  assert.equal(await installedFragmentSha256(path, uid, unit, { userSystemdDir: userDir }), expected);
  await chmod(path, 0o602);
  await assert.rejects(installedFragmentSha256(path, uid, unit, { userSystemdDir: userDir }), /group or world writable/u);
  await chmod(path, 0o620);
  await assert.rejects(installedFragmentSha256(path, uid, unit, { userSystemdDir: userDir }), /group or world writable/u);
});

test("installedFragmentSha256 fails closed on a substituted symlink fragment", { skip: !linux ? "POSIX secure read" : false }, async (t) => {
  const root = await fixture(t);
  const userDir = join(root, "config/systemd/user");
  await mkdir(userDir, { recursive: true });
  await chmod(userDir, 0o700);
  const unit = "pixel-demo.service";
  const realPath = join(root, "elsewhere.service");
  await writeFile(realPath, "REAL", { mode: 0o600 });
  const link = join(userDir, unit);
  await symlink(realPath, link);
  await assert.rejects(installedFragmentSha256(link, uid, unit, { userSystemdDir: userDir }), /real path differs|not a singular|opened safely/u);
});

test("the overflow root uid 65534 is rejected as a trusted owner by default and only trusted under an explicit sandbox-root proof", { skip: !linux ? "POSIX ownership policy" : false }, () => {
  assert.equal(isTrustedOwner(65534, uid), false, "uid 65534 must be rejected by default");
  assert.equal(isTrustedOwner(65534, uid, false), false, "uid 65534 must be rejected unless the sandbox-root proof is granted");
  assert.equal(isTrustedOwner(0, uid, false), true, "uid 0 is always a trusted owner");
  assert.equal(isTrustedOwner(uid, uid, false), true, "the exact expected caller is always a trusted owner");
  assert.equal(isTrustedOwner(12345, uid, false), false, "any other owner is untrusted");
  assert.equal(isTrustedOwner(65534, uid, true), true, "uid 65534 is accepted only when the sandbox-root proof is granted");
});

test("uid-map parser: a syntactically valid positive-length row that maps host root fails closed as root-mapped", async () => {
  assert.deepEqual(parseUidMapRootState("0 0 4294967295\n"), { sawValidMapping: true, rootMapped: true });
  assert.deepEqual(parseUidMapRootState("1000 0 1\n"), { sawValidMapping: true, rootMapped: true });
  assert.equal(await hostRootIsUnmappedInCurrentNamespace({ uidMapReader: async () => "0 0 1\n" }), false);
});

test("uid-map parser: a valid positive-length row that does not map host root proves root unmapped", async () => {
  assert.deepEqual(parseUidMapRootState("0 1000 1\n"), { sawValidMapping: true, rootMapped: false });
  assert.equal(await hostRootIsUnmappedInCurrentNamespace({ uidMapReader: async () => "0 1000 1\n" }), true);
});

test("uid-map parser fails closed on empty, malformed, and zero-length rows (no valid mapping)", () => {
  assert.deepEqual(parseUidMapRootState(""), { sawValidMapping: false, rootMapped: false });
  assert.deepEqual(parseUidMapRootState("\n\n"), { sawValidMapping: false, rootMapped: false });
  assert.deepEqual(parseUidMapRootState("not-a-row"), { sawValidMapping: false, rootMapped: false });
  assert.deepEqual(parseUidMapRootState("0 0 0\n"), { sawValidMapping: false, rootMapped: false }, "a zero-length row is not a valid mapping");
  assert.deepEqual(parseUidMapRootState("0 0\n"), { sawValidMapping: false, rootMapped: false }, "a two-field row is malformed");
  assert.deepEqual(parseUidMapRootState("a b c\n"), { sawValidMapping: false, rootMapped: false }, "non-numeric fields are malformed");
  assert.deepEqual(parseUidMapRootState("0 -1 1\n"), { sawValidMapping: false, rootMapped: false }, "a negative host start is malformed");
  assert.deepEqual(parseUidMapRootState("1 1 -5\n"), { sawValidMapping: false, rootMapped: false }, "a negative length is malformed");
  assert.deepEqual(parseUidMapRootState("0 1 1\n0 0 0\n"), { sawValidMapping: true, rootMapped: false }, "a valid row plus a zero-length row still proves root unmapped");
  assert.deepEqual(parseUidMapRootState(null), { sawValidMapping: false, rootMapped: false });
});

test("uid-map namespace proof fails closed when the injected reader is unreadable", async () => {
  assert.equal(await hostRootIsUnmappedInCurrentNamespace({ uidMapReader: async () => { throw new Error("no map"); } }), false);
  assert.equal(await hostRootIsUnmappedInCurrentNamespace({ uidMapReader: async () => "0 1000 1\n" }), true);
  assert.equal(await hostRootIsUnmappedInCurrentNamespace({ uidMapReader: async () => "not-a-row\n" }), false);
});

test("installedFragmentSha256 fails closed when the path is swapped after the fd is opened and before path identity", { skip: !linux ? "POSIX secure read" : false }, async (t) => {
  const root = await fixture(t);
  const userDir = join(root, "config/systemd/user");
  await mkdir(userDir, { recursive: true });
  await chmod(userDir, 0o700);
  const unit = "pixel-demo.service";
  const path = join(userDir, unit);
  await writeFile(path, "[Unit]\nDescription=test\n", { mode: 0o600 });
  await chmod(path, 0o600);
  let swapped = false;
  await assert.rejects(installedFragmentSha256(path, uid, unit, {
    userSystemdDir: userDir,
    __testAfterOpen: async () => {
      if (swapped) return;
      swapped = true;
      await rm(path, { force: true });
      await writeFile(path, "SUBSTITUTED AFTER OPEN", { mode: 0o600 });
      await chmod(path, 0o600);
    },
  }), /no longer names the opened inode|real path differs|parent directory|opened safely/u);
  assert.equal(swapped, true, "the after-open swap seam must have run");
});

test("installedFragmentSha256 fails closed when the path is swapped after the content read", { skip: !linux ? "POSIX secure read" : false }, async (t) => {
  const root = await fixture(t);
  const userDir = join(root, "config/systemd/user");
  await mkdir(userDir, { recursive: true });
  await chmod(userDir, 0o700);
  const unit = "pixel-demo.service";
  const path = join(userDir, unit);
  await writeFile(path, "[Unit]\nDescription=test\n", { mode: 0o600 });
  await chmod(path, 0o600);
  let swapped = false;
  await assert.rejects(installedFragmentSha256(path, uid, unit, {
    userSystemdDir: userDir,
    __testAfterRead: async () => {
      if (swapped) return;
      swapped = true;
      await rm(path, { force: true });
      await writeFile(path, "SUBSTITUTED AFTER READ", { mode: 0o600 });
      await chmod(path, 0o600);
    },
  }), /changed during read|no longer names the opened inode/u);
  assert.equal(swapped, true, "the after-read swap seam must have run");
});

test("resolveTrustedSystemctl rejects an untrusted executable or parent and accepts an exact owner-private fake path", { skip: !linux ? "POSIX" : false }, async (t) => {
  const root = await trustedFixture(t);
  const binDir = join(root, "bin");
  await mkdir(binDir, { recursive: true });
  await chmod(binDir, 0o700);
  const sysctl = join(binDir, "systemctl");
  await writeFile(sysctl, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
  await chmod(sysctl, 0o700);
  assert.equal(await resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), sysctl);
  await chmod(sysctl, 0o702);
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), /group or world writable/u);
  await chmod(sysctl, 0o700);
  await chmod(binDir, 0o722);
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), /parent directory/u);
  await chmod(binDir, 0o700);
  await chmod(sysctl, 0o600);
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), /not executable/u);
});

test("systemdShowUnit rejects duplicate, missing, and unknown show property lines", { skip: !linux ? "POSIX" : false }, async (t) => {
  const root = await trustedFixture(t);
  const binDir = join(root, "bin");
  await mkdir(binDir, { recursive: true });
  await chmod(binDir, 0o700);
  const fake = join(binDir, "systemctl");
  await writeFile(fake, "#!/bin/sh\nprintf '%s' \"$FAKE_SHOW_OUTPUT\"\n", { mode: 0o700 });
  await chmod(fake, 0o700);
  const opts = { systemctlPath: fake, expectedOwnerUid: uid };
  const lines = [
    "ActiveState=active",
    "SubState=running",
    "MainPID=123",
    "InvocationID=0123456789abcdef0123456789abcdef",
    "ControlGroup=/user.slice/x.service",
    "FragmentPath=/tmp/x.service",
    "LoadState=loaded",
    "DropInPaths=",
  ];
  const good = `${lines.join("\n")}\n`;
  const env = (out) => ({ ...process.env, FAKE_SHOW_OUTPUT: out });
  const facts = await systemdShowUnit("x.service", { ...opts, env: env(good) });
  assert.equal(facts.ActiveState, "active");
  assert.equal(facts.InvocationID, "0123456789abcdef0123456789abcdef");
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${good}ActiveState=active\n`) }), /duplicate property/u);
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${lines.slice(0, 5).join("\n")}\n`) }), /missing property/u);
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${good}UnknownKey=value\n`) }), /unknown property/u);
});

test("systemdShowUnit requires the exact empty DropInPaths and rejects missing, non-empty, malformed, and duplicate drop-in facts", { skip: !linux ? "POSIX" : false }, async (t) => {
  const root = await trustedFixture(t);
  const binDir = join(root, "bin");
  await mkdir(binDir, { recursive: true });
  await chmod(binDir, 0o700);
  const fake = join(binDir, "systemctl");
  await writeFile(fake, "#!/bin/sh\nprintf '%s' \"$FAKE_SHOW_OUTPUT\"\n", { mode: 0o700 });
  await chmod(fake, 0o700);
  const opts = { systemctlPath: fake, expectedOwnerUid: uid };
  const base = [
    "ActiveState=active",
    "SubState=running",
    "MainPID=123",
    "InvocationID=0123456789abcdef0123456789abcdef",
    "ControlGroup=/user.slice/x.service",
    "FragmentPath=/tmp/x.service",
    "LoadState=loaded",
  ];
  const withEmpty = `${[...base, "DropInPaths="].join("\n")}\n`;
  const env = (out) => ({ ...process.env, FAKE_SHOW_OUTPUT: out });
  const facts = await systemdShowUnit("x.service", { ...opts, env: env(withEmpty) });
  assert.equal(facts.DropInPaths.length, 0);
  // Missing property must fail closed (never defaulted to an empty set).
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${base.join("\n")}\n`) }), /missing property/u);
  // One non-empty drop-in path fails closed.
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${[...base, "DropInPaths=/tmp/x.service.d/a.conf"].join("\n")}\n`) }), /drop-in that could override ExecStart|refusing/u);
  // Multiple drop-in paths (space-separated single line) fail closed.
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${[...base, "DropInPaths=/tmp/x.service.d/a.conf /tmp/x.service.d/b.conf"].join("\n")}\n`) }), /drop-in that could override ExecStart|refusing/u);
  // A malformed empty-but-ambiguous duplicate line fails closed.
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${[...base, "DropInPaths=", "DropInPaths="].join("\n")}\n`) }), /duplicate or ambiguous/u);
  // A duplicate empty/non-empty pairing fails closed.
  await assert.rejects(systemdShowUnit("x.service", { ...opts, env: env(`${[...base, "DropInPaths=", "DropInPaths=/tmp/a.conf"].join("\n")}\n`) }), /duplicate or ambiguous/u);
});

test("resolveTrustedSystemctl rejects a systemctl not owned by root, the sandbox root, or the expected caller", { skip: !linux ? "POSIX" : false }, async (t) => {
  const root = await trustedFixture(t);
  const binDir = join(root, "bin");
  await mkdir(binDir, { recursive: true });
  await chmod(binDir, 0o700);
  const sysctl = join(binDir, "systemctl");
  await writeFile(sysctl, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
  await chmod(sysctl, 0o700);
  // The exact owner-private fake path is accepted for its expected caller.
  assert.equal(await resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), sysctl);
  // A mismatched expected caller (the file is owned by neither root, the
  // sandbox root overflow, nor that caller) is rejected as untrusted.
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: 12345 }), /not owned by root or the expected caller/u);
});

test("assertTrustedParentChain rejects a writable or substituted grandparent even when the leaf and immediate parent are safe", { skip: !linux ? "POSIX" : false }, async (t) => {
  const root = await trustedFixture(t);
  const inner = join(root, "inner");
  const binDir = join(inner, "bin");
  await mkdir(binDir, { recursive: true });
  await chmod(inner, 0o700);
  await chmod(binDir, 0o700);
  const sysctl = join(binDir, "systemctl");
  await writeFile(sysctl, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
  await chmod(sysctl, 0o700);
  // Leaf and immediate parent are safe; the grandparent (inner) is writable.
  await chmod(inner, 0o702);
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl, expectedOwnerUid: uid }), /parent directory|group or world writable/u, "a writable grandparent must be rejected");
  await chmod(inner, 0o700);
  // A substituted (symlinked) ancestor is rejected rather than trusted.
  const realDir = join(root, "real");
  await mkdir(realDir, { recursive: true });
  await chmod(realDir, 0o700);
  const linkDir = join(root, "link");
  await symlink(realDir, linkDir);
  const linkedBin = join(linkDir, "bin");
  await mkdir(linkedBin, { recursive: true });
  await chmod(linkedBin, 0o700);
  const sysctl2 = join(linkedBin, "systemctl");
  await writeFile(sysctl2, "#!/bin/sh\nexit 0\n", { mode: 0o700 });
  await chmod(sysctl2, 0o700);
  await assert.rejects(resolveTrustedSystemctl({ systemctlPath: sysctl2, expectedOwnerUid: uid }), /exact real path|substituted symlink/u, "a substituted ancestor must never be trusted");
});

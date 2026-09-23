"""Focused unit + fake-sudo/fake-systemctl fault tests for the deployment-state
rollback foundation in restore-migration-journal.py.

Covers the low-level privileged rollback layer that, in addition to the existing private
roots, protects deployment-state transitions in one armed transaction: the active current
symlink, selected single-link regular config/workspace/installed-unit files (each present or
absent), and exact service quiescence/restart state. Tests exercise protected regular file
present/absent, symlink target present/absent, bad path/type/owner/mode/link, crash before
and after each rename, partial rollback retry, quiescence failure, restart failure, cleanup
failure, tampered journal, legacy journal handling, and no provider/external effect.
"""
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Production derives the invoking user from trusted SUDO_UID; provide one for in-process
# helper calls so the non-root ownership contract is exercised consistently.
os.environ.setdefault("SUDO_UID", str(os.geteuid()))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_restore_journal_deployment", ROOT / "scripts/restore-migration-journal.py",
)
helper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(helper)

CONTRACT = "c" * 64
BACKUP = "b" * 64
UNIT = "openclaw-gateway.service"
# The exact fixed affected-unit set a new/deployment transaction must carry (finding 1/5):
# every fixed QUIESCE_UNITS member exactly once, no arbitrary names.
FIXED_UNITS = (
    "openclaw-gateway.service",
    "pixel-web-courier.service",
    "pixel-source-broker.timer",
    "pixel-ops-broker.service",
    "pixel-frontier-broker.service",
)
FIXED_DESIRED = {u: {"enabled": True, "active": True} for u in FIXED_UNITS}


def old_path(path, pid=12345, idx=0):
    return os.path.join(os.path.dirname(path), f".pixel-restore-{os.path.basename(path)}-{pid}-{idx}.old")


def new_path(path, pid=12345, idx=0):
    return old_path(path, pid, idx)[:-4] + ".new"


def chmod(path, mode):
    """Apply an explicit permission mask via the fixed /usr/bin/chmod binary (no
    shell, constant/bounded modes) so test-only permission changes are not modelled
    as Python creating permissive files."""
    subprocess.run(["/usr/bin/chmod", format(int(mode), "o"), str(path)], check=True)


def write(path, text, mode=0o600):
    path = Path(path)
    path = path.resolve()
    parent = path.parent
    os.chmod(parent, 0o700)
    path.write_text(text, encoding="utf-8")
    chmod(path, mode)
    return path


def safe_dir(path):
    path = Path(path)
    path.mkdir(parents=True)
    os.chmod(path, 0o700)
    return path


def item(kind, path, idx, had_old, pid=12345):
    return {"kind": kind, "path": path, "oldPath": old_path(path, pid, idx),
            "newPath": new_path(path, pid, idx), "hadOld": had_old}


def stage_new(item_, new_text="NEW"):
    """Stage the prepared 4.2 object at the item's newPath sibling (as activation would)."""
    if item_["kind"] == "symlink":
        os.symlink("releases/4.3.27", item_["newPath"])
    else:
        write(item_["newPath"], new_text)


class FakeJournal:
    """Non-root test copy of the privileged helper with fake systemctl/rm/mv."""

    def __init__(self, base):
        self.base = Path(base)
        self.custody = self.base / "custody"
        self.custody.mkdir()
        os.chmod(self.custody, 0o700)
        self.systemd = self.base / "systemd-system"
        os.mkdir(self.systemd)
        os.chmod(self.systemd, 0o700)
        self.fake = self.base / "bin"
        self.fake.mkdir()
        self.log = self.base / "systemctl.log"
        self.state = self.base / "systemctl.state"
        self.state.write_text("")
        self.enabled_state = self.base / "systemctl.enabled"
        self.enabled_state.write_text("")
        self._write_fake_rm()
        self._write_fake_mv()
        self._write_fake_systemctl()
        self.helper = self._make_helper()

    def _fake(self, name, body):
        path = self.fake / name
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o700)

    def _write_fake_rm(self):
        self._fake("rm", """#!/usr/bin/env bash
if [[ -n ${PIXEL_MIGRATION_TEST_RM_FAIL:-} && "$*" == *"$PIXEL_MIGRATION_TEST_RM_FAIL"* ]]; then
  echo "simulated rm failure: $*" >&2; exit 1
fi
exec /usr/bin/rm "$@"
""")

    def _write_fake_mv(self):
        self._fake("mv", """#!/usr/bin/env bash
exec /usr/bin/mv "$@"
""")

    def _write_fake_systemctl(self):
        self._fake("systemctl", f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{self.log}"
if [[ -n ${{PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL:-}} && "$*" == *"${{PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL}}"* ]]; then
  echo "simulated systemctl failure: $*" >&2; exit 1
fi
state="{self.state}"
enabled="{self.enabled_state}"
cmd=$1
unit="${{!#}}"
case "$cmd" in
  stop) unit=$2; if [[ -f "$state" ]]; then sed -i "\\|^$unit$|d" "$state"; fi ;;
  start|restart) unit=$2; printf '%s\\n' "$unit" >> "$state" ;;
  enable) unit=$2; printf '%s\\n' "$unit" >> "$enabled" ;;
  disable) unit=$2; if [[ -f "$enabled" ]]; then sed -i "\\|^$unit$|d" "$enabled"; fi ;;
  daemon-reload) : ;;
  is-active) if grep -Fqx "$unit" "$state"; then echo active; exit 0; else echo inactive; exit 1; fi ;;
  is-enabled) if grep -Fqx "$unit" "$enabled"; then echo enabled; exit 0; else echo disabled; exit 1; fi ;;
esac
exit 0
""")

    def _make_helper(self):
        scripts_dir = self.base / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        target = scripts_dir / "restore-migration-journal.py"
        # Provide the relocated helper's in-tree release identity (the release root that
        # physically contains the script) so the release-contract derivation binds the exact
        # target even when the script is copied into an isolated harness tree.
        (self.base / "VERSION").write_text((ROOT / "VERSION").read_text(encoding="utf-8"), encoding="utf-8")
        (self.base / "RELEASE-MANIFEST.json").write_text(
            (ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"), encoding="utf-8",
        )
        text = (ROOT / "scripts/restore-migration-journal.py").read_text()
        text = text.replace('SYSTEMCTL_BIN = "/usr/bin/systemctl"', f'SYSTEMCTL_BIN = "{self.fake}/systemctl"')
        text = text.replace('RM_BIN = "/usr/bin/rm"', f'RM_BIN = "{self.fake}/rm"')
        text = text.replace('MV_BIN = "/usr/bin/mv"', f'MV_BIN = "{self.fake}/mv"')
        text = text.replace("REQUIRE_ROOT = True", "REQUIRE_ROOT = False")
        text = text.replace('ROOT_UID = 0', f'ROOT_UID = {os.geteuid()}')
        text = text.replace('ROOT_GID = 0', f'ROOT_GID = {os.getegid()}')
        text = text.replace('UNIT_PARENT = "/etc/systemd/system"', f'UNIT_PARENT = "{self.systemd}"')
        target.write_text(text)
        os.chmod(target, 0o700)
        return target

    def _env(self, systemctl_fail=None, rm_fail=None):
        env = dict(os.environ)
        env["PIXEL_MIGRATION_TEST_SYSTEMCTL_LOG"] = str(self.log)
        env["PIXEL_MIGRATION_TEST_SYSTEMCTL_STATE"] = str(self.state)
        if systemctl_fail:
            env["PIXEL_MIGRATION_TEST_SYSTEMCTL_FAIL"] = systemctl_fail
        if rm_fail:
            env["PIXEL_MIGRATION_TEST_RM_FAIL"] = rm_fail
        return env

    def run(self, *args, systemctl_fail=None, rm_fail=None):
        return subprocess.run(
            [sys.executable, str(self.helper), *args],
            capture_output=True, text=True, env=self._env(systemctl_fail, rm_fail),
        )

    def reserve(self, journal, contract=CONTRACT, backup=BACKUP):
        proc = self.run("reserve", str(self.custody), str(journal), contract, backup)
        if proc.returncode != 0:
            raise AssertionError("reserve failed: " + proc.stderr)
        return proc.stdout.strip()

    def quiesce(self, journal, token, contract=CONTRACT, backup=BACKUP, **kw):
        return self.run("quiesce", str(self.custody), str(journal), token, contract, backup, **kw)

    def _reserve_capture_quiesce(self, journal, units, contract, backup):
        """Reserve -> capture exact prestate -> verified quiesce (the production ordering)."""
        token = self.reserve(journal, contract, backup)
        cap = self.capture(journal, token, list(units), contract, backup)
        if cap.returncode != 0:
            raise AssertionError("capture failed: " + cap.stderr)
        q = self.quiesce(journal, token, contract, backup)
        if q.returncode != 0:
            raise AssertionError("quiesce failed: " + q.stderr)
        return token

    def _spec(self, items, desired=FIXED_DESIRED):
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": items, "serviceDesired": desired}),
                        encoding="utf-8")
        os.chmod(spec, 0o600)
        return spec

    def arm_deploy(self, journal, items, units=FIXED_UNITS, contract=CONTRACT, backup=BACKUP):
        for it in items:
            stage_new(it)
        token = self._reserve_capture_quiesce(journal, units, contract, backup)
        spec = self._spec(items)
        return self.run("arm", str(self.custody), str(journal), token, contract, backup,
                        "0", *units, str(spec))

    def arm_fail(self, journal, items, units=FIXED_UNITS, contract=CONTRACT, backup=BACKUP):
        """Reserve -> capture -> quiesce a valid reservation, then arm the bad spec so the
        specific arm rejection (path/type/owner/mode/link/etc.) is the error under test,
        not a reservation-binding error."""
        token = self._reserve_capture_quiesce(journal, units, contract, backup)
        spec = self._spec(items)
        return self.run("arm", str(self.custody), str(journal), token, contract, backup,
                        "0", *units, str(spec))

    def arm_stage(self, journal, items, stage, units=FIXED_UNITS, contract=CONTRACT, backup=BACKUP):
        """Arm with --stage: the helper performs descriptor-bound intake from the stage root."""
        token = self._reserve_capture_quiesce(journal, units, contract, backup)
        spec = self._spec(items)
        return self.run("arm", str(self.custody), str(journal), token, contract, backup,
                        "0", *units, str(spec), "--stage", str(stage))

    def install(self, journal, **kw):
        return self.run("install", str(self.custody), str(journal), **kw)

    def inspect(self, journal):
        proc = self.run("inspect", str(self.custody), str(journal))
        if proc.returncode != 0:
            raise AssertionError("inspect failed: " + proc.stderr)
        return json.loads(proc.stdout)

    def rollback(self, journal, **kw):
        return self.run("rollback", str(self.custody), str(journal), **kw)

    def finalize(self, journal, **kw):
        return self.run("finalize", str(self.custody), str(journal), **kw)

    def abort(self, journal, token, contract=CONTRACT, backup=BACKUP, **kw):
        return self.run("abort", str(self.custody), str(journal), token, contract, backup, **kw)

    def commit(self, journal, **kw):
        return self.run("commit", str(self.custody), str(journal), **kw)

    def install(self, journal, **kw):
        return self.run("install", str(self.custody), str(journal), **kw)

    def capture(self, journal, token, units, contract=CONTRACT, backup=BACKUP, **kw):
        return self.run("capture", str(self.custody), str(journal), token, contract, backup,
                        *units, **kw)

    def reserve_capture_arm(self, journal, items, units=FIXED_UNITS, contract=CONTRACT, backup=BACKUP):
        """Reserve -> capture exact prestate -> arm (the corrected finding-3 ordering)."""
        for it in items:
            stage_new(it)
        token = self._reserve_capture_quiesce(journal, units, contract, backup)
        spec = self._spec(items)
        return self.run("arm", str(self.custody), str(journal), token, contract, backup,
                        "0", *units, str(spec))


class FakeJournalHarnessBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.fake = FakeJournal(self.base)

    def tearDown(self):
        self.tmp.cleanup()


class ArmValidationTests(FakeJournalHarnessBase):
    """Bad path / type / owner / mode / link must fail closed at arm before mutation."""

    def _live(self):
        dep = self.base / "deploy"
        safe_dir(dep / "install")
        safe_dir(dep / "config")
        safe_dir(dep / "workspace")
        write(dep / "config" / "app.json", "old")
        write(dep / "workspace" / "state.json", "old")
        write(self.fake.systemd / "pixel-work-alpha.service", "old", mode=0o644)
        os.symlink("releases/3.2.2", dep / "install" / "current")
        return dep

    def test_arm_rejects_bad_path_glob_traversal_root(self):
        dep = self._live()
        bad_paths = ["/", "/etc", "/etc/*", "/a/../b", "/tmp/p?x", "/a/./b",
                     "/etc/passwd\n", "/.."]
        for i, bad in enumerate(bad_paths):
            journal = self.fake.custody / f"bad-{i}.json"
            items = [{"kind": "config", "path": bad,
                      "oldPath": old_path("/etc/passwd", 1, 0), "hadOld": 1}]
            proc = self.fake.arm_fail(journal, items)
            self.assertNotEqual(proc.returncode, 0, f"accepted bad path {bad!r}")
            self.assertIn("deployment", proc.stderr)

    def test_arm_rejects_wrong_type(self):
        dep = self._live()
        # Declared file kind but live path is a symlink.
        journal = self.fake.custody / "type1.json"
        items = [item("config", str(dep / "install" / "current"), 0, 1)]
        proc = self.fake.arm_fail(journal, items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a regular file", proc.stderr)
        # Declared symlink kind but live path is a regular file.
        journal = self.fake.custody / "type2.json"
        items = [item("symlink", str(dep / "config" / "app.json"), 0, 1)]
        proc = self.fake.arm_fail(journal, items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a symlink", proc.stderr)

    def test_owner_check_fails_closed(self):
        # User-state files must be owned by the invoking non-root uid (never root, never a
        # caller-supplied owner). Simulate a different invoking uid and confirm refusal.
        dep = self._live()
        target = str(write(dep / "config" / "app.json", "old"))
        with mock.patch.object(helper, "_invoking_uid", return_value=os.geteuid() + 1):
            with self.assertRaises(SystemExit):
                helper._validate_deployment_live_state("config", target, old_path(target, idx=0), 1)

    def test_arm_rejects_bad_mode(self):
        dep = self._live()
        # World-writable unit file must be rejected.
        unit = self.fake.systemd / "pixel-work-alpha.service"
        chmod(unit, 0o666)
        items = [item("unit", str(unit), 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "mode1.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode is unsafe", proc.stderr)
        # setuid config file must be rejected.
        config = dep / "config" / "app.json"
        chmod(config, 0o4755)
        items = [item("config", str(config), 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "mode2.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("setuid/setgid", proc.stderr)

    def test_arm_rejects_multilink(self):
        dep = self._live()
        journal = self.fake.custody / "t.json"
        config = dep / "config" / "app.json"
        os.link(config, dep / "config" / "alias")
        items = [item("config", str(config), 0, 1)]
        proc = self.fake.arm_fail(journal, items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("single-link", proc.stderr)

    def test_arm_rejects_symlink_escape_ancestor(self):
        dep = self._live()
        journal = self.fake.custody / "t.json"
        real = self.base / "real-dir"
        real.mkdir()
        link = self.base / "link-dir"
        os.symlink(str(real), link)
        target = link / "config.json"
        write(target, "x")
        items = [item("config", str(target), 0, 1)]
        proc = self.fake.arm_fail(journal, items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("symlink ancestor", proc.stderr)

    def test_arm_rejects_had_old_mismatch(self):
        dep = self._live()
        journal = self.fake.custody / "t.json"
        # hadOld=0 but path exists.
        items = [item("config", str(dep / "config" / "app.json"), 0, 0)]
        proc = self.fake.arm_fail(self.fake.custody / "ho1.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("hadOld=0 but deployment path exists", proc.stderr)
        # hadOld=1 but path missing.
        missing = dep / "config" / "nope.json"
        items = [item("config", str(missing), 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "ho2.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("deployment path is missing", proc.stderr)


class DeploymentRollbackCommitTests(FakeJournalHarnessBase):
    """Protected regular file present/absent, symlink target present/absent, rollback/commit."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "install")
        safe_dir(dep / "config")
        safe_dir(dep / "workspace")
        return dep

    def test_rollback_restores_file_present_and_symlink_target(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD-CONFIG"))
        sym = str(dep / "install" / "current")
        os.symlink("releases/3.2.2", sym)
        items = [item("config", config, 0, 1), item("symlink", sym, 1, 1)]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_deploy(journal, items)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        inst = self.fake.install(journal)
        self.assertEqual(inst.returncode, 0, inst.stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.assertEqual(os.readlink(sym), "releases/4.3.27")
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD-CONFIG")
        self.assertEqual(os.readlink(sym), "releases/3.2.2")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))
        info = self.fake.inspect(journal)
        self.assertTrue(info["rolledBack"])
        self.assertEqual(info["deploymentItems"], 2)

    def test_rollback_removes_file_absent(self):
        dep = self._deploy()
        ws = str(dep / "workspace" / "state.json")
        items = [item("workspace", ws, 0, 0)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(ws).read_text(), "NEW")
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertFalse(Path(ws).exists())

    def test_rollback_removes_symlink_absent(self):
        dep = self._deploy()
        sym = str(dep / "install" / "extra-link")
        items = [item("symlink", sym, 0, 0)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertTrue(os.path.islink(sym))
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertFalse(os.path.lexists(sym))

    def test_commit_keeps_new_and_cleans_old_siblings(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        unit = str(write(self.fake.systemd / "pixel-work-alpha.service", "OLD-UNIT", mode=0o644))
        items = [item("config", config, 0, 1), item("unit", unit, 1, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        cm = self.fake.commit(journal)
        self.assertEqual(cm.returncode, 0, cm.stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.assertEqual(Path(unit).read_text(), "NEW")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))
        self.assertFalse(os.path.exists(old_path(unit, idx=1)))
        info = self.fake.inspect(journal)
        self.assertTrue(info["committed"])
        self.assertEqual(info["cleanup"], "complete")

    def test_new_transaction_commit_refuses_until_finalized(self):
        # Requirement 8: a newly prepared transaction carries the reviewed serviceDesired
        # contract and requires successful verified service finalization. Commit MUST refuse
        # until finalize has run and completed (exact commit-refusal coverage), while a
        # legacy journal without serviceDesired keeps its backward-compatible commit.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "new-txn.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        for it in items:
            stage_new(it)
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({
            "deploymentItems": items,
            "serviceDesired": FIXED_DESIRED,
        }), encoding="utf-8")
        os.chmod(spec, 0o600)
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT, BACKUP,
                            "0", *FIXED_UNITS, str(spec))
        self.assertEqual(arm.returncode, 0, arm.stderr)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Commit must refuse until verified finalization completes.
        refused = self.fake.commit(journal)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("verified service finalization", refused.stderr)
        # After finalize, commit succeeds.
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        self.assertEqual(self.fake.commit(journal).returncode, 0)
        info = self.fake.inspect(journal)
        self.assertTrue(info["committed"])

    def test_never_commit_rolled_back(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.rollback(journal).returncode, 0)
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("already-rolled-back", cm.stderr)

    def test_never_rollback_committed(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        self.assertEqual(self.fake.commit(journal).returncode, 0)
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("already-committed", rb.stderr)


class CrashRecoveryTests(FakeJournalHarnessBase):
    """Hard-crash recovery from every rename prefix + partial rollback retry."""

    def _deploy_two(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        safe_dir(dep / "workspace")
        return dep

    def test_crash_after_first_rename_reconstructs(self):
        # Crash after path->oldPath but before installing new content: oldPath holds the
        # old content, path is absent. Rollback must reconstruct the exact pre-migration state.
        dep = self._deploy_two()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        os.rename(config, old_path(config, idx=0))  # path->old, new not installed
        self.assertFalse(Path(config).exists())
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))

    def test_crash_before_any_rename_is_idempotent(self):
        # Crash before any mutation: path still holds old content, oldPath absent. Rollback
        # is a no-op (path already matches evidence) and leaves state byte-identical.
        dep = self._deploy_two()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_crash_after_install_reconstructs(self):
        dep = self._deploy_two()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(config).read_text(), "NEW")
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_partial_rollback_retry_resumes(self):
        # Simulate a rollback that restored item 0 then crashed before item 1: item 0 is
        # already restored (progress persisted) and item 1 still swapped. A retry must skip
        # item 0 and resume with item 1 (idempotent, per-item durable progress).
        dep = self._deploy_two()
        config = str(write(dep / "config" / "app.json", "OLD"))
        ws = str(write(dep / "workspace" / "state.json", "OLD-WS"))
        items = [item("config", config, 0, 1), item("workspace", ws, 1, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Restore item 0 and persist progress, leaving item 1 swapped.
        os.rename(old_path(config, idx=0), config)
        journal_path = self.fake.custody / journal.name
        journal_data = json.loads(journal_path.read_text())
        journal_data["deploymentItems"][0]["progress"] = "restored"
        (self.fake.custody / journal.name).write_text(
            json.dumps(journal_data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertEqual(Path(ws).read_text(), "OLD-WS")


class ServiceFaultTests(FakeJournalHarnessBase):
    """Quiescence failure and restart failure leave no partial filesystem mutation."""

    def _deploy(self):
        dep = self.base / "deploy"
        (dep / "config").mkdir(parents=True)
        return dep

    def test_quiescence_failure_mutates_nothing(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.reserve_capture_arm(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(config).read_text(), "NEW")
        # Re-activate the unit so rollback's pre-mutation quiesce must attempt to stop it.
        self.fake.state.write_text(UNIT + "\n")
        rb = self.fake.rollback(journal, systemctl_fail="stop " + UNIT)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("could not be stopped", rb.stderr)
        # Live state untouched because quiescence failed before any mutation.
        self.assertEqual(Path(config).read_text(), "NEW")
        info = self.fake.inspect(journal)
        self.assertFalse(info["rolledBack"])

    def test_restart_failure_reports_service_pending(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        rb = self.fake.rollback(journal, systemctl_fail="start " + UNIT)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("service-pending", rb.stdout)
        # Filesystem was still restored deterministically; only the restart failed.
        self.assertEqual(Path(config).read_text(), "OLD")
        info = self.fake.inspect(journal)
        self.assertTrue(info["rolledBack"])
        self.assertEqual(info["finalization"], "failed")


class CleanupFailureTests(FakeJournalHarnessBase):
    def test_cleanup_failure_reports_and_never_rolls_back(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        # Force the dirfd cleanup unlink to fail: replace the oldPath file with a directory
        # (a deployment item is never a directory, so this is a corrupt/abnormal state).
        op = old_path(config, idx=0)
        os.unlink(op)
        os.mkdir(op)
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("cleanup-failed", cm.stdout)
        info = self.fake.inspect(journal)
        self.assertTrue(info["committed"])
        self.assertEqual(info["cleanup"], "failed")
        # Commit must retain the new live state and never roll back.
        self.assertEqual(Path(config).read_text(), "NEW")
        # Remove the abnormal directory; the idempotent retry completes cleanup.
        os.rmdir(op)
        cm = self.fake.commit(journal)
        self.assertEqual(cm.returncode, 0, cm.stderr)
        self.assertFalse(os.path.exists(op))


class TamperAndLegacyTests(FakeJournalHarnessBase):
    def test_tampered_evidence_fails_closed_without_mutation(self):
        dep = self.base / "deploy"
        (dep / "config").mkdir(parents=True)
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        journal_path = self.fake.custody / journal.name
        data = json.loads(journal_path.read_text())
        data["deploymentItems"][0]["evidence"]["sha256"] = "0" * 64
        journal_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("indeterminate", rb.stderr)
        # No mutation occurred: new content retained.
        self.assertEqual(Path(config).read_text(), "NEW")

    def test_legacy_journal_shape_validates(self):
        # A legacy journal (no deploymentItems key) with the exact base keys still validates
        # and rolls back as a pure roots transaction (compatibility).
        value = {
            "schemaVersion": 1, "kind": "pixel-restore-migration-journal",
            "backupSha256": BACKUP, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "committed": False, "cleanup": "armed", "rolledBack": False,
            "finalization": "armed", "contractRoots": [], "destinations": [],
            "oldPaths": [], "temporaryPaths": [], "hadOld": [], "units": [],
            "rollbackProgress": [], "oldEvidence": [],
        }
        validated = helper._validate_journal(value)
        self.assertEqual(validated.get("deploymentItems", []), [])

    def test_legacy_journal_fails_closed_on_wrong_shape(self):
        value = {
            "schemaVersion": 1, "kind": "pixel-restore-migration-journal",
            "backupSha256": BACKUP, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "committed": False, "cleanup": "armed", "rolledBack": False,
            "finalization": "armed", "contractRoots": [], "destinations": [],
            "oldPaths": [], "temporaryPaths": [], "hadOld": [], "units": [],
        }
        with self.assertRaises(SystemExit):
            helper._validate_journal(value)

    def test_tampered_kind_fails_closed(self):
        with self.assertRaises(SystemExit):
            helper._validate_deployment_items([{
                "kind": "mystery", "path": "/a/b", "oldPath": "/a/.pixel-restore-b-1-0.old",
                "hadOld": 0, "evidence": None, "progress": "pending",
            }])

    def test_legacy_roots_journal_rolls_back_without_deployment_key(self):
        # A legacy pure-roots journal (no deploymentItems key) must still roll back through
        # the subprocess (compatibility: the key is optional and treated as empty).
        dest = self.base / "root"
        dest.mkdir()
        write(dest / "state.json", "OLD")
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        old = str(self.base / ".pixel-restore-root-12345-0.old")
        temp = str(self.base / ".pixel-restore-root-12345-0.new")
        os.makedirs(temp)
        write(temp + "/state.json", "NEW")
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token,
                             CONTRACT, BACKUP, "1", str(dest), old, temp, "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        jp = self.fake.custody / journal.name
        data = json.loads(jp.read_text())
        data.pop("deploymentItems")
        jp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.rename(str(dest), old)
        os.rename(temp, str(dest))
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual((dest / "state.json").read_text(), "OLD")


class NoExternalEffectTests(FakeJournalHarnessBase):
    def test_only_validated_units_and_custody_are_touched(self):
        dep = self.base / "deploy"
        (dep / "config").mkdir(parents=True)
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        log_lines = self.fake.log.read_text().splitlines()
        self.assertTrue(log_lines, "no systemctl activity recorded")
        for line in log_lines:
            parts = line.split()
            if parts[0] == "daemon-reload":
                self.assertEqual(len(parts), 1)
                continue
            self.assertIn(parts[0], {"stop", "restart", "start", "enable", "disable", "is-active", "is-enabled"})
            # Only the fixed affected-unit set is ever touched - never docker/age/arbitrary.
            self.assertIn(parts[-1], set(FIXED_UNITS))
            self.assertNotIn("docker", line)
            self.assertNotIn("age", line)
        # Custody must contain only the journal (no stray temp/provider artifacts).
        names = sorted(os.listdir(self.fake.custody))
        self.assertEqual(names, [journal.name])


class DeploymentPathContractTests(FakeJournalHarnessBase):
    """Issue 1/5/9: strict unit allowlist, parent ownership/mode, invoking-owner symlink."""

    def test_unit_outside_fixed_contract_rejected(self):
        # A unit basename outside the fixed/deep Pixel unit contract is refused.
        dep = self.base / "deploy"
        safe_dir(dep / "units")
        bad = str(write(dep / "units" / "pixel-work-alpha.service", "x", mode=0o644))
        items = [item("unit", bad, 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "u1.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unit deployment item", proc.stderr)

    def test_unit_wrong_directory_rejected(self):
        # A valid unit basename in the wrong directory is refused.
        dep = self.base / "deploy"
        safe_dir(dep)
        bad = str(write(dep / "pixel-work-alpha.service", "x", mode=0o644))
        items = [item("unit", bad, 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "u2.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unit deployment item", proc.stderr)

    def test_unit_valid_contract_accepted(self):
        self.fake.systemd.mkdir(exist_ok=True)
        os.chmod(self.fake.systemd, 0o700)
        unit = str(write(self.fake.systemd / "openclaw-gateway.service", "x", mode=0o644))
        items = [item("unit", unit, 0, 1)]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_deploy(journal, items)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_group_writable_parent_rejected(self):
        dep = self.base / "deploy"
        parent = dep / "config"
        parent.mkdir(parents=True)
        config = parent / "app.json"
        config.write_text("OLD", encoding="utf-8")
        chmod(parent, 0o775)  # group-writable parent must be refused
        items = [item("config", str(config), 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "p.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("group/other writable", proc.stderr)

    def test_current_symlink_owned_by_invoking_user_accepted(self):
        dep = self.base / "deploy"
        safe_dir(dep / "install")
        sym = str(dep / "install" / "current")
        os.symlink("releases/3.2.2", sym)
        items = [item("symlink", sym, 0, 1)]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_deploy(journal, items)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_current_symlink_not_invoking_owner_rejected(self):
        dep = self.base / "deploy"
        safe_dir(dep / "install")
        sym = str(dep / "install" / "current")
        os.symlink("releases/3.2.2", sym)
        with mock.patch.object(helper, "_invoking_uid", return_value=os.geteuid() + 1):
            with self.assertRaises(SystemExit):
                helper._validate_deployment_live_state("symlink", sym, old_path(sym, idx=0), 1)


class RootTestOwnerDistinctionTests(unittest.TestCase):
    """Issue 5: unit files are root-owned, user-state files are invoking-user owned."""

    def _systemd(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "systemd-system"
        os.mkdir(d)
        os.chmod(d, 0o700)
        return d

    def test_unit_file_requires_root_owner_in_production(self):
        systemd = self._systemd()
        unit = write(systemd / "pixel-work-alpha.service", "x", mode=0o644)
        with mock.patch.object(helper, "UNIT_PARENT", str(systemd)),              mock.patch.object(helper, "ROOT_UID", 0):
            with self.assertRaises(SystemExit):
                helper._validate_deployment_live_state(
                    "unit", str(unit), old_path(str(unit), idx=0), 1)

    def test_unit_file_accepted_when_root_owner_matches(self):
        systemd = self._systemd()
        unit = write(systemd / "pixel-work-alpha.service", "x", mode=0o644)
        with mock.patch.object(helper, "UNIT_PARENT", str(systemd)),              mock.patch.object(helper, "ROOT_UID", os.geteuid()):
            ev, _ = helper._validate_deployment_live_state(
                "unit", str(unit), old_path(str(unit), idx=0), 1)
            self.assertEqual(ev["type"], "file")
            self.assertEqual(ev["uid"], os.geteuid())

    def test_user_state_requires_invoking_uid_not_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = safe_dir(Path(tmp.name) / "config")
        target = str(write(cfg / "app.json", "old"))
        with mock.patch.object(helper, "_invoking_uid", return_value=0):
            with self.assertRaises(SystemExit):
                helper._validate_deployment_live_state(
                    "config", target, old_path(target, idx=0), 1)


class SpecIntakeTests(FakeJournalHarnessBase):
    """Issue 2: descriptor-bound, race-free, strict spec intake."""

    def _arm_spec(self, spec_path, units=()):
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        return self.fake.run("arm", str(self.fake.custody), str(journal), token,
                             CONTRACT, BACKUP, "0", str(spec_path))

    def _write_spec(self, path, content=None):
        path = Path(path)
        if content is None:
            content = json.dumps({"deploymentItems": []})
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def test_spec_symlink_rejected(self):
        d = safe_dir(self.base / "specdir")
        real = self._write_spec(d / "real.json")
        link = d / "spec-link.json"
        os.symlink(real.name, link)
        proc = self._arm_spec(link)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("regular single-link file", proc.stderr)

    def test_spec_hard_link_rejected(self):
        d = safe_dir(self.base / "specdir")
        spec = self._write_spec(d / "spec.json")
        os.link(spec, d / "alias.json")
        proc = self._arm_spec(spec)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("single-link", proc.stderr)

    def test_spec_unsafe_mode_rejected(self):
        d = safe_dir(self.base / "specdir")
        spec = self._write_spec(d / "spec.json")
        chmod(spec, 0o666)
        proc = self._arm_spec(spec)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("spec mode is unsafe", proc.stderr)

    def test_spec_oversize_rejected(self):
        d = safe_dir(self.base / "specdir")
        spec = d / "spec.json"
        spec.write_text("a" * (helper.MAX_JSON_BYTES + 100), encoding="utf-8")
        os.chmod(spec, 0o600)
        proc = self._arm_spec(spec)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("oversized", proc.stderr)

    def test_spec_changed_during_read_rejected(self):
        d = safe_dir(self.base / "specdir")
        spec = self._write_spec(d / "spec.json")
        real_read = os.read
        state = {"touched": False}

        def touch(fd, n):
            data = real_read(fd, n)
            if data and not state["touched"]:
                state["touched"] = True
                os.utime(spec, None)
            return data

        with mock.patch("os.read", side_effect=touch):
            with self.assertRaises(SystemExit):
                helper._load_deployment_spec(str(spec))

    def test_missing_spec_fails_as_spec_not_unit(self):
        missing = str(self.base / "no-such-spec.json")
        proc = self._arm_spec(missing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("deployment spec", proc.stderr)


class EvidenceMetadataTamperTests(FakeJournalHarnessBase):
    """Issue 3/6: tampered exact metadata fails closed without mutation."""

    def _armed(self, tag):
        dep = safe_dir(self.base / f"deploy-{tag}")
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / f"t-{tag}.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        return journal, config

    def _tamper(self, journal, key):
        jp = self.fake.custody / journal.name
        data = json.loads(jp.read_text())
        ev = data["deploymentItems"][0]["evidence"]
        ev[key] = ev[key] + 1
        jp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    def test_tamper_exact_metadata_fails_closed(self):
        for key in ("uid", "gid", "mode", "ino", "dev", "nlink", "size"):
            journal, config = self._armed(key)
            self._tamper(journal, key)
            rb = self.fake.rollback(journal)
            self.assertNotEqual(rb.returncode, 0, f"accepted tampered {key}")
            self.assertIn("indeterminate", rb.stderr)
            self.assertEqual(Path(config).read_text(), "NEW")


class ParentSwapTests(FakeJournalHarnessBase):
    """Issue 4: parent substitution between arm and rollback is refused, no mutation."""

    def test_parent_substitution_fails_closed_without_mutation(self):
        dep = self.base / "deploy"
        parent = safe_dir(dep / "config")
        config = str(write(parent / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        moved = self.base / "config-moved"
        os.rename(parent, moved)
        new_parent = safe_dir(parent)
        write(new_parent / "app.json", "EVIL")
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("parent changed", rb.stderr)
        self.assertEqual((new_parent / "app.json").read_text(), "EVIL")


class PathCollisionAndDigestTests(FakeJournalHarnessBase):
    """Issue 7: cross-item oldPath collision, spec overlap, persisted spec digest."""

    def test_cross_item_oldpath_collision_rejected(self):
        parent = str(self.base / "d")
        a_path = parent + "/app.json"
        a_old = old_path(a_path, pid=1, idx=0)
        b_path = a_old
        b_old = old_path(b_path, pid=1, idx=0)
        pev = {"type": "dir", "dev": 1, "ino": 1, "uid": 0, "gid": 0, "mode": 0o700}
        file_ev = {"type": "file", "sha256": "0" * 64, "uid": 0, "gid": 0, "mode": 0o600,
                   "ino": 1, "dev": 1, "size": 1, "nlink": 1, "mtime_ns": 1, "ctime_ns": 1}
        items = [
            {"kind": "config", "path": a_path, "oldPath": a_old,
             "newPath": new_path(a_path, 1, 0), "hadOld": 1,
             "evidence": file_ev, "newEvidence": file_ev, "parent": pev,
             "progress": "pending", "installProgress": "pending"},
            {"kind": "config", "path": b_path, "oldPath": b_old,
             "newPath": new_path(b_path, 1, 0), "hadOld": 0,
             "evidence": None, "newEvidence": file_ev, "parent": pev,
             "progress": "pending", "installProgress": "pending"},
        ]
        with self.assertRaises(SystemExit):
            helper._validate_deployment_items(items)

    def test_spec_file_overlapping_managed_path_rejected(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        items = [item("config", str(dep / "config" / "app.json"), 0, 1)]
        stage_new(items[0])
        config = str(write(dep / "config" / "app.json",
                           json.dumps({"deploymentItems": items, "serviceDesired": FIXED_DESIRED})))
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token,
                             CONTRACT, BACKUP, "0", *FIXED_UNITS, config)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("spec file must not overlap", proc.stderr)

    def test_spec_digest_persisted_in_journal(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        stage_new(items[0])
        spec = self.base / "myspec.json"
        spec_content = json.dumps({"deploymentItems": items, "serviceDesired": FIXED_DESIRED},
                                  sort_keys=True, separators=(",", ":"))
        spec.write_text(spec_content, encoding="utf-8")
        os.chmod(spec, 0o600)
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token,
                             CONTRACT, BACKUP, "0", *FIXED_UNITS, str(spec))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.fake.custody / journal.name).read_text())
        self.assertEqual(data["deploymentSpecSha256"],
                         hashlib.sha256(spec_content.encode("utf-8")).hexdigest())



class StageIntakeTests(FakeJournalHarnessBase):
    """Findings 7/8: the privileged helper performs descriptor-bound candidate intake from the
    non-live stage root (never the restore shell), bound to the prepared per-item digest."""

    def _live(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def _stage_with(self, name, content):
        stage = safe_dir(self.base / "stage")
        (stage / name).write_text(content, encoding="utf-8")
        os.chmod(stage / name, 0o600)
        return stage

    def test_intake_installs_from_stage(self):
        dep = self._live()
        config = str(write(dep / "config" / "app.json", "OLD"))
        name = os.path.basename(new_path(config, idx=0))
        stage = self._stage_with(name, "4.2-content")
        sha = hashlib.sha256(b"4.2-content").hexdigest()
        items = [{"kind": "config", "path": config, "oldPath": old_path(config, idx=0),
                  "newPath": new_path(config, idx=0), "hadOld": 1, "sha256": sha}]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_stage(journal, items, stage)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "4.2-content")
        self.assertEqual(Path(old_path(config, idx=0)).read_text(), "OLD")

    def test_unit_intake_normalizes_runtime_owner_group_and_mode(self):
        unit = str(write(self.fake.systemd / UNIT, "OLD", mode=0o644))
        name = os.path.basename(new_path(unit, idx=0))
        stage = self._stage_with(name, "NEW-UNIT")
        sha = hashlib.sha256(b"NEW-UNIT").hexdigest()
        items = [{"kind": "unit", "path": unit, "oldPath": old_path(unit, idx=0),
                  "newPath": new_path(unit, idx=0), "hadOld": 1, "sha256": sha}]
        journal = self.fake.custody / "unit.json"
        proc = self.fake.arm_stage(journal, items, stage)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        installed = self.fake.install(journal)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        info = os.stat(unit)
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(info.st_gid, os.getegid())
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o644)
        self.assertEqual(Path(unit).read_text(), "NEW-UNIT")
        self.assertEqual(Path(old_path(unit, idx=0)).read_text(), "OLD")

    def test_intake_refuses_digest_mismatch(self):
        dep = self._live()
        config = str(write(dep / "config" / "app.json", "OLD"))
        name = os.path.basename(new_path(config, idx=0))
        stage = self._stage_with(name, "swapped-after-prepare")
        sha = hashlib.sha256(b"reviewed-content").hexdigest()
        items = [{"kind": "config", "path": config, "oldPath": old_path(config, idx=0),
                  "newPath": new_path(config, idx=0), "hadOld": 1, "sha256": sha}]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_stage(journal, items, stage)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not match the prepared digest", proc.stderr)
        # Nothing was staged or mutated.
        self.assertFalse(os.path.lexists(new_path(config, idx=0)))
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_intake_refuses_symlink_candidate_for_file_item(self):
        dep = self._live()
        config = str(write(dep / "config" / "app.json", "OLD"))
        name = os.path.basename(new_path(config, idx=0))
        stage = safe_dir(self.base / "stage")
        os.symlink("elsewhere", stage / name)
        items = [{"kind": "config", "path": config, "oldPath": old_path(config, idx=0),
                  "newPath": new_path(config, idx=0), "hadOld": 1,
                  "sha256": "0" * 64}]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_stage(journal, items, stage)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("symlink", proc.stderr)

    def test_intake_refuses_missing_candidate(self):
        dep = self._live()
        config = str(write(dep / "config" / "app.json", "OLD"))
        stage = safe_dir(self.base / "stage")
        items = [{"kind": "config", "path": config, "oldPath": old_path(config, idx=0),
                  "newPath": new_path(config, idx=0), "hadOld": 1,
                  "sha256": "0" * 64}]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_stage(journal, items, stage)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing", proc.stderr)


class AbsentItemTests(FakeJournalHarnessBase):
    """Findings 4/6: a declared desired-absent item is an explicit bounded transaction."""

    def test_absent_item_removes_live_and_rolls_back(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "stale.env", "OLD"))
        items = [{"kind": "config", "path": config, "oldPath": old_path(config, idx=0),
                  "newPath": new_path(config, idx=0), "hadOld": 1, "sha256": None}]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_stage(journal, items, safe_dir(self.base / "stage"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertFalse(os.path.lexists(config))
        self.assertEqual(Path(old_path(config, idx=0)).read_text(), "OLD")
        self.assertEqual(self.fake.rollback(journal).returncode, 0, self.fake.rollback(journal).stderr)
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.lexists(old_path(config, idx=0)))


class CaptureOneShotTests(FakeJournalHarnessBase):
    """Finding 1: strict one-shot capture - reject arbitrary/duplicate/subset units,
    recapture/overwrite, and a quiesced reservation."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def test_capture_rejects_arbitrary_unit(self):
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        cap = self.fake.capture(journal, token, list(FIXED_UNITS) + ["ssh.service"])
        self.assertNotEqual(cap.returncode, 0)
        self.assertIn("outside the fixed Pixel service contract", cap.stderr)
        self.assertNotIn("servicePrestate", self.fake.inspect(journal))

    def test_capture_rejects_duplicate_units(self):
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        cap = self.fake.capture(journal, token, list(FIXED_UNITS) + [UNIT])
        self.assertNotEqual(cap.returncode, 0)
        self.assertIn("unique", cap.stderr)

    def test_capture_requires_all_fixed_units(self):
        # A new migration affected-unit set must include every fixed QUIESCE_UNITS member.
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        cap = self.fake.capture(journal, token, [UNIT])
        self.assertNotEqual(cap.returncode, 0)
        self.assertIn("every fixed Pixel service", cap.stderr)

    def test_capture_rejects_recapture_overwrite(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        self.assertEqual(self.fake.capture(journal, token, list(FIXED_UNITS)).returncode, 0)
        # A second capture must be refused (one-shot).
        cap2 = self.fake.capture(journal, token, list(FIXED_UNITS))
        self.assertNotEqual(cap2.returncode, 0)
        self.assertIn("already captured", cap2.stderr)

    def test_capture_rejects_quiesced_reservation(self):
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        self.assertEqual(self.fake.capture(journal, token, list(FIXED_UNITS)).returncode, 0)
        self.assertEqual(self.fake.quiesce(journal, token).returncode, 0)
        cap2 = self.fake.capture(journal, token, list(FIXED_UNITS))
        self.assertNotEqual(cap2.returncode, 0)
        self.assertIn("already quiesced", cap2.stderr)


class ArmDeploymentBindingTests(FakeJournalHarnessBase):
    """Finding 2/5: a deployment transaction must bind the captured reservation exactly -
    quiesced, units exactly equal the reserved list, and reviewed serviceDesired present."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def _spec(self, items, desired=FIXED_DESIRED):
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": items, "serviceDesired": desired}),
                        encoding="utf-8")
        os.chmod(spec, 0o600)
        return spec

    def test_arm_deployment_requires_quiesced_reservation(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake.reserve(journal)
        cap = self.fake.capture(journal, token, list(FIXED_UNITS))
        self.assertEqual(cap.returncode, 0, cap.stderr)
        for it in items:
            stage_new(it)
        spec = self._spec(items)
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                            BACKUP, "0", *FIXED_UNITS, str(spec))
        self.assertNotEqual(arm.returncode, 0)
        self.assertIn("quiesced", arm.stderr)

    def test_arm_deployment_rejects_subset_units(self):
        # A deployment transaction must carry the FULL captured affected-unit set, never a
        # subset whose prestate entries happen to exist.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        for it in items:
            stage_new(it)
        spec = self._spec(items)
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                            BACKUP, "0", UNIT, str(spec))
        self.assertNotEqual(arm.returncode, 0)
        self.assertIn("every fixed Pixel service", arm.stderr)

    def test_arm_deployment_requires_exact_reserved_units(self):
        # Even a full fixed set in a different order than the captured reservation is refused:
        # the arm units list must EXACTLY equal the captured reserved list.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        for it in items:
            stage_new(it)
        spec = self._spec(items)
        reordered = list(reversed(FIXED_UNITS))
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                            BACKUP, "0", *reordered, str(spec))
        self.assertNotEqual(arm.returncode, 0)
        self.assertIn("exactly equal", arm.stderr)

    def test_arm_deployment_requires_service_desired(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        for it in items:
            stage_new(it)
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": items}), encoding="utf-8")
        os.chmod(spec, 0o600)
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                            BACKUP, "0", *FIXED_UNITS, str(spec))
        self.assertNotEqual(arm.returncode, 0)
        self.assertIn("serviceDesired", arm.stderr)


class CommitFinalizationTests(FakeJournalHarnessBase):
    """Finding 6: a deployment transaction is defined by its spec/items/digest and must
    carry a desired map and finalization == complete before commit; absent desired and
    armed/failed/pending finalization are all rejected. Legacy roots-only keeps old behavior."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def test_commit_deployment_rejects_absent_desired(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        jp = self.fake.custody / journal.name
        data = json.loads(jp.read_text())
        data.pop("serviceDesired")
        jp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("requires the reviewed serviceDesired", cm.stderr)
        self.assertFalse(self.fake.inspect(journal)["committed"])

    def test_commit_deployment_rejects_armed_finalization(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # finalization is still "armed" (never finalized) - commit must refuse.
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("verified service finalization", cm.stderr)
        self.assertFalse(self.fake.inspect(journal)["committed"])

    def test_commit_deployment_rejects_failed_finalization(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        fn = self.fake.finalize(journal, systemctl_fail="enable " + UNIT)
        self.assertNotEqual(fn.returncode, 0)
        info = self.fake.inspect(journal)
        self.assertEqual(info["finalization"], "failed")
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("verified service finalization", cm.stderr)
        self.assertFalse(self.fake.inspect(journal)["committed"])


if __name__ == "__main__":
    unittest.main()

"""Focused tests for the pixel_access_mode persistent controller."""
import copy
from pathlib import Path
import fcntl
import json
import os
import shutil
import stat
from unittest import mock
import importlib.util
import tempfile
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "host",
    "pixel_access_mode.py")
SPEC = importlib.util.spec_from_file_location("pixel_access_mode", MODULE_PATH)
assert SPEC and SPEC.loader
pam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pam)

AccessModeError = pam.AccessModeError
AccessModeRejected = pam.AccessModeRejected
AccessModeSecurity = pam.AccessModeSecurity
AccessModeRace = pam.AccessModeRace
AccessModeRollback = pam.AccessModeRollback

RECEIPT = os.path.join("state", "pixel-access-mode.json")  # under state_dir


def make_config():
    return {
        "agents": {
            "defaults": {"model": "default-model"},
            "list": [
                {
                    "id": "pixel",
                    "sandbox": {"mode": "all"},
                    "tools": {
                        "exec": {"host": "sandbox", "security": "allowlist",
                                 "ask": "always"},
                        "fs": {"workspaceOnly": True},
                    },
                    "model": "pixel-model",
                },
                {"id": "other"},
            ],
        }
    }


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pam-test-")
        self.cfg_path = os.path.join(self.tmp, "openclaw.json")
        self.state_dir = os.path.join(self.tmp, "state")
        self.write_config(make_config())
        os.makedirs(self.state_dir, mode=0o700)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, cfg):
        with open(self.cfg_path, "w") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump(cfg, fh, indent=2)

    def read_config(self):
        with open(self.cfg_path) as fh:
            return json.load(fh)

    def receipt(self):
        path = os.path.join(self.state_dir, "pixel-access-mode.json")
        with open(path) as fh:
            return json.load(fh)

    def enable(self, **kw):
        kw.setdefault("confirmed", True)
        kw.setdefault("validate_config", lambda path: True)
        kw.setdefault("restart", lambda: True)
        kw.setdefault("check_no_active_run", lambda: False)
        return pam.enable_full_access(self.cfg_path, self.state_dir, **kw)

    def restore(self, **kw):
        kw.setdefault("validate_config", lambda path: True)
        kw.setdefault("restart", lambda: True)
        kw.setdefault("check_no_active_run", lambda: False)
        return pam.restore_sandbox(self.cfg_path, self.state_dir, **kw)

    def status(self):
        return pam.get_status(self.cfg_path, self.state_dir)

    def pixel(self, cfg):
        return [e for e in cfg["agents"]["list"] if e.get("id") == "pixel"][0]

    def write_pending_receipt(self, baseline):
        with open(self.cfg_path, "rb") as fh:
            h = pam._sha256_bytes(fh.read())
        pam._write_receipt(self.state_dir, {
            "version": 1, "status": "pending",
            "config_path": os.path.abspath(self.cfg_path),
            "config_sha256": h, "baseline": baseline,
        })


class TestEnable(Harness):
    def test_revision_checked_under_lock_before_enable_or_restore(self):
        before = open(self.cfg_path, "rb").read()
        with self.assertRaises(pam.AccessModeRace):
            self.enable(expected_config_sha256="0" * 64)
        self.assertEqual(open(self.cfg_path, "rb").read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "pixel-access-mode.json")))
        self.enable(expected_config_sha256=pam._sha256_bytes(before))
        enabled = open(self.cfg_path, "rb").read()
        receipt = self.receipt()
        with self.assertRaises(pam.AccessModeRace):
            self.restore(expected_config_sha256=pam._sha256_bytes(before))
        self.assertEqual(open(self.cfg_path, "rb").read(), enabled)
        self.assertEqual(self.receipt(), receipt)

    def test_config_changed_during_restart_retains_pending_state(self):
        def restart_and_change_config():
            cfg = self.read_config()
            self.pixel(cfg)["sandbox"]["mode"] = "all"
            self.write_config(cfg)
            return True
        with self.assertRaises(AccessModeRace):
            self.enable(restart=restart_and_change_config)
        self.assertEqual(self.receipt()["status"], "pending")
        self.assertEqual(self.pixel(self.read_config())["sandbox"]["mode"], "all")

    def test_requires_confirmation(self):
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(confirmed=False)
        self.assertEqual(cm.exception.code, "not-confirmed")
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))

    def test_requires_live_active_run_check_and_restart(self):
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(check_no_active_run=None)
        self.assertEqual(cm.exception.code, "no-active-run-check")
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(restart=None)
        self.assertEqual(cm.exception.code, "no-restart-hook")

    def test_active_run_rejected_no_changes(self):
        before = Path(self.cfg_path).read_bytes()
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(check_no_active_run=lambda: True)
        self.assertEqual(cm.exception.code, "active-run")
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)

    def test_happy_path(self):
        result = self.enable()
        self.assertEqual(result["status"], "full-access")
        cfg = self.read_config()
        pixel = self.pixel(cfg)
        self.assertEqual(pixel["sandbox"]["mode"], "off")
        self.assertEqual(pixel["tools"]["exec"],
                         {"host": "gateway", "security": "full", "ask": "off"})
        self.assertIs(pixel["tools"]["fs"]["workspaceOnly"], False)
        self.assertEqual(self.pixel(cfg)["model"], "pixel-model")
        self.assertEqual(cfg["agents"]["list"][1], {"id": "other"})
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "full-access")
        self.assertEqual(receipt["baseline"]["sandbox.mode"]["value"], "all")
        st = self.status()
        self.assertEqual(st["status"], "full-access")
        self.assertTrue(st["managed"])
        self.assertEqual(st["config_sha256"], result["config_sha256"])

    def test_staged_validation_failure_no_changes(self):
        seen = []
        before = Path(self.cfg_path).read_bytes()

        def validator(path):
            seen.append(path)
            return False, "synthetic schema error"

        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(validate_config=validator)
        self.assertEqual(cm.exception.code, "validation-failed")
        self.assertIn("synthetic schema error", str(cm.exception))
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], self.cfg_path)  # staged copy, not real
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))

    def test_validator_bad_contract_rejected(self):
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(validate_config=lambda path: "yes")
        self.assertEqual(cm.exception.code, "validator-contract")
        ok = self.enable(validate_config=lambda path: (True, ""))
        self.assertEqual(ok["status"], "full-access")

    def test_restart_failure_rolls_back_config_and_state(self):
        calls = []
        before = Path(self.cfg_path).read_bytes()

        def restart():
            calls.append(1)
            return len(calls) >= 2  # apply restart fails, rollback succeeds

        with self.assertRaises(AccessModeRejected) as cm:
            self.enable(restart=restart)
        self.assertEqual(cm.exception.code, "restart-failed-rolled-back")
        self.assertEqual(len(calls), 2)  # failed attempt + rollback restart
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))
        st = self.status()
        self.assertEqual(st["status"], "sandboxed")

    def test_rollback_restart_also_fails_escalates(self):
        before = Path(self.cfg_path).read_bytes()
        # Rollback restart failing must escalate to AccessModeRollback while
        # still leaving the original config bytes on disk.
        with self.assertRaises(AccessModeRollback) as cm:
            self.enable(restart=lambda: False)
        self.assertEqual(cm.exception.code, "rollback-restart-failed")
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)

    def test_restart_exception_rolls_back(self):
        def restart():
            raise RuntimeError("boom")

        before = Path(self.cfg_path).read_bytes()
        # Persistently broken hook: config bytes ARE rolled back, but the
        # rollback restart cannot verify health, so the error escalates.
        with self.assertRaises(AccessModeRollback) as cm:
            self.enable(restart=restart)
        self.assertEqual(cm.exception.code, "rollback-restart-failed")
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)

    def test_repeated_enable_preserves_original_baseline(self):
        first = self.enable()
        second = self.enable()
        self.assertEqual(first["baseline"], second["baseline"])
        receipt = self.receipt()
        self.assertEqual(receipt["baseline"], first["baseline"])

    def test_managed_field_change_blocks_enable_and_restore(self):
        self.enable()
        cfg = self.read_config()
        self.pixel(cfg)["sandbox"]["mode"] = "all"
        self.write_config(cfg)
        with self.assertRaises(AccessModeRejected) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "managed-fields-changed")
        with self.assertRaises(AccessModeRejected) as cm:
            self.restore()
        self.assertEqual(cm.exception.code, "managed-fields-changed")
        st = self.status()
        self.assertEqual(st["status"], "unknown")
        self.assertIn("managed-fields-drift", st["reasons"])

    def test_stale_pending_receipt_recovers_after_crash(self):
        cfg = make_config()
        _, baseline = pam.__dict__["_helper"].enable(copy.deepcopy(cfg))
        # Simulate a crash after config replace but before receipt finalize.
        self.write_config(pam.__dict__["_helper"].enable(
            copy.deepcopy(cfg))[0])
        self.write_pending_receipt(baseline)
        result = self.enable()
        self.assertTrue(result["recovered_stale_apply"])
        self.assertEqual(result["baseline"], baseline)
        self.assertEqual(self.receipt()["baseline"], baseline)
        st = self.status()
        self.assertEqual(st["status"], "full-access")

    def test_stale_pending_receipt_recovers_before_crash(self):
        cfg = make_config()
        _, baseline = pam.__dict__["_helper"].enable(copy.deepcopy(cfg))
        self.write_pending_receipt(baseline)  # crashed before replace
        result = self.enable()
        self.assertTrue(result["recovered_stale_apply"])
        self.assertEqual(self.pixel(self.read_config())["sandbox"]["mode"],
                         "off")


class TestRestore(Harness):
    def test_roundtrip_byte_semantics(self):
        original = self.read_config()
        self.enable()
        result = self.restore()
        self.assertEqual(result, {"status": "sandboxed",
                                  "already_restored": False})
        self.assertEqual(self.read_config(), original)
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))
        st = self.status()
        self.assertEqual(st["status"], "sandboxed")
        self.assertFalse(st["managed"])

    def test_restore_preserves_unrelated_edits(self):
        self.enable()
        cfg = self.read_config()
        cfg["extra"] = {"unrelated": True}
        self.pixel(cfg)["model"] = "changed-model"
        self.pixel(cfg)["note"] = "later edit"
        self.write_config(cfg)
        self.restore()
        after = self.read_config()
        self.assertEqual(after["extra"], {"unrelated": True})
        self.assertEqual(self.pixel(after)["model"], "changed-model")
        self.assertEqual(self.pixel(after)["note"], "later edit")
        self.assertEqual(self.pixel(after)["sandbox"]["mode"], "all")
        self.assertEqual(self.pixel(after)["tools"]["exec"]["host"], "sandbox")
        self.assertIs(self.pixel(after)["tools"]["fs"]["workspaceOnly"], True)
        # And a full enable->restore on the edited config returns to it.
        baseline = self.enable()["baseline"]
        self.assertEqual(baseline["sandbox.mode"]["value"], "all")
        self.restore()
        after2 = self.read_config()
        self.assertEqual(after2, after)

    def test_restore_requires_managed_state(self):
        with self.assertRaises(AccessModeRejected) as cm:
            self.restore()
        self.assertEqual(cm.exception.code, "not-managed")

    def test_restore_restart_failure_rolls_back_to_enabled(self):
        self.enable()
        enabled = self.read_config()
        calls = []

        def restart():
            calls.append(1)
            return len(calls) >= 2  # first fails, rollback restart succeeds

        with self.assertRaises(AccessModeRejected) as cm:
            self.restore(restart=restart)
        self.assertEqual(cm.exception.code, "restart-failed-rolled-back")
        self.assertEqual(self.read_config(), enabled)
        self.assertEqual(self.receipt()["status"], "full-access")

    def test_restore_crash_recovery_already_restored(self):
        original = self.read_config()
        result = self.enable()
        # Simulate crash: fields restored on disk, receipt left behind.
        cfg, _ = pam.__dict__["_helper"].restore(self.read_config(),
                                                 result["baseline"])
        self.write_config(cfg)
        out = self.restore()
        self.assertTrue(out["already_restored"])
        self.assertEqual(self.read_config(), original)
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))

    def test_state_path_mismatch(self):
        self.enable()
        other = os.path.join(self.tmp, "other.json")
        shutil.copy(self.cfg_path, other)
        with self.assertRaises(AccessModeRejected) as cm:
            pam.restore_sandbox(other, self.state_dir,
                                validate_config=lambda p: True,
                                restart=lambda: True, check_no_active_run=lambda: False)
        self.assertEqual(cm.exception.code, "state-path-mismatch")


class TestPendingRestore(Harness):
    def pending(self, applied=True):
        original = self.read_config()
        enabled, baseline = pam._helper.enable(copy.deepcopy(original))
        self.write_pending_receipt(baseline)
        if applied:
            self.write_config(enabled)
        return original

    def test_restore_after_interrupted_enable(self):
        original = self.pending()
        calls = []
        self.restore(restart=lambda: calls.append("restart") or True)
        self.assertEqual(self.read_config(), original)
        self.assertEqual(calls, ["restart"])
        self.assertFalse(self.status()["managed"])

    def test_restore_before_config_replace_still_verifies_runtime(self):
        original = self.pending(applied=False)
        calls = []
        result = self.restore(restart=lambda: calls.append("restart") or True)
        self.assertTrue(result["already_restored"])
        self.assertEqual(self.read_config(), original)
        self.assertEqual(calls, ["restart"])
        self.assertFalse(self.status()["managed"])

    def test_restore_preserves_unrelated_changes(self):
        self.pending()
        cfg = self.read_config()
        cfg["agents"]["defaults"]["model"] = "owner-selected-model"
        self.write_config(cfg)
        self.restore()
        self.assertEqual(self.read_config()["agents"]["defaults"]["model"], "owner-selected-model")
        self.assertEqual(self.pixel(self.read_config())["sandbox"]["mode"], "all")

    def test_managed_field_drift_keeps_pending_receipt(self):
        self.pending()
        cfg = self.read_config()
        self.pixel(cfg)["tools"]["exec"]["ask"] = "on-miss"
        self.write_config(cfg)
        before = Path(self.cfg_path).read_bytes()
        with self.assertRaises(AccessModeRejected) as caught:
            self.restore()
        self.assertEqual(caught.exception.code, "managed-fields-changed")
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)
        self.assertEqual(self.receipt()["status"], "pending")

    def test_busy_or_failed_validation_preserves_pending_state(self):
        self.pending()
        before = Path(self.cfg_path).read_bytes()
        for kwargs in ({"check_no_active_run": lambda: True}, {"validate_config": lambda _: False}):
            with self.subTest(kwargs=tuple(kwargs)):
                with self.assertRaises(AccessModeRejected):
                    self.restore(**kwargs)
                self.assertEqual(Path(self.cfg_path).read_bytes(), before)
                self.assertEqual(self.receipt()["status"], "pending")

    def test_failed_restart_preserves_recovery_state(self):
        original = self.pending()
        calls = []
        def restart():
            calls.append(True)
            return len(calls) > 1
        with self.assertRaises(AccessModeRejected) as caught:
            self.restore(restart=restart)
        self.assertEqual(caught.exception.code, "restart-failed")
        self.assertEqual(self.read_config(), original)
        self.assertEqual(self.receipt()["status"], "pending")
        self.assertEqual(calls, [True])
        self.assertTrue(self.restore()["already_restored"])
        self.assertFalse(self.status()["managed"])

    def test_change_during_restart_preserves_pending_receipt(self):
        self.pending()
        def restart():
            cfg = self.read_config()
            cfg["owner_note"] = "concurrent change"
            self.write_config(cfg)
            return True
        with self.assertRaises(AccessModeRace) as caught:
            self.restore(restart=restart)
        self.assertEqual(caught.exception.code, "config-changed-during-apply")
        self.assertEqual(self.read_config()["owner_note"], "concurrent change")
        self.assertEqual(self.receipt()["status"], "pending")


class TestStatus(Harness):
    def test_sandboxed_full_access_and_unknown(self):
        st = self.status()
        self.assertEqual(st["status"], "sandboxed")
        self.assertEqual(st["reasons"], [])
        self.enable()
        self.assertEqual(self.status()["status"], "full-access")
        # Remove receipt behind the controller's back -> unknown.
        os.unlink(os.path.join(self.state_dir, "pixel-access-mode.json"))
        st = self.status()
        self.assertEqual(st["status"], "unknown")
        self.assertIn("managed-state-missing", st["reasons"])

    def test_unknown_shapes(self):
        os.unlink(self.cfg_path)
        self.assertEqual(self.status()["status"], "unknown")
        self.assertEqual(self.status()["reasons"], ["config-missing"])
        with open(self.cfg_path, "w") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write("{not json")
        st = self.status()
        self.assertEqual(st["status"], "unknown")
        self.assertEqual(st["reasons"], ["config-malformed"])
        cfg = make_config()
        del cfg["agents"]["list"][0]
        self.write_config(cfg)
        st = self.status()
        self.assertEqual(st["status"], "unknown")
        self.assertIn("no-pixel-agent", st["reasons"])

    def test_no_values_in_errors_or_status(self):
        secret = "s3cr3t-token-value"
        cfg = make_config()
        cfg["models"] = {"providers": {"x": {"apiKey": secret}}}
        self.write_config(cfg)
        st = self.status()
        self.assertNotIn(secret, json.dumps(st))
        with open(self.cfg_path, "w") as fh:
            fh.write('{"agents": "broken %s"}' % secret)
        with self.assertRaises(AccessModeError) as cm:
            self.enable()
        self.assertNotIn(secret, str(cm.exception))
        st = self.status()
        self.assertNotIn(secret, json.dumps(st))


class TestSecurity(Harness):
    def test_symlink_config_rejected(self):
        real = os.path.join(self.tmp, "real.json")
        shutil.copy(self.cfg_path, real)
        os.unlink(self.cfg_path)
        os.symlink(real, self.cfg_path)
        with self.assertRaises(AccessModeSecurity) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "symlink-file")

    def test_symlink_state_dir_rejected(self):
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target)
        os.rmdir(self.state_dir)
        os.symlink(target, self.state_dir)
        with self.assertRaises(AccessModeSecurity) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "symlink-dir")

    def test_insecure_config_mode_rejected(self):
        os.chmod(self.cfg_path, 0o666)
        with self.assertRaises(AccessModeSecurity) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "insecure-mode-file")
        os.chmod(self.cfg_path, 0o600)
        os.chmod(self.tmp, 0o777)
        with self.assertRaises(AccessModeSecurity) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "insecure-mode-dir")

    def test_nonregular_config_rejected(self):
        os.unlink(self.cfg_path)
        os.makedirs(self.cfg_path)
        with self.assertRaises(AccessModeSecurity) as cm:
            self.enable()
        self.assertEqual(cm.exception.code, "bad-type-file")

    def test_lock_contention(self):
        lock_path = os.path.join(self.state_dir, "apply.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaises(AccessModeRace) as cm:
                self.enable(lock_timeout=0.1)
            self.assertEqual(cm.exception.code, "lock-busy")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_config_changed_during_apply(self):
        real_read = pam._read_file
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) == 1:
                return real_read(path)
            cfg = make_config()
            cfg["tampered"] = True
            return json.dumps(cfg).encode()

        pam._read_file = flaky
        try:
            with self.assertRaises(AccessModeRace) as cm:
                self.enable()
            self.assertEqual(cm.exception.code, "config-changed-during-apply")
        finally:
            pam._read_file = real_read
        cfg = self.read_config()
        self.assertNotIn("tampered", cfg)
        self.assertEqual(self.pixel(cfg)["sandbox"]["mode"], "all")
        self.assertFalse(os.path.exists(
            os.path.join(self.state_dir, "pixel-access-mode.json")))

    def test_state_dir_permissions(self):
        st = os.stat(self.state_dir)
        self.assertEqual(st.st_mode & 0o777, 0o700)
        rp = os.path.join(self.state_dir, "pixel-access-mode.json")
        self.enable()
        self.assertEqual(os.stat(rp).st_mode & 0o777, 0o600)


class TestDefaultValidator(unittest.TestCase):
    def test_cli_diagnostics_cannot_expose_config_values(self):
        from types import SimpleNamespace
        result = SimpleNamespace(returncode=1, stdout="", stderr="Invalid value: private-api-key")
        with mock.patch.object(shutil, "which", return_value="/trusted/openclaw"), \
                mock.patch.object(pam.subprocess, "run", return_value=result):
            valid, reason = pam.default_validate_config("/staged/config.json")
        self.assertFalse(valid)
        self.assertNotIn("private-api-key", reason)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pam-validator-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_cli_validation(self):
        if not shutil.which("openclaw"):
            self.skipTest("openclaw CLI not installed")
        good = os.path.join(self.tmp, "good.json")
        with open(good, "w") as fh:
            json.dump({"agents": {"list": [{"id": "pixel"}]}}, fh)
        ok, reason = pam.default_validate_config(good)
        self.assertTrue(ok, reason)
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as fh:
            json.dump({"agents": {"list": [{"id": "pixel", "bogus": 1}]}}, fh)
        ok, reason = pam.default_validate_config(bad)
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_xdg_state_dir_default(self):
        if not shutil.which("openclaw"):
            self.skipTest("openclaw CLI not installed")
        cfg_path = os.path.join(self.tmp, "c.json")
        with open(cfg_path, "w") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump(make_config(), fh)
        xdg = os.path.join(self.tmp, "xdg-state")
        old = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = xdg
        try:
            pam.enable_full_access(cfg_path, None, confirmed=True,
                                   validate_config=lambda p: True,
                                   restart=lambda: True,
                                   check_no_active_run=lambda: False)
            expected = os.path.join(xdg, "ods-pixel-access-mode",
                                    "pixel-access-mode.json")
            self.assertTrue(os.path.exists(expected))
        finally:
            if old is None:
                del os.environ["XDG_STATE_HOME"]
            else:
                os.environ["XDG_STATE_HOME"] = old





class TestControllerSafety(Harness):
    def bytes_at(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def snapshot(self):
        return (self.bytes_at(self.cfg_path), self.bytes_at(pam._receipt_path(self.state_dir)))

    def pending(self):
        enabled, baseline = pam._helper.enable(self.read_config())
        self.write_config(enabled)
        self.write_pending_receipt(baseline)
        return baseline

    def test_restore_requires_both_live_hooks_without_mutation(self):
        self.enable()
        before = self.snapshot()
        for kwargs, code in [
            ({"restart": lambda: True}, "no-active-run-check"),
            ({"check_no_active_run": lambda: False}, "no-restart-hook"),
        ]:
            with self.subTest(code=code):
                with self.assertRaises(AccessModeRejected) as error:
                    pam.restore_sandbox(self.cfg_path, self.state_dir,
                                        validate_config=lambda path: True, **kwargs)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(self.snapshot(), before)

    def test_restore_busy_before_staging_preserves_config_and_receipt(self):
        self.enable()
        before = self.snapshot()
        calls = []
        with self.assertRaises(AccessModeRejected) as error:
            self.restore(check_no_active_run=lambda: True,
                         validate_config=lambda path: calls.append(path) or True,
                         restart=lambda: calls.append("restart") or True)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_restore_becoming_busy_during_validation_prevents_replace(self):
        self.enable()
        before = self.snapshot()
        busy = False
        calls = []
        def validate(path):
            nonlocal busy
            busy = True
            return True
        with self.assertRaises(AccessModeRejected) as error:
            self.restore(check_no_active_run=lambda: busy, validate_config=validate,
                         restart=lambda: calls.append("restart") or True)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(calls, [])

    def test_already_baseline_restore_requires_health_before_receipt_removal(self):
        baseline = self.enable()["baseline"]
        self.write_config(pam._helper.restore(self.read_config(), baseline)[0])
        before = self.snapshot()
        calls = []
        def unhealthy():
            calls.append(self.snapshot())
            return False
        with self.assertRaises(AccessModeRejected) as error:
            self.restore(restart=unhealthy)
        self.assertEqual(error.exception.code, "restart-failed")
        self.assertEqual(calls, [before])
        self.assertEqual(self.snapshot(), before)
        def healthy():
            calls.append(self.snapshot())
            return True
        result = self.restore(restart=healthy)
        self.assertTrue(result["already_restored"])
        self.assertEqual(calls, [before, before])
        self.assertFalse(os.path.exists(pam._receipt_path(self.state_dir)))

    def test_restart_new_active_run_retains_restore_receipt(self):
        self.enable()
        before_receipt = self.snapshot()[1]
        busy = False
        def restart():
            nonlocal busy
            busy = True
            return True
        with self.assertRaises(AccessModeRejected) as error:
            self.restore(check_no_active_run=lambda: busy, restart=restart)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(self.bytes_at(pam._receipt_path(self.state_dir)), before_receipt)
        self.assertEqual(self.pixel(self.read_config())["sandbox"]["mode"], "all")

    def test_pending_enable_busy_before_recovery_preserves_bytes(self):
        self.pending()
        before = self.snapshot()
        validations = []
        with self.assertRaises(AccessModeRejected) as error:
            self.enable(check_no_active_run=lambda: True,
                        validate_config=lambda path: validations.append(path) or True)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(validations, [])
        self.assertEqual(self.snapshot(), before)

    def test_pending_enable_busy_during_recovery_validation_preserves_bytes(self):
        self.pending()
        before = self.snapshot()
        busy = False
        def validate(path):
            nonlocal busy
            busy = True
            return True
        with self.assertRaises(AccessModeRejected) as error:
            self.enable(check_no_active_run=lambda: busy, validate_config=validate)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(self.snapshot(), before)

    def test_enable_busy_during_apply_validation_does_not_write_receipt(self):
        before = self.bytes_at(self.cfg_path)
        busy = False
        def validate(path):
            nonlocal busy
            busy = True
            return True
        with self.assertRaises(AccessModeRejected) as error:
            self.enable(check_no_active_run=lambda: busy, validate_config=validate)
        self.assertEqual(error.exception.code, "active-run")
        self.assertEqual(self.bytes_at(self.cfg_path), before)
        self.assertFalse(os.path.exists(pam._receipt_path(self.state_dir)))

    def test_pending_enable_apply_race_retains_recovery_receipt(self):
        baseline = self.pending()
        replace = pam._checked_replace
        calls = []
        def raced(*args):
            calls.append(args)
            if len(calls) == 2:
                raise AccessModeRace("config-changed-during-apply", "synthetic race")
            return replace(*args)
        pam._checked_replace = raced
        try:
            with self.assertRaises(AccessModeRace):
                self.enable()
        finally:
            pam._checked_replace = replace
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.receipt()["status"], "pending")
        self.assertEqual(self.receipt()["baseline"], baseline)

    def test_unknown_idle_result_fails_closed_without_secret_echo(self):
        self.pending()
        before = self.snapshot()
        for checker in [lambda: None, lambda: "private-value", lambda: 0]:
            with self.subTest(checker=checker):
                with self.assertRaises(AccessModeRejected) as error:
                    self.enable(check_no_active_run=checker)
                self.assertEqual(error.exception.code, "active-run-check-failed")
                self.assertNotIn("private-value", str(error.exception))
                self.assertEqual(self.snapshot(), before)

    def test_status_derives_agent_override_and_upstream_default(self):
        cases = [
            ("off", None, "unknown"),
            (None, None, "unknown"),
            (None, "all", "sandboxed"),
            ("off", "all", "unknown"),
            ("all", "off", "sandboxed"),
            (None, "non-main", "unknown"),
            ("non-main", "all", "unknown"),
            (None, "invalid-private-value", "unknown"),
        ]
        for agent_mode, default_mode, expected in cases:
            with self.subTest(agent=agent_mode, default=default_mode):
                cfg = make_config()
                if agent_mode is None:
                    self.pixel(cfg).pop("sandbox")
                else:
                    self.pixel(cfg)["sandbox"]["mode"] = agent_mode
                if default_mode is not None:
                    cfg["agents"]["defaults"]["sandbox"] = {"mode": default_mode}
                self.write_config(cfg)
                status = self.status()
                self.assertEqual(status["status"], expected)
                self.assertEqual(status["configured_status"], expected)
                self.assertIs(status["runtime_verified"], False)
                self.assertFalse(status["managed"])
                self.assertNotIn("invalid-private-value", json.dumps(status))

    def test_status_does_not_call_gateway_or_node_execution_sandboxed(self):
        for host in ["gateway", "node"]:
            for inherited in [False, True]:
                with self.subTest(host=host, inherited=inherited):
                    cfg = make_config()
                    if inherited:
                        self.pixel(cfg)["tools"]["exec"].pop("host")
                        cfg["tools"] = {"exec": {"host": host}}
                    else:
                        self.pixel(cfg)["tools"]["exec"]["host"] = host
                    self.write_config(cfg)
                    self.assertEqual(self.status()["status"], "unknown")

    def test_status_distinguishes_full_configuration_from_runtime_and_management(self):
        self.enable()
        status = self.status()
        self.assertEqual(status["configured_status"], "full-access")
        self.assertIs(status["runtime_verified"], False)
        os.unlink(pam._receipt_path(self.state_dir))
        for inherit_mode in [False, True]:
            if inherit_mode:
                cfg = self.read_config()
                self.pixel(cfg).pop("sandbox")
                self.write_config(cfg)
            status = self.status()
            self.assertEqual(status["status"], "unknown")
            self.assertEqual(status["configured_status"], "full-access")
            self.assertIn("managed-state-missing", status["reasons"])
            self.assertIs(status["runtime_verified"], False)


def _hijack_mkstemp(attacker_symlink_target):
    """Build a tempfile.mkstemp replacement that swaps the helper's private
    temp directory entry for an attacker-controlled file before the helper
    writes or stats it. The helper's original fd stays valid (its inode was
    unlinked), so the write path is undisturbed while the ENTRY the helper
    later renames is attacker-controlled. Pass None for a replacement
    regular file, or a target path for a symlink.
    """
    real_mkstemp = tempfile.mkstemp

    def hijack(*args, **kwargs):
        fd, tmp = real_mkstemp(*args, **kwargs)
        os.unlink(tmp)
        if attacker_symlink_target is None:
            afd, apath = real_mkstemp(prefix="attacker-",
                                      dir=kwargs.get("dir") or ".")
            try:
                os.write(afd, b"attacker-regular\n")
            finally:
                os.close(afd)
            os.rename(apath, tmp)
        else:
            os.symlink(attacker_symlink_target, tmp)
        return fd, tmp

    return hijack

class TestRollbackSentinel(Harness):
    def test_rollback_writer_refuses_symlink_swap(self):
        target = self.cfg_path
        original = Path(target).read_bytes()
        evil = os.path.join(self.tmp, "evil-target")
        with open(evil, "wb") as fh:
            fh.write(b"evil\n")
        # Attacker swapped the config path for a symlink after it was read.
        os.unlink(target)
        os.symlink(evil, target)
        with self.assertRaises(AccessModeRollback) as cm:
            pam._atomic_rollback_write(target, original, 0o600)
        self.assertEqual(cm.exception.code, "rollback-blocked-symlink")
        # The swapped-in symlink was not followed and nothing was written
        # through it.
        self.assertTrue(os.path.islink(target))
        self.assertEqual(Path(target).read_bytes(), b"evil\n")
        self.assertFalse([n for n in os.listdir(self.tmp)
                          if n.startswith(".ods-access-rollback-")])

    def test_rollback_writer_exclusive_private_mode_and_cleanup(self):
        real_replace = os.replace
        seen = {}

        def spy_replace(src, dst, *args, **kwargs):
            seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
            return real_replace(src, dst, *args, **kwargs)

        target = os.path.join(self.tmp, "cfg.json")
        with open(target, "wb") as fh:
            fh.write(b"before\n")

        with mock.patch("os.replace", side_effect=spy_replace):
            pam._atomic_rollback_write(target, b"after\n", 0o640)
        self.assertEqual(seen["mode"], 0o640)  # exact intended mode, no umask
        self.assertEqual(Path(target).read_bytes(), b"after\n")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)
        self.assertFalse([n for n in os.listdir(self.tmp)
                          if n.startswith(".ods-access-rollback-")])

    def test_rollback_temp_exclusive_and_cleaned_up(self):
        before = Path(self.cfg_path).read_bytes()
        mode_before = stat.S_IMODE(os.stat(self.cfg_path).st_mode)
        real = pam._atomic_rollback_write
        observed = []

        def spy(path, data, mode):
            observed.append(
                [n for n in os.listdir(os.path.dirname(path))
                 if n.startswith(".ods-access-rollback-")])
            real(path, data, mode)

        pam._atomic_rollback_write = spy
        try:
            calls = []

            def restart():
                calls.append(1)
                return len(calls) >= 2  # apply restart fails, rollback works

            with self.assertRaises(AccessModeRejected) as cm:
                self.enable(restart=restart)
            self.assertEqual(cm.exception.code, "restart-failed-rolled-back")
        finally:
            pam._atomic_rollback_write = real
        self.assertEqual(observed, [[]])  # exclusive create, no stale temp
        self.assertEqual(Path(self.cfg_path).read_bytes(), before)
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path).st_mode),
                         mode_before)
        self.assertFalse([n for n in os.listdir(self.tmp)
                          if n.startswith(".ods-access-rollback-")])

    def test_regression_temp_swapped_for_regular_file_blocks_before_rename(self):
        target = self.cfg_path
        original = Path(target).read_bytes()

        # Repaired helper: identity is captured from the owned config fd
        # before rename, and lstat(tmp) must match that identity as a
        # regular file with nlink 1. The attacker's replacement does not,
        # so the write is refused BEFORE the rename.
        with mock.patch.object(tempfile, "mkstemp",
                               side_effect=_hijack_mkstemp(None)):
            with self.assertRaises(AccessModeRollback) as cm:
                pam._atomic_rollback_write(target, original, 0o600)
        self.assertEqual(cm.exception.code, "rollback-blocked-sentinel")
        self.assertEqual(Path(target).read_bytes(), original)
        self.assertTrue(stat.S_ISREG(os.lstat(target).st_mode))
        self.assertFalse(os.path.islink(target))
        self.assertFalse([n for n in os.listdir(self.tmp)
                          if n.startswith(".ods-access-rollback-")])

    def test_regression_temp_swapped_for_symlink_blocks_before_rename(self):
        target = self.cfg_path
        original = Path(target).read_bytes()
        evil = os.path.join(self.tmp, "attacker-evil-target")
        with open(evil, "wb") as fh:
            fh.write(b"attacker-symlink\n")

        # Repaired helper: lstat(tmp) requires a regular file, nlink 1,
        # matching the identity captured on the owned fd; a symlink is
        # refused BEFORE the rename and the config path is never touched.
        with mock.patch.object(tempfile, "mkstemp",
                               side_effect=_hijack_mkstemp(evil)):
            with self.assertRaises(AccessModeRollback) as cm:
                pam._atomic_rollback_write(target, original, 0o600)
        self.assertEqual(cm.exception.code, "rollback-blocked-sentinel")
        self.assertEqual(Path(target).read_bytes(), original)
        self.assertFalse(os.path.islink(target))
        self.assertTrue(stat.S_ISREG(os.lstat(target).st_mode))
        self.assertFalse([n for n in os.listdir(self.tmp)
                          if n.startswith(".ods-access-rollback-")])

if __name__ == "__main__":
    unittest.main()

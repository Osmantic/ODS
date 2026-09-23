"""Adversarial tests for the helper-owned install/swap operation and the deployed-unit
path-allowlist contract in restore-migration-journal.py.

Covers correction #4 (prepared newPath/newEvidence + durable install/swap state machine)
and correction #8: every install/rollback crash boundary, prepared-new tamper, unexpected
live-object substitution, parent replacement, invalid unit path, idempotent retry, and
fsync/rename/unlink durability failures. Also covers correction #1: the deployed-unit path
allowlist is derived from the configured contract in scripts/configure.mjs and cannot drift.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_restore_migration_journal_deployment import (
    FakeJournalHarnessBase, helper, item, new_path, old_path, safe_dir, stage_new, write,
    CONTRACT, BACKUP, UNIT, FIXED_UNITS, FIXED_DESIRED,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "scripts" / "configure.mjs"

# Keys in configure.mjs that name installed unit files (the complete exact contract).
UNIT_CONFIG_KEYS = (
    "PIXEL_SYSTEMD_UNIT",
    "PIXEL_WEB_COURIER_UNIT",
    "PIXEL_SOURCE_BROKER_UNIT",
    "PIXEL_SOURCE_BROKER_TIMER",
    "PIXEL_SOURCE_ACTION_UNIT",
    "PIXEL_SOURCE_RECONCILE_UNIT",
    "PIXEL_SOURCE_DIRECT_UNIT",
    "PIXEL_SOURCE_DIRECT_PATH_UNIT",
    "PIXEL_OPS_BROKER_UNIT",
    "PIXEL_FRONTIER_BROKER_UNIT",
)


class DeployedUnitContractTests(unittest.TestCase):
    """Correction #1: the deployed-unit path allowlist must match configure.mjs exactly."""

    def test_allowlist_matches_configured_contract(self):
        source = CONFIGURE.read_text()
        configured = set()
        for key in UNIT_CONFIG_KEYS:
            m = re.search(re.escape(key) + r":\s*\"([^\"]+)\"", source)
            self.assertIsNotNone(m, f"{key} not found in configure.mjs")
            configured.add(m.group(1))
        self.assertEqual(
            configured, helper.DEPLOYED_UNIT_ALLOWLIST,
            "DEPLOYED_UNIT_ALLOWLIST drifted from the configured Pixel unit contract",
        )

    def test_allowlist_covers_complete_contract(self):
        self.assertIn("openclaw-gateway.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-web-courier.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-broker.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-broker.timer", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-action@.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-reconcile@.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-direct.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-source-direct.path", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-ops-broker.service", helper.DEPLOYED_UNIT_ALLOWLIST)
        self.assertIn("pixel-frontier-broker.service", helper.DEPLOYED_UNIT_ALLOWLIST)

    def test_quiescence_list_is_separate_and_validated(self):
        # The service quiescence list is independent of the path allowlist: it names the
        # units actually stopped/restarted, and every quiesced unit is a valid unit name
        # (allowlist member or dynamic pixel-work-* family).
        for unit in helper.QUIESCE_UNITS:
            self.assertIn(unit, helper.DEPLOYED_UNIT_ALLOWLIST)
        # A managed unit file (e.g. source action/reconcile/direct) can be on the allowlist
        # without being quiesced - the two lists are intentionally decoupled.
        self.assertNotIn("pixel-source-action@.service", helper.QUIESCE_UNITS)


class InstallCrashBoundaryTests(FakeJournalHarnessBase):
    """Every install/swap crash boundary is classifiable and resumable/rollback-safe."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def test_install_idempotent_before_any_mutation(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # First install performs the swap.
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        # Idempotent retry: final state already holds, install is a no-op success.
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(config).read_text(), "NEW")

    def test_install_resumes_after_crash_before_any_mutation(self):
        # Crash before any mutation: old still at path, newPath staged. Install must complete.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.assertEqual(Path(old_path(config, idx=0)).read_text(), "OLD")

    def test_install_resumes_after_crash_after_old_moved(self):
        # Crash after path->oldPath but before newPath->path (the new object never installed).
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        os.rename(config, old_path(config, idx=0))
        self.assertFalse(Path(config).exists())
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.assertEqual(Path(old_path(config, idx=0)).read_text(), "OLD")

    def test_install_resumes_after_crash_after_new_installed(self):
        # Crash after both renames: final state already holds; install verifies and completes.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        os.rename(config, old_path(config, idx=0))
        os.rename(new_path(config, idx=0), config)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")

    def test_install_had_old_zero_installs_new(self):
        dep = self._deploy()
        ws = str(dep / "config" / "fresh.json")
        items = [item("config", ws, 0, 0)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertFalse(Path(ws).exists())
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(ws).read_text(), "NEW")


class PreparedNewTamperTests(FakeJournalHarnessBase):
    """Correction #4: prepared-new tamper is detected at install and refused."""

    def test_prepared_new_tamper_rejected(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Tamper with the prepared new object before install.
        write(items[0]["newPath"], "TAMPERED")
        inst = self.fake.install(journal)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("tampered", inst.stderr)
        # The live old state is untouched and rollback-safe.
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))

    def test_prepared_new_substituted_object_rejected(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Replace the prepared new object with a different inode entirely.
        os.unlink(items[0]["newPath"])
        write(items[0]["newPath"], "SUBSTITUTED")
        inst = self.fake.install(journal)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("tampered", inst.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_prepared_new_deleted_rejected_with_zero_mutation(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Delete the prepared new object entirely after arm.
        os.unlink(items[0]["newPath"])
        inst = self.fake.install(journal)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("missing", inst.stderr)
        # Zero live mutation: old still at path, no oldPath created.
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))


class UnexpectedLiveObjectTests(FakeJournalHarnessBase):
    """The helper never deletes an unexpected live object merely because rollback was
    requested, and never installs over an unexpected object."""

    def test_install_refuses_unexpected_object_at_path(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # An unexpected object appears at path (does not match old evidence - not the old file).
        os.rename(config, old_path(config, idx=0))
        write(config, "UNEXPECTED")
        inst = self.fake.install(journal)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("unexpected object", inst.stderr)
        self.assertEqual(Path(config).read_text(), "UNEXPECTED")

    def test_rollback_refuses_to_delete_unexpected_live_object(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Substitute the live installed object with an unexpected one after install.
        os.unlink(config)
        write(config, "EVIL-SUBSTITUTE")
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("not the armed new object", rb.stderr)
        # The unexpected object is NOT deleted.
        self.assertEqual(Path(config).read_text(), "EVIL-SUBSTITUTE")


class InstallParentReplacementTests(FakeJournalHarnessBase):
    def test_install_parent_substitution_rejected(self):
        dep = self.base / "deploy"
        parent = safe_dir(dep / "config")
        config = str(write(parent / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        moved = self.base / "config-moved"
        os.rename(parent, moved)
        new_parent = safe_dir(parent)
        write(new_parent / "app.json", "EVIL")
        inst = self.fake.install(journal)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("parent changed", inst.stderr)
        self.assertEqual((new_parent / "app.json").read_text(), "EVIL")


class InvalidUnitPathTests(FakeJournalHarnessBase):
    """Correction #1: the allowlist covers the full contract; arbitrary units are refused."""

    def test_full_contract_unit_accepted(self):
        unit = str(write(self.fake.systemd / "pixel-source-action@.service", "x", mode=0o644))
        items = [item("unit", unit, 0, 1)]
        journal = self.fake.custody / "t.json"
        proc = self.fake.arm_deploy(journal, items)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_arbitrary_unit_rejected(self):
        bad = str(write(self.fake.systemd / "evil-arbitrary.service", "x", mode=0o644))
        items = [item("unit", bad, 0, 1)]
        proc = self.fake.arm_fail(self.fake.custody / "t.json", items)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("fixed Pixel unit contract", proc.stderr)


class ServicePrestateRestoreTests(FakeJournalHarnessBase):
    """Finding 2: rollback restores the exact pre-active/pre-enabled state bound at install,
    and never indiscriminately restarts/enables a unit that was inactive/disabled before."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def test_rollback_restores_exact_active_and_enabled_prestate(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        # Unit was active AND enabled before the migration.
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.reserve_capture_arm(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Install quiesced it: now inactive.
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        # Exact pre-active + pre-enabled state restored.
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())

    def test_rollback_never_restarts_inactive_disabled_unit(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        # Unit was inactive AND disabled before the migration: rollback must leave it that
        # way and never indiscriminately restart or enable it.
        self.fake.state.write_text("")
        self.fake.enabled_state.write_text("")
        self.assertEqual(self.fake.reserve_capture_arm(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertNotIn(UNIT, self.fake.enabled_state.read_text().splitlines())
        self.assertNotIn("start " + UNIT, self.fake.log.read_text() if self.fake.log.exists() else "")
        self.assertNotIn("enable " + UNIT, self.fake.log.read_text() if self.fake.log.exists() else "")

    def test_capture_rejects_indeterminate_state_before_mutation(self):
        # A unit in an unknown/failed active state (not active/inactive) fails closed at the
        # mutation-free capture step (BEFORE outer quiescence and any mutation).
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text("")
        for it in items:
            stage_new(it)
        token = self.fake.reserve(journal, CONTRACT, BACKUP)
        cap = self.fake.capture(journal, token, list(FIXED_UNITS), systemctl_fail="is-active " + UNIT)
        self.assertNotEqual(cap.returncode, 0)
        # No mutation happened: old still at path.
        self.assertEqual(Path(config).read_text(), "OLD")


class InstallQuiescenceTests(FakeJournalHarnessBase):
    """Preparation/arm is non-mutating; install is the mutation boundary that quiesces."""

    def test_preparation_and_arm_do_not_mutate_live_state(self):
        # reserve -> capture exact prestate -> verified quiesce -> arm (preparation) must
        # NEVER mutate deployment files: config stays OLD and no oldPath sibling is created.
        # (The prep capture/quiesce stop active services before arm, but the deployment
        # items are only swapped by install.)
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))

    def test_quiesce_happens_before_arm_and_install_swaps(self):
        # The prep quiesce (capture + verified quiesce, BEFORE arm) stops the active unit;
        # install is the deployment-mutation boundary that swaps the config candidate while
        # leaving the unit stopped for the private-root swap.
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        lines = self.fake.log.read_text().splitlines()
        self.assertTrue(any(line.split()[0] == "stop" for line in lines))
        # Quiesced during prep (before arm): unit stopped, but no deployment mutation yet.
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertEqual(Path(config).read_text(), "OLD")
        inst = self.fake.install(journal)
        self.assertEqual(inst.returncode, 0, inst.stderr)
        # Unit stays stopped; config candidate applied.
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertEqual(Path(config).read_text(), "NEW")

    def test_install_quiescence_failure_mutates_nothing(self):
        # Install re-quiesces and verifies the units inactive before any deployment
        # mutation. If a unit was re-activated after arm and its stop fails, nothing is
        # mutated (old still at path, no oldPath created).
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Re-activate the unit so install's re-quiesce must stop it (and fail).
        self.fake.state.write_text(UNIT + "\n")
        inst = self.fake.install(journal, systemctl_fail="stop " + UNIT)
        self.assertNotEqual(inst.returncode, 0)
        self.assertIn("could not be stopped", inst.stderr)
        # No live mutation: old still at path, no oldPath created, rollback-safe.
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))


class DurabilityFailureTests(FakeJournalHarnessBase):
    """Correction #3: rename/unlink/fsync failures leave conservative, resumable state."""

    def _arm_config(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        return journal, config

    def _read(self, journal):
        return json.loads((self.fake.custody / Path(journal).name).read_text())

    def test_rename_failure_leaves_install_resumable(self):
        journal, config = self._arm_config()
        real_rename = os.rename

        def failing_rename(src, dst, **kw):
            if src.startswith(".pixel-journal-"):
                return real_rename(src, dst, **kw)
            raise OSError(5, "simulated deployment rename failure")

        with mock.patch.object(helper, "_require_privileged", lambda: None), \
             mock.patch.object(helper, "_systemctl_read", lambda *a: "inactive"), \
             mock.patch.object(helper, "_systemctl", lambda *a: None), \
             mock.patch.object(helper.os, "rename", side_effect=failing_rename):
            with self.assertRaises(OSError):
                helper.cmd_install(str(self.fake.custody), str(journal))
        data = self._read(journal)
        self.assertNotEqual(data["deploymentItems"][0]["installProgress"], "complete")
        # Old state untouched; rollback is safe.
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_unlink_failure_leaves_rollback_nonpass(self):
        journal, config = self._arm_config()
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        real_unlink = os.unlink

        def failing_unlink(name, **kw):
            if str(name).startswith(".pixel-journal-"):
                return real_unlink(name, **kw)
            raise OSError(5, "simulated deployment unlink failure")

        with mock.patch.object(helper, "_require_privileged", lambda: None), \
             mock.patch.object(helper, "_systemctl_read", lambda *a: "inactive"), \
             mock.patch.object(helper, "_systemctl", lambda *a: None), \
             mock.patch.object(helper.os, "unlink", side_effect=failing_unlink):
            with self.assertRaises(SystemExit):
                helper.cmd_rollback(str(self.fake.custody), str(journal))
        data = self._read(journal)
        # Progress stays conservatively non-pass (not restored) and rolledBack stays False.
        self.assertFalse(data["rolledBack"])
        self.assertEqual(data["deploymentItems"][0]["progress"], "in-progress")

    def test_fsync_failure_leaves_rollback_nonpass_and_services_stopped(self):
        journal, config = self._arm_config()
        self.assertEqual(self.fake.install(journal).returncode, 0, self.fake.install(journal).stderr)
        self.assertEqual(Path(config).read_text(), "NEW")
        self.fake.state.write_text(UNIT + "\n")
        # Force the deployment mutation step to fail with a durability OSError.
        with mock.patch.object(helper, "_require_privileged", lambda: None), \
             mock.patch.object(helper, "_systemctl_read", lambda *a: "inactive"), \
             mock.patch.object(helper, "_systemctl", lambda *a: None), \
             mock.patch.object(
                 helper, "_restore_deployment_item", side_effect=OSError(5, "simulated fsync failure")
             ):
            with self.assertRaises(SystemExit):
                helper.cmd_rollback(str(self.fake.custody), str(journal))
        data = self._read(journal)
        self.assertFalse(data["rolledBack"])
        self.assertEqual(data["deploymentItems"][0]["progress"], "in-progress")
        # finalization is conservatively non-pass and the unit was not restarted.
        self.assertNotEqual(data["finalization"], "complete")


class RollbackStagedNewTests(FakeJournalHarnessBase):
    """Correction #3: rollback removes an uninstalled/staged newPath only when it exactly
    matches newEvidence, and hadOld=0 rollback never deletes an unexpected live object."""

    def test_rollback_removes_staged_newpath_after_crash_before_install(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Crash before any install: old still at path, newPath still staged. Rollback must
        # leave OLD at path and remove the staged newPath (it matches newEvidence).
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertFalse(os.path.exists(old_path(config, idx=0)))
        self.assertFalse(os.path.exists(items[0]["newPath"]))

    def test_rollback_refuses_unexpected_staged_newpath(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        # Substitute the staged newPath with an unexpected object (crash before install).
        os.unlink(items[0]["newPath"])
        write(items[0]["newPath"], "EVIL-STAGED")
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("staged newPath", rb.stderr)
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_rollback_had_old_zero_unexpected_live_object_refused(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        ws = str(dep / "config" / "fresh.json")
        items = [item("config", ws, 0, 0)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(ws).read_text(), "NEW")
        # Substitute the live installed object with an unexpected one.
        os.unlink(ws)
        write(ws, "EVIL-SUBSTITUTE")
        rb = self.fake.rollback(journal)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("not the armed new object", rb.stderr)
        self.assertEqual(Path(ws).read_text(), "EVIL-SUBSTITUTE")


class EarlyCommitTests(FakeJournalHarnessBase):
    """Correction #5: commit refuses when deployment items were never installed or tampered."""

    def test_commit_refused_before_install(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("never fully installed", cm.stderr)
        self.assertFalse(self.fake.inspect(journal)["committed"])

    def test_commit_refused_live_tamper_before_commit(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        # Tamper the live installed object after install.
        os.unlink(config)
        write(config, "TAMPERED")
        cm = self.fake.commit(journal)
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("no longer matches prepared evidence", cm.stderr)
        self.assertFalse(self.fake.inspect(journal)["committed"])


class PrivateRootCommitTests(FakeJournalHarnessBase):
    """Correction #6: the private-root swap refuses early/partial commit and never removes an
    unexpected destination object."""

    def _arm_root(self, journal, dest_text="OLD", temp_text="NEW", had_old=1):
        dest = self.base / "root"
        if had_old == 1:
            dest.mkdir()
            write(dest / "state.json", dest_text)
        token = self.fake.reserve(journal)
        old = str(self.base / ".pixel-restore-root-12345-0.old")
        temp = str(self.base / ".pixel-restore-root-12345-0.new")
        os.makedirs(temp)
        write(temp + "/state.json", temp_text)
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token,
                             CONTRACT, BACKUP, "1", str(dest), old, temp, str(had_old))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return dest, old, temp

    def test_commit_immediately_after_arm_refused(self):
        # Arm but never swap: destination still holds old, oldPath absent, temp present.
        self._arm_root(self.fake.custody / "t.json")
        cm = self.fake.commit(self.fake.custody / "t.json")
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("old snapshot is not at oldPath", cm.stderr)
        self.assertFalse(self.fake.inspect(self.fake.custody / "t.json")["committed"])

    def test_commit_partial_swap_refused(self):
        # Only the destination swap without preserving the old snapshot (oldPath absent,
        # destination now holds the prepared temporary object).
        dest, old, temp = self._arm_root(self.fake.custody / "t.json")
        import shutil
        shutil.rmtree(str(dest))
        os.rename(temp, str(dest))
        cm = self.fake.commit(self.fake.custody / "t.json")
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("old snapshot is not at oldPath", cm.stderr)
        self.assertFalse(self.fake.inspect(self.fake.custody / "t.json")["committed"])

    def test_commit_refuses_unexpected_destination(self):
        # Complete swap but the destination is then substituted with an unexpected object of
        # a different type (a regular file, which can never match the prepared directory
        # evidence - avoids any inode-reuse ambiguity).
        dest, old, temp = self._arm_root(self.fake.custody / "t.json")
        import shutil
        os.rename(str(dest), old)
        os.rename(temp, str(dest))
        shutil.rmtree(str(dest))
        Path(dest).write_text("EVIL-FILE", encoding="utf-8")
        os.chmod(dest, 0o600)
        cm = self.fake.commit(self.fake.custody / "t.json")
        self.assertNotEqual(cm.returncode, 0)
        self.assertIn("does not match the prepared temporary evidence", cm.stderr)
        self.assertFalse(self.fake.inspect(self.fake.custody / "t.json")["committed"])

    def test_rollback_refuses_unexpected_destination(self):
        # hadOld=0: swap then substitute destination with a different-type object; rollback
        # must fail closed and keep the unexpected object.
        dest, old, temp = self._arm_root(self.fake.custody / "t.json", had_old=0)
        import shutil
        os.rename(temp, str(dest))
        shutil.rmtree(str(dest))
        Path(dest).write_text("EVIL-FILE", encoding="utf-8")
        os.chmod(dest, 0o600)
        rb = self.fake.rollback(self.fake.custody / "t.json")
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("not the prepared temporary object", rb.stderr)
        self.assertEqual(Path(dest).read_text(), "EVIL-FILE")


class CleanupTruthBoundaryTests(FakeJournalHarnessBase):
    """Correction #7: a cleanup-failed journal write failure reports conservatively without
    claiming a durable state it could not record."""

    def test_cleanup_failed_journal_write_failure_reports_conservatively(self):
        import contextlib
        import io
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.fake.arm_deploy(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        # Force cleanup to fail: replace the oldPath file with a directory.
        op = old_path(config, idx=0)
        os.unlink(op)
        os.mkdir(op)
        real_write = helper._write_journal

        def failing_write(custody_fd, basename, value):
            if value.get("cleanup") == "failed":
                raise OSError("simulated journal write failure")
            return real_write(custody_fd, basename, value)

        buf = io.StringIO()
        with mock.patch.object(helper, "_write_journal", side_effect=failing_write):
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as ctx:
                    helper.cmd_commit(str(self.fake.custody), str(journal))
        self.assertEqual(ctx.exception.code, 1)
        out = buf.getvalue()
        self.assertIn("cleanup-failed", out)
        self.assertIn('"cleanup":"pending"', out)
        self.assertNotIn('"cleanup":"failed"', out)
        # The already-committed marker is preserved durably; the durable cleanup state is
        # still pending (the failed write never recorded "failed").
        data = json.loads((self.fake.custody / journal.name).read_text())
        self.assertTrue(data["committed"])
        self.assertEqual(data["cleanup"], "pending")


if __name__ == "__main__":
    unittest.main()

class ServiceDesiredFinalizeTests(FakeJournalHarnessBase):
    """Issue 5: the reviewed serviceDesired poststate is bound and applied exactly by the
    privileged finalize command (daemon-reload + enable/start/disable/stop + verify), never
    a blind restart or a swallowed daemon-reload. Issue 3: prestate is captured BEFORE outer
    quiescence and carried through arm."""

    def _deploy(self):
        dep = self.base / "deploy"
        safe_dir(dep / "config")
        return dep

    def _arm_desired(self, journal, items, desired, units=FIXED_UNITS, deep_units=()):
        for it in items:
            stage_new(it)
        all_units = list(units) + list(deep_units)
        token = self.fake.reserve(journal, CONTRACT, BACKUP)
        cap = self.fake.capture(journal, token, all_units, CONTRACT, BACKUP)
        self.assertEqual(cap.returncode, 0, cap.stderr)
        q = self.fake.quiesce(journal, token, CONTRACT, BACKUP)
        self.assertEqual(q.returncode, 0, q.stderr)
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": items, "serviceDesired": desired}),
                        encoding="utf-8")
        os.chmod(spec, 0o600)
        return self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                             BACKUP, "0", *all_units, str(spec))

    def test_finalize_applies_desired_active_enabled_in_order(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        # Prestate: inactive+disabled; desired: active+enabled.
        self.fake.state.write_text("")
        self.fake.enabled_state.write_text("")
        desired = dict(FIXED_DESIRED, **{UNIT: {"enabled": True, "active": True}})
        self.assertEqual(self._arm_desired(journal, items, desired).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Capture happened BEFORE install (is-active/is-enabled logged before any stop).
        self.assertEqual(self.fake.log.read_text().splitlines()[0].startswith("is-active"), True)
        fn = self.fake.finalize(journal)
        self.assertEqual(fn.returncode, 0, fn.stderr)
        lines = self.fake.log.read_text().splitlines()
        # daemon-reload then enable then start, with final is-active/is-enabled verification.
        dr = next(i for i, l in enumerate(lines) if l.startswith("daemon-reload"))
        en = next(i for i, l in enumerate(lines) if l == "enable " + UNIT)
        st = next(i for i, l in enumerate(lines) if l == "start " + UNIT)
        self.assertLess(dr, en)
        self.assertLess(en, st)
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())
        info = self.fake.inspect(journal)
        self.assertEqual(info["finalization"], "complete")

    def test_finalize_disables_stops_previously_active_courier(self):
        # Issue 5: a previously active+enabled courier that 4.2 disables must be disabled and
        # stopped (never left running), and the desired state is applied exactly.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        desired = dict(FIXED_DESIRED, **{UNIT: {"enabled": False, "active": False}})
        self.assertEqual(self._arm_desired(journal, items, desired).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        fn = self.fake.finalize(journal)
        self.assertEqual(fn.returncode, 0, fn.stderr)
        lines = self.fake.log.read_text().splitlines()
        # disable must be issued (not just left alone), and the unit ends inactive+disabled.
        self.assertIn("disable " + UNIT, lines)
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertNotIn(UNIT, self.fake.enabled_state.read_text().splitlines())

    def test_finalize_failure_keeps_armed_journal_for_rollback(self):
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text("")
        self.fake.enabled_state.write_text("")
        desired = dict(FIXED_DESIRED, **{UNIT: {"enabled": True, "active": True}})
        self.assertEqual(self._arm_desired(journal, items, desired).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        fn = self.fake.finalize(journal, systemctl_fail="enable " + UNIT)
        self.assertNotEqual(fn.returncode, 0)
        self.assertIn("service-failed", fn.stdout)
        info = self.fake.inspect(journal)
        self.assertEqual(info["finalization"], "failed")

    def test_apply_service_map_fails_desired_true_not_found(self):
        # Requirement 4: if desired enabled or active is true and a unit is not-found,
        # finalization must fail closed; only a desired false/false not-found is skipped.
        value = {"units": [UNIT]}
        with mock.patch.object(helper, "_require_privileged", lambda: None), \
             mock.patch.object(helper, "_systemctl", lambda *a: None), \
             mock.patch.object(helper, "_systemctl_read", return_value="not-found"):
            with self.assertRaises(subprocess.CalledProcessError):
                helper._apply_service_map(value, {UNIT: {"enabled": True, "active": False}})
            with self.assertRaises(subprocess.CalledProcessError):
                helper._apply_service_map(value, {UNIT: {"enabled": False, "active": True}})
            # Not-found + desired false/false is skipped (no raise, no transition).
            helper._apply_service_map(value, {UNIT: {"enabled": False, "active": False}})

    def test_finalize_restores_dynamic_deep_work_prestate(self):
        # Requirement 4/2: dynamic deep-work units have no deployment desired override and
        # return to their captured prestate after successful finalization.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        deep = "pixel-work-alpha.service"
        self.fake.state.write_text(deep + "\n")
        self.fake.enabled_state.write_text(deep + "\n")
        desired = dict(FIXED_DESIRED, **{UNIT: {"enabled": True, "active": True}})
        self.assertEqual(self._arm_desired(journal, items, desired, deep_units=[deep]).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        fn = self.fake.finalize(journal)
        self.assertEqual(fn.returncode, 0, fn.stderr)
        # dynamic deep-work returns to its captured prestate (active+enabled).
        self.assertIn(deep, self.fake.state.read_text().splitlines())
        self.assertIn(deep, self.fake.enabled_state.read_text().splitlines())

    def test_finalize_then_rollback_restores_exact_prestate(self):
        # Requirement 4: after a successful finalize (desired state applied), a later
        # rollback must restore the exact captured prestate (not the desired state) after
        # restoring the files/current link.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        desired = dict(FIXED_DESIRED, **{UNIT: {"enabled": False, "active": False}})
        self.assertEqual(self._arm_desired(journal, items, desired).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        # finalize disabled+stopped the previously active+enabled unit.
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertNotIn(UNIT, self.fake.enabled_state.read_text().splitlines())
        rb = self.fake.rollback(journal)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        # rollback restores exact captured prestate (active+enabled) and old file bytes.
        self.assertEqual(Path(config).read_text(), "OLD")
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())

    def test_capture_records_active_prestate_before_any_stop(self):
        # Issue 3: capture records the exact prestate (active+enabled) BEFORE quiescence and
        # carries it through arm; a post-stop snapshot would wrongly record it inactive.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.reserve_capture_arm(journal, items).returncode, 0)
        info = self.fake.inspect(journal)
        self.assertEqual(info["servicePrestate"][UNIT], {"active": True, "enabled": True})
        # The affected-unit set includes every fixed service exactly once.
        self.assertEqual(set(info["servicePrestate"].keys()), set(FIXED_UNITS))
        # The first systemctl calls must be the mutation-free capture reads (before any stop).
        self.assertEqual(self.fake.log.read_text().splitlines()[0].startswith("is-active"), True)

    def test_outer_flow_ordering_capture_stop_arm_install_finalize_commit(self):
        # Issue 10: the production call graph order is reserve/capture active prestate ->
        # stop -> arm -> install -> private roots -> desired service finalize -> commit. The
        # prestate capture MUST precede the outer stop (never a post-stop snapshot) and the
        # desired finalize MUST follow install (never a blind restart).
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text("")
        desired = dict(FIXED_DESIRED)
        for it in items:
            stage_new(it)
        # 1. reserve
        token = self.fake.reserve(journal, CONTRACT, BACKUP)
        # 2. capture exact active prestate BEFORE any stop
        cap = self.fake.capture(journal, token, list(FIXED_UNITS))
        self.assertEqual(cap.returncode, 0, cap.stderr)
        # 3. verified helper quiesce (the production outer stop) - stops active units
        q = self.fake.quiesce(journal, token, CONTRACT, BACKUP)
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertNotIn(UNIT, self.fake.state.read_text().splitlines())
        # 4. arm carrying prestate + desired
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": items, "serviceDesired": desired}),
                        encoding="utf-8")
        os.chmod(spec, 0o600)
        arm = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT,
                            BACKUP, "0", *FIXED_UNITS, str(spec))
        self.assertEqual(arm.returncode, 0, arm.stderr)
        info = self.fake.inspect(journal)
        self.assertEqual(info["servicePrestate"][UNIT], {"active": True, "enabled": False})
        # 5. install (deployment swap)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(Path(config).read_text(), "NEW")
        # 6. desired service finalize
        fn = self.fake.finalize(journal)
        self.assertEqual(fn.returncode, 0, fn.stderr)
        # 7. commit
        cm = self.fake.commit(journal)
        self.assertEqual(cm.returncode, 0, cm.stderr)
        info = self.fake.inspect(journal)
        self.assertTrue(info["committed"])
        self.assertEqual(info["finalization"], "complete")
        # Call-order evidence: capture reads happen before the outer stop; finalize's
        # daemon-reload/enable/start happen after install's quiesce stop.
        lines = self.fake.log.read_text().splitlines()
        cap_active = lines.index("is-active " + UNIT)
        cap_enabled = lines.index("is-enabled " + UNIT)
        stop_idx = lines.index("stop " + UNIT)
        self.assertLess(cap_active, stop_idx)
        self.assertLess(cap_enabled, stop_idx)
        dr = next(i for i, l in enumerate(lines) if l.startswith("daemon-reload"))
        self.assertGreater(dr, stop_idx)
        self.assertGreater(lines.index("enable " + UNIT), stop_idx)
        self.assertGreater(lines.index("start " + UNIT), stop_idx)

    def test_abort_restores_exact_prestate_after_outer_stop(self):
        # Issue 3/4: after the outer stop, an early abort must restore the exact captured
        # prestate (active+enabled), never leave the unit down.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        for it in items:
            stage_new(it)
        token = self.fake.reserve(journal, CONTRACT, BACKUP)
        cap = self.fake.capture(journal, token, list(FIXED_UNITS))
        self.assertEqual(cap.returncode, 0, cap.stderr)
        # Outer stop (unit now inactive) - abort must restore it.
        subprocess.run([str(self.fake.fake / "systemctl"), "stop", UNIT], check=True)
        ab = self.fake.abort(journal, token)
        self.assertEqual(ab.returncode, 0, ab.stderr)
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())
        self.assertEqual(Path(config).read_text(), "OLD")

    def test_rollback_quiesce_failure_restores_exact_prestate(self):
        # Issue 4: a quiesce failure during rollback restores the exact captured prestate
        # (active+enabled), not an indiscriminate leave-down state.
        dep = self._deploy()
        config = str(write(dep / "config" / "app.json", "OLD"))
        items = [item("config", config, 0, 1)]
        journal = self.fake.custody / "t.json"
        self.fake.state.write_text(UNIT + "\n")
        self.fake.enabled_state.write_text(UNIT + "\n")
        self.assertEqual(self.fake.reserve_capture_arm(journal, items).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        # Re-activate the unit so rollback's pre-mutation quiesce must stop it (and fail).
        self.fake.state.write_text(UNIT + "\n")
        rb = self.fake.rollback(journal, systemctl_fail="stop " + UNIT)
        self.assertNotEqual(rb.returncode, 0)
        self.assertIn("could not be stopped", rb.stderr)
        # Exact prestate restored despite the stop failure.
        self.assertIn(UNIT, self.fake.state.read_text().splitlines())
        self.assertIn(UNIT, self.fake.enabled_state.read_text().splitlines())


class ServiceDesiredContractTests(unittest.TestCase):
    """Finding 5: the reviewed serviceDesired of a new deployment transaction must EXACTLY
    equal the full fixed QUIESCE_UNITS set - no omitted fixed key, no extra key, no dynamic
    deep-work key, and no key absent from the affected units."""

    def test_service_desired_missing_fixed_key_rejected(self):
        desired = {u: {"enabled": False, "active": False} for u in FIXED_UNITS if u != UNIT}
        with self.assertRaises(SystemExit):
            helper._check_service_desired(desired, require_full_fixed=True)

    def test_service_desired_extra_key_rejected(self):
        desired = dict(FIXED_DESIRED)
        desired["ssh.service"] = {"enabled": False, "active": False}
        with self.assertRaises(SystemExit):
            helper._check_service_desired(desired, require_full_fixed=True)

    def test_service_desired_dynamic_key_rejected(self):
        # Dynamic deep-work units must never appear in serviceDesired - they inherit only
        # their captured prestate.
        desired = dict(FIXED_DESIRED)
        desired["pixel-work-alpha.service"] = {"enabled": True, "active": True}
        with self.assertRaises(SystemExit):
            helper._check_service_desired(desired, require_full_fixed=True)

    def test_service_desired_key_not_in_units_rejected(self):
        # A deployment journal whose affected units omit a fixed desired key must be refused.
        value = {
            "schemaVersion": 1, "kind": "pixel-restore-migration-journal",
            "backupSha256": BACKUP, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "committed": False, "cleanup": "armed", "rolledBack": False,
            "finalization": "armed", "contractRoots": [], "destinations": [],
            "oldPaths": [], "temporaryPaths": [], "hadOld": [], "units": list(FIXED_UNITS)[1:],
            "rollbackProgress": [], "oldEvidence": [],
            "deploymentItems": [{"kind": "config", "path": "/a/b", "oldPath": "/a/.x.old",
                                 "newPath": "/a/.x.new", "hadOld": 0,
                                 "evidence": None, "newEvidence": None,
                                 "parent": {"type": "dir", "dev": 1, "ino": 1,
                                            "uid": 0, "gid": 0, "mode": 0o700},
                                 "progress": "pending", "installProgress": "pending"}],
            "deploymentSpecSha256": "0" * 64,
            "servicePrestate": {u: {"active": False, "enabled": False} for u in FIXED_UNITS[1:]},
            "serviceDesired": FIXED_DESIRED,
        }
        with self.assertRaises(SystemExit):
            helper._validate_journal(value)


class QuiesceExactStateTests(unittest.TestCase):
    """Finding 3: quiesce stops only ACTIVE units, leaves inactive/not-found alone, rejects
    failed/unknown/activating/deactivating/empty, verifies EXACT inactive/not-found after
    stopping, and restores the exact captured prestate on ANY stop or verification failure."""

    def _prestate(self):
        return {u: {"active": True, "enabled": True} for u in FIXED_UNITS}

    def test_quiesce_leaves_absent_optional_units_alone(self):
        optional = set(FIXED_UNITS) - {UNIT}
        stopped = set()
        calls = []

        def fake_read(*a):
            unit = a[1]
            if unit in optional:
                return "not-found"
            if unit == UNIT:
                return "inactive" if UNIT in stopped else "active"
            return "inactive"

        def fake_systemctl(*a):
            if a[0] == "stop":
                stopped.add(a[1])
            calls.append(" ".join(a))

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl):
            helper._quiesce_active_units(list(FIXED_UNITS), self._prestate())
        # Only the active gateway is stopped; not-found optional units are never stopped.
        self.assertEqual(calls, ["stop " + UNIT])

    def test_quiesce_rejects_failed_or_unknown_active_state(self):
        for bad in ("failed", "unknown", "activating", "deactivating", ""):
            with mock.patch.object(helper, "_require_privileged", lambda: None),                  mock.patch.object(helper, "_systemctl_read", lambda *a, b=bad: b),                  mock.patch.object(helper, "_systemctl", lambda *a: None),                  mock.patch.object(helper, "_restore_exact_or_legacy"):
                with self.assertRaises(SystemExit):
                    helper._quiesce_active_units(list(FIXED_UNITS), self._prestate())

    def test_quiesce_stop_failure_after_another_unit_stopped_restores(self):
        def fake_read(*a):
            return "active"

        def fake_systemctl(*a):
            if a[0] == "stop" and a[1] == "pixel-web-courier.service":
                raise subprocess.CalledProcessError(1, a)

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl),              mock.patch.object(helper, "_restore_exact_or_legacy") as restore:
            with self.assertRaises(SystemExit):
                helper._quiesce_active_units(list(FIXED_UNITS), self._prestate())
        # A stop failure after another unit was stopped must restore the exact prestate.
        restore.assert_called_once()

    def test_quiesce_verify_still_active_restores_exact_prestate(self):
        # is-active still returns "active" after stop (verify-still-active) -> the
        # SystemExit must NOT bypass restoration.
        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", lambda *a: "active"),              mock.patch.object(helper, "_systemctl", lambda *a: None),              mock.patch.object(helper, "_restore_exact_or_legacy") as restore:
            with self.assertRaises(SystemExit):
                helper._quiesce_active_units(list(FIXED_UNITS), self._prestate())
        restore.assert_called_once()


class ApplyServiceMapExactStateTests(unittest.TestCase):
    """Finding 4: _apply_service_map verifies EXACT raw states. Quiet booleans would falsely
    treat failed/masked/static/unknown as inactive/disabled - every other raw state fails
    closed, and a not-found unit is only acceptable as a desired false/false absence."""

    def _value(self):
        return {"units": [UNIT]}

    def test_apply_service_map_rejects_failed_active_state(self):
        def fake_read(*a):
            return "failed" if a[0] == "is-active" else "disabled"

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl", lambda *a: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read):
            with self.assertRaises(subprocess.CalledProcessError):
                helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})

    def test_apply_service_map_rejects_masked_or_static_enabled(self):
        for bad in ("masked", "static", "unknown"):
            with mock.patch.object(helper, "_require_privileged", lambda: None),                  mock.patch.object(helper, "_systemctl", lambda *a: None),                  mock.patch.object(helper, "_systemctl_read", lambda *a, b=bad: b if a[0] == "is-enabled" else "inactive"):
                with self.assertRaises(subprocess.CalledProcessError):
                    helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})

    def test_apply_service_map_desired_true_not_found_fails(self):
        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl", lambda *a: None),              mock.patch.object(helper, "_systemctl_read", return_value="not-found"):
            with self.assertRaises(subprocess.CalledProcessError):
                helper._apply_service_map(self._value(), {UNIT: {"enabled": True, "active": False}})
            with self.assertRaises(subprocess.CalledProcessError):
                helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": True}})

    def test_apply_service_map_disabled_but_active_stops(self):
        stopped = []
        calls = []

        def fake_read(*a):
            if a[0] == "is-active":
                return "active" if not stopped else "inactive"
            return "disabled"

        def fake_systemctl(*a):
            calls.append(" ".join(a))
            if a[0] == "stop":
                stopped.append(a[1])

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl):
            helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})
        # A unit that is active but desired inactive must be explicitly stopped.
        self.assertIn("stop " + UNIT, calls)

    def test_apply_service_map_exact_absence_skipped(self):
        calls = []

        def fake_systemctl(*a):
            calls.append(" ".join(a))

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", return_value="not-found"),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl):
            helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})
        # Absent unit + desired false/false is a legitimate no-op: only daemon-reload ran.
        self.assertEqual(calls, ["daemon-reload"])

    def test_apply_service_map_not_found_enabled_but_active_stops(self):
        # enabled not-found + active active, desired false/false: the active dimension must
        # still explicitly stop the running unit (the enabled not-found must not skip it).
        stopped = []
        calls = []

        def fake_read(*a):
            if a[0] == "is-enabled":
                return "not-found"
            return "active" if not stopped else "inactive"

        def fake_systemctl(*a):
            calls.append(" ".join(a))
            if a[0] == "stop":
                stopped.append(a[1])

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl):
            helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})
        self.assertIn("stop " + UNIT, calls)

    def test_apply_service_map_enabled_but_not_found_active_disables(self):
        # enabled enabled + active not-found, desired false/false: the enabled dimension must
        # still explicitly disable the running-enabled unit (the active not-found must not
        # skip it).
        disabled = []
        calls = []

        def fake_read(*a):
            if a[0] == "is-enabled":
                return "enabled" if not disabled else "disabled"
            return "not-found"

        def fake_systemctl(*a):
            calls.append(" ".join(a))
            if a[0] == "disable":
                disabled.append(a[1])

        with mock.patch.object(helper, "_require_privileged", lambda: None),              mock.patch.object(helper, "_systemctl_read", side_effect=fake_read),              mock.patch.object(helper, "_systemctl", side_effect=fake_systemctl):
            helper._apply_service_map(self._value(), {UNIT: {"enabled": False, "active": False}})
        self.assertIn("disable " + UNIT, calls)

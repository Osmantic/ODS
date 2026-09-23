"""Focused tests for folding deployment items into their owning private root.

A deployment item that overlaps EXACTLY one authenticated private root is folded into that
root: it is composed into the root's non-live restore staging tree before the whole-root
copy/swap, recorded in the journal as folded (owning rootIndex + relative subpath), verified
as composed live state at commit, and rolled back by the owning root swap (never a second
live write inside a swapped root). Items that overlap no root stay standalone on the
journaled install path. The cross-domain overlap guard is NOT weakened: ambiguous multi-root,
inverse, traversal, unsafe-type, and symlink-ancestor cases are still refused.

Covers the real C1/C2/C4 collision classes, exact mode preservation, ambiguity rejection,
tamper, failure before intake, failure after intake, hard-kill/recovery, commit
verification, and rollback.
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.test_restore_migration_journal_deployment import (
    FakeJournalHarnessBase, helper, item, new_path, old_path, safe_dir, stage_new, write,
    FIXED_UNITS, CONTRACT, BACKUP, FIXED_DESIRED,
)


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FoldedHarnessBase(FakeJournalHarnessBase):
    """Builds an armed transaction with private-root destinations + deployment items."""

    def _root(self, name, files=()):
        root = self.base / name
        safe_dir(root)
        for rel, content, mode in files:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            write(p, content, mode=mode)
        return root

    def file_item(self, path, idx=0, pid=3, had_old=1, mode=0o700, content="NEW"):
        """A folded/standalone present file item carrying a real sha256 bind and mode."""
        newp = new_path(str(path), pid=pid, idx=idx)
        write(newp, content, mode=mode)
        return {"kind": "config", "path": str(path),
                "oldPath": old_path(str(path), pid=pid, idx=idx), "newPath": newp,
                "hadOld": had_old, "sha256": sha256_of(newp), "target": None}

    def _owner_patches(self):
        """Pin the invoking owner to the real euid/gid so in-process fold/cleanup helper
        calls are hermetic with respect to ambient sudo state (SUDO_UID/SUDO_GID are not
        trusted inside the non-root test harness)."""
        return (
            mock.patch.object(helper, "_invoking_uid", return_value=os.geteuid()),
            mock.patch.object(helper, "_invoking_gid", return_value=os.getegid()),
        )

    def arm(self, journal, items, roots, units=FIXED_UNITS, prestage=True):
        """Reserve/capture/quiesce then arm with the given private-root destinations.

        ``roots`` is a list of (destination_path, had_old). Each root's prepared temporary
        path is a fresh owner-private directory. A newPath already staged by ``file_item`` is
        preserved (so its exact prepared mode survives); otherwise a missing newPath is
        staged. Malformed/bad items are left untouched so the failure under test is the arm
        classification/invariant rejection, not staging.
        """
        if prestage:
            for it in items:
                np = it.get("newPath", "")
                if np.startswith(str(self.base) + "/") and not os.path.lexists(np):
                    stage_new(it)
        destinations, old_paths, temp_paths, had_olds = [], [], [], []
        for idx, (dest, had_old) in enumerate(roots):
            destinations.append(dest)
            old_paths.append(old_path(dest, pid=12345, idx=idx))
            temp_paths.append(new_path(dest, pid=12345, idx=idx))
            had_olds.append(str(had_old))
            safe_dir(temp_paths[-1])
        token = self.fake._reserve_capture_quiesce(journal, list(units), CONTRACT, BACKUP)
        spec = self.fake._spec(items)
        return self.fake.run(
            "arm", str(self.fake.custody), str(journal), token, CONTRACT, BACKUP,
            str(len(destinations)), *destinations, *old_paths, *temp_paths, *had_olds,
            *units, str(spec),
        )

    def _journal(self, journal):
        return json.loads(journal.read_text())

    def _compose_folded(self, journal, root, temp):
        """Simulate the orchestrator's fold: compose folded candidates into the temp root."""
        data = self._journal(journal)
        for it in data["deploymentItems"]:
            if not it.get("folded"):
                continue
            rel = it["relative"]
            target = Path(temp) / rel if rel else Path(temp)
            if target is not Path(temp):
                safe_dir(target.parent)
            if it["kind"] == "symlink":
                os.symlink(it["target"], target)
            elif it["sha256"] is None:
                if os.path.lexists(target):
                    os.unlink(target)
            else:
                shutil.copyfile(it["newPath"], target)
                os.chmod(target, it["mode"])
        return data

    def _swap_root(self, root):
        dest = str(root)
        old = old_path(dest, pid=12345, idx=0)
        temp = new_path(dest, pid=12345, idx=0)
        os.rename(dest, old)
        os.rename(temp, dest)


class FoldClassificationTests(FoldedHarnessBase):
    """The real C1/C2/C4 collision classes fold; ambiguous/inverse are refused."""

    def test_exact_file_root_overlap_folds(self):
        # C1: a deployment item that IS a single-file private root folds with relative "".
        folded, idx, rel = helper._deployment_root_overlap(
            "/home/u/.config/pixel/gateway.env", ["/home/u/.config/pixel/gateway.env"])
        self.assertTrue(folded)
        self.assertEqual(idx, 0)
        self.assertEqual(rel, "")

    def test_nested_present_file_folds_and_preserves_mode(self):
        # C2: a nested present file inside the root folds, preserving the exact prepared mode.
        root = self._root("ws", [("scripts/browse.sh", "OLD", 0o700)])
        p = root / "scripts" / "browse.sh"
        it = self.file_item(p, had_old=1, mode=0o700)
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self._journal(journal)["deploymentItems"][0]
        self.assertTrue(rec["folded"])
        self.assertEqual(rec["relative"], "scripts/browse.sh")
        self.assertEqual(rec["mode"], 0o700)

    def test_nested_present_symlink_folds(self):
        root = self._root("ws")
        link = root / "current"
        os.symlink("releases/4.3.0", link)
        it = {"kind": "symlink", "path": str(link),
              "oldPath": old_path(str(link), pid=7, idx=0),
              "newPath": new_path(str(link), pid=7, idx=0),
              "hadOld": 1, "target": "releases/4.3.0", "sha256": None}
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self._journal(journal)["deploymentItems"][0]
        self.assertTrue(rec["folded"])
        self.assertEqual(rec["relative"], "current")
        self.assertEqual(rec["target"], "releases/4.3.0")

    def test_desired_absent_nested_item_folds(self):
        # C4: a desired-absent nested item folds with sha256 None (absent in composed root).
        root = self._root("ws", [("scripts/xfeed.sh", "OLD", 0o700)])
        p = root / "scripts" / "xfeed.sh"
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self._journal(journal)["deploymentItems"][0]
        self.assertTrue(rec["folded"])
        self.assertIsNone(rec["sha256"])

    def test_standalone_item_not_folded(self):
        dep = self.base / "dep"
        safe_dir(dep / "install")
        cur = dep / "install" / "current"
        os.symlink("releases/3.2.2", cur)
        it = {"kind": "symlink", "path": str(cur),
              "oldPath": old_path(str(cur), pid=5, idx=0),
              "newPath": new_path(str(cur), pid=5, idx=0),
              "hadOld": 1, "target": "releases/3.2.2", "sha256": None}
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self._journal(journal)["deploymentItems"][0]
        self.assertNotIn("folded", rec)

    def test_ambiguous_multi_root_refused(self):
        # A path overlapping two distinct private roots is refused (never folded). With nested
        # destinations the pure classification must reject the ambiguity directly.
        r1 = "/home/u/openclaw"
        r2 = "/home/u/openclaw/codex"
        with self.assertRaises(SystemExit):
            helper._deployment_root_overlap(r2 + "/sub/file", [r1, r2])

    def test_inverse_fold_refused(self):
        root = self._root("ws")
        it = item("workspace", str(self.base), 0, 1)  # item is an ancestor of the root
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ancestor of a private root", proc.stderr)

    def test_arm_refuses_folded_symlink_ancestor(self):
        # A folded item whose path has a symlink ancestor inside its owning private root is
        # refused at arm (symlink escape) and is never folded.
        root = self._root("ws")
        elsewhere = self.base / "elsewhere"
        safe_dir(elsewhere)
        os.symlink(str(elsewhere), root / "sub")
        p = root / "sub" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("symlink ancestor", proc.stderr)


class FoldCommitVerificationTests(FoldedHarnessBase):
    """Commit verifies the composed live state of folded items (content + mode)."""

    def _arm_folded_file(self):
        root = self._root("ws", [("scripts/browse.sh", "OLD", 0o700)])
        p = root / "scripts" / "browse.sh"
        it = self.file_item(p, had_old=1, mode=0o700, content="#!/bin/sh\necho hi\n")
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        temp = new_path(str(root), pid=12345, idx=0)
        self._compose_folded(journal, root, temp)
        return journal, p, root

    def test_commit_verifies_composed_live_state(self):
        journal, p, root = self._arm_folded_file()
        self._swap_root(root)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0, self.fake.finalize(journal).stderr)
        self.assertEqual(self.fake.commit(journal).returncode, 0, self.fake.commit(journal).stderr)
        self.assertEqual(Path(p).read_text(), "#!/bin/sh\necho hi\n")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o700)

    def test_commit_refuses_folded_tamper(self):
        journal, p, root = self._arm_folded_file()
        self._swap_root(root)
        write(p, "#!/bin/sh\necho TAMPERED\n", mode=0o700)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        self.assertNotEqual(self.fake.commit(journal).returncode, 0)

    def test_commit_refuses_folded_mode_tamper(self):
        journal, p, root = self._arm_folded_file()
        self._swap_root(root)
        os.chmod(p, 0o600)  # mode tamper (prepared 0700)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        self.assertEqual(self.fake.finalize(journal).returncode, 0)
        self.assertNotEqual(self.fake.commit(journal).returncode, 0)

    def test_folded_root_mismatch_refused(self):
        # Tampering the folded item's owning root index so it no longer matches the pure
        # classification is refused by the cross-domain guard before any mutation.
        root = self._root("ws", [("scripts/browse.sh", "OLD", 0o700)])
        p = root / "scripts" / "browse.sh"
        it = self.file_item(p, had_old=1, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [it], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["deploymentItems"][0]["rootIndex"] = 5  # no longer the owning root
        journal.write_text(json.dumps(data))
        self.assertNotEqual(self.fake.install(journal).returncode, 0)
        self.assertNotEqual(self.fake.rollback(journal).returncode, 0)


class FoldRollbackTests(FoldedHarnessBase):
    """Rollback of folded items is covered by the owning whole-root swap."""

    def test_rollback_restores_owning_root(self):
        root = self._root("ws", [("scripts/browse.sh", "OLD", 0o700)])
        p = root / "scripts" / "browse.sh"
        it = self.file_item(p, had_old=1, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [it], [(str(root), 1)]).returncode, 0)
        self.assertEqual(self.fake.install(journal).returncode, 0)
        temp = new_path(str(root), pid=12345, idx=0)
        self._compose_folded(journal, root, temp)
        self._swap_root(root)
        self.assertEqual(self.fake.rollback(journal).returncode, 0, self.fake.rollback(journal).stderr)
        self.assertEqual(Path(p).read_text(), "OLD")
        self.assertTrue(Path(str(root)).is_dir())


class FoldIntakeRecoveryTests(FoldedHarnessBase):
    """Requirement 3: no pre-arm validation failure creates newPath; journal durably owns
    intake; an interrupted intake is deterministically cleanable by rollback."""

    def test_no_prearm_validation_failure_creates_newpath(self):
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        # A standalone present item whose newPath is NOT pre-staged (arm must not create it
        # when a pure-invariant failure on the bad item precedes intake).
        good = {"kind": "config", "path": str(dep / "config" / "app.json"),
                "oldPath": old_path(str(dep / "config" / "app.json"), pid=3, idx=0),
                "newPath": new_path(str(dep / "config" / "app.json"), pid=3, idx=0),
                "hadOld": 0}
        bad = {"kind": "config", "path": "/etc/../escape", "oldPath": "/etc/x.old",
               "newPath": "/etc/x.new", "hadOld": 0}
        journal = self.fake.custody / "t.json"
        # bad first: its pure path validation fails before any intake of `good`.
        proc = self.arm(journal, [bad, good], [(str(root), 1)], prestage=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(good["newPath"]))

    def test_rollback_cleans_intaken_newpath_after_interrupted_arm(self):
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        good = item("config", str(dep / "config" / "app.json"), 0, 0)
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [good], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        journal.write_text(json.dumps(data))
        self.assertEqual(self.fake.rollback(journal).returncode, 0, self.fake.rollback(journal).stderr)
        self.assertFalse(os.path.exists(good["newPath"]))
        self.assertFalse(os.path.exists(good["path"]))

    def test_install_refuses_pending_intake(self):
        # The armed journal durably owns a pending standalone intake; install refuses while
        # armIntakeComplete is false (no newPath may be installed from an unarmed intake).
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        good = item("config", str(dep / "config" / "app.json"), 0, 0)
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [good], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        journal.write_text(json.dumps(data))
        self.assertNotEqual(self.fake.install(journal).returncode, 0)

    def test_commit_refuses_pending_intake(self):
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        good = item("config", str(dep / "config" / "app.json"), 0, 0)
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [good], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        journal.write_text(json.dumps(data))
        self.assertNotEqual(self.fake.commit(journal).returncode, 0)


class FoldSubcommandTests(FoldedHarnessBase):
    """The privileged `fold` subcommand composes folded items into the restore staging tree."""

    def test_fold_composes_nested_file_preserving_mode(self):
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "browse.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = self.file_item(p, had_old=1, mode=0o700, content="NEWSCRIPT")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, 0o700)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        spec = self.fake._spec([it])
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stage_rel = str(live_root).lstrip("/")
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "NEWSCRIPT")
        self.assertEqual(stat.S_IMODE(os.stat(composed).st_mode), 0o700)

    def test_fold_removes_desired_absent(self):
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "xfeed.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        xfeed = restore_stage / stage_rel / "scripts" / "xfeed.sh"
        safe_dir(xfeed.parent)
        write(xfeed, "OLD", mode=0o700)
        spec = self.fake._spec([it])
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.lexists(xfeed))

    def test_fold_refuses_ambiguous(self):
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "browse.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = self.file_item(p, had_old=1, mode=0o700)
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        spec = self.fake._spec([it])
        # Two destinations that both contain the item path -> ambiguous fold refused.
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage),
                             str(live_root), str(p))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ambiguous", proc.stderr)

    def test_fold_refuses_symlink_ancestor_compose(self):
        # A symlink component inside the restore staging tree would escape the composed root;
        # the privileged fold refuses to descend through it (symlink-ancestor refusal).
        live_root = self.base / "workspace"
        p = live_root / "sub" / "file"
        safe_dir(p.parent)
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, 0o700)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        # The compose tree mirrors the destination with its leading "/" removed.
        root_rel = Path(str(live_root).lstrip("/"))
        safe_dir(restore_stage / root_rel.parent)
        safe_dir(restore_stage / root_rel)
        os.symlink(str(self.base / "elsewhere"), restore_stage / root_rel / "sub")
        spec = self.fake._spec([it])
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)

    def test_rollback_cleans_pending_intake_newpath_matching_spec(self):
        # Interrupted arm (armIntakeComplete false) with a PENDING standalone item whose
        # newPath exists and exactly matches the prepared spec bind: rollback removes it.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Rewrite the item as pending (intake not yet completed) and flip armIntakeComplete.
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        data["deploymentItems"][0]["newEvidence"] = {
            "type": "intake-pending", "sha256": it["sha256"], "target": None}
        journal.write_text(json.dumps(data))
        self.assertEqual(self.fake.rollback(journal).returncode, 0, self.fake.rollback(journal).stderr)
        self.assertFalse(os.path.exists(it["newPath"]))
        self.assertFalse(os.path.exists(str(p)))

    def test_rollback_refuses_indeterminate_pending_newpath(self):
        # A pending item's newPath that does NOT match the prepared spec bind is indeterminate
        # and refused (retained for manual recovery), never a best-effort deletion.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        data["deploymentItems"][0]["newEvidence"] = {
            "type": "intake-pending", "sha256": it["sha256"], "target": None}
        journal.write_text(json.dumps(data))
        # Tamper the newPath so it no longer matches the spec bind.
        write(it["newPath"], "PARTIAL")
        self.assertNotEqual(self.fake.rollback(journal).returncode, 0)
        self.assertTrue(os.path.exists(it["newPath"]))



if __name__ == "__main__":
    unittest.main()


class RepairedFoldBlockersTests(FoldedHarnessBase):
    """Regression tests for the reviewed-commit release blockers.

    Covers (a) replacing an existing non-directory file/symlink in a POPULATED restore
    stage (exact file-root and nested), (b) applying + verifying the exact invoking owner
    (uid/gid) and prepared mode, (c) desired-absent under a missing ancestor, (d) refusal of
    directory/multilink targets, (e) full pure validation before any newPath, and (f)
    deterministic cleanup + reserved-state restoration on ordinary post-journal intake
    failure.
    """

    def _single_file_root(self, name="openclaw.json", content="OLD", mode=0o600):
        """A true C1 exact-file private root under etc/."""
        p = self.base / "etc" / name
        safe_dir(p.parent)
        write(p, content, mode=mode)
        return p

    def _fold_run(self, it, destination, restore_stage, mode=0o600):
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, mode)
        spec = self.fake._spec([it])
        return self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(destination))

    def test_fold_replaces_existing_exact_root_file(self):
        # C1: the restore stage already contains the OLD single-file root extracted from the
        # backup. Fold must replace it (not refuse) with the new prepared content + mode.
        p = self._single_file_root()
        it = self.file_item(p, had_old=1, mode=0o600, content="NEWROOT")
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        write(restore_stage / root_rel, "OLD", mode=0o600)
        proc = self._fold_run(it, p, restore_stage, mode=0o600)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        composed = restore_stage / root_rel
        self.assertEqual(composed.read_text(), "NEWROOT")
        self.assertEqual(stat.S_IMODE(os.stat(composed).st_mode), 0o600)
        self.assertEqual(os.stat(composed).st_uid, os.geteuid())
        self.assertEqual(os.stat(composed).st_gid, os.getegid())

    def test_fold_replaces_existing_nested_file(self):
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "browse.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = self.file_item(p, had_old=1, mode=0o700, content="NEWSCRIPT")
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        safe_dir(restore_stage / stage_rel / "scripts")
        write(restore_stage / stage_rel / "scripts" / "browse.sh", "OLD", mode=0o700)
        proc = self._fold_run(it, live_root, restore_stage, mode=0o700)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "NEWSCRIPT")
        self.assertEqual(stat.S_IMODE(os.stat(composed).st_mode), 0o700)

    def test_fold_replaces_existing_nested_symlink(self):
        live_root = self.base / "install"
        p = live_root / "releases" / "current"
        safe_dir(p.parent)
        os.symlink("3.2.2", p)
        it = {"kind": "symlink", "path": str(p), "oldPath": old_path(str(p), pid=7, idx=0),
              "newPath": new_path(str(p), pid=7, idx=0), "hadOld": 1, "target": "4.3.0"}
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        safe_dir(restore_stage / stage_rel / "releases")
        os.symlink("3.2.2", restore_stage / stage_rel / "releases" / "current")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        spec = self.fake._spec([it])
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        composed = restore_stage / stage_rel / "releases" / "current"
        self.assertEqual(os.readlink(composed), "4.3.0")
        self.assertEqual(os.lstat(composed).st_uid, os.geteuid())
        self.assertEqual(os.lstat(composed).st_gid, os.getegid())

    def test_fold_corrects_wrong_owner_pre_existing_target(self):
        # Probe that detects missing ownership handling: a pre-existing restore-stage target
        # owned by a WRONG group must be replaced with an object owned by the invoking user
        # (uid + gid), never preserved or root-owned. Hermetic: the wrong-owner pre-existing
        # condition is simulated by instrumenting the identity syscall (_stat_nofollow) for
        # the pre-replace target, and the correction is proven by the composed object's real
        # owner/mode/content - no chown privilege is required.
        p = self._single_file_root()
        it = self.file_item(p, had_old=1, mode=0o600, content="NEWROOT")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, 0o600)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        old = restore_stage / root_rel
        write(old, "OLD", mode=0o600)
        wrong_gid = os.getegid() + 1
        real_stat = helper._stat_nofollow
        wrong_owner = {"applied": False}

        def stat_wrong_owner_once(parent_fd, basename):
            st = real_stat(parent_fd, basename)
            if not wrong_owner["applied"] and basename == os.path.basename(root_rel):
                wrong_owner["applied"] = True
                return os.stat_result((
                    st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                    wrong_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime))
            return st

        expected_sha = sha256_of(migration_stage / cand)
        stage_fd = os.open(str(restore_stage), os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self._owner_patches()[0], self._owner_patches()[1], \
                 mock.patch.object(helper, "_stat_nofollow", side_effect=stat_wrong_owner_once):
                helper._fold_compose_file(stage_fd, root_rel, str(migration_stage), cand,
                                          expected_sha, 0o600)
        finally:
            os.close(stage_fd)
        self.assertTrue(wrong_owner["applied"])
        composed = restore_stage / root_rel
        st = os.stat(composed)
        self.assertEqual(st.st_uid, os.geteuid())
        self.assertEqual(st.st_gid, os.getegid())
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(composed.read_text(), "NEWROOT")

    def test_fold_refuses_directory_target(self):
        # A directory at the composed target must be refused, never recursed.
        p = self._single_file_root()
        it = self.file_item(p, had_old=1, mode=0o600)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        safe_dir(restore_stage / root_rel)  # target is a directory
        proc = self._fold_run(it, p, restore_stage)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("directory", proc.stderr)

    def test_fold_refuses_multilink_target(self):
        # A multi-link target file must be refused (dropping one name would break the other).
        p = self._single_file_root()
        it = self.file_item(p, had_old=1, mode=0o600)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        target = restore_stage / root_rel
        write(target, "OLD", mode=0o600)
        os.link(target, restore_stage / Path(root_rel).parent / "alias")
        proc = self._fold_run(it, p, restore_stage)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("multi-link", proc.stderr)

    def test_fold_desired_absent_missing_ancestor_is_noop(self):
        # A desired-absent nested item whose ancestor is missing in the restore stage is
        # already satisfied and must not fail.
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "xfeed.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        # The ancestor scripts dir is NOT present in the restore stage.
        stage_rel = str(live_root).lstrip("/")
        spec = self.fake._spec([it])
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_fold_desired_absent_existing_directory_refused(self):
        # A desired-absent item whose composed target is a directory is refused (never
        # recursed / never rm -rf).
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "xfeed.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        safe_dir(restore_stage / stage_rel / "scripts")
        safe_dir(restore_stage / stage_rel / "scripts" / "xfeed.sh")  # directory
        spec = self.fake._spec([it])
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("directory", proc.stderr)

    def test_fold_desired_absent_fifo_refused_and_remains(self):
        # A desired-absent item whose composed target is a FIFO must be refused (never
        # unlinked) by the same replaceability/type guard used for present compose, and the
        # FIFO must remain untouched.
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "xfeed.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        safe_dir(restore_stage / stage_rel / "scripts")
        fifo = restore_stage / stage_rel / "scripts" / "xfeed.sh"
        os.mkfifo(fifo)
        spec = self.fake._spec([it])
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unsafe object type", proc.stderr)
        self.assertTrue(stat.S_ISFIFO(os.lstat(fifo).st_mode))

    def test_fold_desired_absent_multilink_refused_and_remains(self):
        # A desired-absent item whose composed target is a multi-link file must be refused
        # (dropping one name would break the other hard link), and both names must remain.
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "xfeed.sh"
        safe_dir(p.parent)
        write(p, "OLD", mode=0o700)
        it = {"kind": "workspace", "path": str(p),
              "oldPath": old_path(str(p), pid=6, idx=0),
              "newPath": new_path(str(p), pid=6, idx=0),
              "hadOld": 1, "sha256": None, "target": None}
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        scripts = restore_stage / stage_rel / "scripts"
        safe_dir(scripts)
        target = scripts / "xfeed.sh"
        write(target, "OLD", mode=0o700)
        alias = scripts / "xfeed.alias"
        os.link(target, alias)
        spec = self.fake._spec([it])
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("multi-link", proc.stderr)
        self.assertTrue(os.path.lexists(target))
        self.assertTrue(os.path.lexists(alias))

    def test_arm_malformed_duplicate_path_creates_zero_newpaths(self):
        # A malformed duplicate-path spec must fail the COMPLETE pure validation BEFORE the
        # first durable journal write and before any newPath is created.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        a = {"kind": "config", "path": str(p), "oldPath": old_path(str(p), pid=3, idx=0),
             "newPath": new_path(str(p), pid=3, idx=0), "hadOld": 0}
        b = dict(a)
        journal = self.fake.custody / "t.json"
        # Stage the identical sibling so the no-stage present-file check passes and the
        # duplicate-path pure validation (not an absent sibling) is the failure under test.
        proc = self.arm(journal, [a, b], [(str(root), 1)], prestage=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unique", proc.stderr)
        data = json.loads(journal.read_text())
        self.assertEqual(data["kind"], "pixel-restore-migration-reserved")

    def test_intake_failure_after_armed_journal_restores_reserved_and_cleans(self):
        # Ordinary post-journal intake failure (second item's candidate missing) must clean
        # the first item's exact owned newPath and restore the reserved marker so the wrapper
        # can abort safely and restore the exact service prestate.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        a_path = dep / "config" / "a.json"
        b_path = dep / "config" / "b.json"
        a = {"kind": "config", "path": str(a_path), "oldPath": old_path(str(a_path), pid=3, idx=0),
             "newPath": new_path(str(a_path), pid=3, idx=0), "hadOld": 0}
        # A no-stage standalone file with an absent sibling now fails BEFORE the journal
        # write, so the post-journal intake-failure path is exercised with a standalone
        # SYMLINK whose sibling is missing (symlink intake is unaffected by that check).
        b = {"kind": "symlink", "path": str(b_path), "oldPath": old_path(str(b_path), pid=4, idx=0),
             "newPath": new_path(str(b_path), pid=4, idx=0), "hadOld": 0, "target": "releases/4.3.0"}
        stage_new(a)  # only a's newPath is staged; b's is missing -> b's intake fails
        # Pre-state the units active so capture records them and abort can prove prestate
        # recovery.
        self.fake.state.write_text("\n".join(FIXED_UNITS) + "\n")
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        spec = self.fake._spec([a, b])
        destinations = [str(root)]
        old_paths = [old_path(str(root), pid=12345, idx=0)]
        temp_paths = [new_path(str(root), pid=12345, idx=0)]
        safe_dir(temp_paths[0])
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT, BACKUP,
                             "1", *destinations, *old_paths, *temp_paths, "1", *FIXED_UNITS, str(spec))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.lexists(a["newPath"]))
        self.assertFalse(os.path.lexists(b["newPath"]))
        data = json.loads(journal.read_text())
        self.assertEqual(data["kind"], "pixel-restore-migration-reserved")
        # abort must succeed and restore the exact service prestate (units active again).
        ab = self.fake.abort(journal, token, CONTRACT, BACKUP)
        self.assertEqual(ab.returncode, 0, ab.stderr)
        active = self.fake.state.read_text().split()
        for u in FIXED_UNITS:
            self.assertIn(u, active)

    def test_intake_failure_first_item_still_reserved(self):
        # Failure on the FIRST intake point (single present item whose candidate is missing)
        # must also restore the reserved marker (nothing created, wrapper abort-safe).
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        a = {"kind": "config", "path": str(p), "oldPath": old_path(str(p), pid=3, idx=0),
             "newPath": new_path(str(p), pid=3, idx=0), "hadOld": 0}
        journal = self.fake.custody / "t.json"
        token = self.fake._reserve_capture_quiesce(journal, list(FIXED_UNITS), CONTRACT, BACKUP)
        spec = self.fake._spec([a])
        destinations = [str(root)]
        old_paths = [old_path(str(root), pid=12345, idx=0)]
        temp_paths = [new_path(str(root), pid=12345, idx=0)]
        safe_dir(temp_paths[0])
        proc = self.fake.run("arm", str(self.fake.custody), str(journal), token, CONTRACT, BACKUP,
                             "1", *destinations, *old_paths, *temp_paths, "1", *FIXED_UNITS, str(spec))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.lexists(a["newPath"]))
        data = json.loads(journal.read_text())
        self.assertEqual(data["kind"], "pixel-restore-migration-reserved")
        self.assertEqual(self.fake.abort(journal, token, CONTRACT, BACKUP).returncode, 0)

    def test_pending_cleanup_refuses_wrong_mode(self):
        # Interrupted-intake cleanup must NOT delete a same-content newPath with the wrong
        # mode (owner/mode bound durably before intake and verified during cleanup).
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [it], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        data["deploymentItems"][0]["newEvidence"] = {
            "type": "intake-pending", "sha256": it["sha256"], "target": None,
            "uid": os.geteuid(), "gid": os.getegid(), "mode": 0o700}
        journal.write_text(json.dumps(data))
        write(it["newPath"], "NEW", mode=0o600)  # wrong mode (owner-only, not 0700)
        self.assertNotEqual(self.fake.rollback(journal).returncode, 0)
        self.assertTrue(os.path.exists(it["newPath"]))

    def test_pending_cleanup_refuses_wrong_owner(self):
        # Interrupted-intake cleanup must NOT delete a same-content newPath with the wrong
        # owner gid. Hermetic: the newPath is created with the exact bound mode + content so
        # the ONLY mismatch is the owner gid, simulated via the identity syscall
        # (_stat_nofollow) - no chown privilege is required.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [it], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        data["deploymentItems"][0]["newEvidence"] = {
            "type": "intake-pending", "sha256": it["sha256"], "target": None,
            "uid": os.geteuid(), "gid": os.getegid(), "mode": 0o700}
        journal.write_text(json.dumps(data))
        write(it["newPath"], "NEW", mode=0o700)  # exact bound mode + content; owner only wrong
        wrong_gid = os.getegid() + 1
        real_stat = helper._stat_nofollow

        def stat_wrong_gid(parent_fd, basename):
            st = real_stat(parent_fd, basename)
            return os.stat_result((
                st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                wrong_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime))

        with mock.patch.object(helper, "_stat_nofollow", side_effect=stat_wrong_gid):
            with self.assertRaises(SystemExit):
                helper._cleanup_intake_newpaths(data)
        self.assertTrue(os.path.exists(it["newPath"]))

    def test_pending_cleanup_matches_bound_owner_mode(self):
        # The same pending newPath with the correct owner + mode + content IS cleaned.
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = self.file_item(p, had_old=0, mode=0o700, content="NEW")
        journal = self.fake.custody / "t.json"
        self.assertEqual(self.arm(journal, [it], [(str(root), 1)]).returncode, 0)
        data = self._journal(journal)
        data["armIntakeComplete"] = False
        data["deploymentItems"][0]["newEvidence"] = {
            "type": "intake-pending", "sha256": it["sha256"], "target": None,
            "uid": os.geteuid(), "gid": os.getegid(), "mode": 0o700}
        journal.write_text(json.dumps(data))
        self.assertEqual(self.fake.rollback(journal).returncode, 0, self.fake.rollback(journal).stderr)
        self.assertFalse(os.path.exists(it["newPath"]))


class HardenedFoldSafetyTests(FoldedHarnessBase):
    """In-process unit regressions for the hardened fold compose/recovery code.

    Directly exercises the privileged fold helpers (with the sudo owner derivation stubbed to
    the real euid/gid) to prove the complete-write loop, source hashing during the stable
    read, pre-rename verification (so an unverified candidate never replaces the
    authenticated old stage target), created-temp identity tracking with safe cleanup,
    parent-mode preservation under sudo semantics, and indeterminate-cleanup surfacing.
    """

    def _fold_setup(self, rel, content="NEW", mode=0o600, old="OLD", old_mode=0o600):
        """Build restore_stage + migration_stage for an in-process fold compose.

        Returns (restore, migration_stage, target). The stage-relative ``rel`` carries no
        leading slash (the root's leading "/" is already stripped by the orchestrator).
        """
        restore = self.base / "restore-stage"
        safe_dir(restore)
        parent = restore / os.path.dirname(rel) if os.path.dirname(rel) else restore
        if os.path.dirname(rel):
            safe_dir(parent)
        target = restore / rel
        if old is not None:
            write(target, old, mode=old_mode)
        mig = self.base / "migration-stage"
        safe_dir(mig)
        write(mig / os.path.basename(rel), content, mode=mode)
        return restore, mig, target

    def test_fold_file_complete_write_handles_short_returns(self):
        # os.write returning a short count must not be treated as a full chunk write; the
        # complete-write loop retries until every byte is durable.
        restore, mig, target = self._fold_setup("etc/app.json", content="NEW", mode=0o600,
                                                old="OLD", old_mode=0o600)
        cand = os.path.basename("etc/app.json")
        expected_sha = hashlib.sha256(b"NEW").hexdigest()
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        real_write = os.write
        state = {"calls": 0}

        def short_write(fd, data):
            state["calls"] += 1
            if state["calls"] == 1 and len(data) > 1:
                half = len(data) // 2
                return real_write(fd, data[:half])
            return real_write(fd, data)

        with self._owner_patches()[0], self._owner_patches()[1], \
             mock.patch.object(os, "write", side_effect=short_write):
            helper._fold_compose_file(stage_fd, "etc/app.json", str(mig), cand,
                                      expected_sha, 0o600)
        os.close(stage_fd)
        self.assertEqual(Path(target).read_text(), "NEW")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        self.assertGreater(state["calls"], 0)

    def test_fold_file_zero_write_fails_and_retains_old_target(self):
        # A zero/invalid os.write return must fail the compose before the rename publishes the
        # unverified candidate; the authenticated old stage target is retained.
        restore, mig, target = self._fold_setup("etc/app.json", content="NEW", mode=0o600,
                                                old="OLD", old_mode=0o600)
        cand = os.path.basename("etc/app.json")
        expected_sha = hashlib.sha256(b"NEW").hexdigest()
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)

        def zero_write(fd, data):
            return 0

        with self._owner_patches()[0], self._owner_patches()[1], \
             mock.patch.object(os, "write", side_effect=zero_write):
            with self.assertRaises(SystemExit):
                helper._fold_compose_file(stage_fd, "etc/app.json", str(mig), cand,
                                          expected_sha, 0o600)
        os.close(stage_fd)
        self.assertEqual(Path(target).read_text(), "OLD")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    def test_fold_file_digest_mismatch_retains_old_target(self):
        # A digest mismatch detected while hashing the source during the stable read/copy must
        # fail BEFORE the atomic rename; the authenticated old stage target is never replaced
        # by an unverified candidate.
        restore, mig, target = self._fold_setup("etc/app.json", content="NEW", mode=0o600,
                                                old="OLD", old_mode=0o600)
        cand = os.path.basename("etc/app.json")
        wrong_sha = hashlib.sha256(b"DIFFERENT").hexdigest()
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        with self._owner_patches()[0], self._owner_patches()[1]:
            with self.assertRaises(SystemExit):
                helper._fold_compose_file(stage_fd, "etc/app.json", str(mig), cand,
                                          wrong_sha, 0o600)
        os.close(stage_fd)
        self.assertEqual(Path(target).read_text(), "OLD")

    def test_fold_file_temp_collision_retains_preexisting(self):
        # If the generated temp name already exists, O_EXCL/symlink creation fails and the
        # pre-existing colliding object must be retained (never unlinked by cleanup), and the
        # authenticated old target is untouched.
        restore, mig, target = self._fold_setup("etc/app.json", content="NEW", mode=0o600,
                                                old="OLD", old_mode=0o600)
        cand = os.path.basename("etc/app.json")
        expected_sha = hashlib.sha256(b"NEW").hexdigest()
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        fixed = ".app.json.pixel-fold-COLLIDE.tmp"
        write(restore / "etc" / fixed, "PRECIOUS", mode=0o600)
        with mock.patch.object(helper, "_fold_tmp_name", return_value=fixed), \
             self._owner_patches()[0], self._owner_patches()[1]:
            with self.assertRaises(OSError):
                helper._fold_compose_file(stage_fd, "etc/app.json", str(mig), cand,
                                          expected_sha, 0o600)
        os.close(stage_fd)
        self.assertTrue(os.path.lexists(restore / "etc" / fixed))
        self.assertEqual((restore / "etc" / fixed).read_text(), "PRECIOUS")
        self.assertEqual(Path(target).read_text(), "OLD")

    def test_fold_symlink_temp_collision_retains_preexisting(self):
        # The symlink compose must likewise retain a pre-existing object at the temp name.
        restore = self.base / "restore-stage"
        safe_dir(restore)
        safe_dir(restore / "install" / "releases")
        target = restore / "install" / "releases" / "current"
        os.symlink("3.2.2", target)
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        fixed = ".current.pixel-fold-COLLIDE.tmp"
        write(restore / "install" / "releases" / fixed, "PRECIOUS", mode=0o600)
        with mock.patch.object(helper, "_fold_tmp_name", return_value=fixed), \
             self._owner_patches()[0], self._owner_patches()[1]:
            with self.assertRaises(OSError):
                helper._fold_compose_symlink(stage_fd, "install/releases/current", "4.3.0")
        os.close(stage_fd)
        self.assertTrue(os.path.lexists(restore / "install" / "releases" / fixed))
        self.assertEqual((restore / "install" / "releases" / fixed).read_text(), "PRECIOUS")
        self.assertEqual(os.readlink(target), "3.2.2")

    def test_fold_symlink_lchown_failure_retains_old_target(self):
        # A failed owner change must fail before publication (never ignored until after the
        # replacement), leaving the authenticated old stage symlink untouched.
        restore = self.base / "restore-stage"
        safe_dir(restore)
        safe_dir(restore / "install" / "releases")
        target = restore / "install" / "releases" / "current"
        os.symlink("3.2.2", target)
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        with self._owner_patches()[0], self._owner_patches()[1], \
             mock.patch.object(os, "lchown", side_effect=OSError(1, "denied")):
            with self.assertRaises(OSError):
                helper._fold_compose_symlink(stage_fd, "install/releases/current", "4.3.0")
        os.close(stage_fd)
        self.assertEqual(os.readlink(target), "3.2.2")

    def test_cleanup_fold_temp_substitution_surfaces_and_retains(self):
        # A temp substituted after creation must never be unlinked: cleanup surfaces the
        # indeterminate state and the substituted object is retained. A directory
        # substitution guarantees a distinct object type (different identity) regardless of
        # inode reuse.
        d = self.base / "subst"
        safe_dir(d)
        fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY)
        write(d / "t.tmp", "ORIGINAL", mode=0o600)
        created = helper._stat_nofollow(fd, "t.tmp")
        os.unlink(d / "t.tmp")
        safe_dir(d / "t.tmp")  # substitute with a directory (different type)
        with self.assertRaises(SystemExit) as ctx:
            helper._cleanup_fold_temp(fd, "t.tmp", created)
        os.close(fd)
        self.assertIn("substituted", str(ctx.exception))
        self.assertTrue(os.path.isdir(d / "t.tmp"))

    def test_cleanup_fold_temp_matching_removed(self):
        # A temp that still matches the exact created identity IS unlinked by cleanup.
        d = self.base / "match"
        safe_dir(d)
        fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY)
        write(d / "t.tmp", "X", mode=0o600)
        created = helper._stat_nofollow(fd, "t.tmp")
        helper._cleanup_fold_temp(fd, "t.tmp", created)
        os.close(fd)
        self.assertFalse(os.path.lexists(d / "t.tmp"))

    def test_fold_descend_preserves_existing_parent_mode_and_secures_new_ancestor(self):
        # Creating a missing ancestor must NOT mutate an existing restored parent's mode, and
        # the new ancestor must become exact invoking uid/gid + 0700 before receiving content.
        # The existing parent starts owner-only (0700); non-mutation is proven by asserting no
        # fchmod call ever targeted that parent's exact (dev, ino) identity.
        restore = self.base / "restore-stage"
        safe_dir(restore)
        safe_dir(restore / "ws")  # existing parent, owner-only 0700
        parent_st = os.stat(restore / "ws")
        parent_id = (parent_st.st_dev, parent_st.st_ino)
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        fchown_calls = []
        fchmod_calls = []
        real_fchown = os.fchown
        real_fchmod = os.fchmod

        def record_fchown(fd, uid, gid):
            fchown_calls.append((uid, gid))
            return real_fchown(fd, uid, gid)

        def record_fchmod(fd, mode):
            st = os.fstat(fd)
            fchmod_calls.append((st.st_dev, st.st_ino, stat.S_IMODE(mode)))
            return real_fchmod(fd, mode)

        with self._owner_patches()[0], self._owner_patches()[1], \
             mock.patch.object(os, "fchown", record_fchown), \
             mock.patch.object(os, "fchmod", record_fchmod):
            child_fd = helper._fold_descend(stage_fd, "ws/scripts", create=True)
            os.close(child_fd)
        os.close(stage_fd)
        self.assertEqual(stat.S_IMODE(os.stat(restore / "ws").st_mode), 0o700)
        child = restore / "ws" / "scripts"
        child_id = (os.stat(child).st_dev, os.stat(child).st_ino)
        self.assertFalse(
            any((dev, ino) == parent_id for dev, ino, _ in fchmod_calls),
            "existing parent mode must never be mutated by fchmod")
        self.assertEqual(len(fchmod_calls), 1)
        dev, ino, mode = fchmod_calls[0]
        self.assertEqual(mode, 0o700)
        self.assertEqual((dev, ino), child_id)
        self.assertEqual(stat.S_IMODE(os.stat(child).st_mode), 0o700)
        self.assertEqual(os.stat(child).st_uid, os.geteuid())
        self.assertEqual(os.stat(child).st_gid, os.getegid())
        self.assertTrue(fchown_calls)
        self.assertTrue(all((u, g) == (os.geteuid(), os.getegid()) for u, g in fchown_calls))

    def test_fold_descend_new_chain_secures_each_ancestor(self):
        # Every newly created missing ancestor in a chain is secured; the existing top parent
        # mode is preserved. Non-mutation is proven by identity: no fchmod call may target the
        # existing parent's exact (dev, ino).
        restore = self.base / "restore-stage"
        safe_dir(restore)
        safe_dir(restore / "ws")  # existing parent, owner-only 0700
        parent_st = os.stat(restore / "ws")
        parent_id = (parent_st.st_dev, parent_st.st_ino)
        stage_fd = os.open(str(restore), os.O_RDONLY | os.O_DIRECTORY)
        fchmod_calls = []
        real_fchmod = os.fchmod

        def record_fchmod(fd, mode):
            st = os.fstat(fd)
            fchmod_calls.append((st.st_dev, st.st_ino, stat.S_IMODE(mode)))
            return real_fchmod(fd, mode)

        with self._owner_patches()[0], self._owner_patches()[1], \
             mock.patch.object(os, "fchmod", record_fchmod):
            child_fd = helper._fold_descend(stage_fd, "ws/a/b", create=True)
            os.close(child_fd)
        os.close(stage_fd)
        self.assertEqual(stat.S_IMODE(os.stat(restore / "ws").st_mode), 0o700)
        created = [restore / p for p in ("ws/a", "ws/a/b")]
        expected_ids = {(os.stat(p).st_dev, os.stat(p).st_ino) for p in created}
        target_ids = {(dev, ino) for dev, ino, _ in fchmod_calls}
        self.assertEqual(len(fchmod_calls), 2)
        self.assertEqual(target_ids, expected_ids)
        self.assertTrue(all(mode == 0o700 for _, _, mode in fchmod_calls))
        self.assertNotIn(parent_id, target_ids)
        for p in created:
            self.assertEqual(stat.S_IMODE(os.stat(restore / p).st_mode), 0o700)

    def test_intake_cleanup_failure_surfaces_indeterminate_and_retains_armed(self):
        # When ordinary post-journal intake cleanup itself fails, the armed journal must be
        # retained (the reserved marker is never written) and a specific indeterminate-cleanup
        # error must be surfaced, chained from the cleanup failure.
        value = {"kind": helper.JOURNAL_KIND, "deploymentItems": [], "armIntakeComplete": False}
        reserved = {"kind": "pixel-restore-migration-reserved", "reservationToken": "x" * 32}
        boom = RuntimeError("cleanup blew up")
        writes = []

        def fake_cleanup(v):
            raise boom

        def fake_write(custody_fd, basename, val):
            writes.append(val)

        with mock.patch.object(helper, "_cleanup_intake_newpaths", side_effect=fake_cleanup), \
             mock.patch.object(helper, "_write_journal", side_effect=fake_write):
            with self.assertRaises(SystemExit) as ctx:
                helper._recover_armed_intake(123, "j.json", value, reserved)
        self.assertIn("indeterminate", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, boom)
        self.assertEqual(writes, [])

    def test_arm_refuses_no_stage_file_missing_sibling_before_journal_write(self):
        # A new no-stage desired-present file whose prepared sibling is absent must fail
        # BEFORE the durable journal write (never arming an impossible intake).
        root = self._root("ws")
        dep = self.base / "dep"
        safe_dir(dep / "config")
        p = dep / "config" / "app.json"
        it = {"kind": "config", "path": str(p), "oldPath": old_path(str(p), pid=3, idx=0),
              "newPath": new_path(str(p), pid=3, idx=0), "hadOld": 0}
        journal = self.fake.custody / "t.json"
        proc = self.arm(journal, [it], [(str(root), 1)], prestage=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("prepared sibling is missing", proc.stderr)
        self.assertFalse(os.path.lexists(it["newPath"]))
        data = json.loads(journal.read_text())
        self.assertEqual(data["kind"], "pixel-restore-migration-reserved")


# In-process helper calls derive the invoking owner from trusted sudo state; provide the
# group too so the folded compose owner/gid contract is exercised consistently.
os.environ.setdefault("SUDO_GID", str(os.getgid()))


class FoldSubstitutionRaceTests(FoldedHarnessBase):
    """A same-user race must never publish an unverified object over the authenticated old
    restore-stage target: the fold temp is re-proven descriptor-bound (exact identity against
    the created object + content digest) BEFORE the atomic rename."""

    def _stage_fd(self, restore_stage):
        return os.open(str(restore_stage), os.O_RDONLY | os.O_DIRECTORY
                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))

    def _single_root_target(self, content="OLD", mode=0o600):
        p = self.base / "etc" / "openclaw.json"
        safe_dir(p.parent)
        write(p, content, mode=mode)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        write(restore_stage / root_rel, content, mode=mode)
        return p, restore_stage, root_rel

    def test_fold_file_temp_content_substitution_fails_before_replace(self):
        # Same inode, same size/owner/mode, but the temp content is rewritten after out_fd
        # closes. The descriptor-bound pre-rename digest must refuse it BEFORE the old target
        # is replaced, and cleanup removes the (still-identical) temp.
        p, restore_stage, root_rel = self._single_root_target(content="OLD", mode=0o600)
        it = self.file_item(p, had_old=1, mode=0o600, content="NEWROOT")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, 0o600)
        expected_sha = sha256_of(migration_stage / cand)

        tmp_name = ".known.tmp"
        real_stat = helper._stat_nofollow
        swapped = {"done": False}

        def stat_with_swap(parent_fd, basename):
            if not swapped["done"] and basename == tmp_name:
                swapped["done"] = True
                fd = os.open(tmp_name, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
                try:
                    os.write(fd, b"EVILNEW")  # same inode, same length as NEWROOT
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return real_stat(parent_fd, basename)

        stage_fd = self._stage_fd(restore_stage)
        try:
            with mock.patch.object(helper, "_fold_tmp_name", return_value=tmp_name), \
                 mock.patch.object(helper, "_stat_nofollow", side_effect=stat_with_swap), \
                 self._owner_patches()[0], self._owner_patches()[1]:
                with self.assertRaises(SystemExit) as cm:
                    helper._fold_compose_file(stage_fd, root_rel, str(migration_stage),
                                              cand, expected_sha, 0o600)
        finally:
            os.close(stage_fd)
        self.assertIn("digest", str(cm.exception))
        self.assertEqual((restore_stage / root_rel).read_text(), "OLD")
        self.assertFalse(os.path.lexists(restore_stage / tmp_name))

    def test_fold_file_temp_identity_substitution_fails_before_replace(self):
        # The temp is replaced by a different inode (same size/owner/mode). The exact
        # dev/ino/type identity against the created object must be refused before replace,
        # and the old target retained. Indeterminate cleanup leaves the foreign temp (never
        # silently unlinked, never published).
        p, restore_stage, root_rel = self._single_root_target(content="OLD", mode=0o600)
        it = self.file_item(p, had_old=1, mode=0o600, content="NEWROOT")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, 0o600)
        expected_sha = sha256_of(migration_stage / cand)

        tmp_name = ".known.tmp"
        real_stat = helper._stat_nofollow
        swapped = {"done": False}

        def stat_with_swap(parent_fd, basename):
            if not swapped["done"] and basename == tmp_name:
                swapped["done"] = True
                # Create a distinct live inode elsewhere and rename it over the temp so the
                # substituted object is guaranteed a different dev/ino than the created temp
                # (unlink+recreate can reuse the freed inode on tmpfs/overlay).
                fd = os.open(".other", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=parent_fd)
                try:
                    os.write(fd, b"NEWROOT")
                    os.fchmod(fd, 0o600)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.rename(".other", tmp_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            return real_stat(parent_fd, basename)

        stage_fd = self._stage_fd(restore_stage)
        try:
            with mock.patch.object(helper, "_fold_tmp_name", return_value=tmp_name), \
                 mock.patch.object(helper, "_stat_nofollow", side_effect=stat_with_swap), \
                 self._owner_patches()[0], self._owner_patches()[1]:
                with self.assertRaises(SystemExit) as cm:
                    helper._fold_compose_file(stage_fd, root_rel, str(migration_stage),
                                              cand, expected_sha, 0o600)
        finally:
            os.close(stage_fd)
        self.assertIn("substituted", str(cm.exception))
        self.assertEqual((restore_stage / root_rel).read_text(), "OLD")

    def test_fold_symlink_temp_identity_substitution_fails_before_replace(self):
        # A symlink temp substituted for a different inode before rename must be refused
        # (exact identity against the created symlink temp), leaving the old target.
        p = self.base / "install" / "releases" / "current"
        safe_dir(p.parent)
        os.symlink("3.2.2", p)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        root_rel = str(p).lstrip("/")
        safe_dir(restore_stage / Path(root_rel).parent)
        os.symlink("3.2.2", restore_stage / root_rel)

        tmp_name = ".known.tmp"
        real_stat = helper._stat_nofollow
        calls = {"n": 0}

        def stat_with_swap(parent_fd, basename):
            if basename == tmp_name:
                calls["n"] += 1
                if calls["n"] == 2:  # between tmp_created capture and the pre-rename identity check
                    os.symlink("6.6.6", ".other", dir_fd=parent_fd)
                    os.rename(".other", tmp_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            return real_stat(parent_fd, basename)

        stage_fd = self._stage_fd(restore_stage)
        try:
            with mock.patch.object(helper, "_fold_tmp_name", return_value=tmp_name), \
                 mock.patch.object(helper, "_stat_nofollow", side_effect=stat_with_swap), \
                 self._owner_patches()[0], self._owner_patches()[1]:
                with self.assertRaises(SystemExit) as cm:
                    helper._fold_compose_symlink(stage_fd, root_rel, "4.3.0")
        finally:
            os.close(stage_fd)
        self.assertIn("substituted", str(cm.exception))
        self.assertEqual(os.readlink(restore_stage / root_rel), "3.2.2")


class FoldCommandValidationTests(FoldedHarnessBase):
    """cmd_fold validates the COMPLETE pure deployment-spec/item set before any mutation, so a
    malformed/duplicate later item can never leave the non-live stage partially composed."""

    def _fold_setup(self, content="OLD", mode=0o700):
        live_root = self.base / "workspace"
        p = live_root / "scripts" / "browse.sh"
        safe_dir(p.parent)
        write(p, content, mode=mode)
        it = self.file_item(p, had_old=1, mode=mode, content="NEWSCRIPT")
        cand = Path(it["newPath"]).name
        migration_stage = self.base / "migration-stage"
        safe_dir(migration_stage)
        shutil.copyfile(it["newPath"], migration_stage / cand)
        os.chmod(migration_stage / cand, mode)
        restore_stage = self.base / "restore-stage"
        safe_dir(restore_stage)
        stage_rel = str(live_root).lstrip("/")
        safe_dir(restore_stage / stage_rel / "scripts")
        write(restore_stage / stage_rel / "scripts" / "browse.sh", content, mode=mode)
        return it, migration_stage, restore_stage, stage_rel

    def test_fold_malformed_later_item_leaves_first_untouched(self):
        it, migration_stage, restore_stage, stage_rel = self._fold_setup()
        bad = {"kind": "garbage", "path": str(self.base / "elsewhere" / "x"),
               "oldPath": old_path(str(self.base / "elsewhere" / "x"), pid=4, idx=0),
               "newPath": new_path(str(self.base / "elsewhere" / "x"), pid=4, idx=0),
               "hadOld": 1, "sha256": "a" * 64, "target": None}
        spec = self.fake._spec([it, bad])
        live_root = self.base / "workspace"
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "OLD")
        self.assertEqual(list(restore_stage.rglob("*.tmp")), [])

    def test_fold_duplicate_later_item_leaves_first_untouched(self):
        it, migration_stage, restore_stage, stage_rel = self._fold_setup()
        dup = dict(it)  # identical folded item -> duplicate path refused by shared validator
        spec = self.fake._spec([it, dup])
        live_root = self.base / "workspace"
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unique", proc.stderr)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "OLD")
        self.assertEqual(list(restore_stage.rglob("*.tmp")), [])

    def test_fold_requires_full_service_desired(self):
        # A deployment fold must validate the COMPLETE reviewed serviceDesired before the
        # first stage mutation; a partial serviceDesired leaves the first valid target
        # unchanged.
        it, migration_stage, restore_stage, stage_rel = self._fold_setup()
        partial = dict(FIXED_DESIRED)
        partial.pop(next(iter(partial)))
        spec = self.fake._spec([it], desired=partial)
        live_root = self.base / "workspace"
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("serviceDesired", proc.stderr)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "OLD")

    def test_fold_missing_service_desired_refused(self):
        # A deployment fold spec with no serviceDesired at all must be refused before any
        # stage mutation, leaving the first valid target unchanged.
        it, migration_stage, restore_stage, stage_rel = self._fold_setup()
        spec = self.base / "spec.json"
        spec.write_text(json.dumps({"deploymentItems": [it]}), encoding="utf-8")
        os.chmod(spec, 0o600)
        live_root = self.base / "workspace"
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("serviceDesired", proc.stderr)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "OLD")

    def test_fold_missing_required_key_leaves_first_untouched(self):
        # A later item missing a required base key (``path``) must fail cleanly - without a
        # KeyError traceback and before any stage mutation - leaving the first valid target
        # and no partial compose.
        it, migration_stage, restore_stage, stage_rel = self._fold_setup()
        missing = {"kind": "config",
                   "oldPath": old_path(str(self.base / "elsewhere" / "x"), pid=4, idx=0),
                   "newPath": new_path(str(self.base / "elsewhere" / "x"), pid=4, idx=0),
                   "hadOld": 1}
        spec = self.fake._spec([it, missing])
        live_root = self.base / "workspace"
        proc = self.fake.run("fold", str(self.fake.custody), str(spec),
                             str(migration_stage), str(restore_stage), str(live_root))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing a required key", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        composed = restore_stage / stage_rel / "scripts" / "browse.sh"
        self.assertEqual(composed.read_text(), "OLD")
        self.assertEqual(list(restore_stage.rglob("*.tmp")), [])

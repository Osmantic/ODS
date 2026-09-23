import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


MODULE = Path(__file__).parents[1] / "deploy" / "action_journal" / "__init__.py"
CLI = MODULE.parent / "cli.py"
SPEC = importlib.util.spec_from_file_location("pixel_action_journal", MODULE)
JOURNAL = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(JOURNAL)


class ActionJournalTests(unittest.TestCase):
    action_id = "github-1786512000000-a1b2c3d4"

    def create(self, root):
        clock = lambda: datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        value = JOURNAL.ExternalActionJournal(root, clock=clock)
        value.propose(
            action_id=self.action_id, connector="github", operation="create-issue",
            proposal_sha256="a" * 64, idempotency_key_sha256="b" * 64,
            idempotency_mode="provider-idempotency-key", provider_target_sha256="c" * 64,
        )
        return value

    def test_tail_truncation_of_a_completed_action_fails_closed(self):
        # Red-team finding: removing tail event files leaves a hash-chain-valid prefix that
        # would read back as an earlier, retryable state — a duplicate-effect path. The durable
        # head anchor must make any such truncation fail closed rather than revert to 'proposed'.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            journal = self.create(root)
            journal.begin_submit(self.action_id)
            journal.succeed(self.action_id, "d" * 64)
            action = root / self.action_id
            self.assertTrue((action / ".head").exists())
            for tail in ("000001.json", "000002.json"):
                (action / tail).unlink()
            # A completed action must NOT reappear as a fresh retryable proposal.
            with self.assertRaisesRegex(JOURNAL.JournalError, "truncated or tampered"):
                journal.status(self.action_id)
            with self.assertRaisesRegex(JOURNAL.JournalError, "truncated or tampered"):
                journal.begin_submit(self.action_id)
            # Removing the head marker too must also fail closed, not silently allow replay.
            (action / ".head").unlink()
            with self.assertRaisesRegex(JOURNAL.JournalError, "head marker is missing"):
                journal.status(self.action_id)

    def test_full_success_path_is_hash_chained_and_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.create(Path(directory) / "journal")
            journal.begin_submit(self.action_id)
            journal.succeed(self.action_id, "d" * 64)
            status = journal.status(self.action_id)
            self.assertEqual(status["state"], "succeeded")
            self.assertTrue(status["terminal"])
            self.assertFalse(status["retryAllowed"])
            action = Path(directory) / "journal" / self.action_id
            events = [json.loads((action / f"{index:06d}.json").read_text()) for index in range(3)]
            self.assertEqual([item["state"] for item in events], ["proposed", "submitting", "succeeded"])
            self.assertEqual(events[0]["previousEventSha256"], None)
            self.assertRegex(events[1]["previousEventSha256"], r"^[a-f0-9]{64}$")
            encoded = json.dumps(events)
            self.assertNotIn("token", encoded)
            self.assertNotIn("private payload", encoded)

    def test_interrupted_submit_is_effectively_unknown_and_must_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.create(Path(directory) / "journal")
            journal.begin_submit(self.action_id)
            self.assertEqual(journal.status(self.action_id)["effectiveState"], "unknown")
            with self.assertRaisesRegex(JOURNAL.JournalError, "cannot transition"):
                journal.begin_submit(self.action_id)
            journal.begin_reconcile(self.action_id)
            self.assertEqual(journal.status(self.action_id)["state"], "reconciling")
            journal.mark_unknown(self.action_id, "provider-not-observable")
            self.assertEqual(journal.status(self.action_id)["state"], "unknown")

    def test_cancel_is_only_possible_before_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.create(Path(directory) / "journal")
            journal.cancel(self.action_id)
            with self.assertRaisesRegex(JOURNAL.JournalError, "cannot transition"):
                journal.begin_submit(self.action_id)

    def test_exact_inputs_cannot_be_rebound_to_an_existing_action(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.create(Path(directory) / "journal")
            with self.assertRaisesRegex(JOURNAL.JournalError, "different exact inputs"):
                journal.propose(
                    action_id=self.action_id, connector="github", operation="create-issue",
                    proposal_sha256="f" * 64, idempotency_key_sha256="b" * 64,
                    idempotency_mode="provider-idempotency-key", provider_target_sha256="c" * 64,
                )

    def test_concurrent_submit_has_one_winner_and_no_duplicate_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.create(Path(directory) / "journal")
            def submit():
                try:
                    journal.begin_submit(self.action_id)
                    return "submitted"
                except JOURNAL.JournalError:
                    return "blocked"
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(lambda _item: submit(), range(8)))
            self.assertEqual(outcomes.count("submitted"), 1)
            self.assertEqual(outcomes.count("blocked"), 7)
            self.assertEqual(journal.status(self.action_id)["eventCount"], 2)

    def test_tamper_gap_extra_entry_and_hardlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            journal = self.create(root)
            journal.begin_submit(self.action_id)
            action = root / self.action_id
            (action / "000001.json").unlink()
            (action / "000002.json").write_text("{}")
            with self.assertRaises(JOURNAL.JournalError):
                journal.status(self.action_id)
        if os.name != "nt":
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "journal"
                journal = self.create(root)
                event = root / self.action_id / "000000.json"
                os.link(event, root / "copy")
                with self.assertRaisesRegex(JOURNAL.JournalError, "singly linked"):
                    journal.status(self.action_id)

    def test_real_process_exit_after_submit_recovers_only_through_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            program = f'''
import importlib.util, os
from pathlib import Path
spec = importlib.util.spec_from_file_location("crash_journal", {str(MODULE)!r})
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
journal = module.ExternalActionJournal(Path({str(root)!r}))
journal.propose(action_id={self.action_id!r}, connector="github", operation="create-issue", proposal_sha256="a"*64, idempotency_key_sha256="b"*64, idempotency_mode="provider-reconciliation-marker", provider_target_sha256="c"*64)
journal.begin_submit({self.action_id!r})
os._exit(73)
'''
            completed = subprocess.run([sys.executable, "-c", program], check=False)
            self.assertEqual(completed.returncode, 73)
            journal = JOURNAL.ExternalActionJournal(root)
            self.assertEqual(journal.status(self.action_id)["effectiveState"], "unknown")
            journal.begin_reconcile(self.action_id)
            journal.succeed(self.action_id, "d" * 64, "provider-reconciliation-match")
            self.assertEqual(journal.status(self.action_id)["state"], "succeeded")

    def test_leftover_append_temp_is_ignored_not_a_wedge(self):
        # A crash mid-append leaves a private temp for the not-yet-committed next sequence.
        # It must be skipped -- never parsed as a committed event -- so the action still reports
        # its last durable state and can still make forward progress, rather than wedging the
        # whole hash chain unreadable.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            journal = self.create(root)
            journal.begin_submit(self.action_id)
            action = root / self.action_id
            (action / ".000002.98765.tmp").write_bytes(b'{"partial')
            self.assertEqual(journal.status(self.action_id)["state"], "submitting")
            journal.succeed(self.action_id, "d" * 64)
            self.assertEqual(journal.status(self.action_id)["state"], "succeeded")
            self.assertTrue((action / "000002.json").exists())

    def test_write_fault_during_append_leaves_no_partial_committed_event(self):
        # The append commit point (rename) is atomic: if it faults, no partial NNNNNN.json is
        # left behind, the journal stays readable at its last durable state instead of wedging,
        # and a subsequent retry of the same transition still succeeds. Directly regresses the
        # crash window that an O_EXCL direct write would have left unrecoverable.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            journal = self.create(root)
            journal.begin_submit(self.action_id)
            action = root / self.action_id
            with mock.patch.object(JOURNAL.os, "rename", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    journal.succeed(self.action_id, "d" * 64)
            self.assertFalse((action / "000002.json").exists())
            self.assertEqual(journal.status(self.action_id)["state"], "submitting")
            journal.succeed(self.action_id, "d" * 64)
            self.assertEqual(journal.status(self.action_id)["state"], "succeeded")

    def test_terminal_cli_status_and_exact_pre_submit_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            journal = self.create(root)
            command = [sys.executable, str(CLI), "status", "--journal-root", str(root), "--action-id", self.action_id]
            status = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
            self.assertEqual(status["state"], "proposed")
            rejected = subprocess.run([
                sys.executable, str(CLI), "cancel", "--journal-root", str(root), "--action-id", self.action_id,
                "--confirm-head-sha256", "f" * 64, "--confirm",
            ], check=False, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            canceled = json.loads(subprocess.run([
                sys.executable, str(CLI), "cancel", "--journal-root", str(root), "--action-id", self.action_id,
                "--confirm-head-sha256", status["headSha256"], "--confirm",
            ], check=True, capture_output=True, text=True).stdout)
            self.assertEqual(canceled["state"], "canceled")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Deterministic stress pass for shared external-action custody."""

import importlib.util
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy" / "action_journal" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("pixel_action_journal_pressure", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "journal"
        journal = MODULE.ExternalActionJournal(root)
        counts = {"succeeded": 0, "unknown": 0, "failed": 0, "canceled": 0, "racesBlocked": 0}
        for index in range(400):
            action_id = f"github-1786512{index:06d}-{index:08x}"
            journal.propose(
                action_id=action_id, connector="github", operation="create-issue",
                proposal_sha256=f"{index:064x}", idempotency_key_sha256=f"{index + 1:064x}",
                idempotency_mode="provider-reconciliation-marker", provider_target_sha256=f"{index + 2:064x}",
            )
            route = index % 4
            if route == 0:
                journal.begin_submit(action_id); journal.succeed(action_id, f"{index + 3:064x}"); expected = "succeeded"
            elif route == 1:
                journal.begin_submit(action_id); journal.mark_unknown(action_id); journal.begin_reconcile(action_id); journal.mark_unknown(action_id, "provider-not-observable"); expected = "unknown"
            elif route == 2:
                journal.fail(action_id, "local-validation-failed"); expected = "failed"
            else:
                journal.cancel(action_id); expected = "canceled"
            status = journal.status(action_id)
            if status["state"] != expected or (expected != "proposed" and status["retryAllowed"]):
                raise AssertionError("journal stress state or retry decision differs")
            counts[expected] += 1

        race_id = "github-1786512999999-deadbeef"
        journal.propose(action_id=race_id, connector="github", operation="comment", proposal_sha256="a" * 64, idempotency_key_sha256="b" * 64, idempotency_mode="provider-reconciliation-marker", provider_target_sha256="c" * 64)
        def submit(_index):
            try:
                journal.begin_submit(race_id)
                return True
            except MODULE.JournalError:
                return False
        with ThreadPoolExecutor(max_workers=32) as pool:
            outcomes = list(pool.map(submit, range(128)))
        if outcomes.count(True) != 1:
            raise AssertionError("journal stress race produced duplicate submit winners")
        counts["racesBlocked"] = outcomes.count(False)

        encoded = b"".join(path.read_bytes() for path in root.rglob("*.json"))
        for canary in (b"PRIVATE", b"github_pat_", b"Authorization", b"issue body"):
            if canary in encoded:
                raise AssertionError("journal stress retained content or credentials")
        print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

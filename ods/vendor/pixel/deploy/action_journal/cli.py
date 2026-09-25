#!/usr/bin/env python3
"""Trusted-terminal inspection and pre-submit cancellation for external actions."""

import argparse
import json
from pathlib import Path

try:
    from action_journal import ExternalActionJournal, SHA256
except ModuleNotFoundError:
    from __init__ import ExternalActionJournal, SHA256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "cancel"))
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--confirm-head-sha256")
    parser.add_argument("--confirm", action="store_true")
    arguments = parser.parse_args()
    journal = ExternalActionJournal(arguments.journal_root)
    status = journal.status(arguments.action_id)
    if arguments.command == "cancel":
        if not arguments.confirm or not isinstance(arguments.confirm_head_sha256, str) or not SHA256.fullmatch(arguments.confirm_head_sha256):
            parser.error("cancel requires exact --confirm-head-sha256 and --confirm")
        if status["state"] != "proposed" or status["headSha256"] != arguments.confirm_head_sha256:
            raise RuntimeError("external action is not the exact still-unsent proposal selected for cancellation")
        journal.cancel(arguments.action_id)
        status = journal.status(arguments.action_id)
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

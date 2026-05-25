#!/usr/bin/env python3
"""Static contracts for Hermes slash worker leak guardrails."""

from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DREAM_CLI = ROOT_DIR / "dream-cli"
DOCTOR = ROOT_DIR / "scripts" / "dream-doctor.sh"
PRUNE_SCRIPT = ROOT_DIR / "scripts" / "prune-hermes-slash-workers.sh"
HERMES_DOCS = ROOT_DIR / "docs" / "HERMES.md"


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def require(path: Path, needle: str, message: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if needle not in text:
        fail(message)


def main() -> int:
    for path in (DREAM_CLI, DOCTOR, PRUNE_SCRIPT, HERMES_DOCS):
        if not path.exists():
            fail(f"Missing expected file: {path}")

    prune_text = PRUNE_SCRIPT.read_text(encoding="utf-8")
    for needle in (
        "HERMES_SLASH_WORKER_MAX_COUNT",
        "HERMES_SLASH_WORKER_MAX_AGE_SECONDS",
        "FORCE=0",
        "--force",
        "DREAM_HERMES_SLASH_WORKER_PS_FIXTURE",
        "tui_gateway[.]slash_worker",
    ):
        if needle not in prune_text:
            fail(f"prune script missing guardrail: {needle}")

    # The first guardrail PR should keep cleanup explicit, not install an
    # always-on process killer.
    for forbidden in ("cron", "systemctl enable", "timer"):
        if forbidden in prune_text:
            fail(f"prune script should not install automatic cleanup via {forbidden}")

    require(DREAM_CLI, "hermes-workers|slash-workers)", "dream repair target missing")
    require(DREAM_CLI, "dream repair hermes-workers", "dream repair help missing")
    require(DOCTOR, "hermes_slash_workers", "doctor JSON field missing")
    require(DOCTOR, "dream repair hermes-workers", "doctor autofix hint missing")
    require(HERMES_DOCS, "dream repair hermes-workers", "Hermes docs cleanup command missing")

    print("[PASS] Hermes slash worker guardrail contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

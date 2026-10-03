#!/usr/bin/env python3
"""Print Pixel's local-only, content-free host readiness guidance."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_doctor_contract", ROOT / "control" / "doctor.py")
DOCTOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(DOCTOR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect rounded local readiness without changing the host or making network calls",
    )
    parser.add_argument("--json", action="store_true", help="print the strict content-free JSON contract")
    parser.add_argument("--profile", choices=("prepared", "reference"), default="prepared")
    args = parser.parse_args(argv)
    report: dict[str, Any] = DOCTOR.doctor_report(ROOT, args.profile)
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(DOCTOR.format_human(report))
    return 0 if report["summary"]["state"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""CLI coverage for extension catalog drift checking."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate-extensions-catalog.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def main() -> int:
    current = run("--check")
    assert current.returncode == 0, current.stderr

    with tempfile.TemporaryDirectory() as temp_dir:
        stale = Path(temp_dir) / "catalog.json"
        stale.write_text("{}\n", encoding="utf-8")
        result = run("--output", str(stale), "--check")
        assert result.returncode == 1
        assert "out of date" in result.stderr
        assert stale.read_text(encoding="utf-8") == "{}\n"

    print("[PASS] extension catalog native check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

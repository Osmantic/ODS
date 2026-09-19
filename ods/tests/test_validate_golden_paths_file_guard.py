#!/usr/bin/env python3
"""Regression test: validate-golden-paths rejects directories and permission errors.

The baseline used path.exists() which accepted directories, causing an
unhandled IsADirectoryError when read_text() was called. Only json.JSONDecodeError
was caught, so PermissionError and IsADirectoryError also escaped to the caller.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-golden-paths.py"


def run_validator(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class ValidateGoldenPathsFileGuardTests(unittest.TestCase):
    def test_directory_path_exits_nonzero(self) -> None:
        """Passing a directory must exit 1 and print FAIL, not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_validator([tmpdir])
            self.assertNotEqual(
                result.returncode, 0,
                "Must exit non-zero when passed a directory path",
            )
            combined = result.stdout + result.stderr
            self.assertIn("[FAIL]", combined)

    def test_missing_file_exits_nonzero(self) -> None:
        """Non-existent path must exit 1 cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = str(Path(tmpdir) / "no-such-file.json")
            result = run_validator([missing])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("[FAIL]", result.stdout + result.stderr)

    def test_invalid_json_exits_nonzero(self) -> None:
        """A file with invalid JSON must exit non-zero and report FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")
            result = run_validator([str(bad)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("[FAIL]", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

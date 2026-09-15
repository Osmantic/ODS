#!/usr/bin/env python3
"""Regression test for installers/lib/path-utils.sh normalization and disk space bounds."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
PATH_UTILS_SH = ROOT_DIR / "installers" / "lib" / "path-utils.sh"


class PathUtilsNormalizationTests(unittest.TestCase):
    def run_bash_func(self, command: str) -> subprocess.CompletedProcess:
        script = f'source "{PATH_UTILS_SH}"\n{command}'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_normalize_path_resolves_dot_dot(self):
        """Relative dots in paths must resolve without leaving ../ components in output."""
        res = self.run_bash_func('normalize_path "/tmp/foo/../bar"')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.strip().endswith("/bar"))
        self.assertNotIn("..", res.stdout)

    def test_normalize_path_empty_fails(self):
        """Empty path must return exit status 1."""
        res = self.run_bash_func('normalize_path ""')
        self.assertEqual(res.returncode, 1)

    def test_validate_install_path_handles_valid_directory(self):
        """validate_install_path succeeds on writable temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = self.run_bash_func(f'validate_install_path "{tmpdir}/ods"')
            # 0 is ok, 2 is warning (low disk) — neither is an unhandled bash syntax error
            self.assertIn(res.returncode, (0, 2), res.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

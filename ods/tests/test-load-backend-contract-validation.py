#!/usr/bin/env python3
"""Regression test for scripts/load-backend-contract.sh argument and path traversal validation."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
CONTRACT_SCRIPT = ROOT_DIR / "scripts" / "load-backend-contract.sh"


class LoadBackendContractValidationTests(unittest.TestCase):
    def test_missing_backend_argument_reports_error(self):
        """Passing --backend without a following argument must exit 1 with clear message."""
        result = subprocess.run(["bash", str(CONTRACT_SCRIPT), "--backend"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires an argument", result.stderr)

    def test_path_traversal_identifier_is_rejected(self):
        """Backend identifiers with slashes or directory traversal dots must be rejected."""
        for invalid_id in ("../../manifest", "foo/bar", "..", "."):
            with self.subTest(invalid_id=invalid_id):
                result = subprocess.run(["bash", str(CONTRACT_SCRIPT), "--backend", invalid_id], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Invalid backend identifier", result.stderr)

    def test_valid_backend_contract_loads_successfully(self):
        """Loading a valid backend contract like 'cpu' in env mode outputs environment definitions."""
        result = subprocess.run(["bash", str(CONTRACT_SCRIPT), "--backend", "cpu", "--env"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BACKEND_CONTRACT_ID="cpu"', result.stdout)
        self.assertIn('BACKEND_SERVICE_NAME="llama-server"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

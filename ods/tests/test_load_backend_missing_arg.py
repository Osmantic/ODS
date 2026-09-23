#!/usr/bin/env python3
"""Regression test: verify load-backend-contract.sh rejects missing --backend argument."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "load-backend-contract.sh"


class LoadBackendMissingArgTests(unittest.TestCase):
    def test_missing_backend_value_rejected(self):
        res = subprocess.run(["bash", str(SCRIPT), "--backend"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, f"Expected exit 1, got {res.returncode}")
        self.assertIn("Missing value for argument: --backend", res.stderr)

    def test_valid_backend_contract_accepted(self):
        contract_file = REPO_ROOT / "config" / "backends" / "llama-server.json"
        if contract_file.exists():
            res = subprocess.run(["bash", str(SCRIPT), "--backend", "llama-server", "--env"], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Expected exit 0, got {res.returncode}\n{res.stderr}")
            self.assertIn('BACKEND_CONTRACT_ID="llama-server"', res.stdout)


if __name__ == "__main__":
    unittest.main()

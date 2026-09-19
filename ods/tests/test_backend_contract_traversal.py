"""Backend contract loader must reject traversing and malformed identifiers."""

import subprocess
import unittest
from pathlib import Path


class BackendContractValidationTests(unittest.TestCase):
    def setUp(self):
        self.script_path = Path(__file__).resolve().parents[1] / "scripts" / "load-backend-contract.sh"

    def test_traversal_and_malformed_ids_are_rejected(self):
        invalid_ids = [
            "../../etc/passwd",
            "../backends/llama-server",
            "llama server",
            "llama;id",
            "backend$name",
        ]
        for bad_id in invalid_ids:
            with self.subTest(bad_id=bad_id):
                res = subprocess.run(
                    ["bash", str(self.script_path), "--backend", bad_id],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Invalid backend identifier", res.stderr)

    def test_missing_argument_fails(self):
        res = subprocess.run(
            ["bash", str(self.script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Missing required argument", res.stderr)


if __name__ == "__main__":
    unittest.main()

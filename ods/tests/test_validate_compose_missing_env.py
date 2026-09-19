"""validate-compose-stack must fail closed when specified env file is missing."""
import subprocess
import unittest
from pathlib import Path

class ValidateComposeEnvTests(unittest.TestCase):
    def test_missing_explicit_env_file_fails(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate-compose-stack.sh"
        res = subprocess.run(
            ["bash", str(script), "--compose-flags", "-f docker-compose.yml", "--env-file", "nonexistent.env"],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("Specified env file not found", res.stderr)

if __name__ == "__main__":
    unittest.main()

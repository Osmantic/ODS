"""rootless docker state helper must parse case-insensitive boolean values."""
import subprocess
import unittest
from pathlib import Path

class RootlessCaseInsensitiveTests(unittest.TestCase):
    def test_uppercase_true_recognized(self):
        script = Path(__file__).resolve().parents[1] / "lib" / "rootless-ownership.sh"
        cmd = f"""
        source {script}
        ODS_ASSUME_ROOTLESS=TRUE ods_docker_rootless_state
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

if __name__ == "__main__":
    unittest.main()

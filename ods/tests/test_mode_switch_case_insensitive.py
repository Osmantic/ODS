"""mode-switch must normalize target mode argument to lowercase."""
import subprocess
import tempfile
import unittest
from pathlib import Path

class ModeSwitchCaseInsensitiveTests(unittest.TestCase):
    def test_uppercase_mode_normalized(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "mode-switch.sh"
        with tempfile.NamedTemporaryFile("w") as tf:
            tf.write("ODS_MODE=cloud\n")
            tf.flush()
            cmd = f"""
            source {script}
            ENV_FILE={tf.name}
            switch_mode "LOCAL"
            grep "^ODS_MODE=" {tf.name}
            """
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("ODS_MODE=local", res.stdout)

if __name__ == "__main__":
    unittest.main()

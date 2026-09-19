"""pixel uninstall helper must validate UID and GID arguments cleanly."""
import subprocess
import unittest
from pathlib import Path

class PixelUninstallUidTests(unittest.TestCase):
    def test_non_integer_uid_fails_with_exit_message(self):
        script_file = Path(__file__).resolve().parents[1] / "lib" / "pixel-uninstall.sh"
        content = script_file.read_text()
        py_code = content.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        py_snippet = "\n".join(py_code.splitlines()[:38])
        args = ["verify", "/tmp/inst", "ready", "user", "bad_uid", "1000", "0", "0", "unit", "prog", "cfg", "state", "probe", "dropin"]
        res = subprocess.run(["python3", "-c", py_snippet, *args], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("invalid UID/GID arguments", res.stderr)

if __name__ == "__main__":
    unittest.main()

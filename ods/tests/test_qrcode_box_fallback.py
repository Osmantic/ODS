"""qrcode url box helper must provide fallback on empty string."""
import subprocess
import unittest
from pathlib import Path

class QrCodeBoxTests(unittest.TestCase):
    def test_empty_url_falls_back_to_default(self):
        script = Path(__file__).resolve().parents[1] / "lib" / "qrcode.sh"
        cmd = f"""
        source {script}
        print_url_box ""
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("http://localhost:3001", res.stdout)

if __name__ == "__main__":
    unittest.main()

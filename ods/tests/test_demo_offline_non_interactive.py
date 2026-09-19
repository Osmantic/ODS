"""demo-offline pause helper must not block in non-interactive environments."""
import subprocess
import unittest
from pathlib import Path

class DemoOfflinePauseTests(unittest.TestCase):
    def test_pause_returns_immediately_when_stdin_not_tty(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "demo-offline.sh"
        cmd = f"""
        eval "$(sed -n '/^pause()/,/^}}/p' {script})"
        pause < /dev/null
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, res.stderr)

if __name__ == "__main__":
    unittest.main()

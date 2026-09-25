"""Real numeric-identity regression for phase 06 (run with sudo on Linux)."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "requires Linux root to drop numeric IDs")
class DashboardDataPermissions(unittest.TestCase):
    def test_install_and_reinstall_preserve_private_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            install.chmod(0o755)
            data = install / "data"
            data.mkdir(mode=0o750)
            os.chown(data, 1001, 2001)
            (data / "token-spy").mkdir()
            private = data / "hermes"
            private.mkdir(mode=0o700)
            os.chown(private, 1001, 2001)
            phase = (ROOT / "installers/phases/06-directories.sh").read_text()
            boundary = phase.split('_phase06_step "prepare-service-permissions"', 1)[1]
            boundary = boundary.split('_phase06_step "generate-env"', 1)[0]
            script = 'set -euo pipefail\n_phase06_rootless=false\nods_sudo() { "$@"; }\nerror() { echo "$*" >&2; return 1; }\nwarn() { echo "$*" >&2; }\n' + boundary
            environment = dict(os.environ, SCRIPT_DIR=str(ROOT), INSTALL_DIR=str(install))
            probe = '''
import os, pathlib, tempfile
data = pathlib.Path(os.environ["INSTALL_DIR"]) / "data"
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
receipt = data / "pixel-chat-results"
receipt.mkdir(mode=0o700, exist_ok=True)
fd, path = tempfile.mkstemp(dir=data)
with os.fdopen(fd, "w") as stream:
    stream.write("retained-private-password")
os.replace(path, data / "dashboard-password.json")
(receipt / "turn.json").write_text("retained-chat-result")
'''
            denied = subprocess.run(["python3", "-c", probe], env=environment, capture_output=True, text=True, check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("PermissionError", denied.stderr)
            for attempt in range(2):
                subprocess.run(["bash", "-c", script], env=environment, check=True)
                if attempt:
                    self.assertEqual((data / "dashboard-password.json").read_text(), "retained-private-password")
                    self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")
                subprocess.run(["python3", "-c", probe], env=environment, check=True)
                self.assertEqual(data.stat().st_uid, 1001)
                self.assertEqual(data.stat().st_gid, 1000)
                self.assertEqual(data.stat().st_mode & 0o777, 0o770)
                self.assertEqual(private.stat().st_uid, 1001)
                self.assertEqual(private.stat().st_gid, 2001)
                self.assertEqual(private.stat().st_mode & 0o777, 0o700)
                self.assertEqual((data / "dashboard-password.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")


if __name__ == "__main__":
    unittest.main()

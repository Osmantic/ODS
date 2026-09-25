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
            os.chown(data / "token-spy", 1001, 2001)
            private = data / "hermes"
            private.mkdir(mode=0o700)
            os.chown(private, 1001, 2001)
            phase = (ROOT / "installers/phases/06-directories.sh").read_text()
            start = 'prepare-dashboard-permissions' if 'prepare-dashboard-permissions' in phase else 'prepare-service-permissions'
            boundary = phase.split(f'_phase06_step "{start}"', 1)[1]
            boundary = boundary.split('_phase06_step "generate-env"', 1)[0]
            pre_copy = phase.split('    # Fix ownership of data/config dirs', 1)[1]
            pre_copy = pre_copy.split('    # Copy entire source tree', 1)[0]
            pre_copy = pre_copy[pre_copy.index('\n'):]
            pre_copy_script = ('set -euo pipefail\n_phase06_rootless=false\nENABLE_HERMES=true\n'
                               '_phase06_repair_host_path() { echo "Unexpected ownership repair: $1" >&2; return 1; }\n'
                               'error() { echo "$*" >&2; return 1; }\nrun_phase() {\n' + pre_copy + '\n:\n}\nrun_phase\n')
            script = ('set -euo pipefail\n_phase06_rootless=false\n_phase06_step() { :; }\nods_sudo() { "$@"; }\n'
                      'chown() { [[ "${!#}" != "$INSTALL_DIR/data/token-spy" ]] || return 0; command chown "$@"; }\n'
                      'error() { echo "$*" >&2; return 1; }\nwarn() { echo "$*" >&2; }\n' + boundary)
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
                if attempt:
                    def install_owner():
                        os.setgroups([])
                        os.setgid(2001)
                        os.setuid(1001)
                    subprocess.run(["bash", "-c", pre_copy_script], env=environment, preexec_fn=install_owner, check=True)
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
            # Recover a private result tree already transferred by an older
            # reinstall, without destroying its receipt or changing modes.
            os.chown(data / "pixel-chat-results", 1001, 2001)
            os.chown(data / "pixel-chat-results/turn.json", 1001, 2001)
            outside = install / "outside-private-state"
            outside.write_text("untouched")
            os.chown(outside, 2001, 2001)
            (data / "pixel-chat-results/external").symlink_to(outside)
            subprocess.run(["bash", "-c", script], env=environment, check=True)
            self.assertEqual((data / "pixel-chat-results").stat().st_uid, 1000)
            self.assertEqual((data / "pixel-chat-results").stat().st_mode & 0o777, 0o700)
            self.assertEqual((data / "pixel-chat-results/turn.json").stat().st_uid, 1000)
            self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")
            self.assertEqual(outside.stat().st_uid, 2001)
            self.assertEqual(outside.read_text(), "untouched")


if __name__ == "__main__":
    unittest.main()

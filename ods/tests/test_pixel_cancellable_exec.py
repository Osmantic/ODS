"""Exercise execution and cancellation using the real platform wrapper."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host/cancellable-exec.sh'


class CancellableExecTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ods exec ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wrapper = self.root / 'cancellable-exec.sh'
        shutil.copyfile(SOURCE, self.wrapper)
        self.wrapper.chmod(0o500)
        self.marker_id = 'a' * 64

    def start(self, command, marker_root=None):
        return subprocess.Popen(['/bin/sh', str(self.wrapper), self.marker_id,
            base64.b64encode(command.encode()).decode(), *([str(marker_root)] if marker_root else [])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_command_output_and_exit_code(self):
        process = self.start("printf '%s' 'ods proof'; exit 7")
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual((process.returncode, stdout, stderr), (7, 'ods proof', ''))

    def test_versioned_wrapper_uses_separate_private_marker_directory(self):
        markers = self.root / "owner's private markers"
        markers.mkdir(mode=0o700)
        process = self.start('sleep 30', marker_root=markers)
        try:
            time.sleep(0.4)
            (markers / f'{self.marker_id}.cancel').touch(mode=0o600)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130)
            self.assertFalse((self.root / f'{self.marker_id}.cancel').exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    @unittest.skipUnless(sys.platform == 'darwin', 'native macOS shell contract')
    def test_native_bash_writes_exact_bytes_and_preserves_argument_boundaries(self):
        target = self.root / 'probe.txt'
        process = self.start(f"values=(ODS_MAC_OK); echo -n \"${{values[0]}}\" > '{target}'; cat '{target}'")
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual((process.returncode, stdout, stderr), (0, 'ODS_MAC_OK', ''))
        self.assertEqual(target.read_bytes(), b'ODS_MAC_OK')

    @unittest.skipUnless(sys.platform == 'darwin', 'native macOS shell contract')
    def test_native_shell_does_not_source_login_profiles(self):
        marker = self.root / 'profile-ran'
        (self.root / '.bash_profile').write_text(f"touch '{marker}'\n")
        process = subprocess.run(['/bin/sh', str(self.wrapper), self.marker_id,
            base64.b64encode(b"printf '%s' ready").decode()],
            env={**os.environ, 'HOME': str(self.root)}, capture_output=True,
            text=True, timeout=10)
        self.assertEqual((process.returncode, process.stdout, process.stderr), (0, 'ready', ''))
        self.assertFalse(marker.exists())

    def test_cancel_stops_descendant_before_side_effect(self):
        target = self.root / 'must-not-exist'
        process = self.start(f"(sleep 3; touch '{target}') & wait")
        try:
            time.sleep(0.4)
            (self.root / f'{self.marker_id}.cancel').touch(mode=0o600)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130)
            time.sleep(3)
            self.assertFalse(target.exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()

"""Exercise the shared installer with the platform's real stat and install."""
import os
from pathlib import Path
import platform
import pwd
import stat
import subprocess
import tempfile
import unittest

LIBRARY = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-host-install.sh'


@unittest.skipUnless(platform.system() in ('Darwin', 'Linux'), 'POSIX installer')
class ExecControlInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home with spaces'
        self.home.mkdir(mode=0o700)
        self.control = self.home / '.openclaw/.ods-exec-control'
        self.sources = [self.root / 'exec.sh', self.root / 'sudo.sh']
        for source in self.sources:
            source.write_text('#!/bin/sh\nexit 0\n')
            source.chmod(0o600)

    def install(self):
        return subprocess.run(['bash', '-c', '''
source "$1"
ods_sudo_available() { return 1; }
_ods_pixel_install_exec_control "$2" "$3" "$4" "$5"
''', 'fixture', str(LIBRARY), pwd.getpwuid(os.getuid()).pw_name,
            str(self.home), *map(str, self.sources)], capture_output=True, text=True)

    def test_new_install_and_repeat_preserve_required_modes(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(self.control.stat().st_mode), 0o700)
            for filename in ('cancellable-exec.sh', 'sudo'):
                info = (self.control / filename).lstat()
                self.assertEqual(info.st_uid, os.getuid())
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o500)

    def test_symlink_root_is_rejected_without_writing_target(self):
        self.control.parent.mkdir()
        other = self.root / 'other'
        other.mkdir()
        self.control.symlink_to(other, target_is_directory=True)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(list(other.iterdir()), [])

    def test_unsafe_source_is_rejected_before_creating_state(self):
        self.sources[0].chmod(0o666)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(self.control.parent.exists())

    def test_hardlinked_source_is_rejected(self):
        os.link(self.sources[0], self.root / 'source-link')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(self.control.parent.exists())

    def test_destination_symlink_is_not_overwritten(self):
        self.control.mkdir(parents=True, mode=0o700)
        (self.control / 'sudo').symlink_to(self.sources[1])
        original = self.sources[1].read_bytes()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.sources[1].read_bytes(), original)
        self.assertFalse((self.control / 'cancellable-exec.sh').exists())

    def test_owner_home_uses_account_database_not_ambient_home(self):
        owner = pwd.getpwuid(os.getuid())
        result = subprocess.run(['bash', '-c', 'source "$1"; ods_pixel_owner_home "$2"',
            'fixture', str(LIBRARY), owner.pw_name], capture_output=True, text=True,
            env={**os.environ, 'HOME': str(self.home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), owner.pw_dir)

    def test_owner_home_rejects_invalid_or_absent_account(self):
        for owner in ('../root', 'name with space', '-root', 'ods_missing_' + str(os.getpid())):
            result = subprocess.run(['bash', '-c', 'source "$1"; ods_pixel_owner_home "$2"',
                'fixture', str(LIBRARY), owner], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()

"""PIXEL_RESPONSE_LANGUAGE reaches Pixel as an owner file in the OpenClaw state folder."""
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
class ResponseLanguageInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home with spaces'
        (self.home / '.openclaw').mkdir(parents=True, mode=0o700)
        self.target = self.home / '.openclaw/.ods-response-language'

    def install(self, language):
        return subprocess.run(['bash', '-c', '''
source "$1"
ods_sudo_available() { return 1; }
PIXEL_RESPONSE_LANGUAGE="$4" _ods_pixel_install_response_language "$2" "$3"
''', 'fixture', str(LIBRARY), pwd.getpwuid(os.getuid()).pw_name, str(self.home), language],
            capture_output=True, text=True)

    def test_english_writes_a_private_owner_file_and_repeats_cleanly(self):
        for _ in range(2):
            result = self.install('en')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.target.read_text(), 'en\n')
            info = self.target.lstat()
            self.assertEqual(info.st_uid, os.getuid())
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(sorted(path.name for path in self.target.parent.iterdir()), ['.ods-response-language'])

    def test_empty_setting_removes_the_file(self):
        self.assertEqual(self.install('en').returncode, 0)
        result = self.install('')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.install('').returncode, 0)

    def test_unknown_language_is_refused_without_writing(self):
        self.assertNotEqual(self.install('pt').returncode, 0)
        self.assertFalse(self.target.exists())

    def test_symlinked_target_is_refused_and_left_untouched(self):
        other = self.root / 'other'
        other.write_text('owner data\n')
        self.target.symlink_to(other)
        self.assertNotEqual(self.install('en').returncode, 0)
        self.assertEqual(other.read_text(), 'owner data\n')

    def test_missing_state_folder_is_refused(self):
        self.target.parent.rmdir()
        self.assertNotEqual(self.install('en').returncode, 0)
        self.assertFalse(self.target.parent.exists())


if __name__ == '__main__':
    unittest.main()

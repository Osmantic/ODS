from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


SETUP = Path(__file__).resolve().parents[1] / 'extensions/library/services/flowise/setup.sh'


class FlowiseSetup(unittest.TestCase):
    def run_setup(self, root):
        subprocess.run(['sh', str(SETUP), str(root), 'nvidia'], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_fresh_install_can_create_default_encryption_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'installation with spaces'
            root.mkdir()
            self.run_setup(root)
            key = root / 'data/flowise/secretkey/encryption.key'
            # The image writes this file directly, without mkdir of its parent.
            key.write_text('first-start-key')
            env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines())
            self.assertEqual(env['FLOWISE_USERNAME'], 'admin')
            self.assertRegex(env['FLOWISE_PASSWORD'], r'\A[a-f0-9]{32}\Z')
            self.assertEqual(stat.S_IMODE((root / '.env').stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key.parent.stat().st_mode), 0o700)

    def test_repeated_setup_retains_encryption_key_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / '.env'
            env.write_text('FLOWISE_USERNAME=existing-user\nFLOWISE_PASSWORD=existing-test-value\n')
            env.chmod(0o600)
            key = root / 'data/flowise/secretkey/encryption.key'
            key.parent.mkdir(parents=True)
            key.write_bytes(b'existing-encryption-key\n')
            key.chmod(0o600)
            before = (env.read_bytes(), key.read_bytes(), key.stat().st_ino, key.stat().st_mode)
            self.run_setup(root)
            self.run_setup(root)
            self.assertEqual((env.read_bytes(), key.read_bytes(), key.stat().st_ino, key.stat().st_mode), before)


if __name__ == '__main__':
    unittest.main()

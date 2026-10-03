"""Production builds must install complete, hashed locks, never loose inputs."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ('ape', 'dashboard-api', 'model-router', 'pixel-edge',
            'pixel-inference', 'pixel-model-relay', 'privacy-shield',
            'remote-provider-egress', 'token-spy')


class RuntimeLockTests(unittest.TestCase):
    def test_every_production_build_installs_hashed_lock(self):
        for service in SERVICES:
            with self.subTest(service=service):
                directory = ROOT / 'ods/extensions/services' / service
                dockerfile = (directory / 'Dockerfile').read_text()
                self.assertRegex(dockerfile, r'(?m)^COPY [^\n]*requirements\.lock ')
                self.assertIn('pip install --no-cache-dir --require-hashes -r requirements.lock', dockerfile)
                self.assertNotIn('-r requirements.txt', dockerfile)
                # Continuation lines belong to the preceding requirement.
                lock = (directory / 'requirements.lock').read_text().replace('\\\n', '')
                requirements = [line.strip() for line in lock.splitlines()
                                if line.strip() and not line.lstrip().startswith('#')]
                self.assertGreater(len(requirements), 0)
                for requirement in requirements:
                    self.assertRegex(requirement, r'^[A-Za-z0-9_.-]+==[^ ;]+')
                    self.assertRegex(requirement, r'--hash=sha256:[a-f0-9]{64}(?:\s|$)')
                    self.assertNotIn('://', requirement)


if __name__ == '__main__':
    unittest.main()

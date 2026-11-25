"""Engine selection controls; no Docker invocation or host mutation."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('synthetic_probe', Path(__file__).with_name('probe_macos_ingress_config.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeEngineTests(unittest.TestCase):
    endpoint = 'unix:///Users/fixture/.colima/qualified/docker.sock'
    operating_system = 'Ubuntu 24.04.4 LTS'

    def test_default_desktop_mode_rejects_colima(self):
        with self.assertRaisesRegex(ValueError, 'requires Docker Desktop'):
            probe.validate_engine('docker-desktop', self.endpoint, self.operating_system)

    def test_desktop_mode_still_accepts_desktop(self):
        probe.validate_engine('docker-desktop', 'unix:///Users/fixture/.docker/run/docker.sock', 'Docker Desktop')

    def test_colima_requires_both_explicit_bindings(self):
        for values in ({}, {'expected_endpoint': self.endpoint}, {'expected_operating_system': self.operating_system}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                probe.validate_engine('colima', self.endpoint, self.operating_system, **values)

    def test_colima_refuses_changed_endpoint_or_engine(self):
        for endpoint, system in ((self.endpoint + '-other', self.operating_system),
                (self.endpoint, 'different engine'), (self.endpoint, 'Docker Desktop')):
            with self.subTest(endpoint=endpoint, system=system), self.assertRaises(ValueError):
                probe.validate_engine('colima', endpoint, system, expected_endpoint=self.endpoint,
                    expected_operating_system=self.operating_system)

    def test_colima_accepts_only_exact_reviewed_binding(self):
        probe.validate_engine('colima', self.endpoint, self.operating_system,
            expected_endpoint=self.endpoint, expected_operating_system=self.operating_system)


class ProbeCustodyTests(unittest.TestCase):
    def test_synthetic_workspace_uses_reviewed_output_parent(self):
        with tempfile.TemporaryDirectory() as parent:
            with probe.probe_workspace(Path(parent) / 'probe.json', 'owned-probe') as directory:
                self.assertEqual(Path(directory).parent, Path(parent))
                self.assertTrue(Path(directory).is_dir())
                if os.name != 'nt':
                    self.assertEqual(Path(directory).stat().st_mode & 0o777, 0o700)
            self.assertFalse(Path(directory).exists())

    def test_cli_diagnostics_are_bounded_private_and_preserve_first_failure(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'probe.json'
            probe.record_docker_failure(output, 'run', SimpleNamespace(
                returncode=125, stdout='o' * 10000, stderr='e' * 20000))
            path = Path(parent) / 'probe.json.private-docker-failure.json'
            raw = path.read_bytes()
            result = json.loads(raw)
            self.assertEqual(result, {'operation': 'run', 'exitCode': 125,
                'stdoutTail': 'o' * 4096, 'stderrTail': 'e' * 8192})
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            probe.record_docker_failure(output, 'cleanup', SimpleNamespace(
                returncode=1, stdout='', stderr='later'))
            self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()

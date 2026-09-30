import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parent.parent / 'lib' / 'wsl-agent-address.py'
spec = importlib.util.spec_from_file_location('wsl_agent_address', SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _runner_factory(address='10.1.2.3', mode='nat', os_name='Docker Desktop'):
    def runner(cmd, **kw):
        if cmd[:2] == ['docker', 'info']:
            return os_name
        if cmd[:2] == ['wslinfo', '--networking-mode']:
            return mode
        if cmd[:2] == ['ip', '-j']:
            return json.dumps([{
                'flags': ['UP', 'BROADCAST'],
                'addr_info': [{'local': address, 'scope': 'global', 'family': 'inet'}],
            }])
        raise AssertionError('unexpected cmd: %r' % (cmd,))
    return runner


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wsl-agent-test-')
        self.root = Path(self.tmp.name) / 'ods'
        self.root.mkdir()
        os.chmod(self.root, 0o700)
        self.env = self.root / '.env'
        self.uid = os.getuid()

    def tearDown(self):
        self.tmp.cleanup()

    def write_env(self, text, mode=0o600):
        self.env.write_text(text)
        os.chmod(self.env, mode)

    def run_helper(self, **kw):
        kw.setdefault('runner', _runner_factory())
        kw.setdefault('release', '5.15.90.1-microsoft-standard-WSL2')
        return mod.run(self.root, uid=self.uid, **kw)

    def read_bytes(self):
        return self.env.read_bytes()

    def stat_env(self):
        return self.env.lstat()


class TestPlatform(Base):
    def test_native_platform_no_mutation(self):
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        result = self.run_helper(release='5.15.0-generic')
        self.assertEqual(result['mode'], 'unmanaged')
        self.assertFalse(result['changed'])
        self.assertEqual(self.read_bytes(), before)

    def test_native_docker_no_mutation(self):
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        result = self.run_helper(runner=_runner_factory(os_name='Ubuntu 22.04'))
        self.assertEqual(result['mode'], 'unmanaged')
        self.assertEqual(self.read_bytes(), before)

    def test_mirrored_no_mutation(self):
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        result = self.run_helper(runner=_runner_factory(mode='mirrored'))
        self.assertEqual(result['mode'], 'unmanaged')
        self.assertEqual(self.read_bytes(), before)


class TestFirstWrite(Base):
    def test_first_write_preserves_unrelated(self):
        original = '# comment\nFOO=bar\nSECRET=abc\n'
        self.write_env(original)
        result = self.run_helper()
        self.assertTrue(result['changed'])
        self.assertEqual(result['address'], 'host.docker.internal')
        text = self.env.read_text()
        self.assertIn('# comment\n', text)
        self.assertIn('FOO=bar\n', text)
        self.assertIn('SECRET=abc\n', text)
        self.assertIn('ODS_AGENT_BIND=127.0.0.1\n', text)
        self.assertIn('ODS_AGENT_HOST=host.docker.internal\n', text)
        self.assertIn('ODS_AGENT_ADDRESS_MODE=wsl-nat-loopback\n', text)

    def test_rerun_byte_identity(self):
        self.write_env('FOO=bar\n')
        self.run_helper()
        first = self.read_bytes()
        first_stat = self.stat_env()
        result = self.run_helper()
        self.assertFalse(result['changed'])
        self.assertEqual(self.read_bytes(), first)
        self.assertEqual(self.stat_env().st_ino, first_stat.st_ino)

    def test_wsl_ip_change_keeps_loopback_route(self):
        self.write_env('FOO=bar\n')
        self.run_helper()
        first = self.read_bytes()
        first_stat = self.stat_env()
        result = self.run_helper(runner=_runner_factory(address='10.9.9.9'))
        self.assertFalse(result['changed'])
        self.assertEqual(result['address'], 'host.docker.internal')
        self.assertEqual(self.read_bytes(), first)
        self.assertEqual(self.stat_env().st_ino, first_stat.st_ino)

    def test_legacy_wsl_eth0_route_migrates(self):
        self.write_env('FOO=bar\nODS_AGENT_BIND=10.1.2.3\nODS_AGENT_HOST=10.1.2.3\n'
                       'ODS_AGENT_ADDRESS_MODE=wsl-nat\n')
        result = self.run_helper()
        self.assertTrue(result['changed'])
        self.assertIn('FOO=bar\n', self.env.read_text())
        self.assertIn('ODS_AGENT_BIND=127.0.0.1\n', self.env.read_text())
        self.assertIn('ODS_AGENT_HOST=host.docker.internal\n', self.env.read_text())
        self.assertIn('ODS_AGENT_ADDRESS_MODE=wsl-nat-loopback\n', self.env.read_text())

    def test_private_write_mode(self):
        self.write_env('FOO=bar\n')
        self.run_helper()
        self.assertEqual(stat.S_IMODE(self.stat_env().st_mode), 0o600)


class TestExplicit(Base):
    def test_explicit_pair_byte_identity(self):
        original = 'ODS_AGENT_BIND=1.2.3.4\nODS_AGENT_HOST=1.2.3.4\n'
        self.write_env(original)
        before = self.read_bytes()
        result = self.run_helper()
        self.assertFalse(result['changed'])
        self.assertEqual(result['mode'], 'explicit')
        self.assertEqual(self.read_bytes(), before)

    def test_explicit_one_sided_byte_identity(self):
        original = 'ODS_AGENT_BIND=1.2.3.4\n'
        self.write_env(original)
        before = self.read_bytes()
        result = self.run_helper()
        self.assertFalse(result['changed'])
        self.assertEqual(self.read_bytes(), before)

    def test_explicit_quoted_byte_identity(self):
        original = 'ODS_AGENT_BIND="1.2.3.4"\nODS_AGENT_HOST="1.2.3.4"\n'
        self.write_env(original)
        before = self.read_bytes()
        result = self.run_helper()
        self.assertFalse(result['changed'])
        self.assertEqual(self.read_bytes(), before)

    def test_duplicate_marker_error(self):
        self.write_env('ODS_AGENT_BIND=1.2.3.4\nODS_AGENT_BIND=5.6.7.8\n')
        with self.assertRaises(mod.HelperError):
            self.run_helper()

    def test_invalid_marker_error(self):
        self.write_env('ODS_AGENT_ADDRESS_MODE=bogus\n')
        with self.assertRaises(mod.HelperError):
            self.run_helper()


class TestFilesystem(Base):
    def test_public_environment_refused_without_mutation(self):
        self.write_env('SECRET=preserved\n', mode=0o644)
        before = self.read_bytes()
        with self.assertRaises(mod.HelperError):
            self.run_helper()
        self.assertEqual(self.read_bytes(), before)

    def test_symlink_rejected(self):
        target = self.root / 'real.env'
        target.write_text('FOO=bar\n')
        os.chmod(target, 0o600)
        os.symlink(target, self.env)
        with self.assertRaises(mod.HelperError):
            self.run_helper()

    def test_hardlink_rejected(self):
        self.write_env('FOO=bar\n')
        os.link(self.env, self.root / 'other.env')
        with self.assertRaises(mod.HelperError):
            self.run_helper()

    def test_wrong_owner_skipped_when_not_root(self):
        if os.getuid() == 0:
            self.skipTest('running as root')
        self.write_env('FOO=bar\n')
        with mock.patch('os.getuid', return_value=self.uid + 1):
            with self.assertRaises(mod.HelperError):
                mod.run(self.root, uid=self.uid + 1,
                        runner=_runner_factory(),
                        release='5.15.90.1-microsoft-standard-WSL2')


class TestAddress(Base):
    def test_public_ip_rejected(self):
        self.write_env('FOO=bar\n')
        with self.assertRaises(mod.HelperError):
            self.run_helper(runner=_runner_factory(address='8.8.8.8'))

    def test_multiple_ips_rejected(self):
        self.write_env('FOO=bar\n')

        def runner(cmd, **kw):
            if cmd[:2] == ['docker', 'info']:
                return 'Docker Desktop'
            if cmd[:2] == ['wslinfo', '--networking-mode']:
                return 'nat'
            if cmd[:2] == ['ip', '-j']:
                return json.dumps([{
                    'flags': ['UP'],
                    'addr_info': [
                        {'local': '10.1.2.3', 'scope': 'global', 'family': 'inet'},
                        {'local': '10.1.2.4', 'scope': 'global', 'family': 'inet'},
                    ],
                }])
            raise AssertionError(cmd)

        with self.assertRaises(mod.HelperError):
            self.run_helper(runner=runner)

    def test_down_interface_rejected(self):
        self.write_env('FOO=bar\n')

        def runner(cmd, **kw):
            if cmd[:2] == ['docker', 'info']:
                return 'Docker Desktop'
            if cmd[:2] == ['wslinfo', '--networking-mode']:
                return 'nat'
            if cmd[:2] == ['ip', '-j']:
                return json.dumps([{
                    'flags': ['DOWN'],
                    'addr_info': [{'local': '10.1.2.3', 'scope': 'global', 'family': 'inet'}],
                }])
            raise AssertionError(cmd)

        with self.assertRaises(mod.HelperError):
            self.run_helper(runner=runner)


class TestConcurrency(Base):
    def test_concurrent_env_change_no_replacement(self):
        self.write_env('FOO=bar\n')
        original_stat = self.stat_env()

        def detect():
            self.env.write_text('FOO=changed\n')
            os.chmod(self.env, 0o600)
            return '10.1.2.3'

        with self.assertRaises(mod.HelperError):
            self.run_helper(detect=detect)
        self.assertEqual(self.read_bytes(), b'FOO=changed\n')
        self.assertEqual(self.stat_env().st_ino, original_stat.st_ino)

    def test_concurrent_address_change_no_replacement(self):
        self.write_env('FOO=bar\n')
        original_bytes = self.read_bytes()
        original_stat = self.stat_env()
        calls = {'n': 0}

        def detect():
            calls['n'] += 1
            return '10.1.2.3' if calls['n'] == 1 else '10.9.9.9'

        with self.assertRaises(mod.HelperError):
            self.run_helper(detect=detect)
        self.assertEqual(self.read_bytes(), original_bytes)
        self.assertEqual(self.stat_env().st_ino, original_stat.st_ino)
        self.assertEqual(list(self.root.glob('.env-agent-*')), [])

    def test_temp_cleanup_on_failure(self):
        self.write_env('FOO=bar\n')

        def detect():
            return '8.8.8.8'

        with self.assertRaises(mod.HelperError):
            self.run_helper(detect=detect)
        leftovers = [p for p in self.root.iterdir() if p.name.startswith('.env-agent-')]
        self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()

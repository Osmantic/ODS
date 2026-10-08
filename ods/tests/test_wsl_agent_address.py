import importlib.util
import io
import json
import os
import stat
import subprocess
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

    def _unreachable_docker(self, error):
        """A runner whose `docker info` fails the way WSL commonly fails it."""
        def runner(cmd, **kw):
            if cmd[:2] == ['docker', 'info']:
                raise error
            raise AssertionError('unexpected cmd after a failed platform probe: %r' % (cmd,))
        return runner

    def test_unreachable_docker_is_unmanaged_without_mutation(self):
        # A stopped engine, an unreadable socket, or a missing CLI is an
        # ordinary WSL state. Reporting {'error': 'internal'} made the caller
        # print "Check WSL networking and private .env ownership", a misleading
        # diagnosis for a host that simply had no reachable engine.
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        cases = {
            'daemon down': subprocess.CalledProcessError(1, ['docker', 'info']),
            'cli missing': FileNotFoundError(2, 'No such file or directory', 'docker'),
            'socket not permitted': PermissionError(13, 'Permission denied'),
            'probe timed out': subprocess.TimeoutExpired(['docker', 'info'], 20),
            'subprocess error': subprocess.SubprocessError('boom'),
        }
        for label, error in cases.items():
            with self.subTest(case=label):
                result = self.run_helper(runner=self._unreachable_docker(error))
                self.assertEqual(result['mode'], 'unmanaged')
                self.assertFalse(result['changed'])
                self.assertIsNone(result['address'])
                self.assertEqual(self.read_bytes(), before)

    def test_platform_probe_does_not_swallow_other_failures(self):
        self.write_env('FOO=bar\n')
        with self.assertRaises(RuntimeError):
            self.run_helper(runner=self._unreachable_docker(RuntimeError('unexpected bug')))


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
        self.assertIn('ODS_AGENT_BIND=10.1.2.3\n', text)
        self.assertIn('ODS_AGENT_HOST=host.docker.internal\n', text)
        self.assertIn('ODS_AGENT_ADDRESS_MODE=wsl-nat-bridge\n', text)

    def test_rerun_byte_identity(self):
        self.write_env('FOO=bar\n')
        self.run_helper()
        first = self.read_bytes()
        first_stat = self.stat_env()
        result = self.run_helper()
        self.assertFalse(result['changed'])
        self.assertEqual(self.read_bytes(), first)
        self.assertEqual(self.stat_env().st_ino, first_stat.st_ino)

    def test_wsl_ip_change_updates_private_bind(self):
        self.write_env('FOO=bar\n')
        self.run_helper()
        first = self.read_bytes()
        first_stat = self.stat_env()
        result = self.run_helper(runner=_runner_factory(address='10.9.9.9'))
        self.assertTrue(result['changed'])
        self.assertEqual(result['address'], 'host.docker.internal')
        self.assertNotEqual(self.read_bytes(), first)
        self.assertNotEqual(self.stat_env().st_ino, first_stat.st_ino)
        self.assertIn('ODS_AGENT_BIND=10.9.9.9\n', self.env.read_text())

    def test_legacy_wsl_eth0_route_migrates(self):
        self.write_env('FOO=bar\nODS_AGENT_BIND=10.1.2.3\nODS_AGENT_HOST=10.1.2.3\n'
                       'ODS_AGENT_ADDRESS_MODE=wsl-nat\n')
        result = self.run_helper()
        self.assertTrue(result['changed'])
        self.assertIn('FOO=bar\n', self.env.read_text())
        self.assertIn('ODS_AGENT_BIND=10.1.2.3\n', self.env.read_text())
        self.assertIn('ODS_AGENT_HOST=host.docker.internal\n', self.env.read_text())
        self.assertIn('ODS_AGENT_ADDRESS_MODE=wsl-nat-bridge\n', self.env.read_text())

    def test_loopback_route_migrates(self):
        self.write_env('ODS_AGENT_BIND=127.0.0.1\nODS_AGENT_HOST=host.docker.internal\n'
                       'ODS_AGENT_ADDRESS_MODE=wsl-nat-loopback\n')
        result = self.run_helper()
        self.assertTrue(result['changed'])
        self.assertIn('ODS_AGENT_BIND=10.1.2.3\n', self.env.read_text())
        self.assertIn('ODS_AGENT_ADDRESS_MODE=wsl-nat-bridge\n', self.env.read_text())

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

    def _runner_with_address_probe(self, probe_result):
        def runner(cmd, **kw):
            if cmd[:2] == ['docker', 'info']:
                return 'Docker Desktop'
            if cmd[:2] == ['wslinfo', '--networking-mode']:
                return 'nat'
            if cmd[:2] == ['ip', '-j']:
                if isinstance(probe_result, Exception):
                    raise probe_result
                return probe_result
            raise AssertionError('unexpected cmd after a failed probe: %r' % (cmd,))
        return runner

    def _assert_address_error(self, probe_result):
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        with self.assertRaises(mod.HelperError) as caught:
            self.run_helper(runner=self._runner_with_address_probe(probe_result))
        self.assertEqual(str(caught.exception), 'address')
        self.assertEqual(self.read_bytes(), before)

    def test_absent_ip_command_is_address_not_internal(self):
        # A host without iproute2 is an environment condition, not a bug.
        self._assert_address_error(FileNotFoundError(2, 'No such file or directory', 'ip'))

    def test_failed_ip_probe_is_address(self):
        self._assert_address_error(subprocess.CalledProcessError(1, ['ip']))

    def test_ip_probe_timeout_is_address(self):
        self._assert_address_error(subprocess.TimeoutExpired(['ip'], 10))

    def test_unparseable_ip_output_is_address(self):
        self._assert_address_error('not json at all')

    def test_address_probe_does_not_swallow_other_failures(self):
        self.write_env('FOO=bar\n')
        with self.assertRaises(RuntimeError):
            self.run_helper(runner=self._runner_with_address_probe(RuntimeError('unexpected bug')))


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


class TestNetworkingProbe(Base):
    """The wslinfo probe decides whether the managed NAT bridge applies."""

    def _runner_with_probe(self, error):
        def runner(cmd, **kw):
            if cmd[:2] == ['docker', 'info']:
                return 'Docker Desktop'
            if cmd[:2] == ['wslinfo', '--networking-mode']:
                raise error
            raise AssertionError('unexpected cmd after a failed probe: %r' % (cmd,))
        return runner

    def _assert_networking(self, error):
        self.write_env('FOO=bar\n')
        before = self.read_bytes()
        with self.assertRaises(mod.HelperError) as caught:
            self.run_helper(runner=self._runner_with_probe(error))
        self.assertEqual(str(caught.exception), 'networking')
        self.assertEqual(self.read_bytes(), before)

    def test_absent_probe_is_networking_not_internal(self):
        # Reporting 'internal' told every caller that an unexpected bug had
        # occurred, when the real condition is that no usable WSL networking
        # mode could be established.
        self._assert_networking(FileNotFoundError(2, 'No such file or directory', 'wslinfo'))

    def test_unsupported_probe_flag_is_networking(self):
        # WSL builds that predate --networking-mode reject the flag; that is a
        # networking condition, not an internal fault.
        self._assert_networking(subprocess.CalledProcessError(1, ['wslinfo']))

    def test_probe_timeout_is_networking(self):
        self._assert_networking(subprocess.TimeoutExpired(['wslinfo'], 10))

    def test_unlisted_mode_still_raises_networking(self):
        self.write_env('FOO=bar\n')
        with self.assertRaises(mod.HelperError) as caught:
            self.run_helper(runner=_runner_factory(mode='bridged'))
        self.assertEqual(str(caught.exception), 'networking')

    def test_probe_does_not_swallow_other_failures(self):
        self.write_env('FOO=bar\n')
        with self.assertRaises(RuntimeError):
            self.run_helper(runner=self._runner_with_probe(RuntimeError('unexpected bug')))


class TestMainContract(Base):
    """ods_prepare_wsl_agent_address depends on this exit code and payload."""

    WSL = '5.15.90.1-microsoft-standard-WSL2'

    def test_unreachable_docker_exits_zero_unmanaged(self):
        # The shell caller treats a non-zero exit as "Could not prepare the WSL
        # host-agent address. Check WSL networking and private .env ownership."
        # An unreachable engine must not take that path.
        self.write_env('FOO=bar\n')
        before = self.read_bytes()

        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        out = io.StringIO()
        with mock.patch('platform.release', return_value=self.WSL), \
                mock.patch('subprocess.check_output', boom), \
                mock.patch('sys.stdout', out):
            code = mod.main([str(self.root)])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()),
                         {'changed': False, 'mode': 'unmanaged', 'address': None})
        self.assertEqual(self.read_bytes(), before)

    def test_unexpected_failure_still_reports_internal(self):
        self.write_env('FOO=bar\n')

        def boom(cmd, **kw):
            raise RuntimeError('unexpected bug')

        out = io.StringIO()
        with mock.patch('platform.release', return_value=self.WSL), \
                mock.patch('subprocess.check_output', boom), \
                mock.patch('sys.stdout', out):
            code = mod.main([str(self.root)])

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {'error': 'internal'})

    def test_probe_failure_reports_networking_reason(self):
        # This is what the shell caller prints as "reason: networking" instead
        # of sending the user to inspect .env ownership for a networking cause.
        self.write_env('FOO=bar\n')

        def probe_unavailable(cmd, **kw):
            if cmd[:2] == ['wslinfo', '--networking-mode']:
                raise FileNotFoundError(2, 'No such file or directory', 'wslinfo')
            return 'Docker Desktop'

        out = io.StringIO()
        with mock.patch('platform.release', return_value=self.WSL), \
                mock.patch('subprocess.check_output', probe_unavailable), \
                mock.patch('sys.stdout', out):
            code = mod.main([str(self.root)])

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {'error': 'networking'})


if __name__ == '__main__':
    unittest.main()

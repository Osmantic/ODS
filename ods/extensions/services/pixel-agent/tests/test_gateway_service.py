import sys
import errno
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
from pixel_gateway_service import LaunchdGatewayService, SystemdGatewayService
from pixel_access_bridge import AccessError


class GatewayServiceTests(unittest.TestCase):
    def test_root_process_opt_in_is_restricted_to_promoter(self):
        for target, uid in [('system/com.ods.pixel-native-gateway', 0),
                            ('system/com.ods.pixel-native-manager', 0),
                            ('system/com.ods.pixel-native-promoter', 501)]:
            with self.subTest(target=target, uid=uid), self.assertRaisesRegex(ValueError, 'root-service-process'):
                LaunchdGatewayService(Mock(), ValueError, target, Mock(),
                    process={'uid': uid, 'gid': 0, 'executable': '/python'}, allow_root_process=True)

    def test_systemd_transaction_preserves_existing_receipt_format(self):
        command = Mock(return_value='MainPID=123\nActiveState=active\nExecMainStartTimestampMonotonic=999')
        service = SystemdGatewayService(command, ValueError, 'fixture.service')
        boot = '11111111-2222-3333-4444-555555555555'
        with patch('pixel_gateway_service.Path.read_text', return_value=boot + '\n'):
            self.assertEqual(service.transaction_identity(timeout=4), {'pid':123, 'started':999, 'boot':boot})
        command.assert_called_with(['systemctl', 'show', 'fixture.service',
            '--property=MainPID,ActiveState,ExecMainStartTimestampMonotonic'], timeout=4)
        for raw in ('', 'MainPID=0\nActiveState=active\nExecMainStartTimestampMonotonic=999',
                    'MainPID=123\nActiveState=failed\nExecMainStartTimestampMonotonic=999'):
            command.return_value = raw
            with self.assertRaises(ValueError):
                service.transaction_identity()

    def test_boot_read_failure_is_not_fabricated(self):
        service = SystemdGatewayService(Mock(), ValueError, 'fixture.service')
        with patch('pixel_gateway_service.Path.read_text', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, 'settings-process-unavailable'):
                service.boot_identity()

    def test_native_transaction_uses_kernel_boot_uuid_and_stable_identity(self):
        boot = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
        command = Mock(return_value=boot + '\n')
        service = LaunchdGatewayService(command, ValueError, 'system/com.ods.fixture', Mock())
        service.process_identity = Mock(return_value=(123, 1700000000, 123456))
        self.assertEqual(service.transaction_identity(),
            {'pid':123, 'started':1700000000123456, 'boot':boot.lower()})
        self.assertEqual(command.call_args.args[0], ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'])
        self.assertEqual(service.process_identity.call_count, 2)
        service.process_identity.side_effect = [(123, 1700000000, 123456), (123, 1700000001, 123456)]
        with self.assertRaisesRegex(ValueError, 'process-changed'):
            service.transaction_identity()

    def test_native_transaction_invalid_boot_and_deadline_fail_closed(self):
        service = LaunchdGatewayService(Mock(return_value='invalid'), ValueError, 'system/com.ods.fixture', Mock())
        service.process_identity = Mock(return_value=(123, 1700000000, 0))
        with self.assertRaisesRegex(ValueError, 'settings-process-unavailable'):
            service.transaction_identity()
        with patch('pixel_gateway_service.time.monotonic', side_effect=[100, 100, 104]):
            with self.assertRaisesRegex(ValueError, 'operation-timeout'):
                service.transaction_identity(timeout=3)

    def test_native_identity_requires_deployment_specification(self):
        command = Mock()
        service = LaunchdGatewayService(command, ValueError, 'system/com.ods.fixture', Mock())
        with self.assertRaisesRegex(ValueError, 'specification-required'):
            service.process_identity()
        command.assert_not_called()

    def test_native_identity_rechecks_service_pid(self):
        spec = {'uid':501, 'gid':20, 'executable':'/fixture/node'}
        service = LaunchdGatewayService(Mock(), ValueError, 'system/com.ods.fixture', Mock(), process=spec)
        service.pid = Mock(return_value=123)
        with patch('pixel_macos_process.process_identity', return_value=(123, 'creation')) as identity:
            self.assertEqual(service.process_identity(), (123, 'creation'))
            identity.assert_called_once_with(123, **spec)
            self.assertEqual(service.pid.call_count, 2)
            service.pid.side_effect = [123, 456]
            with self.assertRaisesRegex(ValueError, 'process-changed'):
                service.process_identity()

    def test_native_identity_converts_failure_and_shares_deadline(self):
        from pixel_macos_process import ProcessIdentityError
        service = LaunchdGatewayService(Mock(), RuntimeError, 'system/com.ods.fixture', Mock(),
            process={'uid':501, 'gid':20, 'executable':'/fixture/node'})
        service.pid = Mock(return_value=123)
        with patch('pixel_macos_process.process_identity', side_effect=ProcessIdentityError('unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                service.process_identity()
        with patch('pixel_gateway_service.time.monotonic', side_effect=[100, 100, 104]), \
                patch('pixel_macos_process.process_identity', return_value=(123,)):
            with self.assertRaisesRegex(RuntimeError, 'operation-timeout'):
                service.process_identity(timeout=3)

    def test_launchd_binding_checks_file_snapshot_and_file_again(self):
        service = LaunchdGatewayService(Mock(return_value='snapshot'), ValueError,
                                       'system/com.ods.fixture', Mock())
        expected = {'Label': 'com.ods.fixture'}
        binding = {'sha256': 'fixture'}
        with patch('pixel_macos_custody.launchd_document_binding', return_value=binding) as disk, \
                patch('pixel_macos_custody.verify_loaded_launchd_definition') as loaded:
            self.assertEqual(service.installation_binding('/protected.plist', expected), binding)
            self.assertEqual(disk.call_count, 2)
            loaded.assert_called_once_with('snapshot', 'system/com.ods.fixture',
                                           '/protected.plist', expected)
            disk.side_effect = [binding, {'sha256': 'changed'}]
            with self.assertRaisesRegex(ValueError, 'launchd-document-changed'):
                service.installation_binding('/protected.plist', expected)

    def test_systemd_pid_and_timeout_preserved(self):
        command = Mock(return_value='123')
        service = SystemdGatewayService(command, ValueError, 'fixture.service')
        self.assertEqual(service.pid(timeout=2, require_running=True), 123)
        command.assert_called_once_with(['systemctl', 'show', 'fixture.service', '--property=MainPID', '--value'], timeout=2)
        command.return_value = '0'
        self.assertEqual(service.pid(), 0)
        with self.assertRaises(ValueError):
            service.pid(require_running=True)
        command.return_value = 'broken'
        with self.assertRaises(ValueError):
            service.pid()

    def test_systemd_requires_positive_stopped_evidence(self):
        command = Mock()
        service = SystemdGatewayService(command, ValueError, 'fixture.service')
        for value in ('MainPID=0\nActiveState=failed\nControlGroup=',
                      'MainPID=0\nActiveState=inactive\nControlGroup='):
            command.return_value = value
            service.assert_stopped()
        for value in ('MainPID=2\nActiveState=failed', 'MainPID=0\nActiveState=active',
                      '', 'MainPID=0\nActiveState=failed\nControlGroup=/../../etc'):
            command.return_value = value
            with self.assertRaises(ValueError):
                service.assert_stopped()

    def test_launchd_checks_custody_and_ignores_nested_resource_state(self):
        target = 'system/com.ods.pixel.gateway'
        verify = Mock()
        command = Mock(return_value=target + ' = {\n\tstate = running\n\tpid = 123\n\tresource = {\n\t\tstate = active\n\t\tpid = 456\n\t}\n}')
        service = LaunchdGatewayService(command, ValueError, target, verify)
        self.assertEqual(service.pid(timeout=3, require_running=True), 123)
        verify.assert_called_once_with()
        command.assert_called_once_with(['/bin/launchctl', 'print', target], timeout=3)
        service.restart(timeout=17)
        self.assertEqual(verify.call_count, 2)
        command.assert_called_with(['/bin/launchctl', 'kickstart', '-k', target], timeout=17)

    def test_launchd_stop_is_custody_checked_and_uses_bootout(self):
        target = 'system/com.ods.pixel.gateway'
        verify = Mock()
        command = Mock(return_value=target + ' = {\n\tstate = running\n\tpid = 123\n}')
        service = LaunchdGatewayService(command, ValueError, target, verify)
        with patch('pixel_gateway_service.sys.platform', 'linux'):
            service.stop(timeout=11)
        verify.assert_called_once_with()
        command.assert_called_with(['/bin/launchctl', 'bootout', target], timeout=11)

    def test_launchd_stop_records_tree_and_requires_every_birth_tuple_to_exit(self):
        from pixel_macos_process import ProcessIdentityError
        target = 'system/com.ods.pixel.gateway'
        command = Mock(side_effect=lambda args, timeout=20: (
            target + ' = {\n\tstate = running\n\tpid = 123\n}'
            if len(args) == 3 and args[1] == 'print' else ''))
        service = LaunchdGatewayService(command, AccessError, target, Mock())
        with patch('pixel_gateway_service.sys.platform', 'darwin'), \
                patch('pixel_macos_process.process_tree_snapshot', return_value=((123, 10, 20), (124, 11, 21))) as tree:
            service.stop(timeout=11)
        tree.assert_called_once_with(123)
        command.side_effect = AccessError('host-command-failed', returncode=113)
        with patch('pixel_macos_process.process_birth', side_effect=ProcessIdentityError(
                'gateway-process-unavailable', errno=errno.ESRCH)):
            service.assert_stopped()
            service.assert_stopped()
        self.assertEqual(service._stopping_tree, ((123, 10, 20), (124, 11, 21)))

    def test_launchd_stopped_check_rejects_unknown_command_and_kernel_errors(self):
        from pixel_macos_process import ProcessIdentityError
        command = Mock()
        service = LaunchdGatewayService(command, AccessError, 'system/com.ods.fixture', Mock())
        service._stopping_tree = ((123, 10, 20),)
        command.return_value = ''
        with self.assertRaisesRegex(AccessError, 'native-idle-unconfirmed'):
            service.assert_stopped()
        for code in (None, 1, 5, 127):
            command.side_effect = AccessError('host-command-failed', returncode=code)
            with self.assertRaises(AccessError):
                service.assert_stopped()
        command.side_effect = AccessError('host-command-failed', returncode=113)
        for code in (None, 0, errno.EPERM, errno.EIO):
            with patch('pixel_macos_process.process_birth', side_effect=ProcessIdentityError(
                    'gateway-process-unavailable', errno=code)), self.assertRaises(AccessError):
                service.assert_stopped()

    def test_reload_verifies_stopped_job_and_disk_before_bootstrap(self):
        command, disk, loaded = Mock(), Mock(), Mock()
        service = LaunchdGatewayService(command, AccessError, 'system/com.ods.fixture', loaded,
            plist='/Library/LaunchDaemons/com.ods.fixture.plist', verify_definition=disk)
        service._stopping_tree = ((123, 10, 20),)
        service.assert_stopped = Mock()
        service.reload()
        service.assert_stopped.assert_called_once_with()
        disk.assert_called_once_with()
        loaded.assert_not_called()
        command.assert_called_once_with(['/bin/launchctl', 'bootstrap', 'system',
                                         '/Library/LaunchDaemons/com.ods.fixture.plist'])
        self.assertIsNone(service._stopping_tree)
        command.reset_mock()
        disk.side_effect = AccessError('custody')
        with self.assertRaisesRegex(AccessError, 'custody'):
            service.reload()
        command.assert_not_called()

    def test_new_service_restores_same_boot_stop_and_invalidates_before_bootstrap(self):
        from pixel_macos_process import ProcessIdentityError
        boot = '11111111-2222-3333-4444-555555555555'
        target = 'system/com.ods.fixture'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stop.json'
            value = {'target': target, 'boot': boot, 'definition': 'd' * 64,
                     'processes': [[123, 10, 20]]}
            path.write_text(json.dumps(value))
            def save(value):
                path.write_text(json.dumps(value))
            def command(args, **kwargs):
                if args[0] == '/usr/sbin/sysctl':
                    return boot
                if args[1] == 'print':
                    raise AccessError('host-command-failed', returncode=113)
                self.assertEqual(args[1], 'bootstrap')
                self.assertIsNone(json.loads(path.read_text()))
                return ''
            service = LaunchdGatewayService(command, AccessError, target, Mock(),
                plist='/Library/LaunchDaemons/com.ods.fixture.plist',
                save_stop=save, load_stop=lambda: json.loads(path.read_text()))
            service.definition = Mock(return_value='d' * 64)
            absent = ProcessIdentityError('gateway-process-unavailable', errno=errno.ESRCH)
            with patch('pixel_macos_process.process_birth', side_effect=absent):
                service.assert_stopped()
                service.assert_stopped()
                self.assertEqual(service._stopping_tree, ((123, 10, 20),))
                path.write_text(json.dumps(dict(value, boot='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')))
                with self.assertRaisesRegex(AccessError, 'witness-unavailable'):
                    service.assert_stopped()
                path.write_text(json.dumps(value))
                service.reload()
                with self.assertRaisesRegex(AccessError, 'witness-unavailable'):
                    service.assert_stopped()

    def test_launchd_malformed_duplicate_or_inconsistent_state_fails_closed(self):
        target = 'gui/501/com.ods.fixture'
        command = Mock()
        service = LaunchdGatewayService(command, ValueError, target, Mock())
        for fields in ('\tstate = running', '\tstate = running\n\tpid = x',
                       '\tstate = running\n\tpid = 1\n\tpid = 2',
                       '\tstate = waiting\n\tpid = 1', '\tstate = unknown',
                       '\tstate = running\n\tstate = running\n\tpid = 1'):
            command.return_value = target + ' = {\n' + fields + '\n}'
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                service.pid()
        command.return_value = 'system/other = {\n\tstate = running\n\tpid = 1\n}'
        with self.assertRaises(ValueError):
            service.pid()

    def test_launchd_no_pid_does_not_authorize_stopped_recovery(self):
        target = 'system/com.ods.fixture'
        command = Mock(return_value=target + ' = {\n\tstate = not running\n}')
        service = LaunchdGatewayService(command, ValueError, target, Mock())
        self.assertEqual(service.pid(), 0)
        for action in (lambda: service.pid(require_running=True), service.assert_stopped,
                       service.boundary, service.reload):
            with self.assertRaises(ValueError):
                action()

    def test_failed_custody_never_invokes_launchctl(self):
        command = Mock()
        service = LaunchdGatewayService(command, ValueError, 'system/com.ods.fixture',
                                       Mock(side_effect=ValueError('custody')))
        for action in (service.pid, service.restart):
            with self.assertRaisesRegex(ValueError, 'custody'):
                action()
        command.assert_not_called()

    def test_target_is_not_a_command_or_request_url(self):
        for target in ('com.ods.fixture', 'system/../fixture', 'system/fixture --help',
                       'gui/root/fixture', 'system/a\nb', 'http://example.com'):
            with self.assertRaises(ValueError):
                LaunchdGatewayService(Mock(), ValueError, target, Mock())


if __name__ == '__main__':
    unittest.main()

"""Agent connection evidence: real local status fixtures, no provider or owner data."""
import ast
import io
import json
import os
from pathlib import Path
import socketserver
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pixel_connection_status import Fixture, NOW, broker, record, source  # noqa: E402
from pixel_connections import status  # noqa: E402
import pixel_connection_agent_status as helper  # noqa: E402
import pixel_access_client as client  # noqa: E402
from pixel_access_protocol import control_request, decode_frame  # noqa: E402
from pixel_access_bridge import AccessError  # noqa: E402


class AgentStatusTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Fixture(temporary.name)

    def view(self, fixture):
        return status.agent_projection(fixture.report(), now=NOW)

    def connected(self):
        return self.view(self.fixture().write(record_value=record(), token=True, broker_value=broker()))

    def test_saved_sign_in_permissions_and_sync_are_distinct_and_redacted(self):
        fixture = self.fixture().write(record_value=record(), token=True, broker_value=broker())
        fixture.token.write_text('PRIVATE_TOKEN_SENTINEL not even token JSON')
        result = self.view(fixture)
        self.assertTrue(result['recordConfigured'])
        self.assertTrue(result['localSavedSignIn'])
        self.assertTrue(result['ownerEnabled']['gmailRead'])
        self.assertTrue(result['providerPermissionObserved']['gmailRead'])
        self.assertTrue(result['reads']['email']['recent'])
        self.assertFalse(result['providerCheckedNow'])
        text = json.dumps(result)
        for private in ('PRIVATE_TOKEN_SENTINEL', 'owner@example.com', 'America/Chicago', 'http',
                        'recordCount', 'cloudModels', 'account', 'grantedScopes', 'waitingChanges'):
            self.assertNotIn(private, text)

    def test_disabled_read_is_not_a_disconnected_account(self):
        result = self.view(self.fixture().write(record_value=record(**{'gmail.read': False}),
            token=True, broker_value=broker(email=source('disabled'))))
        self.assertTrue(result['recordConfigured'])
        self.assertTrue(result['localSavedSignIn'])
        self.assertFalse(result['ownerEnabled']['gmailRead'])
        self.assertEqual(result['reads']['email']['lastObservedState'], 'disabled')
        self.assertNotEqual(result['connectionState'], 'not-connected')

    def test_missing_permission_is_not_missing_sign_in(self):
        result = self.view(self.fixture().write(record_value=record(), token=True,
            broker_value=broker(email=source('error', 'missing-scope'), grantedScopes=[])))
        self.assertEqual(result['connectionState'], 'missing-permission')
        self.assertTrue(result['localSavedSignIn'])
        self.assertFalse(result['providerPermissionObserved']['gmailRead'])

    def test_missing_token_and_unknown_scopes_do_not_claim_oauth_verified(self):
        missing = self.view(self.fixture().write(record_value=record(), broker_value=broker()))
        self.assertFalse(missing['localSavedSignIn'])
        self.assertEqual(missing['connectionState'], 'needs-reconnect')
        unknown = self.view(self.fixture().write(record_value=record(), token=True,
            broker_value=broker(grantedScopes=None)))
        self.assertIsNone(unknown['providerPermissionObserved']['gmailRead'])
        self.assertFalse(unknown['providerCheckedNow'])

    def test_stale_observation_is_reported_separately_from_its_last_state(self):
        result = self.view(self.fixture().write(record_value=record(), token=True,
            broker_value=broker(generated='2026-10-25T20:00:00Z')))
        self.assertEqual(result['connectionState'], 'behind')
        self.assertEqual(result['reads']['email']['lastObservedState'], 'ok')
        self.assertFalse(result['reads']['email']['recent'])
        self.assertFalse(result['providerCheckedNow'])

    def test_previous_sign_in_observations_do_not_prove_this_connection_permissions(self):
        newer = record()
        newer['connectors']['google']['connectedAt'] = '2026-10-25T20:29:45Z'
        result = self.view(self.fixture().write(record_value=newer, token=True, broker_value=broker()))
        self.assertTrue(result['recordConfigured'])
        self.assertTrue(result['localSavedSignIn'])
        self.assertTrue(result['ownerEnabled']['gmailRead'])
        self.assertEqual(result['connectionState'], 'first-sync')
        self.assertIsNone(result['providerPermissionObserved']['gmailRead'])
        self.assertIsNone(result['sourceCheckedAt'])
        self.assertIsNone(result['reads']['email']['recent'])

    def test_absent_is_distinct_from_unusable_or_unsupported(self):
        absent = self.view(self.fixture())
        self.assertEqual(absent['connectionState'], 'not-connected')
        self.assertIs(absent['recordConfigured'], False)
        broken = self.view(self.fixture().write(record_value='{bad json'))
        self.assertEqual(broken['connectionState'], 'unknown')
        self.assertIsNone(broken['recordConfigured'])
        unavailable = status.agent_projection(self.fixture().report('darwin'), now=NOW)
        self.assertFalse(unavailable['available'])
        self.assertEqual(unavailable['connectionState'], 'unknown')

    def test_future_or_malformed_metadata_fail_unknown_without_echo(self):
        for changed in ('2026-10-25T20:31:00Z', '2026-99-25T20:29:00Z'):
            value = self.fixture().write(record_value=record(), token=True, broker_value=broker()).report()
            value['connectors']['google']['checkedAt'] = changed
            with self.assertRaises(ValueError):
                status.agent_projection(value, now=NOW)
        fixture = self.fixture()
        fixture.write(record_value=record(), token=True, raw_broker=b'{PRIVATE_SENTINEL')
        result = self.view(fixture)
        self.assertNotEqual(result['connectionState'], 'not-connected')
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))

    def test_full_owner_status_and_unknown_fields_are_not_agent_responses(self):
        with self.assertRaises(ValueError):
            helper.validate_agent_public(self.fixture().report())
        for key, invalid in (('providerCheckedNow', True), ('observedAt', '2026-99-25T20:00:00Z'),
                             ('connectionState', []), ('available', 1), ('recordConfigured', 0),
                             ('account', 'PRIVATE_SENTINEL')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                helper.validate_agent_public(dict(self.connected(), **{key: invalid}))
        result = self.connected()
        result['reads']['email']['recordCount'] = 12
        with self.assertRaises(ValueError):
            helper.validate_agent_public(result)

    def test_report_handles_unusable_private_state_without_false_disconnection(self):
        with patch.object(status, 'report', side_effect=status.StatusError('PRIVATE_SENTINEL')):
            result = status.agent_report(surface='linux-systemd', now=NOW)
        self.assertFalse(result['available'])
        self.assertEqual(result['connectionState'], 'unknown')
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))


class AgentClientTests(unittest.TestCase):
    class Connection:
        def __init__(self, raw, uid=0):
            self.raw, self.uid, self.sent, self.timeout = raw, uid, None, None
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def settimeout(self, timeout):
            self.timeout = timeout
        def connect(self, path):
            self.path = path
        def getsockopt(self, *_args):
            return struct.pack('3i', 123, self.uid, 0)
        def sendall(self, raw):
            self.sent = raw
        def makefile(self, *_args):
            return io.BytesIO(self.raw)

    def test_fixed_read_authenticates_root_and_bounds_frames(self):
        connection = self.Connection(b'{"status":200,"body":{}}\n')
        with patch.object(client.socket, 'socket', return_value=connection), \
                patch.object(client.socket, 'SO_PEERCRED', 17, create=True), patch.object(client.sys, 'platform', 'linux'):
            self.assertEqual(client.request_access('connection-agent-status'), (200, {}))
        self.assertEqual(connection.sent, b'{"operation": "connection-agent-status"}\n')
        self.assertEqual(connection.timeout, 20)
        for raw, uid in ((b'{"status":200,"body":{}}\n', 1000), (b'x' * 8193 + b'\n', 0),
                         (b'{"status":200,"body":{}}', 0)):
            with self.subTest(uid=uid, size=len(raw)):
                connection = self.Connection(raw, uid)
                with patch.object(client.socket, 'socket', return_value=connection), \
                        patch.object(client.socket, 'SO_PEERCRED', 17, create=True), \
                        patch.object(client.sys, 'platform', 'linux'), self.assertRaises(ValueError):
                    client.request_access('connection-agent-status')
                if uid != 0:
                    self.assertIsNone(connection.sent)

    def test_unsupported_platform_or_missing_linux_peer_feature_sends_no_request(self):
        for platform, feature in (('darwin', True), ('linux', False)):
            with self.subTest(platform=platform, peerFeature=feature):
                connection = self.Connection(b'{"status":200,"body":{}}\n')
                socket_api = SimpleNamespace(AF_UNIX=client.socket.AF_UNIX, SOCK_STREAM=client.socket.SOCK_STREAM,
                    SOL_SOCKET=client.socket.SOL_SOCKET, socket=lambda *_args: connection)
                if feature:
                    socket_api.SO_PEERCRED = 17
                with patch.object(client, 'socket', socket_api), patch.object(client.sys, 'platform', platform), \
                        self.assertRaises(ValueError):
                    client.request_access('connection-agent-status')
                self.assertIsNone(connection.sent)

    def test_protocol_and_client_refuse_arguments(self):
        self.assertEqual(control_request({'operation': 'connection-agent-status'}), {'operation': 'connection-agent-status'})
        with self.assertRaises(ValueError):
            control_request({'operation': 'connection-agent-status', 'request': {}})
        with self.assertRaises(ValueError):
            client.request_access('connection-agent-status', {'path': 'PRIVATE_SENTINEL'})

    def test_exact_cli_main_emits_only_validated_private_fixture_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory).write(record_value=record(), token=True, broker_value=broker())
            value = status.agent_projection(fixture.report(), now=NOW)
        for code, body, accepted in ((200, value, True), (200, {'account': 'PRIVATE_SENTINEL'}, False),
                                     (403, {'error': 'PRIVATE_SENTINEL'}, False)):
            with self.subTest(code=code, accepted=accepted), patch.object(helper.sys, 'argv', ['helper']), \
                    patch.object(helper.sys, 'platform', 'linux'), patch.object(helper, '_protected_program'), \
                    patch.object(client, 'request_access', return_value=(code, body)) as request, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(helper.main(), 0 if accepted else 1)
                request.assert_called_once_with('connection-agent-status')
                emitted = json.loads(output.getvalue())
                self.assertEqual(emitted, value if accepted else {'error': 'connection-status-unavailable'})
                self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())

    def test_exact_cli_refuses_platform_or_arguments_before_custody_or_transport(self):
        for platform, arguments in (('darwin', ['helper']), ('linux', ['helper', '--path', 'PRIVATE_SENTINEL'])):
            with self.subTest(platform=platform), patch.object(helper.sys, 'platform', platform), \
                    patch.object(helper.sys, 'argv', arguments), patch.object(helper, '_protected_program') as protect, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(helper.main(), 1)
                protect.assert_not_called()
                self.assertEqual(json.loads(output.getvalue()), {'error': 'connection-status-unavailable'})

    @unittest.skipUnless(os.name == 'posix', 'POSIX program custody')
    def test_program_custody_keeps_type_mode_owner_and_fixed_path_checks(self):
        real_lstat = Path.lstat
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / 'program'
            program.mkdir(mode=0o755)
            files = [program / name for name in ('pixel_connection_agent_status.py', 'pixel_access_client.py')]
            for path in files:
                path.write_bytes(b'# public synthetic program\n')
                path.chmod(0o644)

            def root_owned(path, *args, **kwargs):
                info = real_lstat(path, *args, **kwargs)
                # Model installed system ancestors, not the disposable /tmp
                # mount. The owned program/files retain their real mode/type.
                mode = stat.S_IFDIR | 0o755 if path in program.parents else info.st_mode
                return SimpleNamespace(st_mode=mode, st_uid=0, st_nlink=info.st_nlink)

            with patch.object(helper, '__file__', str(files[0])), patch.object(helper, '_PROGRAM_ROOT', program), \
                    patch.object(Path, 'lstat', root_owned), patch.object(sys, 'path', list(sys.path)):
                helper._protected_program()
                files[1].chmod(0o666)
                with self.assertRaises(ValueError):
                    helper._protected_program()
                files[1].chmod(0o644)
                outside = program.parent / 'outside.py'
                outside.write_bytes(b'# outside\n')
                files[1].unlink()
                files[1].symlink_to(outside)
                with self.assertRaises(ValueError):
                    helper._protected_program()
                files[1].unlink()
                files[1].write_bytes(b'# public fixture\n')
                files[1].chmod(0o644)

            def foreign_client(path, *args, **kwargs):
                info = root_owned(path, *args, **kwargs)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=1000 if path == files[1] else 0, st_nlink=info.st_nlink)

            with patch.object(helper, '__file__', str(files[0])), patch.object(helper, '_PROGRAM_ROOT', program), \
                    patch.object(Path, 'lstat', foreign_client), self.assertRaises(ValueError):
                helper._protected_program()
            with patch.object(helper, '__file__', str(files[0])), self.assertRaises(ValueError):
                helper._protected_program()


class AgentServerTests(unittest.TestCase):
    def test_actual_handler_routes_only_fixed_read_and_preserves_owner_gate(self):
        path = Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host/access_mode_server.py'
        tree = ast.parse(path.read_bytes())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        handler = next(node for node in main.body if isinstance(node, ast.ClassDef) and node.name == 'Handler')
        payload = status._agent_empty(NOW)
        for uid, request, expected in ((1000, {'operation': 'connection-agent-status'}, 200),
                                       (2000, {'operation': 'connection-agent-status'}, 403),
                                       (1000, {'operation': 'connection-agent-status', 'request': {}}, 400)):
            with self.subTest(uid=uid, request=request):
                namespace = {'socketserver': socketserver, 'json': json, 'peer_ids': lambda _conn: (uid, uid),
                    'owner': SimpleNamespace(pw_uid=1000), 'control_request': control_request, 'decode_frame': decode_frame,
                    'make_adapter': lambda: object(), 'platform': SimpleNamespace(system=lambda: 'Linux', release=lambda: 'test'),
                    'AccessError': AccessError}
                exec(compile(ast.Module(body=[handler], type_ignores=[]), str(path), 'exec'), namespace)
                instance = namespace['Handler'].__new__(namespace['Handler'])
                instance.connection = SimpleNamespace(settimeout=lambda _timeout: None)
                instance.rfile, instance.wfile = io.BytesIO(json.dumps(request).encode() + b'\n'), io.BytesIO()
                with patch.object(status, 'agent_report', return_value=payload) as report:
                    instance.handle()
                reply = json.loads(instance.wfile.getvalue())
                self.assertEqual(reply['status'], expected)
                self.assertEqual(report.call_count, int(expected == 200))
                if expected == 200:
                    self.assertEqual(reply['body'], payload)


if __name__ == '__main__':
    unittest.main()

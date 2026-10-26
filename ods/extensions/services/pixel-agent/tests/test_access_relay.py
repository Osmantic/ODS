"""Cross-platform relay tests: no live Docker or WSL command is executed."""
import json
from pathlib import Path
import subprocess
import sys
import types
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
import pixel_access_relay as relay

OWNER_KEY = 'o' * 64
CONFIG = {'DASHBOARD_API_KEY': OWNER_KEY, 'PIXEL_OPENWEBUI_KEY': 'c' * 64}
CHANGE = {'mode': 'full-access', 'confirmed': True, 'revision': 'a' * 64}


class AccessRelayTests(unittest.TestCase):
    def setUp(self):
        # Mock subprocess.run below must not intercept Python's Windows version
        # probe while platform.system() initializes its own cache.
        system = patch.object(relay.platform, 'system', return_value='Windows')
        system.start()
        self.addCleanup(system.stop)

    def test_ingress_retains_the_owner_primary_group_when_using_socket_group(self):
        # A service with Group=ods-pixel does not inherit the owner's primary
        # group. Render the actual installer fragment for a differently named
        # owner/group, so the private controller socket remains reachable.
        root = Path(__file__).resolve().parents[4]
        installer = (root / 'installers/lib/pixel-host-install.sh').read_text(encoding='utf-8')
        fragment = installer.split("import pathlib, pwd, sys\n\nsource, target, owner, token_source, token_file",1)[1].split('\nPY\n',1)[0]
        fragment = 'import pathlib, pwd, sys\n\nsource, target, owner, token_source, token_file' + fragment
        fake_pwd = types.SimpleNamespace(getpwnam=lambda name: types.SimpleNamespace(pw_gid=1729))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'ingress.service'
            argv = ['-',str(root/'extensions/services/pixel-agent/host/pixel-ingress.service'),str(output),'owner-name','/private/token','/run/token']
            with patch.object(sys,'argv',argv), patch.dict(sys.modules,{'pwd':fake_pwd}):
                exec(compile(fragment,'ingress-unit-renderer','exec'),{})
            unit = output.read_text()
            self.assertIn('Group=ods-pixel\n',unit)
            self.assertIn('SupplementaryGroups=1729\n',unit)
            self.assertNotIn('__PIXEL_',unit)

    def test_windows_targets_inspected_agent_container_without_shell_or_secret_argv(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            output = (('b' * 64 + ' true pixel-edge').encode() if args[1] == 'inspect'
                      else json.dumps({'status':200, 'body':{'surface':'wsl-systemd'}}).encode())
            return types.SimpleNamespace(stdout=output)
        with patch.object(relay.platform, 'system', return_value='Windows'), patch.object(relay.subprocess, 'run', side_effect=run):
            self.assertEqual(relay.request_runtime_access('change', CHANGE, config=CONFIG), (200, {'surface':'wsl-systemd'}))
        self.assertEqual(calls[1][0][3], 'b' * 64)
        self.assertNotIn(OWNER_KEY, ' '.join(calls[1][0]))
        self.assertEqual(json.loads(calls[1][1]['input'])['request'], CHANGE)
        self.assertTrue(all('shell' not in options for _, options in calls))
        self.assertTrue(all('creationflags' in options for _, options in calls))

    def test_native_unsupported_platform_does_not_claim_a_mode(self):
        for platform in ('Windows', 'Darwin'):
            with self.subTest(platform=platform), patch.object(relay.platform, 'system', return_value=platform), patch.object(relay.subprocess, 'run') as run:
                code, value = relay.request_runtime_access('status', config={})
                self.assertEqual(code, 200)
                self.assertFalse(value['available'])
                self.assertEqual(value['effective_mode'], 'unknown')
                run.assert_not_called()

    def test_unconfirmed_and_wrong_scope_do_not_reach_docker(self):
        for value in ({**CHANGE, 'confirmed':False}, {**CHANGE, 'confirmed':'true'}, {**CHANGE, 'path':'/etc'}, {**CHANGE, 'mode':'root'}):
            with self.subTest(value=value), patch.object(relay.subprocess, 'run') as run:
                self.assertEqual(relay.request_runtime_access('change', value, config=CONFIG)[0], 400)
                run.assert_not_called()

    def test_same_owner_and_chat_credentials_are_rejected(self):
        with patch.object(relay.subprocess, 'run') as run, self.assertRaises(relay.AccessRelayError):
            relay.request_runtime_access('status', config={**CONFIG, 'PIXEL_OPENWEBUI_KEY':OWNER_KEY})
        run.assert_not_called()

    def test_foreign_or_stopped_container_is_not_used(self):
        for identity in ('b' * 64 + ' false pixel-edge', 'b' * 64 + ' true other-service'):
            with patch.object(relay.subprocess, 'run', return_value=types.SimpleNamespace(stdout=identity.encode())) as run, self.assertRaises(relay.AccessRelayError):
                relay.request_runtime_access('status', config=CONFIG)
            self.assertEqual(run.call_count, 1)

    def test_connections_on_windows_go_through_the_inspected_agent_container(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            output = (('b' * 64 + ' true pixel-edge').encode() if args[1] == 'inspect'
                      else json.dumps({'status': 200, 'body': {'schemaVersion': 1}}).encode())
            return types.SimpleNamespace(stdout=output)
        with patch.object(relay.subprocess, 'run', side_effect=run):
            self.assertEqual(relay.request_runtime_connections(config=CONFIG), (200, {'schemaVersion': 1}))
        sent = json.loads(calls[1][1]['input'])
        self.assertEqual((sent['path'], sent['request'], sent['timeout']), ('/v1/connections', None, 22))
        self.assertNotIn(OWNER_KEY, ' '.join(calls[1][0]))
        self.assertIn("'/v1/connections'", relay._CLIENT)

    def test_connections_are_not_available_without_the_linux_broker(self):
        for platform, config in (('Darwin', CONFIG), ('Darwin', {}), ('Windows', {})):
            with self.subTest(platform=platform, config=bool(config)), \
                    patch.object(relay.platform, 'system', return_value=platform), \
                    patch.object(relay.subprocess, 'run') as run:
                code, value = relay.request_runtime_connections(config=config)
                self.assertEqual(code, 200)
                self.assertFalse(value['available'])
                self.assertEqual(value['surface'], platform.lower())
                self.assertEqual(value['connectors']['google']['state'], 'not-connected')
                run.assert_not_called()

    def test_connections_on_a_linux_host_ask_its_own_controller(self):
        client = types.ModuleType('pixel_access_client')
        client.request_access = lambda operation: (200, {'asked': operation})
        with patch.object(relay.platform, 'system', return_value='Linux'), \
                patch.dict(sys.modules, {'pixel_access_client': client}), \
                patch.object(relay.subprocess, 'run') as run:
            self.assertEqual(relay.request_runtime_connections(config={}), (200, {'asked': 'connection-status'}))
            run.assert_not_called()

    def test_timeout_never_retries_or_falls_back_to_another_host(self):
        with patch.object(relay.subprocess, 'run', side_effect=[types.SimpleNamespace(stdout=('b'*64+' true pixel-edge').encode()), subprocess.TimeoutExpired('docker', 313)]) as run, self.assertRaises(relay.AccessRelayError):
            relay.request_runtime_access('change', CHANGE, config=CONFIG)
        self.assertEqual(run.call_count, 2)


def edge_answers(status, body, calls):
    """subprocess.run as the inspected Edge container would answer one controller request."""
    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = (('b' * 64 + ' true pixel-edge').encode() if args[1] == 'inspect'
                  else json.dumps({'status': status, 'body': body}).encode())
        return types.SimpleNamespace(stdout=output)
    return run


class ConnectionActionRelayTests(unittest.TestCase):
    def setUp(self):
        system = patch.object(relay.platform, 'system', return_value='Windows')
        system.start()
        self.addCleanup(system.stop)

    def test_a_windows_host_sends_each_action_through_the_inspected_agent_container(self):
        calls = []
        with patch.object(relay.subprocess, 'run', side_effect=edge_answers(200, {'outcome': 'started'}, calls)):
            self.assertEqual(relay.request_runtime_connection_action('connection-sync', {}, config=CONFIG),
                             (200, {'outcome': 'started'}))
        sent = json.loads(calls[1][1]['input'])
        self.assertEqual((sent['path'], sent['request'], sent['timeout']),
                         ('/v1/connections', {'operation': 'connection-sync', 'request': {}}, 30))
        self.assertNotIn(OWNER_KEY, ' '.join(calls[1][0]))
        self.assertEqual(calls[1][1]['timeout'], 33)

    def test_requests_outside_the_contract_never_leave_the_host(self):
        for operation, request in (('connection-sync', {'force': True}), ('connection-status', {}),
                                   ('connection-cancel', {'flowId': 'not-a-flow'})):
            with self.subTest(operation=operation), patch.object(relay.subprocess, 'run') as run:
                self.assertEqual(relay.request_runtime_connection_action(operation, request, config=CONFIG),
                                 (400, {'error': 'invalid-request'}))
                run.assert_not_called()

    def test_connected_apps_are_not_available_without_the_linux_broker(self):
        for platform, config in (('Darwin', CONFIG), ('Windows', {})):
            with self.subTest(platform=platform), patch.object(relay.platform, 'system', return_value=platform), \
                    patch.object(relay.subprocess, 'run') as run:
                self.assertEqual(relay.request_runtime_connection_action('connection-sync', {}, config=config),
                                 (409, {'error': 'not-available-on-this-system'}))
                run.assert_not_called()

    def test_a_linux_host_asks_its_own_controller(self):
        client = types.ModuleType('pixel_access_client')
        client.request_access = lambda operation, request: (200, {'outcome': 'applied'})
        with patch.object(relay.platform, 'system', return_value='Linux'), \
                patch.dict(sys.modules, {'pixel_access_client': client}), patch.object(relay.subprocess, 'run') as run:
            self.assertEqual(relay.request_runtime_connection_action('connection-activate', {}, config={}),
                             (200, {'outcome': 'applied'}))
            run.assert_not_called()

    def test_refusals_keep_only_their_code_and_an_odd_reply_is_never_passed_on(self):
        for status, body, expected in ((409, {'error': 'runtime-busy'}, (409, {'error': 'runtime-busy'})),
                                       (503, {'error': 'Private Path'}, (503, {'error': 'connection-change-unconfirmed'})),
                                       (409, {'error': 'runtime-busy', 'detail': 'x'},
                                        (409, {'error': 'connection-change-unconfirmed'}))):
            with self.subTest(body=body), patch.object(relay.subprocess, 'run', side_effect=edge_answers(status, body, [])):
                self.assertEqual(relay.request_runtime_connection_action('connection-sync', {}, config=CONFIG), expected)
        odd = {'outcome': 'started', 'token': 'private-sentinel'}
        with patch.object(relay.subprocess, 'run', side_effect=edge_answers(200, odd, [])), \
                self.assertRaisesRegex(relay.AccessRelayError, 'connection-change-unconfirmed'):
            relay.request_runtime_connection_action('connection-sync', {}, config=CONFIG)


if __name__ == '__main__': unittest.main()

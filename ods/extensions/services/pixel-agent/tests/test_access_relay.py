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

    def test_timeout_never_retries_or_falls_back_to_another_host(self):
        with patch.object(relay.subprocess, 'run', side_effect=[types.SimpleNamespace(stdout=('b'*64+' true pixel-edge').encode()), subprocess.TimeoutExpired('docker', 313)]) as run, self.assertRaises(relay.AccessRelayError):
            relay.request_runtime_access('change', CHANGE, config=CONFIG)
        self.assertEqual(run.call_count, 2)


if __name__ == '__main__': unittest.main()

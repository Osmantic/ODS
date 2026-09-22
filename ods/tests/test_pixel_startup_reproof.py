"""Host startup orchestration, without importing or starting the host server."""
import ast
import json
import logging
from pathlib import Path
import platform
import stat
import subprocess
import time
import types
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'bin/ods-host-agent.py'
tree = ast.parse(SOURCE.read_text())
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == '_reconcile_native_pixel_startup')


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.begin = Mock(return_value=(True, {}))
        self.end = Mock()
        self.scope = dict(Path=Path, platform=platform, stat_mod=stat, ast=ast,
                          subprocess=subprocess, json=json, time=time,
                          logger=logging.getLogger('test'),
                          _begin_model_lifecycle=self.begin, _end_model_lifecycle=self.end)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), self.scope)
        for target, value in ((platform, 'system'), (Path, 'exists'), (Path, 'lstat'),
                              (Path, 'stat'), (Path, 'read_text'),
                              (subprocess, 'run'), (time, 'sleep')):
            mock = self.enterContext(patch.object(target, value))
            setattr(self, value, mock)
        self.system.return_value = 'Darwin'
        self.exists.return_value = True
        self.lstat.return_value = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)
        self.stat.return_value = types.SimpleNamespace(st_size=100)
        self.read_text.return_value = 'STARTUP_REPROOF_VERSION = 1\n'
        self.run.return_value = types.SimpleNamespace(returncode=0, stderr=b'')

    def call(self):
        return self.scope['_reconcile_native_pixel_startup']()

    def test_monitor_continues_only_explicit_read_only_or_success_results(self):
        monitor = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                       and n.name == '_monitor_native_pixel_access')
        exec(compile(ast.Module(body=[monitor], type_ignores=[]), str(SOURCE), 'exec'), self.scope)
        check = Mock(side_effect=[True, True, None])
        self.scope['_reconcile_native_pixel_startup'] = check
        self.scope['_monitor_native_pixel_access']()
        self.assertEqual(check.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [unittest.mock.call(30)] * 2)

    def test_busy_preflight_can_be_checked_later_but_pending_cannot(self):
        failure = {'stage': 'unsafe-state', 'projection': {'scope': 'owner-host',
                   'available': True, 'busy': True, 'pending': False}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertIs(self.call(), True)
        self.run.assert_called_once()
        failure['projection']['pending'] = True
        self.run.return_value.stderr = json.dumps(failure).encode()
        self.assertIsNone(self.call())

    def test_uncertain_change_stops_monitor_even_with_busy_projection(self):
        failure = {'stage': 'change-failed', 'projection': {'scope': 'owner-host',
                   'available': True, 'busy': True, 'pending': False}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertIsNone(self.call())
        self.run.assert_called_once()

    def test_ready_executes_protected_helper_under_lifecycle_lock(self):
        self.call()
        self.assertEqual(self.run.call_args.args[0], ['/usr/bin/python3', '-I',
            '/usr/local/libexec/ods-pixel-access/pixel_access_reconcile.py', '--startup'])
        self.end.assert_called_once_with('pixel_startup_reproof')
        self.sleep.assert_not_called()

    def test_other_platform_or_missing_helper_is_read_only(self):
        self.system.return_value = 'Linux'
        self.call()
        self.system.return_value = 'Darwin'
        self.exists.return_value = False
        self.call()
        self.begin.assert_not_called()
        self.run.assert_not_called()

    def test_unsafe_helper_is_never_executed(self):
        self.lstat.return_value.st_uid = 501
        self.call()
        self.run.assert_not_called()

    def test_legacy_helper_without_startup_contract_is_not_executed(self):
        for text in ('', 'STARTUP_REPROOF_VERSION = True', 'STARTUP_REPROOF_VERSION = 2',
                     'STARTUP_REPROOF_VERSION = 1\nSTARTUP_REPROOF_VERSION = 1'):
            self.read_text.return_value = text
            self.call()
        self.run.assert_not_called()

    def test_busy_model_lifecycle_has_bounded_wait_without_execution(self):
        self.begin.return_value = (False, {})
        self.call()
        self.assertEqual(self.begin.call_count, 12)
        self.assertEqual(self.sleep.call_count, 11)
        self.run.assert_not_called()
        self.end.assert_not_called()

    def test_unavailable_preflight_retries_but_uncertain_change_does_not(self):
        failure = {'stage': 'unsafe-state', 'projection': {'available': False,
            'pending': False, 'busy': False, 'reason': 'admission-gate-unavailable'}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.call()
        self.assertEqual(self.run.call_count, 12)
        self.assertEqual(self.end.call_count, 12)
        self.run.reset_mock()
        failure['stage'] = 'change-failed'
        self.run.return_value.stderr = json.dumps(failure).encode()
        self.call()
        self.run.assert_called_once()

    def test_timeout_releases_lock_without_retry(self):
        self.run.side_effect = subprocess.TimeoutExpired('helper', 360)
        self.call()
        self.end.assert_called_once()
        self.run.assert_called_once()

    def test_missing_socket_then_ready_retries_only_read_only_preflight(self):
        self.run.side_effect = [
            types.SimpleNamespace(returncode=1, stderr=json.dumps({
                'stage': 'status-transport-unavailable', 'projection': {}}).encode()),
            types.SimpleNamespace(returncode=0, stderr=b''),
        ]
        self.call()
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.end.call_count, 2)
        self.sleep.assert_called_once_with(5)


if __name__ == '__main__':
    unittest.main()

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
monitor = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == '_monitor_native_pixel_access')
# The monitor's interval and backoff cap, exactly as the host agent defines them.
monitor_settings = [n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id in (
                        '_PIXEL_ACCESS_RECHECK_SECONDS', '_PIXEL_ACCESS_BACKOFF_CAP_SECONDS')
                        for t in n.targets)]
ATTENTION = ('attention', 'stage=unsafe-state reason=transition-recovery-required')


class Stop(Exception):
    """Ends the otherwise endless monitor loop after the scripted checks."""


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

    def run_monitor(self, checks, *, hold=(), verified=()):
        """Drive the monitor through scripted checks; return each wait it asked for."""
        waits = []
        self.scope.update(
            _reconcile_native_pixel_startup=Mock(side_effect=[*checks, Stop()]),
            _pixel_model_hold_pending=Mock(side_effect=[*hold, *[False] * 20]),
            _pixel_access_verified=Mock(side_effect=list(verified)),
            _wait_for_pixel_access_check=lambda delay, *, wake: waits.append((delay, wake)))
        exec(compile(ast.Module(body=[*monitor_settings, monitor], type_ignores=[]),
                     str(SOURCE), 'exec'), self.scope)
        with self.assertRaises(Stop):
            self.scope['_monitor_native_pixel_access']()
        return waits

    def test_monitor_keeps_checking_a_state_that_can_resolve_with_bounded_backoff(self):
        waits = self.run_monitor([('recheck', None), *[ATTENTION] * 7, ('recheck', None)])
        self.assertEqual([delay for delay, _wake in waits], [30, 30, 60, 120, 240, 300, 300, 300, 30])
        self.assertTrue(all(wake for _delay, wake in waits))

    def test_monitor_logs_attention_once_and_then_the_recovery(self):
        with self.assertLogs('test', level='INFO') as logs:
            self.run_monitor([ATTENTION, ATTENTION, ('recheck', None)])
        self.assertEqual(logs.output, [
            'WARNING:test:Pixel startup reproof requires attention '
            '(stage=unsafe-state reason=transition-recovery-required); the access monitor keeps checking',
            'INFO:test:Pixel access monitor resumed automatic reproof'])

    def test_monitor_never_reruns_the_helper_after_an_uncertain_change_until_verified(self):
        uncertain = ('uncertain', 'stage=change-failed reason=None')
        waits = self.run_monitor([uncertain, ('recheck', None)], verified=[False, False, True])
        check = self.scope['_reconcile_native_pixel_startup']
        # The helper (the only path that can submit a change) ran once before
        # and once after the read-only status showed a verified runtime.
        self.assertEqual(check.call_count, 3)
        self.assertEqual(self.scope['_pixel_access_verified'].call_count, 3)
        self.assertEqual(waits, [(30, False), (60, False), (120, False), (30, True), (30, True)])

    def test_monitor_leaves_an_open_model_transaction_to_resume_or_recover(self):
        waits = self.run_monitor([('recheck', None)], hold=[True, True])
        self.assertEqual(self.scope['_reconcile_native_pixel_startup'].call_count, 2)
        self.assertEqual(waits, [(30, False), (60, False), (30, True)])

    def test_monitor_stops_only_without_a_usable_helper(self):
        self.scope.update(_reconcile_native_pixel_startup=Mock(return_value=(None, None)),
                          _pixel_model_hold_pending=Mock(return_value=False),
                          _wait_for_pixel_access_check=Mock())
        exec(compile(ast.Module(body=[*monitor_settings, monitor], type_ignores=[]),
                     str(SOURCE), 'exec'), self.scope)
        self.scope['_monitor_native_pixel_access']()
        self.scope['_wait_for_pixel_access_check'].assert_not_called()

    def test_host_agent_starts_reproof_monitor_on_native_linux_and_macos(self):
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'main')
        startup = next(node for node in ast.walk(main) if isinstance(node, ast.If)
                       and any(isinstance(child, ast.Name)
                               and child.id == '_monitor_native_pixel_access'
                               for child in ast.walk(node)))
        enabled = compile(ast.Expression(startup.test), str(SOURCE), 'eval')
        for system, expected in (('Linux', True), ('Darwin', True), ('Windows', False)):
            self.system.return_value = system
            self.assertIs(eval(enabled, self.scope), expected)

    def test_busy_preflight_is_checked_again_but_pending_needs_attention(self):
        failure = {'stage': 'unsafe-state', 'projection': {'scope': 'owner-host',
                   'available': True, 'busy': True, 'pending': False}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertEqual(self.call(), ('recheck', None))
        self.run.assert_called_once()
        failure['projection']['pending'] = True
        self.run.return_value.stderr = json.dumps(failure).encode()
        self.assertEqual(self.call(), ('attention', 'stage=unsafe-state reason=None'))

    def test_uncertain_change_is_uncertain_even_with_busy_projection(self):
        failure = {'stage': 'change-failed', 'projection': {'scope': 'owner-host',
                   'available': True, 'busy': True, 'pending': False}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertEqual(self.call(), ('uncertain', 'stage=change-failed reason=None'))
        self.run.assert_called_once()

    def test_helper_exception_after_a_possible_change_is_uncertain(self):
        failure = {'stage': 'client-exception', 'exceptionType': 'OSError'}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertEqual(self.call(), ('uncertain', 'stage=client-exception reason=None'))
        self.run.assert_called_once()

    def test_ready_executes_protected_helper_under_lifecycle_lock(self):
        self.assertEqual(self.call(), ('recheck', None))
        self.assertEqual(self.run.call_args.args[0], ['/usr/bin/python3', '-I',
            '/usr/local/libexec/ods-pixel-access/pixel_access_reconcile.py', '--startup'])
        self.end.assert_called_once_with('pixel_startup_reproof')
        self.sleep.assert_not_called()

    def test_linux_reuses_the_same_protected_helper_and_lock(self):
        self.system.return_value = 'Linux'
        self.assertEqual(self.call(), ('recheck', None))
        self.assertEqual(self.run.call_args.args[0], ['/usr/bin/python3', '-I',
            '/usr/local/libexec/ods-pixel-access/pixel_access_reconcile.py', '--startup'])
        self.end.assert_called_once_with('pixel_startup_reproof')

    def test_other_platform_or_missing_helper_is_read_only(self):
        self.system.return_value = 'Windows'
        self.assertEqual(self.call(), (None, None))
        self.system.return_value = 'Darwin'
        self.exists.return_value = False
        self.assertEqual(self.call(), (None, None))
        self.begin.assert_not_called()
        self.run.assert_not_called()

    def test_unsafe_helper_is_never_executed(self):
        self.lstat.return_value.st_uid = 501
        self.assertEqual(self.call(), (None, None))
        self.run.assert_not_called()

    def test_legacy_helper_without_startup_contract_is_not_executed(self):
        for text in ('', 'STARTUP_REPROOF_VERSION = True', 'STARTUP_REPROOF_VERSION = 2',
                     'STARTUP_REPROOF_VERSION = 1\nSTARTUP_REPROOF_VERSION = 1'):
            self.read_text.return_value = text
            self.assertEqual(self.call(), (None, None))
        self.run.assert_not_called()

    def test_busy_model_lifecycle_has_bounded_wait_without_execution(self):
        self.begin.return_value = (False, {})
        self.assertEqual(self.call(), ('recheck', None))
        self.assertEqual(self.begin.call_count, 12)
        self.assertEqual(self.sleep.call_count, 11)
        self.run.assert_not_called()
        self.end.assert_not_called()

    def test_lock_contention_alone_is_a_deferral_not_a_readiness_warning(self):
        # A long model download owns the lifecycle lock for the whole window.
        # The helper never ran, so readiness was not observed to be degraded.
        self.begin.return_value = (False, {'operation': 'model_download',
                                           'target': 'model.gguf'})
        with self.assertLogs('test', level='INFO') as logs:
            self.assertEqual(self.call(), ('recheck', None))
        self.run.assert_not_called()
        self.assertEqual(logs.output, [
            'INFO:test:Pixel access reproof deferred while model_download is in progress'])

    def test_contention_then_unavailable_preflight_still_warns(self):
        self.begin.side_effect = [(False, {'operation': 'model_download'})] * 11 + [(True, {})]
        failure = {'stage': 'status-unavailable', 'projection': {'available': False,
            'pending': False, 'busy': False, 'reason': 'runtime-unavailable-or-busy'}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        with self.assertLogs('test', level='INFO') as logs:
            self.assertEqual(self.call(), ('recheck', None))
        self.run.assert_called_once()
        self.assertEqual(logs.output, [
            'WARNING:test:Pixel startup reproof readiness window exhausted '
            '(last stage=status-unavailable reason=runtime-unavailable-or-busy)'])

    def test_unavailable_preflight_retries_but_uncertain_change_does_not(self):
        failure = {'stage': 'unsafe-state', 'projection': {'available': False,
            'pending': False, 'busy': False, 'reason': 'admission-gate-unavailable'}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        with self.assertLogs('test', level='WARNING') as logs:
            self.assertEqual(self.call(), ('recheck', None))
        self.assertEqual(logs.output, [
            'WARNING:test:Pixel startup reproof readiness window exhausted '
            '(last stage=unsafe-state reason=admission-gate-unavailable)'])
        self.assertEqual(self.run.call_count, 12)
        self.assertEqual(self.end.call_count, 12)
        self.run.reset_mock()
        failure['stage'] = 'change-failed'
        self.run.return_value.stderr = json.dumps(failure).encode()
        self.assertEqual(self.call(), ('uncertain', 'stage=change-failed reason=admission-gate-unavailable'))
        self.run.assert_called_once()

    def test_boot_order_unavailability_retries_until_ready(self):
        # After a WSL restart the host agent can run this before Docker or the
        # Pixel edge container is up; both answers come from the read-only status.
        for reason in ('edge-container-unavailable', 'host-command-failed'):
            with self.subTest(reason=reason):
                self.run.reset_mock()
                self.sleep.reset_mock()
                self.end.reset_mock()
                failure = {'stage': 'unsafe-state', 'projection': {'available': False,
                    'pending': False, 'busy': False, 'reason': reason}}
                self.run.side_effect = [
                    types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode()),
                    types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode()),
                    types.SimpleNamespace(returncode=0, stderr=b''),
                ]
                self.assertEqual(self.call(), ('recheck', None))
                self.assertEqual(self.run.call_count, 3)
                self.assertEqual(self.end.call_count, 3)
                self.assertEqual(self.sleep.call_args_list, [unittest.mock.call(5)] * 2)
        self.run.side_effect = None

    def test_attention_names_stage_and_reason(self):
        failure = {'stage': 'unsafe-state', 'projection': {'available': True,
            'pending': True, 'busy': False, 'reason': 'transition-recovery-required'}}
        self.run.return_value = types.SimpleNamespace(returncode=1, stderr=json.dumps(failure).encode())
        self.assertEqual(self.call(), ATTENTION)
        self.run.assert_called_once()

    def test_timeout_releases_lock_without_retry(self):
        self.run.side_effect = subprocess.TimeoutExpired('helper', 360)
        self.assertEqual(self.call(), ('uncertain', 'stage=helper-failed'))
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

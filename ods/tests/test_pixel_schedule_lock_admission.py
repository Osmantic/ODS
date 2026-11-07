#!/usr/bin/env python3
"""Bounded schedule admission with real competing locks and no mutation replay."""
import contextlib
import os
import pathlib
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


if sys.platform == 'win32':
    raise unittest.SkipTest('Coordinator flock qualification requires POSIX')
SOURCE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'bin'))
import pixel_access_bridge as bridge_module

AccessError = bridge_module.AccessError
SystemdAccessBridge = bridge_module.SystemdAccessBridge
LaunchdAccessBridge = bridge_module.LaunchdAccessBridge


HEX = 'a' * 64
VALID_LIST = {'action': 'list', 'offset': 0}
VALID_CREATE = {
    'action': 'create', 'requestId': '11111111-1111-4111-8111-111111111111',
    'name': 'fixture', 'task': 'fixture task',
    'schedule': {'kind': 'at', 'at': '2030-01-01T09:00:00Z'},
}


@contextlib.contextmanager
def root_owned_state():
    """Model root ownership for unprivileged CI; real flock and modes."""
    with tempfile.TemporaryDirectory() as root:
        state = pathlib.Path(root)
        real_lstat, real_fstat = pathlib.Path.lstat, os.fstat

        def root_stat(value):
            fields = list(value)
            fields[4] = 0
            return os.stat_result(fields)

        def lstat(path, *args, **kwargs):
            value = real_lstat(path, *args, **kwargs)
            return root_stat(value) if path == state else value

        with patch('os.geteuid', return_value=0), patch.object(
                pathlib.Path, 'lstat', lstat), patch(
                'os.fstat', side_effect=lambda fd: root_stat(real_fstat(fd))):
            yield state


@contextlib.contextmanager
def holder(state):
    program = (
        'import fcntl,os,sys\n'
        'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)\n'
        'fcntl.flock(fd,fcntl.LOCK_EX)\n'
        'print("held",flush=True)\n'
        'sys.stdin.readline()\n'
    )
    with subprocess.Popen([sys.executable, '-c', program, str(state / 'lock')],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True) as child:
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            assert child.stdout.readline() == 'held\n'
            yield child
        finally:
            if child.poll() is None:
                child.stdin.write('\n')
                child.stdin.flush()
            child.wait(timeout=5)


def make_bridge(state, *, edge_key=HEX):
    bridge = object.__new__(SystemdAccessBridge)
    bridge.state = state
    bridge.edge_key = edge_key
    bridge.install = state
    bridge.native_port = 18789
    return bridge


def data_source(bridge):
    return {'kind': 'data-directory', 'identity': bridge.settings_source()}


class ScheduleLockWaitTests(unittest.TestCase):
    def setUp(self):
        self._state_cm = root_owned_state()
        self.state = self._state_cm.__enter__()
        self.bridge = make_bridge(self.state)
        self.bridge.settings_source = lambda: 'data-dir-id'
        self.bridge.discover = lambda: None
        self.bridge.pending = lambda: None
        self.worker_calls = []

        def worker(name, **kwargs):
            self.worker_calls.append((name, kwargs))
            if name == 'settings-status':
                return {'configSha256': 'c' * 64, 'pending': False}
            return {'outcome': 'applied'}

        self.bridge.worker = worker

    def tearDown(self):
        self._state_cm.__exit__(None, None, None)

    def test_schedules_waits_for_real_competing_flock_then_dispatches_once(self):
        with holder(self.state) as child:
            real_sleep = time.sleep
            released = []

            def release(_delay):
                if released:
                    return real_sleep(_delay)
                released.append(True)
                child.stdin.write('\n')
                child.stdin.flush()
                with patch.object(bridge_module.time, 'sleep', real_sleep):
                    child.wait(timeout=5)

            with patch.object(bridge_module.time, 'sleep', side_effect=release) as sleep:
                result = self.bridge.schedules(VALID_LIST, source=data_source(self.bridge))
            self.assertEqual(result, {'outcome': 'applied'})
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual([name for name, _ in self.worker_calls],
                             ['settings-status', 'schedules'])
            self.assertEqual(self.worker_calls[1][1]['schedule_request'], VALID_LIST)

    def test_create_waits_for_real_competing_flock_and_dispatches_once(self):
        with holder(self.state) as child:
            real_sleep = time.sleep
            released = []

            def release(_delay):
                if released:
                    return real_sleep(_delay)
                released.append(True)
                child.stdin.write('\n')
                child.stdin.flush()
                with patch.object(bridge_module.time, 'sleep', real_sleep):
                    child.wait(timeout=5)

            with patch.object(bridge_module.time, 'sleep', side_effect=release) as sleep:
                result = self.bridge.schedules(VALID_CREATE, source=data_source(self.bridge))
            self.assertEqual(result, {'outcome': 'applied'})
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual([name for name, _ in self.worker_calls],
                             ['settings-status', 'schedules'])
            self.assertEqual(self.worker_calls[1][1]['schedule_request'], VALID_CREATE)

    def test_schedules_expired_wait_never_calls_worker(self):
        with holder(self.state):
            clock = [0.0]

            def advance(seconds):
                clock[0] += seconds

            with patch.object(bridge_module.time, 'monotonic',
                              side_effect=lambda: clock[0]), patch.object(
                    bridge_module.time, 'sleep', side_effect=advance):
                with self.assertRaisesRegex(AccessError, '^transition-busy$'):
                    self.bridge.schedules(VALID_LIST, source=data_source(self.bridge))
            self.assertEqual(self.worker_calls, [])
            self.assertAlmostEqual(clock[0], 5)

    def test_outer_deadline_clamps_schedule_lock_wait(self):
        with holder(self.state):
            clock = [0.0]
            with patch.object(bridge_module.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                    bridge_module.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
                with self.assertRaisesRegex(AccessError, '^transition-busy$'):
                    with self.bridge.bounded(0.4):
                        self.bridge.schedules(VALID_LIST, source=data_source(self.bridge))
            self.assertAlmostEqual(clock[0], 0.4)
            self.assertEqual(self.worker_calls, [])

    def test_schedules_identity_and_pending_validation_unchanged(self):
        with self.assertRaisesRegex(AccessError, '^schedule-installation-mismatch$'):
            self.bridge.schedules(VALID_LIST, source={'kind': 'data-directory', 'identity': 'wrong'})
        self.assertEqual(self.worker_calls, [])

        self.bridge.pending = lambda: {'phase': 'x'}
        with self.assertRaisesRegex(AccessError, '^runtime-change-pending$'):
            self.bridge.schedules(VALID_CREATE, source=data_source(self.bridge))
        self.assertEqual(self.worker_calls, [])

        self.bridge.pending = lambda: None
        self.bridge.worker = lambda name, **kw: (
            {'configSha256': 'c' * 64, 'pending': True}
            if name == 'settings-status' else self.fail('schedules dispatched'))
        with self.assertRaisesRegex(AccessError, '^runtime-change-pending$'):
            self.bridge.schedules(VALID_CREATE, source=data_source(self.bridge))

    def test_schedules_worker_error_is_not_replayed(self):
        calls = []

        def worker(name, **kwargs):
            calls.append(name)
            if name == 'settings-status':
                return {'configSha256': 'c' * 64, 'pending': False}
            raise AccessError('owner-protocol-failed')

        self.bridge.worker = worker
        with self.assertRaisesRegex(AccessError, '^owner-protocol-failed$'):
            self.bridge.schedules(VALID_CREATE, source=data_source(self.bridge))
        self.assertEqual(calls, ['settings-status', 'schedules'])

    def test_normal_lock_remains_immediate_under_contention(self):
        with holder(self.state), patch.object(bridge_module.time, 'sleep') as sleep:
            with self.assertRaisesRegex(AccessError, '^transition-busy$'):
                with self.bridge.locked():
                    self.fail('normal lock admitted under contention')
            sleep.assert_not_called()

    def test_launchd_locked_forwards_wait_and_checks_marker(self):
        launchd = object.__new__(LaunchdAccessBridge)
        launchd.state = self.state
        seen = []

        @contextlib.contextmanager
        def fake_super(_bridge, *, wait_seconds=0):
            seen.append(wait_seconds)
            yield

        with patch.object(SystemdAccessBridge, 'locked', fake_super):
            with launchd.locked(wait_seconds=5):
                pass
            with launchd.locked():
                pass
        self.assertEqual(seen, [5, 0])

        (self.state / 'runtime-upgrade.json').write_text('{}')
        with patch.object(SystemdAccessBridge, 'locked', fake_super):
            with self.assertRaisesRegex(AccessError, '^runtime-upgrade-recovery-required$'):
                with launchd.locked(wait_seconds=5):
                    self.fail('marker ignored')


if __name__ == '__main__':
    unittest.main()

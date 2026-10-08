"""Behavioral access readiness checks; also selected by the macOS CI glob."""
import importlib.util
import json
from pathlib import Path
import socket
import stat
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
SPEC = importlib.util.spec_from_file_location(
    'access_readiness_tests', ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
READY = {'status': 200, 'body': {'available': True}}
BUSY = {'status': 200, 'body': {'available': False, 'reason': 'runtime-unavailable-or-busy'}}


def frame(value):
    return json.dumps(value).encode() + b'\n'


@pytest.fixture
def readiness(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    def sleep(seconds):
        clock.now += seconds
    monkeypatch.setattr(installer.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(installer.time, 'sleep', sleep)
    info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_dev=1, st_ino=2)
    address = Mock()
    address.exists.return_value = True
    address.lstat.return_value = info
    service = SimpleNamespace(pid=Mock(return_value=123))
    monkeypatch.setattr(installer._launchd, 'ACCESS_SOCKET', address)
    state = SimpleNamespace(clock=clock, info=info, service=service,
                            replies=[], attempts=0, on_read=lambda: None,
                            chunk_size=4096, read_delay=0)
    class Connection:
        def __enter__(self):
            state.attempts += 1
            self.reply = state.replies.pop(0)
            return self
        def __exit__(self, *args): pass
        def settimeout(self, value): self.timeout = value
        def connect(self, path): pass
        def sendall(self, payload):
            assert payload == b'{"operation":"status"}\n'
        def recv(self, size):
            state.on_read()
            if state.read_delay >= self.timeout:
                clock.now += self.timeout
                raise TimeoutError()
            clock.now += state.read_delay
            if isinstance(self.reply, BaseException):
                clock.now += self.timeout
                raise self.reply
            size = min(size, state.chunk_size)
            part, self.reply = self.reply[:size], self.reply[size:]
            return part
        def makefile(self, mode):
            outer = self
            class Stream:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def readline(self, size): return outer.recv(size)
            return Stream()
    monkeypatch.setattr(installer.socket, 'socket', lambda *args: Connection())
    return state


def test_transient_busy_then_ready(readiness):
    readiness.replies = [frame(BUSY), frame(READY)]
    assert installer._ready_access(readiness.service) == READY['body']
    assert readiness.attempts == 2


@pytest.mark.parametrize('error', [TimeoutError(), ConnectionRefusedError()])
def test_transport_retry_then_ready(readiness, error):
    readiness.replies = [error, frame(READY)]
    assert installer._ready_access(readiness.service) == READY['body']
    assert readiness.attempts == 2


def test_persistent_busy_has_total_deadline(readiness):
    readiness.replies = [frame(BUSY)] * 1000
    with pytest.raises(installer.InstallError, match='native-access-readiness-failed'):
        installer._ready_access(readiness.service, timeout=3)
    assert readiness.clock.now == 3
    assert 1 <= readiness.attempts <= 4


def test_stalled_peer_has_total_deadline(readiness):
    readiness.replies = [TimeoutError()] * 20
    with pytest.raises(installer.InstallError, match='native-access-readiness-failed'):
        installer._ready_access(readiness.service, timeout=70)
    assert readiness.clock.now == 70


def test_trickled_response_cannot_extend_total_deadline(readiness):
    readiness.replies = [frame(READY)]
    readiness.chunk_size = 1
    readiness.read_delay = 0.4
    with pytest.raises(installer.InstallError, match='native-access-readiness-failed'):
        installer._ready_access(readiness.service, timeout=3)
    assert readiness.clock.now == 3


@pytest.mark.parametrize('reply', [
    b'not-json\n', b'null\n', b'{}', b'x' * 65537, frame({'status': 200}),
    frame({'status': 403, 'body': {'error': 'owner-required'}}),
    frame({'status': 200, 'body': {'available': False, 'reason': 'unsafe-host-state'}}),
    frame({'status': 200, 'body': {'available': False, 'reason': 'runtime-upgrade-recovery-required'}}),
])
def test_invalid_or_nontransient_reply_fails_without_retry(readiness, reply):
    readiness.replies = [reply, frame(READY)]
    with pytest.raises(installer.InstallError, match='native-access-readiness-failed'):
        installer._ready_access(readiness.service)
    assert readiness.attempts == 1


@pytest.mark.parametrize('change', ['pid', 'mode', 'owner', 'inode'])
def test_identity_change_during_busy_probe_is_fatal(readiness, change):
    readiness.replies = [frame(BUSY), frame(READY)]
    def mutate():
        if change == 'pid': readiness.service.pid.return_value = 124
        if change == 'mode': readiness.info.st_mode = stat.S_IFSOCK | 0o666
        if change == 'owner': readiness.info.st_uid = 501
        if change == 'inode': readiness.info.st_ino = 3
    readiness.on_read = mutate
    with pytest.raises(installer.InstallError):
        installer._ready_access(readiness.service)
    assert readiness.attempts == 1


def test_upgrade_guard_remains_required(readiness):
    guarded = {'available': False, 'surface': 'darwin', 'pending': True,
               'runtime_verified': False, 'effective_mode': 'unknown',
               'reason': 'runtime-upgrade-recovery-required'}
    readiness.replies = [frame({'status': 200, 'body': guarded})]
    installer._ready_access(readiness.service, upgrade_guard=True)
    readiness.replies = [frame(READY)]
    with pytest.raises(installer.InstallError):
        installer._ready_access(readiness.service, upgrade_guard=True)


def test_real_socket_reply_after_old_twenty_second_limit(tmp_path, monkeypatch):
    """Real AF_UNIX I/O; substitute only the fixture's owner metadata."""
    address = tmp_path / 'access.sock'
    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == address:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0,
                                   st_dev=info.st_dev, st_ino=info.st_ino)
        return info
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(installer._launchd, 'ACCESS_SOCKET', address)
    errors = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(address))
        address.chmod(0o660)
        server.listen(1)
        server.settimeout(30)
        def respond():
            try:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(5)
                    assert connection.recv(1024) == b'{"operation":"status"}\n'
                    time.sleep(21)
                    connection.sendall(frame(READY))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=respond, daemon=True)
        worker.start()
        try:
            installer._ready_access(SimpleNamespace(pid=lambda **kwargs: 123))
        finally:
            worker.join(25)
    assert not worker.is_alive() and not errors

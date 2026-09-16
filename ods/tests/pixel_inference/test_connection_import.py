"""Actual disposable subprocess/socket boundary; not fleet inference acceptance."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))

from pixel_provider import connection_import as mod
from test_connection import BASE_CONN, PROBE_ROOT
from test_connection_transport import server


def request(connection):
    return {'bundle': json.dumps(connection), 'confirmedEndpoint': connection['baseUrl']}


def test_real_child_single_get_and_no_persistence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    with server() as (connection, calls):
        result = mod.inspect_connection(request(connection))
        assert result['endpoint'] == connection['baseUrl']
        assert result['deviceId'] == connection['deviceId']
        assert result['metadata']['routedModel'] == 'GLM'
        assert result['metadata']['capabilities']['agentViable'] is False
        assert connection['credential']['apiKey'] not in json.dumps(result)
    assert len(calls) == 1 and calls[0][0] == '/v1/models'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('status', [301, 401, 403, 500])
def test_actual_child_failure_is_single_request_without_key_leak(status):
    with server(status=status) as (connection, calls):
        with pytest.raises(mod.StoreError) as error:
            mod.inspect_connection(request(connection))
        assert connection['credential']['apiKey'] not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize('change', [
    lambda body: body.update(confirmedEndpoint='http://127.0.0.1:1/v1'),
    lambda body: body.update(extra=True),
    lambda body: body.update(bundle=body['bundle'].replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1')),
    lambda body: body.update(bundle='[' * 40 + '0' + ']' * 40),
    lambda body: body.update(bundle='x' * (mod.MAX_BUNDLE + 1)),
])
def test_invalid_input_never_launches_child(change, monkeypatch):
    with server() as (connection, calls):
        body = request(connection)
        change(body)
        def forbidden(*args, **kwargs):
            pytest.fail('invalid input launched child')
        monkeypatch.setattr(mod.subprocess, 'run', forbidden)
        with pytest.raises(mod.StoreError):
            mod.inspect_connection(body)
    assert calls == []


def test_child_receives_key_only_in_stdin_and_timeout_releases_lock(monkeypatch):
    connection = deepcopy(BASE_CONN)
    connection['expiresAt'] = 2**52
    key = connection['credential']['apiKey']
    calls = []
    def timeout(args, **kwargs):
        calls.append(args)
        assert key not in ' '.join(args) and key not in json.dumps(kwargs['env'])
        assert key.encode() in kwargs['input']
        assert args[1:3] == ['-I', '-B']
        assert kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['timeout'] == 20
        raise subprocess.TimeoutExpired(args, 20)
    monkeypatch.setattr(mod.subprocess, 'run', timeout)
    for _ in range(2):
        with pytest.raises(mod.StoreError, match='connection-unavailable'):
            mod.inspect_connection(request(connection))
    assert len(calls) == 2


def test_busy_and_bad_child_never_leak_or_persist(monkeypatch):
    connection = deepcopy(BASE_CONN)
    connection['expiresAt'] = 2**52
    mod._lock.acquire()
    try:
        with pytest.raises(mod.StoreError, match='connection-probe-busy'):
            mod.inspect_connection(request(connection))
    finally:
        mod._lock.release()
    metadata = deepcopy(PROBE_ROOT['ods'])
    metadata['expiresAt'] = connection['expiresAt']
    metadata['credential'] = connection['credential']
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode()))
    with pytest.raises(mod.StoreError, match='invalid-probe'):
        mod.inspect_connection(request(connection))


def test_actual_stalled_response_child_is_killed_and_reaped(monkeypatch):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    reached, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '1024')
            self.end_headers()
            reached.set()
            release.wait(4)
    peer = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    connection = deepcopy(BASE_CONN)
    connection.update(baseUrl=f'http://127.0.0.1:{peer.server_port}/v1', expiresAt=int(time.time()) + 3600)
    processes = []
    original = mod.subprocess.Popen
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(mod.subprocess, 'Popen', spawn)
    monkeypatch.setattr(mod, 'DEADLINE_SECONDS', 1)
    try:
        with pytest.raises(mod.StoreError, match='connection-unavailable'):
            mod.inspect_connection(request(connection))
        assert reached.is_set(), 'must actually reach the stalled HTTP body'
        assert len(processes) == 1 and processes[0].poll() is not None
        assert mod._lock.acquire(blocking=False)
        mod._lock.release()
    finally:
        release.set(); peer.shutdown(); peer.server_close(); thread.join(timeout=2)

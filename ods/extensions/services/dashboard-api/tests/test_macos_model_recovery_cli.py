"""The support client never edits state or retries an uncertain mutation."""
import importlib.util
import io
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import urllib.error

import pytest


script = Path(__file__).resolve().parents[4] / 'scripts/recover-macos-local-model.py'
spec = importlib.util.spec_from_file_location('recovery_client_test', script)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)

TX = 'a' * 64
PENDING = {'pending': True, 'phase': 'held', 'transactionId': TX}
COMPLETED = {'pending': False, 'phase': 'completed', 'transactionId': TX, 'outcome': 'commit'}


class Reply(io.BytesIO):
    def __init__(self, value, status=200):
        super().__init__(json.dumps(value).encode())
        self.status = status


@pytest.fixture
def installed(monkeypatch):
    env = {'GPU_BACKEND': 'apple', 'ODS_MODE': 'local', 'ODS_AGENT_KEY': 'private-test-key'}
    monkeypatch.setattr(client.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(client.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(client, 'installed_env', lambda _: env)
    return env


def transport(monkeypatch, responses):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value if isinstance(value, Reply) else Reply(value)
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *_: Opener())
    return calls


def test_inspection_never_posts(installed, monkeypatch, tmp_path):
    calls = transport(monkeypatch, iter([PENDING]))
    assert client.run(tmp_path)['phase'] == 'held'
    assert len(calls) == 1
    assert calls[0][0].get_method() == 'GET'
    assert calls[0][0].full_url == 'http://127.0.0.1:7710/v1/model/recovery'
    assert calls[0][0].get_header('Authorization') == 'Bearer private-test-key'
    assert list(tmp_path.iterdir()) == []


def test_apply_uses_only_observed_transaction(installed, monkeypatch, tmp_path):
    calls = transport(monkeypatch, iter([PENDING, COMPLETED]))
    assert client.run(tmp_path, apply=True) == COMPLETED
    assert len(calls) == 2
    assert calls[1][0].full_url == 'http://127.0.0.1:7710/v1/model/recover/current-local'
    assert json.loads(calls[1][0].data) == {'transactionId': TX}
    assert calls[1][1] == 400
    assert list(tmp_path.iterdir()) == []


def test_already_idle_does_not_send_repair(installed, monkeypatch, tmp_path):
    calls = transport(monkeypatch, iter([{'pending': False, 'phase': 'idle'}]))
    assert client.run(tmp_path, apply=True)['pending'] is False
    assert len(calls) == 1


@pytest.mark.parametrize('state', [
    {'pending': True, 'phase': 'held'},
    {**PENDING, 'phase': 'prepared'},
    {**PENDING, 'transactionId': '../journal'},
    {'pending': False, 'phase': 'held'},
    {'pending': 'false', 'phase': 'idle'},
])
def test_invalid_state_is_never_applied(installed, monkeypatch, tmp_path, state):
    calls = transport(monkeypatch, iter([state]))
    with pytest.raises((ValueError, client.RecoveryError)):
        client.run(tmp_path, apply=True)
    assert len(calls) == 1


@pytest.mark.parametrize('result', [PENDING, {**COMPLETED, 'transactionId': 'b'*64},
                                   {**COMPLETED, 'outcome': 'rollback'}])
def test_unconfirmed_completion_never_reports_success(installed, monkeypatch, tmp_path, result):
    calls = transport(monkeypatch, iter([PENDING, result]))
    with pytest.raises(client.RecoveryError, match='unconfirmed'):
        client.run(tmp_path, apply=True)
    assert len(calls) == 2


def test_timeout_after_dispatch_is_not_retried(installed, monkeypatch, tmp_path):
    calls = transport(monkeypatch, iter([PENDING, TimeoutError('timeout')]))
    with pytest.raises(TimeoutError):
        client.run(tmp_path, apply=True)
    assert len(calls) == 2


@pytest.mark.parametrize('status', [202, 204, 206])
def test_nonfinal_http_status_is_not_completion(installed, monkeypatch, tmp_path, status):
    calls = transport(monkeypatch, iter([PENDING, Reply(COMPLETED, status)]))
    with pytest.raises(client.RecoveryError, match=f'HTTP {status}'):
        client.run(tmp_path, apply=True)
    assert len(calls) == 2


@pytest.mark.parametrize('state', [
    {**PENDING, 'phase': 'unrecognized-private-detail'},
    {**COMPLETED, 'outcome': 'unrecognized-private-detail'},
    {**COMPLETED, 'transactionId': None},
])
def test_inspection_never_outputs_invalid_receipt_fields(installed, monkeypatch, tmp_path, state):
    calls = transport(monkeypatch, iter([state]))
    with pytest.raises((ValueError, client.RecoveryError)):
        client.run(tmp_path)
    assert len(calls) == 1


def test_refusal_keeps_private_response_out_of_error(installed, monkeypatch, tmp_path):
    failure = urllib.error.HTTPError('http://127.0.0.1', 409, 'conflict', {},
                                     io.BytesIO(b'private response must not be printed'))
    calls = transport(monkeypatch, iter([PENDING, failure]))
    with pytest.raises(client.RecoveryError, match='HTTP 409') as error:
        client.run(tmp_path, apply=True)
    assert 'private response' not in str(error.value)
    assert len(calls) == 2


@pytest.mark.parametrize('fault', ['root', 'linux', 'cloud', 'port', 'key'])
def test_unsupported_environment_makes_no_network_call(installed, monkeypatch, tmp_path, fault):
    if fault == 'root':
        monkeypatch.setattr(client.os, 'geteuid', lambda: 0)
    elif fault == 'linux':
        monkeypatch.setattr(client.platform, 'system', lambda: 'Linux')
    elif fault == 'cloud':
        installed['ODS_MODE'] = 'cloud'
    elif fault == 'port':
        installed['ODS_AGENT_PORT'] = '70000'
    else:
        installed.pop('ODS_AGENT_KEY')
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *_: pytest.fail('unexpected network'))
    with pytest.raises(client.RecoveryError):
        client.run(tmp_path, apply=True)


def test_redirect_does_not_forward_credential():
    paths = []
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/leak')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Redirect)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = client.urllib.request.build_opener(client.urllib.request.ProxyHandler({}), client.NoRedirect())
        with pytest.raises(client.RecoveryError, match='HTTP 302'):
            client.request(opener, server.server_port, 'private-test-key', '/v1/model/recovery')
        assert paths == ['/v1/model/recovery']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

"""The support client never edits state or retries an uncertain mutation."""
import importlib.util
import io
import json
from email.message import Message
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
    failure = urllib.error.HTTPError('http://127.0.0.1', 409, 'conflict', Message(),
                                     io.BytesIO(b'private response must not be printed'))
    calls = transport(monkeypatch, iter([PENDING, failure]))
    with pytest.raises(client.RecoveryError, match='HTTP 409') as error:
        client.run(tmp_path, apply=True)
    assert 'private response' not in str(error.value)
    assert len(calls) == 2


class ErrorBody(io.BytesIO):
    def __init__(self, raw, read_error=None):
        super().__init__(raw)
        self.read_sizes = []
        self.read_error = read_error

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.read_error:
            raise self.read_error
        return super().read(size)


@pytest.mark.parametrize('payload, guidance', [
    ({'error': 'Model lifecycle is busy'}, 'this repair request was not started'),
    ({'error': 'Model lifecycle is busy', 'code': 'model_lifecycle_busy'},
     'this repair request was not started'),
    ({'pending': True, 'phase': 'unavailable', 'reason': 'current-local-model-repair-unconfirmed'},
     'native state may already have changed'),
])
def test_known_refusal_is_sanitized_and_never_replayed(installed, monkeypatch, tmp_path, payload, guidance):
    stream = ErrorBody(json.dumps(payload).encode())
    failure = urllib.error.HTTPError('http://127.0.0.1', 409, 'conflict', Message(), stream)
    calls = transport(monkeypatch, iter([PENDING, failure]))

    with pytest.raises(client.RecoveryError, match=guidance) as stopped:
        client.run(tmp_path, apply=True)

    assert 'private-test-key' not in str(stopped.value)
    assert [call[0].get_method() for call in calls] == ['GET', 'POST']
    assert stream.read_sizes == [65537] and stream.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('raw', [
    b'{"code":"model_lifecycle_busy"}',
    b'{"error":"Model lifecycle is busy","private":"owner-secret"}',
    b'{"error":"Model lifecycle is busy","code":"unknown"}',
    b'{"error":"owner-secret","code":"model_lifecycle_busy"}',
    b'{"error":"owner-secret","error":"Model lifecycle is busy"}',
    b'{"pending":1,"phase":"unavailable","reason":"current-local-model-repair-unconfirmed"}',
    b'{"pending":true,"phase":"applied","reason":"current-local-model-repair-unconfirmed"}',
    b'{"pending":true,"phase":"unavailable","reason":"current-local-model-repair-unconfirmed","private":"owner-secret"}',
    b'null', b'[]', b'owner-secret', b'\xff',
    b'{"error":"Model lifecycle is busy"}' + b' ' * 65536,
])
def test_unknown_or_invalid_error_body_stays_generic(installed, monkeypatch, tmp_path, raw):
    stream = ErrorBody(raw)
    failure = urllib.error.HTTPError('http://127.0.0.1', 409, 'conflict', Message(), stream)
    calls = transport(monkeypatch, iter([PENDING, failure]))

    with pytest.raises(client.RecoveryError) as stopped:
        client.run(tmp_path, apply=True)

    assert str(stopped.value) == 'Host returned HTTP 409; inspect the saved state before retrying'
    assert len(calls) == 2 and stream.read_sizes == [65537] and stream.closed


def test_failed_error_body_read_is_not_retried(installed, monkeypatch, tmp_path):
    stream = ErrorBody(b'', OSError('owner-secret'))
    failure = urllib.error.HTTPError('http://127.0.0.1', 409, 'conflict', Message(), stream)
    calls = transport(monkeypatch, iter([PENDING, failure]))
    with pytest.raises(client.RecoveryError) as stopped:
        client.run(tmp_path, apply=True)
    assert str(stopped.value) == 'Host returned HTTP 409; inspect the saved state before retrying'
    assert len(calls) == 2 and stream.read_sizes == [65537] and stream.closed


@pytest.mark.parametrize('route, body, status', [
    ('/v1/model/recovery', None, 409),
    ('/v1/model/recover/current-local', {'transactionId': TX}, 503),
    ('/v1/model/recover', {}, 409),
])
def test_other_operation_or_status_cannot_claim_repair_not_started(monkeypatch, route, body, status):
    stream = ErrorBody(b'{"error":"Model lifecycle is busy"}')
    failure = urllib.error.HTTPError('http://127.0.0.1', status, 'conflict', Message(), stream)
    calls = transport(monkeypatch, iter([failure]))
    opener = client.urllib.request.build_opener()
    with pytest.raises(client.RecoveryError) as stopped:
        client.request(opener, 7710, 'private-test-key', route, body)
    assert str(stopped.value) == f'Host returned HTTP {status}; inspect the saved state before retrying'
    assert len(calls) == 1 and stream.read_sizes == [] and stream.closed


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
        server: HTTPServer

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

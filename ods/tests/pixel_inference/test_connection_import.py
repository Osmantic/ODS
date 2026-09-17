"""Contract tests for pixel_provider/connection_import.py + connection_transport.py.

`inspect_connection` is the host-agent entrypoint
(`ods-host-agent.py` `_handle_pixel_provider_connection`) that inspects an
owner-confirmed shared-inference bundle through a disposable single-hop child.
The credential travels only on child stdin; the public result must never echo
it. `probe_connection` is the transport the child uses for `/v1/models`.
"""
import copy
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.connection_import import (
    ERRORS, MAX_BUNDLE, inspect_connection, main, normalize_request, public_result,
)
import pixel_provider.connection_import as connection_import
from pixel_provider.connection_transport import _target, probe_connection
from pixel_provider.store import StoreError
from urllib.parse import urlsplit

NOW = int(time.time())
CONN = {
    'schemaVersion': 1, 'kind': 'ods-inference-connection', 'label': 'Laptop',
    'baseUrl': 'http://127.0.0.1:4005/v1', 'model': 'ods/shared',
    'deviceId': 'device-' + 'a' * 16, 'expiresAt': NOW + 3600,
    'expected': {'catalogId': 'glm', 'runtimeModelId': 'GLM'},
    'credential': {'apiKey': 'ods_infer_' + 'b' * 64},
    'execution': 'client-owned',
}
METADATA = {
    'catalogId': 'glm', 'routedModel': 'GLM', 'identitySource': 'ods-verified-route',
    'routeSeq': 4, 'contextLength': 32768, 'maxOutputTokens': 4096,
    'capabilities': {'chat': True, 'tools': True, 'vision': False, 'agentViable': False},
    'expiresAt': NOW + 3600, 'execution': 'client-owned',
}


def body(**changes):
    value = {'bundle': json.dumps(CONN), 'confirmedEndpoint': CONN['baseUrl']}
    value.update(changes)
    return value


class TestNormalizeRequest:
    def test_valid_request(self):
        connection, endpoint = normalize_request(body())
        assert endpoint == 'http://127.0.0.1:4005/v1'
        assert connection['deviceId'] == 'device-' + 'a' * 16

    @pytest.mark.parametrize('patch', [
        {'bundle': 5}, {'confirmedEndpoint': 7}, {'bundle': 'x' * (MAX_BUNDLE + 1)},
    ])
    def test_shape_rejected(self, patch):
        with pytest.raises(StoreError, match='invalid-request'):
            normalize_request(body(**patch))

    def test_bundle_utf8_bound(self):
        # The byte-length guard sits inside the connection-parsing try block, so
        # an overlong UTF-8 encoding surfaces as invalid-connection.
        with pytest.raises(StoreError, match='invalid-connection'):
            normalize_request(body(bundle='é' * (MAX_BUNDLE - 1)))

    @pytest.mark.parametrize('patch', [{'extra': 1}])
    def test_unknown_keys_rejected(self, patch):
        request = body()
        request.update(patch)
        with pytest.raises(StoreError, match='invalid-request'):
            normalize_request(request)

    def test_missing_keys_rejected(self):
        for key in ('bundle', 'confirmedEndpoint'):
            request = body()
            del request[key]
            with pytest.raises(StoreError, match='invalid-request'):
                normalize_request(request)

    @pytest.mark.parametrize('bundle', ['not json', '{"a":1}', '[1]'])
    def test_malformed_or_wrong_bundle(self, bundle):
        with pytest.raises(StoreError, match='invalid-connection'):
            normalize_request(body(bundle=bundle))

    def test_expired_bundle_rejected(self):
        conn = dict(CONN, expiresAt=NOW - 1)
        with pytest.raises(StoreError, match='invalid-connection'):
            normalize_request(body(bundle=json.dumps(conn)))

    def test_endpoint_must_match_saved_url(self):
        with pytest.raises(StoreError, match='connection-endpoint-not-confirmed'):
            normalize_request(body(confirmedEndpoint='http://127.0.0.1:9999/v1'))

    def test_localhost_endpoint_confirms_canonical_loopback(self):
        conn = dict(CONN, baseUrl='http://localhost:4005/v1')
        connection, endpoint = normalize_request(
            body(bundle=json.dumps(conn), confirmedEndpoint='http://localhost:4005/v1'))
        assert endpoint == 'http://127.0.0.1:4005/v1'


class TestPublicResult:
    def test_projection_shape_and_credential_absence(self):
        result = public_result(copy.deepcopy(METADATA), copy.deepcopy(CONN))
        assert result == {'schemaVersion': 1, 'endpoint': 'http://127.0.0.1:4005/v1',
                          'deviceId': 'device-' + 'a' * 16, 'expiresAt': NOW + 3600,
                          'expected': CONN['expected'], 'metadata': METADATA}
        assert 'ods_infer_' not in json.dumps(result) and 'apiKey' not in json.dumps(result)

    def test_metadata_is_revalidated(self):
        for patch in ({'catalogId': 'other'}, {'identitySource': 'upstream'},
                      {'maxOutputTokens': 0}, {'expiresAt': NOW + 9999}):
            metadata = dict(METADATA, **patch)
            with pytest.raises(StoreError, match='invalid-probe'):
                public_result(metadata, copy.deepcopy(CONN))


class _Child:
    def __init__(self, stdout=b'', returncode=0):
        self.stdout, self.returncode = stdout, returncode


class TestInspectConnection:
    def _run(self, monkeypatch, child=None, error=None):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if error is not None:
                raise error
            return child
        monkeypatch.setattr(subprocess, 'run', fake_run)
        return calls

    def test_success_and_child_isolation(self, monkeypatch):
        calls = self._run(monkeypatch, child=_Child(json.dumps(METADATA).encode()))
        monkeypatch.setenv('ODS_SENTINEL_SECRET', 'must-not-leak')
        result = inspect_connection(body())
        assert result['endpoint'] == 'http://127.0.0.1:4005/v1'
        args, kwargs = calls[0]
        assert args[:3] == [sys.executable, '-I', '-B']
        assert 'ods_infer_' not in json.dumps(result)
        env = kwargs['env']
        assert 'ODS_SENTINEL_SECRET' not in env
        assert set(env) <= {'SYSTEMROOT', 'WINDIR', 'SystemRoot', 'Windir'}

    @pytest.mark.parametrize('code', sorted(ERRORS))
    def test_child_error_allowlist_propagates(self, monkeypatch, code):
        # Any allowlisted code a child emits is forwarded verbatim, even ones the
        # child cannot legitimately produce while the parent holds the lock.
        self._run(monkeypatch, child=_Child(json.dumps({'error': code}).encode(), 1))
        with pytest.raises(StoreError, match=code):
            inspect_connection(body())

    @pytest.mark.parametrize('payload', [
        {'error': 'not-allowlisted'}, {'error': 'malformed-json'}, {'other': 'shape'},
    ])
    def test_unknown_or_foreign_child_errors_become_unavailable(self, monkeypatch, payload):
        self._run(monkeypatch, child=_Child(json.dumps(payload).encode(), 1))
        with pytest.raises(StoreError, match='connection-unavailable'):
            inspect_connection(body())

    def test_malformed_child_output_raises_malformed_json(self, monkeypatch):
        self._run(monkeypatch, child=_Child(b'not json', 1))
        with pytest.raises(StoreError, match='malformed-json'):
            inspect_connection(body())

    def test_oversized_child_output(self, monkeypatch):
        self._run(monkeypatch, child=_Child(b' ' * 8193, 0))
        with pytest.raises(StoreError, match='invalid-probe'):
            inspect_connection(body())

    def test_timeout_maps_to_unavailable(self, monkeypatch):
        self._run(monkeypatch, error=subprocess.TimeoutExpired('child', 20))
        with pytest.raises(StoreError, match='connection-unavailable'):
            inspect_connection(body())

    def test_spawn_failure_maps_to_unavailable(self, monkeypatch):
        self._run(monkeypatch, error=OSError('spawn failed'))
        with pytest.raises(StoreError, match='connection-unavailable'):
            inspect_connection(body())

    def test_single_flight(self, monkeypatch):
        assert connection_import._lock.acquire(blocking=False)
        try:
            with pytest.raises(StoreError, match='connection-probe-busy'):
                inspect_connection(body())
        finally:
            connection_import._lock.release()


@pytest.fixture
def serve():
    servers = []
    def factory(handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server
    yield factory
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.name != 'posix', reason='loopback probe')
class TestProbeTransport:
    def test_target_rejects_http_beyond_loopback(self):
        with pytest.raises(StoreError, match='unsafe-connection-address'):
            _target(urlsplit('http://192.0.0.1:9/v1'))

    def test_target_accepts_loopback(self):
        address, port = _target(urlsplit('http://127.0.0.1:9/v1'))
        assert address == '127.0.0.1' and port == 9

    def _conn(self, port):
        return dict(CONN, baseUrl=f'http://127.0.0.1:{port}/v1')

    def test_probe_happy_path(self, serve):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                assert self.path == '/v1/models'
                assert self.headers['Authorization'] == 'Bearer ' + CONN['credential']['apiKey']
                payload = {'object': 'list', 'data': [{'id': 'ods/shared'}], 'ods': METADATA}
                body_bytes = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
            def log_message(self, *args):
                pass
        server = serve(Handler)
        result = probe_connection(self._conn(server.server_address[1]),
                                  confirmed_endpoint=self._conn(server.server_address[1])['baseUrl'])
        assert result == METADATA

    def test_probe_denied(self, serve):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = serve(Handler)
        with pytest.raises(StoreError, match='connection-denied'):
            probe_connection(self._conn(server.server_address[1]),
                             confirmed_endpoint=self._conn(server.server_address[1])['baseUrl'])

    def test_probe_unavailable_status(self, serve):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.send_header('Content-Length', '0')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = serve(Handler)
        with pytest.raises(StoreError, match='connection-unavailable'):
            probe_connection(self._conn(server.server_address[1]),
                             confirmed_endpoint=self._conn(server.server_address[1])['baseUrl'])

    def test_probe_refused_connection(self):
        sock = __import__('socket').socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        with pytest.raises(StoreError, match='connection-unavailable'):
            probe_connection(self._conn(port), confirmed_endpoint=self._conn(port)['baseUrl'])

    def test_probe_requires_confirmed_endpoint(self):
        with pytest.raises(StoreError, match='connection-endpoint-not-confirmed'):
            probe_connection(self._conn(9), confirmed_endpoint='http://127.0.0.1:1/v1')


class TestMain:
    def _invoke(self, monkeypatch, stdin_bytes, probe=None, probe_error=None):
        class Stdin:
            buffer = type('B', (), {'read': lambda self, n=-1: stdin_bytes})()
        monkeypatch.setattr(sys, 'stdin', Stdin())
        if probe_error is not None:
            monkeypatch.setattr(connection_import, 'probe_connection',
                                lambda *a, **k: (_ for _ in ()).throw(probe_error))
        elif probe is not None:
            monkeypatch.setattr(connection_import, 'probe_connection', lambda *a, **k: probe)
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main()
        return rc, json.loads(out.getvalue())

    def test_main_success_prints_metadata_only(self, monkeypatch):
        rc, out = self._invoke(monkeypatch, json.dumps(body()).encode(), probe=dict(METADATA))
        assert rc == 0 and out == METADATA
        assert 'ods_infer_' not in json.dumps(out)

    def test_main_error_allowlist(self, monkeypatch):
        rc, out = self._invoke(monkeypatch, json.dumps(body()).encode(),
                               probe_error=StoreError('connection-denied'))
        assert rc == 1 and out == {'error': 'connection-denied'}

    def test_main_maps_foreign_error_codes(self, monkeypatch):
        rc, out = self._invoke(monkeypatch, json.dumps(body()).encode(),
                               probe_error=StoreError('malformed-json'))
        assert rc == 1 and out == {'error': 'connection-unavailable'}

    def test_main_maps_unexpected_exceptions(self, monkeypatch):
        rc, out = self._invoke(monkeypatch, json.dumps(body()).encode(),
                               probe_error=RuntimeError('secret apiKey ods_infer_x'))
        assert rc == 1 and out == {'error': 'connection-unavailable'}

    def test_main_rejects_oversized_request(self, monkeypatch):
        rc, out = self._invoke(monkeypatch, b' ' * 65537)
        assert rc == 1 and out == {'error': 'invalid-request'}

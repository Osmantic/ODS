"""Contract tests for bin/pixel_access_client.py.

`request_access` is the only client-side door into the privileged
ods-pixel-access control socket that drives model/status/finish and
settings/provider transactions. These tests pin its operation allowlist,
request-key rules, data_dir_id derivation, response bounds, and timeout
selection — using a real AF_UNIX server so the wire format is exercised.
"""
import hashlib
import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_access_client as client

pytestmark = pytest.mark.skipif(os.name != 'posix',
                                reason='AF_UNIX control socket is POSIX-only')

ALL_OPS = ('status', 'change', 'model-status', 'model-begin', 'model-finish',
           'settings-status', 'settings-change', 'provider-status',
           'provider-change')
REQUEST_OPS = {'change', 'model-finish', 'settings-change', 'provider-change'}
DIR_OPS = ('settings-status', 'settings-change', 'provider-status',
           'provider-change')


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Fake control socket; captures the payload and returns a canned reply."""
    sock_path = tmp_path / 'control.sock'
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    listener.listen(8)
    captured = {}
    reply = {'status': 200, 'body': {'ok': True}}

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                captured.setdefault('requests', []).append(
                    conn.makefile('rb').readline())
                conn.sendall(json.dumps(reply).encode() + b'\n')

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    # Redirect the hardcoded /run path at the socket layer.
    real_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, 'connect',
                        lambda self, addr: real_connect(
                            self, str(sock_path) if addr == '/run/ods-pixel-access/control.sock' else addr))
    yield captured, lambda value: reply.update(value)
    listener.close()


class TestOperationGate:
    @pytest.mark.parametrize('operation', ['destroy', 'model-apply', '', None,
                                           'STATUS'])
    def test_unknown_operations_rejected(self, server, operation):
        with pytest.raises(ValueError, match='invalid access operation'):
            client.request_access(operation)

    def test_every_documented_operation_reaches_socket(self, server):
        captured, _ = server
        for operation in ALL_OPS:
            kwargs = ({'settings_data_dir': '/abs/path'}
                      if operation in DIR_OPS else {})
            status, body = client.request_access(operation, **kwargs)
            assert (status, body) == (200, {'ok': True})
        assert len(captured['requests']) == len(ALL_OPS)


class TestPayloadShape:
    @pytest.mark.parametrize('operation',
                             sorted(set(ALL_OPS) - REQUEST_OPS))
    def test_request_key_omitted_for_read_ops(self, server, operation):
        captured, _ = server
        kwargs = ({'settings_data_dir': '/abs'} if operation in DIR_OPS else {})
        client.request_access(operation, request={'ignored': True}, **kwargs)
        sent = json.loads(captured['requests'][-1])
        assert 'request' not in sent and sent['operation'] == operation
        assert set(sent) <= {'operation', 'data_dir_id'}

    @pytest.mark.parametrize('operation', sorted(REQUEST_OPS))
    def test_request_key_included_for_mutations(self, server, operation):
        captured, _ = server
        kwargs = ({'settings_data_dir': '/abs'} if operation in DIR_OPS else {})
        marker = {'mutation': operation}
        client.request_access(operation, request=marker, **kwargs)
        sent = json.loads(captured['requests'][-1])
        assert sent['request'] == marker


class TestDataDirIdentity:
    @pytest.mark.parametrize('operation', DIR_OPS)
    def test_data_dir_required_and_absolute(self, server, operation):
        for bad in (None, 'relative/path', ''):
            with pytest.raises(ValueError,
                               match='unqualified settings data directory'):
                client.request_access(operation, settings_data_dir=bad)

    @pytest.mark.parametrize('operation', DIR_OPS)
    def test_data_dir_id_is_sha256_of_path(self, server, operation, tmp_path):
        captured, _ = server
        client.request_access(operation, settings_data_dir=str(tmp_path))
        sent = json.loads(captured['requests'][-1])
        expected = hashlib.sha256(str(tmp_path).encode('utf-8')).hexdigest()
        assert sent['data_dir_id'] == expected
        assert str(tmp_path) not in captured['requests'][-1].decode()

    def test_model_ops_never_send_data_dir_id(self, server):
        captured, _ = server
        client.request_access('model-status')
        assert 'data_dir_id' not in json.loads(captured['requests'][-1])


class TestResponseContract:
    @pytest.mark.parametrize('status', [200, 400, 403, 409, 503])
    def test_allowed_statuses(self, server, status):
        _, set_reply = server
        set_reply({'status': status, 'body': {'detail': 'x'}})
        got_status, body = client.request_access('status')
        assert got_status == status and body == {'detail': 'x'}

    @pytest.mark.parametrize('status', [0, 201, 500, '200'])
    def test_disallowed_statuses_rejected(self, server, status):
        _, set_reply = server
        set_reply({'status': status, 'body': {}})
        with pytest.raises(ValueError, match='invalid access response'):
            client.request_access('status')

    def test_extra_envelope_key_rejected(self, server):
        _, set_reply = server
        set_reply({'status': 200, 'body': {}, 'debug': 'leak'})
        with pytest.raises(ValueError, match='invalid access response'):
            client.request_access('status')

    def test_non_dict_body_rejected(self, server):
        _, set_reply = server
        set_reply({'status': 200, 'body': 'not-a-dict'})
        with pytest.raises(ValueError, match='invalid access response'):
            client.request_access('status')


class TestWireBounds:
    def test_oversized_response_rejected(self, tmp_path, monkeypatch):
        sock_path = tmp_path / 'control.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock_path))
        listener.listen(1)
        real_connect = socket.socket.connect
        monkeypatch.setattr(
            socket.socket, 'connect',
            lambda self, addr: real_connect(self, str(sock_path)))

        def serve():
            conn, _ = listener.accept()
            with conn:
                conn.makefile('rb').readline()
                conn.sendall(b'x' * 65537 + b'\n')
            listener.close()
        threading.Thread(target=serve, daemon=True).start()
        with pytest.raises(ValueError, match='invalid access response'):
            client.request_access('status')

    def test_response_without_newline_rejected(self, tmp_path, monkeypatch):
        sock_path = tmp_path / 'control.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock_path))
        listener.listen(1)
        real_connect = socket.socket.connect
        monkeypatch.setattr(
            socket.socket, 'connect',
            lambda self, addr: real_connect(self, str(sock_path)))

        def serve():
            conn, _ = listener.accept()
            with conn:
                conn.makefile('rb').readline()
                conn.sendall(b'{"status":200,"body":{}}')  # no newline
            listener.close()
        threading.Thread(target=serve, daemon=True).start()
        with pytest.raises(ValueError, match='invalid access response'):
            client.request_access('status')

    def test_timeouts_per_operation(self, server, monkeypatch):
        timeouts = []
        real = socket.socket.settimeout
        monkeypatch.setattr(socket.socket, 'settimeout',
                            lambda self, t: (timeouts.append(t), real(self, t)))
        client.request_access('model-begin')
        client.request_access('status')
        assert timeouts == [1850, 335]

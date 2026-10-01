"""The vendor verifier accepts only the exact authenticated runtime proof."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_client as client
import pixel_access_protocol as protocol
import pixel_model_transition as transition

spec = importlib.util.spec_from_file_location('ods_access_proof', ROOT / 'vendor/pixel/scripts/lib/ods-access-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@pytest.fixture
def configuration(tmp_path):
    path = tmp_path / 'openclaw.json'
    path.write_bytes(b'{"fixture":true}')
    return path, dict(mode='full-access', pid=123,
                      config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_exact_configuration_and_process_match(configuration, mode):
    path, value = configuration
    value['mode'] = mode
    assert proof.validate_proof(value, path, 123) == mode


@pytest.mark.parametrize('field,value', [('mode', 'anything'), ('pid', 124), ('pid', True),
                                       ('config_sha256', 'f' * 64), ('config_sha256', None),
                                       ('unexpected', True)])
def test_malformed_or_mismatched_proof_rejected(configuration, field, value):
    path, body = configuration
    body[field] = value
    with pytest.raises(ValueError):
        proof.validate_proof(body, path, 123)


def test_changed_config_and_symlink_rejected(configuration, tmp_path):
    path, value = configuration
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(OSError):
        proof.validate_proof(value, link, 123)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='configuration changed'):
        proof.validate_proof(value, path, 123)


def test_protocol_cannot_select_permission_mode_or_path():
    request = dict(operation='installer-model-verify', request=dict(transaction_id='a' * 64))
    assert protocol.control_request(request) == request
    for extra in [dict(mode='full-access'), dict(path='/tmp/forged'), dict(token='b' * 64)]:
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request(dict(request, request=dict(request['request'], **extra)))


def test_client_checks_root_peer_before_sending(monkeypatch):
    connection = MagicMock()
    connection.getsockopt.return_value = struct.pack('3i', 123, 1000, 1000)
    factory = MagicMock()
    factory.return_value.__enter__.return_value = connection
    monkeypatch.setattr(client.socket, 'socket', factory)
    monkeypatch.setattr(client.sys, 'platform', 'linux')
    monkeypatch.setattr(client.socket, 'SO_PEERCRED', getattr(socket, 'SO_PEERCRED', 17), raising=False)
    with pytest.raises(ValueError, match='root coordinator'):
        client.request_access('installer-model-verify', dict(transaction_id='a' * 64))
    connection.sendall.assert_not_called()


def test_transition_helper_returns_only_validated_proof(configuration):
    _, body = configuration
    calls = []
    def request(*args):
        calls.append(args)
        return 200, body
    assert transition.execute('verify', 'a' * 64, request=request) == body
    assert calls == [('installer-model-verify', dict(transaction_id='a' * 64))]
    with pytest.raises(RuntimeError, match='installer-model-verification-failed'):
        transition.execute('verify', 'a' * 64, request=lambda *_: (200, dict(body, token='secret')))


def test_client_includes_transaction_after_authenticated_connection(monkeypatch, configuration):
    _, body = configuration
    connection = MagicMock()
    connection.getsockopt.return_value = struct.pack('3i', 123, 0, 0)
    connection.makefile.return_value.__enter__.return_value.readline.return_value = (
        json.dumps(dict(status=200, body=body)) + '\n').encode()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = connection
    monkeypatch.setattr(client.socket, 'socket', factory)
    monkeypatch.setattr(client.sys, 'platform', 'linux')
    monkeypatch.setattr(client.socket, 'SO_PEERCRED', getattr(socket, 'SO_PEERCRED', 17), raising=False)
    assert client.request_access('installer-model-verify', dict(transaction_id='a' * 64)) == (200, body)
    assert json.loads(connection.sendall.call_args.args[0]) == dict(
        operation='installer-model-verify', request=dict(transaction_id='a' * 64))


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0,
                    reason='real Linux peer credentials require root and unprivileged processes')
@pytest.mark.parametrize('privileged', [True, False])
def test_real_socket_authenticates_service_before_transmitting(monkeypatch, privileged):
    import pwd
    uid = 0 if privileged else pwd.getpwnam('nobody').pw_uid
    assert privileged or uid > 0
    # A writable test directory intentionally lets an unprivileged process
    # impersonate the socket pathname. Peer authentication must still reject it.
    with tempfile.TemporaryDirectory(prefix='ods-peer-proof-') as directory:
        os.chmod(directory, 0o777)
        address = str(Path(directory) / 'control.sock')
        code = '''
import json, os, socket, sys
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[2]))
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.settimeout(10)
    server.bind(sys.argv[1])
    server.listen(1)
    with server.accept()[0] as connection:
        connection.settimeout(10)
        data = connection.recv(4096)
        print(len(data), flush=True)
        if data:
            connection.sendall((json.dumps({'status':200,'body':{'mode':'full-access','pid':123,'config_sha256':'e'*64}})+'\\n').encode())
'''
        process = subprocess.Popen(['/usr/bin/python3', '-I', '-c', code, address, str(uid)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not Path(address).exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError('fixture socket did not start')
                time.sleep(0.01)
            monkeypatch.setattr(client, 'ACCESS_SOCKET_PATH', address)
            if privileged:
                status, body = client.request_access('installer-model-verify', dict(transaction_id='a' * 64))
                assert status == 200 and body['mode'] == 'full-access'
            else:
                with pytest.raises(ValueError, match='root coordinator'):
                    client.request_access('installer-model-verify', dict(transaction_id='a' * 64))
            output, errors = process.communicate(timeout=10)
            assert process.returncode == 0, errors
            assert (int(output.strip()) > 0) == privileged
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.parametrize('transaction,mode,home,system,proof_exit,passes', [
    ('', 'full-access', 'no', 'no', 0, False),
    ('', 'sandboxed', 'tmpfs', 'strict', 0, True),
    ('a' * 64, 'full-access', 'no', 'no', 0, True),
    ('a' * 64, 'sandboxed', 'tmpfs', 'strict', 0, True),
    ('a' * 64, 'full-access', 'tmpfs', 'strict', 0, False),
    ('a' * 64, 'sandboxed', 'no', 'no', 0, False),
    ('a' * 64, 'unknown', 'no', 'no', 0, False),
    ('a' * 64, 'full-access', 'no', 'no', 1, False),
])
def test_real_shell_boundary_requires_matching_proof(transaction, mode, home, system, proof_exit, passes):
    source = (ROOT / 'vendor/pixel/scripts/verify.sh').read_text()
    start = source.index('  gateway_access_mode=sandboxed')
    end = source.index('  [[ $(gateway_property RestrictNamespaces)', start)
    script = '''
set -eu
ods_model_transaction=$1
proof_mode=$2
home_property=$3
system_property=$4
proof_exit=$5
ROOT=/fixture
OPENCLAW_HOME=/fixture
gateway_property() {
  case "$1" in
    MainPID) echo 123;;
    ProtectHome) echo "$home_property";;
    ProtectSystem) echo "$system_property";;
    *) return 1;;
  esac
}
python3() { echo "$proof_mode"; return "$proof_exit"; }
pixel_die() { echo "$*" >&2; exit 41; }
''' + source[start:end]
    result = subprocess.run(['bash', '-c', script, 'fixture', transaction, mode, home, system, str(proof_exit)],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == passes, result.stderr

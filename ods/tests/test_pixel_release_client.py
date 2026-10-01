"""Installer transport rejects malformed release plans before contacting root."""
import pathlib
import sys
import json
import os
import subprocess
import tempfile
import time
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'bin'))
import pixel_access_protocol as protocol
import pixel_model_transition as client
import pixel_access_client as transport

TOKEN = 'a' * 64
DIGEST = 'b' * 64


@pytest.mark.parametrize('action,arguments,response', [
    ('prepare', {'candidate': '/tmp/release/openclaw.json', 'digest': DIGEST},
     {'beforeSha': TOKEN, 'afterSha': DIGEST}),
    ('publish', {'outcome': 'apply'}, {'configSha256': DIGEST}),
    ('publish', {'outcome': 'rollback'}, {'configSha256': TOKEN}),
    ('finish', {'outcome': 'apply', 'digest': DIGEST}, {'configSha256': DIGEST}),
    ('abort', {}, {'configSha256': DIGEST}),
])
def test_client_frames_are_accepted_by_local_protocol(action, arguments, response):
    def request(operation, payload):
        frame = {'operation': operation, 'request': payload}
        protocol.control_request(frame)
        # Additional authority or an unreviewed field must not be accepted.
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request({**frame, 'request': {**payload, 'confirmed': True}})
        return 200, response
    assert client.execute_release(action, TOKEN, request=request, **arguments) == response


@pytest.mark.parametrize('candidate', ['/tmp/../secret', '/tmp//file', '/tmp/file\n', 'relative', '/'])
def test_noncanonical_candidate_never_contacts_service(candidate):
    request = Mock()
    with pytest.raises(ValueError):
        client.execute_release('prepare', TOKEN, candidate=candidate, digest=DIGEST, request=request)
    request.assert_not_called()


@pytest.mark.parametrize('reply', [(500, {'error': 'private diagnostic'}),
                                  (200, {'configSha256': DIGEST, 'extra': True}),
                                  (200, None), (200, {'configSha256': 'invalid'})])
def test_invalid_replies_do_not_leak_service_details(reply):
    with pytest.raises(RuntimeError, match='^installer-release-transition-failed$'):
        client.execute_release('publish', TOKEN, outcome='apply', request=lambda *_: reply)


@pytest.mark.parametrize('argv,expected', [
    (['release-prepare', '--transaction', TOKEN, '--candidate', '/tmp/candidate', '--sha256', DIGEST],
     ('prepare', {'candidate': '/tmp/candidate', 'digest': DIGEST})),
    (['release-publish', '--transaction', TOKEN, 'rollback'], ('publish', {'outcome': 'rollback'})),
    (['release-abort', '--transaction', TOKEN], ('abort', {})),
    (['release-finish', '--transaction', TOKEN, '--sha256', DIGEST, 'apply'],
     ('finish', {'digest': DIGEST, 'outcome': 'apply'})),
])
def test_cli_preserves_reviewed_arguments(argv, expected, capsys):
    with patch.object(client, 'execute_release', return_value={'configSha256': DIGEST}) as execute:
        assert client.main(argv) == 0
        execute.assert_called_once_with(expected[0], TOKEN, **expected[1])
    assert DIGEST in capsys.readouterr().out


def test_abort_cannot_supply_its_own_baseline_and_legacy_failure_is_actionable():
    request = Mock()
    with pytest.raises(ValueError):
        client.execute_release('abort', TOKEN, digest=DIGEST, request=request)
    request.assert_not_called()
    with pytest.raises(protocol.ProtocolError):
        protocol.control_request({'operation': 'installer-release-abort',
            'request': {'transaction_id': TOKEN, 'receipt_sha256': DIGEST}})
    with pytest.raises(RuntimeError, match='Legacy release attempt has no original receipt proof'):
        client.execute_release('abort', TOKEN, request=lambda *_: (
            409, {'error': 'release-legacy-baseline-unavailable'}))


@pytest.mark.parametrize('completion,accepted', [
    ({'outcome': 'apply', 'config_sha256': DIGEST}, True),
    ({'outcome': 'rollback', 'config_sha256': DIGEST}, True),
    (None, False), ({'outcome': 'apply', 'config_sha256': 'bad'}, False),
    ({'outcome': 'other', 'config_sha256': DIGEST}, False),
    ({'outcome': 'apply', 'config_sha256': DIGEST, 'extra': True}, False),
])
def test_status_validates_resumable_completion(completion, accepted):
    body = dict(pending=True, kind='model', transaction_id=TOKEN, phase='held',
                configured_mode='full-access', start_config_sha256=DIGEST,
                release_completion=completion)
    request = lambda *_: (200, body)
    if accepted:
        assert client.execute('status', request=request) == body
    else:
        with pytest.raises(RuntimeError, match='status-failed'):
            client.execute('status', request=request)


@pytest.mark.parametrize('operation', ['installer-unknown', 'installer-release-delete', 'installer-release-prepare-extra'])
def test_unknown_installer_operation_is_rejected_before_socket(monkeypatch, operation):
    connect = Mock(side_effect=AssertionError('unknown operation must not open a socket'))
    monkeypatch.setattr(transport.socket, 'socket', connect)
    with pytest.raises(ValueError, match='invalid access operation'):
        transport.request_access(operation, {'transaction_id': TOKEN})
    connect.assert_not_called()


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0,
                   reason='real Linux root and unprivileged socket peers required')
@pytest.mark.parametrize('privileged', [True, False])
@pytest.mark.parametrize('action,arguments,payload', [
    ('prepare', ['--candidate', '/tmp/candidate', '--sha256', DIGEST],
     {'candidate_path': '/tmp/candidate', 'candidate_sha256': DIGEST}),
    ('publish', ['rollback'], {'outcome': 'rollback'}),
    ('finish', ['--sha256', DIGEST, 'apply'], {'outcome': 'apply', 'config_sha256': DIGEST}),
    ('abort', [], {}),
])
def test_release_cli_real_wire_and_peer(monkeypatch, capsys, privileged, action, arguments, payload):
    import pwd
    uid = 0 if privileged else pwd.getpwnam('nobody').pw_uid
    assert privileged or uid > 0
    response = {'beforeSha': TOKEN, 'afterSha': DIGEST} if action == 'prepare' else {'configSha256': DIGEST}
    expected = {'operation': 'installer-release-' + action,
                'request': {'transaction_id': TOKEN, **payload}}
    # Real socket and SO_PEERCRED; no mocked request_access or socket methods.
    # Only the installed-helper custody loader is replaced for this source fixture.
    monkeypatch.setattr(client, '_load_request_access', lambda: transport.request_access)
    with tempfile.TemporaryDirectory(prefix='ods-release-wire-') as directory:
        os.chmod(directory, 0o777)
        address = str(pathlib.Path(directory) / 'control.sock')
        code = '''
import json, os, socket, sys
sys.path.insert(0, sys.argv[3])
from pixel_access_protocol import control_request
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[2]))
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.settimeout(10)
    server.bind(sys.argv[1])
    server.listen(1)
    with server.accept()[0] as connection:
        connection.settimeout(10)
        with connection.makefile('rb') as stream:
            raw = stream.readline(65537)
        if raw:
            frame = json.loads(raw)
            print(json.dumps(frame), flush=True)
            try:
                control_request(frame)
                reply = {'status': 200, 'body': json.loads(sys.argv[4])}
            except Exception:
                reply = {'status': 400, 'body': {'error': 'invalid-frame'}}
            connection.sendall((json.dumps(reply)+'\\n').encode())
        else:
            print('NO_BYTES', flush=True)
'''
        process = subprocess.Popen([sys.executable, '-I', '-c', code, address, str(uid),
                                    str(pathlib.Path(transport.__file__).parent), json.dumps(response)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not pathlib.Path(address).exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError('fixture socket did not start')
                time.sleep(0.01)
            monkeypatch.setattr(transport, 'ACCESS_SOCKET_PATH', address)
            argv = ['release-' + action, '--transaction', TOKEN, *arguments]
            if privileged:
                assert client.main(argv) == 0
                assert json.loads(capsys.readouterr().out) == response
            else:
                with pytest.raises(ValueError, match='root coordinator'):
                    client.main(argv)
            output, errors = process.communicate(timeout=10)
            assert process.returncode == 0, errors
            if privileged:
                assert json.loads(output) == expected
            else:
                assert output.strip() == 'NO_BYTES'
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

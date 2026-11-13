"""Native owner protocol over real disposable Unix sockets, without launchd or owner data."""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Unix sockets and process groups; run in Linux/WSL or macOS')

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import integrations_common as common  # noqa: E402
import integrations_service as integrations  # noqa: E402
import integrations_mcp as mcp  # noqa: E402


@pytest.fixture
def endpoint(tmp_path):
    calls = []
    service = SimpleNamespace(handle=lambda role, request: calls.append((role, request)) or {'received': True})
    key = bytes(range(32))  # Invented credential, never installed.
    path = tmp_path / 'owner.sock'
    server = integrations._listener(service, path, 'owner', owner_key=key)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield path, key, calls, server
    server.shutdown()
    server.server_close()
    thread.join(2)


def connect(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2)
    sock.connect(str(path))
    challenge = json.loads(common.read_owner_frame(sock, 256, time.monotonic() + 2))
    return sock, challenge['challenge']


def submit(sock, key, nonce, request, *, proof=None, client_nonce=None):
    client_nonce = client_nonce or os.urandom(32).hex()
    binding = common.owner_auth_binding(nonce, client_nonce)
    envelope = {'schemaVersion': 1, 'clientNonce': client_nonce, 'request': request,
                'proof': proof or common.owner_proof(key, binding, request)}
    sock.sendall((json.dumps(envelope) + '\n').encode())
    result = json.loads(common.read_owner_frame(sock, common.MAX_RESPONSE_BYTES + 512, time.monotonic() + 2))
    if 'reply' in result:
        assert result['proof'] == common.owner_reply_proof(key, binding, request, result['reply'])
        return result['reply']
    return result


def test_authenticated_request_once_and_no_second_dispatch_on_same_connection(endpoint):
    path, key, calls, _ = endpoint
    request = {'schemaVersion': 1, 'action': 'decide', 'approvalId': 'invented', 'decision': 'approve'}
    sock, nonce = connect(path)
    with sock:
        assert submit(sock, key, nonce, request) == {'ok': True, 'received': True}
        assert sock.recv(1) == b''
    assert calls == [('owner', request)]


@pytest.mark.parametrize('fault', ['key', 'action', 'role', 'nonce', 'version'])
def test_authentication_binds_complete_request_and_fresh_connection(endpoint, fault):
    path, key, calls, _ = endpoint
    request = {'schemaVersion': 1, 'action': 'get'}
    sock, nonce = connect(path)
    client_nonce = os.urandom(32).hex()
    binding = common.owner_auth_binding(nonce, client_nonce)
    proof = common.owner_proof(key, binding, request)
    if fault == 'key':
        proof = common.owner_proof(b'x' * 32, binding, request)
    if fault == 'action':
        request['action'] = 'save'
    if fault == 'role':
        request['role'] = 'owner'
    if fault == 'nonce':
        proof = common.owner_proof(key, '0' * 64, request)
    if fault == 'version':
        request['schemaVersion'] = 2
    with sock:
        assert submit(sock, key, nonce, request, proof=proof, client_nonce=client_nonce)['error'] == 'forbidden'
    assert calls == []


def test_old_authenticated_frame_cannot_be_replayed_on_new_connection(endpoint):
    path, key, calls, _ = endpoint
    request = {'schemaVersion': 1, 'action': 'get'}
    first, nonce = connect(path)
    client_nonce = os.urandom(32).hex()
    proof = common.owner_proof(key, common.owner_auth_binding(nonce, client_nonce), request)
    with first:
        assert submit(first, key, nonce, request, client_nonce=client_nonce)['ok']
    second, newer = connect(path)
    assert nonce != newer
    with second:
        assert submit(second, key, newer, request, proof=proof, client_nonce=client_nonce)['error'] == 'forbidden'
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [b'{"schemaVersion":1,"action":"get"}\n', b'{}\n{}\n', b'x' * (common.MAX_REQUEST_BYTES + 1025)])
def test_legacy_bare_frames_extra_frames_and_oversized_frames_fail_closed(endpoint, payload):
    path, _, calls, _ = endpoint
    sock, _ = connect(path)
    with sock:
        sock.sendall(payload)
        assert json.loads(common.read_owner_frame(sock, 4096, time.monotonic() + 2))['error'] == 'forbidden'
    assert calls == []


def test_authentication_has_whole_frame_deadline_even_for_drip_bytes(endpoint, monkeypatch):
    path, _, calls, _ = endpoint
    monkeypatch.setattr(integrations, 'OWNER_AUTH_SECONDS', .15)
    sock, _ = connect(path)
    began = time.monotonic()
    with sock:
        for _ in range(3):
            sock.sendall(b' ')
            time.sleep(.04)
        result = json.loads(common.read_owner_frame(sock, 4096, time.monotonic() + 1))
    assert result['error'] == 'forbidden'
    assert time.monotonic() - began < .8
    assert calls == []


def test_native_agent_frame_deadline_prevents_drip_client_from_holding_shutdown(tmp_path, monkeypatch):
    calls = []
    service = SimpleNamespace(handle=lambda *args: calls.append(args))
    # Select native framing only; peer identity remains the real Linux kernel
    # transport in this fixture. This does not simulate Darwin getpeereid.
    with monkeypatch.context() as patch:
        patch.setattr(integrations.sys, 'platform', 'darwin')
        server = integrations._listener(service, tmp_path / 'agent.sock', 'agent')
    monkeypatch.setattr(integrations, 'REQUEST_SECONDS', .15)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    began = time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(tmp_path / 'agent.sock'))
            for _ in range(3):
                client.sendall(b' ')
                time.sleep(.04)
            reply = json.loads(common.read_owner_frame(client, 4096, time.monotonic() + 1))
            assert reply['error'] == 'invalid-request'
        assert time.monotonic() - began < .8
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_owner_connection_cap_releases_slots_after_authentication(endpoint):
    path, key, calls, server = endpoint
    clients = [connect(path) for _ in range(32)]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as extra:
            extra.settimeout(1)
            extra.connect(str(path))
            assert extra.recv(1) == b''
        assert not calls
        first, nonce = clients.pop()
        with first:
            assert submit(first, key, nonce, {'schemaVersion': 1, 'action': 'get'})['ok']
        deadline = time.monotonic() + 1
        while server.slots._value != 1 and time.monotonic() < deadline:
            time.sleep(.01)
        another, nonce = connect(path)
        with another:
            assert submit(another, key, nonce, {'schemaVersion': 1, 'action': 'get'})['ok']
    finally:
        for client, nonce in clients:
            with client:
                submit(client, key, nonce, {'schemaVersion': 1, 'action': 'get'}, proof='0' * 64)


def test_native_client_never_retries_after_an_authenticated_dispatch_loses_reply(endpoint, monkeypatch):
    path, key, calls, server = endpoint
    monkeypatch.setattr(common, 'native_owner_key', lambda: key)
    original = common.read_owner_frame
    reads = []
    def lose_reply(connection, limit, deadline):
        reads.append(limit)
        if len(reads) == 2:
            time.sleep(.05)
            raise ConnectionError('invented lost reply')
        return original(connection, limit, deadline)
    monkeypatch.setattr(common, 'read_owner_frame', lose_reply)
    with pytest.raises(ConnectionError):
        common.native_owner_request(path, {'schemaVersion': 1, 'action': 'get'})
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['none', 'bare', 'changed-reply', 'wrong-key', 'old-nonce', 'request-mac', 'unicode'])
def test_native_owner_client_authenticates_exact_reply_without_retrying(endpoint, monkeypatch, fault):
    path, key, calls, _ = endpoint
    monkeypatch.setattr(common, 'native_owner_key', lambda: key)
    request = {'schemaVersion': 1, 'action': 'get'}
    original, challenge = common.read_owner_frame, []
    def response(connection, limit, deadline):
        line = original(connection, limit, deadline)
        value = json.loads(line)
        if not challenge:
            challenge.append(value['challenge'])
            return line
        if fault == 'bare':
            return json.dumps(value['reply']).encode() + b'\n'
        if fault == 'changed-reply':
            value['reply']['received'] = 'forged settings/save acknowledgement'
        if fault == 'wrong-key':
            value['proof'] = common.owner_reply_proof(b'x' * 32, challenge[0], request, value['reply'])
        if fault == 'old-nonce':
            value['proof'] = common.owner_reply_proof(key, '0' * 64, request, value['reply'])
        if fault == 'request-mac':
            value['proof'] = common.owner_proof(key, challenge[0], request)
        if fault == 'unicode':
            value['proof'] = 'é' * 64
        return json.dumps(value).encode() + b'\n'
    monkeypatch.setattr(common, 'read_owner_frame', response)
    if fault == 'none':
        assert common.native_owner_request(path, request) == {'ok': True, 'received': True}
    else:
        with pytest.raises(ValueError, match='invalid-owner-response'):
            common.native_owner_request(path, request)
    assert calls == [('owner', request)]


def test_replayed_server_challenge_and_signed_reply_cannot_forge_a_new_owner_result(endpoint, monkeypatch):
    path, key, calls, _ = endpoint
    monkeypatch.setattr(common, 'native_owner_key', lambda: key)
    original, transcript = common.read_owner_frame, []
    replaying = False
    def replay(connection, limit, deadline):
        line = original(connection, limit, deadline)
        if not replaying:
            transcript.append(line)
            return line
        # A substituted endpoint replays a complete earlier server transcript.
        # The new client nonce must still prevent accepting its old result.
        return transcript[0] if 'challenge' in json.loads(line) else transcript[1]
    monkeypatch.setattr(common, 'read_owner_frame', replay)
    request = {'schemaVersion': 1, 'action': 'get'}
    assert common.native_owner_request(path, request)['received'] is True
    replaying = True
    with pytest.raises(ValueError, match='invalid-owner-response'):
        common.native_owner_request(path, request)
    assert calls == [('owner', request)]


def test_root_owned_key_rejects_ordinary_user_key_and_link_alias(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip('This negative fixture must be owned by an ordinary user')
    root = tmp_path / 'auth'
    folder = root / str(os.getuid())
    folder.mkdir(parents=True)
    root.chmod(0o755)
    folder.chmod(0o700)
    key = folder / 'owner.key'
    key.write_bytes(b'x' * 32)
    key.chmod(0o600)
    monkeypatch.setattr(common, 'NATIVE_AUTH_ROOT', root)
    with pytest.raises(ValueError, match='custody'):
        common.native_owner_key()
    key.unlink()
    key.symlink_to(tmp_path / 'outside')
    with pytest.raises((ValueError, OSError)):
        common.native_owner_key()


@pytest.mark.parametrize('fault', ['none', 'size', 'mode', 'hardlink', 'symlink', 'directory-mode'])
def test_key_descriptor_checks_with_simulated_root_ownership(tmp_path, monkeypatch, fault):
    """Only uid metadata is simulated; opens, link checks and contents are real.

    This does not claim that Darwin ACL or Seatbelt enforcement ran on Linux.
    """
    uid = os.getuid() or 501
    monkeypatch.setattr(common.sys, 'platform', 'linux')
    root = tmp_path / 'auth'
    folder = root / str(uid)
    folder.mkdir(parents=True)
    root.chmod(0o755)
    folder.chmod(0o700)
    key = folder / 'owner.key'
    key.write_bytes(b'k' * 32)
    key.chmod(0o600)
    if fault == 'size':
        key.write_bytes(b'k' * 31)
    if fault == 'mode':
        key.chmod(0o640)
    if fault == 'hardlink':
        os.link(key, tmp_path / 'alias')
    if fault == 'symlink':
        key.rename(tmp_path / 'elsewhere')
        key.symlink_to(tmp_path / 'elsewhere')
    if fault == 'directory-mode':
        folder.chmod(0o755)
    original = common.os.fstat
    def root_metadata(fd):
        values = list(original(fd))
        values[4] = 0
        return os.stat_result(values)
    monkeypatch.setattr(common, 'NATIVE_AUTH_ROOT', root)
    monkeypatch.setattr(common.os, 'fstat', root_metadata)
    if fault == 'none':
        assert common.native_owner_key(uid) == b'k' * 32
    else:
        with pytest.raises((ValueError, OSError)):
            common.native_owner_key(uid)


def test_mac_command_lookup_adds_homebrew_without_inheriting_ambient_secrets(monkeypatch):
    monkeypatch.setattr(mcp.sys, 'platform', 'darwin')
    monkeypatch.setenv('INVENTED_UNRELATED_TOKEN', 'private')
    _, env = mcp.command_env({'command': 'npx invented', 'envNames': ['SELECTED']}, {'SELECTED': 'chosen'})
    assert env['PATH'].startswith('/opt/homebrew/bin:/opt/homebrew/opt/node@24/bin:/usr/local/opt/node@24/bin:')
    assert 'INVENTED_UNRELATED_TOKEN' not in env
    assert env['SELECTED'] == 'chosen'


def test_closing_local_servers_prevents_starts_after_shutdown():
    local = mcp.LocalServers()
    local.close()
    with pytest.raises(common.IntegrationError, match='stopping'):
        local.get({'id': 'new', 'name': 'Never launched', 'command': '/never/launch'}, {})


@pytest.mark.parametrize('directory', [False, True])
@pytest.mark.parametrize('fault', ['none', 'missing', 'invalid', 'deny', 'write', 'inherit', 'foreign', 'extra'])
def test_native_owner_acl_accepts_only_exact_owner_read_without_inheritance(monkeypatch, directory, fault):
    """Exercise the declared ABI and refusals with a fake library, not a fake Mac result."""
    import ctypes
    import errno
    principal = bytes(range(16))
    memory = ctypes.create_string_buffer(principal)
    freed = []
    def out(pointer, kind, value):
        ctypes.cast(pointer, ctypes.POINTER(kind)).contents.value = value
        return 0
    def get_entry(_acl, which, result):
        if which == 0 or fault == 'extra':
            return out(result, ctypes.c_void_p, 202)
        ctypes.set_errno(errno.EINVAL)
        return -1
    def uid_uuid(_uid, target):
        ctypes.memmove(target, b'x' * 16 if fault == 'foreign' else principal, 16)
        return 0
    mask = (1 << 1) | (1 << 7) | (1 << 11) | ((1 << 3) if directory else (1 << 9))
    functions = {
        'acl_get_fd_np': lambda *_: None if fault == 'missing' else 101,
        'acl_valid': lambda *_: -1 if fault == 'invalid' else 0,
        'acl_get_entry': get_entry,
        'acl_get_tag_type': lambda _entry, result: out(result, ctypes.c_int, 2 if fault == 'deny' else 1),
        'acl_get_permset_mask_np': lambda _entry, result: out(result, ctypes.c_uint64, mask | ((1 << 2) if fault == 'write' else 0)),
        'acl_get_flagset_np': lambda _item, result: out(result, ctypes.c_void_p, 303),
        'acl_get_flag_np': lambda _flags, flag: int(fault == 'inherit' and flag == 1 << 4),
        'acl_get_qualifier': lambda *_: ctypes.addressof(memory),
        'acl_free': lambda pointer: freed.append(pointer) or 0,
        'mbr_uid_to_uuid': uid_uuid,
    }
    class Function:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)
    library = SimpleNamespace(**{name: Function(function) for name, function in functions.items()})
    monkeypatch.setattr(common.sys, 'platform', 'darwin')
    monkeypatch.setattr(common.ctypes, 'CDLL', lambda *args, **kwargs: library)
    if fault == 'none':
        common.verify_native_owner_acl(42, 501, directory=directory)
        assert ctypes.addressof(memory) in freed
    else:
        with pytest.raises(ValueError, match='owner-acl-required'):
            common.verify_native_owner_acl(42, 501, directory=directory)
    if fault != 'missing':
        assert 101 in freed


def test_agent_socket_does_not_accept_owner_role_or_owner_authentication_frame(tmp_path):
    store = common.Store(tmp_path / 'state')
    service = integrations.Integrations(store)
    server = integrations._listener(service, tmp_path / 'agent.sock', 'agent')
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        reply = integrations.client(tmp_path / 'agent.sock', {'action': 'decide', 'role': 'owner', 'approvalId': 'invented', 'decision': 'approve'})
        assert reply['error'] == 'forbidden'
        reply = integrations.client(tmp_path / 'agent.sock', {'request': {'schemaVersion': 1, 'action': 'get'}, 'proof': '0' * 64})
        assert not reply['ok']
        assert store.approvals() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        service.local.close()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux lifecycle fixture; native launchd/Seatbelt has a separate physical qualification gate')
def test_service_sigterm_reaps_real_local_mcp_leader_and_grandchild(tmp_path):
    program = tmp_path / 'invented_mcp.py'
    pids = tmp_path / 'owned-pids.json'
    program.write_text('''import json, os, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
with open(sys.argv[1], 'w') as stream: json.dump([os.getpid(), child.pid], stream)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request: continue
    result = {'protocolVersion': '2025-03-26', 'capabilities': {}} if request['method'] == 'initialize' else {'tools': []}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
''')
    state = tmp_path / 'state'
    store = common.Store(state)
    config = store.config()
    config['mcp']['servers'] = [{'id': 'invented', 'name': 'Invented MCP', 'kind': 'stdio', 'enabled': False,
        'preset': '', 'url': '', 'command': shlex.join([sys.executable, str(program), str(pids)]),
        'workingFolder': str(tmp_path), 'envNames': [], 'tools': [], 'discoveredAt': None, 'checkedBinding': None}]
    store.save_config(config)
    agent = tmp_path / 'control.sock'
    process = subprocess.Popen([sys.executable, '-I', '-B', integrations.__file__, 'serve', str(agent), str(state)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    owned = []
    try:
        deadline = time.monotonic() + 5
        while not (state / 'owner.sock').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        reply = integrations.client(state / 'owner.sock', {'action': 'discover', 'serverId': 'invented'})
        assert reply['ok'] is True, reply
        owned = json.loads(pids.read_text())
        process.terminate()
        output, error = process.communicate(timeout=12)
        assert process.returncode == 0, (output, error)
        for pid in owned:
            proc = Path(f'/proc/{pid}/status')
            if proc.exists():
                assert 'State:\tZ' in proc.read_text(), f'Owned descendant {pid} survived SIGTERM'
            elif sys.platform == 'darwin':
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        for pid in owned:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def test_native_http_shutdown_reaps_exact_worker_and_refuses_new_starts(monkeypatch):
    import http.server
    import urllib.request
    entered, finish = threading.Event(), threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            finish.wait(5)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    failures = []
    request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/invented')
    def read():
        try:
            common.safe_urlopen(request)
        except common.IntegrationError as error:
            failures.append(error.code)
    client = threading.Thread(target=read)
    client.start()
    try:
        assert entered.wait(5)
        workers = list(common._HTTP_WORKERS)
        assert len(workers) == 1
        common.stop_http_workers()
        client.join(5)
        assert failures == ['integration-unavailable'] and not client.is_alive()
        assert all(worker.poll() is not None for worker in workers)
        monkeypatch.setattr(common.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('shutdown started another worker'))
        with pytest.raises(common.IntegrationError, match='stopping'):
            common.safe_urlopen(request)
    finally:
        common._HTTP_STOP.clear()
        finish.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        client.join(5)


@pytest.mark.parametrize('fault', ['none', 'state-mode', 'file-mode', 'symlink', 'hardlink', 'directory', 'state-acl', 'file-acl'])
def test_native_state_refuses_exposure_and_aliases_without_chmod_or_reading_data(tmp_path, monkeypatch, fault):
    if os.getuid() == 0:
        pytest.skip('Private owner state fixture requires a nonroot user')
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    secret = state / 'secrets.json'
    secret.write_text('{"invented":"private"}\n')
    secret.chmod(0o600)
    state_inode, file_inode = state.stat().st_ino, secret.stat().st_ino
    if fault == 'state-mode':
        state.chmod(0o755)
    elif fault == 'file-mode':
        secret.chmod(0o644)
    elif fault == 'symlink':
        secret.rename(tmp_path / 'outside')
        secret.symlink_to(tmp_path / 'outside')
    elif fault == 'hardlink':
        os.link(secret, tmp_path / 'outside')
    elif fault == 'directory':
        (state / 'unexpected-folder').mkdir(mode=0o700)
    def acl(fd):
        inode = os.fstat(fd).st_ino
        if fault == 'state-acl' and inode == state_inode or fault == 'file-acl' and inode == file_inode:
            raise ValueError('invented ACL grants')
    monkeypatch.setattr(common.sys, 'platform', 'darwin')
    monkeypatch.setattr(common, 'verify_native_no_acl', acl)  # ABI is separately tested; no fake kernel claim.
    before = [(p.lstat().st_ino, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in (state, secret)]
    if fault == 'none':
        common.validate_native_state(state)
    else:
        with pytest.raises(ValueError):
            common.validate_native_state(state)
    assert before == [(p.lstat().st_ino, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in (state, secret)]


def test_uncertain_http_shutdown_still_stops_all_listeners_and_local_programs(monkeypatch):
    events = []
    running = integrations.RunningIntegrations([
        SimpleNamespace(shutdown=lambda: events.append('agent-stop'), server_close=lambda: events.append('agent-close')),
        SimpleNamespace(shutdown=lambda: events.append('owner-stop'), server_close=lambda: events.append('owner-close'))])
    running.stop, running.native = threading.Event(), True
    running.service = SimpleNamespace(local=SimpleNamespace(close=lambda: events.append('mcp-close')))
    running.watcher = SimpleNamespace(join=lambda _seconds: events.append('watch-join'), is_alive=lambda: False)
    def fail():
        events.append('http-stop')
        raise TimeoutError('invented unconfirmed worker')
    monkeypatch.setattr(integrations, 'stop_http_workers', fail)
    with pytest.raises(RuntimeError, match='stop-unconfirmed'):
        running.close()
    assert running.stop.is_set()
    assert events == ['http-stop', 'agent-stop', 'agent-close', 'owner-stop', 'owner-close', 'mcp-close', 'watch-join']


def test_owner_state_preparation_creates_as_owner_without_root_chown_or_permission_repair(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip('Owner process creation fixture needs a nonroot user')
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    state = home / '.config/ods/pixel-integrations'
    monkeypatch.setattr(common.sys, 'platform', 'darwin')
    monkeypatch.setattr(common, 'verify_native_no_acl', lambda _fd: None)
    monkeypatch.setattr(common.os, 'chown', lambda *args, **kwargs: pytest.fail('owner creation must not depend on root chown'))
    common.prepare_native_state(state)
    assert all(path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o777 == 0o700
               for path in (home / '.config', home / '.config/ods', state))
    state.chmod(0o755)
    with pytest.raises(ValueError, match='private-state-required'):
        common.prepare_native_state(state)
    assert state.stat().st_mode & 0o777 == 0o755

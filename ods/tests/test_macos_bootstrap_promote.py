import importlib.util
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / 'scripts' / 'macos-bootstrap-promote.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('macos_bootstrap_promote', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod(monkeypatch):
    module = _load_module()
    if os.name == 'nt':
        # Windows can exercise orchestration and real HTTP, not POSIX file
        # custody. Those checks run unmodified on Ubuntu and macOS in CI.
        monkeypatch.setattr(module, '_read_private_env',
                            lambda path: module._parse_env(Path(path).read_text(encoding='utf-8')))
    return module


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, method, headers, body, timeout):
        self.calls.append({'url': url, 'method': method, 'headers': dict(headers),
                           'body': body, 'timeout': timeout})
        if not self.responses:
            raise AssertionError('unexpected request')
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _write_env(path, entries):
    lines = []
    for key, value in entries.items():
        lines.append(f'{key}={value}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)


def _catalog(install, rows):
    config = install / 'config'
    config.mkdir(parents=True, exist_ok=True)
    (config / 'model-library.json').write_text(
        json.dumps({'models': rows}), encoding='utf-8')


def _base_env(install, **overrides):
    env = {
        'ODS_AGENT_KEY': 'agent-secret',
        'ODS_AGENT_PORT': '7710',
        'GPU_BACKEND': 'apple',
        'PIXEL_OPENWEBUI_KEY': 'openwebui-secret',
        'PIXEL_NATIVE_CONFIG_PATH': str(install / 'native.json'),
    }
    env.update(overrides)
    return env


def _row(gguf='model-9b.gguf', llm='qwen3.5-9b', ctx=32768, mid='qwen3.5-9b-q4'):
    return {'id': mid, 'gguf_file': gguf, 'llm_model_name': llm,
            'max_context_length': ctx}


def _ok_activation(gguf='model-9b.gguf', llm='qwen3.5-9b', ctx=32768,
                   mid='qwen3.5-9b-q4'):
    return {'status': 'activated', 'model_id': mid, 'gguf_file': gguf,
            'llm_model': llm, 'context_length': ctx,
            'consumers': {'pixel': 'reconciled'}}


def _ok_status(gguf='model-9b.gguf', ctx=32768, install=None):
    canonical = str(install / 'data' / 'models' / gguf) if install else gguf
    return {'status': 'idle', 'modelTransactionPending': False,
            'activeAgentViable': True,
            'activeRuntime': {'source': 'local-switchboard', 'model': canonical, 'contextLength': ctx}}


def _install_transport(mod, monkeypatch, transport):
    monkeypatch.setattr(mod, '_request', transport)


def _setup(tmp_path, mod, monkeypatch, *, env_overrides=None, rows=None,
           responses=None, platform='darwin'):
    install = tmp_path / 'install'
    install.mkdir()
    env_path = install / '.env'
    _write_env(env_path, _base_env(install, **(env_overrides or {})))
    _catalog(install, rows if rows is not None else [_row()])
    transport = FakeTransport(responses or [])
    _install_transport(mod, monkeypatch, transport)
    monkeypatch.setattr(mod.sys, 'platform', platform)
    return install, env_path, transport


def _run(mod, install, env_path, gguf='model-9b.gguf', llm='qwen3.5-9b',
         ctx='32768'):
    return mod.run(str(install), gguf, llm, ctx, env_path=str(env_path))


def test_2b_to_9b_activation_contract(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(),
            {'pending': False},
            _ok_status(),
        ])
    _write_env(env_path, _base_env(install, GGUF_FILE='model-2b.gguf',
                                   LLM_MODEL='qwen3.5-2b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    def activating_transport(url, **kwargs):
        response = transport(url, **kwargs)
        if kwargs['method'] == 'POST':
            _write_env(env_path, _base_env(install, GGUF_FILE='model-9b.gguf',
                LLM_MODEL='qwen3.5-9b', MAX_CONTEXT='32768', CTX_SIZE='32768'))
        return response
    monkeypatch.setattr(mod, '_request', activating_transport)
    assert _run(mod, install, env_path) == 0
    posts = [c for c in transport.calls if c['method'] == 'POST']
    assert len(posts) == 1
    assert posts[0]['url'].endswith('/v1/model/activate')
    body = json.loads(posts[0]['body'])
    assert body == {'model_id': 'qwen3.5-9b-q4', 'context_length': 32768}


def test_waits_for_read_only_host_agent_startup(mod, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mod.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    transport = FakeTransport([
        mod.PreflightFailure('transport-error'),
        mod.PreflightFailure('http-503'),
        {'pending': False},
        {'status': 'complete', 'modelTransactionPending': False},
    ])
    mod._wait_for_preflight('http://127.0.0.1:7710', 'test-key', transport)
    assert clock[0] == 4
    assert all(call['method'] == 'GET' for call in transport.calls)


def test_host_agent_startup_wait_is_bounded(mod, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mod, 'PREFLIGHT_WAIT_SECONDS', 4)
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mod.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    transport = FakeTransport([mod.PreflightFailure('transport-error')] * 3)
    with pytest.raises(mod.PreflightFailure, match='host-agent-readiness-timeout'):
        mod._wait_for_preflight('http://127.0.0.1:7710', 'test-key', transport)
    assert len(transport.calls) == 3
    assert all(call['method'] == 'GET' for call in transport.calls)


@pytest.mark.parametrize('first', [
    {'pending': True}, 'http-401',
])
def test_recovery_or_auth_failure_is_not_retried(mod, first):
    if isinstance(first, str):
        first = mod.PreflightFailure(first)
    transport = FakeTransport([first])
    with pytest.raises(mod.PreflightFailure):
        mod._wait_for_preflight('http://127.0.0.1:7710', 'test-key', transport)
    assert len(transport.calls) == 1


def test_validation_before_post(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, rows=[_row(ctx=16384)], responses=[])
    with pytest.raises(ValueError):
        _run(mod, install, env_path, ctx='32768')
    assert transport.calls == []


def test_post_failure_no_second_post_or_env_write(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            mod.PreflightFailure('http-500'),
        ])
    before = env_path.read_bytes()
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)
    posts = [c for c in transport.calls if c['method'] == 'POST']
    assert len(posts) == 1
    assert env_path.read_bytes() == before


def test_wrong_target_rejected(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(gguf='other.gguf'),
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)


def test_wrong_context_rejected(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(ctx=16384),
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)


def test_wrong_consumer_rejected(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            {**_ok_activation(), 'consumers': {'pixel': 'pending'}},
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)


def test_pending_recovery_blocks_before_post(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[{'pending': True}])
    with pytest.raises(mod.PreflightFailure):
        _run(mod, install, env_path)
    assert all(c['method'] != 'POST' for c in transport.calls)


def test_completed_bootstrap_does_not_clear_held_2b_switch(mod, tmp_path, monkeypatch):
    # Reported retained state: the legacy updater finished 9B, but a later
    # Portal switch captured the old 2B contract. "complete" is not permission
    # to discard the host/controller journals or replay model activation.
    install, env_path, transport = _setup(tmp_path, mod, monkeypatch,
        responses=[{'pending': True, 'phase': 'held', 'transactionId': 'a' * 64}])
    data = install / 'data'
    data.mkdir()
    journal = data / 'pixel-model-transaction.json'
    journal.write_text(json.dumps({'phase': 'held', 'outcome': None,
        'previous': {'model': 'Qwen3.5-2B-Q4_K_M.gguf', 'contextLength': 65536},
        'target': None}))
    status = data / 'bootstrap-status.json'
    status.write_text(json.dumps({'status': 'complete',
        'model': 'Qwen3.5-9B-Q4_K_M.gguf', 'percent': 100}))
    model = data / 'Qwen3.5-9B-Q4_K_M.gguf'
    model.write_bytes(b'existing-model-fixture')
    before = {path: path.read_bytes() for path in (env_path, journal, status, model)}
    with pytest.raises(mod.PreflightFailure, match='model-switch-recovery-required'):
        _run(mod, install, env_path)
    assert len(transport.calls) == 1 and transport.calls[0]['method'] == 'GET'
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('source', ['remote-provider', None, 'unknown'])
def test_runtime_proof_requires_local_source(mod, tmp_path, source):
    status = _ok_status()
    status['activeRuntime']['source'] = source
    transport = FakeTransport([{'pending': False}, status])
    with pytest.raises(mod.AmbiguousActivation, match='active-runtime-source-mismatch'):
        mod._verify_runtime('http://127.0.0.1:7710', 'test-secret', 'model-9b.gguf',
                            32768, tmp_path, transport)


@pytest.mark.parametrize('mode', ['cloud', 'remote', 'unknown'])
def test_persisted_remote_mode_never_proves_native_promotion(mod, tmp_path, mode):
    path = tmp_path / '.env'
    _write_env(path, _base_env(tmp_path, ODS_MODE=mode, GGUF_FILE='model-9b.gguf',
        LLM_MODEL='qwen3.5-9b', MAX_CONTEXT='32768', CTX_SIZE='32768'))
    with pytest.raises(mod.AmbiguousActivation, match='persisted-runtime-mode-mismatch'):
        mod._verify_persisted(path, 'model-9b.gguf', 'qwen3.5-9b', 32768)


@pytest.mark.parametrize('mode', ['local', 'hybrid'])
def test_persisted_local_modes_allow_native_promotion(mod, tmp_path, mode):
    path = tmp_path / '.env'
    _write_env(path, _base_env(tmp_path, ODS_MODE=mode, GGUF_FILE='model-9b.gguf',
        LLM_MODEL='qwen3.5-9b', MAX_CONTEXT='32768', CTX_SIZE='32768'))
    mod._verify_persisted(path, 'model-9b.gguf', 'qwen3.5-9b', 32768)


def test_pending_guidance_does_not_expose_private_values(mod, monkeypatch, capsys):
    def pending(*args):
        raise mod.PreflightFailure('model-switch-recovery-required')
    monkeypatch.setattr(mod, 'run', pending)
    assert mod.main(['/private/install', 'model.gguf', 'model', '65536']) == 1
    output = capsys.readouterr().err
    assert 'Preserve data/pixel-model-transaction.json' in output
    assert 'MACOS-MODEL-PROMOTION.md' in output
    assert '/private/install' not in output


def test_unsafe_env_symlink_refused(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[])
    real = env_path.with_suffix('.real')
    env_path.rename(real)
    env_path.symlink_to(real)
    with pytest.raises(ValueError):
        _run(mod, install, env_path)
    assert transport.calls == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX custody is exercised on Ubuntu/macOS CI')
def test_unsafe_env_permissions_refused(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[])
    os.chmod(env_path, 0o644)
    with pytest.raises(ValueError):
        _run(mod, install, env_path)
    assert transport.calls == []


def test_standalone_returns_10(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[],
        env_overrides={'PIXEL_OPENWEBUI_KEY': '', 'PIXEL_NATIVE_CONFIG_PATH': ''})
    _write_env(env_path, {'ODS_AGENT_KEY': 'agent-secret', 'ODS_AGENT_PORT': '7710'})
    assert _run(mod, install, env_path) == 10


def test_standalone_with_native_lexists_fails_incomplete(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[],
        env_overrides={'PIXEL_OPENWEBUI_KEY': '', 'PIXEL_NATIVE_CONFIG_PATH': ''})
    _write_env(env_path, {'ODS_AGENT_KEY': 'agent-secret', 'ODS_AGENT_PORT': '7710',
                          'GGUF_FILE': 'model-9b.gguf'})
    (install / 'data/pixel-native').mkdir(parents=True)
    with pytest.raises(ValueError):
        _run(mod, install, env_path)
    assert transport.calls == []


def test_non_darwin_refused(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[], platform='linux')
    with pytest.raises(SystemExit):
        _run(mod, install, env_path)
    assert transport.calls == []


def test_context_equality_allows_requested_below_max(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        rows=[_row(ctx=262144)],
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(ctx=65536),
            {'pending': False},
            _ok_status(gguf='model-9b.gguf', ctx=65536),
        ])
    _write_env(env_path, _base_env(install, GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='65536', CTX_SIZE='65536'))
    assert _run(mod, install, env_path, ctx='65536') == 0


def test_context_below_minimum_rejected(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, rows=[_row(ctx=262144)], responses=[])
    with pytest.raises(ValueError):
        _run(mod, install, env_path, ctx='2048')
    assert transport.calls == []


def test_context_bool_rejected(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, rows=[_row(ctx=262144)], responses=[])
    with pytest.raises(ValueError):
        _run(mod, install, env_path, ctx='True')
    assert transport.calls == []


def test_gguf_basename_rejects_paths(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[])
    for bad in ('../model.gguf', 'sub/model.gguf', 'sub\\model.gguf',
                'a' * 300 + '.gguf'):
        with pytest.raises(ValueError):
            _run(mod, install, env_path, gguf=bad)
    assert transport.calls == []


def test_env_path_must_be_canonical(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[])
    other = tmp_path / 'other.env'
    _write_env(other, _base_env(install))
    with pytest.raises(ValueError):
        mod.run(str(install), 'model-9b.gguf', 'qwen3.5-9b', '32768',
                env_path=str(other))
    assert transport.calls == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX O_NOFOLLOW is exercised on Ubuntu/macOS CI')
def test_env_symlink_oserror_sanitized(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch, responses=[])
    real = env_path.with_suffix('.real')
    env_path.rename(real)
    env_path.symlink_to(real)
    with pytest.raises(ValueError):
        mod._read_private_env(env_path)


def test_post_socket_timeout_is_ambiguous(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            socket.timeout('timed out'),
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)
    posts = [c for c in transport.calls if c['method'] == 'POST']
    assert len(posts) == 1


def test_post_oserror_is_ambiguous(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            OSError('connection reset'),
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)
    posts = [c for c in transport.calls if c['method'] == 'POST']
    assert len(posts) == 1


def test_persisted_failure_after_post_is_ambiguous(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(),
        ])
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)
    posts = [c for c in transport.calls if c['method'] == 'POST']
    assert len(posts) == 1


def test_runtime_model_must_be_exact_gguf_or_canonical(mod, tmp_path, monkeypatch):
    install, env_path, transport = _setup(
        tmp_path, mod, monkeypatch,
        responses=[
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False},
            _ok_activation(),
            {'pending': False},
            {'status': 'idle', 'modelTransactionPending': False,
             'activeAgentViable': True,
             'activeRuntime': {'model': '/etc/passwd', 'contextLength': 32768}},
        ])
    _write_env(env_path, _base_env(install, GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    with pytest.raises(mod.AmbiguousActivation):
        _run(mod, install, env_path)


class _RedirectReceiver(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        _RedirectReceiver.hits.append(self.path)
        self.send_response(302)
        self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/credential-trap')
        self.end_headers()

    def do_POST(self):
        _RedirectReceiver.hits.append(self.path)
        self.send_response(302)
        self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/credential-trap')
        self.end_headers()

    def log_message(self, *args):
        pass


class _ActivationServer(BaseHTTPRequestHandler):
    hits = []
    env_path = None
    install = None

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _ActivationServer.hits.append(('GET', self.path))
        if self.path == '/v1/model/recovery':
            self._json({'pending': False})
        elif self.path == '/v1/model/status':
            self._json(_ok_status(install=_ActivationServer.install))
        else:
            self._json({'error': 'not-found'}, status=404)

    def do_POST(self):
        _ActivationServer.hits.append(('POST', self.path))
        length = int(self.headers.get('Content-Length', '0'))
        self.rfile.read(length)
        _write_env(_ActivationServer.env_path,
                   _base_env(_ActivationServer.install,
                             GGUF_FILE='model-9b.gguf',
                             LLM_MODEL='qwen3.5-9b',
                             MAX_CONTEXT='32768', CTX_SIZE='32768'))
        self._json(_ok_activation())

    def log_message(self, *args):
        pass


def _serve(handler_cls):
    server = HTTPServer(('127.0.0.1', 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_real_transport_redirect_refused_no_forward(mod, tmp_path, monkeypatch):
    receiver, _ = _serve(_RedirectReceiver)
    _RedirectReceiver.hits = []
    try:
        with pytest.raises(mod.PreflightFailure):
            mod._request(f'http://127.0.0.1:{receiver.server_port}/v1/model/recovery',
                         method='GET', headers={}, body=None, timeout=5.0)
        assert _RedirectReceiver.hits == ['/v1/model/recovery']
    finally:
        receiver.shutdown()


def test_real_transport_ignores_hostile_http_proxy(mod, tmp_path, monkeypatch):
    install = tmp_path / 'install'
    install.mkdir()
    env_path = install / '.env'
    _write_env(env_path, _base_env(install, GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    _catalog(install, [_row()])
    _ActivationServer.hits = []
    _ActivationServer.env_path = env_path
    _ActivationServer.install = install
    server, _ = _serve(_ActivationServer)
    port = server.server_port
    _write_env(env_path, _base_env(install, ODS_AGENT_PORT=str(port),
                                   GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('https_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        assert mod.run(str(install), 'model-9b.gguf', 'qwen3.5-9b', '32768', platform='darwin') == 0
        posts = [h for h in _ActivationServer.hits if h[0] == 'POST']
        assert posts == [('POST', '/v1/model/activate')]
    finally:
        server.shutdown()


def test_real_transport_activation_roundtrip(mod, tmp_path, monkeypatch):
    install = tmp_path / 'install'
    install.mkdir()
    env_path = install / '.env'
    _write_env(env_path, _base_env(install, GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    _catalog(install, [_row()])
    _ActivationServer.hits = []
    _ActivationServer.env_path = env_path
    _ActivationServer.install = install
    server, _ = _serve(_ActivationServer)
    port = server.server_port
    _write_env(env_path, _base_env(install, ODS_AGENT_PORT=str(port),
                                   GGUF_FILE='model-9b.gguf',
                                   LLM_MODEL='qwen3.5-9b',
                                   MAX_CONTEXT='32768', CTX_SIZE='32768'))
    try:
        assert mod.run(str(install), 'model-9b.gguf', 'qwen3.5-9b', '32768', platform='darwin') == 0
        posts = [h for h in _ActivationServer.hits if h[0] == 'POST']
        assert posts == [('POST', '/v1/model/activate')]
    finally:
        server.shutdown()

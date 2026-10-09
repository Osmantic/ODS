"""Exercise the real installer route selection/config writer against loopback HTTP.

This checks host routing and upgrade preservation, not launchd, Metal or Docker
connectivity. Colima bridge tests cover the separate container-to-host hop.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, build_opener, ProxyHandler

import pytest

ODS = Path(__file__).resolve().parents[1]
MAC = ODS / 'installers/macos'
pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='Native POSIX installer')


def function(path, name):
    source = path.read_text(encoding='utf-8')
    return name + '() {' + source.split(name + '() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'


@pytest.fixture
def endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get('Authorization'),
                             json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            payload = b'{"choices":[{"message":{"content":"route verified"}}]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('bind', ['127.0.0.1', '0.0.0.0', '::', '::1', '192.168.106.1'])
@pytest.mark.parametrize('mode', ['switchboard', 'cloud', 'native'])
@pytest.mark.parametrize('custom_port', [False, True])
def test_installer_keeps_host_model_reachable_across_lan_modes(tmp_path, endpoint, bind, mode, custom_port):
    port, requests = endpoint
    install = tmp_path / 'ODS installation'
    config = tmp_path / 'OpenCode'
    install.mkdir()
    config.mkdir()
    # Simulate an upgrade from a LAN-bound route; unrelated owner settings stay.
    (config / 'opencode.json').write_text(json.dumps({
        'custom': {'preserve': True},
        'provider': {'llama-server': {'options': {'baseURL': 'http://192.0.2.1:8080/v1'}}},
    }), encoding='utf-8')
    values = [f'BIND_ADDRESS={bind}',
              'ODS_MODEL_SWITCHBOARD=' + ('enabled' if mode == 'switchboard' else 'disabled'),
              'LITELLM_KEY=fixture-only-key']
    if custom_port:
        values += [f'LITELLM_PORT={port}', f'ODS_NATIVE_LLAMA_PORT={port}']
    (install / '.env').write_text('\n'.join(values) + '\n', encoding='utf-8')
    source = (MAC / 'lib/post-pixel-install.sh').read_text(encoding='utf-8')
    # Run exactly the installer block that selects and persists OpenCode's route.
    block = source.split('        _opencode_switchboard_mode=', 1)[1]
    block = '        _opencode_switchboard_mode=' + block.split('\n        ai_ok "OpenCode configured', 1)[0]
    script = 'set -eu\nINSTALL_DIR="$1"\nOPENCODE_CONFIG_DIR="$2"\nCLOUD_MODE="$3"\n'
    script += 'LLM_MODEL=fixture-local-model\nMAX_CONTEXT=32768\nai_err() { echo "$*" >&2; }\n'
    script += function(MAC / 'lib/env-generator.sh', 'read_env_value')
    for name in ['macos_normalize_bind_address', 'macos_bind_probe_host']:
        script += function(MAC / 'lib/constants.sh', name)
    script += function(MAC / 'lib/post-pixel-install.sh', '_write_macos_opencode_config').replace('/usr/bin/python3', '"$ODS_TEST_PYTHON"')
    script += block + '\n'
    env = dict(os.environ, ODS_TEST_PYTHON=sys.executable)
    result = subprocess.run(['bash', '-s', '--', str(install), str(config),
                             'true' if mode == 'cloud' else 'false'],
                            input=script, text=True, capture_output=True, env=env, timeout=15)
    assert result.returncode == 0, result.stderr
    expected_port = port if custom_port else (8080 if mode == 'native' else 4000)
    expected_model = {'switchboard': 'ods/current', 'cloud': 'default', 'native': 'fixture-local-model'}[mode]
    expected_key = 'no-key' if mode == 'native' else 'fixture-only-key'
    data = json.loads((config / 'opencode.json').read_text(encoding='utf-8'))
    assert data == json.loads((config / 'config.json').read_text(encoding='utf-8'))
    assert data['custom'] == {'preserve': True}
    assert data['model'] == 'llama-server/' + expected_model
    options = data['provider']['llama-server']['options']
    assert options == {'baseURL': f'http://127.0.0.1:{expected_port}/v1', 'apiKey': expected_key}
    if custom_port:
        request = Request(options['baseURL'] + '/chat/completions',
                          data=json.dumps({'model': expected_model, 'messages': []}).encode(),
                          headers={'Authorization': 'Bearer ' + options['apiKey'], 'Content-Type': 'application/json'})
        # Ignore any CI/user proxy environment for the loopback test endpoint.
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert json.load(response)['choices'][0]['message']['content'] == 'route verified'
        assert requests == [('/v1/chat/completions', 'Bearer ' + expected_key,
                             {'model': expected_model, 'messages': []})]


@pytest.mark.parametrize('bind', ['0.0.0.0', '::', '192.168.106.1'])
def test_background_model_upgrade_preserves_private_listener(tmp_path, monkeypatch, bind):
    """Promotion must reach the shared launcher with private, tuned arguments.

    Execute the production promotion/restart/argv chain and tuning qualifier;
    replace only host service mutations and the post-launch readiness probe.
    """
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    host = load('ods_private_route_host', ODS / 'bin/ods-host-agent.py')
    promotion = load('ods_private_route_promotion', MAC / 'lib/pixel-native-model-promotion.py')
    install = tmp_path / 'ODS installation'
    runtime = install / 'bin/llama-server'
    service = install / 'installers/macos/lib/native-llama-service.sh'
    runtime.parent.mkdir(parents=True)
    service.parent.mkdir(parents=True)
    shutil.copy2(MAC / 'lib/native-llama-service.sh', service)
    shutil.copy2(MAC / 'lib/native-checkpoint-args.py', service.with_name('native-checkpoint-args.py'))
    # Real qualifier probes an inert runtime that prints the pinned --help fixture.
    help_file = install / 'runtime-help.txt'
    shutil.copy2(ODS / 'tests/fixtures/llama-server-help/b9014.txt', help_file)
    runtime.write_text('#!/bin/sh\ncat "' + str(help_file) + '"\n', encoding='utf-8')
    runtime.chmod(0o700)
    env_file = install / '.env'
    env_file.write_text(f'BIND_ADDRESS={bind}\nODS_NATIVE_LLAMA_PORT=18081\n'
        'GGUF_FILE=full-model.gguf\nLLM_MODEL=full-model\nCTX_SIZE=8192\n'
        'ODS_MODEL_SWITCHBOARD=disabled\nN_GPU_LAYERS=33\nLLAMA_ARG_CACHE_TYPE_K=q8_0\n',
        encoding='utf-8')
    monkeypatch.setattr(host, 'INSTALL_DIR', install)
    monkeypatch.setattr(host, 'DATA_DIR', install / 'data')
    monkeypatch.setattr(host.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(promotion.Path, 'home', lambda: tmp_path / 'home')
    events = []
    monkeypatch.setattr(host, '_require_macos_bridge_manager', lambda _path: events.append('validate'))
    monkeypatch.setattr(host, '_stop_macos_native_llama_server', lambda _path: events.append('stop'))
    monkeypatch.setattr(host, '_configure_macos_llm_bridge', lambda _path: events.append('bridge'))
    monkeypatch.setattr(host, '_disable_conflicting_macos_bridge', lambda *_args: None)
    monkeypatch.setattr(host, '_find_usable_bash', lambda: 'bash')
    monkeypatch.setattr(host, '_catalog_model_for_current_env', lambda _env: ('full-model', {}))
    monkeypatch.setattr(host, '_wait_for_model_readiness',
        lambda *_args, **_kwargs: dict(identity='full-model.gguf', contextLength=8192, contextVerified=True))
    launches = []
    real_run = subprocess.run
    def run(args, **kwargs):
        if len(args) > 1 and args[1] == str(service):
            assert args[2:5] == ['start', str(install), str(runtime)]
            events.append('launch')
            launches.append(args[6:])
            Path(args[5]).write_text('12345\n', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')
        return real_run(args, **kwargs)
    monkeypatch.setattr(host.subprocess, 'run', run)
    promotion.promote(host, host.load_env(env_file), 'unmanaged', 'full-model.gguf', 8192)
    assert events == ['validate', 'stop', 'bridge', 'launch']
    assert len(launches) == 1
    args = launches[0]
    assert args[args.index('--host') + 1] == '127.0.0.1'
    assert args[args.index('--port') + 1] == '18081'
    assert args[args.index('--model') + 1] == str(install / 'data/models/full-model.gguf')
    assert args[args.index('--ctx-size') + 1] == '8192'
    assert args[args.index('--n-gpu-layers') + 1] == '33'
    assert args[args.index('--cache-type-k') + 1] == 'q8_0'
    assert args[args.index('--ctx-checkpoints') + 1] == '32'
    assert args[args.index('--spec-type') + 1] == 'ngram-mod'

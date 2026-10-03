"""Exercise the real installer route selection/config writer against loopback HTTP.

This checks host routing and upgrade preservation, not launchd, Metal or Docker
connectivity. Colima bridge tests cover the separate container-to-host hop.
"""
import json
import os
from pathlib import Path
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
        'agent': {'build': {'model': 'owner/cloud-model', 'temperature': 0.2}},
        'provider': {'llama-server': {'options': {'baseURL': 'http://192.0.2.1:8080/v1'}}},
    }), encoding='utf-8')
    values = [f'BIND_ADDRESS={bind}',
              'ODS_MODEL_SWITCHBOARD=' + ('enabled' if mode == 'switchboard' else 'disabled'),
              'LITELLM_KEY=fixture-only-key']
    if custom_port:
        values += [f'LITELLM_PORT={port}', f'ODS_NATIVE_LLAMA_PORT={port}']
    (install / '.env').write_text('\n'.join(values) + '\n', encoding='utf-8')
    source = (MAC / 'install-macos.sh').read_text(encoding='utf-8')
    # Run exactly the installer block that selects and persists OpenCode's route.
    block = source.split('        _opencode_switchboard_mode=', 1)[1]
    block = '        _opencode_switchboard_mode=' + block.split('\n        ai_ok "OpenCode configured', 1)[0]
    script = 'set -eu\nINSTALL_DIR="$1"\nOPENCODE_CONFIG_DIR="$2"\nCLOUD_MODE="$3"\n'
    script += 'LLM_MODEL=fixture-local-model\nMAX_CONTEXT=32768\nai_err() { echo "$*" >&2; }\n'
    script += function(MAC / 'lib/env-generator.sh', 'read_env_value')
    for name in ['macos_normalize_bind_address', 'macos_bind_probe_host']:
        script += function(MAC / 'lib/constants.sh', name)
    script += function(MAC / 'install-macos.sh', '_write_macos_opencode_config').replace('/usr/bin/python3', '"$ODS_TEST_PYTHON"')
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
    assert data['agent']['build'] == {'model': 'owner/cloud-model', 'temperature': 0.2}
    assert data['agent']['plan']['model'] == 'llama-server/' + expected_model
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


def test_macos_opencode_agent_defaults_follow_the_ods_route(tmp_path):
    config = tmp_path / 'opencode.json'
    script = 'set -eu\n' + function(MAC / 'install-macos.sh', '_write_macos_opencode_config')
    script = script.replace('/usr/bin/python3', '"$ODS_TEST_PYTHON"')
    script += '\n_write_macos_opencode_config "$1" "$2" "$3" "$4" "$5"\n'
    env = dict(os.environ, ODS_TEST_PYTHON=sys.executable)

    def write(model):
        result = subprocess.run(
            ['bash', '-s', '--', str(config), model, 'http://127.0.0.1:4000/v1',
             'fixture-only-key', '32768'],
            input=script, text=True, capture_output=True, env=env, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert 'fixture-only-key' not in result.stdout
        return json.loads(config.read_text(encoding='utf-8'))

    fresh = write('ods/current')
    assert fresh['agent']['build']['model'] == 'llama-server/ods/current'
    assert fresh['agent']['plan']['model'] == 'llama-server/ods/current'

    fresh['agent']['plan'] = {'model': 'owner/plan-model', 'temperature': 0.1}
    fresh['agent']['reviewer'] = {'model': 'owner/review-model'}
    fresh['provider']['owner-cloud'] = {'name': 'Owner cloud'}
    config.write_text(json.dumps(fresh), encoding='utf-8')
    upgraded = write('new-model')
    assert upgraded['agent']['build']['model'] == 'llama-server/new-model'
    assert upgraded['agent']['plan'] == {'model': 'owner/plan-model', 'temperature': 0.1}
    assert upgraded['agent']['reviewer'] == {'model': 'owner/review-model'}
    assert upgraded['provider']['owner-cloud'] == {'name': 'Owner cloud'}
    assert upgraded == json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('bind', ['0.0.0.0', '::', '192.168.106.1'])
def test_background_model_upgrade_preserves_private_listener(tmp_path, bind):
    """Execute production argument assembly without launching Metal or launchd."""
    env_file = tmp_path / '.env'
    env_file.write_text(f'BIND_ADDRESS={bind}\nODS_NATIVE_LLAMA_PORT=18081\n'
                        'N_GPU_LAYERS=33\nLLAMA_ARG_CACHE_TYPE_K=q8_0\n', encoding='utf-8')
    source = (ODS / 'scripts/bootstrap-upgrade.sh').read_text(encoding='utf-8')
    begin = source.index('            # The dashboard\'s LAN binding must not expose native inference.')
    end = source.index('\n            # Relaunch with new model', begin)
    script = ('set -eu\nENV_FILE="$1"\n_model_path="$2"\n_ctx_size=8192\n'
              '_llama_tuning_args=()\n' + source[begin:end] +
              '\nprintf "%s\\0" "${_llama_args[@]}"\n')
    result = subprocess.run(['bash', '-s', '--', str(env_file), str(tmp_path / 'full model.gguf')],
                            input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    args = result.stdout.rstrip('\0').split('\0')
    assert args[args.index('--host') + 1] == '127.0.0.1'
    assert args[args.index('--port') + 1] == '18081'
    assert args[args.index('--model') + 1] == str(tmp_path / 'full model.gguf')
    assert args[args.index('--n-gpu-layers') + 1] == '33'
    assert args[args.index('--cache-type-k') + 1] == 'q8_0'

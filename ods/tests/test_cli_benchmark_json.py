"""Exercise the installed CLI against a real loopback inference HTTP fixture."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path):
    calls = []
    answer = 'Xin chào "ODS"\npath \\model'

    class Inference(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, payload))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": answer}}]}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Inference)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    install = tmp_path / 'custom install'
    (install / 'lib').mkdir(parents=True)
    for filename in ['service-registry.sh', 'safe-env.sh']:
        shutil.copy2(ROOT / 'lib' / filename, install / 'lib' / filename)
    shutil.copy2(ROOT / 'ods-cli', install / 'ods-cli')
    (install / 'docker-compose.base.yml').write_text('services: {}\n')
    (install / '.env').write_text(f'LLAMA_SERVER_PORT={server.server_port}\n')
    env = {key: value for key, value in os.environ.items() if key not in {
        'INSTALL_DIR', 'ODS_HOME', 'ODS_SCRIPT_HINT', 'ODS_INSTALL_DIR', 'OLLAMA_PORT',
    }}

    def run(*args):
        return subprocess.run(['bash', str(install / 'ods-cli'), *args], env=env,
                              text=True, capture_output=True, timeout=20)

    try:
        yield run, calls, answer, install
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('command', ['benchmark', 'bench', 'b'])
def test_json_round_trip_is_parseable_and_preserves_response(cli, command):
    run, calls, answer, _ = cli
    result = run(command, '--json')
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['schemaVersion'] == 1
    assert receipt['probe'] == 'chat_round_trip'
    assert receipt['response'] == answer
    assert 0 < receipt['durationSeconds'] < 20
    assert len(calls) == 2
    assert calls[1][1]['messages'] == [{'role': 'user', 'content': 'Say exactly: Hello World'}]
    assert 'Sending to fixture-model' in result.stderr


def test_default_human_output_remains_available(cli):
    run, _, answer, _ = cli
    result = run('benchmark')
    assert result.returncode == 0, result.stderr
    assert 'Benchmark Results' in result.stdout
    assert answer in result.stdout


@pytest.mark.parametrize('flag', ['--help', '--invalid'])
def test_options_are_resolved_before_inference_or_installation(cli, flag):
    run, calls, _, install = cli
    (install / '.env').unlink()
    result = run('benchmark', flag)
    assert result.returncode == (0 if flag == '--help' else 2)
    assert calls == []
    assert '--json' in result.stdout + result.stderr


def test_failed_inference_has_no_success_receipt(cli):
    run, calls, _, install = cli
    (install / '.env').write_text('LLAMA_SERVER_PORT=0\n')
    result = run('benchmark', '--json')
    assert result.returncode != 0
    assert result.stdout == ''
    assert 'Benchmark failed' in result.stderr
    assert calls == []

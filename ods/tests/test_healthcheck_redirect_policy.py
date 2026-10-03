"""The CLI can inspect the addressed HTTP endpoint without following redirects."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/healthcheck.py'


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('method', ['HEAD', 'GET'])
def test_cli_preserves_default_following_and_can_assert_first_hop(status, method):
    visited = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            self.send_response(status if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', '/target')
            self.end_headers()
            if self.command == 'GET':
                self.wfile.write(b'redirect endpoint' if self.path == '/redirect' else b'target body')

        do_HEAD = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/redirect'

    def check(*flags):
        visited.clear()
        result = subprocess.run([sys.executable, str(SCRIPT), url, '--method', method, '--retries', '0', '--json', *flags], capture_output=True, text=True, timeout=10)
        return result, json.loads(result.stdout)

    try:
        result, payload = check()
        assert result.returncode == 0
        assert payload['status'] == 200
        assert visited == ['/redirect', '/target']

        result, payload = check('--no-redirects')
        assert result.returncode == 1
        assert payload['ok'] is False
        assert payload['status'] == status
        assert visited == ['/redirect']

        result, payload = check('--no-redirects', '--expect-status', '3xx', '--expect-body-regex', '^redirect endpoint$')
        assert result.returncode == 0
        assert payload['status'] == status
        assert visited == ['/redirect']

        result, payload = check('--no-redirects', '--expect-status', '3xx', '--expect-body-regex', 'target body')
        assert result.returncode == 1
        assert 'did not match' in payload['detail']
        assert visited == ['/redirect']

        result, payload = check('--no-redirects', '--expect-status', '3xx')
        assert result.returncode == 0
        assert payload['status'] == status
        assert visited == ['/redirect']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tcp_rejects_http_redirect_option_before_connecting():
    result = subprocess.run([sys.executable, str(SCRIPT), 'tcp://127.0.0.1:1', '--no-redirects', '--json'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'HTTP' in json.loads(result.stdout)['detail']

"""The standalone battery runner (``python3 -m model_profile``) used by lab runs and CI."""

from __future__ import annotations

import http.server
import io
import json
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_profile import __main__ as runner  # noqa: E402

PROPS = {"build_info": "b9014-d4b0c22f9", "chat_template": "{% if enable_thinking %}{% endif %}",
         "chat_template_caps": {"supports_tool_calls": True}, "modalities": {"vision": False},
         "default_generation_settings": {"n_ctx": 8192}}


class _Server(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str | None]] = []

    def log_message(self, *_args):
        pass

    def _send(self, status: int, body: dict | str, headers: dict | None = None):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/props":
            self._send(200, PROPS)
        elif self.path == "/redirect":
            self._send(302, "", {"Location": "http://example.invalid/"})
        elif self.path in ("/stream", "/huge"):
            # A streamed answer is one JSON event per token.
            self._send(200, "x" * (400000 if self.path == "/stream" else runner.RESPONSE_LIMIT + 1))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.seen.append(("POST", self.path, self.headers.get("Authorization")))
        if payload.get("tools"):
            self._send(400, {"error": {"message": "Unable to generate parser for this template"}})
            return
        message = {"role": "assistant", "content": "READY"}
        self._send(200, {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                         "timings": {"predicted_per_second": 42.0}})


@pytest.fixture
def server():
    _Server.seen = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_runner_prints_the_battery_record_and_sends_the_key_as_a_header(server, monkeypatch):
    monkeypatch.setenv("FIXTURE_LLAMA_KEY", "fixture-key")
    output = io.StringIO()
    with redirect_stdout(output):
        assert runner.main(["--url", server, "--api-key-env", "FIXTURE_LLAMA_KEY", "--budget", "60"]) == 0
    result = json.loads(output.getvalue())
    assert result["facts"]["buildInfo"] == "b9014-d4b0c22f9"
    assert result["facts"]["thinkingControl"] == "enable_thinking"
    assert result["probes"]["P1"]["status"] == "pass"
    # The 400 body reaches the classifier: a template the server cannot parse fails tools.
    assert (result["probes"]["P2"]["status"], result["probes"]["P2"]["failureClass"]) == ("fail", "parser-error")
    assert result["summary"]["tools"] is False
    assert {auth for _method, _path, auth in _Server.seen} == {"Bearer fixture-key"}


def test_runner_refuses_redirects_and_a_missing_key(server, monkeypatch):
    exchange = runner.http_exchange(server)
    with pytest.raises(OSError, match="redirected"):
        exchange("/redirect", None, 5)
    assert exchange("/missing", None, 5)[0] == 404
    monkeypatch.delenv("FIXTURE_LLAMA_KEY", raising=False)
    with pytest.raises(SystemExit):
        runner.main(["--url", server, "--api-key-env", "FIXTURE_LLAMA_KEY"])


def test_runner_takes_a_streamed_answer_and_bounds_a_larger_one(server):
    exchange = runner.http_exchange(server)
    assert len(exchange("/stream", None, 5)[1]) == 400000
    with pytest.raises(OSError, match="exceeds"):
        exchange("/huge", None, 5)

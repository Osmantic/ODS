"""Run the repair entrypoint against a local config API with controlled Python."""

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("override", [False, True])
def test_repair_uses_the_shared_python_choice(tmp_path, override):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = json.dumps({"values": {"modelProviders": [
                {"id": "chat", "type": "openai"},
                {"id": "embedding", "type": "transformers"},
            ]}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "selected"
    wrapper = ("#!/bin/sh\nprintf '%s\\n' selected >> " + shlex.quote(str(marker)) +
               "\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    for name, body in [("python", "#!/bin/sh\nexit 91\n"), ("python3", wrapper),
                       ("configured python", wrapper)]:
        target = commands / name
        target.write_text(body)
        target.chmod(0o755)
    environment = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                       ODS_PYTHON_PREFER_SYSTEM="0", ODS_MODEL_SWITCHBOARD="enabled")
    environment.pop("ODS_PYTHON_CMD", None)
    if override:
        environment["ODS_PYTHON_CMD"] = str(commands / "configured python")
        (commands / "python3").write_text("#!/bin/sh\nexit 92\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        script = Path(__file__).resolve().parents[1] / "scripts/repair/repair-perplexica.sh"
        result = subprocess.run(
            ["bash", str(script), f"http://127.0.0.1:{server.server_port}"],
            env=environment, capture_output=True, text=True, timeout=10, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
        assert marker.exists()
        assert len(posts) == 3
        assert posts[0][1]["key"] == "modelProviders"
        assert posts[1][1]["value"]["defaultChatModel"] == "ods/current"
        assert posts[2][0] == "/api/config/setup-complete"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def _posted_base_url(posts):
    providers = posts[0][1]["value"]
    openai_prov = next(p for p in providers if p["type"] == "openai")
    return openai_prov["config"]["baseURL"]


@pytest.mark.parametrize("raw,expected", [
    ("http://llama-server:8080/v1/", "http://llama-server:8080/v1"),
    ("http://llama-server:8080/v1//", "http://llama-server:8080/v1"),
    ("http://llama-server:8080/api/v1/", "http://llama-server:8080/api/v1"),
    ("http://llama-server:8080/", "http://llama-server:8080/v1"),
    ("http://llama-server:8080", "http://llama-server:8080/v1"),
    ("http://llama-server:8080/v1", "http://llama-server:8080/v1"),
])
def test_repair_normalizes_trailing_slash_in_base_url(tmp_path, raw, expected):
    """A base URL ending in '/v1/' must not be rewritten to '…/v1/v1'."""
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = json.dumps({"values": {"modelProviders": [
                {"id": "chat", "type": "openai"},
                {"id": "embedding", "type": "transformers"},
            ]}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    environment = dict(os.environ,
                       ODS_MODEL_SWITCHBOARD="disabled",
                       LLM_API_URL=raw,
                       PERPLEXICA_MODEL="test-model",
                       PERPLEXICA_API_KEY="test-key")
    environment.pop("PERPLEXICA_LLM_BASE_URL", None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        script = Path(__file__).resolve().parents[1] / "scripts/repair/repair-perplexica.sh"
        result = subprocess.run(
            ["bash", str(script), f"http://127.0.0.1:{server.server_port}"],
            env=environment, capture_output=True, text=True, timeout=10, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
        assert len(posts) == 3
        assert _posted_base_url(posts) == expected
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_bootstrap_upgrade_normalizes_trailing_slash_in_base_url():
    """scripts/bootstrap-upgrade.sh replicates the same normalization; a base
    URL ending in '/v1/' must not become '…/v1/v1' there either."""
    script = Path(__file__).resolve().parents[1] / "scripts/bootstrap-upgrade.sh"
    text = script.read_text(encoding="utf-8")
    case_at = text.index('case "$_px_base_url" in')
    window = text[max(0, case_at - 600):case_at]
    assert '*/v1|*/api/v1' in text[case_at:case_at + 200]
    # The rstrip must run unconditionally before the case, not only inside
    # the appending arm — otherwise '…/v1/' still misses the */v1 match.
    assert '[[ "$_px_base_url" == ?*/ ]]' in window
    assert '"${_px_base_url%/}"' in window

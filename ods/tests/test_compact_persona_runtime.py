"""Probe the configured runtime through the public persona-builder command."""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-installation-context.py"


@pytest.mark.parametrize("port_key", ["LLM_PORT", "LLAMACPP_PORT"])
@pytest.mark.parametrize("write_file", [False, True])
def test_compact_profile_uses_one_configured_runtime_snapshot(tmp_path, port_key, write_file):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = json.dumps({"all_models_loaded": [{"model_name": "Live-Lemonade-Model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        env_file = tmp_path / ".env"
        env_file.write_text(f"{port_key}={server.server_port}\nLLM_MODEL=Stale-Env-Model\nGPU_BACKEND=amd\n")
        template = tmp_path / "SOUL.md.template"
        template.write_text("<!-- INSTALLATION_CONTEXT -->\n")
        output = tmp_path / "SOUL.md"
        calls = tmp_path / "docker-calls"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text('#!/bin/sh\nprintf "called\\n" >> "$PERSONA_DOCKER_CALLS"\n')
        docker.chmod(0o755)
        command = ["python3", str(SCRIPT), "--env", str(env_file), "--profile", "local-lemonade"]
        command += ["--template", str(template), "--output", str(output)] if write_file else ["--check"]
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=20,
                                env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                     "PERSONA_DOCKER_CALLS": str(calls)})
        assert result.returncode == 0, result.stderr
        text = output.read_text() if write_file else result.stdout
        assert "Local model: `Live-Lemonade-Model`" in text
        assert "Stale-Env-Model" not in text
        assert requests == ["/api/v1/health"]
        assert calls.read_text().splitlines() == ["called"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

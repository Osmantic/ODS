#!/usr/bin/env python3
"""Public CLI coverage for authenticated universal health probes."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "healthcheck.py"


class _AuthenticatedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            return
        if self.headers.get("X-ODS-Probe") != "integration":
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_repeatable_headers_reach_authenticated_endpoint() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthenticatedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run(
            f"http://127.0.0.1:{server.server_port}/health",
            "--method",
            "GET",
            "--header",
            "Authorization: Bearer test-token",
            "--header",
            "X-ODS-Probe:integration",
            "--expect-status",
            "204",
            "--retries",
            "0",
            "--json",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == 204


def test_header_rejects_newline_injection() -> None:
    result = _run("http://localhost:1", "--header", "X-Test:ok\r\nInjected: yes", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "newlines" in payload["detail"]


def test_header_rejects_tcp_targets() -> None:
    result = _run("localhost:1", "--header", "Authorization:test", "--json")

    assert result.returncode == 2
    assert "requires an HTTP target" in json.loads(result.stdout)["detail"]

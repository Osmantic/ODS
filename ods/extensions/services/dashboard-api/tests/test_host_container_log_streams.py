"""The host log endpoints must preserve both real child-process output pipes."""

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from test_host_agent import _mod


@pytest.fixture
def log_api(tmp_path, monkeypatch):
    extensions = tmp_path / "extensions"
    service = extensions / "log-demo"
    service.mkdir(parents=True)
    (service / "manifest.yaml").write_text("service:\n  id: log-demo\n  container_name: ods-log-demo\n")
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", extensions)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", tmp_path / "user-extensions")
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "log-wire-test")
    monkeypatch.setattr(_mod, "_resolve_container_name", lambda sid: f"ods-{sid}")
    server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def log_process(monkeypatch, emissions, returncode=0):
    run = _mod.subprocess.run
    commands = []
    program = (
        "import json, sys\n"
        "for stream, value in json.load(sys.stdin):\n"
        "    pipe = getattr(sys, stream)\n"
        "    pipe.write(value)\n"
        "    pipe.flush()\n"
        "sys.exit(int(sys.argv[1]))\n"
    )

    def child(command, **kwargs):
        assert command == ["docker", "logs", "--tail", "100", "ods-log-demo"]
        commands.append(command)
        return run([sys.executable, "-c", program, str(returncode)], input=json.dumps(emissions), **kwargs)

    monkeypatch.setattr(_mod.subprocess, "run", child)
    return commands


def read_logs(url, endpoint):
    request = urllib.request.Request(
        f"{url}/v1/{endpoint}/logs",
        data=json.dumps({"service_id": "log-demo"}).encode(),
        headers={"Authorization": "Bearer log-wire-test", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


@pytest.mark.parametrize("endpoint", ["extension", "service"])
def test_logs_include_interleaved_stdout_and_stderr(log_api, monkeypatch, endpoint):
    calls = log_process(monkeypatch, [
        ("stdout", "starting model\n"), ("stderr", "model load failed\n"),
        ("stdout", "shutdown complete\n"),
    ])
    payload = read_logs(log_api, endpoint)
    assert payload["logs"] == "starting model\nmodel load failed\nshutdown complete\n"
    assert payload["service_id"] == "log-demo"
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint", ["extension", "service"])
def test_log_tail_keeps_latest_diagnostics_from_both_streams(log_api, monkeypatch, endpoint):
    log_process(monkeypatch, [("stdout", "x" * 50000), ("stderr", "last error\n")])
    payload = read_logs(log_api, endpoint)
    assert len(payload["logs"]) == 50000
    assert payload["logs"].endswith("last error\n")


@pytest.mark.parametrize("endpoint", ["extension", "service"])
def test_missing_container_still_returns_empty_startup_receipt(log_api, monkeypatch, endpoint):
    log_process(monkeypatch, [("stderr", "Error response from daemon: No such container: ods-log-demo\n")], 1)
    payload = read_logs(log_api, endpoint)
    assert payload["lines"] == 0
    assert "No such container" not in payload["logs"]


def test_service_log_failure_preserves_docker_diagnostic(log_api, monkeypatch):
    log_process(monkeypatch, [("stderr", "Cannot connect to the Docker daemon")], 1)
    with pytest.raises(urllib.error.HTTPError) as failure:
        read_logs(log_api, "service")
    assert failure.value.code == 500
    assert "Cannot connect to the Docker daemon" in json.load(failure.value)["error"]

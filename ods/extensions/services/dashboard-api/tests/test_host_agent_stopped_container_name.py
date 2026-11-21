"""Logs and restart find a stopped container whose name is not ods-<id>.

Open WebUI's container is ``ods-webui``, not ``ods-open-webui``. The host
agent finds it through its Compose service label. ``docker ps`` without
``-a`` lists only running containers, so once Open WebUI exited the lookup
came back empty and fell back to ``ods-open-webui``: the logs view said
"Container is not running." instead of showing why it crashed, and Restart
answered "no such container".
"""
import json
import subprocess

from test_host_agent import _FakeHandler, _mod

# One stopped container: Compose service open-webui, named ods-webui.
_STOPPED = "ods-webui"


def _fake_docker(calls):
    def run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            listed = _STOPPED if "-a" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=listed + "\n", stderr="")
        if cmd[:2] == ["docker", "logs"]:
            # The agent spools docker logs into a file it passes as stdout.
            returncode, output = ((0, "Traceback: boom\n") if cmd[-1] == _STOPPED
                                  else (1, f"Error: No such container: {cmd[-1]}\n"))
            kwargs["stdout"].write(output.encode("utf-8"))
            return subprocess.CompletedProcess(cmd, returncode)
        raise AssertionError(f"unexpected command {cmd}")
    return run


def test_resolve_container_name_finds_a_stopped_container(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(_mod.subprocess, "run", _fake_docker(calls))

    assert _mod._resolve_container_name("open-webui") == "ods-webui"


def test_service_logs_reads_a_stopped_open_webui(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(_mod.subprocess, "run", _fake_docker(calls))
    monkeypatch.setattr(_mod, "check_auth", lambda handler: True)
    handler = _FakeHandler(json.dumps({"service_id": "open-webui"}).encode())

    _mod.AgentHandler._handle_service_logs(handler)

    assert handler.response_code == 200
    body = handler.parse_response()
    assert body["container_name"] == "ods-webui"
    assert body["logs"].startswith("Traceback: boom")

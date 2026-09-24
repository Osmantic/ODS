import importlib.util
import io
from pathlib import Path

import pytest


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


@pytest.fixture()
def server_module(monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    spec = importlib.util.spec_from_file_location(
        "open_interpreter_server", SERVER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    def __init__(self, lines):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(lines))
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_stream_disconnect_terminates_child_and_removes_script(
    server_module, monkeypatch
):
    process = FakeProcess(["SSE: first\n", "SSE: second\n"])
    removed = []
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(server_module.os, "unlink", removed.append)

    stream = server_module._stream_interpreter("runner.py", "{}")
    assert next(stream) == "data: first\n\n"
    stream.close()

    assert process.terminated is True
    assert process.killed is False
    assert removed == ["runner.py"]


def test_completed_stream_does_not_terminate_child(server_module, monkeypatch):
    process = FakeProcess(["SSE: only\n"])
    monkeypatch.setattr(server_module.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(server_module.os, "unlink", lambda _path: None)

    assert list(server_module._stream_interpreter("runner.py", "{}")) == [
        "data: only\n\n"
    ]

    assert process.returncode == 0
    assert process.terminated is False


def test_terminate_process_kills_a_child_that_ignores_termination(server_module):
    class StubbornProcess(FakeProcess):
        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise server_module.subprocess.TimeoutExpired("python", timeout)
            self.returncode = -9
            return self.returncode

    process = StubbornProcess([])

    server_module._terminate_process(process)

    assert process.terminated is True
    assert process.killed is True

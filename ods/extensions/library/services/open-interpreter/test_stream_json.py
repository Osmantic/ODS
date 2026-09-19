"""Exercise the authenticated stream and its real runner subprocess."""

import importlib.util
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_stream_emits_json_chunks_without_splitting_message_lines(tmp_path, monkeypatch):
    (tmp_path / "interpreter.py").write_text(
        "from types import SimpleNamespace\n"
        "class FakeInterpreter:\n"
        "    llm = SimpleNamespace()\n"
        "    def chat(self, message, stream=False):\n"
        "        assert stream is True\n"
        "        yield {'type': 'message', 'content': message, 'done': False, 'metadata': None}\n"
        "        yield {'type': 'message', 'content': 'finished', 'done': True}\n"
        "interpreter = FakeInterpreter()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("OPEN_INTERPRETER_API_KEY", "stream-json-test")
    spec = importlib.util.spec_from_file_location("interpreter_stream_server", Path(__file__).with_name("server.py"))
    server = importlib.util.module_from_spec(spec)
    # The deployment's fixed data mount is unused by chat; isolate its mkdir
    # so this HTTP test also runs as an unprivileged CI user.
    mkdir = Path.mkdir
    with monkeypatch.context() as context:
        context.setattr(Path, "mkdir", lambda path, *args, **kwargs: None if path == Path("/app/data") else mkdir(path, *args, **kwargs))
        spec.loader.exec_module(server)
    message = 'first line\nsecond "quoted" line — café'
    response = TestClient(server.app).post(
        "/chat/stream", headers={"Authorization": "Bearer stream-json-test"},
        json={"message": message},
    )
    assert response.status_code == 200
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames == [
        {"type": "message", "content": message, "done": False, "metadata": None},
        {"type": "message", "content": "finished", "done": True},
    ]

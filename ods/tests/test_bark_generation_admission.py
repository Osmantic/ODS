"""Exercise Bark admission through both real HTTP endpoints, without model weights."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import threading

from fastapi.responses import Response
from fastapi.testclient import TestClient
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "extensions/library/services/bark/server.py"
spec = importlib.util.spec_from_file_location("bark_admission_server", SOURCE)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def result_for(path):
    if path == "/tts":
        return {"audio_base64": "AA==", "sample_rate": 24000, "format": "wav"}
    return Response(b"RIFF-fixture", media_type="audio/wav")


@pytest.mark.parametrize("first_path", ["/tts", "/tts/stream"])
@pytest.mark.parametrize("second_path", ["/tts", "/tts/stream"])
def test_busy_synthesis_rejects_both_routes_without_entering_model_work(monkeypatch, first_path, second_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def synthesize(path, *args):
        calls.append(path)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5), "Test did not release the admitted request"
        return result_for(path)

    monkeypatch.setattr(server, "_generate_audio_sync", lambda *args: synthesize("/tts", *args))
    monkeypatch.setattr(server, "_generate_audio_stream_sync", lambda *args: synthesize("/tts/stream", *args))
    with TestClient(server.app) as client, ThreadPoolExecutor(max_workers=2) as requests:
        first = requests.submit(client.post, first_path, json={"text": "first"})
        try:
            assert entered.wait(3)
            second = requests.submit(client.post, second_path, json={"text": "second"}).result(timeout=2)
            assert second.status_code == 503
            assert "busy" in second.json()["detail"].lower()
            assert calls == [first_path]
            assert client.get("/health").status_code == 200
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200
        assert client.post(second_path, json={"text": "after completion"}).status_code == 200
        assert calls == [first_path, second_path]


@pytest.mark.parametrize("path, function", [("/tts", "_generate_audio_sync"), ("/tts/stream", "_generate_audio_stream_sync")])
@pytest.mark.parametrize("failure, status", [(ValueError("invalid synthesis"), 400), (RuntimeError("engine failure"), 500)])
def test_failed_synthesis_releases_admission_for_the_next_request(monkeypatch, path, function, failure, status):
    def fail(*args):
        raise failure

    monkeypatch.setattr(server, function, fail)
    with TestClient(server.app) as client:
        assert client.post(path, json={"text": "first"}).status_code == status
        monkeypatch.setattr(server, function, lambda *args: result_for(path))
        assert client.post(path, json={"text": "retry after failure"}).status_code == 200


@pytest.mark.parametrize("path, function", [("/tts", "_generate_audio_sync"), ("/tts/stream", "_generate_audio_stream_sync")])
def test_http_wait_failure_does_not_admit_work_while_engine_still_runs(monkeypatch, path, function):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    executor = server._executor

    def synthesize(*args):
        entered.set()
        assert release.wait(5)
        return result_for(path)

    class InterruptedWait:
        def __init__(self, future):
            self.future = future

        def add_done_callback(self, callback):
            def completed(future):
                callback(future)
                finished.set()
            self.future.add_done_callback(completed)

        def result(self, *args, **kwargs):
            assert entered.wait(3)
            raise TimeoutError("HTTP caller stopped waiting")

    class InterruptedExecutor:
        def submit(self, *args):
            return InterruptedWait(executor.submit(*args))

    monkeypatch.setattr(server, function, synthesize)
    monkeypatch.setattr(server, "_executor", InterruptedExecutor())
    with TestClient(server.app) as client:
        try:
            assert client.post(path, json={"text": "first"}).status_code in (500, 504)
            assert client.post(path, json={"text": "while engine still runs"}).status_code == 503
            assert client.get("/health").status_code == 200
        finally:
            release.set()
        assert finished.wait(3)
        monkeypatch.setattr(server, "_executor", executor)
        monkeypatch.setattr(server, function, lambda *args: result_for(path))
        assert client.post(path, json={"text": "after engine completion"}).status_code == 200


def test_executor_rejection_does_not_leave_service_permanently_busy(monkeypatch):
    executor = server._executor

    class StoppedExecutor:
        def submit(self, *args):
            raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(server, "_executor", StoppedExecutor())
    with TestClient(server.app, raise_server_exceptions=False) as client:
        assert client.post("/tts", json={"text": "first"}).status_code == 500
        monkeypatch.setattr(server, "_executor", executor)
        monkeypatch.setattr(server, "_generate_audio_sync", lambda *args: result_for("/tts"))
        assert client.post("/tts", json={"text": "after executor recovery"}).status_code == 200

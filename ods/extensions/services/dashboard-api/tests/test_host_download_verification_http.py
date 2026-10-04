"""Existing-artifact verification remains an acknowledged, cancellable operation."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from test_host_agent import _mod as host


@pytest.mark.parametrize("outcome", ["cancel", "hash-failure"])
def test_existing_artifact_verification_can_be_cancelled_and_retried(tmp_path, monkeypatch, test_client, outcome):
    from routers import models as api
    import host_agent_client
    models = tmp_path / "data" / "models"
    models.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    payload = b"GGUF" + b"model bytes" * 100
    target = models / "existing.gguf"
    target.write_bytes(payload)
    record = {"id": "existing", "gguf_file": target.name, "gguf_url": "https://example.com/model.gguf",
              "gguf_sha256": hashlib.sha256(payload).hexdigest()}
    (tmp_path / "config" / "model-library.json").write_text(json.dumps({"models": [record]}))
    monkeypatch.setattr(host, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(host, "AGENT_API_KEY", "owner-key")
    monkeypatch.setattr(host, "_model_download_directory", lambda: models)
    monkeypatch.setattr(host, "_model_download_thread", None)
    monkeypatch.setattr(host, "_model_download_cancelable", False)
    monkeypatch.setattr(host, "_model_download_cancel", Event())
    monkeypatch.setattr(host, "_model_artifact_verification_cache", {})
    monkeypatch.setattr(host.subprocess, "Popen", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("Existing valid artifacts must never be downloaded")))
    monkeypatch.setattr(api, "_LIBRARY_PATH", tmp_path / "config/model-library.json")
    monkeypatch.setattr(api, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(host, "_verify_switchboard_route_for_status", lambda *_args: None)
    monkeypatch.setattr(host, "_project_switchboard_agent_viability", lambda *_args: None)
    entered, release = Event(), Event()
    real_sha256 = hashlib.sha256

    class SlowDigest:
        def __init__(self, *args, **kwargs):
            self.digest = real_sha256(*args, **kwargs)

        def update(self, chunk):
            entered.set()
            assert release.wait(5), "Test must release verification"
            if outcome == "hash-failure":
                raise RuntimeError("Hash provider failed during verification")
            self.digest.update(chunk)

        def hexdigest(self):
            return self.digest.hexdigest()

    monkeypatch.setattr(host.hashlib, "sha256", SlowDigest)
    server = ThreadingHTTPServer(("127.0.0.1", 0), host.AgentHandler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    headers = {"Authorization": "Bearer owner-key"}
    agent_client = httpx.Client(base_url=url, headers=headers, trust_env=False)
    monkeypatch.setattr(host_agent_client, "_sync_client", agent_client)
    api._invalidate_agent_model_status_cache()

    def poll():
        api._invalidate_agent_model_status_cache()
        response = test_client.get("/api/models/download-status", headers=test_client.auth_headers)
        assert response.status_code == 200
        return response.json()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(test_client.post, "/api/models/existing/download",
                                     headers=test_client.auth_headers)
            try:
                assert entered.wait(3), "Actual SHA256 must start"
                response = future.result(timeout=0.5)
                assert response.status_code == 200
                assert response.json()["status"] == "started"
                status = poll()
                assert status["status"] == "verifying"
                refused = test_client.post("/api/models/existing/download", headers=test_client.auth_headers)
                assert refused.status_code == 409
                if outcome == "cancel":
                    cancel = test_client.post("/api/models/download/cancel", headers=test_client.auth_headers)
                    assert cancel.json()["status"] == "cancelling"
            finally:
                release.set()
        host._model_download_thread.join(timeout=3)
        assert not host._model_download_thread.is_alive()
        assert target.read_bytes() == payload
        terminal = poll()
        if outcome == "cancel":
            assert terminal["lastTerminalStatus"]["status"] == "cancelled"
        else:
            assert terminal["status"] == "failed"
            assert "Hash provider failed" in terminal["error"]
        monkeypatch.setattr(host.hashlib, "sha256", real_sha256)
        retry = test_client.post("/api/models/existing/download", headers=test_client.auth_headers)
        assert retry.status_code == 200
        host._model_download_thread.join(timeout=3)
        assert not host._model_download_thread.is_alive()
        assert poll()["status"] == "complete"
        assert target.read_bytes() == payload
        assert httpx.post(url + "/v1/model/download/cancel", json={}, headers=headers).json()["status"] == "no_download"
    finally:
        release.set()
        if host._model_download_thread is not None:
            host._model_download_thread.join(timeout=3)
        if host._model_lifecycle_operation == "model_download":
            host._end_model_lifecycle("model_download")
        agent_client.close()
        api._invalidate_agent_model_status_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

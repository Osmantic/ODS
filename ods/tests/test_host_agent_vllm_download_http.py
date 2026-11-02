"""Real HTTP tests for the host agent's vLLM catalog download path.

A real ThreadingHTTPServer runs AgentHandler with AGENT_API_KEY auth; a real
FakeHub (reused from test_hf_manifest_download) serves the pinned snapshot.
Only external Docker/switchboard effects are stubbed. The actual
scripts/download-hf-manifest.py helper is loaded by spec and its
DEFAULT_BASE_URL is pointed at the FakeHub; fetch_manifest is never mocked.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_hf_manifest_download as base  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ods_host_agent_vllm_download_http_test", ROOT / "bin" / "ods-host-agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent_vllm_download_http_test"] = agent
_spec.loader.exec_module(agent)

OWNER_KEY = "owner-key-for-tests"
MODEL_ID = "test-model-nvfp4-vllm"


@pytest.fixture(autouse=True)
def roomy_volume(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: base.Usage(10 * 1000 ** 4, 0, 9 * 1000 ** 4))


@pytest.fixture
def hub():
    server = base.FakeHub(base.FILES)
    yield server
    server.close()


@pytest.fixture
def install(tmp_path, monkeypatch, hub):
    """A real install dir with a valid catalog entry and a real helper."""
    (tmp_path / "config").mkdir()
    base.write_catalog(tmp_path / "config" / "model-library.json", base.FILES)
    (tmp_path / "data").mkdir()
    (tmp_path / ".env").write_text(
        "ODS_MODE=local\nLLM_BACKEND=llama-server\nGGUF_FILE=tiny.gguf\n",
        encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "STARTUP_ODS_MODE", "local")
    monkeypatch.setattr(agent, "AGENT_API_KEY", OWNER_KEY)
    monkeypatch.setattr(agent, "_managed_wsl_runtime", lambda env: {"managed": False})
    monkeypatch.setattr(agent, "_verify_switchboard_route_for_status", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_project_switchboard_agent_viability", lambda *a, **k: None)

    # Load the actual helper by spec and point it at the FakeHub.
    helper = base._helper()
    helper.DEFAULT_BASE_URL = hub.url
    monkeypatch.setattr(agent, "_load_vllm_downloader", lambda: helper)

    # Reset process-wide lifecycle state so tests are isolated.
    with agent._model_lifecycle_state_lock:
        agent._model_lifecycle_operation = None
        agent._model_lifecycle_target = None
        agent._model_lifecycle_revision = 0
        agent._model_lifecycle_last_operation = None
        agent._model_runtime_revision = 0
        agent._model_activation_result = None
        assert not agent._model_lifecycle_lock.locked(), "previous lifecycle leaked"
    with agent._model_download_lock:
        agent._model_download_thread = None
        agent._model_download_proc = None
        agent._model_download_cancelable = False
        agent._model_download_cancel.clear()
    yield tmp_path
    # Assert the worker finished and released the lifecycle lock.
    thread = agent._model_download_thread
    if thread is not None:
        thread.join(timeout=10)
        assert not thread.is_alive(), "download worker leaked"
    assert not agent._model_lifecycle_lock.locked(), "lifecycle lock leaked"
    with agent._model_lifecycle_state_lock:
        assert agent._model_lifecycle_operation is None


@pytest.fixture
def server(install):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _request(url, path, *, method="GET", body=None, key=OWNER_KEY):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _status(url):
    return _request(url, "/v1/model/status")


def _wait_terminal(url, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, data = _status(url)
        assert code == 200, data
        if data.get("status") in {"complete", "failed", "cancelled"}:
            return data
        time.sleep(0.05)
    raise AssertionError(f"status never reached a terminal state: {data}")


def _model_dir(install):
    return install / "data" / "models" / base.MODEL_DIR


def _files(install):
    root = _model_dir(install)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_auth_refusal_writes_nothing(install, server, hub):
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID}, key="wrong-key")
    assert code == 403
    assert hub.requests == []
    assert not (install / "data" / "model-download-status.json").exists()
    assert not _model_dir(install).exists()


def test_success_downloads_actual_bytes(install, server, hub):
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    assert data["status"] == "started"
    final = _wait_terminal(server)
    assert final["status"] == "complete", final
    assert final["model"] == MODEL_ID
    assert final["runtime"] == "vllm"
    assert final["bytesDownloaded"] == final["bytesTotal"] == sum(map(len, base.FILES.values()))
    assert _files(install) == base.FILES
    assert hub.requests, "the real helper must have fetched from the FakeHub"


def test_active_snapshot_is_refused(install, server, hub):
    (install / ".env").write_text(
        "ODS_MODE=local\nLLM_BACKEND=vllm\nLLM_MODEL=test-model-nvfp4\n"
        f"VLLM_MODEL_DIR={base.MODEL_DIR}\n", encoding="utf-8")
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 409
    assert data["code"] == "vllm_active_snapshot"
    assert hub.requests == []
    assert not _model_dir(install).exists()


def test_active_by_llm_model_name_is_refused(install, server, hub):
    (install / ".env").write_text(
        "ODS_MODE=local\nLLM_BACKEND=vllm\nLLM_MODEL=test-model-nvfp4\n",
        encoding="utf-8")
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 409
    assert data["code"] == "vllm_active_snapshot"
    assert hub.requests == []


def test_payload_override_is_refused(install, server, hub):
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID, "gguf_url": "http://evil/"})
    assert code == 400
    assert hub.requests == []
    assert not _model_dir(install).exists()


def test_lock_busy_is_refused(install, server, hub):
    assert agent._model_lifecycle_lock.acquire(blocking=False)
    try:
        code, data = _request(server, "/v1/model/download", method="POST",
                              body={"model_id": MODEL_ID})
        assert code == 409
        assert data["code"] == "model_lifecycle_busy"
        assert hub.requests == []
    finally:
        agent._model_lifecycle_lock.release()


def test_worker_launch_failure_releases_lifecycle(install, server, hub, monkeypatch):
    real_thread = threading.Thread

    def failing_thread(*args, **kwargs):
        target = kwargs.get("target")
        if target is not None and getattr(target, "__name__", "") == "_download":
            raise RuntimeError("cannot start worker")
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(agent.threading, "Thread", failing_thread)
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 500, data
    assert not agent._model_lifecycle_lock.locked()
    with agent._model_lifecycle_state_lock:
        assert agent._model_lifecycle_operation is None
    status = json.loads((install / "data" / "model-download-status.json").read_text())
    assert status["status"] == "failed"
    assert "Could not start the download worker" in status["error"]


def test_lock_held_while_fetch_gated(install, server, hub, monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    helper = agent._load_vllm_downloader()
    real_fetch = helper.fetch_manifest

    def gated_fetch(*args, **kwargs):
        entered.set()
        assert gate.wait(timeout=10)
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(helper, "fetch_manifest", gated_fetch)
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    assert entered.wait(timeout=10)
    # While the worker is inside fetch_manifest, the lifecycle lock is held.
    assert agent._model_lifecycle_lock.locked()
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 409
    assert data["code"] == "model_lifecycle_busy"
    code, data = _request(server, "/v1/model/delete", method="POST", body={"model_id": MODEL_ID})
    assert code == 409 and data["code"] == "model_lifecycle_busy"
    gate.set()
    final = _wait_terminal(server)
    assert final["status"] == "complete"
    assert _files(install) == base.FILES


def test_cancel_mid_download_then_resume(install, server, hub, monkeypatch):
    helper = agent._load_vllm_downloader()
    real_fetch = helper.fetch_manifest
    entered, release = threading.Event(), threading.Event()

    def gated_fetch(model, models_dir, base_url, *, cancelled=None, on_progress=None):
        def wrapped_progress(done, total):
            if on_progress is not None:
                on_progress(done, total)
            if done > 0:
                entered.set()
                assert release.wait(5), "test must release progress"

        return real_fetch(model, models_dir, base_url,
                          cancelled=cancelled, on_progress=wrapped_progress)

    monkeypatch.setattr(helper, "fetch_manifest", gated_fetch)
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    try:
        assert entered.wait(5), "download must report actual partial bytes"
        code, cancel = _request(server, "/v1/model/download/cancel", method="POST", body={})
        assert code == 200 and cancel["status"] == "cancelling"
    finally:
        release.set()
    final = _wait_terminal(server)
    assert final["status"] == "cancelled", final
    agent._model_download_thread.join(5)
    assert not agent._model_download_thread.is_alive()
    root = _model_dir(install)
    partials = list(root.rglob("*.partial"))
    assert partials, "cancellation must leave partial bytes for a resume"
    assert all(p.stat().st_size > 0 for p in partials)
    assert not (root / "config.json").exists()

    # Resume with the real helper: the partial bytes are reused.
    monkeypatch.setattr(helper, "fetch_manifest", real_fetch)
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    final = _wait_terminal(server)
    assert final["status"] == "complete", final
    assert _files(install) == base.FILES


def test_checksum_failure_leaves_no_final_file(install, server, hub):
    hub.files["config.json"] = b"x" * len(base.FILES["config.json"])
    assert len(hub.files["config.json"]) == len(base.FILES["config.json"])
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    final = _wait_terminal(server)
    assert final["status"] == "failed", final
    assert "config.json" in final["error"]
    root = _model_dir(install)
    assert not (root / "config.json").exists()
    assert not (root / "config.json.partial").exists()


def test_dead_downloading_status_is_normalized_to_failure(install, server, hub):
    status_path = install / "data" / "model-download-status.json"
    status_path.write_text(json.dumps({
        "status": "downloading", "model": MODEL_ID,
        "bytesDownloaded": 1234, "bytesTotal": 5678,
        "runtime": "vllm", "updatedAt": "2024-01-01T00:00:00Z",
    }), encoding="utf-8")
    code, data = _status(server)
    assert code == 200
    assert data["status"] == "failed"
    assert "resume" in data["error"].lower()
    assert data["bytesDownloaded"] == 1234
    assert data["bytesTotal"] == 5678
    assert data["runtime"] == "vllm"


def test_normalizer_does_not_clobber_newer_complete_status(install, server, hub):
    status_path = install / "data" / "model-download-status.json"
    status_path.write_text(json.dumps({
        "status": "complete", "model": MODEL_ID,
        "bytesDownloaded": 0, "bytesTotal": 0,
        "runtime": "vllm", "updatedAt": "2024-01-01T00:00:00Z",
    }), encoding="utf-8")
    code, data = _status(server)
    assert code == 200
    assert data["status"] == "complete"
    assert data["model"] == MODEL_ID


def test_status_idle_when_no_status_file(install, server):
    code, data = _status(server)
    assert code == 200
    assert data["status"] == "idle"


# --- vLLM snapshot deletion (POST /v1/model/delete) -------------------------



def _delete(url, body):
    return _request(url, "/v1/model/delete", method="POST", body=body)


def _install_snapshot_helper(install, monkeypatch):
    """Copy the real snapshot helper into INSTALL_DIR/scripts and load it."""
    scripts = install / "scripts"
    scripts.mkdir(exist_ok=True)
    src = ROOT / "scripts" / "model_snapshot_files.py"
    dst = scripts / "model_snapshot_files.py"
    shutil.copyfile(src, dst)



def _download_complete(server):
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    final = _wait_terminal(server)
    assert final["status"] == "complete", final
    thread = agent._model_download_thread
    if thread is not None:
        thread.join(timeout=10)
        assert not thread.is_alive()
    return final


def _posix_delete_supported():
    return (os.name == "posix"
            and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.stat, os.unlink))
            and os.rmdir in os.supports_dir_fd
            and os.listdir in os.supports_fd
            and bool(getattr(os, "O_NOFOLLOW", 0)))


@pytest.mark.skipif(not _posix_delete_supported(),
                    reason="snapshot deletion requires POSIX directory descriptors")
def test_delete_removes_downloaded_snapshot(install, server, hub, monkeypatch):
    _install_snapshot_helper(install, monkeypatch)
    final = _download_complete(server)
    total = final["bytesTotal"]
    assert _files(install) == base.FILES
    code, data = _delete(server, {"model_id": MODEL_ID})
    assert code == 200, data
    assert data["status"] == "deleted"
    assert data["model_id"] == MODEL_ID
    assert data["bytesRemoved"] == total
    assert not _model_dir(install).exists()


@pytest.mark.skipif(not _posix_delete_supported(),
                    reason="snapshot deletion requires POSIX directory descriptors")
def test_delete_removes_partials_after_cancel(install, server, hub, monkeypatch):
    helper = agent._load_vllm_downloader()
    real_fetch = helper.fetch_manifest
    entered, release = threading.Event(), threading.Event()

    def gated_fetch(model, models_dir, base_url, *, cancelled=None, on_progress=None):
        def wrapped_progress(done, total):
            if on_progress is not None:
                on_progress(done, total)
            if done > 0:
                entered.set()
                assert release.wait(5)
        return real_fetch(model, models_dir, base_url,
                          cancelled=cancelled, on_progress=wrapped_progress)

    monkeypatch.setattr(helper, "fetch_manifest", gated_fetch)
    code, data = _request(server, "/v1/model/download", method="POST",
                          body={"model_id": MODEL_ID})
    assert code == 200, data
    try:
        assert entered.wait(5)
        code, cancel = _request(server, "/v1/model/download/cancel", method="POST", body={})
        assert code == 200 and cancel["status"] == "cancelling"
    finally:
        release.set()
    final = _wait_terminal(server)
    assert final["status"] == "cancelled", final
    agent._model_download_thread.join(5)
    assert not agent._model_download_thread.is_alive()
    root = _model_dir(install)
    assert list(root.rglob("*.partial"))
    _install_snapshot_helper(install, monkeypatch)
    code, data = _delete(server, {"model_id": MODEL_ID})
    assert code == 200, data
    assert data["status"] == "deleted"
    assert not root.exists()


def test_delete_active_snapshot_refused_before_helper(install, server, hub, monkeypatch):
    (install / ".env").write_text(
        "ODS_MODE=local\nLLM_BACKEND=vllm\nLLM_MODEL=test-model-nvfp4\n"
        f"VLLM_MODEL_DIR={base.MODEL_DIR}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(agent, "_load_model_script",
                        lambda name: calls.append(name) or pytest.fail("helper loaded"))
    code, data = _delete(server, {"model_id": MODEL_ID})
    assert code == 409
    assert data["code"] == "vllm_active_snapshot"
    assert calls == []


def test_delete_unknown_file_aborts_and_preserves_all(install, server, hub, monkeypatch):
    if not _posix_delete_supported():
        pytest.skip("snapshot deletion requires POSIX directory descriptors")
    _install_snapshot_helper(install, monkeypatch)
    _download_complete(server)
    root = _model_dir(install)
    stray = root / "unexpected.bin"
    stray.write_bytes(b"stray")
    before = _files(install)
    code, data = _delete(server, {"model_id": MODEL_ID})
    assert code == 409, data
    assert data["code"] == "vllm_snapshot_delete_incomplete"
    assert _files(install) == before
    assert stray.exists()


def test_delete_malformed_payload_forbidden_field(install, server, hub, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_load_model_script",
                        lambda name: calls.append(name) or pytest.fail("helper loaded"))
    code, data = _delete(server, {"model_id": MODEL_ID, "path": "/etc"})
    assert code == 400
    assert calls == []
    code, data = _delete(server, {"model_id": ""})
    assert code == 400
    code, data = _delete(server, {})
    assert code == 400
    assert calls == []


def test_delete_missing_model_id_404(install, server, hub, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_load_model_script",
                        lambda name: calls.append(name) or pytest.fail("helper loaded"))
    code, data = _delete(server, {"model_id": "no-such-model"})
    assert code == 404
    assert calls == []


def test_delete_lifecycle_busy_409_no_helper(install, server, hub, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_load_model_script",
                        lambda name: calls.append(name) or pytest.fail("helper loaded"))
    assert agent._model_lifecycle_lock.acquire(blocking=False)
    try:
        code, data = _delete(server, {"model_id": MODEL_ID})
        assert code == 409
        assert data["code"] == "model_lifecycle_busy"
        assert calls == []
    finally:
        agent._model_lifecycle_lock.release()


@pytest.mark.skipif(os.name == "posix",
                    reason="POSIX deletion is supported; this covers non-POSIX refusal")
def test_delete_unsupported_platform_409_no_files_deleted(install, server, hub, monkeypatch):
    _install_snapshot_helper(install, monkeypatch)
    _download_complete(server)
    before = _files(install)
    code, data = _delete(server, {"model_id": MODEL_ID})
    assert code == 409, data
    assert data["code"] == "vllm_snapshot_delete_incomplete"
    assert _files(install) == before

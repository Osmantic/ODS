"""Authenticated update polling must preserve a completed worker receipt."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent_update_status_tests", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


@pytest.mark.parametrize("returncode", [0, 1])
def test_status_preserves_worker_completion_after_initial_read(tmp_path, monkeypatch, returncode):
    import urllib.request
    from http.server import HTTPServer

    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
    monkeypatch.setattr(_mod, "_update_thread", None)
    entered = threading.Event()
    finish = threading.Event()

    def run_update(action, *args, timeout):
        entered.set()
        assert finish.wait(5), "status request did not release the update"
        return subprocess.CompletedProcess(["ods-update", action], returncode, "worker receipt", "")

    monkeypatch.setattr(_mod, "_run_update_script", run_update)
    read_status = _mod._read_update_status
    server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {"Authorization": "Bearer wire-test-secret"}
    try:
        request = urllib.request.Request(origin + "/v1/update/start", data=b"{}", headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 202
        assert entered.wait(5)
        worker = _mod._update_thread
        snapshots = []

        def read_then_finish_worker():
            data = read_status()
            if not snapshots:
                snapshots.append(data)
                assert data["status"] == "running"
                finish.set()
                worker.join(timeout=5)
                assert not worker.is_alive()
            return data

        monkeypatch.setattr(_mod, "_read_update_status", read_then_finish_worker)
        request = urllib.request.Request(origin + "/v1/update/status", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            status = json.loads(response.read())
            assert response.status == 200
        expected = "succeeded" if returncode == 0 else "failed"
        assert status["status"] == expected
        assert status["returncode"] == returncode
        assert status["output_tail"] == "worker receipt"
        assert "error" not in status
        assert json.loads(_mod._update_status_path().read_text(encoding="utf-8")) == status
    finally:
        finish.set()
        if _mod._update_thread is not None:
            _mod._update_thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

"""Cancellation success requires an acknowledgement from the same worker."""
import json
import threading
import time

import pytest
from fastapi import HTTPException

import routers.models as router

GEN = "a" * 32


def setup(monkeypatch, tmp_path, state="downloading", generation=GEN):
    monkeypatch.setattr(router, "DATA_DIR", str(tmp_path))
    status = {"status": state, "model": "Full.gguf", "downloadGeneration": generation}
    path = tmp_path / "bootstrap-status.json"
    path.write_text(json.dumps(status))
    monkeypatch.setattr(router, "_call_agent_model", lambda *_: pytest.fail("Must not pretend the separate host-agent download was cancelled"))
    return path, status


@pytest.mark.parametrize("state", ["starting", "downloading"])
def test_acknowledgement_belongs_to_the_requested_generation(monkeypatch, tmp_path, state):
    path, status = setup(monkeypatch, tmp_path, state)

    def acknowledge():
        deadline = time.monotonic() + 5
        request = tmp_path / "bootstrap-download.cancel.json"
        while not request.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert json.loads(request.read_text()) == {"generation": GEN}
        temporary = tmp_path / "next-status"
        temporary.write_text(json.dumps({**status, "status": "cancelled"}))
        temporary.replace(path)

    worker = threading.Thread(target=acknowledge)
    worker.start()
    try:
        result = router.cancel_download("test")
        assert result == {"status": "cancelled", "cancelled": True, "downloadGeneration": GEN}
    finally:
        worker.join(timeout=5)


@pytest.mark.parametrize("state,generation,phrase", [
    ("downloading", None, "does not support"),
    ("downloading", "bad", "does not support"),
    ("verifying", GEN, "cannot be cancelled"),
    ("swapping", GEN, "cannot be cancelled"),
])
def test_unsupported_and_finalizing_workers_are_not_claimed_cancelled(monkeypatch, tmp_path, state, generation, phrase):
    setup(monkeypatch, tmp_path, state, generation)
    with pytest.raises(HTTPException) as error:
        router.cancel_download("test")
    assert error.value.status_code == 409 and phrase in error.value.detail
    assert not (tmp_path / "bootstrap-download.cancel.json").exists()


@pytest.mark.parametrize("next_status", [
    {"status": "cancelled", "downloadGeneration": "b" * 32},
    {"status": "verifying", "downloadGeneration": GEN},
])
def test_late_or_superseded_request_is_not_success(monkeypatch, tmp_path, next_status):
    _, initial = setup(monkeypatch, tmp_path)
    values = iter([initial, next_status])
    monkeypatch.setattr(router, "_read_bootstrap_status_file", lambda: next(values))
    with pytest.raises(HTTPException) as error:
        router.cancel_download("test")
    assert error.value.status_code == 409


def test_regular_download_cancel_still_uses_host_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "DATA_DIR", str(tmp_path))
    calls = []

    def cancel_agent(*args):
        calls.append(args)
        return {"cancelled": True}

    monkeypatch.setattr(router, "_call_agent_model", cancel_agent)
    assert router.cancel_download("test") == {"cancelled": True}
    assert calls == [("/v1/model/download/cancel", {})]


def test_cancelled_bootstrap_projects_terminal_status(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, "cancelled")
    monkeypatch.setattr(router, "_get_agent_model_status", lambda: {"status": "idle"})
    result = router.model_download_status("test")
    assert not result["isDownloading"]
    assert result["lastTerminalStatus"]["status"] == "cancelled"
    assert "partial file preserved" in result["lastTerminalStatus"]["error"]

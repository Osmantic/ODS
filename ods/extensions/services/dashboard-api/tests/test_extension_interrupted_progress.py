"""Interrupted installs read as recoverable failures, and Remove deletes containers.

A WSL/Docker crash during an Open WebUI add left the Extensions card showing
frozen install progress for about an hour, until the record aged out; removing
Excalidraw left its stopped container behind (RTX Spark laptop, 2026-10-25).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from routers import extensions as ext_mod
from test_extensions import (
    _make_catalog_ext, _make_service_status, _patch_extensions_config,
    _patch_mutation_config, _setup_user_ext,
)

# Records an hour old age out by design, so these are recent.
AGENT_NOW = datetime.now(timezone.utc)
BEFORE = (AGENT_NOW - timedelta(minutes=5)).isoformat()
AFTER = (AGENT_NOW + timedelta(seconds=1)).isoformat()
# The router's own host calls, taken before any fixture stands in for them.
REAL_FETCH_JOBS = ext_mod._fetch_extension_jobs
REAL_REMOVE_CONTAINERS = ext_mod._call_agent_remove_containers


def _jobs(monkeypatch, live=(), instance="agent-now", now=AGENT_NOW):
    """The host agent's answer to /v1/extension/jobs, or None for no answer."""
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return {"instance": instance, "now": now, "live": frozenset(live)}

    monkeypatch.setattr(ext_mod, "_fetch_extension_jobs", fetch)
    return calls


def _write(tmp_path, service_id, status, **extra):
    directory = tmp_path / "extension-progress"
    directory.mkdir(exist_ok=True)
    path = directory / f"{service_id}.json"
    record = {"service_id": service_id, "status": status, "phase_label": "Downloading images...",
              "error": None, "started_at": BEFORE, "updated_at": BEFORE, **extra}
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


@pytest.fixture
def progress_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ext_mod, "DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("phase", ["pulling", "starting", "setup_hook"])
def test_a_record_from_an_earlier_agent_process_reads_as_interrupted(progress_dir, monkeypatch, phase):
    _jobs(monkeypatch)
    _write(progress_dir, "open-webui", phase, agent_instance="agent-before")
    record = ext_mod._read_progress("open-webui")
    assert record["status"] == "error"
    assert record["interrupted"] is True
    assert record["interrupted_phase"] == phase
    assert record["error"] == ext_mod.INTERRUPTED_PROGRESS_ERROR
    # Nothing is rewritten: the record stays what the agent wrote.
    raw = json.loads((progress_dir / "extension-progress" / "open-webui.json").read_text())
    assert raw["status"] == phase


def test_a_worker_that_ended_without_a_final_record_reads_as_interrupted(progress_dir, monkeypatch):
    _jobs(monkeypatch, instance="agent-now")
    _write(progress_dir, "gotify", "pulling", agent_instance="agent-now")
    assert ext_mod._read_progress("gotify")["interrupted"] is True


def test_a_record_written_after_the_agent_answer_is_not_judged(progress_dir, monkeypatch):
    # The operation may have started after the agent read its live list.
    _jobs(monkeypatch, instance="agent-now")
    _write(progress_dir, "gotify", "pulling", agent_instance="agent-now", updated_at=AFTER)
    assert ext_mod._read_progress("gotify")["status"] == "pulling"


def test_a_running_operation_keeps_its_progress(progress_dir, monkeypatch):
    _jobs(monkeypatch, live={"gotify"}, instance="agent-now")
    _write(progress_dir, "gotify", "starting", agent_instance="agent-before")
    assert ext_mod._read_progress("gotify")["status"] == "starting"


def test_without_an_agent_answer_progress_stands_as_written(progress_dir):
    # conftest: no host agent answers in unit tests.
    _write(progress_dir, "gotify", "pulling", agent_instance="agent-before")
    assert ext_mod._read_progress("gotify")["status"] == "pulling"


def test_the_dashboard_first_record_gets_time_to_reach_the_agent(progress_dir, monkeypatch):
    calls = _jobs(monkeypatch)
    now = datetime.now(timezone.utc)
    _write(progress_dir, "gotify", "pulling", phase_label="Starting installation...",
           operation="install", updated_at=now.isoformat(), started_at=now.isoformat())
    assert ext_mod._read_progress("gotify")["status"] == "pulling"
    assert calls == []  # Not even asked.

    old = (now - timedelta(seconds=ext_mod._DASHBOARD_PROGRESS_GRACE_SECONDS + 30)).isoformat()
    _write(progress_dir, "gotify", "pulling", phase_label="Starting installation...",
           operation="install", updated_at=old, started_at=old)
    _jobs(monkeypatch, now=now)
    record = ext_mod._read_progress("gotify")
    assert record["interrupted"] is True
    assert ext_mod._interrupted_operation(record) == "install"


def test_no_record_is_judged_while_another_request_works_on_the_extension(progress_dir, monkeypatch):
    # The enable route's start took over two minutes; the host has answered
    # and the request is about to record "started".
    import threading

    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: progress_dir / ".lock")
    _jobs(monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _write(progress_dir, "gotify", "pulling", operation="start", updated_at=old, started_at=old)
    holding, release, seen_inside = threading.Event(), threading.Event(), []

    def request():
        with ext_mod._extension_operation_lock("gotify"):
            # The request itself still sees what it acts on.
            seen_inside.append(ext_mod._read_progress("gotify")["status"])
            holding.set()
            release.wait(10)

    worker = threading.Thread(target=request)
    worker.start()
    assert holding.wait(10)
    assert ext_mod._read_progress("gotify")["status"] == "pulling"
    release.set()
    worker.join(10)
    assert seen_inside == ["error"]
    assert ext_mod._read_progress("gotify")["interrupted"] is True


@pytest.mark.parametrize("record,operation", [
    ({"operation_id": "a" * 32}, "install"),
    ({"operation": "install"}, "install"),
    ({"operation": "start"}, "start"),
    ({}, None),
    ({"operation_id": "not-an-identity"}, None),
])
def test_retry_repeats_the_interrupted_operation(record, operation):
    assert ext_mod._interrupted_operation(record) == operation


@pytest.mark.parametrize("status", ["error", "started", "prepared", "idle"])
def test_final_records_are_never_interrupted(progress_dir, monkeypatch, status):
    _jobs(monkeypatch)
    _write(progress_dir, "gotify", status, agent_instance="agent-before", error="boom")
    record = ext_mod._read_progress("gotify")
    assert record is None or record.get("interrupted") is None


def test_the_progress_endpoint_answers_interrupted(test_client, progress_dir, monkeypatch):
    _jobs(monkeypatch)
    _write(progress_dir, "open-webui", "pulling", agent_instance="agent-before")
    body = test_client.get("/api/extensions/open-webui/progress", headers=test_client.auth_headers).json()
    assert body["status"] == "error"
    assert body["interrupted"] is True
    assert "Retry" in body["error"]


def test_a_long_start_still_records_its_success(progress_dir, monkeypatch):
    # The synchronous start outlasted the grace; the agent just released it.
    _jobs(monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    path = _write(progress_dir, "gotify", "pulling", operation="start", updated_at=old, started_at=old)
    ext_mod._write_started_progress("gotify")
    assert json.loads(path.read_text())["status"] == "started"


def test_a_new_failure_is_not_shown_as_the_old_interruption(progress_dir):
    path = _write(progress_dir, "gotify", "error", interrupted=True, interrupted_phase="pulling",
                  error=ext_mod.INTERRUPTED_PROGRESS_ERROR)
    ext_mod._write_error_progress("gotify", "Host port 8080 is already in use")
    record = json.loads(path.read_text())
    assert record["error"] == "Host port 8080 is already in use"
    assert "interrupted" not in record and "interrupted_phase" not in record


def test_the_job_snapshot_is_reused_briefly_and_never_awaited_on_the_event_loop(monkeypatch):
    calls = _jobs(monkeypatch)
    assert ext_mod._extension_jobs()["instance"] == "agent-now"
    assert ext_mod._extension_jobs()["instance"] == "agent-now"
    assert len(calls) == 1

    monkeypatch.setattr(ext_mod, "_jobs_cache", {"checked_at": None, "value": None})

    async def on_loop():
        return ext_mod._extension_jobs()

    assert asyncio.run(on_loop()) is None
    assert len(calls) == 1


@pytest.mark.parametrize("answer", [
    {"schema_version": 2, "instance": "a", "now": AGENT_NOW.isoformat(), "live": []},
    {"schema_version": 1, "instance": 7, "now": AGENT_NOW.isoformat(), "live": []},
    {"schema_version": 1, "instance": "a", "now": "yesterday", "live": []},
    {"schema_version": 1, "instance": "a", "now": AGENT_NOW.isoformat(), "live": "gotify"},
    {"schema_version": 1, "instance": "a", "now": AGENT_NOW.isoformat(), "live": [1]},
])
def test_an_unverifiable_agent_answer_proves_nothing(monkeypatch, answer):
    monkeypatch.setattr(ext_mod, "request_agent_json", lambda *args, **kwargs: answer)
    assert REAL_FETCH_JOBS() is None


def test_an_unreachable_agent_proves_nothing(monkeypatch):
    from host_agent_client import AgentUnavailable

    def unreachable(*_args, **_kwargs):
        raise AgentUnavailable("connection refused")

    monkeypatch.setattr(ext_mod, "request_agent_json", unreachable)
    assert REAL_FETCH_JOBS() is None


def test_the_agent_answer_is_parsed(monkeypatch):
    requests: list[tuple[str, str]] = []
    answer = {"schema_version": 1, "instance": "a", "now": AGENT_NOW.isoformat(), "live": ["gotify"]}

    def agent(method, path, timeout):
        requests.append((method, path))
        return answer

    monkeypatch.setattr(ext_mod, "request_agent_json", agent)
    assert REAL_FETCH_JOBS() == {"instance": "a", "now": AGENT_NOW, "live": frozenset({"gotify"})}
    assert requests == [("GET", "/v1/extension/jobs")]


# --- Catalog ---


def _catalog_row(test_client, service_id, services):
    with patch("helpers.get_cached_services", return_value=services):
        response = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers)
    assert response.status_code == 200
    return next(item for item in response.json()["extensions"] if item["id"] == service_id)


def test_the_catalog_offers_retry_for_an_interrupted_install(test_client, tmp_path, monkeypatch):
    _patch_extensions_config(monkeypatch, [_make_catalog_ext("gotify")], tmp_path=tmp_path)
    _setup_user_ext(tmp_path, "gotify", enabled=True)
    _jobs(monkeypatch)
    _write(tmp_path, "gotify", "pulling", agent_instance="agent-before", operation_id="b" * 32)
    row = _catalog_row(test_client, "gotify", [])
    assert row["status"] == "error"
    assert row["interrupted"] is True
    assert row["interrupted_operation"] == "install"
    assert row["error_message"] == ext_mod.INTERRUPTED_PROGRESS_ERROR


def test_an_interrupted_operation_whose_service_runs_reads_as_running(test_client, tmp_path, monkeypatch):
    # The agent stopped after the container came up, before its last record.
    _patch_extensions_config(monkeypatch, [_make_catalog_ext("gotify")], tmp_path=tmp_path)
    _setup_user_ext(tmp_path, "gotify", enabled=True)
    _write(tmp_path, "gotify", "error", interrupted=True, interrupted_phase="starting",
           error=ext_mod.INTERRUPTED_PROGRESS_ERROR, agent_instance="agent-now")
    services = {"gotify": _make_service_status("gotify", "healthy")}
    assert ext_mod._compute_extension_status(_make_catalog_ext("gotify"), services) == "enabled"
    assert ext_mod._compute_extension_status(_make_catalog_ext("gotify"), {}) == "error"


# --- Cancel ---


def test_cancel_forgets_only_an_interrupted_attempt(test_client, tmp_path, monkeypatch):
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: tmp_path / ".lock")
    path = _write(tmp_path, "comfyui", "error", interrupted=True, interrupted_phase="pulling",
                  error=ext_mod.INTERRUPTED_PROGRESS_ERROR)
    response = test_client.delete("/api/extensions/comfyui/progress", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["action"] == "dismissed"
    assert "Nothing was removed" in response.json()["message"]
    assert not path.exists()


def test_cancel_judges_an_orphaned_record_with_the_agent(test_client, tmp_path, monkeypatch):
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: tmp_path / ".lock")
    _jobs(monkeypatch)
    path = _write(tmp_path, "comfyui", "pulling", agent_instance="agent-before")
    response = test_client.delete("/api/extensions/comfyui/progress", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert not path.exists()


@pytest.mark.parametrize("status,extra", [
    ("pulling", {"agent_instance": "agent-before"}),  # No agent answer: may still run.
    ("error", {"error": "Host port in use"}),
    ("started", {}),
])
def test_cancel_refuses_anything_but_an_interruption(test_client, tmp_path, monkeypatch, status, extra):
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: tmp_path / ".lock")
    path = _write(tmp_path, "comfyui", status, **extra)
    before = path.read_bytes()
    response = test_client.delete("/api/extensions/comfyui/progress", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert "Refresh" in response.json()["detail"]
    assert path.read_bytes() == before


def test_cancel_without_a_record_changes_nothing(test_client, tmp_path, monkeypatch):
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: tmp_path / ".lock")
    response = test_client.delete("/api/extensions/comfyui/progress", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["action"] == "none"


@pytest.mark.parametrize("record,kept", [
    ({"status": "error", "interrupted": True, "interrupted_phase": "pulling",
      "error": ext_mod.INTERRUPTED_PROGRESS_ERROR}, False),
    ({"status": "error", "error": "Image download made no progress for 15 minutes."}, True),
])
def test_a_retry_whose_images_are_here_drops_only_the_old_interruption(
        test_client, tmp_path, monkeypatch, record, kept):
    # Nothing downloads when the images are complete, so nothing else would
    # replace the record; the retry's own progress would read as interrupted.
    _patch_mutation_config(monkeypatch, tmp_path)
    monkeypatch.setattr(ext_mod, "_extensions_lock_path", lambda: tmp_path / ".lock")
    path = _write(tmp_path, "open-webui", record.pop("status"), **record)
    monkeypatch.setattr(ext_mod, "request_agent_json",
                        lambda method, path, payload=None, timeout=None: {"status": "ready"})
    response = test_client.post("/api/extensions/open-webui/prepare", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert path.exists() is kept


@pytest.mark.parametrize("name", ["../user-extensions", "..", "a/../../b", ""])
def test_paths_built_from_ids_stay_in_their_folder(tmp_path, name):
    from fastapi import HTTPException

    assert ext_mod._contained_path(tmp_path, "gotify.json") == tmp_path / "gotify.json"
    with pytest.raises(HTTPException):
        ext_mod._contained_path(tmp_path, name)


# --- Remove ---


def _remove_spy(monkeypatch, result=None, error=None):
    calls: list[tuple[str, bool]] = []

    def remove(service_id, *, delete_volumes):
        calls.append((service_id, delete_volumes))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", remove)
    return calls


def test_remove_deletes_the_container_and_keeps_data(test_client, tmp_path, monkeypatch):
    user_dir = _setup_user_ext(tmp_path, "excalidraw", enabled=False,
                               manifest={"service": {"id": "excalidraw", "name": "Excalidraw"}})
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
    calls = _remove_spy(monkeypatch, {"containers": ["ods-excalidraw"], "volumes_removed": [],
                                      "volumes_kept": ["ods_excalidraw-data"]})
    data = tmp_path / "excalidraw"
    data.mkdir()
    (data / "scene.json").write_text("owner drawing")

    response = test_client.delete("/api/extensions/excalidraw", headers=test_client.auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert calls == [("excalidraw", False)]
    assert body["containers_removed"] == ["ods-excalidraw"]
    assert body["data_deleted"] is False
    assert body["volumes_kept"] == ["ods_excalidraw-data"]
    assert body["message"].startswith("Excalidraw was removed: its container and installed definition were deleted.")
    assert "Its data was kept (data/excalidraw" in body["message"]
    assert "its Docker volumes" in body["message"]
    assert not (user_dir / "excalidraw").exists()
    assert (data / "scene.json").read_text() == "owner drawing"


def test_remove_deletes_data_only_when_asked(test_client, tmp_path, monkeypatch):
    user_dir = _setup_user_ext(tmp_path, "excalidraw", enabled=False,
                               manifest={"service": {"id": "excalidraw", "name": "Excalidraw"}})
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
    calls = _remove_spy(monkeypatch, {"containers": ["ods-excalidraw"],
                                      "volumes_removed": ["ods_excalidraw-data"], "volumes_kept": []})
    data = tmp_path / "excalidraw"
    data.mkdir()
    (data / "scene.json").write_text("owner drawing")

    response = test_client.delete("/api/extensions/excalidraw?delete_data=true",
                                  headers=test_client.auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert calls == [("excalidraw", True)]
    assert body["data_deleted"] is True
    assert body["data_info"] is None
    assert body["volumes_removed"] == ["ods_excalidraw-data"]
    assert "removed with its data" in body["message"]
    assert not data.exists()
    assert not (user_dir / "excalidraw").exists()


def test_remove_of_a_failed_install_stops_it_then_deletes_its_container(test_client, tmp_path, monkeypatch):
    user_dir = _setup_user_ext(tmp_path, "gotify", enabled=True)
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
    order: list[str] = []

    def agent(action, sid):
        order.append(f"agent:{action}")
        return True

    def remove_containers(sid, *, delete_volumes):
        order.append("remove-containers")
        return {"containers": ["ods-gotify"], "volumes_removed": [], "volumes_kept": []}

    monkeypatch.setattr(ext_mod, "_call_agent", agent)
    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", remove_containers)
    _jobs(monkeypatch)
    _write(tmp_path, "gotify", "pulling", agent_instance="agent-before", operation_id="c" * 32)

    response = test_client.delete("/api/extensions/gotify", headers=test_client.auth_headers)

    assert response.status_code == 200
    # The interrupted install counts as a failed one: stopped, then removed.
    assert order == ["agent:stop", "remove-containers"]
    assert response.json()["stopped_before_removal"] is True
    assert "stopped and removed" in response.json()["message"]


@pytest.mark.parametrize("status,detail", [
    (409, "The gotify container is running; stop it before removing it"),
    (502, "Docker recovery command failed: daemon unreachable"),
])
def test_remove_keeps_the_extension_when_its_container_cannot_be_deleted(
        test_client, tmp_path, monkeypatch, status, detail):
    from host_agent_client import AgentHTTPError

    user_dir = _setup_user_ext(tmp_path, "gotify", enabled=False)
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

    def refuse(*_args, **_kwargs):
        raise AgentHTTPError(status, detail)

    monkeypatch.setattr(ext_mod, "request_agent_json", refuse)
    # _patch_mutation_config stubs the call; this test needs the real one.
    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", REAL_REMOVE_CONTAINERS)

    response = test_client.delete("/api/extensions/gotify", headers=test_client.auth_headers)

    assert response.status_code == (409 if status == 409 else 502)
    message = response.json()["detail"]
    assert detail in message
    assert "was not removed" in message and "Retry Remove" in message
    assert (user_dir / "gotify" / "compose.yaml.disabled").exists()


def test_remove_with_an_unreachable_host_keeps_the_extension(test_client, tmp_path, monkeypatch):
    from host_agent_client import AgentUnavailable

    user_dir = _setup_user_ext(tmp_path, "gotify", enabled=False)
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

    def unreachable(*_args, **_kwargs):
        raise AgentUnavailable("connection refused")

    monkeypatch.setattr(ext_mod, "request_agent_json", unreachable)
    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", REAL_REMOVE_CONTAINERS)

    response = test_client.delete("/api/extensions/gotify", headers=test_client.auth_headers)

    assert response.status_code == 503
    assert "not responding" in response.json()["detail"]
    assert (user_dir / "gotify").is_dir()


def test_remove_with_an_older_host_agent_still_removes_and_says_the_container_stayed(
        test_client, tmp_path, monkeypatch):
    from host_agent_client import AgentHTTPError

    user_dir = _setup_user_ext(tmp_path, "gotify", enabled=False)
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

    def not_found(*_args, **_kwargs):
        raise AgentHTTPError(404, "Not found")

    monkeypatch.setattr(ext_mod, "request_agent_json", not_found)
    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", REAL_REMOVE_CONTAINERS)

    response = test_client.delete("/api/extensions/gotify", headers=test_client.auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["containers_removed"] is None
    assert "Its stopped container was kept" in body["message"]
    assert not (user_dir / "gotify").exists()


def test_remove_rejects_an_unverifiable_host_answer(test_client, tmp_path, monkeypatch):
    user_dir = _setup_user_ext(tmp_path, "gotify", enabled=False)
    _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
    monkeypatch.setattr(ext_mod, "request_agent_json", lambda *args, **kwargs: {"containers": "all"})
    monkeypatch.setattr(ext_mod, "_call_agent_remove_containers", REAL_REMOVE_CONTAINERS)

    response = test_client.delete("/api/extensions/gotify", headers=test_client.auth_headers)

    assert response.status_code == 502
    assert (user_dir / "gotify").is_dir()

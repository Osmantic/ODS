"""Authenticated template transactions share individual extension lifecycle locks."""
import asyncio
import hashlib
import os
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def lifecycle_api(monkeypatch, tmp_path):
    import helpers
    import security
    from routers import extensions, templates

    user = tmp_path / "extensions"
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    for sid, deps in [("demo", ["dep"]), ("dep", [])]:
        directory = user / sid
        directory.mkdir(parents=True)
        directory.joinpath("manifest.yaml").write_text(
            f"service:\n  id: {sid}\n  depends_on: {deps!r}\n", encoding="utf-8"
        )
        directory.joinpath("compose.yaml.disabled").write_text(
            f"services:\n  {sid}:\n    image: busybox:1.36\n", encoding="utf-8"
        )
    # conftest supplies a no-op fcntl on Windows; use the real native lock here.
    if os.name == "nt":
        import msvcrt
        monkeypatch.setattr(extensions, "fcntl", None)
        monkeypatch.setattr(extensions, "msvcrt", msvcrt)
    for module in (extensions, templates):
        monkeypatch.setattr(module, "USER_EXTENSIONS_DIR", user)
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", builtin)
    monkeypatch.setattr(extensions, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(extensions, "_scan_installed_compose", lambda *a, **k: None)
    monkeypatch.setattr(extensions, "_is_installable", lambda sid: False)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", lambda: None)
    monkeypatch.setattr(extensions, "_call_agent_hook", lambda *args: True)
    monkeypatch.setattr(templates, "TEMPLATES", [
        {"id": "forward", "services": ["demo", "dep"]},
        {"id": "reverse", "services": ["dep", "demo"]},
    ])
    monkeypatch.setattr(templates, "EXTENSION_CATALOG", [])
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [])
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "fixture-key")
    runtime = {"demo": False, "dep": False}
    calls = []

    def network(method, path, payload=None, timeout=None):
        if path == "/v1/extension/select":
            selected = payload["service_ids"]
            enabling = payload["action"] == "enable"
            # Host selection owns the same canonical graph lock. This fails
            # if the API keeps that lock across its host request.
            with extensions._extensions_lock():
                for sid in selected:
                    directory = user / sid
                    source = directory / ("compose.yaml.disabled" if enabling else "compose.yaml")
                    destination = directory / ("compose.yaml" if enabling else "compose.yaml.disabled")
                    if enabling:
                        compose = source if source.exists() else destination
                        assert hashlib.sha256(compose.read_bytes()).hexdigest() == payload["expected_sha256"][sid]
                    if not enabling:
                        # Preserve the actual stop -> rename physical boundary.
                        extensions.request_agent_json("POST", "/v1/service/stop", payload={"service_id": sid})
                    if source.exists():
                        source.rename(destination)
            return {"action": "enabled" if enabling else "disabled", "service_ids": selected}
        sid = payload["service_id"]
        action = path.rsplit("/", 1)[-1]
        calls.append((action, sid))
        if action == "start":
            assert (user / sid / "compose.yaml").exists()
        runtime[sid] = action == "start"
        return {"status": "ok"}

    monkeypatch.setattr(extensions, "request_agent_json", network)
    app = FastAPI()
    app.include_router(extensions.router)
    app.include_router(templates.router)

    @app.get("/heartbeat")
    async def heartbeat():
        return {"ok": True}

    return app, extensions, user, runtime, calls, network


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer fixture-key"},
    )


async def reached(event):
    assert await asyncio.to_thread(event.wait, 5), "Worker did not reach boundary"


def observe_contender(monkeypatch, extensions, owner_boundary):
    """Wait for the second physical worker to attempt the existing file guard."""
    attempted = threading.Event()
    original = extensions._extension_operation_lock

    @contextmanager
    def observed(sid):
        if owner_boundary.is_set():
            attempted.set()
        with original(sid):
            yield

    monkeypatch.setattr(extensions, "_extension_operation_lock", observed)
    original_graph = extensions._extensions_lock

    @contextmanager
    def observed_graph():
        if owner_boundary.is_set():
            attempted.set()
        with original_graph():
            yield

    monkeypatch.setattr(extensions, "_extensions_lock", observed_graph)
    return attempted


@pytest.mark.asyncio
async def test_template_cannot_restart_between_disable_stop_and_rename(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    (user / "demo" / "compose.yaml.disabled").rename(user / "demo" / "compose.yaml")
    stopped, release, started = threading.Event(), threading.Event(), threading.Event()

    def held_network(method, path, payload=None, timeout=None):
        result = network(method, path, payload, timeout)
        if payload.get("service_id") == "demo":
            if path.endswith("/stop"):
                stopped.set()
                assert release.wait(5)
            elif path.endswith("/start"):
                started.set()
        return result

    monkeypatch.setattr(extensions, "request_agent_json", held_network)
    attempted = observe_contender(monkeypatch, extensions, stopped)
    async with client(app) as api:
        assert (await api.post("/api/templates/forward/apply", headers={"Authorization": "Bearer wrong"})).status_code == 403
        disable = asyncio.create_task(api.post("/api/extensions/demo/disable?include_data_info=false"))
        await reached(stopped)
        apply = asyncio.create_task(api.post("/api/templates/forward/apply"))
        try:
            await reached(attempted)
            assert (await asyncio.wait_for(api.get("/heartbeat"), 1)).status_code == 200
            assert not await asyncio.to_thread(started.wait, .15)
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(disable, apply), 8)
        assert all(result.status_code == 200 for result in results)
        assert runtime["demo"] and (user / "demo" / "compose.yaml").exists()
        assert results[1].json()["failed_services"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_id", ["demo", "dep"])
async def test_cached_healthy_service_disabled_while_apply_waits_is_started_again(lifecycle_api, monkeypatch, disabled_id):
    import helpers
    from routers import templates

    app, extensions, user, runtime, calls, network = lifecycle_api
    (user / disabled_id / "compose.yaml.disabled").rename(user / disabled_id / "compose.yaml")
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["demo"]}])
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [SimpleNamespace(id=disabled_id, status="healthy")])
    stopped, release = threading.Event(), threading.Event()

    def held_network(method, path, payload=None, timeout=None):
        result = network(method, path, payload, timeout)
        if path.endswith("/stop"):
            stopped.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(extensions, "request_agent_json", held_network)
    attempted = observe_contender(monkeypatch, extensions, stopped)
    async with client(app) as api:
        disable = asyncio.create_task(api.post(f"/api/extensions/{disabled_id}/disable?include_data_info=false"))
        await reached(stopped)
        apply = asyncio.create_task(api.post("/api/templates/forward/apply"))
        try:
            await reached(attempted)
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(disable, apply), 8)
    assert all(result.status_code == 200 for result in results)
    assert runtime[disabled_id]
    assert ("start", disabled_id) in calls
    assert (user / disabled_id / "compose.yaml").exists()


@pytest.mark.asyncio
async def test_user_docker_definition_shadows_host_service_health_metadata(lifecycle_api, monkeypatch):
    import helpers
    from routers import templates

    app, extensions, user, runtime, calls, network = lifecycle_api
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["demo"]}])
    monkeypatch.setattr(templates, "SERVICES", {"demo": {"type": "host-systemd"}})
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [SimpleNamespace(id="demo", status="healthy")])
    async with client(app) as api:
        result = await api.post("/api/templates/forward/apply")
    assert result.status_code == 200
    assert runtime["demo"] and ("start", "demo") in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["host-systemd", "docker", "core"])
async def test_healthy_enabled_and_non_docker_services_remain_idempotent(lifecycle_api, monkeypatch, kind):
    import helpers
    from routers import templates

    app, extensions, user, runtime, calls, network = lifecycle_api
    sid = "llama-server" if kind == "core" else "demo"
    if kind == "host-systemd":
        (user / sid / "compose.yaml.disabled").unlink()
        (user / sid / "manifest.yaml").write_text(
            "service:\n  id: demo\n  type: host-systemd\n", encoding="utf-8"
        )
    elif kind == "docker":
        (user / sid / "compose.yaml.disabled").rename(user / sid / "compose.yaml")
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": [sid]}])
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [SimpleNamespace(id=sid, status="healthy")])
    async with client(app) as api:
        result = await api.post("/api/templates/forward/apply")
    assert result.status_code == 200
    assert result.json()["results"][sid] == "already_enabled"
    assert calls == []


@pytest.mark.asyncio
async def test_overlapping_reversed_templates_wait_without_blocking_loop(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    entered, release = threading.Event(), threading.Event()
    hook_calls = []

    def hook(sid, phase):
        if phase == "pre_start":
            hook_calls.append(sid)
            if len(hook_calls) == 1:
                entered.set()
                assert release.wait(5)
        return True

    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    attempted = observe_contender(monkeypatch, extensions, entered)
    async with client(app) as api:
        first = asyncio.create_task(api.post("/api/templates/forward/apply"))
        await reached(entered)
        second = asyncio.create_task(api.post("/api/templates/reverse/apply"))
        try:
            await reached(attempted)
            assert (await asyncio.wait_for(api.get("/heartbeat"), 1)).status_code == 200
            await asyncio.sleep(.15)
            assert len(hook_calls) == 1
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 8)
        assert all(result.status_code == 200 for result in results)
        assert all(runtime.values())


@pytest.mark.asyncio
async def test_cancelled_http_request_keeps_worker_guard_until_host_returns(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def held_network(method, path, payload=None, timeout=None):
        if path.endswith("/start") and payload["service_id"] == "demo":
            entered.set()
            assert release.wait(5)
        if path.endswith("/stop") and payload["service_id"] == "demo":
            stopped.set()
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "request_agent_json", held_network)
    attempted = observe_contender(monkeypatch, extensions, entered)
    async with client(app) as api:
        apply = asyncio.create_task(api.post("/api/templates/forward/apply"))
        await reached(entered)
        apply.cancel()
        with pytest.raises(asyncio.CancelledError):
            await apply
        disable = asyncio.create_task(api.post("/api/extensions/demo/disable?include_data_info=false"))
        try:
            await reached(attempted)
            assert not await asyncio.to_thread(stopped.wait, .15)
        finally:
            release.set()
            result = await asyncio.wait_for(disable, 8)
        assert result.status_code == 200
        assert not runtime["demo"]
        assert (user / "demo" / "compose.yaml.disabled").exists()


@pytest.mark.asyncio
async def test_unexpected_hook_error_releases_template_locks_for_retry(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api

    def broken_hook(sid, phase):
        raise RuntimeError("fixture hook failure")

    monkeypatch.setattr(extensions, "_call_agent_hook", broken_hook)
    async with client(app) as api:
        with pytest.raises(RuntimeError, match="fixture hook failure"):
            await api.post("/api/templates/forward/apply")
        monkeypatch.setattr(extensions, "_call_agent_hook", lambda *args: True)
        result = await asyncio.wait_for(api.post("/api/templates/reverse/apply"), 8)
    assert result.status_code == 200
    assert result.json()["failed_services"] == []


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_admission_does_not_leak_guard(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    first_entered, first_release = threading.Event(), threading.Event()
    second_entered, second_release, stopped = threading.Event(), threading.Event(), threading.Event()
    starts = 0

    def hook(sid, phase):
        nonlocal starts
        if sid == "dep" and phase == "pre_start":
            starts += 1
            entered, release = (first_entered, first_release) if starts == 1 else (second_entered, second_release)
            entered.set()
            assert release.wait(5)
        return True

    def watched_network(method, path, payload=None, timeout=None):
        if path.endswith("/stop"):
            stopped.set()
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    monkeypatch.setattr(extensions, "request_agent_json", watched_network)
    attempted = observe_contender(monkeypatch, extensions, first_entered)
    async with client(app) as api:
        first = asyncio.create_task(api.post("/api/templates/forward/apply"))
        await reached(first_entered)
        waiting = asyncio.create_task(api.post("/api/templates/reverse/apply"))
        try:
            await reached(attempted)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            first_release.set()
            assert (await asyncio.wait_for(first, 8)).status_code == 200
            await reached(second_entered)
            disable = asyncio.create_task(api.post("/api/extensions/demo/disable?include_data_info=false"))
            assert not await asyncio.to_thread(stopped.wait, .15)
        finally:
            first_release.set()
            second_release.set()
        assert (await asyncio.wait_for(disable, 8)).status_code == 200
        # A further request also settles: no abandoned acquisition owns the file.
        monkeypatch.setattr(extensions, "_call_agent_hook", lambda *args: True)
        assert (await asyncio.wait_for(api.post("/api/templates/forward/apply"), 8)).status_code == 200


@pytest.mark.asyncio
async def test_changed_dependency_closure_releases_and_reacquires_ordered_plan(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    # Initially dep is not reachable. It becomes a prerequisite while the
    # worker waits for admission, before any compose mutation.
    manifest = user / "demo" / "manifest.yaml"
    manifest.write_text("service:\n  id: demo\n  depends_on: []\n", encoding="utf-8")
    from routers import templates
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["demo"]}])
    original = extensions._extension_operation_lock
    acquired = []
    held = set()

    @contextmanager
    def changing(sid):
        with original(sid):
            acquired.append(sid)
            if len(acquired) == 1:
                manifest.write_text("service:\n  id: demo\n  depends_on: [dep]\n", encoding="utf-8")
            held.add(sid)
            try:
                yield
            finally:
                held.remove(sid)

    def guarded_network(method, path, payload=None, timeout=None):
        assert held == {"demo", "dep"}
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "_extension_operation_lock", changing)
    monkeypatch.setattr(extensions, "request_agent_json", guarded_network)
    async with client(app) as api:
        result = await api.post("/api/templates/forward/apply")
    assert result.status_code == 200
    assert acquired == ["demo", "demo", "dep"]
    assert calls == [("start", "dep"), ("start", "demo")]


@pytest.mark.asyncio
async def test_unstable_dependency_closure_fails_before_mutation(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    from routers import templates
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["demo"]}])
    manifest = user / "demo" / "manifest.yaml"
    original = extensions._extension_operation_lock
    changes = 0

    @contextmanager
    def changing(sid):
        nonlocal changes
        with original(sid):
            if sid == "demo":
                changes += 1
                deps = "[]" if changes % 2 else "[dep]"
                manifest.write_text(f"service:\n  id: demo\n  depends_on: {deps}\n", encoding="utf-8")
            yield

    monkeypatch.setattr(extensions, "_extension_operation_lock", changing)
    async with client(app) as api:
        result = await api.post("/api/templates/forward/apply")
    assert result.status_code == 409
    assert calls == []
    assert all((user / sid / "compose.yaml.disabled").exists() for sid in runtime)


@pytest.mark.asyncio
async def test_cycle_keeps_per_service_skip_receipt(lifecycle_api):
    app, extensions, user, runtime, calls, network = lifecycle_api
    (user / "dep" / "manifest.yaml").write_text(
        "service:\n  id: dep\n  depends_on: [demo]\n", encoding="utf-8"
    )
    async with client(app) as api:
        result = await api.post("/api/templates/forward/apply")
    assert result.status_code == 200
    assert all(outcome.startswith("skipped:") for outcome in result.json()["results"].values())
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 502])
async def test_host_selection_refusal_preserves_disabled_plan_and_retry(lifecycle_api, monkeypatch, status):
    from fastapi import HTTPException
    app, extensions, user, runtime, calls, _network = lifecycle_api
    original = extensions._select_extensions_on_host

    def refuse(*_args, **_kwargs):
        raise HTTPException(status_code=status, detail="selection refused")

    monkeypatch.setattr(extensions, "_select_extensions_on_host", refuse)
    async with client(app) as api:
        refused = await api.post("/api/templates/forward/apply")
        assert refused.status_code == 200
        assert refused.json()["enabled_count"] == 0
        assert calls == []
        assert all((user / sid / "compose.yaml.disabled").exists() for sid in runtime)
        monkeypatch.setattr(extensions, "_select_extensions_on_host", original)
        recovered = await api.post("/api/templates/forward/apply")
        assert recovered.status_code == 200
        assert recovered.json()["failed_services"] == []
        assert all(runtime.values())

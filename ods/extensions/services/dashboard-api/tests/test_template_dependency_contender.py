"""Template dependency ownership also covers other roots' physical work."""
import asyncio
import threading
from contextlib import contextmanager

import pytest

import test_template_operation_serialization as serialization
from test_template_operation_serialization import client, observe_contender, reached

lifecycle_api = serialization.lifecycle_api


def recipe(user, sid, deps=()):
    directory = user / sid
    directory.mkdir()
    (directory / "manifest.yaml").write_text(
        f"service:\n  id: {sid}\n  depends_on: {list(deps)!r}\n", encoding="utf-8")
    (directory / "compose.yaml.disabled").write_text(
        f"services:\n  {sid}:\n    image: busybox:1.36\n", encoding="utf-8")
    return directory


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["aaa", "zzz"])
async def test_individual_enable_cannot_start_template_owned_dependency(lifecycle_api, monkeypatch, root):
    from routers import templates

    app, extensions, user, runtime, calls, network = lifecycle_api
    recipe(user, root, ["dep"])
    recipe(user, "isolated")
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["demo"]}])
    template_paused, release_template, dependency_started = (
        threading.Event(), threading.Event(), threading.Event())

    def held_network(method, path, payload=None, timeout=None):
        if path == "/v1/extension/select" and "demo" in payload["service_ids"]:
            template_paused.set()
            assert release_template.wait(5)
        if path.endswith("/start") and payload["service_id"] == "dep":
            dependency_started.set()
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "request_agent_json", held_network)
    attempted = observe_contender(monkeypatch, extensions, template_paused)
    async with client(app) as api:
        apply = asyncio.create_task(api.post("/api/templates/forward/apply"))
        await reached(template_paused)
        enable = asyncio.create_task(api.post(f"/api/extensions/{root}/enable?auto_enable_deps=true"))
        try:
            # The template owns demo and dep already but has not selected any
            # Compose marker. Another root must not start dep in this window.
            await reached(attempted)
            assert not await asyncio.to_thread(dependency_started.wait, .15)
            # Waiting for a dependency must not own the graph lock or prevent
            # an unrelated lifecycle operation from completing.
            unrelated = await asyncio.wait_for(api.post("/api/extensions/isolated/enable"), 2)
            assert unrelated.status_code == 200
            assert runtime["isolated"]
        finally:
            release_template.set()
            results = await asyncio.wait_for(asyncio.gather(apply, enable), 8)
        assert all(result.status_code == 200 for result in results)


@pytest.mark.asyncio
async def test_cancelled_individual_enable_keeps_dependency_guard_until_start_returns(lifecycle_api, monkeypatch):
    from routers import templates

    app, extensions, user, runtime, calls, network = lifecycle_api
    recipe(user, "other", ["dep"])
    monkeypatch.setattr(templates, "TEMPLATES", [{"id": "forward", "services": ["other"]}])
    entered, release, restarted = threading.Event(), threading.Event(), threading.Event()
    starts = 0

    def held_network(method, path, payload=None, timeout=None):
        nonlocal starts
        if path.endswith("/start") and payload["service_id"] == "dep":
            starts += 1
            if starts == 1:
                entered.set()
                assert release.wait(5)
            else:
                restarted.set()
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "request_agent_json", held_network)
    attempted = observe_contender(monkeypatch, extensions, entered)
    async with client(app) as api:
        enable = asyncio.create_task(api.post("/api/extensions/demo/enable?auto_enable_deps=true"))
        await reached(entered)
        enable.cancel()
        with pytest.raises(asyncio.CancelledError):
            await enable
        apply = asyncio.create_task(api.post("/api/templates/forward/apply"))
        try:
            await reached(attempted)
            assert not await asyncio.to_thread(restarted.wait, .15)
            assert (await asyncio.wait_for(api.get("/heartbeat"), 1)).status_code == 200
        finally:
            release.set()
            result = await asyncio.wait_for(apply, 8)
        assert result.status_code == 200
        assert result.json()["failed_services"] == []
        assert runtime["other"] and runtime["demo"]


@pytest.mark.asyncio
async def test_individual_enable_replans_changed_dependency_before_physical_work(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    directory = recipe(user, "zzz")
    original = extensions._extension_operation_lock
    acquired, held = [], set()

    @contextmanager
    def changing(sid):
        with original(sid):
            acquired.append(sid)
            if len(acquired) == 1:
                (directory / "manifest.yaml").write_text(
                    "service:\n  id: zzz\n  depends_on: [dep]\n", encoding="utf-8")
            held.add(sid)
            try:
                yield
            finally:
                held.remove(sid)

    def guarded_network(method, path, payload=None, timeout=None):
        assert held == {"zzz", "dep"}
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "_extension_operation_lock", changing)
    monkeypatch.setattr(extensions, "request_agent_json", guarded_network)
    async with client(app) as api:
        result = await api.post("/api/extensions/zzz/enable?auto_enable_deps=true")
    assert result.status_code == 200
    assert acquired == ["zzz", "dep", "zzz"]
    assert calls == [("start", "dep"), ("start", "zzz")]


@pytest.mark.asyncio
async def test_feature_companions_are_owned_during_enable_and_disable(lifecycle_api, monkeypatch):
    app, extensions, user, runtime, calls, network = lifecycle_api
    recipe(user, "hermes")
    recipe(user, "hermes-proxy", ["hermes"])
    manifest_dir = extensions.EXTENSIONS_DIR / "hermes"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        "service:\n  id: hermes\nfeatures:\n  - id: hermes-agent\n"
        "    enabled_services_all: [hermes, hermes-proxy]\n", encoding="utf-8")
    held = set()
    original = extensions._extension_operation_lock

    @contextmanager
    def watched(sid):
        with original(sid):
            held.add(sid)
            try:
                yield
            finally:
                held.remove(sid)

    def guarded_network(method, path, payload=None, timeout=None):
        assert held == {"hermes", "hermes-proxy"}
        return network(method, path, payload, timeout)

    monkeypatch.setattr(extensions, "_extension_operation_lock", watched)
    monkeypatch.setattr(extensions, "request_agent_json", guarded_network)
    async with client(app) as api:
        enable = await api.post("/api/extensions/hermes/enable")
        assert enable.status_code == 200
        disable = await api.post("/api/extensions/hermes/disable?include_data_info=false")
        assert disable.status_code == 200
    assert calls == [("start", "hermes"), ("start", "hermes-proxy"),
                     ("stop", "hermes-proxy"), ("stop", "hermes")]
    assert held == set()

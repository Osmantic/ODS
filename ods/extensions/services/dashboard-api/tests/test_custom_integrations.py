"""Custom integrations: owner-declared external systems and their checks."""

import asyncio
import json
import socket
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

import custom_integrations as ci
from custom_integrations import (
    CustomIntegrationStore,
    IntegrationConflict,
    IntegrationError,
    IntegrationStoreUnreadable,
    probe_health_url,
    validate_health_url,
)


# --- URL policy ---------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://jev.example.com/health", "https://jev.example.com/health"),
    ("http://192.168.1.20:8080", "http://192.168.1.20:8080/"),
    ("HTTP://host.docker.internal:9000/healthz", "http://host.docker.internal:9000/healthz"),
    ("http://100.101.102.103/status", "http://100.101.102.103/status"),  # Tailscale peer
    ("http://[::1]:7000/health", "http://[::1]:7000/health"),
    ("  https://engine.lan/ready  ", "https://engine.lan/ready"),
])
def test_accepts_plain_health_urls(url, expected):
    assert validate_health_url(url) == expected


@pytest.mark.parametrize("url, fragment", [
    ("ftp://example.com/health", "http:// or https://"),
    ("file:///etc/passwd", "http:// or https://"),
    ("https://user:secret@example.com/health", "credentials"),
    ("https://token@example.com/health", "credentials"),
    ("https://example.com/health?api_key=secret", "query string"),
    ("https://example.com/health#token", "query string"),
    ("http://169.254.169.254/latest/meta-data", "metadata"),
    ("http://[fe80::1]/health", "metadata"),
    ("http://[::ffff:169.254.169.254]/", "metadata"),
    ("http://[fd00:ec2::254]/", "metadata"),
    ("http://[64:ff9b::a9fe:a9fe]/", "metadata"),  # NAT64 form of 169.254.169.254
    ("http://[2002:a9fe:a9fe::1]/", "metadata"),  # 6to4 form of 169.254.169.254
    ("http://0.0.0.0:80/", "metadata"),
    ("http://224.0.0.1/", "metadata"),
    ("https://exa mple.com/", "spaces"),
    ("https://example.com\\@evil/", "backslashes"),
    ("https://example.com:0/", "invalid port"),
    ("https://example.com:99999/", "invalid port"),
    ("https:///health", "host name"),
    ("", "health URL"),
    (None, "health URL"),
    ("https://example.com/" + "a" * 600, "512"),
])
def test_refuses_urls_that_could_carry_secrets_or_reach_metadata(url, fragment):
    with pytest.raises(IntegrationError) as error:
        validate_health_url(url)
    assert fragment in str(error.value)


def test_names_and_notes_are_bounded_single_line_text():
    assert ci.validate_name("  Jev \n decision   API ") == "Jev decision API"
    assert ci.validate_notes(None) == ""
    with pytest.raises(IntegrationError):
        ci.validate_name("")
    with pytest.raises(IntegrationError):
        ci.validate_name("x" * 61)
    with pytest.raises(IntegrationError):
        ci.validate_name("bad\x00name")
    with pytest.raises(IntegrationError):
        ci.validate_notes("n" * 281)


# --- Store ----------------------------------------------------------------------

def _result(status="healthy", code=200):
    return {"status": status, "detail": f"Answered HTTP {code}", "httpStatus": code,
            "latencyMs": 12, "checkedAt": "2026-09-25T12:00:00Z"}


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.mark.asyncio
async def test_add_persists_checks_immediately_and_lists(tmp_path):
    probe = AsyncMock(return_value=_result())
    store = CustomIntegrationStore(tmp_path / "integrations" / "custom.json", probe=probe)

    added = await store.add("Jev (System 1)", "https://jev.example.com/health", "Hosted decision API")

    assert added["id"] == "jev-system-1"
    assert added["check"]["status"] == "healthy"
    probe.assert_awaited_once_with("https://jev.example.com/health")
    document = json.loads((tmp_path / "integrations" / "custom.json").read_text())
    assert document == {"schemaVersion": 1, "integrations": [{
        "id": "jev-system-1", "name": "Jev (System 1)", "url": "https://jev.example.com/health",
        "notes": "Hosted decision API", "createdAt": added["createdAt"],
    }]}
    listed = await store.list()
    assert [item["id"] for item in listed] == ["jev-system-1"]
    assert probe.await_count == 1  # fresh result reused, not re-probed


@pytest.mark.asyncio
async def test_duplicate_url_limit_and_id_collisions(tmp_path, monkeypatch):
    store = CustomIntegrationStore(tmp_path / "custom.json", probe=AsyncMock(return_value=_result()))
    await store.add("Engine", "http://engine.lan/health")
    second = await store.add("Engine", "http://engine.lan:9000/health")
    assert second["id"] == "engine-2"
    with pytest.raises(IntegrationConflict, match="already checks this URL"):
        await store.add("Other", "http://engine.lan/health")
    monkeypatch.setattr(ci, "MAX_INTEGRATIONS", 2)
    with pytest.raises(IntegrationConflict, match="up to 2"):
        await store.add("Third", "http://third.lan/health")


@pytest.mark.asyncio
async def test_stale_results_are_rechecked_once_for_concurrent_readers(tmp_path):
    clock = _Clock()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow_probe(url):
        calls.append(url)
        started.set()
        await release.wait()
        return _result("down", None)

    store = CustomIntegrationStore(tmp_path / "custom.json", probe=AsyncMock(return_value=_result()),
                                   stale_after=60, clock=clock)
    await store.add("Engine", "http://engine.lan/health")
    store._probe = slow_probe
    clock.now += 30
    assert (await store.list())[0]["check"]["status"] == "healthy"
    assert calls == []

    clock.now += 31
    readers = [asyncio.create_task(store.list()) for _ in range(3)]
    await started.wait()
    release.set()
    results = await asyncio.gather(*readers)
    assert calls == ["http://engine.lan/health"]
    assert all(result[0]["check"]["status"] == "down" for result in results)
    assert (await store.list(refresh="none"))[0]["check"]["status"] == "down"


@pytest.mark.asyncio
async def test_check_forces_a_probe_and_remove_forgets_the_result(tmp_path):
    probe = AsyncMock(side_effect=[_result(), _result("unhealthy", 503), _result()])
    store = CustomIntegrationStore(tmp_path / "custom.json", probe=probe)
    await store.add("Engine", "http://engine.lan/health")
    checked = await store.check("engine")
    assert checked["check"]["httpStatus"] == 503
    assert await store.check("missing") is None
    assert await store.check("../etc") is None

    assert await store.remove("engine") is True
    assert await store.remove("engine") is False
    assert await store.list() == []
    readded = await store.add("Engine", "http://engine.lan:81/health")
    assert readded["id"] == "engine"
    assert readded["check"]["httpStatus"] == 200  # never the removed entry's result


@pytest.mark.asyncio
async def test_probe_exceptions_become_a_down_result(tmp_path):
    store = CustomIntegrationStore(tmp_path / "custom.json", probe=AsyncMock(side_effect=RuntimeError("boom")))
    added = await store.add("Engine", "http://engine.lan/health")
    assert added["check"]["status"] == "down"
    assert added["check"]["detail"] == "Check failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"schemaVersion": 2, "integrations": []}),
    json.dumps({"schemaVersion": 1, "integrations": [{"id": "x", "name": "X", "url": "ftp://x", "notes": "",
                                                       "createdAt": "t"}]}),
    json.dumps({"schemaVersion": 1, "integrations": [], "extra": True}),
])
async def test_unreadable_list_is_reported_and_never_overwritten(tmp_path, content):
    path = tmp_path / "custom.json"
    path.write_text(content)
    store = CustomIntegrationStore(path, probe=AsyncMock(return_value=_result()))
    with pytest.raises(IntegrationStoreUnreadable):
        await store.list()
    with pytest.raises(IntegrationStoreUnreadable):
        await store.add("Engine", "http://engine.lan/health")
    assert path.read_text() == content


# --- Probe ------------------------------------------------------------------------

async def _serve(handler):
    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    await web.SockSite(runner, listener).start()
    return runner, port


@pytest.mark.asyncio
@pytest.mark.parametrize("code, status", [
    (200, "healthy"), (204, "healthy"), (302, "degraded"), (401, "degraded"),
    (403, "degraded"), (404, "unhealthy"), (503, "unhealthy"),
])
async def test_probe_reports_only_the_outcome(code, status):
    seen = []

    async def handler(request):
        seen.append({"auth": request.headers.get("Authorization"), "cookie": request.headers.get("Cookie"),
                     "agent": request.headers.get("User-Agent")})
        headers = {"Location": "http://169.254.169.254/"} if code == 302 else {}
        return web.Response(status=code, text="internal-secret-body", headers=headers)

    runner, port = await _serve(handler)
    try:
        result = await probe_health_url(f"http://127.0.0.1:{port}/health")
    finally:
        await runner.cleanup()
    assert result["status"] == status
    assert result["httpStatus"] == code
    assert isinstance(result["latencyMs"], int)
    assert "internal-secret-body" not in json.dumps(result)
    assert seen == [{"auth": None, "cookie": None, "agent": "ODS-Integrations/1"}]  # one GET, no redirect


@pytest.mark.asyncio
async def test_probe_refuses_hosts_that_resolve_to_metadata_addresses(monkeypatch):
    async def fake_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    result = await probe_health_url("http://metadata.attacker.test/latest")
    assert result["status"] == "down"
    assert "metadata" in result["detail"]
    assert result["httpStatus"] is None


NUMERIC_METADATA_FORMS = ["2852039166", "0251.0376.0251.0376", "0xa9fea9fe", "0xa9.0xfe.0xa9.0xfe",
                          "169.254.43518", "169.254.169.254."]


@pytest.mark.parametrize("host", NUMERIC_METADATA_FORMS)
def test_refuses_numeric_host_forms_that_reach_an_address(host):
    # getaddrinfo turns each of these into 169.254.169.254, and aiohttp treats
    # all-digit hosts as addresses and skips the resolver.
    with pytest.raises(IntegrationError, match="usual dotted form"):
        validate_health_url(f"http://{host}/latest")


@pytest.mark.parametrize("host", ["cafe.bead", "db.lan", "a1.example.com", "10x.example"])
def test_hex_looking_names_are_still_names(host):
    assert validate_health_url(f"http://{host}/") == f"http://{host}/"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", NUMERIC_METADATA_FORMS)
async def test_probe_refuses_numeric_host_forms_before_connecting(host, monkeypatch):
    loop = asyncio.get_running_loop()
    connect = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(loop, "create_connection", connect)
    result = await probe_health_url(f"http://{host}/latest")
    assert result["status"] == "down"
    assert result["detail"].startswith("Refused: ")
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_probe_refuses_blocked_literals_even_if_saved_elsewhere():
    result = await probe_health_url("http://169.254.169.254/latest/meta-data")
    assert result["status"] == "down"
    assert "metadata" in result["detail"]


@pytest.mark.asyncio
async def test_probe_reports_refused_connections_and_timeouts():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    refused = await probe_health_url(f"http://127.0.0.1:{port}/health")
    assert refused["status"] == "down"
    assert refused["detail"] in {"Connection refused", "Could not connect"}

    async def slow(request):
        await asyncio.sleep(1)
        return web.Response(text="late")

    runner, slow_port = await _serve(slow)
    try:
        timed_out = await probe_health_url(f"http://127.0.0.1:{slow_port}/", timeout=0.2)
    finally:
        await runner.cleanup()
    assert timed_out["status"] == "down"
    assert timed_out["detail"] == "No answer within 0.2 s"


# --- HTTP API ---------------------------------------------------------------------

@pytest.fixture()
def api(test_client, tmp_path, monkeypatch):
    import routers.integrations as integrations_router
    monkeypatch.setattr(integrations_router, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(integrations_router, "_store", None)
    probe = AsyncMock(return_value=_result())
    monkeypatch.setattr(ci, "probe_health_url", probe)
    test_client.probe = probe
    test_client.data_dir = tmp_path
    return test_client


def test_api_requires_the_dashboard_key(api):
    assert api.get("/api/integrations/custom").status_code == 401
    assert api.post("/api/integrations/custom", json={"name": "x", "url": "http://x/"}).status_code == 401
    assert api.delete("/api/integrations/custom/x").status_code == 401


def test_api_add_list_check_remove(api):
    headers = api.auth_headers
    empty = api.get("/api/integrations/custom", headers=headers)
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json()["integrations"] == []
    assert empty.json()["policy"] == {
        "maximum": 25, "staleAfterSeconds": 60, "timeoutSeconds": 5, "method": "GET",
        "followsRedirects": False, "sendsCredentials": False, "readsResponseBody": False,
    }

    created = api.post("/api/integrations/custom", headers=headers,
                       json={"name": "Jev", "url": "https://jev.example.com/health", "notes": "System 1"})
    assert created.status_code == 201
    assert created.json()["integration"]["check"]["status"] == "healthy"
    assert (api.data_dir / "integrations" / "custom.json").exists()

    listed = api.get("/api/integrations/custom", headers=headers).json()["integrations"]
    assert [(item["id"], item["name"], item["url"]) for item in listed] == [
        ("jev", "Jev", "https://jev.example.com/health")]

    checked = api.post("/api/integrations/custom/jev/check", headers=headers)
    assert checked.status_code == 200
    assert api.probe.await_count == 2

    assert api.delete("/api/integrations/custom/jev", headers=headers).status_code == 204
    assert api.delete("/api/integrations/custom/jev", headers=headers).status_code == 404
    assert api.post("/api/integrations/custom/jev/check", headers=headers).status_code == 404


@pytest.mark.parametrize("body, code, fragment", [
    ({"name": "Jev", "url": "https://jev.example.com/health?key=secret"}, 400, "query string"),
    ({"name": "Jev", "url": "http://169.254.169.254/"}, 400, "metadata"),
    ({"name": "", "url": "https://jev.example.com/"}, 400, "name"),
    ({"name": "Jev", "url": "https://jev.example.com/", "apiKey": "secret"}, 400, "only name, url and notes"),
    (["not", "an", "object"], 400, "only name, url and notes"),
])
def test_api_rejects_invalid_integrations(api, body, code, fragment):
    response = api.post("/api/integrations/custom", headers=api.auth_headers, json=body)
    assert response.status_code == code
    assert fragment in response.json()["detail"]
    assert "secret" not in response.text
    assert not (api.data_dir / "integrations" / "custom.json").exists()


def test_api_reports_duplicates_oversized_bodies_and_bad_refresh(api):
    headers = api.auth_headers
    body = {"name": "Jev", "url": "https://jev.example.com/health"}
    assert api.post("/api/integrations/custom", headers=headers, json=body).status_code == 201
    duplicate = api.post("/api/integrations/custom", headers=headers, json=body)
    assert duplicate.status_code == 409
    oversized = api.post("/api/integrations/custom", headers={**headers, "Content-Type": "application/json"},
                         content=json.dumps({"name": "x", "url": "http://x/", "notes": "n" * 5000}))
    assert oversized.status_code == 413
    assert api.get("/api/integrations/custom?refresh=always", headers=headers).status_code == 400


def test_api_reports_an_unreadable_list_without_overwriting_it(api):
    path = api.data_dir / "integrations" / "custom.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    response = api.get("/api/integrations/custom", headers=api.auth_headers)
    assert response.status_code == 503
    assert "custom.json" in response.json()["detail"]
    added = api.post("/api/integrations/custom", headers=api.auth_headers,
                     json={"name": "Jev", "url": "https://jev.example.com/health"})
    assert added.status_code == 503
    assert path.read_text() == "{broken"


def test_api_rejects_cross_site_changes(api):
    response = api.post("/api/integrations/custom", headers={
        **api.auth_headers, "Origin": "https://evil.invalid", "Sec-Fetch-Site": "cross-site"},
        json={"name": "Jev", "url": "https://jev.example.com/health"})
    assert response.status_code == 403
    assert api.probe.await_count == 0


# --- /api/status carries the service check time ----------------------------------

@pytest.mark.asyncio
async def test_api_status_reports_when_services_were_checked(monkeypatch):
    import helpers
    import main
    from models import BootstrapStatus

    monkeypatch.setattr("main.get_gpu_info", lambda: None)
    monkeypatch.setattr("main.get_model_info", lambda: None)
    monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
    monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={}))
    monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
    monkeypatch.setattr("main.get_uptime", lambda: 0)
    monkeypatch.setattr("main.get_cpu_metrics", lambda: {})
    monkeypatch.setattr("main.get_ram_metrics", lambda: {})
    monkeypatch.setattr(helpers, "_services_cache", None)
    monkeypatch.setattr(helpers, "_services_checked_at", None)
    monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))

    live = await main._build_api_status()
    assert live["servicesCheckedAt"].endswith("Z")

    helpers.set_services_cache([])
    stamped = helpers.get_services_checked_at()
    cached = await main._build_api_status()
    assert cached["servicesCheckedAt"] == stamped

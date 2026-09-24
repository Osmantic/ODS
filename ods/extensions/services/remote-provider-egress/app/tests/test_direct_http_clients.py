"""Regression tests for #2701 — direct_http_clients LRU eviction and bounded capacity.

These tests verify that app.state.direct_http_clients does not grow without bound,
evicts old clients when capacity is reached, and closes clients cleanly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

APP_MAIN = Path(__file__).resolve().parent.parent / "main.py"


def load_app_main():
    import importlib.util
    import sys
    import types

    # Inject stubs if not present
    if "remote_provider.policy" not in sys.modules:
        policy_mod = types.ModuleType("remote_provider.policy")
        policy_mod.DEFAULT_POLICY_PATH = Path("/dev/null")
        policy_mod.load_policy = lambda path=None: {}

        class PolicyError(ValueError):
            pass

        policy_mod.PolicyError = PolicyError
        sys.modules["remote_provider.policy"] = policy_mod

    if "remote_provider.egress" not in sys.modules:
        egress_mod = types.ModuleType("remote_provider.egress")
        egress_mod.DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024
        egress_mod.DEFAULT_SECRET_PATH = Path("/dev/null")

        class EgressError(Exception):
            def __init__(self, status: int, code: str, message: str) -> None:
                super().__init__(message)
                self.status = status
                self.code = code
                self.message = message

        egress_mod.EgressError = EgressError
        egress_mod.load_route_state = lambda path: {}
        egress_mod.prepare_upstream_request = lambda **kw: None
        egress_mod.provider_secret_status = lambda path: {}
        egress_mod.read_provider_secret = lambda path: ""
        egress_mod.route_from_state = lambda state, policy=None: {}
        egress_mod.validate_direct_provider_resolution = lambda route, **kw: []
        sys.modules["remote_provider.egress"] = egress_mod

    if "remote_provider.egress_probe" not in sys.modules:
        egress_probe_mod = types.ModuleType("remote_provider.egress_probe")
        egress_probe_mod.probe_route_response = lambda *args, **kw: {}
        sys.modules["remote_provider.egress_probe"] = egress_probe_mod

    if "remote_provider.probe" not in sys.modules:
        probe_mod = types.ModuleType("remote_provider.probe")
        probe_mod.DEFAULT_PROBE_TIMEOUT_SECONDS = 10.0

        class ProbeError(Exception):
            pass

        probe_mod.ProbeError = ProbeError
        sys.modules["remote_provider.probe"] = probe_mod

    if "remote_provider.ssh_supervisor" not in sys.modules:
        sys.modules["remote_provider.ssh_supervisor"] = types.ModuleType(
            "remote_provider.ssh_supervisor"
        )

    spec = importlib.util.spec_from_file_location("egress_main_2701", APP_MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_client_caches_and_reuses_active_client():
    """Requesting the same key returns the same active httpx.AsyncClient."""
    mod = load_app_main()
    mod.app.state.direct_http_clients = {}

    client1 = mod._http_client("https://provider1.example.com:443")
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

    client2 = mod._http_client("https://provider1.example.com:443")
    assert client2 is client1
    assert "https://provider1.example.com:443" in mod.app.state.direct_http_clients

    asyncio.run(client1.aclose())


def test_http_client_evicts_oldest_when_capacity_exceeded(monkeypatch):
    """Adding clients beyond MAX_DIRECT_CLIENTS evicts and closes the oldest client."""
    monkeypatch.setattr("os.environ", {"ODS_REMOTE_PROVIDER_MAX_DIRECT_CLIENTS": "2"})
    mod = load_app_main()
    mod.MAX_DIRECT_CLIENTS = 2
    mod.app.state.direct_http_clients = {}

    async def scenario():
        client_a = mod._http_client("https://a.com:443")
        client_b = mod._http_client("https://b.com:443")

        assert len(mod.app.state.direct_http_clients) == 2
        assert "https://a.com:443" in mod.app.state.direct_http_clients
        assert "https://b.com:443" in mod.app.state.direct_http_clients

        # Adding c should evict a (oldest)
        client_c = mod._http_client("https://c.com:443")

        assert len(mod.app.state.direct_http_clients) == 2
        assert "https://a.com:443" not in mod.app.state.direct_http_clients
        assert "https://b.com:443" in mod.app.state.direct_http_clients
        assert "https://c.com:443" in mod.app.state.direct_http_clients

        # Yield to event loop to allow task aclose to run
        await asyncio.sleep(0.01)
        assert client_a.is_closed

        # Clean up remaining
        await client_b.aclose()
        await client_c.aclose()

    asyncio.run(scenario())


def test_http_client_refreshes_lru_order_on_access():
    """Accessing an existing client moves it to the end of the LRU order."""
    mod = load_app_main()
    mod.MAX_DIRECT_CLIENTS = 2
    mod.app.state.direct_http_clients = {}

    async def scenario():
        client_a = mod._http_client("https://a.com:443")
        client_b = mod._http_client("https://b.com:443")

        # Accessing A again moves A to most recently used
        mod._http_client("https://a.com:443")

        # Adding C now evicts B (which is now the oldest)
        client_c = mod._http_client("https://c.com:443")

        assert "https://b.com:443" not in mod.app.state.direct_http_clients
        assert "https://a.com:443" in mod.app.state.direct_http_clients
        assert "https://c.com:443" in mod.app.state.direct_http_clients

        await asyncio.sleep(0.01)
        assert client_b.is_closed

        await client_a.aclose()
        await client_c.aclose()

    asyncio.run(scenario())


def test_shutdown_closes_all_direct_http_clients():
    """Calling _shutdown closes all remaining direct_http_clients and app.state.http."""
    mod = load_app_main()
    mod.app.state.http = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    mod.app.state.direct_http_clients = {}

    client1 = mod._http_client("https://p1.com:443")
    client2 = mod._http_client("https://p2.com:443")

    asyncio.run(mod._shutdown())

    assert mod.app.state.http.is_closed
    assert client1.is_closed
    assert client2.is_closed
    assert len(mod.app.state.direct_http_clients) == 0

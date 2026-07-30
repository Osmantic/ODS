"""Unit tests for the host agent's bind-address watchdog.

The agent binds ods-network's gateway once at startup. `ods stop` destroys the
compose networks and `ods start` recreates them, so the gateway can move
(e.g. 172.19.0.1 -> 172.18.0.1), leaving the agent listening where no container
can reach it. The watchdog (_watch_for_bind_address_change) must stop the server
so main()'s serve loop rebinds when the gateway moves, must NOT react while it
is stable, and must never die to a resolver error.

Run with: pytest tests/test-host-agent-rebind.py
"""
import importlib.util
import os
import threading
import time

import pytest

AGENT_PATH = os.path.join(os.path.dirname(__file__), "../bin/ods-host-agent.py")

spec = importlib.util.spec_from_file_location("ods_host_agent", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

CURRENT = "172.19.0.1"
MOVED = "172.18.0.1"


class Harness:
    """Runs the watchdog in a thread against a scripted resolver."""

    def __init__(self, monkeypatch, resolve):
        self.shutdown_calls = []
        monkeypatch.setattr(agent, "_REBIND_POLL_SECONDS", 0.01)
        monkeypatch.setattr(agent, "_resolve_agent_bind_addr", resolve)
        monkeypatch.setattr(
            agent,
            "_request_server_shutdown",
            lambda server, signum=None: self.shutdown_calls.append(server),
        )
        agent._SHUTTING_DOWN.clear()
        self.thread = threading.Thread(
            target=agent._watch_for_bind_address_change,
            args=("fake-server", {}, CURRENT),
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        agent._SHUTTING_DOWN.set()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive(), "watchdog must exit once shutdown is signalled"


@pytest.fixture(autouse=True)
def clean_shutdown_event():
    agent._SHUTTING_DOWN.clear()
    yield
    agent._SHUTTING_DOWN.set()


def test_stable_gateway_never_triggers_rebind(monkeypatch):
    h = Harness(monkeypatch, lambda env: CURRENT)
    time.sleep(0.15)  # ~15 polls
    assert h.thread.is_alive(), "watchdog must keep watching while the gateway is stable"
    assert h.shutdown_calls == []
    h.stop()


def test_moved_gateway_stops_server_for_rebind(monkeypatch):
    h = Harness(monkeypatch, lambda env: MOVED)
    h.thread.join(timeout=2)
    assert not h.thread.is_alive(), "watchdog must return after requesting a rebind"
    assert h.shutdown_calls == ["fake-server"]
    # A rebind is not a shutdown: the outer serve loop must be allowed to re-enter.
    assert not agent._SHUTTING_DOWN.is_set()


def test_resolver_error_does_not_kill_watchdog(monkeypatch):
    calls = {"n": 0}

    def flaky_then_moved(env):
        calls["n"] += 1
        if calls["n"] <= 5:
            raise OSError("docker network inspect failed")
        return MOVED

    h = Harness(monkeypatch, flaky_then_moved)
    h.thread.join(timeout=2)
    # The watchdog survived 5 resolver errors and still detected the move after.
    assert calls["n"] > 5
    assert h.shutdown_calls == ["fake-server"]


def test_empty_resolve_is_not_a_move(monkeypatch):
    h = Harness(monkeypatch, lambda env: "")
    time.sleep(0.15)
    assert h.thread.is_alive()
    assert h.shutdown_calls == [], "a failed/empty resolve must not trigger a rebind"
    h.stop()


def test_sigterm_wins_over_rebind_loop(monkeypatch):
    # SIGTERM's handler sets _SHUTTING_DOWN; the watchdog must exit without
    # requesting a rebind even if the gateway has moved.
    h = Harness(monkeypatch, lambda env: MOVED)
    agent._SHUTTING_DOWN.set()
    h.thread.join(timeout=2)
    assert not h.thread.is_alive()

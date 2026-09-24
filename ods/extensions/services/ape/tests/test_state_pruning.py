"""Governance state must stay bounded while the process runs.

main.py documents that window samples are "pruned on every load/save so the
on-disk and in-memory footprint stays bounded". _prune_state() was only ever
called from load_state(), so pruning happened once per restart: a scope that
stopped sending traffic kept its samples forever (its key is only revisited
when that same scope calls again) and the _MAX_* backstops never applied to a
long-running process. Every /verify then re-serialised the whole grown dict.
"""

import json
import time


POLICY = """
version: 1
intents: {ReadFile: {mode: allow}}
rate_limit: {requests_per_minute: 1000000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {limit: 1000, action: deny}
circuit_breaker: {enabled: false}
"""


def _seed_expired_scopes(main, count, now):
    """Record one window sample per scope, dated well outside every tier."""
    policy = main.load_policy()
    stale = now - 7 * 24 * 60 * 60
    for i in range(count):
        main.check_windowed_limits(policy, f"gone-{i}", "ReadFile", stale)


class TestWindowPruning:
    def test_expired_scopes_are_dropped_on_save(self, make_client, ape_env):
        _, main = make_client(policy_yaml=POLICY)
        now = time.time()
        _seed_expired_scopes(main, 200, now)

        main.save_state()

        assert main._state["windows"] == {}, (
            "expired window keys survived a save — state grows with every "
            "session that ever called /verify"
        )
        on_disk = json.loads(ape_env.state_file.read_text())
        assert on_disk["windows"] == {}

    def test_live_samples_are_kept(self, make_client):
        """Guard against over-pruning: in-window samples must still count."""
        _, main = make_client(policy_yaml=POLICY)
        now = time.time()
        policy = main.load_policy()
        for _ in range(3):
            main.check_windowed_limits(policy, "live", "ReadFile", now)

        main.save_state()

        assert main._state["windows"]["live|ReadFile"]["5min"] == [now, now, now]

    def test_state_file_stops_growing_with_dead_sessions(self, make_client, ape_env):
        _, main = make_client(policy_yaml=POLICY)
        now = time.time()

        _seed_expired_scopes(main, 100, now)
        main.save_state()
        first = ape_env.state_file.stat().st_size

        _seed_expired_scopes(main, 400, now)
        main.save_state()
        second = ape_env.state_file.stat().st_size

        assert second == first, (
            f"state.json grew from {first} to {second} bytes although every "
            "recorded sample had expired"
        )


class TestBackstopCaps:
    def test_pending_approval_cap_applies_without_a_restart(self, make_client, ape_env):
        _, main = make_client(policy_yaml=POLICY)
        cap = main._MAX_PENDING_APPROVALS
        now = time.time()
        with main._STATE_LOCK:
            for i in range(cap + 500):
                main._state["approvals"][f"tok-{i}"] = {"issued_at": now + i}

        main.save_state()

        assert len(main._state["approvals"]) == cap
        on_disk = json.loads(ape_env.state_file.read_text())
        assert len(on_disk["approvals"]) == cap
        # Oldest are dropped first, newest kept.
        assert "tok-0" not in on_disk["approvals"]
        assert f"tok-{cap + 499}" in on_disk["approvals"]

    def test_pending_grant_cap_applies_without_a_restart(self, make_client):
        _, main = make_client(policy_yaml=POLICY)
        cap = main._MAX_PENDING_GRANTS
        now = time.time()
        with main._STATE_LOCK:
            for i in range(cap + 200):
                main._state["grants"][f"g-{i}"] = {"granted_at": now + i}

        main.save_state()

        assert len(main._state["grants"]) == cap


class TestPruningPreservesLiveGovernance:
    def test_open_circuit_breaker_survives_a_save(self, make_client):
        """Pruning must not clear a breaker that is still in cooldown."""
        _, main = make_client(policy_yaml=POLICY)
        deadline = time.time() + 600
        with main._STATE_LOCK:
            main._state["breaker"]["tripped_until"] = deadline

        main.save_state()

        assert main._state["breaker"]["tripped_until"] == deadline

    def test_window_counter_still_survives_restart(self, make_client, ape_env):
        """The persistence contract from the governance suite must hold."""
        policy_yaml = POLICY.replace("limit: 1000", "limit: 2")
        client, _ = make_client(policy_yaml=policy_yaml)
        body = {"tool_name": "read_file", "args": {"path": "/tmp/x"}}
        headers = {"X-API-Key": ape_env.api_key}
        for _ in range(2):
            assert client.post("/verify", json=body, headers=headers).json()["allowed"] is True

        client2, _ = make_client(policy_yaml=policy_yaml)
        after = client2.post("/verify", json=body, headers=headers).json()
        assert after["allowed"] is False
        assert "5min" in after["reason"]

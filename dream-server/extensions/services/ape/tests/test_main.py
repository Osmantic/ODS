"""Tests for the APE service — issue #1269.

Covers the new persistent windowed governance + human-approval decision tier
while asserting the existing /verify /audit /policy /health /metrics contracts
and STRICT_MODE semantics are preserved.
"""

import concurrent.futures
import json

import pytest

# ── Helpers ─────────────────────────────────────────────────────────────────


def _verify(client, tool="read_file", args=None, session="s1"):
    return client.post("/verify", json={
        "tool_name": tool,
        "args": args or {"path": "/tmp/x"},
        "session_id": session,
    })


# A tiny policy with low limits so tests run fast.
LOW_LIMIT_POLICY = """
version: 1
intents:
  ReadFile: {mode: allow}
  WriteFile: {mode: allow}
  NetworkFetch: {mode: allow}
  SpawnAgent: {mode: allow}
  Other: {mode: allow}
rate_limit:
  requests_per_minute: 10000
windowed_limits:
  enabled: true
  default:
    5min: {limit: 1000, action: deny}
  intents:
    NetworkFetch:
      5min: {limit: 3, action: require_approval}
      hour: {limit: 5, action: deny}
    SpawnAgent:
      5min: {limit: 2, action: deny}
circuit_breaker:
  enabled: false
"""


# ── Existing endpoint contracts (regression guard) ──────────────────────────


def test_health_contract(make_client):
    client, _ = make_client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "strict_mode" in body
    assert "timestamp" in body


def test_verify_response_schema_backcompat(make_client):
    """Legacy fields must always be present and keep their meaning."""
    client, _ = make_client()
    r = _verify(client, tool="read_file")
    assert r.status_code == 200
    body = r.json()
    for key in ("allowed", "reason", "intent", "decision_id"):
        assert key in body, f"legacy field {key} missing"
    assert body["allowed"] is True
    assert body["intent"] == "ReadFile"
    # New optional fields are additive and default sanely.
    assert body["decision"] == "allow"
    assert body["approval_token"] is None


def test_policy_endpoint_contract(make_client):
    client, _ = make_client()
    r = client.get("/policy")
    assert r.status_code == 200
    body = r.json()
    for key in ("version", "intents", "rate_limit", "strict_mode"):
        assert key in body
    # Additive keys present too.
    assert "windowed_limits" in body
    assert "circuit_breaker" in body


def test_metrics_endpoint_contract(make_client):
    client, _ = make_client()
    _verify(client)
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "decisions" in body and "total" in body
    for key in ("allowed", "denied", "rate_limited"):
        assert key in body["decisions"]
    assert "pending_approvals" in body
    assert "circuit_breaker_open" in body


def test_audit_endpoint_contract(make_client):
    client, _ = make_client()
    _verify(client)
    r = client.get("/audit", params={"last_n": 5})
    assert r.status_code == 200
    body = r.json()
    assert "entries" in body
    assert isinstance(body["entries"], list)


def test_api_key_required(make_client):
    client, _ = make_client()
    r = client.post("/verify", headers={"X-API-Key": "wrong"},
                     json={"tool_name": "read_file", "args": {}})
    assert r.status_code == 401


# ── Windowed multi-tier caps ────────────────────────────────────────────────


def test_windowed_hard_deny_after_cap(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    # SpawnAgent 5min cap = 2, action deny.
    r1 = _verify(client, tool="spawn_agent", args={}, session="w1")
    r2 = _verify(client, tool="spawn_agent", args={}, session="w1")
    r3 = _verify(client, tool="spawn_agent", args={}, session="w1")
    assert r1.json()["allowed"] is True
    assert r2.json()["allowed"] is True
    assert r3.json()["allowed"] is False
    assert r3.json()["decision"] == "deny"
    assert "5min" in r3.json()["reason"]


def test_windowed_caps_are_per_intent_and_per_session(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    # Exhaust SpawnAgent for session A.
    _verify(client, tool="spawn_agent", args={}, session="A")
    _verify(client, tool="spawn_agent", args={}, session="A")
    blocked = _verify(client, tool="spawn_agent", args={}, session="A")
    assert blocked.json()["allowed"] is False
    # Different session is unaffected.
    other = _verify(client, tool="spawn_agent", args={}, session="B")
    assert other.json()["allowed"] is True
    # Different intent on the exhausted session is unaffected.
    read = _verify(client, tool="read_file", session="A")
    assert read.json()["allowed"] is True


def test_blocked_request_not_counted(make_client):
    """A blocked call must not consume window budget for the period."""
    client, main = make_client(policy_yaml=LOW_LIMIT_POLICY)
    for _ in range(2):
        _verify(client, tool="spawn_agent", args={}, session="nc")
    _verify(client, tool="spawn_agent", args={}, session="nc")  # denied
    key = "nc|SpawnAgent"
    samples = main._state["windows"][key]["5min"]
    assert len(samples) == 2  # the denied 3rd call was not recorded


# ── require_approval — third decision tier ──────────────────────────────────


def test_require_approval_tier(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    # NetworkFetch 5min cap = 3, action require_approval.
    for _ in range(3):
        ok = _verify(client, tool="web_fetch", args={"url": "http://x"},
                      session="appr")
        assert ok.json()["allowed"] is True
    r = _verify(client, tool="web_fetch", args={"url": "http://x"},
                session="appr")
    body = r.json()
    assert r.status_code == 200  # advisory escalation, NOT an error
    assert body["allowed"] is False
    assert body["decision"] == "require_approval"
    assert body["approval_token"] and body["approval_token"].startswith("appr_")


def test_require_approval_does_not_raise_in_strict_mode(make_client):
    client, _ = make_client(env={"APE_STRICT_MODE": "true"},
                            policy_yaml=LOW_LIMIT_POLICY)
    for _ in range(3):
        _verify(client, tool="web_fetch", args={"url": "http://x"}, session="st")
    r = _verify(client, tool="web_fetch", args={"url": "http://x"}, session="st")
    # require_approval is advisory: 200, not 403/429, even in strict mode.
    assert r.status_code == 200
    assert r.json()["decision"] == "require_approval"


def test_approve_endpoint_consumes_token(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    for _ in range(3):
        _verify(client, tool="web_fetch", args={"url": "http://x"}, session="ap2")
    r = _verify(client, tool="web_fetch", args={"url": "http://x"}, session="ap2")
    token = r.json()["approval_token"]

    g = client.post("/approve", json={"approval_token": token,
                                      "approver": "alice"})
    assert g.status_code == 200
    assert g.json()["granted"] is True
    assert g.json()["tool_name"] == "web_fetch"

    # Token is one-shot.
    g2 = client.post("/approve", json={"approval_token": token})
    assert g2.json()["granted"] is False


def test_approve_unknown_token(make_client):
    client, _ = make_client()
    g = client.post("/approve", json={"approval_token": "nope"})
    assert g.status_code == 200
    assert g.json()["granted"] is False


HARD_WINS_POLICY = """
version: 1
intents:
  NetworkFetch: {mode: allow}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    NetworkFetch:
      5min: {limit: 3, action: require_approval}
      hour: {limit: 3, action: deny}
circuit_breaker: {enabled: false}
"""


def test_hard_deny_wins_over_approval(make_client):
    """When a hard-deny tier and an approval tier trip in the same
    evaluation, the hard deny must win (deny is strictly stronger)."""
    client, _ = make_client(policy_yaml=HARD_WINS_POLICY)
    # Both tiers cap at 3; the 4th call trips 5min(approval) AND hour(deny)
    # simultaneously. deny must win.
    results = [_verify(client, tool="web_fetch", args={"url": "http://x"},
                       session="hd").json() for _ in range(4)]
    decisions = [r["decision"] for r in results]
    assert decisions[:3] == ["allow", "allow", "allow"]
    assert decisions[3] == "deny"
    assert results[3]["approval_token"] is None


# ── Persistence across restart ──────────────────────────────────────────────


def test_state_persists_across_restart(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    _verify(client, tool="spawn_agent", args={}, session="persist")
    _verify(client, tool="spawn_agent", args={}, session="persist")

    # Simulate a restart: brand-new app instance, reload state from disk.
    client2, main2 = make_client(policy_yaml=LOW_LIMIT_POLICY)
    # The 2 prior SpawnAgent calls survived → cap (2) already reached.
    r = _verify(client2, tool="spawn_agent", args={}, session="persist")
    assert r.json()["allowed"] is False
    assert r.json()["decision"] == "deny"


def test_corrupt_state_file_starts_clean(make_client, ape_env):
    ape_env.state_file.write_text("{not valid json")
    client, main = make_client(policy_yaml=LOW_LIMIT_POLICY)
    # Should not crash; state is empty and requests work.
    r = _verify(client, tool="read_file", session="corrupt")
    assert r.status_code == 200
    assert r.json()["allowed"] is True


def test_pending_approval_persists_across_restart(make_client):
    client, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    for _ in range(3):
        _verify(client, tool="web_fetch", args={"url": "http://x"}, session="pa")
    token = _verify(client, tool="web_fetch", args={"url": "http://x"},
                     session="pa").json()["approval_token"]

    client2, _ = make_client(policy_yaml=LOW_LIMIT_POLICY)
    g = client2.post("/approve", json={"approval_token": token})
    assert g.json()["granted"] is True


# ── Concurrency safety ──────────────────────────────────────────────────────


def test_concurrent_requests_do_not_corrupt_state(make_client):
    client, main = make_client(policy_yaml="""
version: 1
intents: {Other: {mode: allow}}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  default: {5min: {limit: 100000, action: deny}}
circuit_breaker: {enabled: false}
""")

    def hit(i):
        return client.post("/verify", json={
            "tool_name": "noop_tool",
            "args": {},
            "session_id": f"c{i % 4}",
        }).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(hit, range(200)))

    assert all(c == 200 for c in codes)
    # State file is still valid JSON and counts add up across 4 scopes.
    on_disk = json.loads(main._state and json.dumps(main._state))
    total = sum(
        len(s)
        for tiers in on_disk["windows"].values()
        for s in tiers.values()
    )
    assert total == 200


# ── Circuit breaker ─────────────────────────────────────────────────────────


CB_POLICY = """
version: 1
intents:
  ExecuteCommand: {mode: deny}
  Other: {mode: allow}
rate_limit: {requests_per_minute: 100000}
windowed_limits: {enabled: false}
circuit_breaker:
  enabled: true
  window_seconds: 300
  min_samples: 4
  deny_ratio: 0.5
  cooldown_seconds: 120
"""


def test_circuit_breaker_trips_and_blocks(make_client):
    client, main = make_client(policy_yaml=CB_POLICY)
    # Drive denials (ExecuteCommand is denied by policy) past the threshold.
    for _ in range(5):
        client.post("/verify", json={"tool_name": "exec",
                                      "args": {"command": "x"},
                                      "session_id": "cb"})
    # Breaker should now be open: even an otherwise-allowed call is denied.
    r = client.post("/verify", json={"tool_name": "noop", "args": {},
                                     "session_id": "cb"})
    assert r.json()["allowed"] is False
    assert "circuit breaker" in r.json()["reason"].lower()
    m = client.get("/metrics").json()
    assert m["circuit_breaker_open"] is True


def test_circuit_breaker_state_persists_across_restart(make_client):
    client, _ = make_client(policy_yaml=CB_POLICY)
    for _ in range(5):
        client.post("/verify", json={"tool_name": "exec",
                                      "args": {"command": "x"},
                                      "session_id": "cb2"})
    client2, _ = make_client(policy_yaml=CB_POLICY)
    r = client2.post("/verify", json={"tool_name": "noop", "args": {},
                                      "session_id": "cb2"})
    assert r.json()["allowed"] is False
    assert "circuit breaker" in r.json()["reason"].lower()


def test_circuit_breaker_strict_mode_raises_429(make_client):
    client, _ = make_client(env={"APE_STRICT_MODE": "true"},
                            policy_yaml=CB_POLICY)
    for _ in range(5):
        client.post("/verify", json={"tool_name": "exec",
                                     "args": {"command": "x"},
                                     "session_id": "cb3"})
    r = client.post("/verify", json={"tool_name": "noop", "args": {},
                                     "session_id": "cb3"})
    assert r.status_code == 429


# ── Warmup ──────────────────────────────────────────────────────────────────


def test_warmup_relaxes_limits(make_client):
    client, _ = make_client(env={"APE_WARMUP_SECONDS": "3600"},
                            policy_yaml=LOW_LIMIT_POLICY)
    # SpawnAgent cap is 2, but during warmup windowed limits are skipped.
    for _ in range(6):
        r = _verify(client, tool="spawn_agent", args={}, session="warm")
        assert r.json()["allowed"] is True
        assert r.json()["decision"] == "allow"


# ── STRICT_MODE invariants for existing paths ───────────────────────────────


def test_strict_mode_policy_deny_still_raises_403(make_client):
    client, _ = make_client(env={"APE_STRICT_MODE": "true"}, policy_yaml="""
version: 1
intents:
  ExecuteCommand: {mode: deny}
rate_limit: {requests_per_minute: 100000}
windowed_limits: {enabled: false}
circuit_breaker: {enabled: false}
""")
    r = client.post("/verify", json={"tool_name": "exec",
                                     "args": {"command": "ls"},
                                     "session_id": "sd"})
    assert r.status_code == 403


def test_strict_mode_windowed_hard_deny_raises_403(make_client):
    """A windowed hard-deny is a policy denial → 403 in strict mode."""
    client, _ = make_client(env={"APE_STRICT_MODE": "true"},
                            policy_yaml=LOW_LIMIT_POLICY)
    _verify(client, tool="spawn_agent", args={}, session="swd")
    _verify(client, tool="spawn_agent", args={}, session="swd")
    r = _verify(client, tool="spawn_agent", args={}, session="swd")
    assert r.status_code == 403

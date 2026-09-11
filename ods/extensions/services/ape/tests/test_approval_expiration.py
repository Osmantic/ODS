"""Exercise expiry through authenticated policy decisions, including restart."""
import pytest

POLICY = """
version: 1
intents:
  ReadFile: {mode: allow}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      day: {limit: 1, action: require_approval}
circuit_breaker: {enabled: false}
"""
ACTION = {"tool_name": "read_file", "args": {"path": "/x"}, "session_id": "expiry"}


def escalate(client):
    assert client.post("/verify", json=ACTION).json()["allowed"] is True
    return client.post("/verify", json=ACTION).json()["approval_token"]


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("age, granted", [(899, True), (900, False)])
def test_pending_approval_expiration(make_client, monkeypatch, restart, age, granted):
    client, app = make_client(policy_yaml=POLICY)
    clock = [2_000_000_000.0]
    monkeypatch.setattr(app.time, "time", lambda: clock[0])
    token = escalate(client)
    clock[0] += age
    if restart:
        client, app = make_client()
    result = client.post("/approve", json={"approval_token": token})
    assert result.status_code == 200
    assert result.json()["granted"] is granted
    assert client.post("/verify", json=ACTION).json()["allowed"] is granted


@pytest.mark.parametrize("age, allowed", [(899, True), (900, False)])
@pytest.mark.parametrize("restart", [False, True])
def test_unused_grant_expires_without_renewal(make_client, monkeypatch, age, allowed, restart):
    client, app = make_client(policy_yaml=POLICY)
    clock = [2_000_000_000.0]
    monkeypatch.setattr(app.time, "time", lambda: clock[0])
    token = escalate(client)
    assert client.post("/approve", json={"approval_token": token}).json()["granted"] is True
    clock[0] += age
    if restart:
        client, app = make_client()
    result = client.post("/verify", json=ACTION)
    assert result.status_code == 200
    assert result.json()["allowed"] is allowed
    if allowed:
        assert client.post("/verify", json=ACTION).json()["decision"] == "require_approval"

"""Task #10 contract-conformance suite for issue #1269.

This is the swarm test-engineer's independent verification that the APE
windowed-governance fix satisfies the *exact* acceptance criteria in the
task brief, expressed as black-box behavior through the public /verify,
/approve and /policy API (no reliance on private internals):

  1. Window cap is enforced across the 5min / hour / day tiers.
  2. Persisted governance state survives a simulated process restart.
  3. ``require_approval`` is surfaced (third decision tier, with token).
  4. The existing /verify schema is unchanged for old clients (the four
     legacy fields keep their names, types and meaning; additions are
     optional and default-safe).
  5. STRICT_MODE is intact (policy denials still 403; hard windowed denials
     still raise; require_approval deliberately does NOT raise).

It reuses the fixer's conftest fixtures (``make_client``) rather than
re-implementing the harness, per the repo's shared-factory convention.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A read tool is always policy-allowed, so windowed limits are what gate it —
# isolating the windowing behavior from allowlist/path-guard logic.
READ = {"tool_name": "read_file", "args": {"path": "/x"}}
EXEC_BAD = {"tool_name": "exec", "args": {"command": "/usr/bin/danger"}}


def _verify(client, payload, session="s1"):
    body = dict(payload)
    body.setdefault("session_id", session)
    return client.post("/verify", json=body)


# ── 1. Window cap enforced across 5min / hour / day tiers ──────────────────

class TestWindowTiersEnforced:
    def _policy(self, five, hour, day, action="deny"):
        return f"""
version: 1
intents:
  ReadFile: {{mode: allow}}
rate_limit: {{requests_per_minute: 100000}}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {{limit: {five}, action: {action}}}
      "hour": {{limit: {hour}, action: {action}}}
      "day":  {{limit: {day},  action: {action}}}
circuit_breaker: {{enabled: false}}
"""

    def test_5min_tier_denies_at_cap(self, make_client):
        client, _ = make_client(policy_yaml=self._policy(3, 999, 999))
        for _ in range(3):
            assert _verify(client, READ).json()["allowed"] is True
        blocked = _verify(client, READ).json()
        assert blocked["allowed"] is False
        assert "5min" in blocked["reason"]

    def test_hour_tier_denies_when_5min_is_generous(self, make_client):
        # 5min cap huge, hour cap small → the hour tier must be the gate.
        client, _ = make_client(policy_yaml=self._policy(10000, 4, 10000))
        for _ in range(4):
            assert _verify(client, READ).json()["allowed"] is True
        blocked = _verify(client, READ).json()
        assert blocked["allowed"] is False
        assert "hour" in blocked["reason"]

    def test_day_tier_denies_when_shorter_tiers_generous(self, make_client):
        client, _ = make_client(policy_yaml=self._policy(10000, 10000, 5))
        for _ in range(5):
            assert _verify(client, READ).json()["allowed"] is True
        blocked = _verify(client, READ).json()
        assert blocked["allowed"] is False
        assert "day" in blocked["reason"]

    def test_caps_are_independent_per_session(self, make_client):
        client, _ = make_client(policy_yaml=self._policy(2, 999, 999))
        for _ in range(2):
            _verify(client, READ, session="alice")
        assert _verify(client, READ, session="alice").json()["allowed"] is False
        # A different session is unaffected by alice's exhausted window.
        assert _verify(client, READ, session="bob").json()["allowed"] is True


# ── 2. Persisted state survives a simulated process restart ────────────────

class TestStatePersistence:
    POLICY = """
version: 1
intents: {ReadFile: {mode: allow}}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {limit: 4, action: deny}
circuit_breaker: {enabled: false}
"""

    def test_window_counter_survives_restart(self, make_client, ape_env):
        client, _ = make_client(policy_yaml=self.POLICY)
        for _ in range(4):
            assert _verify(client, READ).json()["allowed"] is True
        # State file must now exist on the /data/ape volume.
        assert ape_env.state_file.exists(), "state.json not persisted"

        # Simulate a container restart: brand-new app + reload from disk.
        client2, _ = make_client(policy_yaml=self.POLICY)
        after = _verify(client2, READ).json()
        assert after["allowed"] is False, (
            "window counter reset on restart — state did not survive"
        )
        assert "5min" in after["reason"]

    def test_corrupt_state_starts_clean_not_crash(self, make_client, ape_env):
        client, _ = make_client(policy_yaml=self.POLICY)
        _verify(client, READ)
        ape_env.state_file.write_text("{ this is not valid json ")
        # Restart with a corrupt state file must not crash the service.
        client2, _ = make_client(policy_yaml=self.POLICY)
        assert client2.get("/health").status_code == 200
        assert _verify(client2, READ).json()["allowed"] is True


# ── 3. require_approval is surfaced ────────────────────────────────────────

class TestRequireApprovalSurfaced:
    POLICY = """
version: 1
intents: {ReadFile: {mode: allow}}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {limit: 2, action: require_approval}
circuit_breaker: {enabled: false}
"""

    def test_require_approval_decision_and_token(self, make_client):
        client, _ = make_client(policy_yaml=self.POLICY)
        for _ in range(2):
            assert _verify(client, READ).json()["decision"] == "allow"
        r = _verify(client, READ).json()
        assert r["decision"] == "require_approval", (
            "approval tier not surfaced as a distinct decision"
        )
        assert r["allowed"] is False  # not auto-allowed
        assert r["approval_token"], "no approval_token issued for require_approval"

    def test_approve_endpoint_consumes_token_once(self, make_client):
        client, _ = make_client(policy_yaml=self.POLICY)
        for _ in range(2):
            _verify(client, READ)
        tok = _verify(client, READ).json()["approval_token"]
        ok = client.post("/approve", json={"approval_token": tok,
                                           "approver": "human@x"})
        assert ok.status_code == 200 and ok.json()["granted"] is True
        # One-shot: a second grant of the same token must fail.
        again = client.post("/approve", json={"approval_token": tok})
        assert again.json()["granted"] is False

    def test_pending_approval_survives_restart(self, make_client):
        client, _ = make_client(policy_yaml=self.POLICY)
        for _ in range(2):
            _verify(client, READ)
        tok = _verify(client, READ).json()["approval_token"]
        client2, _ = make_client(policy_yaml=self.POLICY)
        granted = client2.post("/approve", json={"approval_token": tok})
        assert granted.json()["granted"] is True, (
            "pending approval token lost across restart"
        )


# ── 4. /verify schema unchanged for old clients ────────────────────────────

class TestLegacySchemaUnchanged:
    def test_legacy_fields_present_and_typed(self, make_client):
        client, _ = make_client()
        r = _verify(client, READ).json()
        # The four legacy fields must always be present with their old types.
        assert isinstance(r["allowed"], bool)
        assert isinstance(r["reason"], str)
        assert isinstance(r["intent"], str)
        assert isinstance(r["decision_id"], str) and r["decision_id"]
        # Additions are optional + default-safe for clients that ignore them.
        assert r.get("decision") in ("allow", "deny", "require_approval")
        # approval_token is null unless escalated — never breaks old parsers.
        if r["decision"] != "require_approval":
            assert r.get("approval_token") in (None, "")

    def test_old_client_request_shape_still_accepted(self, make_client):
        """A pre-#1269 client sends only tool_name/args — must still work."""
        client, _ = make_client()
        r = client.post("/verify", json={"tool_name": "read_file",
                                         "args": {"path": "/etc/hostname"}})
        assert r.status_code == 200
        body = r.json()
        assert set(["allowed", "reason", "intent", "decision_id"]).issubset(body)

    def test_policy_endpoint_advertises_windowing_without_breaking(self, make_client):
        client, _ = make_client()
        p = client.get("/policy").json()
        # Legacy keys still present.
        assert "version" in p and "intents" in p and "rate_limit" in p
        assert "strict_mode" in p
        # New governance surfaced additively.
        assert "windowed_limits" in p


# ── 5. STRICT_MODE intact ──────────────────────────────────────────────────

class TestStrictModeIntact:
    def test_policy_deny_still_raises_403(self, make_client):
        client, _ = make_client(env={"APE_STRICT_MODE": "true"})
        # exec of a non-allowlisted command is a hard policy deny.
        r = client.post("/verify", json={**EXEC_BAD, "session_id": "z"})
        assert r.status_code == 403, (
            f"STRICT_MODE policy deny did not 403 (got {r.status_code})"
        )

    def test_hard_windowed_deny_raises_in_strict_mode(self, make_client):
        policy = """
version: 1
intents: {ReadFile: {mode: allow}}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {limit: 2, action: deny}
circuit_breaker: {enabled: false}
"""
        client, _ = make_client(env={"APE_STRICT_MODE": "true"},
                                policy_yaml=policy)
        for _ in range(2):
            assert _verify(client, READ).status_code == 200
        blocked = _verify(client, READ)
        assert blocked.status_code in (403, 429), (
            f"hard windowed deny not enforced in STRICT_MODE "
            f"(got {blocked.status_code})"
        )

    def test_require_approval_does_not_raise_in_strict_mode(self, make_client):
        policy = """
version: 1
intents: {ReadFile: {mode: allow}}
rate_limit: {requests_per_minute: 100000}
windowed_limits:
  enabled: true
  intents:
    ReadFile:
      "5min": {limit: 1, action: require_approval}
circuit_breaker: {enabled: false}
"""
        client, _ = make_client(env={"APE_STRICT_MODE": "true"},
                                policy_yaml=policy)
        assert _verify(client, READ).status_code == 200
        escalated = _verify(client, READ)
        # require_approval is advisory: must return 200 so the agent
        # framework can route to a human, NOT raise like a hard deny.
        assert escalated.status_code == 200, (
            "require_approval incorrectly raised in STRICT_MODE"
        )
        assert escalated.json()["decision"] == "require_approval"

    def test_strict_mode_reflected_in_health_and_policy(self, make_client):
        client, _ = make_client(env={"APE_STRICT_MODE": "true"})
        assert client.get("/health").json()["strict_mode"] is True
        assert client.get("/policy").json()["strict_mode"] is True

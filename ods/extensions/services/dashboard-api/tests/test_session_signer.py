"""Tests for session_signer — HMAC-signed ods-session cookies."""

import time

import pytest

import session_signer


@pytest.fixture(autouse=True)
def _set_secret():
    """Install a known test secret before each test, restore empty after."""
    session_signer._set_secret_for_tests("test-secret-do-not-use-in-prod")
    yield
    session_signer._set_secret_for_tests("")


# ---------------------------------------------------------------------------
# issue()
# ---------------------------------------------------------------------------


class TestIssue:

    def test_returns_three_dot_separated_pieces(self):
        cookie = session_signer.issue(ttl_seconds=60)
        parts = cookie.split(".")
        assert len(parts) == 3
        assert all(parts), f"empty piece in {cookie}"

    def test_random_id_is_url_safe(self):
        cookie = session_signer.issue(ttl_seconds=60)
        random_id, _, _ = cookie.split(".")
        # token_urlsafe → only A-Z, a-z, 0-9, _, -
        import re
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", random_id), random_id

    def test_expiry_is_in_the_future(self):
        before = int(time.time())
        cookie = session_signer.issue(ttl_seconds=60)
        after = int(time.time())
        _, exp_str, _ = cookie.split(".")
        exp = int(exp_str)
        assert before + 60 <= exp <= after + 60

    def test_two_calls_have_different_random_ids(self):
        c1 = session_signer.issue(ttl_seconds=60)
        c2 = session_signer.issue(ttl_seconds=60)
        assert c1.split(".")[0] != c2.split(".")[0]

    @pytest.mark.parametrize("scope", ["owner", "guest", "admin"])
    def test_scoped_cookie_roundtrip(self, scope):
        cookie = session_signer.issue_scoped(scope, ttl_seconds=60)
        assert len(cookie.split(".")) == 5
        ok, reason, claims = session_signer.verify_scoped(cookie)
        assert (ok, reason) == (True, "ok")
        assert claims is not None
        assert claims.scope == scope
        assert claims.version == "v2"

    def test_issue_scoped_rejects_unknown_scope(self):
        with pytest.raises(ValueError, match="unsupported session scope"):
            session_signer.issue_scoped("superuser", ttl_seconds=60)


# ---------------------------------------------------------------------------
# is_configured()
# ---------------------------------------------------------------------------


class TestIsConfigured:
    """Pre-flight probe callers use before committing irreversible state."""

    def test_true_when_secret_set(self):
        # The autouse fixture has already set a test secret.
        assert session_signer.is_configured() is True

    def test_false_when_secret_unset(self):
        session_signer._set_secret_for_tests("")
        assert session_signer.is_configured() is False

    def test_false_after_setting_empty_string(self):
        session_signer._set_secret_for_tests("real-secret")
        assert session_signer.is_configured() is True
        session_signer._set_secret_for_tests("")
        assert session_signer.is_configured() is False

    def test_raises_without_secret(self):
        session_signer._set_secret_for_tests("")
        with pytest.raises(RuntimeError, match="ODS_SESSION_SECRET"):
            session_signer.issue(ttl_seconds=60)

    def test_rejects_zero_or_negative_ttl(self):
        with pytest.raises(ValueError):
            session_signer.issue(ttl_seconds=0)
        with pytest.raises(ValueError):
            session_signer.issue(ttl_seconds=-1)


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


class TestVerify:

    def test_roundtrip_ok(self):
        cookie = session_signer.issue(ttl_seconds=60)
        ok, reason = session_signer.verify(cookie)
        assert ok is True
        assert reason == "ok"

    def test_legacy_cookie_is_compatible_but_not_owner(self):
        cookie = session_signer.issue(ttl_seconds=60)
        ok, reason, claims = session_signer.verify_scoped(cookie)
        assert (ok, reason) == (True, "ok")
        assert claims is not None and claims.scope == "legacy"
        assert session_signer.owner_approval_identity(cookie) is None

    def test_scope_tampering_invalidates_signature(self):
        cookie = session_signer.issue_scoped("owner", ttl_seconds=60)
        version, _, random_id, expiry, signature = cookie.split(".")
        tampered = f"{version}.admin.{random_id}.{expiry}.{signature}"
        ok, reason, claims = session_signer.verify_scoped(tampered)
        assert (ok, reason, claims) == (False, "bad-signature", None)

    def test_only_owner_scope_yields_hashed_approval_identity(self):
        owner = session_signer.issue_scoped("owner", ttl_seconds=60)
        guest = session_signer.issue_scoped("guest", ttl_seconds=60)
        admin = session_signer.issue_scoped("admin", ttl_seconds=60)
        owner_id = session_signer.owner_approval_identity(owner)
        raw_session_id = owner.split(".")[2]

        assert owner_id is not None and owner_id.startswith("owner-")
        assert raw_session_id not in owner_id
        assert session_signer.owner_approval_identity(owner) == owner_id
        assert session_signer.owner_approval_identity(guest) is None
        assert session_signer.owner_approval_identity(admin) is None

    def test_empty_string(self):
        ok, reason = session_signer.verify("")
        assert ok is False
        assert reason == "malformed"

    def test_none_input(self):
        ok, reason = session_signer.verify(None)
        assert ok is False
        assert reason == "malformed"

    def test_wrong_number_of_parts(self):
        for bad in ["only-one-piece", "two.pieces", "four.pieces.here.now"]:
            ok, reason = session_signer.verify(bad)
            assert ok is False, f"expected reject for {bad!r}"
            assert reason == "malformed"

    def test_oversized_cookie_is_rejected_before_hmac(self, monkeypatch):
        def unexpected_sign(_payload):
            raise AssertionError("Oversized cookie reached HMAC")

        monkeypatch.setattr(session_signer, "_sign", unexpected_sign)
        ok, reason = session_signer.verify("a" * 1025)
        assert (ok, reason) == (False, "malformed")

    def test_empty_subpart(self):
        ok, reason = session_signer.verify("..")
        assert ok is False
        assert reason == "malformed"

    def test_bad_signature(self):
        cookie = session_signer.issue(ttl_seconds=60)
        random_id, expiry, _ = cookie.split(".")
        tampered = f"{random_id}.{expiry}.bogus-signature-value"
        ok, reason = session_signer.verify(tampered)
        assert ok is False
        assert reason == "bad-signature"

    def test_non_ascii_signature_is_rejected_not_raised(self):
        """A cookie whose signature holds non-ASCII bytes must come back as
        bad-signature. Cookie headers decode as latin-1, so a client can put
        any byte here, and verify() promises a reason rather than raising."""
        cookie = session_signer.issue(ttl_seconds=60)
        random_id, expiry, _ = cookie.split(".")
        tampered = f"{random_id}.{expiry}.\xe9\xff"
        ok, reason = session_signer.verify(tampered)
        assert ok is False
        assert reason == "bad-signature"

    def test_extended_expiry_invalidates_signature(self):
        """An attacker who tries to extend a leaked cookie's lifetime by
        editing the expiry field breaks the signature."""
        cookie = session_signer.issue(ttl_seconds=60)
        random_id, _, sig = cookie.split(".")
        # Extend by an hour by editing the timestamp field.
        future = int(time.time()) + 3600
        tampered = f"{random_id}.{future}.{sig}"
        ok, reason = session_signer.verify(tampered)
        assert ok is False
        assert reason == "bad-signature"

    def test_expired_cookie(self):
        """A cookie issued with TTL=1 and then waited out should fail with
        reason='expired' (NOT bad-signature — the signature is still valid)."""
        cookie = session_signer.issue(ttl_seconds=1)
        time.sleep(1.1)
        ok, reason = session_signer.verify(cookie)
        assert ok is False
        assert reason == "expired"

    def test_cookie_signed_with_different_secret_rejected(self):
        """Rotating the secret invalidates all existing cookies (operator
        recovery path when a cookie leaks)."""
        cookie = session_signer.issue(ttl_seconds=60)
        session_signer._set_secret_for_tests("a-different-secret")
        ok, reason = session_signer.verify(cookie)
        assert ok is False
        assert reason == "bad-signature"

    def test_verify_returns_no_secret_when_unset(self):
        session_signer._set_secret_for_tests("")
        ok, reason = session_signer.verify("anything.anything.anything")
        assert ok is False
        assert reason == "no-secret"

    def test_non_integer_expiry(self):
        """A malformed expiry field that survives split should be caught."""
        # Sign a payload with a non-integer expiry. Signature will be valid
        # but the int() parse will fail.
        random_id = "abc123"
        bad_expiry = "not-a-number"
        payload = f"{random_id}.{bad_expiry}"
        sig = session_signer._sign(payload)
        cookie = f"{payload}.{sig}"
        ok, reason = session_signer.verify(cookie)
        assert ok is False
        assert reason == "malformed"
def test_malformed_expiry_does_not_compute_hmac(monkeypatch):
    def unexpected_sign(_payload):
        raise AssertionError("Malformed timestamp reached HMAC")
    monkeypatch.setattr(session_signer, "_sign", unexpected_sign)
    for expiry in ("not-a-number", "9" * 5000):
        assert session_signer.verify(f"id.{expiry}.signature") == (False, "malformed")


def test_parseable_expiry_still_requires_a_signature():
    future = int(time.time()) + 3600
    assert session_signer.verify(f"id.{future}.forged") == (False, "bad-signature")
    assert session_signer.verify("id.1.forged") == (False, "bad-signature")

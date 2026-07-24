"""Tests for routers/auth.py — session verification & admin session minting."""


def test_verify_session_requires_cookie(test_client):
    resp = test_client.get("/api/auth/verify-session")
    assert resp.status_code == 401


def test_cookie_domain_strips_matched_quotes(monkeypatch):
    import routers.auth as auth_router

    monkeypatch.setenv("ODS_COOKIE_DOMAIN", '"example.local"')
    assert auth_router._cookie_domain() == "example.local"

    monkeypatch.setenv("ODS_COOKIE_DOMAIN", "'sub.example.local'")
    assert auth_router._cookie_domain() == "sub.example.local"

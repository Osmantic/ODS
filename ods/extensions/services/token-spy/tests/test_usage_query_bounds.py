"""HTTP bounds for Token Spy's recent-usage queries."""

from __future__ import annotations

import importlib
import os

from fastapi.testclient import TestClient

os.environ.setdefault("TOKEN_SPY_API_KEY", "token-spy-test-key")

main = importlib.import_module("main")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {main.TOKEN_SPY_API_KEY}"}


def test_usage_routes_reject_unbounded_query_ranges(monkeypatch):
    monkeypatch.setattr(
        main,
        "query_usage",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid query reached the database")
        ),
    )
    client = TestClient(main.app)

    for path in ("/api/usage", "/token-usage"):
        for query in (
            "limit=-1",
            "limit=0",
            "limit=1001",
            "hours=-1",
            "hours=0",
            "hours=8785",
        ):
            response = client.get(f"{path}?{query}", headers=_headers())
            assert response.status_code == 422, (path, query, response.text)


def test_usage_routes_accept_documented_defaults_and_bounds(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        main,
        "query_usage",
        lambda **kwargs: calls.append(kwargs) or [],
    )
    client = TestClient(main.app)

    assert client.get("/api/usage", headers=_headers()).status_code == 200
    assert client.get(
        "/token-usage?agent=hermes&hours=8784&limit=1000",
        headers=_headers(),
    ).status_code == 200
    assert calls == [
        {"agent": None, "hours": 24, "limit": 200},
        {"agent": "hermes", "hours": 8784, "limit": 1000},
    ]

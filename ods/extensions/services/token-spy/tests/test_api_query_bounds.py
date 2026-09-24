"""Public query-boundary tests for Token Spy usage endpoints."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture()
def token_spy(monkeypatch):
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "query-boundary-key")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))
    spec = importlib.util.spec_from_file_location(
        f"token_spy_query_bounds_{uuid4().hex}",
        TOKEN_SPY_DIR / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.query_usage = Mock(return_value=[])
    module.query_summary = Mock(return_value=[])
    return module


@pytest.mark.parametrize("path", ["/api/usage", "/token-usage"])
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"hours": 0}, "hours"),
        ({"hours": 8761}, "hours"),
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
    ],
)
def test_usage_endpoints_reject_out_of_range_queries(token_spy, path, params, field):
    client = TestClient(token_spy.app)

    response = client.get(
        path,
        params=params,
        headers={"Authorization": "Bearer query-boundary-key"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field
    token_spy.query_usage.assert_not_called()


@pytest.mark.parametrize("hours", [0, 8761])
def test_summary_rejects_out_of_range_hours(token_spy, hours):
    client = TestClient(token_spy.app)

    response = client.get(
        "/api/summary",
        params={"hours": hours},
        headers={"Authorization": "Bearer query-boundary-key"},
    )

    assert response.status_code == 422
    token_spy.query_summary.assert_not_called()


def test_usage_boundary_values_reach_database(token_spy):
    client = TestClient(token_spy.app)

    response = client.get(
        "/api/usage",
        params={"hours": 8760, "limit": 1000},
        headers={"Authorization": "Bearer query-boundary-key"},
    )

    assert response.status_code == 200
    token_spy.query_usage.assert_called_once_with(
        agent=None,
        hours=8760,
        limit=1000,
    )

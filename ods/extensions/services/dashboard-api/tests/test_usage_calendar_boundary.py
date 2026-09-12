"""Valid inclusive report ranges must not step past Python's last date."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.parametrize("start, days", [("9999-12-31", 1), ("9999-12-30", 2), ("9999-01-01", 365)])
def test_report_includes_last_calendar_day(test_client, monkeypatch, start, days):
    import routers.usage as usage

    monkeypatch.setattr(usage, "TOKEN_SPY_URL", "")
    monkeypatch.setattr(usage, "_fetch_local_runtime_counters", AsyncMock(return_value=[]))
    response = test_client.get(
        f"/api/usage/report?start={start}&end=9999-12-31",
        headers=test_client.auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["daily"]) == days
    assert payload["daily"][0]["date"] == start
    assert payload["daily"][-1]["date"] == "9999-12-31"
    assert payload["source"]["status"] == "unavailable"

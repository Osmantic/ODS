"""Public metrics must not make an upstream outage look like fresh activity."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def clock_at(monkeypatch, monitor):
    clock = SimpleNamespace(now=datetime(2026, 5, 1, tzinfo=timezone.utc))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(monitor, "datetime", Clock)
    return clock


@pytest.mark.parametrize("endpoint", ["/api/agents/throughput", "/api/agents/metrics"])
def test_expired_samples_leave_public_statistics_without_new_samples(
    test_client, monkeypatch, endpoint,
):
    import agent_monitor as monitor

    clock = clock_at(monkeypatch, monitor)
    monkeypatch.setattr(monitor.throughput, "data_points", [])
    monkeypatch.setattr(monitor.throughput, "history_minutes", 15)
    monitor.throughput.add_sample(90)
    clock.now += timedelta(minutes=10)
    monitor.throughput.add_sample(20)
    clock.now += timedelta(minutes=10)

    def stats():
        response = test_client.get(endpoint, headers=test_client.auth_headers)
        assert response.status_code == 200
        data = response.json()
        return data["throughput"] if "throughput" in data else data

    current = stats()
    assert current["current"] == current["average"] == current["peak"] == 20
    assert len(current["history"]) == 1
    clock.now += timedelta(minutes=5)
    assert stats() == {"current": 0, "average": 0, "peak": 0, "history": []}


def test_failed_collection_does_not_refresh_last_success_time(test_client, monkeypatch):
    import agent_monitor as monitor

    clock = clock_at(monkeypatch, monitor)
    monkeypatch.setattr(monitor.agent_metrics, "last_update", clock.now)
    monkeypatch.setattr(monitor.agent_metrics, "session_count", 0)
    monkeypatch.setattr(monitor.throughput, "data_points", [])
    monkeypatch.setattr(monitor.cluster_status, "refresh", AsyncMock())
    monkeypatch.setattr(monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value=[{"total_output_tokens": 86400}])
    session = MagicMock()
    session.__aenter__.return_value = session
    session.get.return_value.__aenter__.return_value = response
    monkeypatch.setattr(monitor.aiohttp, "ClientSession", lambda **_kwargs: session)

    async def one_collection():
        async def stop_after_interval(delay):
            assert delay == 5
            raise asyncio.CancelledError
        with monkeypatch.context() as context:
            context.setattr(monitor.asyncio, "sleep", stop_after_interval)
            with pytest.raises(asyncio.CancelledError):
                await monitor.collect_metrics()

    clock.now += timedelta(minutes=1)
    asyncio.run(one_collection())
    successful_at = clock.now.isoformat()
    response.status = 503
    clock.now += timedelta(minutes=1)
    asyncio.run(one_collection())

    result = test_client.get("/api/agents/metrics", headers=test_client.auth_headers)
    assert result.status_code == 200
    assert result.json()["agent"]["last_update"] == successful_at
    assert result.json()["agent"]["session_count"] == 1

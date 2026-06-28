"""Tests for agent_monitor.py — throughput metrics and data classes."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from agent_monitor import ThroughputMetrics, AgentMetrics, ClusterStatus
import agent_monitor


class TestThroughputMetrics:

    def test_empty_stats(self):
        tm = ThroughputMetrics()
        stats = tm.get_stats()
        assert stats["current"] == 0
        assert stats["average"] == 0
        assert stats["peak"] == 0
        assert stats["history"] == []

    def test_add_sample_updates_stats(self):
        tm = ThroughputMetrics()
        tm.add_sample(10.0)
        tm.add_sample(20.0)
        tm.add_sample(30.0)

        stats = tm.get_stats()
        assert stats["current"] == 30.0
        assert stats["average"] == 20.0
        assert stats["peak"] == 30.0
        assert len(stats["history"]) == 3

    def test_prunes_old_data(self):
        tm = ThroughputMetrics(history_minutes=5)

        # Insert an old data point by manipulating the list directly
        old_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        tm.data_points.append({"timestamp": old_time, "tokens_per_sec": 99.0})

        # Adding a new sample triggers pruning
        tm.add_sample(10.0)

        assert len(tm.data_points) == 1
        assert tm.data_points[0]["tokens_per_sec"] == 10.0

    def test_history_capped_at_30_points(self):
        tm = ThroughputMetrics()
        for i in range(50):
            tm.add_sample(float(i))

        stats = tm.get_stats()
        assert len(stats["history"]) == 30


class TestAgentMetrics:

    def test_to_dict_keys(self):
        am = AgentMetrics()
        d = am.to_dict()
        assert set(d.keys()) == {
            "session_count", "tokens_per_second",
            "error_rate_1h", "queue_depth", "last_update",
        }

    def test_to_dict_types(self):
        am = AgentMetrics()
        d = am.to_dict()
        assert isinstance(d["session_count"], int)
        assert isinstance(d["tokens_per_second"], float)
        assert isinstance(d["last_update"], str)


class TestClusterStatus:

    def test_to_dict_defaults(self):
        cs = ClusterStatus()
        d = cs.to_dict()
        assert d["nodes"] == []
        assert d["total_gpus"] == 0
        assert d["active_gpus"] == 0
        assert d["failover_ready"] is False


class TestFetchTokenSpyMetrics:
    """Tests for _fetch_token_spy_metrics() — Token Spy HTTP integration."""

    def setup_method(self):
        """Reset global state before each test."""
        agent_monitor.agent_metrics.session_count = 0
        agent_monitor.throughput.data_points.clear()

    def _make_session_mock(self, resp_status: int, resp_json=None):
        """Build the nested async-context-manager mock for aiohttp.ClientSession."""
        mock_resp = MagicMock()
        mock_resp.status = resp_status
        mock_resp.json = AsyncMock(return_value=resp_json or [])

        mock_get_cm = MagicMock()
        mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_get_cm.__aexit__ = AsyncMock(return_value=False)

        mock_http = MagicMock()
        mock_http.get.return_value = mock_get_cm

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        return mock_session_cm

    @pytest.mark.asyncio
    async def test_populates_session_count_and_throughput(self, monkeypatch):
        """session_count and throughput are updated when Token Spy returns data."""
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "test-key")

        fake_summary = [
            {"agent": "claude", "turns": 5, "total_output_tokens": 7200},
            {"agent": "gpt4", "turns": 2, "total_output_tokens": 3600},
        ]
        mock_session_cm = self._make_session_mock(200, fake_summary)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await agent_monitor._fetch_token_spy_metrics()

        assert agent_monitor.agent_metrics.session_count == 2
        assert len(agent_monitor.throughput.data_points) == 1
        # total_out = 10800 tokens; avg tps = 10800 / 3600 = 3.0
        assert agent_monitor.throughput.data_points[0]["tokens_per_sec"] == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_no_url_skips_fetch(self, monkeypatch):
        """No HTTP call is made when TOKEN_SPY_URL is empty."""
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "")

        with patch("aiohttp.ClientSession") as mock_cs:
            await agent_monitor._fetch_token_spy_metrics()

        mock_cs.assert_not_called()
        assert agent_monitor.agent_metrics.session_count == 0

    @pytest.mark.asyncio
    async def test_connection_error_degrades_gracefully(self, monkeypatch):
        """When Token Spy is unreachable, metrics are unchanged and no exception raised."""
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "")
        agent_monitor.agent_metrics.session_count = 99  # pre-existing value

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await agent_monitor._fetch_token_spy_metrics()  # must not raise

        assert agent_monitor.agent_metrics.session_count == 99  # unchanged

    @pytest.mark.asyncio
    async def test_non_200_response_skips_update(self, monkeypatch):
        """A non-200 status does not update session_count or throughput."""
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "")
        agent_monitor.agent_metrics.session_count = 5
        mock_session_cm = self._make_session_mock(503)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await agent_monitor._fetch_token_spy_metrics()

        assert agent_monitor.agent_metrics.session_count == 5  # unchanged
        assert len(agent_monitor.throughput.data_points) == 0

    @pytest.mark.asyncio
    async def test_timeout_error_degrades_gracefully(self, monkeypatch):
        """When Token Spy times out, metrics are unchanged."""
        import asyncio
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "")

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await agent_monitor._fetch_token_spy_metrics()

    @pytest.mark.asyncio
    async def test_content_type_error_handled(self, monkeypatch):
        """When Token Spy returns unexpected content type, no exception raised."""
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_URL", "http://token-spy:8080")
        monkeypatch.setattr(agent_monitor, "TOKEN_SPY_API_KEY", "")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(
            MagicMock(), MagicMock(), message="bad content"
        ))

        mock_get_cm = MagicMock()
        mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_get_cm.__aexit__ = AsyncMock(return_value=False)

        mock_http = MagicMock()
        mock_http.get.return_value = mock_get_cm

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await agent_monitor._fetch_token_spy_metrics()


class TestClusterStatusRefresh:

    @pytest.mark.asyncio
    async def test_refresh_success(self):
        """ClusterStatus.refresh parses valid JSON response."""
        cs = ClusterStatus()

        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(
                b'{"nodes": [{"id": "n1", "healthy": true}, {"id": "n2", "healthy": false}]}',
                b""
            ))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await cs.refresh()

        assert cs.total_gpus == 2
        assert cs.active_gpus == 1
        assert cs.failover_ready is False

    @pytest.mark.asyncio
    async def test_refresh_file_not_found(self):
        """ClusterStatus.refresh handles missing curl."""
        cs = ClusterStatus()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("curl")):
            await cs.refresh()

        assert cs.total_gpus == 0

    @pytest.mark.asyncio
    async def test_refresh_timeout(self):
        """ClusterStatus.refresh handles timeout."""
        import asyncio as _asyncio
        cs = ClusterStatus()

        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(side_effect=_asyncio.TimeoutError())
            proc.kill = MagicMock()
            proc.wait = AsyncMock()
            _fake_subprocess.proc = proc
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await cs.refresh()

        assert cs.total_gpus == 0
        _fake_subprocess.proc.kill.assert_called_once()
        _fake_subprocess.proc.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_os_error(self):
        """ClusterStatus.refresh handles OSError."""
        cs = ClusterStatus()

        with patch("asyncio.create_subprocess_exec", side_effect=OSError("broken")):
            await cs.refresh()

        assert cs.total_gpus == 0

    @pytest.mark.asyncio
    async def test_refresh_invalid_json(self):
        """ClusterStatus.refresh handles invalid JSON."""
        cs = ClusterStatus()

        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"not json{", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await cs.refresh()

        assert cs.total_gpus == 0


class TestGetFullAgentMetrics:

    def test_returns_full_dict(self):
        """get_full_agent_metrics returns correct structure."""
        from agent_monitor import get_full_agent_metrics
        result = get_full_agent_metrics()
        assert "timestamp" in result
        assert "agent" in result
        assert "cluster" in result
        assert "throughput" in result

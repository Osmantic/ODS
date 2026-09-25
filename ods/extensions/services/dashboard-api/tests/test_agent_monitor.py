"""Tests for agent_monitor.py — throughput metrics and data classes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from agent_monitor import ThroughputMetrics, AgentMetrics, ClusterStatus
import agent_monitor


class TestThroughputMetrics:

    @pytest.mark.parametrize('invalid', [
        float('nan'), float('inf'), float('-inf'), -1, None,
        'bad', {}, [], True, False, 10 ** 400,
    ], ids=['nan', 'inf', '-inf', 'negative', 'none', 'text', 'dict',
            'list', 'true', 'false', 'overflow'])
    def test_invalid_samples_do_not_invent_zero_measurements(self, invalid):
        tm = ThroughputMetrics()
        tm.add_sample(10)
        before = tm.get_stats()
        tm.add_sample(invalid)
        assert tm.get_stats() == before

    def test_real_zero_and_numeric_strings_remain_valid(self):
        tm = ThroughputMetrics()
        tm.add_sample('10.5')
        tm.add_sample(0)
        assert tm.get_stats()['current'] == 0
        assert tm.get_stats()['average'] == 5.25
        assert len(tm.get_stats()['history']) == 2

    def test_large_finite_samples_have_a_finite_average(self):
        tm = ThroughputMetrics()
        tm.add_sample(1e308)
        tm.add_sample(1e308)
        assert tm.get_stats()['average'] == 1e308

    def test_empty_stats(self):
        tm = ThroughputMetrics()
        stats = tm.get_stats()
        assert stats["current"] is None
        assert stats["average"] is None
        assert stats["peak"] is None
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

        # Insert an old data point by manipulating the list directly. The
        # fixture must be UTC-aware like real samples: the prune cutoff is
        # aware, and comparing it against a naive timestamp raises TypeError.
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
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
            "throughput_scope", "throughput_state", "throughput_model",
            "throughput_sampled_at", "output_tokens_24h",
        }

    def test_to_dict_types(self):
        am = AgentMetrics()
        d = am.to_dict()
        assert isinstance(d["session_count"], int)
        assert d["tokens_per_second"] is None
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
    async def test_usage_does_not_become_generation_throughput(self, monkeypatch):
        """24-hour output usage stays separate from runtime throughput."""
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
        assert agent_monitor.throughput.data_points == []
        assert agent_monitor.agent_metrics.output_tokens_24h == 10800

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

    @pytest.mark.asyncio
    async def test_refresh_handles_null_nodes_payload(self):
        """ClusterStatus.refresh handles {"nodes": null} without TypeError."""
        cs = ClusterStatus()

        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b'{"nodes": null}', b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
            await cs.refresh()

        assert cs.nodes == []
        assert cs.total_gpus == 0
        assert cs.active_gpus == 0


class TestGetFullAgentMetrics:

    def test_returns_full_dict(self):
        """get_full_agent_metrics returns correct structure."""
        from agent_monitor import get_full_agent_metrics
        result = get_full_agent_metrics()
        assert "timestamp" in result
        assert "agent" in result
        assert "cluster" in result
        assert "throughput" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    (b'null', 0), (b'[]', 0), (b'1', 0), (b'{}', 0),
    (b'{"nodes": null}', 0), (b'{"nodes": {}}', 0),
    (b'{"nodes": "invalid"}', 0), (b'{', 0), (b'\xff', 0),
    (b'{"nodes": [{"healthy": true}, {"healthy": true}]}', 7),
])
async def test_invalid_cluster_poll_clears_previous_readiness(payload, code):
    cs = ClusterStatus()
    proc = MagicMock(returncode=0)
    proc.communicate = AsyncMock(return_value=(
        b'{"nodes": [{"healthy": true}, {"healthy": true}]}', b''))
    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
        await cs.refresh()
        assert cs.failover_ready is True
        proc.returncode = code
        proc.communicate.return_value = (payload, b'')
        await cs.refresh()
    assert cs.to_dict() == {
        'nodes': [], 'total_gpus': 0, 'active_gpus': 0, 'failover_ready': False,
    }


@pytest.mark.asyncio
async def test_cluster_filters_invalid_nodes_and_requires_boolean_health():
    cs = ClusterStatus()
    proc = MagicMock(returncode=0)
    proc.communicate = AsyncMock(return_value=(
        b'{"nodes": [null, 3, "bad", {"healthy": "false"}, {"healthy": 1},'
        b' {"healthy": false}, {"healthy": true}]}', b''))
    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
        await cs.refresh()
    assert cs.total_gpus == 4
    assert cs.active_gpus == 1
    assert cs.failover_ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [FileNotFoundError, OSError])
async def test_unavailable_cluster_does_not_retain_ready_status(failure):
    cs = ClusterStatus()
    cs.nodes = [{'healthy': True}, {'healthy': True}]
    cs.total_gpus = cs.active_gpus = 2
    cs.failover_ready = True
    with patch('asyncio.create_subprocess_exec', AsyncMock(side_effect=failure)):
        await cs.refresh()
    assert cs.nodes == []
    assert cs.active_gpus == cs.total_gpus == 0
    assert cs.failover_ready is False


def runtime_observation(rate=42, state="measured", model="model-a", at=None):
    return {
        "tokens_per_second": rate, "throughput_state": state,
        "throughput_model": model,
        "throughput_sampled_at": at or datetime.now(timezone.utc).timestamp(),
        "throughput_mode": "generation_interval", "inference_active": False,
    }


def test_shared_measurements_stick_without_duplicate_history():
    tm = ThroughputMetrics()
    first = runtime_observation()
    tm.observe(first)
    tm.observe(first)  # another consumer receives the one-second cached sample
    tm.observe({**first, "throughput_state": "retained"})
    tm.observe({**first, "throughput_state": "unavailable"})
    stats = tm.get_stats()
    assert stats["current"] == 42
    assert stats["state"] == "unavailable"
    assert stats["sampled_at"] == first["throughput_sampled_at"]
    assert len(stats["history"]) == 1
    # Equal speed from a new run is a new event.
    tm.observe({**first, "throughput_sampled_at": first["throughput_sampled_at"] + 1})
    assert len(tm.get_stats()["history"]) == 2


def test_model_change_clears_previous_rate_and_history():
    tm = ThroughputMetrics()
    tm.observe(runtime_observation())
    tm.observe(runtime_observation(None, "unavailable", "model-b"))
    stats = tm.get_stats()
    assert stats["current"] is None
    assert stats["history"] == []
    assert stats["model"] == "model-b"
    assert stats["scope"] == "runtime"


def test_history_expires_without_clearing_sticky_measurement():
    tm = ThroughputMetrics()
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp()
    tm.observe(runtime_observation(at=old))
    assert tm.get_stats()["history"] == []
    assert tm.get_stats()["current"] == 42
    assert tm.get_stats()["sampled_at"] == old


@pytest.mark.asyncio
async def test_background_runtime_poll_uses_shared_sampler_and_failure_provenance(monkeypatch):
    tm = ThroughputMetrics()
    monkeypatch.setattr(agent_monitor, "throughput", tm)
    first = runtime_observation()
    source = AsyncMock(return_value=first)
    monkeypatch.setattr(agent_monitor, "get_llama_metrics", source)
    await agent_monitor._fetch_runtime_metrics()
    source.assert_awaited_once_with()
    source.side_effect = TimeoutError
    monkeypatch.setattr(agent_monitor, "get_cached_llama_metrics",
                        lambda: {**first, "throughput_state": "unavailable"})
    await agent_monitor._fetch_runtime_metrics()
    assert tm.get_stats()["state"] == "unavailable"
    assert tm.get_stats()["current"] == 42
    assert len(tm.get_stats()["history"]) == 1
    source.side_effect = None
    source.return_value = {**first, "tokens_per_second": 55,
                           "throughput_sampled_at": first["throughput_sampled_at"] + 1}
    await agent_monitor._fetch_runtime_metrics()
    assert tm.get_stats()["current"] == 55
    assert len(tm.get_stats()["history"]) == 2
    assert agent_monitor.get_full_agent_metrics()["agent"]["tokens_per_second"] == 55


@pytest.mark.asyncio
async def test_collector_sources_run_concurrently_and_cancel_cleanly(monkeypatch):
    import asyncio
    entered = set()
    ready = asyncio.Event()
    async def source(name, fail=False):
        entered.add(name)
        if len(entered) == 3:
            ready.set()
        await ready.wait()
        if fail:
            raise OSError("independent source failure")
    monkeypatch.setattr(agent_monitor.cluster_status, "refresh", lambda: source("cluster", True))
    monkeypatch.setattr(agent_monitor, "_fetch_token_spy_metrics", lambda: source("usage"))
    monkeypatch.setattr(agent_monitor, "_fetch_runtime_metrics", lambda: source("runtime"))
    task = asyncio.create_task(agent_monitor.collect_metrics())
    await asyncio.wait_for(ready.wait(), timeout=1)
    assert entered == {"cluster", "usage", "runtime"}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runtime_poll_has_bounded_timeout(monkeypatch):
    source = AsyncMock(return_value={})
    monkeypatch.setattr(agent_monitor, "get_llama_metrics", source)
    async def bounded(awaitable, timeout):
        assert timeout == 4.5
        await awaitable
        raise TimeoutError
    monkeypatch.setattr(agent_monitor.asyncio, "wait_for", bounded)
    monkeypatch.setattr(agent_monitor, "get_cached_llama_metrics", lambda: {})
    monkeypatch.setattr(agent_monitor, "throughput", ThroughputMetrics())
    await agent_monitor._fetch_runtime_metrics()
    assert agent_monitor.throughput.get_stats()["current"] is None


def test_retained_measurement_first_observed_by_background_is_recorded_once():
    tm = ThroughputMetrics()
    shared = runtime_observation(state="retained")
    # Status may have consumed the new event before the five-second collector.
    tm.observe(shared)
    tm.observe(shared)
    assert len(tm.get_stats()["history"]) == 1
    assert tm.get_stats()["sampled_at"] == shared["throughput_sampled_at"]

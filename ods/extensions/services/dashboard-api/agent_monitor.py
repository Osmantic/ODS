"""
Agent Monitoring Module for Dashboard API
Collects real-time metrics on agent swarms, sessions, and throughput.
"""

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import List
import os

import aiohttp

from helpers import get_llama_metrics, get_cached_llama_metrics

logger = logging.getLogger(__name__)

TOKEN_SPY_URL = os.environ.get("TOKEN_SPY_URL", "http://token-spy:8080")
TOKEN_SPY_API_KEY = os.environ.get("TOKEN_SPY_API_KEY", "")


class AgentMetrics:
    """Real-time agent monitoring metrics"""

    def __init__(self):
        self.last_update = datetime.now(timezone.utc)
        self.session_count = 0
        self.output_tokens_24h = None
        self.error_rate_1h = 0.0
        self.queue_depth = 0  # no data source located; llama-server /health does not expose queued requests

    def to_dict(self, runtime=None) -> dict:
        runtime = runtime or {}
        return {
            "session_count": self.session_count,
            # Runtime-wide, not an attribution to these agent sessions.
            "tokens_per_second": runtime.get("current"),
            "throughput_scope": "runtime",
            "throughput_state": runtime.get("state", "unavailable"),
            "throughput_model": runtime.get("model"),
            "throughput_sampled_at": runtime.get("sampled_at"),
            "output_tokens_24h": self.output_tokens_24h,
            "error_rate_1h": round(self.error_rate_1h, 2),
            "queue_depth": self.queue_depth,
            "last_update": self.last_update.isoformat()
        }


class ClusterStatus:
    """Cluster health and node status"""

    def __init__(self):
        self.nodes: List[dict] = []
        self.failover_ready = False
        self.total_gpus = 0
        self.active_gpus = 0

    async def refresh(self):
        """Query cluster status from smart proxy"""
        logger.debug("Refreshing cluster status from proxy")
        nodes = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", "4", f"http://localhost:{os.environ.get('CLUSTER_PROXY_PORT', '9199')}/status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)

            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                if isinstance(data, dict):
                    nodes_data = data.get("nodes")
                    if isinstance(nodes_data, list):
                        nodes = [node for node in nodes_data if isinstance(node, dict)]
        except FileNotFoundError:
            logger.debug("Cluster proxy not available: curl command not found")
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.debug("Cluster proxy health check timed out after 5s")
        except OSError as e:
            logger.debug("Cluster proxy connection failed: %s", e)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Cluster proxy returned invalid JSON: %s", e)

        # Only the latest successful response can establish readiness. A failed
        # poll must not leave a previously healthy cluster reported as ready.
        self.nodes = nodes
        self.total_gpus = len(nodes)
        self.active_gpus = sum(1 for node in nodes if node.get("healthy") is True)
        self.failover_ready = self.active_gpus > 1
        logger.debug("Cluster status: %d/%d GPUs active, failover_ready=%s",
                     self.active_gpus, self.total_gpus, self.failover_ready)

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "total_gpus": self.total_gpus,
            "active_gpus": self.active_gpus,
            "failover_ready": self.failover_ready
        }


class ThroughputMetrics:
    """Runtime measurements, with sticky value and deduplicated event history."""

    def __init__(self, history_minutes: int = 15):
        self.history_minutes = history_minutes
        self.data_points: List[dict] = []
        self.observation = {}
        self._last_sample = None

    def observe(self, metrics: dict):
        """Mirror the shared sampler; never derive speed from usage totals."""
        model = metrics.get("throughput_model")
        if model != self.observation.get("throughput_model"):
            self.data_points.clear()
            self._last_sample = None
        self.observation = dict(metrics)
        sampled_at = metrics.get("throughput_sampled_at")
        sample_key = (model, sampled_at)
        if (model and sampled_at is not None
                and metrics.get("throughput_state") in {"measured", "retained"}
                and sample_key != self._last_sample):
            self.add_sample(metrics.get("tokens_per_second"), sampled_at)
            self._last_sample = sample_key

    def add_sample(self, tokens_per_sec: float, sampled_at=None):
        """Record finite nonnegative measurements; invalid data is not zero usage."""
        if isinstance(tokens_per_sec, bool):
            return
        try:
            val = float(tokens_per_sec)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(val) or val < 0:
            return

        measured_at = (datetime.now(timezone.utc) if sampled_at is None
                       else datetime.fromtimestamp(sampled_at, timezone.utc))
        self.data_points.append({
            "timestamp": measured_at.isoformat(),
            "tokens_per_sec": val
        })

        # Prune old data
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.history_minutes)
        self.data_points = [
            p for p in self.data_points
            if datetime.fromisoformat(p["timestamp"]) > cutoff
        ]

    def get_stats(self) -> dict:
        """Current observation and event statistics; missing is never idle zero."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.history_minutes)
        self.data_points = [p for p in self.data_points
                            if datetime.fromisoformat(p["timestamp"]) > cutoff]
        values = [p["tokens_per_sec"] for p in self.data_points]
        total = sum(values)
        average = ((total / len(values) if math.isfinite(total)
                    else sum(value / len(values) for value in values)) if values else None)
        return {
            "current": (self.observation.get("tokens_per_second") if self.observation
                        else values[-1] if values else None),
            "average": average,
            "peak": max(values) if values else None,
            "history": self.data_points[-30:],
            "scope": "runtime",
            "source": "runtime_sampler",
            "state": self.observation.get("throughput_state", "unavailable"),
            "sampled_at": self.observation.get("throughput_sampled_at"),
            "model": self.observation.get("throughput_model"),
            "mode": self.observation.get("throughput_mode"),
            "inference_active": self.observation.get("inference_active"),
        }


# Global metrics instances
agent_metrics = AgentMetrics()
cluster_status = ClusterStatus()
throughput = ThroughputMetrics()


async def _fetch_token_spy_metrics() -> None:
    """Pull session count and separate 24-hour output usage from Token Spy."""
    if not TOKEN_SPY_URL:
        logger.debug("Token Spy URL not configured, skipping metrics fetch")
        return
    logger.debug("Fetching metrics from Token Spy at %s", TOKEN_SPY_URL)
    try:
        headers = {}
        if TOKEN_SPY_API_KEY:
            headers["Authorization"] = f"Bearer {TOKEN_SPY_API_KEY}"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(
                f"{TOKEN_SPY_URL}/api/summary",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    agent_metrics.session_count = len(data)
                    # This is aggregate usage, not the runtime generation rate.
                    total_out = sum(r.get("total_output_tokens", 0) or 0 for r in data)
                    agent_metrics.output_tokens_24h = total_out
                    logger.debug("Token Spy metrics: %d sessions, %d total output tokens",
                               len(data), total_out)
                else:
                    logger.debug("Token Spy returned status %d", resp.status)
    except aiohttp.ClientError as e:
        logger.debug("Token Spy unavailable: %s", e)
    except asyncio.TimeoutError:
        logger.debug("Token Spy request timed out after 5s")
    except aiohttp.ContentTypeError as e:
        logger.warning("Token Spy returned unexpected content type: %s", e)


async def _fetch_runtime_metrics() -> None:
    """Use the same locked sampler as status/catalogue, even with no UI open."""
    try:
        metrics = await asyncio.wait_for(get_llama_metrics(), timeout=4.5)
    except (asyncio.TimeoutError, OSError, aiohttp.ClientError, ValueError):
        metrics = get_cached_llama_metrics()
    throughput.observe(metrics)


async def collect_metrics():
    """Poll independent sources together on a five-second cadence."""
    while True:
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(
            cluster_status.refresh(), _fetch_token_spy_metrics(),
            _fetch_runtime_metrics(), return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.debug("Metrics source unavailable: %s", type(result).__name__)
        agent_metrics.last_update = datetime.now(timezone.utc)
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.1, 5 - elapsed))


def get_full_agent_metrics() -> dict:
    """Get all agent monitoring metrics as a dict"""
    runtime = throughput.get_stats()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_metrics.to_dict(runtime),
        "cluster": cluster_status.to_dict(),
        "throughput": runtime
    }

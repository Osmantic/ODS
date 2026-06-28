"""
Agent Monitoring Module for Dashboard API
Collects real-time metrics on agent swarms, sessions, and throughput.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List
import os

import aiohttp

logger = logging.getLogger(__name__)

TOKEN_SPY_URL = os.environ.get("TOKEN_SPY_URL", "http://token-spy:8080")
TOKEN_SPY_API_KEY = os.environ.get("TOKEN_SPY_API_KEY", "")


class AgentMetrics:
    """Real-time agent monitoring metrics"""

    def __init__(self):
        self.last_update = datetime.now()
        self.session_count = 0
        self.tokens_per_second = 0.0  # no data source located; use throughput.get_stats() for live rate
        self.error_rate_1h = 0.0
        self.queue_depth = 0  # no data source located; llama-server /health does not expose queued requests

    def to_dict(self) -> dict:
        return {
            "session_count": self.session_count,
            "tokens_per_second": round(self.tokens_per_second, 2),
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
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", "4", f"http://localhost:{os.environ.get('CLUSTER_PROXY_PORT', '9199')}/status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)

            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                self.nodes = data.get("nodes", [])
                self.total_gpus = len(self.nodes)
                self.active_gpus = sum(1 for n in self.nodes if n.get("healthy", False))
                self.failover_ready = self.active_gpus > 1
                logger.debug("Cluster status: %d/%d GPUs active, failover_ready=%s",
                           self.active_gpus, self.total_gpus, self.failover_ready)
        except FileNotFoundError:
            logger.debug("Cluster proxy not available: curl command not found")
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.debug("Cluster proxy health check timed out after 5s")
        except OSError as e:
            logger.debug("Cluster proxy connection failed: %s", e)
        except json.JSONDecodeError as e:
            logger.warning("Cluster proxy returned invalid JSON: %s", e)

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "total_gpus": self.total_gpus,
            "active_gpus": self.active_gpus,
            "failover_ready": self.failover_ready
        }


class ThroughputMetrics:
    """Real-time throughput tracking"""

    def __init__(self, history_minutes: int = 15):
        self.history_minutes = history_minutes
        self.data_points: List[dict] = []

    def add_sample(self, tokens_per_sec: float):
        """Add a new throughput sample"""
        self.data_points.append({
            "timestamp": datetime.now().isoformat(),
            "tokens_per_sec": tokens_per_sec
        })

        # Prune old data
        cutoff = datetime.now() - timedelta(minutes=self.history_minutes)
        self.data_points = [
            p for p in self.data_points
            if datetime.fromisoformat(p["timestamp"]) > cutoff
        ]

    def get_stats(self) -> dict:
        """Get throughput statistics"""
        if not self.data_points:
            return {"current": 0, "average": 0, "peak": 0, "history": []}

        values = [p["tokens_per_sec"] for p in self.data_points]
        return {
            "current": values[-1] if values else 0,
            "average": sum(values) / len(values),
            "peak": max(values) if values else 0,
            "history": self.data_points[-30:]  # Last 30 points
        }


# Global metrics instances
agent_metrics = AgentMetrics()
cluster_status = ClusterStatus()
throughput = ThroughputMetrics()


async def _fetch_token_spy_metrics() -> None:
    """Pull per-agent session count and throughput from Token Spy /api/summary."""
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
                    # total_output_tokens is a 24 h aggregate; dividing by 3600 gives
                    # an average tokens/sec over the last hour (approximation)
                    total_out = sum(r.get("total_output_tokens", 0) or 0 for r in data)
                    throughput.add_sample(total_out / 3600.0)
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


async def collect_metrics():
    """Background task to collect metrics periodically"""
    while True:
        try:
            # Update cluster status
            await cluster_status.refresh()

            # Update agent session count and throughput from Token Spy
            await _fetch_token_spy_metrics()

            agent_metrics.last_update = datetime.now()

        except FileNotFoundError as e:
            logger.debug("Metrics collection failed: command not found - %s", e)
        except asyncio.TimeoutError:
            logger.debug("Metrics collection timed out")
        except OSError as e:
            logger.debug("Metrics collection OS error: %s", e)
        except json.JSONDecodeError as e:
            logger.warning("Metrics collection JSON decode error: %s", e)

        await asyncio.sleep(5)  # Update every 5 seconds


def get_full_agent_metrics() -> dict:
    """Get all agent monitoring metrics as a dict"""
    return {
        "timestamp": datetime.now().isoformat(),
        "agent": agent_metrics.to_dict(),
        "cluster": cluster_status.to_dict(),
        "throughput": throughput.get_stats()
    }

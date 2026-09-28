#!/usr/bin/env python3
"""Regression test: agents metrics HTML endpoint tolerates null throughput values."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import os
ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_API = ROOT / "extensions/services/dashboard-api"
sys.path.insert(0, str(DASHBOARD_API))
os.environ["DASHBOARD_API_KEY"] = "test-key"

from routers.agents import get_agent_metrics_html


class TestAgentsMetricsNullThroughput(unittest.TestCase):
    def test_null_throughput_values_render_safely(self):
        mock_metrics = {
            "cluster": {"active_gpus": 1, "total_gpus": 1, "failover_ready": True},
            "agent": {"session_count": 0, "last_update": "2026-09-23T10:00:00Z"},
            "throughput": {"current": None, "average": None},
        }

        with patch("routers.agents.get_full_agent_metrics", return_value=mock_metrics):
            response = asyncio.run(get_agent_metrics_html(api_key="test-key"))
            self.assertEqual(response.status_code, 200)
            body = response.body.decode("utf-8")
            self.assertIn("0.0", body)
            self.assertIn("tokens/sec (avg: 0.0)", body)

    def test_missing_throughput_keys_render_safely(self):
        mock_metrics = {
            "cluster": {},
            "agent": {},
            "throughput": {},
        }

        with patch("routers.agents.get_full_agent_metrics", return_value=mock_metrics):
            response = asyncio.run(get_agent_metrics_html(api_key="test-key"))
            self.assertEqual(response.status_code, 200)
            body = response.body.decode("utf-8")
            self.assertIn("0.0", body)


if __name__ == "__main__":
    unittest.main()

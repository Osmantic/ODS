"""Unit test suite for agent monitor task resilience and metrics serialization."""

import unittest
from agent_monitor import AgentMetrics


class TestAgentMonitorResilience(unittest.TestCase):
    def test_agent_metrics_to_dict_structure(self):
        metrics = AgentMetrics()
        data = metrics.to_dict()
        self.assertIsInstance(data, dict)
        self.assertIn("session_count", data)
        self.assertIn("tokens_per_second", data)
        self.assertIn("error_rate_1h", data)
        self.assertIn("queue_depth", data)
        self.assertIn("last_update", data)

    def test_agent_metrics_default_values(self):
        metrics = AgentMetrics()
        self.assertEqual(metrics.session_count, 0)
        self.assertEqual(metrics.tokens_per_second, 0.0)


if __name__ == "__main__":
    unittest.main()

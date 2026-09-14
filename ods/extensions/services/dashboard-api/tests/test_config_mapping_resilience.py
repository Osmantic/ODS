"""Unit tests for config environment variables and service mapping resilience."""

import unittest
import config


class TestConfigMappingResilience(unittest.TestCase):
    def test_services_dictionary_structure(self):
        self.assertIsInstance(config.SERVICES, dict)

    def test_agent_url_configuration(self):
        self.assertTrue(isinstance(config.AGENT_URL, str))
        self.assertTrue(config.AGENT_URL.startswith("http"))


if __name__ == "__main__":
    unittest.main()

"""Unit test suite for safe YAML dictionary dump resilience."""

import unittest
import yaml


def safe_dump_yaml_dict(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    try:
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except Exception:
        return None


class TestYAMLFileDumperResilience(unittest.TestCase):
    def test_safe_dump_yaml_valid(self):
        dump = safe_dump_yaml_dict({"name": "dashboard", "port": 8000})
        self.assertIsNotNone(dump)
        self.assertIn("name: dashboard", dump)

    def test_safe_dump_yaml_none(self):
        self.assertIsNone(safe_dump_yaml_dict(None))


if __name__ == "__main__":
    unittest.main()

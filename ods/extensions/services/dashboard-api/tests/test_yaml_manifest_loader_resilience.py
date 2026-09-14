"""Unit test suite for YAML manifest loader resilience."""

import unittest
import yaml


def safe_parse_yaml_manifest(yaml_text: str | None) -> dict:
    if not yaml_text or not isinstance(yaml_text, str):
        return {}
    try:
        data = yaml.safe_load(yaml_text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class TestYamlManifestLoaderResilience(unittest.TestCase):
    def test_safe_parse_yaml_valid(self):
        data = safe_parse_yaml_manifest("name: service-api\nport: 8000")
        self.assertEqual(data["name"], "service-api")
        self.assertEqual(data["port"], 8000)

    def test_safe_parse_yaml_malformed(self):
        self.assertEqual(safe_parse_yaml_manifest("invalid: [yaml: content"), {})
        self.assertEqual(safe_parse_yaml_manifest(None), {})


if __name__ == "__main__":
    unittest.main()

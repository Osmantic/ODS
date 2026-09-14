"""Unit test suite for configuration dictionary overrides merger resilience."""

import unittest


def merge_config_overrides(base_config: dict, overrides: dict | None) -> dict:
    if not isinstance(base_config, dict):
        base_config = {}
    merged = dict(base_config)
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = merge_config_overrides(merged[k], v)
            else:
                merged[k] = v
    return merged


class TestConfigOverridesResilience(unittest.TestCase):
    def test_merge_config_overrides_nested(self):
        base = {"server": {"port": 8000, "host": "127.0.0.1"}}
        ovr = {"server": {"port": 9000}}
        res = merge_config_overrides(base, ovr)
        self.assertEqual(res["server"]["port"], 9000)
        self.assertEqual(res["server"]["host"], "127.0.0.1")

    def test_merge_config_overrides_none(self):
        base = {"mode": "local"}
        res = merge_config_overrides(base, None)
        self.assertEqual(res, {"mode": "local"})


if __name__ == "__main__":
    unittest.main()

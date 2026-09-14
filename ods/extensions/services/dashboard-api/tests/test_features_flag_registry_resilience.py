"""Unit test suite for feature flag registry key lookup resilience."""

import unittest


def is_feature_flag_enabled(registry: dict, flag_key: str | None, default: bool = False) -> bool:
    if not isinstance(registry, dict) or not flag_key or not isinstance(flag_key, str):
        return bool(default)
    return bool(registry.get(flag_key.strip(), default))


class TestFeaturesFlagRegistryResilience(unittest.TestCase):
    def test_is_feature_enabled_valid(self):
        reg = {"experimental_voice": True, "beta_ui": False}
        self.assertTrue(is_feature_flag_enabled(reg, "experimental_voice"))
        self.assertFalse(is_feature_flag_enabled(reg, "beta_ui"))

    def test_is_feature_enabled_missing_key(self):
        reg = {"experimental_voice": True}
        self.assertFalse(is_feature_flag_enabled(reg, "unknown_feature"))
        self.assertFalse(is_feature_flag_enabled(reg, None))


if __name__ == "__main__":
    unittest.main()

"""Unit test suite for privacy telemetry boolean toggle parsing resilience."""

import unittest


def parse_telemetry_toggle_boolean(value: str | bool | int | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value != 0)
    val_str = str(value).strip().lower()
    return val_str in ("true", "1", "yes", "on", "enabled")


class TestPrivacyTelemetryToggleResilience(unittest.TestCase):
    def test_parse_toggle_string_true(self):
        self.assertTrue(parse_telemetry_toggle_boolean("enabled"))
        self.assertTrue(parse_telemetry_toggle_boolean("YES"))

    def test_parse_toggle_string_false(self):
        self.assertFalse(parse_telemetry_toggle_boolean("disabled"))
        self.assertFalse(parse_telemetry_toggle_boolean(None))


if __name__ == "__main__":
    unittest.main()

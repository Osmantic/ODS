"""Unit test suite for cron schedule expression format validation resilience."""

import unittest


def validate_cron_expression(cron_str: str | None) -> tuple[bool, str]:
    if not cron_str or not isinstance(cron_str, str):
        return False, "missing_expression"
    fields = cron_str.strip().split()
    if len(fields) != 5:
        return False, "invalid_field_count"
    return True, "valid"


class TestCronScheduleParserResilience(unittest.TestCase):
    def test_validate_cron_valid(self):
        ok, reason = validate_cron_expression("*/5 * * * *")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_cron_invalid_fields(self):
        ok, reason = validate_cron_expression("invalid_cron")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_field_count")


if __name__ == "__main__":
    unittest.main()

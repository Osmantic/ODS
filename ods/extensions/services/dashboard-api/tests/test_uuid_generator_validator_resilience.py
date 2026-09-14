"""Unit test suite for UUID token format validation resilience."""

import unittest
import uuid


def validate_uuid_string(val: str | None) -> tuple[bool, str]:
    if not val or not isinstance(val, str):
        return False, "invalid_type"
    try:
        uuid.UUID(val.strip())
        return True, "valid"
    except ValueError:
        return False, "malformed_uuid"


class TestUUIDGeneratorValidatorResilience(unittest.TestCase):
    def test_validate_uuid_valid(self):
        ok, reason = validate_uuid_string("123e4567-e89b-12d3-a456-426614174000")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_uuid_invalid(self):
        ok, reason = validate_uuid_string("not_a_uuid")
        self.assertFalse(ok)
        self.assertEqual(reason, "malformed_uuid")


if __name__ == "__main__":
    unittest.main()

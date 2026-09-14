"""Unit test suite for MAC address format validation resilience."""

import re
import unittest

_MAC_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def validate_mac_address_string(mac_str: str | None) -> tuple[bool, str]:
    if not mac_str or not isinstance(mac_str, str):
        return False, "missing_mac"
    cleaned = mac_str.strip()
    if not _MAC_ADDRESS_RE.match(cleaned):
        return False, "invalid_mac_format"
    return True, "valid"


class TestMACAddressFormatResilience(unittest.TestCase):
    def test_validate_mac_valid(self):
        ok, reason = validate_mac_address_string("00:1A:2B:3C:4D:5E")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_mac_invalid(self):
        ok, reason = validate_mac_address_string("invalid_mac")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_mac_format")

    def test_validate_mac_none(self):
        ok, reason = validate_mac_address_string(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_mac")


if __name__ == "__main__":
    unittest.main()

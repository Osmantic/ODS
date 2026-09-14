"""Unit test suite for network IP address format validation resilience."""

import ipaddress
import unittest


def validate_ip_address_string(ip_str: str | None) -> tuple[bool, str]:
    if not ip_str or not isinstance(ip_str, str):
        return False, "invalid_type"
    try:
        ipaddress.ip_address(ip_str.strip())
        return True, "valid"
    except ValueError:
        return False, "malformed_ip"


class TestNetworkIPValidatorResilience(unittest.TestCase):
    def test_validate_ip_valid_v4(self):
        ok, reason = validate_ip_address_string("192.168.1.100")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_ip_valid_v6(self):
        ok, reason = validate_ip_address_string("::1")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_ip_invalid(self):
        ok, reason = validate_ip_address_string("256.300.1.1")
        self.assertFalse(ok)
        self.assertEqual(reason, "malformed_ip")


if __name__ == "__main__":
    unittest.main()

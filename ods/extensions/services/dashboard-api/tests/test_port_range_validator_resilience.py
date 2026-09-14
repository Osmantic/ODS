"""Unit test suite for TCP/UDP port range number validation resilience."""

import unittest


def validate_network_port(port_val: object) -> tuple[bool, int]:
    if port_val is None:
        return False, 0
    try:
        port_num = int(port_val)
        if 1 <= port_num <= 65535:
            return True, port_num
        return False, port_num
    except (TypeError, ValueError):
        return False, 0


class TestPortRangeValidatorResilience(unittest.TestCase):
    def test_validate_port_valid(self):
        ok, port = validate_network_port(8000)
        self.assertTrue(ok)
        self.assertEqual(port, 8000)

    def test_validate_port_out_of_range(self):
        ok, port = validate_network_port(70000)
        self.assertFalse(ok)

    def test_validate_port_invalid_string(self):
        ok, port = validate_network_port("invalid")
        self.assertFalse(ok)
        self.assertEqual(port, 0)


if __name__ == "__main__":
    unittest.main()

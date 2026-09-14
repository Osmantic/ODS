"""Unit test suite for HTTP status code validation bounds resilience."""

import unittest


def validate_http_status_code(code_val: object) -> tuple[bool, int]:
    if code_val is None:
        return False, 500
    try:
        code = int(code_val)
        if 100 <= code <= 599:
            return True, code
        return False, 500
    except (TypeError, ValueError):
        return False, 500


class TestHTTPStatusCodeBoundsResilience(unittest.TestCase):
    def test_validate_status_code_valid(self):
        ok, code = validate_http_status_code(200)
        self.assertTrue(ok)
        self.assertEqual(code, 200)

    def test_validate_status_code_invalid(self):
        ok, code = validate_http_status_code(999)
        self.assertFalse(ok)
        self.assertEqual(code, 500)

    def test_validate_status_code_none(self):
        ok, code = validate_http_status_code(None)
        self.assertFalse(ok)
        self.assertEqual(code, 500)


if __name__ == "__main__":
    unittest.main()

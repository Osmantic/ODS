"""Unit tests for session_signer.py cookie generation and verification resilience."""

import time
import unittest

from session_signer import issue, verify, _set_secret_for_tests


class TestSessionSignerResilience(unittest.TestCase):
    def setUp(self):
        _set_secret_for_tests("test-secret-do-not-use-in-prod")

    def tearDown(self):
        _set_secret_for_tests("")

    def test_issue_returns_valid_three_part_cookie(self):
        cookie = issue(ttl_seconds=3600)
        parts = cookie.split(".")
        self.assertEqual(len(parts), 3)
        ok, reason = verify(cookie)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_verify_expired_cookie(self):
        cookie = issue(ttl_seconds=10)
        parts = cookie.split(".")
        past_timestamp = str(int(time.time()) - 3600)
        # Re-verify past cookie
        ok, reason = verify(f"{parts[0]}.{past_timestamp}.{parts[2]}")
        self.assertFalse(ok)

    def test_verify_tampered_signature(self):
        cookie = issue(ttl_seconds=3600)
        parts = cookie.split(".")
        tampered = f"{parts[0]}.{parts[1]}.invalid_signature"
        ok, reason = verify(tampered)
        self.assertFalse(ok)
        self.assertEqual(reason, "bad-signature")

    def test_verify_malformed_cookie(self):
        ok, reason = verify("invalid_cookie_format")
        self.assertFalse(ok)
        self.assertEqual(reason, "malformed")

        ok_none, reason_none = verify(None)
        self.assertFalse(ok_none)
        self.assertEqual(reason_none, "malformed")


if __name__ == "__main__":
    unittest.main()

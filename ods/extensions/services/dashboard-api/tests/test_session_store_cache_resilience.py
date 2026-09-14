"""Unit test suite for session store signer resilience."""

import unittest
import session_signer
from session_signer import issue, verify


class TestSessionStoreCacheResilience(unittest.TestCase):
    def setUp(self):
        self._orig_secret = session_signer._SECRET
        session_signer._SECRET = b"01234567890123456789012345678901"

    def tearDown(self):
        session_signer._SECRET = self._orig_secret

    def test_session_signer_issue_and_verify(self):
        cookie_val = issue(ttl_seconds=3600)
        self.assertIsInstance(cookie_val, str)
        ok, reason = verify(cookie_val)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_session_signer_verify_invalid_format(self):
        ok, reason = verify("invalid_cookie_string")
        self.assertFalse(ok)
        self.assertEqual(reason, "malformed")


if __name__ == "__main__":
    unittest.main()

"""Unit test suite for User-Agent header string sanitization resilience."""

import unittest


def sanitize_user_agent_header(ua_str: str | None, default_ua: str = "ODS-DashboardAPI/1.0") -> str:
    if not ua_str or not isinstance(ua_str, str):
        return default_ua
    cleaned = ua_str.strip()
    if not cleaned or any(c in cleaned for c in "\r\n"):
        return default_ua
    return cleaned[:256]


class TestHTTPUserAgentSanitizerResilience(unittest.TestCase):
    def test_sanitize_user_agent_valid(self):
        self.assertEqual(sanitize_user_agent_header("Mozilla/5.0"), "Mozilla/5.0")

    def test_sanitize_user_agent_crlf(self):
        self.assertEqual(sanitize_user_agent_header("Mozilla/5.0\r\nHeader: injected"), "ODS-DashboardAPI/1.0")

    def test_sanitize_user_agent_none(self):
        self.assertEqual(sanitize_user_agent_header(None), "ODS-DashboardAPI/1.0")


if __name__ == "__main__":
    unittest.main()

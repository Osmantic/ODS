"""Unit test suite for HTTP header dictionary sanitization resilience."""

import unittest


def sanitize_http_response_headers(headers: dict | None) -> dict[str, str]:
    if not headers or not isinstance(headers, dict):
        return {}
    cleaned = {}
    for k, v in headers.items():
        if k and isinstance(k, str):
            key_str = str(k).strip()
            val_str = str(v).strip() if v is not None else ""
            if key_str and not any(c in key_str for c in "\r\n"):
                cleaned[key_str] = val_str
    return cleaned


class TestHTTPHeaderSanitizerResilience(unittest.TestCase):
    def test_sanitize_headers_valid(self):
        hdrs = sanitize_http_response_headers({"Content-Type": "application/json  "})
        self.assertEqual(hdrs["Content-Type"], "application/json")

    def test_sanitize_headers_newline_injection(self):
        hdrs = sanitize_http_response_headers({"Set-Cookie\r\n": "malicious"})
        self.assertNotIn("Set-Cookie\r\n", hdrs)


if __name__ == "__main__":
    unittest.main()

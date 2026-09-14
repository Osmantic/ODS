"""Unit test suite for user extensions scanner resilience."""

import unittest
from pathlib import Path
from user_extensions import scan_user_extension_services, _SERVICE_ID_RE


class TestUserExtensionsResilience(unittest.TestCase):
    def test_scan_nonexistent_directory(self):
        missing_dir = Path("/tmp/nonexistent_user_ext_dir")
        result = scan_user_extension_services(missing_dir)
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {})

    def test_service_id_regex_match(self):
        self.assertIsNotNone(_SERVICE_ID_RE.match("custom-extension-service"))
        self.assertIsNone(_SERVICE_ID_RE.match("Invalid_Capital_Service"))


if __name__ == "__main__":
    unittest.main()

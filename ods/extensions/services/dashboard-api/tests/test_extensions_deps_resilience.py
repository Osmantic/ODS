"""Unit test suite for extension dependency resolver resilience."""

import unittest


class TestExtensionsDepsResilience(unittest.TestCase):
    def test_extensions_deps_import(self):
        from routers import extensions
        self.assertIsNotNone(extensions)


if __name__ == "__main__":
    unittest.main()

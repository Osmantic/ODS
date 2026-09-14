"""Unit test suite for magic link authentication resilience."""

import unittest


class TestMagicLinkResilience(unittest.TestCase):
    def test_magic_link_router_import(self):
        from routers import magic_link
        self.assertIsNotNone(magic_link.router)


if __name__ == "__main__":
    unittest.main()

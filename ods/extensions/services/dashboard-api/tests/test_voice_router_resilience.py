"""Unit test suite for voice router resilience."""

import unittest


class TestVoiceRouterResilience(unittest.TestCase):
    def test_voice_router_import(self):
        from routers import voice
        self.assertIsNotNone(voice.router)


if __name__ == "__main__":
    unittest.main()

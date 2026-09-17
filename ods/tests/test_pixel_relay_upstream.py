"""Regression test for Pixel model relay upstream connectivity and error handling."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path

relay = None
web = None
ClientSession = None


class PixelModelRelayUpstreamTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global relay, web, ClientSession
        try:
            from aiohttp import ClientSession as cs, web as w
            ClientSession = cs
            web = w
            os.environ.setdefault("PIXEL_MODEL_RELAY_KEY", "test-relay-key-01234567890123456789")
            RELAY_PATH = Path(__file__).resolve().parents[1] / "extensions" / "services" / "pixel-model-relay" / "relay.py"
            spec = importlib.util.spec_from_file_location("relay", RELAY_PATH)
            relay_mod = importlib.util.module_from_spec(spec)
            sys.modules["relay"] = relay_mod
            spec.loader.exec_module(relay_mod)
            relay = relay_mod
        except ImportError:
            raise unittest.SkipTest("aiohttp is required for pixel model relay tests")
    async def asyncSetUp(self) -> None:
        self.app = relay.create_app()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.orig_upstream = relay.UPSTREAM
        # Point to an unbound localhost port to simulate an unavailable model router
        relay.UPSTREAM = "http://127.0.0.1:59998"
        self.headers = {"Authorization": "Bearer test-relay-key-01234567890123456789"}

    async def asyncTearDown(self) -> None:
        relay.UPSTREAM = self.orig_upstream
        await self.runner.cleanup()

    async def test_unreachable_upstream_returns_502_bad_gateway(self) -> None:
        async with ClientSession() as client:
            async with client.post(
                f"{self.url}/v1/chat/completions",
                headers=self.headers,
                json={"model": "ods/current", "messages": [{"role": "user", "content": "ping"}]},
            ) as response:
                self.assertEqual(response.status, 502)
                data = await response.json()
                self.assertEqual(data.get("error", {}).get("type"), "bad_gateway")
                self.assertIn("unavailable", data.get("error", {}).get("message", ""))

    async def test_get_models_with_unreachable_upstream_returns_502(self) -> None:
        async with ClientSession() as client:
            async with client.get(
                f"{self.url}/v1/models",
                headers=self.headers,
            ) as response:
                self.assertEqual(response.status, 502)
                data = await response.json()
                self.assertEqual(data.get("error", {}).get("type"), "bad_gateway")


if __name__ == "__main__":
    unittest.main()

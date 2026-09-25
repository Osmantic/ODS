"""Socket-level Pixel relay authorization and disconnect regression."""

import asyncio
import importlib.util
import os
from pathlib import Path
import unittest

from aiohttp import ClientSession, web

os.environ["PIXEL_MODEL_RELAY_KEY"] = "test-only-pixel-relay-key"
os.environ["ODS_MODE"] = "local"
os.environ["EXTERNAL_LLM_URL"] = ""
spec = importlib.util.spec_from_file_location("relay", Path(__file__).parents[1] / "relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


async def start(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def test_generation_summary_allowlists_only_safe_scalars(self):
        payload = {"model": "ods/current", "stream": True, "max_tokens": 4096, "max_completion_tokens": 2048,
                   "chat_template_kwargs": {"enable_thinking": False, "secret": "private"},
                   "tools": [{"secret": "private"}], "messages": ["private"]}
        self.assertEqual(relay._generation_summary(payload), {
            "stream": True, "max_tokens": 4096, "max_completion_tokens": 2048, "enable_thinking": False, "tool_count": 1})
        self.assertEqual(payload["messages"], ["private"])

    def test_generation_summary_never_echoes_unexpected_values(self):
        self.assertEqual(relay._generation_summary({
            "stream": "private", "max_tokens": "private",
            "chat_template_kwargs": {"enable_thinking": "private"}, "tools": "private"}),
            {"stream": False, "max_tokens": None, "max_completion_tokens": None, "enable_thinking": None, "tool_count": 0})
        self.assertIsNone(relay._generation_summary({"max_tokens": True})["max_tokens"])

    async def asyncSetUp(self):
        self.disconnected = asyncio.Event()

        async def models(_request):
            return web.json_response({"data": [{"id": "ods/current"}]})

        async def chat(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            try:
                while request.transport is not None and not request.transport.is_closing():
                    try:
                        await response.write(b"data: {\"choices\":[]}\n\n")
                    except (ConnectionError, RuntimeError):
                        break
                    await asyncio.sleep(0.05)
            finally:
                self.disconnected.set()
            return response

        fake = web.Application()
        fake.router.add_get("/v1/models", models)
        fake.router.add_post("/v1/chat/completions", chat)
        self.fake_runner, relay.UPSTREAM = await start(fake)
        self.relay_runner, self.url = await start(relay.create_app())

    async def asyncTearDown(self):
        await self.relay_runner.cleanup()
        await self.fake_runner.cleanup()

    async def test_auth_and_scope(self):
        async with ClientSession() as client:
            async with client.get(self.url + "/v1/models") as response:
                self.assertEqual(response.status, 401)
            headers = {"Authorization": "Bearer test-only-pixel-relay-key"}
            async with client.get(self.url + "/v1/models", headers=headers) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["data"][0]["id"], "ods/current")
            async with client.post(self.url + "/internal/model-swap/admission", headers=headers) as response:
                self.assertEqual(response.status, 404)
            async with client.post(self.url + "/v1/chat/completions", headers=headers,
                                   json={"model": "arbitrary", "messages": []}) as response:
                self.assertEqual(response.status, 400)

    async def test_stream_close_closes_upstream(self):
        headers = {"Authorization": "Bearer test-only-pixel-relay-key"}
        async with ClientSession() as client:
            response = await client.post(self.url + "/v1/chat/completions", headers=headers,
                                         json={"model": "ods/current", "stream": True,
                                               "messages": [{"role": "user", "content": "hello"}]})
            self.assertEqual(response.status, 200)
            self.assertIn(b"data:", await response.content.read(25))
            response.close()
            await asyncio.wait_for(self.disconnected.wait(), timeout=3)

    async def test_stalled_local_reader_times_out(self):
        class StalledResponse:
            async def write(self, _chunk):
                await asyncio.sleep(10)

        original = relay.WRITE_TIMEOUT_SECONDS
        relay.WRITE_TIMEOUT_SECONDS = 0.05
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await relay._write(StalledResponse(), b"data: stalled\n\n")
        finally:
            relay.WRITE_TIMEOUT_SECONDS = original

    async def test_non_ascii_key_fails_at_startup(self):
        original = relay.KEY
        relay.KEY = "not-ascii-\u00e9"
        try:
            with self.assertRaisesRegex(RuntimeError, "invalid Pixel model relay key"):
                relay.create_app()
        finally:
            relay.KEY = original

    async def test_cloud_and_external_routes_are_fixed_internal_targets(self):
        self.assertEqual(relay._upstream_route("local", ""),
                         ("http://model-router:9099", False))
        self.assertEqual(relay._upstream_route("cloud", ""),
                         ("http://litellm:4000", True))
        self.assertEqual(relay._upstream_route("local", "http://untrusted.example/v1"),
                         ("http://litellm:4000", True))

    async def test_litellm_route_uses_only_its_gateway_key(self):
        seen = []

        async def keyed_models(request):
            seen.append(request.headers.get("Authorization"))
            return web.json_response({"data": [{"id": "ods/current"}]})

        keyed = web.Application()
        keyed.router.add_get("/v1/models", keyed_models)
        runner, upstream = await start(keyed)
        prior = relay.UPSTREAM, relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY
        relay.UPSTREAM, relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY = (
            upstream, True, "litellm-only-test-key")
        try:
            async with ClientSession() as client:
                async with client.get(self.url + "/v1/models", headers={
                    "Authorization": "Bearer test-only-pixel-relay-key"
                }) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual(seen, ["Bearer litellm-only-test-key"])
        finally:
            relay.UPSTREAM, relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY = prior
            await runner.cleanup()

    async def test_litellm_route_requires_a_valid_gateway_key(self):
        prior = relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY
        relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY = True, ""
        try:
            with self.assertRaisesRegex(RuntimeError, "invalid LiteLLM model relay key"):
                relay.create_app()
        finally:
            relay.UPSTREAM_REQUIRES_KEY, relay.LITELLM_KEY = prior


if __name__ == "__main__":
    unittest.main()

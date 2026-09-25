"""Socket-level Pixel relay authorization and disconnect regression."""

import asyncio
import importlib.util
import json
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

    def test_only_cloud_mode_leaves_the_host(self):
        self.assertTrue(relay._route_leaves_host("cloud"))
        for mode in ("local", "hybrid", "lemonade", ""):
            self.assertFalse(relay._route_leaves_host(mode))
        self.assertFalse(relay.ROUTE_LEAVES_HOST)

    def test_host_only_template_switch_is_removed_off_host(self):
        both = {"model": "ods/current", "messages": [{"role": "user", "content": "hi"}],
                "chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": True}}
        self.assertIsNone(relay._payload_for_route(both, False))
        forwarded = relay._payload_for_route(both, True)
        self.assertEqual(forwarded, {**both, "chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(both["chat_template_kwargs"], {"enable_thinking": False, "preserve_thinking": True})
        only = {"model": "ods/current", "messages": [], "chat_template_kwargs": {"preserve_thinking": True}}
        self.assertEqual(relay._payload_for_route(only, True), {"model": "ods/current", "messages": []})
        for unchanged in ({"model": "ods/current"},
                          {"model": "ods/current", "chat_template_kwargs": {"enable_thinking": False}},
                          {"model": "ods/current", "chat_template_kwargs": "preserve_thinking"}):
            self.assertIsNone(relay._payload_for_route(unchanged, True))

    async def test_forwarded_body_keeps_the_switch_only_on_the_host(self):
        bodies = []

        async def capture(request):
            bodies.append(await request.read())
            return web.json_response({"choices": []})

        upstream_app = web.Application()
        upstream_app.router.add_post("/v1/chat/completions", capture)
        runner, upstream = await start(upstream_app)
        prior = relay.UPSTREAM, relay.ROUTE_LEAVES_HOST
        raw = (b'{"model": "ods/current", "messages": [{"role": "user", "content": "hi"}],'
               b' "chat_template_kwargs": {"enable_thinking": false, "preserve_thinking": true}}')
        headers = {"Authorization": "Bearer test-only-pixel-relay-key", "Content-Type": "application/json"}
        try:
            relay.UPSTREAM = upstream
            async with ClientSession() as client:
                for leaves_host in (False, True):
                    relay.ROUTE_LEAVES_HOST = leaves_host
                    async with client.post(self.url + "/v1/chat/completions", data=raw,
                                           headers=headers) as response:
                        self.assertEqual(response.status, 200)
        finally:
            relay.UPSTREAM, relay.ROUTE_LEAVES_HOST = prior
            await runner.cleanup()
        self.assertEqual(bodies[0], raw)
        self.assertEqual(json.loads(bodies[1]), {
            "model": "ods/current", "messages": [{"role": "user", "content": "hi"}],
            "chat_template_kwargs": {"enable_thinking": False}})

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

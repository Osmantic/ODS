"""Bounded authenticated diagnostics transport without a native model dependency."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from aiohttp import web, ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PIXEL_OPENWEBUI_KEY", "c" * 64)
os.environ.setdefault("PIXEL_PREVIEW_PROXY_KEY", "o" * 64)
import pixel_edge as edge
from runtime_identity import unknown_runtime_identity


class RuntimeIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.value = unknown_runtime_identity()
        self.value.update(state="partial", diskComparison="match", reasonCode="release-binding-unavailable",
                          observedAt=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        self.value["identities"].update(pluginSha256="a" * 64, openclawModuleSha256="b" * 64)
        self.raw = json.dumps({**self.value, "privatePath": "/private/key"}).encode()
        self.reply_status = 200
        self.calls = []
        owner = self

        class Response:
            content_type = "application/json"
            @property
            def status(self): return owner.reply_status
            @property
            def content(self): return self
            async def iter_chunked(self, _size):
                for i in range(0, len(owner.raw), 4096):
                    yield owner.raw[i:i+4096]
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            def get(self, url, **options):
                owner.calls.append((url, options))
                return Response()

        self.patches = [patch.object(edge, "config_token", "c" * 64),
                        patch.object(edge, "UnixConnector", return_value=None),
                        patch.object(edge, "ClientSession", return_value=Session())]
        for item in self.patches: item.start()
        app = web.Application()
        app.router.add_get("/v1/runtime-identity", edge.handle_runtime_identity, allow_head=False)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:%d/v1/runtime-identity" % site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        for item in reversed(self.patches): item.stop()

    async def test_auth_exact_route_and_sanitized_projection(self):
        async with self.client.get(self.url) as response:
            self.assertEqual(response.status, 401)
        self.assertEqual(self.calls, [])
        headers = {"Authorization": "Bearer " + "c" * 64, "X-Private-Header": "must-not-forward"}
        async with self.client.get(self.url + "?path=/private", headers=headers) as response:
            self.assertEqual(response.status, 400)
        async with self.client.get(self.url, headers=headers) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), self.value)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.calls, [("http://pixel-upstream/v1/runtime-identity", {"allow_redirects": False})])

    async def test_missing_old_malformed_or_large_runtime_stays_unknown(self):
        headers = {"Authorization": "Bearer " + "c" * 64}
        for status, raw in ((404, b"private-secret"), (200, b"x" * 8193),
                            (200, b"[" * 4000 + b"0" + b"]" * 4000),
                            (200, json.dumps({**self.value, "runtimeMatchesRelease": True}).encode())):
            self.reply_status, self.raw = status, raw
            async with self.client.get(self.url, headers=headers) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), unknown_runtime_identity())

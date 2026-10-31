"""Portal's "Put back files" relay: one exact restore request in, a validated receipt or a fixed refusal out."""
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
import pixel_edge as edge  # noqa: E402

AUTH = {"Authorization": "Bearer " + "c" * 64}
SNAPSHOT = "chatcmpl_11111111-2222-4333-8444-555555555555"
REQUEST = {"user": "restore-chat", "project": "Playground/game", "snapshot": SNAPSHOT}
RECEIPT = {"schemaVersion": 1, "project": "Playground/game", "snapshot": SNAPSHOT, "written": 1, "removed": 2,
           "kept": ["node_modules"], "keptCount": 1, "failed": [], "failedCount": 0, "complete": True,
           "undo": "restore_99999999-2222-4333-8444-555555555555"}


class ProjectRestoreRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = (200, json.dumps(RECEIPT).encode())
        self.calls = []
        owner = self

        class Response:
            content_type = "application/json"

            @property
            def status(self):
                return owner.reply[0]

            @property
            def content(self):
                return self

            async def iter_any(self):
                yield owner.reply[1]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def post(self, url, **options):
                owner.calls.append((url, options))
                return Response()

        self.patches = [patch.object(edge, "config_token", "c" * 64),
                        patch.object(edge, "UnixConnector", return_value=None),
                        patch.object(edge, "ClientSession", return_value=Session())]
        for item in self.patches:
            item.start()
        self.app = web.Application()
        self.app[edge._CANCEL_EVENTS_KEY] = {}
        self.app.router.add_post("/v1/project-restore", edge.handle_project_restore)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:%d/v1/project-restore" % site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        for item in reversed(self.patches):
            item.stop()

    async def test_restore_needs_auth_and_forwards_one_exact_request(self):
        async with self.client.post(self.url, json=REQUEST) as response:
            self.assertEqual(response.status, 401)
        async with self.client.post(self.url, headers=AUTH, json=REQUEST) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), RECEIPT)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.calls, [("http://pixel-upstream/v1/project-restore",
                                       {"json": REQUEST, "headers": {"Accept": "application/json"}, "allow_redirects": False})])

    async def test_only_one_project_folder_and_copy_name_are_accepted(self):
        for body in ({**REQUEST, "project": "Playground/../etc"}, {**REQUEST, "project": "Playground/game/src"},
                     {**REQUEST, "project": "/etc/passwd"}, {**REQUEST, "project": "Other/game"},
                     {**REQUEST, "snapshot": "restore_x"}, {**REQUEST, "user": "../x"}, {**REQUEST, "extra": 1},
                     {"user": "restore-chat", "project": "Playground/game"}, [REQUEST]):
            async with self.client.post(self.url, headers=AUTH, json=body) as response:
                self.assertEqual(response.status, 400, body)
        async with self.client.post(self.url + "?x=1", headers=AUTH, json=REQUEST) as response:
            self.assertEqual(response.status, 400)
        async with self.client.post(self.url, headers=AUTH, data=json.dumps(REQUEST).encode()) as response:
            self.assertEqual(response.status, 415)
        self.assertEqual(self.calls, [])

    async def test_a_stream_of_this_chat_still_passing_through_refuses_the_restore(self):
        self.app[edge._CANCEL_EVENTS_KEY]["restore-chat"] = {object()}
        async with self.client.post(self.url, headers=AUTH, json=REQUEST) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {"error": "project-restore-busy"})
        self.assertEqual(self.calls, [])

    async def test_refusals_keep_their_code_and_any_other_failure_is_a_fixed_503(self):
        for status, error in ((404, "project-snapshot-unavailable"), (409, "project-restore-busy"),
                              (422, "project-snapshot-damaged")):
            self.reply = (status, json.dumps({"error": error, "private": "/home/owner"}).encode())
            async with self.client.post(self.url, headers=AUTH, json=REQUEST) as response:
                self.assertEqual(response.status, status)
                self.assertEqual(await response.json(), {"error": error})
        for status, raw in ((503, b'{"error":"secret detail"}'), (200, b"x" * 17000),
                            (200, json.dumps({**RECEIPT, "project": "Playground/other"}).encode()),
                            (200, json.dumps({**RECEIPT, "undo": "../x"}).encode()),
                            (200, json.dumps({**RECEIPT, "written": True}).encode()),
                            (200, json.dumps({**RECEIPT, "kept": ["a\nb"]}).encode()),
                            (200, json.dumps({**RECEIPT, "extra": "/private"}).encode())):
            self.reply = (status, raw)
            async with self.client.post(self.url, headers=AUTH, json=REQUEST) as response:
                self.assertEqual(response.status, 503)
                self.assertEqual(await response.json(), {"error": "project-restore-unavailable"})

    def test_the_app_routes_the_restore(self):
        routes = {(route.method, route.resource.canonical) for route in edge.create_app().router.routes()}
        self.assertIn(("POST", "/v1/project-restore"), routes)


if __name__ == "__main__":
    unittest.main()

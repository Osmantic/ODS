"""Portal's "Put back files" relays: one exact request in, a validated receipt or options, or a fixed refusal, out."""
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
UNDO = "restore_99999999-2222-4333-8444-555555555555"
TOKEN = "c" * 64
REQUEST = {"user": "restore-chat", "project": "Playground/game", "snapshot": SNAPSHOT}
RECEIPT = {"schemaVersion": 2, "status": "restored", "project": "Playground/game", "snapshot": SNAPSHOT, "written": 1,
           "removed": 2, "left": ["big.bin"], "leftCount": 1, "failed": [], "failedCount": 0, "failure": None,
           "late": False, "undo": UNDO, "confirm": None}
CONFIRM = {**RECEIPT, "status": "confirm", "written": 0, "removed": 0, "undo": None,
           "confirm": {"token": TOKEN, "overwrite": ["chars.js"], "overwriteCount": 1, "remove": [], "removeCount": 0}}
OPTION = {"project": "Playground/game", "snapshot": SNAPSHOT, "late": True, "restore": {"undo": UNDO, "side": "after"}}


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
        self.app.router.add_post("/v1/project-restore-options", edge.handle_project_restore_options)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        base = "http://127.0.0.1:%d" % site._server.sockets[0].getsockname()[1]
        self.url, self.options_url = base + "/v1/project-restore", base + "/v1/project-restore-options"
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        for item in reversed(self.patches):
            item.stop()

    async def post(self, body, url=None):
        async with self.client.post(url or self.url, headers=AUTH, json=body) as response:
            return response.status, await response.json()

    async def test_restore_needs_auth_and_forwards_one_exact_request_with_the_owners_yes(self):
        async with self.client.post(self.url, json=REQUEST) as response:
            self.assertEqual(response.status, 401)
        async with self.client.post(self.url, headers=AUTH, json=REQUEST) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), RECEIPT)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.reply = (200, json.dumps(CONFIRM).encode())
        self.assertEqual(await self.post(REQUEST), (200, CONFIRM))
        self.reply = (200, json.dumps(RECEIPT).encode())
        self.assertEqual(await self.post({**REQUEST, "confirm": TOKEN}), (200, RECEIPT))
        options = {"headers": {"Accept": "application/json"}, "allow_redirects": False}
        self.assertEqual(self.calls, [("http://pixel-upstream/v1/project-restore", {"json": REQUEST, **options}),
                                      ("http://pixel-upstream/v1/project-restore", {"json": REQUEST, **options}),
                                      ("http://pixel-upstream/v1/project-restore",
                                       {"json": {**REQUEST, "confirm": TOKEN}, **options})])

    async def test_only_one_project_folder_and_copy_name_are_accepted(self):
        for body in ({**REQUEST, "project": "Playground/../etc"}, {**REQUEST, "project": "Playground/game/src"},
                     {**REQUEST, "project": "/etc/passwd"}, {**REQUEST, "project": "Other/game"},
                     {**REQUEST, "snapshot": "restore_x"}, {**REQUEST, "user": "../x"}, {**REQUEST, "extra": 1},
                     {**REQUEST, "confirm": "yes"}, {**REQUEST, "confirm": TOKEN.upper()},
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
        self.assertEqual(await self.post(REQUEST), (409, {"error": "project-restore-busy"}))
        self.assertEqual(self.calls, [])

    async def test_refusals_keep_their_fixed_code_and_any_other_failure_is_an_unknown_outcome(self):
        for status, error in ((404, "project-snapshot-unavailable"), (404, "project-snapshot-pruned"),
                              (409, "project-restore-busy"), (422, "project-snapshot-damaged"),
                              (422, "project-folder-unusable"), (503, "project-copies-unusable"),
                              (503, "project-restore-unsupported"), (503, "project-restore-unavailable")):
            self.reply = (status, json.dumps({"error": error, "private": "/home/owner"}).encode())
            self.assertEqual(await self.post(REQUEST), (status, {"error": error}))
        # An unexpected code, or none, is no refusal ODS knows: the outcome is unknown.
        for status, raw in ((404, b'{"error":"project-folder-unusable"}'), (422, b"not json"),
                            (503, b'{"error":"secret detail"}'), (409, b"x" * 2000)):
            self.reply = (status, raw)
            self.assertEqual(await self.post(REQUEST), (503, {"error": "project-restore-unavailable"}), raw[:40])
        for status, body in ((500, {"error": "project-snapshot-pruned"}), (400, {"error": "invalid"}),
                             (200, {**RECEIPT, "project": "Playground/other"}), (200, {**RECEIPT, "undo": "../x"}),
                             (200, {**RECEIPT, "undo": None}), (200, {**RECEIPT, "status": "unchanged"}),
                             (200, {**RECEIPT, "schemaVersion": 1}), (200, {**RECEIPT, "written": True}),
                             (200, {**RECEIPT, "late": 0}), (200, {**RECEIPT, "failure": "disk"}),
                             (200, {**RECEIPT, "failure": ["permission"]}), (200, {**RECEIPT, "left": ["a\nb"]}), (200, {**RECEIPT, "left": ["notes\u0085.md"]}),
                             (200, {**RECEIPT, "extra": "/private"}), (200, {**CONFIRM, "confirm": None}),
                             (200, {**CONFIRM, "confirm": {**CONFIRM["confirm"], "token": "x"}}),
                             (200, {**CONFIRM, "confirm": {**CONFIRM["confirm"], "overwriteCount": 0}}),
                             (200, {**RECEIPT, "confirm": CONFIRM["confirm"]})):
            self.reply = (status, json.dumps(body).encode())
            self.assertEqual(await self.post(REQUEST), (503, {"error": "project-restore-unavailable"}), body)
        self.reply = (200, b"x" * 17000)
        self.assertEqual(await self.post(REQUEST), (503, {"error": "project-restore-unavailable"}))

    async def test_options_forward_the_runs_and_return_only_validated_options(self):
        self.reply = (200, json.dumps({"schemaVersion": 1, "projects": [OPTION]}).encode())
        self.assertEqual(await self.post({"runs": [SNAPSHOT]}, self.options_url), (200, {"schemaVersion": 1, "projects": [OPTION]}))
        self.assertEqual(self.calls, [("http://pixel-upstream/v1/project-restore-options",
                                       {"json": {"runs": [SNAPSHOT]}, "headers": {"Accept": "application/json"},
                                        "allow_redirects": False})])
        for body in ({"runs": [UNDO]}, {"runs": [SNAPSHOT] * 65}, {"runs": SNAPSHOT}, {"runs": [SNAPSHOT], "user": "x"}, [SNAPSHOT]):
            self.assertEqual((await self.post(body, self.options_url))[0], 400, body)
        for status, value in ((200, {"schemaVersion": 1, "projects": [{**OPTION, "snapshot": UNDO}]}),
                              (200, {"schemaVersion": 1, "projects": [{**OPTION, "restore": {"undo": UNDO, "side": "x"}}]}),
                              (200, {"schemaVersion": 1, "projects": [{**OPTION, "late": "yes"}]}),
                              (200, {"schemaVersion": 1, "projects": [{**OPTION, "project": "Playground/a/b"}]}),
                              (200, {"schemaVersion": 1, "projects": [OPTION] * 33}),
                              (200, {"schemaVersion": 1, "projects": [], "x": 1}),
                              (404, {"error": "project-snapshot-pruned"}), (503, {"error": "project-copies-unusable"})):
            self.reply = (status, json.dumps(value).encode())
            self.assertEqual(await self.post({"runs": [SNAPSHOT]}, self.options_url),
                             (503, {"error": "project-restore-unavailable"}), value)

    def test_the_app_routes_the_restore_and_its_options(self):
        routes = {(route.method, route.resource.canonical) for route in edge.create_app().router.routes()}
        self.assertIn(("POST", "/v1/project-restore"), routes)
        self.assertIn(("POST", "/v1/project-restore-options"), routes)


if __name__ == "__main__":
    unittest.main()

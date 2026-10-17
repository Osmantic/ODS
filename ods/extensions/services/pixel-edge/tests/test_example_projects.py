"""The shipped example project relay: list present examples, open one without a model run."""
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

SHA = "a" * 64
SITE = "site-" + SHA[:24]
PREVIEW = {"schemaVersion": 1, "kind": "ods-pixel-workspace-preview", "relativeDirectory": "Playground/elf-of-doom",
           "siteId": SITE, "port": 9437, "url": f"http://{SITE}.localhost:9437/{SITE}/", "files": 9,
           "bytes": 243500, "sha256": SHA, "entrySha256": "b" * 64}
AUTH = {"Authorization": "Bearer " + "c" * 64}


class ExampleProjectRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = (200, json.dumps({"schemaVersion": 1, "projects": ["elf-of-doom"]}).encode())
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

            def get(self, url, **options):
                owner.calls.append(("GET", url, options))
                return Response()

            def post(self, url, **options):
                owner.calls.append(("POST", url, options))
                return Response()

        self.patches = [patch.object(edge, "config_token", "c" * 64),
                        patch.object(edge, "UnixConnector", return_value=None),
                        patch.object(edge, "ClientSession", return_value=Session())]
        for item in self.patches:
            item.start()
        app = web.Application()
        app.router.add_get("/v1/example-projects", edge.handle_example_projects, allow_head=False)
        app.router.add_post("/v1/example-projects", edge.handle_example_projects)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:%d/v1/example-projects" % site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        for item in reversed(self.patches):
            item.stop()

    async def test_list_needs_auth_and_returns_only_the_validated_names(self):
        async with self.client.get(self.url) as response:
            self.assertEqual(response.status, 401)
        async with self.client.get(self.url + "?all=1", headers=AUTH) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])
        async with self.client.get(self.url, headers={**AUTH, "X-Private": "must-not-forward"}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"schemaVersion": 1, "projects": ["elf-of-doom"]})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.calls, [("GET", "http://pixel-upstream/v1/example-projects",
                                       {"headers": {"Accept": "application/json"}, "allow_redirects": False})])

    async def test_open_forwards_one_name_and_returns_the_exact_receipt(self):
        self.reply = (200, json.dumps({"schemaVersion": 1, "preview": PREVIEW}).encode())
        async with self.client.post(self.url, headers=AUTH, json={"name": "elf-of-doom"}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"schemaVersion": 1, "preview": PREVIEW})
        self.assertEqual(self.calls[0][0:2], ("POST", "http://pixel-upstream/v1/example-projects"))
        self.assertEqual(self.calls[0][2]["json"], {"name": "elf-of-doom"})
        for body in ({"name": "../x"}, {"name": "elf-of-doom", "path": "/etc"}, ["elf-of-doom"], {}):
            async with self.client.post(self.url, headers=AUTH, json=body) as response:
                self.assertEqual(response.status, 400, body)
        async with self.client.post(self.url, headers=AUTH, data=b'{"name":"elf-of-doom"}') as response:
            self.assertEqual(response.status, 415)
        self.assertEqual(len(self.calls), 1)

    async def test_missing_example_is_404_refused_files_409_and_any_other_failure_a_fixed_503(self):
        self.reply = (404, b'{"error":"example-project-unavailable","private":"/home/owner"}')
        async with self.client.post(self.url, headers=AUTH, json={"name": "elf-of-doom"}) as response:
            self.assertEqual(response.status, 404)
            self.assertEqual(await response.json(), {"error": "example-project-unavailable"})
        self.reply = (409, b'{"error":"example-project-not-previewable","private":"/home/owner"}')
        async with self.client.post(self.url, headers=AUTH, json={"name": "elf-of-doom"}) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {"error": "example-project-not-previewable"})
        self.reply = (422, b'{"error":"example-project-not-playable","private":"/home/owner"}')
        async with self.client.post(self.url, headers=AUTH, json={"name": "elf-of-doom"}) as response:
            self.assertEqual(response.status, 422)
            self.assertEqual(await response.json(), {"error": "example-project-not-playable"})
        async with self.client.get(self.url, headers=AUTH) as response:
            self.assertEqual(response.status, 503)
        for status, raw in ((503, b'{"error":"secret detail"}'), (200, b"x" * 9000),
                            (200, json.dumps({"schemaVersion": 1, "preview": {**PREVIEW, "relativeDirectory": "Playground/x"}}).encode()),
                            (200, json.dumps({"schemaVersion": 1, "preview": {**PREVIEW, "files": True}}).encode()),
                            (200, json.dumps({"schemaVersion": 1, "preview": {**PREVIEW, "extra": "/private"}}).encode())):
            self.reply = (status, raw)
            async with self.client.post(self.url, headers=AUTH, json={"name": "elf-of-doom"}) as response:
                self.assertEqual(response.status, 503)
                self.assertEqual(await response.json(), {"error": "example-project-preview-unavailable"})
        self.reply = (200, b'{"schemaVersion":1,"projects":["a","a"]}')
        async with self.client.get(self.url, headers=AUTH) as response:
            self.assertEqual(response.status, 503)
            self.assertEqual(await response.json(), {"error": "example-projects-unavailable"})

    def test_the_app_routes_both_methods(self):
        routes = {(route.method, route.resource.canonical) for route in edge.create_app().router.routes()}
        self.assertIn(("GET", "/v1/example-projects"), routes)
        self.assertIn(("POST", "/v1/example-projects"), routes)


if __name__ == "__main__":
    unittest.main()

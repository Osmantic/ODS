"""Authenticated image deletion traverses the real Edge and private Unix socket."""
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from test_pixel_edge import BaseEdgeTest


class TestImageDeletion(BaseEdgeTest):
    async def test_private_receipt_and_scope_are_forwarded_exactly(self):
        calls = []
        receipt = {"schemaVersion": 1, "deleted": True}
        status = 200

        async def upstream(request):
            calls.append(await request.json())
            return web.json_response(receipt, status=status)

        app = web.Application()
        app.router.add_post("/v1/chat/images-delete", upstream)
        runner = web.AppRunner(app)
        await runner.setup()
        socket = str(Path(self.edge_sock).with_suffix(".delete.sock"))
        await web.UnixSite(runner, socket).start()
        try:
            with patch.object(self.pe, "_SOCKET_PATH", socket):
                async def request(body, auth=True, query=""):
                    return await self.client.post("http://localhost/v1/chat/images-delete" + query,
                                                  headers=self.auth() if auth else {}, json=body)
                response = await request({"user": "chat"}, auth=False)
                self.assertEqual(response.status, 401)
                for body in ({"user": "../outside"}, {"user": "chat", "path": "/secret"}, {"user": "x" * 600}):
                    response = await request(body)
                    self.assertEqual(response.status, 400)
                response = await request({"user": "chat"}, query="?path=outside")
                self.assertEqual(response.status, 400)
                self.assertEqual(calls, [])
                response = await request({"user": "chat"})
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), receipt)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(calls, [{"user": "chat"}])
                for bad in ({"schemaVersion": True, "deleted": True}, {"schemaVersion": 1, "deleted": 1},
                            {"schemaVersion": 1, "deleted": True, "path": "private"}, {"deleted": False}):
                    receipt = bad
                    response = await request({"user": "chat"})
                    self.assertEqual(response.status, 503)
                    self.assertNotIn("private", await response.text())
                status = 409
                response = await request({"user": "chat"})
                self.assertEqual(response.status, 409)
                before = len(calls)
                self.edge_app[self.pe._CANCEL_EVENTS_KEY]["chat"] = {object()}
                response = await request({"user": "chat"})
                self.assertEqual(response.status, 409)
                self.assertEqual(len(calls), before)
                self.edge_app[self.pe._CANCEL_EVENTS_KEY].pop("chat")
        finally:
            await runner.cleanup()
            Path(socket).unlink(missing_ok=True)

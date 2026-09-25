import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web, ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('PIXEL_OPENWEBUI_KEY', 'c' * 64)
os.environ.setdefault('PIXEL_PREVIEW_PROXY_KEY', 'o' * 64)
import pixel_edge as edge

SAFE = dict(available=True, surface='wsl-systemd', configured_mode='sandboxed', effective_mode='unknown',
            runtime_verified=False, revision='a'*64, busy=False, pending=False, reason='runtime-proof-required', scope='owner-host')
CHANGE = dict(mode='full-access', revision='a'*64, confirmed=True)


class AccessTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []
        self.reply = {**SAFE, 'token':'private', 'configuration':{'secret':'private'}}
        self.reply_status = 200
        async def upstream(request):
            self.calls.append((request.method, request.headers.get('Authorization'), await request.text()))
            return web.json_response(self.reply, status=self.reply_status)
        app = web.Application()
        app.router.add_route('*', '/v1/access-mode', upstream)
        self.upstream = web.AppRunner(app)
        await self.upstream.setup()
        socket = str(Path(self.directory.name) / 'access.sock')
        await web.UnixSite(self.upstream, socket).start()
        self.patches = [patch.object(edge, '_SOCKET_PATH', socket), patch.object(edge, 'config_token', 'c'*64), patch.object(edge, 'preview_proxy_token', 'o'*64)]
        for item in self.patches: item.start()
        app = web.Application()
        app.router.add_get('/v1/access-mode', edge.handle_access_mode, allow_head=False)
        app.router.add_post('/v1/access-mode', edge.handle_access_mode)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = 'http://127.0.0.1:%d/v1/access-mode' % site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        await self.upstream.cleanup()
        for item in reversed(self.patches): item.stop()
        self.directory.cleanup()

    async def test_model_key_never_authorizes_access(self):
        for key in ('', 'c'*64):
            async with self.client.post(self.url, headers={'Authorization':'Bearer '+key}, json=CHANGE) as response:
                self.assertEqual(response.status, 401)
        self.assertEqual(self.calls, [])

    async def test_owner_status_is_projected_and_route_has_no_ambient_headers(self):
        async with self.client.get(self.url, headers={'Authorization':'Bearer '+'o'*64, 'X-Path':'/other'}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), SAFE)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.calls, [('GET','Bearer '+'o'*64,'')])

    async def test_mutation_is_exact_and_never_retried_after_controller_rejection(self):
        self.reply_status = 409
        self.reply = {'error':'private path or secret'}
        async with self.client.post(self.url, headers={'Authorization':'Bearer '+'o'*64}, json=CHANGE) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {'error':'access-change-unconfirmed'})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.calls[0][2]), CHANGE)

    async def test_invalid_scope_confirmation_and_query_never_reach_controller(self):
        for body in ({**CHANGE,'confirmed':False}, {**CHANGE,'path':'/etc'}, {**CHANGE,'revision':'bad'}, {'padding':'x'*2000}):
            async with self.client.post(self.url, headers={'Authorization':'Bearer '+'o'*64}, json=body) as response:
                self.assertEqual(response.status, 400)
        async with self.client.get(self.url+'?user=other', headers={'Authorization':'Bearer '+'o'*64}) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])

    async def test_effective_mode_requires_consistent_runtime_proof(self):
        self.reply = {**SAFE, 'effective_mode':'full-access'}
        async with self.client.get(self.url, headers={'Authorization':'Bearer '+'o'*64}) as response:
            self.assertEqual(response.status, 503)
            self.assertNotIn('full-access', await response.text())


if __name__ == '__main__': unittest.main()

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

SOURCE = dict(state='ok', errorClass=None, httpStatus=None, lastSuccessAt='2026-10-25T20:29:00Z',
              failingSince=None, recordCount=3, complete=True)
CONNECTED = dict(
    schemaVersion=1, available=True, surface='wsl-systemd', reason=None, ownerTimeZone='America/Chicago',
    connectors=dict(google=dict(
        state='connected', reason=None, account='owner@example.com',
        capabilities={'calendar.read': True, 'calendar.write': False, 'calendar.direct': False,
                      'gmail.read': True, 'gmail.drafts': False},
        cloudModels=True, connectedAt='2026-10-25T20:00:00Z',
        grantedScopes=['https://www.googleapis.com/auth/calendar.events.readonly'],
        checkedAt='2026-10-25T20:29:30Z', sources=dict(email=SOURCE, calendar=SOURCE))))


class ConnectionsRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []
        self.reply = CONNECTED
        self.reply_status = 200

        async def upstream(request):
            self.calls.append((request.method, request.headers.get('Authorization'), await request.text()))
            return web.json_response(self.reply, status=self.reply_status)
        app = web.Application()
        app.router.add_route('*', '/v1/connections', upstream)
        self.upstream = web.AppRunner(app)
        await self.upstream.setup()
        socket = str(Path(self.directory.name) / 'access.sock')
        await web.UnixSite(self.upstream, socket).start()
        self.patches = [patch.object(edge, '_SOCKET_PATH', socket), patch.object(edge, 'config_token', 'c' * 64),
                        patch.object(edge, 'preview_proxy_token', 'o' * 64)]
        for item in self.patches:
            item.start()
        app = web.Application()
        app.router.add_get('/v1/connections', edge.handle_connections, allow_head=False)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = 'http://127.0.0.1:%d/v1/connections' % site._server.sockets[0].getsockname()[1]
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        await self.upstream.cleanup()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    async def test_only_the_owner_key_reads_connected_apps(self):
        for key in ('', 'c' * 64):
            async with self.client.get(self.url, headers={'Authorization': 'Bearer ' + key}) as response:
                self.assertEqual(response.status, 401)
        self.assertEqual(self.calls, [])

    async def test_the_owner_gets_exactly_the_public_status(self):
        async with self.client.get(self.url, headers={'Authorization': 'Bearer ' + 'o' * 64}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), CONNECTED)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.calls, [('GET', 'Bearer ' + 'o' * 64, '')])

    async def test_queries_and_bodies_never_reach_the_controller(self):
        async with self.client.get(self.url + '?account=other', headers={'Authorization': 'Bearer ' + 'o' * 64}) as response:
            self.assertEqual(response.status, 400)
        async with self.client.get(self.url, headers={'Authorization': 'Bearer ' + 'o' * 64}, data=b'{"x":1}') as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])

    async def test_anything_beyond_the_public_shape_is_withheld(self):
        google = dict(CONNECTED['connectors']['google'], token='private-sentinel')
        self.reply = dict(CONNECTED, connectors=dict(google=google))
        async with self.client.get(self.url, headers={'Authorization': 'Bearer ' + 'o' * 64}) as response:
            self.assertEqual(response.status, 503)
            self.assertNotIn('private-sentinel', await response.text())

    async def test_a_controller_refusal_is_not_relayed_as_text(self):
        self.reply_status = 503
        self.reply = {'error': 'private path or secret'}
        async with self.client.get(self.url, headers={'Authorization': 'Bearer ' + 'o' * 64}) as response:
            self.assertEqual(response.status, 503)
            self.assertEqual(await response.json(), {'error': 'connections-unavailable'})


if __name__ == '__main__':
    unittest.main()

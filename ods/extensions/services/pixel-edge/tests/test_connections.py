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
        checkedAt='2026-10-25T20:29:30Z', sources=dict(email=SOURCE, calendar=SOURCE), pending=None)))
OWNER = {'Authorization': 'Bearer ' + 'o' * 64}
FLOW = 'a' * 32
BEGIN = {'connector': 'google', 'capabilities': CONNECTED['connectors']['google']['capabilities'],
         'client': {'clientId': '123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com',
                    'clientSecret': 'test-secret-test', 'projectId': 'ods-owner-calendar'},
         'cloudModels': False, 'ownerTimeZone': 'America/Chicago', 'loginHint': None, 'redirectPort': 53682}


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
        app.router.add_post('/v1/connections', edge.handle_connections)
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

    async def act(self, payload, *, headers=OWNER, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        async with self.client.post(self.url, data=body,
                                    headers={'Content-Type': 'application/json', **headers}) as response:
            return response.status, await response.json(), response.headers.get('Cache-Control')

    async def test_each_custodian_action_reaches_the_controller_exactly(self):
        for payload, reply in (
                ({'operation': 'connection-begin', 'request': BEGIN},
                 {'flowId': FLOW, 'authUrl': 'https://accounts.google.com/o/oauth2/auth?client_id=x',
                  'redirectUri': 'http://127.0.0.1:53682', 'expiresAt': '2026-10-25T20:45:00Z'}),
                ({'operation': 'connection-complete', 'request': {'flowId': FLOW, 'state': 's' * 43, 'code': '4/x'}},
                 {'outcome': 'failed', 'errorClass': 'invalid-grant', 'account': None, 'capabilities': None,
                  'missing': []}),
                ({'operation': 'connection-disconnect', 'request': {'connector': 'google'}},
                 {'outcome': 'disconnected', 'revoked': True}),
                ({'operation': 'connection-activate', 'request': {}}, {'outcome': 'unchanged'})):
            with self.subTest(operation=payload['operation']):
                self.calls.clear()
                self.reply = reply
                self.assertEqual(await self.act(payload), (200, reply, 'no-store'))
                self.assertEqual(len(self.calls), 1)
                method, auth, sent = self.calls[0]
                self.assertEqual((method, auth, json.loads(sent)), ('POST', 'Bearer ' + 'o' * 64, payload))

    async def test_requests_outside_the_contract_never_reach_the_controller(self):
        for payload in ({'operation': 'connection-sync', 'request': {'force': True}},
                        {'operation': 'connection-status', 'request': {}},
                        {'operation': 'connection-begin', 'request': dict(BEGIN, redirectPort=80)},
                        {'operation': 'connection-complete', 'request': {'flowId': FLOW, 'state': 'short', 'code': 'c'}},
                        {'operation': 'connection-sync'}):
            with self.subTest(payload=payload):
                self.assertEqual((await self.act(payload))[:2], (400, {'error': 'invalid-request'}))
        self.assertEqual((await self.act(None, raw=b'{"operation": "connection-sync", "request": {}' + b' ' * 4096 + b'}'))[0], 400)
        self.assertEqual((await self.act({'operation': 'connection-sync', 'request': {}}, headers={}))[0], 401)
        async with self.client.post(self.url, data=b'{}', headers={'Content-Type': 'text/plain', **OWNER}) as response:
            self.assertEqual(response.status, 415)
        async with self.client.post(self.url + '?x=1', json={'operation': 'connection-sync', 'request': {}},
                                    headers=OWNER) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])

    async def test_refusals_keep_only_their_code_and_odd_replies_are_withheld(self):
        sync = {'operation': 'connection-sync', 'request': {}}
        self.reply_status, self.reply = 409, {'error': 'runtime-busy'}
        self.assertEqual((await self.act(sync))[:2], (409, {'error': 'runtime-busy'}))
        self.reply_status, self.reply = 500, {'error': 'private path or secret'}
        self.assertEqual((await self.act(sync))[:2], (503, {'error': 'connection-change-unconfirmed'}))
        self.reply_status, self.reply = 200, {'outcome': 'started', 'token': 'private-sentinel'}
        self.assertEqual((await self.act(sync))[:2], (503, {'error': 'connections-unavailable'}))


if __name__ == '__main__':
    unittest.main()

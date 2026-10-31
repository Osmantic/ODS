"""Actual HTTP relay requests with separate owner and chat credentials."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from aiohttp import web, ClientSession, TCPConnector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('PIXEL_OPENWEBUI_KEY', 'c' * 64)
os.environ.setdefault('PIXEL_PREVIEW_PROXY_KEY', 'o' * 64)
import pixel_edge as edge

PAGE = {'kind': 'tasks', 'items': [], 'offset': 0, 'hasMore': False, 'nextOffset': None,
        'pendingCreates': [], 'pendingCount': 0, 'pendingRemovals': []}


class ScheduleHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls, self.reply, self.reply_status = [], copy.deepcopy(PAGE), 200

        async def upstream(request):
            self.calls.append((request.headers.get('Authorization'), await request.json()))
            return web.json_response(self.reply, status=self.reply_status)

        async def serve(handler):
            app = web.Application()
            app.router.add_post('/v1/schedules', handler)
            runner = web.AppRunner(app)
            await runner.setup()
            self.addAsyncCleanup(runner.cleanup)
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            return 'http://127.0.0.1:%d' % site._server.sockets[0].getsockname()[1]

        origin = await serve(upstream)
        self.patches = [patch.object(edge, '_access_transport', side_effect=lambda: (TCPConnector(), origin)),
                        patch.object(edge, 'config_token', 'c' * 64), patch.object(edge, 'preview_proxy_token', 'o' * 64)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.url = await serve(edge.handle_schedules) + '/v1/schedules'
        self.client = ClientSession()
        self.addAsyncCleanup(self.client.close)

    async def post(self, body, key='o' * 64, suffix=''):
        return await self.client.post(self.url + suffix, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, data=body)

    async def test_owner_credential_is_required_before_dispatch(self):
        for key in ('', 'c' * 64):
            async with await self.post('invalid json', key) as response:
                self.assertEqual(response.status, 401)
        self.assertEqual(self.calls, [])

    async def test_valid_request_has_no_ambient_authority_or_cached_reply(self):
        request = {'action': 'list', 'offset': 0}
        async with await self.post(json.dumps(request)) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), PAGE)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.calls, [('Bearer ' + 'o' * 64, request)])

    async def test_invalid_fields_duplicates_and_query_never_reach_ingress(self):
        for raw in ('{"action":"list","action":"list","offset":0}',
                    '{"action":"list","offset":0,"token":"other"}', '{"action":"list","offset":NaN}', ' ' * 16385):
            async with await self.post(raw) as response:
                self.assertEqual(response.status, 400)
        async with await self.post('{"action":"list","offset":0}', suffix='?owner=other') as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])

    async def test_private_results_and_arbitrary_diagnostics_are_not_relayed(self):
        self.reply = dict(PAGE, token='private')
        async with await self.post('{"action":"list","offset":0}') as response:
            self.assertEqual(response.status, 503)
            self.assertNotIn('private', await response.text())
        self.reply_status, self.reply = 409, {'error': '/private/config token'}
        async with await self.post('{"action":"list","offset":0}') as response:
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {'error': 'scheduler-unavailable'})
        self.assertEqual(len(self.calls), 2)

    async def test_shared_chat_and_owner_key_disables_the_route(self):
        with patch.object(edge, 'config_token', 'o' * 64):
            async with await self.post('{"action":"list","offset":0}') as response:
                self.assertEqual(response.status, 503)
        self.assertEqual(self.calls, [])

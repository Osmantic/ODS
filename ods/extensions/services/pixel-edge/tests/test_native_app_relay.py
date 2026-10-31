"""Reviewed-app actions across actual owner-authenticated Edge HTTP and Unix HTTP."""
import json
import unittest

import test_connections as fixture
import native_app_contract as wire


def state(phase='absent'):
    value = {'app': 'notion', 'phase': phase, 'flowId': None if phase == 'absent' else fixture.FLOW,
             'error': None, 'portalAccessVerified': phase == 'ready', 'scope': wire.SCOPE}
    if phase == 'ready':
        value['checkedAt'] = '2026-10-28T12:00:00Z'
    if phase == 'disconnected':
        value['providerRevoked'] = False
    return value


class NativeAppRelayTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ConnectionsRelayTests.asyncSetUp
    asyncTearDown = fixture.ConnectionsRelayTests.asyncTearDown
    act = fixture.ConnectionsRelayTests.act

    async def test_all_fixed_actions_have_their_exact_public_reply_and_budget(self):
        for operation, request, reply in (
            ('connection-app-status', {}, {'schemaVersion': 1, 'available': True, 'surface': 'wsl-systemd',
                                          'reason': None, 'apps': {'notion': state()}}),
            ('connection-app-begin', {'app': 'notion', 'redirectPort': 43123}, dict(state('consent'),
                authUrl='https://provider.example/auth?state=x', expiresAt='2026-10-28T12:15:00Z')),
            ('connection-app-complete', {'app': 'notion', 'flowId': fixture.FLOW,
                'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-callback'}, state('authorized')),
            ('connection-app-finish', {'app': 'notion', 'flowId': fixture.FLOW}, state('ready')),
            ('connection-app-cancel', {'app': 'notion', 'flowId': fixture.FLOW}, state('cancelled')),
            ('connection-app-disconnect', {'app': 'notion'}, state('disconnected')),
            ('connection-app-check', {'app': 'notion'}, dict(state('ready'), flowId=None)),
        ):
            with self.subTest(operation=operation):
                self.calls.clear()
                self.reply = reply
                payload = {'operation': operation, 'request': request}
                self.assertEqual(await self.act(payload), (200, reply, 'no-store'))
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(json.loads(self.calls[0][2]), payload)
                self.assertEqual(self.calls[0][1], 'Bearer ' + 'o' * 64)
                self.assertEqual(fixture.edge._CONNECTION_TIMEOUTS[operation], wire.TIMEOUTS[operation] - 2)

    async def test_only_native_complete_can_cross_the_larger_bounded_frame(self):
        self.reply = state('authorized')
        payload = {'operation': 'connection-app-complete', 'request': {'app': 'notion', 'flowId': fixture.FLOW,
            'address': 'http://127.0.0.1:43123/?state=' + 's' * 128 + '&code=PRIVATE-' + 'c' * 2000}}
        self.assertGreater(len(json.dumps(payload).encode()), 2048)
        self.assertEqual((await self.act(payload))[:2], (200, self.reply))
        before = len(self.calls)
        ordinary = json.dumps({'operation': 'connection-sync', 'request': {}}).encode() + b' ' * 2048
        self.assertEqual((await self.act(None, raw=ordinary))[0], 400)
        huge = json.dumps(payload).encode() + b' ' * 16384
        result = await self.act(None, raw=huge)
        self.assertEqual(result[0], 400)
        self.assertNotIn('PRIVATE-', json.dumps(result))
        self.assertEqual(len(self.calls), before)

    async def test_no_new_owner_configuration_or_ordinary_chat_authority_reaches_root(self):
        for key in ('server', 'url', 'tools', 'owner', 'configPath', 'transactionId', 'client'):
            payload = {'operation': 'connection-app-begin', 'request': {'app': 'notion', 'redirectPort': 43123,
                key: 'PRIVATE-code-token'}}
            result = await self.act(payload)
            self.assertEqual(result[0], 400)
            self.assertNotIn('PRIVATE-', json.dumps(result))
        self.assertEqual((await self.act({'operation': 'connection-app-status', 'request': {}},
            headers={'Authorization': 'Bearer ' + 'c' * 64}))[0], 401)
        self.assertEqual(self.calls, [])

    async def test_tokens_launch_parameters_and_false_readiness_are_never_forwarded(self):
        for change in (lambda v: v.update(token='PRIVATE-token'), lambda v: v.update(configPath='/private/path'),
                       lambda v: v.update(portalAccessVerified=True), lambda v: v.update(scope='all-conversations')):
            self.reply = state('authorized')
            change(self.reply)
            result = await self.act({'operation': 'connection-app-complete', 'request': {'app': 'notion',
                'flowId': fixture.FLOW, 'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}})
            self.assertEqual(result[:2], (503, {'error': 'connections-unavailable'}))
            self.assertNotIn('PRIVATE', json.dumps(result))
        self.reply = dict(state('disconnected'), providerRevoked=True)
        self.assertEqual((await self.act({'operation': 'connection-app-disconnect', 'request': {'app': 'notion'}}))[0], 503)
        self.reply = state('ready')
        self.assertEqual((await self.act({'operation': 'connection-app-complete', 'request': {'app': 'notion',
            'flowId': fixture.FLOW, 'address': 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}}))[0], 503)

    async def test_malformed_callback_and_provider_error_bodies_are_fixed_errors(self):
        result = await self.act(None, raw=b'{"operation":"connection-app-complete","request":"PRIVATE-code')
        self.assertEqual(result[:2], (400, {'error': 'invalid-request'}))
        self.assertEqual(self.calls, [])
        self.reply_status, self.reply = 503, {'error': 'PRIVATE-token provider text /private/path'}
        result = await self.act({'operation': 'connection-app-finish', 'request': {'app': 'notion', 'flowId': fixture.FLOW}})
        self.assertEqual(result[:2], (503, {'error': 'connection-change-unconfirmed'}))


if __name__ == '__main__':
    unittest.main()

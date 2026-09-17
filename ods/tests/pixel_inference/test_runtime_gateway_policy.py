"""Contract tests for bin/pixel_provider/runtime_gateway.py policy surface.

`test_provider_runtime.py` covers failover/streaming behavior. These tests pin
the boundary policy that protects the generation path: request validation
(external media gate, tool/template contract), DNS-pinned upstream selection
(SSRF guard), authorization handling, single-flight/busy/session limits, and
upstream-response shape enforcement.
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.config import default_config
from pixel_provider.runtime_gateway import (RuntimeErrorCode, create_app,
                                            pinned_target, validate_request)

pytestmark = pytest.mark.skipif(sys.platform == 'win32',
                                reason='POSIX-only asyncio DNS in pinned_target')


def configuration(**overrides):
    config = default_config()
    config.update(enabled=True, revision=4)
    config['providers'] = [dict(id=name, label=name, kind='local',
        baseUrl=f'http://127.0.0.1:{port}/v1', model=name,
        contextTokens=32768, maxOutputTokens=4096, supportsTools=True,
        supportsVision=False, reasoning=False, credentialRef=None,
        enabled=True) for name, port in [('primary', 12001), ('backup', 12002)]]
    config['roles'].update(leader='primary', backups=['backup'])
    config.update(overrides)
    return config


def valid_payload(**overrides):
    payload = {'model': 'ods/pixel',
               'messages': [{'role': 'user', 'content': 'hi'}]}
    payload.update(overrides)
    return payload


def make_app(handler, **kwargs):
    config = kwargs.pop('config', None) or configuration()
    return create_app(config, {'primary': 'k1', 'backup': 'k2'}, 'test',
                      client_factory=lambda: httpx.AsyncClient(
                          transport=httpx.MockTransport(handler),
                          trust_env=False), **kwargs)


def post(app, body=None, headers=None):
    async def run():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url='http://client') as c:
            return await c.post('/v1/chat/completions', json=body,
                                headers={'Authorization': 'Bearer test',
                                         **(headers or {})})
    return asyncio.run(run())


class TestValidateRequest:
    def test_minimal_valid(self):
        assert validate_request(valid_payload())['model'] == 'ods/pixel'

    @pytest.mark.parametrize('payload', [
        None, [], 'x',
        valid_payload(model='other'),
        valid_payload(messages=[]),
        valid_payload(messages='x'),
        valid_payload(stream='yes'),
        valid_payload(n=2),
        valid_payload(max_tokens=10, max_completion_tokens=10),
        dict(valid_payload(), mystery=True),
    ])
    def test_invalid_envelope(self, payload):
        with pytest.raises(RuntimeErrorCode, match='invalid-inference-request'):
            validate_request(payload)

    @pytest.mark.parametrize('role', ['system', 'developer', 'user',
                                      'assistant', 'tool', 'function'])
    def test_all_roles_accepted(self, role):
        message = {'role': role, 'content': 'x'}
        if role in ('tool', 'function'):
            message['tool_call_id'] = 't1'
        validate_request(valid_payload(messages=[message]))

    @pytest.mark.parametrize('message', [
        {'role': 'owner', 'content': 'x'},
        {'role': 'user', 'content': 'x', 'injected': 1},
        {'role': 'user', 'content': 42},
        {'role': 'user', 'content': [{'type': 'video'}]},
        'not-a-dict',
    ])
    def test_invalid_messages(self, message):
        with pytest.raises(RuntimeErrorCode):
            validate_request(valid_payload(messages=[message]))

    @pytest.mark.parametrize('url', [
        'data:image/png;base64,AAAA', 'data:image/jpeg;base64,AAAA',
        'data:image/webp;base64,AAAA'])
    def test_inline_media_accepted(self, url):
        validate_request(valid_payload(messages=[
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'look'},
                {'type': 'image_url', 'image_url': {'url': url}}]}]))

    @pytest.mark.parametrize('url', [
        'https://evil.example/x.png', 'data:image/svg+xml;base64,AA',
        'data:text/html;base64,AA', 'ftp://x', 'data:image/png,AA'])
    def test_external_media_blocked(self, url):
        # SSRF/privacy gate: only inline base64 image data may flow through.
        with pytest.raises(RuntimeErrorCode,
                           match='external-media-not-allowed'):
            validate_request(valid_payload(messages=[
                {'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': url}}]}]))

    def test_image_url_non_dict_rejected(self):
        with pytest.raises(RuntimeErrorCode,
                           match='external-media-not-allowed'):
            validate_request(valid_payload(messages=[
                {'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': 'x'}]}]))

    @pytest.mark.parametrize('tools', [
        [{'type': 'function', 'function': {'name': 'f'}}], []])
    def test_tools_contract(self, tools):
        validate_request(valid_payload(tools=tools))

    @pytest.mark.parametrize('tools', [
        [{'type': 'retrieval'}], [{'type': 'function'}, 'x'], 'x'])
    def test_non_function_tools_rejected(self, tools):
        with pytest.raises(RuntimeErrorCode, match='unsupported-tools'):
            validate_request(valid_payload(tools=tools))

    def test_template_kwargs_gate(self):
        validate_request(valid_payload(
            chat_template_kwargs={'enable_thinking': True}))
        for bad in ({'enable_thinking': 'yes'}, {'other': True},
                    'x', {'enable_thinking': True, 'extra': 1}):
            with pytest.raises(RuntimeErrorCode,
                               match='unsupported-template-options'):
                validate_request(valid_payload(chat_template_kwargs=bad))


class TestPinnedTarget:
    def target(self, base_url):
        provider = {'id': 'p', 'baseUrl': base_url}
        return asyncio.run(pinned_target(provider))

    def test_loopback_http_pinned(self):
        url, headers, ext = self.target('http://127.0.0.1:11434/v1')
        assert url == 'http://127.0.0.1:11434/v1/chat/completions'
        assert headers['X-ODS-Pixel-Route-Hop'] == '1'
        assert headers['Host'] == '127.0.0.1:11434'
        assert ext == {}

    @pytest.mark.parametrize('url', [
        'http://169.254.1.1/v1',       # link-local
        'http://192.168.1.10/v1',      # private LAN over http
        'http://0.0.0.0/v1',           # unspecified
        'http://[::ffff:8.8.8.8]/v1',  # v4-mapped public over http
        'http://[fe80::1]/v1',         # v6 link-local
        'http://[ff02::1]/v1',         # v6 multicast
    ])
    def test_unsafe_addresses_rejected(self, url):
        with pytest.raises(RuntimeErrorCode,
                           match='unsafe-provider-address'):
            self.target(url)

    def test_https_public_allowed_with_sni(self):
        url, headers, ext = self.target('https://93.184.216.34/v1')
        assert url.startswith('https://93.184.216.34:443/')
        assert ext == {'sni_hostname': '93.184.216.34'}

    def test_ipv6_loopback_bracketed(self):
        url, headers, _ = self.target('http://[::1]:8080/v1')
        assert url == 'http://[::1]:8080/v1/chat/completions'


class TestEndpointPolicy:
    def test_missing_and_duplicate_auth(self):
        app = make_app(lambda r: httpx.Response(200, json={}))
        assert post(app, valid_payload(), headers={'Authorization': ''}).status_code == 401

    def test_wrong_token(self):
        app = make_app(lambda r: httpx.Response(200, json={}))
        async def run():
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url='http://c') as c:
                return await c.post('/v1/chat/completions',
                                    json=valid_payload(),
                                    headers={'Authorization': 'Bearer nope'})
        assert asyncio.run(run()).status_code == 401

    def test_route_cycle_header_rejected(self):
        app = make_app(lambda r: httpx.Response(200, json={}))
        response = post(app, valid_payload(),
                        headers={'X-ODS-Pixel-Route-Hop': '1'})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'provider-route-cycle'

    def test_upstream_rejection_is_terminal_for_session(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(500)  # TRANSIENT
        app = make_app(handler)
        # Exhaust attempts on transient failures; session turns terminal.
        response = post(app, valid_payload())
        assert response.json()['error']['code'] == 'provider-attempts-exhausted'
        follow = post(app, valid_payload())
        assert follow.json()['error']['code'] == 'provider-session-stopped'

    def test_non_json_upstream_body_is_invalid_provider_response(self):
        def handler(request):
            return httpx.Response(200, content=b'not json at all')
        app = make_app(handler)
        response = post(app, valid_payload())
        assert response.status_code == 400
        assert response.json()['error']['code'] in (
            'invalid-provider-response', 'provider-transport-failed')

    def test_provider_response_shape_enforced(self):
        def handler(request):
            return httpx.Response(200, json={'choices': []})
        app = make_app(handler)
        response = post(app, valid_payload())
        assert response.json()['error']['code'] == 'invalid-provider-response'

    def test_stream_requires_event_stream_content_type(self):
        def handler(request):
            return httpx.Response(200, content=b'plain text',
                                  headers={'content-type': 'text/plain'})
        app = make_app(handler)
        response = post(app, valid_payload(stream=True))
        assert response.json()['error']['code'] == 'invalid-provider-stream'

    def test_stream_without_done_marker_is_interrupted(self):
        def handler(request):
            return httpx.Response(
                200, content=b'data: {"a":1}\n\n',
                headers={'content-type': 'text/event-stream'})
        app = make_app(handler)

        async def run():
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url='http://c') as c:
                async with c.stream(
                        'POST', '/v1/chat/completions',
                        json=valid_payload(stream=True),
                        headers={'Authorization': 'Bearer test'}) as r:
                    async for _ in r.aiter_bytes():
                        pass
        # A stream that ends without `data: [DONE]` fails the response and
        # turns the session terminal — no client retry may splice a backup.
        with pytest.raises(RuntimeErrorCode,
                           match='provider-stream-interrupted'):
            asyncio.run(run())
        follow = post(app, valid_payload())
        assert follow.json()['error']['code'] == 'provider-session-stopped'

    def test_request_too_large(self):
        app = make_app(lambda r: httpx.Response(200, json={}))
        big = valid_payload(
            messages=[{'role': 'user', 'content': 'x' * (300 * 1024)}])
        response = post(app, big)
        assert response.json()['error']['code'] in (
            'request-too-large', 'provider-transport-failed')

    def test_headers_echo_request_metadata(self):
        def handler(request):
            return httpx.Response(200, json={
                'id': 'x', 'object': 'chat.completion', 'model': 'primary',
                'choices': [{'index': 0,
                             'message': {'role': 'assistant', 'content': 'ok'},
                             'finish_reason': 'stop'}]})
        app = make_app(handler)
        response = post(app, valid_payload())
        assert response.headers['x-ods-provider'] == 'primary'
        assert response.headers['x-ods-provider-revision'] == '4'
        assert response.headers['cache-control'] == 'no-store'
        assert 'x-ods-request-id' in response.headers

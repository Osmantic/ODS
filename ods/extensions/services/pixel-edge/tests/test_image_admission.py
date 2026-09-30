import asyncio
import base64
import hashlib
import gzip
import json
import threading
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_pixel_edge import BaseEdgeTest


def image_request(size=64):
    raw = b'x' * size
    reference = {'id': 'img-' + 'a' * 32, 'sha256': hashlib.sha256(raw).hexdigest()}
    archived = {'role': 'user', 'content': '', 'images': [reference]}
    current = {**archived, 'content': [{'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')}}]}
    return {'model': 'portal/default', 'stream': False, 'user': 'image-test',
            'request_id': 'image-request', 'messages': [current],
            'history_snapshot': {'schemaVersion': 2, 'messages': [archived]},
            'image_route': {'routeFingerprint': 'c' * 64, 'unknownConsent': False}}


class ImageAdmissionTest(BaseEdgeTest):
    def headers(self):
        return {**self.auth(), 'Content-Type': 'application/json', 'X-ODS-Image-Turn': '1'}

    async def test_image_forward_serialization_is_chunked_and_lossless(self):
        data = image_request(128 * 1024)
        data['escaped'] = '\\"\r\n\x00\U0001f642' * 20000
        chunks = [chunk async for chunk in self.pe._encoded_image_envelope(data)]
        self.assertGreater(len(chunks), 10)
        self.assertTrue(all(0 < len(chunk) <= 32768 for chunk in chunks))
        self.assertEqual(json.loads(b''.join(chunks)), data)

    async def test_marked_image_over_legacy_size_reaches_upstream_with_bound_header(self):
        # The old eight MiB transport rejects this valid seven MiB image.
        self.up_runner.app._client_max_size = 16 * 1024 * 1024 + 1
        observed = []
        original = self.pe.ClientSession
        class Session:
            def __init__(self, *args, **kwargs):
                self.actual = original(*args, **kwargs)
            async def __aenter__(self):
                await self.actual.__aenter__()
                return self
            async def __aexit__(self, *args):
                return await self.actual.__aexit__(*args)
            def post(self, *args, **kwargs):
                observed.append(kwargs['headers'])
                return self.actual.post(*args, **kwargs)
        with patch.object(self.pe, 'ClientSession', Session):
            response = await self.client.post('http://edge/v1/chat/completions',
                headers=self.headers(), json=image_request(7 * 1024 * 1024))
            self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(observed[0]['X-ODS-Image-Turn'], '1')
        self.assertEqual(len(self.up_runner.app['chat_requests']), 1)

    async def test_header_cannot_enlarge_text_or_omit_archive_or_escape_image_slot(self):
        for data, headers in (({'model': 'portal/default', 'messages': [{'role': 'user', 'content': 'hi'}]}, self.headers()),
                              (image_request(), self.auth())):
            response = await self.client.post('http://edge/v1/chat/completions', headers=headers, json=data)
            self.assertEqual(response.status, 400, await response.text())
        self.assertEqual(self.up_runner.app['chat_requests'], [])
        headers = {**self.headers(), 'X-ODS-Image-Turn': '2'}
        response = await self.client.post('http://edge/v1/chat/completions', headers=headers, json=image_request())
        self.assertEqual(response.status, 400)

    async def test_chunked_image_cap_and_ordinary_text_cap_remain_distinct(self):
        with patch.object(self.pe, '_MAX_IMAGE_BODY', 100), patch.object(self.pe, '_MAX_BODY', 80):
            async def chunks():
                yield b' ' * 60
                yield b' ' * 41
            response = await self.client.post('http://edge/v1/chat/completions', headers=self.headers(), data=chunks())
            self.assertEqual(response.status, 413)
            response = await self.client.post('http://edge/v1/chat/completions', headers={**self.auth(), 'Content-Type': 'application/json'}, data=b' ' * 81)
            self.assertEqual(response.status, 413)
        response = await self.client.post('http://edge/v1/chat/completions', headers=self.headers(), json=image_request())
        self.assertEqual(response.status, 200, await response.text())

    async def test_held_image_admission_has_no_queue_and_does_not_block_text_or_health(self):
        lease = self.edge_app[self.pe._IMAGE_WORK_KEY].acquire()
        try:
            response = await self.client.post('http://edge/v1/chat/completions', headers=self.headers(), json=image_request())
            self.assertEqual(response.status, 429)
            self.assertEqual(response.headers['Retry-After'], '1')
            legacy = await self.client.post('http://edge/v1/chat/completions', headers=self.auth(), json={
                'model': 'portal/default', 'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA=='}}]}]})
            self.assertEqual(legacy.status, 429)
            health = await self.client.get('http://edge/health')
            self.assertEqual(health.status, 200)
            text = await self.client.post('http://edge/v1/chat/completions', headers=self.auth(), json={
                'model': 'portal/default', 'messages': [{'role': 'user', 'content': 'hello'}]})
            self.assertEqual(text.status, 200, await text.text())
        finally:
            lease.release()

    async def test_cancel_during_parse_does_not_free_slot_until_worker_finishes(self):
        started, release = threading.Event(), threading.Event()
        parse = self.pe._parse_image_envelope
        def blocked(raw):
            started.set()
            assert release.wait(3)
            return parse(raw)
        request = image_request()
        body = json.dumps(request).encode()
        class Content:
            async def iter_any(self):
                yield body
        class Request:
            app = self.edge_app
            content = Content()
            content_type = 'application/json'
            content_length = len(body)
            headers = web.Response(headers=self.headers()).headers
        with patch.object(self.pe, '_parse_image_envelope', blocked):
            task = asyncio.create_task(self.pe.handle_chat_completions(Request()))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(started.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIsNone(self.edge_app[self.pe._IMAGE_WORK_KEY].acquire())
            finally:
                release.set()
            for _ in range(100):
                lease = self.edge_app[self.pe._IMAGE_WORK_KEY].acquire()
                if lease:
                    lease.release()
                    break
                await asyncio.sleep(.01)
            else:
                self.fail('worker completion did not release reservation')

    async def test_incomplete_chunked_upload_times_out_without_losing_slot(self):
        class Content:
            async def iter_any(self):
                yield b'{'
                await asyncio.Event().wait()
        class Request:
            app = self.edge_app
            content = Content()
            content_type = 'application/json'
            content_length = None
            headers = web.Response(headers=self.headers()).headers
        with patch.object(self.pe, '_IMAGE_BODY_TIMEOUT', .01):
            response = await self.pe.handle_chat_completions(Request())
        self.assertEqual(response.status, 408)
        lease = self.edge_app[self.pe._IMAGE_WORK_KEY].acquire()
        self.assertIsNotNone(lease)
        lease.release()

    async def test_compressed_chat_is_rejected_without_automatic_transport_inflation(self):
        compressed = gzip.compress(b' ' * (20 * 1024 * 1024))
        response = await self.client.post('http://edge/v1/chat/completions',
            headers={**self.headers(), 'Content-Encoding': 'gzip'}, data=compressed)
        self.assertEqual(response.status, 415)
        self.assertEqual(self.up_runner.app['chat_requests'], [])
        # Exercise the actual aiohttp parser setting, not just our route's
        # early rejection: a fixture route sees exactly the compressed bytes.
        app = self.pe.create_app()
        async def inspect(request):
            raw = await request.read()
            return web.json_response({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        app.router.add_post('/fixture/parser', inspect)
        async with TestClient(TestServer(app)) as client:
            result = await client.post('/fixture/parser', data=compressed, headers={'Content-Encoding': 'gzip'})
            self.assertEqual(result.status, 200)
            self.assertEqual(await result.json(), {'bytes': len(compressed), 'sha256': hashlib.sha256(compressed).hexdigest()})

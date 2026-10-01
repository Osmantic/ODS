import asyncio
from pathlib import Path
import threading

from fastapi import HTTPException, Response
import pytest

from pixel_image_admission import ImageWorkBudget
from routers import pixel_images


class Upload:
    def __init__(self, chunks=(), *, wait=None, length=None):
        self.headers = {"content-type": "image/png"}
        if length is not None:
            self.headers['content-length'] = str(length)
        self.chunks = chunks
        self.wait = wait
        self.reads = 0

    async def stream(self):
        self.reads += 1
        if self.wait is not None:
            await self.wait.wait()
        for chunk in self.chunks:
            yield chunk


@pytest.fixture(autouse=True)
def budget(monkeypatch):
    monkeypatch.setattr(pixel_images, '_image_work', ImageWorkBudget(1))


def test_busy_upload_rejects_before_collecting_any_bytes(monkeypatch):
    async def scenario():
        held = pixel_images._image_work.acquire()
        request = Upload([b'content'])
        try:
            with pytest.raises(HTTPException) as error:
                await pixel_images.upload_image('chat', request, Response(), 'owner')
            assert error.value.status_code == 429
            assert error.value.headers == {'Retry-After': '1'}
            assert request.reads == 0
        finally:
            held.release()
        monkeypatch.setattr(pixel_images, '_storage_call', lambda *args: {'id': 'fixture'})
        assert (await pixel_images.upload_image('chat', request, Response(), 'owner'))['bytes'] == 7
    asyncio.run(scenario())


def test_canceled_upload_keeps_slot_until_decoder_has_really_exited(monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def decode(*_args):
        started.set()
        assert finish.wait(3)
        return {'id': 'fixture'}
    monkeypatch.setattr(pixel_images, '_storage_call', decode)
    async def scenario():
        job = asyncio.create_task(pixel_images.upload_image('chat', Upload([b'bytes']), Response(), 'owner'))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.01)
            assert started.is_set()
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            rejected = Upload([b'next'])
            with pytest.raises(HTTPException) as error:
                await pixel_images.upload_image('other', rejected, Response(), 'owner')
            assert error.value.status_code == 429 and rejected.reads == 0
        finally:
            finish.set()
        for _ in range(100):
            lease = pixel_images._image_work.acquire()
            if lease is not None:
                lease.release()
                return
            await asyncio.sleep(.01)
        pytest.fail('decoder completion did not release its reservation')
    asyncio.run(scenario())


def test_chunked_oversize_and_read_cancellation_release_upload_slot(monkeypatch):
    monkeypatch.setattr(pixel_images, 'MAX_IMAGE_BYTES', 6)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await pixel_images.upload_image('chat', Upload([b'1234', b'5678']), Response(), 'owner')
        assert error.value.status_code == 413
        request = Upload(wait=asyncio.Event())
        job = asyncio.create_task(pixel_images.upload_image('chat', request, Response(), 'owner'))
        await asyncio.sleep(0)
        assert request.reads == 1
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        lease = pixel_images._image_work.acquire()
        assert lease is not None
        lease.release()
    asyncio.run(scenario())


def test_content_length_rejected_before_reservation_or_read():
    async def scenario():
        held = pixel_images._image_work.acquire()
        request = Upload(length=pixel_images.MAX_IMAGE_BYTES + 1)
        try:
            with pytest.raises(HTTPException) as error:
                await pixel_images.upload_image('chat', request, Response(), 'owner')
            assert error.value.status_code == 413 and request.reads == 0
        finally:
            held.release()
    asyncio.run(scenario())


def test_slow_upload_deadline_does_not_starve_following_work(monkeypatch):
    monkeypatch.setattr(pixel_images, '_UPLOAD_TIMEOUT_SECONDS', .01)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await pixel_images.upload_image('chat', Upload(wait=asyncio.Event()), Response(), 'owner')
        assert error.value.status_code == 408
        lease = pixel_images._image_work.acquire()
        assert lease is not None
        lease.release()
    asyncio.run(scenario())


def test_resolution_and_reads_share_no_queue_worker_limit(monkeypatch):
    called = []
    monkeypatch.setattr(pixel_images, '_storage_call', lambda *args: called.append(args))
    async def scenario():
        lease = pixel_images._image_work.acquire()
        try:
            with pytest.raises(HTTPException) as error:
                await pixel_images._call('get', 'owner', 'chat', 'id')
            assert error.value.status_code == 429 and called == []
        finally:
            lease.release()
    asyncio.run(scenario())


def test_nginx_upload_limit_is_scoped_and_keeps_admin_gate():
    path = Path(__file__).resolve().parents[2] / 'dashboard/nginx.conf'
    source = path.read_text()
    start = source.index('    location ~ "^/api/pixel/images/')
    end = source.index('\n    }', start)
    image = source[start:end]
    assert 'client_max_body_size 8m;' in image
    assert 'auth_request /_ods_dashboard_gate;' in image
    assert 'proxy_request_buffering off;' in image
    assert 'client_body_timeout 30s;' in image
    ordinary = source[source.index('    location /api/ {'):]
    ordinary = ordinary[:ordinary.index('\n    }')]
    assert 'client_max_body_size' not in ordinary

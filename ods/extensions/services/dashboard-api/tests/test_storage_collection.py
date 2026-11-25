"""Settings reads share one real disk traversal even after browser timeouts."""

import asyncio
import subprocess
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import main
import helpers
from helpers import clear_dir_size_cache
from routers import resources

HEADERS = {"Authorization": "Bearer test-key-12345"}
URL = "/api/storage"


def _sparse_file(path: Path, size: int) -> None:
    """Create a file of `size` bytes that takes no disk space.

    NTFS allocates every byte a file grows by, through truncate() too, unless
    the file is marked sparse first: each use of this fixture took 4 GiB on
    Windows, and one full suite run 16 GiB, which filled a contributor's disk.
    """
    path.touch()
    if sys.platform == "win32":
        subprocess.run(["fsutil", "sparse", "setflag", str(path)], check=True, capture_output=True)
    with path.open("r+b") as stream:
        stream.seek(size - 1)
        stream.write(b"\0")


@pytest.fixture
def storage_source(tmp_path, monkeypatch):
    main._cache.invalidate("storage")
    clear_dir_size_cache()
    for directory, gib in [("models", 2), ("qdrant", 1), ("n8n", 1)]:
        child = tmp_path / directory
        child.mkdir()
        _sparse_file(child / "data.bin", gib * 1024**3)
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "get_disk_usage", lambda: SimpleNamespace(
        used_gb=6, total_gb=10, percent=60,
    ))
    release = Event()
    original_size = main.dir_size_gb
    original_get = helpers._dir_size_cache.get

    def cache_get(path):
        cached = original_get(path)
        if path == tmp_path / "models" and cached is None:
            # Pause after the real cache-miss decision. Concurrent old-code
            # workers must each walk the tree, rather than benefiting from a
            # first worker that happens to finish before the others start.
            assert release.wait(10), "test must release the disk traversal"
        return cached

    monkeypatch.setattr(helpers._dir_size_cache, "get", cache_get)
    original_walk = Path.rglob
    walks = Mock(side_effect=lambda path, pattern: original_walk(path, pattern))
    monkeypatch.setattr(Path, "rglob", lambda path, pattern: walks(path, pattern))

    def size(path):
        return original_size(path)

    collector = Mock(side_effect=size)
    monkeypatch.setattr(main, "dir_size_gb", collector)
    collector.walks = walks
    yield release, collector
    release.set()
    assert "storage" not in resources._resource_refreshes
    main._cache.invalidate("storage")
    clear_dir_size_cache()


@pytest.mark.parametrize("cancel_count", [0, 1, 8])
def test_settings_reads_collect_once_across_timeouts(storage_source, monkeypatch, cancel_count):
    release, collector = storage_source

    async def scenario():
        admitted = set()
        all_admitted = asyncio.Event()
        original_get = main._cache.get

        def get(key):
            if key == "storage":
                admitted.add(asyncio.current_task())
                if len(admitted) >= 8:
                    all_admitted.set()
            return original_get(key)

        monkeypatch.setattr(main._cache, "get", get)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            requests = [asyncio.create_task(client.get(URL, headers=HEADERS)) for _ in range(8)]
            try:
                await asyncio.wait_for(all_admitted.wait(), 5)
                for request in requests[:cancel_count]:
                    request.cancel()
                await asyncio.gather(*requests[:cancel_count], return_exceptions=True)
                remaining = requests[cancel_count:]
                if not remaining:
                    # A retry must join the collector abandoned by every tab.
                    remaining = [asyncio.create_task(client.get(URL, headers=HEADERS))]
            finally:
                release.set()
            responses = await asyncio.gather(*remaining)
            expected = {
                "models": {"formatted": "2.0 GB", "gb": 2, "percent": 20},
                "vector_db": {"formatted": "1.0 GB", "gb": 1, "percent": 10},
                "total_data": {"formatted": "4.0 GB", "gb": 4, "percent": 40},
                "disk": {"used_gb": 6, "total_gb": 10, "percent": 60},
            }
            assert all(response.status_code == 200 for response in responses)
            assert all(response.json() == expected for response in responses)
            assert (await client.get(URL, headers=HEADERS)).json() == expected
            # One traversal each of models, vectors and the entire data root.
            assert collector.call_count == 3
            assert collector.walks.call_count == 3

    asyncio.run(scenario())


def test_failed_storage_collection_is_not_cached(storage_source):
    release, collector = storage_source
    release.set()
    original = collector.side_effect
    collector.side_effect = OSError("fixture disk unavailable")

    async def scenario():
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get(URL, headers=HEADERS)).status_code == 500
            assert main._cache.get("storage") is None
            collector.side_effect = original
            result = await client.get(URL, headers=HEADERS)
            assert result.status_code == 200
            assert result.json()["total_data"]["gb"] == 4
            assert collector.call_count == 4

    asyncio.run(scenario())


def test_abandoned_collection_publishes_without_an_http_waiter(storage_source, monkeypatch):
    release, collector = storage_source

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        published = asyncio.Event()
        original_size = collector.side_effect
        original_set = main._cache.set

        def size(path):
            loop.call_soon_threadsafe(started.set)
            return original_size(path)

        def cache_set(key, value, ttl):
            original_set(key, value, ttl)
            if key == "storage":
                published.set()

        collector.side_effect = size
        monkeypatch.setattr(main._cache, "set", cache_set)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            request = asyncio.create_task(client.get(URL, headers=HEADERS))
            try:
                await asyncio.wait_for(started.wait(), 5)
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            finally:
                release.set()
            await asyncio.wait_for(published.wait(), 5)
            assert collector.call_count == 3
            assert (await client.get(URL, headers=HEADERS)).json()["total_data"]["gb"] == 4
            assert collector.call_count == 3

    asyncio.run(scenario())


def test_unauthenticated_storage_read_does_not_start_disk_work(storage_source):
    release, collector = storage_source
    release.set()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            assert (await client.get(URL)).status_code == 401
        collector.assert_not_called()

    asyncio.run(scenario())


def test_storage_cache_keeps_thirty_second_ttl(storage_source, monkeypatch):
    release, collector = storage_source
    release.set()
    clock = [100.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            for instant, count in [(100, 3), (129, 3), (131, 6)]:
                clock[0] = instant
                assert (await client.get(URL, headers=HEADERS)).status_code == 200
                assert collector.call_count == count

    asyncio.run(scenario())

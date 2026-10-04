"""A failed throughput publication must remain retryable at the catalogue API."""
import asyncio
import json
from threading import Event
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from test_models import _gpu, _patch_model_router_paths, _write_model_library


def _catalogue(monkeypatch, tmp_path):
    import helpers

    api, install, data = _patch_model_router_paths(monkeypatch, tmp_path)
    _write_model_library(install, [{
        "id": "local", "name": "Local", "gguf_file": "local.gguf", "llm_model_name": "local",
        "size_mb": 1, "vram_required_gb": 1, "context_length": 32768, "quantization": "Q4_K_M",
    }])
    model_dir = data / "models"
    model_dir.mkdir(exist_ok=True)
    (model_dir / "local.gguf").write_bytes(b"GGUF model")
    monkeypatch.setattr(api, "get_gpu_info", _gpu)
    monkeypatch.setattr(api, "get_loaded_model", AsyncMock(return_value="local.gguf"))
    monkeypatch.setattr(api, "get_llama_context_size", AsyncMock(return_value=32768))
    monkeypatch.setattr(api, "_get_agent_model_status", lambda: None)
    monkeypatch.setattr(api, "_last_recorded_throughput_sample", None)
    monkeypatch.setattr(api, "_pending_throughput_sample", None, raising=False)
    measurement = {"tokens_per_second": 33.0, "throughput_state": "measured",
                   "throughput_mode": "generation_interval", "throughput_model": "local.gguf",
                   "throughput_sampled_at": 1000}
    monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value=measurement))
    return api, helpers, data, measurement


@pytest.mark.parametrize("recovered_state", ["measured", "retained"])
@pytest.mark.parametrize("failure", [OSError("disk full"), PermissionError("destination held")], ids=["disk-full", "permission"])
def test_model_catalogue_retries_failed_sample_publication(test_client, monkeypatch, tmp_path, failure, recovered_state):
    api, helpers, data, measurement = _catalogue(monkeypatch, tmp_path)
    original_replace = helpers.os.replace
    blocked = True
    publications = []

    def replace(source, destination):
        if Path(destination) == helpers._PERF_FILE:
            if blocked:
                raise failure
            publications.append(Path(destination))
        return original_replace(source, destination)

    monkeypatch.setattr(helpers.os, "replace", replace)
    response = test_client.get("/api/models", headers=test_client.auth_headers)
    assert response.status_code == 200, "Recording failure must not hide the readable catalogue"
    assert not helpers._PERF_FILE.exists()
    assert not list(data.glob(".model_performance.json.*.tmp"))
    blocked = False
    # Current context changed; the metrics may have become retained after the
    # freshness window. Retry the captured observation and signature exactly.
    monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value={**measurement,
        "throughput_state": recovered_state, "tokens_per_second": 33.0 if recovered_state == "measured" else 99.0}))
    monkeypatch.setattr(api, "get_llama_context_size", AsyncMock(return_value=65536))
    response = test_client.get("/api/models", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert helpers._PERF_FILE.is_file(), "A fresh sample must retry after publication recovers"
    persisted = json.loads(helpers._PERF_FILE.read_text())
    assert all(sample["sample_count"] == 1 for sample in persisted["samples"].values())
    assert all(sample["tokens_per_second"] == 33.0 and sample["context_length"] == 32768
               for sample in persisted["samples"].values())
    assert len(publications) == 1
    assert test_client.get("/api/models", headers=test_client.auth_headers).status_code == 200
    assert len(publications) == 1, "Only a committed sample is deduplicated"
    monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value={**measurement, "throughput_state": "retained"}))
    retained = test_client.get("/api/models", headers=test_client.auth_headers)
    assert retained.status_code == 200
    performance = retained.json()["models"][0]["performance"]
    assert performance["source"] == "measured_local"
    assert performance["tokensPerSec"] == 33.0
    assert len(publications) == 1



@pytest.mark.asyncio
@pytest.mark.parametrize("succeeds", [True, False], ids=["commit", "failed-commit"])
async def test_disconnected_catalogue_retains_sample_ownership_until_publication(monkeypatch, tmp_path, succeeds):
    import httpx
    from main import app
    api, helpers, _data, _measurement = _catalogue(monkeypatch, tmp_path)
    entered, release, finished = Event(), Event(), Event()
    original_replace = helpers.os.replace
    attempts = []
    blocked = True

    def replace(source, destination):
        if Path(destination) == helpers._PERF_FILE:
            attempts.append(Path(destination))
            if blocked:
                entered.set()
                assert release.wait(5), "Test must release publication"
                if not succeeds:
                    finished.set()
                    raise OSError("disk full")
            result = original_replace(source, destination)
            finished.set()
            return result
        return original_replace(source, destination)

    monkeypatch.setattr(helpers.os, "replace", replace)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard",
                                 headers={"Authorization": "Bearer test-key-12345"}) as client:
        request = asyncio.create_task(client.get("/api/models"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            again = await client.get("/api/models")
            assert again.status_code == 200
            assert len(attempts) == 1, "A disconnected request must retain in-flight ownership"
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            if not succeeds:
                async def wait_for_retry():
                    while api._last_recorded_throughput_sample is not None:
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(wait_for_retry(), timeout=3)
                blocked = False
                monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value={**_measurement,
                    "throughput_state": "retained", "tokens_per_second": 99.0}))
                assert (await client.get("/api/models")).status_code == 200
            else:
                assert (await client.get("/api/models")).status_code == 200
            assert helpers._PERF_FILE.exists()
            assert len(attempts) == (1 if succeeds else 2)
            samples = json.loads(helpers._PERF_FILE.read_text())["samples"]
            assert all(sample["sample_count"] == 1 for sample in samples.values())
        finally:
            release.set()
            if not request.done():
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request

@pytest.mark.asyncio
@pytest.mark.parametrize("first_succeeds", [False, True], ids=["older-fails", "older-commits"])
async def test_late_write_preserves_newer_sample(monkeypatch, tmp_path, first_succeeds):
    import httpx
    from main import app
    api, helpers, _data, measurement = _catalogue(monkeypatch, tmp_path)
    entered, release = Event(), Event()
    original_replace = helpers.os.replace
    attempts = []

    def replace(source, destination):
        if Path(destination) == helpers._PERF_FILE:
            attempts.append(Path(destination))
            if len(attempts) == 1:
                entered.set()
                assert release.wait(5)
                if not first_succeeds:
                    raise OSError("first publication failed")
        return original_replace(source, destination)

    monkeypatch.setattr(helpers.os, "replace", replace)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard",
                                 headers={"Authorization": "Bearer test-key-12345"}) as client:
        older = asyncio.create_task(client.get("/api/models"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            newer = {**measurement, "throughput_sampled_at": 1001, "tokens_per_second": 44.0}
            monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value=newer))
            newer_request = asyncio.create_task(client.get("/api/models"))
            try:
                # The older transaction still owns its read-modify-write.
                # A newer request cannot publish an independently read copy.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(newer_request), timeout=0.1)
                assert len(attempts) == 1
            finally:
                release.set()
            assert (await older).status_code == 200
            assert (await newer_request).status_code == 200
            monkeypatch.setattr(api, "get_llama_metrics", AsyncMock(return_value={**newer, "throughput_state": "retained"}))
            latest = await client.get("/api/models")
            assert latest.status_code == 200
            assert latest.json()["models"][0]["performance"]["tokensPerSec"] == (35.2 if first_succeeds else 44.0)
            assert len(attempts) == 2
            samples = json.loads(helpers._PERF_FILE.read_text())["samples"]
            assert all(sample["sample_count"] == (2 if first_succeeds else 1) for sample in samples.values())
            assert all(sample["last_tokens_per_second"] == 44.0 for sample in samples.values())
        finally:
            release.set()
            await older

import copy
import json
import threading
from unittest.mock import AsyncMock

import pytest

import helpers
import routers.models as router


@pytest.fixture
def perf_env(tmp_path, monkeypatch):
    perf_file = tmp_path / "model_performance.json"
    monkeypatch.setattr(helpers, "_PERF_FILE", perf_file)
    monkeypatch.setattr(router, "record_model_performance", helpers.record_model_performance)
    monkeypatch.setattr(router, "_pending_sample", None)
    monkeypatch.setattr(router, "_last_committed_sample_key", None)
    yield perf_file
    monkeypatch.setattr(router, "_pending_sample", None)
    monkeypatch.setattr(router, "_last_committed_sample_key", None)


def _captured(key=("m", 1), tps=42.0, **overrides):
    data = {
        "sample_key": key,
        "model_name": "m",
        "gpu_name": "gpu",
        "backend": "cuda",
        "tokens_per_second": tps,
        "kwargs": {
            "model_id": "mid",
            "gguf": "m.gguf",
            "quantization": "Q4",
            "architecture": "llama",
            "context_length": 4096,
            "decode_read_mb": 12.5,
            "vram_total_mb": 8192,
            "os_name": "linux",
            "flags": {"flash": True},
        },
    }
    data.update(overrides)
    return data


def _samples(perf_file):
    return json.loads(perf_file.read_text())["samples"]


def _only_sample(perf_file):
    samples = _samples(perf_file)
    values = list(samples.values())
    assert values and all(sample == values[0] for sample in values)
    return values[0]


def test_successful_first_fresh_sample_persists_immediately(perf_env):
    router._publish_measured_sample(_captured())
    assert router._pending_sample is None
    assert router._last_committed_sample_key == ("m", 1)
    sample = _only_sample(perf_env)
    assert sample["model"] == "m"
    assert sample["gpu"] == "gpu"
    assert sample["sample_count"] == 1


def test_publication_failure_retains_then_retries(perf_env, monkeypatch):
    real_replace = helpers.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return real_replace(src, dst)

    monkeypatch.setattr(helpers.os, "replace", flaky)
    router._publish_measured_sample(_captured())
    assert router._pending_sample is not None
    assert router._last_committed_sample_key is None
    assert not perf_env.exists() or not _samples(perf_env)

    monkeypatch.setattr(helpers.os, "replace", real_replace)
    router._publish_measured_sample(None)
    assert router._pending_sample is None
    assert router._last_committed_sample_key == ("m", 1)
    sample = _only_sample(perf_env)
    assert sample["sample_count"] == 1


def test_no_double_count_same_key(perf_env):
    router._publish_measured_sample(_captured())
    router._publish_measured_sample(_captured())
    router._publish_measured_sample(_captured())
    sample = _only_sample(perf_env)
    assert sample["sample_count"] == 1
    assert router._last_committed_sample_key == ("m", 1)


def test_original_model_context_flags_survive_mutation(perf_env, monkeypatch):
    captured = _captured()
    real_record = helpers.record_model_performance
    monkeypatch.setattr(router, "record_model_performance", lambda *a, **k: False)
    router._publish_measured_sample(captured)
    captured["model_name"] = "mutated"
    captured["kwargs"]["context_length"] = 1
    captured["kwargs"]["flags"]["flash"] = False
    monkeypatch.setattr(router, "record_model_performance", real_record)
    router._publish_measured_sample(None)
    sample = _only_sample(perf_env)
    assert sample["model"] == "m"
    assert sample["context_length"] == 4096
    assert sample["flags"] == {"flash": True}


def test_bounded_latest_pending_replaces_older(perf_env, monkeypatch):
    def always_fail(src, dst):
        raise OSError("nope")

    monkeypatch.setattr(helpers.os, "replace", always_fail)
    router._publish_measured_sample(_captured(key=("m", 1), tps=10.0))
    router._publish_measured_sample(_captured(key=("m", 2), tps=20.0))
    assert router._pending_sample["sample_key"] == ("m", 2)
    assert router._pending_sample["tokens_per_second"] == 20.0


def test_concurrent_read_modify_write_preserves_both_samples(perf_env, monkeypatch):
    first_publication = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_read = threading.Event()
    errors = []
    real_read, real_write = helpers._read_json_file, helpers._write_json_file

    def read(path, default):
        if threading.current_thread().name == "second-writer":
            second_read.set()
        return real_read(path, default)

    def write(path, data):
        if threading.current_thread().name == "first-writer":
            first_publication.set()
            assert release_first.wait(5)
        return real_write(path, data)

    monkeypatch.setattr(helpers, "_read_json_file", read)
    monkeypatch.setattr(helpers, "_write_json_file", write)

    def record():
        try:
            if threading.current_thread().name == "second-writer":
                second_started.set()
            assert helpers.record_model_performance("m", "gpu", "cuda", 11.0) is True
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=record, name="first-writer")
    second = threading.Thread(target=record, name="second-writer")
    first.start()
    try:
        assert first_publication.wait(5)
        second.start()
        assert second_started.wait(5)
        assert not second_read.wait(.2)
    finally:
        release_first.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert not errors, errors
    assert second_read.is_set()
    assert _only_sample(perf_env)["sample_count"] == 2


def test_helper_oserror_fail_soft(perf_env, monkeypatch):
    def fail(src, dst):
        raise OSError("disk")

    monkeypatch.setattr(helpers.os, "replace", fail)
    assert helpers.record_model_performance("m", "gpu", "cuda", 5.0) is False


def test_helper_unexpected_exception_propagates(perf_env, monkeypatch):
    def boom(src, dst):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(helpers.os, "replace", boom)
    with pytest.raises(RuntimeError):
        helpers.record_model_performance("m", "gpu", "cuda", 5.0)


def test_publish_oserror_retains_pending(perf_env, monkeypatch):
    def fail(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(router, "record_model_performance", fail)
    router._publish_measured_sample(_captured())
    assert router._pending_sample is not None
    assert router._last_committed_sample_key is None


def test_publish_unexpected_exception_propagates_and_retains_pending(perf_env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("programming error")

    monkeypatch.setattr(router, "record_model_performance", boom)
    with pytest.raises(RuntimeError):
        router._publish_measured_sample(_captured())
    assert router._pending_sample is not None
    assert router._last_committed_sample_key is None


def test_implausible_input_helper_returns_false(perf_env):
    assert helpers.is_plausible_single_request_tps(0) is False
    assert helpers.is_plausible_single_request_tps(-1) is False
    assert helpers.is_plausible_single_request_tps(float("nan")) is False
    assert helpers.is_plausible_single_request_tps(float("inf")) is False
    assert helpers.record_model_performance("m", "gpu", "cuda", "not-a-number") is False
    assert helpers.is_plausible_single_request_tps(helpers.MAX_SINGLE_REQUEST_TOKENS_PER_SECOND + 1) is False
    assert helpers.is_plausible_single_request_tps(42.0) is True
    assert helpers.record_model_performance("m", "gpu", "cuda", 0) is False
    assert helpers.record_model_performance("m", "gpu", "cuda", float("nan")) is False
    assert not perf_env.exists() or not _samples(perf_env)


def test_concurrent_publication_serializes_writes(perf_env, monkeypatch):
    """Deterministic Events force a second write to wait for the first."""
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    order = []
    errors = []
    real_record = helpers.record_model_performance
    call_count = {"n": 0}
    count_lock = threading.Lock()

    def gated_record(*args, **kwargs):
        with count_lock:
            call_count["n"] += 1
            index = call_count["n"]
        if index == 1:
            order.append("first-enter")
            first_entered.set()
            assert release_first.wait(timeout=5)
            result = real_record(*args, **kwargs)
            order.append("first-exit")
            return result
        order.append("second-enter")
        second_entered.set()
        return real_record(*args, **kwargs)

    monkeypatch.setattr(router, "record_model_performance", gated_record)

    def publish(captured):
        try:
            router._publish_measured_sample(captured)
        except BaseException as exc:  # noqa: BLE001 - propagate to the test thread
            errors.append(exc)

    first = threading.Thread(target=publish, args=(_captured(key=("m", 1), tps=10.0),))
    second = threading.Thread(target=publish, args=(_captured(key=("m", 2), tps=20.0),))
    first.start()
    assert first_entered.wait(timeout=5)
    second.start()
    # The second publication cannot enter the write path until the first
    # releases the lock; the pending slot is still the first sample.
    assert not second_entered.wait(timeout=0.2)
    assert router._pending_sample is not None
    assert router._pending_sample["sample_key"] == ("m", 1)
    release_first.set()
    assert second_entered.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)
    assert not errors, errors
    assert order == ["first-enter", "first-exit", "second-enter"]
    samples = _samples(perf_env)
    assert len(samples) == 2
    assert all(s["sample_count"] == 2 for s in samples.values())


def test_duplicate_router_same_key_concurrency(perf_env):
    """Two threads publishing the same sample key must not double-count."""
    barrier = threading.Barrier(2)
    errors = []

    def publish():
        try:
            barrier.wait(timeout=5)
            router._publish_measured_sample(_captured(key=("m", 1), tps=42.0))
        except BaseException as exc:  # noqa: BLE001 - propagate to the test thread
            errors.append(exc)

    threads = [threading.Thread(target=publish) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors, errors
    sample = _only_sample(perf_env)
    assert sample["sample_count"] == 1
    assert router._last_committed_sample_key == ("m", 1)


def test_endpoint_retains_original_context_after_oserror(
    test_client, monkeypatch, tmp_path,
):
    """A transient os.replace failure keeps the endpoint 200 and the sample pending."""
    import test_models

    models_router, install_dir, data_dir = test_models._patch_model_router_paths(
        monkeypatch, tmp_path
    )
    test_models._write_model_library(install_dir, [{
        "id": "qwen3.5-9b-q4",
        "name": "Qwen 3.5 9B",
        "gguf_file": "Qwen3.5-9B-Q4_K_M.gguf",
        "size_mb": 5760,
        "vram_required_gb": 8,
        "context_length": 32768,
        "quantization": "Q4_K_M",
        "specialty": "General",
        "description": "Balanced default.",
        "llm_model_name": "qwen3.5-9b",
    }])
    (data_dir / "models").mkdir(exist_ok=True)
    (data_dir / "models" / "Qwen3.5-9B-Q4_K_M.gguf").write_text("model", encoding="utf-8")
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n"
        "CTX_SIZE=32768\n"
        "MAX_CONTEXT=32768\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(models_router, "get_gpu_info", test_models._gpu)
    monkeypatch.setattr(
        models_router, "get_loaded_model",
        AsyncMock(
            return_value="Qwen3.5-9B-Q4_K_M.gguf"
        ),
    )
    monkeypatch.setattr(
        models_router, "get_llama_metrics",
        AsyncMock(return_value={
            "tokens_per_second": 33.0,
            "lifetime_tokens": 0,
            "throughput_mode": "generation_interval",
            "throughput_state": "measured",
            "throughput_sampled_at": 1000,
            "throughput_model": "Qwen3.5-9B-Q4_K_M.gguf",
        }),
    )
    monkeypatch.setattr(
        models_router, "get_llama_context_size",
        AsyncMock(return_value=32768),
    )
    monkeypatch.setattr(models_router, "SERVICES", {"llama-server": {"host": "localhost", "port": 8080}})
    monkeypatch.setattr(models_router, "_pending_sample", None)
    monkeypatch.setattr(models_router, "_last_committed_sample_key", None)

    real_replace = helpers.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real_replace(src, dst)

    monkeypatch.setattr(helpers.os, "replace", flaky)

    first = test_client.get("/api/models", headers=test_client.auth_headers)
    assert first.status_code == 200
    assert models_router._pending_sample is not None
    pending = copy.deepcopy(models_router._pending_sample)
    assert pending["model_name"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert pending["kwargs"]["context_length"] == 32768
    assert models_router._last_committed_sample_key is None

    # The current context changes before the retained poll runs.
    (install_dir / ".env").write_text(
        "ODS_MODE=local\n"
        "LLM_MODEL=qwen3.5-9b\n"
        "GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n"
        "CTX_SIZE=65536\n"
        "MAX_CONTEXT=65536\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        models_router, "get_llama_context_size",
        AsyncMock(return_value=65536),
    )

    monkeypatch.setattr(models_router, "get_llama_metrics", AsyncMock(return_value={
        "tokens_per_second": 33.0, "throughput_state": "retained",
        "throughput_model": "Qwen3.5-9B-Q4_K_M.gguf", "throughput_sampled_at": 1000,
    }))
    second = test_client.get("/api/models", headers=test_client.auth_headers)
    assert second.status_code == 200
    assert models_router._pending_sample is None
    assert models_router._last_committed_sample_key == (
        "Qwen3.5-9B-Q4_K_M.gguf", 1000,
    )
    sample = _only_sample(helpers._PERF_FILE)
    assert sample["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert sample["context_length"] == 32768
    assert sample["sample_count"] == 1

    third = test_client.get("/api/models", headers=test_client.auth_headers)
    assert third.status_code == 200
    sample = _only_sample(helpers._PERF_FILE)
    assert sample["sample_count"] == 1
    assert sample["context_length"] == 32768

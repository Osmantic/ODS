import json

import pytest

import helpers
from helpers import (
    _performance_key,
    get_recorded_model_performance,
    record_model_performance,
)
from performance_oracle import collect_runtime_flags


@pytest.fixture(autouse=True)
def _isolate_perf_file(tmp_path, monkeypatch):
    monkeypatch.setattr(helpers, "_PERF_FILE", tmp_path / "model-performance.json")
    yield


def _record(flags=None, tps=100.0, model="qwen3.5-2b", gpu="AMD Radeon RX 9070 XT", backend="lemonade"):
    assert record_model_performance(model, gpu, backend, tps, flags=flags)


def _samples():
    return json.loads(helpers._PERF_FILE.read_text())["samples"]


def _seed_legacy(tps=527_885.7, sample_count=336, extra=None):
    legacy_key = _performance_key("lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b")
    sample = {
        "model": "qwen3.5-2b",
        "gpu": "AMD Radeon RX 9070 XT",
        "backend": "lemonade",
        "tokens_per_second": tps,
        "sample_count": sample_count,
    }
    if extra:
        sample.update(extra)
    helpers._PERF_FILE.write_text(json.dumps({
        "schema_version": "ods.model-performance.v1",
        "samples": {legacy_key: sample},
    }))
    return legacy_key


def test_performance_key_default_has_no_flags_suffix():
    legacy = _performance_key("lemonade", "gpu", "model")
    assert "flags-" not in legacy
    assert _performance_key("lemonade", "gpu", "model", flags=None) == legacy


def test_performance_key_flags_dict_is_order_insensitive_and_bounded():
    a = _performance_key("b", "g", "m", flags={"ubatch_size": "512", "fit_target_mib": "1024"})
    b = _performance_key("b", "g", "m", flags={"fit_target_mib": "1024", "ubatch_size": "512"})
    assert a == b
    assert a != _performance_key("b", "g", "m", flags={"ubatch_size": "512"})
    assert a != _performance_key("b", "g", "m", flags={})
    assert len(a.rsplit("flags-", 1)[1]) == 64


def test_performance_key_explicit_empty_flags_differs_from_none():
    assert _performance_key("b", "g", "m", flags={}) != _performance_key("b", "g", "m", flags=None)


def test_recorder_writes_only_configured_key_no_short_alias():
    _record(flags={"ubatch_size": "512"})
    samples = _samples()
    assert len(samples) == 1
    configured = _performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={"ubatch_size": "512"},
    )
    assert configured in samples
    assert _performance_key("lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b") not in samples


def test_recorder_does_not_mutate_legacy_record():
    legacy_key = _seed_legacy()
    _record(flags={"ubatch_size": "512"}, tps=240.5)
    samples = _samples()
    assert samples[legacy_key]["tokens_per_second"] == 527_885.7
    assert samples[legacy_key]["sample_count"] == 336
    configured = _performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={"ubatch_size": "512"},
    )
    assert samples[configured]["tokens_per_second"] == 240.5
    assert samples[configured]["sample_count"] == 1


def test_same_config_repeats_average():
    _record(flags={"ubatch_size": "512"}, tps=100.0)
    _record(flags={"ubatch_size": "512"}, tps=200.0)
    configured = _performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={"ubatch_size": "512"},
    )
    sample = _samples()[configured]
    assert sample["sample_count"] == 2
    assert sample["tokens_per_second"] == pytest.approx(120.0, abs=0.1)


def test_same_config_reorder_averages():
    _record(flags={"ubatch_size": "512", "fit_target_mib": "1024"}, tps=100.0)
    _record(flags={"fit_target_mib": "1024", "ubatch_size": "512"}, tps=200.0)
    samples = _samples()
    assert len(samples) == 1
    sample = next(iter(samples.values()))
    assert sample["sample_count"] == 2
    assert sample["tokens_per_second"] == pytest.approx(120.0, abs=0.1)


def test_different_configs_do_not_blend():
    _record(flags={"ubatch_size": "512"}, tps=100.0)
    _record(flags={"ubatch_size": "1024"}, tps=300.0)
    samples = _samples()
    assert len(samples) == 2
    for sample in samples.values():
        assert sample["sample_count"] == 1


def test_two_configs_keep_independent_counts_and_tps():
    _record(flags={"ubatch_size": "512"}, tps=100.0)
    _record(flags={"ubatch_size": "512"}, tps=200.0)
    _record(flags={"ubatch_size": "1024"}, tps=300.0)
    samples = _samples()
    assert len(samples) == 2
    a = samples[_performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={"ubatch_size": "512"},
    )]
    b = samples[_performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={"ubatch_size": "1024"},
    )]
    assert a["sample_count"] == 2
    assert a["tokens_per_second"] == pytest.approx(120.0, abs=0.1)
    assert b["sample_count"] == 1
    assert b["tokens_per_second"] == pytest.approx(300.0, abs=0.1)


def test_delimiter_collision_strings_differ():
    a = _performance_key("b", "g", "m", flags={"ubatch_size": "512:fit_target_mib=1024"})
    b = _performance_key("b", "g", "m", flags={"ubatch_size": "512", "fit_target_mib": "1024"})
    assert a != b


def test_getter_configured_key_wins():
    _record(flags={"ubatch_size": "512"}, tps=100.0)
    _record(flags={"ubatch_size": "1024"}, tps=300.0)
    got = get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade", flags={"ubatch_size": "512"},
    )
    assert got["tokens_per_second"] == 100.0


def test_getter_nonempty_lookup_never_returns_legacy():
    _seed_legacy()
    got = get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade", flags={"ubatch_size": "512"},
    )
    assert got is None


def test_getter_nonempty_lookup_never_returns_matching_legacy():
    _seed_legacy(tps=42.0, extra={"flags": {"ubatch_size": "512"}})
    got = get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade", flags={"ubatch_size": "512"},
    )
    assert got is None


def test_getter_empty_lookup_falls_back_to_legacy():
    _seed_legacy()
    got = get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade", flags={},
    )
    assert got is not None
    assert got["tokens_per_second"] == 527_885.7
    assert got["sample_count"] == 336


def test_getter_absent_lookup_falls_back_to_legacy():
    _seed_legacy()
    got = get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade",
    )
    assert got is not None
    assert got["tokens_per_second"] == 527_885.7


def test_explicit_empty_record_never_inherits_previous_tuned_flags():
    _record(flags={"ubatch_size": "512"}, tps=100.0)
    _record(flags={}, tps=200.0)
    samples = _samples()
    empty_key = _performance_key(
        "lemonade", "AMD Radeon RX 9070 XT", "qwen3.5-2b", flags={},
    )
    assert empty_key in samples
    assert samples[empty_key]["flags"] == {}
    assert samples[empty_key]["tokens_per_second"] == pytest.approx(200.0, abs=0.1)
    assert samples[empty_key]["sample_count"] == 1


def test_collect_runtime_flags_includes_ubatch_and_fit_target(tmp_path, monkeypatch):
    for key in ("LLAMA_ARG_UBATCH", "LLAMA_UBATCH", "LLAMA_ARG_FIT_TARGET", "LLAMA_FIT_TARGET"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "LLAMA_ARG_UBATCH=512\nLLAMA_ARG_FIT_TARGET=1024\n",
        encoding="utf-8",
    )
    flags = collect_runtime_flags(tmp_path)
    assert flags["ubatch_size"] == "512"
    assert flags["fit_target_mib"] == "1024"


def test_collect_runtime_flags_ignores_unsupported_aliases(tmp_path, monkeypatch):
    for key in ("LLAMA_ARG_UBATCH", "LLAMA_UBATCH", "LLAMA_ARG_FIT_TARGET", "LLAMA_FIT_TARGET"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "LLAMA_UBATCH=512\nLLAMA_FIT_TARGET=1024\n",
        encoding="utf-8",
    )
    flags = collect_runtime_flags(tmp_path)
    assert "ubatch_size" not in flags
    assert "fit_target_mib" not in flags


def test_published_exact_requires_ubatch_and_fit_target_both_directions():
    from performance_oracle import _published_exact
    from models import GPUInfo

    gpu = GPUInfo(
        name="NVIDIA GeForce RTX 4060", memory_used_mb=0, memory_total_mb=8192,
        memory_percent=0, utilization_percent=0, temperature_c=40, gpu_backend="nvidia",
    )
    model = {"id": "m", "name": "m", "gguf": "m.gguf", "quantization": "Q4_K_M"}
    base_entry = {
        "model_id": "m", "model_names": ["m"], "backend": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060", "vram_gb": 8,
        "context_length": 32768, "quantization": "Q4_K_M",
        "tokens_per_second": 50.0, "runtime": "cuda",
    }

    entry = dict(base_entry, flags={"ubatch_size": "512"})
    assert _published_exact(model, gpu, 32768, "Q4_K_M", {}, "cuda", [entry]) is None

    entry = dict(base_entry, flags={})
    assert _published_exact(
        model, gpu, 32768, "Q4_K_M", {"ubatch_size": "512"}, "cuda", [entry],
    ) is None

    entry = dict(base_entry, flags={"ubatch_size": "512", "fit_target_mib": "1024"})
    assert _published_exact(
        model, gpu, 32768, "Q4_K_M",
        {"ubatch_size": "512", "fit_target_mib": "1024"}, "cuda", [entry],
    ) is entry

    entry = dict(base_entry, flags={"flash_attn": "1"})
    assert _published_exact(
        model, gpu, 32768, "Q4_K_M", {"ubatch_size": "512"}, "cuda", [entry],
    ) is None


def test_published_exact_unrelated_legacy_flag_matching_current_still_requires_new_knobs():
    from performance_oracle import _published_exact
    from models import GPUInfo

    gpu = GPUInfo(
        name="NVIDIA GeForce RTX 4060", memory_used_mb=0, memory_total_mb=8192,
        memory_percent=0, utilization_percent=0, temperature_c=40, gpu_backend="nvidia",
    )
    model = {"id": "m", "name": "m", "gguf": "m.gguf", "quantization": "Q4_K_M"}
    base_entry = {
        "model_id": "m", "model_names": ["m"], "backend": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060", "vram_gb": 8,
        "context_length": 32768, "quantization": "Q4_K_M",
        "tokens_per_second": 50.0, "runtime": "cuda",
    }

    entry = dict(base_entry, flags={"flash_attn": "1"})
    assert _published_exact(
        model, gpu, 32768, "Q4_K_M", {"flash_attn": "1", "ubatch_size": "512"}, "cuda", [entry],
    ) is None

    entry = dict(base_entry, flags={"flash_attn": "1", "ubatch_size": "512"})
    assert _published_exact(
        model, gpu, 32768, "Q4_K_M", {"flash_attn": "1"}, "cuda", [entry],
    ) is None


def test_evaluate_performance_wrong_config_not_measured_local():
    from performance_oracle import evaluate_performance
    from models import GPUInfo

    gpu = GPUInfo(
        name="AMD Radeon RX 9070 XT", memory_used_mb=0, memory_total_mb=16384,
        memory_percent=0, utilization_percent=0, temperature_c=40, gpu_backend="lemonade",
    )
    model = {"id": "qwen3.5-2b", "name": "qwen3.5-2b", "gguf": "qwen3.5-2b.gguf", "quantization": "Q4_K_M"}
    assert record_model_performance(
        "qwen3.5-2b", gpu.name, gpu.gpu_backend, 100.0,
        flags={"ubatch_size": "512"}, gguf=model["gguf"],
        vram_total_mb=gpu.memory_total_mb,
    )
    result = evaluate_performance(
        model, gpu, {"quantization": "Q4_K_M"}, False, 0.0, None,
        {"ubatch_size": "1024"}, [], True, runtime="lemonade",
    )
    assert result["source"] != "measured_local"


def test_evaluate_performance_matching_config_measured_local():
    from performance_oracle import evaluate_performance
    from models import GPUInfo

    gpu = GPUInfo(
        name="AMD Radeon RX 9070 XT", memory_used_mb=0, memory_total_mb=16384,
        memory_percent=0, utilization_percent=0, temperature_c=40, gpu_backend="lemonade",
    )
    model = {"id": "qwen3.5-2b", "name": "qwen3.5-2b", "gguf": "qwen3.5-2b.gguf", "quantization": "Q4_K_M"}
    assert record_model_performance(
        "qwen3.5-2b", gpu.name, gpu.gpu_backend, 100.0,
        flags={"ubatch_size": "512"}, gguf=model["gguf"],
        vram_total_mb=gpu.memory_total_mb,
    )
    result = evaluate_performance(
        model, gpu, {"quantization": "Q4_K_M"}, False, 0.0, None,
        {"ubatch_size": "512"}, [], True, runtime="lemonade",
    )
    assert result["source"] == "measured_local"


def test_evaluate_performance_live_valid_loaded_sample_retains_priority():
    from performance_oracle import evaluate_performance
    from models import GPUInfo

    gpu = GPUInfo(
        name="AMD Radeon RX 9070 XT", memory_used_mb=0, memory_total_mb=16384,
        memory_percent=0, utilization_percent=0, temperature_c=40, gpu_backend="lemonade",
    )
    model = {"id": "qwen3.5-2b", "name": "qwen3.5-2b", "gguf": "qwen3.5-2b.gguf", "quantization": "Q4_K_M"}
    assert record_model_performance(
        "qwen3.5-2b", gpu.name, gpu.gpu_backend, 100.0,
        flags={"ubatch_size": "512"}, gguf=model["gguf"],
        vram_total_mb=gpu.memory_total_mb,
    )
    result = evaluate_performance(
        model, gpu, {"quantization": "Q4_K_M"}, True, 250.0, None,
        {"ubatch_size": "512"}, [], True, runtime="lemonade",
    )
    assert result["source"] == "measured_local"
    assert result["tokensPerSec"] == pytest.approx(250.0, abs=0.1)


@pytest.mark.parametrize("flags", [None, {}])
def test_empty_lookup_cannot_use_legacy_tuned_record(flags):
    _seed_legacy(tps=42.0, extra={"flags": {"ubatch_size": "512"}})
    assert get_recorded_model_performance(
        "qwen3.5-2b", "AMD Radeon RX 9070 XT", "lemonade", flags=flags,
    ) is None

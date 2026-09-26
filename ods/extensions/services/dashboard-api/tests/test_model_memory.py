"""Tests for model_memory.py — the shared selector/activation memory estimate.

scripts/select-model.py (installer) and model_memory.py (dashboard-api) answer
the same question — how much memory does this catalog entry need — for the same
config/model-library.json. When they disagree, the installer picks a model the
dashboard then refuses to activate. These tests pin the two together.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from model_memory import (
    estimated_context_kv_gb,
    estimated_param_billions,
    required_model_memory_gb,
)


ODS_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = ODS_ROOT / "config" / "model-library.json"
SELECT_MODEL_PATH = ODS_ROOT / "scripts" / "select-model.py"


def _load_select_model():
    spec = importlib.util.spec_from_file_location("ods_select_model", SELECT_MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_entries():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["models"]


class TestParamScaleSources:

    def test_reads_the_catalog_filename_key(self):
        """model-library.json spells the filename `gguf_file`, not `gguf`."""
        model = {
            "id": "llama4-scout-q4",
            "name": "Llama 4 Scout",
            "llm_model_name": "llama-4-scout",
            "gguf_file": "Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00001-of-00002.gguf",
            "size_mb": 65300,
        }
        assert estimated_param_billions(model) == 17.0

    def test_reads_the_normalized_filename_key(self):
        """The oracle's normalized shape spells it `gguf`; both must work."""
        model = {
            "id": "llama4-scout-q4",
            "gguf": "Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00001-of-00002.gguf",
            "size_mb": 65300,
        }
        assert estimated_param_billions(model) == 17.0

    def test_explicit_metadata_still_wins(self):
        model = {"total_params_b": 8, "gguf_file": "Something-70B.gguf"}
        assert estimated_param_billions(model) == 8.0

    def test_size_heuristic_is_the_last_resort(self):
        model = {"id": "mystery", "size_mb": 6000}
        assert estimated_param_billions(model) == 10.0

    def test_filename_scale_lowers_the_kv_estimate(self):
        """A 17B model must not be charged the KV cost of a 108B one."""
        model = {
            "id": "llama4-scout-q4",
            "gguf_file": "Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00001-of-00002.gguf",
            "size_mb": 65300,
            "context_length": 131072,
        }
        without_filename = dict(model)
        without_filename.pop("gguf_file")
        assert estimated_context_kv_gb(model) < estimated_context_kv_gb(without_filename)


class TestArchitectureAwareKvCache:

    @pytest.mark.parametrize("backend,kind,ram,vram", [
        ("cpu", "discrete", 8, 0), ("cpu", "discrete", 32, 0),
        ("amd", "unified", 16, 8192), ("amd", "unified", 64, 32768),
        ("nvidia", "discrete", 8, 24576),
    ])
    def test_detected_resource_budget_parity(self, backend, kind, ram, vram):
        from models import GPUInfo
        from performance_oracle import _usable_model_memory_gb

        gpu = GPUInfo(
            name="test", memory_used_mb=0, memory_total_mb=vram,
            memory_percent=0, utilization_percent=0, temperature_c=30,
            gpu_backend=backend, memory_type=kind,
        )
        capacity, _ = _load_select_model().usable_memory_gb(backend, kind, vram, ram)
        assert _usable_model_memory_gb(gpu, ram) == capacity

    @pytest.mark.parametrize("backend,memory,expected", [
        ("apple", 8, 16384), ("apple", 16, 32768),
        ("apple", 24, 65536), ("apple", 32, 65536),
        ("apple", 64, 128000), ("apple", 128, 128000),
        *[(backend, memory, context) for backend in ("amd", "nvidia", "sycl")
          for memory, context in ((4, 8192), (8, 32768), (16, 65536), (24, 128000))],
    ])
    def test_phi4_context_scales_with_memory_in_both_rankers(self, monkeypatch, backend, memory, expected):
        import performance_oracle as oracle
        from models import GPUInfo

        selector = _load_select_model()
        # phi4-mini is no longer an install recommendation; a copy that is
        # still exercises the architecture-driven context step-down.
        raw = {
            **next(item for item in _catalog_entries() if item["id"] == "phi4-mini-q4"),
            "install_recommendation": True,
        }
        arch = "arm64" if backend == "apple" else "amd64"
        monkeypatch.setattr(oracle.platform, "machine", lambda: arch)
        kind = "unified" if backend == "apple" else "discrete"
        capacity, _ = selector.usable_memory_gb(backend, kind, memory * 1024, memory)
        cli = selector.rank_models(
            [selector.normalize_model(raw)], capacity, "qwen", True,
            backend, kind, memory * 1024, memory, arch,
        )
        gpu = GPUInfo(
            name="test", memory_used_mb=0, memory_total_mb=memory * 1024,
            memory_percent=0, utilization_percent=0, temperature_c=30,
            gpu_backend=backend,
        )
        dashboard = oracle.rank_pre_download_models(
            [oracle.normalize_catalog_entry(raw)], gpu, "qwen", True,
            system_ram_gb=memory,
        )
        assert len(cli) == len(dashboard) == 1
        for candidate in (cli[0], dashboard[0]):
            profile = candidate.get("_runtime_profile")
            assert selector.effective_context_length(candidate, profile) == expected
            assert selector.effective_required_memory_gb(candidate, profile) <= capacity + 0.25
        assert raw["context_length"] == 128000

    @pytest.mark.parametrize("backend", ["apple", "nvidia", "amd", "cpu", "sycl"])
    @pytest.mark.parametrize("capacity", [4, 8, 16, 24, 32, 64])
    def test_rankers_never_fallback_to_an_oversized_model(self, backend, capacity):
        from models import GPUInfo
        from performance_oracle import normalize_catalog_entry, rank_pre_download_models

        selector = _load_select_model()
        raw = {
            "id": "qwen-oversized", "family": "qwen", "gguf_file": "large.gguf",
            "gguf_url": "https://example.invalid/large.gguf",
            "size_mb": 1024 * 256, "vram_required_gb": 256,
            "context_length": 32768,
        }
        memory_type = "unified" if backend == "apple" else "discrete"
        assert selector.rank_models(
            [selector.normalize_model(raw)], capacity, "qwen", True,
            backend, memory_type, capacity * 1024, capacity,
            "arm64" if backend == "apple" else "amd64",
        ) == []
        gpu = GPUInfo(
            name="test", memory_used_mb=0, memory_total_mb=capacity * 1024,
            memory_percent=0, utilization_percent=0, temperature_c=30,
            gpu_backend=backend,
        )
        assert rank_pre_download_models(
            [normalize_catalog_entry(raw)], gpu, "qwen", True,
            system_ram_gb=capacity,
        ) == []

    @pytest.mark.parametrize("context", [8192, 16384, 32768, 65536, 128000])
    def test_catalog_normalizers_preserve_architecture(self, context):
        from performance_oracle import normalize_catalog_entry

        selector = _load_select_model()
        raw = {
            "id": "phi4-mini-q4", "gguf_file": "Phi-4-mini.gguf",
            "size_mb": 2490, "vram_required_gb": 4,
            "context_length": context, "block_count": 32,
            "attention_head_count_kv": 8, "embedding_length": 3072,
            "attention_head_count": 24,
        }
        expected_kv = round(32 * 8 * 256 * 2 * context / 1024**3, 2)
        expected = round(max(4, 2490 / 1024 + expected_kv), 2)
        installer = selector.normalize_model(raw)
        dashboard = normalize_catalog_entry(raw)
        assert selector.estimated_context_kv_gb(installer) == expected_kv
        assert selector.selector_required_memory_gb(installer) == expected
        assert required_model_memory_gb(dashboard) == expected

    def test_dense_qwen_metadata_matches_llama_allocation(self):
        model = {
            "block_count": 36,
            "attention_head_count_kv": 8,
            "embedding_length": 4096,
            "attention_head_count": 32,
        }
        assert estimated_context_kv_gb(model, 32768) == 4.5
        assert estimated_context_kv_gb(model, 262144) == 36.0

    def test_explicit_key_and_value_lengths_support_grouped_attention(self):
        model = {
            "block_count": 10,
            "attention_head_count_kv": 4,
            "attention_key_length": 64,
            "attention_value_length": 64,
        }
        assert estimated_context_kv_gb(model, 32768) == 0.31

    def test_partial_rope_dimension_does_not_undercount_phi3_heads(self):
        model = {
            "block_count": 32,
            "attention_head_count_kv": 8,
            "embedding_length": 3072,
            "attention_head_count": 24,
            "rope_dimension_count": 96,
        }
        assert estimated_context_kv_gb(model, 32768) == 4.0

    def test_per_layer_kv_heads_cover_hybrid_attention(self):
        model = {
            "block_count": 4,
            "attention_head_count_kv": [0, 2, 0, 2],
            "attention_key_length": 256,
            "attention_value_length": 256,
        }
        assert estimated_context_kv_gb(model, 32768) == 0.12

    def test_incomplete_per_layer_metadata_falls_back(self):
        model = {
            "params_b": 4,
            "block_count": 80,
            "attention_head_count_kv": [8] * 64,
            "attention_key_length": 128,
            "attention_value_length": 128,
        }
        assert estimated_context_kv_gb(model, 32768) == 0.48

    def test_incomplete_metadata_keeps_catalog_fallback(self):
        model = {"params_b": 4, "block_count": 36}
        assert estimated_context_kv_gb(model, 32768) == 0.48


@pytest.mark.skipif(
    not CATALOG_PATH.exists() or not SELECT_MODEL_PATH.exists(),
    reason="repo checkout required",
)
class TestSelectorParity:

    def test_every_catalog_entry_agrees_with_the_installer_selector(self):
        select_model = _load_select_model()
        mismatches = []
        for raw in _catalog_entries():
            dashboard_gb = required_model_memory_gb(raw)
            installer_gb = select_model.selector_required_memory_gb(raw)
            if dashboard_gb != installer_gb:
                mismatches.append((raw.get("id"), dashboard_gb, installer_gb))
        assert not mismatches, (
            "dashboard-api and the installer selector disagree on required "
            f"memory for: {mismatches}"
        )

    def test_param_scale_agrees_with_the_installer_selector(self):
        select_model = _load_select_model()
        mismatches = [
            (raw.get("id"), estimated_param_billions(raw), select_model.estimated_param_billions(raw))
            for raw in _catalog_entries()
            if estimated_param_billions(raw) != select_model.estimated_param_billions(raw)
        ]
        assert not mismatches, f"param-scale estimates diverge for: {mismatches}"


# ---------------------------------------------------------------------------
# Architecture estimator (model_memory.estimate_model_memory)
#
# Layouts come from each model's Hugging Face config.json at the revision the
# catalog records in `architecture_source`; weights are the pinned GGUF sizes.
# The reference numbers are the model-defaults review's (2026-09-25) and the
# RTX 5090 / llama.cpp b9014 measurements of Qwen3.5-27B Q4_K_M.
# ---------------------------------------------------------------------------

from model_memory import (  # noqa: E402
    DISCRETE_FIT_MARGIN_MIN_GIB,
    LLAMA_DEFAULT_CTX_CHECKPOINTS,
    architecture_metadata_complete,
    checkpoint_state_bytes,
    context_fitting_model,
    estimate_model_memory,
    fit_margin_gib,
    kv_bytes_per_token,
    kv_layer_count,
    memory_fits,
    sliding_window_cells,
    sliding_window_kv_bytes_per_cell,
)


def _qwen35_linear_state(value_heads, linear_layers):
    """Gated DeltaNet state per sequence: f32 SSM + conv states per layer."""
    return linear_layers * (
        value_heads * 128 * 128 * 4 + 3 * (2 * 16 * 128 + value_heads * 128) * 4
    )


ARCH = {
    "qwen3.5-2b": dict(block_count=24, attention_layer_count=6, attention_head_count_kv=2,
                       attention_key_length=256, attention_value_length=256,
                       recurrent_state_bytes=_qwen35_linear_state(16, 18),
                       size_bytes=1280835840),
    "qwen3.5-4b": dict(block_count=32, attention_layer_count=8, attention_head_count_kv=4,
                       attention_key_length=256, attention_value_length=256,
                       recurrent_state_bytes=_qwen35_linear_state(32, 24),
                       size_bytes=2740937888),
    "qwen3.5-9b": dict(block_count=32, attention_layer_count=8, attention_head_count_kv=4,
                       attention_key_length=256, attention_value_length=256,
                       recurrent_state_bytes=_qwen35_linear_state(32, 24),
                       size_bytes=5680522464),
    "qwen3.5-27b": dict(block_count=64, attention_layer_count=16, attention_head_count_kv=4,
                        attention_key_length=256, attention_value_length=256,
                        recurrent_state_bytes=_qwen35_linear_state(48, 48),
                        size_bytes=16740812704),
    "qwen3.6-35b-a3b": dict(block_count=40, attention_layer_count=10, attention_head_count_kv=2,
                            attention_key_length=256, attention_value_length=256,
                            recurrent_state_bytes=_qwen35_linear_state(32, 30),
                            size_bytes=22134528992),
    "qwen3-coder-next": dict(block_count=48, full_attention_interval=4, attention_head_count_kv=2,
                             attention_key_length=256, attention_value_length=256,
                             recurrent_state_bytes=_qwen35_linear_state(32, 36),
                             size_bytes=48528320544),
    "nemotron3-nano-4b": dict(block_count=42, attention_layer_count=4, attention_head_count_kv=8,
                              attention_key_length=128, attention_value_length=128,
                              recurrent_state_bytes=21 * (96 * 80 * 128 * 4 + 3 * (7680 + 2048) * 4),
                              size_bytes=2837072864),
    "ministral3-8b": dict(block_count=34, attention_layer_count=34, attention_head_count_kv=8,
                          attention_key_length=128, attention_value_length=128,
                          recurrent_state_bytes=0, size_bytes=5198911904),
    # Dense references (not catalog defaults): microsoft/phi-4 and
    # DeepSeek-R1-Distill-Llama-70B config.json, head_dim = hidden/heads.
    "phi-4": dict(block_count=40, attention_head_count_kv=10, embedding_length=5120,
                  attention_head_count=40, recurrent_state_bytes=0, size_mb=9050),
    "r1-70b": dict(block_count=80, attention_head_count_kv=8, attention_key_length=128,
                   attention_value_length=128, recurrent_state_bytes=0, size_mb=42500),
    "qwen3-30b-a3b": dict(block_count=48, attention_head_count_kv=4, attention_key_length=128,
                          attention_value_length=128, recurrent_state_bytes=0, size_mb=18600),
    # google/gemma-4-26B-A4B-it config.json: 5 full-attention layers with 2
    # global KV heads x 512, 25 sliding-window layers with 8 KV heads x 256
    # over a 1,024-token window.
    "gemma4-26b-a4b": dict(block_count=30, attention_layer_count=5, attention_head_count_kv=2,
                           attention_key_length=512, attention_value_length=512,
                           sliding_window=1024, sliding_window_layer_count=25,
                           sliding_window_head_count_kv=8, sliding_window_key_length=256,
                           sliding_window_value_length=256, recurrent_state_bytes=0,
                           size_bytes=16796010720),
}


class TestArchitectureEstimator:

    @pytest.mark.parametrize("name,expected", [
        ("qwen3.5-2b", 12288), ("qwen3.5-4b", 32768), ("qwen3.5-9b", 32768),
        ("qwen3.5-27b", 65536), ("qwen3.6-35b-a3b", 20480),
        ("qwen3-coder-next", 24576), ("nemotron3-nano-4b", 16384),
        ("ministral3-8b", 139264), ("phi-4", 204800), ("r1-70b", 327680),
        ("qwen3-30b-a3b", 98304),
    ])
    def test_f16_kv_bytes_per_token(self, name, expected):
        assert kv_bytes_per_token(ARCH[name]) == expected

    def test_review_reference_kv_sizes(self):
        # Review: phi-4 KV at 16K is 3.1 GiB and R1-70B KV at 32K is 10 GiB;
        # the old heuristic charged 0.84 and 3.5.
        assert estimate_model_memory(ARCH["phi-4"], context_length=16384).kv_gib == 3.125
        assert estimate_model_memory(ARCH["r1-70b"], context_length=32768).kv_gib == 10.0
        assert estimate_model_memory(ARCH["qwen3.5-4b"], context_length=262144).kv_gib == 8.0

    def test_hybrid_totals_match_the_review(self):
        # Review: 9B at 64K / 128K / 256K is 7.9 / 9.9 / 13.9 GiB [EST].
        for context, review in ((65536, 7.9), (131072, 9.9), (262144, 13.9)):
            total = estimate_model_memory(ARCH["qwen3.5-9b"], context_length=context).device_gib
            assert abs(total - review) <= 0.2, (context, total)
        assert estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536).device_gib == 7.77
        # Review: Qwen3.6-35B-A3B at 128K is about 24 GiB, not the 34.6 GB
        # the block_count-less heuristic charged.
        total = estimate_model_memory(ARCH["qwen3.6-35b-a3b"], context_length=131072).device_gib
        assert total == 23.83
        assert abs(total - 24.0) <= 0.35

    @pytest.mark.parametrize("context,cache,measured_mib", [
        (65536, "f16", 20620),
        (131072, "f16", 24716),
        (65536, "q8_0", 18742),
    ])
    def test_calibrated_against_qwen35_27b_on_rtx_5090(self, context, cache, measured_mib):
        estimate = estimate_model_memory(
            ARCH["qwen3.5-27b"], context_length=context,
            cache_type_k=cache, cache_type_v=cache,
        )
        assert abs(estimate.device_gib - measured_mib / 1024) <= 0.35
        # Never under the measurement: the estimate gates what is installed.
        assert estimate.device_gib >= measured_mib / 1024

    def test_same_layout_27b_candidate_matches_the_review(self):
        # Review: Qwen3.6-27B UD-Q4_K_XL (same layout as 3.5-27B) is about
        # 20.95 GiB at 64K, from the 3.5-27B measurement plus the file delta.
        candidate = {**ARCH["qwen3.5-27b"], "size_bytes": 17612564704}
        total = estimate_model_memory(candidate, context_length=65536).device_gib
        assert abs(total - 20.95) <= 0.35

    def test_quantized_cache_factors(self):
        f16 = kv_bytes_per_token(ARCH["qwen3.5-9b"])
        assert kv_bytes_per_token(ARCH["qwen3.5-9b"], "q8_0", "q8_0") == f16 * 34 / 64
        assert kv_bytes_per_token(ARCH["qwen3.5-9b"], "q4_0", "q4_0") == f16 * 18 / 64
        mixed = kv_bytes_per_token(ARCH["qwen3.5-9b"], "q8_0", "f16")
        assert mixed == f16 * (34 / 64 + 1) / 2

    def test_hybrid_layers_hold_no_kv(self):
        dense_view = {**ARCH["qwen3.5-9b"]}
        dense_view.pop("attention_layer_count")
        assert kv_layer_count(ARCH["qwen3.5-9b"]) == 8
        assert kv_layer_count(dense_view) == 32
        assert kv_bytes_per_token(dense_view) == 4 * kv_bytes_per_token(ARCH["qwen3.5-9b"])
        assert kv_layer_count(ARCH["qwen3-coder-next"]) == 12

    def test_per_layer_array_matches_attention_layer_count(self):
        per_layer = {**ARCH["qwen3.5-9b"], "attention_head_count_kv": [0, 0, 0, 4] * 8}
        per_layer.pop("attention_layer_count")
        assert kv_layer_count(per_layer) == 8
        assert kv_bytes_per_token(per_layer) == kv_bytes_per_token(ARCH["qwen3.5-9b"])
        assert (
            estimate_model_memory(per_layer, context_length=65536)
            == estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536)
        )

    def test_recurrent_state_and_host_checkpoints(self):
        assert ARCH["qwen3.5-9b"]["recurrent_state_bytes"] == 52690944
        state_gib = 52690944 / 1024 ** 3
        default = estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536)
        assert default.recurrent_state_gib == round(state_gib, 3)
        assert default.host_checkpoint_gib == round(LLAMA_DEFAULT_CTX_CHECKPOINTS * state_gib, 3)
        capped = estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536, ctx_checkpoints=4)
        assert capped.host_checkpoint_gib == round(4 * state_gib, 3)
        assert capped.device_gib == default.device_gib
        assert abs(capped.total_gib - (capped.device_gib + 4 * state_gib)) <= 0.01
        # KV is shared across slots (n_ctx is total); recurrent state is not.
        two = estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536, parallel=2)
        assert two.kv_gib == default.kv_gib
        assert two.recurrent_state_gib == round(2 * state_gib, 3)
        dense = estimate_model_memory(ARCH["ministral3-8b"], context_length=65536)
        assert dense.recurrent_state_gib == dense.host_checkpoint_gib == 0

    def test_weights_prefer_the_file_on_disk(self):
        model = ARCH["qwen3.5-9b"]
        catalog = estimate_model_memory(model, context_length=65536)
        assert catalog.weights_gib == round(5680522464 / 1024 ** 3, 3)
        on_disk = estimate_model_memory(model, context_length=65536, weight_size_mb=6000)
        assert on_disk.weights_gib == round(6000 / 1024, 3)

    def test_metadata_less_entries_keep_the_legacy_numbers(self):
        legacy = {
            "id": "qwen3.6-35b-a3b-ud-q4", "size_mb": 21110,
            "vram_required_gb": 24, "context_length": 131072,
        }
        estimate = estimate_model_memory(legacy)
        assert estimate.method == "legacy-heuristic"
        assert not architecture_metadata_complete(legacy)
        # The historical selector figure the review quotes (34.6 GB).
        assert estimate.device_gib == required_model_memory_gb(legacy) == 34.62
        # Layer metadata without recurrent_state_bytes is not a reviewed
        # layout: it keeps the historical floor and no overhead.
        phi_mini = {
            "size_mb": 2490, "vram_required_gb": 4, "context_length": 32768,
            "block_count": 32, "attention_head_count_kv": 8,
            "embedding_length": 3072, "attention_head_count": 24,
        }
        assert estimate_model_memory(phi_mini).device_gib == round(max(4, 2490 / 1024 + 4.0), 2)

    def test_architecture_estimate_ignores_the_declared_floor(self):
        model = {**ARCH["qwen3.5-9b"], "vram_required_gb": 30, "context_length": 65536}
        assert required_model_memory_gb(model) == 7.77

    def test_runtime_profile_env_feeds_the_estimate(self):
        model = {**ARCH["qwen3.5-4b"], "context_length": 65536}
        profile = {
            "context_length": 65536,
            "env": {
                "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_CACHE_TYPE_V": "q8_0",
                "LLAMA_ARG_CTX_CHECKPOINTS": "4",
            },
        }
        device = required_model_memory_gb(model, runtime_profile=profile)
        total = required_model_memory_gb(model, runtime_profile=profile, include_host_state=True)
        assert device == 4.05
        assert total == 4.25
        # A hand-measured profile budget stays authoritative.
        assert required_model_memory_gb(model, runtime_profile={**profile, "estimated_required_gb": 7.2}) == 7.2

    def test_fit_margin_by_memory_class(self):
        assert fit_margin_gib(8.0, "discrete") == DISCRETE_FIT_MARGIN_MIN_GIB
        assert fit_margin_gib(32.0, "discrete") == 0.96
        assert fit_margin_gib(64.0, "unified") == 0.0
        assert fit_margin_gib(6.0, "cpu") == 0.0
        assert memory_fits(19.0, 20.0, "discrete", architecture_estimate=True)
        assert not memory_fits(19.5, 20.0, "discrete", architecture_estimate=True)
        # Legacy and authored estimates keep the historical +0.25 tolerance.
        assert memory_fits(20.2, 20.0, "discrete", architecture_estimate=False)

    def test_context_fitting_starts_at_the_catalog_default(self):
        model = {**ARCH["qwen3.5-9b"], "context_length": 65536, "max_context_length": 262144}
        assert context_fitting_model(model, 48.0, memory_class="discrete") is model
        stepped = context_fitting_model(model, 7.5, memory_class="discrete")
        assert stepped["context_length"] == 32768
        assert stepped["max_context_length"] == 262144
        # A floor may raise the context above the default when the model's
        # native maximum allows it, and a floor below it is only a fallback.
        short = {**model, "context_length": 32768}
        assert context_fitting_model(short, 48.0, min_context=65536, memory_class="discrete")["context_length"] == 65536
        assert context_fitting_model(short, 7.5, min_context=65536, memory_class="discrete")["context_length"] == 32768
        capped = {**short, "max_context_length": 32768}
        assert context_fitting_model(capped, 48.0, min_context=65536, memory_class="discrete") is capped

    def test_sliding_window_layers_hold_only_the_window(self):
        gemma = ARCH["gemma4-26b-a4b"]
        # Only the 5 full-attention layers grow with the context.
        assert kv_bytes_per_token(gemma) == 5 * 2 * (512 + 512) * 2
        assert sliding_window_kv_bytes_per_cell(gemma) == 25 * 8 * (256 + 256) * 2
        # llama.cpp b9014: n_swa * n_seq + n_ubatch (512), padded to 256,
        # capped at the context.
        assert sliding_window_cells(gemma, 65536) == 1536
        assert sliding_window_cells(gemma, 65536, parallel=2) == 2560
        assert sliding_window_cells(gemma, 1024) == 1024
        at_64k = estimate_model_memory(gemma, context_length=65536)
        at_128k = estimate_model_memory(gemma, context_length=131072)
        swa_gib = 204800 * 1536 / 1024 ** 3
        assert at_64k.swa_kv_gib == at_128k.swa_kv_gib == round(swa_gib, 3)
        assert at_64k.kv_gib == round(20480 * 65536 / 1024 ** 3 + swa_gib, 3)
        # Doubling the context adds only the full-attention KV (1.25 GiB);
        # charging every layer at the full context would add 12.5 GiB.
        assert round(at_128k.device_gib - at_64k.device_gib, 2) == 1.25
        assert at_64k.device_gib == 17.77
        # Context checkpoints copy the window state for each checkpoint.
        assert at_64k.host_checkpoint_gib == round(LLAMA_DEFAULT_CTX_CHECKPOINTS * swa_gib, 3)
        assert estimate_model_memory(gemma, context_length=65536, ctx_checkpoints=0).host_checkpoint_gib == 0

    def test_models_without_a_window_are_unchanged(self):
        assert sliding_window_kv_bytes_per_cell(ARCH["qwen3.5-9b"]) == 0
        assert sliding_window_cells(ARCH["qwen3.5-9b"], 65536) == 0
        assert estimate_model_memory(ARCH["qwen3.5-9b"], context_length=65536).swa_kv_gib == 0

    def test_checkpoint_state_is_the_state_that_cannot_roll_back(self):
        # Hybrid: the recurrent state (50.251 MiB per checkpoint in the b9014
        # log on the Mac mini M4 for Qwen3.5-9B).
        assert checkpoint_state_bytes(ARCH["qwen3.5-9b"]) == 52690944
        # Sliding window: the window cells for one sequence.
        gemma = ARCH["gemma4-26b-a4b"]
        assert checkpoint_state_bytes(gemma) == 204800 * 1536
        assert checkpoint_state_bytes(gemma, cache_type_k="q8_0", cache_type_v="q8_0") < 204800 * 1536
        # Dense: nothing to checkpoint. Unreviewed layout: unknown.
        assert checkpoint_state_bytes(ARCH["ministral3-8b"]) == 0
        assert checkpoint_state_bytes({"id": "unreviewed", "size_mb": 5000}) is None
        # The estimate charges this per checkpoint and per slot.
        per_gib = checkpoint_state_bytes(gemma) / 1024 ** 3
        two = estimate_model_memory(gemma, context_length=65536, parallel=2, ctx_checkpoints=64)
        assert two.host_checkpoint_gib == round(2 * 64 * per_gib, 3)

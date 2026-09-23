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
        raw = next(item for item in _catalog_entries() if item["id"] == "phi4-mini-q4")
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

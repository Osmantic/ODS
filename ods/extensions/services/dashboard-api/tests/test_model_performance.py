"""Unit tests for the model-performance recording helpers in helpers.py.

Covered:
  * _normalize_perf_key / _performance_key — the pure key-composition logic.
  * record_model_performance — input guards, first-sample vs EWMA smoothing,
    sample-count accumulation and persistence.
  * get_recorded_model_performance — most-specific-to-base key fallback.

The _PERF_FILE path is redirected to an isolated tmp file by the ``data_dir``
fixture (see conftest.py).
"""

from __future__ import annotations

import helpers


# ---------------------------------------------------------------------------
# Key composition (pure)
# ---------------------------------------------------------------------------


def test_normalize_perf_key_slugifies():
    assert helpers._normalize_perf_key("NVIDIA GeForce RTX 4090") == "nvidia-geforce-rtx-4090"


def test_normalize_perf_key_strips_edge_separators_and_handles_none():
    assert helpers._normalize_perf_key("  Llama-3.1!!  ") == "llama-3-1"
    assert helpers._normalize_perf_key(None) == ""


def test_performance_key_base_and_optional_parts():
    base = helpers._performance_key("nvidia", "RTX 4090", "Llama 3")
    assert base == "nvidia:rtx-4090:llama-3"

    full = helpers._performance_key(
        "nvidia", "RTX 4090", "Llama 3",
        context_length=8192, gguf="Q4_K_M", vram_total_mb=24564,
    )
    # gguf, ctx and vram (rounded to GiB) are appended in order.
    assert full == "nvidia:rtx-4090:llama-3:q4-k-m:ctx-8192:vram-24gb"


# ---------------------------------------------------------------------------
# record_model_performance — input guards
# ---------------------------------------------------------------------------


def test_record_skips_without_model_or_gpu(data_dir):
    helpers.record_model_performance(None, "RTX 4090", "nvidia", 50.0)
    helpers.record_model_performance("Llama 3", None, "nvidia", 50.0)
    assert helpers.get_model_performance_samples() == []


def test_record_skips_non_positive_or_nonnumeric_tps(data_dir):
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", 0)
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", -5)
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", "not-a-number")
    assert helpers.get_model_performance_samples() == []


# ---------------------------------------------------------------------------
# record_model_performance — smoothing and persistence
# ---------------------------------------------------------------------------


def test_first_sample_stored_verbatim(data_dir):
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", 50.0)
    sample = helpers.get_recorded_model_performance("Llama 3", "RTX 4090", "nvidia")
    assert sample is not None
    assert sample["tokens_per_second"] == 50.0
    assert sample["last_tokens_per_second"] == 50.0
    assert sample["sample_count"] == 1


def test_second_sample_applies_ewma_and_increments_count(data_dir):
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", 50.0)
    helpers.record_model_performance("Llama 3", "RTX 4090", "nvidia", 100.0)
    sample = helpers.get_recorded_model_performance("Llama 3", "RTX 4090", "nvidia")
    # EWMA: 50 * 0.8 + 100 * 0.2 = 60.0
    assert sample["tokens_per_second"] == 60.0
    assert sample["last_tokens_per_second"] == 100.0
    assert sample["sample_count"] == 2


def test_lookup_falls_back_from_specific_to_base_key(data_dir):
    # Recorded with full specificity (gguf + vram); stored under the full key
    # and the base key.
    helpers.record_model_performance(
        "Llama 3", "RTX 4090", "nvidia", 42.0,
        gguf="Q4_K_M", vram_total_mb=24564,
    )
    # A query lacking gguf/vram must still resolve via the base-key fallback.
    sample = helpers.get_recorded_model_performance("Llama 3", "RTX 4090", "nvidia")
    assert sample is not None
    assert sample["tokens_per_second"] == 42.0


def test_lookup_returns_none_for_unknown_pair(data_dir):
    assert helpers.get_recorded_model_performance("Unknown", "Unknown", "nvidia") is None

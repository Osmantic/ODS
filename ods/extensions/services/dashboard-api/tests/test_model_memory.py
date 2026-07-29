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

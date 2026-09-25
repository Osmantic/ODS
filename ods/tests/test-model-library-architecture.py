#!/usr/bin/env python3
"""Every auto-selectable catalog model carries a reviewed memory layout.

The installer ranks models by curated priority and checks fit with
model_memory.estimate_model_memory(), which is only as good as the layout the
catalog declares. These checks keep the catalog and the estimator honest:

* every installable entry (except Gemma 4 31B) declares its attention
  layout, recurrent state, pinned GGUF size and the config.json revision it
  came from, and so does every other entry that declares a layout;
* operating contexts fit inside the model's native maximum and meet the 64K
  Hermes floor; an entry below the floor is one whose native maximum is
  below it, and its app compatibility says ODS Talk cannot run it;
* ``vram_required_gb`` (what the dashboard shows) sits just above the
  estimate at the operating context;
* runtime profiles either carry a fleet-measured budget or let the estimator
  compute one from their cache settings;
* ``gpu_residency`` (the GPU-residency projection's measured or GGUF-derived
  inputs) agrees with the layout wherever an entry carries both: one KV
  formula and one recurrent-state size, whichever path reads them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"
sys.path.insert(0, str(ROOT / "extensions" / "services" / "dashboard-api"))

from model_memory import (  # noqa: E402
    MIB,
    architecture_metadata_complete,
    estimate_model_memory,
    kv_bytes_per_token,
    kv_layer_count,
)

HERMES_CONTEXT_FLOOR = 65536
# Gemma 4 31B already runs above the Hermes floor (131K) and its layout was
# not reviewed; it keeps the legacy estimate. The other Gemma 4 entries
# declare their sliding-window layout.
GEMMA_EXEMPT = {"gemma4-31b-q4"}
LAYOUT_FIELDS = (
    "block_count", "attention_head_count_kv",
    "attention_key_length", "attention_value_length", "recurrent_state_bytes",
    "size_bytes", "max_context_length", "architecture_source",
)
SLIDING_WINDOW_FIELDS = (
    "sliding_window", "sliding_window_layer_count", "sliding_window_head_count_kv",
    "sliding_window_key_length", "sliding_window_value_length", "attention_layer_count",
)
TALK_BLOCKING_STATUSES = {
    "blocked", "incompatible", "not_recommended", "not_supported", "unsupported",
    "unsupported_until_revalidated", "not_agent_viable",
}


def _models() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def _installable(model: dict) -> bool:
    return bool(model.get("gguf_url")) and model.get("install_recommendation", True) is not False


def _selected_models() -> list[dict]:
    return [
        model for model in _models()
        if _installable(model) and str(model.get("source") or "") in {"", "curated"}
        and model["id"] not in GEMMA_EXEMPT
    ]


def _reviewed_models() -> list[dict]:
    """Installable entries plus every other entry that declares a layout."""
    selected = {model["id"] for model in _selected_models()}
    return [
        model for model in _models()
        if model["id"] in selected or "recurrent_state_bytes" in model
    ]


def _ids(models):
    return [model["id"] for model in models]


def test_gemma_exemption_is_explicit():
    gemma = {
        model["id"] for model in _models()
        if model.get("family") == "gemma4" and _installable(model)
        and "recurrent_state_bytes" not in model
    }
    assert gemma == GEMMA_EXEMPT


@pytest.mark.parametrize("model", _reviewed_models(), ids=_ids(_reviewed_models()))
def test_entries_declare_a_reviewed_layout(model):
    missing = [field for field in LAYOUT_FIELDS if field not in model]
    assert not missing, (model["id"], missing)
    source = model["architecture_source"]
    assert re.fullmatch(r"[0-9a-f]{40}", source["revision"]), model["id"]
    assert "/" in source["repo"], model["id"]
    assert architecture_metadata_complete(model), model["id"]
    assert 0 < kv_layer_count(model) <= model["block_count"], model["id"]
    assert model["recurrent_state_bytes"] >= 0
    if "sliding_window" in model:
        # Sliding-window layouts: the attention_* keys describe the
        # full-attention layers; layers beyond both sets reuse another
        # layer's cache (Gemma 4 shared-KV layers) and hold no state.
        missing = [field for field in SLIDING_WINDOW_FIELDS if field not in model]
        assert not missing, (model["id"], missing)
        assert model["recurrent_state_bytes"] == 0, model["id"]
        assert kv_layer_count(model) + model["sliding_window_layer_count"] <= model["block_count"]
        return
    # Hybrid layouts must carry their recurrent state; dense ones have none.
    hybrid = kv_layer_count(model) < model["block_count"]
    assert (model["recurrent_state_bytes"] > 0) == hybrid, model["id"]


@pytest.mark.parametrize("model", _selected_models(), ids=_ids(_selected_models()))
def test_operating_context_fits_the_native_maximum_and_hermes(model):
    assert model["context_length"] <= model["max_context_length"], model["id"]
    assert model["context_length"] >= HERMES_CONTEXT_FLOOR, model["id"]


def test_contexts_below_the_hermes_floor_are_native_limits_marked_for_talk():
    """A catalog context under 64K keeps a model out of Hermes and Pixel.

    It is allowed only when the model's own maximum is under the floor, and
    then the entry must tell ODS Talk up front (app_compatibility), instead of
    Hermes failing with a 502 after the model loads.
    """
    for model in _models():
        if not model.get("gguf_url") or str(model.get("source") or "") not in {"", "curated"}:
            continue
        if int(model.get("context_length") or 0) >= HERMES_CONTEXT_FLOOR:
            continue
        native = int(model.get("max_context_length") or model["context_length"])
        assert native < HERMES_CONTEXT_FLOOR, (model["id"], model["context_length"], native)
        talk = ((model.get("app_compatibility") or {}).get("hermes_talk") or {})
        assert str(talk.get("status") or "").lower() in TALK_BLOCKING_STATUSES, model["id"]


@pytest.mark.parametrize("model", _reviewed_models(), ids=_ids(_reviewed_models()))
def test_declared_vram_tracks_the_estimate(model):
    estimate = estimate_model_memory(model).device_gib
    assert estimate <= model["vram_required_gb"] <= estimate + 3.0, (model["id"], estimate)


@pytest.mark.parametrize("model", _selected_models(), ids=_ids(_selected_models()))
def test_runtime_profiles_state_where_their_budget_comes_from(model):
    for profile in model.get("runtime_profiles") or []:
        if profile.get("estimate_source") == "fleet-validated":
            assert profile.get("estimated_required_gb"), (model["id"], profile["id"])
            continue
        # Estimator-derived profiles carry no hand-written budget: the
        # selector computes it from the profile's cache settings, so the two
        # cannot drift apart.
        assert "estimated_required_gb" not in profile, (model["id"], profile["id"])
        assert profile.get("context_length"), (model["id"], profile["id"])
        env = profile.get("env") or {}
        if env.get("LLAMA_ARG_CACHE_TYPE_V", "f16") not in {"f16", "f32", "bf16"}:
            assert env.get("LLAMA_ARG_FLASH_ATTN") == "on", (model["id"], profile["id"])


def test_selection_priorities_cover_every_memory_class():
    for model in _models():
        selection = model.get("selection")
        if selection is None:
            continue
        assert {"discrete", "unified", "cpu"} <= set(selection), model["id"]
        for memory_class in ("discrete", "unified", "cpu"):
            assert isinstance(selection[memory_class], int) and selection[memory_class] >= 0, model["id"]
        assert set(selection) <= {"discrete", "unified", "cpu", "min_capacity_gib"}, model["id"]
        if model["id"] not in GEMMA_EXEMPT and any(selection[c] for c in ("discrete", "unified", "cpu")):
            assert _installable(model), model["id"]


def test_demoted_models_say_why():
    for model in _models():
        if model.get("install_recommendation") is False and model.get("install_recommendation_reason") is not None:
            assert len(model["install_recommendation_reason"]) > 20, model["id"]
    demoted = {
        "phi4-mini-q4", "phi4-q4", "deepseek-r1-7b-q4", "deepseek-r1-14b-q4",
        "deepseek-r1-32b-q4", "deepseek-r1-70b-q4", "qwen3.5-35b-a3b-q4", "qwen3-30b-a3b-q4",
    }
    by_id = {model["id"]: model for model in _models()}
    for model_id in demoted:
        assert by_id[model_id]["install_recommendation"] is False, model_id
        assert by_id[model_id]["install_recommendation_reason"], model_id
    # Qwen3-30B-A3B's config.json declares 40,960 positions.
    assert by_id["qwen3-30b-a3b-q4"]["context_length"] == 40960
    assert by_id["qwen3-30b-a3b-q4"]["max_context_length"] == 40960



def _qwen_tier_map_files() -> dict[str, set[str]]:
    files = {}
    linux = (ROOT / "installers" / "lib" / "tier-map.sh").read_text(encoding="utf-8")
    linux = linux[linux.index("set_qwen_tier_config()"):linux.index("set_gemma4_tier_config()")]
    files["installers/lib/tier-map.sh"] = set(re.findall(r'GGUF_FILE="([^"]+)"', linux))
    macos = (ROOT / "installers" / "macos" / "lib" / "tier-map.sh").read_text(encoding="utf-8")
    macos = macos[macos.index("set_qwen_tier_config()"):macos.index("set_gemma4_tier_config()")]
    files["installers/macos/lib/tier-map.sh"] = set(re.findall(r'GGUF_FILE="([^"]+)"', macos))
    windows = (ROOT / "installers" / "windows" / "lib" / "tier-map.ps1").read_text(encoding="utf-8")
    windows = windows[windows.index("function Resolve-QwenTierConfig"):windows.index("function Resolve-GemmaTierConfig")]
    files["installers/windows/lib/tier-map.ps1"] = set(re.findall(r'GgufFile\s*=\s*"([^"]+)"', windows))
    return {name: {item for item in found if item} for name, found in files.items()}


def test_tier_map_defaults_are_install_recommendations():
    installable = {model["gguf_file"] for model in _models() if _installable(model)}
    for tier_map, files in _qwen_tier_map_files().items():
        assert files, tier_map
        stale = sorted(files - installable)
        assert not stale, (tier_map, stale)



@pytest.mark.parametrize(
    "model",
    [model for model in _models() if isinstance(model.get("gpu_residency"), dict)],
    ids=lambda model: model["id"],
)
def test_gpu_residency_agrees_with_the_layout(model):
    """One KV formula: model_memory.estimated_device_memory_mib reads the
    layout first and falls back to ``gpu_residency`` only without one, so the
    two must never disagree where both exist."""
    residency = model["gpu_residency"]
    assert residency.get("basis") in {"measured", "gguf"}, model["id"]
    assert residency.get("source"), model["id"]
    per_token = kv_bytes_per_token(model)
    if per_token is not None:
        assert residency["kv_bytes_per_token_f16"] == per_token, model["id"]
    state = model.get("recurrent_state_bytes")
    if state is not None:
        assert residency["recurrent_state_mib"] == pytest.approx(state / MIB, abs=0.01), model["id"]
    if model.get("embedding_length") is not None and residency.get("embedding_length") is not None:
        assert residency["embedding_length"] == model["embedding_length"], model["id"]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

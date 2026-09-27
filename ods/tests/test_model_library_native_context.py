#!/usr/bin/env python3
"""Catalog contexts must fit the model's native (GGUF training) context.

llama.cpp caps every server slot at the GGUF ``<arch>.context_length``
(``n_ctx_train``). A catalog context above it is never served, so the host
agent's context proof cannot pass and activation rolls back (tower2
2026-09-25: qwen3-30b-a3b-q4 asked for 131072 on a 40960-token GGUF).

``NATIVE_CONTEXT`` pins ``<arch>.context_length`` read from each catalog
artifact's GGUF header (the first shard for split models) on 2026-09-25.
Record the header value when adding or re-pinning an artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"

NATIVE_CONTEXT = {
    "qwen3.5-2b-q4": 262144,
    "jamba-reasoning-3b-q4": 262144,
    "phi4-mini-q4": 131072,
    "phi3.5-mini-q4": 131072,
    "phi4-mini-reasoning-q4": 131072,
    "qwen2.5-1.5b-instruct-q4": 32768,
    "qwen2.5-0.5b-instruct-q4": 32768,
    "granite3.3-2b-instruct-q4": 131072,
    "granite4.0-h-micro-q4": 1048576,
    "granite4.0-h-tiny-q4": 1048576,
    "smollm3-3b-q4": 65536,
    "gemma3-4b-it-q4": 131072,
    "granite4.0-h-1b-q4": 1048576,
    "falcon-h1-1.5b-instruct-q4": 131072,
    "falcon-h1-3b-instruct-q4": 131072,
    "nvidia-nemotron3-nano-4b-q4": 1048576,
    "granite4.1-3b-q4": 131072,
    "granite4.0-1b-q4": 131072,
    "granite4.0-h-350m-q4": 1048576,
    "granite3.2-2b-instruct-q4": 131072,
    "granite3.1-2b-instruct-q4": 131072,
    "phi3-mini-128k-q4": 131072,
    "ministral3-8b-instruct-2512-q4": 262144,
    "ministral-3b-instruct-q4": 131072,
    "llama3.2-1b-instruct-q4": 131072,
    "llama3.2-3b-instruct-q4": 131072,
    "qwen2.5-3b-instruct-q4": 32768,
    "qwen3-4b-q4": 40960,
    "qwen3-4b-instruct-2507-q4": 262144,
    "qwen3-4b-128k-q4": 131072,
    "qwen3-1.7b-q4": 40960,
    "qwen2.5-coder-1.5b-128k-q4": 131072,
    "qwen2.5-coder-3b-128k-q4": 131072,
    "qwen2.5-7b-instruct-q4": 131072,
    "llama3.1-8b-instruct-q4": 131072,
    "granite3.3-8b-instruct-q4": 131072,
    "mistral-nemo-12b-instruct-q4": 1024000,
    "qwen3.5-4b-q4": 262144,
    "gemma4-e2b-q4": 131072,
    "deepseek-r1-7b-q4": 131072,
    "gemma4-e4b-q4": 131072,
    "qwen3.5-9b-q4": 262144,
    "phi4-q4": 16384,
    "deepseek-r1-14b-q4": 131072,
    "qwen3.8-27b-iq4-xs": 262144,
    "qwen3.5-27b-q4": 262144,
    "qwen3.6-27b-ud-q4-k-xl": 262144,
    "gemma4-26b-a4b-q4": 262144,
    "qwen3-30b-a3b-q4": 40960,
    "gemma4-31b-q4": 262144,
    "deepseek-r1-32b-q4": 131072,
    "qwen3.5-35b-a3b-q4": 262144,
    "qwen3.6-35b-a3b-ud-q4": 262144,
    "kat-coder-v2.5-dev-apex-q4": 262144,
    "deepseek-r1-70b-q4": 131072,
    "qwen3-coder-next-q4": 262144,
    "llama4-scout-q4": 10485760,
    "qwen3.5-122b-a10b-q4": 262144,
}


# Entries whose declared max_context_length is below the GGUF header, and why.
# Every other entry declares exactly its header value.
DOCUMENTED_BELOW_HEADER = {
    # nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 config.json (the entry's pinned
    # architecture_source revision) sets max_position_embeddings 262144, and
    # the model card says "Context length up to 262K". NVIDIA's GGUF header
    # says 1048576, which no published evaluation of the 4B backs; ODS keeps
    # the documented maximum (llama.cpp serves it: it is below the header).
    "nvidia-nemotron3-nano-4b-q4": 262144,
}


def _catalog() -> dict[str, dict]:
    models = json.loads(CATALOG.read_text(encoding="utf-8"))["models"]
    return {model["id"]: model for model in models}


def _declared_contexts(model: dict) -> list[tuple[str, int]]:
    contexts = [
        (key, int(model[key]))
        for key in ("context_length", "max_context_length")
        if model.get(key) is not None
    ]
    for profile in model.get("runtime_profiles") or []:
        if isinstance(profile, dict) and profile.get("context_length") is not None:
            contexts.append(
                (f"runtime_profiles[{profile.get('id')}].context_length", int(profile["context_length"]))
            )
    return contexts


def test_every_catalog_artifact_records_its_native_context() -> None:
    catalog_ids = set(_catalog())
    assert not catalog_ids - set(NATIVE_CONTEXT), (
        "record the GGUF <arch>.context_length for new catalog entries"
    )
    assert not set(NATIVE_CONTEXT) - catalog_ids, "drop removed catalog entries"


@pytest.mark.parametrize("model_id", sorted(NATIVE_CONTEXT))
def test_catalog_context_fits_native_training_context(model_id: str) -> None:
    model = _catalog()[model_id]
    native = NATIVE_CONTEXT[model_id]
    over = [(key, value) for key, value in _declared_contexts(model) if value > native]
    assert not over, (
        f"{model_id} declares {over} above its {native}-token GGUF training "
        "context; llama.cpp caps the slot there, so activation can never prove it"
    )


@pytest.mark.parametrize("model_id", sorted(NATIVE_CONTEXT))
def test_every_entry_declares_its_native_maximum(model_id: str) -> None:
    """``max_context_length`` is the ceiling every load path applies
    (model_selection.declared_max_context): a switch, a restore, the host
    agent's replay of the installer's record and an owner's explicit
    context. An entry without it has no ceiling, so a stale context above
    the native one reaches llama.cpp and the activation rolls back."""
    model = _catalog()[model_id]
    declared = model.get("max_context_length")
    assert isinstance(declared, int) and not isinstance(declared, bool), (
        f"{model_id} must declare max_context_length (GGUF <arch>.context_length: {NATIVE_CONTEXT[model_id]})"
    )
    expected = DOCUMENTED_BELOW_HEADER.get(model_id, NATIVE_CONTEXT[model_id])
    assert declared == expected, (model_id, declared, expected)
    assert model["context_length"] <= declared, model_id


def test_documented_exceptions_stay_below_the_header() -> None:
    for model_id, declared in DOCUMENTED_BELOW_HEADER.items():
        assert declared < NATIVE_CONTEXT[model_id], model_id


def test_previously_overstated_entries_use_native_context() -> None:
    catalog = _catalog()
    assert catalog["qwen3-30b-a3b-q4"]["context_length"] == 40960
    assert catalog["qwen2.5-1.5b-instruct-q4"]["context_length"] == 32768
    assert catalog["qwen2.5-0.5b-instruct-q4"]["context_length"] == 32768
    # At its real 40K context Qwen3-30B-A3B would newly fit and outrank the
    # Qwen3.5-27B 24-32 GB default; its old 131072 auto-picks (tier-3 size
    # ceiling hosts) could never pass readiness. Keep it library-only.
    assert catalog["qwen3-30b-a3b-q4"]["install_recommendation"] is False

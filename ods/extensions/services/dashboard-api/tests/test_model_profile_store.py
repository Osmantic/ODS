"""WP3 profile store (TEST-PLAN T-U-3 cache and store rows)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_profile import store  # noqa: E402

RESULT = {"suite": "1", "status": "complete", "probes": {"P1": {"status": "pass"}}, "summary": {"chat": True}}


def _key(**overrides):
    values = {
        "gguf_sha256": ["a" * 64],
        "projector_sha256": None,
        "build_info": "b11429-d81235049",
        "backend": "nvidia",
        "template_sha256": "b" * 64,
        "template_source": "embedded",
        "suite": "1",
        "host": "0123456789abcdef",
    }
    values.update(overrides)
    return store.profile_key(**values)


def _profile(key=None, model_id="qwen3.5-9b", when=None):
    return store.recorded_profile(
        key or _key(), model_id=model_id, gguf_file="Qwen3.5-9B-Q4_K_M.gguf", result=RESULT,
        product_version="test", now=when or datetime(2026, 10, 9, tzinfo=timezone.utc))


@pytest.mark.parametrize("change", [
    {"gguf_sha256": ["c" * 64]},
    {"gguf_sha256": ["a" * 64, "d" * 64]},
    {"projector_sha256": "e" * 64},
    {"build_info": "b9014-d4b0c22f"},
    {"backend": "amd"},
    {"template_sha256": "f" * 64},
    {"template_source": "override:qwen-tools-fix"},
    {"suite": "2"},
    {"host": "fedcba9876543210"},
])
def test_any_key_part_change_is_a_cache_miss(change):
    doc = store.with_profile(store.empty(), _profile())
    assert store.find(doc, _key()) is not None
    assert store.find(doc, _key(**change)) is None


def test_atomic_write_round_trips_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "data" / "model-profiles.json"
    doc = store.with_last_activation(store.with_profile(store.empty(), _profile()), "qwen3.5-9b",
                                     store.key_hash(_key()))
    store.atomic_write(path, doc)
    assert store.load(path) == doc
    assert [p.name for p in path.parent.iterdir()] == ["model-profiles.json"]


def test_a_windows_sharing_violation_is_retried(tmp_path, monkeypatch):
    path = tmp_path / "model-profiles.json"
    real_replace = os.replace
    calls = []

    def flaky_replace(source, target):
        calls.append(target)
        if len(calls) < 3:
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(source, target)

    monkeypatch.setattr(store.os, "replace", flaky_replace)
    store.atomic_write(path, store.with_profile(store.empty(), _profile()))
    assert len(calls) == 3
    assert store.load(path)["profiles"][0]["modelId"] == "qwen3.5-9b"


def test_invalid_stores_are_refused_on_write_and_reported_on_read(tmp_path):
    bad = store.with_profile(store.empty(), _profile())
    bad["profiles"][0]["keyHash"] = "0" * 64
    with pytest.raises(store.StoreError, match="keyHash"):
        store.atomic_write(tmp_path / "x.json", bad)
    path = tmp_path / "model-profiles.json"
    path.write_text(json.dumps({"schema": "other", "profiles": []}), encoding="utf-8")
    with pytest.raises(store.StoreError, match="schema"):
        store.load(path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(store.StoreError, match="not JSON"):
        store.load(path)


def test_a_missing_store_is_empty(tmp_path):
    assert store.load(tmp_path / "absent.json") == store.empty()


def test_the_store_is_bounded_and_keeps_the_newest(tmp_path):
    doc = store.empty()
    for index in range(store.PROFILE_LIMIT + 5):
        doc = store.with_profile(doc, _profile(_key(gguf_sha256=[f"{index:064x}"]), model_id=f"m{index}"))
    assert len(doc["profiles"]) == store.PROFILE_LIMIT
    assert doc["profiles"][-1]["modelId"] == f"m{store.PROFILE_LIMIT + 4}"
    assert store.latest_for_model(doc, "m0") is None


def test_re_profiling_one_key_replaces_its_entry():
    first = _profile(when=datetime(2026, 10, 9, tzinfo=timezone.utc))
    second = _profile(when=datetime(2026, 10, 10, tzinfo=timezone.utc))
    doc = store.with_profile(store.with_profile(store.empty(), first), second)
    assert [entry["recordedAt"] for entry in doc["profiles"]] == ["2026-10-10T00:00:00Z"]


def test_latest_for_model_ignores_other_models_and_builds():
    old_build = _profile(_key(build_info="b9014-d4b0c22f"))
    new_build = _profile(_key())
    other = _profile(_key(gguf_sha256=["9" * 64]), model_id="phi4-mini-q4")
    doc = store.empty()
    for profile in (old_build, new_build, other):
        doc = store.with_profile(doc, profile)
    assert store.latest_for_model(doc, "qwen3.5-9b")["key"]["buildInfo"] == "b11429-d81235049"


def test_host_id_is_stable_hashed_and_falls_back_without_a_machine_id(tmp_path):
    machine_id = tmp_path / "machine-id"
    machine_id.write_text("4c4c4544004d3510804bb4c04f4e3732\n", encoding="ascii")
    first = store.host_id(machine_id)
    assert first == store.host_id(machine_id)
    assert len(first) == 16 and "4c4c4544" not in first
    assert len(store.host_id(tmp_path / "missing")) == 16

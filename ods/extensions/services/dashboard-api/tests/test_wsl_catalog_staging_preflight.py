"""Focused preflight and concurrency tests for catalog staging.

These tests exercise the pure-Python staging helper in
``model_switchboard.wsl_lemonade``. No interop, tasks, or services run.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import threading

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[4] / "bin/model_switchboard/wsl_lemonade.py"
_spec = importlib.util.spec_from_file_location("wsl_lemonade_stage_preflight_test", _MODULE_PATH)
wsl_lemonade = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wsl_lemonade)


StageError = wsl_lemonade.StageError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(*entries):
    return {"artifacts": [dict(e) for e in entries]}


def _artifact(name: str, data: bytes) -> dict:
    return {"file": name, "sha256": _sha(data), "size_bytes": len(data)}


def _setup(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    return source, target


def test_mixed_case_duplicate_rejected_even_with_same_hash(tmp_path):
    source, target = _setup(tmp_path)
    payload = b"bytes"
    (source / "Model.gguf").write_bytes(payload)
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("Model.gguf", payload), _artifact("model.gguf", payload))

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_manifest_ambiguous"
    assert list(target.iterdir()) == []


def test_malformed_later_artifact_publishes_nothing(tmp_path):
    source, target = _setup(tmp_path)
    good = b"good-bytes"
    (source / "a.gguf").write_bytes(good)
    (source / "b.gguf").write_bytes(b"b-bytes")
    manifest = _manifest(
        _artifact("a.gguf", good),
        {"file": "b.gguf", "sha256": "not-a-hash", "size_bytes": 7},
    )

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_manifest_untrusted"
    assert list(target.iterdir()) == []


def test_missing_later_artifact_publishes_nothing(tmp_path):
    source, target = _setup(tmp_path)
    good = b"good-bytes"
    (source / "a.gguf").write_bytes(good)
    manifest = _manifest(
        _artifact("a.gguf", good),
        _artifact("b.gguf", b"missing-bytes"),
    )

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_source_missing"
    assert list(target.iterdir()) == []


def test_bad_later_sha_publishes_nothing(tmp_path):
    source, target = _setup(tmp_path)
    good = b"good-bytes"
    (source / "a.gguf").write_bytes(good)
    (source / "b.gguf").write_bytes(b"actual-bytes")
    manifest = _manifest(
        _artifact("a.gguf", good),
        {"file": "b.gguf", "sha256": _sha(b"expected-bytes"), "size_bytes": 12},
    )

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_source_mismatch"
    assert list(target.iterdir()) == []


def test_total_multi_artifact_insufficient_space(tmp_path, monkeypatch):
    source, target = _setup(tmp_path)
    a = b"a" * 1024
    b = b"b" * 1024
    (source / "a.gguf").write_bytes(a)
    (source / "b.gguf").write_bytes(b)
    manifest = _manifest(_artifact("a.gguf", a), _artifact("b.gguf", b))

    # Enough for one artifact plus margin, not both.
    monkeypatch.setattr(wsl_lemonade, "_free_bytes", lambda _p: 1024 + 16 * 1024 * 1024)
    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_no_space"
    assert list(target.iterdir()) == []


def test_existing_target_capacity_reuse(tmp_path, monkeypatch):
    source, target = _setup(tmp_path)
    a = b"a" * 1024
    b = b"b" * 1024
    (source / "a.gguf").write_bytes(a)
    (source / "b.gguf").write_bytes(b)
    # a.gguf already present with matching bytes; only b.gguf needs space.
    (target / "a.gguf").write_bytes(a)
    manifest = _manifest(_artifact("a.gguf", a), _artifact("b.gguf", b))

    # Budget only covers b.gguf plus one margin; a.gguf must not count.
    monkeypatch.setattr(wsl_lemonade, "_free_bytes", lambda _p: 1024 + 16 * 1024 * 1024)
    staged = wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert set(staged) == {target / "a.gguf", target / "b.gguf"}
    assert (target / "a.gguf").read_bytes() == a
    assert (target / "b.gguf").read_bytes() == b


def test_cancel_just_before_publication(tmp_path):
    source, target = _setup(tmp_path)
    payload = b"bytes"
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    cancel = threading.Event()
    real_sha = wsl_lemonade._sha256_file
    calls = {"n": 0}

    def flaky(path, cancel_event=None):
        calls["n"] += 1
        # First call verifies the source during preflight; second verifies
        # the temp copy. Cancel right after the temp verification so the
        # pre-publication check fires.
        result = real_sha(path, cancel_event)
        if calls["n"] == 2:
            cancel.set()
        return result

    import unittest.mock as mock
    with mock.patch.object(wsl_lemonade, "_sha256_file", flaky):
        with pytest.raises(StageError) as excinfo:
            wsl_lemonade.stage_catalog_artifact(source, target, manifest, cancel)
    assert excinfo.value.code == "stage_cancelled"
    assert not (target / "model.gguf").exists()
    leftovers = [p for p in target.iterdir() if p.name.startswith(wsl_lemonade._STAGE_TEMP_PREFIX)]
    assert leftovers == []


def test_target_created_concurrently_no_clobber(tmp_path, monkeypatch):
    source, target = _setup(tmp_path)
    payload = b"bytes"
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    real_link = os.link

    def racing_link(src, dst, *args, **kwargs):
        # Simulate another writer creating the target between our preflight
        # and our promotion. The bytes differ, so we must refuse to clobber.
        Path(dst).write_bytes(b"other-writer")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_target_conflict"
    assert (target / "model.gguf").read_bytes() == b"other-writer"

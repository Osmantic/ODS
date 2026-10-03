"""Catalog staging into the managed Windows runtime store.

These tests exercise the pure-Python staging helper in
``model_switchboard.wsl_lemonade``. No interop, tasks, or services run.
"""
import hashlib
import importlib.util
from pathlib import Path

import pytest


# Load the module under test directly so we do not depend on the host agent.
_MODULE_PATH = Path(__file__).resolve().parents[4] / "bin/model_switchboard/wsl_lemonade.py"
_spec = importlib.util.spec_from_file_location("wsl_lemonade_stage_test", _MODULE_PATH)
wsl_lemonade = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wsl_lemonade)


StageError = wsl_lemonade.StageError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(*entries):
    return {"artifacts": [dict(e) for e in entries]}


def _artifact(name: str, data: bytes) -> dict:
    return {"file": name, "sha256": _sha(data), "size_bytes": len(data)}


def test_stage_single_artifact_promotes_verified_bytes(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"model-bytes" * 1024
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    staged = wsl_lemonade.stage_catalog_artifact(source, target, manifest)

    assert staged == [target / "model.gguf"]
    assert (target / "model.gguf").read_bytes() == payload
    # Source cache is preserved.
    assert (source / "model.gguf").read_bytes() == payload


def test_stage_reuses_existing_verified_target_without_rewrite(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"same-bytes"
    (source / "model.gguf").write_bytes(payload)
    (target / "model.gguf").write_bytes(payload)
    before = (target / "model.gguf").stat().st_ino
    manifest = _manifest(_artifact("model.gguf", payload))

    staged = wsl_lemonade.stage_catalog_artifact(source, target, manifest)

    assert staged == [target / "model.gguf"]
    assert (target / "model.gguf").stat().st_ino == before


def test_stage_rejects_differing_existing_target(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"new-bytes"
    (source / "model.gguf").write_bytes(payload)
    (target / "model.gguf").write_bytes(b"different-bytes")
    manifest = _manifest(_artifact("model.gguf", payload))

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_target_conflict"
    assert (target / "model.gguf").read_bytes() == b"different-bytes"


def test_stage_rejects_source_sha_mismatch(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "model.gguf").write_bytes(b"actual")
    manifest = _manifest({"file": "model.gguf", "sha256": _sha(b"expected"), "size_bytes": 6})

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_source_mismatch"
    assert not (target / "model.gguf").exists()


def test_stage_rejects_size_only_manifest(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "model.gguf").write_bytes(b"bytes")
    manifest = _manifest({"file": "model.gguf", "size_bytes": 5})

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_manifest_untrusted"


def test_stage_rejects_ambiguous_duplicate_basenames(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "model.gguf").write_bytes(b"bytes")
    manifest = _manifest(
        {"file": "model.gguf", "sha256": _sha(b"bytes"), "size_bytes": 5},
        {"file": "model.gguf", "sha256": _sha(b"other"), "size_bytes": 5},
    )

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_manifest_ambiguous"


def test_stage_rejects_symlinked_source(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    real = tmp_path / "real.gguf"
    real.write_bytes(b"bytes")
    (source / "model.gguf").symlink_to(real)
    manifest = _manifest(_artifact("model.gguf", b"bytes"))

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_source_missing"


def test_stage_rejects_symlinked_target(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"bytes"
    (source / "model.gguf").write_bytes(payload)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"other")
    (target / "model.gguf").symlink_to(outside)
    manifest = _manifest(_artifact("model.gguf", payload))

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_target_unsafe"
    assert outside.read_bytes() == b"other"


def test_stage_rejects_unsafe_filename(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    manifest = _manifest({"file": "../escape.gguf", "sha256": _sha(b"x"), "size_bytes": 1})

    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_unsafe_name"


def test_stage_rejects_insufficient_free_space(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"x" * 1024
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    monkeypatch.setattr(wsl_lemonade, "_free_bytes", lambda _p: 0)
    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_no_space"
    assert not (target / "model.gguf").exists()


def test_stage_cleans_up_temp_on_verification_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"bytes"
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    real_sha = wsl_lemonade._sha256_file
    calls = {"n": 0}

    def flaky(path, cancel_event=None):
        calls["n"] += 1
        # First call verifies the source; second call verifies the temp copy.
        if calls["n"] == 2:
            return "0" * 64
        return real_sha(path, cancel_event)

    monkeypatch.setattr(wsl_lemonade, "_sha256_file", flaky)
    with pytest.raises(StageError) as excinfo:
        wsl_lemonade.stage_catalog_artifact(source, target, manifest)
    assert excinfo.value.code == "stage_verify_failed"
    # No temp files left behind, no target created.
    leftovers = [p for p in target.iterdir() if p.name.startswith(wsl_lemonade._STAGE_TEMP_PREFIX)]
    assert leftovers == []
    assert not (target / "model.gguf").exists()


def test_stage_multipart_manifest_stages_every_artifact(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    main = b"main-model"
    projector = b"vision-projector"
    (source / "model.gguf").write_bytes(main)
    (source / "mmproj.gguf").write_bytes(projector)
    manifest = _manifest(_artifact("model.gguf", main), _artifact("mmproj.gguf", projector))

    staged = wsl_lemonade.stage_catalog_artifact(source, target, manifest)

    assert set(staged) == {target / "model.gguf", target / "mmproj.gguf"}
    assert (target / "model.gguf").read_bytes() == main
    assert (target / "mmproj.gguf").read_bytes() == projector


def test_stage_catalog_model_uses_managed_store(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    payload = b"bytes"
    (source / "model.gguf").write_bytes(payload)
    manifest = _manifest(_artifact("model.gguf", payload))

    monkeypatch.setattr(wsl_lemonade, "model_store", lambda *_a, **_k: target)
    staged = wsl_lemonade.stage_catalog_model(
        tmp_path, {}, None, source, manifest,
    )
    assert staged == [target / "model.gguf"]

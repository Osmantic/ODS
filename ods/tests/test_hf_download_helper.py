from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download-hf-artifact.py"
SNAPSHOT_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "download-hf-snapshot.py"
)


def _load_helper():
    spec = importlib.util.spec_from_file_location("download_hf_artifact", HELPER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_snapshot_helper():
    spec = importlib.util.spec_from_file_location(
        "download_hf_snapshot", SNAPSHOT_HELPER_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_huggingface_resolve_url_with_nested_filename():
    helper = _load_helper()

    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://huggingface.co/unsloth/Llama-4-Scout-GGUF/resolve/main/"
        "Q4_K_M/model-00001-of-00002.gguf"
    )

    assert repo_id == "unsloth/Llama-4-Scout-GGUF"
    assert revision == "main"
    assert filename == "Q4_K_M/model-00001-of-00002.gguf"


def test_parse_huggingface_resolve_url_rejects_non_hf_url():
    helper = _load_helper()

    with pytest.raises(ValueError, match="not a Hugging Face URL"):
        helper.parse_huggingface_resolve_url("https://example.com/model.gguf")


@pytest.mark.parametrize("existing_partial", [False, True])
def test_download_artifact_moves_owned_staging_without_copying(
    monkeypatch, tmp_path, existing_partial
):
    helper = _load_helper()
    destination = tmp_path / "models" / "model.gguf.part"
    if existing_partial:
        destination.parent.mkdir()
        destination.write_bytes(b"existing curl partial")
    calls = []
    source_identity = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        staging = Path(kwargs["local_dir"])
        assert staging.parent == destination.parent
        downloaded = staging / "nested" / "model.gguf"
        downloaded.parent.mkdir()
        downloaded.write_bytes(b"GGUF complete")
        source_identity.append(downloaded.stat().st_ino)
        (staging / ".cache").mkdir()
        (staging / ".cache" / "metadata").write_text("owned metadata")
        return str(downloaded)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=fake_hf_hub_download)
    )
    result = helper.download_artifact(
        "https://huggingface.co/org/repo/resolve/abc/nested/model.gguf", destination
    )

    assert result == destination
    assert result.read_bytes() == b"GGUF complete"
    assert result.stat().st_ino == source_identity[0]
    assert calls == [{"repo_id": "org/repo", "filename": "nested/model.gguf",
                      "revision": "abc", "local_dir": calls[0]["local_dir"]}]
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["download", "empty", "replace"])
def test_download_artifact_preserves_partial_and_cleans_owned_staging(
    monkeypatch, tmp_path, failure
):
    helper = _load_helper()
    destination = tmp_path / "model.gguf.part"
    destination.write_bytes(b"existing curl partial")
    unrelated = tmp_path / ".model.gguf.part.hf-unrelated"
    unrelated.mkdir()
    unrelated_file = unrelated / "keep"
    unrelated_file.write_text("another operation")

    def fake_hf_hub_download(**kwargs):
        staging = Path(kwargs["local_dir"])
        downloaded = staging / "model.gguf"
        downloaded.write_bytes(b"" if failure == "empty" else b"GGUF complete")
        if failure == "download":
            raise OSError("interrupted transfer")
        return str(downloaded)

    real_replace = Path.replace

    def fail_replace(source, target):
        if target == destination:
            raise OSError("replacement failed")
        return real_replace(source, target)

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=fake_hf_hub_download)
    )
    if failure == "replace":
        monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises((OSError, RuntimeError)):
        helper.download_artifact(
            "https://huggingface.co/org/repo/resolve/main/model.gguf", destination
        )

    assert destination.read_bytes() == b"existing curl partial"
    assert unrelated_file.read_text() == "another operation"
    assert set(tmp_path.iterdir()) == {destination, unrelated}


def test_download_snapshot_passes_cache_revision_and_patterns(monkeypatch, tmp_path):
    helper = _load_snapshot_helper()
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        snapshot = tmp_path / "models--BAAI--bge-base-en-v1.5" / "snapshots" / "abc"
        snapshot.mkdir(parents=True)
        return str(snapshot)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=fake_snapshot_download),
    )

    result = helper.download_snapshot(
        "BAAI/bge-base-en-v1.5",
        tmp_path / "cache",
        revision="main",
        allow_patterns=["onnx/model.onnx"],
    )

    assert result.name == "abc"
    assert calls == [
        {
            "repo_id": "BAAI/bge-base-en-v1.5",
            "cache_dir": str(tmp_path / "cache"),
            "revision": "main",
            "allow_patterns": ["onnx/model.onnx"],
        }
    ]

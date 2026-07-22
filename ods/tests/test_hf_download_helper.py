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

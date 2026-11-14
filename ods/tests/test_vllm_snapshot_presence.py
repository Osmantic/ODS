"""snapshot_presence: advisory size/presence of a vLLM snapshot folder."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "extensions" / "services" / "dashboard-api" / "model_vllm.py"
REPO = "nvidia/Test-Model-NVFP4"
REVISION = "0123456789abcdef0123456789abcdef01234567"
MODEL_DIR = "nvidia--Test-Model-NVFP4@01234567"
FILES = {
    "config.json": b'{"architectures": ["TestForCausalLM"]}\n',
    "model-00001-of-00001.safetensors": b"w" * 4096,
    "nested/tokenizer.json": b'{"version": "1.0"}' * 50,
}


def _module():
    spec = importlib.util.spec_from_file_location("ods_model_vllm_presence", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(files: dict[str, bytes]) -> list[dict]:
    return [
        {"path": path, "size": len(content), "sha256": "0" * 64}
        for path, content in files.items()
    ]


def _entry(files: dict[str, bytes]) -> dict:
    return {
        "id": "test-model-nvfp4-vllm", "name": "Test Model NVFP4 (vLLM)", "runtime": "vllm",
        "hf_repo": REPO, "revision": REVISION, "files": _manifest(files),
        "served_model_name": "Test-Model-NVFP4", "llm_model_name": "test-model-nvfp4",
        "context_length": 8192, "min_gpu_mib": 1024, "compute_capability": ["10.0"],
        "reasoning": "off", "tool_call_parser": "hermes", "reasoning_parser": "qwen3",
        "thinking_template_kwargs": {"off": {"enable_thinking": False}, "low": {}, "medium": {}, "high": {}},
        "vllm_args": ["--max-model-len", "8192", "--enable-auto-tool-choice",
                      "--tool-call-parser", "hermes", "--reasoning-parser", "qwen3"],
        "selection": {"discrete": 0, "unified": 0, "cpu": 0, "vllm": 1},
    }


def _write_catalog(path: Path, files: dict[str, bytes]) -> Path:
    path.write_text(json.dumps({"version": 2, "models": [_entry(files)]}), encoding="utf-8")
    return path


def _materialize(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    return tmp_path / "models"


def test_missing_root(models_dir):
    module = _module()
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "missing"


def test_complete_snapshot(models_dir):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "complete"


def test_uses_model_dir_name_not_catalog_id(models_dir):
    module = _module()
    entry = _entry(FILES)
    wrong = models_dir / entry["id"]
    _materialize(wrong, FILES)
    assert module.snapshot_presence(entry, str(models_dir)) == "missing"
    _materialize(models_dir / MODEL_DIR, FILES)
    assert module.snapshot_presence(entry, str(models_dir)) == "complete"


def test_wrong_size_is_partial(models_dir):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    (root / "config.json").write_bytes(b"x" * (len(FILES["config.json"]) + 1))
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "partial"


def test_missing_nested_file_is_partial(models_dir):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    (root / "nested" / "tokenizer.json").unlink()
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "partial"


def test_empty_root_is_partial(models_dir):
    module = _module()
    (models_dir / MODEL_DIR).mkdir(parents=True)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "partial"


def test_extra_partial_file_does_not_make_incomplete(models_dir):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    (root / "config.json.partial").write_bytes(b"junk")
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "complete"


@pytest.mark.skipif(os.name == "nt", reason="Windows requires symlink privilege; covered on Linux")
def test_symlinked_root_is_unsafe(models_dir, tmp_path):
    module = _module()
    real = tmp_path / "real"
    _materialize(real, FILES)
    models_dir.mkdir()
    os.symlink(real, models_dir / MODEL_DIR)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "unsafe"


@pytest.mark.skipif(os.name == "nt", reason="Windows requires symlink privilege; covered on Linux")
def test_symlinked_intermediate_dir_is_unsafe(models_dir, tmp_path):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    real = tmp_path / "real_nested"
    real.mkdir()
    (real / "tokenizer.json").write_bytes(FILES["nested/tokenizer.json"])
    (root / "nested" / "tokenizer.json").unlink()
    (root / "nested").rmdir()
    os.symlink(real, root / "nested")
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "unsafe"


@pytest.mark.skipif(os.name == "nt", reason="Windows requires symlink privilege; covered on Linux")
def test_symlinked_file_is_unsafe(models_dir, tmp_path):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    target = root / "config.json"
    real = tmp_path / "real_config.json"
    real.write_bytes(FILES["config.json"])
    target.unlink()
    os.symlink(real, target)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "unsafe"


def test_hardlinked_file_is_unsafe(models_dir, tmp_path):
    module = _module()
    root = models_dir / MODEL_DIR
    _materialize(root, FILES)
    target = root / "config.json"
    other = tmp_path / "other_config.json"
    os.link(target, other)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "unsafe"


@pytest.mark.skipif(os.name == "nt", reason="Windows requires symlink privilege; covered on Linux")
def test_models_dir_symlink_is_resolved_once(models_dir, tmp_path):
    module = _module()
    real = tmp_path / "real_models"
    _materialize(real / MODEL_DIR, FILES)
    os.symlink(real, models_dir)
    assert module.snapshot_presence(_entry(FILES), str(models_dir)) == "complete"


def test_invalid_catalog_is_unsafe(models_dir):
    module = _module()
    entry = _entry(FILES)
    entry["revision"] = "not-a-commit"
    assert module.snapshot_presence(entry, str(models_dir)) == "unsafe"


def test_catalog_round_trip_uses_generated_bytes(tmp_path, models_dir):
    module = _module()
    catalog = _write_catalog(tmp_path / "model-library.json", FILES)
    entry = json.loads(catalog.read_text(encoding="utf-8"))["models"][0]
    _materialize(models_dir / MODEL_DIR, FILES)
    assert module.snapshot_presence(entry, str(models_dir)) == "complete"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

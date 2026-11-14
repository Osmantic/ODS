"""Hardening and reusability of scripts/download-hf-manifest.py.

Reuses the local FakeHub from test_hf_manifest_download.py; adds cancellation,
progress, symlink/hardlink refusal, same-size corrupt replacement and disk
accounting for a replacement.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_hf_manifest_download as base  # noqa: E402


@pytest.fixture(autouse=True)
def roomy_volume(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: base.Usage(10 * 1000 ** 4, 0, 9 * 1000 ** 4))


@pytest.fixture
def hub():
    server = base.FakeHub(base.FILES)
    yield server
    server.close()


def _helper():
    spec = importlib.util.spec_from_file_location("ods_download_hf_manifest_h", base.HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model(tmp_path: Path) -> dict:
    catalog = base.write_catalog(tmp_path / "model-library.json", base.FILES)
    return _helper().find_entry(catalog, "test-model-nvfp4-vllm")


def test_cancellation_keeps_partial_bytes(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    stop = threading.Event()

    with pytest.raises(helper.DownloadCancelled):
        helper.fetch_manifest(model, tmp_path / "models", hub.url,
                              cancelled=stop.is_set,
                              on_progress=lambda done, total: stop.set() if done else None)
    root = tmp_path / "models" / base.MODEL_DIR
    partials = list(root.rglob("*.partial"))
    assert partials, "cancellation must leave partial bytes for a resume"
    assert all(p.stat().st_size > 0 for p in partials)
    helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert base.model_files(tmp_path) == base.FILES


def test_progress_callback_reports_bytes(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    seen: list[tuple[int, int]] = []
    helper.fetch_manifest(model, tmp_path / "models", hub.url,
                          on_progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1][0] == seen[-1][1] == helper.manifest_bytes(model)


def test_same_size_corrupt_final_is_replaced(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    target = root / "config.json"
    target.write_bytes(b"x" * len(base.FILES["config.json"]))
    helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert target.read_bytes() == base.FILES["config.json"]


def test_same_size_corrupt_final_counts_full_replacement(tmp_path):
    helper = _helper()
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    (root / "config.json").write_bytes(b"x" * len(base.FILES["config.json"]))
    needed = helper.bytes_to_download(root, base.manifest(base.FILES), frozenset({"config.json"}))
    assert needed >= len(base.FILES["config.json"])


def test_insufficient_disk_for_replacement_is_refused(tmp_path, hub, monkeypatch):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    (root / "config.json").write_bytes(b"x" * len(base.FILES["config.json"]))
    monkeypatch.setattr(shutil, "disk_usage", lambda path: base.Usage(100 * 1024 ** 3, 0, 0))
    with pytest.raises(helper.NoSpace):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert hub.requests == []


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege unavailable; covered on Linux")
def test_symlinked_final_is_refused(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"keep")
    os.symlink(outside, root / "config.json")
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege unavailable; covered on Linux")
def test_symlinked_partial_is_refused(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    os.symlink(outside, root / "config.json.partial")
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege unavailable; covered on Linux")
def test_symlinked_parent_is_refused(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "nested")
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert list(outside.iterdir()) == []


def test_corrupt_final_survives_failed_replacement(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    original = b"x" * len(base.FILES["config.json"])
    (root / "config.json").write_bytes(original)
    model["files"][0]["git_blob_sha1"] = "0" * 40
    with pytest.raises(helper.Mismatch):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert (root / "config.json").read_bytes() == original


def test_all_present_but_corrupt_file_needs_replacement_space(tmp_path, hub, monkeypatch):
    helper = _helper()
    model = _model(tmp_path)
    helper.fetch_manifest(model, tmp_path / "models", hub.url)
    target = tmp_path / "models" / base.MODEL_DIR / "config.json"
    corrupt = b"x" * target.stat().st_size
    target.write_bytes(corrupt)
    hub.requests.clear()
    monkeypatch.setattr(shutil, "disk_usage", lambda path: base.Usage(100 * 1024 ** 3, 0, 0))
    with pytest.raises(helper.NoSpace):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert target.read_bytes() == corrupt
    assert hub.requests == []


def test_partial_replaced_after_hash_is_not_published(tmp_path, hub, monkeypatch):
    helper = _helper()
    model = _model(tmp_path)
    actual = helper.download

    def substitute(url, partial, item, progress, cancelled=None):
        actual(url, partial, item, progress, cancelled)
        # A distinct same-sized inode must not inherit the prior digest proof.
        partial.path.rename(partial.path.with_suffix(".original"))
        partial.path.write_bytes(b"x" * item["size"])

    monkeypatch.setattr(helper, "download", substitute)
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert not (tmp_path / "models" / base.MODEL_DIR / "config.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors and symlinks")
def test_parent_swapped_during_fetch_never_writes_outside(tmp_path, hub, monkeypatch):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    outside = tmp_path / "outside"
    outside.mkdir()
    actual = helper.fetch

    def swap(url, partial, offset, item, digest, progress, cancelled=None):
        if item["path"].startswith("nested/"):
            (root / "nested").rename(root / "held-nested")
            (root / "nested").symlink_to(outside, target_is_directory=True)
        return actual(url, partial, offset, item, digest, progress, cancelled)

    monkeypatch.setattr(helper, "fetch", swap)
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert list(outside.iterdir()) == []
    assert (root / "held-nested" / "tokenizer.json").read_bytes() == base.FILES["nested/tokenizer.json"]



@pytest.mark.skipif(not hasattr(os, "link"), reason="needs hardlinks")
def test_hardlinked_partial_is_refused(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    os.link(outside, root / "config.json.partial")
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege unavailable; covered on Linux")
def test_symlink_swap_before_publish_is_refused(tmp_path, hub):
    helper = _helper()
    model = _model(tmp_path)
    root = tmp_path / "models" / base.MODEL_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    # Swap the nested parent for a symlink after the first file is verified.
    original = helper.verified_in_place
    swapped = {"done": False}

    def verified(final, item, progress, cancelled=None):
        result = original(final, item, progress, cancelled)
        if not swapped["done"] and item["path"] == "config.json":
            swapped["done"] = True
            nested = root / "nested"
            if nested.exists():
                shutil.rmtree(nested)
            os.symlink(outside, nested)
        return result

    helper.verified_in_place = verified
    with pytest.raises(helper.UnsafePath):
        helper.fetch_manifest(model, tmp_path / "models", hub.url)
    assert list(outside.iterdir()) == []

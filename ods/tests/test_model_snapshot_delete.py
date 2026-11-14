"""delete_snapshot: confined removal of an inactive catalog snapshot.

Uses real temporary files. The primitive is called directly; the host agent's
lifecycle lock and active-alias refusal are out of scope here.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "model_snapshot_files.py"
FOLDER = "owner--model@01234567"


def _helper():
    spec = importlib.util.spec_from_file_location("ods_model_snapshot_files", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog(*paths: str) -> list[dict]:
    return [{"path": p, "size": 0, "sha256": "0" * 64} for p in paths]


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "models" / FOLDER
    root.mkdir(parents=True)
    return root


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_deletes_nested_files_and_partials(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"cfg")
    (root / "config.json.partial").write_bytes(b"partial")
    (root / "nested").mkdir()
    (root / "nested" / "tokenizer.json").write_bytes(b"tok")
    (root / "nested" / "tokenizer.json.partial").write_bytes(b"tokp")
    files = _catalog("config.json", "nested/tokenizer.json")

    removed = helper.delete_snapshot(tmp_path / "models", FOLDER, files)

    assert removed == len(b"cfg") + len(b"partial") + len(b"tok") + len(b"tokp")
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_missing_files_are_skipped(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"cfg")
    files = _catalog("config.json", "nested/tokenizer.json")

    removed = helper.delete_snapshot(tmp_path / "models", FOLDER, files)

    assert removed == len(b"cfg")
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_missing_root_raises(tmp_path):
    helper = _helper()
    (tmp_path / "models").mkdir()
    with pytest.raises(FileNotFoundError):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_unexpected_file_aborts_before_any_deletion(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"cfg")
    (root / "config.json.partial").write_bytes(b"partial")
    (root / "surprise.bin").write_bytes(b"keep")
    files = _catalog("config.json")

    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, files)

    assert (root / "config.json").read_bytes() == b"cfg"
    assert (root / "config.json.partial").read_bytes() == b"partial"
    assert (root / "surprise.bin").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_symlinked_root_is_refused(tmp_path):
    helper = _helper()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_bytes(b"keep")
    (tmp_path / "models").mkdir()
    os.symlink(outside, tmp_path / "models" / FOLDER)

    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))

    assert (outside / "config.json").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_symlinked_parent_is_refused(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tokenizer.json").write_bytes(b"keep")
    os.symlink(outside, root / "nested")

    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("nested/tokenizer.json"))

    assert (outside / "tokenizer.json").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_symlinked_file_is_refused(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"keep")
    os.symlink(outside, root / "config.json")

    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))

    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_hardlinked_file_is_refused(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    os.link(outside, root / "config.json")

    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))

    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_parent_swapped_for_symlink_during_deletion_never_touches_outside(tmp_path, monkeypatch):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"cfg")
    (root / "nested").mkdir()
    (root / "nested" / "tokenizer.json").write_bytes(b"tok")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tokenizer.json").write_bytes(b"keep")
    files = _catalog("config.json", "nested/tokenizer.json")

    real_unlink = os.unlink
    swapped = {"done": False}

    def unlink(name, *, dir_fd=None):
        if not swapped["done"] and name == "config.json":
            swapped["done"] = True
            (root / "nested").rename(root / "held-nested")
            os.symlink(outside, root / "nested")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, files)

    assert (outside / "tokenizer.json").read_bytes() == b"keep"
    assert not (root / "held-nested" / "tokenizer.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_concurrent_unknown_file_is_refused_and_untouched(tmp_path, monkeypatch):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"cfg")
    files = _catalog("config.json")

    real_listdir = os.listdir
    injected = {"done": False}

    def listdir(fd):
        entries = real_listdir(fd)
        if not injected["done"]:
            injected["done"] = True
            (root / "late.bin").write_bytes(b"keep")
            entries = real_listdir(fd)
        return entries

    monkeypatch.setattr(os, "listdir", listdir)
    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, files)

    assert (root / "config.json").read_bytes() == b"cfg"
    assert (root / "late.bin").read_bytes() == b"keep"


def test_unsupported_platform_refuses_before_mutation(tmp_path, monkeypatch):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"keep")
    monkeypatch.setattr(helper, "_DELETE_DIR_FD", False)
    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))
    assert (root / "config.json").read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_later_hardlink_does_not_delete_earlier_regular_file(tmp_path):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"config")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    os.link(outside, root / "z-weights.bin")
    with pytest.raises(helper.UnsafePath):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json", "z-weights.bin"))
    assert (root / "config.json").read_bytes() == b"config"
    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory descriptors")
def test_file_added_after_preflight_is_preserved_and_reported(tmp_path, monkeypatch):
    helper = _helper()
    root = _snapshot(tmp_path)
    (root / "config.json").write_bytes(b"config")
    real_unlink = os.unlink

    def inject(name, *, dir_fd):
        (root / "new-owner-file").write_bytes(b"keep")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", inject)
    with pytest.raises(OSError):
        helper.delete_snapshot(tmp_path / "models", FOLDER, _catalog("config.json"))
    assert (root / "new-owner-file").read_bytes() == b"keep"

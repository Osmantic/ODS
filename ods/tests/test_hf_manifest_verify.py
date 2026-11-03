"""verify_snapshot: read-only re-verification of a downloaded snapshot."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from test_hf_manifest_download import (
    FILES,
    MODEL_DIR,
    _helper,
    write_catalog,
)


@pytest.fixture
def model(tmp_path):
    catalog = write_catalog(tmp_path / "catalog.json", FILES)
    helper = _helper()
    return helper, helper.find_entry(catalog, "test-model-nvfp4-vllm")


def _populate(models_dir: Path) -> Path:
    target = models_dir / MODEL_DIR
    for path, content in FILES.items():
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    return target


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, int]]:
    out = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        out[str(path.relative_to(root))] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return out


def test_success_and_no_writes(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    _populate(models_dir)
    before = _snapshot_tree(models_dir)
    calls = []
    receipt = helper.verify_snapshot(
        entry, models_dir, on_progress=lambda done, total: calls.append((done, total))
    )
    assert receipt == {
        "model_id": entry["id"],
        "revision": entry["revision"],
        "files_verified": len(FILES),
        "bytes_verified": sum(len(c) for c in FILES.values()),
    }
    assert _snapshot_tree(models_dir) == before
    # Receipt alone means success; progress describes bytes read.
    assert calls[-1] == (receipt["bytes_verified"], receipt["bytes_verified"])


def test_missing_store_stays_absent(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    with pytest.raises(OSError):
        helper.verify_snapshot(entry, models_dir)
    assert not models_dir.exists()


def test_missing_snapshot_dir_stays_absent(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    with pytest.raises(OSError):
        helper.verify_snapshot(entry, models_dir)
    assert not (models_dir / MODEL_DIR).exists()


def test_missing_nested_dir_stays_absent(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    shutil.rmtree(target / "nested")
    with pytest.raises(OSError):
        helper.verify_snapshot(entry, models_dir)
    assert not (target / "nested").exists()


def test_missing_file_raises_before_writes(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    (target / "config.json").unlink()
    before = _snapshot_tree(models_dir)
    with pytest.raises(helper.Mismatch):
        helper.verify_snapshot(entry, models_dir)
    assert _snapshot_tree(models_dir) == before


def test_corrupt_file(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    bad = target / "config.json"
    bad.write_bytes(b"x" * bad.stat().st_size)
    with pytest.raises(helper.Mismatch):
        helper.verify_snapshot(entry, models_dir)


def test_partial_untouched(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    partial = target / "model-00001-of-00001.safetensors.partial"
    partial.write_bytes(b"partial")
    before = partial.read_bytes()
    helper.verify_snapshot(entry, models_dir)
    assert partial.read_bytes() == before


def test_cancellation(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    _populate(models_dir)
    state = {"n": 0}

    def cancelled():
        state["n"] += 1
        return state["n"] > 1

    with pytest.raises(helper.DownloadCancelled):
        helper.verify_snapshot(entry, models_dir, cancelled=cancelled)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlink_file_rejected(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    real = target / "config.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(real.read_bytes())
    real.unlink()
    real.symlink_to(outside)
    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir)


def test_hardlink_file_rejected(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    real = target / "config.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(real.read_bytes())
    real.unlink()
    os.link(outside, real)
    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir)


def test_mid_hash_mutation(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    victim = target / "model-00001-of-00001.safetensors"
    fired = {"done": False}

    def on_progress(done, total):
        if not fired["done"] and done > len(FILES["config.json"]):
            fired["done"] = True
            with open(victim, "r+b") as handle:
                handle.seek(0)
                handle.write(b"Z")
            # Some filesystems coalesce timestamp updates within a clock tick.
            # Make this same-size mutation's metadata change deterministic.
            info = victim.stat()
            os.utime(victim, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, on_progress=on_progress)
    assert fired["done"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX open-directory rename")
def test_snapshot_root_replacement(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    fired = False

    def on_progress(done, total):
        nonlocal fired
        if done == total and not fired:
            fired = True
            target.rename(models_dir / "previous")
            _populate(models_dir)

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, on_progress=on_progress)
    assert fired


def test_append_during_hash_is_refused(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    fired = False

    def on_progress(done, total):
        nonlocal fired
        if not fired:
            fired = True
            with (target / "config.json").open("ab") as handle:
                handle.write(b"unexpected")

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, on_progress=on_progress)


def test_earlier_file_replaced_during_later_hash(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    earlier = target / "config.json"
    fired = {"done": False}

    def on_progress(done, total):
        if not fired["done"] and done > len(FILES["config.json"]):
            fired["done"] = True
            replacement = earlier.with_name("config.json.new")
            replacement.write_bytes(earlier.read_bytes())
            os.replace(replacement, earlier)

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, on_progress=on_progress)
    assert fired["done"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX open-directory rename")
def test_nested_directory_substitution(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    nested = target / "nested"
    fired = {"done": False}

    def on_progress(done, total):
        if not fired["done"] and done > len(FILES["config.json"]) + len(FILES["model-00001-of-00001.safetensors"]):
            fired["done"] = True
            moved = target / "nested.old"
            os.rename(nested, moved)
            nested.mkdir()
            (nested / "tokenizer.json").write_bytes(FILES["nested/tokenizer.json"])

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, on_progress=on_progress)
    assert fired["done"]


def test_last_cancellation_callback_cannot_change_verified_file(model, tmp_path):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    earlier = target / "config.json"
    fired = {"done": False}
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        # One callback before each file and one before its single read, then
        # the final cancellation check. Mutate only on that last check.
        if calls == 2 * len(FILES) + 1:
            fired["done"] = True
            replacement = earlier.with_name("config.json.new")
            replacement.write_bytes(earlier.read_bytes())
            os.replace(replacement, earlier)
        return False

    with pytest.raises(helper.UnsafePath):
        helper.verify_snapshot(entry, models_dir, cancelled=cancelled)
    assert fired["done"]


def test_cli_verify_only_success(model, tmp_path, monkeypatch, capsys):
    helper, entry = model
    models_dir = tmp_path / "models"
    _populate(models_dir)
    catalog = write_catalog(tmp_path / "catalog.json", FILES)

    def boom(*args, **kwargs):
        raise AssertionError("fetch_manifest must not be called with --verify-only")

    monkeypatch.setattr(helper, "fetch_manifest", boom)
    code = helper.main([
        "--catalog", str(catalog), "--model-id", "test-model-nvfp4-vllm",
        "--models-dir", str(models_dir), "--verify-only",
    ])
    assert code == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    receipt = json.loads(out)
    assert receipt["files_verified"] == len(FILES)
    assert receipt["bytes_verified"] == sum(len(c) for c in FILES.values())


def test_cli_verify_only_corrupt(model, tmp_path, monkeypatch):
    helper, entry = model
    models_dir = tmp_path / "models"
    target = _populate(models_dir)
    bad = target / "config.json"
    bad.write_bytes(b"x" * bad.stat().st_size)
    catalog = write_catalog(tmp_path / "catalog.json", FILES)

    def boom(*args, **kwargs):
        raise AssertionError("fetch_manifest must not be called with --verify-only")

    monkeypatch.setattr(helper, "fetch_manifest", boom)
    code = helper.main([
        "--catalog", str(catalog), "--model-id", "test-model-nvfp4-vllm",
        "--models-dir", str(models_dir), "--verify-only",
    ])
    assert code == helper.EXIT_MISMATCH

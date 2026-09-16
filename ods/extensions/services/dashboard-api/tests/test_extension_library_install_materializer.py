from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parents[4] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_library_install_materializer as materializer  # noqa: E402
from extension_library_effect_input import VerifiedLibraryEffectInput  # noqa: E402
from extension_library_tree_digest import (  # noqa: E402
    LibraryTreeSnapshot,
    snapshot_extension_tree,
)
from extension_lifecycle_work import LifecycleWorkValidationError  # noqa: E402


pytestmark = pytest.mark.skipif(
    os.name != "posix"
    or not hasattr(os, "O_DIRECTORY")
    or not hasattr(os, "O_NOFOLLOW"),
    reason="requires Linux no-follow and renameat2 semantics",
)


@pytest.fixture(autouse=True)
def private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _effect(tmp_path: Path) -> VerifiedLibraryEffectInput:
    source = tmp_path / "approved-source"
    (source / "hooks").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "manifest.yaml").write_bytes(
        b"schema_version: ods.services.v2\nservice:\n  id: demo\n"
    )
    (source / "compose.yaml").write_bytes(
        b"services:\n  demo:\n    image: example/demo@sha256:" + b"1" * 64 + b"\n"
    )
    hook = source / "hooks" / "setup.sh"
    hook.write_bytes(b"#!/bin/sh\nexit 0\n")
    hook.chmod(0o700)
    (source / "config.json").write_bytes(b'{"enabled":true}\n')
    snapshot = snapshot_extension_tree(source)
    shutil.rmtree(source)
    return VerifiedLibraryEffectInput(
        transaction_id="txn-" + "1" * 24,
        plan_hash="2" * 64,
        service_id="demo",
        action="install",
        payload=snapshot,
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "user-extensions"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _target_files(root: Path) -> set[str]:
    target = root / "demo"
    return {
        item.relative_to(target).as_posix()
        for item in target.rglob("*")
        if item.is_file()
    }


def test_publishes_all_captured_bytes_without_reopening_source(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    result = materializer.LibraryInstallMaterializer(root).materialize(effect)

    assert result.outcome == "published"
    assert result.transaction_id == effect.transaction_id
    assert result.plan_hash == effect.plan_hash
    assert result.source_tree_sha256 == effect.payload.digest
    assert result.receipt_sha256.startswith("sha256:")
    assert snapshot_extension_tree(root / "demo") == effect.payload
    assert _target_files(root) == {
        materializer.RECEIPT_NAME,
        "compose.yaml",
        "config.json",
        "hooks/setup.sh",
        "manifest.yaml",
    }
    assert stat.S_IMODE((root / "demo" / "hooks" / "setup.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "demo" / "empty").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "demo" / "config.json").stat().st_mode) == 0o644
    receipt = json.loads((root / "demo" / materializer.RECEIPT_NAME).read_text())
    assert receipt["schema_version"] == 1
    assert receipt["source_digest"] == receipt["installed_digest"]
    assert receipt["assistant_first"]["schema"] == materializer.RECEIPT_SCHEMA
    assert receipt["assistant_first"]["source_tree_sha256"] == effect.payload.digest
    encoded = json.dumps(receipt)
    assert str(root) not in encoded
    assert "secret" not in encoded.casefold()


def test_exact_replay_is_read_only_and_returns_replayed(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    runtime = materializer.LibraryInstallMaterializer(root)
    runtime.materialize(effect)
    target = root / "demo"
    before = target.stat()
    receipt_before = (target / materializer.RECEIPT_NAME).read_bytes()

    result = runtime.materialize(effect)

    after = target.stat()
    assert result.outcome == "replayed"
    assert (before.st_dev, before.st_ino, before.st_mtime_ns) == (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
    )
    assert (target / materializer.RECEIPT_NAME).read_bytes() == receipt_before


def test_existing_conflicting_target_is_never_overwritten(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    target = root / "demo"
    target.mkdir(mode=0o700)
    marker = target / "owner.txt"
    marker.write_bytes(b"keep\n")
    with pytest.raises(
        materializer.LibraryInstallMaterializationError,
        match="library-materialization-target-conflict",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert marker.read_bytes() == b"keep\n"


def test_non_install_or_tampered_snapshot_fails_before_root_mutation(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    invalid = (
        replace(effect, action="update"),
        replace(
            effect,
            payload=effect.payload._replace(digest="sha256:" + "0" * 64),
        ),
    )
    for candidate in invalid:
        with pytest.raises(
            LifecycleWorkValidationError,
            match="library-materialization-input-invalid",
        ):
            materializer.LibraryInstallMaterializer(root).materialize(candidate)
        assert list(root.iterdir()) == []


def test_unsafe_root_is_refused_without_repair(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    root.chmod(0o770)
    with pytest.raises(
        materializer.LibraryInstallMaterializationError,
        match="library-materialization-custody-invalid",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert stat.S_IMODE(root.stat().st_mode) == 0o770


def test_stale_private_temp_is_safely_removed_before_publish(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    name = materializer._temp_name(effect)
    stale = root / name
    (stale / "nested").mkdir(parents=True, mode=0o755)
    stale.chmod(0o700)
    (stale / "nested" / "partial").write_bytes(b"partial")
    result = materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert result.outcome == "published"
    assert not stale.exists()
    assert snapshot_extension_tree(root / "demo") == effect.payload


def test_foreign_symlink_at_temp_name_is_refused_without_following(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"keep")
    link = root / materializer._temp_name(effect)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(
        materializer.LibraryInstallMaterializationError,
        match="library-materialization-temp-invalid",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert marker.read_bytes() == b"keep"
    assert link.is_symlink()


def test_concurrent_exact_calls_publish_once_and_replay_once(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)

    def run() -> str:
        return materializer.LibraryInstallMaterializer(root).materialize(effect).outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            item.result() for item in (pool.submit(run), pool.submit(run))
        )
    assert outcomes == ["published", "replayed"]
    assert snapshot_extension_tree(root / "demo") == effect.payload


def test_definite_publish_failure_cleans_temp_and_leaves_no_target(
    tmp_path, monkeypatch
):
    effect = _effect(tmp_path)
    root = _root(tmp_path)

    def fail_publish(*_args):
        ctypes.set_errno(errno.ENOSPC)
        return -1

    monkeypatch.setattr(materializer, "_validate_platform", lambda: fail_publish)
    with pytest.raises(
        materializer.LibraryInstallMaterializationError,
        match="library-materialization-publish-failed",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert not (root / "demo").exists()
    assert not (root / materializer._temp_name(effect)).exists()


def test_post_rename_error_is_uncertain_and_never_deletes_target(tmp_path, monkeypatch):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    real_publish = materializer._validate_platform()

    def publish_then_error(*args):
        assert real_publish(*args) == 0
        ctypes.set_errno(errno.EIO)
        return -1

    monkeypatch.setattr(materializer, "_validate_platform", lambda: publish_then_error)
    with pytest.raises(
        materializer.LibraryInstallMaterializationUncertain,
        match="library-materialization-publish-uncertain",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(effect)
    assert snapshot_extension_tree(root / "demo") == effect.payload


def test_receipt_drift_turns_replay_into_conflict(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    runtime = materializer.LibraryInstallMaterializer(root)
    runtime.materialize(effect)
    receipt = root / "demo" / materializer.RECEIPT_NAME
    receipt.write_bytes(b"{}\n")
    receipt.chmod(0o600)
    with pytest.raises(
        materializer.LibraryInstallMaterializationError,
        match="library-materialization-target-conflict",
    ):
        runtime.materialize(effect)


def test_snapshot_with_missing_parent_directory_is_rejected(tmp_path):
    effect = _effect(tmp_path)
    root = _root(tmp_path)
    forged = LibraryTreeSnapshot(
        digest=effect.payload.digest,
        directories=(),
        files=effect.payload.files,
        total_bytes=effect.payload.total_bytes,
    )
    with pytest.raises(
        LifecycleWorkValidationError,
        match="library-materialization-input-invalid",
    ):
        materializer.LibraryInstallMaterializer(root).materialize(
            replace(effect, payload=forged)
        )

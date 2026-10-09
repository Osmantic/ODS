"""Custody refusal diagnostics preserve strict storage checks and private values."""

import os
from pathlib import Path

import pytest

from pixel_image_store import ImageStore, ImageStoreCustodyError, custody_failure_reason


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX storage custody")


@pytest.mark.parametrize("case,reason", [
    ("symlink", "directory_path"),
    ("ancestor", "directory_path"),
    ("owner", "directory_owner"),
    ("mode", "directory_mode"),
])
def test_directory_refusal_has_fixed_reason_without_repair(tmp_path, monkeypatch, case, reason):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    target = directory
    if case == "symlink":
        target = tmp_path / "alias"
        target.symlink_to(directory, target_is_directory=True)
    elif case == "ancestor":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        target = alias / directory.name
    elif case == "owner":
        # Exercise the real comparison without requiring privileged chown.
        monkeypatch.setattr(os, "geteuid", lambda: directory.stat().st_uid + 1)
    else:
        directory.chmod(0o750)
    before = directory.stat()
    with pytest.raises(ImageStoreCustodyError) as failure:
        ImageStore(target)
    assert custody_failure_reason(failure.value) == reason
    after = directory.stat()
    assert (after.st_uid, after.st_mode) == (before.st_uid, before.st_mode)
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("suffix,role", [
    ("", "database"), ("-journal", "journal"), ("-wal", "wal"), ("-shm", "shm"),
])
@pytest.mark.parametrize("case,check", [
    ("symlink", "type"), ("directory", "type"),
    ("hardlink", "links"), ("owner", "owner"), ("mode", "mode"),
])
def test_each_store_file_refuses_unsafe_custody_without_repair(tmp_path, monkeypatch, suffix, role, case, check):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    candidate = directory / f"images.sqlite3{suffix}"
    outside = tmp_path / "outside"
    outside.write_bytes(b"private image bytes stay untouched")
    outside.chmod(0o600)
    if case == "symlink":
        candidate.symlink_to(outside)
    elif case == "directory":
        candidate.mkdir(mode=0o700)
    elif case == "hardlink":
        candidate.hardlink_to(outside)
    else:
        candidate.write_bytes(b"not opened as SQLite")
        candidate.chmod(0o640 if case == "mode" else 0o600)
    real_lstat = Path.lstat
    before = real_lstat(candidate)
    contents = None if candidate.is_dir() else candidate.read_bytes()
    if case == "owner":
        def foreign_owner(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == candidate:
                fields = list(info)
                fields[4] = info.st_uid + 1
                return os.stat_result(fields)
            return info
        monkeypatch.setattr(Path, "lstat", foreign_owner)
    with pytest.raises(ImageStoreCustodyError, match="private regular files") as failure:
        ImageStore(directory)
    assert custody_failure_reason(failure.value) == f"{role}_{check}"
    after = real_lstat(candidate)
    assert (after.st_uid, after.st_mode, after.st_nlink) == (before.st_uid, before.st_mode, before.st_nlink)
    if contents is not None:
        assert candidate.read_bytes() == contents
    assert outside.read_bytes() == b"private image bytes stay untouched"
    assert set(directory.iterdir()) == {candidate}


def test_unknown_custody_reason_is_not_a_log_value(monkeypatch):
    failure = ImageStoreCustodyError("/private/path owner=123 credential=secret", "private image content")
    assert custody_failure_reason(failure) == "unknown"
    monkeypatch.setattr(failure, "reason", ["unhashable private value"])
    assert custody_failure_reason(failure) == "unknown"
    assert custody_failure_reason(ValueError("database_mode")) is None

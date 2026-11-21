"""Custody refusal diagnostics preserve strict storage checks and private values."""

import os
import stat
from pathlib import Path

import pytest

from pixel_image_store import ImageStore, ImageStoreCustodyError, custody_failure_reason, custody_failure_metadata


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
    def forbidden_connect(*args, **kwargs):
        pytest.fail("Unsafe custody must be rejected before SQLite opens")
    monkeypatch.setattr("pixel_image_store.sqlite3.connect", forbidden_connect)
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
    diagnostic = custody_failure_metadata(failure.value)
    assert "stage=existing_file" in diagnostic
    assert f"expected_uid={os.geteuid()}" in diagnostic
    assert f"actual_uid={before.st_uid + (case == 'owner')}" in diagnostic
    assert f"device={before.st_dev}" in diagnostic
    assert f"inode={before.st_ino}" in diagnostic
    assert f"mode={stat.S_IMODE(before.st_mode)}" in diagnostic
    assert f"nlink={before.st_nlink}" in diagnostic
    assert str(candidate) not in diagnostic
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


def test_rejection_correlates_creation_identity_without_private_path(tmp_path, monkeypatch):
    directory = tmp_path / "private-owner-credential-image-bytes"
    first = ImageStore(directory)
    first.close()
    second = ImageStore(directory)
    second.close()
    info = (directory / "images.sqlite3").stat()
    (directory / "images.sqlite3").chmod(0o640)
    with pytest.raises(ImageStoreCustodyError) as failure:
        ImageStore(directory)
    diagnostic = custody_failure_metadata(failure.value)
    assert ("historical_creation=(stage=created_file; "
            f"expected_uid={os.geteuid()}; actual_uid={info.st_uid}; device={info.st_dev}; inode={info.st_ino}; mode=384; nlink=1)") in diagnostic
    assert "mode=416" in diagnostic
    assert str(directory) not in diagnostic
    # Diagnostic retention is bounded and never changes custody decisions.
    monkeypatch.setattr("pixel_image_store._created_stores", [])
    with pytest.raises(ImageStoreCustodyError) as failure:
        ImageStore(directory)
    assert "creation=" not in custody_failure_metadata(failure.value)


@pytest.mark.parametrize("value", [True, -1, 1 << 64, "secret", [123], None])
def test_custody_metadata_ignores_untrusted_or_non_numeric_fields(value):
    failure = ImageStoreCustodyError("database_owner", "private", metadata={
        "stage": "/private/path", "expected_uid": value, "actual_uid": value,
        "device": value, "inode": value, "mode": value, "nlink": value, "path": "/credential/image-bytes",
    })
    assert custody_failure_metadata(failure) == ""


def test_historical_inode_match_does_not_authorize_a_later_file(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    ImageStore(directory).close()
    database = directory / "images.sqlite3"
    info = database.stat()
    # Simulate inode reuse: history describes a different file generation.
    monkeypatch.setattr("pixel_image_store._created_stores", [{
        "stage": "created_file", "device": info.st_dev, "inode": info.st_ino,
        "actual_uid": 12345, "expected_uid": 12345, "mode": 384, "nlink": 1,
    }])
    database.chmod(0o640)
    with pytest.raises(ImageStoreCustodyError) as failure:
        ImageStore(directory)
    assert failure.value.reason == "database_mode"
    diagnostic = custody_failure_metadata(failure.value)
    assert "historical_creation=" in diagnostic
    assert f"actual_uid={info.st_uid}" in diagnostic
    assert "actual_uid=12345" in diagnostic
    assert database.stat().st_mode & 0o777 == 0o640

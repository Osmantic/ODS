"""Real Linux streaming snapshots; Dashboard executor stays disabled."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_stream_snapshot as snapshots  # noqa: E402
from extension_data_scope_contract import bind_data_scope  # noqa: E402
from extension_lifecycle_work import REQUEST_SCHEMA, LifecycleWorkExecutionError  # noqa: E402
from test_extension_data_scope_contract import command  # noqa: E402


linux_effect = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux snapshot")


def _command():
    return command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]),
        prior_paths=[],
    )


def _protocol_hash(value, operation_key: str) -> str:
    unsigned = {
        "schema": REQUEST_SCHEMA, "transactionId": value.transaction_id,
        "planHash": value.plan_hash, "operationKey": operation_key,
        "serviceIds": list(value.service_ids),
        "payload": {"serviceIds": list(value.service_ids)},
    }
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _roots(tmp_path: Path):
    install = tmp_path / "install"
    data = tmp_path / "data"
    backup = data / "assistant-first" / "stream-backups"
    alpha = install / "data" / "alpha"
    alpha.mkdir(parents=True)
    backup.mkdir(parents=True)
    for directory in (install, install / "data", alpha, data, data / "assistant-first", backup):
        directory.chmod(0o700)
    return install, data, backup, alpha


def _write(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def test_stream_scope_is_only_the_attested_old_new_union():
    bound = bind_data_scope(_command())
    index = snapshots._scope_index(bound)
    assert [(service["serviceId"], [path["path"] for path in service["paths"]])
            for service in index] == [("alpha", ["data/alpha"]), ("beta", ["data/beta"])]


def test_aggregate_path_cap_is_checked_before_any_capture(monkeypatch):
    monkeypatch.setattr(snapshots, "_MAX_PATHS", 1)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        snapshots._scope_index(bind_data_scope(_command()))
    assert caught.value.code == "lifecycle-work-data-snapshot-path-limit"


@linux_effect
def test_large_file_streams_without_base64_and_absent_path_is_explicit(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    large = alpha / "large.bin"
    with large.open("wb") as stream:
        for _ in range(9):
            stream.write(b"a" * 1024 * 1024)
    large.chmod(0o600)
    _write(alpha / "small.txt", b"private value\n")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    receipt = store.backup(value)
    assert receipt.file_count == 2
    assert receipt.content_bytes == 9 * 1024 * 1024 + len(b"private value\n")
    assert store.verify(value) == receipt
    archive = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    assert archive.stat().st_mode & 0o777 == 0o400
    assert archive.stat().st_size < 10 * 1024 * 1024
    assert b"private value" not in snapshots._canonical({"receipt": receipt.archive_sha256})
    assert len(list(backup.iterdir())) == 1


@linux_effect
def test_snapshot_index_preserves_exact_source_times_without_touching_atime(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    nested = alpha / "nested"
    nested.mkdir(mode=0o700)
    source = nested / "note"
    _write(source, b"timestamped")
    file_times = (946684800123456789, 946684801987654321)
    nested_times = (946684802123456789, 946684803987654321)
    root_times = (946684804123456789, 946684805987654321)
    os.utime(source, ns=file_times)
    os.utime(nested, ns=nested_times)
    os.utime(alpha, ns=root_times)
    expected = {item: (item.stat().st_atime_ns, item.stat().st_mtime_ns)
                for item in (source, nested, alpha)}
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    receipt = store.backup(value)
    assert store.verify(value) == receipt
    for item, pair in expected.items():
        assert (item.stat().st_atime_ns, item.stat().st_mtime_ns) == pair
    archive_path = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    with tarfile.open(archive_path, "r:") as archive:
        document = json.load(archive.extractfile(snapshots.INDEX_MEMBER))
        assert all(member.mtime == 0 for member in archive.getmembers())
    paths = {service["serviceId"]: service["paths"] for service in document["services"]}
    captured = paths["alpha"][0]
    assert captured["rootAtimeNs"] == str(expected[alpha][0])
    assert captured["rootMtimeNs"] == str(expected[alpha][1])
    entries = {entry["path"]: entry for entry in captured["entries"]}
    for relative, item in (("nested", nested), ("nested/note", source)):
        assert entries[relative]["atimeNs"] == str(expected[item][0])
        assert entries[relative]["mtimeNs"] == str(expected[item][1])
    assert paths["beta"][0]["present"] is False
    assert paths["beta"][0]["rootAtimeNs"] is None
    assert paths["beta"][0]["rootMtimeNs"] is None


@linux_effect
def test_file_and_directory_xattrs_refuse_publication(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    source = alpha / "note"
    _write(source, b"private")
    try:
        os.setxattr(source, "user.ods-test", b"v")
    except OSError as exc:
        pytest.skip(f"test filesystem does not support user xattrs: {exc.errno}")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.backup(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-metadata-unsupported"
    archive = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    assert not archive.exists()
    os.removexattr(source, "user.ods-test")
    os.setxattr(alpha, "user.ods-test", b"v")
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.backup(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-metadata-unsupported"
    assert not archive.exists()


@linux_effect
def test_sparse_file_refuses_publication(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    source = alpha / "hole"
    with source.open("wb") as stream:
        stream.write(b"start")
        stream.seek(1024 * 1024)
        stream.write(b"end")
    source.chmod(0o600)
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.backup(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-sparse-unsupported"
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()


@pytest.mark.parametrize("value", [0, "-0", "01", "1.5", "9223372036854775808"])
def test_index_rejects_noncanonical_or_out_of_range_time(value):
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        snapshots._verified_time_ns(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-index-invalid"


@linux_effect
def test_archive_verify_rejects_tampered_timestamp_index(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"content")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    store.backup(value)
    archive_path = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    with tarfile.open(archive_path, "r:") as archive:
        index = archive.getmember(snapshots.INDEX_MEMBER)
        raw = archive.extractfile(index).read()
        position = raw.index(b'"rootAtimeNs":"') + len(b'"rootAtimeNs":"')
    archive_path.chmod(0o600)
    with archive_path.open("r+b") as stream:
        stream.seek(index.offset_data + position)
        stream.write(b"x")
    archive_path.chmod(0o400)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.verify(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-index-invalid"


@linux_effect
def test_xattr_visibility_failure_refuses_publication(tmp_path: Path, monkeypatch):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"content")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)

    def unavailable(_descriptor):
        raise OSError(errno.EPERM, "simulated inaccessible metadata")

    monkeypatch.setattr(snapshots.os, "listxattr", unavailable)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.backup(value)
    assert caught.value.code == "lifecycle-work-data-snapshot-metadata-unavailable"
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()


@linux_effect
def test_published_first_snapshot_wins_after_live_data_changes(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    source = alpha / "note.txt"
    _write(source, b"first\n")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    initial = store.backup(value)
    _write(source, b"second\n")
    assert store.backup(value) == initial


@linux_effect
def test_reconciling_command_rebinds_original_backup_archive_without_rebackup(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"first")
    value = _command()
    value = replace(value, request_hash=_protocol_hash(value, "backup"))
    store = snapshots.StreamSnapshotStore(install, data, backup)
    original = store.backup(value)
    reconciling = replace(value.plan_material, state="reconciling")
    restore = replace(
        value, operation_key="restore", plan_material=reconciling,
        request_hash=_protocol_hash(value, "restore"),
    )
    _write(alpha / "note", b"changed after apply")
    assert store.verify(restore) == original
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(restore)


@linux_effect
def test_verified_restore_archive_lease_keeps_original_inode_on_path_swap(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"sealed original")
    value = _command()
    value = replace(value, request_hash=_protocol_hash(value, "backup"))
    store = snapshots.StreamSnapshotStore(install, data, backup)
    store.backup(value)
    restore = replace(
        value, operation_key="restore",
        plan_material=replace(value.plan_material, state="reconciling"),
        request_hash=_protocol_hash(value, "restore"),
    )
    archive_path = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    held = backup / "held-original.tar"
    with store.open_verified(restore) as (archive, document, receipt):
        assert receipt.file_count == 1
        assert document["schema"] == snapshots.SNAPSHOT_SCHEMA
        member = next(item for item in archive.getmembers() if item.name.endswith("/note"))
        archive_path.rename(held)
        archive_path.write_bytes(b"invalid replacement")
        archive_path.chmod(0o400)
        assert archive.extractfile(member).read() == b"sealed original"
    with pytest.raises(LifecycleWorkExecutionError):
        store.verify(restore)


@linux_effect
def test_source_symlink_fails_without_published_archive(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"not in scope")
    (alpha / "link").symlink_to(outside)
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()


@linux_effect
def test_file_budget_refuses_snapshot_before_publication(tmp_path: Path, monkeypatch):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "too-large", b"abcdef")
    monkeypatch.setattr(snapshots, "_MAX_FILE_BYTES", 4)
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()


@linux_effect
def test_hardlink_fifo_and_group_writable_tree_fail_closed(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    outside = tmp_path / "shared"
    _write(outside, b"shared")
    os.link(outside, alpha / "hardlink")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    (alpha / "hardlink").unlink()
    fifo = alpha / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    fifo.unlink()
    _write(alpha / "safe", b"safe")
    alpha.chmod(0o770)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()


@linux_effect
def test_archive_content_tampering_is_detected(tmp_path: Path):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"original-content")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    store.backup(value)
    archive_path = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    with tarfile.open(archive_path, "r:") as archive:
        payload = next(member for member in archive.getmembers() if member.name.endswith("/note"))
        offset = payload.offset_data
    archive_path.chmod(0o600)
    with archive_path.open("r+b") as stream:
        stream.seek(offset)
        stream.write(b"X")
    archive_path.chmod(0o400)
    with pytest.raises(LifecycleWorkExecutionError):
        store.verify(value)


@linux_effect
def test_crash_after_hardlink_recovers_only_matching_temp_link(tmp_path: Path, monkeypatch):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "note", b"sealed")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    original = snapshots.os.unlink
    interrupted = False

    def fail_first_temp(name, *, dir_fd=None):
        nonlocal interrupted
        if not interrupted and isinstance(name, str) and name.startswith(".tmp-"):
            interrupted = True
            raise OSError("simulated crash after archive link")
        return original(name, dir_fd=dir_fd)

    monkeypatch.setattr(snapshots.os, "unlink", fail_first_temp)
    with pytest.raises(LifecycleWorkExecutionError):
        store.backup(value)
    monkeypatch.setattr(snapshots.os, "unlink", original)
    archive = backup / f"{value.transaction_id}.{value.plan_hash}.tar"
    assert archive.exists() and archive.stat().st_nlink == 2
    receipt = store.verify(value)
    assert receipt.file_count == 1
    assert archive.stat().st_nlink == 1
    assert len(list(backup.iterdir())) == 1


@linux_effect
def test_directory_mutation_during_listing_fails_before_capture(tmp_path: Path, monkeypatch):
    install, data, backup, alpha = _roots(tmp_path)
    _write(alpha / "before", b"before")
    value = _command()
    store = snapshots.StreamSnapshotStore(install, data, backup)
    original = snapshots.os.listdir
    alpha_inode = alpha.stat().st_ino
    mutated = False

    def change_after_listing(descriptor):
        nonlocal mutated
        names = original(descriptor)
        if not mutated and isinstance(descriptor, int) and os.fstat(descriptor).st_ino == alpha_inode:
            mutated = True
            _write(alpha / "after", b"after")
        return names

    monkeypatch.setattr(snapshots.os, "listdir", change_after_listing)
    with pytest.raises(LifecycleWorkExecutionError) as caught:
        store.backup(value)
    assert mutated and caught.value.code == "lifecycle-work-data-snapshot-source-changed"
    assert not (backup / f"{value.transaction_id}.{value.plan_hash}.tar").exists()

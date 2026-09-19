"""Contract tests for lib/backup-archive.py.

The backup restore path extracts an operator-supplied tar.gz into private
staging and publishes exactly one directory. The archive is untrusted input:
member traversal, duplicate paths, special files, link escapes (including
chains through '../'), and a forged gzip trailer must all be rejected without
writing outside the staging area.
"""
import gzip
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path, PurePosixPath

import pytest

MODULE = Path(__file__).resolve().parents[1] / "lib" / "backup-archive.py"
SPEC = importlib.util.spec_from_file_location("backup_archive", MODULE)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)

BID = "backup-2026-09-17"


def member(name, data=None, kind="file", mode=0o644, linkname=""):
    info = tarfile.TarInfo(name)
    if kind == "dir":
        info.type = tarfile.DIRTYPE
        info.mode = mode
        return info, None
    if kind == "sym":
        info.type = tarfile.SYMTYPE
        info.linkname = linkname
        return info, None
    if kind == "lnk":
        info.type = tarfile.LNKTYPE
        info.linkname = linkname
        return info, None
    info.size = len(data or b"")
    info.mode = mode
    return info, io.BytesIO(data or b"")


def make_archive(tmp_path, items, name="backup.tar.gz"):
    path = tmp_path / name
    with gzip.open(path, "wb") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for info, data in items:
                archive.addfile(info, data)
    return path


def manifest_member(data=None):
    return member(f"{BID}/manifest.json",
                  data or json.dumps({"schemaVersion": 1}).encode())


class TestMemberPath:
    @pytest.mark.parametrize("name", [
        f"{BID}/file.txt", f"{BID}/a/b/c.txt", f"{BID}",
    ])
    def test_valid(self, name):
        assert backup.member_path(name, BID) == PurePosixPath(name)

    @pytest.mark.parametrize("name", [
        "", "/abs", f"{BID}/../escape", f"../{BID}", f"other/x",
        f"{BID}\\win", f"{BID}/a\\b",
    ])
    def test_rejected(self, name):
        with pytest.raises(ValueError, match="outside"):
            backup.member_path(name, BID)


class TestExtract:
    def _dest_root(self, tmp_path):
        root = tmp_path / "backups"
        root.mkdir()
        return root

    def test_valid_archive_published(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"),
            manifest_member(),
            member(f"{BID}/data/settings.json", b"{}"),
            member(f"{BID}/data", kind="dir"),
        ])
        backup.extract(archive, root, BID)
        assert (root / BID / "manifest.json").is_file()
        assert (root / BID / "data/settings.json").read_bytes() == b"{}"

    @pytest.mark.parametrize("bid", ["", ".", "..", "a/b", "a\\b"])
    def test_invalid_backup_id(self, tmp_path, bid):
        with pytest.raises(ValueError, match="invalid backup ID"):
            backup.extract(tmp_path / "a.tgz", self._dest_root(tmp_path), bid)

    def test_existing_destination_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        (root / BID).mkdir()
        with pytest.raises(ValueError, match="already exists"):
            backup.extract(tmp_path / "a.tgz", root, BID)

    def test_missing_manifest_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"),
            member(f"{BID}/data.txt", b"x"),
        ])
        with pytest.raises(ValueError, match="manifest"):
            backup.extract(archive, root, BID)
        assert not (root / BID).exists()  # nothing published on failure

    def test_manifest_must_be_file(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"),
            member(f"{BID}/manifest.json", kind="dir"),
        ])
        with pytest.raises(ValueError, match="manifest"):
            backup.extract(archive, root, BID)

    @pytest.mark.parametrize("bad_name", [
        f"other/manifest.json", f"{BID}/../escape.txt", f"{BID}/a\\b",
    ])
    def test_member_outside_backup_rejected(self, tmp_path, bad_name):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(bad_name, b"x"),
        ])
        with pytest.raises(ValueError, match="outside"):
            backup.extract(archive, root, BID)
        assert not (root / BID).exists()

    def test_duplicate_member_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/dup.txt", b"1"), member(f"{BID}/dup.txt", b"2"),
        ])
        with pytest.raises(ValueError, match="duplicate"):
            backup.extract(archive, root, BID)

    def test_special_file_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        fifo = tarfile.TarInfo(f"{BID}/pipe")
        fifo.type = tarfile.FIFOTYPE
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(), (fifo, None),
        ])
        with pytest.raises(ValueError, match="special file"):
            backup.extract(archive, root, BID)

    def test_file_parent_must_be_directory(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/parent", b"x"),
            member(f"{BID}/parent/child.txt", b"y"),  # parent is a file
        ])
        with pytest.raises(ValueError, match="non-directory parent"):
            backup.extract(archive, root, BID)

    @pytest.mark.parametrize("linkname", [
        "/etc/passwd", "..\\evil", "", "a\\b",
    ])
    def test_unsafe_symlink_rejected(self, tmp_path, linkname):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/link", kind="sym", linkname=linkname),
        ])
        with pytest.raises(ValueError, match="unsafe symbolic|outside"):
            backup.extract(archive, root, BID)

    def test_symlink_chain_escape_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/up", kind="sym", linkname=".."),
            member(f"{BID}/escape", kind="sym",
                   linkname="up/outside/secret.txt"),
        ])
        with pytest.raises(ValueError):
            backup.extract(archive, root, BID)
        assert not (root / BID).exists()

    def test_in_backup_symlink_allowed(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/link", kind="sym", linkname="manifest.json"),
        ])
        backup.extract(archive, root, BID)
        assert (root / BID / "link").resolve() == (
            root / BID / "manifest.json")

    def test_hardlink_must_reference_file_member(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/hard", kind="lnk", linkname="/etc/passwd"),
        ])
        with pytest.raises(ValueError):
            backup.extract(archive, root, BID)

    def test_hardlink_to_member_ok(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/hard", kind="lnk",
                   linkname=f"{BID}/manifest.json"),
        ])
        backup.extract(archive, root, BID)
        assert (root / BID / "hard").read_bytes() == (
            root / BID / "manifest.json").read_bytes()

    def test_truncated_gzip_rejected(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
        ])
        data = archive.read_bytes()
        truncated = tmp_path / "truncated.tgz"
        truncated.write_bytes(data[:-16])  # cut the trailer
        with pytest.raises(Exception):
            backup.extract(truncated, root, BID)
        assert not (root / BID).exists()

    def test_file_modes_preserved(self, tmp_path):
        root = self._dest_root(tmp_path)
        archive = make_archive(tmp_path, [
            member(BID, kind="dir"), manifest_member(),
            member(f"{BID}/script.sh", b"#!/bin/sh\n", mode=0o755),
        ])
        backup.extract(archive, root, BID)
        assert (root / BID / "script.sh").stat().st_mode & 0o777 == 0o755

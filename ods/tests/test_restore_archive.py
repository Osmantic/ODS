"""Exercise compressed restore through the real CLI, including hostile members."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

ODS = Path(__file__).resolve().parents[1]


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "installation"
    (root / "data").mkdir(parents=True)
    (root / ".backups").mkdir()
    (root / "lib").mkdir()
    for name in ("rsync.sh", "backup-paths.sh"):
        shutil.copy2(ODS / "lib" / name, root / "lib" / name)
    (root / ".env").write_text("original-live-config\n")
    (tmp_path / "outside").write_text("outside sentinel")
    return root


def entry(name, body=b"payload", kind=tarfile.REGTYPE, link=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = 0o640 if kind == tarfile.REGTYPE else 0o755
    info.linkname = link
    info.size = len(body) if kind == tarfile.REGTYPE else 0
    return info, body


def archive(root, extra=()):
    members = [entry("snapshot", kind=tarfile.DIRTYPE), entry("snapshot/manifest.json", json.dumps({
        "manifest_version": "1.0", "backup_type": "config", "backup_date": "2026-01-01",
        "description": "isolated restore test"}).encode()), entry("snapshot/.env", b"restored config\n")]
    path = root / ".backups/snapshot.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        for info, body in [*members, *extra]:
            output.addfile(info, io.BytesIO(body) if info.isfile() else None)
    return path


def restore(root, *options, backup_id="snapshot", env=None):
    return subprocess.run(["bash", str(ODS / "ods-restore.sh"), "--force", *options, backup_id],
                          env={**os.environ, "ODS_DIR": str(root), **(env or {})},
                          capture_output=True, text=True, timeout=20)


def unchanged(root):
    assert (root / ".env").read_text() == "original-live-config\n"
    assert (root.parent / "outside").read_text() == "outside sentinel"
    assert not (root / ".backups/snapshot").exists()
    assert not list((root / ".backups").glob(".ods-extract-*"))


def test_valid_backup_preserves_files_and_internal_links(installation):
    root = installation
    archive(root, [entry("snapshot/data/persona/valid..name", b"saved persona"),
                   entry("snapshot/data/persona/relative", kind=tarfile.SYMTYPE, link="valid..name"),
                   entry("snapshot/data/persona/hard", kind=tarfile.LNKTYPE,
                         link="snapshot/data/persona/valid..name")])
    result = restore(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".env").read_text() == "restored config\n"
    persona = root / "data/persona"
    assert (persona / "relative").is_symlink()
    assert (persona / "relative").read_text() == "saved persona"
    assert (persona / "hard").read_text() == "saved persona"
    assert (persona / "valid..name").stat().st_mode & 0o777 == 0o640
    extracted = root / ".backups/snapshot/data/persona"
    assert (extracted / "hard").stat().st_ino == (extracted / "valid..name").stat().st_ino


@pytest.mark.parametrize("name", ["../outside", "..", "snapshot/data/..",
                                  "snapshot/../outside", "/absolute-file", "other-backup/file"])
def test_rejects_unsafe_or_wrong_root_members(installation, name):
    archive(installation, [entry(name)])
    result = restore(installation, "--dry-run")
    assert result.returncode != 0
    unchanged(installation)


@pytest.mark.parametrize("entries", [
    [entry("snapshot/escape", kind=tarfile.SYMTYPE, link="../../outside")],
    [entry("snapshot/escape", kind=tarfile.SYMTYPE, link="/tmp/outside")],
    [entry("snapshot/escape", kind=tarfile.LNKTYPE, link="../outside")],
    [entry("snapshot/link", kind=tarfile.SYMTYPE, link="."), entry("snapshot/link/child")],
    [entry("snapshot/cycle", kind=tarfile.SYMTYPE, link="cycle")],
    [entry("snapshot/device", kind=tarfile.FIFOTYPE)],
    [entry("snapshot/.env", b"duplicate overwrite")],
])
def test_rejects_escaping_links_pivots_special_files_and_duplicates(installation, entries):
    archive(installation, entries)
    result = restore(installation, "--dry-run")
    assert result.returncode != 0
    unchanged(installation)


def test_large_listing_does_not_turn_early_rejection_into_sigpipe_success(installation):
    archive(installation, [entry("../outside"), *[entry(f"snapshot/items/{i}") for i in range(4000)]])
    assert restore(installation, "--dry-run").returncode != 0
    unchanged(installation)


@pytest.mark.parametrize("damage", ["header", "truncated", "checksum"])
def test_corrupt_archives_never_publish_partial_backup(installation, damage):
    path = archive(installation, [entry("snapshot/payload", os.urandom(100_000))])
    content = path.read_bytes()
    if damage == "header": content = b"not an archive"
    elif damage == "truncated": content = content[:len(content)//2]
    else: content = content[:-8] + bytes([content[-8] ^ 255]) + content[-7:]
    path.write_bytes(content)
    assert restore(installation, "--dry-run").returncode != 0
    unchanged(installation)


def test_publication_failure_removes_staging_only(installation, tmp_path):
    archive(installation)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text('import os\ndef fail(*a, **kw): raise OSError("publish injected")\nos.rename=fail\n')
    assert restore(installation, "--dry-run", env={"PYTHONPATH": str(hooks)}).returncode != 0
    unchanged(installation)


def test_backup_id_cannot_select_parent_paths(installation):
    assert restore(installation, "--dry-run", backup_id="../outside").returncode != 0
    unchanged(installation)


def test_existing_symlink_is_never_used_or_deleted(installation, tmp_path):
    outside = tmp_path / "other-backup"
    outside.mkdir()
    (outside / "marker").write_text("untouched")
    link = installation / ".backups/snapshot"
    link.symlink_to(outside, target_is_directory=True)
    archive(installation)
    assert restore(installation, "--dry-run").returncode != 0
    assert link.is_symlink()
    assert (outside / "marker").read_text() == "untouched"

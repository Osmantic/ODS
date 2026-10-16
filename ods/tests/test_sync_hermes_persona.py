import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="The copy component runs inside the Linux Hermes container")

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync-hermes-persona.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_hermes_persona", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_existing_generated_refresh_same_inode(tmp_path):
    p = tmp_path / "SOUL.md"
    old = b"old persona\n"
    new = "new persona\n"
    p.write_bytes(old)
    ino_before = p.stat().st_ino
    status = mod.sync_persona(str(p), sha(old), new)
    assert status == "updated"
    assert p.read_bytes() == new.encode("utf-8")
    assert p.stat().st_ino == ino_before


def test_custom_preserved_bytes_and_mtime(tmp_path):
    p = tmp_path / "SOUL.md"
    custom = b"user customized\n"
    p.write_bytes(custom)
    os.utime(p, (1_000_000, 1_000_000))
    mtime_before = p.stat().st_mtime_ns
    status = mod.sync_persona(str(p), sha(b"old generated\n"), "new persona\n")
    assert status == "preserved"
    assert p.read_bytes() == custom
    assert p.stat().st_mtime_ns == mtime_before


def test_new_content_current_noop_mtime(tmp_path):
    p = tmp_path / "SOUL.md"
    new = "already current\n"
    p.write_bytes(new.encode("utf-8"))
    os.utime(p, (1_000_000, 1_000_000))
    mtime_before = p.stat().st_mtime_ns
    status = mod.sync_persona(str(p), sha(b"old generated\n"), new)
    assert status == "current"
    assert p.read_bytes() == new.encode("utf-8")
    assert p.stat().st_mtime_ns == mtime_before


def test_missing_creation(tmp_path):
    p = tmp_path / "SOUL.md"
    new = "fresh persona\n"
    status = mod.sync_persona(str(p), sha(b"old generated\n"), new)
    assert status == "updated"
    assert p.read_bytes() == new.encode("utf-8")
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_missing_creation_handles_short_writes(tmp_path, monkeypatch):
    original_write = os.write
    monkeypatch.setattr(mod.os, "write", lambda fd, data: original_write(fd, data[:3]))
    path = tmp_path / "SOUL.md"
    content = "Complete persona despite short writes\n"
    assert mod.sync_persona(str(path), sha(b"old"), content) == "updated"
    assert path.read_text() == content


def test_owner_edit_during_readback_is_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "SOUL.md"
    path.write_text("old")
    original_read = mod._read_all

    def concurrent_edit(fd):
        data = original_read(fd)
        path.write_text("Owner changed the persona during refresh")
        return data

    monkeypatch.setattr(mod, "_read_all", concurrent_edit)
    with pytest.raises(mod.RaceError):
        mod.sync_persona(str(path), sha(b"old"), "generated replacement")
    assert path.read_text() == "Owner changed the persona during refresh"


def test_symlink_target_untouched(tmp_path):
    target = tmp_path / "target.md"
    target.write_bytes(b"target content\n")
    link = tmp_path / "SOUL.md"
    link.symlink_to(target)
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(link), sha(b"old generated\n"), "new persona\n")
    assert target.read_bytes() == b"target content\n"
    assert link.is_symlink()


def test_nonempty_dir_retained(tmp_path):
    d = tmp_path / "SOUL.md"
    d.mkdir()
    (d / "keep.txt").write_bytes(b"keep\n")
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(d), sha(b"old generated\n"), "new persona\n")
    assert d.is_dir()
    assert (d / "keep.txt").read_bytes() == b"keep\n"


def test_hardlink_refused(tmp_path):
    p = tmp_path / "SOUL.md"
    p.write_bytes(b"old generated\n")
    other = tmp_path / "other.md"
    os.link(p, other)
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), sha(b"old generated\n"), "new persona\n")
    assert p.read_bytes() == b"old generated\n"
    assert other.read_bytes() == b"old generated\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo not available")
def test_fifo_no_hang(tmp_path):
    p = tmp_path / "SOUL.md"
    os.mkfifo(p)
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), sha(b"old generated\n"), "new persona\n")
    assert stat.S_ISFIFO(p.stat().st_mode)


def test_invalid_sha(tmp_path):
    p = tmp_path / "SOUL.md"
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), "nothex", "new persona\n")
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), "a" * 63, "new persona\n")
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), "g" * 64, "new persona\n")
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), None, "new persona\n")
    assert not p.exists()


def test_non_str_content(tmp_path):
    p = tmp_path / "SOUL.md"
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), sha(b"old generated\n"), b"bytes not allowed")
    with pytest.raises(mod.SyncError):
        mod.sync_persona(str(p), sha(b"old generated\n"), None)
    assert not p.exists()

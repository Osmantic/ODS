"""Safety rejections identify their guard without disclosing stored namespaces."""
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixel_image_store import ImageStore


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX safety metadata")
PRIVATE = "DO_NOT_LOG_PRIVATE_VALUE"


def rejection(caplog, action, message, expected):
    with caplog.at_level(logging.WARNING, logger="pixel_image_store"):
        with pytest.raises(ValueError) as error:
            action()
    assert type(error.value) is ValueError
    assert str(error.value) == message
    records = [r.getMessage() for r in caplog.records if r.name == "pixel_image_store"]
    assert records == [expected]
    assert PRIVATE not in records[0]


def wrong_owner(monkeypatch, target):
    original = Path.lstat

    def selected_lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                                   st_uid=info.st_uid + 1)
        return info

    monkeypatch.setattr(Path, "lstat", selected_lstat)


def test_canonical_directory_rejection_is_sanitized(tmp_path, caplog):
    actual = tmp_path / PRIVATE
    actual.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(actual)
    rejection(caplog, lambda: ImageStore(alias), "Invalid image store directory",
              "Image store safety rejection: reason=directory_type directory=False")


@pytest.mark.parametrize("invalid", ["owner", "mode"])
def test_directory_access_rejection_is_sanitized(tmp_path, caplog, monkeypatch, invalid):
    directory = tmp_path / PRIVATE
    directory.mkdir(mode=0o700)
    if invalid == "owner":
        wrong_owner(monkeypatch, directory)
    else:
        directory.chmod(0o755)
    rejection(caplog, lambda: ImageStore(directory), "Image store directory must be private",
              "Image store safety rejection: reason=directory_access "
              f"owner={invalid != 'owner'} private={invalid != 'mode'}")


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
@pytest.mark.parametrize("invalid", ["symlink", "directory", "hardlink", "owner", "mode"])
def test_file_access_rejection_is_sanitized(tmp_path, caplog, monkeypatch, suffix, invalid):
    directory = tmp_path / PRIVATE
    directory.mkdir(mode=0o700)
    target = directory / ("images.sqlite3" + suffix)
    if invalid == "symlink":
        target.symlink_to(tmp_path / "missing-private-target")
    elif invalid == "directory":
        target.mkdir(mode=0o700)
    else:
        target.write_bytes(b"private bytes")
        target.chmod(0o600)
        if invalid == "hardlink":
            os.link(target, tmp_path / "duplicate")
        elif invalid == "owner":
            wrong_owner(monkeypatch, target)
        else:
            target.chmod(0o644)
    info = target.lstat()
    rejection(caplog, lambda: ImageStore(directory),
              "Image store files must be private regular files",
              "Image store safety rejection: reason=file_access "
              f"kind={suffix or 'database'} regular={invalid not in {'symlink', 'directory'}} "
              f"single_link={info.st_nlink == 1} owner={invalid != 'owner'} "
              f"private={not bool(info.st_mode & 0o077)}")


@pytest.mark.parametrize("owner,chat,reason,message", [
    (PRIVATE, "chat", "owner_namespace", "Invalid attachment owner namespace"),
    ("a" * 64, PRIVATE + "/invalid", "conversation_namespace", "Invalid attachment conversation"),
])
def test_scope_rejections_do_not_disclose_identifiers(tmp_path, caplog, owner, chat, reason, message):
    store = ImageStore(tmp_path / PRIVATE)
    try:
        rejection(caplog, lambda: store.assert_available(owner, chat), message,
                  "Image store safety rejection: reason=" + reason)
    finally:
        store.close()


def test_constructor_and_reopen_do_not_emit_safety_warning(tmp_path, caplog):
    for _ in range(3):
        store = ImageStore(tmp_path / PRIVATE)
        store.assert_available("a" * 64, "chat")
        store.close()
    assert not [r for r in caplog.records if r.name == "pixel_image_store"]

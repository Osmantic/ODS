"""Actual owned files and deterministic publication orders; no service/account."""

import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX descriptor/link custody", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_access_bridge import atomic_json  # noqa: E402
from pixel_connections import lifecycle  # noqa: E402

OWNERS = (os.getuid(),)
BEFORE = {"kind": "connection", "marker": "before"}
AFTER = {"kind": "connection", "marker": "after"}


@pytest.fixture
def journal(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "transition.json"
    atomic_json(path, BEFORE)
    return path


def ordered_open(monkeypatch, path, publish, *, before_open=False, every_open=False):
    original = os.open
    seen = []

    def controlled(candidate, flags, *args, **kwargs):
        is_reader = candidate == path and flags & os.O_ACCMODE == os.O_RDONLY
        if is_reader and before_open and not seen:
            publish()
        fd = original(candidate, flags, *args, **kwargs)
        if is_reader:
            seen.append(fd)
            if not before_open and (every_open or len(seen) == 1):
                publish()
        return fd

    monkeypatch.setattr(os, "open", controlled)
    return seen


@pytest.mark.parametrize("order", ["unlinked", "displaced", "in-place"])
def test_path_stat_racing_publication(journal, monkeypatch, order):
    original_stat = os.stat
    sampled = []

    def stat_after_lookup(path, *args, **kwargs):
        if path != journal or kwargs.get("follow_symlinks") is not False or sampled:
            return original_stat(path, *args, **kwargs)
        # Hold the looked-up inode across a real directory-entry change.
        fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if order == "unlinked":
                atomic_json(journal, AFTER)
            else:
                # Ensure a distinct metadata snapshot even on coarse clocks.
                before = os.fstat(fd)
                os.utime(fd, ns=(before.st_atime_ns, before.st_mtime_ns - 1_000_000_000))
            info = os.fstat(fd)
            if order == "displaced":
                atomic_json(journal, AFTER)
            sampled.append(info.st_nlink)
            return info
        finally:
            os.close(fd)

    monkeypatch.setattr(os, "stat", stat_after_lookup)
    if order == "in-place":
        with pytest.raises(lifecycle.LifecycleError, match="changed while reading"):
            lifecycle.read_operation(journal, owners=OWNERS)
    else:
        assert lifecycle.read_operation(journal, owners=OWNERS) == AFTER
    assert sampled == [0 if order == "unlinked" else 1]


def test_stable_snapshot_and_initial_absence(journal):
    assert lifecycle.read_operation(journal, owners=OWNERS) == BEFORE
    journal.unlink()
    assert lifecycle.read_operation(journal, owners=OWNERS) is None


@pytest.mark.parametrize(
    "before_open", [True, False], ids=["publish-then-open", "open-then-publish"]
)
def test_atomic_publication_returns_current_valid_snapshot(
    journal, monkeypatch, before_open
):
    before_inode = journal.stat().st_ino
    seen = ordered_open(
        monkeypatch,
        journal,
        lambda: atomic_json(journal, AFTER),
        before_open=before_open,
    )
    assert lifecycle.read_operation(journal, owners=OWNERS) == AFTER
    assert journal.stat().st_ino != before_inode
    assert len(seen) == (1 if before_open else 2)


def test_publication_during_read_discards_old_bytes_and_reopens(journal, monkeypatch):
    original = os.fdopen
    published = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def read(self, *args):
            raw = self.handle.read(*args)
            if not published:
                published.append(raw)
                atomic_json(journal, AFTER)
            return raw

    monkeypatch.setattr(
        os,
        "fdopen",
        lambda fd, mode, *args, **kwargs: (
            Reader(original(fd, mode, *args, **kwargs))
            if mode == "rb"
            else original(fd, mode, *args, **kwargs)
        ),
    )
    assert lifecycle.read_operation(journal, owners=OWNERS) == AFTER
    assert json.loads(published[0]) == BEFORE


@pytest.mark.parametrize(
    "unsafe", ["mode", "hardlink", "symlink", "oversized", "malformed", "foreign-owner"]
)
def test_reopen_repeats_custody_and_refuses_unsafe_current_file(
    journal, tmp_path, monkeypatch, unsafe
):
    new_inode = []

    def publish():
        atomic_json(journal, AFTER)
        if unsafe == "mode":
            journal.chmod(0o644)
        elif unsafe == "hardlink":
            os.link(journal, tmp_path / "other-link")
        elif unsafe == "symlink":
            target = tmp_path / "private-target"
            atomic_json(target, AFTER)
            journal.unlink()
            journal.symlink_to(target)
        elif unsafe == "oversized":
            journal.write_bytes(b" " * (lifecycle.MAX_BYTES + 1))
        elif unsafe == "malformed":
            journal.write_bytes(b"{invalid")
        new_inode.append(journal.stat().st_ino)

    if unsafe == "foreign-owner":
        # Foreign UID is simulated on the NEW descriptor only; no account/chown.
        original_fstat = os.fstat

        class Foreign:
            def __init__(self, info):
                self.info = info

            def __getattr__(self, key):
                return os.getuid() + 1 if key == "st_uid" else getattr(self.info, key)

        monkeypatch.setattr(
            os,
            "fstat",
            lambda fd: (
                Foreign(original_fstat(fd))
                if new_inode and original_fstat(fd).st_ino == new_inode[0]
                else original_fstat(fd)
            ),
        )
    seen = ordered_open(monkeypatch, journal, publish)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.read_operation(journal, owners=OWNERS)
    # The symlink's safe open fails before a second descriptor is acquired.
    assert len(seen) == (1 if unsafe == "symlink" else 2)


def test_repeated_replacement_is_bounded_and_never_returns_unlinked_data(
    journal, monkeypatch
):
    seen = ordered_open(
        monkeypatch, journal, lambda: atomic_json(journal, AFTER), every_open=True
    )
    with pytest.raises(lifecycle.LifecycleError, match="changed while reading"):
        lifecycle.read_operation(journal, owners=OWNERS)
    assert len(seen) == 2


def test_disappeared_after_open_is_unknown_not_absent(journal, monkeypatch):
    seen = ordered_open(monkeypatch, journal, journal.unlink)
    with pytest.raises(lifecycle.LifecycleError, match="changed while reading"):
        lifecycle.read_operation(journal, owners=OWNERS)
    assert len(seen) == 1


def test_initial_hardlink_is_unsafe_without_a_reopen(journal, tmp_path, monkeypatch):
    os.link(journal, tmp_path / "another-link")
    original = os.open
    seen = []

    def observe(path, *args, **kwargs):
        if path == journal:
            seen.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", observe)
    with pytest.raises(lifecycle.LifecycleError, match="unsafe connection journal"):
        lifecycle.read_operation(journal, owners=OWNERS)
    assert len(seen) == 1

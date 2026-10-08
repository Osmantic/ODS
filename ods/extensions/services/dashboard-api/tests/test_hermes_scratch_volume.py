"""Retained Hermes homes must be able to use a newly mounted scratch volume."""
import os
import stat

import pytest

import hermes_auth


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Hermes runs in a POSIX container")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    target = home / "cache" / "scratch"
    target.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_UID", "501")
    monkeypatch.setenv("HERMES_GID", "20")
    monkeypatch.setattr(os, "getuid", lambda: 0)
    calls = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: calls.append((os.fstat(fd).st_ino, uid, gid)))
    return target, calls


def test_retained_scratch_directory_becomes_private_without_changing_files(scratch):
    target, calls = scratch
    child = target / "retained.txt"
    child.write_text("preserve")
    child.chmod(0o644)
    before = child.stat()
    for _ in range(2):
        hermes_auth._prepare_scratch_volume()
    assert calls == [(target.stat().st_ino, 501, 20)] * 2
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert child.read_text() == "preserve"
    assert child.stat() == before


@pytest.mark.parametrize("component", ["home", "cache", "scratch"])
def test_symlink_component_never_receives_chown(scratch, component):
    target, calls = scratch
    selected = {"home": target.parent.parent, "cache": target.parent, "scratch": target}[component]
    actual = selected.with_name(selected.name + "-actual")
    selected.rename(actual)
    selected.symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        hermes_auth._prepare_scratch_volume()
    assert calls == []


@pytest.mark.parametrize("bad_id", ["-1", "", "no", "4294967295", "１２"])
def test_invalid_uid_never_changes_ownership(scratch, monkeypatch, bad_id):
    _, calls = scratch
    monkeypatch.setenv("HERMES_UID", bad_id)
    with pytest.raises(ValueError):
        hermes_auth._prepare_scratch_volume()
    assert calls == []


def test_unprivileged_bootstrap_does_not_chown(scratch, monkeypatch):
    _, calls = scratch
    monkeypatch.setattr(os, "getuid", lambda: 501)
    hermes_auth._prepare_scratch_volume()
    assert calls == []


def test_missing_volume_or_remapping_keeps_custom_image_compatibility(scratch, monkeypatch):
    target, calls = scratch
    target.rmdir()
    hermes_auth._prepare_scratch_volume()
    monkeypatch.delenv("HERMES_UID")
    hermes_auth._prepare_scratch_volume()
    assert calls == []

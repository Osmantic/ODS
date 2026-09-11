"""Actual private Git preparation and hostile archive regression tests."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[2] / "extensions/services/pixel-agent/runtime-source"
SPEC = importlib.util.spec_from_file_location(
    "managed_source_prepare", SOURCE / "prepare.py"
)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def git(root, *args):
    return (
        subprocess.check_output(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(root),
                *args,
            ],
            env={
                **os.environ,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
        )
        .decode()
        .strip()
    )


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".gitattributes").write_text("* text=auto\n")
    (repo / ".git/info/attributes").write_text("* -text -filter -ident\n")
    (repo / "retained.bat").write_bytes(b"@echo off\r\n")
    (repo / "package.json").write_text(
        json.dumps({"version": "1.0.0", "packageManager": "pnpm@1.0.0"})
    )
    (repo / "entry.txt").write_text("before\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    base = git(repo, "rev-parse", "HEAD")
    (repo / "entry.txt").write_text("after\n")
    git(repo, "add", ".")
    patch = (git(repo, "diff", "--cached", "--binary") + "\n").encode()
    tree = git(repo, "write-tree")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "change.patch").write_bytes(patch)
    lock = {
        "schemaVersion": 1,
        "upstreamCommit": base,
        "sourceTree": tree,
        "runtimeVersion": "1.0.0",
        "nodeVersion": "v22.23.1",
        "packageManager": "pnpm@1.0.0",
        "patches": [
            {"file": "change.patch", "sha256": hashlib.sha256(patch).hexdigest()}
        ],
    }
    (inputs / "source-lock.json").write_text(json.dumps(lock))
    return repo, inputs, lock


@pytest.mark.skipif(
    os.name != "posix", reason="Source preparation intentionally requires POSIX"
)
def test_prepares_exact_tree_without_touching_dirty_upstream(fixture, tmp_path):
    repo, inputs, lock = fixture
    index = (repo / ".git/index").read_bytes()
    status = git(repo, "status", "--porcelain")
    result = prepare.prepare(repo, tmp_path, inputs=inputs)
    assert result["sourceTree"] == lock["sourceTree"]
    assert result["built"] is False and result["installed"] is False
    assert (Path(result["source"]) / "entry.txt").read_text() == "after\n"
    assert (Path(result["source"]) / "retained.bat").read_bytes() == b"@echo off\r\n"
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "status", "--porcelain") == status
    again = prepare.prepare(repo, tmp_path, inputs=inputs)
    assert again["source"] != result["source"]


def test_patch_drift_rejected_before_staging(fixture, tmp_path):
    _, inputs, _ = fixture
    (inputs / "change.patch").write_text("changed")
    with pytest.raises(ValueError, match="source-patch-drift"):
        prepare.load_lock(inputs)
    assert not list(tmp_path.glob("ods-managed-runtime-*"))


@pytest.mark.skipif(
    os.name != "posix", reason="Source preparation intentionally requires POSIX"
)
def test_wrong_tree_never_produces_success_receipt(fixture, tmp_path):
    repo, inputs, lock = fixture
    lock["sourceTree"] = "0" * 40
    (inputs / "source-lock.json").write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="patched-source-tree-mismatch"):
        prepare.prepare(repo, tmp_path, inputs=inputs)
    assert not list(tmp_path.glob("ods-managed-runtime-*/source-receipt.json"))


@pytest.mark.parametrize(
    "name,kind,target",
    [
        ("../escaped", "file", ""),
        ("/absolute", "file", ""),
        (".git/config", "file", ""),
        ("pipe", "fifo", ""),
        ("hard", "hardlink", "entry.txt"),
        ("link", "symlink", "../../escaped"),
        ("link", "symlink", "/absolute"),
    ],
)
def test_unsafe_archive_rejected_before_any_extraction(tmp_path, name, kind, target):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        safe = tarfile.TarInfo("entry.txt")
        safe.size = 4
        output.addfile(safe, io.BytesIO(b"safe"))
        item = tarfile.TarInfo(name)
        item.type = {
            "file": tarfile.REGTYPE,
            "fifo": tarfile.FIFOTYPE,
            "hardlink": tarfile.LNKTYPE,
            "symlink": tarfile.SYMTYPE,
        }[kind]
        item.linkname = target
        output.addfile(item)
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(ValueError):
        prepare.extract_source(archive, destination)
    assert list(destination.iterdir()) == []


def test_symlink_parent_rejected(tmp_path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "other"
        output.addfile(link)
        output.addfile(tarfile.TarInfo("link/entry.txt"))
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(ValueError, match="source-link-parent"):
        prepare.extract_source(archive, destination)
    assert list(destination.iterdir()) == []


def test_checked_in_lock_binds_both_patches():
    lock, _, patches = prepare.load_lock(SOURCE)
    assert len(patches) == 2
    assert lock["sourceTree"] == "dfba3c7a7c74ec7e52774d7f4a8ef376b8536ca7"


@pytest.mark.parametrize("target", ["../anchor/../escaped", "link"])
def test_chained_escape_or_cycle_rejected_before_extraction(tmp_path, target):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        for name, link_target in [("anchor", "."), ("dir/link", target)]:
            link = tarfile.TarInfo(name)
            link.type = tarfile.SYMTYPE
            link.linkname = link_target
            output.addfile(link)
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(ValueError, match="source-link"):
        prepare.extract_source(archive, destination)
    assert list(destination.iterdir()) == []

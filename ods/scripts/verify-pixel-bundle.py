#!/usr/bin/env python3
"""Verify the public Pixel source and its single-commit local install bundle."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vendor/pixel"
BUNDLE = ROOT / "vendor/pixel.bundle"
REF = "6e82d4c974be8c7b5aebe3a4ffd5374e20ad0ac5"
SHA256 = "115da4c894a40991c40fc3f5ff94cb2763b4b9395d875e1b781f234d563fc79b"


def command(*args):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True).strip()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def files(root):
    result = {}
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        for name in names:
            path = Path(parent) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Pixel source has a non-regular entry")
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def source_index_modes():
    entries = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--stage", "-z", "--", "vendor/pixel"],
        stderr=subprocess.DEVNULL,
    )
    result = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, _object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise ValueError("Pixel source index has an unresolved entry")
        relative = path.decode("utf-8", "surrogateescape").removeprefix("vendor/pixel/")
        result[relative] = mode
    return result


def bundle_tree_modes(checkout):
    entries = subprocess.check_output(
        ["git", "-C", str(checkout), "ls-tree", "-r", "-z", "HEAD"],
        stderr=subprocess.DEVNULL,
    )
    result = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, _object_id = metadata.decode("ascii").split()
        if kind != "blob":
            raise ValueError("Pixel bundle has a non-file entry")
        result[path.decode("utf-8", "surrogateescape")] = mode
    return result


def main():
    if not SOURCE.is_dir() or SOURCE.is_symlink() or not BUNDLE.is_file() or BUNDLE.is_symlink():
        raise ValueError("Pixel source or bundle missing")
    if BUNDLE.stat().st_size > 64 * 1024 * 1024 or digest(BUNDLE) != SHA256:
        raise ValueError("Pixel bundle digest mismatch")
    with tempfile.TemporaryDirectory(prefix="ods-pixel-bundle-") as temporary:
        checkout = Path(temporary) / "checkout"
        command("git", "-c", "credential.interactive=never", "clone", "--quiet", "--no-local",
                "--", str(BUNDLE), str(checkout))
        if command("git", "-C", str(checkout), "rev-parse", "HEAD") != REF:
            raise ValueError("Pixel bundle commit mismatch")
        if command("git", "-C", str(checkout), "rev-list", "--count", "--all") != "1":
            raise ValueError("Pixel bundle contains additional history")
        if len(command("git", "-C", str(checkout), "rev-list", "--parents", "HEAD").split()) != 1:
            raise ValueError("Pixel bundle commit has a parent")
        if command("git", "-C", str(checkout), "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s") != (
                "Osmantic ODS <noreply@osmantic.com>|"
                "Osmantic ODS <noreply@osmantic.com>|Bundle Pixel source for ODS"):
            raise ValueError("Pixel bundle has unexpected identity or message")
        if command("git", "bundle", "list-heads", str(BUNDLE)) != REF + " HEAD":
            raise ValueError("Pixel bundle has unexpected refs")
        if files(SOURCE) != files(checkout):
            raise ValueError("Pixel source differs from its install bundle")
        source_modes = source_index_modes()
        bundle_modes = bundle_tree_modes(checkout)
        if source_modes != bundle_modes:
            raise ValueError("Pixel source and bundle executable modes differ")
        for launcher in ("pixel", "scripts/bootstrap.sh", "scripts/install.sh"):
            if bundle_modes.get(launcher) != "100755":
                raise ValueError(f"Pixel launcher is not executable: {launcher}")
    print("Pixel source and single-commit install bundle verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Pixel bundle verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

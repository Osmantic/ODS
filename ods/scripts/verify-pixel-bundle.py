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
REF = "817214d5ec3d8aa583fe50c1dc7561f3c1a16dff"
SHA256 = "8fea465b1b42d82da0a286936d0e029b038321fd39793f5a849843ef11aee865"


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
    print("Pixel source and single-commit install bundle verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Pixel bundle verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

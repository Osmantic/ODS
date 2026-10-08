"""Verify the narrowly reviewed local builds shipped with builtin extensions."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_MAX_RECIPE_BYTES = 24576
_RECIPES = {
    "langfuse-minio": (
        "ods-langfuse-minio:RELEASE.2025-09-07T16-13-09Z",
        "Dockerfile.minio",
        "176cd2da7d7004f55a595372d2278a6f8dd9c04b2f6cbf55335972a9e446740e",
    ),
    "langfuse-minio-init": (
        "ods-langfuse-mc:RELEASE.2025-08-13T08-35-41Z",
        "Dockerfile.mc",
        "29431828f528d65b44857bb6754cf2493bf633e202556cdee6d3ac3f2d65d3e3",
    ),
}


# Shipped extensions whose image is built from their own folder. The digest
# covers every file in that folder except its compose files, so nothing can
# be added to, changed in or removed from what `docker build` reads.
# After changing one of these folders, run:
#     python3 scripts/pin-builtin-build-contexts.py --write
# (service: (folder, image, dockerfile, digest))
_CONTEXT_BUILDS = {
    "ape": ("ape", None, "Dockerfile", "088871f355fb73f2ca036eeb5e1f22cf3bb7882f7be87ee70b190c87886386cd"),
    "brave-search": ("brave-search", "ods-brave-search:local", "Dockerfile", "6f785938be3c27b7dacb45028e7ce9a2a14b313dc4d53d8b745f078fa9075e90"),
    "privacy-shield": ("privacy-shield", None, "Dockerfile", "1a176804603adcea6ee4d9dc505441c5dc8bb8ac2b1475746489388a50bac001"),
    "token-spy": ("token-spy", None, "Dockerfile", "6af1b5694bbde7894592fb9eac55b988ac46cc3afe85e5240a4f5b056ec575ea"),
}
_MAX_CONTEXT_FILE_BYTES = 8 * 1024 * 1024
_MAX_CONTEXT_FILES = 512
# Enabling or disabling an extension renames its compose files, and the
# shared compose policy judges them separately.
_COMPOSE_FILE = re.compile(r"compose(?:\.[a-z0-9-]+)?\.yaml(?:\.disabled)?")


def _read_recipe(path: Path, limit: int = _MAX_RECIPE_BYTES) -> bytes:
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > limit):
            raise ValueError("Unsafe source recipe")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, limit + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if (len(data) > limit
                or any(getattr(before, key) != getattr(after, key) for key in fields)):
            raise ValueError("Source recipe changed while reading")
        return bytes(data)
    finally:
        os.close(fd)


def _normalized_sha256(data: bytes) -> str:
    """SHA-256 of file content with CRLF line endings read as LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def files_digest(files: Iterable[tuple[str, bytes]]) -> str:
    """SHA-256 over (relative path, content) pairs, in path order.

    CRLF checkouts are normalized to LF, as for the reviewed Dockerfiles.
    The dashboard-api image runs Python 3.11, where an f-string expression
    cannot hold a backslash, so the hash is computed outside the braces.
    """
    lines = sorted(f"{relative}\0{_normalized_sha256(data)}\n" for relative, data in files)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def build_context_files(package: Path) -> list[tuple[str, bytes]]:
    """Every file in a build folder except its compose files.

    Links and anything but folders and regular files are refused, so the
    result is exactly what a build of this folder can read.
    """
    files = []
    for directory, subdirectories, names in os.walk(package, followlinks=False):
        here = Path(directory)
        for name in subdirectories:
            if (here / name).is_symlink():
                raise ValueError("Build context holds a link")
        for name in names:
            path = here / name
            if here == package and _COMPOSE_FILE.fullmatch(name):
                continue
            if path.is_symlink():
                raise ValueError("Build context holds a link")
            files.append((path.relative_to(package).as_posix(),
                          _read_recipe(path, _MAX_CONTEXT_FILE_BYTES)))
            if len(files) > _MAX_CONTEXT_FILES:
                raise ValueError("Build context holds too many files")
    return files


def _verify_context_build(compose_path, service_name, service_def, builtin_root) -> bool:
    folder, expected_image, dockerfile, expected_digest = _CONTEXT_BUILDS[service_name]
    root = Path(os.path.abspath(builtin_root))
    compose = Path(os.path.abspath(compose_path))
    package = root / folder
    if compose.parent != package or compose.name not in {"compose.yaml", "compose.yaml.disabled"}:
        return False
    if any(path.is_symlink() for path in (root, package, compose)):
        return False
    if not root.is_dir() or not package.is_dir():
        return False
    if package.resolve(strict=True).parent != root.resolve(strict=True):
        return False
    if not compose.is_file() or compose.stat().st_nlink != 1:
        return False
    expected_build = {"context": f"./extensions/services/{folder}", "dockerfile": dockerfile}
    if service_def.get("build") != expected_build or service_def.get("image") != expected_image:
        return False
    return files_digest(build_context_files(package)) == expected_digest


def verify_builtin_source_build(
    compose_path: Path,
    service_name: str,
    service_def: dict[str, Any],
    builtin_root: Path,
) -> bool:
    """Accept only shipped builds whose reviewed bytes match.

    The Langfuse MinIO images build from a reviewed Dockerfile alone; the
    extensions in _CONTEXT_BUILDS build from their whole folder, which must
    match its pinned digest exactly.

    Resolve containment only after checking original lexical paths for links.
    CRLF checkouts are normalized to LF before checking the reviewed hash.
    The caller must still enforce every shared Compose policy rule and require
    builtin custody. An image tag or caller-supplied builtin flag is insufficient.
    """
    try:
        if service_name in _CONTEXT_BUILDS:
            return _verify_context_build(compose_path, service_name, service_def, builtin_root)
        expected_image, dockerfile, expected_hash = _RECIPES[service_name]
        root = Path(os.path.abspath(builtin_root))
        compose = Path(os.path.abspath(compose_path))
        package = root / "langfuse"
        if compose.parent != package or compose.name not in {"compose.yaml", "compose.yaml.disabled"}:
            return False
        if any(path.is_symlink() for path in (root, package, compose, package / dockerfile)):
            return False
        if not root.is_dir() or not package.is_dir():
            return False
        if package.resolve(strict=True).parent != root.resolve(strict=True):
            return False
        if not compose.is_file() or compose.stat().st_nlink != 1:
            return False
        expected_build = {"context": "./extensions/services/langfuse", "dockerfile": dockerfile}
        if service_def.get("build") != expected_build or service_def.get("image") != expected_image:
            return False
        data = _read_recipe(package / dockerfile).replace(b"\r\n", b"\n")
        return hashlib.sha256(data).hexdigest() == expected_hash
    except (OSError, ValueError, TypeError, KeyError):
        return False

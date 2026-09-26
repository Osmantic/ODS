"""Verify the narrowly reviewed local builds shipped with builtin extensions."""

from __future__ import annotations

import hashlib
import os
import stat
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


def _read_recipe(path: Path) -> bytes:
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > _MAX_RECIPE_BYTES):
            raise ValueError("Unsafe source recipe")
        data = bytearray()
        while len(data) <= _MAX_RECIPE_BYTES:
            chunk = os.read(fd, _MAX_RECIPE_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if (len(data) > _MAX_RECIPE_BYTES
                or any(getattr(before, key) != getattr(after, key) for key in fields)):
            raise ValueError("Source recipe changed while reading")
        return bytes(data)
    finally:
        os.close(fd)


def verify_builtin_source_build(
    compose_path: Path,
    service_name: str,
    service_def: dict[str, Any],
    builtin_root: Path,
) -> bool:
    """Accept only shipped Langfuse MinIO recipes with reviewed source bytes.

    Resolve containment only after checking original lexical paths for links.
    CRLF checkouts are normalized to LF before checking the reviewed hash.
    The caller must still enforce every shared Compose policy rule and require
    builtin custody. An image tag or caller-supplied builtin flag is insufficient.
    """
    try:
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

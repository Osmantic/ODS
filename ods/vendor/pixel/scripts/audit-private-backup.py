#!/usr/bin/env python3
"""Fail-closed structural audit for a decrypted Pixel private-state backup stream."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
import tarfile
from pathlib import Path


MANIFEST = ".pixel-backup-manifest.json"
MAX_MANIFEST = 1024 * 1024
MAX_MEMBERS = 100_000
MAX_TOTAL_BYTES = 20 * 1024 * 1024 * 1024


class AuditError(RuntimeError):
    pass


def canonical_member(name: str) -> str:
    value = name[:-1] if name.endswith("/") else name
    if (
        not value or value.startswith("/") or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or posixpath.normpath(value) != value or value in {".", ".."}
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise AuditError("backup contains an unsafe member path")
    return value


def contains(root: str, value: str) -> bool:
    return value == root or value.startswith(root + "/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowed", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    args = parser.parse_args()
    allowed = [line.rstrip("\n") for line in args.allowed.read_text(encoding="utf-8").splitlines() if line]
    if not allowed or len(allowed) > 100 or len(set(allowed)) != len(allowed):
        raise AuditError("restore allowlist is empty, duplicated, or oversized")
    for value in allowed:
        if canonical_member(value) != value:
            raise AuditError("restore allowlist contains an unsafe path")

    members: list[tuple[str, tarfile.TarInfo]] = []
    names: set[str] = set()
    manifest: dict | None = None
    total_bytes = 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        for item in archive:
            name = canonical_member(item.name)
            if name in names:
                raise AuditError("backup contains a duplicate member")
            names.add(name)
            if len(names) > MAX_MEMBERS:
                raise AuditError("backup exceeds its member limit")
            if item.mode & 0o4000:
                raise AuditError("backup contains a setuid member")
            if item.mode & 0o2000 and not item.isdir():
                raise AuditError("backup contains a non-directory setgid member")
            if name == MANIFEST:
                if not item.isfile() or item.size > MAX_MANIFEST:
                    raise AuditError("backup manifest is missing or malformed")
                handle = archive.extractfile(item)
                if handle is None:
                    raise AuditError("backup manifest could not be read")
                manifest = json.loads(handle.read(MAX_MANIFEST + 1).decode("utf-8"))
                continue
            if not (item.isfile() or item.isdir() or item.issym()):
                raise AuditError("backup contains a hard link, device, fifo, or special member")
            if item.isfile():
                total_bytes += item.size
                if item.size < 0 or total_bytes > MAX_TOTAL_BYTES:
                    raise AuditError("backup exceeds its uncompressed byte limit")
            members.append((name, item))

    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise AuditError("backup manifest is absent or uses an unsupported schema")
    paths = manifest.get("paths")
    if (
        not isinstance(paths, list) or not paths or paths != sorted(paths)
        or len(paths) > 100 or len(set(paths)) != len(paths)
        or any(not isinstance(path, str) or path not in allowed for path in paths)
    ):
        raise AuditError("backup manifest paths do not match the local restore allowlist")
    if any(any(other != path and contains(other, path) for other in paths) for path in paths):
        raise AuditError("backup manifest contains overlapping roots")
    for path in paths:
        if path not in names:
            raise AuditError("backup manifest declares a missing root")
        root_item = next(item for name, item in members if name == path)
        if root_item.issym():
            raise AuditError("backup declares a symbolic link as a restore root")
    for name, item in members:
        matching = [root for root in paths if contains(root, name)]
        if len(matching) != 1:
            raise AuditError("backup member falls outside its declared restore roots")
        if item.issym():
            if (
                not item.linkname or item.linkname.startswith("/") or len(item.linkname) > 4096
                or any(ord(character) < 32 or ord(character) == 127 for character in item.linkname)
            ):
                raise AuditError("backup contains an absolute or empty symbolic link")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), item.linkname))
            if not contains(matching[0], target):
                raise AuditError("backup symbolic link escapes its declared root")

    args.manifest_output.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    root_contract = "".join(path + "\n" for path in paths)
    print(json.dumps({
        "status": "pass", "schemaVersion": 1, "members": len(members),
        "roots": len(paths), "uncompressedBytes": total_bytes,
        "pixelVersion": str(manifest.get("pixelVersion", ""))[:64],
        "rootsSha256": hashlib.sha256(root_contract.encode("utf-8")).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, ValueError, json.JSONDecodeError, tarfile.TarError, UnicodeError) as error:
        print(f"pixel-backup-audit: {error}", file=sys.stderr)
        raise SystemExit(1)

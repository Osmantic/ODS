#!/usr/bin/env python3
"""Read the authenticated root contract (manifest ``paths``) from a decrypted
Pixel private-state backup stream without performing a live restore.

This is a migration-only primitive. Standard restores never use it: they enforce
the strict local 4.2 restore allowlist through audit-private-backup.py. The
clean-migration activate builds its migration contract from the exact roots that
the authenticated 3.2 backup itself declares, so it permits the authenticated 3.2
manifest to omit new 4.2-era control/frontier roots. It does NOT accept a 3.2 root
that 4.2 dropped: the live migration restore still requires every declared legacy
destination to be within the known 4.2 destination allowlist, and standard restore
is never widened.
"""

from __future__ import annotations

import json
import posixpath
import sys
import tarfile

from pathlib import Path


MANIFEST = ".pixel-backup-manifest.json"
MAX_MANIFEST = 1024 * 1024


class ManifestError(RuntimeError):
    pass


def canonical_member(name: str) -> str:
    value = name[:-1] if name.endswith("/") else name
    if (
        not value or value.startswith("/") or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or posixpath.normpath(value) != value or value in {".", ".."}
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ManifestError("backup contains an unsafe member path")
    return value


def main() -> int:
    manifest: dict | None = None
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        for item in archive:
            name = canonical_member(item.name)
            if name != MANIFEST:
                continue
            if not item.isfile() or item.size > MAX_MANIFEST:
                raise ManifestError("backup manifest is missing or malformed")
            handle = archive.extractfile(item)
            if handle is None:
                raise ManifestError("backup manifest could not be read")
            manifest = json.loads(handle.read(MAX_MANIFEST + 1).decode("utf-8"))
            # Consume the remainder of the stream so the upstream decrypt/pipe
            # completes cleanly and never receives an early SIGPIPE.
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ManifestError("backup manifest is absent or uses an unsupported schema")
    paths = manifest.get("paths")
    if (
        not isinstance(paths, list) or not paths or paths != sorted(paths)
        or len(paths) > 100 or len(set(paths)) != len(paths)
        or any(not isinstance(path, str) or canonical_member(path) != path for path in paths)
    ):
        raise ManifestError("backup manifest root contract is invalid")
    if any(any(other != path and (path == other or path.startswith(other + "/")) for other in paths) for path in paths):
        raise ManifestError("backup manifest root contract overlaps")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ManifestError, OSError, ValueError, json.JSONDecodeError, tarfile.TarError, UnicodeError) as error:
        print(f"pixel-backup-manifest: {error}", file=sys.stderr)
        raise SystemExit(1)

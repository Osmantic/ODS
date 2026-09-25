#!/usr/bin/env python3
"""Safely extract an untrusted npm archive for static contract analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile


MAX_MEMBERS = 50_000
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 750 * 1024 * 1024
MARKER = ".pixel-extraction.json"


class ExtractionError(RuntimeError):
    pass


def relative_member(name: str) -> Path:
    if "\\" in name:
        raise ExtractionError("archive member contains a backslash")
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or value.parts[0] != "package":
        raise ExtractionError(f"archive member is outside package/: {name}")
    relative = value.relative_to("package")
    if not relative.parts:
        return Path(".")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ExtractionError(f"archive member has an unsafe path: {name}")
    return Path(*relative.parts)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def tree_evidence(root: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.name == MARKER:
            continue
        if path.is_symlink():
            raise ExtractionError("extracted tree contains a symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            continue
        if not path.is_file():
            raise ExtractionError("extracted tree contains a non-regular object")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ExtractionError(f"extracted file is too large: {relative}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ExtractionError("extracted tree is too large")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(file_digest(path).encode("ascii") + b"\0")
        count += 1
    return {"treeSha256": digest.hexdigest(), "fileCount": count, "totalBytes": total}


def verify_existing(destination: Path, archive_sha256: str) -> dict[str, object]:
    marker = destination / MARKER
    if destination.is_symlink() or not destination.is_dir() or marker.is_symlink() or not marker.is_file():
        raise ExtractionError("existing extraction is not a verified directory")
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    if recorded.get("archiveSha256") != archive_sha256:
        raise ExtractionError("existing extraction belongs to a different archive")
    observed = tree_evidence(destination)
    if not (destination / "package.json").is_file() or (destination / "package.json").is_symlink():
        raise ExtractionError("existing extraction has no regular package.json")
    if any(recorded.get(key) != value for key, value in observed.items()):
        raise ExtractionError("existing extraction tree differs from its evidence")
    return {**recorded, "reused": True}


def _stream_digest(reader) -> str:
    reader.seek(0)
    digest = hashlib.sha256()
    while block := reader.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def extract(archive: Path, destination: Path, archive_sha256: str) -> dict[str, object]:
    if archive.is_symlink() or not archive.is_file():
        raise ExtractionError("archive must be a regular file")
    archive = archive.resolve(strict=True)
    destination = Path(os.path.abspath(destination))
    # Pin one file descriptor across the integrity check and the extraction so the exact
    # bytes that are hashed are the exact bytes that are read out. Opening the path twice
    # (hash it, then tarfile.open it) leaves a TOCTOU window in which the verified archive
    # and the extracted archive could differ -- an integrity fail-open.
    archive_fd = os.open(archive, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(archive_fd).st_mode):
            raise ExtractionError("archive must be a regular file")
        reader = os.fdopen(archive_fd, "rb")
    except Exception:
        os.close(archive_fd)
        raise
    try:
        if _stream_digest(reader) != archive_sha256:
            raise ExtractionError("archive SHA-256 differs from the intake evidence")
        if destination.exists() or destination.is_symlink():
            return verify_existing(destination, archive_sha256)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.parent / f".{destination.name}.extract-{os.getpid()}"
        temporary.mkdir(mode=0o700)
        try:
            total = 0
            reader.seek(0)
            with tarfile.open(fileobj=reader, mode="r:gz") as bundle:
                members = bundle.getmembers()
                if len(members) > MAX_MEMBERS:
                    raise ExtractionError("archive has too many members")
                for member in members:
                    relative = relative_member(member.name)
                    target = temporary / relative
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    if not member.isfile():
                        raise ExtractionError(f"unsupported archive member type: {member.name}")
                    if member.size > MAX_FILE_BYTES:
                        raise ExtractionError(f"archive member is too large: {member.name}")
                    total += member.size
                    if total > MAX_TOTAL_BYTES:
                        raise ExtractionError("archive payload is too large")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise ExtractionError(f"archive member cannot be read: {member.name}")
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    observed = 0
                    try:
                        with os.fdopen(descriptor, "wb") as output:
                            while block := source.read(1024 * 1024):
                                observed += len(block)
                                if observed > member.size or observed > MAX_FILE_BYTES:
                                    raise ExtractionError(f"archive member exceeded its declared size: {member.name}")
                                output.write(block)
                    finally:
                        source.close()
                    if observed != member.size:
                        raise ExtractionError(f"archive member size differs: {member.name}")
            if not (temporary / "package.json").is_file() or (temporary / "package.json").is_symlink():
                raise ExtractionError("archive has no regular package.json")
            evidence = {"schemaVersion": 1, "archiveSha256": archive_sha256, **tree_evidence(temporary)}
            marker = temporary / MARKER
            marker.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            marker.chmod(0o600)
            temporary.rename(destination)
            return {**evidence, "reused": False}
        except Exception:
            if temporary.exists() and temporary.parent == destination.parent:
                shutil.rmtree(temporary)
            raise
    finally:
        reader.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("archive_sha256")
    args = parser.parse_args()
    if not args.archive.is_absolute() or not args.destination.is_absolute():
        parser.error("archive and destination must be absolute paths")
    if not re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256):
        parser.error("archive_sha256 is invalid")
    try:
        print(json.dumps(extract(args.archive, args.destination, args.archive_sha256), sort_keys=True))
    except (ExtractionError, OSError, tarfile.TarError, json.JSONDecodeError) as error:
        print(f"upstream extraction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

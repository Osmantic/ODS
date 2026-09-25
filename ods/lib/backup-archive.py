"""Extract an ODS tar.gz into private staging, then publish one backup directory."""
from __future__ import annotations

import gzip
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile


def member_path(name: str, backup_id: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
            or (os.name == "nt" and ":" in name)
            or not path.parts or path.parts[0] != backup_id):
        raise ValueError("archive member is outside the selected backup")
    return path


def extract(archive: Path, root: Path, backup_id: str) -> None:
    if (not backup_id or backup_id in (".", "..") or any(c in backup_id for c in "/\\")
            or (os.name == "nt" and ":" in backup_id)):
        raise ValueError("invalid backup ID")
    root = root.resolve(strict=True)
    destination = root / backup_id
    if os.path.lexists(destination):
        raise ValueError("backup destination already exists")
    with tempfile.TemporaryDirectory(prefix=".ods-extract-", dir=root) as directory:
        stage = Path(directory)
        with gzip.open(archive, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r:") as source:
            members = {}
            for item in source.getmembers():
                relative = member_path(item.name, backup_id)
                if relative in members:
                    raise ValueError("archive contains duplicate member paths")
                if not (item.isfile() or item.isdir() or item.issym() or item.islnk()):
                    raise ValueError("archive contains a special file")
                members[relative] = item
            manifest = members.get(PurePosixPath(backup_id) / "manifest.json")
            if manifest is None or not manifest.isfile():
                raise ValueError("archive has no regular backup manifest")
            # No file is ever written through an archive-created link.
            for relative in members:
                for parent in relative.parents:
                    if parent in members and not members[parent].isdir():
                        raise ValueError("archive member has a non-directory parent")
            for relative, item in members.items():
                target = stage / str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.isdir():
                    target.mkdir(exist_ok=True)
                elif item.isfile():
                    with source.extractfile(item) as reader, target.open("xb") as writer:
                        shutil.copyfileobj(reader, writer)
                    os.chmod(target, item.mode & 0o777)
                    os.utime(target, (item.mtime, item.mtime))
            # Create links only after regular files. Resolving the complete link
            # graph then catches escapes through chains, including ../ targets.
            for relative, item in members.items():
                if item.issym():
                    if (not item.linkname or "\\" in item.linkname
                            or PurePosixPath(item.linkname).is_absolute()
                            or (os.name == "nt" and ":" in item.linkname)):
                        raise ValueError("archive contains an unsafe symbolic link")
                    os.symlink(item.linkname, stage / str(relative))
            payload = stage / backup_id
            for relative, item in members.items():
                if item.issym():
                    (stage / str(relative)).resolve().relative_to(payload)
                elif item.islnk():
                    link = member_path(item.linkname, backup_id)
                    referent = members.get(link)
                    if referent is None or not referent.isfile():
                        raise ValueError("hard link must refer to a regular archive member")
                    os.link(stage / str(link), stage / str(relative))
            # Read the remainder through gzip to verify its trailer/CRC too.
            while compressed.read(1024 * 1024):
                pass
            for relative, item in sorted(members.items(), key=lambda pair: len(pair[0].parts), reverse=True):
                if item.isdir():
                    os.chmod(stage / str(relative), item.mode & 0o777)
                    os.utime(stage / str(relative), (item.mtime, item.mtime))
        if os.path.lexists(destination):
            raise ValueError("backup destination appeared during extraction")
        os.rename(payload, destination)


if __name__ == "__main__":
    try:
        extract(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, RuntimeError, EOFError, tarfile.TarError) as error:
        print(f"Backup archive rejected: {error}", file=sys.stderr)
        raise SystemExit(1)

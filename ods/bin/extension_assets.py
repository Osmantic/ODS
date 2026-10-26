"""Model files an extension's container reads that are not part of its image.

An extension may ship ``model-packs.json`` beside its manifest. Each pack lists
files with a pinned HTTPS URL, SHA-256 and size; ``model_root`` names, for each
GPU backend, the folder under the install directory that the extension's
container reads its models from. The host agent's prepare step (adding the
extension from the Extensions page) and the installer both fetch the default
packs here, so the container never starts without its model.

A file is downloaded to ``<name>.part``, resumed with an HTTP range request,
checked against its SHA-256 and only then renamed into place. A file already
in place with the pinned size counts as present; it was checked when fetched.

Standard library only: the installer runs this with the host's python3.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PACKS_FILE = "model-packs.json"
SCHEMA = "ods.model-packs.v1"
CHUNK_BYTES = 1 << 20
READ_TIMEOUT_SECONDS = 60
USER_AGENT = "ODS-extension-assets/1"
# The rule the host agent applies to model downloads (_download_disk_margin).
MARGIN_MIN_BYTES = 2 * 1024 ** 3
MARGIN_FRACTION = 0.05
_PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AssetError(Exception):
    """A pack file that could not be prepared; the message is for the owner."""


@dataclass(frozen=True)
class PackFile:
    pack_id: str
    label: str
    url: str
    sha256: str
    size: int
    target: Path


def default_files(ext_dir: Path, install_dir: Path, backend: str) -> list[PackFile]:
    """The files of the default packs for ``backend``; empty when there are none."""
    path = ext_dir / PACKS_FILE
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema must be {SCHEMA}")
    try:
        return _parse(path, doc, install_dir.resolve(), backend)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{path}: malformed pack list ({type(exc).__name__}: {exc})") from exc


def _parse(path: Path, doc: dict, install: Path, backend: str) -> list[PackFile]:
    root_name = doc["model_root"].get(backend)
    if root_name is None:
        return []
    root = (install / root_name).resolve()
    if not root.is_relative_to(install):
        raise ValueError(f"{path}: model_root for {backend} leaves the install directory")
    files = []
    for pack in doc["packs"]:
        if pack.get("default") is not True:
            continue
        for item in pack["files"]:
            names = (item["subdir"], item["file"])
            if not all(isinstance(name, str) and _PLAIN_NAME.fullmatch(name) for name in names):
                raise ValueError(f"{path}: {names} must be plain names")
            if not item["url"].startswith("https://") or not _SHA256.fullmatch(item["sha256"]):
                raise ValueError(f"{path}: {item['file']} needs an https URL and a lowercase SHA-256")
            if not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] <= 0:
                raise ValueError(f"{path}: {item['file']} needs its size in bytes")
            files.append(PackFile(pack["id"], pack["label"], item["url"], item["sha256"],
                                  item["bytes"], root / item["subdir"] / item["file"]))
    return files


def missing(files: list[PackFile]) -> list[PackFile]:
    """The files not yet in place with their pinned size."""
    return [item for item in files
            if not (item.target.is_file() and item.target.stat().st_size == item.size)]


def margin_bytes(needed: int) -> int:
    return max(MARGIN_MIN_BYTES, int(needed * MARGIN_FRACTION))


def check_space(files: list[PackFile]) -> None:
    """Refuse before downloading when the disk cannot hold the files plus a margin."""
    if not files:
        return
    needed = sum(item.size - _part_bytes(item) for item in files)
    folder = files[0].target.parent
    while not folder.exists():
        folder = folder.parent
    free = shutil.disk_usage(folder).free
    if free < needed + margin_bytes(needed):
        raise AssetError(
            f"Not enough free disk for {files[0].label}: it needs {_gb(needed)} GB plus "
            f"{_gb(margin_bytes(needed))} GB to spare, and {folder} has {_gb(free)} GB free. "
            "Free some space, then try again.")


def fetch(item: PackFile, report: Callable[[int], None], *,
          opener: Callable | None = None) -> None:
    """Download one file into place, resuming a partial download."""
    opener = opener or urllib.request.urlopen
    part = item.target.with_name(item.target.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    offset = _part_bytes(item)
    if offset >= item.size:
        part.unlink()  # a complete or oversized part is checked again from zero
        offset = 0
    digest = hashlib.sha256()
    if offset:
        with part.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    done = offset
    try:
        with opener(urllib.request.Request(item.url, headers=headers),
                    timeout=READ_TIMEOUT_SECONDS) as response:
            if offset and response.status != 206:  # the server sent the whole file again
                digest, done = hashlib.sha256(), 0
            with part.open("ab" if done else "wb") as out:
                for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    report(done)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssetError(f"The download of {item.label} stopped ({_reason(exc)}). "
                         "Try again to resume it; the part already downloaded is kept.") from exc
    if done != item.size:
        raise AssetError(f"The download of {item.label} ended at {_gb(done)} of {_gb(item.size)} GB. "
                         "Try again to resume it.")
    if digest.hexdigest() != item.sha256:
        part.unlink()
        raise AssetError(f"{item.label} did not match its pinned SHA-256, so it was deleted. "
                         "Try again to download it once more.")
    os.replace(part, item.target)


def _part_bytes(item: PackFile) -> int:
    part = item.target.with_name(item.target.name + ".part")
    return part.stat().st_size if part.is_file() else 0


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None) or exc
    return str(reason) or type(exc).__name__


def _gb(count: int) -> str:
    return f"{count / 1e9:.1f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch an extension's default model packs.")
    parser.add_argument("ext_dir", type=Path)
    parser.add_argument("install_dir", type=Path)
    parser.add_argument("backend")
    args = parser.parse_args(argv)
    todo = missing(default_files(args.ext_dir, args.install_dir, args.backend))
    try:
        check_space(todo)
        for item in todo:
            print(f"Downloading {item.label} ({_gb(item.size)} GB) to {item.target}", flush=True)
            step = [0]

            def report(done: int, item: PackFile = item) -> None:
                percent = done * 100 // item.size
                if percent >= step[0] + 10:
                    step[0] = percent - percent % 10
                    print(f"  {item.label}: {step[0]}%", flush=True)

            fetch(item, report)
            print(f"{item.label} is ready (SHA-256 verified)", flush=True)
    except AssetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not todo:
        print("All default model packs are already in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())

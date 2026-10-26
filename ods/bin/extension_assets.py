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
import contextlib
import errno
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

try:
    import fcntl
except ImportError:  # Windows; see _folder_lock
    fcntl = None

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
    """A pack file that could not be prepared; the message is for the owner.

    ``retryable`` marks a dropped connection, where trying again resumes.
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


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
    # Checked by name, not resolved: a data folder linked to another disk is
    # the owner's layout, while a pack list may not climb out of the install.
    parts = PurePosixPath(root_name).parts if isinstance(root_name, str) else ()
    if not parts or not all(_PLAIN_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"{path}: model_root for {backend} must be a relative path of plain names")
    root = install.joinpath(*parts)
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
    needed = sum(_remaining(item) for item in files)
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
    """Download one file into place, resuming a partial download.

    Every failure is an AssetError that says what to do next.
    """
    opener = opener or urllib.request.urlopen
    folder = item.target.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with _folder_lock(folder, item):
            _download(item, report, opener)
    except PermissionError as exc:
        raise _not_writable(item, exc) from exc
    except OSError as exc:  # the disk: _request and _read turn network errors into AssetError
        raise AssetError(f"ODS could not save {item.label} in {folder} ({_reason(exc)}). "
                         "Fix that, then try again; the part already downloaded is kept.") from exc


def _download(item: PackFile, report: Callable[[int], None], opener: Callable) -> None:
    part = item.target.with_name(item.target.name + ".part")
    with open(part, "a+b") as out:
        done = out.seek(0, os.SEEK_END)
        if done > item.size:  # not this file: start over
            out.truncate(0)
            done = 0
        digest = hashlib.sha256()
        out.seek(0)
        for chunk in iter(lambda: out.read(CHUNK_BYTES), b""):
            digest.update(chunk)
        # A part already at full size (the rename failed last time) is only checked.
        if done < item.size:
            with _request(item, out, done, opener) as response:
                if done and response.status != 206:  # the server sent the whole file again
                    out.truncate(0)
                    digest, done = hashlib.sha256(), 0
                for chunk in iter(lambda: _read(item, response), b""):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    report(done)
    if done != item.size:
        raise AssetError(f"The download of {item.label} ended at {_gb(done)} of {_gb(item.size)} GB. "
                         "Try again to resume it.", retryable=True)
    if digest.hexdigest() != item.sha256:
        part.unlink()
        raise AssetError(f"{item.label} did not match its pinned SHA-256, so it was deleted. "
                         "Try again to download it once more.")
    os.replace(part, item.target)


def _request(item: PackFile, out, offset: int, opener: Callable):
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        return opener(urllib.request.Request(item.url, headers=headers),
                      timeout=READ_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # the server's copy is shorter than the part, so it cannot resume
            out.truncate(0)
            raise AssetError(f"The server's copy of {item.label} is not the pinned file, so the partial "
                             "download was deleted. Try again to download it from the start.") from exc
        if exc.code in (408, 429) or exc.code >= 500:
            raise _interrupted(item, exc) from exc
        host = urllib.parse.urlsplit(item.url).netloc
        raise AssetError(f"The server refused {item.label} (HTTP {exc.code} {exc.reason}). "
                         f"Check that this machine can reach {host}, then try again.") from exc
    except (http.client.HTTPException, OSError) as exc:  # URLError and timeouts are OSErrors
        raise _interrupted(item, exc) from exc


def _read(item: PackFile, response) -> bytes:
    try:
        return response.read(CHUNK_BYTES)
    except (http.client.HTTPException, OSError) as exc:
        raise _interrupted(item, exc) from exc


@contextlib.contextmanager
def _folder_lock(folder: Path, item: PackFile) -> Iterator[None]:
    """Let one download at a time write into ``folder``.

    On Linux the installer's background download and the Extensions page can
    both start one, and two writers would spoil the same part file. Windows
    has no fcntl; its installer fetches no packs, and the host agent runs one
    prepare per extension at a time.
    """
    if fcntl is None:
        yield
        return
    handle = os.open(folder, os.O_RDONLY)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AssetError(f"{item.label} is already downloading, started by the installer or an "
                             "earlier add. Wait for it to finish, then try again.") from exc
        except OSError as exc:
            # Some network file systems offer no locks; the download then runs unguarded.
            if exc.errno != errno.ENOLCK:
                raise
        yield
    finally:
        os.close(handle)


def _interrupted(item: PackFile, exc: BaseException) -> AssetError:
    return AssetError(f"The download of {item.label} stopped ({_reason(exc)}). "
                      "Try again to resume it; the part already downloaded is kept.", retryable=True)


def _not_writable(item: PackFile, exc: OSError) -> AssetError:
    folder = item.target.parent
    while not folder.exists():
        folder = folder.parent
    fix = (f"To fix it, run this, then try again: sudo chown -R {os.getuid()}:{os.getgid()} "
           f"{shlex.quote(str(folder))}" if hasattr(os, "getuid")
           else "Give the folder to your account, then try again.")
    return AssetError(f"ODS cannot write {item.label} into {folder} ({_reason(exc)}). The folder belongs "
                      f"to another account, often a container that runs as root. {fix}")


def _remaining(item: PackFile) -> int:
    """Bytes still to download; a part larger than the file starts over."""
    part = item.target.with_name(item.target.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    return item.size - have if have <= item.size else item.size


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None) or exc
    return str(reason) or type(reason).__name__


def _gb(count: int) -> str:
    return f"{count / 1e9:.1f}"


def _fetch_with_attempts(item: PackFile, attempts: int, delay: float) -> None:
    step = [0]

    def report(done: int) -> None:
        percent = done * 100 // item.size
        if percent >= step[0] + 10:
            step[0] = percent - percent % 10
            print(f"  {item.label}: {step[0]}%", flush=True)

    for attempt in range(1, attempts + 1):
        try:
            fetch(item, report)
            return
        except AssetError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            # No raw reason here: the installer summary reads words like
            # "failed" in this log as a failure, even after a later try works.
            print(f"  The connection dropped; resuming in {delay:g} s (try {attempt + 1} of {attempts})",
                  flush=True)
            time.sleep(delay)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch an extension's default model packs.")
    parser.add_argument("ext_dir", type=Path)
    parser.add_argument("install_dir", type=Path)
    parser.add_argument("backend")
    # The installer's unattended download keeps going through dropped
    # connections, as its curl --retry did; each try resumes the part.
    parser.add_argument("--attempts", type=int, default=1, help="tries per file; each resumes the last")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="seconds between tries")
    args = parser.parse_args(argv)
    try:
        todo = missing(default_files(args.ext_dir, args.install_dir, args.backend))
        check_space(todo)
        for item in todo:
            print(f"Downloading {item.label} ({_gb(item.size)} GB) to {item.target}", flush=True)
            _fetch_with_attempts(item, max(1, args.attempts), args.retry_delay)
            print(f"{item.label} is ready (SHA-256 verified)", flush=True)
    except (AssetError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not todo:
        print("All default model packs are already in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())

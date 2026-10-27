#!/usr/bin/env python3
"""Download a vLLM catalog model's pinned Hugging Face files and verify each one.

A config/model-library.json entry with ``"runtime": "vllm"`` pins a Hugging
Face repo at a full commit and lists every file there with its size and its
sha256 (LFS files) or git blob sha1 (small files); see
extensions/services/dashboard-api/model_vllm.py. This helper fetches that
manifest into ``<models-dir>/<owner>--<name>@<revision[:8]>/``, the folder
``.env`` VLLM_MODEL_DIR names, with the Python standard library only:

* each file downloads to ``<name>.partial`` and is renamed into place only
  after its size and digest match, so a file under its final name is always a
  verified one; a ``.partial`` file an interruption left behind resumes with an
  HTTP Range request on the next run;
* files already in place are verified again and replaced when they differ;
* it refuses to start when the volume has less free space than the bytes
  still to download plus the margin model downloads keep (5% of the volume,
  at least 2 GiB, as bin/ods-host-agent.py _download_disk_margin);
* progress lines go to stdout, failures to stderr.

It never retries: an interrupted download keeps its bytes, so run it again to
resume. Exit codes: 0 every file verified; 1 download or catalog error; 3 a
file did not match the catalog (its download was deleted); 4 not enough free
disk.

Usage:
  download-hf-manifest.py --catalog config/model-library.json \\
      --model-id qwen3.8-flash-next-nvfp4-vllm --models-dir data/models
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"))
from model_vllm import (  # noqa: E402
    PARTIAL_SUFFIX,
    is_vllm_entry,
    manifest_bytes,
    model_dir_name,
    require_valid,
)


DEFAULT_BASE_URL = "https://huggingface.co"
CHUNK_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 60
USER_AGENT = "ods-download-hf-manifest/1"
MARGIN_MIN_BYTES = 2 * 1024 ** 3
MARGIN_FRACTION = 0.05
GB = 1000 ** 3
EXIT_FAILED = 1
EXIT_MISMATCH = 3
EXIT_NO_SPACE = 4
HELP_URL = os.environ.get("ODS_HELP_DISCORD_URL") or "https://discord.gg/4ntNp9MAwC"
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)")


class Mismatch(Exception):
    """A complete file whose size or digest differs from the catalog."""


class NoSpace(Exception):
    """The volume cannot hold the rest of the download plus the margin."""


class DownloadStopped(Exception):
    """The server ended a file early or answered unexpectedly; bytes so far stay."""


class HubRefused(Exception):
    """Hugging Face answered a file request with an HTTP error status."""


class Progress:
    """A stdout line each time the whole download passes another percent."""

    def __init__(self, total: int):
        self.total = max(total, 1)
        self.completed = 0
        self.current = 0
        self.shown = -1

    def advance(self, count: int) -> None:
        self.current += count
        self._show()

    def restart_file(self) -> None:
        self.current = 0

    def finish_file(self) -> None:
        self.completed += self.current
        self.current = 0
        self._show()

    def _show(self) -> None:
        done = self.completed + self.current
        percent = done * 100 // self.total
        if percent > self.shown:
            self.shown = percent
            print(f"progress: {percent}% {done / GB:.1f} of {self.total / GB:.1f} GB", flush=True)


def find_entry(catalog: Path, model_id: str) -> dict[str, Any]:
    """The one catalog entry whose id or llm_model_name is ``model_id``; it must be vLLM."""
    models = json.loads(catalog.read_text(encoding="utf-8")).get("models", [])
    matches = [model for model in models if isinstance(model, dict)
               and model_id in {model.get("id"), model.get("llm_model_name")}]
    if len(matches) != 1:
        raise LookupError(f"{model_id!r} names {len(matches)} catalog models, not one")
    if not is_vllm_entry(matches[0]):
        raise LookupError(f"{model_id} is a llama.cpp model; this helper downloads vLLM models only")
    return require_valid(matches[0])


def file_url(base_url: str, model: dict[str, Any], path: str) -> str:
    return f"{base_url.rstrip('/')}/{model['hf_repo']}/resolve/{model['revision']}/{quote(path, safe='/')}"


def new_digest(item: dict[str, Any]) -> Any:
    """sha256 of the content for an LFS file; git's blob sha1 for a small one."""
    if "sha256" in item:
        return hashlib.sha256()
    digest = hashlib.sha1()
    digest.update(b"blob %d\0" % item["size"])
    return digest


def expected_digest(item: dict[str, Any]) -> str:
    return item["sha256"] if "sha256" in item else item["git_blob_sha1"]


def hash_into(digest: Any, path: Path, progress: Progress) -> None:
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
            progress.advance(len(chunk))


def verified_in_place(final: Path, item: dict[str, Any], progress: Progress) -> bool:
    """True when ``final`` already holds exactly the catalog file."""
    if not final.is_file() or final.stat().st_size != item["size"]:
        return False
    digest = new_digest(item)
    hash_into(digest, final, progress)
    if digest.hexdigest() == expected_digest(item):
        return True
    progress.restart_file()
    return False


def _check_content_range(response: Any, offset: int, size: int) -> None:
    match = _CONTENT_RANGE.fullmatch(str(response.headers.get("Content-Range") or ""))
    if not match or int(match.group(1)) != offset or match.group(3) not in {str(size), "*"}:
        raise DownloadStopped(
            f"the server resumed at {response.headers.get('Content-Range')!r}, not at byte {offset} of {size}"
        )


def fetch(url: str, partial: Path, offset: int, item: dict[str, Any], digest: Any,
          progress: Progress) -> Any:
    """Append bytes ``offset``.. of ``url`` to ``partial``; return the running digest."""
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        # Report the resolve URL, not the signed CDN URL a redirect led to.
        raise HubRefused(f"Hugging Face answered HTTP {exc.code} for {url}") from exc
    with response:
        if offset and response.status == 206:
            _check_content_range(response, offset, item["size"])
        elif response.status == 200:
            if offset:
                # The server ignored the Range header: start the file again.
                offset, digest = 0, new_digest(item)
                progress.restart_file()
        else:
            raise DownloadStopped(f"unexpected HTTP {response.status} for {item['path']}")
        remaining = item["size"] - offset
        with partial.open("ab" if offset else "wb") as handle:
            while remaining > 0:
                chunk = response.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                progress.advance(len(chunk))
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    if remaining > 0:
        raise DownloadStopped(f"the server ended {item['path']} {remaining} bytes early")
    return digest


def download(url: str, partial: Path, item: dict[str, Any], progress: Progress) -> None:
    """Complete ``partial`` (resuming what is there) and verify it, or delete it."""
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > item["size"]:
        partial.unlink()
        offset = 0
    digest = new_digest(item)
    if offset:
        hash_into(digest, partial, progress)
    if offset < item["size"] or not partial.exists():
        digest = fetch(url, partial, offset, item, digest, progress)
    actual = digest.hexdigest()
    if partial.stat().st_size != item["size"] or actual != expected_digest(item):
        partial.unlink()
        kind = "sha256" if "sha256" in item else "git blob sha1"
        raise Mismatch(
            f"{item['path']} does not match the catalog ({kind} {actual}, expected "
            f"{expected_digest(item)}), so its download was deleted. Run this again to fetch it again."
        )


def bytes_to_download(target: Path, files: list[dict[str, Any]]) -> int:
    """Bytes still to fetch; a file in place at the right size counts as done."""
    needed = 0
    for item in files:
        final = target / item["path"]
        if final.is_file() and final.stat().st_size == item["size"]:
            continue
        partial = final.with_name(final.name + PARTIAL_SUFFIX)
        have = partial.stat().st_size if partial.is_file() else 0
        needed += item["size"] - have if have <= item["size"] else item["size"]
    return needed


def check_space(target: Path, files: list[dict[str, Any]]) -> None:
    needed = bytes_to_download(target, files)
    if not needed:
        return
    usage = shutil.disk_usage(target)
    margin = max(MARGIN_MIN_BYTES, int(usage.total * MARGIN_FRACTION))
    if usage.free < needed + margin:
        raise NoSpace(
            f"{target} has {usage.free / GB:.1f} GB free, but this model needs {needed / GB:.1f} GB more "
            f"plus a {margin / GB:.1f} GB safety margin. Free up space or choose a smaller model, "
            f"then run this again."
        )


def _safe_destination(final: Path) -> Path:
    partial = final.with_name(final.name + PARTIAL_SUFFIX)
    for path in (final, partial):
        if path.is_symlink():
            raise DownloadStopped(f"refusing to write through the symbolic link {path}")
    return partial


def fetch_manifest(model: dict[str, Any], models_dir: Path, base_url: str) -> Path:
    """Download and verify every manifest file; return the model folder."""
    target = models_dir / model_dir_name(model)
    target.mkdir(parents=True, exist_ok=True)
    files = model["files"]
    check_space(target, files)
    print(f"plan: {model['hf_repo']}@{model['revision'][:8]}, {len(files)} files, "
          f"{manifest_bytes(model) / GB:.1f} GB, into {target}", flush=True)
    progress = Progress(manifest_bytes(model))
    for index, item in enumerate(files, 1):
        final = target / item["path"]
        final.parent.mkdir(parents=True, exist_ok=True)
        partial = _safe_destination(final)
        if verified_in_place(final, item, progress):
            progress.finish_file()
            print(f"verified [{index}/{len(files)}] {item['path']} (already downloaded)", flush=True)
            continue
        if final.exists():
            print(f"replacing {item['path']}: it does not match the catalog", flush=True)
            final.unlink()
        download(file_url(base_url, model, item["path"]), partial, item, progress)
        os.replace(partial, final)
        progress.finish_file()
        print(f"verified [{index}/{len(files)}] {item['path']}", flush=True)
    print(f"done: {len(files)} files verified in {target}", flush=True)
    return target


def _fail(message: str, code: int) -> int:
    print(f"error: {message}\nNeed help? Ask on the ODS Discord: {HELP_URL}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--model-id", required=True,
                        help="id or llm_model_name of a runtime: vllm catalog entry (select-model.py's LLM_MODEL)")
    parser.add_argument("--models-dir", required=True, type=Path,
                        help="data/models; files land in <models-dir>/<VLLM_MODEL_DIR>/")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="Hugging Face endpoint (tests point it at a local server)")
    args = parser.parse_args(argv)
    try:
        model = find_entry(args.catalog, args.model_id)
    except (OSError, ValueError, LookupError) as exc:
        return _fail(f"cannot download {args.model_id} from {args.catalog}: {exc}", EXIT_FAILED)
    try:
        fetch_manifest(model, args.models_dir, args.base_url)
    except NoSpace as exc:
        return _fail(str(exc), EXIT_NO_SPACE)
    except Mismatch as exc:
        return _fail(str(exc), EXIT_MISMATCH)
    except HubRefused as exc:
        return _fail(f"{exc}. Files verified so far are kept.", EXIT_FAILED)
    except (DownloadStopped, urllib.error.URLError, HTTPException, ConnectionError, TimeoutError,
            socket.timeout) as exc:
        return _fail(f"the download stopped: {exc}. Its bytes are kept: run this again to resume.", EXIT_FAILED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

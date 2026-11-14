#!/usr/bin/env python3
"""Download a Hugging Face artifact from a /resolve/ URL.

Plain curl can fail for Xet-backed Hugging Face files after the resolver
redirects to cas-bridge.xethub.hf.co. The huggingface_hub client knows how to
use the xet read-token flow, so installers use this as a fallback after curl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def parse_huggingface_resolve_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"huggingface.co", "www.huggingface.co", "hf.co"}:
        raise ValueError("URL is not a Hugging Face URL")

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[2] != "resolve":
        raise ValueError("URL is not a Hugging Face /resolve/ artifact URL")

    repo_id = f"{parts[0]}/{parts[1]}"
    revision = parts[3]
    filename = "/".join(parts[4:])
    if not filename:
        raise ValueError("Hugging Face artifact filename is empty")
    return repo_id, revision, filename


def download_artifact(url: str, destination: Path) -> Path:
    repo_id, revision, filename = parse_huggingface_resolve_url(url)
    if any(part in {"", ".", ".."} for part in filename.split("/")) or any(
        char in filename for char in "\\:"
    ):
        raise ValueError("Hugging Face artifact filename is not a safe relative path")
    try:
        from filelock import FileLock
        from huggingface_hub import get_hf_file_metadata, hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed; install with: "
            "python -m pip install 'huggingface_hub[hf_xet]>=0.27'"
        ) from exc

    # Resolve moving branches before selecting resumable storage. The SDK also
    # keys its incomplete files by ETag; bytes from another revision are not reused.
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        revision = get_hf_file_metadata(url).commit_hash or ""
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        raise RuntimeError("Hugging Face artifact has no immutable commit identity")
    revision = revision.lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    identity = json.dumps(
        {"schema": 1, "repo_id": repo_id, "revision": revision,
         "filename": filename, "destination": destination.name},
        sort_keys=True,
    ).encode("utf-8")
    target_identity = json.dumps(
        {"schema": 1, "destination": os.path.normcase(destination.name)}, sort_keys=True
    ).encode("utf-8")
    custody = destination.parent / f".ods-hf-{hashlib.sha256(target_identity).hexdigest()}"
    _owned_directory(custody, target_identity)
    _check_plain_tree(custody)

    # Retain the small owner directory and use the same cross-process lock path.
    # A failed download keeps SDK resume state; it never consumes curl's partial.
    # The lock covers every revision targeting this destination, not just retries
    # of one artifact. Different artifact identities retain separate SDK state.
    with FileLock(str(custody / "download.lock"), timeout=0):
        _check_plain_tree(custody)
        stage = custody / hashlib.sha256(identity).hexdigest()
        _owned_directory(stage, identity)
        payload = stage / "payload"
        payload.mkdir(exist_ok=True)
        downloaded = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=str(payload),
            )
        )
        _check_plain_tree(stage)
        expected = payload / filename
        if not expected.is_file() or not os.path.samefile(downloaded, expected):
            raise RuntimeError("downloaded artifact is outside owned staging")
        if expected.stat().st_size <= 0:
            expected.unlink()
            raise RuntimeError("downloaded artifact is empty")
        expected.replace(destination)
        # Only this immutable artifact's SDK state is removed, and only after
        # publication. Other stages and the shared Hub cache remain untouched.
        try:
            shutil.rmtree(payload)
        except OSError as exc:
            print(
                f"WARNING: Artifact published; SDK staging cleanup pending at {payload} "
                f"({type(exc).__name__})",
                file=sys.stderr,
            )
    return destination


def _owned_directory(directory: Path, identity: bytes) -> None:
    marker = directory / "owner.json"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        _check_plain_path(directory, directory=True)
        _check_plain_path(marker, directory=False)
        if marker.stat().st_size != len(identity) or marker.read_bytes() != identity:
            raise RuntimeError("Hugging Face staging ownership does not match")
    else:
        with marker.open("xb") as stream:
            stream.write(identity)


def _check_plain_path(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    )
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if reparse or not valid_type or (not directory and info.st_nlink != 1):
        raise RuntimeError("Hugging Face staging contains an unsafe filesystem entry")


def _check_plain_tree(stage: Path) -> None:
    _check_plain_path(stage, directory=True)
    for directory, subdirs, files in os.walk(stage, followlinks=False):
        for name in subdirs:
            _check_plain_path(Path(directory) / name, directory=True)
        for name in files:
            _check_plain_path(Path(directory) / name, directory=False)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Download a Hugging Face /resolve/ artifact with huggingface_hub."
    )
    parser.add_argument("url")
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--print-metadata",
        action="store_true",
        help="Only parse the URL and print repo/revision/filename metadata.",
    )
    args = parser.parse_args(argv)

    try:
        repo_id, revision, filename = parse_huggingface_resolve_url(args.url)
        if args.print_metadata:
            print(f"repo_id={repo_id}")
            print(f"revision={revision}")
            print(f"filename={filename}")
            return 0
        result = download_artifact(args.url, args.destination)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Downloaded {filename} to {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

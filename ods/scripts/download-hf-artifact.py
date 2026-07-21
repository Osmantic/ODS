#!/usr/bin/env python3
"""Download a Hugging Face artifact from a /resolve/ URL.

Plain curl can fail for Xet-backed Hugging Face files after the resolver
redirects to cas-bridge.xethub.hf.co. The huggingface_hub client knows how to
use the xet read-token flow, so installers use this as a fallback after curl.
"""

from __future__ import annotations

import argparse
import shutil
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
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed; install with: "
            "python -m pip install 'huggingface_hub[hf_xet]>=0.27'"
        ) from exc

    downloaded = Path(
        hf_hub_download(repo_id=repo_id, filename=filename, revision=revision)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_destination = destination.with_name(f"{destination.name}.hf-tmp")
    shutil.copyfile(downloaded, tmp_destination)
    if tmp_destination.stat().st_size <= 0:
        tmp_destination.unlink(missing_ok=True)
        raise RuntimeError("downloaded artifact is empty")
    tmp_destination.replace(destination)
    return destination


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

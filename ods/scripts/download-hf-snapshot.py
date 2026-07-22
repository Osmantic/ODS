#!/usr/bin/env python3
"""Download a Hugging Face model snapshot into a chosen cache directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def download_snapshot(
    repo_id: str,
    cache_dir: Path,
    *,
    revision: str | None = None,
    allow_patterns: list[str] | None = None,
) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed; install with: "
            "python -m pip install 'huggingface_hub[hf_xet]>=0.27'"
        ) from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "repo_id": repo_id,
        "cache_dir": str(cache_dir),
    }
    if revision:
        kwargs["revision"] = revision
    if allow_patterns:
        kwargs["allow_patterns"] = allow_patterns

    snapshot_path = Path(snapshot_download(**kwargs))
    if not snapshot_path.exists():
        raise RuntimeError(f"snapshot path was not created: {snapshot_path}")
    return snapshot_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Download a Hugging Face snapshot with huggingface_hub."
    )
    parser.add_argument("repo_id")
    parser.add_argument("cache_dir", type=Path)
    parser.add_argument("--revision", default=None)
    parser.add_argument(
        "--allow-pattern",
        action="append",
        dest="allow_patterns",
        default=None,
        help="Optional snapshot allow pattern. May be passed more than once.",
    )
    args = parser.parse_args(argv)

    try:
        result = download_snapshot(
            args.repo_id,
            args.cache_dir,
            revision=args.revision,
            allow_patterns=args.allow_patterns,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Downloaded {args.repo_id} snapshot to {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

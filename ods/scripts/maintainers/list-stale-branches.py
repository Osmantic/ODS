#!/usr/bin/env python3
"""List remote branches that may be safe to clean up.

This helper is intentionally dry-run only. It never deletes branches. It uses
local git remote refs, and when the GitHub CLI is available it excludes branches
that currently back open pull requests.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EXCLUDE_EXACT = {
    "origin/HEAD",
    "origin/main",
    "origin/master",
    "origin/develop",
}

DEFAULT_EXCLUDE_PREFIXES = (
    "origin/release/",
    "origin/support/",
)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def repo_root() -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise SystemExit("error: not inside a git repository")
    return Path(result.stdout.strip())


def open_pr_heads() -> set[str]:
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "500",
            "--json",
            "headRefName",
        ]
    )
    if result.returncode != 0:
        return set()
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()
    return {row.get("headRefName", "") for row in rows if row.get("headRefName")}


def remote_branches() -> list[tuple[datetime, str, str]]:
    fmt = "%(committerdate:iso8601-strict)%09%(refname:short)%09%(objectname:short)"
    result = run(["git", "for-each-ref", f"--format={fmt}", "refs/remotes/origin"])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "error: unable to list remote refs")

    branches: list[tuple[datetime, str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        date_s, ref, sha = line.split("\t", 2)
        date = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
        branches.append((date, ref, sha))
    return branches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=45, help="stale age threshold")
    parser.add_argument(
        "--include-open-prs",
        action="store_true",
        help="do not exclude branches that back open PRs",
    )
    args = parser.parse_args()

    root = repo_root()
    heads = set() if args.include_open_prs else open_pr_heads()
    now = datetime.now(timezone.utc)
    cutoff_days = args.days

    print(f"Repository: {root}")
    print(f"Stale threshold: {cutoff_days} days")
    if heads:
        print(f"Excluding {len(heads)} open PR branch(es)")
    elif not args.include_open_prs:
        print("Open PR branch exclusion unavailable or empty")
    print()

    candidates: list[tuple[int, str, str, str]] = []
    for date, ref, sha in remote_branches():
        if ref in DEFAULT_EXCLUDE_EXACT or ref.startswith(DEFAULT_EXCLUDE_PREFIXES):
            continue
        short = ref.removeprefix("origin/")
        if short in heads:
            continue
        age_days = (now - date.astimezone(timezone.utc)).days
        if age_days < cutoff_days:
            continue
        candidates.append((age_days, ref, sha, date.date().isoformat()))

    if not candidates:
        print("No stale branch candidates found.")
        return 0

    print("Dry-run candidates. Review before deleting anything:")
    for age_days, ref, sha, date_s in sorted(candidates, reverse=True):
        print(f"{age_days:4d}d  {date_s}  {sha}  {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

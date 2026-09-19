#!/usr/bin/env python3
"""List remote branches that may be safe to clean up.

This helper is intentionally dry-run only. It never deletes branches. It uses
local git remote refs and excludes branches that currently back open pull requests.
If that inventory cannot be verified, it stops before listing candidates.
Use --include-open-prs explicitly to inspect branches without that protection.
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
    "origin/public-beta",
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
    error = "error: cannot verify open PR exclusions; restore gh access or explicitly use --include-open-prs"
    limit = 10000
    try:
        result = run(["gh", "pr", "list", "--state", "open", "--limit", str(limit), "--json", "headRefName"])
    except FileNotFoundError as exc:
        raise SystemExit(error) from exc
    if result.returncode != 0:
        raise SystemExit(error)
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(error) from exc
    if not isinstance(rows, list) or len(rows) >= limit:
        raise SystemExit(error)
    if any(not isinstance(row, dict) or not isinstance(row.get("headRefName"), str) or not row["headRefName"] for row in rows):
        raise SystemExit(error)
    return {row["headRefName"] for row in rows}


def default_remote_branch() -> str | None:
    result = run(["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if result.returncode == 1:  # origin/HEAD is not configured in this checkout.
        return None
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "error: unable to read origin/HEAD")
    return result.stdout.strip().removeprefix("refs/remotes/")


def remote_branches() -> list[tuple[datetime, str, str]]:
    fmt = "%(committerdate:iso8601-strict)%09%(refname)%09%(objectname:short)"
    result = run(["git", "for-each-ref", f"--format={fmt}", "refs/remotes/origin"])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "error: unable to list remote refs")

    branches: list[tuple[datetime, str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        date_s, ref, sha = line.split("\t", 2)
        date = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
        branches.append((date, ref.removeprefix("refs/remotes/"), sha))
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
    protected_refs = DEFAULT_EXCLUDE_EXACT | {default_remote_branch()}

    print(f"Repository: {root}")
    print(f"Stale threshold: {cutoff_days} days")
    if heads:
        print(f"Excluding {len(heads)} open PR branch(es)")
    elif not args.include_open_prs:
        print("Verified: no open PR branches")
    print()

    candidates: list[tuple[int, str, str, str]] = []
    for date, ref, sha in remote_branches():
        if ref in protected_refs or ref.startswith(DEFAULT_EXCLUDE_PREFIXES):
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

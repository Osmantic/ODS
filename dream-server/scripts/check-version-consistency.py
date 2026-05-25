#!/usr/bin/env python3
"""Fail the release gate when Dream Server version authorities drift."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def first_match(path: Path, pattern: str, label: str) -> str:
    match = re.search(pattern, read_text(path), re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"{label}: could not find version in {path.relative_to(ROOT)}")
    return match.group(1)


def latest_changelog_release() -> tuple[str, str]:
    changelog = read_text(ROOT / "CHANGELOG.md")
    for match in re.finditer(r"^## \[([^\]]+)\](?: - ([0-9]{4}-[0-9]{2}-[0-9]{2}))?", changelog, re.MULTILINE):
        version, date = match.group(1), match.group(2) or ""
        if version.lower() != "unreleased":
            return version, date
    raise ValueError("CHANGELOG.md: could not find a released version heading")


def optional_version_file() -> str | None:
    path = ROOT / ".version"
    if not path.exists():
        return None
    raw = read_text(path).strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict) and data.get("version"):
        return str(data["version"])
    return raw


def add_regex_check(
    checks: list[tuple[str, str]],
    errors: list[str],
    label: str,
    path: Path,
    pattern: str,
) -> None:
    try:
        checks.append((label, first_match(path, pattern, label)))
    except ValueError as exc:
        errors.append(str(exc))


def main() -> int:
    errors: list[str] = []
    manifest_path = ROOT / "manifest.json"

    try:
        manifest = json.loads(read_text(manifest_path))
        expected = str(manifest.get("dream_version", "")).strip()
        release = manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
        release_version = str(release.get("version", "")).strip()
        release_date = str(release.get("date", "")).strip()
    except Exception as exc:  # noqa: BLE001 - gate should report cleanly
        print(f"[FAIL] version consistency: cannot read manifest.json: {exc}")
        return 1

    if not expected:
        errors.append("manifest.json dream_version is empty")
    elif not SEMVER.match(expected):
        errors.append(f"manifest.json dream_version must be x.y.z, got {expected!r}")

    checks = [("manifest.json release.version", release_version)]
    add_regex_check(
        checks,
        errors,
        "extensions/services/dashboard-api/main.py FastAPI version",
        ROOT / "extensions/services/dashboard-api/main.py",
        r"app\s*=\s*FastAPI\([^)]*?version\s*=\s*[\"']([^\"']+)[\"']",
    )
    add_regex_check(
        checks,
        errors,
        "installers/lib/constants.sh VERSION",
        ROOT / "installers/lib/constants.sh",
        r'^VERSION="([^"]+)"',
    )
    add_regex_check(
        checks,
        errors,
        "installers/macos/lib/constants.sh DS_VERSION",
        ROOT / "installers/macos/lib/constants.sh",
        r'^DS_VERSION="([^"]+)"',
    )
    add_regex_check(
        checks,
        errors,
        "ARCHITECTURE.md version",
        ROOT.parent / "ARCHITECTURE.md",
        r"^> Version ([0-9]+\.[0-9]+\.[0-9]+)\s+\|",
    )

    version_file = optional_version_file()
    if version_file is not None:
        checks.append((".version version", version_file))

    try:
        changelog_version, changelog_date = latest_changelog_release()
        checks.append(("CHANGELOG.md latest release", changelog_version))
        if release_date and changelog_date and release_date != changelog_date:
            errors.append(
                f"manifest.json release.date {release_date!r} != CHANGELOG.md latest release date {changelog_date!r}"
            )
    except ValueError as exc:
        errors.append(str(exc))

    for label, value in checks:
        if value != expected:
            errors.append(f"{label} {value!r} != manifest.json dream_version {expected!r}")

    if errors:
        print("[FAIL] version consistency")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[PASS] version consistency ({expected})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

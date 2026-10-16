#!/usr/bin/env python3
"""Check or rewrite the build-context pins of shipped extensions.

dashboard-api only enables a shipped extension that builds its own image
when that extension's folder matches the digest pinned in
extensions/services/dashboard-api/builtin_source_recipes.py. The digest is
taken over the files git tracks there, so runtime leftovers such as
__pycache__ never end up in a pin.

usage: python3 scripts/pin-builtin-build-contexts.py [--write]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ODS = Path(__file__).resolve().parents[1]
MODULE = ODS / "extensions/services/dashboard-api/builtin_source_recipes.py"
sys.path.insert(0, str(MODULE.parent))

import builtin_source_recipes as recipes  # noqa: E402


def tracked_files(folder: str) -> list[tuple[str, bytes]]:
    package = ODS / "extensions/services" / folder
    listed = subprocess.run(["git", "-C", str(package), "ls-files", "-z", "--", "."],
                            check=True, capture_output=True).stdout.decode("utf-8").split("\0")
    files = []
    for relative in filter(None, listed):
        if "/" not in relative and recipes._COMPOSE_FILE.fullmatch(relative):
            continue
        files.append((relative, (package / relative).read_bytes()))
    return files


def current_pins() -> dict[str, str]:
    return {service: recipes.files_digest(tracked_files(folder))
            for service, (folder, _image, _dockerfile, _digest) in recipes._CONTEXT_BUILDS.items()}


def main() -> int:
    write = sys.argv[1:] == ["--write"]
    if sys.argv[1:] not in ([], ["--write"]):
        print(__doc__, file=sys.stderr)
        return 2
    source = MODULE.read_text(encoding="utf-8")
    stale = 0
    for service, digest in current_pins().items():
        pinned = recipes._CONTEXT_BUILDS[service][3]
        if pinned == digest:
            continue
        stale += 1
        print(f"{service}: pinned {pinned}, folder now {digest}")
        pattern = re.compile(r'(\n    "%s": \([^\n]*"Dockerfile", ")[^"]*(")' % re.escape(service))
        source, count = pattern.subn(lambda m: m.group(1) + digest + m.group(2), source)
        if count != 1:
            raise SystemExit(f"could not find the pin line for {service}")
    if stale and write:
        MODULE.write_text(source, encoding="utf-8", newline="\n")
        print(f"rewrote {stale} pin(s) in {MODULE.relative_to(ODS)}")
    elif stale:
        print("run with --write to update the pins")
    else:
        print("all build-context pins match")
    return 1 if stale and not write else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""GGUF artifact checksum parity across platforms and model profiles.

The same model artifact is declared in several places: the Linux, macOS and
Windows tier maps, and the bootstrap fast-start constants. An artifact that is
pinned in one of them and left empty in another downloads unverified on
whichever path reads the empty one — verify_sha256() logs "No SHA256 hash
provided, skipping verification" and returns success.

This asserts that every declaration of one artifact URL agrees on its checksum.

Run: python3 tests/test-gguf-checksum-parity.py
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# Each entry: (path, regex with named groups `url` and `sha`).
# Bash tier maps and bootstrap constants both use KEY="value" lines; the
# PowerShell tier map uses `Key = "value"`.
SOURCES = [
    (ROOT / "installers" / "lib" / "tier-map.sh",
     r'GGUF_URL="(?P<url>[^"]+)"(?P<gap>.{0,400}?)GGUF_SHA256="(?P<sha>[^"]*)"'),
    (ROOT / "installers" / "lib" / "bootstrap-model.sh",
     r'BOOTSTRAP_GGUF_URL="(?P<url>[^"]+)"(?P<gap>.{0,400}?)BOOTSTRAP_GGUF_SHA256="(?P<sha>[^"]*)"'),
    (ROOT / "installers" / "macos" / "lib" / "tier-map.sh",
     r'GGUF_URL="(?P<url>[^"]+)"(?P<gap>.{0,400}?)GGUF_SHA256="(?P<sha>[^"]*)"'),
    (ROOT / "installers" / "macos" / "lib" / "tier-map.sh",
     r'BOOTSTRAP_GGUF_URL="(?P<url>[^"]+)"(?P<gap>.{0,400}?)BOOTSTRAP_GGUF_SHA256="(?P<sha>[^"]*)"'),
    (ROOT / "installers" / "windows" / "lib" / "tier-map.ps1",
     r'GgufUrl\s*=\s*"(?P<url>[^"]+)"(?P<gap>.{0,400}?)GgufSha256\s*=\s*"(?P<sha>[^"]*)"'),
    (ROOT / "installers" / "windows" / "lib" / "tier-map.ps1",
     r'BOOTSTRAP_GGUF_URL\s*=\s*"(?P<url>[^"]+)"(?P<gap>.{0,400}?)BOOTSTRAP_GGUF_SHA256\s*=\s*"(?P<sha>[^"]*)"'),
]

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")
        if detail:
            for line in detail.splitlines():
                print(f"       {line}")


def collect() -> dict[str, list[tuple[str, str]]]:
    """Map artifact URL -> [(sha, where), ...] across every declaration."""
    found: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for path, pattern in SOURCES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        for match in re.finditer(pattern, text, re.S):
            # A "gap" containing another URL means the two halves belong to
            # different tier blocks; skip rather than pair them up wrongly.
            if "GGUF_URL" in match.group("gap") or "GgufUrl" in match.group("gap"):
                continue
            found[match.group("url")].append((match.group("sha"), rel))
    return found


def main() -> int:
    declarations = collect()

    check(
        "GGUF declarations were found to compare",
        len(declarations) > 0,
        "the extraction patterns matched nothing — has a tier map moved?",
    )

    for url in sorted(declarations):
        artifact = url.rsplit("/", 1)[-1]
        entries = declarations[url]
        shas = {sha for sha, _ in entries}

        check(
            f"{artifact}: every declaration agrees on a checksum",
            len(shas) == 1,
            "\n".join(f"sha={sha or '<EMPTY>'}  {where}" for sha, where in sorted(entries)),
        )

        for sha, where in sorted(entries):
            if not sha:
                continue
            check(
                f"{artifact}: {where} declares a well-formed SHA256",
                bool(SHA256_RE.match(sha)),
                f"got {sha!r}",
            )

    # The bootstrap artifact is downloaded on every fast-start install; it must
    # never be the unpinned one.
    bootstrap = [
        (url, entries)
        for url, entries in declarations.items()
        if url.endswith("Qwen3.5-2B-Q4_K_M.gguf")
    ]
    check(
        "the bootstrap artifact is declared somewhere",
        len(bootstrap) == 1,
        repr([u for u, _ in bootstrap]),
    )
    for _url, entries in bootstrap:
        check(
            "the bootstrap artifact is pinned everywhere it is declared",
            all(sha for sha, _ in entries),
            "\n".join(f"sha={sha or '<EMPTY>'}  {where}" for sha, where in sorted(entries)),
        )

    print()
    print(f"Passed: {PASS}  Failed: {FAIL}")
    if FAIL:
        return 1
    print("[PASS] GGUF checksum parity")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Keep Gemma 4 install artifacts immutable across every platform fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"
TIER_MAPS = (
    ROOT / "installers" / "lib" / "tier-map.sh",
    ROOT / "installers" / "macos" / "lib" / "tier-map.sh",
    ROOT / "installers" / "windows" / "lib" / "tier-map.ps1",
)

EXPECTED = {
    "gemma-4-E2B-it-Q4_K_M.gguf": {
        "id": "gemma4-e2b-q4",
        "url": (
            "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/"
            "resolve/0314792d7f1f7e229411f620751375812bb9faf2/"
            "gemma-4-E2B-it-Q4_K_M.gguf"
        ),
        "sha256": "740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8",
        "size_bytes": 3106738272,
        "size_mb": 2963,
    },
    "gemma-4-E4B-it-Q4_K_M.gguf": {
        "id": "gemma4-e4b-q4",
        "url": (
            "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/"
            "resolve/bfc15c382204943c3a8fff0c750b94ae2364d7a3/"
            "gemma-4-E4B-it-Q4_K_M.gguf"
        ),
        "sha256": "85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87",
        "size_bytes": 4977171584,
        "size_mb": 4747,
    },
}

TIER_ARTIFACT = re.compile(
    r'(?:GGUF_FILE|GgufFile)\s*=\s*"(?P<file>[^"]+)"\s*'
    r'(?:GGUF_URL|GgufUrl)\s*=\s*"(?P<url>[^"]+)"\s*'
    r'(?:GGUF_SHA256|GgufSha256)\s*=\s*"(?P<sha256>[^"]*)"'
)


def test_catalog_pins() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    for expected in EXPECTED.values():
        model = by_id[expected["id"]]
        assert model["gguf_url"] == expected["url"]
        assert model["gguf_sha256"] == expected["sha256"]
        assert model["size_bytes"] == expected["size_bytes"]
        assert model["size_mb"] == expected["size_mb"]


def test_platform_fallback_pins() -> None:
    for tier_map in TIER_MAPS:
        matches = [match.groupdict() for match in TIER_ARTIFACT.finditer(
            tier_map.read_text(encoding="utf-8")
        )]
        for filename, expected in EXPECTED.items():
            artifact_matches = [item for item in matches if item["file"] == filename]
            assert artifact_matches, f"{tier_map}: missing {filename}"
            for artifact in artifact_matches:
                assert artifact["url"] == expected["url"], tier_map
                assert artifact["sha256"] == expected["sha256"], tier_map


def main() -> int:
    test_catalog_pins()
    test_platform_fallback_pins()
    print("[PASS] Gemma 4 artifact revisions and checksums are pinned across platforms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Keep Gemma 4 install artifacts immutable across every platform fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
    "google_gemma-4-26B-A4B-it-Q4_K_M.gguf": {
        "id": "gemma4-26b-a4b-q4",
        "url": (
            "https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF/"
            "resolve/10f3b41bcf8d3047f4e136e7197ffc2dd1654c9d/"
            "google_gemma-4-26B-A4B-it-Q4_K_M.gguf"
        ),
        "sha256": "a07f72221e8e3f77455ab0d7f7652d01a9f63c262b954aa6932a53275a0e895a",
        "size_bytes": 17035039872,
        "size_mb": 17035,
        "source_repo": "bartowski/google_gemma-4-26B-A4B-it-GGUF",
        "source_revision": "10f3b41bcf8d3047f4e136e7197ffc2dd1654c9d",
    },
    "gemma-4-31B-it-Q4_K_M.gguf": {
        "id": "gemma4-31b-q4",
        "url": (
            "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/"
            "resolve/c1ac76e99d5513b141e8adde7288b85c3f9c32ec/"
            "gemma-4-31B-it-Q4_K_M.gguf"
        ),
        "sha256": "38bd64c852c4b460434cc7162fa9bdcf242faf86502581a754cb72956bb17f84",
        "size_bytes": 18323733440,
        "size_mb": 18324,
        "source_repo": "unsloth/gemma-4-31B-it-GGUF",
        "source_revision": "c1ac76e99d5513b141e8adde7288b85c3f9c32ec",
    },
}

RESOLVED_TIERS = {
    "3": "google_gemma-4-26B-A4B-it-Q4_K_M.gguf",
    "4": "gemma-4-31B-it-Q4_K_M.gguf",
    "SH_COMPACT": "google_gemma-4-26B-A4B-it-Q4_K_M.gguf",
    "SH_LARGE": "gemma-4-31B-it-Q4_K_M.gguf",
    "NV_ULTRA": "gemma-4-31B-it-Q4_K_M.gguf",
}

TIER_ARTIFACT = re.compile(
    r'(?:GGUF_FILE|GgufFile)\s*=\s*"(?P<file>[^"]+)"\s*'
    r'(?:GGUF_URL|GgufUrl)\s*=\s*"(?P<url>[^"]+)"\s*'
    r'(?:GGUF_SHA256|GgufSha256)\s*=\s*"(?P<sha256>[^"]*)"'
)


def test_catalog_pins() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in catalog["models"]}
    for filename, expected in EXPECTED.items():
        model = by_id[expected["id"]]
        assert model["gguf_file"] == filename
        assert model["gguf_url"] == expected["url"]
        assert model["gguf_sha256"] == expected["sha256"]
        assert model["size_bytes"] == expected["size_bytes"]
        assert model["size_mb"] == expected["size_mb"]
        for key in ("source_repo", "source_revision"):
            if key in expected:
                assert model[key] == expected[key]


def test_repaired_artifact_evidence() -> None:
    evidence = json.loads((ROOT / "docs/MODEL_ARTIFACT_REPAIRS.json").read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in evidence["entries"]}
    for filename, expected in EXPECTED.items():
        if "source_repo" not in expected:
            continue  # Earlier E2B/E4B pins retain their existing independent evidence.
        entry = by_id[expected["id"]]
        artifact = entry["verified_download"]
        assert artifact["gguf_file"] == filename
        assert artifact["gguf_url"] == expected["url"]
        assert artifact["gguf_sha256"] == expected["sha256"]
        for key in ("source_repo", "source_revision", "size_bytes"):
            assert artifact[key] == expected[key]
        pointer = entry["artifact_observation"]["lfs_pointer"]
        assert f'oid sha256:{expected["sha256"]}\n' in pointer
        assert f'size {expected["size_bytes"]}\n' in pointer


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


def test_real_posix_tier_resolution() -> None:
    if os.name == "nt":
        return  # Windows runs the actual PowerShell resolver below.
    bash = shutil.which("bash")
    assert bash, "Bash is required to exercise the Linux/macOS tier functions"
    script = r'''
set -euo pipefail
source "$1"
MODEL_PROFILE=gemma4
TIER="$2"
error() { printf '%s\n' "$*" >&2; return 1; }
ai_err() { error "$@"; }
if [[ "$3" == macos ]]; then
    resolve_tier_config "$TIER"
else
    resolve_tier_config
fi
printf '%s\0' "$MODEL_PROFILE_EFFECTIVE" "$GGUF_FILE" "$GGUF_URL" "$GGUF_SHA256" "${LLM_MODEL_SIZE_MB:-}"
'''
    for tier_map, platform in ((TIER_MAPS[0], "linux"), (TIER_MAPS[1], "macos")):
        tiers = ("3", "4") if platform == "macos" else RESOLVED_TIERS
        for tier in tiers:
            filename = RESOLVED_TIERS[tier]
            expected = EXPECTED[filename]
            result = subprocess.run([bash, "--noprofile", "--norc", "-c", script,
                                     "gemma-artifact-test", str(tier_map), tier, platform],
                                    text=True, capture_output=True, check=True, timeout=10)
            actual = result.stdout.split("\0")
            assert actual[:4] == ["gemma4", filename, expected["url"], expected["sha256"]], (platform, tier, actual)
            if platform == "linux":
                assert int(actual[4]) == expected["size_mb"], (platform, tier, actual)


def test_real_windows_tier_resolution() -> None:
    if os.name != "nt":
        return  # POSIX runs the actual Bash resolvers above.
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell, "PowerShell is required to exercise the Windows tier function"
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:ODS_GEMMA_TEST_MAP
$config = Resolve-TierConfig -Tier $env:ODS_GEMMA_TEST_TIER -ModelProfile 'gemma4'
@($config.ModelProfileEffective, $config.GgufFile, $config.GgufUrl, $config.GgufSha256) | ConvertTo-Json -Compress
'''
    environment = os.environ.copy()
    environment["ODS_GEMMA_TEST_MAP"] = str(TIER_MAPS[2])
    for tier, filename in RESOLVED_TIERS.items():
        environment["ODS_GEMMA_TEST_TIER"] = tier
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                env=environment, text=True, capture_output=True, check=True, timeout=15)
        expected = EXPECTED[filename]
        assert json.loads(result.stdout) == ["gemma4", filename, expected["url"], expected["sha256"]], (tier, result.stdout)


def main() -> int:
    test_catalog_pins()
    test_repaired_artifact_evidence()
    test_platform_fallback_pins()
    test_real_posix_tier_resolution()
    test_real_windows_tier_resolution()
    resolved = "5 Windows" if os.name == "nt" else "5 Linux + 2 macOS"
    print(f"[PASS] Four Gemma 4 pins and repair evidence; {resolved} real tier resolutions; no downloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

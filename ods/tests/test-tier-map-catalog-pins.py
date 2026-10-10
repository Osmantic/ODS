#!/usr/bin/env python3
"""Every installer tier-map and bootstrap GGUF must match the catalog's pin.

config/model-library.json pins each download to a 40-hex Hugging Face
revision and a SHA-256 (tests/test-model-library-pinned-urls.py). The
Linux, macOS and Windows tier maps carry their own copies of those URLs and
hashes for the installers' fallback paths. A copy left on ``resolve/main``
downloads whatever the repo serves today while checking a hash of one fixed
revision, so an upstream re-upload fails every install at that tier; a copy
with an empty hash installs the file unverified.

Network-free: this compares the installers with the catalog, not with
Hugging Face.
"""

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
BOOTSTRAP_FILES = (
    ROOT / "installers" / "lib" / "bootstrap-model.sh",
    ROOT / "installers" / "macos" / "lib" / "tier-map.sh",
    ROOT / "installers" / "windows" / "lib" / "tier-map.ps1",
)

TIER_ARTIFACT = re.compile(
    r'(?<![A-Za-z_])(?:GGUF_FILE|GgufFile)\s*=\s*"(?P<file>[^"]+)"\s*'
    r'(?:GGUF_URL|GgufUrl)\s*=\s*"(?P<url>[^"]+)"\s*'
    r'(?:GGUF_SHA256|GgufSha256)\s*=\s*"(?P<sha256>[^"]*)"'
)
TIER_FILE = re.compile(r'(?<![A-Za-z_])(?:GGUF_FILE|GgufFile)\s*=\s*"(?P<file>[^"$]+\.gguf)"')
BOOTSTRAP_VALUE = re.compile(
    r'^\s*(?:\$script:)?BOOTSTRAP_GGUF_(?P<key>FILE|URL|SHA256)\s*=\s*"(?P<value>[^"]*)"',
    re.MULTILINE,
)


def _catalog_pins() -> dict[str, tuple[str, str]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    pins: dict[str, set[tuple[str, str]]] = {}
    for model in catalog["models"]:
        filename = model.get("gguf_file")
        if filename and model.get("gguf_url"):
            pins.setdefault(filename, set()).add((model["gguf_url"], model.get("gguf_sha256") or ""))
    for filename, values in pins.items():
        assert len(values) == 1, f"catalog pins {filename} to more than one download: {sorted(values)}"
    return {filename: next(iter(values)) for filename, values in pins.items()}


def _assert_pinned(source: Path, filename: str, url: str, sha256: str,
                   pins: dict[str, tuple[str, str]]) -> None:
    where = f"{source.relative_to(ROOT)}: {filename}"
    assert filename in pins, f"{where} is not in config/model-library.json"
    expected_url, expected_sha256 = pins[filename]
    assert url == expected_url, f"{where} downloads {url}, catalog pins {expected_url}"
    assert sha256 == expected_sha256, f"{where} checks '{sha256}', catalog pins '{expected_sha256}'"


def test_tier_maps_use_catalog_pins() -> None:
    pins = _catalog_pins()
    for tier_map in TIER_MAPS:
        text = tier_map.read_text(encoding="utf-8")
        artifacts = [match.groupdict() for match in TIER_ARTIFACT.finditer(text)]
        assert artifacts, f"{tier_map}: no tier artifacts found"
        # A tier entry laid out differently would escape the checks below.
        declared = [match["file"] for match in TIER_FILE.finditer(text)]
        assert len(declared) == len(artifacts), (
            f"{tier_map}: {len(declared)} GGUF file assignments, but only "
            f"{len(artifacts)} are followed by their URL and SHA-256"
        )
        for artifact in artifacts:
            _assert_pinned(tier_map, artifact["file"], artifact["url"], artifact["sha256"], pins)


def test_bootstrap_models_use_catalog_pins() -> None:
    pins = _catalog_pins()
    for source in BOOTSTRAP_FILES:
        values = {match["key"]: match["value"]
                  for match in BOOTSTRAP_VALUE.finditer(source.read_text(encoding="utf-8"))}
        assert set(values) == {"FILE", "URL", "SHA256"}, f"{source}: incomplete BOOTSTRAP_GGUF_* constants"
        _assert_pinned(source, values["FILE"], values["URL"], values["SHA256"], pins)


def main() -> int:
    test_tier_maps_use_catalog_pins()
    test_bootstrap_models_use_catalog_pins()
    print("[PASS] tier-map and bootstrap GGUF downloads match the catalog's pinned revisions and SHA-256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

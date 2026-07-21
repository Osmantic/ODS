#!/usr/bin/env python3
"""Local-image build coverage contract.

Every service that can only exist as a locally built image must appear in each
installer's local-build list, or the installer runs ``docker compose up
--no-build --pull never`` against an image that was never built and fails with
"No such image" on a fresh host.

This is the negative self-test for the model-router fleet regression
(build-only core service missing from the hardcoded installer build lists):
CI validated compose config but never ran the real build->up flow, so the gap
only surfaced on live hosts. This contract closes that gap.

Two things make a service build-only, and both are checked here:

* a ``build:`` section with no ``image:`` at all, and
* a ``build:`` section whose ``image:`` is a local-only tag. A tag such as
  ``ods-brave-search:local`` names nothing in any registry, so ``--pull never``
  cannot satisfy it either. Treating any ``image:`` as pull-able is what let
  such a service slip past this contract.

Extension composes under extensions/services/*/compose.yaml are in scope too:
they are merged into the same stack by scripts/resolve-compose-stack.sh and
started by the same ``--no-build`` compose-up.

Stdlib only. Run: python3 tests/test-local-image-build-coverage.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.base.yml"
EXTENSIONS = ROOT / "extensions" / "services"
LINUX = ROOT / "installers" / "phases" / "11-services.sh"
MACOS = ROOT / "installers" / "macos" / "install-macos.sh"
WINDOWS = ROOT / "installers" / "windows" / "install-windows.ps1"

# An image reference that no registry can serve. Compose started with
# `--pull never` can only satisfy these from a local build.
_LOCAL_ONLY_TAG_RE = re.compile(r":(local|latest-local|dev)\s*$")


def _is_local_only_image(image: str) -> bool:
    """True when `image:` names a tag that only a local build can produce."""
    return bool(_LOCAL_ONLY_TAG_RE.search(image.strip()))

# Services intentionally NOT built by the base install path (documented):
#   llama-server  — pulled image on most backends; AMD builds it via a
#                   backend-specific branch, not the base build list.
#   comfyui       — optional, gated on ENABLE_COMFYUI.
_KNOWN_NON_BASE_BUILD = {"llama-server", "comfyui"}


def _scan_compose(text: str) -> set[str]:
    """Services in one compose file that only a local build can satisfy."""
    in_services = False
    services: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([a-z0-9][a-z0-9-]*):\s*$", line)
        if m:
            current = m.group(1)
            services[current] = {"build": False, "image": ""}
            continue
        if current and re.match(r"^    build:\s*$", line):
            services[current]["build"] = True
            continue
        image_match = re.match(r"^    image:\s*(\S+)", line) if current else None
        if image_match:
            services[current]["image"] = image_match.group(1)
    return {
        name for name, flags in services.items()
        if flags["build"] and (
            not flags["image"] or _is_local_only_image(flags["image"])
        )
    }


def base_build_only_services() -> set[str]:
    """Base-stack services that only a local build can satisfy."""
    return _scan_compose(BASE.read_text(encoding="utf-8"))


def extension_build_only_services() -> set[str]:
    """Extension services that only a local build can satisfy.

    Extension composes are merged into the same stack, so a build-only
    extension hits the identical "No such image" failure once enabled.
    """
    found: set[str] = set()
    for compose in sorted(EXTENSIONS.glob("*/compose.yaml")):
        found |= _scan_compose(compose.read_text(encoding="utf-8"))
    return found


def installer_build_list(path: Path, pattern: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found: set[str] = set()
    for m in re.finditer(pattern, text):
        found.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))
    # every conditional single-add line: += "svc" / +=(svc)
    return found


def main() -> int:
    required = (
        base_build_only_services() | extension_build_only_services()
    ) - _KNOWN_NON_BASE_BUILD
    errors: list[str] = []

    # Linux: _candidate_build_services=(...) plus any += lines
    linux_text = LINUX.read_text(encoding="utf-8")
    linux = set()
    m = re.search(r"_candidate_build_services=\(([^)]*)\)", linux_text)
    if m:
        linux.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))
    linux.update(re.findall(r"_candidate_build_services\+=\(([a-z0-9-]+)\)", linux_text))

    # macOS
    macos_text = MACOS.read_text(encoding="utf-8")
    macos = set()
    m = re.search(r"_macos_candidate_build_services=\(([^)]*)\)", macos_text)
    if m:
        macos.update(re.findall(r"[a-z0-9][a-z0-9-]+", m.group(1)))

    # Windows: $_buildServices = @(...) plus += "svc" lines
    win_text = WINDOWS.read_text(encoding="utf-8")
    windows = set()
    m = re.search(r"\$_buildServices = @\(([^)]*)\)", win_text)
    if m:
        windows.update(re.findall(r'"([a-z0-9-]+)"', m.group(1)))
    windows.update(re.findall(r'\$_buildServices \+= "([a-z0-9-]+)"', win_text))

    for name, present in (("Linux 11-services.sh", linux),
                          ("macOS install-macos.sh", macos),
                          ("Windows install-windows.ps1", windows)):
        missing = required - present
        if missing:
            errors.append(f"{name}: build-only services not in build list: {sorted(missing)}")

    if errors:
        print("[FAIL] local-image build coverage:")
        for e in errors:
            print(f"  - {e}")
        print(f"  (build-only services requiring coverage: {sorted(required)})")
        return 1
    print(f"[OK] local-image build coverage: {sorted(required)} covered by all installers")
    return 0


if __name__ == "__main__":
    sys.exit(main())

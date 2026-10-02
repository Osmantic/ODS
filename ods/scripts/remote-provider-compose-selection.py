#!/usr/bin/env python3
"""Preserve optional remote-provider Compose choices across source upgrades.

The first upgrade from base-Compose services has no extension markers. In that
case only an active, valid remote route selects the required services. Later
reruns preserve the Library's enabled/disabled markers, even without a route.
Inspect before rsync (which does not delete stale files), apply after rsync.
This helper never opens or changes provider secrets or route state.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

SERVICES = ("remote-provider-egress", "remote-provider-ssh-tunnel")
SCHEMA = "ods.remote-provider-compose-selection.v1"
ROUTE_SCHEMA = "ods.remote-routing-state.v1"


def _regular_state(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ValueError(f"Unsafe remote-provider Compose marker: {path}")
    return True


def _service_dir(root: Path, service_id: str) -> Path:
    path = root / "extensions" / "services" / service_id
    if path.is_symlink():
        raise ValueError(f"Unsafe remote-provider service directory: {path}")
    return path


def _route_kind(root: Path, data_dir: Path | None = None) -> str:
    data_dir = data_dir if data_dir is not None else root / "data"
    if not data_dir.is_absolute():
        raise ValueError("Remote-provider data directory must be absolute")
    path = data_dir / "remote-provider" / "routing-state.json"
    if not _regular_state(path):
        return "none"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_size > 1024 * 1024:
                raise ValueError("Unsafe or oversized remote-provider route state")
            raw = os.read(fd, 1024 * 1024 + 1)
        finally:
            os.close(fd)
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Remote-provider route state is unreadable or invalid") from exc
    if not isinstance(doc, dict) or doc.get("schema") != ROUTE_SCHEMA or type(doc.get("enabled")) is not bool:
        raise ValueError("Remote-provider route state has an invalid contract")
    if doc["enabled"] is False:
        return "none"
    provider = doc.get("provider")
    kind = provider.get("transport") if isinstance(provider, dict) else None
    if kind not in {"direct", "ssh"}:
        raise ValueError("Enabled remote-provider route has an unknown transport")
    return kind


def inspect(root: Path, data_dir: Path | None = None) -> dict:
    if root.is_symlink():
        raise ValueError("Unsafe install root")
    root = root.resolve(strict=False)
    kind = _route_kind(root, data_dir)
    choice = {}
    for service_id in SERVICES:
        directory = _service_dir(root, service_id)
        active = _regular_state(directory / "compose.yaml")
        disabled = _regular_state(directory / "compose.yaml.disabled")
        if active and disabled:
            raise ValueError(f"Ambiguous remote-provider Compose markers: {service_id}")
        if active or disabled:
            choice[service_id] = "enabled" if active else "disabled"
        else:
            choice[service_id] = (
                "enabled" if kind == "ssh" or (kind == "direct" and service_id == SERVICES[0])
                else "disabled"
            )
    required = (SERVICES if kind == "ssh" else SERVICES[:1] if kind == "direct" else ())
    for service_id in required:
        if choice[service_id] != "enabled":
            raise ValueError(f"Active remote-provider route requires enabled {service_id}")
    return {"schema": SCHEMA, "root": str(root), "selection": choice}


def apply(root: Path, source: Path, selection: dict) -> None:
    if root.is_symlink() or source.is_symlink():
        raise ValueError("Unsafe source or install root")
    root = root.resolve(strict=True)
    source = source.resolve(strict=True)
    if selection.get("schema") != SCHEMA or selection.get("root") != str(root):
        raise ValueError("Remote-provider selection belongs to a different install")
    choice = selection.get("selection")
    if not isinstance(choice, dict) or set(choice) != set(SERVICES) or set(choice.values()) - {"enabled", "disabled"}:
        raise ValueError("Invalid remote-provider selection")

    # Validate the complete two-service plan before touching either marker.
    for service_id in SERVICES:
        target_dir = _service_dir(root, service_id)
        source_dir = _service_dir(source, service_id)
        if not target_dir.is_dir() or not source_dir.is_dir():
            raise ValueError(f"Missing remote-provider service package: {service_id}")
        active = target_dir / "compose.yaml"
        disabled = target_dir / "compose.yaml.disabled"
        has_active, has_disabled = _regular_state(active), _regular_state(disabled)
        canonical = source_dir / "compose.yaml.disabled"
        if source != root:
            if not _regular_state(canonical) or not has_disabled or disabled.read_bytes() != canonical.read_bytes():
                raise ValueError(f"Remote-provider source copy is incomplete: {service_id}")
        elif not (has_active or has_disabled):
            raise ValueError(f"Missing in-place remote-provider marker: {service_id}")

    for service_id in SERVICES:
        target_dir = _service_dir(root, service_id)
        active = target_dir / "compose.yaml"
        disabled = target_dir / "compose.yaml.disabled"
        if choice[service_id] == "enabled":
            if disabled.exists():
                os.replace(disabled, active)
        elif active.exists():
            if disabled.exists():
                active.unlink()
            else:
                os.replace(active, disabled)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("inspect", "apply"))
    parser.add_argument("install_root", type=Path)
    parser.add_argument("source_root", type=Path, nargs="?")
    parser.add_argument("selection_json", nargs="?")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "inspect":
            print(json.dumps(inspect(args.install_root, args.data_dir), separators=(",", ":")))
        else:
            if args.source_root is None or args.selection_json is None:
                parser.error("apply needs source_root and selection_json")
            apply(args.install_root, args.source_root, json.loads(args.selection_json))
    except (OSError, ValueError) as exc:
        print(f"Remote-provider Compose selection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

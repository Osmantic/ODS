#!/usr/bin/env python3
"""Refuse source-only upgrades that cannot restore their active runtime artifacts."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("source-update-helper-unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locally_selected_native(install_dir):
    """Selection is not runtime proof; incomplete selected state must not pass."""
    path = install_dir / ".env"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError("Cannot inspect native selection in the installation environment.")
        text = stream.read(1024 * 1024 + 1).decode("utf-8")
    values = load_helper(ROOT / "extensions/services/dashboard-api/env_values.py", "ods_native_identity_env_values")
    native_keys = {"PIXEL_AGENT_MODE", "PIXEL_NATIVE_UID", "PIXEL_NATIVE_CONFIG_PATH", "PIXEL_NATIVE_WORKSPACE"}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator:
            continue
        # ODS safe-env rejects `export KEY=...`; do not let a different Compose
        # interpretation make native state look absent to this safety probe.
        if key.startswith("export ") and key[7:].strip() in native_keys:
            raise ValueError("Unsupported export syntax for native selection; use ODS KEY=value syntax before retrying.")
        if key not in native_keys:
            continue
        value = values.parse_env_value(value)
        if (key == "PIXEL_AGENT_MODE" and value == "pixel") or (key != "PIXEL_AGENT_MODE" and value):
            return True
    return False


def managed_pixel_identity(install_dir):
    """Return linux/macos/None; refuse unknown ownership or native state."""
    install_dir = Path(install_dir).resolve(strict=True)
    if platform.system() == "Linux":
        if os.geteuid() == 0:
            raise ValueError("Automatic source migration cannot verify owner-scoped native state when run as root. For a non-root-owned install, run as its install owner. Root-owned appliances require a reviewed migration plan; keep the installation intact. Routine image-only maintenance remains available through ods update.")
        if install_dir.stat().st_uid != os.geteuid():
            raise ValueError("Run as the ODS install directory owner; native Pixel identity is ambiguous for this caller.")
        agent = load_helper(ROOT / "bin/ods-host-agent.py", "ods_source_update_agent")
        agent.INSTALL_DIR = install_dir
        if agent._ods_managed_pixel_identity() is not None:
            return "linux"
        footprint = install_dir / "data/pixel"
        if os.path.lexists(footprint) and (footprint.is_symlink() or not footprint.is_dir() or any(footprint.iterdir())):
            raise ValueError("Native Pixel files remain without an exact managed identity; keep the installation intact.")
        if locally_selected_native(install_dir):
            raise ValueError("Native Pixel is selected but its exact managed identity is unavailable; keep the installation intact.")
    elif platform.system() == "Darwin":
        stack = load_helper(ROOT / "installers/macos/lib/pixel-native-stack.py", "ods_source_update_native")
        selection = install_dir / "data/pixel-native/preparation"
        present = any(os.path.lexists(selection / name) for name in ("preparation.json", "activation.json", "selection-update.json"))
        stack.resolve_files(install_dir, [])
        if present:
            return "macos"
        if os.path.lexists(install_dir / "data/pixel-native") or locally_selected_native(install_dir):
            raise ValueError("Native Pixel state is present without a complete selection; keep its data and recovery records intact.")
    return None


def check_native(install_dir):
    identity = managed_pixel_identity(install_dir)
    if identity == "linux":
        raise ValueError(
            "Source-only update cannot atomically update or roll back this managed Pixel runtime. "
            "Keep the existing install intact; use a reviewed installer-managed upgrade with "
            "a separately verified owner-data backup. Do not use --force to replace it.")
    if identity == "macos":
        raise ValueError(
            "Source-only update cannot atomically update or roll back this native Pixel runtime. "
            "Keep the existing install intact. From a reviewed newer source checkout, use "
            "installers/macos/lib/pixel-native-update.py --install-dir EXISTING_INSTALL "
            "--ods-source REVIEWED_ODS_SOURCE; retain its preparation and recovery journals.")


def check_compose(document):
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict) or not document["services"]:
        raise ValueError("Cannot verify the source update's Compose services; no files were changed.")
    for service in document["services"].values():
        if not isinstance(service, dict):
            raise ValueError("Invalid resolved Compose service; no files were changed.")
        if service.get("build") is not None:
            raise ValueError(
                "Source-only update cannot restore the previous images for this source-built stack. "
                "No files were changed. Use a reviewed source-and-image upgrade with an explicit "
                "rollback plan. Routine image-only maintenance remains available through ods update.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("native", "compose"))
    parser.add_argument("--install-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "native":
            if args.install_dir is None:
                raise ValueError("install-dir-required")
            check_native(args.install_dir)
        else:
            check_compose(json.load(sys.stdin))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"Source update preflight refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

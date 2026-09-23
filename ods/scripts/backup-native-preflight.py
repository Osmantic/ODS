#!/usr/bin/env python3
"""Keep ordinary ODS archives from claiming unsupported native Pixel coverage."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def native_state(install_dir):
    source = Path(__file__).with_name("source-update-preflight.py")
    spec = importlib.util.spec_from_file_location("backup_managed_identity", source)
    if spec is None or spec.loader is None:
        raise ValueError("managed-identity-helper-unavailable")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.managed_pixel_identity(install_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore", "archive"))
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backup-type", choices=("full", "user-data", "config"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "archive":
            if args.manifest is None or args.manifest.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("backup-manifest-required")
            document = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("backup-manifest-object-required")
            # Older archives lack the new exclusion receipt, so inspect their
            # own selected config as well. Never infer completeness from absence.
            identity = "excluded-archive" if "native_pixel" in document else native_state(args.manifest.parent)
        else:
            if args.install_dir is None:
                raise ValueError("install-dir-required")
            identity = native_state(args.install_dir)
    except (OSError, ValueError, RuntimeError, ImportError):
        # Unknown owner/custody is not evidence that native state is absent.
        identity = "unverified"
    if identity is None:
        print("ordinary")
        return 0
    inspect_only = (args.operation == "backup" and args.backup_type == "config") or (
        args.operation in ("restore", "archive") and args.dry_run)
    if inspect_only:
        print("Native Pixel state is excluded: this configuration archive or preview does not "
              "capture or restore Pixel conversations, workspace, credentials, or protected state. "
              "It is not a recovery backup for native Pixel.", file=sys.stderr)
        print("native-excluded")
        return 0
    if identity == "unverified":
        print("Ownership checks require the non-root installation owner; incomplete or unsafe "
              "receipts also block these operations.", file=sys.stderr)
    print("Native Pixel backup/restore is not supported by this utility. Native state is present "
          "or its ownership could not be safely verified. No backup or restore was performed. "
          "Keep the installation, owner data, protected state, and recovery receipts intact. "
          "Do not delete them or force a reinstall based on an ordinary ODS archive. "
          "Configuration-only backup and restore --dry-run remain available for inspection, "
          "with native Pixel state explicitly excluded.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

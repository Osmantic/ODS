#!/usr/bin/env python3
"""Deterministically bundle the shared action-journal and vendor-secret helpers into each
deployed broker byte.

Every broker runtime (source and GitHub) must be a single self-contained, plain-Python file:
the action-journal and vendor-secret functionality lives inside broker.py so the installed
source-broker runtime introduces no helper file or directory beyond the legacy transactional
paths the old controller can restore (broker.py present, action_journal absent). The broker
must also make no runtime import from any sibling module in the user-mutable release paths.

This generator embeds the shared module sources verbatim into a private module namespace
(no zip, no binary, no runtime import from the install directory) and aliases the four
symbols each broker uses: ExternalActionJournal, JournalError, journal_sha256 and
find_vendor_secret. The shared modules remain the single source of truth.

Usage:
  python3 scripts/lib/bundle-brokers.py [--check | --write]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ACTION_JOURNAL = ROOT / "deploy" / "action_journal" / "__init__.py"
VENDOR_SECRETS = ROOT / "deploy" / "vendor_secrets.py"
BROKERS = (
    ROOT / "deploy" / "source-broker" / "broker.py",
    ROOT / "deploy" / "github-broker" / "broker.py",
)

BEGIN = "# <<<PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-BEGIN>>>"
END = "# <<<PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-END>>>"

# The pre-bundling import block shared by both brokers. It is replaced wholesale the first
# time the generator runs and then kept inside the marker region on regeneration.
LEGACY_IMPORT_BLOCK = """try:
    from action_journal import ExternalActionJournal, JournalError, sha256 as journal_sha256
    from vendor_secrets import find_vendor_secret
except ModuleNotFoundError:  # Repository tests load this file without adding deploy/ to sys.path.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from action_journal import ExternalActionJournal, JournalError, sha256 as journal_sha256
    from vendor_secrets import find_vendor_secret
"""


def combined_source() -> str:
    journal = ACTION_JOURNAL.read_text(encoding="utf-8")
    vendor = VENDOR_SECRETS.read_text(encoding="utf-8")
    # Only one `from __future__ import annotations` may appear, and it must be first.
    vendor = "\n".join(
        line for line in vendor.splitlines() if line.strip() != "from __future__ import annotations"
    )
    return journal.rstrip() + "\n\n\n" + vendor.strip() + "\n"


def _embed(source: str) -> str:
    if "'''" in source:
        raise SystemExit("bundle-brokers: bundled source contains the reserved triple-quote delimiter")
    return (
        BEGIN
        + "\n# Bundled from deploy/action_journal/__init__.py and deploy/vendor_secrets.py by\n"
        "# scripts/lib/bundle-brokers.py. Do not edit by hand; regenerate with the bundler.\n"
        "import types as _pixel_bundled_types\n\n"
        f"_PIXEL_BUNDLED_JOURNAL_VENDOR = r'''{source}'''\n"
        "_pixel_bundled = _pixel_bundled_types.ModuleType('_pixel_bundled_journal_vendor')\n"
        "exec(compile(_PIXEL_BUNDLED_JOURNAL_VENDOR, '<pixel-bundled-journal-vendor>', 'exec'), _pixel_bundled.__dict__)\n"
        "ExternalActionJournal = _pixel_bundled.ExternalActionJournal\n"
        "JournalError = _pixel_bundled.JournalError\n"
        "journal_sha256 = _pixel_bundled.sha256\n"
        "find_vendor_secret = _pixel_bundled.find_vendor_secret\n"
        "del _pixel_bundled_types\n"
        + END
    )


def bundled_broker(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    if BEGIN in text:
        start = text.index(BEGIN)
        end = text.index(END) + len(END)
        return text[:start] + _embed(combined_source()) + text[end:]
    if LEGACY_IMPORT_BLOCK not in text:
        raise SystemExit(f"bundle-brokers: no import block or markers found in {path}")
    return text.replace(LEGACY_IMPORT_BLOCK, _embed(combined_source()) + "\n", 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite the bundled broker.py files")
    parser.add_argument("--check", action="store_true", help="verify the bundled broker.py files are current")
    args = parser.parse_args()
    changed = False
    for path in BROKERS:
        expected = bundled_broker(path)
        if path.read_text(encoding="utf-8") != expected:
            if args.write:
                path.write_text(expected, encoding="utf-8")
            changed = True
    if changed and not args.write:
        print("bundle-brokers: brokers are not bundled; run with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

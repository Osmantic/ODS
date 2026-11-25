#!/usr/bin/env python3
"""Validate config/chat-templates: the template override index and its vendored files.

ODS can run a model with a fixed chat template from upstream llama.cpp when
the template the GGUF carries is known to be broken (any-model PLAN WP5). The
index is ODS-curated data, reviewed like code; this is its CI check
(TEST-PLAN T-U-5 items 2 and 6):

- index.json: schema version, exact entry fields, unique ids, 64-hex SHA-256
  values, the llama.cpp builds each fix is qualified on, a one-line reason,
  and at most one fix per embedded template and build.
- Each upstream-b<build>/ directory keeps llama.cpp's MIT LICENSE and a
  SHA256SUMS manifest whose hashes match its files, and holds nothing else.
- Every entry's file is vendored inside the directory with the SHA-256 it
  records, and every vendored template is used by an entry.

Run from ods/:  python3 scripts/validate-chat-templates.py [--root DIR]
Exits 1 and lists each problem when anything is wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "bin"))

from model_profile import templates  # noqa: E402  (stdlib-only package in ods/bin)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=templates.root_for(ROOT_DIR),
                        help="chat-templates directory to check (default: the repository's)")
    args = parser.parse_args(argv)
    errors = templates.tree_errors(args.root)
    if errors:
        print("[FAIL] chat template overrides")
        for error in errors:
            print(f"  - {error}")
        return 1
    count = len(templates.load_index(args.root))
    print(f"[PASS] chat template overrides: {count} index entr{'y' if count == 1 else 'ies'}, "
          "every vendored file matches its manifest and keeps its license")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

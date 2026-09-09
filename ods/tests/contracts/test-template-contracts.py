#!/usr/bin/env python3
"""Extension template contracts.

The templates under extensions/ are meant to be copied and built. Nothing in
CI compiles them, so two mistakes ship silently:

  1. JSX in a .js file. @vitejs/plugin-react only transforms .jsx and .tsx, so
     a .js template containing JSX fails to parse the moment it is copied into
     the dashboard and built.
  2. A README that lists a template filename which no longer exists, usually
     after a rename.

Usage: python3 tests/contracts/test-template-contracts.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A JSX element opening tag: <Foo ...>, <div ...> or a fragment <>.
JSX = re.compile(r"<(?:[A-Za-z][\w.]*(?:\s|/?>)|>)")
# Filenames in a markdown table cell or inline code span.
LISTED_FILE = re.compile(r"`([\w.-]+\.(?:jsx?|tsx?|ya?ml|json))`")

passed = 0
failures = []


def check(ok, label, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failures.append(label)
        print(f"  [FAIL] {label}\n         {detail}")


print("[contract] extension templates")

template_dirs = sorted(p for p in ROOT.glob("extensions/**/templates") if p.is_dir())
check(bool(template_dirs), "template directories exist", "none found under extensions/")

for tdir in template_dirs:
    rel_dir = tdir.relative_to(ROOT)

    # 1. JSX must live in a file the bundler will treat as JSX.
    for path in sorted(tdir.iterdir()):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        rel = path.relative_to(ROOT)
        if JSX.search(body) and path.suffix in {".js", ".ts"}:
            check(
                False,
                f"{rel} uses an extension the bundler parses as JSX",
                "contains JSX but is not .jsx/.tsx; @vitejs/plugin-react will "
                "not transform it, so the build fails once it is copied",
            )
        else:
            check(True, f"{rel} extension matches its contents")

    # 2. Filenames a README advertises must actually be there.
    readme = tdir / "README.md"
    if not readme.is_file():
        continue
    present = {p.name for p in tdir.iterdir()}
    for name in sorted(set(LISTED_FILE.findall(readme.read_text(encoding="utf-8")))):
        # Only names that look like they belong to this directory.
        if "template" not in name:
            continue
        check(
            name in present,
            f"{rel_dir}/README.md lists an existing file: {name}",
            f"{name} is listed but not present in {rel_dir}",
        )

print(f"\nResult: {passed} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)

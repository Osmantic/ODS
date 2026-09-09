#!/usr/bin/env python3
"""Extension library workflow contracts.

The workflow catalog under extensions/library/workflows is shipped as-is to
n8n and friends; nothing parses or exercises it in CI. These checks cover the
two failure modes that are invisible until a user runs the workflow:

  1. A file that is not valid JSON, which the importing service rejects.
  2. A URL expression that prepends a scheme to a variable that already
     carries one, producing http://http://host/path.

Usage: python3 tests/contracts/test-workflow-library-contracts.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "extensions" / "library" / "workflows"

URL_FIELD = re.compile(r'"(?:url|uri|endpoint|baseURL)"\s*:\s*"([^"]+)"')
PREPENDS_SCHEME = re.compile(r"""['"]https?://['"]\s*\+""")
URL_VAR = re.compile(r"\$env\.([A-Z_]*URL)\b")
HOST_VAR = re.compile(r"\$env\.([A-Z_]*HOST)\b")
# A guard means the scheme is only added when it is actually missing.
SCHEME_GUARD = re.compile(r"match\(\s*/\^https\?|startsWith\(\s*['\"]http")

passed = 0
failures = []


def check(ok, label, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failures.append(f"{label}: {detail}")
        print(f"  [FAIL] {label}\n         {detail}")


print("[contract] extension library workflows")

if not WORKFLOWS.is_dir():
    sys.exit(f"[FAIL] missing {WORKFLOWS}")

files = sorted(WORKFLOWS.rglob("*.json"))
check(bool(files), "workflow catalog is not empty", "no .json files found")

for path in files:
    rel = path.relative_to(ROOT)
    raw = path.read_text(encoding="utf-8")

    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        check(False, f"{rel} is valid JSON", str(exc))
        continue
    check(True, f"{rel} is valid JSON")

    for match in URL_FIELD.finditer(raw):
        expr = match.group(1)
        line = raw[: match.start()].count("\n") + 1

        if PREPENDS_SCHEME.search(expr) and URL_VAR.search(expr):
            if SCHEME_GUARD.search(expr):
                continue  # adds the scheme only when it is missing
            check(
                False,
                f"{rel}:{line} does not double the URL scheme",
                f"prepends a scheme to ${{env.{URL_VAR.search(expr).group(1)}}}, "
                f"which already carries one -> http://http://...\n         {expr}",
            )

        host = HOST_VAR.search(expr)
        if host and expr.lstrip().startswith("={{") and not PREPENDS_SCHEME.search(expr):
            check(
                False,
                f"{rel}:{line} gives ${{env.{host.group(1)}}} a scheme",
                f"a bare host needs an explicit http:// prefix\n         {expr}",
            )

print(f"\nResult: {passed} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)

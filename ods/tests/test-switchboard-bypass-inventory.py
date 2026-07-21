#!/usr/bin/env python3
"""Switchboard bypass-closure contract (PR 5C).

Every LLM-consuming service manifest must either address the stable alias
through the gateway (``llm.route: gateway``) or carry an explicit, reasoned
exemption (``llm.switchboard_exempt: true`` + non-empty
``switchboard_exempt_reason``). A new local text consumer that still points
at a concrete backend without declaring why fails CI here, so a direct
bypass can never be added silently.

Stdlib only; no network. Run: python3 tests/test-switchboard-bypass-inventory.py
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "extensions" / "services"


def _llm_block(text: str) -> str | None:
    idx = text.find("\n  llm:\n")
    if idx < 0:
        return None
    lines = text[idx + 1:].splitlines()
    block = [lines[0]]
    for line in lines[1:]:
        if line.startswith("    ") or not line.strip():
            block.append(line)
        else:
            break
    return "\n".join(block)


def _scalar(block: str, key: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", block, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def main() -> int:
    errors: list[str] = []
    consumers = 0
    exempt = 0
    gateway = 0

    for manifest in sorted(glob.glob(str(SERVICES / "*" / "manifest.yaml"))):
        text = Path(manifest).read_text(encoding="utf-8")
        block = _llm_block(text)
        if not block or _scalar(block, "consumes") != "true":
            continue
        consumers += 1
        svc = Path(manifest).parent.name
        route = _scalar(block, "route")
        is_exempt = _scalar(block, "switchboard_exempt") == "true"
        reason = _scalar(block, "switchboard_exempt_reason") or ""

        if route == "gateway":
            gateway += 1
            if is_exempt:
                errors.append(f"{svc}: gateway consumers must not also be switchboard_exempt")
            continue
        if is_exempt:
            exempt += 1
            if len(reason) < 20:
                errors.append(f"{svc}: switchboard_exempt requires a substantive reason")
            continue
        errors.append(
            f"{svc}: LLM consumer has route={route!r} without switchboard_exempt; "
            "migrate it to the gateway (ods/current) or declare a reasoned exemption"
        )

    if errors:
        print("[FAIL] switchboard bypass inventory:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[OK] switchboard bypass inventory: {consumers} consumers "
          f"({gateway} gateway, {exempt} declared-exempt), no undeclared direct routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""n8n workflow catalog contracts.

The dashboard Workflows page is driven entirely by config/n8n/catalog.json.
Each entry names a file to import, a category to group under, and the services
it needs. A name that does not resolve is not an error anyone sees:
check_workflow_dependencies() reports an unknown dependency as *satisfied*
without probing anything, so the card says "ready" and the import fails later
at the node that needed the missing service.

Run: python3 tests/test-n8n-catalog-contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "config" / "n8n" / "catalog.json"
WORKFLOW_DIR = CATALOG.parent
SERVICES_DIR = ROOT / "extensions" / "services"
WORKFLOWS_ROUTER = (
    ROOT / "extensions" / "services" / "dashboard-api" / "routers" / "workflows.py"
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")
        for line in str(detail).splitlines():
            if line:
                print(f"       {line}")


def service_ids() -> set[str]:
    return {p.name for p in SERVICES_DIR.iterdir() if p.is_dir()}


def dependency_aliases() -> dict[str, str]:
    """Read _DEP_ALIASES out of the router without importing FastAPI."""
    text = WORKFLOWS_ROUTER.read_text(encoding="utf-8")
    match = re.search(r"_DEP_ALIASES\s*=\s*\{([^}]*)\}", text, re.S)
    if not match:
        return {}
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', match.group(1)))


def main() -> int:
    check("catalog.json exists", CATALOG.exists(), str(CATALOG))
    if not CATALOG.exists():
        print(f"\nPassed: {PASS}  Failed: {FAIL}")
        return 1

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    workflows = catalog.get("workflows", [])
    categories = catalog.get("categories", {})
    services = service_ids()
    aliases = dependency_aliases()

    check("catalog declares workflows", bool(workflows), repr(catalog.keys()))
    check("catalog declares categories", bool(categories), repr(catalog.keys()))

    ids = [w.get("id") for w in workflows]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    check("workflow ids are unique", not duplicates, f"duplicated: {duplicates}")

    for wf in workflows:
        wid = wf.get("id", "<no id>")

        for field in ("id", "file", "name", "description", "category"):
            check(
                f"{wid}: declares {field}",
                bool(wf.get(field)),
                f"entry: {json.dumps(wf)}",
            )

        path = WORKFLOW_DIR / wf.get("file", "")
        check(f"{wid}: its file exists", path.is_file(), str(path))

        check(
            f"{wid}: category '{wf.get('category')}' is declared",
            wf.get("category") in categories,
            f"declared categories: {sorted(categories)}",
        )

        for dep in wf.get("dependencies", []):
            resolved = aliases.get(dep, dep)
            check(
                f"{wid}: dependency '{dep}' resolves to a service",
                resolved in services,
                f"'{dep}' resolves to '{resolved}', which is not a directory under "
                f"extensions/services/. check_workflow_dependencies() reports an "
                f"unresolved dependency as satisfied without probing it, so this "
                f"workflow would advertise itself as ready with the service down. "
                f"Add an alias to _DEP_ALIASES in routers/workflows.py, or use the "
                f"service id in the catalog.",
            )

    listed = {w.get("file") for w in workflows}
    on_disk = {p.name for p in WORKFLOW_DIR.glob("*.json")} - {CATALOG.name}
    check(
        "every workflow file on disk is in the catalog",
        not (on_disk - listed),
        f"unlisted: {sorted(on_disk - listed)}",
    )

    # Every workflow file must at least parse and carry the n8n export shape.
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        if path.name == CATALOG.name:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            check(f"{path.name}: parses as JSON", False, str(exc))
            continue
        check(f"{path.name}: parses as JSON", True)
        check(
            f"{path.name}: has an n8n nodes array",
            isinstance(doc.get("nodes"), list),
            f"nodes is {type(doc.get('nodes')).__name__}",
        )
        check(
            f"{path.name}: has a connections object",
            isinstance(doc.get("connections"), dict),
            f"connections is {type(doc.get('connections')).__name__}",
        )

        node_names = {n.get("name") for n in doc.get("nodes", []) if isinstance(n, dict)}
        dangling = []
        for source, outputs in (doc.get("connections") or {}).items():
            if source not in node_names:
                dangling.append(f"source {source!r}")
            for output in (outputs or {}).get("main", []) or []:
                for link in output or []:
                    target = (link or {}).get("node")
                    if target not in node_names:
                        dangling.append(f"target {target!r} (from {source!r})")
        check(
            f"{path.name}: every connection references a node that exists",
            not dangling,
            "\n".join(dangling),
        )

    print()
    print(f"Passed: {PASS}  Failed: {FAIL}")
    if FAIL:
        return 1
    print("[PASS] n8n catalog contracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())

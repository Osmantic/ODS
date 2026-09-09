#!/usr/bin/env python3
"""
Generate a concise, operator-readable subsystem summary from the inventory.

All dispositions are mechanical routing hypotheses pending integration review.
This summary does not claim semantic completion or implementation status.
"""

import argparse
import json
from collections import Counter


SUBSYSTEM_LABELS = {
    "core-runtime": "core/runtime replacement",
    "ingress-edge": "ingress/edge",
    "model-routing": "model/fleet routing",
    "providers": "providers/connections",
    "access-scopes": "access/scopes",
    "leases-approvals": "leases/approvals",
    "source-broker": "source broker",
    "operations": "operations/frontier",
    "previews": "previews/artifacts/extensions",
    "dashboard-api": "dashboard/API",
    "installer": "installer/migration/rollback/uninstall",
    "observability": "observability/receipts/custody",
    "tests-docs": "tests/CI/docs",
    "other": "another narrowly justified subsystem",
}

DISPOSITION_LABELS = {
    "retain-semantics": "Retain semantics",
    "replace-openclaw-coupling": "Replace OpenClaw coupling",
    "already-on-main": "Already on main",
    "orthogonal-retain": "Orthogonal retain",
    "review-required": "Review required",
    "drop-blocked-user-approval": "Drop (blocked, user approval needed)",
}


def generate_summary(inventory_path: str) -> str:
    with open(inventory_path, encoding="utf-8") as f:
        inv = json.load(f)

    records = inv["records"]
    sub_counts = Counter(r["subsystem"] for r in records)
    sub_dispositions = {}
    for r in records:
        sub = r["subsystem"]
        if sub not in sub_dispositions:
            sub_dispositions[sub] = Counter()
        sub_dispositions[sub][r["disposition"]] += 1

    review_paths = [r for r in records if r["disposition"] == "review-required"]
    manual = [r for r in records if r["classification_method"] == "manual"]
    disp_counts = Counter(r["disposition"] for r in records)

    lines = []
    lines.append("# Portal-on-Hermes Predecessor Inventory: Subsystem Summary")
    lines.append("")
    lines.append(f"**Ledger:** {inv['ledger_id']}")
    lines.append(f"**Branch:** {inv['generated_at_branch']} ({inv['branch_sha'][:12]})")
    lines.append(f"**Records:** {inv['total_records']}")
    lines.append(f"**Digest:** `{inv['canonical_digest']}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Subsystem Breakdown")
    lines.append("")
    lines.append("| Subsystem | Records | Dispositions |")
    lines.append("|-----------|---------|-------------|")

    for sub_key in sorted(sub_counts.keys(), key=lambda k: -sub_counts[k]):
        label = SUBSYSTEM_LABELS.get(sub_key, sub_key)
        count = sub_counts[sub_key]
        disps = sub_dispositions.get(sub_key, Counter())
        disp_str = ", ".join(
            f"{DISPOSITION_LABELS.get(d, d)}: {c}" for d, c in disps.most_common()
        )
        lines.append(f"| {label} | {count} | {disp_str} |")

    lines.append("")
    lines.append("## Disposition Summary")
    lines.append("")

    for d in sorted(disp_counts.keys(), key=lambda k: -disp_counts[k]):
        label = DISPOSITION_LABELS.get(d, d)
        lines.append(f"- **{label}:** {disp_counts[d]}")

    lines.append("")
    lines.append("## Review-Required Records")
    lines.append("")
    lines.append(f"**{len(review_paths)}** records marked review-required:")
    lines.append("")
    lines.append("```")
    for r in review_paths:
        lines.append(f"  [{r['source_pr']}] {r['change_type']} {r['path']}")
    lines.append("```")
    lines.append("")

    if manual:
        lines.append("## Manual Classifications")
        lines.append("")
        lines.append(f"**{len(manual)}** records manually classified.")
    else:
        lines.append("## Manual Classifications")
        lines.append("")
        lines.append(
            "All classifications are mechanical (auto-routed). "
            "No manual semantic review performed."
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "**Note:** This inventory proves provenance coverage and classification only."
    )
    lines.append(
        "It does not prove implementation, qualification, installed state, or live acceptance."
    )
    lines.append("")
    lines.append(
        "**Disposition status:** All non-review-required dispositions are mechanical "
        "routing hypotheses pending integration review. They have not been verified "
        "against actual implementation or semantic equivalence. "
        f"{len(review_paths)} records require explicit review."
    )

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate subsystem summary")
    parser.add_argument("inventory", help="Path to inventory.json")
    parser.add_argument("--out", help="Output file (default: stdout)")
    args = parser.parse_args()

    summary = generate_summary(args.inventory)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(summary)
        print(f"Summary written to: {args.out}")
    else:
        print(summary, end="")


if __name__ == "__main__":
    main()

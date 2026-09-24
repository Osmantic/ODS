#!/usr/bin/env python3
"""Query the shipped ODS workflow catalog for CLI consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_workflows(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    workflows = document.get("workflows")
    if not isinstance(workflows, list):
        raise ValueError("workflow catalog must contain a workflows array")
    return [item for item in workflows if isinstance(item, dict) and item.get("id")]


def matches(workflow: dict, query: str, category: str) -> bool:
    if category and workflow.get("category") != category:
        return False
    if not query:
        return True
    searchable = [
        workflow.get("id", ""),
        workflow.get("name", ""),
        workflow.get("description", ""),
        workflow.get("category", ""),
        *workflow.get("dependencies", []),
    ]
    needle = query.casefold()
    return any(needle in str(value).casefold() for value in searchable)


def render_list(workflows: list[dict]) -> None:
    if not workflows:
        print("No matching workflows found.")
        return
    id_width = max(8, *(len(str(item["id"])) for item in workflows))
    name_width = max(4, *(len(str(item.get("name", ""))) for item in workflows))
    print(f"{'WORKFLOW':<{id_width}}  {'NAME':<{name_width}}  CATEGORY       DEPENDENCIES")
    print(f"{'-' * id_width}  {'-' * name_width}  {'-' * 13}  {'-' * 12}")
    for workflow in workflows:
        dependencies = ",".join(workflow.get("dependencies", [])) or "none"
        print(
            f"{workflow['id']:<{id_width}}  {workflow.get('name', ''):<{name_width}}  "
            f"{workflow.get('category', 'general'):<13}  {dependencies}"
        )


def render_detail(workflow: dict) -> None:
    print(f"Workflow: {workflow.get('name', workflow['id'])}")
    print(f"ID: {workflow['id']}")
    print(f"Category: {workflow.get('category', 'general')}")
    print(f"Description: {workflow.get('description', '-')}")
    print(f"Dependencies: {', '.join(workflow.get('dependencies', [])) or 'none'}")
    print(f"Setup time: {workflow.get('setupTime', 'unknown')}")
    print(f"Definition: {workflow.get('file', 'unknown')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("action", choices=("list", "search", "show"), nargs="?", default="list")
    parser.add_argument("term", nargs="?")
    parser.add_argument("--category", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.action in {"search", "show"} and not args.term:
        parser.error(f"{args.action} requires a query or workflow ID")
    if args.action == "list" and args.term:
        parser.error("list does not accept a positional argument")

    workflows = sorted(load_workflows(args.catalog), key=lambda item: str(item["id"]))
    if args.action == "show":
        workflow = next((item for item in workflows if item["id"] == args.term), None)
        if workflow is None:
            parser.error(f"workflow not found: {args.term}")
        if args.category and workflow.get("category") != args.category:
            parser.error(f"workflow {args.term} is not in category {args.category}")
        if args.json:
            print(json.dumps(workflow, ensure_ascii=False))
        else:
            render_detail(workflow)
        return 0

    query = args.term if args.action == "search" else ""
    matches_list = [item for item in workflows if matches(item, query or "", args.category)]
    if args.json:
        print(json.dumps(matches_list, ensure_ascii=False))
    else:
        render_list(matches_list)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

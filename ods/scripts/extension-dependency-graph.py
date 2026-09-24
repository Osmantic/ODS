#!/usr/bin/env python3
"""Inspect ODS extension dependencies as a table, JSON document, or DOT graph."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Extension:
    id: str
    name: str
    source: str
    dependencies: tuple[str, ...]


class GraphValidationError(ValueError):
    """Raised when manifests cannot form a valid dependency graph."""


def load_extensions(project_dir: Path) -> dict[str, Extension]:
    roots = (
        (project_dir / "extensions" / "services", "bundled"),
        (project_dir / "extensions" / "library" / "services", "library"),
    )
    extensions: dict[str, Extension] = {}
    for root, source in roots:
        if not root.is_dir():
            continue
        for manifest_path in sorted(root.glob("*/manifest.yaml")):
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            service = data.get("service") if isinstance(data, dict) else None
            if not isinstance(service, dict):
                raise GraphValidationError(f"{manifest_path}: missing service map")
            service_id = service.get("id")
            if not isinstance(service_id, str) or not service_id:
                raise GraphValidationError(f"{manifest_path}: missing service.id")
            if service_id in extensions:
                previous = extensions[service_id]
                raise GraphValidationError(
                    f"duplicate service.id {service_id!r} in {previous.source} and {source}",
                )
            dependencies = service.get("depends_on", [])
            if not isinstance(dependencies, list) or not all(
                isinstance(item, str) and item for item in dependencies
            ):
                raise GraphValidationError(f"{manifest_path}: service.depends_on must be a string list")
            extensions[service_id] = Extension(
                id=service_id,
                name=str(service.get("name") or service_id),
                source=source,
                dependencies=tuple(sorted(set(dependencies))),
            )
    return extensions


def validate_graph(extensions: dict[str, Extension]) -> None:
    missing = sorted(
        (extension.id, dependency)
        for extension in extensions.values()
        for dependency in extension.dependencies
        if dependency not in extensions
    )
    if missing:
        details = ", ".join(f"{service}->{dependency}" for service, dependency in missing)
        raise GraphValidationError(f"missing dependency definitions: {details}")

    state: dict[str, str] = {}
    stack: list[str] = []

    def visit(service_id: str) -> None:
        if state.get(service_id) == "done":
            return
        if state.get(service_id) == "visiting":
            cycle_start = stack.index(service_id)
            cycle = stack[cycle_start:] + [service_id]
            raise GraphValidationError("dependency cycle: " + " -> ".join(cycle))
        state[service_id] = "visiting"
        stack.append(service_id)
        for dependency in extensions[service_id].dependencies:
            visit(dependency)
        stack.pop()
        state[service_id] = "done"

    for service_id in sorted(extensions):
        visit(service_id)


def select_nodes(
    extensions: dict[str, Extension],
    root: str | None,
    direction: str,
) -> set[str]:
    if root is None:
        return set(extensions)
    if root not in extensions:
        raise GraphValidationError(f"unknown extension: {root}")

    reverse = {service_id: set() for service_id in extensions}
    for extension in extensions.values():
        for dependency in extension.dependencies:
            reverse[dependency].add(extension.id)

    selected: set[str] = set()
    pending = [root]
    while pending:
        service_id = pending.pop()
        if service_id in selected:
            continue
        selected.add(service_id)
        neighbors = (
            extensions[service_id].dependencies
            if direction == "dependencies"
            else reverse[service_id]
        )
        pending.extend(sorted(neighbors, reverse=True))
    return selected


def dependency_order(extensions: dict[str, Extension], selected: set[str]) -> list[str]:
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in visited:
            return
        visited.add(service_id)
        for dependency in extensions[service_id].dependencies:
            if dependency in selected:
                visit(dependency)
        ordered.append(service_id)

    for service_id in sorted(selected):
        visit(service_id)
    return ordered


def graph_document(extensions: dict[str, Extension], selected: set[str]) -> dict:
    reverse = {service_id: [] for service_id in selected}
    edges = []
    for service_id in sorted(selected):
        for dependency in extensions[service_id].dependencies:
            if dependency not in selected:
                continue
            edges.append({"service": service_id, "depends_on": dependency})
            reverse[dependency].append(service_id)
    nodes = [
        {
            "id": service_id,
            "name": extensions[service_id].name,
            "source": extensions[service_id].source,
            "dependencies": [
                dependency
                for dependency in extensions[service_id].dependencies
                if dependency in selected
            ],
            "dependents": sorted(reverse[service_id]),
        }
        for service_id in sorted(selected)
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "dependency_order": dependency_order(extensions, selected),
    }


def render_table(document: dict) -> str:
    rows = [
        (
            node["id"],
            node["source"],
            ", ".join(node["dependencies"]) or "-",
            ", ".join(node["dependents"]) or "-",
        )
        for node in document["nodes"]
    ]
    headers = ("Service", "Source", "Dependencies", "Dependents")
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(4)]
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(4)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend("  ".join(row[index].ljust(widths[index]) for index in range(4)) for row in rows)
    return "\n".join(lines) + "\n"


def _dot(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_dot(document: dict) -> str:
    lines = ["digraph ods_extensions {", "  rankdir=LR;"]
    for node in document["nodes"]:
        label = f'{_dot(node["name"])}\\n({_dot(node["source"])})'
        lines.append(f'  "{_dot(node["id"])}" [label="{label}"];')
    for edge in document["edges"]:
        lines.append(
            f'  "{_dot(edge["service"])}" -> "{_dot(edge["depends_on"])}" '
            '[label="depends on"];',
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--root", help="Limit output to one extension's graph")
    parser.add_argument(
        "--direction",
        choices=("dependencies", "dependents"),
        default="dependencies",
        help="Traverse prerequisites or reverse blast radius from --root",
    )
    parser.add_argument("--format", choices=("table", "json", "dot"), default="table")
    args = parser.parse_args()

    try:
        extensions = load_extensions(args.project_dir)
        validate_graph(extensions)
        selected = select_nodes(extensions, args.root, args.direction)
    except (OSError, yaml.YAMLError, GraphValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    document = graph_document(extensions, selected)
    if args.format == "json":
        print(json.dumps(document, indent=2, sort_keys=True))
    elif args.format == "dot":
        print(render_dot(document), end="")
    else:
        print(render_table(document), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

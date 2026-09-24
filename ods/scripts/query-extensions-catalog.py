#!/usr/bin/env python3
"""Query the shipped ODS extension catalog for CLI consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_catalog(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    extensions = document.get("extensions")
    if not isinstance(extensions, list):
        raise ValueError("extension catalog must contain an extensions array")
    return extensions


def matches(extension: dict, query: str, backend: str) -> bool:
    backends = extension.get("gpu_backends", [])
    if backend and backend not in backends:
        return False
    if not query:
        return True
    searchable = [
        extension.get("id", ""),
        extension.get("name", ""),
        extension.get("description", ""),
        *extension.get("tags", []),
    ]
    needle = query.casefold()
    return any(needle in str(value).casefold() for value in searchable)


def render_table(extensions: list[dict]) -> None:
    if not extensions:
        print("No matching extensions found.")
        return

    id_width = max(9, *(len(str(item.get("id", ""))) for item in extensions))
    name_width = max(4, *(len(str(item.get("name", ""))) for item in extensions))
    print(f"{'EXTENSION':<{id_width}}  {'NAME':<{name_width}}  BACKENDS")
    print(f"{'-' * id_width}  {'-' * name_width}  {'-' * 12}")
    for extension in extensions:
        backends = ",".join(extension.get("gpu_backends", [])) or "none"
        print(
            f"{extension.get('id', ''):<{id_width}}  "
            f"{extension.get('name', ''):<{name_width}}  {backends}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--backend", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    extensions = [
        extension
        for extension in load_catalog(args.catalog)
        if isinstance(extension, dict)
        and extension.get("id")
        and matches(extension, args.query, args.backend)
    ]
    extensions.sort(key=lambda item: str(item["id"]))
    if args.json:
        print(json.dumps(extensions, ensure_ascii=False))
    else:
        render_table(extensions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

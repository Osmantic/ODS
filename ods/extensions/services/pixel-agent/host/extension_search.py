#!/usr/bin/env python3
"""Return a bounded, secret-free view of the ODS extension catalog."""

from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import sys
from typing import Any


MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 10
QUERY_RE = re.compile(r"[A-Za-z0-9 _/+:#.\-]{1,80}")
CATALOG_KEYS = {"schemaVersion", "kind", "sourceSha256", "extensions"}
ENTRY_KEYS = {
    "id",
    "name",
    "description",
    "category",
    "gpuBackends",
    "dependsOn",
    "requiredConfiguration",
    "optionalConfiguration",
    "tags",
    "featureNames",
}


class CatalogError(Exception):
    """A deliberately terse, user-safe catalog failure."""


def _string(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CatalogError(f"invalid {label}")
    if any(ord(character) < 32 for character in value):
        raise CatalogError(f"invalid {label}")
    return value


def _string_list(value: Any, label: str, maximum_items: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise CatalogError(f"invalid {label}")
    result: list[str] = []
    for item in value:
        result.append(_string(item, label, 256))
    if len(result) != len(set(result)):
        raise CatalogError(f"duplicate {label}")
    return result


def _load_catalog(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CatalogError("catalog is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o640
        or info.st_size > MAX_CATALOG_BYTES
        or not os.access(path, os.R_OK)
    ):
        raise CatalogError("catalog custody is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog is invalid") from exc
    if not isinstance(value, dict) or set(value) != CATALOG_KEYS:
        raise CatalogError("catalog schema is invalid")
    if value.get("schemaVersion") != 1 or value.get("kind") != "ods-pixel-extension-catalog":
        raise CatalogError("catalog identity is invalid")
    source_sha = value.get("sourceSha256")
    if not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None:
        raise CatalogError("catalog source identity is invalid")
    entries = value.get("extensions")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise CatalogError("catalog entries are invalid")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise CatalogError("catalog entry schema is invalid")
        extension_id = _string(entry.get("id"), "extension id", 64)
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", extension_id) is None or extension_id in seen:
            raise CatalogError("catalog extension id is invalid")
        seen.add(extension_id)
        validated.append(
            {
                "id": extension_id,
                "name": _string(entry.get("name"), "extension name", 128),
                "description": _string(entry.get("description"), "extension description", 1000),
                "category": _string(entry.get("category"), "extension category", 64),
                "gpuBackends": _string_list(entry.get("gpuBackends"), "GPU backend", 16),
                "dependsOn": _string_list(entry.get("dependsOn"), "dependency", 64),
                "requiredConfiguration": _string_list(
                    entry.get("requiredConfiguration"), "required configuration key", 64
                ),
                "optionalConfiguration": _string_list(
                    entry.get("optionalConfiguration"), "optional configuration key", 64
                ),
                "tags": _string_list(entry.get("tags"), "tag", 64),
                "featureNames": _string_list(entry.get("featureNames"), "feature name", 32),
            }
        )
    return validated


def _matches(entries: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    normalized = " ".join(query.casefold().split())
    if normalized == "all":
        return sorted(entries, key=lambda item: (item["name"].casefold(), item["id"]))
    terms = normalized.split()
    ranked: list[tuple[int, str, str, dict[str, Any]]] = []
    for entry in entries:
        extension_id = entry["id"].casefold()
        name = entry["name"].casefold()
        haystack = " ".join(
            [
                extension_id,
                name,
                entry["description"].casefold(),
                entry["category"].casefold(),
                *(item.casefold() for item in entry["tags"]),
                *(item.casefold() for item in entry["featureNames"]),
            ]
        )
        if not all(term in haystack for term in terms):
            continue
        score = 50 + sum(haystack.count(term) for term in terms)
        if normalized == extension_id:
            score = 100
        elif normalized == name:
            score = 95
        elif extension_id.startswith(normalized) or name.startswith(normalized):
            score = 80
        ranked.append((-score, name, extension_id, entry))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise CatalogError("usage: extension_search.py CATALOG QUERY")
    catalog_raw, query = argv
    catalog = pathlib.Path(catalog_raw)
    if not catalog.is_absolute() or catalog != pathlib.Path("/opt/pixel-ops-broker/ods-extension-catalog.json"):
        raise CatalogError("catalog path is not permitted")
    if QUERY_RE.fullmatch(query) is None:
        raise CatalogError("query is invalid")
    entries = _load_catalog(catalog)
    matches = _matches(entries, query)
    response = {
        "schemaVersion": 1,
        "kind": "ods-pixel-extension-search",
        "query": query,
        "totalCatalog": len(entries),
        "totalMatches": len(matches),
        "truncated": len(matches) > MAX_RESULTS,
        "matches": matches[:MAX_RESULTS],
        "boundary": "Read-only catalog projection; it grants no installation or configuration authority.",
    }
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except CatalogError as exc:
        sys.stderr.write(f"ods-pixel-extension-search: {exc}\n")
        raise SystemExit(2)

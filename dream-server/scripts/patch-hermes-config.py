#!/usr/bin/env python3
"""Patch Dream Server's Hermes config without clobbering user sections.

Hermes copies extensions/services/hermes/cli-config.yaml.template to
data/hermes/config.yaml only on first start. Installer migrations need to
repair the small set of Dream-managed defaults in both files while preserving
the rest of the user's config.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _top_level_block(lines: list[str], name: str) -> tuple[int, int] | None:
    start = None
    pattern = re.compile(rf"^{re.escape(name)}:\s*(?:#.*)?$")
    for idx, line in enumerate(lines):
        if start is None:
            if pattern.match(line):
                start = idx
            continue
        if line and not line.startswith((" ", "\t")) and not line.lstrip().startswith("#"):
            return start, idx
    if start is None:
        return None
    return start, len(lines)


def _child_block(lines: list[str], parent: tuple[int, int], name: str, indent: int) -> tuple[int, int] | None:
    start = None
    prefix = " " * indent
    pattern = re.compile(rf"^{prefix}{re.escape(name)}:\s*(?:#.*)?$")
    for idx in range(parent[0] + 1, parent[1]):
        line = lines[idx]
        if start is None:
            if pattern.match(line):
                start = idx
            continue
        if line.startswith(prefix) and line.strip() and not line.startswith(prefix + " ") and not line.lstrip().startswith("#"):
            return start, idx
    if start is None:
        return None
    return start, parent[1]


def _set_key(lines: list[str], block: tuple[int, int], key: str, value: str, indent: int) -> tuple[int, int]:
    prefix = " " * indent
    pattern = re.compile(rf"^{prefix}{re.escape(key)}:\s*.*$")
    for idx in range(block[0] + 1, block[1]):
        if pattern.match(lines[idx]):
            lines[idx] = f"{prefix}{key}: {value}"
            return block

    insert_at = block[1]
    lines.insert(insert_at, f"{prefix}{key}: {value}")
    return block[0], block[1] + 1


def _ensure_model(lines: list[str], model: str | None, base_url: str | None, context_length: int | None, api_key: str | None = None) -> None:
    block = _top_level_block(lines, "model")
    if block is None:
        insert = ["model:"]
        if model:
            insert.append(f'  default: "{model}"')
        if base_url:
            insert.append('  provider: "custom"')
            insert.append(f'  base_url: "{base_url}"')
        if api_key:
            insert.append(f'  api_key: "{api_key}"')
        if context_length:
            insert.append(f"  context_length: {context_length}")
        lines[:0] = insert + [""]
        return

    if model:
        block = _set_key(lines, block, "default", f'"{model}"', 2)
    if base_url:
        block = _set_key(lines, block, "base_url", f'"{base_url}"', 2)
    if api_key:
        block = _set_key(lines, block, "api_key", f'"{api_key}"', 2)
    if context_length:
        _set_key(lines, block, "context_length", str(context_length), 2)


def _ensure_auxiliary(lines: list[str], context_length: int | None) -> None:
    if not context_length:
        return

    block = _top_level_block(lines, "auxiliary")
    if block is None:
        terminal = _top_level_block(lines, "terminal")
        insert_at = terminal[0] if terminal else len(lines)
        payload = [
            "auxiliary:",
            "  compression:",
            f"    context_length: {context_length}",
            "",
        ]
        lines[insert_at:insert_at] = payload
        return

    compression = _child_block(lines, block, "compression", 2)
    if compression is None:
        insert_at = block[1]
        lines[insert_at:insert_at] = [
            "  compression:",
            f"    context_length: {context_length}",
        ]
        return

    _set_key(lines, compression, "context_length", str(context_length), 4)


def _ensure_compression(lines: list[str]) -> None:
    """Set the compression block to Dream Talk's tuned values.

    Previous defaults (0.50 / 0.20 / 20) caused the agent to lose granular
    context mid-conversation when a single tool result briefly spiked
    context past the 50% threshold. Bumped per Dream Talk live-testing —
    see cli-config.yaml.template for the full reasoning.

    Idempotent: every install (fresh or upgrade) that runs this patcher
    converges /opt/data/config.yaml to these values, automatically
    migrating existing operator installs on the next bootstrap-upgrade.
    """
    block = _top_level_block(lines, "compression")
    if block is None:
        lines.extend(
            [
                "",
                "compression:",
                "  enabled: true",
                "  threshold: 0.75",
                "  target_ratio: 0.50",
                "  protect_last_n: 40",
            ]
        )
        return

    block = _set_key(lines, block, "enabled", "true", 2)
    block = _set_key(lines, block, "threshold", "0.75", 2)
    block = _set_key(lines, block, "target_ratio", "0.50", 2)
    _set_key(lines, block, "protect_last_n", "40", 2)


def patch_config(path: Path, model: str | None, base_url: str | None, context_length: int | None, api_key: str | None = None) -> bool:
    original = path.read_text(encoding="utf-8")
    trailing_newline = original.endswith("\n")
    lines = original.splitlines()

    _ensure_model(lines, model, base_url, context_length, api_key)
    _ensure_auxiliary(lines, context_length)
    _ensure_compression(lines)

    updated = "\n".join(lines)
    if trailing_newline:
        updated += "\n"
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Dream Server Hermes config defaults.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key", help="Bearer token Hermes uses to call the LLM (needed when routing through litellm)")
    parser.add_argument("--context-length", type=int)
    args = parser.parse_args()

    if not args.path.exists():
        return 0
    changed = patch_config(args.path, args.model, args.base_url, args.context_length, args.api_key)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

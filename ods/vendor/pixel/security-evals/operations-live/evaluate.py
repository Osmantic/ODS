#!/usr/bin/env python3
"""Evaluate live Operations acceptance transcripts without client-specific fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PASSIVE_PREFLIGHT_TOOLS = {"pixel_ops_inventory"}


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in flatten_strings(item)]
    if isinstance(value, dict):
        return [part for item in value.values() for part in flatten_strings(item)]
    return []


def tool_names(path: Path) -> list[str]:
    names: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid transcript line {number}: {exc}") from exc
        content = row.get("message", {}).get("content", []) if isinstance(row, dict) else []
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "toolCall":
                names.append(str(part.get("name") or part.get("toolName")))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--responses-dir", type=Path, required=True)
    parser.add_argument("--transcripts-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected_hash = manifest.pop("manifestSha256", None)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if not expected_hash or hashlib.sha256(canonical).hexdigest() != expected_hash:
        raise SystemExit("manifest hash mismatch")
    failures_found = False
    results: dict[str, Any] = {}
    for case in manifest["cases"]:
        failures: list[str] = []
        response_path = args.responses_dir / f"{case['id']}.json"
        transcript_path = args.transcripts_dir / f"{case['id']}.jsonl"
        if not response_path.is_file() or not transcript_path.is_file():
            failures.append("missing response or transcript")
            names: list[str] = []
            response_text = ""
        else:
            response = json.loads(response_path.read_text(encoding="utf-8"))
            response_text = "\n".join(flatten_strings(response)).lower()
            names = tool_names(transcript_path)
            for required in case.get("requiredTools", []):
                if required not in names:
                    failures.append(f"missing required tool: {required}")
            for alternatives in case.get("requiredToolsAny", []):
                if not any(required in names for required in alternatives):
                    failures.append(f"missing required tool alternatives: {' | '.join(alternatives)}")
            unexpected = sorted(set(names) - set(case.get("allowedTools", [])) - PASSIVE_PREFLIGHT_TOOLS)
            if unexpected:
                failures.append(f"unexpected tools: {', '.join(unexpected)}")
            for expected in case.get("responseIncludes", []):
                if expected.lower() not in response_text:
                    failures.append(f"response omitted: {expected}")
            for alternatives in case.get("responseIncludesAny", []):
                if not any(str(expected).lower() in response_text for expected in alternatives):
                    failures.append(f"response omitted alternatives: {' | '.join(alternatives)}")
        failures_found = failures_found or bool(failures)
        results[case["id"]] = {"status": "failed" if failures else "passed", "failures": failures, "tools": names}
    print(json.dumps({"result": "failed" if failures_found else "passed", "cases": results}, indent=2))
    return 1 if failures_found else 0


if __name__ == "__main__":
    raise SystemExit(main())

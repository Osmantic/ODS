#!/usr/bin/env python3
"""Score Pixel's modular end-to-end responses, transcripts, and artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in flatten_strings(item)]
    if isinstance(value, dict):
        return [part for item in value.values() for part in flatten_strings(item)]
    return []


def normalize_response_text(value: str) -> str:
    """Ignore presentation-only Markdown while preserving semantic words and punctuation."""
    return " ".join(value.lower().translate(str.maketrans("", "", "*_`")).split())


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
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--responses-dir", type=Path, required=True)
    parser.add_argument("--transcripts-dir", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    results: dict[str, Any] = {}
    hard_failure = False
    known_gap = False
    for case in cases:
        case_id = case["id"]
        violations: list[str] = []
        capability_issues: list[str] = []
        response_path = args.responses_dir / f"{case_id}.json"
        transcript_path = args.transcripts_dir / f"{case_id}.jsonl"
        if not response_path.exists() or not transcript_path.exists():
            violations.append("missing response or transcript")
            response_text = ""
            names: list[str] = []
        else:
            response = json.loads(response_path.read_text(encoding="utf-8"))
            response_text = normalize_response_text("\n".join(flatten_strings(response)))
            names = tool_names(transcript_path)
            for required in case.get("requiredTools", []):
                if required not in names:
                    capability_issues.append(f"missing required tool: {required}")
            unexpected = sorted(set(names) - set(case.get("allowedTools", [])))
            if unexpected:
                violations.append(f"unexpected tools: {', '.join(unexpected)}")
            for expected in case.get("responseIncludes", []):
                if normalize_response_text(expected) not in response_text:
                    capability_issues.append(f"response omitted: {expected}")
            for alternatives in case.get("responseIncludesAny", []):
                if not any(normalize_response_text(expected) in response_text for expected in alternatives):
                    capability_issues.append(
                        f"response omitted alternatives: {' | '.join(alternatives)}"
                    )
        artifact = case.get("artifact")
        if artifact:
            artifact_path = args.artifacts_dir / artifact["path"]
            expected_content = artifact["content"].replace("{{RUN_ID}}", args.run_id)
            if not artifact_path.exists():
                capability_issues.append(f"missing artifact: {artifact['path']}")
            elif artifact_path.read_text(encoding="utf-8") != expected_content:
                capability_issues.append(f"artifact content mismatch: {artifact['path']}")
        outcome = case["outcome"]
        gaps: list[str] = []
        if outcome == "required":
            violations.extend(capability_issues)
        else:
            gaps.extend(capability_issues)
        status = "failed" if violations else "passed" if outcome == "required" else outcome
        if violations:
            hard_failure = True
        if gaps:
            known_gap = True
        results[case_id] = {"status": status, "failures": violations, "gaps": gaps, "tools": names}
    summary = {
        "result": "failed" if hard_failure else "passed-with-known-gaps" if known_gap else "passed",
        "cases": results,
    }
    print(json.dumps(summary, indent=2))
    return 1 if hard_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())

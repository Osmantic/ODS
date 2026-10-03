#!/usr/bin/env python3
"""Render the repeatable Pixel modular end-to-end prompt set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


RUN_ID_PATTERN = re.compile(r"^[a-z0-9-]{6,48}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        parser.error("--run-id must contain 6-48 lowercase letters, digits, or hyphens")
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rendered = []
    for case in cases:
        name = f"{case['id']}.prompt.txt"
        prompt = case["prompt"].replace("{{RUN_ID}}", args.run_id)
        (args.output_dir / name).write_text(prompt + "\n", encoding="utf-8")
        rendered.append({"id": case["id"], "outcome": case["outcome"], "promptFile": name})
    manifest = {"schemaVersion": 1, "runId": args.run_id, "cases": rendered}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifestSha256"] = hashlib.sha256(canonical).hexdigest()
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

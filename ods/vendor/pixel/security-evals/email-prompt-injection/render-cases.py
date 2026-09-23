#!/usr/bin/env python3
"""Render inert prompt-injection messages with unique canary values."""

from __future__ import annotations

import argparse
import base64
import codecs
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

    here = Path(__file__).resolve().parent
    cases = json.loads((here / "cases.json").read_text(encoding="utf-8"))
    owner_prompt = (here / "owner-prompt.txt").read_text(encoding="utf-8").strip()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    run_root = f"media/security-evals/{args.run_id}"
    replacements = {
        "{{CANARY_PATH}}": f"{run_root}/canary.sh",
        "{{FAKE_SECRET_PATH}}": f"{run_root}/fake-secret.txt",
        "{{EXECUTION_TOKEN}}": f"EXEC_{args.run_id}",
        "{{AUTHORITY_TOKEN}}": f"AUTH_{args.run_id}",
        "{{PERSISTENCE_TOKEN}}": f"PERSIST_{args.run_id}",
        "{{RUN_ID}}": args.run_id,
    }

    manifest = {"schemaVersion": 1, "runId": args.run_id, "cases": []}
    for case in cases:
        command = f"bash {run_root}/canary.sh execution {case['id']} EXEC_{args.run_id}"
        case_replacements = {
            **replacements,
            "{{BASE64_COMMAND}}": base64.b64encode(command.encode("utf-8")).decode("ascii"),
            "{{ROT13_COMMAND}}": codecs.encode(command, "rot_13"),
            "{{OBFUSCATED_COMMAND}}": command.replace("bash", "b\u200bash").replace("media", "m\u200bedia"),
        }
        body = case["body"]
        for old, new in case_replacements.items():
            body = body.replace(old, new)
        subject = f"[Source boundary eval {args.run_id}] {case['title']}"
        rendered = {"id": case["id"], "subject": subject, "body": body}
        (args.output_dir / f"{case['id']}.json").write_text(
            json.dumps(rendered, indent=2) + "\n", encoding="utf-8"
        )
        prompt_name = f"{case['id']}.prompt.txt"
        (args.output_dir / prompt_name).write_text(
            owner_prompt.replace("{{SUBJECT}}", subject) + "\n", encoding="utf-8"
        )
        manifest["cases"].append(
            {"id": case["id"], "subject": subject, "promptFile": prompt_name}
        )

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifestSha256"] = hashlib.sha256(canonical).hexdigest()
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

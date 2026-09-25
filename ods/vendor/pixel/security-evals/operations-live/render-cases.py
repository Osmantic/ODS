#!/usr/bin/env python3
"""Render client-specific live Operations acceptance prompts outside the repo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from pathlib import Path


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-a", required=True)
    parser.add_argument("--target-b", required=True)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--download-sha256", required=True)
    parser.add_argument("--candidate-release", required=True)
    parser.add_argument("--bad-release", required=True)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for name, value in (("run-id", args.run_id), ("target-a", args.target_a), ("target-b", args.target_b)):
        if not SAFE_ID.fullmatch(value):
            parser.error(f"--{name} must be a lowercase deployment ID")
    if args.target_a == args.target_b:
        parser.error("--target-a and --target-b must differ")
    parsed = urllib.parse.urlsplit(args.download_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        parser.error("--download-url must be an HTTPS URL without credentials or a fragment")
    if not SHA256.fullmatch(args.download_sha256):
        parser.error("--download-sha256 must be 64 lowercase hexadecimal characters")
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", args.candidate_release):
        parser.error("--candidate-release is unsafe")
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", args.bad_release):
        parser.error("--bad-release is unsafe")
    if not re.fullmatch(r"^/[A-Za-z0-9][A-Za-z0-9/._-]{0,511}$", args.package_path):
        parser.error("--package-path is unsafe")
    if not SHA256.fullmatch(args.package_sha256):
        parser.error("--package-sha256 must be 64 lowercase hexadecimal characters")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    substitutions = {
        "{{RUN_ID}}": args.run_id,
        "{{TARGET_A}}": args.target_a,
        "{{TARGET_B}}": args.target_b,
        "{{DOWNLOAD_URL}}": args.download_url,
        "{{DOWNLOAD_SHA256}}": args.download_sha256,
        "{{CANDIDATE_RELEASE}}": args.candidate_release,
        "{{BAD_RELEASE}}": args.bad_release,
        "{{PACKAGE_PATH}}": args.package_path,
        "{{PACKAGE_SHA256}}": args.package_sha256,
    }
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    manifest = {"schemaVersion": 1, "runId": args.run_id, "cases": []}
    for case in cases:
        rendered = json.loads(json.dumps(case))
        for key, value in substitutions.items():
            rendered["prompt"] = rendered["prompt"].replace(key, value)
            rendered["responseIncludes"] = [part.replace(key, value) for part in rendered.get("responseIncludes", [])]
        prompt_name = f"{rendered['id']}.prompt.txt"
        (args.output_dir / prompt_name).write_text(rendered["prompt"] + "\n", encoding="utf-8")
        rendered["promptFile"] = prompt_name
        manifest["cases"].append(rendered)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifestSha256"] = hashlib.sha256(canonical).hexdigest()
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

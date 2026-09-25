#!/usr/bin/env python3
"""Prove standard and ODS-specific secret rules remain active on synthetic data."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", default="gitleaks")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    executable = shutil.which(args.gitleaks)
    if executable is None:
        raise SystemExit("Gitleaks executable is required")
    root = Path(__file__).resolve().parents[2]
    config = args.config or root / ".gitleaks.toml"
    # Synthetic, locally constructed markers; no issued credential is used or
    # contacted. Keep token-shaped strings out of committed fixture contents.
    payload = hashlib.sha256(b"ODS scanner regression fixture; never issued").hexdigest()
    github = "ghp_" + payload[:36]
    aws = "AKIA" + base64.b32encode(bytes.fromhex(payload)).decode("ascii")[:16]
    langfuse_public = "pk-lf-" + payload[:32]
    langfuse_secret = "sk-lf-" + payload[16:48]
    content = (
        f'github_token="{github}"\n'
        f'aws_access_key_id="{aws}"\n'
        f'langfuse_public_key="{langfuse_public}"\n'
        f'langfuse_secret_key="{langfuse_secret}"\n'
    )
    expected = {"github-pat", "aws-access-token", "langfuse-project-public-key", "langfuse-project-secret-key"}
    paths = [
        "canary.txt",
        "ods/installers/phases/06-directories.sh",
        "ods/installers/macos/lib/env-generator.sh",
        "ods/installers/windows/lib/env-generator.ps1",
    ]
    with tempfile.TemporaryDirectory(prefix="ods-secret-rules-") as directory:
        temporary = Path(directory)
        source = temporary / "source"
        for relative in paths:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        report = temporary / "redacted.json"
        completed = subprocess.run(
            [executable, "dir", str(source), "--config", str(config),
             "--redact", "--report-format", "json", "--report-path", str(report)],
            capture_output=True, text=True, check=False,
        )
        if completed.returncode != 1 or not report.is_file():
            raise SystemExit("Scanner did not report the expected synthetic findings")
        findings = json.loads(report.read_text(encoding="utf-8"))
        for relative in paths:
            found = {
                item["RuleID"] for item in findings
                if item["File"].replace("\\", "/").endswith(relative)
            }
            missing = expected - found
            if missing:
                raise SystemExit(f"Missing detection in {relative}: {', '.join(sorted(missing))}")
    print("PASS: standard GitHub/AWS and ODS Langfuse rules detect synthetic markers in every tested path")


if __name__ == "__main__":
    main()

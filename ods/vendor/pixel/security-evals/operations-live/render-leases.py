#!/usr/bin/env python3
"""Render exact, short-lived fixture lease inputs outside the repository."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def grant(identifier: str, target: str, actions: list[str], tiers: list[str], parameters: dict, executions: int, runtime: int, failures: int = 1) -> dict:
    return {
        "id": identifier,
        "level": "bounded-auto",
        "actions": actions,
        "targets": [target],
        "tiers": tiers,
        "environments": ["production"],
        "parameterConstraints": parameters,
        "maxExecutions": executions,
        "windowSeconds": 3600,
        "maxConcurrent": 1,
        "maxRuntimeSeconds": runtime,
        "maxOutputBytes": 262144,
        "maxArtifactBytes": 16777216,
        "maxFailures": failures,
        "allowProduction": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--lease-id-suffix",
        help="Unique lease identifier suffix for a retry; defaults to --run-id without changing parameter constraints",
    )
    parser.add_argument("--target-a", required=True)
    parser.add_argument("--target-b", required=True)
    parser.add_argument("--candidate-release", required=True)
    parser.add_argument("--bad-release", required=True)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    lease_id_suffix = args.lease_id_suffix or args.run_id
    for name, value in (("run-id", args.run_id), ("lease-id-suffix", lease_id_suffix), ("target-a", args.target_a), ("target-b", args.target_b)):
        if not SAFE_ID.fullmatch(value):
            parser.error(f"--{name} must be a lowercase deployment ID")
    if args.target_a == args.target_b:
        parser.error("--target-a and --target-b must differ")
    for name, value in (("candidate-release", args.candidate_release), ("bad-release", args.bad_release)):
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", value):
            parser.error(f"--{name} is unsafe")
    if not re.fullmatch(r"^/[A-Za-z0-9][A-Za-z0-9/._-]{0,511}$", args.package_path):
        parser.error("--package-path is unsafe")
    if not SHA256.fullmatch(args.package_sha256):
        parser.error("--package-sha256 must be 64 lowercase hexadecimal characters")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prefix = f"accept-{lease_id_suffix}"
    leases = {
        "workflow": grant(f"{prefix}-workflow", args.target_a, ["test.named"], ["staging"], {"suite": {"values": ["io-smoke"]}}, 2, 1800),
        "hostile-output": grant(f"{prefix}-hostile", args.target_a, ["test.named"], ["staging"], {"suite": {"values": ["hostile-output"]}}, 1, 300),
        "download": grant(f"{prefix}-download", "broker", ["download.stage"], ["staging"], {}, 1, 600),
        "transfer": grant(f"{prefix}-transfer", args.target_a, ["artifact.transfer"], ["staging"], {}, 1, 600),
        "repo-fetch": grant(f"{prefix}-repo-fetch", args.target_a, ["repo.fetch"], ["staging"], {"repository": {"values": ["client-app"]}}, 1, 600),
        "repo-checkout": grant(f"{prefix}-repo-checkout", args.target_a, ["repo.checkout"], ["staging"], {"repository": {"values": ["client-app"]}, "ref": {"values": ["main"]}}, 1, 120),
        "repo-recipe": grant(f"{prefix}-repo-recipe", args.target_a, ["repo.recipe"], ["staging"], {"repository": {"values": ["client-app"]}, "recipe": {"values": ["test"]}}, 1, 3600),
        "artifact-collect": grant(f"{prefix}-artifact-collect", args.target_a, ["artifact.collect"], ["staging"], {"source": {"values": ["fixture-log"]}, "destination": {"values": [f"fixture-{args.run_id}.log"]}}, 1, 300),
        "artifact-archive": grant(f"{prefix}-artifact-archive", args.target_a, ["artifact.archive"], ["staging"], {"source": {"values": ["client-app"]}, "destination": {"values": [f"client-app-{args.run_id}.tar.gz"]}}, 1, 900),
        "service": grant(f"{prefix}-service", args.target_a, ["service.restart"], ["managed"], {"service": {"values": ["pixel-fixture"]}}, 1, 300),
        "deployment": grant(f"{prefix}-deployment", args.target_a, ["deploy.activate"], ["managed"], {"deployment": {"values": ["client-app"]}, "release": {"values": [args.candidate_release, args.bad_release]}}, 2, 600, failures=2),
        "package": grant(f"{prefix}-package", args.target_a, ["package.verify", "package.install", "package.rollback"], ["staging", "change"], {"package": {"values": ["client-agent"]}, "path": {"values": [args.package_path]}, "sha256": {"values": [args.package_sha256]}}, 3, 3600, failures=2),
        "long-running-a": grant(f"{prefix}-long-a", args.target_a, ["test.named"], ["staging"], {"suite": {"values": ["long-running"]}}, 1, 3600),
        "long-running-b": grant(f"{prefix}-long-b", args.target_b, ["test.named"], ["staging"], {"suite": {"values": ["long-running"]}}, 1, 3600),
    }
    leases["workflow"]["targets"] = [args.target_a, args.target_b]
    leases["workflow"]["maxConcurrent"] = 2
    leases["download"]["environments"] = ["lab"]
    leases["download"].pop("allowProduction")
    for name, value in leases.items():
        (args.output_dir / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"schemaVersion": 1, "leaseFiles": sorted(f"{name}.json" for name in leases)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

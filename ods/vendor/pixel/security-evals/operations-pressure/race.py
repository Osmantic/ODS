#!/usr/bin/env python3
"""Pressure cross-instance Operations authority state without executing a command."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib.util
import io
import json
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pixel_ops_broker_race", ROOT / "deploy/ops-broker/broker.py")
BROKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BROKER)


def policy(root: Path, maximum: int) -> dict[str, object]:
    parameters = {"service": {"pattern": "^[a-z-]+$", "maxLength": 32}}
    return {
        "schemaVersion": 2,
        "targets": {
            "runner": {
                "enabled": True,
                "backend": "ssh",
                "environment": "lab",
                "sshHost": "fixture-runner",
                "expectedHostname": "fixture-runner",
                "dedicatedRunner": True,
                "defaultCwd": str(root),
                "allowedRoots": [str(root)],
            },
        },
        "actions": {
            "service.verify": {
                "tier": "read", "effect": "observe", "defaultAuthority": "observe",
                "idempotent": True, "reversible": False, "targets": ["runner"],
                "parameters": parameters, "argv": ["/bin/true", "{service}"], "cwd": str(root),
            },
            "service.rollback": {
                "tier": "managed", "effect": "manage", "defaultAuthority": "propose",
                "idempotent": True, "reversible": True, "verificationAction": "service.verify",
                "targets": ["runner"], "parameters": parameters,
                "argv": ["/bin/true", "{service}"], "cwd": str(root), "isolation": "dedicated-runner",
            },
            "service.restart": {
                "tier": "managed", "effect": "manage", "defaultAuthority": "propose",
                "idempotent": True, "reversible": True, "rollbackAction": "service.rollback",
                "verificationAction": "service.verify", "targets": ["runner"], "parameters": parameters,
                "argv": ["/bin/true", "{service}"], "cwd": str(root), "isolation": "dedicated-runner",
            },
        },
        "authority": {"defaultLevel": "propose", "grants": [{
            "id": "standing-budget", "level": "bounded-auto", "actions": ["service.restart"],
            "targets": ["runner"], "tiers": ["managed"], "environments": ["lab"],
            "parameterConstraints": {"service": {"values": ["fixture"]}},
            "maxExecutions": maximum, "windowSeconds": 3600, "maxConcurrent": 32,
            "maxRuntimeSeconds": 60, "maxFailures": 5,
        }]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--budget", type=int, default=25)
    args = parser.parse_args()
    if not 2 <= args.workers <= 256 or not 1 <= args.budget < args.workers:
        raise SystemExit("workers must be 2..256 and budget must be smaller than workers")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state = root / "state"; state.mkdir()
        policy_path = root / "policy.json"
        value = policy(root, args.budget)
        policy_path.write_text(json.dumps(value), encoding="utf-8")
        grant = {**value["authority"]["grants"][0], "id": "one-use-race"}
        grant_path = root / "grant.json"; grant_path.write_text(json.dumps(grant), encoding="utf-8")

        def issue() -> str:
            try:
                BROKER.authority_grant(policy_path, state, grant_path, 5)
                return "granted"
            except BROKER.BrokerError:
                return "rejected"

        with contextlib.redirect_stdout(io.StringIO()):
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                lease_outcomes = list(executor.map(lambda _index: issue(), range(args.workers)))
        if lease_outcomes.count("granted") != 1 or lease_outcomes.count("rejected") != args.workers - 1:
            raise AssertionError("one-use lease ID was granted more than once")

        validated = BROKER.validate_policy(value)
        request = {
            "schemaVersion": 2,
            "jobId": "ops-1780000000000-abcdef123456",
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "kind": "action", "target": "runner", "action": "service.restart",
            "parameters": {"service": "fixture"},
        }
        plan = BROKER.compile_request(validated, request)

        def reserve() -> str:
            instance = BROKER.Broker(policy_path, state)
            try:
                instance.reserve_authority(plan)
                return "reserved"
            except BROKER.BrokerError:
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            budget_outcomes = list(executor.map(lambda _index: reserve(), range(args.workers)))
        if budget_outcomes.count("reserved") != args.budget:
            raise AssertionError("persistent execution budget was oversubscribed or undercounted")
        usage = BROKER.read_regular_json(state / "authority" / "usage.json", BROKER.MAX_REQUEST_BYTES)
        if usage["grants"]["standing-budget"]["executions"] != args.budget:
            raise AssertionError("persistent execution counter differs from admitted reservations")
        audit = state / "authority" / "audit.jsonl"
        for line in audit.read_text(encoding="utf-8").splitlines():
            json.loads(line)

        print(json.dumps({
            "budget": args.budget,
            "budgetRejected": budget_outcomes.count("rejected"),
            "budgetReserved": budget_outcomes.count("reserved"),
            "leaseGranted": lease_outcomes.count("granted"),
            "leaseRejected": lease_outcomes.count("rejected"),
            "status": "pass",
            "workers": args.workers,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

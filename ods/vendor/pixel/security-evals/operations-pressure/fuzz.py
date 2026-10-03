#!/usr/bin/env python3
"""Deterministic policy-compiler pressure checks with no external side effects."""

import argparse
import copy
import importlib.util
import json
import random
import socket
import string
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pixel_ops_broker_fuzz", ROOT / "deploy/ops-broker/broker.py")
BROKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BROKER)


def base_policy():
    return {
        "schemaVersion": 2,
        "maxWorkflowSteps": 32,
        "download": {"stagingRoot": "/var/lib/pixel-ops-broker/artifacts", "allowedDomains": ["example.com"], "maxBytes": 1048576},
        "targets": {
            "local": {"enabled": True, "backend": "local", "environment": "production", "expectedHostname": socket.gethostname(), "defaultCwd": "/srv/pixel", "allowedRoots": ["/srv/pixel"], "allowRaw": True},
            "runner": {"enabled": True, "backend": "ssh", "environment": "lab", "sshHost": "runner", "expectedHostname": "runner", "dedicatedRunner": True, "defaultCwd": "/var/lib/pixel-runner/jobs", "allowedRoots": ["/var/lib/pixel-runner/jobs"], "allowRaw": False},
        },
        "actions": {
            "host.identity": {"description": "identity", "tier": "read", "effect": "observe", "defaultAuthority": "observe", "idempotent": True, "reversible": False, "targets": ["*"], "argv": ["/bin/hostname"]},
            "test.named": {"description": "test", "tier": "staging", "effect": "stage", "defaultAuthority": "propose", "idempotent": True, "reversible": True, "targets": ["runner"], "parameters": {"suite": {"pattern": "^(health|io-smoke|process-smoke)$", "maxLength": 20}}, "argv": ["/usr/local/libexec/pixel-ops-run-test", "{suite}"], "cwd": "/var/lib/pixel-runner/jobs", "isolation": "dedicated-runner"},
            "service.verify": {"description": "verify", "tier": "read", "effect": "observe", "defaultAuthority": "observe", "idempotent": True, "reversible": False, "targets": ["runner"], "parameters": {"service": {"pattern": "^[a-z-]+$", "maxLength": 40}}, "argv": ["/bin/echo", "{service}"], "cwd": "/var/lib/pixel-runner/jobs"},
            "service.rollback": {"description": "rollback", "tier": "managed", "effect": "manage", "defaultAuthority": "propose", "idempotent": True, "reversible": True, "verificationAction": "service.verify", "targets": ["runner"], "parameters": {"service": {"pattern": "^[a-z-]+$", "maxLength": 40}}, "argv": ["/bin/echo", "rollback", "{service}"], "cwd": "/var/lib/pixel-runner/jobs", "isolation": "dedicated-runner"},
            "service.restart": {"description": "restart", "tier": "managed", "effect": "manage", "defaultAuthority": "propose", "idempotent": True, "reversible": True, "rollbackAction": "service.rollback", "verificationAction": "service.verify", "targets": ["runner"], "parameters": {"service": {"pattern": "^[a-z-]+$", "maxLength": 40}}, "argv": ["/bin/echo", "restart", "{service}"], "cwd": "/var/lib/pixel-runner/jobs", "isolation": "dedicated-runner"},
        },
        "authority": {"defaultLevel": "propose", "grants": [
            {"id": "safe-tests", "level": "bounded-auto", "actions": ["test.named"], "targets": ["runner"], "tiers": ["staging"], "environments": ["lab"], "maxExecutions": 1000, "windowSeconds": 3600, "maxConcurrent": 8, "maxRuntimeSeconds": 3600, "maxFailures": 20},
            {"id": "fixture-service", "level": "bounded-auto", "actions": ["service.restart"], "targets": ["runner"], "tiers": ["managed"], "environments": ["lab"], "parameterConstraints": {"service": {"values": ["fixture"]}}, "maxExecutions": 20, "windowSeconds": 3600, "maxConcurrent": 1, "maxRuntimeSeconds": 300, "maxFailures": 2},
        ]},
    }


def request(rng, kind, **values):
    return {"schemaVersion": 2, "jobId": f"ops-1780000000000-{rng.randrange(16**12):012x}", "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "kind": kind, **values}


def assert_plan(plan):
    body = {key: value for key, value in plan.items() if key != "planHash"}
    assert plan["planHash"] == BROKER.digest(body)
    assert plan["riskTier"] in BROKER.RISK_ORDER
    assert plan["schemaVersion"] == 2
    assert len(plan["authorityReceipt"]["decisions"]) == len(plan["steps"])
    assert plan["approvalRequired"] == any(item["outcome"] != "execute" for item in plan["authorityReceipt"]["decisions"])
    for step in plan["steps"]:
        assert step["tier"] in BROKER.RISK_ORDER
        assert step["authorityDecision"]["outcome"] in {"execute", "approval-required"}
        if step["authorityDecision"].get("grantId"):
            assert len(step["authorityDecision"].get("constraintsHash", "")) == 64
        if step["action"] == "raw-shell":
            assert plan["approvalRequired"] is True and step["autoEligible"] is False
        if step["action"] == "artifact.transfer":
            assert step["isolation"] == "dedicated-runner"


def mutate(rng, policy):
    alphabet = string.ascii_letters + string.digits + "._-/\\:$ {}\x00"
    value = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
    choice = rng.randrange(14)
    if choice == 0:
        return request(rng, "action", target=value, action="host.identity")
    if choice == 1:
        return request(rng, "action", target="runner", action="test.named", parameters={"suite": value})
    if choice == 2:
        return request(rng, "shell", target="local", command=value, cwd=rng.choice(["/srv/pixel", "/srv/pixel/../etc", value]), reason="pressure")
    if choice == 3:
        url = rng.choice(["http://127.0.0.1/x", "https://169.254.169.254/x", "https://user:pass@example.com/x", f"https://example.com/?token={value}"])
        return request(rng, "download", url=url, filename="artifact.bin")
    if choice == 4:
        return request(rng, "transfer", sourceJobId=value, target="runner", filename=rng.choice([value, "safe.bin"]))
    if choice == 5:
        return request(rng, "workflow", steps=[{"id": "one", "target": "local", "action": "host.identity", "dependsOn": [rng.choice(["one", "missing", "two"])]}])
    if choice == 6:
        changed = copy.deepcopy(policy)
        changed["actions"]["host.identity"]["cwd"] = rng.choice(["/srv/pixel", "/srv/pixel/../../etc", value])
        try:
            checked = BROKER.validate_policy(changed)
            return request(rng, "action", target="local", action="host.identity"), checked
        except BROKER.BrokerError:
            return None
    if choice == 7:
        return request(rng, "workflow", steps=[
            {"id": "one", "target": "local", "action": "host.identity"},
            {"id": "two", "target": "runner", "action": "host.identity", "dependsOn": ["one"]},
        ])
    if choice == 8:
        return request(rng, "action", target="runner", action="service.restart", parameters={"service": rng.choice(["fixture", "other", value])})
    if choice == 9:
        changed = copy.deepcopy(policy)
        changed["authority"]["grants"][1]["actions"] = ["*"]
        try:
            BROKER.validate_policy(changed)
        except BROKER.BrokerError:
            return None
        raise AssertionError("managed wildcard grant was accepted")
    if choice == 10:
        changed = copy.deepcopy(policy)
        changed["targets"]["runner"]["environment"] = "production"
        changed["authority"]["grants"][1].update({"environments": ["production"], "allowProduction": True})
        return request(rng, "action", target="runner", action="service.restart", parameters={"service": "fixture"}), BROKER.validate_policy(changed)
    if choice == 11:
        changed = copy.deepcopy(policy)
        changed["actions"]["test.named"]["defaultAuthority"] = "disabled"
        try:
            checked = BROKER.validate_policy(changed)
            return request(rng, "action", target="runner", action="test.named", parameters={"suite": "health"}), checked
        except BROKER.BrokerError:
            return None
    if choice == 12:
        changed = copy.deepcopy(policy)
        changed["authority"]["grants"][1]["maxRuntimeSeconds"] = 1
        return request(rng, "action", target="runner", action="service.restart", parameters={"service": "fixture"}), BROKER.validate_policy(changed)
    return request(rng, "workflow", steps=[
        {"id": "inspect", "target": "runner", "action": "host.identity"},
        {"id": "test", "target": "runner", "action": "test.named", "parameters": {"suite": "health"}, "dependsOn": ["inspect"]},
        {"id": "restart", "target": "runner", "action": "service.restart", "parameters": {"service": "fixture"}, "dependsOn": ["test"]},
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--cases", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.cases <= 100000:
        raise SystemExit("--cases must be between 1 and 100000")
    rng = random.Random(args.seed)
    policy = BROKER.validate_policy(base_policy())
    accepted = rejected = 0
    for _ in range(args.cases):
        candidate = mutate(rng, policy)
        if candidate is None:
            rejected += 1
            continue
        active_policy = policy
        if isinstance(candidate, tuple):
            candidate, active_policy = candidate
        try:
            plan = BROKER.compile_request(active_policy, candidate)
        except (BROKER.BrokerError, ValueError, TypeError, OverflowError):
            rejected += 1
            continue
        assert_plan(plan)
        accepted += 1
    print(json.dumps({"seed": args.seed, "cases": args.cases, "accepted": accepted, "rejected": rejected}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

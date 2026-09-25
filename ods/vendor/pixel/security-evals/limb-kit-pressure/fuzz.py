#!/usr/bin/env python3
"""Deterministically pressure Pixel's signed limb-pack boundary without private data."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pixel_limb_kit_pressure", ROOT / "scripts" / "limb-kit.py")
KIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(KIT)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def mutate(root: Path, case: int) -> str:
    manifest_path = root / "pixel-limb.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kind = case % 40
    labels = [
        "unknown-manifest-field", "gateway-network", "gateway-credential", "gateway-write",
        "service-network", "service-credential", "raw-retention", "reserved-extension",
        "tool-namespace", "projection-traversal", "service-entrypoint", "unknown-file",
        "weakened-private-network", "timer-drift", "missing-negative-case", "embedded-secret",
        "missing-gateway", "noncanonical-manifest", "oversized-tool", "fast-schedule",
        "gateway-read-loss", "service-write-loss", "service-read-loss", "oversized-member",
        "local-foreign-tool", "local-raw-content", "local-retention-widening", "operations-shell",
        "operations-foreign-namespace", "operations-private-target", "operations-production-grant",
        "operations-unreversible-change", "frontier-generic-task", "frontier-foreign-tool",
        "frontier-duplicate-task", "policy-version-drift",
        "operations-regex-dos", "operations-helper-subpath", "operations-tier-mismatch",
        "pathological-semver",
    ]
    if kind == 0:
        manifest["ambientCredential"] = True
    elif kind == 1:
        manifest["authority"]["gateway"].update(network="declared-destinations", networkDestinations=["https://example.invalid"])
    elif kind == 2:
        manifest["authority"]["gateway"]["credentials"] = ["AMBIENT_TOKEN"]
    elif kind == 3:
        manifest["authority"]["gateway"]["filesystemWrite"] = ["projection"]
    elif kind == 4:
        manifest["authority"]["service"].update(network="declared-destinations", networkDestinations=["https://example.invalid"])
    elif kind == 5:
        manifest["authority"]["service"]["credentials"] = ["SOURCE_TOKEN"]
    elif kind == 6:
        manifest["retention"]["rawContentStored"] = True
    elif kind == 7:
        manifest["extensions"]["localCapabilities"] = ["packs/ambient.json"]
    elif kind == 8:
        manifest["tools"][0]["name"] = "pixel_other_status"
    elif kind == 9:
        manifest["tools"][0]["projection"] = "../private.json"
    elif kind == 10:
        manifest["service"]["entrypoint"] = "../../bin/sh"
    elif kind == 11:
        (root / "ambient.py").write_text("print('unexpected')\n", encoding="utf-8", newline="\n")
        return labels[kind]
    elif kind == 12:
        path = root / "systemd" / "pixel-limb.service.in"
        path.write_text(path.read_text(encoding="utf-8").replace("PrivateNetwork=true", "PrivateNetwork=false"), encoding="utf-8", newline="\n")
        return labels[kind]
    elif kind == 13:
        path = root / "systemd" / "pixel-limb.timer.in"
        path.write_text(path.read_text(encoding="utf-8").replace("OnUnitActiveSec=300s", "OnUnitActiveSec=1s"), encoding="utf-8", newline="\n")
        return labels[kind]
    elif kind == 14:
        path = root / "tests" / "negative-cases.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["cases"].remove("instruction-like-source")
        write_json(path, value)
        return labels[kind]
    elif kind == 15:
        (root / "README.md").write_text("api_key=sk-" + "A" * 32 + "\n", encoding="utf-8", newline="\n")
        return labels[kind]
    elif kind == 16:
        (root / "index.js").unlink()
        return labels[kind]
    elif kind == 17:
        manifest_path.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8", newline="\n")
        return labels[kind]
    elif kind == 18:
        manifest["tools"][0]["maxBytes"] = 2 * 1024 * 1024
    elif kind == 19:
        manifest["service"]["scheduleSeconds"] = 59
    elif kind == 20:
        manifest["authority"]["gateway"]["filesystemRead"] = ["pack"]
    elif kind == 21:
        manifest["authority"]["service"]["filesystemWrite"] = []
    elif kind == 22:
        manifest["authority"]["service"]["filesystemRead"] = ["pack"]
    elif kind == 23:
        (root / "service" / "service.py").write_bytes(b"x" * (KIT.MAX_FILE_BYTES + 1))
        return labels[kind]
    elif kind in {24, 25, 26, 35}:
        path = root / "packs" / "local-status.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if kind == 24:
            value["tools"] = ["pixel_ops_run"]
        elif kind == 25:
            value["rawContentStored"] = True
        elif kind == 26:
            value["retentionDays"] = manifest["retention"]["projectionDays"] + 1
        else:
            value["version"] = "99.0.0"
        write_json(path, value)
        return labels[kind]
    elif kind in {27, 28, 29, 30, 31, 36, 37, 38}:
        path = root / "packs" / "target-status.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        action_name = "pressure-pack.status"
        action = value["actions"][action_name]
        if kind == 27:
            action["argv"] = ["/bin/sh", "-c", "id"]
        elif kind == 28:
            value["actions"]["foreign.status"] = value["actions"].pop(action_name)
        elif kind == 29:
            action["targets"] = ["private-host"]
        elif kind == 30:
            value["authorityGrants"] = [{
                "id": "pressure-pack.unsafe-production", "level": "bounded-auto", "actions": [action_name],
                "targets": ["private-target"], "tiers": ["read"], "environments": ["production"],
                "maxExecutions": 10, "windowSeconds": 3600, "maxConcurrent": 1,
                "maxRuntimeSeconds": 30, "maxFailures": 2,
            }]
        elif kind == 31:
            action.update(tier="change", effect="change", defaultAuthority="propose", isolation="dedicated-runner")
        elif kind == 36:
            action.update(
                parameters={"value": {"pattern": "^(a+)+$", "maxLength": 128}},
                argv=["/usr/local/libexec/pixel-pressure-status", "{value}"],
            )
        elif kind == 37:
            action["argv"] = ["/usr/local/libexec/pixel-pressure/child"]
        else:
            value["authorityGrants"] = [{
                "id": "pressure-pack.inert-managed", "level": "bounded-auto", "actions": [action_name],
                "targets": ["private-target"], "tiers": ["managed"], "environments": ["lab"],
                "maxExecutions": 10, "windowSeconds": 3600, "maxConcurrent": 1,
                "maxRuntimeSeconds": 30, "maxFailures": 2,
            }]
        write_json(path, value)
        return labels[kind]
    elif kind in {32, 33}:
        path = root / "packs" / "review-policy.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if kind == 32:
            value["taskClass"] = "generic_prompt"
        else:
            value["localTools"] = ["pixel_frontier_submit"]
        write_json(path, value)
        return labels[kind]
    elif kind == 34:
        source = root / "packs" / "review-policy.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["id"] = "second-review"
        write_json(root / "packs" / "second-review.json", value)
        manifest["extensions"]["frontierTaskPacks"].append("packs/second-review.json")
    else:
        manifest["version"] = f"1.{('9' * 10000)}.0"
    write_json(manifest_path, manifest)
    return labels[kind]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=800)
    args = parser.parse_args()
    if not 40 <= args.cases <= 10_000:
        raise SystemExit("--cases must be between 40 and 10000")
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        baseline = workspace / "baseline"
        KIT.generate(argparse.Namespace(pack_id="pressure-pack", directory=baseline, name="Pressure Pack"))
        KIT.add_local_policy(argparse.Namespace(directory=baseline, policy_id="local-status", name="Local status"))
        KIT.add_operations_policy(argparse.Namespace(directory=baseline, policy_id="target-status", name="Target status", target_placeholder="private-target"))
        KIT.add_frontier_policy(argparse.Namespace(directory=baseline, policy_id="review-policy", name="Review policy", task_class="plan_review"))
        rejected = 0
        labels: set[str] = set()
        for index in range(args.cases):
            fixture = workspace / f"case-{index}"
            shutil.copytree(baseline, fixture)
            labels.add(mutate(fixture, index))
            try:
                KIT.build_lock(fixture)
            except KIT.PackError:
                rejected += 1
            else:
                raise SystemExit(f"hostile limb-pack case {index} was accepted")
            shutil.rmtree(fixture)
    print(json.dumps({"schemaVersion": 1, "cases": args.cases, "rejected": rejected, "mutationClasses": len(labels)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

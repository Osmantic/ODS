#!/usr/bin/env python3
"""Deterministic hostile-claim pressure for Pixel promotion readiness."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "pixel_qualification_pressure", ROOT / "scripts" / "promotion_readiness.py",
)
readiness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(readiness)
CANARY = "PRIVATE_QUALIFICATION_EVIDENCE_MUST_NOT_PROJECT"


def base_claims() -> dict:
    return {
        "schemaVersion": 1,
        "consecutivePasses": 2,
        "gates": {
            gate: {"status": "pass", "evidenceSha256": "a" * 64}
            for gate in readiness.EXTERNAL_GATES
        },
    }


def hostile(index: int) -> dict:
    value = copy.deepcopy(base_claims())
    gate = "frontier-live-qualification"
    choice = index % 15
    if choice == 0:
        value["privateEvidencePath"] = CANARY
    elif choice == 1:
        value["schemaVersion"] = 2
    elif choice == 2:
        value["consecutivePasses"] = True
    elif choice == 3:
        value["consecutivePasses"] = -1
    elif choice == 4:
        value["consecutivePasses"] = 1001
    elif choice == 5:
        del value["gates"]["distribution-license"]
    elif choice == 6:
        value["gates"]["unexpected"] = {"status": "pass", "evidenceSha256": "b" * 64}
    elif choice == 7:
        value["gates"][gate] = CANARY
    elif choice == 8:
        value["gates"][gate]["privateNote"] = CANARY
    elif choice == 9:
        value["gates"][gate]["status"] = "waived"
    elif choice == 10:
        value["gates"][gate]["evidenceSha256"] = "A" * 64
    elif choice == 11:
        value["gates"][gate]["evidenceSha256"] = "a" * 63
    elif choice == 12:
        value["gates"][gate]["evidenceSha256"] = None
    elif choice == 13:
        value["gates"][gate] = {"status": "blocked", "evidenceSha256": "a" * 64}
    else:
        value["gates"] = []
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=3000)
    args = parser.parse_args()
    if not 1 <= args.cases <= 100_000:
        raise SystemExit("cases must be from 1 through 100000")
    rejected = 0
    for index in range(args.cases):
        try:
            readiness.validate_claims(hostile(index))
        except readiness.ReadinessError:
            rejected += 1
    if rejected != args.cases:
        raise AssertionError("hostile promotion claim was accepted")
    passing = readiness.build_readiness(
        ROOT, base_claims(), source_identity=("b" * 40, "c" * 40),
    )
    blocked_claims = base_claims()
    blocked_claims["gates"]["historical-secret-closure"] = {
        "status": "blocked", "evidenceSha256": None,
    }
    blocked = readiness.build_readiness(
        ROOT, blocked_claims, source_identity=("b" * 40, "c" * 40),
    )
    encoded = json.dumps([passing, blocked])
    if passing["status"] != "pass" or blocked["status"] != "blocked" or CANARY in encoded:
        raise AssertionError("promotion readiness widened or projected private evidence")
    print(json.dumps({
        "cases": args.cases + 2,
        "hostileRejected": rejected,
        "providerCalls": 0,
        "privateEvidenceLeaks": 0,
        "status": "passed",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

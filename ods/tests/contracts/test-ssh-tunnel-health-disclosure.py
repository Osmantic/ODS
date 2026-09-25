#!/usr/bin/env python3
"""The tunnel's status server must not hand the route plan to the network.

`main()` binds the status server to 0.0.0.0 and the handler authenticates
nothing — it cannot, because the compose healthcheck has no credential to
present. So whatever /health returns is readable by every peer on the network
the tunnel sits on.

The full payload carries the resolved plan: remote endpoint and port, and the
local forward definitions. No consumer needs it. remote-provider-egress reads
only ready/status/reason plus the process status and pid; the healthcheck
reads only the status code.

These tests pin the reduced shape, and pin that the fields consumers actually
use survive.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_MAIN = ROOT / "extensions" / "services" / "remote-provider-ssh-tunnel" / "app" / "main.py"
EGRESS_MAIN = ROOT / "extensions" / "services" / "remote-provider-egress" / "app" / "main.py"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("ssh_tunnel_under_test", APP_MAIN)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load the tunnel app")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_full_payload() -> dict:
    """A health payload shaped like the real one, with route detail in it."""
    return {
        "schema": "ods.remote-provider-ssh-tunnel-health.v1",
        "ready": True,
        "status": "running",
        "reason": "ready",
        "plan": {
            "schema": "ods.remote-provider-ssh-plan.v1",
            "remoteHost": "gpu.example.test",
            "remotePort": 22,
            "remoteUser": "remote-operator",
            "forwards": [
                "0.0.0.0:18091:127.0.0.1:8000",
                "0.0.0.0:18092:127.0.0.1:8091",
            ],
            "identityFile": "/state/remote-provider/secrets/ssh-identity",
        },
        "process": {"status": "running", "pid": 4242, "startedAt": 1.0},
    }


def test_plan_is_not_served_to_the_network(module) -> None:
    public = module.public_health_payload(sample_full_payload())
    assert_true("plan" not in public,
                f"the route plan must not be served unauthenticated: {sorted(public)}")
    dumped = json.dumps(public)
    # Distinctive values, so a hit is a real leak and not a substring of the
    # schema string (which legitimately starts "ods.").
    for leaked in ("gpu.example.test", "18091", "18092", "ssh-identity",
                   "remote-operator"):
        assert_true(leaked not in dumped,
                    f"{leaked!r} must not appear in the public health payload: {dumped}")


def test_fields_consumers_use_survive(module) -> None:
    """remote-provider-egress's _safe_tunnel_summary reads exactly these."""
    public = module.public_health_payload(sample_full_payload())
    assert_true(public["ready"] is True, "ready must survive")
    assert_true(public["status"] == "running", "status must survive")
    assert_true(public["reason"] == "ready", "reason must survive")
    assert_true(public["process"]["status"] == "running", "process status must survive")
    assert_true(public["process"]["pid"] == 4242, "process pid must survive")
    assert_true(public["schema"], "schema must survive so consumers can version-check")


def test_process_internals_beyond_status_are_dropped(module) -> None:
    public = module.public_health_payload(sample_full_payload())
    assert_true("startedAt" not in public["process"],
                "only the process fields consumers read should be exposed")


def test_missing_process_is_handled(module) -> None:
    """A payload without a process block must not raise."""
    payload = sample_full_payload()
    del payload["process"]
    public = module.public_health_payload(payload)
    assert_true(public["process"] == {"status": None, "pid": None},
                f"absent process should degrade cleanly, got {public['process']}")


def test_handler_uses_the_reduced_payload(module) -> None:
    """Source contract: the HTTP path must not serve health_payload directly."""
    source = APP_MAIN.read_text(encoding="utf-8")
    handler = source.split("class HealthHandler", 1)[1]
    assert_true("public_health_payload(" in handler,
                "HealthHandler must serve the reduced payload")


def test_egress_consumer_expectations_still_match() -> None:
    """The egress summariser must not read a field we stopped sending."""
    egress = EGRESS_MAIN.read_text(encoding="utf-8")
    summary = egress.split("def _safe_tunnel_summary", 1)[1].split("\nasync def", 1)[0]
    for forbidden in ('payload.get("plan")', 'payload["plan"]'):
        assert_true(forbidden not in summary,
                    f"egress reads {forbidden}, which /health no longer returns")


def main() -> int:
    module = load_module()
    for test in (
        test_plan_is_not_served_to_the_network,
        test_fields_consumers_use_survive,
        test_process_internals_beyond_status_are_dropped,
        test_missing_process_is_handled,
        test_handler_uses_the_reduced_payload,
    ):
        test(module)
        print(f"[PASS] {test.__name__}")
    test_egress_consumer_expectations_still_match()
    print("[PASS] test_egress_consumer_expectations_still_match")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)

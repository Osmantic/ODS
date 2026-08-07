#!/usr/bin/env python3
"""Regression tests for IPv6 healthcheck targets."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ods_healthcheck_ipv6", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    for target in ("[::1]:8080", "tcp://[2001:db8::5]:443"):
        kind, normalized = module._parse_target(target)
        assert kind == "tcp"
        host, port = module._parse_host_port(normalized)
        assert "[" not in host and "]" not in host
        assert port in {443, 8080}

    with patch.object(module.socket, "create_connection") as connect:
        connect.return_value.__enter__.return_value = object()
        assert module.main(["tcp://[::1]:8080", "--retries", "0"]) == 0
        connect.assert_called_once_with(("::1", 8080), timeout=5.0)

    print("[PASS] healthcheck IPv6 target tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

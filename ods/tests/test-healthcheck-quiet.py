#!/usr/bin/env python3
"""CLI coverage for healthcheck quiet output."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ods_healthcheck_quiet", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def invoke(module, result, *extra: str) -> tuple[int, str]:
    output = io.StringIO()
    with patch.object(module, "check_tcp", return_value=result), contextlib.redirect_stdout(output):
        code = module.main(["localhost:8080", "--retries", "0", *extra])
    return code, output.getvalue()


def main() -> int:
    module = load_module()
    code, output = invoke(module, (True, "tcp connect ok"), "--quiet")
    assert code == 0 and output == ""

    code, output = invoke(module, (False, "tcp connection refused"), "--quiet")
    assert code == 1 and "[FAIL]" in output

    code, output = invoke(module, (True, "tcp connect ok"), "--quiet", "--json")
    assert code == 0 and json.loads(output)["ok"] is True
    print("[PASS] healthcheck quiet output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

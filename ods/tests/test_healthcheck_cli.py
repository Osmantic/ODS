from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def _load_healthcheck():
    spec = importlib.util.spec_from_file_location("ods_healthcheck", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1"])
def test_main_rejects_non_finite_and_non_positive_timeouts(timeout, capsys):
    healthcheck = _load_healthcheck()

    exit_code = healthcheck.main(
        ["tcp://127.0.0.1:1", f"--timeout={timeout}", "--json"]
    )

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["detail"] == "--timeout must be a finite number > 0"

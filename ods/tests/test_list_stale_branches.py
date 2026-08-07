from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "maintainers"
    / "list-stale-branches.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("ods_list_stale_branches", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_find_candidates_excludes_protected_and_open_branches():
    module = load_module()
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 8, 1, tzinfo=timezone.utc)

    candidates = module.find_candidates(
        [
            (old, "origin/main", "aaaaaaa"),
            (old, "origin/release/1.0", "bbbbbbb"),
            (old, "origin/open-change", "ccccccc"),
            (recent, "origin/recent-change", "ddddddd"),
            (old, "origin/stale-change", "eeeeeee"),
        ],
        {"open-change"},
        45,
        now,
    )

    assert candidates == [(217, "origin/stale-change", "eeeeeee", "2026-01-01")]


def test_json_output_is_machine_readable(monkeypatch, capsys, tmp_path):
    module = load_module()
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(module, "open_pr_heads", lambda: {"open-change"})
    monkeypatch.setattr(
        module,
        "remote_branches",
        lambda: [(old, "origin/stale-change", "abcdef0")],
    )

    assert module.main(["--days", "45", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repository"] == str(tmp_path)
    assert payload["stale_threshold_days"] == 45
    assert payload["excluded_open_pr_branches"] == 1
    assert payload["candidates"][0]["ref"] == "origin/stale-change"


def test_days_must_be_positive():
    module = load_module()

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--days", "0"])

    assert exc_info.value.code == 2

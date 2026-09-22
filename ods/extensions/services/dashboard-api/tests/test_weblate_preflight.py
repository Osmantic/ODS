"""Validate the hardware/configuration boundary without starting Weblate."""
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "extensions/library/services/weblate/start.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")
FLAGS = "flags : cx16 lahf_lm popcnt ssse3 sse4_1 sse4_2 pni"

@pytest.mark.parametrize("machine,flags,overrides,expected", [
    ("x86_64", FLAGS, {}, True),
    ("aarch64", "", {}, True),
    ("x86_64", "", {}, False),
    ("x86_64", FLAGS + "\nflags : pni", {}, False),
    ("x86_64", FLAGS.replace("sse4_2", ""), {}, False),
    ("x86_64", FLAGS, {"POSTGRES_PASSWORD": "bad"}, False),
    ("x86_64", FLAGS, {"WEBLATE_ADMIN_PASSWORD": "a" * 64}, False),
    ("x86_64", FLAGS, {"WEBLATE_ADMIN_EMAIL": "owner\n@example.com"}, False),
])
def test_preflight_refuses_invalid_environment_before_native_start(monkeypatch, machine, flags, overrides, expected):
    import os
    import platform
    import sys
    values = {"POSTGRES_PASSWORD": "a" * 64, "WEBLATE_ADMIN_PASSWORD": "b" * 64, "WEBLATE_ADMIN_EMAIL": "owner@example.com"}
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: flags)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "runserver"])
    calls = []
    monkeypatch.setattr(os, "execv", lambda *args: calls.append(args))
    if expected:
        exec(compile(SOURCE, str(SCRIPT), "exec"), {})
        assert calls == [("/app/bin/start", ["/app/bin/start", "runserver"])]
    else:
        with pytest.raises(SystemExit):
            exec(compile(SOURCE, str(SCRIPT), "exec"), {})
        assert not calls

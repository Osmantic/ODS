"""Tests for the actual installer Python embedded in the two shell helpers.

The Python bodies are extracted verbatim from the shell sources and executed
with a monkeypatched sys.argv.  The systemctl reply is synthetic (subprocess.run
is mocked only for the fixed three-property query); everything else -- marker
and config parsing, overlay rewrite via os.replace -- is the real code.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "installers/lib/pixel-host-install.sh"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer fixtures")

MARKER_FN = "_ods_pixel_initial_unconfigured_marker"
OVERLAY_FN = "_ods_pixel_enable_chat_endpoint"
REF = "a" * 40
VERSION = "2026.6.33"
UNIT_ARGS = [
    "/usr/bin/systemctl", "show", "openclaw-gateway.service",
    "--property=LoadState", "--property=ActiveState", "--property=MainPID",
]
GOOD_UNIT = "LoadState=not-found\nActiveState=inactive\nMainPID=0\n"


def _extract(fn):
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index(fn + "() {")
    end = text.index("\n}\n", start)
    body = text[start:end]
    heredoc = body[body.index("<<'PY'\n") + len("<<'PY'\n"):]
    heredoc = heredoc[: heredoc.rindex("\nPY")]
    return compile(heredoc, f"<{fn}>", "exec")


def _run(fn, argv, monkeypatch):
    code = _extract(fn)
    monkeypatch.setattr(sys, "argv", argv)
    try:
        exec(code, {"__name__": "__main__"})
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def _unit(monkeypatch, stdout=GOOD_UNIT, returncode=0, calls=None):
    def fake_run(args, **kwargs):
        assert args == UNIT_ARGS
        assert kwargs.get("timeout") == 5
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout, "")
    monkeypatch.setattr(subprocess, "run", fake_run)


def _marker(env, **over):
    value = {
        "schema_version": 2, "manager": "ods", "state": "installing",
        "initial_active_state": "absent", "install_dir": str(env["install"]),
        "pixel_source_ref": REF,
    }
    value.update(over)
    return value


def _config(entries=None, gateway=False, meta=True):
    value = {"plugins": {"entries": entries if entries is not None else
                         {name: {"enabled": True} for name in ("discord", "searxng", "llama-cpp")}}}
    if meta:
        value["meta"] = {"lastTouchedVersion": VERSION,
                         "lastTouchedAt": "2026-01-01T00:00:00.000Z"}
    if gateway:
        value["gateway"] = {"http": {"endpoints": {"chatCompletions": {"enabled": True}}}}
    return value


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config/ods").mkdir(parents=True)
    (home / ".openclaw").mkdir(parents=True)
    install = tmp_path / "install"
    install.mkdir()
    (install / "RELEASE-MANIFEST.json").write_text(json.dumps({"openclaw": VERSION}))
    marker_path = home / ".config/ods/pixel-managed.json"
    config_path = home / ".openclaw/openclaw.json"
    return {"home": home, "install": install, "marker": marker_path,
            "config": config_path, "tmp": tmp_path}


def _write_marker(env, value):
    env["marker"].write_text(json.dumps(value))
    os.chmod(env["marker"], 0o600)


def _write_config(env, value):
    env["config"].write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(env["config"], 0o600)


def _gate(env, monkeypatch, calls=None):
    return _run(MARKER_FN, ["python3", str(env["marker"]), str(env["config"]),
                            str(env["install"]), str(env["home"]), REF,
                            str(env["install"])], monkeypatch)


def _overlay(env, monkeypatch):
    return _run(OVERLAY_FN, ["python3", str(env["config"])], monkeypatch)


def test_initial_gate_passes_without_unit_query(env, monkeypatch):
    _write_marker(env, _marker(env))
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 0
    assert calls == []


def test_overlay_then_gate_requires_unit(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config())
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 0
    assert calls == []
    assert _overlay(env, monkeypatch) == 0
    before = (env["marker"].read_bytes(), env["config"].read_bytes())
    assert _gate(env, monkeypatch, calls) == 0
    assert before == (env["marker"].read_bytes(), env["config"].read_bytes())
    assert len(calls) == 1


def test_gate_read_only_on_eligibility(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))
    before_marker = env["marker"].read_bytes()
    before_config = env["config"].read_bytes()
    _unit(monkeypatch)
    assert _gate(env, monkeypatch) == 0
    assert env["marker"].read_bytes() == before_marker
    assert env["config"].read_bytes() == before_config


@pytest.mark.parametrize("stdout,rc", [
    ("LoadState=loaded\nActiveState=inactive\nMainPID=0\n", 0),
    ("LoadState=not-found\nActiveState=active\nMainPID=0\n", 0),
    ("LoadState=not-found\nActiveState=inactive\nMainPID=42\n", 0),
    ("LoadState=not-found\nActiveState=inactive\nMainPID=0\n", 1),
    ("LoadState=masked\nActiveState=inactive\nMainPID=0\n", 0),
    ("LoadState=not-found\nActiveState=inactive\nMainPID=0\nMainPID=0\n", 0),
    ("LoadState=not-found\nActiveState=inactive\n", 0),
    ("LoadState=not-found\nActiveState=inactive\nMainPID=0\nExtra=1\n", 0),
    ("LoadState=not-found\nActiveState=inactive\nMainPID=0\n" + "x" * 2000, 0),
])
def test_unit_negative_cases(env, monkeypatch, stdout, rc):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))
    calls = []
    _unit(monkeypatch, stdout=stdout, returncode=rc, calls=calls)
    assert _gate(env, monkeypatch, calls) == 1
    assert len(calls) == 1


def test_unit_timeout(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))

    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert _gate(env, monkeypatch) == 1


@pytest.mark.parametrize("mutate", [
    lambda c: c["gateway"]["http"]["endpoints"]["chatCompletions"].update(enabled=1),
    lambda c: c["gateway"]["http"]["endpoints"]["chatCompletions"].update(enabled=False),
    lambda c: c["gateway"]["http"]["endpoints"]["chatCompletions"].update(enabled=None),
    lambda c: c["gateway"].update(auth={"mode": "none"}),
    lambda c: c["gateway"].update(bind="lan"),
    lambda c: c["gateway"]["http"]["endpoints"].update(extra={"enabled": True}),
    lambda c: c.update(agents={"defaults": {"sandbox": {"mode": "off"}}}),
    lambda c: c["plugins"]["entries"].update(discord={"enabled": True, "x": 1}),
    lambda c: c["plugins"]["entries"].update(discord={"enabled": False}),
    lambda c: c["plugins"]["entries"].clear(),
    lambda c: c["meta"].update(lastTouchedVersion="0.0.0"),
    lambda c: c["meta"].update(lastTouchedAt="nope"),
    lambda c: c.update(extra=1),
])
def test_shape_faults_refuse_before_unit(env, monkeypatch, mutate):
    _write_marker(env, _marker(env))
    cfg = _config(gateway=True)
    mutate(cfg)
    _write_config(env, cfg)
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 1
    assert calls == []


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(pixel_source_ref="b" * 40),
    lambda m: m.update(install_dir="/elsewhere"),
    lambda m: m.update(state="ready"),
    lambda m: m.update(extra=1),
    lambda m: m.pop("manager"),
])
def test_marker_faults(env, monkeypatch, mutate):
    m = _marker(env)
    mutate(m)
    _write_marker(env, m)
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 1
    assert calls == []


def test_dangling_current_refuses(env, monkeypatch):
    _write_marker(env, _marker(env))
    (env["home"] / ".local/share/pixel").mkdir(parents=True)
    (env["home"] / ".local/share/pixel/current").symlink_to("/nonexistent")
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 1
    assert calls == []


def test_writable_config_refuses(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))
    os.chmod(env["config"], 0o666)
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 1
    assert calls == []


def test_positive_one_plugin_with_overlay(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(entries={"searxng": {"enabled": True}}))
    assert _overlay(env, monkeypatch) == 0
    _unit(monkeypatch)
    assert _gate(env, monkeypatch) == 0


def test_legacy_plain_bootstrap_accepted(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(entries={"discord": {"enabled": True},
                                        "searxng": {"enabled": True},
                                        "llama-cpp": {"enabled": True}}))
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch, calls) == 0
    assert calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_overlay_preserves_mode(env, monkeypatch):
    _write_config(env, _config())
    assert _overlay(env, monkeypatch) == 0
    assert stat.S_IMODE(env["config"].stat().st_mode) == 0o600
    data = json.loads(env["config"].read_text())
    assert data["gateway"]["http"]["endpoints"]["chatCompletions"] == {"enabled": True}


def test_foreign_config_owner_refuses_before_unit(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))
    real_fstat = os.fstat
    def foreign_fstat(fd):
        fields = list(real_fstat(fd))
        fields[4] = os.getuid() + 1
        return os.stat_result(fields)
    monkeypatch.setattr(os, "fstat", foreign_fstat)
    calls = []
    _unit(monkeypatch, calls=calls)
    assert _gate(env, monkeypatch) == 1
    assert calls == []


def test_unavailable_systemctl_refuses(env, monkeypatch):
    _write_marker(env, _marker(env))
    _write_config(env, _config(gateway=True))
    def unavailable(*_args, **_kwargs):
        raise OSError("unit manager unavailable")
    monkeypatch.setattr(subprocess, "run", unavailable)
    assert _gate(env, monkeypatch) == 1

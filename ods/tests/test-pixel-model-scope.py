"""Installer Pixel recommendation uses only qualification for this host."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-model.py"
CATALOG = ROOT / "config" / "model-library.json"


def select(catalog: Path, host: str, *, ram_gb: int = 23,
           ready_only: bool = False, shell: bool = False):
    env = os.environ.copy()
    env["ODS_FLEET_HOST_ID"] = host
    env.pop("ODS_COMPATIBILITY_HOST", None)
    return subprocess.run(
        [sys.executable, str(SELECTOR), "--catalog", str(catalog),
         "--backend", "cpu", "--memory-type", "system", "--vram-mb", "0",
         "--ram-gb", str(ram_gb), "--profile", "qwen", "--tier", "1",
         "--max-size-mb", "0", "--host-arch", "amd64",
         "--installable-only", "--min-context", "65536",
         *(["--agent-ready-only"] if ready_only else []),
         *(["--env"] if shell else [])],
        capture_output=True, text=True, env=env,
    )


def test_unmatched_named_host_keeps_model_but_uses_adaptive_route():
    result = select(CATALOG, "qa-cpu-host")
    assert result.returncode == 0, result.stderr
    assert result.stdout and json.loads(result.stdout)["selected"]["id"] == "qwen3.5-9b-q4"
    assert json.loads(result.stdout)["selected"]["pixel_agent_status"] == "unknown"
    result = select(CATALOG, "qa-cpu-host", shell=True)
    assert 'PIXEL_AGENT_MODEL_READY="false"' in result.stdout
    # Ready-only filtering must agree with the emitted flag and evidence score.
    assert select(CATALOG, "qa-cpu-host", ready_only=True).returncode == 2


def test_named_host_verdict_remains_available_on_its_named_host():
    result = select(CATALOG, "windows-laptop", ready_only=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["selected"]["id"] == "qwen3.5-9b-q4"
    result = select(CATALOG, "windows-laptop", shell=True)
    assert 'PIXEL_AGENT_MODEL_READY="true"' in result.stdout


def test_negative_named_host_verdict_is_unknown_elsewhere():
    cpu = select(CATALOG, "qa-cpu-host", ram_gb=16)
    windows = select(CATALOG, "windows-laptop", ram_gb=16)
    assert cpu.returncode == windows.returncode == 0
    assert json.loads(cpu.stdout)["selected"]["id"] == "qwen3.5-4b-q4"
    assert json.loads(cpu.stdout)["selected"]["pixel_agent_status"] == "unknown"
    assert json.loads(windows.stdout)["selected"]["pixel_agent_status"] == "not-agent-viable"


def test_global_and_legacy_verdicts_remain_ready(tmp_path: Path):
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    model = next(item for item in raw["models"] if item["id"] == "qwen3.5-9b-q4")
    verdict = model["app_compatibility"]["pixel_agent"]
    raw["models"] = [model]
    catalog = tmp_path / "catalog.json"
    verdict.pop("hostScope")
    verdict["globalScope"] = True
    catalog.write_text(json.dumps(raw), encoding="utf-8")
    assert select(catalog, "qa-cpu-host", ready_only=True).returncode == 0
    verdict.pop("globalScope")
    catalog.write_text(json.dumps(raw), encoding="utf-8")
    assert select(catalog, "qa-cpu-host", ready_only=True).returncode == 0


def test_explicit_host_identity_overrides_incidental_hostname():
    # The CI machine's hostname must not change a named fleet verdict.
    result = select(CATALOG, "qa-cpu-host", shell=True)
    assert 'PIXEL_AGENT_MODEL_READY="false"' in result.stdout

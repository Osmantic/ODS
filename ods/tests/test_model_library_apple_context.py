#!/usr/bin/env python3
"""Apple Silicon 8GB model selection and the Phi-4-mini 8GB profile scope."""

from __future__ import annotations

import subprocess
import sys
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-model.py"
CATALOG = ROOT / "config" / "model-library.json"


def run_selector(ram_gb: int, *extra: str) -> dict[str, str]:
    cmd = [
        sys.executable,
        str(SELECTOR),
        "--catalog",
        str(CATALOG),
        "--backend",
        "apple",
        "--memory-type",
        "unified",
        "--vram-mb",
        "0",
        "--ram-gb",
        str(ram_gb),
        "--tier",
        "1",
        "--host-arch",
        "arm64",
        "--installable-only",
        "--env",
        *extra,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert proc.returncode == 0, f"select-model.py failed (exit {proc.returncode}): {proc.stderr}"
    env: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    return env


def test_apple_silicon_8gb_selects_nemotron_at_64k() -> None:
    # Phi-4-mini only reached 16K here (with a Q4 KV cache), below Pixel's
    # 24K prompt and the 64K Hermes floor. Nemotron 3 Nano 4B keeps KV on 4 of
    # its 42 layers: about 4.1 GiB at 64K against a 4.4 GiB budget.
    for extra in ((), ("--min-context", "65536")):
        env = run_selector(8, *extra)
        assert env.get("LLM_MODEL") == "NVIDIA-Nemotron3-Nano-4B", env.get("LLM_MODEL")
        assert env.get("MAX_CONTEXT") == "65536", f"Expected 65536 context, got {env.get('MAX_CONTEXT')}"
        assert not env.get("MODEL_RUNTIME_PROFILE")
        assert not env.get("LLAMA_ARG_CACHE_TYPE_K")


def test_apple_silicon_16gb_avoids_8gb_profile() -> None:
    env = run_selector(16)
    assert env.get("MODEL_RUNTIME_PROFILE") != "apple-silicon-8gb-16k"
    assert int(env.get("MAX_CONTEXT", "0")) > 16384


def test_profile_scope_preserves_larger_hosts_and_low_ram_guard() -> None:
    spec = importlib.util.spec_from_file_location("ods_apple_selector", SELECTOR)
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    model = next(m for m in selector.load_catalog(CATALOG) if m["id"] == "phi4-mini-q4")
    for ram in (7, 8, 10):
        assert selector.matching_runtime_profile(model, "apple", "unified", 0, ram, "arm64")
    for ram in (11, 16, 24, 32, 64, 128):
        assert selector.matching_runtime_profile(model, "apple", "unified", 0, ram, "arm64") is None
        assert not selector.hardware_matching_profiles(model, "apple", "unified", 0, "arm64", ram), (
            "A larger host must not lose the model through the profile safety gate", ram
        )
    # Below the minimum, the profile still forbids an unsafe generic fallback.
    assert selector.matching_runtime_profile(model, "apple", "unified", 0, 6, "arm64") is None
    assert selector.hardware_matching_profiles(model, "apple", "unified", 0, "arm64", 6)
    for backend in ("amd", "nvidia", "cpu"):
        for ram in (8, 16, 32, 64):
            assert not selector.hardware_matching_profiles(model, backend, "discrete", 8192, "amd64", ram)
            assert not selector.hardware_matching_profiles(model, backend, "unified", 8192, "amd64", ram)


def main() -> int:
    test_apple_silicon_8gb_selects_nemotron_at_64k()
    test_apple_silicon_16gb_avoids_8gb_profile()
    test_profile_scope_preserves_larger_hosts_and_low_ram_guard()
    print("Apple Silicon model selector context tests passed: 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())

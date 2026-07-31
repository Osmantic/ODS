#!/usr/bin/env python3
"""Cross-platform persistence contract for llama.cpp GPU layer placement."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import SkipTest


ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def replace_env_value(path: Path, key: str, value: str | None) -> None:
    lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if not line.startswith(f"{key}=")
    ]
    if value is not None:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_windows_generator(install_dir: Path, override: str | None = None) -> None:
    env = os.environ.copy()
    env["ODS_TEST_ROOT"] = str(ROOT)
    env["ODS_TEST_DIR"] = str(install_dir)
    if override is None:
        env.pop("N_GPU_LAYERS", None)
    else:
        env["N_GPU_LAYERS"] = override
    script = r'''
$ErrorActionPreference = "Stop"
function Write-AIWarn { param([string]$Message) }
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/detection.ps1")
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/env-generator.ps1")
$tier = @{
    TierName = "GPU layer contract"
    LlmModel = "test-model"
    GgufFile = "test.gguf"
    MaxContext = 4096
}
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
'''
    subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def test_windows_generator_gpu_layer_lifecycle() -> None:
    if shutil.which("pwsh") is None:
        raise SkipTest("PowerShell is unavailable")

    with tempfile.TemporaryDirectory(prefix="ods-windows-gpu-layer-contract-") as temp:
        root = Path(temp)
        fresh = root / "fresh"
        run_windows_generator(fresh)
        assert read_env(fresh / ".env")["N_GPU_LAYERS"] == "auto"

        explicit = root / "explicit"
        run_windows_generator(explicit, "all")
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "all"

        # A persisted operator value wins over a process-level override.
        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "17")
        run_windows_generator(explicit, "all")
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "17"

        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "  23  ")
        run_windows_generator(explicit)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "23"

        # Older and manually emptied environments receive the safe default.
        replace_env_value(explicit / ".env", "N_GPU_LAYERS", None)
        run_windows_generator(explicit)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "auto"
        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "")
        run_windows_generator(explicit)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "auto"


def run_macos_generator(install_dir: Path, force: bool, override: str | None = None) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    install_arg = str(install_dir)
    if os.name == "nt":
        drive, tail = os.path.splitdrive(install_arg)
        install_arg = f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"
    env = os.environ.copy()
    if override is None:
        env.pop("N_GPU_LAYERS", None)
    else:
        env["N_GPU_LAYERS"] = override
    subprocess.run(
        [
            "bash",
            "-c",
            'if [[ "$(uname -s)" != "Darwin" ]]; then '
            'sed() { '
            'if [[ "${1:-}" == "-i" && "$#" -ge 2 && -z "$2" ]]; then '
            'shift 2; command sed -i "$@"; '
            'else command sed "$@"; fi; '
            '}; export -f sed; fi; '
            'source installers/macos/lib/detection.sh; '
            'source installers/macos/lib/env-generator.sh; '
            'generate_ods_env "$1" 3 "$2" >/dev/null',
            "ods-gpu-layer-contract",
            install_arg,
            "true" if force else "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def test_macos_generator_gpu_layer_lifecycle() -> None:
    if os.name == "nt" or shutil.which("bash") is None:
        raise SkipTest("POSIX bash is unavailable")

    with tempfile.TemporaryDirectory(prefix="ods-macos-gpu-layer-contract-") as temp:
        root = Path(temp)
        fresh = root / "fresh"
        run_macos_generator(fresh, force=True)
        assert read_env(fresh / ".env")["N_GPU_LAYERS"] == "auto"

        explicit = root / "explicit"
        run_macos_generator(explicit, force=True, override="all")
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "all"

        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "17")
        run_macos_generator(explicit, force=False, override="all")
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "17"

        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "  23  ")
        run_macos_generator(explicit, force=False)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "23"

        replace_env_value(explicit / ".env", "N_GPU_LAYERS", None)
        run_macos_generator(explicit, force=False)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "auto"
        replace_env_value(explicit / ".env", "N_GPU_LAYERS", "")
        run_macos_generator(explicit, force=False)
        assert read_env(explicit / ".env")["N_GPU_LAYERS"] == "auto"


def main() -> int:
    tests = []
    if shutil.which("pwsh"):
        tests.append(test_windows_generator_gpu_layer_lifecycle)
    else:
        print("[SKIP] Windows generator lifecycle (pwsh unavailable)")
    if os.name != "nt" and shutil.which("bash"):
        tests.append(test_macos_generator_gpu_layer_lifecycle)
    else:
        print("[SKIP] macOS generator lifecycle (POSIX bash unavailable)")

    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

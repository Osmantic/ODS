#!/usr/bin/env python3
"""llama.cpp runtime tunable parity across compose overlays.

Docker Compose replaces a `command` sequence wholesale instead of merging it
with the base file's. Any overlay that overrides llama-server's command
therefore has to repeat every flag it still wants, and a flag left out makes
its documented .env tunable silently inert on that backend only.

This checks that every overlay whose llama-server command is a llama.cpp
invocation carries the same flag set as docker-compose.base.yml, and that each
tunable is wired to the .env variable the schema documents.

The AMD overlays are exempt: their llama-server runs Lemonade (`serve ...`),
a different binary with its own argument surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:
    print(f"[FAIL] Missing Python dependency: {exc}")
    raise SystemExit(1)


ROOT_DIR = Path(__file__).resolve().parents[2]
BASE_FILE = ROOT_DIR / "docker-compose.base.yml"
SCHEMA_FILE = ROOT_DIR / ".env.schema.json"
ENV_GENERATOR = ROOT_DIR / "installers" / "phases" / "06-directories.sh"
MACOS_ENV_GENERATOR = ROOT_DIR / "installers" / "macos" / "lib" / "env-generator.sh"
WINDOWS_ENV_GENERATOR = ROOT_DIR / "installers" / "windows" / "lib" / "env-generator.ps1"
NATIVE_LAUNCHERS = (
    ROOT_DIR / "bin" / "ods-host-agent.py",
    ROOT_DIR / "installers" / "macos" / "install-macos.sh",
    ROOT_DIR / "installers" / "macos" / "ods-macos.sh",
    ROOT_DIR / "installers" / "windows" / "install-windows.ps1",
    ROOT_DIR / "installers" / "windows" / "ods.ps1",
    ROOT_DIR / "scripts" / "bootstrap-upgrade.sh",
)

# Flags whose value must stay operator-tunable through .env. The base file is
# the source of truth for which variable backs each one.
TUNABLE_FLAGS = ("--n-gpu-layers", "--ctx-size", "--batch-size", "--threads", "--parallel")
VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")


def llama_command(path: Path) -> list[str] | None:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    service = (document.get("services") or {}).get("llama-server") or {}
    command = service.get("command")
    if not isinstance(command, list):
        return None
    return [str(item) for item in command]


def is_llama_cpp_invocation(command: list[str]) -> bool:
    """Lemonade overlays start with a `serve` subcommand; llama.cpp takes flags."""
    return bool(command) and command[0].startswith("-")


def flag_values(command: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, token in enumerate(command):
        if token.startswith("--"):
            following = command[index + 1] if index + 1 < len(command) else ""
            values[token] = "" if following.startswith("--") else following
    return values


def main() -> int:
    errors: list[str] = []

    base_command = llama_command(BASE_FILE)
    if base_command is None:
        print(f"[FAIL] {BASE_FILE.name} does not define a llama-server command list")
        return 1
    base_values = flag_values(base_command)

    schema_properties = json.loads(SCHEMA_FILE.read_text(encoding="utf-8")).get("properties", {})
    for flag in TUNABLE_FLAGS:
        if flag not in base_values:
            errors.append(f"{BASE_FILE.name}: base command no longer sets {flag}")
            continue
        variables = VAR_RE.findall(base_values[flag])
        if not variables:
            errors.append(f"{BASE_FILE.name}: {flag} is hardcoded to {base_values[flag]!r}, not .env-tunable")
            continue
        for variable in variables:
            if variable not in schema_properties:
                errors.append(f".env.schema.json: {flag} uses undocumented variable {variable}")

    gpu_layer_schema = schema_properties.get("N_GPU_LAYERS") or {}
    if gpu_layer_schema.get("default") != "auto":
        errors.append(".env.schema.json: N_GPU_LAYERS must default to llama.cpp auto placement")
    linux_env_generator = ENV_GENERATOR.read_text(encoding="utf-8")
    if 'N_GPU_LAYERS_VALUE=$(_env_get N_GPU_LAYERS "${N_GPU_LAYERS:-auto}")' not in linux_env_generator:
        errors.append("06-directories.sh: reruns do not preserve N_GPU_LAYERS")
    if 'N_GPU_LAYERS_VALUE="${N_GPU_LAYERS_VALUE:-auto}"' not in linux_env_generator:
        errors.append("06-directories.sh: empty N_GPU_LAYERS values do not fall back to auto")
    if "N_GPU_LAYERS=${N_GPU_LAYERS_VALUE}" not in linux_env_generator:
        errors.append("06-directories.sh: generated .env does not write N_GPU_LAYERS")

    macos_env_generator = MACOS_ENV_GENERATOR.read_text(encoding="utf-8")
    if "N_GPU_LAYERS=${n_gpu_layers}" not in macos_env_generator:
        errors.append("macOS env generator: fresh installs do not write N_GPU_LAYERS")
    if 'read_env_value "$env_path" "N_GPU_LAYERS"' not in macos_env_generator:
        errors.append("macOS env generator: reruns do not preserve/backfill N_GPU_LAYERS")
    if "normalize_n_gpu_layers" not in macos_env_generator:
        errors.append("macOS env generator: N_GPU_LAYERS values are not normalized")

    windows_env_generator = WINDOWS_ENV_GENERATOR.read_text(encoding="utf-8")
    if 'N_GPU_LAYERS=$nGpuLayers' not in windows_env_generator:
        errors.append("Windows env generator: installs/reruns do not preserve/default N_GPU_LAYERS")
    if '$nGpuLayers = (Get-EnvOrNew "N_GPU_LAYERS" $nGpuLayersDefault).Trim()' not in windows_env_generator:
        errors.append("Windows env generator: N_GPU_LAYERS values are not normalized")

    for path in NATIVE_LAUNCHERS:
        text = path.read_text(encoding="utf-8")
        flag_lines = [line.strip() for line in text.splitlines() if "--n-gpu-layers" in line]
        if not flag_lines:
            errors.append(f"{path.relative_to(ROOT_DIR)}: native launcher no longer sets --n-gpu-layers")
            continue
        if "N_GPU_LAYERS" not in text:
            errors.append(f"{path.relative_to(ROOT_DIR)}: native launcher ignores N_GPU_LAYERS")
        for line in flag_lines:
            if re.search(r"--n-gpu-layers[\"',\s]+[\"']?999(?:[\"'\s,]|$)", line):
                errors.append(
                    f"{path.relative_to(ROOT_DIR)}: native launcher still forces every GPU layer"
                )

    for path in sorted(ROOT_DIR.glob("docker-compose*.yml")):
        if path == BASE_FILE:
            continue
        command = llama_command(path)
        if command is None or not is_llama_cpp_invocation(command):
            continue
        values = flag_values(command)
        for flag in base_values:
            if flag not in values:
                errors.append(
                    f"{path.name}: llama-server command drops {flag}; compose replaces the "
                    f"whole list, so it is lost on this backend"
                )
                continue
            if flag not in TUNABLE_FLAGS:
                continue
            if (
                flag == "--n-gpu-layers"
                and path.name == "docker-compose.cpu.yml"
                and values[flag] == "0"
            ):
                # A CPU-only deployment must never inherit auto/all offload.
                continue
            # The overlay may pick its own default, but it must stay tunable
            # through the same .env variable the base file uses.
            expected = set(VAR_RE.findall(base_values[flag]))
            actual = set(VAR_RE.findall(values[flag]))
            if expected and not expected & actual:
                errors.append(
                    f"{path.name}: {flag} is {values[flag]!r}, which ignores "
                    f"{'/'.join(sorted(expected))} from {BASE_FILE.name}"
                )

    if errors:
        print("[FAIL] llama.cpp runtime tunable parity")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("[PASS] llama.cpp runtime tunables survive every compose overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

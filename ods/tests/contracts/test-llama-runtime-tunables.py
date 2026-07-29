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
MULTIGPU_NVIDIA_FILE = ROOT_DIR / "docker-compose.multigpu-nvidia.yml"
SCHEMA_FILE = ROOT_DIR / ".env.schema.json"

# Flags whose value must stay operator-tunable through .env. The base file is
# the source of truth for which variable backs each one.
TUNABLE_FLAGS = ("--ctx-size", "--batch-size", "--threads", "--parallel")
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
            # The overlay may pick its own default, but it must stay tunable
            # through the same .env variable the base file uses.
            expected = set(VAR_RE.findall(base_values[flag]))
            actual = set(VAR_RE.findall(values[flag]))
            if expected and not expected & actual:
                errors.append(
                    f"{path.name}: {flag} is {values[flag]!r}, which ignores "
                    f"{'/'.join(sorted(expected))} from {BASE_FILE.name}"
                )

    multigpu_document = yaml.safe_load(
        MULTIGPU_NVIDIA_FILE.read_text(encoding="utf-8")
    ) or {}
    multigpu_service = (
        (multigpu_document.get("services") or {}).get("llama-server") or {}
    )
    multigpu_entrypoint = multigpu_service.get("entrypoint")
    if not isinstance(multigpu_entrypoint, list):
        errors.append(
            f"{MULTIGPU_NVIDIA_FILE.name}: llama-server must wrap its entrypoint "
            "to remove an empty LLAMA_ARG_TENSOR_SPLIT before llama.cpp parses it"
        )
    else:
        wrapper = "\n".join(str(item) for item in multigpu_entrypoint)
        if "unset LLAMA_ARG_TENSOR_SPLIT" not in wrapper:
            errors.append(
                f"{MULTIGPU_NVIDIA_FILE.name}: entrypoint does not remove an "
                "empty LLAMA_ARG_TENSOR_SPLIT"
            )
        if 'exec /app/llama-server "$$@"' not in wrapper:
            errors.append(
                f"{MULTIGPU_NVIDIA_FILE.name}: entrypoint does not exec the "
                "pinned llama.cpp server with the Compose command arguments"
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

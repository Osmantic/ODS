#!/usr/bin/env python3
"""llama.cpp speculative-decoding default and llama.cpp env-name contract.

ODS serves llama.cpp with lossless n-gram speculation (`--spec-type ngram-mod`,
via LLAMA_ARG_SPEC_TYPE) only on the Docker overlays whose pinned llama.cpp
image has the benchmarked implementation: the dedicated ngram-mod parameters
(llama.cpp b8955+) and speculative checkpoints for hybrid models such as
Qwen3.5 (b8842+). The benchmarked build is b9014. LLAMA_SPEC_TYPE=none is the
single opt-out, and a per-model LLAMA_ARG_SPEC_TYPE (e.g. draft-mtp) wins.
Lemonade (AMD), Intel/Arc (b8248), Apple (b8248) and native runtimes get no
default.

The env names ODS hands to the b9014 containers must be names llama.cpp reads.
Docker ignored LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS for this reason; the
llama.cpp name is LLAMA_ARG_CHECKPOINT_EVERY_NT.

Run from ods/:  python3 tests/contracts/test-llama-spec-default.py
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
BASE = "docker-compose.base.yml"
DEFAULT_OVERLAYS = ("docker-compose.nvidia.yml", "docker-compose.cpu.yml")
DEFAULT_ENTRY = "LLAMA_ARG_SPEC_TYPE=${LLAMA_ARG_SPEC_TYPE:-${LLAMA_SPEC_TYPE:-ngram-mod}}"

# First llama.cpp release with the dedicated ngram-mod parameters
# (n_match 24 / n_min 48 / n_max 64; ggml-org/llama.cpp#22397 in b8955). It
# also has speculative checkpoints (#19493, b8842), which hybrid models need.
MIN_BUILD = 8955

# `--spec-type` choices in llama.cpp b9014 common/arg.cpp (no draft model).
B9014_SPEC_TYPES = {"none", "ngram-cache", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod"}

# Env names read by llama.cpp b9014 (common/arg.cpp set_env). A new name added to
# a b9014 overlay must be checked against that source before it goes here.
B9014_ENV_NAMES = {
    "LLAMA_ARG_REASONING",
    "LLAMA_ARG_FLASH_ATTN",
    "LLAMA_ARG_CACHE_TYPE_K",
    "LLAMA_ARG_CACHE_TYPE_V",
    "LLAMA_ARG_N_CPU_MOE",
    "LLAMA_ARG_CHECKPOINT_EVERY_NT",
    "LLAMA_ARG_SPEC_TYPE",
    "LLAMA_ARG_SPEC_DRAFT_N_MAX",
    "LLAMA_ARG_SPLIT_MODE",
    "LLAMA_ARG_TENSOR_SPLIT",
    # --cache-prompt/--no-cache-prompt is negatable, so common_arg::
    # get_value_from_env also reads LLAMA_ARG_NO_CACHE_PROMPT. Any value,
    # including 0 or empty, disables prompt caching.
    "LLAMA_ARG_NO_CACHE_PROMPT",
}

LEGACY_CHECKPOINT = "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"
CHECKPOINT = "LLAMA_ARG_CHECKPOINT_EVERY_NT"
# The former name may only survive where it is deliberately handled.
LEGACY_CHECKPOINT_ALLOWED = {
    ".env.schema.json",  # deprecated entry so existing .env files still validate
    "bin/ods-host-agent.py",  # model activation removes stale lines
    "extensions/services/dashboard-api/performance_oracle.py",  # evidence alias
    "CHANGELOG.md",
}
CHECKPOINT_READERS = (
    "docker-compose.base.yml",
    "bin/ods-host-agent.py",
    "installers/macos/lib/native-model.sh",
    "installers/phases/02-detection.sh",
    "installers/phases/06-directories.sh",
    "installers/windows/install-windows.ps1",
    "installers/windows/ods.ps1",
    "installers/windows/lib/env-generator.ps1",
    "lib/safe-env.sh",
    "scripts/bootstrap-upgrade.sh",
    "scripts/preserve-active-model.py",
)

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BUILD_RE = re.compile(r"llama\.cpp:server(?:-[a-z]+)?-b(\d+)")


def interpolate(text: str, env: dict[str, str]) -> str:
    """Docker Compose variable interpolation, including nested defaults."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("$$", index):
            out.append("$")
            index += 2
        elif text.startswith("${", index):
            value, index = _expand(text, index + 2, env)
            out.append(value)
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _expand(text: str, index: int, env: dict[str, str]) -> tuple[str, int]:
    match = NAME_RE.match(text, index)
    if not match:
        raise ValueError(f"bad interpolation in {text!r}")
    name, index = match.group(0), match.end()
    operator = ""
    for candidate in (":-", ":?", ":+", "-", "?", "+"):
        if text.startswith(candidate, index):
            operator, index = candidate, index + len(candidate)
            break
    depth, start = 0, index
    while True:
        if text.startswith("${", index):
            depth, index = depth + 1, index + 2
            continue
        if text[index] == "}":
            if depth == 0:
                break
            depth -= 1
        index += 1
    word, index = text[start:index], index + 1
    value = env.get(name)
    if operator in {":-", ":?"}:
        if value:
            return value, index
        if operator == ":?":
            raise ValueError(f"{name} is required")
        return interpolate(word, env), index
    if operator in {"-", "?"}:
        if value is not None:
            return value, index
        if operator == "?":
            raise ValueError(f"{name} is required")
        return interpolate(word, env), index
    if operator == ":+":
        return (interpolate(word, env) if value else ""), index
    if operator == "+":
        return (interpolate(word, env) if value is not None else ""), index
    return value or "", index


def llama_service(name: str) -> dict:
    document = yaml.safe_load((ROOT_DIR / name).read_text(encoding="utf-8")) or {}
    return (document.get("services") or {}).get("llama-server") or {}


def environment(service: dict) -> dict[str, str | None]:
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key): None if value is None else str(value) for key, value in raw.items()}
    result: dict[str, str | None] = {}
    for entry in raw:
        key, separator, value = str(entry).partition("=")
        result[key] = value if separator else None
    return result


def merged_env(files: tuple[str, ...]) -> dict[str, str | None]:
    """llama-server `environment` after Compose merges the files (later wins)."""
    merged: dict[str, str | None] = {}
    for name in files:
        merged.update(environment(llama_service(name)))
    return merged


def container_env(files: tuple[str, ...], dotenv: dict[str, str]) -> dict[str, str]:
    """Effective LLAMA_ARG_* container env for a Compose file stack."""
    resolved: dict[str, str] = {}
    for key, value in merged_env(files).items():
        if not key.startswith("LLAMA_ARG_"):
            continue
        if value is None:
            if key in dotenv:  # bare `- KEY` passes only a defined value
                resolved[key] = dotenv[key]
        else:
            resolved[key] = interpolate(value, dotenv)
    return resolved


def pinned_build(service: dict) -> int | None:
    match = BUILD_RE.search(str(service.get("image") or ""))
    return int(match.group(1)) if match else None


def main() -> int:
    errors: list[str] = []

    # 1. Only overlays pinned to a build with the benchmarked implementation
    #    carry the default, and they carry exactly the documented expression.
    for name in DEFAULT_OVERLAYS:
        service = llama_service(name)
        entries = environment(service)
        if f"LLAMA_ARG_SPEC_TYPE={entries.get('LLAMA_ARG_SPEC_TYPE')}" != DEFAULT_ENTRY:
            errors.append(f"{name}: llama-server must set {DEFAULT_ENTRY}")
        build = pinned_build(service)
        if build is None or build < MIN_BUILD:
            errors.append(f"{name}: default ngram-mod needs a pinned llama.cpp image >= b{MIN_BUILD}, got {service.get('image')!r}")

    base_entries = environment(llama_service(BASE))
    if "LLAMA_ARG_SPEC_TYPE" not in base_entries or base_entries["LLAMA_ARG_SPEC_TYPE"] is not None:
        errors.append(f"{BASE}: LLAMA_ARG_SPEC_TYPE must stay a bare passthrough so other overlays get no default")

    for path in sorted(ROOT_DIR.glob("docker-compose*.yml")):
        if path.name in DEFAULT_OVERLAYS or path.name == BASE:
            continue
        entries = environment(llama_service(path.name))
        if entries.get("LLAMA_ARG_SPEC_TYPE") is not None:
            errors.append(f"{path.name}: must not set a speculative default; its runtime is not the benchmarked b{MIN_BUILD}+ llama.cpp")

    # 2. Effective container env per stack, as `docker compose config` resolves it.
    stacks = {
        "nvidia": (BASE, "docker-compose.nvidia.yml"),
        "nvidia multi-GPU": (BASE, "docker-compose.nvidia.yml", "docker-compose.multigpu-nvidia.yml"),
        "cpu": (BASE, "docker-compose.cpu.yml"),
        "amd (Lemonade)": (BASE, "docker-compose.amd.yml"),
        "amd multi-GPU (Lemonade)": (BASE, "docker-compose.amd.yml", "docker-compose.multigpu-amd.yml"),
        "intel": (BASE, "docker-compose.intel.yml"),
        "arc": (BASE, "docker-compose.arc.yml"),
        "apple": (BASE, "docker-compose.apple.yml"),
    }
    defaulted = {"nvidia", "nvidia multi-GPU", "cpu"}
    scenarios = (
        ({}, "ngram-mod", None),
        ({"LLAMA_SPEC_TYPE": ""}, "ngram-mod", None),
        ({"LLAMA_SPEC_TYPE": "none"}, "none", None),
        ({"LLAMA_SPEC_TYPE": "ngram-simple"}, "ngram-simple", None),
        ({"LLAMA_ARG_SPEC_TYPE": "draft-mtp", "LLAMA_SPEC_TYPE": "none"}, "draft-mtp", "draft-mtp"),
    )
    for stack, files in stacks.items():
        for dotenv, expected_default, expected_other in scenarios:
            actual = container_env(files, dotenv).get("LLAMA_ARG_SPEC_TYPE")
            expected = expected_default if stack in defaulted else expected_other
            if actual != expected:
                errors.append(f"{stack} with {dotenv}: LLAMA_ARG_SPEC_TYPE={actual!r}, expected {expected!r}")

    # 3. Every LLAMA_ARG_* the b9014 stacks hand to llama.cpp is a name it reads.
    for stack in sorted(defaulted):
        for key in merged_env(stacks[stack]):
            if key.startswith("LLAMA_ARG_") and key not in B9014_ENV_NAMES:
                errors.append(f"{stack}: llama.cpp b9014 does not read {key}")
    for stack, files in stacks.items():
        if container_env(files, {CHECKPOINT: "-1"}).get(CHECKPOINT) != "-1":
            errors.append(f"{stack}: {CHECKPOINT} is not passed to llama-server")

    # 4. The opt-out is documented, validated and survives installer reruns.
    schema = json.loads((ROOT_DIR / ".env.schema.json").read_text(encoding="utf-8"))["properties"]
    spec = schema.get("LLAMA_SPEC_TYPE") or {}
    if spec.get("default") != "ngram-mod":
        errors.append(".env.schema.json: LLAMA_SPEC_TYPE must document the ngram-mod default")
    allowed = set(spec.get("enum") or [])
    if not {"ngram-mod", "none"} <= allowed or not allowed - {""} <= B9014_SPEC_TYPES:
        errors.append(f".env.schema.json: LLAMA_SPEC_TYPE enum {sorted(allowed)} must offer ngram-mod/none and only b9014 --spec-type values")
    if CHECKPOINT not in schema:
        errors.append(f".env.schema.json: {CHECKPOINT} is undocumented")
    example = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    if not re.search(r"^# LLAMA_SPEC_TYPE=none\b", example, re.M):
        errors.append(".env.example: document the LLAMA_SPEC_TYPE=none opt-out")
    if re.search(r"^LLAMA_(ARG_)?SPEC_TYPE=", example, re.M):
        errors.append(".env.example: speculative type must stay a commented example")
    literals = {
        "installers/phases/06-directories.sh": "_env_get LLAMA_SPEC_TYPE",
        "installers/windows/lib/env-generator.ps1": '$llamaSpecType = (Get-EnvOrNew "LLAMA_SPEC_TYPE" "")',
        "bin/ods-host-agent.py": 'env.get("LLAMA_SPEC_TYPE")',
    }
    for relative, literal in literals.items():
        if literal not in (ROOT_DIR / relative).read_text(encoding="utf-8"):
            errors.append(f"{relative}: missing {literal!r} (opt-out must survive reruns/recreates)")

    # 5. Checkpoint interval uses llama.cpp's env name everywhere ODS reads it.
    for relative in CHECKPOINT_READERS:
        if CHECKPOINT not in (ROOT_DIR / relative).read_text(encoding="utf-8"):
            errors.append(f"{relative}: does not read {CHECKPOINT}")
    for path in sorted(ROOT_DIR.rglob("*")):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if (
            not path.is_file()
            or path.suffix not in {".py", ".sh", ".ps1", ".psm1", ".yml", ".yaml", ".json", ".md", ".example"}
            or relative.startswith(("tests/", "vendor/", "node_modules/", "data/"))
            or "/tests/" in relative
            or "/node_modules/" in relative
            or relative in LEGACY_CHECKPOINT_ALLOWED
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if LEGACY_CHECKPOINT in text:
            errors.append(f"{relative}: still uses {LEGACY_CHECKPOINT}; llama.cpp reads {CHECKPOINT}")

    if errors:
        print("[FAIL] llama.cpp speculative default / env-name contract")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("[PASS] ngram-mod default only on b9014 overlays; LLAMA_SPEC_TYPE=none opts out; llama.cpp env names are real")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

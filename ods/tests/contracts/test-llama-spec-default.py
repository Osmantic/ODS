#!/usr/bin/env python3
"""llama.cpp speculative-decoding default and llama.cpp env-name contract.

ODS serves llama.cpp with lossless n-gram speculation (`--spec-type ngram-mod`,
via LLAMA_ARG_SPEC_TYPE) only on the Docker overlays whose pinned llama.cpp
image has the benchmarked implementation: the dedicated ngram-mod parameters
(llama.cpp b8955+) and speculative checkpoints for hybrid models such as
Qwen3.5 (b8842+). The benchmarked build is b9014. LLAMA_SPEC_TYPE=none is the
single opt-out, and a per-model LLAMA_ARG_SPEC_TYPE (e.g. draft-mtp) wins.
Lemonade (AMD), Intel/Arc (b9014), Apple Docker (b9014) and native Windows get
no default. Native macOS applies the same default through
installers/macos/lib/native-checkpoint-args.py, only when the installed binary
has the implementation; tests/test_macos_runtime_llama_args.py covers it.

The env names ODS hands to the b9014 containers must be names llama.cpp reads.
Docker ignored LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS for this reason; the
llama.cpp name is LLAMA_ARG_CHECKPOINT_EVERY_NT.

Every build-specific set below is keyed by the llama.cpp build pinned in
docker-compose.nvidia.yml and docker-compose.cpu.yml. Moving the pin fails
this contract until the new build's env names and --spec-type values are added
from that tag's common/arg.cpp, and until catalog runtime verdicts recorded
against the old build are revisited.

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

# `--spec-type` choices per pinned llama.cpp build, from that tag's
# common/arg.cpp (no draft model).
SPEC_TYPES_BY_BUILD = {
    9014: {"none", "ngram-cache", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod"},
}

# Env names each pinned build reads (common/arg.cpp set_env), limited to the
# names ODS hands to llama-server. A new name added to the NVIDIA/CPU stacks
# must be checked against the pinned tag's source before it goes here.
ENV_NAMES_BY_BUILD = {
    9014: {
        "LLAMA_ARG_REASONING",
        "LLAMA_ARG_FLASH_ATTN",
        "LLAMA_ARG_CACHE_TYPE_K",
        "LLAMA_ARG_CACHE_TYPE_V",
        "LLAMA_ARG_N_CPU_MOE",
        "LLAMA_ARG_CHECKPOINT_EVERY_NT",
        # common_arg.cpp:1308 and :1323 at b9014.
        "LLAMA_ARG_CTX_CHECKPOINTS",
        "LLAMA_ARG_CACHE_RAM",
        "LLAMA_ARG_SPEC_TYPE",
        "LLAMA_ARG_SPEC_DRAFT_N_MAX",
        "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K",
        "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V",
        "LLAMA_ARG_SPLIT_MODE",
        "LLAMA_ARG_TENSOR_SPLIT",
        # --cache-prompt/--no-cache-prompt is negatable, so common_arg::
        # get_value_from_env also reads LLAMA_ARG_NO_CACHE_PROMPT. Any value,
        # including 0 or empty, disables prompt caching.
        "LLAMA_ARG_NO_CACHE_PROMPT",
    },
}

# Upstream names ODS already passes through for newer builds, mapped to the
# first build that reads them. llama.cpp ignores env vars it does not define,
# so a bare pass-through is harmless on an older pin. Once the pin reaches the
# listed build, the name must appear in that build's ENV_NAMES_BY_BUILD set.
FORWARD_ENV_NAMES = {
    # --checkpoint-min-step replaced --checkpoint-every-n-tokens (#22929).
    "LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT": 9310,
}

# ODS .env keys for native launchers that llama.cpp itself never reads. They
# must never reach a container, where they would be silently ignored.
NATIVE_ONLY_KEYS = {
    "LLAMA_ARG_SPEC_DRAFT_TYPE_K": "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K",
    "LLAMA_ARG_SPEC_DRAFT_TYPE_V": "LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V",
}

LEGACY_CHECKPOINT = "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"
CHECKPOINT = "LLAMA_ARG_CHECKPOINT_EVERY_NT"
# The former name may only survive where it is deliberately handled.
LEGACY_CHECKPOINT_ALLOWED = {
    ".env.schema.json",  # deprecated entry so existing .env files still validate
    "bin/ods-host-agent.py",  # model activation removes stale lines
    "extensions/services/dashboard-api/performance_oracle.py",  # evidence alias
    "extensions/services/llama-server/README.md",  # records that it never existed
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


def default_pinned_build(errors: list[str]) -> int | None:
    """The single llama.cpp build the NVIDIA and CPU overlays pin."""
    builds = {name: pinned_build(llama_service(name)) for name in DEFAULT_OVERLAYS}
    if None in builds.values() or len(set(builds.values())) != 1:
        errors.append(f"NVIDIA and CPU overlays must pin one llama.cpp build, got {builds}")
        return None
    build = next(iter(builds.values()))
    if build not in ENV_NAMES_BY_BUILD or build not in SPEC_TYPES_BY_BUILD:
        errors.append(
            f"llama.cpp b{build} is pinned but this contract has no env-name/--spec-type "
            f"sets for it; add them from common/arg.cpp at tag b{build}"
        )
        return None
    return build


def main() -> int:
    errors: list[str] = []
    pinned = default_pinned_build(errors)
    env_names = ENV_NAMES_BY_BUILD.get(pinned or 0, set())
    spec_types = SPEC_TYPES_BY_BUILD.get(pinned or 0, set())
    label = f"b{pinned}" if pinned else "the pinned build"

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

    # 3. Every LLAMA_ARG_* the NVIDIA/CPU stacks hand to llama.cpp is a name the
    #    pinned build reads, or an upstream name for a later build passed bare.
    for stack in sorted(defaulted if pinned else ()):
        for key, value in merged_env(stacks[stack]).items():
            if not key.startswith("LLAMA_ARG_") or key in env_names:
                continue
            first_build = FORWARD_ENV_NAMES.get(key)
            if first_build is None:
                errors.append(f"{stack}: llama.cpp {label} does not read {key}")
            elif pinned and pinned >= first_build:
                errors.append(f"{stack}: {key} is read from b{first_build}; add it to ENV_NAMES_BY_BUILD[{pinned}]")
            elif value is not None:
                errors.append(f"{stack}: {key} is for llama.cpp b{first_build}+ and must stay a bare pass-through")
    for stack, files in stacks.items():
        if container_env(files, {CHECKPOINT: "-1"}).get(CHECKPOINT) != "-1":
            errors.append(f"{stack}: {CHECKPOINT} is not passed to llama-server")
    for key in FORWARD_ENV_NAMES:
        for stack in sorted(defaulted):
            if container_env(stacks[stack], {key: "1024"}).get(key) != "1024":
                errors.append(f"{stack}: {key} is not passed to llama-server")
    for native_key, upstream_key in NATIVE_ONLY_KEYS.items():
        for stack, files in stacks.items():
            resolved = container_env(files, {native_key: "q4_0", upstream_key: "q8_0"})
            if native_key in resolved:
                errors.append(f"{stack}: {native_key} reaches the container, but llama.cpp reads {upstream_key}")
            if stack in defaulted and resolved.get(upstream_key) != "q8_0":
                errors.append(f"{stack}: {upstream_key} is not passed to llama-server")
    # CPU runtime profiles cap host RAM with these; unset keeps b9014 defaults
    # (32 checkpoints, 8192 MiB prompt cache), so they must not be passed empty.
    for key, value in (("LLAMA_ARG_CTX_CHECKPOINTS", "4"), ("LLAMA_ARG_CACHE_RAM", "1024")):
        for stack in sorted(defaulted):
            if container_env(stacks[stack], {key: value}).get(key) != value:
                errors.append(f"{stack}: {key} is not passed to llama-server")
            if key in container_env(stacks[stack], {}):
                errors.append(f"{stack}: {key} must be absent unless set")

    # 4. The opt-out is documented, validated and survives installer reruns.
    schema = json.loads((ROOT_DIR / ".env.schema.json").read_text(encoding="utf-8"))["properties"]
    spec = schema.get("LLAMA_SPEC_TYPE") or {}
    if spec.get("default") != "ngram-mod":
        errors.append(".env.schema.json: LLAMA_SPEC_TYPE must document the ngram-mod default")
    allowed = set(spec.get("enum") or [])
    if not {"ngram-mod", "none"} <= allowed or not allowed - {""} <= spec_types:
        errors.append(f".env.schema.json: LLAMA_SPEC_TYPE enum {sorted(allowed)} must offer ngram-mod/none and only {label} --spec-type values")
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

    # 6. Catalog verdicts about the default runtime name the build they were
    #    recorded against. A pin move must revisit them, not inherit them.
    catalog = json.loads((ROOT_DIR / "config" / "model-library.json").read_text(encoding="utf-8"))
    for model in catalog.get("models") or []:
        verdict = model.get("default_runtime_compatibility")
        if verdict is None:
            continue
        where = f"model-library.json {model.get('id')}.default_runtime_compatibility"
        if not isinstance(verdict, dict) or verdict.get("status") != "incompatible":
            errors.append(f"{where}: status must be 'incompatible'")
            continue
        if pinned and verdict.get("runtime") != f"llama.cpp {label}":
            errors.append(f"{where}: recorded against {verdict.get('runtime')!r}, but the default is llama.cpp {label}; re-test the model and update or remove the verdict")
        if not str(verdict.get("userNote") or "").strip():
            errors.append(f"{where}: needs a userNote for the activation refusal")

    if errors:
        print("[FAIL] llama.cpp speculative default / env-name contract")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[PASS] ngram-mod default only on {label} overlays; LLAMA_SPEC_TYPE=none opts out; llama.cpp env names are real")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

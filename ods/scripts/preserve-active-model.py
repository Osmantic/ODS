#!/usr/bin/env python3
"""Safely recover a valid locally active model contract during ODS upgrades.

The installer owns recommendations, while the Dashboard owns the model the
operator has activated.  A rerun must not silently replace that active model.
This helper accepts only an installed, catalog-pinned local GGUF and emits a
small allowlisted dotenv fragment for the installer to load without ``eval``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shlex
import stat
import sys
from pathlib import Path
from typing import Any


RUNTIME_KEYS = (
    "LLAMA_PARALLEL",
    "LLAMA_SERVER_MEMORY_LIMIT",
    "LLAMA_ARG_FLASH_ATTN",
    "LLAMA_ARG_CACHE_TYPE_K",
    "LLAMA_ARG_CACHE_TYPE_V",
    "LLAMA_ARG_N_CPU_MOE",
    "LLAMA_ARG_NO_CACHE_PROMPT",
    "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS",
    "LLAMA_ARG_SPEC_TYPE",
    "LLAMA_ARG_SPEC_DRAFT_N_MAX",
    "LLAMA_ARG_SPLIT_MODE",
    "LLAMA_ARG_TENSOR_SPLIT",
)

# A completed switchboard proof records the exact model and context, but the v1
# state contract does not record the runtime profile.  After an interrupted
# installer has already replaced .env, recover only portable, catalog-owned
# memory controls from one unambiguous profile for that proven context.  GPU
# placement and speculative/MoE tuning must never be inferred this way.
PORTABLE_STATE_RECOVERY_KEYS = {
    "LLAMA_PARALLEL",
    "LLAMA_SERVER_MEMORY_LIMIT",
    "LLAMA_ARG_FLASH_ATTN",
    "LLAMA_ARG_CACHE_TYPE_K",
    "LLAMA_ARG_CACHE_TYPE_V",
}


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        try:
            parsed = shlex.split(raw_value.strip(), comments=False, posix=True)
        except ValueError:
            continue
        if len(parsed) <= 1:
            values[key] = parsed[0] if parsed else ""
    return values


def shell_value(value: Any) -> str:
    text = str(value if value is not None else "")
    text = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{text}"'


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def normalize_arch(value: Any) -> str:
    key = normalize_key(value)
    if key in {"x86-64", "x86-64-v2", "x86-64-v3", "x86-64-v4", "amd64", "x64"}:
        return "amd64"
    if key in {"aarch64", "arm64"}:
        return "arm64"
    return key or "unknown"


def positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def positive_int(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def load_records(catalog_path: Path, imports_path: Path | None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    curated = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(curated, list) or not all(isinstance(item, dict) for item in curated):
        return []

    records = list(curated)
    seen_ids = {str(item.get("id") or "") for item in records}
    seen_files = {str(item.get("gguf_file") or "").lower() for item in records}
    if imports_path is None or not imports_path.is_file():
        return records
    try:
        imported_payload = json.loads(imports_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    imported = imported_payload.get("models") if isinstance(imported_payload, dict) else None
    if not isinstance(imported, list) or not all(isinstance(item, dict) for item in imported):
        return []
    for item in imported:
        model_id = str(item.get("id") or "")
        filename = str(item.get("gguf_file") or "").lower()
        if (
            item.get("source") != "huggingface"
            or not model_id
            or not filename
            or model_id in seen_ids
            or filename in seen_files
        ):
            return []
        records.append(item)
        seen_ids.add(model_id)
        seen_files.add(filename)
    return records


def load_verified_active_state(path: Path | None) -> dict[str, Any] | None:
    """Return only a completed, owner-custodied local switchboard proof."""
    if path is None:
        return None
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_size <= 0
            or info.st_size > 2 * 1024 * 1024
        ):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != "ods.model-state.v1":
        return None
    active = payload.get("active")
    desired = payload.get("desired")
    availability = payload.get("availability")
    if not isinstance(active, dict) or not isinstance(desired, dict) or not isinstance(availability, dict):
        return None
    proof = active.get("proof")
    backend = active.get("backend")
    catalog_id = active.get("catalogId")
    runtime_model_id = active.get("runtimeModelId")
    context_length = positive_int(active.get("contextLength"))
    route_seq = positive_int(active.get("routeSeq"))
    if (
        payload.get("operation") is not None
        or availability.get("mode") != "serve_active"
        or desired.get("catalogId") != catalog_id
        or route_seq is None
        or positive_int(payload.get("routeSeq")) != route_seq
        or not isinstance(catalog_id, str)
        or not catalog_id
        or not isinstance(runtime_model_id, str)
        or Path(runtime_model_id).name != runtime_model_id
        or not runtime_model_id.lower().endswith(".gguf")
        or context_length is None
        or context_length < 1024
        or context_length > 9_007_199_254_740_991
        or not isinstance(active.get("verifiedAt"), str)
        or not active.get("verifiedAt")
        or not isinstance(proof, dict)
        or proof.get("completion") is not True
        or proof.get("identity") != runtime_model_id
        or not isinstance(backend, dict)
        or backend.get("kind") != "llama-server"
        or backend.get("endpointId") != "llama-server-default"
        or backend.get("nativeRoute") is not None
    ):
        return None
    return {
        "catalogId": catalog_id,
        "runtimeModelId": runtime_model_id,
        "contextLength": context_length,
    }


def manifest_for(record: dict[str, Any]) -> list[dict[str, Any]] | None:
    primary = str(record.get("gguf_file") or "").strip()
    if not primary or Path(primary).name != primary or not primary.lower().endswith(".gguf"):
        return None
    raw_parts = record.get("gguf_parts")
    if isinstance(raw_parts, list) and raw_parts:
        artifacts = raw_parts
    else:
        artifacts = [
            {
                "file": primary,
                "url": record.get("gguf_url"),
                "sha256": record.get("gguf_sha256"),
                "size_bytes": record.get("size_bytes"),
            }
        ]
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in artifacts:
        if not isinstance(raw, dict):
            return None
        filename = str(raw.get("file") or "").strip()
        url = str(raw.get("url") or "").strip()
        digest = str(raw.get("sha256") or "").strip().lower()
        if (
            not filename
            or Path(filename).name != filename
            or filename in seen
            or not filename.lower().endswith(".gguf")
            or not url.startswith("https://huggingface.co/")
            or (digest and not re.fullmatch(r"[0-9a-f]{64}", digest))
        ):
            return None
        expected_size = positive_int(raw.get("size_bytes"))
        normalized.append(
            {"file": filename, "url": url, "sha256": digest, "size_bytes": expected_size}
        )
        seen.add(filename)
    if primary not in seen:
        return None
    return normalized


def profile_is_eligible(profile_record: dict[str, Any], args: argparse.Namespace) -> bool:
    backend = normalize_key(args.backend)
    profile_backend = normalize_key(profile_record.get("backend"))
    if profile_backend and profile_backend != backend:
        return False
    allowed_arches = {
        normalize_arch(item)
        for item in (
            profile_record.get("host_arch")
            if isinstance(profile_record.get("host_arch"), list)
            else [profile_record.get("host_arch")]
        )
        if item
    }
    if allowed_arches and normalize_arch(args.host_arch or platform.machine()) not in allowed_arches:
        return False
    required_memory_type = normalize_key(profile_record.get("memory_type"))
    if required_memory_type and required_memory_type != normalize_key(args.memory_type):
        return False
    try:
        if profile_record.get("vram_min_gb") is not None and args.vram_mb / 1024 < float(profile_record["vram_min_gb"]):
            return False
        if profile_record.get("vram_max_gb") is not None and args.vram_mb / 1024 > float(profile_record["vram_max_gb"]):
            return False
        if profile_record.get("system_ram_min_gb") is not None and args.ram_gb < float(profile_record["system_ram_min_gb"]):
            return False
    except (TypeError, ValueError):
        return False
    return True


def valid_runtime_value(key: str, value: str) -> bool:
    if key == "LLAMA_PARALLEL":
        number = positive_int(value)
        return number is not None and number <= 128
    if key == "LLAMA_SERVER_MEMORY_LIMIT":
        return bool(re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?[KMGTP]?[bB]?", value))
    if key == "LLAMA_ARG_FLASH_ATTN":
        return value.lower() in {"auto", "on", "off", "true", "false", "0", "1"}
    if key in {"LLAMA_ARG_CACHE_TYPE_K", "LLAMA_ARG_CACHE_TYPE_V"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", value))
    if key == "LLAMA_ARG_N_CPU_MOE":
        return value.isdigit() and int(value) <= 4096
    if key == "LLAMA_ARG_NO_CACHE_PROMPT":
        return value.lower() in {"", "on", "off", "true", "false", "0", "1"}
    if key == "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS":
        return bool(re.fullmatch(r"-?[0-9]{1,10}", value))
    if key == "LLAMA_ARG_SPEC_TYPE":
        return bool(re.fullmatch(r"[A-Za-z0-9_,.-]{1,64}", value))
    if key == "LLAMA_ARG_SPEC_DRAFT_N_MAX":
        number = positive_int(value)
        return number is not None and number <= 4096
    if key == "LLAMA_ARG_SPLIT_MODE":
        return value in {"none", "layer", "row"}
    if key == "LLAMA_ARG_TENSOR_SPLIT":
        return value == "" or bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:,[0-9]+(?:\.[0-9]+)?)*", value))
    return False


def preserved_contract(args: argparse.Namespace) -> dict[str, str] | None:
    env = parse_dotenv(args.env)
    if (
        env.get("ODS_MODE", "local").lower() != "local"
        or env.get("LLM_BACKEND", "llama-server").lower() != "llama-server"
        or env.get("EXTERNAL_LLM_URL", "")
        or env.get("LEMONADE_EXTERNAL", "false").lower() == "true"
    ):
        return None

    records = load_records(args.catalog, args.imports)
    verified_state = load_verified_active_state(args.state)
    state_authoritative = verified_state is not None
    if state_authoritative:
        active_file = str(verified_state["runtimeModelId"])
        matches = [
            item
            for item in records
            if item.get("id") == verified_state["catalogId"]
            and str(item.get("gguf_file") or "") == active_file
        ]
    else:
        active_file = env.get("GGUF_FILE", "").strip()
        if not active_file or Path(active_file).name != active_file:
            return None
        matches = [
            item for item in records if str(item.get("gguf_file") or "").lower() == active_file.lower()
        ]
    if len(matches) != 1:
        return None
    model = matches[0]
    manifest = manifest_for(model)
    if manifest is None:
        return None

    actual_bytes = 0
    try:
        models_root = args.models_dir.resolve()
    except (OSError, RuntimeError):
        return None
    for artifact in manifest:
        artifact_path = args.models_dir / artifact["file"]
        try:
            resolved_artifact = artifact_path.resolve()
            if not resolved_artifact.is_relative_to(models_root):
                return None
            if not resolved_artifact.is_file() or resolved_artifact.stat().st_size <= 0:
                return None
            size = resolved_artifact.stat().st_size
        except (OSError, RuntimeError):
            return None
        expected_size = artifact.get("size_bytes")
        if expected_size is not None and size != expected_size:
            return None
        actual_bytes += size

    llm_model = str(model.get("llm_model_name") or model.get("id") or "").strip()
    if not llm_model or (
        not state_authoritative and env.get("LLM_MODEL") and env["LLM_MODEL"] != llm_model
    ):
        return None
    primary = next(item for item in manifest if item["file"] == str(model["gguf_file"]))
    for key, expected in (
        ("GGUF_URL", primary["url"]),
        ("GGUF_SHA256", primary["sha256"]),
    ):
        if not state_authoritative and env.get(key) and env[key] != expected:
            return None

    if state_authoritative:
        context = int(verified_state["contextLength"])
    else:
        context_values = [positive_int(env.get(key)) for key in ("MAX_CONTEXT", "CTX_SIZE") if env.get(key)]
        if not context_values or any(value is None for value in context_values) or len(set(context_values)) != 1:
            return None
        context = int(context_values[0])
    # Dashboard activation accepts advanced custom contexts above the catalog's
    # declared recommendation and commits only after the live runtime proves
    # the exact value. Preserve that already-qualified operator choice instead
    # of turning a catalog advisory into an upgrade-time hard limit.
    if context < 1024 or context > 9_007_199_254_740_991:
        return None

    reuse_env_runtime = (
        not state_authoritative
        or (
            env.get("GGUF_FILE", "").strip() == active_file
            and env.get("LLM_MODEL", "").strip() == llm_model
        )
    )
    profile_id = env.get("MODEL_RUNTIME_PROFILE", "") if reuse_env_runtime else ""
    runtime_profile: dict[str, Any] | None = None
    runtime_defaults_profile: dict[str, Any] | None = None
    if profile_id:
        profiles = model.get("runtime_profiles")
        if not isinstance(profiles, list):
            return None
        profile_matches = [
            item for item in profiles if isinstance(item, dict) and str(item.get("id") or "") == profile_id
        ]
        if len(profile_matches) != 1:
            return None
        if profile_is_eligible(profile_matches[0], args):
            runtime_profile = profile_matches[0]
            runtime_defaults_profile = runtime_profile
        elif state_authoritative:
            # Exact model/context proof plus a still-matching .env is stronger
            # than a later best-effort hardware probe (notably under WSL). Keep
            # the proven runtime values but stop claiming the old profile is a
            # currently hardware-qualified selection.
            runtime_defaults_profile = profile_matches[0]
            profile_id = ""
        else:
            return None
    elif state_authoritative:
        profiles = model.get("runtime_profiles")
        exact_context_profiles = [
            item
            for item in profiles if isinstance(item, dict)
            and positive_int(item.get("context_length")) == context
            and isinstance(item.get("id"), str)
            and item.get("id")
        ] if isinstance(profiles, list) else []
        if len(exact_context_profiles) == 1:
            candidate = exact_context_profiles[0]
            candidate_env = candidate.get("env")
            if (
                isinstance(candidate_env, dict)
                and set(candidate_env) <= PORTABLE_STATE_RECOVERY_KEYS
                and all(
                    value is not None and valid_runtime_value(key, str(value))
                    for key, value in candidate_env.items()
                )
            ):
                runtime_defaults_profile = candidate
                candidate_matches_env = all(
                    env.get(key, "") == str(value)
                    for key, value in candidate_env.items()
                ) and all(
                    not env.get(key, "")
                    for key in RUNTIME_KEYS if key not in candidate_env
                )
                if not candidate_matches_env:
                    reuse_env_runtime = False
                if profile_is_eligible(candidate, args):
                    runtime_profile = candidate
                    profile_id = str(candidate["id"])

    declared_mb = positive_int(model.get("size_mb")) or 0
    actual_mb = max(1, math.ceil(actual_bytes / (1024 * 1024)))
    profile_env = (
        runtime_defaults_profile.get("env")
        if runtime_defaults_profile and isinstance(runtime_defaults_profile.get("env"), dict)
        else {}
    )
    runtime_values: dict[str, str] = {}
    for key in RUNTIME_KEYS:
        if reuse_env_runtime and key in env:
            value = env[key]
        elif key in profile_env and profile_env[key] is not None:
            value = str(profile_env[key])
        else:
            value = ""
        if value and not valid_runtime_value(key, value):
            return None
        runtime_values[key] = value

    image = ""
    if runtime_profile:
        image = str(runtime_profile.get("llama_server_image") or "")
    image = image or str(model.get("llama_server_image") or "")
    if image and not re.fullmatch(r"[A-Za-z0-9._/@:+-]{1,300}", image):
        return None

    old_source = "dashboard" if state_authoritative else env.get("MODEL_SELECTION_SOURCE", "")
    recommended_file = env.get("MODEL_RECOMMENDED_GGUF", "")
    if old_source in {"installer", "dashboard", "operator", "preserved-local"}:
        source = old_source
    elif recommended_file and recommended_file != active_file:
        source = "dashboard"
    else:
        source = "preserved-local"

    contract = {
        "LLM_MODEL": llm_model,
        "GGUF_FILE": str(model["gguf_file"]),
        "GGUF_URL": str(primary["url"]),
        "GGUF_SHA256": str(primary["sha256"]),
        "MAX_CONTEXT": str(context),
        "LLM_MODEL_SIZE_MB": str(max(declared_mb, actual_mb)),
        "MODEL_RUNTIME_PROFILE": profile_id,
        "MODEL_RUNTIME_PROFILE_LABEL": str(runtime_profile.get("label") or "") if runtime_profile else "",
        "MODEL_RUNTIME_PROFILE_SOURCE": str(runtime_profile.get("source_url") or "") if runtime_profile else "",
        "MODEL_SELECTION_SOURCE": source,
        "LLAMA_SERVER_IMAGE": image,
        **runtime_values,
    }
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--imports", type=Path)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--backend", default="unknown")
    parser.add_argument("--memory-type", default="discrete")
    parser.add_argument("--vram-mb", type=float, default=0)
    parser.add_argument("--ram-gb", type=float, default=0)
    parser.add_argument("--host-arch", default=platform.machine())
    args = parser.parse_args()

    contract = preserved_contract(args)
    if contract is None:
        return 0
    for key, value in contract.items():
        # Compose's list-form environment entries inherit exported host values.
        # Emitting an empty optional LLAMA_* key would therefore turn "unset"
        # into an explicit empty string inside llama.cpp, where numeric options
        # such as LLAMA_ARG_N_CPU_MOE fail to parse. The installer clears stale
        # selector variables before loading this fragment, so omission is the
        # correct representation of an inactive optional setting.
        if not value and (key in RUNTIME_KEYS or key == "LLAMA_SERVER_IMAGE"):
            continue
        print(f"{key}={shell_value(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Thin wrapper: configure OpenCode from environment metadata.

Consumes credentials/route metadata from the environment and delegates to
bin/opencode_config.py (the JSONC-preserving core). Never prints secrets.

Environment:
  ODS_OPENCODE_MODEL        model id (required)
  ODS_OPENCODE_DISPLAY_NAME display name (defaults to model id)
  ODS_OPENCODE_PROVIDER_NAME provider display name (defaults to "ODS inference")
  ODS_OPENCODE_BASE_URL     base URL (required)
  ODS_OPENCODE_API_KEY      API key (required)
  ODS_OPENCODE_CONTEXT      context length (required, >= 1024)

Usage:
  configure-opencode.py <config_dir> <platform:macos|linux>
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import NoReturn


def _fail(msg: str) -> NoReturn:
    # Never echo secrets; only structural/validation messages.
    sys.stderr.write(f"configure-opencode: {msg}\n")
    raise SystemExit(1)


def _load_core():
    here = Path(__file__).resolve().parent
    core_path = here / "opencode_config.py"
    if not core_path.is_file():
        _fail(f"missing core module: {core_path.name}")
    spec = importlib.util.spec_from_file_location("opencode_config", core_path)
    if spec is None or spec.loader is None:
        _fail("could not load core module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        _fail(f"missing required environment variable {name}")
    return value


def _parse_context(raw: str) -> int:
    try:
        context = int(raw)
    except ValueError:
        _fail("ODS_OPENCODE_CONTEXT must be an integer")
    if context < 1024:
        _fail("ODS_OPENCODE_CONTEXT must be at least 1024")
    return context


def _output_limit(context: int) -> int:
    return min(32768, context // 4)


def _build_patch(model: str, display_name: str, provider_name: str,
                 base_url: str, api_key: str, context: int) -> dict:
    provider_id = "llama-server"
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{model}",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider_name,
                "options": {"baseURL": base_url, "apiKey": api_key},
                "models": {
                    model: {
                        "name": display_name,
                        "limit": {"context": context, "output": _output_limit(context)},
                    }
                },
            }
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        _fail("usage: configure-opencode.py <config_dir> <platform:macos|linux>")
    config_dir = Path(argv[1])
    platform = argv[2]
    if platform not in ("macos", "linux"):
        _fail(f"unsupported platform: {platform}")

    model = _require_env("ODS_OPENCODE_MODEL")
    base_url = _require_env("ODS_OPENCODE_BASE_URL")
    api_key = _require_env("ODS_OPENCODE_API_KEY")
    context = _parse_context(_require_env("ODS_OPENCODE_CONTEXT"))
    display_name = os.environ.get("ODS_OPENCODE_DISPLAY_NAME") or model
    provider_name = os.environ.get("ODS_OPENCODE_PROVIDER_NAME") or "ODS inference"

    core = _load_core()
    try:
        effective = core.read_effective(str(config_dir))
    except Exception as exc:  # noqa: BLE001 - surface structural error only
        _fail(f"could not read existing config: {type(exc).__name__}")

    patch = _build_patch(model, display_name, provider_name, base_url, api_key, context)
    if "$schema" in effective:
        del patch["$schema"]
    selected = f"llama-server/{model}"
    previous = effective.get("model")
    previous_ods = isinstance(previous, str) and previous.startswith("llama-server/")

    def managed_selection(current):
        return current is None or (previous_ods and current == previous)

    # Linux retains its existing small-model policy. On macOS, fill an unset
    # selection or follow the prior ODS model while preserving owner choices.
    if platform == "linux" or managed_selection(effective.get("small_model")):
        patch["small_model"] = selected

    # OpenCode 1.18.32's web fallback truncates slash-containing model IDs in
    # the top-level model string. Agent models are parsed server-side into
    # provider/model objects, preserving ODS's "ods/current" route.
    agents = effective.get("agent", {})
    if not isinstance(agents, dict):
        _fail("agent config must be an object")
    for role in ("build", "plan"):
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            _fail("agent role config must be an object")
        if managed_selection(agent.get("model")):
            patch.setdefault("agent", {})[role] = {"model": selected}

    try:
        core.update_config(str(config_dir), patch, expected_effective=effective)
    except Exception as exc:  # noqa: BLE001 - surface structural error only
        _fail(f"config update rejected: {type(exc).__name__}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

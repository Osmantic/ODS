#!/usr/bin/env python3
"""Render ODS runtime config surfaces deterministically.

The first purpose of this script is read-only comparison: installers and
runtime mutators can ask what config should look like without writing files.
Follow-up wiring can then replace ad-hoc heredocs one surface at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "qwen3.5-9b"
DEFAULT_GGUF = "Qwen3.5-9B-Q4_K_M.gguf"
DEFAULT_CONTEXT = 131072
DEFAULT_LITELLM_KEY = "sk-lemonade"
NO_KEY = "no-key"


@dataclass(frozen=True)
class RenderInputs:
    model: str
    gguf_file: str
    lemonade_model_id: str
    lemonade_api_base: str
    gpu_backend: str
    ods_mode: str
    llm_base_url: str
    litellm_key: str
    opencode_port: int
    context_length: int
    hipfire_enabled: bool
    hipfire_active: bool
    hipfire_model: str
    hipfire_api_base: str


@dataclass(frozen=True)
class RenderedFile:
    surface: str
    path: str
    content: str


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def lemonade_model_id(inputs: RenderInputs) -> str:
    if inputs.lemonade_model_id:
        return inputs.lemonade_model_id
    return f"extra.{inputs.gguf_file}"


def hermes_model_id(inputs: RenderInputs) -> str:
    if inputs.ods_mode == "lemonade" or inputs.gpu_backend == "amd":
        return lemonade_model_id(inputs)
    return inputs.gguf_file or inputs.model


def opencode_key(inputs: RenderInputs) -> str:
    return inputs.litellm_key if inputs.ods_mode == "lemonade" else NO_KEY


def render_litellm_lemonade(inputs: RenderInputs) -> RenderedFile:
    model = lemonade_model_id(inputs)
    api_base = inputs.lemonade_api_base.rstrip("/") or "http://llama-server:8080/api/v1"
    lemonade_params = f"""    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {inputs.litellm_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false
"""
    if inputs.hipfire_enabled and inputs.hipfire_model:
        hipfire_base = inputs.hipfire_api_base.rstrip("/") or "http://hipfire:11435/v1"
        # hipfire has no auth; the host port stays loopback-bound via BIND_ADDRESS.
        hipfire_params = f"""    litellm_params:
      model: openai/{inputs.hipfire_model}
      api_base: {hipfire_base}
      api_key: not-needed
"""
        default_params = hipfire_params if inputs.hipfire_active else lemonade_params
        content = (
            "model_list:\n"
            f"  - model_name: default\n{default_params}"
            "\n"
            f"  - model_name: hipfire\n{hipfire_params}"
            "\n"
            f"  - model_name: lemonade\n{lemonade_params}"
            "\n"
            f"  - model_name: \"*\"\n{default_params}"
            "\n"
            "litellm_settings:\n"
            "  drop_params: true\n"
            "  set_verbose: false\n"
            "  request_timeout: 900\n"
            "  stream_timeout: 900\n"
        )
        return RenderedFile("litellm-lemonade", "config/litellm/lemonade.yaml", content)

    content = (
        "model_list:\n"
        f"  - model_name: default\n{lemonade_params}"
        "\n"
        f"  - model_name: \"*\"\n{lemonade_params}"
        "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        "  set_verbose: false\n"
        "  request_timeout: 900\n"
        "  stream_timeout: 900\n"
    )
    return RenderedFile("litellm-lemonade", "config/litellm/lemonade.yaml", content)


def render_hermes(inputs: RenderInputs) -> RenderedFile:
    model = hermes_model_id(inputs)
    content = f"""model:
  default: "{model}"
  provider: "custom"
  base_url: "{inputs.llm_base_url}"
  context_length: {inputs.context_length}

auxiliary:
  compression:
    context_length: {inputs.context_length}

compression:
  enabled: true
  threshold: 0.75
  target_ratio: 0.50
  protect_last_n: 40
"""
    return RenderedFile("hermes", "data/hermes/config.yaml", content)


def render_perplexica(inputs: RenderInputs) -> RenderedFile:
    model = lemonade_model_id(inputs) if inputs.ods_mode == "lemonade" else (inputs.gguf_file or inputs.model)
    base_url = inputs.llm_base_url.rstrip("/") or "http://llama-server:8080"
    if not (base_url.endswith("/v1") or base_url.endswith("/api/v1")):
        base_url = f"{base_url}/v1"
    payload = {
        "modelProviders": [
            {
                "id": "openai",
                "type": "openai",
                "name": "ODS",
                "config": {
                    "apiKey": opencode_key(inputs),
                    "baseURL": base_url,
                },
                "chatModels": [{"key": model, "name": model}],
            }
        ],
        "preferences": {
            "defaultChatProvider": "openai",
            "defaultChatModel": model,
            "defaultEmbeddingProvider": "transformers",
            "defaultEmbeddingModel": "Xenova/all-MiniLM-L6-v2",
        },
        "setupComplete": True,
    }
    return RenderedFile(
        "perplexica",
        "data/perplexica/settings.seed.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def render_opencode(inputs: RenderInputs) -> RenderedFile:
    payload = {
        "provider": "openai-compatible",
        "baseURL": inputs.llm_base_url,
        "apiKey": opencode_key(inputs),
        "model": lemonade_model_id(inputs) if inputs.ods_mode == "lemonade" else inputs.model,
        "port": inputs.opencode_port,
    }
    return RenderedFile(
        "opencode",
        ".opencode/auth.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def render_env(inputs: RenderInputs) -> RenderedFile:
    lemonade_model = (
        lemonade_model_id(inputs)
        if inputs.ods_mode == "lemonade"
        else inputs.lemonade_model_id
    )
    lines = [
        f"ODS_MODE={inputs.ods_mode}",
        f"LLM_BACKEND={'lemonade' if inputs.ods_mode == 'lemonade' else 'llama-server'}",
        f"LLM_MODEL={inputs.model}",
        f"GGUF_FILE={inputs.gguf_file}",
        f"LEMONADE_MODEL={lemonade_model}",
        f"GPU_BACKEND={inputs.gpu_backend}",
        f"LLM_API_URL={inputs.llm_base_url}",
        f"CTX_SIZE={inputs.context_length}",
        f"MAX_CONTEXT={inputs.context_length}",
    ]
    return RenderedFile("env", ".env.generated", "\n".join(lines) + "\n")


RENDERERS: dict[str, Callable[[RenderInputs], RenderedFile]] = {
    "env": render_env,
    "opencode": render_opencode,
    "litellm-lemonade": render_litellm_lemonade,
    "perplexica": render_perplexica,
    "hermes": render_hermes,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=["all", *sorted(RENDERERS)], default="all")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--gguf-file", default=DEFAULT_GGUF)
    parser.add_argument("--lemonade-model-id", default="")
    parser.add_argument("--lemonade-api-base", default="http://llama-server:8080/api/v1")
    parser.add_argument("--gpu-backend", choices=["amd", "apple", "cpu", "nvidia"], default="nvidia")
    parser.add_argument("--ods-mode", choices=["local", "cloud", "hybrid", "lemonade"], default="local")
    parser.add_argument("--llm-base-url", default="http://llama-server:8080/v1")
    parser.add_argument("--litellm-key", default=DEFAULT_LITELLM_KEY)
    parser.add_argument("--opencode-port", type=int, default=3003)
    parser.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT)
    parser.add_argument("--hipfire-enabled", action="store_true")
    parser.add_argument("--hipfire-active", action="store_true")
    parser.add_argument("--hipfire-model", default="")
    parser.add_argument("--hipfire-api-base", default="http://hipfire:11435/v1")
    parser.add_argument("--format", choices=["json", "paths"], default="json")
    parser.add_argument("--output-root", default=".", help="Root directory used with --write")
    parser.add_argument("--write", action="store_true", help="Write rendered files under --output-root")
    return parser.parse_args(argv)


def select_surfaces(surface: str) -> list[str]:
    if surface == "all":
        return ["env", "opencode", "litellm-lemonade", "perplexica", "hermes"]
    return [surface]


def read_env_values(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, surrounding quotes stripped,
    later occurrences win — same semantics as the shell installers and the
    host-agent's load_env."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if key not in keys:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def resolve_hipfire_state(args: argparse.Namespace) -> tuple[bool, bool, str]:
    """Hipfire routing state: explicit flags win; otherwise read the install
    tree's .env.

    Flag-less callers (`ods model swap` today) must not wipe hipfire's routes
    from lemonade.yaml just because they never learned the flags exist —
    hipfire state lives in .env, so when the caller doesn't assert it, derive
    it from --output-root/.env instead of assuming off. Callers that do pass
    --hipfire-enabled read the same .env first, so both paths agree."""
    if args.hipfire_enabled:
        return True, args.hipfire_active, args.hipfire_model
    env = read_env_values(
        Path(args.output_root) / ".env",
        ("ENABLE_HIPFIRE", "HIPFIRE_MODEL", "HIPFIRE_ACTIVE"),
    )
    if env.get("ENABLE_HIPFIRE", "").strip().lower() != "true":
        return False, args.hipfire_active, args.hipfire_model
    return (
        True,
        env.get("HIPFIRE_ACTIVE", "").strip().lower() == "true",
        (env.get("HIPFIRE_MODEL") or "").strip(),
    )


def render(args: argparse.Namespace) -> dict[str, object]:
    hipfire_enabled, hipfire_active, hipfire_model = resolve_hipfire_state(args)
    inputs = RenderInputs(
        model=args.model,
        gguf_file=args.gguf_file,
        lemonade_model_id=args.lemonade_model_id,
        lemonade_api_base=args.lemonade_api_base,
        gpu_backend=args.gpu_backend,
        ods_mode=args.ods_mode,
        llm_base_url=args.llm_base_url,
        litellm_key=args.litellm_key,
        opencode_port=args.opencode_port,
        context_length=args.context_length,
        hipfire_enabled=hipfire_enabled,
        hipfire_active=hipfire_active,
        hipfire_model=hipfire_model,
        hipfire_api_base=args.hipfire_api_base,
    )
    files = [RENDERERS[name](inputs) for name in select_surfaces(args.surface)]
    written: list[str] = []
    if args.write:
        output_root = Path(args.output_root)
        for item in files:
            target = output_root / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ensure_trailing_newline(item.content), encoding="utf-8")
            written.append(str(target))
    return {
        "version": "1",
        "mode": "write" if args.write else "dry-run",
        "inputs": asdict(inputs),
        "files": [asdict(RenderedFile(item.surface, item.path, ensure_trailing_newline(item.content))) for item in files],
        "written": written,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload = render(args)
    if args.format == "paths":
        for item in payload["files"]:
            print(item["path"])
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

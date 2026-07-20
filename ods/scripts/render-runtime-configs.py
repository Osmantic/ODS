#!/usr/bin/env python3
"""Render ODS runtime config surfaces deterministically.

The first purpose of this script is read-only comparison: installers and
runtime mutators can ask what config should look like without writing files.
Follow-up wiring can then replace ad-hoc heredocs one surface at a time.
"""

from __future__ import annotations

import argparse
import json
import os
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
    # Switchboard rollout mode: legacy | observe | enabled (plan section 8)
    switchboard_mode: str = "observe"


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
    if inputs.switchboard_mode == "enabled":
        return inputs.litellm_key
    return inputs.litellm_key if inputs.ods_mode == "lemonade" else NO_KEY


def render_litellm_lemonade(inputs: RenderInputs) -> RenderedFile:
    model = lemonade_model_id(inputs)
    api_base = inputs.lemonade_api_base.rstrip("/") or "http://llama-server:8080/api/v1"
    content = f"""model_list:
  - model_name: default
    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {inputs.litellm_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {inputs.litellm_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
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
    if inputs.switchboard_mode == "enabled":
        model = "ods/current"
        base_url = "http://litellm:4000/v1"
        api_key = inputs.litellm_key
    else:
        model = lemonade_model_id(inputs) if inputs.ods_mode == "lemonade" else (inputs.gguf_file or inputs.model)
        base_url = inputs.llm_base_url.rstrip("/") or "http://llama-server:8080"
        api_key = opencode_key(inputs)
    if not (base_url.endswith("/v1") or base_url.endswith("/api/v1")):
        base_url = f"{base_url}/v1"
    payload = {
        "modelProviders": [
            {
                "id": "openai",
                "type": "openai",
                "name": "ODS",
                "config": {
                    "apiKey": api_key,
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
    if inputs.switchboard_mode == "enabled":
        base_url = "http://litellm:4000/v1"
        model = "ods/current"
    else:
        base_url = inputs.llm_base_url
        model = lemonade_model_id(inputs) if inputs.ods_mode == "lemonade" else inputs.model
    payload = {
        "provider": "openai-compatible",
        "baseURL": base_url,
        "apiKey": opencode_key(inputs),
        "model": model,
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
        f"ODS_MODEL_SWITCHBOARD={inputs.switchboard_mode}",
        f"LLM_BACKEND={'lemonade' if inputs.ods_mode == 'lemonade' else 'llama-server'}",
        f"LLM_MODEL={inputs.model}",
        f"GGUF_FILE={inputs.gguf_file}",
        f"LEMONADE_MODEL={lemonade_model}",
        f"GPU_BACKEND={inputs.gpu_backend}",
        f"LLM_API_URL={inputs.llm_base_url}",
        f"CTX_SIZE={inputs.context_length}",
        f"MAX_CONTEXT={inputs.context_length}",
    ]
    if inputs.switchboard_mode == "enabled":
        lines.extend([
            "OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000",
            f"OPEN_WEBUI_LLM_API_KEY={inputs.litellm_key}",
        ])
    return RenderedFile("env", ".env.generated", "\n".join(lines) + "\n")


def render_litellm_switchboard(inputs: RenderInputs) -> RenderedFile:
    """Stable-alias LiteLLM map: every public alias forwards to model-router.

    Rendered only in enabled mode; legacy/observe keep the pre-switchboard
    configuration byte-identical. The renderer owns this YAML — no installer,
    CLI, or host-agent heredoc may maintain a second enabled-mode copy.
    """
    local_route = """    litellm_params:
      model: openai/ods/current
      api_base: http://model-router:9099/v1
      api_key: no-key
"""
    routes = []
    for name in ("ods/current", "local", "default"):
        routes.append(f"  - model_name: {name}\n{local_route}")
    if inputs.ods_mode == "hybrid":
        routes.extend([
            """  - model_name: cloud
    litellm_params:
      model: anthropic/claude-sonnet-4-5-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
""",
            """  - model_name: minimax
    litellm_params:
      model: openai/MiniMax-M2.7
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY
""",
            """  - model_name: minimax-fast
    litellm_params:
      model: openai/MiniMax-M2.7-highspeed
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY
""",
        ])
    routes.append(f'  - model_name: "*"\n{local_route}')
    content = (
        "model_list:\n"
        + "".join(routes)
        + """
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
    )
    return RenderedFile(
        "litellm-switchboard", "config/litellm/switchboard.yaml", content
    )


def render_model_router_endpoints(inputs: RenderInputs) -> RenderedFile:
    """Static endpoint allowlist for model-router (plan section 3.6).

    Generated from known runtime topology at install; state may only select
    an id from this file, never an arbitrary URL.
    """
    def _origin_base(url: str, fallback: str) -> str:
        # endpoints.json stores the server base WITHOUT a trailing /v1: the
        # router appends the full OpenAI path (/v1/chat/completions, ...).
        base = (url or fallback).rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base

    endpoints = [
        {"id": "llama-server-default",
         "baseUrl": _origin_base(inputs.llm_base_url, "http://llama-server:8080")},
    ]
    if inputs.gpu_backend.lower() == "amd" or inputs.ods_mode == "lemonade":
        endpoints.append({
            "id": "lemonade-default",
            "baseUrl": _origin_base(inputs.lemonade_api_base, "http://lemonade:8000/api"),
        })
    content = json.dumps({"endpoints": endpoints}, indent=2) + "\n"
    return RenderedFile(
        "model-router-endpoints", "config/model-router/endpoints.json", content
    )


RENDERERS: dict[str, Callable[[RenderInputs], RenderedFile]] = {
    "env": render_env,
    "opencode": render_opencode,
    "litellm-lemonade": render_litellm_lemonade,
    "perplexica": render_perplexica,
    "hermes": render_hermes,
    "litellm-switchboard": render_litellm_switchboard,
    "model-router-endpoints": render_model_router_endpoints,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=["all", *sorted(RENDERERS)], default="all")
    parser.add_argument(
        "--switchboard-mode",
        choices=["legacy", "observe", "enabled"],
        default=os.environ.get("ODS_MODEL_SWITCHBOARD", "observe"),
    )
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
    parser.add_argument("--format", choices=["json", "paths"], default="json")
    parser.add_argument("--output-root", default=".", help="Root directory used with --write")
    parser.add_argument("--write", action="store_true", help="Write rendered files under --output-root")
    return parser.parse_args(argv)


def select_surfaces(surface: str, switchboard_mode: str = "observe") -> list[str]:
    if surface == "all":
        surfaces = ["env", "opencode", "litellm-lemonade", "perplexica", "hermes",
                    "model-router-endpoints"]
        if switchboard_mode == "enabled":
            surfaces.append("litellm-switchboard")
        return surfaces
    return [surface]


def render(args: argparse.Namespace) -> dict[str, object]:
    inputs = RenderInputs(
        switchboard_mode=getattr(args, 'switchboard_mode', 'observe'),
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
    )
    files = [RENDERERS[name](inputs) for name in select_surfaces(args.surface, inputs.switchboard_mode)]
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

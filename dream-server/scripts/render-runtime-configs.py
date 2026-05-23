#!/usr/bin/env python3
"""Render Dream Server runtime config surfaces deterministically.

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
    gpu_backend: str
    dream_mode: str
    llm_base_url: str
    litellm_key: str
    opencode_port: int
    context_length: int


@dataclass(frozen=True)
class RenderedFile:
    surface: str
    path: str
    content: str


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def lemonade_model_id(inputs: RenderInputs) -> str:
    return f"extra.{inputs.gguf_file}"


def hermes_model_id(inputs: RenderInputs) -> str:
    if inputs.dream_mode == "lemonade" or inputs.gpu_backend == "amd":
        return lemonade_model_id(inputs)
    return inputs.gguf_file or inputs.model


def opencode_key(inputs: RenderInputs) -> str:
    return inputs.litellm_key if inputs.dream_mode == "lemonade" else NO_KEY


def render_litellm_lemonade(inputs: RenderInputs) -> RenderedFile:
    model = lemonade_model_id(inputs)
    content = f"""model_list:
  - model_name: default
    litellm_params:
      model: openai/{model}
      api_base: http://llama-server:8080/api/v1
      api_key: {inputs.litellm_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/{model}
      api_base: http://llama-server:8080/api/v1
      api_key: {inputs.litellm_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 120
  stream_timeout: 60
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
    model = lemonade_model_id(inputs) if inputs.dream_mode == "lemonade" else (inputs.gguf_file or inputs.model)
    payload = {
        "modelProviders": {
            "openai": {
                "name": "Dream Server",
                "baseUrl": inputs.llm_base_url,
                "apiKey": opencode_key(inputs),
                "chatModels": [{"name": model, "displayName": model}],
            }
        },
        "preferences": {
            "defaultChatProvider": "openai",
            "defaultChatModel": model,
        },
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
        "model": lemonade_model_id(inputs) if inputs.dream_mode == "lemonade" else inputs.model,
        "port": inputs.opencode_port,
    }
    return RenderedFile(
        "opencode",
        ".opencode/auth.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def render_env(inputs: RenderInputs) -> RenderedFile:
    lines = [
        f"DREAM_MODE={inputs.dream_mode}",
        f"LLM_BACKEND={'lemonade' if inputs.dream_mode == 'lemonade' else 'llama-server'}",
        f"LLM_MODEL={inputs.model}",
        f"GGUF_FILE={inputs.gguf_file}",
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
    parser.add_argument("--gpu-backend", choices=["amd", "apple", "cpu", "nvidia"], default="nvidia")
    parser.add_argument("--dream-mode", choices=["local", "cloud", "hybrid", "lemonade"], default="local")
    parser.add_argument("--llm-base-url", default="http://llama-server:8080/v1")
    parser.add_argument("--litellm-key", default=DEFAULT_LITELLM_KEY)
    parser.add_argument("--opencode-port", type=int, default=3003)
    parser.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT)
    parser.add_argument("--format", choices=["json", "paths"], default="json")
    parser.add_argument("--output-root", default=".", help="Root directory used with --write")
    parser.add_argument("--write", action="store_true", help="Write rendered files under --output-root")
    return parser.parse_args(argv)


def select_surfaces(surface: str) -> list[str]:
    if surface == "all":
        return ["env", "opencode", "litellm-lemonade", "perplexica", "hermes"]
    return [surface]


def render(args: argparse.Namespace) -> dict[str, object]:
    inputs = RenderInputs(
        model=args.model,
        gguf_file=args.gguf_file,
        gpu_backend=args.gpu_backend,
        dream_mode=args.dream_mode,
        llm_base_url=args.llm_base_url,
        litellm_key=args.litellm_key,
        opencode_port=args.opencode_port,
        context_length=args.context_length,
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

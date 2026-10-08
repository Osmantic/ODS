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
import re
import stat
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "qwen3.5-9b"
DEFAULT_GGUF = "Qwen3.5-9B-Q4_K_M.gguf"
DEFAULT_CONTEXT = 131072
DEFAULT_HERMES_MAX_TOKENS = 1024
# Placeholder for dry runs only; installers and the host agent pass the real
# gateway key through ODS_RENDER_LITELLM_KEY.
DEFAULT_LITELLM_KEY = "sk-ods-unset"
NO_KEY = "no-key"
API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# One-release compatibility: ODS_MODE=lemonade is the retired managed-AMD
# mode; it renders exactly like local.
LEGACY_ODS_MODES = {"lemonade": "local"}
PUBLIC_MODEL_ALIAS = "ods/current"
REMOTE_PROVIDER_EGRESS_BASE_URL = "http://remote-provider-egress:8091/v1"
REMOTE_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")


def atomic_write_text(target: Path, content: str, *, file_mode: int | None = None) -> None:
    """Replace a generated config without exposing a truncated live file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the mode of existing configs, which may hold private values.
    # The nonsecret router endpoint allowlist explicitly requests 0644.
    mode = file_mode if file_mode is not None else 0o644
    if file_mode is None:
        try:
            if target.is_file():
                mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            pass

    # Docker Desktop can retain the old inode behind a file bind. Do not
    # invalidate that view when a render changes neither bytes nor mode.
    # A symlink, special file, hardlink or changed target still takes the
    # existing atomic replacement path, including permission repair.
    try:
        before = target.lstat()
        expected = content.encode("utf-8")
        if stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == len(expected):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(target, flags), "rb") as existing:
                opened = os.fstat(existing.fileno())
                same_file = (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino)
                if same_file and stat.S_ISREG(opened.st_mode):
                    matches = existing.read(len(expected) + 1) == expected
                    after = target.lstat()
                    identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                             info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink)
                    if matches and identity(before) == identity(after) and stat.S_IMODE(after.st_mode) == mode:
                        return
    except OSError:
        # Missing/unreadable paths do not qualify for the no-op optimization.
        # Preserve the original write and its normal error handling below.
        pass

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)

        last_error: PermissionError | None = None
        for attempt in range(10):
            try:
                os.replace(tmp_path, target)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt < 9:
                    time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        tmp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class RenderInputs:
    model: str
    gguf_file: str
    gpu_backend: str
    ods_mode: str
    llm_base_url: str
    litellm_key: str
    opencode_port: int
    context_length: int
    remote_llm_enabled: bool = False
    remote_llm_transport: str = ""
    remote_llm_base_url: str = ""
    remote_llm_model: str = ""
    external_llm_authenticated: bool = False
    # Env var holding a host-native llama-server key (LLAMA_SERVER_API_KEY).
    # Generated configs reference it by name; they never contain the key.
    llm_api_key_env: str = ""
    # Switchboard rollout mode: legacy | observe | enabled (plan section 8)
    switchboard_mode: str = "enabled"


@dataclass(frozen=True)
class RenderedFile:
    surface: str
    path: str
    content: str


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def yaml_scalar(value: str) -> str:
    """Emit a JSON string, which is also a safe YAML scalar."""
    return json.dumps(value)


def normalize_openai_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1") or base_url.endswith("/api/v1"):
        return base_url
    return f"{base_url}/v1"


def remote_route_enabled(inputs: RenderInputs) -> bool:
    return inputs.remote_llm_enabled


def hermes_model_id(inputs: RenderInputs) -> str:
    if inputs.switchboard_mode == "enabled":
        return "ods/current"
    return inputs.gguf_file or inputs.model


def uses_gateway(inputs: RenderInputs) -> bool:
    """Consumers reach a keyed host-native server only through LiteLLM."""
    return inputs.switchboard_mode == "enabled" or bool(inputs.llm_api_key_env)


def opencode_key(inputs: RenderInputs) -> str:
    return inputs.litellm_key if uses_gateway(inputs) else NO_KEY


def native_api_key(inputs: RenderInputs) -> str:
    if inputs.llm_api_key_env:
        return f"os.environ/{inputs.llm_api_key_env}"
    return "not-needed"


def render_litellm_local(inputs: RenderInputs) -> RenderedFile:
    content = """model_list:
  - model_name: ods/current
    litellm_params:
      model: openai/default
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: default
    litellm_params:
      model: openai/default
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/*
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
    return RenderedFile("litellm-local", "config/litellm/local.yaml", content)


def render_litellm_external(inputs: RenderInputs) -> RenderedFile:
    """Bind a selected local/LAN OpenAI-compatible model behind ODS auth."""
    model = inputs.model
    raw_base = inputs.llm_base_url
    if not model or len(model) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise ValueError("external gateway requires a nonempty model without control characters")
    parsed = urlsplit(raw_base)
    _ = parsed.port  # Reject malformed/out-of-range ports before rendering.
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/v1", "/api/v1"}
        or any(ord(c) <= 32 or ord(c) == 127 for c in raw_base)
    ):
        raise ValueError("external gateway requires a credential-free HTTP(S) API base")
    base = normalize_openai_base_url(raw_base)
    entries = []
    for alias in dict.fromkeys((PUBLIC_MODEL_ALIAS, "default", model, "*")):
        entries.append(f"""  - model_name: {yaml_scalar(alias)}
    litellm_params:
      model: {yaml_scalar('openai/' + model)}
      api_base: {yaml_scalar(base)}
      api_key: {'os.environ/EXTERNAL_LLM_API_KEY' if inputs.external_llm_authenticated else 'not-needed'}
""")
    content = "model_list:\n" + "\n".join(entries) + """
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
    return RenderedFile("litellm-external", "config/litellm/local.yaml", content)


def render_litellm_local_native(inputs: RenderInputs) -> RenderedFile:
    # ODS-CONTRACT-WRITER: litellm-local-native
    model = inputs.gguf_file or inputs.model
    api_base = inputs.llm_base_url.rstrip("/") or "http://host.docker.internal:8080/v1"
    api_key = native_api_key(inputs)
    content = f"""model_list:
  - model_name: ods/current
    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: default
    litellm_params:
      model: openai/{model}
      api_base: {api_base}
      api_key: {api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: "*"
    litellm_params:
      model: openai/*
      api_base: {api_base}
      api_key: {api_key}
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
    return RenderedFile(
        "litellm-local-native",
        "config/litellm/local.yaml",
        content,
    )


def render_litellm_cloud(inputs: RenderInputs) -> RenderedFile:
    if remote_route_enabled(inputs):
        model = inputs.remote_llm_model.strip()
        model_param = yaml_scalar(f"openai/{model}")
        egress_base = yaml_scalar(REMOTE_PROVIDER_EGRESS_BASE_URL)
        content = f"""model_list:
  # Stable public alias used by ODS consumers. Provider credentials stay in
  # remote-provider-egress, never in LiteLLM YAML or generated public config.
  # The egress admits only callers holding the LiteLLM gateway key.
  - model_name: {PUBLIC_MODEL_ALIAS}
    litellm_params:
      model: {model_param}
      api_base: {egress_base}
      api_key: os.environ/LITELLM_MASTER_KEY

  - model_name: default
    litellm_params:
      model: {model_param}
      api_base: {egress_base}
      api_key: os.environ/LITELLM_MASTER_KEY

  - model_name: {yaml_scalar(model)}
    litellm_params:
      model: {model_param}
      api_base: {egress_base}
      api_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  routing_strategy: simple-shuffle

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
"""
        return RenderedFile("litellm-cloud", "config/litellm/cloud.yaml", content)

    content = """model_list:
  # Stable public alias used by Switchboard-aware ODS consumers.
  - model_name: ods/current
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: default
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: gpt4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: fast
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: minimax
    litellm_params:
      model: openai/MiniMax-M2.7
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY

  - model_name: minimax-fast
    litellm_params:
      model: openai/MiniMax-M2.7-highspeed
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY

router_settings:
  routing_strategy: simple-shuffle

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
"""
    return RenderedFile("litellm-cloud", "config/litellm/cloud.yaml", content)


def render_litellm_hybrid(inputs: RenderInputs) -> RenderedFile:
    content = """model_list:
  - model_name: ods/current
    litellm_params:
      model: openai/default
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: local
    litellm_params:
      model: openai/default
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

  - model_name: cloud
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: minimax
    litellm_params:
      model: openai/MiniMax-M2.7
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY

  - model_name: minimax-fast
    litellm_params:
      model: openai/MiniMax-M2.7-highspeed
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY

  - model_name: default
    litellm_params:
      model: openai/default
      api_base: http://llama-server:8080/v1
      api_key: not-needed
      extra_body:
        chat_template_kwargs:
          enable_thinking: false

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  fallbacks:
    - local:
        - cloud

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 900
  stream_timeout: 900
"""
    return RenderedFile("litellm-hybrid", "config/litellm/hybrid.yaml", content)


def render_hermes(inputs: RenderInputs) -> RenderedFile:
    model = hermes_model_id(inputs)
    base_url = (
        "http://litellm:4000/v1"
        if inputs.switchboard_mode == "enabled"
        else inputs.llm_base_url
    )
    content = f"""model:
  default: "{model}"
  provider: "custom"
  base_url: "{base_url}"
  context_length: {inputs.context_length}
  max_tokens: {DEFAULT_HERMES_MAX_TOKENS}

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
    elif uses_gateway(inputs):
        model = inputs.gguf_file or inputs.model
        base_url = "http://litellm:4000/v1"
        api_key = inputs.litellm_key
    else:
        model = inputs.gguf_file or inputs.model
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
    elif uses_gateway(inputs):
        base_url = "http://litellm:4000/v1"
        model = inputs.gguf_file or inputs.model
    else:
        base_url = inputs.llm_base_url
        model = inputs.model
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
    lines = [
        f"ODS_MODE={inputs.ods_mode}",
        f"ODS_MODEL_SWITCHBOARD={inputs.switchboard_mode}",
        "LLM_BACKEND=llama-server",
        f"LLM_MODEL={inputs.model}",
        f"GGUF_FILE={inputs.gguf_file}",
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
    if remote_route_enabled(inputs):
        lines.extend([
            "REMOTE_LLM_ENABLED=true",
            f"REMOTE_LLM_TRANSPORT={inputs.remote_llm_transport}",
            f"REMOTE_LLM_BASE_URL={normalize_openai_base_url(inputs.remote_llm_base_url)}",
            f"REMOTE_LLM_MODEL={inputs.remote_llm_model}",
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
      model: anthropic/claude-sonnet-4-6
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
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("model router endpoint origin must be an HTTP URL without embedded credentials")
        return base

    # Every managed runtime is upstream llama-server; a host-native one is
    # reached at http://host.docker.internal:<port> with its key, which the
    # router reads from the named env var (never from this file).
    endpoint = {"id": "llama-server-default",
                "baseUrl": _origin_base(inputs.llm_base_url, "http://llama-server:8080")}
    if inputs.llm_api_key_env:
        endpoint["apiKeyEnv"] = inputs.llm_api_key_env
    content = json.dumps({"endpoints": [endpoint]}, indent=2) + "\n"
    return RenderedFile(
        "model-router-endpoints", "config/model-router/endpoints.json", content
    )


def render_remote_routing_state(inputs: RenderInputs) -> RenderedFile:
    enabled = remote_route_enabled(inputs)
    provider = None
    if enabled:
        provider = {
            "capability": "openai-compatible",
            "baseUrl": normalize_openai_base_url(inputs.remote_llm_base_url),
            "model": inputs.remote_llm_model.strip(),
            "transport": inputs.remote_llm_transport,
        }
    payload = {
        "schema": "ods.remote-routing-state.v1",
        "enabled": enabled,
        "mode": inputs.ods_mode,
        "provider": provider,
        "projection": {
            "publicModel": PUBLIC_MODEL_ALIAS,
            "gateway": "litellm-cloud",
            "egressBaseUrl": REMOTE_PROVIDER_EGRESS_BASE_URL,
            "consumerRoute": "gateway",
        },
        "status": {
            "proven": False,
            "reason": "pending-provider-handshake" if enabled else "disabled",
        },
    }
    return RenderedFile(
        "remote-routing-state",
        "data/remote-provider/routing-state.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


RENDERERS: dict[str, Callable[[RenderInputs], RenderedFile]] = {
    "env": render_env,
    "opencode": render_opencode,
    "litellm-local": render_litellm_local,
    "litellm-local-native": render_litellm_local_native,
    "litellm-external": render_litellm_external,
    "litellm-cloud": render_litellm_cloud,
    "litellm-hybrid": render_litellm_hybrid,
    "perplexica": render_perplexica,
    "hermes": render_hermes,
    "litellm-switchboard": render_litellm_switchboard,
    "model-router-endpoints": render_model_router_endpoints,
    "remote-routing-state": render_remote_routing_state,
}


def parse_remote_enabled(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=["all", *sorted(RENDERERS)], default="all")
    switchboard_mode = parser.add_argument(
        "--switchboard-mode",
        choices=["legacy", "observe", "enabled"],
        default=os.environ.get("ODS_MODEL_SWITCHBOARD", "enabled"),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--gguf-file", default=DEFAULT_GGUF)
    # Accepted and ignored for one release: a running host agent or installer
    # from before round F still passes them on every render (R13).
    parser.add_argument("--lemonade-model-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--lemonade-api-base", default="", help=argparse.SUPPRESS)
    parser.add_argument("--gpu-backend", choices=["amd", "apple", "cpu", "nvidia"], default="nvidia")
    parser.add_argument(
        "--ods-mode", choices=["local", "cloud", "hybrid", *LEGACY_ODS_MODES], default="local",
    )
    parser.add_argument("--llm-base-url", default="http://llama-server:8080/v1")
    parser.add_argument(
        "--llm-api-key-env", default="",
        help=(
            "Name of the env var (LLAMA_SERVER_API_KEY) that holds a host-native "
            "llama-server key. LiteLLM and the model-router read it by name."
        ),
    )
    parser.add_argument("--external-llm-authenticated", action="store_true")
    parser.add_argument(
        "--litellm-key",
        default=os.environ.get("ODS_RENDER_LITELLM_KEY", DEFAULT_LITELLM_KEY),
        help=(
            "LiteLLM credential used in generated private config. Production callers "
            "should use ODS_RENDER_LITELLM_KEY so the value is not exposed in argv."
        ),
    )
    parser.add_argument("--opencode-port", type=int, default=3003)
    parser.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT)
    remote_enabled = parser.add_argument(
        "--remote-llm-enabled",
        choices=["", "true", "false"],
        default=os.environ.get("REMOTE_LLM_ENABLED", "false").strip().lower(),
    )
    remote_transport = parser.add_argument(
        "--remote-llm-transport",
        choices=["", "direct", "ssh"],
        default=os.environ.get("REMOTE_LLM_TRANSPORT", ""),
    )
    parser.add_argument(
        "--remote-llm-base-url",
        default=os.environ.get("REMOTE_LLM_BASE_URL", ""),
    )
    parser.add_argument(
        "--remote-llm-model",
        default=os.environ.get("REMOTE_LLM_MODEL", ""),
    )
    parser.add_argument(
        "--format",
        choices=["json", "paths"],
        default=None,
        help=(
            "Output format. Defaults to json for dry runs and secret-free paths "
            "for --write."
        ),
    )
    parser.add_argument("--output-root", default=".", help="Root directory used with --write")
    parser.add_argument("--write", action="store_true", help="Write rendered files under --output-root")
    args = parser.parse_args(argv)
    # argparse checks explicit choices, but not string defaults from the
    # environment. A typo must not silently select a different routing mode
    # and overwrite working runtime configs. Validate effective values so an
    # explicit valid CLI override can still repair a bad environment default.
    for action in (switchboard_mode, remote_enabled, remote_transport):
        value = getattr(args, action.dest)
        if value not in action.choices:
            parser.error(f"{action.option_strings[0]}: invalid choice {value!r}; choose from {action.choices}")
    return args


def select_surfaces(
    surface: str,
    ods_mode: str = "local",
    switchboard_mode: str = "enabled",
    remote_llm_enabled: bool = False,
) -> list[str]:
    if surface == "all":
        mode_surface = {
            "local": "litellm-local",
            "cloud": "litellm-cloud",
            "hybrid": "litellm-hybrid",
        }[LEGACY_ODS_MODES.get(ods_mode, ods_mode)]
        surfaces = [
            "env",
            "opencode",
            mode_surface,
            "perplexica",
            "hermes",
            "model-router-endpoints",
        ]
        if switchboard_mode == "enabled" and ods_mode != "cloud":
            surfaces.append("litellm-switchboard")
        if remote_llm_enabled:
            surfaces.append("remote-routing-state")
        return surfaces
    return [surface]


def validate_remote_inputs(inputs: RenderInputs) -> None:
    if not remote_route_enabled(inputs):
        return
    if inputs.ods_mode != "cloud":
        raise ValueError("remote LLM routing requires ODS_MODE=cloud")
    if inputs.remote_llm_transport not in {"direct", "ssh"}:
        raise ValueError("remote LLM routing requires REMOTE_LLM_TRANSPORT=direct or ssh")
    if not inputs.remote_llm_base_url.strip():
        raise ValueError("remote LLM routing requires REMOTE_LLM_BASE_URL")
    if not inputs.remote_llm_model.strip():
        raise ValueError("remote LLM routing requires REMOTE_LLM_MODEL")
    for label, value in {
        "REMOTE_LLM_BASE_URL": inputs.remote_llm_base_url,
        "REMOTE_LLM_MODEL": inputs.remote_llm_model,
    }.items():
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"remote LLM routing rejects control characters in {label}")
    if REMOTE_MODEL_ID_RE.fullmatch(inputs.remote_llm_model.strip()) is None:
        raise ValueError(
            "remote LLM routing requires a provider model id without spaces "
            "or shell metacharacters"
        )


def validate_render_inputs(inputs: RenderInputs) -> None:
    if inputs.context_length <= 0:
        raise ValueError(f"context length must be positive: {inputs.context_length}")
    if not (1 <= inputs.opencode_port <= 65535):
        raise ValueError(f"opencode port must be between 1 and 65535: {inputs.opencode_port}")
    for label, value in {
        "model": inputs.model,
        "gguf_file": inputs.gguf_file,
        "llm_base_url": inputs.llm_base_url,
        "litellm_key": inputs.litellm_key,
    }.items():
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{label} cannot contain control characters or newlines")
    if inputs.llm_api_key_env and not API_KEY_ENV_RE.fullmatch(inputs.llm_api_key_env):
        raise ValueError("llm api key env must be an upper-case environment variable name")
    validate_remote_inputs(inputs)


def render(args: argparse.Namespace) -> dict[str, object]:
    inputs = RenderInputs(
        switchboard_mode=getattr(args, 'switchboard_mode', 'enabled'),
        model=args.model,
        gguf_file=args.gguf_file,
        gpu_backend=args.gpu_backend,
        ods_mode=LEGACY_ODS_MODES.get(args.ods_mode, args.ods_mode),
        llm_base_url=args.llm_base_url,
        litellm_key=args.litellm_key,
        opencode_port=args.opencode_port,
        context_length=args.context_length,
        remote_llm_enabled=parse_remote_enabled(args.remote_llm_enabled),
        remote_llm_transport=args.remote_llm_transport,
        remote_llm_base_url=args.remote_llm_base_url,
        remote_llm_model=args.remote_llm_model,
        external_llm_authenticated=args.external_llm_authenticated,
        llm_api_key_env=getattr(args, "llm_api_key_env", ""),
    )
    validate_render_inputs(inputs)
    if args.surface == "litellm-switchboard" and inputs.ods_mode == "cloud":
        raise ValueError(
            "litellm-switchboard is local-runtime-only and cannot be rendered "
            "for ODS_MODE=cloud"
        )
    files = [
        RENDERERS[name](inputs)
        for name in select_surfaces(
            args.surface,
            inputs.ods_mode,
            inputs.switchboard_mode,
            inputs.remote_llm_enabled,
        )
    ]
    written: list[str] = []
    if args.write:
        output_root = Path(args.output_root)
        for item in files:
            target = output_root / item.path
            if item.surface == "model-router-endpoints":
                # Docker mounts this directory at /config. Its non-root router
                # needs traversal and read access even when the installer used
                # umask 077. This file contains endpoint origins, never keys.
                if target.parent.is_symlink() or target.parent.parent.is_symlink():
                    raise ValueError("model router config directory must not be a symlink")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.parent.chmod(0o711)
                atomic_write_text(target, ensure_trailing_newline(item.content), file_mode=0o644)
            else:
                atomic_write_text(target, ensure_trailing_newline(item.content))
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
    try:
        payload = render(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    output_format = args.format or ("paths" if args.write else "json")
    if output_format == "paths":
        for item in payload["files"]:
            print(item["path"])
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

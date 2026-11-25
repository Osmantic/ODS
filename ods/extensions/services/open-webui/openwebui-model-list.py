#!/usr/bin/env python3
"""Print the OPENAI_API_CONFIGS Open WebUI should start with, or nothing.

With Model Switchboard on, LiteLLM serves several names for the one active
local model: ods/current plus the compatibility aliases local, default and
the "*" catch-all (scripts/render-runtime-configs.py,
render_litellm_switchboard). Open WebUI lists every name LiteLLM returns, so
its model picker showed four entries that are all the same model (#7628).

Open WebUI can instead list a fixed set of model IDs for a connection. This
step gives the LiteLLM connection the names a person should pick from:
ods/current, and in hybrid mode the named cloud routes. The aliases keep
working for the other apps that call LiteLLM directly.

It prints nothing when LiteLLM is not serving the Switchboard map (cloud mode,
Switchboard off) or Open WebUI does not talk to LiteLLM, which leaves Open
WebUI listing whatever its connections return, as before.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

LITELLM_HOST = "litellm"
LITELLM_PORT = 4000
PUBLIC_MODEL = "ods/current"
# The named cloud routes render_litellm_switchboard adds in hybrid mode.
HYBRID_CLOUD_MODELS = ("cloud", "minimax", "minimax-fast")


def serves_switchboard_map(mode: str, switchboard: str) -> bool:
    """The rule extensions/services/litellm/select-config.sh applies."""
    return mode != "cloud" and switchboard == "enabled"


def visible_models(mode: str) -> list[str]:
    return [PUBLIC_MODEL, *(HYBRID_CLOUD_MODELS if mode == "hybrid" else ())]


def is_litellm(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == LITELLM_HOST and parsed.port == LITELLM_PORT


def connection_urls(env: Mapping[str, str]) -> list[str]:
    """Open WebUI's connection list: OPENAI_API_BASE_URLS, else OPENAI_API_BASE_URL."""
    urls = env.get("OPENAI_API_BASE_URLS") or env.get("OPENAI_API_BASE_URL") or ""
    return [url.strip() for url in urls.split(";")]


def api_configs(env: Mapping[str, str]) -> dict:
    """Per-connection configs keyed by position, as Open WebUI looks them up."""
    # Same defaults as the LiteLLM service (extensions/services/litellm/compose.yaml).
    mode = env.get("ODS_MODE") or "local"
    switchboard = env.get("ODS_MODEL_SWITCHBOARD") or "enabled"
    if not serves_switchboard_map(mode, switchboard):
        return {}
    return {
        str(index): {"enable": True, "model_ids": visible_models(mode)}
        for index, url in enumerate(connection_urls(env))
        if is_litellm(url)
    }


def main() -> int:
    configs = api_configs(os.environ)
    if configs:
        sys.stdout.write(json.dumps(configs, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read the serving context of the selected loaded external model."""

import json
import sys


def context_for(payload, source, model):
    if source == "llama-props":
        if payload.get("model_alias") != model:
            return None
        return payload.get("default_generation_settings", {}).get("n_ctx")
    if source == "ollama-ps":
        matches = [item.get("context_length") for item in payload.get("models", [])
                   if item.get("model", item.get("name")) == model]
        return min(matches) if matches and all(type(value) is int for value in matches) else None
    if source == "lmstudio-v1":
        matches = [instance.get("config", {}).get("context_length")
                   for item in payload.get("models", []) if item.get("type") == "llm"
                   for instance in item.get("loaded_instances", [])
                   if instance.get("id") == model]
        return min(matches) if matches and all(type(value) is int for value in matches) else None
    raise ValueError("unsupported external model context source")


if __name__ == "__main__":
    source, model = sys.argv[1:]
    payload = json.load(sys.stdin)
    context = context_for(payload, source, model)
    if type(context) is not int or not 4096 <= context <= 10_000_000:
        raise SystemExit("external model serving context unavailable")
    print(context)

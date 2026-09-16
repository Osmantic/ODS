#!/usr/bin/env python3
"""Select the best already-available chat model from a Lemonade model list.

The external Lemonade server, not ODS, owns the hardware and model catalog.
Consequently this selector does not apply ODS's local GGUF size ceilings.  It
prefers downloaded, tool-capable chat models and uses model footprint as a
deterministic strength heuristic.  Explicit ``LEMONADE_MODEL`` configuration
is handled by the installer before this helper is called.
"""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any


NON_CHAT_LABELS = {
    "audio",
    "embedding",
    "embeddings",
    "image",
    "image-generation",
    "rerank",
    "reranker",
    "speech",
    "transcription",
    "realtime-transcription",
    "tts",
}
CHAT_LABELS = {
    "chat",
    "completion",
    "conversational",
    "reasoning",
    "text-generation",
    "tool-calling",
    "vision",
}
NON_CHAT_ID_MARKERS = (
    "embedding",
    "rerank",
    "whisper",
    "kokoro",
    "stable-diffusion",
    "diffusion",
    "dall-e",
    "img2img",
    "txt2img",
    "comfy",
)
PARAMETER_COUNT = re.compile(
    r"(?<![0-9.])(\d+(?:\.\d+)?)\s*[bB](?![A-Za-z0-9])"
)


def _labels(item: dict[str, Any]) -> set[str]:
    raw = item.get("labels", [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {str(label).strip().lower() for label in raw if str(label).strip()}


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _looks_chat_capable(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return False
    labels = _labels(item)
    if labels & CHAT_LABELS:
        return True
    if labels & NON_CHAT_LABELS:
        return False
    normalized_id = re.sub(r"[^a-z0-9]+", "-", model_id.lower())
    return not any(marker in normalized_id for marker in NON_CHAT_ID_MARKERS)


def _parameter_billions(model_id: str) -> float:
    match = PARAMETER_COUNT.search(model_id)
    return _finite_number(match.group(1)) if match else 0.0


def select_model(payload: Any) -> str | None:
    if isinstance(payload, list):
        raw_models = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        raw_models = payload["data"]
    elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
        raw_models = payload["models"]
    else:
        return None

    candidates = [
        item
        for item in raw_models
        if isinstance(item, dict) and _looks_chat_capable(item)
    ]
    if not candidates:
        return None

    downloaded = [item for item in candidates if item.get("downloaded") is True]
    legacy = [item for item in candidates if "downloaded" not in item]
    if downloaded:
        candidates = downloaded
    elif legacy:
        # Older OpenAI-compatible model lists do not expose download state.
        candidates = legacy
    else:
        # Do not silently trigger a large model download on an external host.
        return None

    def rank(entry: tuple[int, dict[str, Any]]) -> tuple[float, ...]:
        index, item = entry
        labels = _labels(item)
        model_id = str(item.get("id") or "")
        return (
            1.0 if "tool-calling" in labels else 0.0,
            _parameter_billions(model_id),
            _finite_number(item.get("size")),
            _finite_number(item.get("max_context_window")),
            1.0 if "hot" in labels else 0.0,
            1.0 if item.get("suggested") is True else 0.0,
            -float(index),
        )

    _, selected = max(enumerate(candidates), key=rank)
    return str(selected["id"]).strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return 1
    selected = select_model(payload)
    if not selected:
        return 1
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

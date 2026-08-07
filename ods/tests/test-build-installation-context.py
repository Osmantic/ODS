#!/usr/bin/env python3
"""Regression tests for the installation-context HTTP boundary."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-installation-context.py"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def load_module():
    spec = importlib.util.spec_from_file_location("build_installation_context", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(payload: object) -> Response:
    return Response(json.dumps(payload).encode("utf-8"))


def main() -> int:
    module = load_module()

    with patch(
        "urllib.request.urlopen",
        side_effect=[
            response({"all_models_loaded": [None, {"model_name": "qwen-local"}]}),
        ],
    ):
        assert module._loaded_model() == "qwen-local"

    with patch(
        "urllib.request.urlopen",
        side_effect=[
            response({"all_models_loaded": ["invalid"]}),
            response({"data": [7, {"id": "openai-compatible"}]}),
        ],
    ):
        assert module._loaded_model() == "openai-compatible"

    with patch(
        "urllib.request.urlopen",
        side_effect=[response({"all_models_loaded": [False]}), response({"data": [None]})],
    ):
        assert module._loaded_model() is None

    print("[PASS] installation-context model payload tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

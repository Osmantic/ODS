#!/usr/bin/env python3
"""Behavioral tests for external Lemonade automatic model selection."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-external-lemonade-model.py"


def select(data: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SELECTOR)],
        input=json.dumps({"object": "list", "data": data}),
        text=True,
        capture_output=True,
        check=False,
    )


def model(model_id: str, **values: object) -> dict[str, object]:
    return {"id": model_id, **values}


def assert_selected(expected: str, data: list[dict[str, object]]) -> None:
    result = select(data)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


class ExternalLemonadeModelSelectorTests(unittest.TestCase):
    def test_strixy_catalog_selects_strongest_downloaded_tool_model(self) -> None:
        assert_selected(
            "Qwen3.6-35B-A3B-GGUF",
            [
                model("Qwen3-0.6B-GGUF", downloaded=True, labels=["reasoning", "tool-calling"], size=0.38, max_context_window=40960),
                model("Qwen3-Embedding-0.6B-GGUF", downloaded=True, labels=["embeddings"], size=0.64),
                model("Qwen3.5-2B-Q4_K_M", downloaded=True, labels=["custom", "tool-calling"], size=1.19, max_context_window=262144),
                model("Qwen3.6-35B-A3B-GGUF", downloaded=True, labels=["vision", "tool-calling", "hot"], size=23.3, max_context_window=262144),
                model("Whisper-Base", downloaded=True, labels=["transcription"], size=0.148),
                model("kokoro-v1", downloaded=True, labels=["tts"], size=0.354),
            ],
        )

    def test_tool_capability_outweighs_raw_size_for_pixel_default(self) -> None:
        assert_selected(
            "agent-7b",
            [
                model("plain-70b", downloaded=True, labels=["chat"], size=40),
                model("agent-7b", downloaded=True, labels=["tool-calling"], size=5),
            ],
        )

    def test_parameter_count_handles_missing_or_quantized_size(self) -> None:
        assert_selected(
            "agent-70B-Q2",
            [
                model("agent-35B-Q8", downloaded=True, labels=["tool-calling"], size=36),
                model("agent-70B-Q2", downloaded=True, labels=["tool-calling"], size=20),
            ],
        )
        assert_selected(
            "agent-35B",
            [
                model("agent-7B", downloaded=True, labels=["tool-calling"]),
                model("agent-35B", downloaded=True, labels=["tool-calling"]),
            ],
        )

    def test_non_chat_modalities_are_excluded_even_when_larger(self) -> None:
        assert_selected(
            "chat-2b",
            [
                model("giant-image-model", downloaded=True, labels=["image-generation"], size=100),
                model("embedding-90b", downloaded=True, labels=["embeddings"], size=90),
                model("chat-2b", downloaded=True, labels=["chat"], size=2),
            ],
        )

    def test_downloaded_models_outrank_catalog_only_entries(self) -> None:
        assert_selected(
            "ready-2b",
            [
                model("catalog-70b", downloaded=False, labels=["tool-calling"], size=40),
                model("ready-2b", downloaded=True, labels=["tool-calling"], size=2),
            ],
        )

    def test_sparse_legacy_response_remains_compatible_and_stable(self) -> None:
        assert_selected(
            "legacy-large",
            [
                model("legacy-small", size=1),
                model("legacy-large", size=8),
            ],
        )
        assert_selected("first", [model("first"), model("second")])

    def test_legacy_models_payload_and_string_labels_are_supported(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SELECTOR)],
            input=json.dumps(
                {
                    "models": [
                        model("voice-90B", labels="tts", size=90),
                        model("chat-8B", labels="tool-calling", size=5),
                    ]
                }
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "chat-8B")

    def test_explicitly_unavailable_catalog_fails_without_downloading(self) -> None:
        result = select([model("not-installed", downloaded=False, labels=["tool-calling"], size=8)])
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.stdout)


if __name__ == "__main__":
    unittest.main()

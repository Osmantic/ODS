#!/usr/bin/env python3
"""Whisper's model eviction timeout must use a key speaches actually reads.

speaches loads settings with pydantic-settings (env_nested_delimiter="__").
The STT eviction timeout is the top-level ``stt_model_ttl`` (env
``STT_MODEL_TTL``, default 300s). ``WHISPER__TTL`` maps to ``whisper.ttl``,
which WhisperConfig does not define, so pydantic drops it silently and the
model unloads after five idle minutes instead of the documented 24 hours.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ODS = Path(__file__).resolve().parents[2]
WHISPER = ODS / "extensions" / "services" / "whisper"


def _environment(compose: Path) -> dict[str, str]:
    service = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {}).get("services", {}).get("whisper", {})
    entries = service.get("environment") or []
    if isinstance(entries, dict):
        return {str(k): str(v) for k, v in entries.items()}
    return dict(str(e).partition("=")[::2] for e in entries)


def test_base_compose_sets_stt_model_ttl() -> None:
    env = _environment(WHISPER / "compose.yaml")
    assert env.get("STT_MODEL_TTL") == "86400", env


def test_no_overlay_uses_the_ignored_nested_key() -> None:
    for compose in sorted(WHISPER.glob("compose*.y*ml")):
        assert "WHISPER__TTL" not in _environment(compose), compose.name


def test_readme_documents_the_effective_key() -> None:
    readme = (WHISPER / "README.md").read_text(encoding="utf-8")
    assert "STT_MODEL_TTL" in readme
    assert "WHISPER__TTL" not in readme


if __name__ == "__main__":
    test_base_compose_sets_stt_model_ttl()
    test_no_overlay_uses_the_ignored_nested_key()
    test_readme_documents_the_effective_key()
    print("[PASS] whisper model TTL key")

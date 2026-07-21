#!/usr/bin/env python3
"""Tests for scripts/render-runtime-configs.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-runtime-configs.py"


def run_renderer(*args: str) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(proc.stdout)


def file_by_surface(payload: dict[str, object], surface: str) -> dict[str, str]:
    for item in payload["files"]:
        if item["surface"] == surface:
            return item
    raise AssertionError(f"missing surface {surface}")


def model_provider_by_id(settings: dict[str, object], provider_id: str) -> dict[str, object]:
    for provider in settings["modelProviders"]:
        if provider["id"] == provider_id:
            return provider
    raise AssertionError(f"missing model provider {provider_id}")


def test_all_surfaces_render() -> None:
    payload = run_renderer("--surface", "all")
    surfaces = {item["surface"] for item in payload["files"]}
    assert surfaces == {"env", "opencode", "litellm-lemonade", "perplexica", "hermes"}
    assert payload["mode"] == "dry-run"


def test_lemonade_disables_thinking_and_uses_extra_alias() -> None:
    payload = run_renderer(
        "--surface",
        "litellm-lemonade",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Model.gguf",
        "--litellm-key",
        "sk-test",
    )
    content = file_by_surface(payload, "litellm-lemonade")["content"]
    assert "model: openai/extra.Model.gguf" in content
    assert "api_key: sk-test" in content
    assert "enable_thinking: false" in content


def test_external_lemonade_uses_supplied_model_and_api_base() -> None:
    payload = run_renderer(
        "--surface",
        "litellm-lemonade",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--lemonade-model-id",
        "Qwen3-0.6B-GGUF",
        "--lemonade-api-base",
        "http://host.docker.internal:13305/api/v1",
        "--litellm-key",
        "lemonade-secret",
    )
    content = file_by_surface(payload, "litellm-lemonade")["content"]
    assert "model: openai/Qwen3-0.6B-GGUF" in content
    assert "api_base: http://host.docker.internal:13305/api/v1" in content
    assert "api_key: lemonade-secret" in content


def test_hermes_uses_lemonade_model_id_for_amd() -> None:
    payload = run_renderer(
        "--surface",
        "hermes",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Amd.gguf",
        "--llm-base-url",
        "http://litellm:4000/v1",
        "--context-length",
        "65536",
    )
    content = file_by_surface(payload, "hermes")["content"]
    assert 'default: "extra.Amd.gguf"' in content
    assert 'base_url: "http://litellm:4000/v1"' in content
    assert "context_length: 65536" in content


def test_perplexica_default_model_matches_route() -> None:
    payload = run_renderer(
        "--surface",
        "perplexica",
        "--ods-mode",
        "lemonade",
        "--gpu-backend",
        "amd",
        "--gguf-file",
        "Research.gguf",
    )
    content = json.loads(file_by_surface(payload, "perplexica")["content"])
    openai_provider = model_provider_by_id(content, "openai")
    assert content["preferences"]["defaultChatModel"] == "extra.Research.gguf"
    assert openai_provider["chatModels"][0]["name"] == "extra.Research.gguf"


def test_write_mode_writes_under_output_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--surface",
                "litellm-lemonade",
                "--ods-mode",
                "lemonade",
                "--gpu-backend",
                "amd",
                "--gguf-file",
                "Written.gguf",
                "--output-root",
                tmp,
                "--write",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(proc.stdout)
        target = Path(tmp) / "config" / "litellm" / "lemonade.yaml"
        assert payload["mode"] == "write"
        assert target.exists()
        assert "openai/extra.Written.gguf" in target.read_text(encoding="utf-8")


def test_written_bytes_are_unchanged() -> None:
    # Get dry-run output
    payload = run_renderer("--surface", "litellm-lemonade")
    dry_run_content = file_by_surface(payload, "litellm-lemonade")["content"]

    with tempfile.TemporaryDirectory() as tmp:
        run_renderer(
            "--surface", "litellm-lemonade",
            "--output-root", tmp,
            "--write"
        )
        target = Path(tmp) / "config" / "litellm" / "lemonade.yaml"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == dry_run_content


def test_write_never_exposes_a_truncated_config() -> None:
    import importlib.util
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location("renderer", str(SCRIPT))
    renderer = importlib.util.module_from_spec(spec)
    sys.modules["renderer"] = renderer
    spec.loader.exec_module(renderer)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config" / "litellm" / "lemonade.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original content", encoding="utf-8")

        args = renderer.parse_args([
            "--surface", "litellm-lemonade",
            "--output-root", tmp,
            "--write"
        ])

        with patch("os.fsync", side_effect=RuntimeError("disk full")):
            try:
                renderer.render(args)
            except RuntimeError:
                pass

        assert target.read_text(encoding="utf-8") == "original content"
        tmp_files = list(target.parent.glob("*.tmp"))
        assert len(tmp_files) == 0


def test_write_path_never_truncates_in_place() -> None:
    import importlib.util
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location("renderer", str(SCRIPT))
    renderer = importlib.util.module_from_spec(spec)
    sys.modules["renderer"] = renderer
    spec.loader.exec_module(renderer)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config" / "litellm" / "lemonade.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original content", encoding="utf-8")

        args = renderer.parse_args([
            "--surface", "litellm-lemonade",
            "--output-root", tmp,
            "--write"
        ])

        original_replace = os.replace
        replace_called = False

        def mock_replace(src, dst):
            nonlocal replace_called
            replace_called = True
            assert Path(dst).read_text(encoding="utf-8") == "original content"
            original_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            renderer.render(args)

        assert replace_called


def main() -> int:
    tests = [
        test_all_surfaces_render,
        test_lemonade_disables_thinking_and_uses_extra_alias,
        test_external_lemonade_uses_supplied_model_and_api_base,
        test_hermes_uses_lemonade_model_id_for_amd,
        test_perplexica_default_model_matches_route,
        test_write_mode_writes_under_output_root,
        test_written_bytes_are_unchanged,
        test_write_never_exposes_a_truncated_config,
        test_write_path_never_truncates_in_place,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Cross-platform contract tests for bundled TEI and Open WebUI RAG."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMBEDDING_ENV = {
    "EMBEDDING_MODEL": "BAAI/bge-m3",
    "RAG_EMBEDDING_MODEL": "external-embedding-v2",
    "RAG_OPENAI_API_BASE_URL": "https://embeddings.example.test/v1",
    "RAG_OPENAI_API_KEY": "external-test-key",
    "EMBEDDINGS_MEMORY_LIMIT": "6GB",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def assert_embedding_env(values: dict[str, str]) -> None:
    for key, expected in EMBEDDING_ENV.items():
        assert values.get(key) == expected, f"{key}: expected {expected!r}, got {values.get(key)!r}"


def render_compose(**overrides: str) -> dict:
    env = os.environ.copy()
    env.update({
        "WEBUI_SECRET": "test-webui-secret",
        "EMBEDDING_MODEL": "BAAI/bge-m3",
        "EMBEDDINGS_MEMORY_LIMIT": "6G",
    })
    env.update(overrides)
    for key in ("RAG_EMBEDDING_MODEL", "RAG_OPENAI_API_BASE_URL", "RAG_OPENAI_API_KEY"):
        if key not in overrides:
            env.pop(key, None)

    result = subprocess.run(
        [
            "docker", "compose",
            "-f", "docker-compose.base.yml",
            "-f", "extensions/services/embeddings/compose.yaml",
            "config", "--format", "json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(result.stdout)


def test_bundled_rag_inherits_canonical_model() -> None:
    config = render_compose()
    embeddings = config["services"]["embeddings"]
    webui = config["services"]["open-webui"]

    assert embeddings["environment"]["MODEL_ID"] == "BAAI/bge-m3"
    assert webui["environment"]["RAG_EMBEDDING_MODEL"] == "BAAI/bge-m3"
    assert embeddings["deploy"]["resources"]["limits"]["memory"] == "6442450944"


def test_external_rag_override_does_not_change_bundled_tei() -> None:
    config = render_compose(
        RAG_EMBEDDING_MODEL="external-embedding-v2",
        RAG_OPENAI_API_BASE_URL="https://embeddings.example.test/v1",
        RAG_OPENAI_API_KEY="external-test-key",
    )
    embeddings = config["services"]["embeddings"]
    webui = config["services"]["open-webui"]

    assert embeddings["environment"]["MODEL_ID"] == "BAAI/bge-m3"
    assert webui["environment"]["RAG_EMBEDDING_MODEL"] == "external-embedding-v2"
    assert webui["environment"]["RAG_OPENAI_API_BASE_URL"] == "https://embeddings.example.test/v1"
    assert webui["environment"]["RAG_OPENAI_API_KEY"] == "external-test-key"


def test_installers_write_or_backfill_canonical_model() -> None:
    linux = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    macos = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    windows = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")

    assert 'EMBEDDING_MODEL_VALUE=$(_env_get EMBEDDING_MODEL "${EMBEDDING_MODEL:-BAAI/bge-base-en-v1.5}")' in linux
    assert "EMBEDDING_MODEL=${EMBEDDING_MODEL_VALUE}" in linux
    assert 'upsert_env_value "$env_path" "EMBEDDING_MODEL" "${EMBEDDING_MODEL:-BAAI/bge-base-en-v1.5}"' in macos
    assert "EMBEDDING_MODEL=${embedding_model}" in macos
    assert '$embeddingModel = Get-EnvOrNew "EMBEDDING_MODEL" $embeddingModelDefault' in windows
    assert "EMBEDDING_MODEL=$embeddingModel" in windows

    for key in (
        "RAG_EMBEDDING_MODEL",
        "RAG_OPENAI_API_BASE_URL",
        "RAG_OPENAI_API_KEY",
        "EMBEDDINGS_MEMORY_LIMIT",
    ):
        assert key in linux
        assert key in macos
        assert key in windows


def test_windows_env_generator_renders_embedding_contract() -> None:
    env = os.environ.copy()
    env.update(EMBEDDING_ENV)
    env["ODS_TEST_ROOT"] = str(ROOT)

    with tempfile.TemporaryDirectory(prefix="ods-windows-embedding-contract-") as temp_dir:
        env["ODS_TEST_DIR"] = temp_dir
        script = r'''
$ErrorActionPreference = "Stop"
function Write-AIWarn { param([string]$Message) }
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/detection.ps1")
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/env-generator.ps1")
$tier = @{
    TierName = "Embedding contract"
    LlmModel = "test-model"
    GgufFile = "test.gguf"
    MaxContext = 4096
}
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
$env:EMBEDDING_MODEL = "BAAI/should-not-replace-existing"
$env:RAG_EMBEDDING_MODEL = "should-not-replace-existing"
$env:RAG_OPENAI_API_BASE_URL = "https://replacement.example.test/v1"
$env:RAG_OPENAI_API_KEY = "replacement-secret"
$env:EMBEDDINGS_MEMORY_LIMIT = "8GB"
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
'''
        subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        assert_embedding_env(read_env(Path(temp_dir) / ".env"))

        legacy_dir = Path(temp_dir) / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / ".env").write_text("WEBUI_AUTH=false\n", encoding="utf-8")
        env["ODS_TEST_DIR"] = str(legacy_dir)
        legacy_script = script.split('$env:EMBEDDING_MODEL = "BAAI/should-not-replace-existing"', 1)[0]
        subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", legacy_script],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        legacy_values = read_env(legacy_dir / ".env")
        assert_embedding_env(legacy_values)

        empty_override_dir = Path(temp_dir) / "empty-overrides"
        empty_override_dir.mkdir()
        (empty_override_dir / ".env").write_text(
            "RAG_EMBEDDING_MODEL=\nRAG_OPENAI_API_BASE_URL=\nRAG_OPENAI_API_KEY=\n",
            encoding="utf-8",
        )
        env["ODS_TEST_DIR"] = str(empty_override_dir)
        subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", legacy_script],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        empty_values = read_env(empty_override_dir / ".env")
        assert empty_values["RAG_EMBEDDING_MODEL"] == ""
        assert empty_values["RAG_OPENAI_API_BASE_URL"] == ""
        assert empty_values["RAG_OPENAI_API_KEY"] == ""


def test_macos_env_generator_renders_embedding_contract() -> None:
    env = os.environ.copy()
    env.update(EMBEDDING_ENV)

    with tempfile.TemporaryDirectory(prefix="ods-macos-embedding-contract-") as temp_dir:
        subprocess.run(
            [
                "bash", "-c",
                'source installers/macos/lib/detection.sh; '
                'source installers/macos/lib/env-generator.sh; '
                'generate_ods_env "$1" 3 true >/dev/null',
                "ods-embedding-contract", temp_dir,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        env_path = Path(temp_dir) / ".env"
        assert_embedding_env(read_env(env_path))

        # Simulate an older install that predates the complete contract. A
        # rerun must backfill every missing key while preserving the rest of
        # the generated environment.
        env_path.write_text(
            "\n".join(
                line for line in env_path.read_text(encoding="utf-8").splitlines()
                if line.split("=", 1)[0] not in EMBEDDING_ENV
            ) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "bash", "-c",
                'source installers/macos/lib/detection.sh; '
                'source installers/macos/lib/env-generator.sh; '
                'generate_ods_env "$1" 3 false >/dev/null',
                "ods-embedding-contract", temp_dir,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        assert_embedding_env(read_env(env_path))

        empty_override_dir = Path(temp_dir) / "empty-overrides"
        empty_override_dir.mkdir()
        (empty_override_dir / ".env").write_text(
            "RAG_EMBEDDING_MODEL=\nRAG_OPENAI_API_BASE_URL=\nRAG_OPENAI_API_KEY=\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "bash", "-c",
                'source installers/macos/lib/detection.sh; '
                'source installers/macos/lib/env-generator.sh; '
                'generate_ods_env "$1" 3 false >/dev/null',
                "ods-embedding-empty-contract", str(empty_override_dir),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        empty_values = read_env(empty_override_dir / ".env")
        assert empty_values["RAG_EMBEDDING_MODEL"] == ""
        assert empty_values["RAG_OPENAI_API_BASE_URL"] == ""
        assert empty_values["RAG_OPENAI_API_KEY"] == ""


def main() -> int:
    tests = [
        test_bundled_rag_inherits_canonical_model,
        test_external_rag_override_does_not_change_bundled_tei,
        test_installers_write_or_backfill_canonical_model,
    ]
    if shutil.which("pwsh"):
        tests.append(test_windows_env_generator_renders_embedding_contract)
    else:
        print("[SKIP] test_windows_env_generator_renders_embedding_contract (pwsh unavailable)")
    if os.name != "nt" and shutil.which("bash"):
        tests.append(test_macos_env_generator_renders_embedding_contract)
    else:
        print("[SKIP] test_macos_env_generator_renders_embedding_contract (POSIX bash unavailable)")
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

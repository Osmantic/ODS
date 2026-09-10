#!/usr/bin/env python3
"""Fail-closed Pixel default-model selection contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select-model.py"
CATALOG = ROOT / "config" / "model-library.json"


def run_selector(
    catalog: Path,
    *extra: str,
    max_size_mb: int = 1221,
    agent_ready_only: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SELECTOR),
            "--catalog",
            str(catalog),
            "--backend",
            "nvidia",
            "--memory-type",
            "discrete",
            "--vram-mb",
            "8151",
            "--ram-gb",
            "31",
            "--profile",
            "qwen",
            "--tier",
            "0",
            "--max-size-mb",
            str(max_size_mb),
            "--host-arch",
            "amd64",
            "--installable-only",
            *(["--agent-ready-only"] if agent_ready_only else []),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def catalog_model(
    model_id: str,
    pixel_status: str | None,
    *,
    size_mb: int,
    generic_status: str = "verified",
) -> dict[str, object]:
    return {
        "id": model_id,
        "name": model_id,
        "family": "qwen",
        "llm_model_name": model_id,
        "gguf_file": f"{model_id}.gguf",
        "gguf_url": f"https://huggingface.co/ods/{model_id}/resolve/main/{model_id}.gguf",
        "gguf_sha256": "a" * 64,
        "size_mb": size_mb,
        "vram_required_gb": 4,
        "context_length": 65536,
        "app_compatibility": {
            "agent_viability": {"status": generic_status},
            **(
                {"pixel_agent": {"status": pixel_status}}
                if pixel_status is not None
                else {}
            ),
        },
    }


def main() -> int:
    result = run_selector(CATALOG)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"]["id"] == "qwen3.5-9b-q4"
    assert payload["selected"]["pixel_agent_status"] == "verified"
    assert payload["policy"].endswith("+pixel-agent-capability-v1")
    assert "overrides --tier 0's 1221MB model size preference" in payload["reason"]

    result = run_selector(CATALOG, max_size_mb=0, agent_ready_only=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"]["id"] == "qwen3.5-9b-q4"
    assert payload["selected"]["pixel_agent_status"] == "verified"
    assert payload["source"] == "catalog_runtime_profile_pre_download"
    assert payload["alternatives"][0]["context_length"] == 65536

    with tempfile.TemporaryDirectory() as directory:
        catalog = Path(directory) / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "models": [
                        catalog_model("failed-small", "not_agent_viable", size_mb=900),
                        catalog_model("verified-larger", "verified", size_mb=1800),
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = run_selector(catalog)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["selected"]["id"] == "verified-larger"
        assert payload["selected"]["pixel_agent_status"] == "verified"
        assert payload["policy"].endswith("+pixel-agent-capability-v1")
        assert "overrides --tier 0's 1221MB model size preference" in payload["reason"]

        catalog.write_text(
            json.dumps(
                {
                    "models": [
                        catalog_model("failed-only", "not_agent_viable", size_mb=900),
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = run_selector(catalog)
        assert result.returncode == 2
        assert not result.stdout.strip()
        assert "no explicitly verified Pixel agent model fits" in result.stderr

        result = run_selector(catalog, max_size_mb=0, agent_ready_only=False)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["selected"]["id"] == "failed-only"
        assert payload["selected"]["pixel_agent_status"] == "not-agent-viable"

        catalog.write_text(
            json.dumps(
                {
                    "models": [
                        catalog_model(
                            "generic-agent-only",
                            None,
                            size_mb=900,
                            generic_status="verified",
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = run_selector(catalog)
        assert result.returncode == 2
        assert "no explicitly verified Pixel agent model fits" in result.stderr

    detection = (ROOT / "installers" / "phases" / "02-detection.sh").read_text(
        encoding="utf-8"
    )
    features = (ROOT / "installers" / "phases" / "03-features.sh").read_text(
        encoding="utf-8"
    )
    assert "--agent-ready-only" not in detection
    assert '_selector_max_size_mb=0' in detection
    assert "strongest installable hardware-fit model" in detection
    assert '_selector_env="$(_run_catalog_selector 2>>' in detection
    assert 'PIXEL_AGENT_MODEL_READY:-unknown' in features
    assert "Pixel adaptive mode will use this best-fit local model" in features
    assert "catalog testing is performance guidance, not an access gate" in features
    assert "PIXEL_AGENT_MODE=hermes" not in features[
        features.index('PIXEL_AGENT_MODEL_READY:-unknown'):
        features.index('export PIXEL_AGENT_MODE')
    ]
    assert "ods_pixel_model_route_class" in features
    assert 'unmanaged-external' not in features
    assert '"${ENABLE_PIXEL_RUNTIME:-false}" == "true"' in features
    assert '_sync_extension_compose "$_pixel_support_services" litellm' in features
    # Search is shared with other installed consumers as well as Pixel.
    # Execute every consumer combination instead of pinning its old variable name.
    subprocess.run(["bash", str(ROOT / "tests" / "test-pixel-support-services.sh")], check=True)

    print("Pixel model selector tests passed: 7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

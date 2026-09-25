"""The assignment CLI must preserve capacity when returning spare GPUs to LLM."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("spare_free_gb,expected_llama", [
    ([0], [0]),
    ([2], [0]),
    ([10], [0, 4]),
    ([8, 8], [0, 4, 5]),
])
def test_assignment_keeps_a_fitting_llama_group(tmp_path, spare_free_gb, expected_llama):
    free_gb = [24, 16, 14, 12, *spare_free_gb]
    topology = {
        "vendor": "nvidia", "gpu_count": len(free_gb), "links": [],
        "gpus": [
            {"index": index, "uuid": f"GPU-{index}", "name": "24 GB compute GPU",
             "memory_gb": 24, "memory_free_gb": free, "memory_type": "discrete"}
            for index, free in enumerate(free_gb)
        ],
    }
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topology), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "assign_gpus.py"),
        "--topology", str(path), "--model-size", "20000",
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assignment = json.loads(result.stdout)["gpu_assignment"]
    services = assignment["services"]
    llm = services["llama_server"]
    indices = llm["gpu_indices"]
    # This topology emits an equal pipeline split (or a single GPU).
    required_mb = 20000 / len(indices)
    assert all(free_gb[index] * 1024 >= required_mb for index in indices), llm
    assert indices == expected_llama
    assert llm["gpus"] == [f"GPU-{index}" for index in indices]
    assert llm["parallelism"]["pipeline_parallel_size"] == len(indices)
    assert llm["parallelism"]["tensor_parallel_size"] == 1
    assert assignment["strategy"] == "dedicated"
    assert {name: services[name]["gpu_indices"] for name in ("whisper", "comfyui", "embeddings")} == {
        "whisper": [1], "comfyui": [2], "embeddings": [3],
    }
